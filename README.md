# tuicc

A minimal, function-oriented TUI control center for tiling window managers.

One key summons it, and you operate the whole system from one place — sidebar with your workspaces, live preview of what's on screen, and (eventually) quick actions, launcher, systeminfo, and more.

Built on a small core that only translates window-manager state into a
generic model, with everything else as swappable modules.

This is an early project of mine — the beauty is that it's fairly simple to run on any WM, as long as you're able to write your own WM provider: the only part of the code that talks directly to your WM and translates it into tuicc's data.

![tuicc showing a workspace with overlapping floating windows](./screenshot.png)

## Status: early / experimental

This is a from-scratch rebuild, actively in progress. Right now it can:

- Read live window/workspace state from **sway** and **i3** via the `sway.py` and `i3.py` providers (expandable to any WM, I hope!), including floating windows alongside tiled ones
- Render a workspace sidebar and a live preview of the focused workspace's windows, with proper Unicode box-drawing and a configurable color theme
- Tab through workspaces in the sidebar — the preview follows your selection, independent of the WM's own focus
- Arrow-key navigate into the preview and between individual windows (tiled and floating), then Enter to actually focus that window or switch to that workspace, exiting tuicc
- Load layout, navigation, provider, and theme settings from a TOML config, with transparent, human-editable presets (no hidden defaults in code) — colors accept named values, hex, or [R,G,B], approximated to the nearest of curses's 256-color palette
- Both providers are covered by a small fixture-based test suite (`tests/`), recorded from real sway and i3 sessions, so provider changes can be checked without a running WM

Not yet built: scrollable-WM support (`scroll`/niri, see below), quick
actions, launcher, bars, and a way to summon tuicc with a keybind
instead of running it from a terminal. See the architecture section
below for where this is headed.

## Try it

```bash
git clone https://github.com/Lshika-linux/tuicc
cd tuicc
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

Tab cycles through workspaces. Arrow keys move into the preview and
between windows, or back out to the sidebar. Enter switches to the
selected workspace or focuses the selected window, then exits. `q`
quits without doing anything. Requires a running sway or i3 session;
set `provider = "sway"` or `provider = "i3"` under `[wm]` in your
config (see below).

## Architecture

The core does three things, and only three things:

- **WM provider layer** — translates window-manager state (sway, i3,
  and later others) into a generic model (`Window`, `Region`,
  `WMState`), so nothing else in the codebase needs to know which WM
  you're running. Providers also expose actions back to the WM
  (switching workspace, focusing a window) through the same contract,
  so `main.py` never hardcodes a WM-specific command.
- **Layout engine** — converts a layout's ratios (0..1, independent of
  terminal size) into absolute terminal cells for each module. This gives
  you the freedom to run it fullscreen, as I do, or however you like.
- **Input routing** — tab order, spatial (arrow-key) navigation, and
  hotkeys, all operating on a generic `NavItem` list, independent of
  which module an item belongs to. Navigation inside a module (e.g.
  cycling windows in the preview) uses a predictable left-to-right
  order rather than pure spatial search, which breaks down once
  windows overlap (floating windows in particular).

Modules — the sidebar, the preview, and future ones like quick actions
or a launcher — live as standalone files under `modules/`, each owning
both how it draws itself and where its own focusable items are. The
core never guesses a module's internal layout, and never hardcodes a
module's name. Modules can talk to each other only indirectly, through
values `main.py` computes and passes to all of them — e.g. selecting a
workspace in the sidebar tells the preview what to show, without either
module knowing the other exists.

Colors are resolved from config into curses color pairs at startup
(`theme.py` for the pure resolution logic, `theme_setup.py` for the
one-time curses setup) and passed down to every module the same way —
a module that doesn't care about a given role just ignores it.

Floating windows aren't drawn to mirror reality exactly (they can
overlap arbitrarily, and neither sway nor i3 exposes true stacking
order) — the goal is a readable overview of everything that's open, so
tiled windows draw first and floating windows draw on top, always, in
a distinct accent color with a filled background.

Config and presets are plain, transparent TOML — what you see is what
you get, no hidden defaults baked into Python. If you delete your config,
tuicc regenerates a fresh default one :D

## Project structure

```
src/tuicc/
├── model.py              # Window, Region, WMState — the generic WM model
├── layout.py              # ModuleBox, Layout — module positions as ratios
├── layout_engine.py       # ratios -> absolute terminal cells
├── navigation.py          # NavItem, tab/spatial/hotkey navigation
├── theme.py                 # resolves config colors (named/hex/RGB) to curses color numbers
├── theme_setup.py           # one-time curses color pair setup at startup
├── config.py               # loads + merges packaged defaults, presets, user config
├── render.py                # module registry (draw + nav_items per module)
├── render_utils.py          # shared curses drawing helpers
├── defaults/config.toml     # packaged default config
├── presets/                 # layout presets (plain TOML)
├── modules/
│   ├── sidebar.py             # workspace list, reports nav items
│   └── preview.py             # windows of the currently focused workspace
└── providers/
    ├── base.py               # Provider contract every WM provider implements
    ├── registry.py            # provider name -> Provider class
    ├── sway.py                # sway implementation
    └── i3.py                  # i3 implementation
