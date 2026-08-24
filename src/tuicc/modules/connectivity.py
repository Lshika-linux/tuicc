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
from tuicc.render_utils import draw_box_outline, draw_centered_lines, draw_table_box, display_width, wc_truncate
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


def required_fh(cfg) -> int:
    """How many rows this box needs: WiFi header + visible_slots WiFi
    rows + a spacer + Bluetooth header + visible_slots Bluetooth rows,
    plus 2 for the top/bottom border — 2*visible_slots + 5. Constant
    regardless of how many real networks/devices actually exist (the
    windowed-list section always renders exactly visible_slots rows,
    real or "[empty]" placeholder), so this only depends on the
    CONFIG value, never runtime state. Used by render.py's
    apply_auto_fh() for boxes with fh_auto set.
    """
    return 2 * cfg.connectivity_visible_slots + 5


def _connected_first(devices):
    """Neither WiFi nor Bluetooth has an inherent relevance ordering
    guaranteed across every backend — this client-side sort is what
    actually provides it, applied uniformly to both rather than relied
    on being "free" from any one daemon. Originally written Bluetooth-
    only: bluez's GetManagedObjects() walk (get_devices()) returns
    devices in whatever order the D-Bus object tree happens to
    iterate, arbitrary from tuicc's own point of view, while iwd's
    Station.GetOrderedNetworks() (IwdBackend.get_networks()'s own
    D-Bus call) DOES hand back the connected network sorted first on
    its own — confirmed live, no client-side sort needed there, which
    is why this used to be called only for ctx.bluetooth_devices.
    Found live testing the NetworkManager backend for real: its own
    get_networks() (a manual per-BSSID walk + merge, no equivalent
    "ordered" D-Bus call to lean on — see networkmanager.py's own
    module docstring for why its whole approach differs from iwd's)
    carries no such guarantee either, so a connected network could
    just as easily land third in a 3-slot view as bluez's own devices
    once did (found live, Rafi's own report, the original reason this
    function exists at all) — now applied to WiFi too, harmless on iwd
    since sorting an already-sorted list changes nothing. `None` stays
    `None` (a poll error, nothing to sort); Python's own sorted() is
    stable, so items tied on `connected` keep their original relative
    order rather than being shuffled for no reason.
    """
    if devices is None:
        return None
    return sorted(devices, key=lambda d: not d.connected)


def _build_rows(ctx, box_h):
    """One row per line the box will render, in order — the single
    source of truth draw() and nav_items() both walk, so their row
    indices can never drift apart. WiFi/Bluetooth each get a
    fixed-slot-plus-scroll window (windowed_list.py, shared with
    media.py/sysmon.py) — see CLAUDE/NOTES/design-decisions.md
    #connectivity-module-design. box_h is accepted for signature
    symmetry with media.py's _build_rows but unused here.
    """
    wifi_networks = _connected_first(ctx.wifi_networks)
    bluetooth_devices = _connected_first(ctx.bluetooth_devices)
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
_passphrase_visible = False  # show-hide toggle (Tab, see toggle_passphrase_visibility())


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
    global _entering_passphrase_ssid, _passphrase_input, _passphrase_waiting, _passphrase_error, _passphrase_visible
    _entering_passphrase_ssid = ssid
    _passphrase_input = ""
    _passphrase_waiting = False
    _passphrase_error = None
    _passphrase_visible = False


def cancel_passphrase_entry() -> None:
    """Discards the whole flow without producing a further answer —
    called on the user pressing Escape (only reachable while typing or
    looking at an error, never while _passphrase_waiting — see
    main.py's own tier) and on main.py noticing the daemon itself
    already cancelled the request (IwdAgent.mailbox.has_pending() went
    False on its own — see agent_mailbox.py's module docstring)."""
    global _entering_passphrase_ssid, _passphrase_input, _passphrase_waiting, _passphrase_error, _passphrase_visible
    _entering_passphrase_ssid = None
    _passphrase_input = ""
    _passphrase_waiting = False
    _passphrase_error = None
    _passphrase_visible = False


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


def toggle_passphrase_visibility() -> None:
    """Bound to wifi_passphrase_visibility_toggle (default Tab — see
    that keybind's own comment in defaults/config.toml for why Tab and
    not, say, a Ctrl+ combo: this overlay is a full mode_stack tier
    that swallows every key, and any printable character typed here
    becomes part of the passphrase itself via handle_passphrase_key
    above, so the toggle can't be a plain letter without also typing
    itself into the field. Global_shortcuts fire before module-local
    keys ever see them (main.py's loop order — see GUIDE.md), so a
    Ctrl+<letter> pick would risk silently triggering an unrelated
    power_menu action (e.g. Ctrl+R already means Reboot) instead of
    ever reaching here — Tab carries no such risk and, being a fully
    modal overlay, doesn't compete with Tab's usual next-item meaning
    either."""
    global _passphrase_visible
    _passphrase_visible = not _passphrase_visible


def is_passphrase_visible() -> bool:
    return _passphrase_visible


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


# ---------- header action flash: instant feedback for P/S/A ----------
# A brief accent flash on just the ONE bracket group ([PWR ...]/
# [SCAN ...]/[PAIRABLE ...]) whose key was actually just pressed —
# Rafi's own ask, found live: the header's own status legend (see
# _wifi_header_status_segments/_bt_header_status_segments) shows real
# state, but nothing confirmed a keypress itself LANDED the instant you
# pressed it — StatusWorker.is_pending() alone isn't a fit here (its
# own window is a single D-Bus round trip, often too brief to actually
# see even at the app's own idle 300ms redraw cadence — see scan's own
# similar timing story, this same session). A dedicated, real minimum-
# duration flash instead: main.py calls flash_header_action() directly
# at each request_action() call site (scan/power/pairable), draw()
# checks it via _is_header_action_flashing() when building that one
# group's own segments.
_flash_until = {}  # {(section, action): monotonic deadline}
_FLASH_DURATION_SECONDS = 0.4  # long enough to survive the idle 300ms redraw cadence with room to spare


def flash_header_action(section: str, action: str) -> None:
    """action is "power"/"scan"/"pairable" — matches the StatusWorker
    pending_key each request_action() call already uses for that same
    action, kept as the same vocabulary rather than inventing a second
    one.
    """
    _flash_until[(section, action)] = time.monotonic() + _FLASH_DURATION_SECONDS


def _is_header_action_flashing(section: str, action: str) -> bool:
    deadline = _flash_until.get((section, action))
    return deadline is not None and time.monotonic() < deadline


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


