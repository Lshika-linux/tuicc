# tuicc

A minimal, function-oriented TUI control center for tiling window managers.

One key summons it, and you operate the whole system from one place — sidebar with your workspaces, live preview of what's on screen, and (eventually) quick actions, launcher, systeminfo, and more.

Built on a small core that only translates window-manager state into a
generic model, with everything else as swappable modules.

This is an early project of mine — the beauty is that it's fairly simple to run on any WM, as long as you're able to write your own WM provider: the only part of the code that talks directly to your WM and translates it into tuicc's data.

## Status: early / experimental

This is a from-scratch rebuild, actively in progress. Right now it can:

- Read live window/workspace state from **sway** via the `sway.py` provider (expandable to any WM, I hope!)
- Render a workspace sidebar and a live preview of the focused
  workspace's windows (read-only for now — no interaction yet)
- Load layout and settings from a TOML config, with transparent,
  human-editable presets (no hidden defaults in code)

Not yet built: i3/other WM providers, quick actions, launcher, bars,
input handling beyond "press any key to quit", and more. See the
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

Requires a running sway session (i3 and others coming later).

## Architecture

The core does three things, and only three things:

- **WM provider layer** — translates window-manager state (sway, later
  i3/others) into a generic model (`Window`, `Region`, `WMState`), so
  nothing else in the codebase needs to know which WM you're running.
- **Layout engine** — converts a layout's ratios (0..1, independent of
  terminal size) into absolute terminal cells for each module. This gives
  you the freedom to run it fullscreen, as I do, or however you like.
- **Input routing** — tab order, spatial (arrow-key) navigation, and
  hotkeys, all operating on a generic `NavItem` list, independent of
  which module an item belongs to. I hope to make the navigation feel
  intuitive — feedback welcome :)

Everything else — the sidebar, the preview, and future modules like
quick actions or a launcher — is a swappable, movable, and (eventually)
disableable module built on top of this core, registered by name rather
than hardcoded.

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
├── render.py                # curses rendering, module registry
├── defaults/config.toml     # packaged default config
├── presets/                 # layout presets (plain TOML)
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
