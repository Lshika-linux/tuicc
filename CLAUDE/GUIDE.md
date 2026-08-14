# GUIDE.md

Architecture notes and the behavioral verification checklist for Claude Code sessions working in this repo. Imported from root `CLAUDE.md`.

## Architecture

Full architecture, config format, and the WM-provider-writing guide are in `README.md` — read that first for the big picture. This section only covers cross-file wiring that isn't obvious from any single file.

**Two same-named, different-typed `ctx` objects.** `draw(stdscr, box, ctx, module_name)` and `nav_items(box, ctx, module_name)` (every module's contract) receive a `RenderContext` (`context.py`) — per-frame render state (selection, theme, config, connectivity snapshot, etc). A `TARGET_KIND` handler's `handle(ctx, item, cfg)` (registered in `ACTION_HANDLERS`) receives an `ActionContext` (`actions.py`) — just `.provider` and `.connectivity`. Same parameter name, unrelated dataclasses; don't assume a module's `handle()` can reach into render state, or that `draw()` can call WM actions directly.

**Adding a module** touches `render.py`'s `MODULES` and `NAV_PROVIDERS` dicts only — never `draw_all()`/`collect_nav_items()` themselves (enforced by a comment at the top of `render.py`, not by code). If the module needs a custom `target_kind` (not just the generic `"region"`/`"window"` handled by `actions.py`'s `BASE_HANDLERS`), it registers its own handler(s) into `ACTION_HANDLERS` — either a single `TARGET_KIND` + `handle()` (see `quick_actions.py`, `power_menu.py`) or a `HANDLERS` dict for multiple kinds (see `connectivity.py`: `wifi_network`/`bluetooth_device`). `MODULES` has a second consumer besides `render.py` itself: resize mode's spawn picker (`main.py`) diffs `MODULES.keys()` against `cfg.layout.boxes` to offer only modules not currently placed — a new module is spawnable from inside tuicc for free, no extra wiring.

**Layout boxes are plain, independent x/y/w/h ratios** (`layout.py`) — no `right_of`/`below`/`above`/`bottom`/`cols`/`rows`/`fill_to` derivation system anymore (removed; see git history/README if you need the old reasoning). `compute_boxes()` (`layout_engine.py`) is a flat per-box loop, not a dependency resolver. Resizing or moving one box never affects another.

