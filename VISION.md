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
  fallback for plain-terminal launches. Confirmed live (found
  post-v0.1.0-work, real machine): this fallback race isn't a rare
  rapid-multi-launch edge case — a single ordinary launch mismarked an
  unrelated app (still holding focus a moment) as tuicc itself,
  silently hiding it everywhere. `self_app_id` (set + documented
  prominently now — see defaults/config.toml's own comment, README's
  "Summoning tuicc", the wiki's Writing-a-WM-Provider page) fixes this
  deterministically for anyone who sets it; the fallback itself is
  unchanged. **Backlog, not v0.1.0-blocking, explicitly deferred by the
  user pending a real report:** a non-blocking retry (defer the
  fallback's mark_self() call into the main loop, re-checking each
  frame whether the currently-focused window has stayed the same for
  several consecutive frames before committing, instead of one
  snapshot at startup) was designed and considered. Rejected for now —
  not because it's wrong, but because it only ever improves the
  fallback's odds probabilistically (self_app_id already fixes it for
  free, deterministically), adds real state-machine complexity to
  main.py's loop, and has at least one plausible way to perform *worse*
  than today's immediate snapshot: waiting longer gives more real time
  for something else to transiently steal focus mid-check (a
  notification, a hover-focus blip), whereas grabbing a snapshot fast
  is less exposed to that specific interference window. Revisit only
  if someone hits this without `self_app_id` set and a fix is
  genuinely wanted, not preemptively.
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

Action failures (e.g. a `connect()` call raising) were left as a known,
documented gap here — "revisit if it turns out to matter in practice."
It did: found live building R5's control module (a toggle's command
failing fast — `gammastep` exiting immediately for lack of a
configured GeoClue2 provider — produced zero visible feedback), fixed
as part of that same pass, not deferred further. `StatusWorker` now
tracks a domain's action errors separately from its poll errors
(`get_action_error()`, its own dict — poll and action errors can't
share a slot, since `_run()` always re-polls every domain in the same
loop iteration right after processing that iteration's actions, which
would silently clobber an action error the instant that poll
succeeds). For control.toggle specifically, `control.py`'s
`run_state_command` also had to solve a second problem past
`StatusWorker`'s own fix: a `[[control.toggle]]` command runs detached
(`spawn_detached`, fire-and-forget — some, like the packaged Idle
Inhibit example, are meant to run forever), so a fast, silent failure
doesn't raise a catchable Python exception at all, `StatusWorker`'s fix
alone can't see it. Solved with a brief (300ms) non-blocking wait right
after spawning — long enough to catch a command that's already exited
by then (the common "missing dependency"/"bad args" case, exactly
what `gammastep` hit), short enough not to meaningfully delay a
legitimate long-runner past its first two poll cycles. `modules/
control.py` surfaces the result two ways: the row's dot/label switches
to a dedicated `[ERROR]` state (outranking a plain poll error — the
last thing the user *did* is more relevant than the last thing that
was merely *observed*), and the failed command's captured combined
stdout+stderr is appended to the sidebar preview panel — split into
one `preview_text` entry per physical line, not one entry holding an
embedded `\n`: found live in the same pass, a raw `\n` inside a single
`draw_centered_lines` (`render_utils.py`) entry makes curses jump to
column 0 of the next *real terminal row* on that newline, escaping the
preview box's own x-position entirely, rather than wrapping within it.

Verified live end-to-end, this session's own test machine (gammastep
genuinely installed but never configured — exactly the state a fresh
install of the packaged Night Light example would be in): pressing
Enter on Night Light with no GeoClue2 provider set up produces —

```
Control
● Airplane Mode [off]
⚠ Night Light [ERROR]
● Mic Mute [off]
...
```

— and the preview panel shows the toggle's own gammastep invocation
right alongside its full, real, three-line stderr, each on its own
correctly-centered row:

```
> Night Light: off → on <
$ gammastep
⚠ Error: GeoClue2 provider is not installed!
⚠ Error: Failed to start provider: geoclue2
⚠ Error: Latitude and longitude must be set.
```

Nothing about this is control.toggle-specific in principle — any
`[[control.toggle]]` command that fails fast, for any reason, surfaces
the exact same way.

