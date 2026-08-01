"""Entry point: ties config, provider, layout engine and rendering together."""

import curses
import subprocess
import sys
import time
import locale
from pathlib import Path

locale.setlocale(locale.LC_ALL, "")

# Relative to main.py's own location, not to cwd — so tuicc works whether
# you launch it via `cd tuicc && python main.py` (cwd == tuicc) or via a
# WM keybind spawning it from an arbitrary directory (e.g. a floating
# terminal launched with a custom app_id, cwd defaults to $HOME).
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from tuicc.config import load_config
from tuicc.context import RenderContext
from tuicc.actions import ActionContext
from tuicc.providers.registry import build_provider
from tuicc.connectivity.registry import build_wifi_backend, build_bluetooth_backend
from tuicc.connectivity.worker import ConnectivityWorker
from tuicc.layout_engine import compute_boxes
from tuicc.navigation import (
    tab_order,
    resolve_direction_move,
    global_shortcut_item,
    next_module_name,
    next_item_in_module,
    module_of_item,
    first_item_in_module,
)
from tuicc.render import draw_all, collect_nav_items, ACTION_HANDLERS
from tuicc.theme_setup import setup_theme
from tuicc.modules.launcher import resolve_selected, handle_typing_key


def main(stdscr):
    curses.curs_set(0)
    stdscr.nodelay(False)
    stdscr.timeout(1000)
    stdscr.keypad(True)

    cfg = load_config()
    theme_pairs = setup_theme(cfg.theme)
    provider = build_provider(cfg.provider_name)
    provider.mark_self()

    wifi_backend = build_wifi_backend(cfg.wifi_backend_name)
    bluetooth_backend = build_bluetooth_backend(cfg.bluetooth_backend_name)
    connectivity = ConnectivityWorker(wifi_backend, bluetooth_backend)
    connectivity.start()

    action_ctx = ActionContext(provider=provider, connectivity=connectivity)

    direction_keys = {
        cfg.keybinds["left"]: "left",
        cfg.keybinds["right"]: "right",
        cfg.keybinds["up"]: "up",
        cfg.keybinds["down"]: "down",
    }

    selected_id = None
    focus_id = None
    pending_confirm = None
    active_module = cfg.layout.boxes[0].name if cfg.layout.boxes else None

    typing_mode = False
    search_query = ""
    search_selected_index = 0
    saved_selected_id = None
    saved_active_module = None
    pending_moves = []
    MOVE_TIMEOUT_SECONDS = 8.0

    while True:
        stdscr.timeout(50 if (pending_moves or connectivity.has_pending()) else 1000)
        stdscr.erase()

        term_height, term_width = stdscr.getmaxyx()
        boxes = compute_boxes(cfg.layout, term_width, term_height)
        state = provider.get_state()

        if focus_id is None:
            focus_id = state.focused_region_id

        if pending_moves:
            front = pending_moves[0]
            current_ids = {w.id for r in state.regions for w in r.windows}
            new_ids = current_ids - front["known_ids"]
            if new_ids:
                new_id = next(iter(new_ids))
                provider.move_window_to_region(new_id, front["target_region"])
                pending_moves.pop(0)
            elif time.monotonic() - front["started_at"] > MOVE_TIMEOUT_SECONDS:
                pending_moves.pop(0)

        ctx = RenderContext(
            state=state,
            selected_id=selected_id,
            focus_id=focus_id,
            theme=theme_pairs,
            config=cfg,
            pending_confirm=pending_confirm,
            active_module=active_module,
            typing_mode=typing_mode,
            search_query=search_query,
            search_selected_index=search_selected_index,
            wifi_networks=connectivity.get_wifi_networks(),
            bluetooth_devices=connectivity.get_bluetooth_devices(),
            connectivity=connectivity,
        )

        items = collect_nav_items(cfg.layout, boxes, ctx)
        ordered = tab_order(items, mode=cfg.tab_order)

        still_valid = any(item.id == selected_id for item in ordered)
        if not still_valid:
            match = None
            for item in ordered:
                if item.target_kind == "region" and item.focus_target == state.focused_region_id:
                    match = item
                    break
            if match is not None:
                selected_id = match.id
            elif ordered:
                selected_id = ordered[0].id
            else:
                selected_id = None
            ctx.selected_id = selected_id

        selected_item = None
        for item in ordered:
            if item.id == selected_id:
                selected_item = item
                break

        draw_all(stdscr, cfg.layout, boxes, ctx)
        stdscr.refresh()

        key = stdscr.getch()

        if key == -1:
            continue

        if pending_confirm is not None:
            if key == ord("y"):
                subprocess.Popen(
                    pending_confirm["command"], shell=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                    start_new_session=True,
                )
                break
            elif key == ord("n"):
                pending_confirm = None
            continue

        global_item = global_shortcut_item(cfg.global_shortcuts, key)
        if global_item is not None:
            handler = ACTION_HANDLERS.get(global_item.target_kind)
            if handler is not None:
                should_exit, pending = handler(action_ctx, global_item, cfg)
                if pending is not None:
                    pending_confirm = pending
                if should_exit:
                    break
            continue

        if typing_mode:
            if key == cfg.keybinds["confirm"]:
                cmd = resolve_selected(search_query, search_selected_index)
                if cmd is not None:
                    known_ids = {w.id for r in state.regions for w in r.windows}
                    subprocess.Popen(
                        cmd, shell=True,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        stdin=subprocess.DEVNULL,
                        start_new_session=True,
                    )
                    pending_moves.append({
                        "target_region": focus_id,
                        "known_ids": known_ids,
                        "started_at": time.monotonic(),
                    })
                    typing_mode = False
                    selected_id = saved_selected_id
                    active_module = saved_active_module
            else:
                search_query, search_selected_index, typing_mode = handle_typing_key(
                    key, cfg, search_query, search_selected_index
                )
                if not typing_mode:
                    selected_id = saved_selected_id
                    active_module = saved_active_module
            continue

        if key == cfg.keybinds["confirm"] and selected_item is not None:
            handler = ACTION_HANDLERS.get(selected_item.target_kind)
            if handler is not None:
                should_exit, pending = handler(action_ctx, selected_item, cfg)
                if pending is not None:
                    pending_confirm = pending
                if should_exit:
                    break

        elif key == cfg.keybinds["switch_module"]:
            module_names = [box.name for box in cfg.layout.boxes]
            next_name = next_module_name(module_names, active_module)
            if next_name is not None:
                active_module = next_name
                first_item = first_item_in_module(ordered, active_module)
                if first_item is not None:
                    selected_id = first_item.id
                    if first_item.target_kind == "region":
                        focus_id = first_item.focus_target
        elif key == cfg.keybinds["tab"] and ordered:
            next_item = next_item_in_module(ordered, active_module, selected_id)
            if next_item is not None:
                selected_id = next_item.id
                if next_item.target_kind == "region":
                    focus_id = next_item.focus_target
        elif key in direction_keys and selected_item is not None:
            direction = direction_keys[key]
            next_item = resolve_direction_move(ordered, selected_item, direction, focus_id)

            if next_item is not None:
                selected_id = next_item.id
                active_module = module_of_item(next_item)
                if next_item.target_kind == "region":
                    focus_id = next_item.focus_target
        elif cfg.vim_mode and key == cfg.keybinds["insert"]:
            saved_selected_id = selected_id
            saved_active_module = active_module
            typing_mode = True
            search_query = ""
            search_selected_index = 0
            active_module = "launcher"
        elif not cfg.vim_mode and 32 <= key <= 126:
            saved_selected_id = selected_id
            saved_active_module = active_module
            typing_mode = True
            search_query = chr(key)
            search_selected_index = 0
            active_module = "launcher"


if __name__ == "__main__":
    curses.wrapper(main)
