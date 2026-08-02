"""Entry point: ties config, provider, layout engine and rendering together."""

import curses
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

from tuicc.config import (
    load_config,
    save_new_preset,
    available_preset_numbers,
    set_active_preset,
    build_layout_from_preset,
)
from tuicc.context import RenderContext
from tuicc.actions import ActionContext, spawn_detached
from tuicc.providers.registry import build_provider
from tuicc.connectivity.registry import build_wifi_backend, build_bluetooth_backend
from tuicc.connectivity.worker import ConnectivityWorker
from tuicc.layout import ModuleBox
from tuicc.layout_engine import compute_boxes
from tuicc.navigation import (
    tab_order,
    resolve_direction_move,
    resolve_selection,
    global_shortcut_item,
    next_module_name,
    next_item_in_module,
    first_item_in_module,
)
from tuicc.render import draw_all, collect_nav_items, ACTION_HANDLERS, MODULES
from tuicc.theme_setup import setup_theme
from tuicc.modules.launcher import resolve_selected, handle_typing_key, enter_typing_mode
from tuicc.pending_moves import resolve_pending_move
from tuicc.resize_mode import enter_resize, resize_step, move_step, cancel_resize


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
    claimed_window_ids = set()
    MOVE_TIMEOUT_SECONDS = 8.0

    last_restore_launch = 0.0
    RESTORE_STAGGER_SECONDS = 0.3

    resize_mode = False
    resize_box = None
    resize_snapshot = None
    resize_dimension = "size"
    resize_is_new_box = False
    resize_confirm_delete = False
    resize_message = None
    resize_message_until = 0.0

    spawn_picker_mode = False
    spawn_picker_choices = []

    def commit_resize():
        # Ends whatever resize-mode session is active, keeping the
        # box's current state (no revert) — same effect as pressing
        # Enter, used as a shared "wrap up before doing something else"
        # step so F1/F2/F3/F4 can interrupt an in-progress resize
        # instead of being swallowed by its hijack block.
        nonlocal resize_mode, resize_box, resize_snapshot, resize_dimension, resize_is_new_box, resize_confirm_delete
        resize_mode = False
        resize_box = None
        resize_snapshot = None
        resize_dimension = "size"
        resize_is_new_box = False
        resize_confirm_delete = False

    def do_spawn_picker():
        nonlocal spawn_picker_mode, spawn_picker_choices
        available = sorted(set(MODULES.keys()) - {b.name for b in cfg.layout.boxes})
        if available:
            spawn_picker_choices = available[:9]
            spawn_picker_mode = True

    def do_enter_resize():
        nonlocal resize_box, resize_snapshot, resize_mode
        if active_module is not None:
            resize_box = next((b for b in cfg.layout.boxes if b.name == active_module), None)
            if resize_box is not None:
                resize_snapshot = enter_resize(resize_box)
                resize_mode = True

    def do_save_layout():
        nonlocal resize_message, resize_message_until
        preset_number = save_new_preset(cfg.layout)
        set_active_preset(preset_number)
        cfg.preset_number = preset_number
        resize_message = f"Saved and applied preset {preset_number}"
        resize_message_until = time.monotonic() + 3.0

    def do_cycle_preset():
        nonlocal active_module, resize_message, resize_message_until
        numbers = available_preset_numbers()
        if numbers:
            idx = numbers.index(cfg.preset_number) if cfg.preset_number in numbers else -1
            next_number = numbers[(idx + 1) % len(numbers)]
            cfg.layout = build_layout_from_preset(next_number)
            set_active_preset(next_number)
            cfg.preset_number = next_number
            active_module = cfg.layout.boxes[0].name if cfg.layout.boxes else None
            resize_message = f"preset {next_number}"
            resize_message_until = time.monotonic() + 3.0

    while True:
        stdscr.timeout(
            50 if (pending_moves or action_ctx.restore_queue or connectivity.has_pending()
                   or resize_message is not None) else 1000
        )
        stdscr.erase()

        term_height, term_width = stdscr.getmaxyx()
        boxes = compute_boxes(cfg.layout, term_width, term_height)
        state = provider.get_state()

        if focus_id is None:
            focus_id = state.focused_region_id

        if action_ctx.restore_queue:
            now = time.monotonic()
            if now - last_restore_launch >= RESTORE_STAGGER_SECONDS:
                session_entry = action_ctx.restore_queue.pop(0)
                known_ids = {w.id for r in state.regions for w in r.windows}
                pid = spawn_detached(session_entry["cmdline"], shell_true=False)
                pending_entry = {
                    "target_region": session_entry["target_region"],
                    "known_ids": known_ids,
                    "pid": pid,
                    "app_id": session_entry["app_id"],
                    "started_at": now,
                    "floating": session_entry.get("floating", False),
                }
                if pending_entry["floating"]:
                    pending_entry["rect"] = (
                        session_entry["x"], session_entry["y"],
                        session_entry["w"], session_entry["h"],
                    )
                pending_moves.append(pending_entry)
                last_restore_launch = now

        if pending_moves:
            now = time.monotonic()
            current_windows = [w for r in state.regions for w in r.windows]
            still_pending = []
            for entry in pending_moves:
                match = resolve_pending_move(entry, current_windows, claimed_window_ids)
                if match is not None:
                    claimed_window_ids.add(match.id)
                    provider.move_window_to_region(match.id, entry["target_region"])
                    if entry.get("floating"):
                        provider.set_floating_geometry(match.id, entry["target_region"], entry["rect"])
                elif now - entry["started_at"] <= MOVE_TIMEOUT_SECONDS:
                    still_pending.append(entry)
            pending_moves = still_pending
            if not pending_moves:
                claimed_window_ids.clear()

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
        ctx.selected_item = selected_item

        draw_all(stdscr, cfg.layout, boxes, ctx)

        if spawn_picker_mode:
            choices = "  ".join(f"{i+1} {name}" for i, name in enumerate(spawn_picker_choices))
            hint = f"SPAWN — {choices}  Esc cancel"
            try:
                stdscr.addstr(0, 0, hint[:term_width], theme_pairs.get("urgent", 0))
            except curses.error:
                pass
        elif resize_mode:
            if resize_confirm_delete:
                hint = f"Delete {active_module}? y/n"
            else:
                action = "resize" if resize_dimension == "size" else "move"
                hint = (
                    f"[{resize_dimension.upper()}] {active_module} — ←→↑↓ {action}  "
                    f"M switch  Del delete  Enter done  Esc cancel  |  "
                    f"F1 spawn  F3 save preset  F4 cycle preset"
                )
            try:
                stdscr.addstr(0, 0, hint[:term_width], theme_pairs.get("urgent", 0))
            except curses.error:
                pass
        elif resize_message is not None:
            if time.monotonic() < resize_message_until:
                try:
                    stdscr.addstr(0, 0, resize_message[:term_width], theme_pairs.get("accent", 0))
                except curses.error:
                    pass
            else:
                resize_message = None

        stdscr.refresh()

        key = stdscr.getch()

        if key == -1:
            continue

        if pending_confirm is not None:
            if key == cfg.keybinds["confirm_yes"]:
                if "restore_entries" in pending_confirm:
                    if "kill_regions" in pending_confirm:
                        kill_regions = set(pending_confirm["kill_regions"])
                        for region in provider.get_state().regions:
                            if region.id in kill_regions:
                                for window in region.windows:
                                    provider.close_window(window.id)
                    action_ctx.restore_queue.extend(pending_confirm["restore_entries"])
                else:
                    spawn_detached(pending_confirm["command"], pending_confirm["shell_true"])
                exit_after = pending_confirm["exit_after_confirm"]
                pending_confirm = None
                if exit_after:
                    break
            elif key == cfg.keybinds["confirm_no"]:
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
                    # .desktop Exec= is spec'd to never be shell-interpreted.
                    pid = spawn_detached(cmd, shell_true=False)
                    pending_moves.append({
                        "target_region": focus_id,
                        "known_ids": known_ids,
                        "pid": pid,
                        # No pre-known app_id for a plain launcher spawn —
                        # unlike a saved-session restore, there's nothing
                        # to compare a resulting window's app_id against.
                        "app_id": None,
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

        if spawn_picker_mode:
            if ord("1") <= key < ord("1") + len(spawn_picker_choices):
                choice = spawn_picker_choices[key - ord("1")]
                new_box = ModuleBox(name=choice, x=0.4, y=0.4, w=0.2, h=0.2)
                cfg.layout.boxes.append(new_box)
                resize_box = new_box
                resize_snapshot = enter_resize(new_box)
                resize_dimension = "move"
                resize_is_new_box = True
                resize_mode = True
            spawn_picker_mode = False
            spawn_picker_choices = []
            continue

        if resize_mode:
            if resize_confirm_delete:
                if key == cfg.keybinds["confirm_yes"]:
                    cfg.layout.boxes.remove(resize_box)
                    commit_resize()
                elif key == cfg.keybinds["confirm_no"]:
                    resize_confirm_delete = False
                continue
            if key in direction_keys:
                x_cells, y_cells, w_cells, h_cells = boxes[active_module]
                direction = direction_keys[key]
                grow = direction in ("right", "down")
                if resize_dimension == "size":
                    dimension = "w" if direction in ("left", "right") else "h"
                    resize_step(resize_box, dimension, grow, term_width, term_height, x_cells, y_cells)
                else:
                    dimension = "x" if direction in ("left", "right") else "y"
                    move_step(resize_box, dimension, grow, term_width, term_height, w_cells, h_cells)
            elif key == cfg.keybinds["move_toggle"]:
                resize_dimension = "move" if resize_dimension == "size" else "size"
            elif key == cfg.keybinds["delete_box"]:
                resize_confirm_delete = True
            elif key == cfg.keybinds["confirm"]:
                # Keeps the change in cfg.layout (in memory only) and
                # returns to normal navigation — resize as many other
                # modules as you like before writing anything to disk
                # via save_layout.
                commit_resize()
            elif key == 27:  # Escape
                if resize_is_new_box:
                    # Nothing to revert to — it didn't exist before this
                    # resize-mode session, so cancelling removes it.
                    cfg.layout.boxes.remove(resize_box)
                else:
                    cancel_resize(resize_box, resize_snapshot)
                commit_resize()
            elif key == cfg.keybinds["spawn_box"]:
                commit_resize()
                do_spawn_picker()
            elif key == cfg.keybinds["resize"]:
                commit_resize()
                do_enter_resize()
            elif key == cfg.keybinds["save_layout"]:
                commit_resize()
                do_save_layout()
            elif key == cfg.keybinds["cycle_preset"]:
                commit_resize()
                do_cycle_preset()
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
                    selected_id, active_module, focus_id = resolve_selection(first_item, focus_id)
        elif key == cfg.keybinds["tab"] and ordered:
            next_item = next_item_in_module(ordered, active_module, selected_id)
            if next_item is not None:
                selected_id, active_module, focus_id = resolve_selection(next_item, focus_id)
        elif key in direction_keys and selected_item is not None:
            direction = direction_keys[key]
            next_item = resolve_direction_move(ordered, selected_item, direction, focus_id)
            if next_item is not None:
                selected_id, active_module, focus_id = resolve_selection(next_item, focus_id)
        elif key == cfg.keybinds["spawn_box"]:
            do_spawn_picker()
        elif key == cfg.keybinds["resize"] and active_module is not None:
            do_enter_resize()
        elif key == cfg.keybinds["save_layout"]:
            do_save_layout()
        elif key == cfg.keybinds["cycle_preset"]:
            do_cycle_preset()
        elif cfg.vim_mode and key == cfg.keybinds["insert"]:
            (saved_selected_id, saved_active_module, typing_mode,
             search_query, search_selected_index, active_module) = enter_typing_mode(
                selected_id, active_module, ""
            )
        elif not cfg.vim_mode and 32 <= key <= 126:
            (saved_selected_id, saved_active_module, typing_mode,
             search_query, search_selected_index, active_module) = enter_typing_mode(
                selected_id, active_module, chr(key)
            )


if __name__ == "__main__":
    curses.wrapper(main)
