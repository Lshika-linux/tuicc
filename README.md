# tuicc

A minimal, function-oriented TUI control center for tiling window managers.

One key summons it, and you operate the whole system from one place — sidebar with your workspaces, live preview of what's on screen, and (eventually) quick actions, launcher, systeminfo, and more.

Built on a small core that only translates window-manager state into a
generic model, with everything else as swappable modules.

This is an early project of mine — the beauty is that it's fairly simple to run on any WM, as long as you're able to write your own WM provider: the only part of the code that talks directly to your WM and translates it into tuicc's data.

## Status: early / experimental

This is a from-scratch rebuild, actively in progress. Right now it can:

- Read live window/workspace state from **sway** via the `sway.py` provider (expandable to any WM, I hope!)
- Render a workspace sidebar and a live preview of the focused workspace's windows
- Tab through workspaces in the sidebar — the preview follows your selection, independent of sway's own focus
- Press Enter to actually switch sway to the selected workspace and exit
- Load layout and settings from a TOML config, with transparent,
  human-editable presets (no hidden defaults in code)

Not yet built: i3/other WM providers, quick actions, launcher, bars,
selecting individual windows in the preview, and more. See the
architecture section below for where this is headed.

## Try it

```bash
git clone https://github.com/Lshika-linux/tuicc
cd tuicc
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

Tab to move between workspaces, Enter to switch to one, `q` to quit
without switching. Requires a running sway session (i3 and others
coming later).

## Architecture

The core does three things, and only three things:

- **WM provider layer** — translates window-manager state (sway, later
  i3/others) into a generic model (`Window`, `Region`, `WMState`), so
  nothing else in the codebase needs to know which WM you're running.
  Providers also expose actions back to the WM (switching workspace,
  focusing a window) through the same contract, so `main.py` never
  hardcodes a WM-specific command.
- **Layout engine** — converts a layout's ratios (0..1, independent of
  terminal size) into absolute terminal cells for each module. This gives
  you the freedom to run it fullscreen, as I do, or however you like.
- **Input routing** — tab order, spatial (arrow-key) navigation, and
  hotkeys, all operating on a generic `NavItem` list, independent of
  which module an item belongs to. I hope to make the navigation feel
  intuitive — feedback welcome :)

Modules — the sidebar, the preview, and future ones like quick actions
or a launcher — live as standalone files under `modules/`, each owning
both how it draws itself and where its own focusable items are. The
core never guesses a module's internal layout, and never hardcodes a
module's name. Modules can talk to each other only indirectly, through
values `main.py` computes and passes to all of them — e.g. selecting a
workspace in the sidebar tells the preview what to show, without either
module knowing the other exists.

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
    └── sway.py                # sway implementation
```

## Why

Oh lord, that's the big question.

Because I don't rice, I use. I want a functioning, no-bullshit, no-bells-and-whistles space, and I hope to build that. If you vibe with that, you're in the right place. (Colors and styling will of course be customizable — I'm still trying to make it look appealing, don't worry.)

Existing tiling WM status bars and launchers tend to be single-purpose
and WM-specific. tuicc aims for one keybind, one place, that adapts to
whichever tiling WM (or scrolling WM) you're actually running.

If you read this, genuinely, many thanks for taking the time. If you have questions, or wanna contact me for any reason, please do not hesitate, it will absolutely make my day. Have a great day internet stranger!!