**`Domain.poll_interval` (per-domain, not just the shared 5s default):**
found live, this session, right after the media module's own transport
controls landed — the user connected bluetooth headphones and reported
tuicc's output list taking a visible "pause" to reflect it. Root cause
wasn't tuicc at all: WirePlumber already auto-switches the default
sink to a newly-connected bluetooth device on its own (confirmed live
— `wpctl status` showed it as the marked default already, unprompted,
no `request_action` call from tuicc anywhere in that path) — the
"pause" was purely `StatusWorker`'s own shared 5s poll cadence being
the *only* way audio/media, specifically, ever notice a change that
happens entirely outside tuicc's control flow (nothing to trigger an
action-driven re-poll, unlike wifi/bluetooth *connect* which tuicc
itself always initiates). `Domain` gained an optional `poll_interval`
(`None` = use `StatusWorker`'s own shared default, unchanged behavior
for wifi/bluetooth/every control.toggle); `main.py`'s `"audio"`/
`"media"` domains now set `poll_interval=1`. `_run()`'s single shared
`last_poll` became a per-domain dict — a domain becomes due either on
its own interval elapsing or (unchanged from before) immediately after
an action targeting it was just processed.

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
- **`audio/`** — done. Routing & volume via PipeWire: `wpctl` backend
  (status/list sinks, set-default, set-volume, set-mute), `pactl`
  fallback backend, registry pattern as in connectivity. Pure parsing
  functions + recorded fixtures. Not yet wired into StatusWorker
  domains or a slider UI — that's the remaining part of the **control**
  module below.
- **`brightness.py`** — done. `brightnessctl`-backed, a single plain
  module (not a registry) since there's exactly one reasonable tool for
  this, unlike wifi/bluetooth/audio which genuinely have several. Same
  not-yet-wired-into-a-slider status as audio/ above.
- **`media/`** — done. Playback via MPRIS (`org.mpris.MediaPlayer2.*`,
  jeepney, plain request-response — confirmed genuinely simpler than
  iwd, as predicted: no stateful station/adapter lookup first, just
  `GetAll` + a handful of no-argument methods). `get_players()`
  enumerates every currently-running MPRIS bus name
  (`org.freedesktop.DBus.ListNames`, filtered by prefix) rather than
  assuming a single player the way iwd has one Station — real, live-
  captured fixture (Firefox playing a YouTube tab) showed Metadata
  fields are genuinely optional and vary between players (no
  `mpris:length`/`mpris:artUrl` at all here), so `parse_metadata()`
  defaults every field rather than trusting any of them present. "show
  the active one (Playing wins), Tab to others" ended up needing zero
  special-case logic at all: `modules/media.py` gives every detected
  player its own row of nav items, and the *existing* Tab-driven
  navigation already cycles through them like any other list — no
  separate "switch active player" concept was needed.

