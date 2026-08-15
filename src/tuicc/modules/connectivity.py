"""Connectivity module: wifi networks and bluetooth devices, in two
sections of one box. WiFi/Bluetooth headers are the only level-1
(cross-module Tab-reachable) items — Enter on one claims mode_stack
("connectivity_browsing") and browses that section's own networks/
devices exclusively; see this module's own "level-2 browsing" section
docstring for the full design (a genuine input claim, not the
orthogonal two-level-expand mechanism sessions.py/media.py/sysmon.py
use). Within browsing, Enter connects/disconnects the selected item —
actual connect/disconnect happens on the StatusWorker's background
thread (ctx.status), never blocking the render loop.

CLAUDE/VISION.md's R4 added two more input claims this module owns the
display state for, same "module-level state, main.py notices and pushes
onto mode_stack on its behalf" idiom sessions.py's naming field
established: a wifi passphrase prompt (is_entering_passphrase()'s own
quartet, driven by iwd_agent.py's IwdAgent registering a D-Bus agent
with iwd) and a bluetooth pairing confirm (is_confirming_pairing(),
driven by bluez_agent.py's BluezAgent). Both are triggered from
outside normal navigation entirely — an iwd/bluez daemon callback can
arrive at any time, not just right after a keypress — so main.py
notices them via IwdAgent.mailbox.has_pending()/BluezAgent.mailbox.
has_pending() each frame, not via dispatch_action() the way every
other mode_stack tier in this codebase is entered. See
agent_mailbox.py's own module docstring for the cross-thread handoff
this all rests on.

---
IMPORTANT: draw() and nav_items() must agree on exactly which row
each item lands on. Rather than computing row positions twice (and
risking the two computations drifting apart), _build_rows() is the
single source of truth both functions iterate over.
"""

import curses
import time
from datetime import datetime

from tuicc.navigation import NavItem
from tuicc.render_utils import draw_box_outline, draw_centered_lines, display_width, wc_truncate
from tuicc.keybinds import key_label
from tuicc.windowed_list import section_nav_indices as _section_nav_indices
from tuicc.windowed_list import section_rows as _section_rows
from tuicc.windowed_list import header_with_count as _header_with_count


SIGNAL_SEGMENTS = 5


