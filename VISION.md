# tuicc — Vision Update & Refactor Handoff

Handoff for Claude Code sessions working in `Lshika-linux/tuicc` (public
repo, no fork — work happens on branches, one branch per refactor,
merged to main when tests are green). This document supersedes any
earlier "experimental repo" concept.

Maintainer: Rafi (`Lshika-linux`). NixOS, ThinkPad T480, sway, kitty.
**Rafi drives all architectural decisions — propose, explain why, wait
for explicit confirmation before changing anything.** He reads Python
well and is building writing fluency; explain new concepts once, don't
re-teach known ones. Learning is a primary project goal.

---

## 1. Philosophy (the thesis)

**tuicc is a control center for tiling window managers, built on user
sovereignty.** One keybind toggles a fullscreen terminal surface where
everything you can do is visible at once. No submenus, no hidden state,
no bullshit — the screen is the documentation. The user has absolute
authority over the machine: actions work, or they say clearly why they
didn't. tuicc is what it is: a terminal application, transparent TOML,
zero GUI dependencies. Close it and it leaves you alone. It is not a DE.

Explicitly NOT the framing: "phone control center". That metaphor was a
useful design crutch (density, one screen, modality) but contradicts the
philosophy — phones hide and restrict; tuicc reveals and obeys.

### Core identity (untouchable)
**sidebar + preview + launcher are the identity of tuicc.** Any future
proposal that weakens them (e.g. "launcher as a popup") is rejected on
sight. The launcher's "start typing from anywhere" behavior is the one
specialty tuicc never gives up.

### The four-category test
Every feature must be one of:
1. **Toggle** — manage system state (wifi, VPN, DND, power profile)
2. **Card** — switch context (workspace, window, session)
3. **Result** — launch something (apps)
4. **Context** — read-only decision input (system stats, battery,
   weather, calendar). Hard constraint: **bounded and read-only** — one
   view, no streams, at most one trivial action (e.g. add a calendar
   note). Weather = now + short outlook, not radar. Calendar = today/
   tomorrow agenda + month dots, not event management.

Feeds (notifications, RSS, mail) fail the test — endless, consumptive,
not contexts. Out.

### No-silent-failure (cross-cutting principle, non-negotiable)
The user cannot distinguish "no wifi networks around" from "D-Bus is
down" today. That ends. Errors become *state*, not swallowed
exceptions: workers/backends record `last_error` per domain, modules
render it (`urgent` color, e.g. `⚠ D-Bus unreachable`). Distinguish
"empty" from "unknown" at the model level (None vs `[]`).

Current violations to fix as refactors touch them:
- `ConnectivityWorker._run`: `except Exception: pass` on both actions
  and polling
- `IwdBackend`: six silent `return []` / bare `return` paths
- `BluezBackend._run`: silent `""` on timeout / missing bluetoothctl
- `sessions.capture_session`: silently skips uncapturable windows —
  keep the skip (documented decision) but surface it:
  "saved 9/10 windows, 1 skipped: no pid"

### Division of labor with the WM
Instant reactions (volume keys, media keys, brightness keys) stay as WM
keybinds — document a recommended sway snippet, never rebuild them. No
daemon mode, no background key listening, ever. Persistent bars are not
tuicc's job; the "glance" answer is: summon for a second, see state,
dismiss.

## 2. Lifecycle model (decided)

**tuicc is a persistent process toggled by the WM.** The process never
exits in normal use — the WM shows and hides its window. This gives
instant summon (warm process, warm caches) without any daemon.

- **Toggle lives in the WM, for free.** Canonical setups documented
  per WM in a README "Summoning tuicc" section — ONE recommended path
  per WM, not a menu of options:
  - sway/i3/scroll: scratchpad —
    `for_window [app_id="tuicc_scratch"] move scratchpad` +
    `bindsym $mod+grave [app_id="tuicc_scratch"] scratchpad show`
  - Hyprland: special workspace (`togglespecialworkspace`)
  - niri: dedicated workspace (no scratchpad concept exists there)
- **Dismiss ≠ quit.** Handler contract (write into CLAUDE.md):
  - *State actions* (toggles, connectivity, sessions, launcher spawns)
    — stay visible
  - *Focus actions* (Enter on workspace/window) — perform + dismiss
  - *Power actions* (lock/logout/…) — perform (+confirm) + quit is fine
  - quick_actions gets a per-action `exit_after` TOML field when revived
- **Escape chain:** with an active input claim — release claim; at top
  level — dismiss (and return WM focus to the origin region captured at
  summon, where the launch style needs it — config
  `return_to_origin`, default off; scratchpad restores focus itself).