def _status_dot(theme, value):
    """Same shape as _connection_dot above, generalized to any boolean
    adapter-level state (Powered/Scanning/Pairable) rather than just
    connected/disconnected. `None` (adapter not polled yet) renders as
    the same dim outline dot a real `False` would — same "unknown reads
    as off, never wrongly claimed on" tolerance _yes_no() already uses
    for exactly this reason elsewhere in this module.
    """
    if value:
        return "\u25cf", theme.get("accent", 0) | curses.A_BOLD  # ●, filled
    return "\u25cb", theme.get("text", 0) | curses.A_DIM  # ○, dim outline


def _scanning_dot(theme, scanning):
    """Same shape as _status_dot above, but the filled state BLINKS
    while a scan/discovery is actually in progress this exact frame,
    via the same _pending_blink_style() every other live-action
    indicator in this module already uses — replaces the old separate
    right-aligned "Scanning…"/"Discovering…" text entirely
    (found live this same session: keeping both was redundant, and the
    two segments now sit right next to the label with no spare room
    for a second, separately-positioned indicator anyway).
    """
    if not scanning:
        return "\u25cb", theme.get("text", 0) | curses.A_DIM  # ○, dim outline
    color, attr = _pending_blink_style(theme)
    return "\u25cf", color | attr  # ●, blinking


def _header_status_dim(theme, segments):
    """Overrides every segment's own color to one flat, dim text
    color — keeps each segment's TEXT (including which dot glyph,
    ●/○) exactly as computed, only recolors it. Applied whenever the
    header row itself isn't the current selection (level-1 navigation
    — Rafi's own call: the whole legend should read as quiet/inert
    background info until you've actually navigated onto this row,
    same "dim until relevant" instinct sessions.py's own LOAD/SAVE/
    DEL/NAME and media.py's own transport glyphs already apply, just
    keyed off row selection here rather than an expanded/collapsed
    state).
    """
    dim = theme.get("text", 0) | curses.A_DIM
    return [(text, dim) for text, _color in segments]


def _flash_group(theme, segments, flashing):
    """Overrides one bracket group's own segments to flat accent
    (bold) while `flashing` — same "keep the text, override the
    color" shape as _header_status_dim above, just applied per-group
    instead of to the whole row. See flash_header_action()'s own
    docstring for why this exists (a real, minimum-duration keypress
    confirmation, not a repurposed StatusWorker.is_pending() flicker).
    """
    if not flashing:
        return segments
    flash_color = theme.get("accent", 0) | curses.A_BOLD
    return [(text, flash_color) for text, _color in segments]


def _wifi_header_status_segments(theme, powered, scanning, is_controllable, power_flashing=False, scan_flashing=False):
    """(text, color) segments for the WiFi header row's own compact
    "[P]WR ● [S]CAN ○" status legend — real Powered/Scanning state,
    plus a built-in key legend: the accent-colored letter in each word
    IS the actual keybind for that action (wifi_power_toggle resolves
    to "p", scan to "s" — see defaults/config.toml's own comments), so
    this one row does the job _header_enter_hint_footer's separate
    preview-only strip used to do alone, but lives in the module's own
    box regardless of whether preview exists in the current layout at
    all — the first real instance of CLAUDE/NOTES/design-decisions.md
    #module-self-sufficiency-vs-preview's own still-mostly-unbuilt
    mechanism #2 (a module branching what it draws in its OWN box).
    Drawn as a sequence of separately-colored segments (curses can't
    color part of one addstr() call) — draw()'s own caller walks this
    list left to right, advancing x by each segment's own display_width.

    is_controllable=False flattens the whole thing to one dim color
    via _header_status_dim — see that function's own docstring. True
    only once you've actually entered level-2 browsing for THIS
    section (see draw()'s own comment) — not merely having the
    collapsed header row selected/hovered at level 1, where P/S/A
    can't actually be pressed yet.

    power_flashing/scan_flashing each briefly flash JUST that one
    bracket group accent — built as its own small segment list first
    (_flash_group applied before the groups are ever concatenated),
    so pressing P never flashes SCAN too. Built PER GROUP, not as one
    combined "flash everything" flag, deliberately mirroring
    _header_status_dim's own "override color, keep text" shape one
    level more granular.
    """
    text_color = theme.get("text", 0)
    accent = theme.get("accent", 0) | curses.A_BOLD
    pwr_group = _flash_group(theme, [
        ("[", text_color),
        ("P", accent),
        ("WR ", text_color),
        _status_dot(theme, powered),
        ("]", text_color),
    ], power_flashing)
    scan_group = _flash_group(theme, [
        ("[", text_color),
        ("S", accent),
        ("CAN ", text_color),
        _scanning_dot(theme, scanning),
        ("]", text_color),
    ], scan_flashing)
    segments = [("  ", text_color), *pwr_group, (" ", text_color), *scan_group]
    return segments if is_controllable else _header_status_dim(theme, segments)


def _bt_header_status_segments(
    theme, powered, scanning, pairable, is_controllable,
    power_flashing=False, scan_flashing=False, pairable_flashing=False,
):
    """Same reasoning as _wifi_header_status_segments above, plus a
    third "P[A]IRABLE" segment/flash for bt_pairable_toggle's own key.
    The accent letter sits at "PAIRABLE"'s own SECOND character
    (matching where the "a" keybind actually falls, not the word's
    first letter the way PWR/SCAN's own accent letters do) — Rafi's
    own explicit call, not a mistake: "obarvi... A v PAIRABLE na
    accent".
    """
    text_color = theme.get("text", 0)
    accent = theme.get("accent", 0) | curses.A_BOLD
    pwr_group = _flash_group(theme, [
        ("[", text_color),
        ("P", accent),
        ("WR ", text_color),
        _status_dot(theme, powered),
        ("]", text_color),
    ], power_flashing)
    scan_group = _flash_group(theme, [
        ("[", text_color),
        ("S", accent),
        ("CAN ", text_color),
        _scanning_dot(theme, scanning),
        ("]", text_color),
    ], scan_flashing)
    pairable_group = _flash_group(theme, [
        ("[", text_color),
        ("P", text_color),
        ("A", accent),
        ("IRABLE ", text_color),
        _status_dot(theme, pairable),
        ("]", text_color),
    ], pairable_flashing)
    segments = [("  ", text_color), *pwr_group, (" ", text_color), *scan_group, (" ", text_color), *pairable_group]
    return segments if is_controllable else _header_status_dim(theme, segments)


def _draw_segments(stdscr, row, x, segments, max_w):
    """Draws a sequence of (text, color) segments left to right,
    starting at (row, x), advancing x by each segment's own
    display_width — curses can't color part of a single addstr() call,
    so any inline multi-color text in this module (the header row's
    own "[P]WR ● [S]CAN ○" legend) goes through this instead of one
    big string. Stops once max_w is exhausted rather than overflowing
    past it — each segment is wc_truncate'd to whatever room is left,
    so a too-narrow box clips cleanly mid-segment instead of just
    stopping abruptly between segments, same "degrade, don't corrupt"
    tolerance every other primitive here has for a too-small box.
    """
    cx = x
    remaining = max_w
    for text, color in segments:
        if remaining <= 0:
            break
        clipped = wc_truncate(text, remaining)
        if not clipped:
            continue
        try:
            stdscr.addstr(row, cx, clipped, color)
        except curses.error:
            pass
        advance = display_width(clipped)
        cx += advance
        remaining -= advance


