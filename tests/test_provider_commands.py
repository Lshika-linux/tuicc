"""Tests for Provider.focus_region / focus_window / move_window_to_region
across both sway and i3 — using a fake connection that just records the
command string, so no live WM is needed.
"""

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
