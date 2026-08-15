"""Tests for Provider.focus_region / focus_window / move_window_to_region
across both sway and i3 — using a fake connection that just records the
command string, so no live WM is needed.

copy_to_clipboard() is different — it shells out to a real external CLI
tool (wl-copy / xclip), not conn.command() — tested by monkeypatching
subprocess.run directly, same pattern control.py's own probe_state
tests already use for the identical class of external-tool boundary.
"""

import subprocess

import tuicc.providers.sway as sway_module
import tuicc.providers.i3 as i3_module
from tuicc.providers.sway import SwayProvider
from tuicc.providers.i3 import I3Provider


class FakeConnection:
    def __init__(self):
        self.commands = []

    def command(self, cmd):
        self.commands.append(cmd)


def test_sway_move_window_to_region():
    conn = FakeConnection()
    provider = SwayProvider(conn=conn)

    provider.move_window_to_region("42", "3")

    assert conn.commands == ["[con_id=42] move container to workspace number 3"]


def test_sway_focus_region():
    conn = FakeConnection()
    provider = SwayProvider(conn=conn)

    provider.focus_region("3")

    assert conn.commands == ["workspace 3"]


def test_sway_focus_window():
    conn = FakeConnection()
    provider = SwayProvider(conn=conn)

    provider.focus_window("42")

    assert conn.commands == ["[con_id=42] focus"]


def test_i3_move_window_to_region():
    conn = FakeConnection()
    provider = I3Provider(conn=conn)

    provider.move_window_to_region("42", "3")

    assert conn.commands == ["[con_id=42] move container to workspace number 3"]


def test_i3_focus_region_uses_number_prefix():
    """i3's focus_region deliberately differs from sway's: 'workspace N'
    can create a new, separate workspace if a named workspace happens
    to share that number as a prefix (e.g. "3: web"). 'workspace number
    N' matches by numeric id regardless of any trailing name.
    """
    conn = FakeConnection()
    provider = I3Provider(conn=conn)

    provider.focus_region("3")

    assert conn.commands == ["workspace number 3"]


def test_i3_focus_window():
    conn = FakeConnection()
    provider = I3Provider(conn=conn)

    provider.focus_window("42")

    assert conn.commands == ["[con_id=42] focus"]


def test_sway_mark_self():
    import os
    conn = FakeConnection()
    provider = SwayProvider(conn=conn)

    provider.mark_self()

    assert conn.commands == [f"mark --add _tuicc_self_{os.getpid()}"]


def test_i3_mark_self():
    import os
    conn = FakeConnection()
    provider = I3Provider(conn=conn)

    provider.mark_self()

    assert conn.commands == [f"mark --add _tuicc_self_{os.getpid()}"]


def test_sway_mark_self_with_app_id_uses_criteria():
    import os
    conn = FakeConnection()
    provider = SwayProvider(conn=conn)

    provider.mark_self("tuicc_scratch")

    assert conn.commands == [f'[app_id="tuicc_scratch"] mark --add _tuicc_self_{os.getpid()}']


def test_i3_mark_self_with_app_id_uses_class_criteria():
    import os
    conn = FakeConnection()
    provider = I3Provider(conn=conn)

    provider.mark_self("tuicc_scratch")

    assert conn.commands == [f'[class="tuicc_scratch"] mark --add _tuicc_self_{os.getpid()}']


def test_sway_dismiss_self():
    import os
    conn = FakeConnection()
    provider = SwayProvider(conn=conn)

    provider.dismiss_self()

    assert conn.commands == [f"[con_mark=_tuicc_self_{os.getpid()}] move scratchpad"]


def test_i3_dismiss_self():
    import os
    conn = FakeConnection()
    provider = I3Provider(conn=conn)

    provider.dismiss_self()

    assert conn.commands == [f"[con_mark=_tuicc_self_{os.getpid()}] move scratchpad"]


def test_sway_focus_self():
    import os
    conn = FakeConnection()
    provider = SwayProvider(conn=conn)

    provider.focus_self()

    assert conn.commands == [f"[con_mark=_tuicc_self_{os.getpid()}] focus"]


def test_sway_focus_self_fullscreen():
    import os
    conn = FakeConnection()
    provider = SwayProvider(conn=conn)

    provider.focus_self(fullscreen=True)

    assert conn.commands == [f"[con_mark=_tuicc_self_{os.getpid()}] focus, fullscreen enable"]


def test_sway_focus_self_force_relayout():
    # See Provider.focus_self()'s docstring: forces sway to run a real
    # layout pass for tuicc's own workspace by briefly toggling
    # fullscreen off and back on — the fix for a sibling window landing
    # there never getting a real rect computed while tuicc stays
    # fullscreen without interruption.
    import os
    conn = FakeConnection()
    provider = SwayProvider(conn=conn)

    provider.focus_self(fullscreen=True, force_relayout=True)

    assert conn.commands == [f"[con_mark=_tuicc_self_{os.getpid()}] focus, fullscreen disable, fullscreen enable"]


