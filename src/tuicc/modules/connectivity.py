"""Connectivity module: wifi networks and bluetooth devices, in two
sections of one box. Enter connects to the selected item — actual
connect/disconnect happens on the StatusWorker's background thread
(ctx.connectivity), never blocking the render loop.

---
IMPORTANT: draw() and nav_items() must agree on exactly which row
each item lands on. Rather than computing row positions twice (and
risking the two computations drifting apart, the way sidebar.py's
item_y had to stay carefully in sync this morning), _build_rows()
is the single source of truth both functions iterate over.
"""

import curses
import time

from tuicc.navigation import NavItem
from tuicc.render_utils import draw_box_outline


def _signal_bars(signal):
    if signal is None:
        return "?   "
    if signal >= 75:
        return "****"
    if signal >= 50:
        return "*** "
    if signal >= 25:
        return "**  "
    return "*   "


MAX_WIFI_ROWS = 4


def _build_rows(ctx, box_h):
    """One row per line the box will render, in order. Each row is
    ("header", label) | ("empty", label) | ("error", message) |
    ("wifi_item", WifiNetwork) | ("bt_item", BluetoothDevice). draw()
    renders every kind; nav_items() only emits a NavItem for the two
    *_item kinds — but both walk this exact same list, so their row
    indices can never drift apart.

    Bluetooth's row budget is reserved first and always shown in
    full — it's usually a short list (a handful of paired devices at
    most). Wifi gets whatever's left, capped at MAX_WIFI_ROWS, with a
    "+N more" row if the real list (already ordered by signal
    strength) is longer than what fits — so Bluetooth is never pushed
    off-screen by a long wifi scan, and truncation is shown, not
    hidden.

    "error" is distinct from "empty": ctx.wifi_networks/bluetooth_devices
    being None (StatusWorker's last poll for that domain raised) is not
    the same as an empty list (nothing found) — see
    RenderContext.wifi_networks' own docstring. A None with no
    ctx.wifi_error/bluetooth_error yet (the brief window before the
    very first poll completes, right after startup) is treated as
    "empty" rather than "error" — nothing has actually failed yet,
    there's just nothing to show.
    """
    bt_devices = ctx.bluetooth_devices
    bt_row_count = len(bt_devices) if bt_devices else 1  # 1 for "(none paired)"/error

    inner_rows = max(box_h - 2, 0)
    reserved_for_bt = 1 + bt_row_count  # header + its rows
    reserved_for_spacer = 1
    reserved_for_wifi_header = 1
    available_for_wifi = max(inner_rows - reserved_for_bt - reserved_for_spacer - reserved_for_wifi_header, 0)
    wifi_limit = min(available_for_wifi, MAX_WIFI_ROWS)

    rows = [("header", "WiFi")]

    wifi_networks = ctx.wifi_networks
    if wifi_networks is None and ctx.wifi_error:
        rows.append(("error", ctx.wifi_error))
    elif not wifi_networks:
        rows.append(("empty", "(none found)"))
    else:
        shown = wifi_networks[:wifi_limit]
        for network in shown:
            rows.append(("wifi_item", network))
        remaining = len(wifi_networks) - len(shown)
        if remaining > 0:
            rows.append(("empty", f"+{remaining} more"))

    rows.append(("spacer", None))
    rows.append(("header", "Bluetooth"))

    if bt_devices is None and ctx.bluetooth_error:
        rows.append(("error", ctx.bluetooth_error))
    elif not bt_devices:
        rows.append(("empty", "(none paired)"))
    else:
        for device in bt_devices:
            rows.append(("bt_item", device))

    return rows


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

    inner_w = max(w - 4, 0)

    for i, (kind, payload) in enumerate(_build_rows(ctx, h)):
        row = y + 1 + i
        if row >= y + h - 1:
            break

        if kind == "header":
            try:
                stdscr.addstr(row, x + 2, payload, theme.get("accent", 0) | curses.A_BOLD)
            except curses.error:
                pass

        elif kind == "error":
            # No-silent-failure (VISION.md, R3): distinct from "empty"
            # both visually (urgent, same role power_menu's confirm=true
            # actions and sessions.py's DEL use) and in meaning — the
            # backend couldn't be reached at all, not "genuinely
            # nothing there".
            try:
                stdscr.addstr(row, x + 2, f"⚠ {payload}"[:max(inner_w, 0)], theme.get("urgent", 0))
            except curses.error:
                pass

        elif kind == "empty":
            try:
                stdscr.addstr(row, x + 2, payload, theme.get("text", 0) | curses.A_DIM)
            except curses.error:
                pass

        elif kind == "wifi_item":
            network = payload
            is_selected = f"connectivity:wifi:{network.ssid}" == ctx.selected_id
            bars = _signal_bars(network.signal)
            name_width = max(inner_w - 3 - len(bars), 0)
            name = network.ssid[:name_width].ljust(name_width)

            pending = ctx.connectivity is not None and ctx.connectivity.is_pending("wifi", network.ssid)
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

            rest = f" {name} {bars}"[:max(inner_w - 1, 0)]
            try:
                stdscr.addstr(row, x + 2, dot, dot_color)
                stdscr.addstr(row, x + 3, rest, text_color | attr)
            except curses.error:
                pass

        elif kind == "bt_item":
            device = payload
            is_selected = f"connectivity:bt:{device.id}" == ctx.selected_id
            battery = f" {device.battery}%" if device.battery is not None else ""

            pending = ctx.connectivity is not None and ctx.connectivity.is_pending("bluetooth", device.id)
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

            rest = f" {device.name}{battery}"[:max(inner_w - 1, 0)]
            try:
                stdscr.addstr(row, x + 2, dot, dot_color)
                stdscr.addstr(row, x + 3, rest, text_color | attr)
            except curses.error:
                pass