def _preview_available(cfg) -> bool:
    """Whether the "preview" box exists anywhere in the CURRENT
    layout — the check CLAUDE/NOTES/design-decisions.md
    #module-self-sufficiency-vs-preview's own "mechanism #2" names but
    never actually built until now: "a module can check any(b.name ==
    'preview' for b in ctx.config.layout.boxes) and branch how much it
    draws in its OWN box accordingly." First real consumer is
    _forget_hidden_hint_segments' own border-line fallback below —
    Rafi's own call: that hint only needs to live in-module when
    there's nowhere ELSE (preview's own preview_footer) it could show
    instead.
    """
    return any(b.name == "preview" for b in cfg.layout.boxes)


def _forget_hidden_hint_segments(theme, cfg):
    """(text, color) segments for "[F]orget   [H]idden connect" — the
    Connectivity box's own bottom-border-line fallback for wifi_forget/
    wifi_connect_hidden's own discoverability, shown only when
    _preview_available() is False (see draw()'s own comment for the
    full "why only then" reasoning) and only while actually browsing
    WiFi (same "don't hint at something not currently actionable"
    instinct the header's own PWR/SCAN/PAIRABLE legend already applies
    via is_controllable).

    The bracket wraps ONLY the mnemonic letter itself ("[F]orget", not
    "[Forget]") — a different convention from the header's own
    "[PWR ...]" (which brackets the whole group) deliberately matching
    Rafi's own literal ask instead: a classic "underlined hotkey
    letter in a word" menu convention. Hardcodes "F"/"H" rather than
    reading them back out of cfg.keybinds — same known tradeoff the
    header's own P/S/A legend already accepts (see wifi_power_toggle's
    own comment in defaults/config.toml): correct for the packaged
    defaults ("f"/"h"), and would read wrong if a user ever rebinds
    wifi_forget/wifi_connect_hidden away from them, exactly like
    rebinding wifi_power_toggle away from "p" already would for [P]WR.
    """
    urgent = theme.get("urgent", 0)
    bold_urgent = urgent | curses.A_BOLD
    return [
        ("[", urgent), ("F", bold_urgent), ("]orget", urgent),
        ("   ", urgent),
        ("[", urgent), ("H", bold_urgent), ("]idden connect", urgent),
    ]


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
            shown = _passphrase_input if _passphrase_visible else "•" * len(_passphrase_input)
            lines.append((f"{shown}_", theme.get("text", 0)))
            toggle_label = key_label(ctx.config.keybinds["wifi_passphrase_visibility_toggle"])
            toggle_verb = "hide" if _passphrase_visible else "show"
            dim = theme.get("text", 0) | curses.A_DIM
            lines.append((f"{key_label(ctx.config.keybinds['confirm'])} to connect, Esc to cancel", dim))
            lines.append((f"{toggle_label} to {toggle_verb}", dim))
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

    # Fallback discoverability for wifi_forget/wifi_connect_hidden,
    # cut right into the box's own bottom border — only when preview
    # isn't in the layout at all (its own preview_footer already
    # covers this otherwise, see _browsing_hint_footer) and only while
    # actually browsing WiFi (nothing else uses these two keys). See
    # _forget_hidden_hint_segments' own docstring for the full design;
    # _preview_available() is CLAUDE/NOTES/design-decisions.md
    # #module-self-sufficiency-vs-preview's own "mechanism #2", its
    # first real consumer.
    if not _preview_available(ctx.config) and is_browsing() and browsing_section() == "wifi":
        hint_segments = _forget_hidden_hint_segments(theme, ctx.config)
        hint_w = sum(display_width(text) for text, _color in hint_segments)
        hint_x = x + max((w - hint_w) // 2, 1)
        _draw_segments(stdscr, y + h - 1, hint_x, hint_segments, max(w - 2, 0))

    inner_w = max(w - 4, 0)

    for i, (kind, payload) in enumerate(_build_rows(ctx, h)):
        row = y + 1 + i
        if row >= y + h - 1:
            break

        if kind in ("wifi_header", "bt_header"):
            header_text = payload
            is_wifi = kind == "wifi_header"
            if not is_wifi:
                header_text = header_text.replace("Bluetooth", "BT")
            item_id = "connectivity:wifi:header" if is_wifi else "connectivity:bt:header"
            is_selected = item_id == ctx.selected_id
            # Selection shows on the header TEXT's own color (same
            # convention every other row in this module already uses)
            # — Enter here enters browsing (see this module's own
            # "level-2 browsing" section docstring). Real Powered/
            # Scanning/(Bluetooth's own Pairable) state is drawn as a
            # compact "[P]WR ● [S]CAN ○[...]" legend right after the
            # label — see _wifi_header_status_segments' own docstring
            # for the full "why" (both a live status readout AND a
            # built-in key legend in one, right in this module's own
            # box regardless of whether preview exists in the layout
            # at all). Replaces the old separate right-aligned
            # "Scanning…"/"Discovering…" blink text entirely — the
            # SCAN segment's own dot now blinks instead (_scanning_dot).
            header_color = theme.get("selected", 0) if is_selected else theme.get("accent", 0)
            # The legend only lights up once you've actually ENTERED
            # level-2 browsing for THIS section — merely having the
            # collapsed header row selected/hovered at level 1 keeps it
            # dim. Rafi's own call: P/S/A only do anything once
            # main.py's handle_connectivity_browsing is actually
            # dispatching keys (a real mode_stack claim), so dim-until-
            # then honestly signals "not controllable yet", not just
            # "not currently looked at".
            section = "wifi" if is_wifi else "bluetooth"
            is_controllable = is_browsing() and browsing_section() == section
            scanning_domain = "wifi_scanning" if is_wifi else "bluetooth_discovering"
            scanning = ctx.status is not None and bool(ctx.status.get(scanning_domain))
            if is_wifi:
                adapter = ctx.status.get("wifi_adapter") if ctx.status is not None else None
                status_segments = _wifi_header_status_segments(
                    theme, adapter.powered if adapter else None, scanning, is_controllable,
                    power_flashing=_is_header_action_flashing(section, "power"),
                    scan_flashing=_is_header_action_flashing(section, "scan"),
                )
            else:
                adapter = ctx.status.get("bluetooth_adapter") if ctx.status is not None else None
                status_segments = _bt_header_status_segments(
                    theme, adapter.powered if adapter else None, scanning,
                    adapter.pairable if adapter else None, is_controllable,
                    power_flashing=_is_header_action_flashing(section, "power"),
                    scan_flashing=_is_header_action_flashing(section, "scan"),
                    pairable_flashing=_is_header_action_flashing(section, "pairable"),
                )
            # Right-aligned to the box's own right edge (same "block's
            # own last character sits one cell in from the border"
            # convention sessions.py's own _action_positions() uses),
            # not left-anchored right after the label — Rafi's own ask:
            # a consistent right edge regardless of how long "WiFi
            # [19]"/"BT [3]" happens to be. The label's own available
            # width shrinks to make room rather than risking overlap;
            # status_segments' own leading "  " (see _wifi_header_
            # status_segments' own construction) already supplies the
            # visual gap between the two, no separate margin needed here.
            status_w = sum(display_width(text) for text, _color in status_segments)
            status_x = x + w - 1 - status_w
            clipped_label = wc_truncate(header_text, max(status_x - (x + 2), 0))
            try:
                stdscr.addstr(row, x + 2, clipped_label, header_color | curses.A_BOLD)
            except curses.error:
                pass
            _draw_segments(stdscr, row, status_x, status_segments, status_w)

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
            # "[new] " prefix, not a " (new)" suffix — see
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
            # "[new] " prefix — same reasoning as wifi_item's own
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


def _power_progress_line(status, adapter_info, theme, domain_name="wifi"):
    """Same role as _action_progress_line above, for a radio power
    toggle specifically — "Powering on…"/"Powering off…" while in
    flight, or the real failure message once it's done. Uses its own
    fixed pending_key ("power", set explicitly by main.py's
    handle_connectivity_browsing — see its own request_action() calls)
    rather than the request's own target bool, so this only ever has
    to check ONE key regardless of which direction was requested,
    instead of checking both is_pending(domain_name, True) and
    is_pending(domain_name, False). Direction is inferred from
    adapter_info's own last-known `powered` (the toggle always
    requests the opposite of what it saw) — genuinely just a best
    guess while the real answer is still in flight, same as every
    other "Connecting…"/"Disconnecting…" progress line already is.
    `domain_name` defaults to "wifi" (this function's original, only
    consumer); generalized 2026-08-16 to also serve "bluetooth" once
    its own adapter power toggle landed — both domains' StatusWorker
    actions use the identical "power" pending_key, so the same function
    body works unchanged for either, just scoped by which domain it's
    told to check.
    """
    if status is None:
        return None
    if status.is_pending(domain_name, "power"):
        text_color, attr = _pending_blink_style(theme)
        target_on = adapter_info is None or not adapter_info.powered
        return ("Powering on…" if target_on else "Powering off…", text_color | attr)
    action_error = status.get_action_error_for(domain_name, "power")
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


def _connection_diagnostics_table(diagnostics):
    """impala's own bordered panel worth of live, negotiated details
    for the CURRENTLY connected network — a second table entry,
    stacked under "Device" (see draw_preview()'s own docstring), not
    folded into the Device table itself since it's a per-connection
    concept, not a per-device one — see
    ConnectionDiagnostics' own docstring for the live-confirmed reason
    this is genuinely different information from WifiNetwork.security,
    not a duplicate. Every column is independently optional, same
    "only show what's real" discipline _adapter_info_table() already
    has. Live Security goes through the existing _security_label() —
    the SAME normalization WifiNetwork.security already gets — so
    iwd's own already-human "WPA3-Personal" and NetworkManager's raw
    "sae" token both end up in the identical display vocabulary
    regardless of which backend produced them (a "WPA3-Personal"-
    shaped string isn't a key in _WIFI_SECURITY_LABELS, so it passes
    through that lookup completely unchanged — no double-translation
    risk). Labeled "Live Security", not plain "Security" — found live,
    Rafi's own report: sitting right below the network's own preview_
    text "Security: ..." line (WifiNetwork.security, the network's
    static configured type), an identically-labeled second value read
    as a contradiction/duplicate rather than two genuinely different,
    both-true answers.

    One row: briefly split into two (2026-08-15, same session) once
    Tx/Rx Rate and IP Address joined Frequency/Channel/Live Security/
    RSSI/Cipher — reverted back to one the same day, Rafi's own call,
    once it turned out to comfortably fit as a single line after all.
    """
    columns = []
    if diagnostics.frequency is not None:
        columns.append(("Frequency", f"{diagnostics.frequency / 1000:.2f} GHz"))
    if diagnostics.channel is not None:
        columns.append(("Channel", str(diagnostics.channel)))
    if diagnostics.security is not None:
        columns.append(("Live Security", _security_label(diagnostics.security)))
    if diagnostics.rssi is not None:
        columns.append(("RSSI", f"{diagnostics.rssi} dBm"))
    if diagnostics.cipher is not None:
        columns.append(("Cipher", diagnostics.cipher))
    if diagnostics.tx_bitrate is not None:
        # Arrow suffix matches sysmon.py's own swap in/out convention
        # (↓/↑) — ↑ = leaving this machine (upload/transmit), ↓ =
        # arriving at it (download/receive), same direction both
        # readers already know from any real network UI.
        columns.append(("Tx Rate ↑", f"{diagnostics.tx_bitrate:.1f} Mb/s"))
    if diagnostics.rx_bitrate is not None:
        columns.append(("Rx Rate ↓", f"{diagnostics.rx_bitrate:.1f} Mb/s"))
    if diagnostics.ip_address is not None:
        columns.append(("IP Address", diagnostics.ip_address))

    return [columns] if columns else []


def _wifi_preview_tables(adapter_info, scanning, diagnostics=None, connected=False, ssid=None):
    """The stacked "Device" (+ "Connection - <ssid>", only when
    connected and diagnostics are actually available) tables every
    WiFi-section NavItem's preview_data carries — one shared builder
    so the
    header, real network rows, and the empty-section placeholder can't
    drift out of sync with each other on what gets shown where. The
    ssid in the second table's own title exists so it can't be
    mistaken for yet another property of the DEVICE above it — found
    live, Rafi's own report: "Connection" alone, directly under
    "Device", read as more device info at a glance, not "here's what's
    happening on THIS specific network."
    """
    device_columns = _adapter_info_table(adapter_info, scanning)
    tables = [("Device", [device_columns] if device_columns else [])]
    if connected and diagnostics is not None:
        title = f"Connection - {ssid}" if ssid else "Connection"
        tables.append((title, _connection_diagnostics_table(diagnostics)))
    return tables


def _bt_adapter_info_table(adapter, discovering):
    """Bluetooth's own "Device" table — mirrors _adapter_info_table()
    above exactly, scoped to what has a real action behind it (see
    BluetoothAdapterInfo's own docstring for why Discoverable isn't
    here): Name/Powered/Pairable each independently optional, Discovering
    always shown (same "fold in the tighter-polled live domain instead
    of a stale duplicate field" reasoning Scanning already has).
    """
    if adapter is None:
        return None
    columns = []
    if adapter.name:
        columns.append(("Name", adapter.name))
    if adapter.powered is not None:
        columns.append(("Powered", _yes_no(adapter.powered)))
    if adapter.pairable is not None:
        columns.append(("Pairable", _yes_no(adapter.pairable)))
    columns.append(("Discovering", _yes_no(discovering)))
    if adapter.address:
        columns.append(("Address", adapter.address))
    return columns


def _bt_preview_tables(adapter_info, discovering):
    """The stacked "Device" table every Bluetooth-section NavItem's
    preview_data carries — mirrors _wifi_preview_tables()'s own role
    (one shared builder so the header, real device rows, and the
    empty-section placeholder can't drift out of sync), minus a
    "Connection" second table: BlueZ has no live post-connection
    diagnostic equivalent to iwd's StationDiagnostic (confirmed live,
    2026-08-16, against a real connected device — org.bluez.Device1
    carries no live RSSI once connected, only during discovery), so
    there's nothing genuine to show there.
    """
    device_columns = _bt_adapter_info_table(adapter_info, discovering)
    return [("Device", [device_columns] if device_columns else [])]


def draw_preview(stdscr, box, preview_data, theme) -> int:
    """Registered into render.py's PREVIEW_RENDERERS (keyed "connectivity")
    — draws the WiFi Device/Connection table stack a NavItem's own
    preview_data carries (_wifi_preview_tables()'s return value, [(title,
    rows), ...]) and returns how much height it used, so preview.py can
    keep stacking preview_text/preview_footer below it. This whole
    indirection exists so preview.py never has to know this module's
    content shape at all; see CLAUDE/NOTES/design-decisions.md
    #module-self-sufficiency-vs-preview for the full reasoning. A table
    that doesn't fit in whatever height is left is simply skipped, not
    partially drawn — same "degrade, don't corrupt" tolerance every
    other primitive here has for a too-small box.

    A 2026-08-16/17 side-by-side "Available networks | Selected: <ssid>"
    panel briefly lived here (see git history if reviving this idea) —
    reverted the same session, Rafi's own call: it duplicated the exact
    list the WiFi box's own rows already show (dot/[new]/signal%), just
    in a second place, with no new information — the sidebar's whole
    job IS that list; preview's job is detail on the one thing browsed,
    not a second copy of what sidebar already shows.
    """
    x, y, w, h = box
    content_y, content_h = y, h
    for table_title, table_rows in (preview_data or []):
        needed_h = 2 + 2 * len(table_rows)  # top+bottom border + a header+value pair per row
        table_h = min(needed_h, content_h)
        if table_h < needed_h:
            break
        draw_table_box(
            stdscr, content_y, x, table_h, w, table_title, table_rows,
            header_color=theme.get("text", 0), value_color=theme.get("text", 0) | curses.A_DIM,
            border_color=theme.get("text", 0),
        )
        content_y += table_h
        content_h -= table_h
    return h - content_h


def _wifi_scan_preview_text(networks, error, theme, status=None, adapter_info=None):
    """The hover-preview for the WiFi header row itself (name kept from
    when Enter here triggered a scan directly — it now enters browsing
    instead, see this module's own "level-2 browsing" section) — the
    FULL list of currently available networks, not just the
    connectivity_visible_slots window the box's scrollable section
    shows. Adapter/device info lives in a separate preview_table now
    (see _adapter_info_table above), not folded in here — only the
    live power-toggle progress line (_power_progress_line) is, since
    "Powering on…" is exactly as relevant here as anywhere else
    wifi's own preview shows. Same None-vs-[] error handling as
    _build_rows' own — `networks` is the raw ctx.wifi_networks (may be
    None), not the "or []"-normalized local nav_items() otherwise
    uses, so a real poll failure still shows as an error here too, not
    "no networks".

    Each network's own name is left-padded to a shared width before
    its signal % (same "label padded, value follows" convention
    _wifi_preview_text() below uses, just with no literal label text —
    there's nothing to pad but the name itself), so every percentage
    lands in the same column — plus a blank line between every row
    (Rafi's own call, 2026-08-15, after a boxed version was tried and
    rejected as unnecessary: "give it breathing room" was the actual
    ask, not a border).
    """
    power_progress = _power_progress_line(status, adapter_info, theme)
    trailer = [power_progress] if power_progress is not None else []
    if networks is None and error:
        return [(f"⚠ {error}", theme.get("urgent", 0))] + trailer
    if not networks:
        return [("No networks found", theme.get("text", 0) | curses.A_DIM)] + trailer
    rows = []
    for network in networks:
        dot = "●" if network.connected else "○"
        prefix = "[new] " if not network.known else ""
        color = theme.get("accent", 0) if network.connected else theme.get("text", 0)
        signal = f"{network.signal}%" if network.signal is not None else ""
        rows.append((f"{dot} {prefix}{network.ssid}", signal, color))
    name_width = max(len(name) for name, _signal, _color in rows)
    lines = [(f"Available networks [{len(networks)}]", theme.get("accent", 0))]
    for name, signal, color in rows:
        lines.append(("", 0))
        lines.append((f"{name.ljust(name_width)} {signal}".rstrip(), color))
    if trailer:
        lines.append(("", 0))
        lines.extend(trailer)
    return lines


def _bt_discover_preview_text(devices, error, theme, discovering=False):
    """Same reasoning as _wifi_scan_preview_text above, for the
    Bluetooth header row and ctx.bluetooth_devices — name left, a
    bracketed status word right, blank line between rows.

    ALWAYS shows the FULL current device list (paired and not) —
    title is always "Devices [N]", the same N the sidebar's own header
    count and row list already show. An earlier version (2026-08-16)
    filtered this down to paired-only devices while idle ("Known
    devices [N]"), reasoning that an unqualified list would
    misrepresent recently-discovered unpaired devices as "known" — a
    real point about the TITLE, but it meant this preview's own count
    silently disagreed with the sidebar's honest full count the moment
    a scan ended. Reverted, 2026-08-17, Rafi's own report: two
    different numbers for the same box read as a bug, not two
    deliberately different definitions. `paired` is itself a
    persisted bluez fact, not something that goes stale outside a scan
    the way rssi does — showing it via the same "[new]" tag the
    sidebar's own rows already use is honest regardless of
    `discovering`.

    Per-row status, in order of priority:
    - "[new]" — not paired. Always shown, scan or not (paired-ness
      never goes stale).
    - "[known; connected]" — paired AND currently connected. Checked
      before the rssi-based statuses below and shown regardless of
      `discovering` too — device.connected is a real-time D-Bus fact,
      not scan-result-dependent, so it's just as honest to say outside
      a scan as during one. Found live, Rafi's own follow-up question:
      "detected" claims something about the scan's own advertisement
      results, which isn't actually true for a connected device (an
      active classic-BT connection often doesn't keep advertising the
      way an idle-but-nearby device does) — "connected" is the
      stronger, more specific fact, so it's said directly.
    - "[known; detected]" / "[known; undetected]" — paired, not
      connected: ONLY shown while `discovering`, since device.rssi
      reverts to None the moment discovery stops (see BluetoothDevice's
      own docstring) — claiming detected/undetected outside a scan
      would be a fabrication, not a stale-but-still-true fact.
    - "" (nothing) — paired, not connected, not discovering. There's
      genuinely nothing honest to say about range without an active
      scan.

    Every row is padded to the SAME total width (name_width + gap +
    status_width), deliberately NOT .rstrip()'d down to its own
    shorter natural length — draw_centered_lines() centers each row
    INDEPENDENTLY, so rows of differing length end up centered at
    different x offsets, a real bug found live (screenshots showing
    device rows staggered diagonally, one x position per row, once
    status words of visibly different lengths made the effect
    obvious; WiFi's own equivalent likely has the identical latent
    bug, just less visible with its own more similar-length rows).
    Keeping every row's own total character count identical sidesteps
    it without touching draw_centered_lines() itself — trailing
    padding renders as plain background, not a visible cost.
    """
    if devices is None and error:
        return [(f"⚠ {error}", theme.get("urgent", 0))]
    visible = devices or []
    if not visible:
        return [("No devices found", theme.get("text", 0) | curses.A_DIM)]
    rows = []
    for device in visible:
        if not device.paired:
            status = "[new]"
        elif device.connected:
            status = "[known; connected]"
        elif discovering:
            status = "[known; detected]" if device.rssi is not None else "[known; undetected]"
        else:
            status = ""
        dot = "●" if device.connected else "○"
        color = theme.get("accent", 0) if device.connected else theme.get("text", 0)
        rows.append((f"{dot} {device.name}", status, color))
    name_width = max(len(name) for name, _status, _color in rows)
    status_width = max((len(status) for _name, status, _color in rows), default=0)
    title = f"Devices [{len(visible)}]"
    lines = [(title, theme.get("accent", 0))]
    for name, status, color in rows:
        lines.append(("", 0))
        text = f"{name.ljust(name_width)} {status.rjust(status_width)}" if status_width > 0 else name
        lines.append((text, color))
    return lines


def _browsing_hint_footer(theme, cfg, section, connected, known=True, has_item=True, powered=True):
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

    wifi_forget/wifi_connect_hidden only ever DO anything while
    section == "wifi" (see main.py's handle_connectivity_browsing —
    bluetooth has no forget/hidden-network equivalent), so they're
    only listed there. Power (wifi_power_toggle/bt_power_toggle) and,
    for bluetooth, Pairable (bt_pairable_toggle) DO have real
    per-section equivalents — see the powered=False branch above for
    Power specifically, and the bluetooth branch below for Pairable.
    [D] Forget specifically is further narrowed to `known` networks
    only — forgetting an unknown network
    (never actually saved, nothing to delete) isn't a real action, so
    offering the key there would be misleading, not just redundant.

    has_item=False (the synthetic placeholder nav_items() emits when
    the browsed section has zero real items — see
    _empty_browsing_nav_item()'s own docstring) drops [Enter] Connect/
    Disconnect and [D] Forget entirely, since neither has a selected
    network/device to act on.

    powered=False narrows the WHOLE hint down to just Power/[Esc] Back
    — found live, Rafi's own report (originally wifi-only, widened
    2026-08-16 once Bluetooth got its own adapter power toggle): with
    the radio off, Scan/Hidden/Forget (wifi) or Scan (bluetooth) all
    genuinely can't do anything (nothing to scan, no radio to connect a
    hidden network on), so offering them was actively confusing, not
    just unhelpful clutter. wifi_power_toggle and bt_power_toggle
    resolve to the SAME physical key (config.toml default "p", not two
    separate ones) — see defaults/config.toml's own comment: the
    header row's own "[P]WR" legend needs one shared letter to read
    correctly for both sections.
    """
    urgent = theme.get("urgent", 0)
    if not powered:
        power_key = cfg.keybinds["wifi_power_toggle"] if section == "wifi" else cfg.keybinds["bt_power_toggle"]
        return [(f"[{key_label(power_key)}] Power   [Esc] Back", urgent)]
    connect_hint = f"[{key_label(cfg.keybinds['confirm'])}] {'Disconnect' if connected else 'Connect'}   " if has_item else ""
    if section != "wifi":
        # Same two-line shape wifi's own "on" hint below has — line 1
        # connect/scan, line 2 the section's own extra keys ending in
        # Power/Esc. Power belongs here too (not just the powered=False
        # branch above) — found live, Rafi's own report: the "radio is
        # on" hint had no way to turn it back OFF.
        return [
            (f"{connect_hint}[{key_label(cfg.keybinds['scan'])}] Scan", urgent),
            (f"[{key_label(cfg.keybinds['bt_pairable_toggle'])}] Pairable   [{key_label(cfg.keybinds['bt_power_toggle'])}] Power   [Esc] Back", urgent),
        ]
    forget_hint = f"   [{key_label(cfg.keybinds['wifi_forget'])}] Forget" if (has_item and known) else ""
    return [
        (f"{connect_hint}[{key_label(cfg.keybinds['scan'])}] Scan{forget_hint}", urgent),
        (f"[{key_label(cfg.keybinds['wifi_connect_hidden'])}] Hidden   [{key_label(cfg.keybinds['wifi_power_toggle'])}] Power   [Esc] Back", urgent),
    ]


def _wifi_property_rows(network, theme):
    """(label, value, color) triples for a network's own property list
    — Signal:/Security:/Known:/Auto-connect:/Hidden:/Last connected:/
    Connected:, each independently optional per network.known. Extracted
    out of _wifi_preview_text() below so a future second consumer of the
    same rows (a side-by-side detail panel was tried and reverted the
    same session — see draw_preview's own docstring) doesn't have to
    re-derive them by hand. Deliberately excludes the live connect/
    disconnect progress line — that's not a real label:value pair, and
    including it here would skew label_width padding calculations for
    callers that pad these rows themselves.
    """
    text = theme.get("text", 0)
    connected_color = theme.get("accent", 0) if network.connected else text
    signal_text = f"{network.signal}%" if network.signal is not None else "unknown"
    rows = [
        ("Signal:", signal_text, text),
        ("Security:", _security_label(network.security), text),
        ("Known:", _yes_no(network.known), text),
    ]
    if network.known:
        rows.append(("Auto-connect:", _yes_no(network.auto_connect), text))
        rows.append(("Hidden:", _yes_no(network.hidden), text))
        if network.last_connected:
            rows.append(("Last connected:", _format_timestamp(network.last_connected), text))
    rows.append(("Connected:", _yes_no(network.connected), connected_color))
    return rows


def _wifi_preview_text(network, theme, status):
    """SSID first, then Signal:/Security:/Known:/... — each label
    padded to a shared width first so every value lands in the same
    column, one blank line between every row so the block has some
    breathing room instead of reading as a packed wall of text (Rafi's
    own call, 2026-08-15, after a boxed version was tried and rejected
    as unnecessary — plain, aligned, spaced-out centered text was the
    actual ask, not a border). A live connect/disconnect attempt's own
    progress/error line (_action_progress_line) rides along as one
    more (unpadded) row — genuinely just one more fact about this same
    network, not a separate concept needing its own alignment.
    """
    rows = _wifi_property_rows(network, theme)
    label_width = max(len(label) for label, _value, _color in rows)
    lines = [(network.ssid, theme.get("accent", 0))]
    for label, value, color in rows:
        lines.append(("", 0))
        lines.append((f"{label.ljust(label_width)} {value}", color))
    progress = _action_progress_line(status, "wifi", network.ssid, network.connected, theme)
    if progress is not None:
        lines.append(("", 0))
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


def _empty_browsing_nav_item(section, row, box, theme, cfg, status, adapter_info, scanning) -> NavItem:
    """The synthetic placeholder nav_items() returns when browsing
    (mode_stack "connectivity_browsing") a section that currently has
    zero real items — WiFi radio just turned off, Bluetooth discovery
    hasn't found anything yet, etc.

    Found live (2026-08-15): without SOME NavItem for loop_state.
    selected_id to point at, frame_update.py's own stale-selection
    recovery (which has no idea this module is mid-claimed-browse)
    silently reassigned selection to an unrelated module (sidebar) the
    instant the list emptied — while mode_stack kept trapping every
    key for handle_connectivity_browsing regardless, since that check
    is keyed purely off mode_stack, not off what's currently selected.
    The result looked exactly like tuicc had frozen in a loop (Escape
    "worked" once, back to the header, but re-entering the still-empty
    section hit the identical reassignment again) even though the
    process itself never hung — confirmed live, tuicc kept running the
    whole time. This placeholder closes that gap: browsing always has
    a real, stable NavItem to select, so the stale-selection recovery
    never has a reason to fire.

    Still carries the same Device table + hint footer real rows get
    (minus Connect/Disconnect/Forget, which need a real item — see
    _browsing_hint_footer's own has_item=False) — "Powered: no" right
    here is exactly what explains why the list is empty and that the
    same power key fixes it, for either section (both wifi and
    bluetooth have their own adapter power toggle — see
    defaults/config.toml's own wifi_power_toggle/bt_power_toggle
    comments). Also carries the live power-toggle progress line
    (_power_progress_line) and narrows the hint to just Power/Back
    while the radio is actually off (_browsing_hint_footer's own
    powered=False) — Scan/Hidden/Forget (wifi) or Scan/Pairable
    (bluetooth) can't do anything without a radio, so offering them
    here would be actively confusing, not just unhelpful. `adapter_info`
    is whichever section's own adapter snapshot the caller passed in
    (AdapterInfo for wifi, BluetoothAdapterInfo for bluetooth — both
    share a `.powered` attribute, which is all this function itself
    ever reads directly); `scanning` is that section's own live
    scanning/discovering domain value.
    """
    x, y, w, h = box
    id_prefix = "wifi" if section == "wifi" else "bt"
    noun = "networks" if section == "wifi" else "devices"
    domain_name = "wifi" if section == "wifi" else "bluetooth"
    powered = adapter_info.powered if adapter_info is not None else True
    power_progress = _power_progress_line(status, adapter_info, theme, domain_name)
    preview_text = [(f"No {noun} in range", theme.get("text", 0) | curses.A_DIM)]
    if power_progress is not None:
        preview_text.append(power_progress)
    tables = _wifi_preview_tables(adapter_info, scanning) if section == "wifi" else _bt_preview_tables(adapter_info, scanning)
    return NavItem(
        id=f"connectivity:{id_prefix}:empty",
        rect=(x + 1, row, w - 2, 1),
        target_kind=f"{id_prefix}_empty",
        preview_text=preview_text,
        preview_footer=_browsing_hint_footer(
            theme, cfg, section, connected=False, known=False, has_item=False, powered=powered,
        ),
        preview_data=tables,
    )


def _wifi_row_nav_item(network, box, row, theme, status, cfg, adapter_info, scanning, diagnostics) -> NavItem:
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
        # more here than at the header, not less. Plus a second
        # "Connection" table (_wifi_preview_tables' own connected=
        # param) — only ever meaningful for whichever row IS the
        # currently connected one, diagnostics is Station-wide, not
        # per-network, so it wouldn't mean anything on any other row.
        preview_data=_wifi_preview_tables(adapter_info, scanning, diagnostics, network.connected, network.ssid),
    )


def _bt_row_nav_item(device, box, row, theme, status, cfg, adapter_info, discovering) -> NavItem:
    x, y, w, h = box
    return NavItem(
        id=f"connectivity:bt:{device.id}",
        rect=(x + 1, row, w - 2, 1),
        focus_target=device.id,
        target_kind="bluetooth_device",
        preview_text=_bt_preview_text(device, theme, status),
        preview_footer=_browsing_hint_footer(theme, cfg, "bluetooth", device.connected),
        # Same reasoning _wifi_row_nav_item's own comment gives — d/n/o
        # (here: p/a) only ever usable from INSIDE browsing, so the
        # adapter's own current state belongs right where those
        # controls actually live, on every row, not just the header.
        preview_data=_bt_preview_tables(adapter_info, discovering),
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
    wifi_networks = _connected_first(ctx.wifi_networks) or []
    bluetooth_devices = _connected_first(ctx.bluetooth_devices) or []
    # Computed once here, not per-row inside the loop below — needed by
    # every wifi row (level 2, see _wifi_row_nav_item's own comment on
    # why the Device/Connection tables need to survive into browsing)
    # for the exact same preview_table content; adapter_info alone is
    # also read by the header's own preview_text below (the live
    # power-toggle progress line — see _wifi_scan_preview_text), even
    # though the header no longer carries the tables themselves (see
    # wifi_header_item's own comment, 2026-08-17).
    adapter_info = ctx.status.get("wifi_adapter") if ctx.status is not None else None
    scanning = bool(ctx.status.get("wifi_scanning")) if ctx.status is not None else False
    diagnostics = ctx.status.get("wifi_diagnostics") if ctx.status is not None else None
    # Bluetooth's own equivalents — same "wifi_adapter"/"wifi_scanning"
    # split (see _bt_preview_tables' own docstring for why there's no
    # "Connection" table on this side).
    bt_adapter_info = ctx.status.get("bluetooth_adapter") if ctx.status is not None else None
    bt_discovering = bool(ctx.status.get("bluetooth_discovering")) if ctx.status is not None else False

    wifi_header_item = None
    bt_header_item = None
    wifi_items: list[NavItem] = []
    wifi_rows: list[int] = []  # one entry per REAL "wifi_item" row drawn, in order
    bt_items: list[NavItem] = []
    bt_rows: list[int] = []
    # First "empty_slot" row's own y, per section — the fallback
    # position for _empty_browsing_nav_item() below when browsing a
    # section with zero real items. `in_wifi_section` tracks which
    # section the loop is currently walking (wifi_header..bt_header
    # exclusive), since "empty_slot" rows themselves carry no section
    # marker of their own (see windowed_list.section_rows()).
    wifi_empty_row = None
    bt_empty_row = None
    in_wifi_section = True

    for i, (kind, payload) in enumerate(_build_rows(ctx, h)):
        row = y + 1 + i
        if row >= y + h - 1:
            break

        if kind == "wifi_header":
            in_wifi_section = True
            wifi_header_item = NavItem(
                id="connectivity:wifi:header", rect=(x + 1, row, w - 2, 1), target_kind="wifi_browse",
                # ctx.wifi_networks via _connected_first (which preserves
                # None unchanged), not the local wifi_networks variable
                # above — that one's already "or []"-normalized, which
                # would silently turn a real poll failure into "no
                # networks found" here instead of the real error, same
                # None-vs-[] distinction the bt_header_item right below
                # already protects the identical way.
                preview_text=_wifi_scan_preview_text(_connected_first(ctx.wifi_networks), ctx.wifi_error, theme, ctx.status, adapter_info),
                preview_footer=_header_enter_hint_footer(theme, cfg, "networks"),
                # No preview_data (Device/Connection tables) at level 1
                # — Rafi's own call, 2026-08-17: those tables are about
                # ONE thing you're browsing/connected to, and belong
                # once you're actually inside browsing (every real row,
                # plus the empty-section placeholder), not stacked
                # above the header's own "what's out there" overview,
                # which already has plenty to show on its own.
            )
        elif kind == "bt_header":
            in_wifi_section = False
            bt_header_item = NavItem(
                id="connectivity:bt:header", rect=(x + 1, row, w - 2, 1), target_kind="bluetooth_browse",
                # ctx.bluetooth_devices directly (via _connected_first,
                # which preserves None unchanged), not the local
                # bluetooth_devices variable above — that one's already
                # "or []"-normalized, which would silently turn a real
                # poll failure into "no devices found" here instead of
                # the real error, same None-vs-[] distinction
                # _wifi_scan_preview_text's own callers already protect.
                preview_text=_bt_discover_preview_text(_connected_first(ctx.bluetooth_devices), ctx.bluetooth_error, theme, bt_discovering),
                preview_footer=_header_enter_hint_footer(theme, cfg, "devices"),
                # Same reasoning as wifi_header_item above — no Device
                # table at level 1.
            )
        elif kind == "wifi_item":
            wifi_items.append(_wifi_row_nav_item(payload, box, row, theme, ctx.status, cfg, adapter_info, scanning, diagnostics))
            wifi_rows.append(row)
        elif kind == "bt_item":
            bt_items.append(_bt_row_nav_item(payload, box, row, theme, ctx.status, cfg, bt_adapter_info, bt_discovering))
            bt_rows.append(row)
        elif kind == "empty_slot":
            if in_wifi_section and wifi_empty_row is None:
                wifi_empty_row = row
            elif not in_wifi_section and bt_empty_row is None:
                bt_empty_row = row

    visible_slots = ctx.config.connectivity_visible_slots

    selected_wifi_index = _selected_wifi_index(wifi_networks, ctx.selected_id)
    before_i, after_i = _section_nav_indices(len(wifi_networks), selected_wifi_index, visible_slots=visible_slots)
    if before_i is not None and wifi_rows:
        wifi_items = [_wifi_row_nav_item(wifi_networks[before_i], box, wifi_rows[0], theme, ctx.status, cfg, adapter_info, scanning, diagnostics)] + wifi_items
    if after_i is not None and wifi_rows:
        wifi_items = wifi_items + [_wifi_row_nav_item(wifi_networks[after_i], box, wifi_rows[-1], theme, ctx.status, cfg, adapter_info, scanning, diagnostics)]

    selected_bt_index = _selected_bt_index(bluetooth_devices, ctx.selected_id)
    before_i, after_i = _section_nav_indices(len(bluetooth_devices), selected_bt_index, visible_slots=visible_slots)
    if before_i is not None and bt_rows:
        bt_items = [_bt_row_nav_item(bluetooth_devices[before_i], box, bt_rows[0], theme, ctx.status, cfg, bt_adapter_info, bt_discovering)] + bt_items
    if after_i is not None and bt_rows:
        bt_items = bt_items + [_bt_row_nav_item(bluetooth_devices[after_i], box, bt_rows[-1], theme, ctx.status, cfg, bt_adapter_info, bt_discovering)]

    if _browsing_section == "wifi":
        if wifi_items:
            return wifi_items
        if wifi_empty_row is not None:
            return [_empty_browsing_nav_item("wifi", wifi_empty_row, box, theme, cfg, ctx.status, adapter_info, scanning)]
        return []
    if _browsing_section == "bluetooth":
        if bt_items:
            return bt_items
        if bt_empty_row is not None:
            return [_empty_browsing_nav_item("bluetooth", bt_empty_row, box, theme, cfg, ctx.status, bt_adapter_info, bt_discovering)]
        return []

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
    selection sitting on the now-unreachable header id.

    ALWAYS sets reselect_item_id, even when the section is empty — an
    earlier version left it unset there on the theory the header id
    "was still valid." Found live it wasn't: the instant browsing
    starts, nav_items() stops returning the header entirely (browsing
    only ever returns THAT section's own items — see this module's own
    "level-2 browsing" section), so a stale header id triggered
    frame_update.py's stale-selection recovery to reassign selection
    to an unrelated module, while mode_stack kept every key trapped
    here regardless — see _empty_browsing_nav_item()'s own docstring
    for the full incident this fixes.
    """
    start_browsing("wifi")
    networks = ctx.status.get("wifi") or []
    ctx.reselect_item_id = f"connectivity:wifi:{networks[0].ssid}" if networks else "connectivity:wifi:empty"
    return False, None


def handle_bt_header(ctx, item, cfg):
    """Same reasoning as handle_wifi_header above, for Bluetooth."""
    start_browsing("bluetooth")
    devices = ctx.status.get("bluetooth") or []
    ctx.reselect_item_id = f"connectivity:bt:{devices[0].id}" if devices else "connectivity:bt:empty"
    return False, None


HANDLERS = {
    "wifi_browse": handle_wifi_header,
    "bluetooth_browse": handle_bt_header,
}
