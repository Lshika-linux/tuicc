"""Tests for Provider.focus_region / focus_window / move_window_to_region
across both sway and i3 — using a fake connection that just records the
command string, so no live WM is needed.

copy_to_clipboard() is different — it shells out to a real external CLI
tool (wl-copy / xclip), not conn.command() — tested by monkeypatching
subprocess.run directly, same pattern control.py's own probe_state
tests already use for the identical class of external-tool boundary.
"""

import subprocess

from i3ipc import Con

import tuicc.providers.sway as sway_module
import tuicc.providers.i3 as i3_module
from tuicc.providers.sway import SwayProvider, MARK_PREFIX as SWAY_MARK_PREFIX
from tuicc.providers.i3 import I3Provider, MARK_PREFIX as I3_MARK_PREFIX


class _FakeConfigReply:
    def __init__(self, config):
        self.config = config


class _FakeCommandReply:
    def __init__(self, success):
        self.success = success


class FakeConnection:
    def __init__(self, tree=None, config_text=None, command_results=None):
        self.commands = []
        self._tree = tree
        self._config_text = config_text
        # Popped in call order, one bool (success) per .command() call —
        # only the set_container_layout-related tests care about this at
        # all (every other test here never inspects .command()'s own
        # return value). Runs out -> defaults to success=True, so the
        # overwhelming majority of tests need never pass this.
        self._command_results = list(command_results) if command_results is not None else None

    def command(self, cmd):
        self.commands.append(cmd)
        success = self._command_results.pop(0) if self._command_results else True
        return [_FakeCommandReply(success)]

    def get_tree(self):
        return self._tree

    def get_config(self):
        return _FakeConfigReply(self._config_text)


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


# ---------- cleanup_stale_self_marks ----------

def _sway_tree_with_leaf(marks=(), pid=None, id_=99):
    return Con({
        "id": 1, "type": "root",
        "rect": {"x": 0, "y": 0, "width": 1000, "height": 800},
        "nodes": [{
            "id": 2, "type": "workspace", "num": 1, "name": "1",
            "rect": {"x": 0, "y": 0, "width": 1000, "height": 800},
            "floating_nodes": [],
            "nodes": [{
                "id": id_, "type": "con", "app_id": "kitty", "name": "w",
                "focused": False, "marks": list(marks), "pid": pid,
                "rect": {"x": 0, "y": 0, "width": 500, "height": 800},
            }],
        }],
    }, None, None)


def test_sway_cleanup_stale_self_marks_unmarks_the_wrong_window():
    # The actual live bug: mark_self()'s focus-race fallback left a
    # stale mark (embedded pid 111, some earlier/dead tuicc process) on
    # an unrelated window (real pid 9219) — cleanup_stale_self_marks()
    # is what's supposed to strip exactly this.
    tree = _sway_tree_with_leaf(marks=[f"{SWAY_MARK_PREFIX}111"], pid=9219, id_=50)
    conn = FakeConnection(tree=tree)
    provider = SwayProvider(conn=conn)

    provider.cleanup_stale_self_marks()

    assert conn.commands == [f"[con_id=50] unmark {SWAY_MARK_PREFIX}111"]


def test_sway_cleanup_stale_self_marks_leaves_a_legitimate_mark_alone():
    tree = _sway_tree_with_leaf(marks=[f"{SWAY_MARK_PREFIX}111"], pid=111, id_=51)
    conn = FakeConnection(tree=tree)
    provider = SwayProvider(conn=conn)

    provider.cleanup_stale_self_marks()

    assert conn.commands == []


def _i3_tree_with_leaf(marks=(), window=None, id_=99):
    return Con({
        "id": 1, "type": "root",
        "rect": {"x": 0, "y": 0, "width": 1000, "height": 800},
        "nodes": [{
            "id": 2, "type": "workspace", "num": 1, "name": "1",
            "rect": {"x": 0, "y": 0, "width": 1000, "height": 800},
            "floating_nodes": [],
            "nodes": [{
                "id": id_, "type": "con", "app_id": None, "window_class": "XTerm",
                "name": "w", "focused": False, "marks": list(marks), "window": window,
                "rect": {"x": 0, "y": 0, "width": 500, "height": 800},
            }],
        }],
    }, None, None)