```

## Writing your own WM provider

tuicc doesn't know what sway, i3, or anything else is — it only knows
the `Provider` contract in `src/tuicc/providers/base.py`. If your WM
can implement this contract, tuicc can run on it, no changes needed
anywhere else in the codebase.

A provider must implement three methods:

```python
class Provider(ABC):
    def get_state(self) -> WMState:
        """Return the current window-manager state."""

    def focus_region(self, region_id: str) -> None:
        """Switch the WM's focus to the given region (e.g. workspace)."""

    def focus_window(self, window_id: str) -> None:
        """Switch the WM's focus to the given window."""
```

`WMState` (defined in `model.py`) is the generic shape every provider
translates into: a list of `Region`s (workspaces, or whatever your WM
calls them), each holding a list of `Window`s, with positions
normalized to 0..1 relative to their region — not pixels, so tuicc
never needs to know your screen resolution. `Window.floating` marks
windows that don't participate in a tiled layout.

`src/tuicc/providers/sway.py` and `src/tuicc/providers/i3.py` are the
reference implementations — both are short and worth reading end to
end before writing your own. Both use the `i3ipc` library to read the
WM's window tree and translate it into `WMState`, and send IPC
commands back to the WM for the two focus methods. They're
deliberately kept close in structure so you can diff them to see
exactly what changes between a Wayland/wlroots WM and an X11 one:
i3 has no `app_id` (falls back to `window_class`), wraps floating
windows in a `floating_con` container that sway flattens away, and
i3's `workspace.leaves()` includes floating windows too, so the i3
provider has to de-duplicate them against `floating_nodes` explicitly.
If you're targeting a wlroots-based WM (sway, or a sway fork like
`scroll`), start from `sway.py`; if you're targeting anything else
speaking i3's IPC protocol (i3 itself, or a fork like `scroll`, which
speaks a superset of it), start from `i3.py`.

Once your provider class exists, register it in
`src/tuicc/providers/registry.py`:

```python
PROVIDERS = {
    "sway": SwayProvider,
    "i3": I3Provider,
    "yourwm": YourWMProvider,  # add this line
}
```

Users select it with `provider = "yourwm"` under `[wm]` in their
config. That's the whole integration surface — nothing in `main.py`,
`render.py`, or any module needs to change.

If your WM's window layout doesn't map cleanly onto sway's model
(e.g. scrolling/infinite layouts), the `rect` normalization is the
part to think hardest about — see the `Window.rect` docstring in
`model.py` for the constraints it needs to satisfy. Open an issue or
a draft PR if you get stuck; I'd genuinely like to see this work on
more than just sway and i3.

**Note:** I'm actively researching what a `scroll`/niri provider would
need — `scroll` (a sway fork with a PaperWM-style scrolling layout)
turns out to speak a superset of sway's IPC, plus a `fully_visible`
flag per window that's a natural fit for a filmstrip-style preview
(previous/current/next column) instead of trying to render an entire
infinite strip. Nothing built yet, but the shape of the problem is
clearer than it was.

## Why

Oh lord, that's the big question.

Because I don't rice, I use. I want a functioning, no-bullshit, no-bells-and-whistles space, and I hope to build that. If you vibe with that, you're in the right place. (Colors and styling will of course be customizable — I'm still trying to make it look appealing, don't worry.)

Existing tiling WM status bars and launchers tend to be single-purpose
and WM-specific. tuicc aims for one keybind, one place, that adapts to
whichever tiling WM (or scrolling WM) you're actually running.

## License

GPLv3 — see [LICENSE](LICENSE) for the full text. In short: you're free to
use, modify, and distribute tuicc, including commercially, but if you
distribute a modified version, it must stay open source under the same
license. So that everybody gets a better tuicc :)
