"""Tests for modules/sessions.py — handle_mode/handle_slot with a
monkeypatched SESSIONS_DIR (real temp files, not mocked I/O, same
reasoning as test_session.py's save/load round-trip tests) and fake
ActionContext-like objects, no real WM connection needed.
"""

from types import SimpleNamespace

import tuicc.modules.sessions as sessions_module
from tuicc.modules.sessions import handle_mode, handle_slot
from tuicc.model import WMState, Region, Window
from tuicc.session import save_session


class _FakeProvider:
    def __init__(self, regions=(), focused_region_id=None):
        self._regions = regions
        self._focused_region_id = focused_region_id

    def get_state(self):
        return WMState(regions=list(self._regions), focused_region_id=self._focused_region_id)


class _FakeCtx:
    def __init__(self, provider=None):
        self.provider = provider or _FakeProvider()
        self.restore_queue = []
        self.reselect_region_id = None


# ---------- handle_mode ----------

def test_handle_mode_sets_active_mode():
    sessions_module._active_mode = "load"
    item = SimpleNamespace(focus_target="save")

    should_exit, pending = handle_mode(_FakeCtx(), item, cfg=None)

    assert sessions_module._active_mode == "save"
    assert should_exit is False
    assert pending is None


# ---------- handle_slot: save ----------

def test_handle_slot_save_writes_a_file(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions_module, "SESSIONS_DIR", tmp_path)
    sessions_module._active_mode = "save"
    item = SimpleNamespace(focus_target="1")

    handle_slot(_FakeCtx(), item, cfg=None)

    assert (tmp_path / "1.toml").exists()


# ---------- handle_slot: load ----------

def test_handle_slot_load_missing_file_is_a_noop(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions_module, "SESSIONS_DIR", tmp_path)
    sessions_module._active_mode = "load"
    ctx = _FakeCtx()
    item = SimpleNamespace(focus_target="1")

    handle_slot(ctx, item, cfg=None)

    assert ctx.restore_queue == []


def test_handle_slot_load_queues_saved_entries(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions_module, "SESSIONS_DIR", tmp_path)
    save_session(
        [{"app_id": "kitty", "cmdline": ["kitty"], "target_region": "5", "floating": False}],
        tmp_path / "2.toml",
    )
    sessions_module._active_mode = "load"
    ctx = _FakeCtx()
    item = SimpleNamespace(focus_target="2")

    handle_slot(ctx, item, cfg=None)

    assert len(ctx.restore_queue) == 1
    assert ctx.restore_queue[0]["app_id"] == "kitty"


def test_handle_slot_load_target_empty_queues_without_asking(tmp_path, monkeypatch):
    # Target region "5" has nothing on it — no need to warn.
    monkeypatch.setattr(sessions_module, "SESSIONS_DIR", tmp_path)
    save_session(
        [{"app_id": "kitty", "cmdline": ["kitty"], "target_region": "5", "floating": False}],
        tmp_path / "4.toml",
    )
    sessions_module._active_mode = "load"
    provider = _FakeProvider(regions=[Region(id="6", name="6", windows=[])])
    ctx = _FakeCtx(provider=provider)
    item = SimpleNamespace(focus_target="4")

    should_exit, pending = handle_slot(ctx, item, cfg=None)

    assert pending is None
    assert len(ctx.restore_queue) == 1


def test_handle_slot_load_sets_reselect_region_id_to_tuiccs_own_region(tmp_path, monkeypatch):
    # See ActionContext.reselect_region_id's docstring — main.py bounces
    # selection back to the sidebar's own-workspace item after a load,
    # instead of leaving the cursor sitting in the Sessions module.
    monkeypatch.setattr(sessions_module, "SESSIONS_DIR", tmp_path)
    save_session(
        [{"app_id": "kitty", "cmdline": ["kitty"], "target_region": "5", "floating": False}],
        tmp_path / "2.toml",
    )
    sessions_module._active_mode = "load"
    provider = _FakeProvider(focused_region_id="1")
    ctx = _FakeCtx(provider=provider)
    item = SimpleNamespace(focus_target="2")

    handle_slot(ctx, item, cfg=None)

    assert ctx.reselect_region_id == "1"


def test_handle_slot_load_target_occupied_asks_for_confirmation(tmp_path, monkeypatch):
    # Target region "5" already has a window on it — must warn instead
    # of silently piling the restored window on top.
    monkeypatch.setattr(sessions_module, "SESSIONS_DIR", tmp_path)
    save_session(
        [{"app_id": "kitty", "cmdline": ["kitty"], "target_region": "5", "floating": False}],
        tmp_path / "5.toml",
    )
    sessions_module._active_mode = "load"
    existing_window = Window(id="w1", app_id="firefox", title="", focused=False, rect=(0, 0, 1, 1))
    provider = _FakeProvider(regions=[Region(id="5", name="5", windows=[existing_window])])
    ctx = _FakeCtx(provider=provider)
    item = SimpleNamespace(focus_target="5")

    should_exit, pending = handle_slot(ctx, item, cfg=None)

    assert ctx.restore_queue == []
    assert should_exit is False
    assert pending["confirm_text"] == "Load? (overwrites your session)"
    assert pending["dismiss_after_confirm"] is False
    assert pending["kill_regions"] == ["5"]
    assert len(pending["restore_entries"]) == 1
    # Not set yet on this path — the restore itself is deferred until
    # confirm_yes (handle_pending_confirm sets it then, see test_actions.py).
    assert ctx.reselect_region_id is None


# ---------- handle_slot: delete ----------

def test_handle_slot_delete_missing_file_is_a_noop(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions_module, "SESSIONS_DIR", tmp_path)
    sessions_module._active_mode = "delete"
    item = SimpleNamespace(focus_target="1")

    should_exit, pending = handle_slot(_FakeCtx(), item, cfg=None)

    assert pending is None


def test_handle_slot_delete_existing_file_returns_confirm_pending(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions_module, "SESSIONS_DIR", tmp_path)
    path = tmp_path / "3.toml"
    path.write_text("")
    sessions_module._active_mode = "delete"
    item = SimpleNamespace(focus_target="3")

    should_exit, pending = handle_slot(_FakeCtx(), item, cfg=None)

    assert should_exit is False
    assert pending["shell_true"] is False
    assert pending["confirm_text"] == "Delete session 3?"
    assert str(path) in pending["command"]