**Resize/spawn-picker/help/launcher mode state, and the pending-moves queue, live in their own files, not in `main.py` locals.** `ResizeState`/`SpawnPickerState` (`resize_mode.py`), `HelpState` (`help_mode.py`), and `LauncherState` (`modules/launcher.py`) are `@dataclass`es; `main.py` holds exactly one instance of each (`resize`, `spawn_picker`, `help_state`, `launcher`) and calls functions that take the instance and mutate it, same "pure function over an explicit value" style as `navigation.py`'s `resolve_selection`/`next_module_name`, deliberately not a class-with-methods rewrite. `main.py` still owns *when* to call them (which key means what, in what order) and the one piece of genuinely main-loop-level state a mode's own module can't own: `cfg`/`theme_pairs` themselves (applying a color edit calls `help_mode.apply_color_edit()` to validate+resolve, then `main.py` does `cfg.theme[role] = color` / `theme_setup.reassign_theme_pairs()` / `config.set_theme_color()` itself) and, for `LauncherState`, `active_module` (main.py sets `active_module = "launcher"` itself right next to each `launcher_mode.enter_typing_mode()` call — the dataclass owns `saved_active_module` for restoring it later, not the live value). The spawned-window matching queue follows the same pattern one level down: `pending_moves.py`'s `PendingMovesQueue` dataclass (`moves` in `main.py`) plus `queue_restore_entry()`/`queue_launcher_spawn()`/`promote_restore_queue()`/`process()` replace what used to be ~72 lines of inline queue bookkeeping — `process()` takes `dismissed` as an explicit param rather than owning it, since `dismissed`'s keypress-reset timing is loop-lifecycle bookkeeping that stays in `main.py`. `pending_confirm` deliberately stays a plain dict, not a dataclass — four different producer call sites (`sessions.py`, `power_menu.py`, `quick_actions.py`) build it with different key subsets — but its *resolution* (`handle_pending_confirm()`) and the handler-dispatch code duplicated at two call sites (`dispatch_action()`) both moved to `actions.py`, which already owned the handler contract; both are pure, value-returning functions in the same `(should_dismiss, pending)` shape every handler already returns, the one carve-out from the "mutate a dataclass" convention this whole paragraph otherwise describes. No module's `draw()` reacts to any of this — `resize`/`spawn_picker`'s hint line, the editing box's highlight outline, and `help_state`'s whole panel are drawn directly onto `stdscr` (via `render_utils.draw_status_line()`, `resize_mode.draw_editing_highlight()`, and `help_mode.draw()` respectively) after/instead of `draw_all()`, not through `RenderContext`. `ResizeState` is a **two-level session**, not a single-shot mode: `active=True, editing=False` is *browsing* — the session (opened by `F2`, `resize_mode.enter_edit_mode()`) is open but no module is being resized/moved, and normal navigation (`Tab`/`Shift+Tab` and their duplicate keys) just picks which module `active_module` refers to, same as fully outside the session; `active=True, editing=True` is *editing* one specific box (`resize_mode.enter_box_editing()`, entered via `confirm` on the active module from browsing, or directly from full normal navigation for `spawn_box`), the module's actual resize/move behavior. `confirm`/`Escape` at the editing level (`resize_mode.commit_box_editing()`/`escape_box_editing()`) return to *browsing*, not out of the session; only `Escape`/`F3` at the browsing level (`resize_mode.exit_edit_mode()`) leave it entirely. `delete_box` (`resize_mode.request_delete()`/`confirm_delete_yes()`/`confirm_delete_no()`) works at both levels. `main.py` dispatches browsing as its own non-`mode_stack` hijack tier (`resize.active and not resize.editing`) — deliberately, permanently, not a `mode_stack` entry (see `CLAUDE/NOTES/design-decisions.md#mode-stack-phase-1`'s Phase 5): it only intercepts `confirm`/`delete_box`/`Escape` and otherwise doesn't `continue`, falling through to the normal bottom dispatch chain so `Tab`/`Shift+Tab`/direction-key movement/`F1`/`F3`/`F4`/`F5`/`F6` keep working unchanged inside a browsing session — the plain `still_claiming` bool every `MODE_HANDLERS` entry returns can't express "fall through", so browsing simply never goes on the stack, same treatment as sessions/media/sysmon's own two-level expansion. Editing itself IS a real `mode_stack` entry (`"resize_editing"`, entered via the shared `do_enter_box_editing()` function), since it always consumes every key — a true modal, unlike browsing. `spawn_box`/`resize`/`save_layout`/`cycle_preset`/`new_preset`/`help` (F6, F2-F5, F1) are each backed by one `do_*()` function, called from both the normal dispatch chain and again, on a handoff, from `handle_resize_editing` — all six are now plain module-level functions in `main.py` taking `loop_state` (and whatever else they need) as explicit params, the end state of the `LoopState` migration (`CLAUDE/NOTES/design-decisions.md#loopstate-migration`) that replaced every closure/`nonlocal` main() used to lean on. Editing's key dispatch itself lives in `resize_mode.handle_editing_key()` (not `main.py` — extracted the same session `app_setup.py` split `main()`'s setup block out, see that commit for the general reasoning), which can't call `main.py`'s `do_*()` functions directly (`resize_mode.py` must never call back into `main.py`); instead each F-key branch returns `EditKeyResult(still_claiming=True, handoff="save_layout")` (etc — the six handoff strings are the same names as the matching `cfg.keybinds` entries, one vocabulary, not two) via a shared `_handoff()` helper local to `resize_mode.py` (`commit_box_editing(state)` first, so an in-progress edit is committed rather than silently discarded — change `_handoff()`, not any one branch, to change that step). `main.py`'s `handle_resize_editing` is a thin wrapper: it reads `result.handoff`, and if set, pops `"resize_editing"` off `mode_stack` and looks the name up in its own `HANDOFF_TARGETS` dict to call the right `do_*()` function — the sync point that used to be one function (`handoff()`) is now two (`_handoff()` in `resize_mode.py`, `HANDOFF_TARGETS` in `main.py`), matched by the same six handoff-name strings on both sides. `do_save_layout()`/`do_cycle_preset()`/`do_new_preset()` additionally call `resize_mode.exit_edit_mode(resize)` themselves, so triggering them from either session level always ends the whole session, not just the current box's edit. `EditKeyResult` also carries `deleted_name` (set only when `confirm_delete_yes` just ran) since resetting `active_module` when the deleted box was the active one is main-loop-owned state `resize_mode.py` can't touch — `main.py`'s wrapper does that check itself. `do_new_preset()` forks the current layout into a brand-new preset number (`config.py`'s `save_new_preset()`, never overwrites an existing file) and switches to it, rather than overwriting the active preset the way `do_save_layout()` does — the gap this closes (there was no way to start a new preset from a layout you like without hand-editing files) was found live, not part of any original design. `config.py`'s `set_active_preset()`/`set_theme_color()` are the two places in the codebase that patch `config.toml` by hand (both go through the shared `_patch_config_line()`, one `key = value` line at a time) instead of going through `tomllib`/`tomli_w`, specifically to avoid stripping the file's comments.

**Nothing is cached per-frame.** `compute_boxes()`, `provider.get_state()`, and `collect_nav_items()` all rerun every loop iteration in `main.py`. Deliberate simplicity-over-performance call — item counts are small enough (dozens) for it not to matter. Don't add caching here without a measured reason.

**Global shortcuts bypass normal input routing.** `cfg.global_shortcuts` (built from `power_menu.action.shortcut` entries) is checked in `main.py`'s loop immediately after the `pending_confirm` check, before typing-mode or module-local key handling — it fires regardless of which module is active. Collisions (against each other or against `[navigation.keys]`) are caught at config load time, not at keypress time.

**`mark_self()` is provider-optional, not abstract.** `Provider.mark_self()` (`providers/base.py`) defaults to a no-op; only sway/i3 implement it via WM marks. The mark string is `_tuicc_self_<pid>`, not a fixed literal — sway/i3 marks must be globally unique, so a shared string would let a second tuicc instance silently steal the first instance's mark. Filtering matches the *prefix*, so every running instance still excludes every other tuicc window, not just its own. `mark_self()` takes an optional `app_id` (from `[wm] self_app_id`, see the launch commands in README's "Summoning tuicc" section) — when given, sway/i3 mark by WM criteria (`app_id`/`class` respectively) instead of "whatever's focused at call time," which actually fixes the race multiple back-to-back instances can hit; `app_id=None` (no config set) falls back to the old focus-based assumption, left as a known, documented limitation for that case only. See `CLAUDE/NOTES/known-limitations.md#mark-self-focus-race` for the concrete failure mode this fallback hits, and `CLAUDE/NOTES/design-decisions.md#optional-provider-methods` for why this whole family of `Provider` methods defaults to a no-op instead of raising.