def test_i3_cleanup_stale_self_marks_unmarks_the_wrong_window(monkeypatch):
    monkeypatch.setattr(i3_module, "_x11_pid_for_window", lambda xid: 9219)
    tree = _i3_tree_with_leaf(marks=[f"{I3_MARK_PREFIX}111"], window=555, id_=50)
    conn = FakeConnection(tree=tree)
    provider = I3Provider(conn=conn)

    provider.cleanup_stale_self_marks()

    assert conn.commands == [f"[con_id=50] unmark {I3_MARK_PREFIX}111"]


def test_i3_cleanup_stale_self_marks_leaves_a_legitimate_mark_alone(monkeypatch):
    monkeypatch.setattr(i3_module, "_x11_pid_for_window", lambda xid: 111)
    tree = _i3_tree_with_leaf(marks=[f"{I3_MARK_PREFIX}111"], window=555, id_=51)
    conn = FakeConnection(tree=tree)
    provider = I3Provider(conn=conn)

    provider.cleanup_stale_self_marks()

    assert conn.commands == []


# ---------- wm_config() ----------
# wm_config_parser.py's own test file covers the parsing logic
# thoroughly — these two just confirm each provider's wm_config()
# actually delegates to get_wm_config(self.conn), same "thin
# IPC-issuing wrapper around a pure/shared function" shape as
# cleanup_stale_self_marks()/_stale_self_marks() above.

def test_sway_wm_config_delegates_to_get_config():
    conn = FakeConnection(config_text="bindsym Mod4+1 workspace number 1")
    provider = SwayProvider(conn=conn)

    result = provider.wm_config()

    assert result.workspace_names == ["1"]


def test_i3_wm_config_delegates_to_get_config():
    conn = FakeConnection(config_text='for_window [class="Discord"] move container to workspace chat')
    provider = I3Provider(conn=conn)

    result = provider.wm_config()

    assert result.routing_rules == {"Discord": "chat"}


# ---------- get_tiled_tree / set_container_layout ----------
# See CLAUDE/NOTES/design-decisions.md#append-layout-doesnt-exist-on-sway.

def _tree_with_workspace(num, nodes, floating_nodes=()):
    return Con({
        "id": 1, "type": "root",
        "rect": {"x": 0, "y": 0, "width": 1000, "height": 800},
        "nodes": [{
            "id": 2, "type": "workspace", "num": num, "name": str(num),
            "rect": {"x": 0, "y": 0, "width": 1000, "height": 800},
            "floating_nodes": list(floating_nodes),
            "nodes": list(nodes),
        }],
    }, None, None)


def _window_node(id_, app_id=None, window_class=None):
    # window_class must be nested under window_properties.class — i3ipc's
    # own Con parser only ever reads it from there (confirmed against
    # its real source), never from a flat top-level "window_class" key.
    node = {
        "id": id_, "type": "con", "app_id": app_id,
        "name": "w", "focused": False, "marks": [], "pid": None,
        "rect": {"x": 0, "y": 0, "width": 500, "height": 800}, "nodes": [], "floating_nodes": [],
    }
    if window_class is not None:
        node["window_properties"] = {"class": window_class}
    return node


def test_sway_get_tiled_tree_walks_the_real_workspace():
    tree = _tree_with_workspace(3, [_window_node(50, app_id="firefox")])
    conn = FakeConnection(tree=tree)
    provider = SwayProvider(conn=conn)

    assert provider.get_tiled_tree("3") == {"type": "window", "app_id": "firefox"}