- **`dismiss_self()` joins the Provider contract** — optional method,
  no-op/degraded default (focus origin, window stays put). sway/i3
  implement via `[con_id=<own>] move scratchpad`, using the mark.
- **`mark_self()` fix:** when a known app_id is available (documented
  launch uses `kitty --app-id tuicc_scratch`), mark via criteria
  (`[app_id=…] mark --add …`) instead of the focused-window assumption
  — kills the documented timing race. Focus-based marking stays as
  fallback for plain-terminal launches.
- **Real quit = Ctrl+C.** No quit menu entry, no quit keybind — it's a
  terminal, that's the idiom. To quit a hidden tuicc: summon, Ctrl+C.
  Document one line: "config changes apply after restart: summon,
  Ctrl+C, relaunch." Add `try/finally` in main for graceful cleanup
  (worker stop, future D-Bus agent unregister).
- Note: scratchpad workspace (`num = -1`) already flows into WMState as
  region "-1"; sidebar ignores it by accident today — keep ignoring it
  consciously. Future idea (backlog, not now): an "S" slot in the
  sidebar showing parked scratchpad windows.

## 3. Target capabilities (end state)

**Windows & workspaces** — sidebar, live preview incl. floating,
focus/switch/close; providers sway + i3 (done), then scroll, niri,
Hyprland.

**Launching** — fuzzy .desktop launcher, spawn onto selected ws (done);
sessions save/restore/delete (done).

**Connectivity v2** — WiFi: scan, connect to new networks (passphrase),
disconnect, signal. Bluetooth: scan, pairing (passkey confirm),
connect/disconnect, battery. VPN toggle via user-defined toggles.

**Control module (new)** — toggles (config-driven) + sliders: volume,
brightness. Nightlight/power-profile/caffeine/DND ship as commented
example toggles.