**R1's lifecycle split: "dismiss" vs. "quit"** (see `CLAUDE/VISION.md` section 2 for the full model). tuicc's main loop, once started, is never expected to exit in normal use — every handler's `should_dismiss` bit (renamed from `should_exit`; see `actions.py`'s module docstring) now means "call `Provider.dismiss_self()` and keep looping," not "terminate." `main.py`'s `while True:` has no `break` left in it anywhere; the only way out is an unhandled exception, which in practice means Ctrl+C (`KeyboardInterrupt`), caught quietly at the very bottom of `main.py` and preceded by a `try/finally` around the loop for cleanup (`StatusWorker.stop()` today; a future D-Bus agent unregister has the same slot). `dismiss_self()` joins `mark_self()`/`resolve_pid()`/`set_floating_geometry()` as a non-abstract, optional `Provider` method — sway/i3 implement it by matching the same `_tuicc_self_<pid>` mark `mark_self()` already applies, via `[con_mark=...] move scratchpad`, so it's immune to the same focus-timing race `mark_self()`'s own fallback path has. `cfg.return_to_origin` (default off) makes top-level Escape also call `provider.focus_region()` back to wherever focus was before tuicc's own window was last focused — tracked as a rolling "value being replaced" in `main.py`'s loop (`origin_region_id`/`last_focused_region_id`), not read at Escape time, because `WMState.focused_region_id` already reflects tuicc's *own* region whenever tuicc itself has WM focus (`parse_tree()` doesn't filter self out of that particular field, only out of each region's `windows` list). That same `origin_region_id`/`last_focused_region_id` block also resets `selected_id` (and, via the recovery logic right below it, `focus_id`) on any real focus change — except one: `pending_moves.process()` returns whether it called `focus_self()` this round (tuicc reclaiming its own focus after a spawn/restore resolves, a real transition but a self-inflicted one), and `main.py`'s `expect_focus_reclaim` flag makes the detector skip the reset for exactly that frame. Without it, a `focus_self()` call landing between selecting a sidebar workspace and confirming a launcher spawn silently overwrote `focus_id` to wherever real focus happened to land, so the spawn targeted the wrong workspace — found live, not from today's changes but pre-existing since the detector was written. See `CLAUDE/NOTES/design-decisions.md#dismiss-vs-quit` for the lifecycle model this is built on.