def test_sway_get_tiled_tree_accepts_an_already_resolved_region_id():
    # Regression: loop_state.focus_id (main.py) isn't always guaranteed
    # bare — see bare_workspace_id()'s own docstring for the real bug
    # this covers. workspace.num stays the bare "3" regardless of the
    # workspace's own real "3:III" name (independent fields on the wire).
    tree = Con({
        "id": 1, "type": "root",
        "rect": {"x": 0, "y": 0, "width": 1000, "height": 800},
        "nodes": [{
            "id": 2, "type": "workspace", "num": 3, "name": "3:III",
            "rect": {"x": 0, "y": 0, "width": 1000, "height": 800},
            "floating_nodes": [], "nodes": [_window_node(50, app_id="firefox")],
        }],
    }, None, None)
    conn = FakeConnection(tree=tree)
    provider = SwayProvider(conn=conn)

    assert provider.get_tiled_tree("3:III") == {"type": "window", "app_id": "firefox"}


def test_sway_get_tiled_tree_none_for_unknown_region():
    tree = _tree_with_workspace(3, [_window_node(50, app_id="firefox")])
    conn = FakeConnection(tree=tree)
    provider = SwayProvider(conn=conn)

    assert provider.get_tiled_tree("9") is None


def test_i3_get_tiled_tree_walks_the_real_workspace():
    tree = _tree_with_workspace(3, [_window_node(50, window_class="Firefox")])
    conn = FakeConnection(tree=tree)
    provider = I3Provider(conn=conn)

    assert provider.get_tiled_tree("3") == {"type": "window", "app_id": "Firefox"}


def _tree_with_group(member_id, wrapper_id, layout):
    # Simulates the tree AFTER "[con_id=<member_id>] layout <layout>"
    # already ran — set_container_layout()'s own job is just reading
    # this back, not building it (see tiled_tree.py's dedicated tests
    # for the pure read-back logic itself). Providers only need to
    # confirm they delegate to it correctly with the right conn.
    return Con({
        "id": 1, "type": "root",
        "rect": {"x": 0, "y": 0, "width": 1000, "height": 800},
        "nodes": [{
            "id": wrapper_id, "type": "con", "layout": layout,
            "rect": {"x": 0, "y": 0, "width": 1000, "height": 800},
            "floating_nodes": [],
            "nodes": [_window_node(member_id, app_id="firefox")],
        }],
    }, None, None)


def test_sway_set_container_layout_delegates_to_tiled_tree():
    tree = _tree_with_group(member_id=3, wrapper_id=2, layout="tabbed")
    conn = FakeConnection(tree=tree)
    provider = SwayProvider(conn=conn)

    result = provider.set_container_layout("3", "tabbed")

    assert result == "2"
    assert conn.commands == ["[con_id=3] layout tabbed"]


def test_sway_set_container_layout_translates_stacked_keyword():
    tree = _tree_with_group(member_id=3, wrapper_id=2, layout="stacked")
    conn = FakeConnection(tree=tree)
    provider = SwayProvider(conn=conn)

    provider.set_container_layout("3", "stacked")

    assert conn.commands == ["[con_id=3] layout stacking"]


def test_sway_set_container_layout_returns_none_on_failure():
    conn = FakeConnection(command_results=[False], tree=_tree_with_group(3, 2, "tabbed"))
    provider = SwayProvider(conn=conn)

    assert provider.set_container_layout("3", "tabbed") is None


def test_i3_set_container_layout_delegates_to_tiled_tree():
    tree = _tree_with_group(member_id=3, wrapper_id=2, layout="tabbed")
    conn = FakeConnection(tree=tree)
    provider = I3Provider(conn=conn)

    result = provider.set_container_layout("3", "tabbed")

    assert result == "2"
    assert conn.commands == ["[con_id=3] layout tabbed"]


def _group_node(id_, layout, member_ids):
    return {
        "id": id_, "type": "con", "layout": layout,
        "rect": {"x": 0, "y": 0, "width": 500, "height": 800},
        "floating_nodes": [],
        "nodes": [_window_node(m, app_id="firefox") for m in member_ids],
    }


def test_sway_list_container_groups_walks_the_real_workspace():
    tree = _tree_with_workspace(3, [_group_node(10, "stacked", [11, 12])])
    conn = FakeConnection(tree=tree)
    provider = SwayProvider(conn=conn)

    assert provider.list_container_groups("3") == [{"label": "S1", "container_id": "10"}]


