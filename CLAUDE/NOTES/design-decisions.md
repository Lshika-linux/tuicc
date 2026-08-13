# Design decisions

Why things are shaped the way they are, when the reasoning isn't obvious from reading the code. Each section is a stable anchor (`CLAUDE/NOTES/design-decisions.md#anchor`).

## Dismiss vs. quit {#dismiss-vs-quit}

tuicc's main loop, once started, is never expected to exit in normal use. Every handler's `should_dismiss` bit (renamed from `should_exit`) means "call `Provider.dismiss_self()` and keep looping," not "terminate." `main.py`'s `while True:` has no `break` in it anywhere — the only way out is an unhandled exception, which in practice means Ctrl+C, caught quietly at the bottom of `main.py` with a `try/finally` around the loop for cleanup. See `CLAUDE/VISION.md` section 2 for the full lifecycle model this comes from.

## Optional `Provider` methods default to a documented no-op, not `abstractmethod` {#optional-provider-methods}

`mark_self()`, `dismiss_self()`, `focus_self()`, `no_focus_next_window()`, `resolve_pid()`, and `set_floating_geometry()` on `Provider` (`providers/base.py`) all default to a no-op (or `None`) rather than being abstract. Each covers a WM capability that not every window manager has an equivalent for (marks, scratchpad-style hiding, focus stealing back, per-window no-autofocus hints, non-native pid lookup, floating geometry). A provider for a WM lacking the concept simply inherits the default and accepts the resulting degraded case — tuicc shows up in its own preview, a launcher spawn's focus briefly wobbles, a restored floating window lands at the WM's default geometry instead of its saved one — never a crash. This is deliberately different from `close_window()`, which stays `abstractmethod`: every WM worth supporting can close a window, so there's no meaningful degraded case to fall back to there.

Severity varies sharply across this group even though the mechanism (no-op default) is the same: `focus_self()`'s degraded case is severe enough to call out explicitly (see `CLAUDE/NOTES/wm-quirks.md#focus-on-map-stealing`) where `mark_self()`'s or `set_floating_geometry()`'s are minor and cosmetic.

## `no_focus_next_window` is keyed on pid, not class/app_id {#pid-vs-class-criteria}

See `CLAUDE/NOTES/wm-quirks.md#no-focus-pid-criteria` for the full tradeoff — the short version: `pid` matches exactly one process and effectively never collides again, where `class`/`app_id` would keep matching every future window of that app for the rest of the WM session.

## `pending_moves.py`: pid > app_id > any-remaining matching tiers {#pending-move-tiers}

`resolve_pending_move()` matches a spawned process back to its window in three exclusive tiers, each only considering windows not already claimed by another entry: (1) exact pid match — unambiguous, but only available when the provider exposes `Window.pid` and the process wasn't launched via a shell; (2) app_id match — distinguishes the expected app from an unrelated new window, but not from a second simultaneous instance of the *same* app_id; (3) any remaining unclaimed new window — last resort.

The tiers are exclusive, not a cascade: an entry expecting a specific pid/app_id waits for exactly that (or times out in the caller) rather than settling for a weaker signal because its own match hasn't appeared yet on this tick. Falling through early looks fine until two entries share an app_id (e.g. two `kitty` launches) and one's window is simply slower to appear — a premature fallback then hands it the wrong entry's window. This exact bug reassigned 3 of 10 windows to the wrong workspace in a burst-launch test before the exclusive-tier fix.

Same-app_id entries not colliding at the app_id tier relies on callers processing pending entries in order, each claiming its match before the next entry looks — `resolve_pending_move()` itself has no notion of which window actually appeared first in real time, it just picks the first unclaimed match available to it. Two simultaneous instances of the same app_id landing on each other's targets (rather than crashing) is an accepted tradeoff, not a bug, as long as both instances are otherwise identical.

`resolve_pending_move()` doesn't mutate `claimed` itself — the caller adds the result's id only once it commits to the match (calls `move_window_to_region`), so a per-tier scan here never leaves a side effect to undo if the caller doesn't use the result.

## Why `PID_GRACE_SECONDS` is 6.0s {#pid-grace-seconds}

Was 1.5s until i3 testing surfaced a real race: `_enrich_pids()`'s `provider.resolve_pid()` call (a full `get_tree()` round-trip plus a fresh Xlib connection per call, see `i3.py`'s `_x11_pid_for_window`) isn't guaranteed to resolve a freshly-mapped window's pid within 1.5s, and once an entry downgrades to app_id-tier matching, the pid tier is gone for good — `resolve_pending_move()`'s tiers are exclusive, so a later successful enrichment can no longer help. If the app_id fallback also doesn't match (common on i3, where a Python/Electron app's real WM_CLASS often isn't its `.desktop`-derived hint), the entry just times out unmatched at `MOVE_TIMEOUT_SECONDS` with nothing left to try. Widened to 6.0s to give the slower, async i3 enrichment path a real chance to land before giving up on the more precise pid signal, while still leaving a couple of seconds of runway for the app_id fallback before the hard timeout.

## `pending_moves.process()`'s three-part return/side-effect contract {#pending-moves-process-contract}

`process()` returns `(reclaimed_focus, resolved_target_regions)` and always tries to reclaim tuicc's own focus (unless `dismissed`) before dropping a timed-out entry, even for entries that never matched — a spawn's own placement failing is a separate concern from tuicc's own fullscreen/focus, which the transient co-location problem (see `CLAUDE/NOTES/wm-quirks.md#fullscreen-drop-on-map`) already broke the moment the new window mapped. Without this, a spawn that timed out unmatched left tuicc's window stuck floating and unfocused indefinitely, with nothing left in the queue to ever call `focus_self()` on its behalf again.

`reclaimed_focus` tells `main.py` whether `focus_self()` was called this round — a real WM-focus transition, but a self-inflicted one, not the user switching to a different real context. `main.py`'s own real-focus-transition detector needs to know this to avoid misreading tuicc reclaiming its own focus as the user having gone elsewhere, which would otherwise silently reset `selected_id`/`focus_id` mid-launcher-session (concrete regression: a spawn's target silently switching workspace because an earlier spawn's `focus_self()` call happened to land between selecting a workspace and confirming — see the regression test).

`resolved_target_regions` lists the target region of every entry that matched a window this round (never the give-up-unmatched path, which has no real destination to report) so `main.py` can let `focus_id` — and the preview panel — follow a spawn/restore to wherever it actually landed. Without this, the preview panel showed nothing, indefinitely, for the rest of that tuicc session after a restore completed, until a dismiss+resummon forced a reset.

`own_region_id` is compared against each resolving entry's `target_region` to decide whether to request `force_relayout` from `focus_self()` — see `CLAUDE/NOTES/wm-quirks.md#fullscreen-suppresses-layout` for why a window landing on tuicc's own fullscreen workspace needs this. `None` (the default) never requests it, same opt-in shape as `fullscreen_only`.
