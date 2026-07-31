"""Regression test for main.py's sys.path setup.

main.py adds its own src/ to sys.path so `import tuicc...` works without
installing the package. This has to be resolved relative to main.py's
own location (via __file__), not the caller's current working directory
— otherwise launching tuicc from anywhere other than the repo root (e.g.
a WM keybind spawning a terminal with a different default cwd) fails
with "No module named 'tuicc'".

This can only be tested by actually running main.py as a subprocess from
an unrelated cwd — __file__ only means anything when the file is really
executed, not when its contents are imported or exec'd from elsewhere.
"""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
MAIN_PY = REPO_ROOT / "main.py"


def test_imports_succeed_when_launched_from_an_unrelated_directory(tmp_path):
    # tmp_path is guaranteed to not be the repo root — the actual scenario
    # that broke: a WM keybind spawning tuicc from $HOME (or wherever),
    # not from inside the tuicc checkout.
    result = subprocess.run(
        [sys.executable, str(MAIN_PY)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
    )

    # main.py is expected to fail here regardless — this sandbox has no
    # real WM connection or TTY for curses to attach to. What matters is
    # *why* it fails: it must get past every `import tuicc...` first.
    # A ModuleNotFoundError for tuicc specifically would mean the src/
    # path resolution broke and regressed to being cwd-dependent again.
    assert "ModuleNotFoundError: No module named 'tuicc'" not in result.stderr