def test_sway_list_container_groups_accepts_an_already_resolved_region_id():
    tree = _tree_with_workspace(3, [_group_node(10, "stacked", [11, 12])])
    conn = FakeConnection(tree=tree)
    provider = SwayProvider(conn=conn)

    assert provider.list_container_groups("3:III") == [{"label": "S1", "container_id": "10"}]


def test_sway_list_container_groups_empty_for_unknown_region():
    tree = _tree_with_workspace(3, [_group_node(10, "stacked", [11, 12])])
    conn = FakeConnection(tree=tree)
    provider = SwayProvider(conn=conn)

    assert provider.list_container_groups("9") == []


def test_i3_list_container_groups_walks_the_real_workspace():
    tree = _tree_with_workspace(3, [_group_node(10, "tabbed", [11, 12])])
    conn = FakeConnection(tree=tree)
    provider = I3Provider(conn=conn)

    assert provider.list_container_groups("3") == [{"label": "T1", "container_id": "10"}]


def test_sway_move_window_to_group_delegates_to_tiled_tree():
    conn = FakeConnection()
    provider = SwayProvider(conn=conn)

    result = provider.move_window_to_group("10", "20")

    assert result is True
    assert len(conn.commands) == 3
    assert conn.commands[0].startswith("[con_id=20] mark --add ")
    assert conn.commands[1].startswith("[con_id=10] move window to mark ")


def test_i3_move_window_to_group_delegates_to_tiled_tree():
    conn = FakeConnection()
    provider = I3Provider(conn=conn)

    result = provider.move_window_to_group("10", "20")

    assert result is True
    assert len(conn.commands) == 3


# ---------- set_floating_geometry ----------
# No prior direct coverage of this method existed — the real, live-
# reported bug (a launcher spawn's "floating" placement silently doing
# nothing whenever loop_state.focus_id already held a resolved "N:Name"
# value) slipped through partly because of that gap; see
# bare_workspace_id()'s own docstring (wm_config_parser.py) for the
# root cause and fix.

def test_sway_set_floating_geometry_computes_absolute_pixels_from_workspace_rect():
    tree = _tree_with_workspace(3, [_window_node(50, app_id="firefox")])
    tree.workspaces()[0].rect.x = 100
    tree.workspaces()[0].rect.y = 50
    tree.workspaces()[0].rect.width = 1000
    tree.workspaces()[0].rect.height = 800
    conn = FakeConnection(tree=tree)
    provider = SwayProvider(conn=conn)

    provider.set_floating_geometry("50", "3", (0.25, 0.25, 0.5, 0.5))

    assert conn.commands == [
        "[con_id=50] floating enable, resize set 500px 400px, move position 350px 250px"
    ]


def test_sway_set_floating_geometry_accepts_an_already_resolved_region_id():
    # The actual regression: this used to silently no-op (workspace
    # lookup returned None, `return` with zero commands issued) whenever
    # region_id was "3:III" instead of bare "3".
    tree = _tree_with_workspace(3, [_window_node(50, app_id="firefox")])
    conn = FakeConnection(tree=tree)
    provider = SwayProvider(conn=conn)

    provider.set_floating_geometry("50", "3:III", (0.25, 0.25, 0.5, 0.5))

    assert len(conn.commands) == 1
    assert conn.commands[0].startswith("[con_id=50] floating enable")


def test_sway_set_floating_geometry_no_op_for_unknown_region():
    tree = _tree_with_workspace(3, [_window_node(50, app_id="firefox")])
    conn = FakeConnection(tree=tree)
    provider = SwayProvider(conn=conn)

    provider.set_floating_geometry("50", "9", (0.25, 0.25, 0.5, 0.5))

    assert conn.commands == []


def test_i3_set_floating_geometry_accepts_an_already_resolved_region_id():
    tree = _tree_with_workspace(3, [_window_node(50, window_class="Firefox")])
    conn = FakeConnection(tree=tree)
    provider = I3Provider(conn=conn)

    provider.set_floating_geometry("50", "3:III", (0.25, 0.25, 0.5, 0.5))

    assert len(conn.commands) == 1
    assert conn.commands[0].startswith("[con_id=50] floating enable")