**`Provider.focus_self()`** (same mark-based pattern as `dismiss_self()`) is called once per resolved entry in `main.py`'s `pending_moves` loop, right after `move_window_to_region()`/`set_floating_geometry()` — not optional-feeling like the rest of this tier: most WMs give a newly-mapped window keyboard focus regardless of stacking order, and tuicc is a floating window that stays visually on top, so without this call every launcher spawn (and session restore) silently steals input away from tuicc on the very first window, not as an edge case. See its docstring in `providers/base.py`, and `CLAUDE/NOTES/wm-quirks.md#focus-on-map-stealing`, for the full severity note.

**`cfg.fullscreen_only`** (`[wm]` in config.toml, packaged default `true`, missing-key fallback `false` — deliberate: a fresh install gets the full experience, an existing config upgrading silently keeps its old behavior) fixes a related but distinct problem: sway/i3 both drop a container back to plain floating the instant its keyboard focus moves away to a new sibling window — which used to happen unavoidably on every spawn, since new windows land wherever real WM focus currently is (typically tuicc's own workspace) and, by default, take focus themselves the moment they map. `Provider.no_focus_next_window(pid)` (see its docstring in `providers/base.py`) now prevents that focus move at the source — `for_window [pid=<spawned pid>] no_focus`, sent right after `spawn_detached()` returns, before the window has had a chance to map — and, found live, preventing the auto-focus turns out to prevent the fullscreen drop too: it's not an independent side effect of a new window merely existing, it's specifically caused by focus leaving the fullscreen container. Called from both spawn paths (`main.py`'s launcher-confirm site, `pending_moves.promote_restore_queue()`) right after the pid is known. `pid` was deliberately chosen over `class`/`app_id` as the criteria: those would keep matching every *future* window of that same app for the rest of the WM session (open it again later, outside tuicc, and it silently won't auto-focus either), where `pid` matches only that one process, essentially never again — sway/i3 have no IPC command to remove a `for_window` rule once added (only a full WM restart clears them), so these rules accumulate one-per-spawn for the session's lifetime, but at the kernel's modern default `pid_max` (4194304) a collision with a stale rule needs years of continuous uptime to become likely even under heavy load, and its damage if it ever happens is cosmetic (one unrelated window doesn't auto-focus, Tab/click fixes it) — see the method's docstring, and `CLAUDE/NOTES/wm-quirks.md#fullscreen-drop-on-map`/`#no-focus-pid-criteria`, for the full tradeoff writeup.

