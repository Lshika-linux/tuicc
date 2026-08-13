# WM quirks

Confirmed sway/i3 behavioral findings that inform tuicc's provider code. Each section is a stable anchor other docs/docstrings can point to (`CLAUDE/NOTES/wm-quirks.md#anchor`).

## Focus-on-map stealing {#focus-on-map-stealing}

Most WMs give a newly-mapped window keyboard focus regardless of stacking order. tuicc is a floating window that stays visually on top of whatever workspace it's summoned into, so without an explicit reclaim, the moment something is spawned from the launcher, the next keystrokes silently go to the new window sitting hidden underneath tuicc — even though tuicc still *looks* focused on screen. This happens on the very first spawn, not as an edge case; it breaks the launcher's "spawn without losing your place" promise immediately.

`Provider.focus_self()` (`providers/base.py`) is the reactive fix: called once per resolved entry in `pending_moves.process()`, right after `move_window_to_region()`/`set_floating_geometry()`. `Provider.no_focus_next_window(pid)` is the proactive complement — see below.

## Fullscreen drop on sibling window map {#fullscreen-drop-on-map}

sway/i3 both drop a fullscreen container back to plain floating the instant its keyboard focus moves away to a new sibling window. This used to happen unavoidably on every spawn: new windows land wherever real WM focus currently is (typically tuicc's own workspace) and, by default, take focus themselves the moment they map.

`Provider.no_focus_next_window(pid)` prevents the focus move at the source — `for_window [pid=<spawned pid>] no_focus`, sent right after `spawn_detached()` returns, before the window has had a chance to map. Confirmed: preventing the auto-focus also prevents the fullscreen drop — the drop is not an independent side effect of a new window merely existing, it's specifically caused by focus leaving the fullscreen container.

Reasserting fullscreen alone (`focus_self(fullscreen=True)`) isn't enough to make a drop that does happen look clean: a floating container's underlying (non-fullscreen) geometry is whatever the WM defaults a new floating window to — usually a small centered box — so a visible "blink" would be a shrink-then-expand pop, not just a flicker, even once fullscreen is recovered within one poll cycle. `install.sh`'s generated `for_window` rules work around this by adding `move position 0 0` and `resize set 100 ppt 100 ppt` before `fullscreen enable`, pinning the floating container's geometry to the full output once, at window-creation time — confirmed on sway (swayfx 0.5.3, based on sway 1.11.0) that this combination against a 1920x1080 output produces a 1916x1048 floating rect; i3 shares the same `resize set <w> [px|ppt] <h> [px|ppt]` syntax.

## Fullscreen suppresses tiling layout for the workspace {#fullscreen-suppresses-layout}

A container with `fullscreen_mode=1` suppresses tiling-layout computation entirely for the rest of its workspace, so a sibling window that maps onto the *same* workspace as a persistently-fullscreen tuicc never gets a real rect computed for it — stuck at `{0,0,0,0}` — for as long as tuicc stays fullscreen without interruption. Confirmed live: `preview.py` (which draws windows from their rect) shows nothing for that window, indefinitely, until something forces sway to actually run a layout pass for that workspace (observed happening incidentally on a manual dismiss+resummon, which drops tuicc out of fullscreen entirely and back).

`Provider.focus_self(force_relayout=True)` works around this deliberately: it briefly toggles fullscreen off and back on to force a layout pass, right when a spawn/restore match resolves onto tuicc's own workspace, instead of leaving the window's rect broken until an unrelated dismiss/resummon fixes it as a side effect. Only meaningful combined with `fullscreen=True`; relies on the floating geometry pin above (`install.sh`'s `move position 0 0, resize set 100 ppt 100 ppt`) so the brief non-fullscreen instant it causes has no size to visibly blink through.

## `no_focus` uses pid criteria, and what that costs {#no-focus-pid-criteria}

`no_focus_next_window(pid)` is implemented via `for_window [pid=<pid>] no_focus` on sway/i3 (`no_focus` is i3-native; sway adopted the same directive and criteria syntax), deliberately keyed on `pid` rather than `class`/`app_id`. `class`/`app_id` would keep matching every *future* window of that same app for the rest of the WM session — open it again later, outside tuicc, and it silently won't auto-focus either. `pid` matches exactly the one process this call is about and, barring pid reuse, essentially never anything else again.

Cost: sway/i3 have no IPC command to remove a `for_window` rule once added (only a full WM restart clears them), so these rules accumulate one per spawn for the process's lifetime. At the kernel's modern default `pid_max` (4194304, e.g. NixOS/most current distros), a collision with a stale rule needs years of continuous uptime to become likely even under heavy process-creation load; only the old 32768 default makes this a realistic (if still rare-per-process) concern. A collision's actual damage, if it ever happens, is cosmetic — one unrelated future window silently doesn't auto-focus on creation (Tab/click fixes it), not data loss.

Also silently a no-op for apps that fork/exec into a child with a different pid than the one `spawn_detached()` returned — see `CLAUDE/NOTES/known-limitations.md#fork-exec-pid-mismatch`.

## `for_window` rules must be split, not comma-chained {#for-window-chained-actions}

`for_window [crit] floating enable, fullscreen enable` (or the fuller chain with move/resize too) as a single rule reports every action as `"success": true`, but the window that actually maps ends up neither floating-sized nor fullscreen — `fullscreen_mode` stays `0`. Splitting the exact same actions into separate `for_window` rules against the same criteria reliably produces the correct end state — confirmed 3/3 repeated tries on swayfx 0.5.3 (based on sway 1.11.0, not vanilla sway). Whether this is a swayfx-specific quirk or also affects vanilla sway/i3 is not yet confirmed — see `CLAUDE/I3_TESTING_LOG.md`. `install.sh`/README generate the split form regardless of WM, since it costs nothing extra even where the chained form would've worked fine.