def _signal_bars(signal):
    """5 discrete filled/empty segments, always all 5 shown, plain
    Unicode Geometric Shapes (▮/▯). See
    CLAUDE/NOTES/design-decisions.md#connectivity-module-design for why.
    None (backend can't report a signal) renders as all-empty, keeping
    the "always 5 segments" visual consistency intact.
    """
    if signal is None:
        return "▯" * SIGNAL_SEGMENTS
    # Integer ceiling division into 5 equal 20%-wide buckets — any
    # nonzero signal shows at least 1 filled segment (never looks like
    # "no signal" when there IS one, however weak), 100% shows all 5.
    level = min(SIGNAL_SEGMENTS, max(0, (signal + 19) // 20))
    return "▮" * level + "▯" * (SIGNAL_SEGMENTS - level)


def _selected_wifi_index(networks, selected_id):
    """Which index into `networks` the current selection corresponds
    to, if any — used to anchor the scroll window (windowed_list.
    window_start) so navigating onto a network keeps its row in view.
    Same pattern media.py's own _selected_output_index uses.
    """
    if not selected_id or not selected_id.startswith("connectivity:wifi:") \
            or selected_id == "connectivity:wifi:header":
        return None
    ssid = selected_id.split(":", 2)[2]
    for i, network in enumerate(networks):
        if network.ssid == ssid:
            return i
    return None


def _selected_bt_index(devices, selected_id):
    if not selected_id or not selected_id.startswith("connectivity:bt:") \
            or selected_id == "connectivity:bt:header":
        return None
    device_id = selected_id.split(":", 2)[2]
    for i, device in enumerate(devices):
        if device.id == device_id:
            return i
    return None


def _build_rows(ctx, box_h):
    """One row per line the box will render, in order — the single
    source of truth draw() and nav_items() both walk, so their row
    indices can never drift apart. WiFi/Bluetooth each get a
    fixed-slot-plus-scroll window (windowed_list.py, shared with
    media.py/sysmon.py) — see CLAUDE/NOTES/design-decisions.md
    #connectivity-module-design. box_h is accepted for signature
    symmetry with media.py's _build_rows but unused here.
    """
    wifi_networks = ctx.wifi_networks
    bluetooth_devices = ctx.bluetooth_devices
    visible_slots = ctx.config.connectivity_visible_slots

    selected_wifi_index = _selected_wifi_index(wifi_networks or [], ctx.selected_id)
    selected_bt_index = _selected_bt_index(bluetooth_devices or [], ctx.selected_id)

    rows = [("wifi_header", _header_with_count("WiFi", wifi_networks))]
    rows.extend(_section_rows(wifi_networks, ctx.wifi_error, selected_wifi_index, "wifi_item", "network",
                               visible_slots=visible_slots))

    rows.append(("spacer", None))
    rows.append(("bt_header", _header_with_count("Bluetooth", bluetooth_devices)))
    rows.extend(_section_rows(bluetooth_devices, ctx.bluetooth_error, selected_bt_index, "bt_item", "device",
                               visible_slots=visible_slots))

    return rows


# ---------- R4: wifi passphrase entry (iwd_agent.py's RequestPassphrase) ----------
# Same "module-level state, quartet of functions" shape as sessions.py's
# naming field — see this module's own docstring for why main.py
# notices and drives this differently (IwdAgent.mailbox.has_pending(),
# not a dispatch_action() call).

_entering_passphrase_ssid = None  # str | None — set for the whole flow: typing, waiting, AND showing an error
_passphrase_input = ""
# True from the moment the typed passphrase is actually sent to iwd
# until the real connect result (success/failure) is known — see
# CLAUDE/NOTES/design-decisions.md#connectivity-module-design for why
# this must stay open rather than close the instant Enter is pressed.
# StatusWorker.is_pending("wifi", ssid) stays True for the whole
# duration, from the first Enter on the network row through the agent
# round-trip to the final success/error.
_passphrase_waiting = False
_passphrase_error = None  # str | None — the most recent failed attempt's real error message


def is_entering_passphrase() -> bool:
    return _entering_passphrase_ssid is not None


def entering_passphrase_ssid() -> str | None:
    """The ssid this flow is currently for, or None if nothing's in
    progress — main.py's own top-of-loop resolution check (is the real
    connect attempt still running, has it failed) needs this to know
    WHICH network's StatusWorker state to look at."""
    return _entering_passphrase_ssid


def start_passphrase_entry(ssid: str) -> None:
    """Called for the FIRST prompt (a brand new RequestPassphrase) AND
    for a RETRY — iwd asking again after a wrong passphrase calls
    RequestPassphrase a second time, which main.py notices the exact
    same way as the first (see its own "connectivity_passphrase" tier)
    — always resets back to a fresh typing state, discarding whatever
    was typed/shown before.
    """
    global _entering_passphrase_ssid, _passphrase_input, _passphrase_waiting, _passphrase_error
    _entering_passphrase_ssid = ssid
    _passphrase_input = ""
    _passphrase_waiting = False
    _passphrase_error = None


def cancel_passphrase_entry() -> None:
    """Discards the whole flow without producing a further answer —
    called on the user pressing Escape (only reachable while typing or
    looking at an error, never while _passphrase_waiting — see
    main.py's own tier) and on main.py noticing the daemon itself
    already cancelled the request (IwdAgent.mailbox.has_pending() went
    False on its own — see agent_mailbox.py's module docstring)."""
    global _entering_passphrase_ssid, _passphrase_input, _passphrase_waiting, _passphrase_error
    _entering_passphrase_ssid = None
    _passphrase_input = ""
    _passphrase_waiting = False
    _passphrase_error = None


def handle_passphrase_key(key: int) -> bool:
    """Same shape as sessions.py's handle_naming_key — Enter isn't
    handled here (submitting needs the live IwdAgent this function
    doesn't have), so the caller checks for confirm before falling
    back to this for everything else. Returns still_claiming (False
    only on Escape, though main.py's own Escape branch already
    short-circuits before reaching this — kept for the same shape/
    safety-net reasoning handle_naming_key's own comment gives).
    """
    global _passphrase_input
    if key == 27:  # Escape
        return False
    if key in (curses.KEY_BACKSPACE, 127, 8):
        _passphrase_input = _passphrase_input[:-1]
        return True
    if 32 <= key <= 126:
        _passphrase_input += chr(key)
    return True


def apply_passphrase() -> str | None:
    """The typed passphrase (may be empty — an empty submit is still a
    real answer main.py hands to IwdAgent.reply_passphrase(), not
    treated as a cancel; iwd itself will simply reject an empty
    passphrase and the connect fails the normal way), or None if
    nothing's in progress. Does NOT clear the flow — main.py still
    needs is_entering_passphrase()/the ssid while it waits for the
    real connect result; call mark_passphrase_submitted() right after
    actually handing this to IwdAgent.reply_passphrase(). Unlike
    sessions.py's apply_naming(), also never writes anything to cfg —
    the caller is the one that knows how to deliver this answer.
    """
    if _entering_passphrase_ssid is None:
        return None
    return _passphrase_input


def mark_passphrase_submitted() -> None:
    """Called right after main.py hands the typed text to
    IwdAgent.reply_passphrase() — switches the overlay from "typing"
    to "waiting for the real result", see is_passphrase_waiting()."""
    global _passphrase_waiting
    _passphrase_waiting = True


def is_passphrase_waiting() -> bool:
    return _passphrase_waiting


def set_passphrase_error(message: str) -> None:
    """Called once main.py sees the connect attempt actually failed
    (StatusWorker.get_action_error_for("wifi", ssid)) — moves back out
    of "waiting" so the overlay shows the real error instead of
    silently sitting there or vanishing."""
    global _passphrase_waiting, _passphrase_error
    _passphrase_waiting = False
    _passphrase_error = message


def passphrase_error() -> str | None:
    return _passphrase_error


# ---------- R4: bluetooth pairing confirm (bluez_agent.py's RequestConfirmation/RequestAuthorization) ----------
# Same "typing/confirm -> waiting -> error-or-close" shape as the wifi
# passphrase flow above, minus the typing step (plain yes/no) — see
# that flow's own comments for the "why wait at all" reasoning
# (StatusWorker.is_pending("bluetooth", device_id) stays True for the
# WHOLE Pair()+Connect() round-trip, not just the instant confirm_yes/
# confirm_no is pressed).

_pairing_request = None  # bluez_agent.PairingRequest | None — set for the whole flow, waiting AND error included
_pairing_waiting = False
_pairing_error = None  # str | None


def is_confirming_pairing() -> bool:
    return _pairing_request is not None


def current_pairing_request():
    """The request this flow is currently for, or None — same role
    entering_passphrase_ssid() plays for the wifi side; main.py's own
    resolution check needs .device_id to know which device's
    StatusWorker state to look at."""
    return _pairing_request


def start_pairing_confirm(request) -> None:
    global _pairing_request, _pairing_waiting, _pairing_error
    _pairing_request = request
    _pairing_waiting = False
    _pairing_error = None


def cancel_pairing_confirm() -> None:
    """Clears the whole flow — called after main.py has already told
    BluezAgent the answer (or the daemon cancelled on its own), same
    role cancel_passphrase_entry() plays for the wifi side."""
    global _pairing_request, _pairing_waiting, _pairing_error
    _pairing_request = None
    _pairing_waiting = False
    _pairing_error = None


def mark_pairing_submitted() -> None:
    global _pairing_waiting
    _pairing_waiting = True


def is_pairing_waiting() -> bool:
    return _pairing_waiting


def set_pairing_error(message: str) -> None:
    global _pairing_waiting, _pairing_error
    _pairing_waiting = False
    _pairing_error = message


def pairing_error() -> str | None:
    return _pairing_error


# ---------- level-2 browsing: a real input claim over one section ----------
# WiFi/Bluetooth headers are the only level-1 (cross-module Tab-
# reachable) items — Enter on one claims mode_stack ("connectivity_
# browsing", pushed by main.py right after dispatch_action(), same
# "module sets its own state, main.py notices and pushes" idiom as
# sessions.py's is_naming()) and browses that section's own items
# exclusively, via main.py's handle_connectivity_browsing hand-rolling
# every key itself — NOT the same mechanism as sessions.py/media.py/
# sysmon.py's own two-level expand (that one stays orthogonal to
# mode_stack; this one is a genuine modal claim, same shape as
# resize_mode.py's editing level, chosen specifically so a dedicated
# "scan" key becomes safe to bind without colliding with ambient
# typing or global shortcuts). Grew three more wifi-only dedicated keys
# after "scan" — forget a known network (request_forget()/
# is_confirming_forget() below, a Y/N sub-state of browsing itself, not
# its own tier), connect to a hidden network by typed SSID
# (is_entering_hidden_ssid()'s own quartet below, a genuine second
# tier, "connectivity_hidden_ssid" — typed text is a different KIND of
# interaction than a plain y/n), and toggle the radio's power (handled
# entirely in main.py's handle_connectivity_browsing, no module state
# of its own needed beyond reading the "wifi_adapter" domain).
_browsing_section = None  # "wifi" | "bluetooth" | None


def is_browsing() -> bool:
    return _browsing_section is not None


def browsing_section() -> str | None:
    return _browsing_section


def start_browsing(section: str) -> None:
    global _browsing_section
    _browsing_section = section


def stop_browsing() -> None:
    global _browsing_section
    _browsing_section = None


def next_browsing_selection(section: str, items: list, current_selected_id: str | None, direction: int) -> str:
    """The next/previous item's own "connectivity:<section>:<key>" id,
    wrapping at both ends (same convention same_row_neighbor(wrap=True)
    already uses for the OTHER two-level modules — a claimed list with
    nowhere else to Tab into shouldn't have a dead end at either edge).
    Built on the existing _selected_wifi_index/_selected_bt_index
    (rather than tracking a separate index) so loop_state.selected_id
    stays the single source of truth for "what's selected" everywhere,
    same as every other module in this codebase. current_selected_id
    not matching anything in `items` (nothing selected yet, or the
    previously-selected item just disappeared) defaults to the first
    item, not a crash or a no-op.
    """
    index_fn = _selected_wifi_index if section == "wifi" else _selected_bt_index
    key_fn = (lambda it: it.ssid) if section == "wifi" else (lambda it: it.id)
    # id prefix is "bt", not "bluetooth" — matches _bt_row_nav_item's
    # own f"connectivity:bt:{device.id}" convention, distinct from the
    # StatusWorker domain name ("bluetooth") `section` itself holds.
    id_prefix = "wifi" if section == "wifi" else "bt"
    current_index = index_fn(items, current_selected_id)
    new_index = 0 if current_index is None else (current_index + direction) % len(items)
    return f"connectivity:{id_prefix}:{key_fn(items[new_index])}"


def toggle_wifi(status, ssid: str) -> None:
    """Enter toggles: connect if not connected, disconnect if it is.
    Extracted from what used to be handle_wifi's own body — individual
    wifi rows are only ever reachable while browsing (see this
    section's own docstring), so this is called directly from main.py's
    handle_connectivity_browsing rather than through dispatch_action.
    """
    networks = status.get("wifi") or []
    network = next((n for n in networks if n.ssid == ssid), None)
    if network is not None and network.connected:
        status.request_action("wifi", "disconnect", ssid)
    else:
        status.request_action("wifi", "connect", ssid)


def toggle_bluetooth(status, device_id: str) -> None:
    """Same reasoning as toggle_wifi above, for bluetooth devices."""
    devices = status.get("bluetooth") or []
    device = next((d for d in devices if d.id == device_id), None)
    if device is not None and device.connected:
        status.request_action("bluetooth", "disconnect", device_id)
    else:
        status.request_action("bluetooth", "connect", device_id)


# ---------- forget confirm: a Y/N sub-state of browsing, not its own mode_stack tier ----------
# Same shape resize_mode.py's own confirm_delete flag uses (and
# bluetooth pairing's inline y/n, above) rather than the shared
# pending_confirm dict — both existing "confirm while already inside a
# claimed mode" precedents in this codebase use a small inline flag
# checked FIRST by that mode's own key handler, not a second claim.
# main.py's handle_connectivity_browsing checks is_confirming_forget()
# before its normal key branches.

_confirming_forget_ssid = None  # str | None


def is_confirming_forget() -> bool:
    return _confirming_forget_ssid is not None


def confirming_forget_ssid() -> str | None:
    return _confirming_forget_ssid


def request_forget(ssid: str) -> None:
    global _confirming_forget_ssid
    _confirming_forget_ssid = ssid


def cancel_forget() -> None:
    global _confirming_forget_ssid
    _confirming_forget_ssid = None


# ---------- hidden network entry: a real mode_stack tier ("connectivity_hidden_ssid") ----------
# Genuinely a different KIND of interaction than forget's plain y/n —
# typed text — so unlike forget above, this DOES need its own claim,
# same shape as the R4 wifi-passphrase-entry quartet above (typing/
# apply/cancel), just user-initiated (the "n" key while browsing)
# rather than daemon-initiated.

_hidden_ssid_input = ""
_entering_hidden_ssid = False


def is_entering_hidden_ssid() -> bool:
    return _entering_hidden_ssid


def start_hidden_ssid_entry() -> None:
    global _hidden_ssid_input, _entering_hidden_ssid
    _hidden_ssid_input = ""
    _entering_hidden_ssid = True


def cancel_hidden_ssid_entry() -> None:
    global _hidden_ssid_input, _entering_hidden_ssid
    _hidden_ssid_input = ""
    _entering_hidden_ssid = False


def handle_hidden_ssid_key(key: int) -> bool:
    """Same shape as handle_passphrase_key above — Enter isn't handled
    here (the caller checks for confirm before falling back to this for
    everything else)."""
    global _hidden_ssid_input
    if key == 27:  # Escape
        return False
    if key in (curses.KEY_BACKSPACE, 127, 8):
        _hidden_ssid_input = _hidden_ssid_input[:-1]
        return True
    if 32 <= key <= 126:
        _hidden_ssid_input += chr(key)
    return True


def apply_hidden_ssid() -> str | None:
    """The typed SSID (may be empty), or None if nothing's in progress
    — same "hand the raw answer to the caller, don't decide here
    whether it's usable" shape apply_passphrase() already has. Unlike
    apply_passphrase(), the caller (main.py) treats an empty submit as
    a silent cancel rather than a real answer — there's no legitimate
    reason to fire connect_hidden("") at a backend."""
    if not _entering_hidden_ssid:
        return None
    return _hidden_ssid_input


def _pending_blink_style(theme):
    """Blinks between selected and dim twice a second — no per-frame
    state needed, since time.time() is already a shared clock every
    frame reads the same way. Only reached while the main loop is
    running its fast (50ms) tick, via StatusWorker.has_pending().
    """
    blink_on = int(time.time() * 2) % 2 == 0
    if blink_on:
        return theme.get("selected", 0), curses.A_BOLD
    return theme.get("text", 0), curses.A_DIM


def _connection_dot(theme, connected):
    """A dedicated connected/disconnected indicator, separate from
    selection highlighting — '>' was overloaded (also used to mean
    "selected" elsewhere, e.g. the launcher's chip strip), so
    connection state gets its own glyph and color instead of sharing
    the row's selection color.
    """
    if connected:
        return "\u25cf", theme.get("accent", 0)  # ●, filled
    return "\u25cb", theme.get("text", 0) | curses.A_DIM  # ○, dim outline


def draw(stdscr, box, ctx, module_name):
    x, y, w, h = box
    theme = ctx.theme or {}

    is_active = module_name == ctx.active_module
    outer_color = theme.get("border_selected", 0) if is_active else theme.get("border", 0)
    draw_box_outline(stdscr, y, x, h, w, outer_color, title="Connectivity")

    # R4's two overlays take over the whole box while active, same
    # "full-box takeover, draw_centered_lines, then return" shape
    # sessions.py's own pending_confirm branch uses — checked via this
    # module's own state (is_entering_passphrase()/is_confirming_pairing()),
    # not ctx.pending_confirm, since these are driven by IwdAgent/
    # BluezAgent mailboxes, not the generic pending_confirm dict (see
    # this module's own docstring for why).
    if is_entering_passphrase():
        lines = [(f"Passphrase for {_entering_passphrase_ssid}", theme.get("accent", 0))]
        if _passphrase_waiting:
            text_color, attr = _pending_blink_style(theme)
            lines.append(("Connecting…", text_color | attr))
        elif _passphrase_error:
            lines.append((f"⚠ {_passphrase_error}", theme.get("urgent", 0)))
            lines.append((f"{key_label(ctx.config.keybinds['confirm'])} to retry, Esc to cancel", theme.get("text", 0) | curses.A_DIM))
        else:
            masked = "•" * len(_passphrase_input)
            lines.append((f"{masked}_", theme.get("text", 0)))
            lines.append((f"{key_label(ctx.config.keybinds['confirm'])} to connect, Esc to cancel", theme.get("text", 0) | curses.A_DIM))
        draw_centered_lines(stdscr, box, lines)
        return

    if is_confirming_pairing():
        request = _pairing_request
        lines = [(f"Pair with {request.device_name}?", theme.get("accent", 0))]
        if _pairing_waiting:
            text_color, attr = _pending_blink_style(theme)
            lines.append(("Pairing…", text_color | attr))
        elif _pairing_error:
            # No retry offered here (unlike the wifi passphrase flow
            # above) — the RequestConfirmation/RequestAuthorization
            # this was answering is already resolved as far as bluez
            # is concerned; a genuine retry means Enter on the device
            # row again, which drives a brand new Pair() attempt (and
            # a brand new RequestConfirmation, if bluez asks again).
            lines.append(("Press any key to dismiss", theme.get("text", 0) | curses.A_DIM))
        else:
            if request.kind == "confirm":
                lines.append((f"Confirm code: {request.passkey:06d}", theme.get("text", 0)))
            hint = f"{key_label(ctx.config.keybinds['confirm_yes'])}/{key_label(ctx.config.keybinds['confirm_no'])}"
            lines.append((hint, theme.get("text", 0)))
        draw_centered_lines(stdscr, box, lines)
        return

    # Forget-confirm (a browsing sub-state, not its own mode_stack tier
    # — see request_forget()'s own docstring) and hidden-network entry
    # (a real tier) each take over the whole box too, same shape as the
    # two R4 overlays above.
    if is_confirming_forget():
        lines = [(f"Forget {_confirming_forget_ssid}?", theme.get("accent", 0))]
        hint = f"{key_label(ctx.config.keybinds['confirm_yes'])}/{key_label(ctx.config.keybinds['confirm_no'])}"
        lines.append((hint, theme.get("text", 0)))
        draw_centered_lines(stdscr, box, lines)
        return

    if is_entering_hidden_ssid():
        lines = [("Hidden network name", theme.get("accent", 0))]
        lines.append((f"{_hidden_ssid_input}_", theme.get("text", 0)))
        lines.append((f"{key_label(ctx.config.keybinds['confirm'])} to connect, Esc to cancel", theme.get("text", 0) | curses.A_DIM))
        draw_centered_lines(stdscr, box, lines)
        return

    inner_w = max(w - 4, 0)

    for i, (kind, payload) in enumerate(_build_rows(ctx, h)):
        row = y + 1 + i
        if row >= y + h - 1:
            break

        if kind in ("wifi_header", "bt_header"):
            header_text = payload
            is_wifi = kind == "wifi_header"
            item_id = "connectivity:wifi:header" if is_wifi else "connectivity:bt:header"
            is_selected = item_id == ctx.selected_id
            # Selection now shows on the header TEXT's own color (same
            # convention every other row in this module already uses)
            # rather than a separate right-aligned "↻ Scan"/"↻ Discover"
            # label — Enter here enters browsing, it doesn't scan
            # anymore (see this module's own "level-2 browsing" section
            # docstring), so a static action-looking label would be
            # misleading. The real scanning/discovering state (main.py's
            # dedicated "wifi_scanning"/"bluetooth_discovering" Domains,
            # polling the daemon's own Scanning/Discovering property —
            # not StatusWorker.is_pending(), which only reflects the
            # brief fire-and-forget scan()/start_discovery() CALL, not
            # the real scan/discovery duration; see CLAUDE/NOTES/
            # design-decisions.md#connectivity-module-design) still
            # blinks on the right, independent of selection — a scan
            # triggered from inside browsing (the new `scan` key) stays
            # visible from level 1 too.
            header_color = theme.get("selected", 0) if is_selected else theme.get("accent", 0)
            scanning_domain = "wifi_scanning" if is_wifi else "bluetooth_discovering"
            scanning = ctx.status is not None and bool(ctx.status.get(scanning_domain))
            try:
                stdscr.addstr(row, x + 2, wc_truncate(header_text, max(inner_w, 0)), header_color | curses.A_BOLD)
                if scanning:
                    trigger_color, trigger_attr = _pending_blink_style(theme)
                    trigger_label = "Scanning…" if is_wifi else "Discovering…"
                    # Right-aligned within the box's own inner width —
                    # only drawn if there's room left after the header
                    # text, so a long SSID-count header ("WiFi [143]")
                    # on a narrow box can't make the two overlap.
                    trigger_x = x + w - 2 - len(trigger_label)
                    if trigger_x > x + 2 + len(header_text):
                        stdscr.addstr(row, trigger_x, trigger_label, trigger_color | trigger_attr)
            except curses.error:
                pass

        elif kind == "error":
            # No-silent-failure (VISION.md, R3): distinct from "empty"
            # both visually (urgent, same role power_menu's confirm=true
            # actions and sessions.py's DEL use) and in meaning — the
            # backend couldn't be reached at all, not "genuinely
            # nothing there".
            try:
                stdscr.addstr(row, x + 2, wc_truncate(f"⚠ {payload}", max(inner_w, 0)), theme.get("urgent", 0))
            except curses.error:
                pass

        elif kind == "empty_slot":
            # An unfilled slot within a fixed-visible_slots section
            # (see windowed_list.section_rows) — same dim styling
            # media.py's own "empty_slot" handling uses.
            try:
                stdscr.addstr(row, x + 2, wc_truncate(payload, max(inner_w, 0)), theme.get("text", 0) | curses.A_DIM)
            except curses.error:
                pass

        elif kind == "wifi_item":
            network = payload
            is_selected = f"connectivity:wifi:{network.ssid}" == ctx.selected_id
            # Level 1 (not browsing this section): these rows aren't
            # Tab-reachable — Enter the header first (see this module's
            # own "level-2 browsing" section). Drawn visibly dimmer so
            # that's obvious at a glance, not just discoverable by
            # trying to Tab onto one and nothing happening.
            browsable = _browsing_section == "wifi"
            bars = _signal_bars(network.signal)
            # "[new] " prefix, not a " (new)" suffix \u2014 see
            # CLAUDE/NOTES/design-decisions.md#connectivity-module-design.
            display_name = ("[new] " if not network.known else "") + network.ssid
            name_width = max(inner_w - 3 - len(bars), 0)
            # network.ssid is real, uncontrolled external data — people
            # genuinely do put emoji in WiFi network names. A plain
            # str.ljust() pads by CHARACTER count, not display width, so
            # a wide/VS16 SSID would under-pad and throw off the signal
            # bars' position below (drawn past name_part's own real
            # rendered width, not its len()) — truncate width-aware,
            # then pad the remainder manually instead.
            truncated_name = wc_truncate(display_name, name_width)
            name = truncated_name + " " * max(name_width - display_width(truncated_name), 0)

            pending = ctx.status is not None and ctx.status.is_pending("wifi", network.ssid)
            if pending:
                text_color, attr = _pending_blink_style(theme)
                dot, dot_color = ("\u25cf" if network.connected else "\u25cb"), (text_color | attr)
            else:
                dot, dot_color = _connection_dot(theme, network.connected)
                if network.connected:
                    text_color = theme.get("selected", 0) if is_selected else theme.get("text", 0)
                    attr = curses.A_BOLD
                else:
                    text_color = theme.get("text", 0)
                    attr = curses.A_BOLD if is_selected else curses.A_DIM
                if not browsable:
                    # Drop any BOLD and force DIM so the whole row
                    # reads as inert rather than a normal selectable
                    # item; connected/disconnected state (the ●/○ dot)
                    # stays legible at a glance either way — only its
                    # own color dims, not its shape.
                    attr = (attr & ~curses.A_BOLD) | curses.A_DIM
                    dot_color |= curses.A_DIM

            # The signal bars get their OWN color, independent of the
            # name's selection/dim styling above — see
            # CLAUDE/NOTES/design-decisions.md#connectivity-module-design.
            name_part = f" {name} "
            bars_color = theme.get("accent", 0) if network.connected else theme.get("text", 0)
            if not browsable:
                bars_color |= curses.A_DIM
            try:
                stdscr.addstr(row, x + 2, dot, dot_color)
                stdscr.addstr(row, x + 3, name_part, text_color | attr)
                stdscr.addstr(row, x + 3 + display_width(name_part), bars, bars_color)
            except curses.error:
                pass

        elif kind == "bt_item":
            device = payload
            is_selected = f"connectivity:bt:{device.id}" == ctx.selected_id
            # See wifi_item's own comment above — same reasoning.
            browsable = _browsing_section == "bluetooth"
            battery = f" {device.battery}%" if device.battery is not None else ""
            # "[new] " prefix \u2014 same reasoning as wifi_item's own
            # display_name above.
            display_name = ("[new] " if not device.paired else "") + device.name

            pending = ctx.status is not None and ctx.status.is_pending("bluetooth", device.id)
            if pending:
                text_color, attr = _pending_blink_style(theme)
                dot, dot_color = ("\u25cf" if device.connected else "\u25cb"), (text_color | attr)
            else:
                dot, dot_color = _connection_dot(theme, device.connected)
                if device.connected:
                    text_color = theme.get("selected", 0) if is_selected else theme.get("text", 0)
                    attr = curses.A_BOLD
                else:
                    text_color = theme.get("text", 0)
                    attr = curses.A_BOLD if is_selected else curses.A_DIM
                if not browsable:
                    attr = (attr & ~curses.A_BOLD) | curses.A_DIM
                    dot_color |= curses.A_DIM

            # device.name is real, uncontrolled external data too (a
            # real Bluetooth device's advertised name) — same risk as
            # network.ssid above.
            rest = wc_truncate(f" {display_name}{battery}", max(inner_w - 1, 0))
            try:
                stdscr.addstr(row, x + 2, dot, dot_color)
                stdscr.addstr(row, x + 3, rest, text_color | attr)
            except curses.error:
                pass


# ---------- hover-preview info (preview.py's NavItem.preview_text mechanism) ----------
# Same mechanism sysmon.py's diagnostics breakdown uses (see its own
# _diagnostics_preview_text) — a selected NavItem's preview_text
# replaces preview.py's normal window-preview content while that item
# is selected. The row itself only has room for what's needed to scan
# the list at a glance (signal bars, connected dot, [new] marker); this
# is everything else the backends fetch for free (see model.py's own
# field-by-field comments for which of these cost an extra D-Bus round
# trip and which don't).

_WIFI_SECURITY_LABELS = {
    "open": "Open (no security)",
    "psk": "WPA/WPA2-Personal",
    "8021x": "Enterprise (802.1x)",
    # "wep"/"sae"/"owe" — concepts iwd's own Network.Type has no
    # equivalent of, only reachable via the NetworkManager backend's
    # own classify_security() (see networkmanager.py's own docstring).
    "wep": "WEP",
    "sae": "WPA3-Personal (SAE)",
    "owe": "Enhanced Open (OWE)",
}


def _security_label(security):
    if security is None:
        return "unknown"
    # Falls back to the raw backend string for any Type/security value
    # not in the small known set above, rather than guessing at a
    # label that might be wrong — same "degrade honestly, don't
    # overclaim" instinct as everywhere else in this codebase.
    return _WIFI_SECURITY_LABELS.get(security, security)


def _format_timestamp(iso_string):
    """iwd's KnownNetwork.LastConnectedTime, an ISO8601 UTC string, to
    a plain local-time "YYYY-MM-DD HH:MM" — the raw "2026-08-10T05:35:00Z"
    form is technically correct but not something you read at a glance
    in a hover preview. Falls back to the raw string on anything that
    doesn't parse instead of raising — a display nicety must never be
    able to crash the render loop over a format iwd's own docs didn't
    quite prepare for.
    """
    try:
        return datetime.fromisoformat(iso_string.replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return iso_string


def _yes_no(value):
    return "yes" if value else "no"


def _action_progress_line(status, domain_name, key, connected, theme):
    """One extra preview line reflecting a live connect/disconnect
    attempt for THIS specific item — "Connecting…"/"Disconnecting…"
    while in flight, or the real failure message once it's done, via
    get_action_error_for() rather than get_action_error() (see
    status_worker.py's own docstring for why: many networks/devices
    share one "wifi"/"bluetooth" domain, so a plain per-domain error
    would let an old network's stale error bleed onto whatever's
    selected now). None when there's nothing currently worth reporting.
    """
    if status is None:
        return None
    if status.is_pending(domain_name, key):
        return ("Disconnecting…" if connected else "Connecting…", theme.get("selected", 0))
    action_error = status.get_action_error_for(domain_name, key)
    if action_error:
        return (f"⚠ {action_error}", theme.get("urgent", 0))
    return None


def _header_enter_hint_footer(theme, cfg, section_label):
    """The red "how do I get in" footer for a header's own preview —
    the level-1 counterpart to _browsing_hint_footer's level-2 "what
    can I do in here" one. A separate boxed-off preview_footer (see
    NavItem.preview_footer's own docstring), not just another line of
    preview_text, so it visually stands apart from the plain network/
    device list above it — a raw discoverability nudge, not a status
    readout. Enter here isn't obvious from the row itself anymore since
    it stopped triggering a scan directly (see this module's own
    "level-2 browsing" section) — this is what replaces that lost
    affordance.
    """
    return [(f"[{key_label(cfg.keybinds['confirm'])}] Browse {section_label}", theme.get("urgent", 0))]


def _adapter_info_table(adapter, scanning):
    """The WiFi header's own device-info table (NavItem.preview_table)
    — impala's own bordered "Device" panel, drawn as a real table box
    by preview.py rather than dumped as plain preview_text lines (a
    first pass did exactly that; found live it read as just more text,
    not the distinct "info panel" impala's own screenshot shows — this
    replaced it). `adapter` is the raw ctx.status.get("wifi_adapter")
    value; None (no wifi adapter found at all) means no table at all,
    same "genuinely nothing there" tolerance WifiBackend.
    get_adapter_info()'s own contract documents. `scanning` folds in
    the existing "wifi_scanning" domain (Station.Scanning, real ground
    truth — see draw()'s own scanning-blink comment) rather than
    duplicating it as a new AdapterInfo field. Every column is
    independently optional (see AdapterInfo's own docstring for why
    model/vendor/supported_modes are None on NetworkManager
    specifically) — only fields that actually have something to say
    become a column, so the table narrows to whatever's real instead
    of showing empty cells.
    """
    if adapter is None:
        return None
    columns = []
    if adapter.name:
        columns.append(("Name", adapter.name))
    if adapter.mode:
        columns.append(("Mode", adapter.mode))
    if adapter.powered is not None:
        columns.append(("Powered", _yes_no(adapter.powered)))
    if adapter.state:
        columns.append(("State", adapter.state))
    columns.append(("Scanning", _yes_no(scanning)))
    if adapter.address:
        columns.append(("Address", adapter.address))
    if adapter.model:
        columns.append(("Model", adapter.model))
    if adapter.vendor:
        columns.append(("Vendor", adapter.vendor))
    if adapter.supported_modes:
        columns.append(("Modes", ", ".join(adapter.supported_modes)))
    return columns


def _wifi_scan_preview_text(networks, error, theme):
    """The hover-preview for the WiFi header row itself (name kept from
    when Enter here triggered a scan directly — it now enters browsing
    instead, see this module's own "level-2 browsing" section) — the
    FULL list of currently available networks, not just the
    connectivity_visible_slots window the box's scrollable section
    shows. Adapter/device info lives in a separate preview_table now
    (see _adapter_info_table above), not folded in here. Same
    None-vs-[] error handling as _build_rows' own — `networks` is the
    raw ctx.wifi_networks (may be None), not the "or []"-normalized
    local nav_items() otherwise uses, so a real poll failure still
    shows as an error here too, not "no networks".
    """
    if networks is None and error:
        return [(f"⚠ {error}", theme.get("urgent", 0))]
    if not networks:
        return [("No networks found", theme.get("text", 0) | curses.A_DIM)]
    lines = [(f"Available networks [{len(networks)}]", theme.get("accent", 0))]
    for network in networks:
        dot = "●" if network.connected else "○"
        prefix = "[new] " if not network.known else ""
        signal = f" {network.signal}%" if network.signal is not None else ""
        color = theme.get("accent", 0) if network.connected else theme.get("text", 0)
        lines.append((f"{dot} {prefix}{network.ssid}{signal}", color))
    return lines


def _bt_discover_preview_text(devices, error, theme):
    """Same reasoning as _wifi_scan_preview_text above, for the
    Bluetooth header row and ctx.bluetooth_devices."""
    if devices is None and error:
        return [(f"⚠ {error}", theme.get("urgent", 0))]
    if not devices:
        return [("No devices found", theme.get("text", 0) | curses.A_DIM)]
    lines = [(f"Available devices [{len(devices)}]", theme.get("accent", 0))]
    for device in devices:
        dot = "●" if device.connected else "○"
        prefix = "[new] " if not device.paired else ""
        battery = f" {device.battery}%" if device.battery is not None else ""
        color = theme.get("accent", 0) if device.connected else theme.get("text", 0)
        lines.append((f"{dot} {prefix}{device.name}{battery}", color))
    return lines


def _browsing_hint_footer(theme, cfg, section, connected, known=True):
    """The red "available keys" footer for a network/device's own
    preview — a separate boxed-off preview_footer (see NavItem.
    preview_footer's own docstring), not just another line of
    preview_text. Individual rows are only ever selectable while
    browsing (see this module's own "level-2 browsing" section), so
    this is always relevant whenever it's reached, no is_browsing()
    check needed.

    [Enter] Connect/Disconnect is always shown, both sections, worded
    from `connected` — it's what toggle_wifi()/toggle_bluetooth()
    actually do on Enter, and the hint should say so unconditionally
    rather than only for the wifi-only extra keys below it.

    wifi_forget/wifi_connect_hidden/wifi_power_toggle only ever DO
    anything while section == "wifi" (see main.py's handle_
    connectivity_browsing — bluetooth rows have no equivalent), so
    they're only listed there. [D] Forget specifically is further
    narrowed to `known` networks only — forgetting an unknown network
    (never actually saved, nothing to delete) isn't a real action, so
    offering the key there would be misleading, not just redundant.
    """
    urgent = theme.get("urgent", 0)
    connect_hint = f"[{key_label(cfg.keybinds['confirm'])}] {'Disconnect' if connected else 'Connect'}"
    if section != "wifi":
        return [(f"{connect_hint}   [{key_label(cfg.keybinds['scan'])}] Scan   [Esc] Back", urgent)]
    forget_hint = f"   [{key_label(cfg.keybinds['wifi_forget'])}] Forget" if known else ""
    return [
        (f"{connect_hint}   [{key_label(cfg.keybinds['scan'])}] Scan{forget_hint}", urgent),
        (f"[{key_label(cfg.keybinds['wifi_connect_hidden'])}] Hidden   [{key_label(cfg.keybinds['wifi_power_toggle'])}] Power   [Esc] Back", urgent),
    ]


def _wifi_preview_text(network, theme, status):
    lines = [(network.ssid, theme.get("accent", 0))]
    signal_text = f"{network.signal}%" if network.signal is not None else "unknown"
    lines.append((f"Signal: {signal_text}", theme.get("text", 0)))
    lines.append((f"Security: {_security_label(network.security)}", theme.get("text", 0)))
    lines.append((f"Known: {_yes_no(network.known)}", theme.get("text", 0)))
    if network.known:
        lines.append((f"Auto-connect: {_yes_no(network.auto_connect)}", theme.get("text", 0)))
        lines.append((f"Hidden: {_yes_no(network.hidden)}", theme.get("text", 0)))
        if network.last_connected:
            lines.append((f"Last connected: {_format_timestamp(network.last_connected)}", theme.get("text", 0)))
    lines.append((f"Connected: {_yes_no(network.connected)}", theme.get("accent", 0) if network.connected else theme.get("text", 0)))
    progress = _action_progress_line(status, "wifi", network.ssid, network.connected, theme)
    if progress is not None:
        lines.append(progress)
    return lines


def _bt_preview_text(device, theme, status):
    lines = [(device.name, theme.get("accent", 0))]
    lines.append((f"Address: {device.id}", theme.get("text", 0)))
    if device.address_type:
        lines.append((f"Address type: {device.address_type}", theme.get("text", 0)))
    if device.icon:
        lines.append((f"Icon: {device.icon}", theme.get("text", 0)))
    lines.append((f"Paired: {_yes_no(device.paired)}", theme.get("text", 0)))
    lines.append((f"Trusted: {_yes_no(device.trusted)}", theme.get("text", 0)))
    lines.append((f"Blocked: {_yes_no(device.blocked)}", theme.get("urgent", 0) if device.blocked else theme.get("text", 0)))
    lines.append((f"Connected: {_yes_no(device.connected)}", theme.get("accent", 0) if device.connected else theme.get("text", 0)))
    if device.battery is not None:
        lines.append((f"Battery: {device.battery}%", theme.get("text", 0)))
    if device.rssi is not None:
        lines.append((f"RSSI: {device.rssi} dBm", theme.get("text", 0)))
    progress = _action_progress_line(status, "bluetooth", device.id, device.connected, theme)
    if progress is not None:
        lines.append(progress)
    return lines


def _wifi_row_nav_item(network, box, row, theme, status, cfg, adapter_info, scanning) -> NavItem:
    x, y, w, h = box
    return NavItem(
        id=f"connectivity:wifi:{network.ssid}",
        rect=(x + 1, row, w - 2, 1),
        focus_target=network.ssid,
        target_kind="wifi_network",
        preview_text=_wifi_preview_text(network, theme, status),
        preview_footer=_browsing_hint_footer(theme, cfg, "wifi", network.connected, network.known),
        # Same Device table the header shows at level 1 — d/n/o
        # (forget/hidden/power) are only ever usable from INSIDE
        # browsing (see this module's own "level-2 browsing" section),
        # so seeing the device's own current state (Powered/Scanning
        # especially) right where those controls actually live matters
        # more here than at the header, not less.
        preview_table_title="Device",
        preview_table=_adapter_info_table(adapter_info, scanning),
    )


def _bt_row_nav_item(device, box, row, theme, status, cfg) -> NavItem:
    x, y, w, h = box
    return NavItem(
        id=f"connectivity:bt:{device.id}",
        rect=(x + 1, row, w - 2, 1),
        focus_target=device.id,
        target_kind="bluetooth_device",
        preview_text=_bt_preview_text(device, theme, status),
        preview_footer=_browsing_hint_footer(theme, cfg, "bluetooth", device.connected),
    )


def nav_items(box, ctx, module_name) -> list[NavItem]:
    """Level 1 (not browsing): just the two headers — WiFi/Bluetooth
    are cross-module-Tab-reachable, individual networks/devices are
    not. Level 2 (browsing one section): just THAT section's own real
    (drawn, windowed) rows, exactly as ctx.config.connectivity_
    visible_slots itself lays them out (see _build_rows), plus — for a
    section that's actually scrolled (more real items than fit) — one
    extra "peek" NavItem just before and/or after the window, reusing
    the boundary row's own y (same shape as media.py's own nav_items()
    — see _section_nav_indices' own docstring for why a peek item is
    never something a user can see selected-but-undrawn). See this
    module's own "level-2 browsing" section docstring for why this is
    a real mode_stack claim (main.py's handle_connectivity_browsing)
    rather than the orthogonal two-level-expand mechanism sessions.py/
    media.py/sysmon.py use — draw() keeps rendering BOTH sections in
    full regardless of browsing state, only what's reachable here
    changes.
    """
    x, y, w, h = box
    theme = ctx.theme or {}
    cfg = ctx.config
    wifi_networks = ctx.wifi_networks or []
    bluetooth_devices = ctx.bluetooth_devices or []
    # Computed once here, not per-row inside the loop below — needed by
    # BOTH the header (level 1) and every wifi row (level 2, see
    # _wifi_row_nav_item's own comment on why the Device table needs to
    # survive into browsing) for the exact same preview_table content.
    adapter_info = ctx.status.get("wifi_adapter") if ctx.status is not None else None
    scanning = bool(ctx.status.get("wifi_scanning")) if ctx.status is not None else False

    wifi_header_item = None
    bt_header_item = None
    wifi_items: list[NavItem] = []
    wifi_rows: list[int] = []  # one entry per REAL "wifi_item" row drawn, in order
    bt_items: list[NavItem] = []
    bt_rows: list[int] = []

    for i, (kind, payload) in enumerate(_build_rows(ctx, h)):
        row = y + 1 + i
        if row >= y + h - 1:
            break

        if kind == "wifi_header":
            wifi_header_item = NavItem(
                id="connectivity:wifi:header", rect=(x + 1, row, w - 2, 1), target_kind="wifi_browse",
                preview_text=_wifi_scan_preview_text(ctx.wifi_networks, ctx.wifi_error, theme),
                preview_footer=_header_enter_hint_footer(theme, cfg, "networks"),
                preview_table_title="Device",
                preview_table=_adapter_info_table(adapter_info, scanning),
            )
        elif kind == "bt_header":
            bt_header_item = NavItem(
                id="connectivity:bt:header", rect=(x + 1, row, w - 2, 1), target_kind="bluetooth_browse",
                preview_text=_bt_discover_preview_text(ctx.bluetooth_devices, ctx.bluetooth_error, theme),
                preview_footer=_header_enter_hint_footer(theme, cfg, "devices"),
            )
        elif kind == "wifi_item":
            wifi_items.append(_wifi_row_nav_item(payload, box, row, theme, ctx.status, cfg, adapter_info, scanning))
            wifi_rows.append(row)
        elif kind == "bt_item":
            bt_items.append(_bt_row_nav_item(payload, box, row, theme, ctx.status, cfg))
            bt_rows.append(row)

    visible_slots = ctx.config.connectivity_visible_slots

    selected_wifi_index = _selected_wifi_index(wifi_networks, ctx.selected_id)
    before_i, after_i = _section_nav_indices(len(wifi_networks), selected_wifi_index, visible_slots=visible_slots)
    if before_i is not None and wifi_rows:
        wifi_items = [_wifi_row_nav_item(wifi_networks[before_i], box, wifi_rows[0], theme, ctx.status, cfg, adapter_info, scanning)] + wifi_items
    if after_i is not None and wifi_rows:
        wifi_items = wifi_items + [_wifi_row_nav_item(wifi_networks[after_i], box, wifi_rows[-1], theme, ctx.status, cfg, adapter_info, scanning)]

    selected_bt_index = _selected_bt_index(bluetooth_devices, ctx.selected_id)
    before_i, after_i = _section_nav_indices(len(bluetooth_devices), selected_bt_index, visible_slots=visible_slots)
    if before_i is not None and bt_rows:
        bt_items = [_bt_row_nav_item(bluetooth_devices[before_i], box, bt_rows[0], theme, ctx.status, cfg)] + bt_items
    if after_i is not None and bt_rows:
        bt_items = bt_items + [_bt_row_nav_item(bluetooth_devices[after_i], box, bt_rows[-1], theme, ctx.status, cfg)]

    if _browsing_section == "wifi":
        return wifi_items
    if _browsing_section == "bluetooth":
        return bt_items

    items = []
    if wifi_header_item is not None:
        items.append(wifi_header_item)
    if bt_header_item is not None:
        items.append(bt_header_item)
    return items


def handle_wifi_header(ctx, item, cfg):
    """Enter on the WiFi header enters level-2 browsing (see this
    module's own "level-2 browsing" section) — main.py notices
    is_browsing() right after this returns and pushes the
    "connectivity_browsing" mode_stack tier on its own behalf, same
    idiom as sessions.py's start_naming()/is_naming(). Jumps straight
    to the first real network (via ActionContext.reselect_item_id +
    main.py's existing do_apply_reselect()) rather than leaving
    selection sitting on the now-unreachable header id; stays on the
    header id when the section is genuinely empty — still valid, e.g.
    to immediately press the scan key.
    """
    start_browsing("wifi")
    networks = ctx.status.get("wifi") or []
    if networks:
        ctx.reselect_item_id = f"connectivity:wifi:{networks[0].ssid}"
    return False, None


def handle_bt_header(ctx, item, cfg):
    """Same reasoning as handle_wifi_header above, for Bluetooth."""
    start_browsing("bluetooth")
    devices = ctx.status.get("bluetooth") or []
    if devices:
        ctx.reselect_item_id = f"connectivity:bt:{devices[0].id}"
    return False, None


HANDLERS = {
    "wifi_browse": handle_wifi_header,
    "bluetooth_browse": handle_bt_header,
}