This is NOT a replacement for `pending_moves.process()`'s existing `focus_self(fullscreen=...)` reassert (threaded from `fullscreen_only`, both on a successful match and the give-up-unmatched path) and `install.sh`'s floating-geometry pinning below — those stay as the fallback for everything `no_focus_next_window` can't cover: apps that fork/exec into a child with a different pid than `spawn_detached()` returned (same known limitation `resolve_pending_move`'s pid tier already has — the rule silently never matches, not an error; see `CLAUDE/NOTES/known-limitations.md#fork-exec-pid-mismatch`), a theoretical map-before-IPC-lands timing race, and any future provider that just doesn't implement it. `focus_self()` chains `, fullscreen enable` onto the same `[con_mark=...] focus` command when `fullscreen_only` is set; the `contrib/*/tuicc_toggle.py` scripts do the same on manual show/focus, reading `fullscreen_only` from `config.toml` themselves at runtime via `tomllib` rather than hardcoding it, so the automatic (spawn/restore), manual (toggle key), and now WM-side (`no_focus_next_window`) paths all stay in sync with one config value. **Reasserting fullscreen alone still isn't enough to make a drop that does happen look clean**: a floating container's underlying (non-fullscreen) geometry is whatever the WM defaults a new floating window to — usually a small centered box — so a visible "blink" would be a shrink-then-expand pop, not just a flicker, even once `focus_self(fullscreen=True)` recovers it within one poll cycle. `install.sh`'s generated `for_window` rules (and the equivalent snippets in README's "Summoning tuicc") work around this by adding `move position 0 0` and `resize set 100 ppt 100 ppt` before `fullscreen enable` — pinning the floating container's own geometry to the full output once, at window-creation time, so any drop that does slip through only ever loses the border/always-on-top treatment, never its size. Verified live: `resize set 100 ppt 100 ppt` + `move position 0 0` against a 1920x1080 output produced a 1916x1048 floating rect (sway; i3 shares the same `resize set <w> [px|ppt] <h> [px|ppt]` syntax). Only applied when `fullscreen_only` is true — plain floating mode (`fullscreen_only = false`) deliberately leaves the WM's own default floating size alone.

**These are one `for_window [criteria] <single action>` rule per action, not one rule with actions comma-chained** — found live testing on this session's own sandbox (swayfx 0.5.3, based on sway 1.11.0, not vanilla sway): `for_window [crit] floating enable, fullscreen enable` (or the fuller chain with move/resize too) as a single rule reports every action as `"success": true`, but the window that actually maps ends up neither floating-sized nor fullscreen — `fullscreen_mode` stays `0`. Splitting the exact same actions into separate `for_window` rules against the same criteria reliably produces the correct end state, confirmed 3/3 repeated tries. Whether this is a swayfx-specific quirk or also affects vanilla sway/i3 is **not yet confirmed** — flag it in `CLAUDE/I3_TESTING_LOG.md` either way if you're testing on real i3/sway. The split form is what `install.sh`/README generate now regardless of WM, since it costs nothing extra even where the chained form would've worked fine.

## Expected behavior — verification checklist

This section is a behavioral contract, not architecture prose: every
item below is meant to be individually, objectively testable against a
live sway or i3 session. Written for exactly this situation — pointing
a fresh Claude Code session at this repo on a machine that hasn't been
live-tested yet — so it knows precisely what "working" means without
re-deriving it from the code, and knows which gaps are *already*
known/reproduced (don't rediscover, either fix or re-confirm) versus
genuinely unverified on that machine's specific WM/app combination.

Diagnostic technique for all of the below: poll `swaymsg -t get_tree`
(sway) / `i3-msg -t get_tree` (i3) on a short interval (0.5s is plenty
— main.py itself polls every 50ms while a spawn/restore is pending, so
0.5s won't miss a transition lasting longer than one frame), walking
the tree and printing, per window: `ws=`/`app_id=` (or `class=` on
i3)/`floating=`/`fullscreen_mode=`/`focused=`/`marks=`, timestamped,
`tee`'d to a log file. `python3 -u` (unbuffered) if piping to `tee` and
expecting to `Ctrl+C` partway through — buffered stdout loses the tail
otherwise. This exact technique found and confirmed every bug fixed
this session; don't invent a different one.

### Launch & lifecycle

- Cold start (`python main.py`, or via the WM keybind/toggle script)
  with `[wm] fullscreen_only = true` (the packaged default): tuicc
  appears **fullscreen immediately** — no manual toggle, no visible
  "floating then snaps to fullscreen" step.
- Cold start with `fullscreen_only = false`: tuicc appears as a plain
  floating window, sized/positioned however the WM defaults a new
  floating window (not forced full-screen) — this is the intentional
  degraded-on-purpose case, not a bug.
- Summon via the toggle key while tuicc is already running (hidden in
  the scratchpad): un-hides it, `fullscreen enable` re-applied if
  `fullscreen_only = true` (see `contrib/*/tuicc_toggle.py` — they
  read this value from `config.toml` at runtime, don't hardcode it).
- Dismiss (Enter on most actions, or Escape at the top level with no
  menu/mode open): hides tuicc to the scratchpad — verify with `ps aux
  | grep main.py` (or equivalent) that the **process is still running**
  afterward, not exited. There is deliberately no quit keybind or quit
  menu entry — the only way to actually end the process is `Ctrl+C` in
  its terminal.
- `return_to_origin = true`: top-level Escape *also* focuses back to
  whatever window/region had focus right before tuicc's own window was
  last focused, on top of the normal dismiss. `false` (default): dismiss
  only, no extra focus-back.

### Focus & fullscreen, under a launcher spawn or session restore

- After confirming a launcher entry (or triggering a session restore),
  tuicc **always** ends up focused again once the spawned window's
  entry resolves (matches and moves) or times out (`MOVE_TIMEOUT_SECONDS
  = 8.0s` — see `pending_moves.py`) — never left with keyboard input
  silently going to the newly-spawned window while tuicc still visually
  looks focused on top.
- With `fullscreen_only = true`: tuicc is back in `fullscreen_mode: 1`
  by the time focus returns, every time — not just "usually." Any
  visible dip to `fullscreen_mode: 0` should be brief (ideally zero,
  see `no_focus_next_window` below) and must **not** show a visible
  shrink-to-small-box-then-expand — check the window's `rect` during
  any dip; it should stay full-output-sized (a `for_window` rule with
  `move position 0 0, resize set 100 ppt 100 ppt` baked in should
  guarantee this regardless of `fullscreen_mode`'s momentary state —
  verify your WM config actually has this, `install.sh` sets it up,
  a hand-written `for_window` rule from before this feature landed
  won't).
- `Provider.no_focus_next_window(pid)` (`for_window [pid=<pid>]
  no_focus`, sway/i3 both) is sent right after every spawn — verify via
  the tree poll that the *newly spawned window itself* never shows
  `focused: true` at any point before tuicc reclaims focus. If it does
  briefly show focused, that's the fallback path being exercised (not
  necessarily a bug — but worth noting which app triggered it, since
  it usually means that app's real pid didn't match what
  `spawn_detached()` captured — see "Known open issues" below).
- While tuicc is **dismissed** mid-spawn-resolution (you hid it before
  the spawn finished resolving): `focus_self()` must **not** fire and
  silently un-hide tuicc — the window stays hidden, `moves.entries`
  still resolves/times out in the background, unaffected.

### Window placement — launcher spawns

- Select a target workspace/region in the sidebar (Tab/arrows, not
  typing), *then* type to search and confirm a launcher entry: the
  spawned app must land on the **selected** region — not on whatever
  workspace tuicc itself happens to be running on. Test this
  specifically with tuicc living on a *different* workspace than the
  target (this exact scenario was the original bug — see
  `expect_focus_reclaim` above).
- Repeat with several launcher spawns back-to-back, in quick
  succession, before the first one has necessarily resolved — none
  should end up on the wrong target, none should get silently dropped
  or swapped with another's target.
- Apps whose real WM_CLASS/`app_id` doesn't match their `.desktop`
  entry's hint (common for Python/Electron apps launched via a bare
  interpreter — e.g. a real app reporting itself as `python3`) still
  resolve correctly via the pid tier, on **both** sway (native
  `Window.pid`) and i3 (`resolve_pid()`'s on-demand X11 `_NET_WM_PID`
  lookup — may take a moment longer than sway, budgeted up to
  `PID_GRACE_SECONDS = 6.0s` before downgrading to the app_id tier).

### Window placement — session restore

- Save a session with **5+ windows across different real apps**
  (mix of light apps like terminals and heavy/slow-starting ones —
  browsers, Electron apps, office suites) spread across several target
  regions, some floating, some tiled. Load it back.
- Restores are staggered `RESTORE_STAGGER_SECONDS = 0.3s` apart, not
  all fired in the same frame — verify via the tree poll that new
  processes actually launch staggered, not bunched.
- Every window ends up on its saved `target_region`; floating windows
  additionally end up at their saved relative position/size (normalized
  0..1 rect, see `set_floating_geometry()`), not just floating-enabled
  at some default WM geometry.
- Loading a session offers a kill-existing-windows-on-target-regions
  confirm first (see "Confirm dialogs" below, `"kill_regions" in
  pending`) — verify both accepting and declining behave correctly:
  accepting closes the named regions' existing windows before
  restoring; declining restores anyway without closing anything.
- **Specifically hunt for apps that never get relocated at all** —
  landing on tuicc's own workspace and staying there past
  `MOVE_TIMEOUT_SECONDS`. This is a known, live-reproduced (not yet
  fixed) failure mode — see "Known open issues" below. If you find a
  real app that triggers it, note which one and whether it's
  reproducible 3/3 tries; that's more valuable than just re-confirming
  the happy path works.

### Confirm dialogs

- Every `[[quick_actions.action]]` / `[[power_menu.action]]` entry with
  `confirm = true` must show a Y/N prompt before running — whether
  triggered by normal selection + Enter, or via its `shortcut` (global,
  works from anywhere — see "Global shortcuts bypass normal input
  routing" above). `confirm_text` shows if set; otherwise a plain Y/N.
- While a confirm dialog is open: only `confirm_yes`/`confirm_no`
  (config-bound keys, default `y`/`n`) do anything, **plus `confirm`
  (Enter) as an alternate to `confirm_yes` specifically** — "yes" is
  itself a kind of confirm, and Enter already means confirm everywhere
  else in tuicc, so it's accepted here too; `confirm_no` has no such
  alternate, only its own bound key answers "no". Every other key,
  including global shortcuts, must leave the dialog open unchanged
  (see `handle_pending_confirm()`'s "any other key" branch). The same
  confirm-or-confirm_yes pattern applies at the other two Y/N sites in
  the codebase too — bluetooth pairing confirm and resize mode's
  delete-box confirm (both in `main.py`) — not just the general
  `pending_confirm` dict `handle_pending_confirm()` resolves.
- `confirm_yes` runs the action, then dismisses tuicc **unless**
  `exit_after = false` was set on that action (default: dismiss).
  `confirm_no` cancels — no command runs, dialog closes, tuicc does not
  dismiss.
- Test with the packaged defaults (Lock/Logout/Reboot/Shutdown in both
  `quick_actions` and `power_menu`) *and* with `shell_true = true`
  entries if your config.toml has any customized ones — those run
  through a real shell, worth confirming the exact command shown in
  hover/preview matches what actually executes.

### Known open issues (as of this writing — confirm current status, don't assume fixed)

- **Fork/exec pid-mismatch apps silently fail to relocate.** Live-
  reproduced (this session, deterministically, 3/3 runs) with a wrapper
  script that backgrounds its real child instead of exec-replacing
  itself: `spawn_detached()`'s captured pid never matches the window's
  real owning pid, and if the app's real `app_id` *also* doesn't match
  the launcher's `.desktop`-derived hint, `resolve_pending_move()`
  never reaches its "any remaining unclaimed window" fallback tier
  (that tier only ever triggers when **both** `pid` and `app_id` are
  `None` on the entry — an app_id-mismatch-but-not-`None` entry never
  gets there). The window just sits on tuicc's own workspace,
  unrelocated, forever, once `MOVE_TIMEOUT_SECONDS` passes and the
  entry is silently dropped. Suspected real-world trigger: OnlyOffice
  (reported live, not yet confirmed to be this exact mechanism — could
  also just be a very slow cold start exceeding the timeout; both are
  worth distinguishing by testing). Proposed fix, not yet implemented:
  a last-resort escalation to the "any remaining" tier right before an
  entry would otherwise time out, when exactly one unclaimed new window
  is still sitting there — low collision risk since it only fires at
  the very end of the timeout window, not as an early shortcut.
- **Resummon keeps a stale workspace selection.** Reported live (this
  session, both sway and i3): dismissing tuicc and re-summoning it
  sometimes shows the sidebar still selected on whatever workspace was
  selected *before* dismiss, not necessarily matching real current WM
  focus. Never conclusively root-caused — could be a real bug in the
  focus-change detector, or could be correct behavior that looked like
  a bug because real focus happened to coincide. Needs a clean,
  deliberate repro (dismiss tuicc while workspace A is selected in the
  sidebar, switch WM focus to workspace B via a non-tuicc means,
  re-summon, check what's selected) before concluding anything.
- **~~`sessions.py` arrow-key navigation bug~~ — FIXED.** Reported
  2026-08-02 as "arrow-key navigation misbehaves specifically within
  the sessions module," not investigated at the time. Very likely the
  same root cause found and fixed live this session (not the same
  investigation, but the mechanism matches exactly): `Up`/`Down` are
  plain duplicates of `Tab`/`Shift+Tab` (see `[navigation.keys]` in
  `defaults/config.toml`), and Tab/Shift+Tab used to get permanently
  stuck the moment selection reached a module immediately followed —
  in position order — by a module with zero nav items. Power Menu is
  followed by Launcher (always empty; typing captures it instead) in
  the packaged default preset, and Sessions sits right after that, so
  reaching Sessions via Tab/arrows from Power Menu hit exactly this
  dead end — which would present as "arrow keys don't work right near
  Sessions," matching the original report closely enough that this is
  almost certainly it. Root cause: `next_item_in_module` returning
  `None` (module exhausted) triggered exactly one `next_module_name` +
  `first_item_in_module` lookup; if that one module was empty, the
  keypress silently did nothing, and since `selected_id`/`active_module`
  never changed, every further press recomputed the identical dead
  end — not intermittent, not timing-sensitive, 100% reproducible.
  Fixed via `navigation.py`'s `next_item_across_modules()`/
  `prev_item_across_modules()`, which keep walking forward/backward
  through `module_names` (wrapping, bounded) until one with an actual
  item turns up, instead of trying exactly one. Verified live: the
  exact stuck repro (navigate to Power Menu's last item, single Tab)
  now correctly lands on Sessions' first item.

### Reporting findings back

If you're running this checklist on real i3 hardware (as opposed to
developing against sway without i3 access, the more common case this
repo is worked on from): after working through the items above, append
a dated entry to **`CLAUDE/I3_TESTING_LOG.md`** (see that file for the exact
template) and commit + push it, along with any code fixes you made.
That file is the loop back to whichever session doesn't have i3
hardware access — it reads the log via `git pull`, not by asking you
directly, so make entries concrete and self-contained rather than
assuming follow-up context.