**Media module (new)** — MPRIS now-playing (artist — track, status),
prev/play-pause/next, and **audio output switching** (speakers —
headphones — BT). Passes the test twice: context (what's playing) +
toggle (control it). Pairs with connectivity: connect BT headphones,
switch output, one screen.

**System monitor (new)** — battery, CPU, RAM overall; per-app CPU/RAM
**mapped to windows** (the differentiator vs htop: "librewolf on ws 2
eats 2 GB", not 400 process rows), aggregated over process subtrees,
sorted by drain; Enter — `close_window()`, secondary — SIGTERM with
confirm.

**Calendar (context module, opt-in)** — reads local ICS files (RFC
5545; synced by vdirsyncer from Nextcloud/Google/anything — tuicc never
touches the network for this). Month-as-dots density view + today/
tomorrow agenda + one action: add a simple note (write a minimal ICS
event; vdirsyncer syncs it back). Bounded, read-mostly.

**Weather (context module, opt-in)** — now + short outlook, read-only.

**Power** — lock/logout/reboot/shutdown, confirm + global shortcuts
(done).

**Frame** — persistent + WM toggle; Tab + Enter canonical navigation
(arrows as optional accelerator); everything on one screen; TOML,
presets, interactive resize (done).

### Explicitly out of scope (with reasons, so the line stays visible)
- **Per-app network monitoring** — needs pcap/eBPF/root; against
  tuicc's spirit. CPU+RAM(+disk I/O) cover the real cases.
- **Equalizer (bass/mid/treble)** — domain-wise belongs to `audio/`
  (sink property; MPRIS has no EQ), but PipeWire has no clean runtime
  EQ API — it's filter-chain configs creating virtual sinks, which is
  why EasyEffects is a whole application. Door stays open via a
  user-defined `[[control.toggle]]` (e.g. toggling EasyEffects), and
  revisit if PipeWire grows a real API.
- **Notifications / RSS / mail** — feeds, not contexts.
- **Daemon mode / background key listening** — the persistent-process
  + WM-toggle model makes it unnecessary; WM keybinds own instant
  actions.

## 4. Refactors

### R1 — Lifecycle: persistent + toggle
Everything in section 2. Mostly semantics + small code: audit every
handler's `should_exit` against the contract, origin capture at summon,
top-level Escape, `dismiss_self()`, mark-via-criteria, try/finally.
Do first — it changes the UX ground everything else stands on.

### R2 — Input claim (generalize typing mode)
main.py holds `input_claim: str | None`. `None` = launcher's privilege
applies: any unbound printable key auto-claims for the launcher
("typing anywhere" is sugar for auto-claim). Other modules must *steal*
the claim explicitly (Enter on a passphrase field). Claimed module gets
raw keys via `handle_input(key) -> still_claiming` (the generalized
`handle_typing_key` shape, pure function). Escape releases the claim.
Pure refactor first: launcher-only, behavior identical, **all 23
existing launcher tests stay green unchanged** — that's the acceptance
criterion (done). Connectivity v2 becomes the second consumer, per the
original plan — but sessions.py's rename field and help_mode's color
editor turned out to be nearer-term second/third consumers, both
text-input cases matching handle_typing_key's own shape exactly
(planned, not yet landed as of this writing).

**Backlog, not v0.1.0-blocking:** resize_mode.py as a further, fourth
consumer — its own `handle_input(key) -> still_claiming` interpreting
resize's browsing/editing dispatch internally, moving the ~80-100
lines of inline `resize.active`/`resize.editing` dispatch out of
main.py into resize_mode.py, where it arguably belongs (module owns
its own behavior; main.py owns *when* to call it). Architecturally
sound, but a bigger lift than the two above, for two concrete reasons:
resize's dispatch today reaches into a lot of main.py-level state
(`active_module`, `cfg.layout.boxes`, the `resize_message`/
`resize_message_until` toast, the `do_save_layout`/`do_cycle_preset`
closures, computed `boxes` for direction deltas) that
`handle_typing_key`'s plain `(state, key, cfg)` signature never
needed; and F1/F3/F4/F6 working from *either* level of the resize
session means some keys have to stay reachable across whatever claim
boundary gets drawn — a plain `still_claiming` bool may not be a rich
enough return shape for that. R2's own acceptance criterion (above)
doesn't require this — revisit after v0.1.0, as its own scoped pass.

### R3 — StatusWorker (generalize ConnectivityWorker) — done
Extract the pattern (thread, action queue, pending set, cached
snapshots, poll interval) into a generic worker; connectivity is client
#1, control/media/system-monitor follow. **No-silent-failure is
implemented here:** backend exceptions become per-domain `last_error`
state rendered by modules; None vs `[]` distinguished in models. Add a
hibernation hook (stop polling while dismissed) as architecture now,
implementation later.

**Deviation from the acceptance criterion above, found live while
building it:** `ConnectivityWorker` does not survive as a thin wrapper
class around the new `status_worker.StatusWorker`. An early version of
this refactor kept one — found live it would've been ~8 methods, each
a 1-line pass-through to the generic worker's own equivalent, nothing
of its own — the exact same redundancy R2's `input_claim`/old-flag
duplication turned out to be, caught before landing rather than after
this time. `ConnectivityWorker` is retired; `main.py` builds a
`StatusWorker` directly with `"wifi"`/`"bluetooth"` `Domain`s, and
`modules/connectivity.py` calls its generic API (`get("wifi")`,
`request_action("wifi", "connect", ssid)`, `is_pending(...)`) instead
of domain-specific method names. This is NOT a pure refactor the way
R1/R2 were — `modules/connectivity.py`'s call sites changed, and the
6 old `ConnectivityWorker` tests were rewritten (not left green
unchanged) against the new API, same behavioral coverage. Worth it for
R5/R6: they register their own domains against the same
`StatusWorker` with zero wrapper boilerplate of their own, rather than
each needing to decide whether to repeat `ConnectivityWorker`'s
mistake.

Hibernation hook landed as `pause()`/`resume()` on `StatusWorker` —
exist and work, but nothing calls them from `main.py`'s dismiss/
resummon path yet, per "architecture now, implementation later" above.

Action failures (e.g. a `connect()` call raising) are still a bare
`except Exception: pass` — not yet surfaced via `last_error` the way
poll failures are. Known, documented gap (see `status_worker.py`'s own
module docstring for the reasoning), not blocking — revisit if it
turns out to matter in practice.

### R4 — Connectivity v2: D-Bus agent pattern
The hardest, most-unlocking piece — land it late, with infrastructure
proven. Passphrase (iwd) and pairing (BlueZ) are callback-driven: tuicc
registers agent objects (`net.connman.iwd.Agent`, `org.bluez.Agent1`)
and the daemon calls back ("RequestPassphrase", "RequestConfirmation
passkey 847291") — D-Bus becomes bidirectional. **Read (don't port —
they're Rust/Go) impala and bluetuith as protocol documentation**, the
way sway.py served i3.py. Side effects: migrate bluez.py from
bluetoothctl CLI parsing to org.bluez D-Bus (jeepney pattern exists in
iwd.py); add scanning (StartDiscovery / Station.Scan) surfaced via the
pending/blink mechanism; agent unregister goes in the R1 try/finally.
Backends stay behind grown-but-compatible ABCs.

### R5 — Control + Media modules
Two domains, two backend packages (do NOT merge them — same lesson as
the iwd/bluez split):
- **`audio/`** — routing & volume via PipeWire: `wpctl` backend
  (status/list sinks, set-default, set-volume, set-mute), `pactl`
  fallback backend, registry pattern as in connectivity. Pure parsing
  functions + recorded fixtures.
- **`media/`** — playback via MPRIS (`org.mpris.MediaPlayer2.*`,
  jeepney, plain request-response — simpler than iwd). Metadata,
  PlaybackStatus, PlayPause/Next/Previous. Multiple players: show the
  active one (Playing wins), Tab to others.

Two modules:
- **control** — toggle grid + volume/brightness sliders. Toggles are a
  *contract, not features*: `[[control.toggle]]` with `label`,
  `status_command` (exit 0 = on), `on_command`, `off_command`; zero
  code per toggle; ship commented examples (tailscale, makoctl DND,
  idle-inhibit, gammastep, power profile, EasyEffects). Brightness via
  `brightnessctl` backend. Slider interaction: Enter grabs, ←→ adjust,
  Enter/Esc release (arrows are free — Tab is canonical navigation).
  Visual rule: ONE box, uniform dense rows (dot + label, the
  `_connection_dot` convention) — not one framed box per feature.
- **media** — now-playing line + transport + **output switching lives
  here** (not in control): "music plays — route it to headphones" is
  one flow. Volume stays in control (system property, not player
  property).

Both feed off StatusWorker (polling, action queue, pending blink).

### R6 — System monitor module
Entity list = windows from WMState (pid via `Window.pid` on sway,
`provider.resolve_pid()` on i3). RAM from `/proc/<pid>/status` (VmRSS);
CPU% as utime+stime delta between StatusWorker samples (~2s) — never in
the render loop; optional disk I/O from `/proc/<pid>/io`. **The real
work: aggregate full process subtrees** (walk
`/proc/<pid>/task/*/children` or PPid chains) or Firefox shows a
comical 3%. Actions: Enter — `provider.close_window()`; secondary —
SIGTERM with the existing pending_confirm dialog. /proc parsing as pure
functions with recorded fixtures, provider-style discipline.

### R7 — Default preset + docs rewrite
Last, and done largely *inside tuicc* (F1 spawn, F2 resize, F3 save).
Compose the thesis screen: left column sidebar — connectivity — control
— power; top strip launcher + sessions + clock; preview dominant; media
+ system along the bottom. Hierarchy by size, not color — preview stays
the biggest thing, toggles small and quiet. Weather/calendar exist but
are NOT in the default preset. Then rewrite README/wiki around the
final philosophy (sections 1–2 of this document) — after R1–R6, half
the current docs will be wrong.

## 5. Phase order

1. **R1** lifecycle (small code, changes UX ground for everything) — done
2. **R2** input claim (pure refactor, launcher tests green) — done
   (launcher, plus sessions.py's rename field and help_mode's color
   editor as nearer-term second/third consumers than originally
   planned; resize_mode deferred, see R2's own section)
3. **R3** StatusWorker (no-silent-failure lands here) — done (not a
   pure refactor in the end, see R3's own section for why)
4. **R5** control + media (fast visible wins, exercise R3 three times)
5. **R6** system monitor (exercises R3 + fixture discipline)
6. **R4** connectivity v2 agents (hardest, most infrastructure needed)
7. **R7** default preset + docs

Each refactor: its own branch, tests green before merge to main,
GitHub issues for R4–R6 so contributors can see the direction.

## 6. Project principles (non-negotiable)

- Rafi confirms before changes; explain the *why* of every decision.
- **No silent failures — errors are state, rendered in the UI.**
- No hidden defaults; transparent TOML, regenerable configs.
- Docstrings carry reasoning ("why"), not restatement ("what").
- Pure functions + recorded fixtures for anything parseable; empirical
  debugging — inspect real data before theorizing.
- `render.py` never hardcodes module names; adding a module = registry
  lines only; modules own draw + nav_items (`_build_rows` where they
  must agree on rows).
- Provider contract stays WM-agnostic; degraded cases are documented
  no-ops, not crashes; grep-verify no sway/i3 references outside
  `providers/` and `tests/`.
- sidebar + preview + launcher are core identity; launcher owns
  unclaimed typing.
- Disciplined commits: short summary, blank line, explanatory body.
- Known limitations get documented, not "fixed" with complexity nobody
  hits.