Two modules:
- **control** — toggle grid (done) + volume/brightness sliders (not
  started). Toggles are a *contract, not features*, done as
  `[[control.toggle]]` — but the shape shipped is NOT what this section
  originally sketched (`status_command`/`on_command`/`off_command`,
  strictly binary). Found live while designing it with the user: a
  binary on/off switch and an N-way cycle (Performance Mode's
  power-saver/balanced/performance) are the exact same mechanism —
  advancing is `(index + 1) % len(states)` regardless of N — so the
  shipped contract unified both into one: `label` + `shell_true` +
  2-or-more `[[control.toggle.state]]` blocks (`name`/`status_command`/
  `command`/optional `color`). Each state's `status_command` (exit 0 =
  "currently this state") is checked in declaration order, first match
  wins; only the LAST state may omit it (implied by elimination if
  nothing earlier matched — a sound conclusion from exhaustive
  checking, not a guess). `color` reuses `theme.resolve_color()` and is
  rendered via a second, independent round of `curses.init_pair()`
  calls (`theme_setup.assign_control_toggle_pairs()`), separate from
  `[theme]`'s own pair range so the two can't collide. Backend logic
  lives in `control.py` (`probe_state`/`find_current_state`/
  `next_state_name`/`run_state_command`, all pure or thin subprocess
  wrappers, fixture-tested) — `modules/control.py` is pure UI glue over
  it, zero per-toggle code. Six shipped, commented-out examples —
  Airplane Mode, Night Light, Mic Mute, Idle Inhibit, Do Not Disturb,
  Performance Mode — chosen after actually researching what real
  quick-settings menus (GNOME/KDE) ship, not the original placeholder
  list this section used to name (tailscale/EasyEffects were considered
  and deliberately dropped as too personal/niche; a "Dark Mode" example
  was considered and dropped as unreliable on a bare sway/i3 session —
  no single command reflects both GTK's and Qt's actual state). One
  `StatusWorker` `Domain` per toggle entry (`toggle:{i}`), not one
  shared domain — same granular-error-surfacing reasoning as the wifi/
  bluetooth split. Sliders (volume via `audio/`, brightness via
  `brightness.py`) are NOT built yet: still needs Domain wiring for
  both backends, the actual slider draw/grab UI, and — per this
  section's original interaction spec (Enter grabs, ←→ adjust, Enter/
  Esc releases, arrows free since Tab is canonical navigation) — a 4th
  `input_claim` (R2) consumer, since slider grab/release is a clean fit
  for that mechanism unlike resize_mode's own deferred case. **Update:
  sliders did NOT land inside control after all — see R8.** Live
  iteration with the user concluded a continuous gauge is a different
  enough visual language from control's own dense toggle rows (next
  bullet) that it deserved its own module (`bars.py`), not a slider
  bolted onto this one; VOL/BRI display now lives there, the actual
  ←→-adjust interaction this paragraph originally scoped is still not
  built (R8's own display-only status). Visual
  rule for the whole module: ONE box, uniform dense rows (dot + label,
  the `_connection_dot` convention) — not one framed box per feature.
- **media** — done (now-playing + transport + output switching).
  **Output switching lives here** (not in control), exactly as
  planned: "music plays — route it to headphones" is one flow, reusing
  `audio/`'s own `set_default_sink` — required wiring `audio/` into a
  real `StatusWorker` `Domain` for the first time (`poll=
  audio_backend.get_sinks`, `actions={"set_default_sink": ...}` only —
  `set_volume`/`set_mute` stay unwired, control's own future slider
  work, not media's concern). One `Domain` each for `"audio"` and
  `"media"`, config gained a new `[audio] audio_backend = "wpctl"` key
  (`.get()`-with-default, not direct indexing like `[network]`'s own
  wifi/bluetooth backend keys — `[audio]` is new, an existing
  config.toml predating it must not hard-crash on load). Transport
  controls (prev/play-pause/next) are 1-3 `NavItem`s per player row,
  positioned at a fixed offset from the row's right edge — prev/next
  are omitted entirely (not just disabled) when a player's own
  `CanGoPrevious`/`CanGoNext` says it doesn't support them, a real,
  common case for single-track web players. Same no-silent-failure
  treatment as connectivity/control: `None`+`get_error()` renders as a
  real `⚠` row, distinct from "genuinely nothing playing"/"no sinks
  found".

**Follow-on, done:** a CAVA-style frequency bar visualizer, built as a
separate pass after Phase 4/5 landed, per the plan above (mono signal,
`cava -p <config>` with `[output] method = raw`, `data_format = ascii`,
`CavaReader` — background reader thread, not a `Domain`, see
`media/cava.py`'s own module docstring). One design point changed from
the original plan during live testing: it landed drawn INLINE to the
right of the Output section's own rows (height = one row per currently-
connected sink, dynamic, via `_cava_row_level()`) rather than as 2
fixed rows below everything — the fixed-rows cut didn't read as
intentional once seen live, the inline placement mirrors how
`"<< ▶ >>"` already fills the empty space to the right of Now Playing's
own rows. Also found live: redraw cadence has to be matched to cava's
own `framerate` (30, in the generated config) or the UI only shows
every Nth frame cava actually produces, reading as choppy even though
the reader thread itself keeps up fine; and a flat idle-state baseline
needs plain color, not also dimmed — the thinnest block glyph (▁) with
`A_DIM` on top turned out too subtle to see against a real theme.

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

**Overall system stats, decided in a long live design discussion with
the user (not in the original plan) — every candidate has to pass a
three-part test the user stated outright: "tells you something, calls
for action, or is used to flex — otherwise don't bother me with it."**
This is a sharper, feature-specific restatement of section 1's own
Context test ("bounded, read-only decision input"), and it's what
actually decided the final list below — several tempting candidates
(uptime, package-update counts, disk/battery health) failed it or hit
real implementation friction and got cut. Final v1 scope, passive
display only (see its own paragraph below for what's explicitly
deferred):

- CPU%/RAM% overall, alongside the per-window list above.
- **Disk usage** (df-style) — calls for action: clean something up.
- **Load average** (1/5/15 min, `/proc/loadavg`) — trivial, free, tells
  you the system is genuinely busy.
- **CPU temp** — deliberately the SPECIFIC CPU package reading, not a
  "hottest sensor in the system" catch-all (considered, explicitly
  rejected: this one is "the flex value", the user's own words, needs
  to be unambiguously the CPU). Confirmed live on this session's own
  machine (T480) that getting this right is genuinely non-trivial: 7
  different `/sys/class/thermal/thermal_zone*` entries with confusing
  type names (`INT3400`, `acpitz`, `SEN1`, `pch_skylake`, `B0D4`,
  `x86_pkg_temp`, `iwlwifi_1`) plus several separate `hwmon` devices —
  naively reading the first thermal zone gives `INT3400` at 20°C,
  nowhere near the real CPU reading. `x86_pkg_temp` (thermal_zone) and
  `hwmon`'s own "coretemp" device's "Package id 0" label agreed with
  each other live (57°C both) — confirms the right target exists, but
  picking it needs a real vendor-aware heuristic (Intel: coretemp
  "Package id N"; AMD: k10temp/zenpower "Tctl"/"Tdie"), not a
  first-zone guess. Build against `sensors -j` (lm-sensors, already
  solves this cross-vendor problem) rather than reimplementing
  hwmon/thermal-zone selection from scratch — same "reuse the one
  already-correct tool" reasoning brightness.py's own docstring gives
  for brightnessctl. Optional dependency, same missing-binary-is-not-
  an-error tolerance as cava.
- **Hottest sensor + its own label** (e.g. "68°C (nvme)") — a SEPARATE
  line from CPU temp, not a replacement for it: found live, discussing
  it with the user — CPU is not always the hottest thing in a laptop,
  NVMe SSDs commonly exceed CPU temps under heavy sustained I/O,
  especially on a thin/passively-cooled chassis exactly like this
  session's own T480. Showing the label alongside the value matters —
  claiming "CPU: 68°C" when it's actually the SSD would be a real,
  actionably wrong claim, not just an imprecise one.
- **CPU thermal throttling** — whether the CPU has throttled RECENTLY
  (since the last poll), a stronger, more directly actionable signal
  than raw temperature alone ("this heat is measurably costing you
  performance right now" vs "it's warm but who knows if that
  matters"). Implemented against
  `/sys/devices/system/cpu/cpu*/thermal_throttle/core_throttle_count`
  (summed across cores, delta between polls) instead of the
  originally-proposed current-vs-max scaling-frequency comparison —
  found during implementation: `scaling_cur_freq` sitting well below
  `cpuinfo_max_freq` is the NORMAL, constant state of an idle or
  power-saving-governed CPU, not a throttling signal on its own: a
  laptop idling at its lowest P-state would read as "throttled" every
  single poll, a false positive on essentially every real machine, not
  an edge case. `core_throttle_count` is the kernel's own thermal
  governor's dedicated counter, incremented only when PROCHOT/real
  thermal throttling actually engages — unambiguous, no idle-vs-
  throttled disambiguation needed. Intel-specific sysfs (not every
  CPU family/driver exposes it) — None (not a false "not throttled")
  when absent, same degraded-not-broken tolerance as everywhere else.
- **Swap I/O rate**, not a static swap-used percentage — found live,
  reasoned through with the user: Linux proactively swaps out rarely-
  touched pages even with plenty of free RAM, so a static "2GB swap
  used" reading is a weak, often-misleading signal (harmless kernel
  housekeeping reads identically to real thrashing on a plain gauge).
  Swap ACTUALLY happening right now (`/proc/vmstat`'s `pswpin`/
  `pswpout` counters, delta between StatusWorker samples — the exact
  same pattern CPU% already uses) is the real thrashing signal.
- **Failed systemd units** (`systemctl --failed`) — shown only when
  count > 0, the same "no news is no news" treatment no-silent-
  failure's own philosophy extends to here: a quiet system says
  nothing, not "0 failed services".
- **OOM killer events since boot** — its own line, separate from the
  general error count below (see that entry for why), shown only when
  it's actually happened: "⚠ OOM killed firefox (14:32)", urgent color.
  Solves a real, common mystery ("why did my browser just vanish with
  no explanation") — detected via `journalctl -k -b -o json`
  (structured output, not fragile free-text scraping) filtered for
  "Killed process".
- **General dmesg/journalctl errors since boot** (`journalctl -p
  err..alert -b`), a bounded count — **deduplicated against OOM/
  failed-systemd** (the user's own explicit call, over the simpler
  "let them overlap" alternative) so the same real event never gets
  counted twice across different lines. Detail (the most recent
  error's own text) surfaces via preview_text on selection, same one-
  line-per-physical-line convention control.py's own action_error
  display already established (a raw embedded "\n" escapes
  draw_centered_lines' own positioning, found live building that).

Explicitly cut, with the reasoning that killed each one:
- **Uptime** — fails the three-part test outright: doesn't inform a
  decision, isn't really flex-worthy either.
- **Package updates available** — would pass the test (calls for
  action: "go update"), cut purely on implementation cost: genuinely
  different per distro (NixOS generation staleness vs Arch's own
  `checkupdates` vs dnf/apt), would need its own registry-backend
  pattern like audio/connectivity rather than one simple source.
  Revisit as its own scoped follow-up, not bundled into R6.
- **Battery health** (cycle count / energy_full vs energy_full_design)
  — genuinely free data (battery.py already reads both fields for
  aggregate()'s own energy-weighting) but belongs to bars.py's BAT
  gauge as a future addition, not this module.
- **Disk health (SMART)** — same treatment as battery health, deferred
  rather than cut outright: real value ("your disk is dying, back up
  now" — often days/weeks of advance warning), but `smartctl` commonly
  needs root/sudo (varies by distro's own udev rules) on top of being
  an external tool dependency. Revisit if the permission story turns
  out cleaner than expected on real hardware.
- **GPU usage** — same vendor-fragmentation problem VISION.md already
  rejects per-app network monitoring over; not pursued.

**Explicitly deferred past R6's own v1 (backlog, not "no"):** a
per-row "open this in a real terminal" action — tuicc computes/shows
its own bounded summary, but Enter (or a secondary key) on, say, the
failed-systemd line would spawn a terminal actually running
`systemctl status <unit>`, letting the user dig into the real, full
output themselves rather than only ever seeing tuicc's own digest.
Needs a new config value (which terminal to spawn — CONTRIBUTING.md's
own "no hardcoded personal preferences" rule means this can't default
to whatever terminal the person building it happens to run) and a
shared per-row-diagnostic-action pattern reusable across several of
the lines above, not just one. Cut from v1 purely to keep the first
pass to "tuicc shows you things", not also "tuicc becomes a launcher
for a second tier of diagnostic tools" in the same pass.

### R7 — Default preset + docs rewrite
Last, and done largely *inside tuicc* (F1 spawn, F2 resize, F3 save).
Compose the thesis screen: left column sidebar — connectivity — control
— power; top strip launcher + sessions + clock; preview dominant; media
+ system along the bottom. Hierarchy by size, not color — preview stays
the biggest thing, toggles small and quiet. Weather/calendar exist but
are NOT in the default preset. Then rewrite README/wiki around the
final philosophy (sections 1–2 of this document) — after R1–R6, half
the current docs will be wrong.

### R8 — Bars module (done) + push-worker resolution (gate before v0.1.0)
A late addition, found live after R5/R7 both already referenced
"sliders" as control's own unfinished business (see R5's own update
note above) — a session of live back-and-forth on VOL/BRI/BAT display
concluded it needed to be its own module, and along the way surfaced an
open architecture question serious enough to need its own gate before
v0.1.0 ships, not just quietly left half-built.

**`bars.py` — done, display-only.** VOL/BRI/BAT as flat vertical
gauges, deliberately NOT sharing control's "one box, dense toggle rows"
visual language (a continuous fill is a different thing to look at than
a dot + label) and deliberately NOT individually framed either — a
per-bar `draw_box_outline()` box was tried live and rejected as "too
much" for what's meant to read as a plain, dense readout, same
instinct that keeps control's own toggles frame-free. Fill uses
`render_utils.eighth_block_level()` (the exact same sub-cell technique
`media.py`'s cava visualizer already used, pulled out into a shared
function once bars needed it too) so a value moving between glyph steps
reads as continuous motion, not a jump — but the FILLED value is
quantized to the nearest `BAR_FILL_STEP` (5%) before it ever reaches
that function, independent of box height: found live, the same real
percentage change could look like a visible step on one box height and
nothing on another, purely because `eighth_block_level`'s own
`num_rows*8` resolution doesn't divide evenly into 100 and that
remainder shifts with height — quantizing the source value first makes
the visible step boundaries height-independent, the readout text above
each bar stays exact regardless (never quantized, never rounded).
Empty cells (a fully-empty row, or a partial glyph's own unpainted
sliver) are never drawn at all, not even a track texture or a colored
background — two earlier attempts at coloring "empty" (a sparse "░"
texture, then a solid darkened background pair) both got visibly
mismatched against each other in one way or another; the fix that
actually worked was simpler than either: draw nothing, let it blend
into the terminal's own background, so there's no separate shade to
mismatch against in the first place. BAT distinguishes three states —
`Charging` (accent glow) > `plugged` (a charger IS connected per
`battery.get_ac_online()`, but this pack isn't accepting charge right
now — e.g. a ThinkPad `charge_control` threshold pausing mid-range;
shown via the label swapping from "BAT" to "AC", not a color change,
found live after a color-based version was asked to be simplified) >
plain (genuinely on battery, no charger). No interaction yet —
`nav_items()` returns `[]` on purpose, same as `clock.py`; the
←→-adjust interaction R5's own control section originally scoped for
sliders is still not built, and is this module's own next piece of
work once R8's push-worker question below is settled (grab/release
naturally wants to know whether the value it's adjusting updates
instantly or on a poll tick).

**The open question this section exists to force a decision on:**
charging-start detection is still not fast enough — confirmed
unsatisfying by the user even after `battery`'s poll_interval was
tightened to 0.3s (matching audio/brightness, which DID feel fine at
that same interval). A `StatusWorker` (pull, poll_interval-based)
counterpart, `PushWorker` (`push_worker.py`) + `CombinedStatus`
(`combined_status.py`), was built as the fix: event-driven instead of
polled, one dedicated thread per domain blocking in `domain.watch()`
instead of a shared tick loop, same external `get()`/`get_error()`/
`request_action()` shape as `StatusWorker` so no module needs to know
which mechanism a given domain actually uses. `battery.watch()` was
built as its pilot domain, using `select.poll()` on `/sys/class/
power_supply/*/uevent` with `POLLPRI|POLLERR` — the low-level mechanism
`upowerd`/`acpid` use internally, confirmed live to correctly detect
the documented "first poll() fires spuriously, arm by reading, THEN it
blocks for real changes" sysfs quirk on this session's own hardware.

**It doesn't actually work, confirmed empirically, not theoretically:**
a live monitor script watching `battery.watch()`'s own yields, run
against several REAL charger unplug/replug cycles on the user's actual
machine, never once saw a real kernel event — every yield was exactly
`fallback_seconds` apart (the safety-net polling this generator also
does, added specifically because a pure push design has no way to
recover if the kernel simply never signals something changed), and the
underlying `/sys` data hadn't changed between samples despite the
physical action having genuinely happened. `select.poll()`'s documented
support for the `power_supply` sysfs class evidently isn't reliably
wired up for this attribute on this exact kernel/driver combination —
not something user space can verify ahead of time, and not something
to trust on other hardware without re-confirming live first. `battery`
was reverted to a plain `StatusWorker` `Domain` (poll_interval=0.3);
`PushWorker`/`CombinedStatus`/`battery.watch()` are all still in the
repo, tested (`test_push_worker.py`, `test_combined_status.py`, the
`watch()` tests in `test_battery.py`), but NOT wired into `main.py` —
proven infrastructure with no domain currently using it.

**This can't ship to v0.1.0 in that state** — half-built infrastructure
sitting unused, with the actual UX problem (charging-start still slow)
still open, is exactly the kind of thing this document's own "known
limitations get documented, not fixed with complexity nobody hits"
principle argues against tolerating indefinitely. Before v0.1.0 tags,
one of two things has to actually happen:

1. **Make it work for real**, via a DIFFERENT event source than sysfs
   `poll()` — that specific mechanism is what failed empirically, not
   necessarily "push in general". Two concrete candidates, in the order
   worth trying them:
   - **`pyudev`, monitoring the `power_supply` subsystem over netlink**
     (`udevadm monitor --subsystem-match=power_supply` is the CLI
     equivalent to manually confirm live first, cheaply, before writing
     any code) — genuinely a different mechanism than kernfs
     `sysfs_notify()`/`poll()`: udev's netlink broadcast happens when
     udev itself processes a kernel uevent, a different code path
     entirely, so today's negative result doesn't rule this out. No
     new runtime dependency conceptually (`pyudev` wraps functionality
     already present via `systemd`/`eudev` on any real Linux desktop)
     but IS a new Python package dependency to actually add.
   - **UPower D-Bus signals** (`org.freedesktop.UPower.Device`'s own
     `PropertiesChanged`) — the standard mechanism real desktop
     environments use for this exact problem, over the same `jeepney`
     D-Bus pattern `iwd.py`/`bluez.py`/`mpris.py` already use, so no
     new library dependency, only a new daemon dependency (`upowerd`,
     near-universal on real Linux desktops but not guaranteed present).
   Whichever one is tried, the acceptance bar is the SAME empirical one
   that caught the sysfs attempt failing: a live monitor script against
   several real charger unplug/replug cycles, not a "this should work
   per the docs" assumption. Confirmed working — wire `battery` back
   onto `PushWorker`, and this section's job is done.
2. **Or delete it** — `push_worker.py`, `combined_status.py`,
   `battery.watch()`, their tests — if neither candidate above pans out
   either. Fast-poll (`StatusWorker` at whatever interval genuinely
   feels acceptable) becomes the final, accepted answer for battery,
   documented as a real hardware/kernel limitation rather than an
   unfinished feature — and `PushWorker`/`CombinedStatus` stop being
   speculative infrastructure carried forward on the hope of a future
   use, which is its own kind of complexity this document's principles
   don't want tolerated indefinitely either.

Either outcome is an acceptable way to close this out — leaving it as
"built, tested, unused, problem still open" is the one outcome that
isn't.

## 5. Phase order

1. **R1** lifecycle (small code, changes UX ground for everything) — done
2. **R2** input claim (pure refactor, launcher tests green) — done
   (launcher, plus sessions.py's rename field and help_mode's color
   editor as nearer-term second/third consumers than originally
   planned; resize_mode deferred, see R2's own section)
3. **R3** StatusWorker (no-silent-failure lands here) — done (not a
   pure refactor in the end, see R3's own section for why)
4. **R5** control + media (fast visible wins, exercise R3 three times)
   — done, including the CAVA-style visualizer follow-on (control's
   volume/brightness sliders still excepted, see R5's own section)
5. **R6** system monitor (exercises R3 + fixture discipline)
6. **R4** connectivity v2 agents (hardest, most infrastructure needed)
7. **R7** default preset + docs
8. **R8** bars module (done) + push-worker resolution — the actual
   final gate: v0.1.0 does not tag until this section's own "make it
   work or delete it" decision is made, whichever way it goes

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