def nav_items(box, ctx, module_name) -> list[NavItem]:
    x, y, w, h = box
    items = []

    for i, (kind, payload) in enumerate(_build_rows(ctx, h)):
        row = y + 1 + i
        if row >= y + h - 1:
            break

        if kind == "wifi_item":
            items.append(NavItem(
                id=f"connectivity:wifi:{payload.ssid}",
                rect=(x + 1, row, w - 2, 1),
                focus_target=payload.ssid,
                target_kind="wifi_network",
            ))
        elif kind == "bt_item":
            items.append(NavItem(
                id=f"connectivity:bt:{payload.id}",
                rect=(x + 1, row, w - 2, 1),
                focus_target=payload.id,
                target_kind="bluetooth_device",
            ))

    return items


def handle_wifi(ctx, item, cfg):
    """Enter toggles: connect if not connected, disconnect if it is —
    same reasoning as handle_bluetooth below (this used to always call
    connect(), so selecting an already-connected network and pressing
    confirm silently re-issued a redundant connect instead of
    disconnecting).
    """
    networks = ctx.connectivity.get("wifi") or []
    network = next((n for n in networks if n.ssid == item.focus_target), None)
    if network is not None and network.connected:
        ctx.connectivity.request_action("wifi", "disconnect", item.focus_target)
    else:
        ctx.connectivity.request_action("wifi", "connect", item.focus_target)
    return False, None


def handle_bluetooth(ctx, item, cfg):
    """Enter toggles: connect if not connected, disconnect if it is —
    otherwise pressing Enter on an already-connected device silently
    did nothing, since only connect() was ever called regardless of
    current state.
    """
    devices = ctx.connectivity.get("bluetooth") or []
    device = next((d for d in devices if d.id == item.focus_target), None)
    if device is not None and device.connected:
        ctx.connectivity.request_action("bluetooth", "disconnect", item.focus_target)
    else:
        ctx.connectivity.request_action("bluetooth", "connect", item.focus_target)
    return False, None


HANDLERS = {
    "wifi_network": handle_wifi,
    "bluetooth_device": handle_bluetooth,
}
