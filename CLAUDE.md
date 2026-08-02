# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run (needs a live sway or i3 session; provider set via [wm] provider = "sway"/"i3" in ~/.config/tuicc/config.toml)
python main.py

# Install deps into a venv
python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt

# Run the full test suite (pytest.ini sets pythonpath = src, no manual PYTHONPATH needed)
pytest tests/ -v

# Run a single test file / single test
pytest tests/test_layout_engine.py -v
pytest tests/test_layout_engine.py::test_name -v

# Same, via nix instead of a venv
nix-shell -p 'python3.withPackages (ps: [ps.pytest ps.i3ipc ps.jeepney])' --run 'pytest tests/ -v'
```

No live WM connection is needed to test: providers are tested against recorded JSON trees (`tests/fixtures/`), and WM commands are tested via a `FakeConnection` that just records `.command()` calls — see `tests/test_provider_commands.py`.

## Architecture

Full architecture, config format, and the WM-provider-writing guide are in `README.md` — read that first for the big picture. This section only covers cross-file wiring that isn't obvious from any single file.

**Two same-named, different-typed `ctx` objects.** `draw(stdscr, box, ctx, module_name)` and `nav_items(box, ctx, module_name)` (every module's contract) receive a `RenderContext` (`context.py`) — per-frame render state (selection, theme, config, connectivity snapshot, etc). A `TARGET_KIND` handler's `handle(ctx, item, cfg)` (registered in `ACTION_HANDLERS`) receives an `ActionContext` (`actions.py`) — just `.provider` and `.connectivity`. Same parameter name, unrelated dataclasses; don't assume a module's `handle()` can reach into render state, or that `draw()` can call WM actions directly.

**Adding a module** touches `render.py`'s `MODULES` and `NAV_PROVIDERS` dicts only — never `draw_all()`/`collect_nav_items()` themselves (enforced by a comment at the top of `render.py`, not by code). If the module needs a custom `target_kind` (not just the generic `"region"`/`"window"` handled by `actions.py`'s `BASE_HANDLERS`), it registers its own handler(s) into `ACTION_HANDLERS` — either a single `TARGET_KIND` + `handle()` (see `quick_actions.py`, `power_menu.py`) or a `HANDLERS` dict for multiple kinds (see `connectivity.py`: `wifi_network`/`bluetooth_device`). `MODULES` has a second consumer besides `render.py` itself: resize mode's spawn picker (`main.py`) diffs `MODULES.keys()` against `cfg.layout.boxes` to offer only modules not currently placed — a new module is spawnable from inside tuicc for free, no extra wiring.

**Layout boxes are plain, independent x/y/w/h ratios** (`layout.py`) — no `right_of`/`below`/`above`/`bottom`/`cols`/`rows`/`fill_to` derivation system anymore (removed; see git history/README if you need the old reasoning). `compute_boxes()` (`layout_engine.py`) is a flat per-box loop, not a dependency resolver. Resizing or moving one box never affects another.

**Resize mode is `main.py`-owned state, not a module.** `resize_mode.py`'s `enter_resize`/`resize_step`/`move_step`/`cancel_resize` are pure `ModuleBox` mutators; the state machine driving them (`resize_mode`, `resize_dimension`, `resize_is_new_box`, `resize_confirm_delete`, `spawn_picker_mode`, ...) lives entirely in `main.py`'s loop — no module's `draw()` reacts to it, and its hint line is written directly onto `stdscr` after `draw_all()` returns, not through `RenderContext`. `spawn_box`/`resize`/`save_layout`/`cycle_preset` (F1-F4) are each backed by one `do_*()` closure defined once in `main()`, called from both the normal dispatch chain and again from inside the `if resize_mode:` hijack (where `commit_resize()` runs first, so an in-progress resize is saved rather than silently discarded) — change the closure, not either call site, to keep the two in sync. `config.py`'s `set_active_preset()` is the one place in the codebase that patches `config.toml` by hand (a single `preset = N` line) instead of going through `tomllib`/`tomli_w`, specifically to avoid stripping the file's comments.

**Nothing is cached per-frame.** `compute_boxes()`, `provider.get_state()`, and `collect_nav_items()` all rerun every loop iteration in `main.py`. Deliberate simplicity-over-performance call — item counts are small enough (dozens) for it not to matter. Don't add caching here without a measured reason.

**Global shortcuts bypass normal input routing.** `cfg.global_shortcuts` (built from `power_menu.action.shortcut` entries) is checked in `main.py`'s loop immediately after the `pending_confirm` check, before typing-mode or module-local key handling — it fires regardless of which module is active. Collisions (against each other or against `[navigation.keys]`) are caught at config load time, not at keypress time.

**`mark_self()` is provider-optional, not abstract.** `Provider.mark_self()` (`providers/base.py`) defaults to a no-op; only sway/i3 implement it via WM marks. The mark string is `_tuicc_self_<pid>`, not a fixed literal — sway/i3 marks must be globally unique, so a shared string would let a second tuicc instance silently steal the first instance's mark. Filtering matches the *prefix*, so every running instance still excludes every other tuicc window, not just its own. Known limitation, left unfixed: `mark_self()` assumes tuicc's own window is whatever is focused at the moment it's called, which can race if multiple instances are spawned back-to-back.