def test_sway_focus_self_force_relayout_ignored_without_fullscreen():
    # force_relayout is only meaningful alongside fullscreen=True — a
    # non-fullscreen tuicc never suppresses its workspace's layout in
    # the first place, nothing to work around.
    import os
    conn = FakeConnection()
    provider = SwayProvider(conn=conn)

    provider.focus_self(fullscreen=False, force_relayout=True)

    assert conn.commands == [f"[con_mark=_tuicc_self_{os.getpid()}] focus"]


def test_i3_focus_self():
    import os
    conn = FakeConnection()
    provider = I3Provider(conn=conn)

    provider.focus_self()

    assert conn.commands == [f"[con_mark=_tuicc_self_{os.getpid()}] focus"]


def test_i3_focus_self_fullscreen():
    import os
    conn = FakeConnection()
    provider = I3Provider(conn=conn)

    provider.focus_self(fullscreen=True)

    assert conn.commands == [f"[con_mark=_tuicc_self_{os.getpid()}] focus, fullscreen enable"]


def test_i3_focus_self_force_relayout():
    import os
    conn = FakeConnection()
    provider = I3Provider(conn=conn)

    provider.focus_self(fullscreen=True, force_relayout=True)

    assert conn.commands == [f"[con_mark=_tuicc_self_{os.getpid()}] focus, fullscreen disable, fullscreen enable"]


def test_i3_focus_self_force_relayout_ignored_without_fullscreen():
    import os
    conn = FakeConnection()
    provider = I3Provider(conn=conn)

    provider.focus_self(fullscreen=False, force_relayout=True)

    assert conn.commands == [f"[con_mark=_tuicc_self_{os.getpid()}] focus"]


def test_sway_no_focus_next_window():
    conn = FakeConnection()
    provider = SwayProvider(conn=conn)

    provider.no_focus_next_window(4242)

    assert conn.commands == ["for_window [pid=4242] no_focus"]


def test_i3_no_focus_next_window():
    conn = FakeConnection()
    provider = I3Provider(conn=conn)

    provider.no_focus_next_window(4242)

    assert conn.commands == ["for_window [pid=4242] no_focus"]


def test_sway_close_window():
    conn = FakeConnection()
    provider = SwayProvider(conn=conn)

    provider.close_window("42")

    assert conn.commands == ["[con_id=42] kill"]


def test_i3_close_window():
    conn = FakeConnection()
    provider = I3Provider(conn=conn)

    provider.close_window("42")

    assert conn.commands == ["[con_id=42] kill"]


# ---------- copy_to_clipboard ----------

def test_sway_copy_to_clipboard_uses_wl_copy(monkeypatch):
    calls = []

    def _run(cmd, **kwargs):
        calls.append((cmd, kwargs.get("input")))
        return subprocess.CompletedProcess(args=cmd, returncode=0)

    monkeypatch.setattr(sway_module.subprocess, "run", _run)
    provider = SwayProvider(conn=FakeConnection())

    result = provider.copy_to_clipboard("hello")

    assert calls == [(["wl-copy"], b"hello")]
    assert result is True


def test_sway_copy_to_clipboard_missing_binary_returns_false(monkeypatch):
    def _raise(cmd, **kwargs):
        raise FileNotFoundError("wl-copy not found")

    monkeypatch.setattr(sway_module.subprocess, "run", _raise)
    provider = SwayProvider(conn=FakeConnection())

    assert provider.copy_to_clipboard("hello") is False


def test_sway_copy_to_clipboard_nonzero_exit_returns_false(monkeypatch):
    def _run(cmd, **kwargs):
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(sway_module.subprocess, "run", _run)
    provider = SwayProvider(conn=FakeConnection())

    assert provider.copy_to_clipboard("hello") is False


def test_i3_copy_to_clipboard_uses_xclip(monkeypatch):
    calls = []

    def _run(cmd, **kwargs):
        calls.append((cmd, kwargs.get("input")))
        return subprocess.CompletedProcess(args=cmd, returncode=0)

    monkeypatch.setattr(i3_module.subprocess, "run", _run)
    provider = I3Provider(conn=FakeConnection())

    result = provider.copy_to_clipboard("hello")

    assert calls == [(["xclip", "-selection", "clipboard"], b"hello")]
    assert result is True


def test_i3_copy_to_clipboard_missing_binary_returns_false(monkeypatch):
    def _raise(cmd, **kwargs):
        raise FileNotFoundError("xclip not found")

    monkeypatch.setattr(i3_module.subprocess, "run", _raise)
    provider = I3Provider(conn=FakeConnection())

    assert provider.copy_to_clipboard("hello") is False
