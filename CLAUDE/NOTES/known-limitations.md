# Known limitations

Gaps that are understood and accepted (or not yet fixed), not silent bugs waiting to be found. Each section is a stable anchor (`CLAUDE/NOTES/known-limitations.md#anchor`).

## `mark_self()`'s focus-based fallback race {#mark-self-focus-race}

`Provider.mark_self(app_id=None)` (the default when `[wm] self_app_id` isn't set in config.toml) assumes tuicc's own window is whichever one is currently focused at call time. Launching several tuicc instances back-to-back can race this assumption — whichever instance calls `mark_self()` last "wins" the currently-focused window, regardless of which one actually owns it.

Passing `app_id` (sway/i3 mark by WM criteria — `app_id`/`class` respectively — instead of "whatever's focused") removes the race entirely. This is why `install.sh` and README's launch commands set `[wm] self_app_id` for the documented scratchpad launch (`kitty --app-id tuicc_scratch`). The focus-based fallback stays as a documented degraded case for setups that don't set it, not a crash.

## Fork/exec pid-mismatch apps silently fail to relocate {#fork-exec-pid-mismatch}

Reliably reproduced (deterministically, 3/3 runs) with a wrapper script that backgrounds its real child instead of exec-replacing itself: `spawn_detached()`'s captured pid never matches the window's real owning pid. If the app's real `app_id` *also* doesn't match the launcher's `.desktop`-derived hint, `resolve_pending_move()` never reaches its "any remaining unclaimed window" fallback tier — that tier only ever triggers when **both** `pid` and `app_id` are `None` on the entry, and an app_id-mismatch-but-not-`None` entry never gets there. The window sits on tuicc's own workspace, unrelocated, until `MOVE_TIMEOUT_SECONDS` passes and the entry is silently dropped.

Suspected real-world trigger: OnlyOffice (not yet confirmed to be this exact mechanism — could also just be a very slow cold start exceeding the timeout).

Proposed fix, not yet implemented: a last-resort escalation to the "any remaining" tier right before an entry would otherwise time out, when exactly one unclaimed new window is still sitting there — low collision risk since it only fires at the very end of the timeout window, not as an early shortcut.

The same pid-mismatch gap silently no-ops `no_focus_next_window()` too (see `CLAUDE/NOTES/wm-quirks.md#no-focus-pid-criteria`) — not a new gap, the same underlying mismatch.

## `for_window` rules accumulate for the WM session {#for-window-accumulation}

sway/i3 have no IPC command to remove a `for_window` rule once added — only a full WM restart clears them. `no_focus_next_window()` adds one such rule per spawn, for the lifetime of the WM session. See `CLAUDE/NOTES/wm-quirks.md#no-focus-pid-criteria` for the collision-probability math and why this is an accepted tradeoff rather than a bug to fix.

## Session-restore relaunch can silently crash for wrapper-launched apps {#restore-relaunch-crash}

`promote_restore_queue()` relaunches a saved session entry from its captured `cmdline`. A match that never happens looks identical from the outside whether the process never started or started and immediately died — confirmed concretely: a saved Obsidian entry's cmdline (`electron <asar path>`), relaunched exactly as captured, crashes with `Cannot find module 'electron'`. The real launcher is a wrapper script (NixOS packages Electron apps this way) that sets up environment before exec'ing into that same binary — invisible to `/proc/<pid>/cmdline`, which only ever captures argv *after* that exec.

Every restore spawn gets a log_path under `SPAWN_LOG_DIR` (named by app_id + wall-clock time, known before the pid is) so a crash like this is at least diagnosable after the fact. `session_entry.get("env")` (session.py's captured `/proc/<pid>/environ` snapshot) is threaded through `spawn_detached()` for the same reason — `None` for entries saved before this existed, or where the environ read failed, falls back to "just use the current environment."

## i3's pid enrichment is scoped to just-spawned windows, not every open window {#pid-enrichment-scope}

`_enrich_pids()` only fills in `.pid` for windows not yet seen by any pending entry and not already claimed — typically 0-1 windows, not every window on the desktop. This scoping matters on a provider whose `get_state()` never populates pid at all (i3 today): without it, every pending entry would burn the full `PID_GRACE_SECONDS` before downgrading to the app_id tier, and if the spawned app's real app_id doesn't match its `.desktop` hint (common for Python/Electron apps launched via a bare interpreter), it would never resolve, silently dropped after `MOVE_TIMEOUT_SECONDS`. A spawn timing out this way leaves its window sitting wherever the WM naturally opened it — typically tuicc's own workspace — with no resolved match to move it, which also happens to be the trigger for the unrelated-looking symptom of tuicc losing fullscreen (a new tiled window landing on its own workspace; see `CLAUDE/NOTES/wm-quirks.md#fullscreen-suppresses-layout`).

`provider.resolve_pid()` defaults to a no-op returning `None` for providers that don't need it (sway's windows already carry a real pid from `get_state()`), so calling `_enrich_pids()` unconditionally is safe everywhere — just a no-op where it isn't needed.

## `battery.watch()` push-notification is unreliable on real hardware, not wired in {#battery-push-unreliable}

`battery.watch()` blocks on `select.poll()` against each BAT pack's `uevent` sysfs attribute (`POLLPRI|POLLERR`), the same low-level mechanism `upowerd`/`acpid` use internally. Confirmed empirically NOT reliable: across several real charger unplug/replug cycles on a T480 (NixOS), `select.poll()` never fired once — every yield came from the generator's own `fallback_seconds` safety net, never the kernel notification, and the underlying `/sys` data hadn't changed between samples despite the physical action genuinely happening. The kernel's documented `power_supply` `poll()` support evidently isn't reliably wired up for this attribute on that kernel/driver combination.

Full status, the two candidate replacement mechanisms (pyudev netlink monitoring, UPower D-Bus signals), and the "make it work or delete it" decision this gates before v0.1.0 ships are in `CLAUDE/VISION.md`'s R8 section — read that before touching `battery.watch()`, `push_worker.py`, or `combined_status.py`. `main.py` currently wires `battery` to a plain fast-poll `StatusWorker` `Domain` (poll_interval=0.3) instead.

One confirmed-correct low-level detail worth keeping regardless of whether `watch()` survives: the first `poll()` call after opening a `power_supply` `uevent` fd fires immediately and unconditionally — not a real change, just how the kernel's poll table registration works for these files. Reading the file's content ("arming" it) is what makes subsequent `poll()` calls only return on an actual change; each detected change has to be re-armed (seek+read) before the next `poll()` call or the same still-unconsumed event immediately re-fires.

## Agent shutdown replies to pending requests before disconnecting {#agent-shutdown-cancels-pending}

`IwdAgent.stop()`/`BluezAgent.stop()` reply to any still-pending request with a Canceled error *before* tearing the D-Bus connection down. Without this — main.py's `finally` block or a plain Ctrl+C calling `stop()` while a passphrase/pairing overlay is still open — the connection used to just close out from under iwd/bluez, leaving its own blocked `Network.Connect()`/pairing call waiting on a peer that had simply vanished mid-request instead of getting a normal, in-protocol answer. This is a suspected (not proven — no way to reproduce a daemon segfault on demand) trigger for a real iwd crash (SIGSEGV, systemd auto-restarted it) encountered during exactly this kind of abrupt-disconnect testing. Even setting the crash risk aside, there's no reason to rely on iwd/bluez handling a dropped peer gracefully when a clean Canceled reply costs nothing. Best-effort `UnregisterAgent` happens afterward — the connection is being torn down either way, so a failure there (daemon already gone, bus already closing) isn't worth surfacing.

## Resummon can keep a stale workspace selection {#stale-resummon-selection}

Reported on both sway and i3: dismissing tuicc and re-summoning it sometimes shows the sidebar still selected on whatever workspace was selected *before* dismiss, not necessarily matching real current WM focus. Never conclusively root-caused — could be a real bug in the focus-change detector, or could be correct behavior that looked like a bug because real focus happened to coincide. Needs a clean, deliberate repro (dismiss tuicc while workspace A is selected in the sidebar, switch WM focus to workspace B via a non-tuicc means, re-summon, check what's selected) before concluding anything.
