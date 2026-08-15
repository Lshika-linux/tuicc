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
nix-shell -p 'python3.withPackages (ps: [ps.pytest ps.i3ipc ps.jeepney ps.wcwidth])' --run 'pytest tests/ -v'
```

No live WM connection is needed to test: providers are tested against recorded JSON trees (`tests/fixtures/`), and WM commands are tested via a `FakeConnection` that just records `.command()` calls — see `tests/test_provider_commands.py`.

@CLAUDE/GUIDE.md
@CLAUDE/VISION.md
