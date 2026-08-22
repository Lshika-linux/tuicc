"""Tests for modules/sessions.py — handle_row/handle_action with a
monkeypatched SESSIONS_DIR (real temp files, not mocked I/O, same
reasoning as test_session.py's save/load round-trip tests) and fake
ActionContext-like objects, no real WM connection needed. Plus the
expand/collapse and rename (naming) state machinery, which main.py
calls into directly (is_expanded/collapse/is_naming/start_naming/
handle_naming_key/apply_naming) rather than through the normal
target_kind dispatch.
"""

from types import SimpleNamespace

import tuicc.modules.sessions as sessions_module
from tuicc.modules.sessions import handle_row, handle_action, expanded_preview, required_fh, SLOT_COUNT
from tuicc.model import WMState, Region, Window
from tuicc.session import save_session


# ---------- required_fh ----------

def test_required_fh_is_slot_count_plus_border():
    assert required_fh(cfg=None) == SLOT_COUNT + 2  # not user-configurable today, see config.py's own SESSION_SLOT_COUNT


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
        self.reselect_item_id = None


class _FakeConfig:
    def __init__(self, session_names=None):
        self.session_names = session_names or {1: "Slot 1", 2: "Slot 2", 3: "Slot 3"}


def _reset_module_state():
    sessions_module._expanded_slot = None
    sessions_module._naming_slot = None
    sessions_module._name_input = ""


# ---------- handle_row ----------

def test_handle_row_expands_the_slot():
    _reset_module_state()
    item = SimpleNamespace(focus_target="2")

    should_dismiss, pending = handle_row(_FakeCtx(), item, cfg=None)

    assert sessions_module._expanded_slot == 2
    assert should_dismiss is False
    assert pending is None


def test_handle_row_sets_reselect_item_id_to_the_slots_first_action():
    # See ActionContext.reselect_item_id's docstring — without this,
    # expanding a row makes its own id vanish from nav_items() on the
    # next frame, tripping main.py's stale-selection recovery into
    # jumping to the sidebar instead.
    _reset_module_state()
    ctx = _FakeCtx()
    item = SimpleNamespace(focus_target="2")

    handle_row(ctx, item, cfg=None)

    assert ctx.reselect_item_id == f"sessions:action:2:{sessions_module.ACTIONS[0]}"


# ---------- handle_action: save ----------

def test_handle_action_save_writes_a_file(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions_module, "SESSIONS_DIR", tmp_path)
    _reset_module_state()
    sessions_module._expanded_slot = 1
    item = SimpleNamespace(focus_target="1:save")

    handle_action(_FakeCtx(), item, cfg=_FakeConfig())

    assert (tmp_path / "1.toml").exists()


def test_handle_action_save_collapses_back_to_browsing(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions_module, "SESSIONS_DIR", tmp_path)
    _reset_module_state()
    sessions_module._expanded_slot = 1
    item = SimpleNamespace(focus_target="1:save")

    handle_action(_FakeCtx(), item, cfg=_FakeConfig())

    assert sessions_module._expanded_slot is None


def test_handle_action_save_sets_reselect_item_id_to_its_own_row(tmp_path, monkeypatch):
    # Same bug class as handle_row (see its test's comment) — save
    # collapsing _expanded_slot must not leave a vanished action id
    # selected either. Unlike load, save has no reason to jump to the
    # sidebar, so this targets the row itself, not reselect_region_id.
    monkeypatch.setattr(sessions_module, "SESSIONS_DIR", tmp_path)
    _reset_module_state()
    sessions_module._expanded_slot = 1
    ctx = _FakeCtx()
    item = SimpleNamespace(focus_target="1:save")

    handle_action(ctx, item, cfg=_FakeConfig())

    assert ctx.reselect_item_id == "sessions:1:row"


# ---------- handle_action: load ----------

def test_handle_action_load_missing_file_is_a_noop(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions_module, "SESSIONS_DIR", tmp_path)
    _reset_module_state()
    ctx = _FakeCtx()
    item = SimpleNamespace(focus_target="1:load")

    handle_action(ctx, item, cfg=_FakeConfig())

    assert ctx.restore_queue == []


def test_handle_action_load_queues_saved_entries(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions_module, "SESSIONS_DIR", tmp_path)
    save_session(
        [{"app_id": "kitty", "cmdline": ["kitty"], "target_region": "5", "floating": False}],
        tmp_path / "2.toml",
    )
    _reset_module_state()
    ctx = _FakeCtx()
    item = SimpleNamespace(focus_target="2:load")

    handle_action(ctx, item, cfg=_FakeConfig())

    assert len(ctx.restore_queue) == 1
    assert ctx.restore_queue[0]["app_id"] == "kitty"


def test_handle_action_load_target_empty_queues_without_asking(tmp_path, monkeypatch):
    # Target region "5" has nothing on it — no need to warn.
    monkeypatch.setattr(sessions_module, "SESSIONS_DIR", tmp_path)
    save_session(
        [{"app_id": "kitty", "cmdline": ["kitty"], "target_region": "5", "floating": False}],
        tmp_path / "4.toml",
    )
    _reset_module_state()
    provider = _FakeProvider(regions=[Region(id="6", name="6", windows=[])])
    ctx = _FakeCtx(provider=provider)
    item = SimpleNamespace(focus_target="4:load")

    should_dismiss, pending = handle_action(ctx, item, cfg=_FakeConfig())

    assert pending is None
    assert len(ctx.restore_queue) == 1


def test_handle_action_load_collapses_back_to_browsing(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions_module, "SESSIONS_DIR", tmp_path)
    save_session(
        [{"app_id": "kitty", "cmdline": ["kitty"], "target_region": "5", "floating": False}],
        tmp_path / "4.toml",
    )
    _reset_module_state()
    sessions_module._expanded_slot = 4
    ctx = _FakeCtx()
    item = SimpleNamespace(focus_target="4:load")

    handle_action(ctx, item, cfg=_FakeConfig())

    assert sessions_module._expanded_slot is None


def test_handle_action_load_sets_reselect_region_id_to_tuiccs_own_region(tmp_path, monkeypatch):
    # See ActionContext.reselect_region_id's docstring — main.py bounces
    # selection back to the sidebar's own-workspace item after a load,
    # instead of leaving the cursor sitting in the Sessions module.
    monkeypatch.setattr(sessions_module, "SESSIONS_DIR", tmp_path)
    save_session(
        [{"app_id": "kitty", "cmdline": ["kitty"], "target_region": "5", "floating": False}],
        tmp_path / "2.toml",
    )
    _reset_module_state()
    provider = _FakeProvider(focused_region_id="1")
    ctx = _FakeCtx(provider=provider)
    item = SimpleNamespace(focus_target="2:load")

    handle_action(ctx, item, cfg=_FakeConfig())

    assert ctx.reselect_region_id == "1"


def test_handle_action_load_target_occupied_asks_for_confirmation(tmp_path, monkeypatch):
    # Target region "5" already has a window on it — must warn instead
    # of silently piling the restored window on top.
    monkeypatch.setattr(sessions_module, "SESSIONS_DIR", tmp_path)
    save_session(
        [{"app_id": "kitty", "cmdline": ["kitty"], "target_region": "5", "floating": False}],
        tmp_path / "5.toml",
    )
    _reset_module_state()
    existing_window = Window(id="w1", app_id="firefox", title="", focused=False, rect=(0, 0, 1, 1))
    provider = _FakeProvider(regions=[Region(id="5", name="5", windows=[existing_window])])
    ctx = _FakeCtx(provider=provider)
    item = SimpleNamespace(focus_target="5:load")

    should_dismiss, pending = handle_action(ctx, item, cfg=_FakeConfig())

    assert ctx.restore_queue == []
    assert should_dismiss is False
    assert pending["confirm_text"] == "Load? (overwrites your session)"
    assert pending["dismiss_after_confirm"] is False
    assert pending["kill_regions"] == ["5"]
    assert len(pending["restore_entries"]) == 1
    # Not set yet on this path — the restore itself is deferred until
    # confirm_yes (handle_pending_confirm sets it then, see test_actions.py).
    assert ctx.reselect_region_id is None


# ---------- handle_action: del ----------

def test_handle_action_del_missing_file_is_a_noop(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions_module, "SESSIONS_DIR", tmp_path)
    _reset_module_state()
    item = SimpleNamespace(focus_target="1:del")

    should_dismiss, pending = handle_action(_FakeCtx(), item, cfg=_FakeConfig())

    assert pending is None


def test_handle_action_del_existing_file_returns_confirm_pending(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions_module, "SESSIONS_DIR", tmp_path)
    path = tmp_path / "3.toml"
    path.write_text("")
    _reset_module_state()
    item = SimpleNamespace(focus_target="3:del")

    should_dismiss, pending = handle_action(_FakeCtx(), item, cfg=_FakeConfig())

    assert should_dismiss is False
    assert pending["shell_true"] is False
    assert pending["confirm_text"] == "Delete session 3?"
    assert str(path) in pending["command"]


# ---------- handle_action: name ----------

def test_handle_action_name_starts_naming_prefilled_with_current_name():
    _reset_module_state()
    cfg = _FakeConfig(session_names={1: "Slot 1", 2: "My Setup", 3: "Slot 3"})
    item = SimpleNamespace(focus_target="2:name")

    handle_action(_FakeCtx(), item, cfg=cfg)

    assert sessions_module.is_naming() is True
    assert sessions_module._naming_slot == 2
    assert sessions_module._name_input == "My Setup"


# ---------- expand/collapse ----------

def test_is_expanded_reflects_expanded_slot():
    _reset_module_state()
    assert sessions_module.is_expanded() is False
    sessions_module._expanded_slot = 1
    assert sessions_module.is_expanded() is True


def test_collapse_clears_expanded_slot():
    _reset_module_state()
    sessions_module._expanded_slot = 2

    sessions_module.collapse()

    assert sessions_module._expanded_slot is None


def test_collapse_returns_the_slot_that_was_expanded():
    # main.py uses this to reselect "sessions:<slot>:row" directly —
    # see collapse()'s docstring for why (nav_items() drops the
    # just-selected action id the instant this runs).
    _reset_module_state()
    sessions_module._expanded_slot = 2

    assert sessions_module.collapse() == 2


def test_collapse_returns_none_when_nothing_was_expanded():
    _reset_module_state()

    assert sessions_module.collapse() is None


# ---------- naming ----------

def test_start_naming_sets_slot_and_prefills_input():
    _reset_module_state()

    sessions_module.start_naming(2, "My Setup")

    assert sessions_module.is_naming() is True
    assert sessions_module._naming_slot == 2
    assert sessions_module._name_input == "My Setup"


def test_handle_naming_key_appends_printable_characters():
    _reset_module_state()
    sessions_module.start_naming(1, "Wor")

    sessions_module.handle_naming_key(ord("k"))

    assert sessions_module._name_input == "Work"


def test_handle_naming_key_backspace_removes_last_character():
    _reset_module_state()
    sessions_module.start_naming(1, "Work")

    sessions_module.handle_naming_key(127)

    assert sessions_module._name_input == "Wor"


def test_handle_naming_key_escape_cancels():
    _reset_module_state()
    sessions_module.start_naming(1, "Work")

    sessions_module.handle_naming_key(27)

    assert sessions_module.is_naming() is False


# ---------- handle_naming_key: return value (still_claiming) ----------
# VISION.md's R2 input_claim shape — main.py's dispatch reads this
# return value directly to decide whether to release the claim,
# instead of re-checking is_naming() afterward.

def test_handle_naming_key_returns_true_on_printable_char():
    _reset_module_state()
    sessions_module.start_naming(1, "Wor")

    assert sessions_module.handle_naming_key(ord("k")) is True


def test_handle_naming_key_returns_true_on_backspace():
    _reset_module_state()
    sessions_module.start_naming(1, "Work")

    assert sessions_module.handle_naming_key(127) is True


def test_handle_naming_key_returns_false_on_escape():
    _reset_module_state()
    sessions_module.start_naming(1, "Work")

    assert sessions_module.handle_naming_key(27) is False
    assert sessions_module._name_input == ""


def test_apply_naming_returns_slot_and_trimmed_name():
    _reset_module_state()
    sessions_module.start_naming(2, "")
    sessions_module.handle_naming_key(ord("h"))
    sessions_module.handle_naming_key(ord("i"))

    result = sessions_module.apply_naming()

    assert result == (2, "hi")
    assert sessions_module.is_naming() is False


def test_apply_naming_strips_whitespace():
    _reset_module_state()
    sessions_module.start_naming(1, "")
    for ch in "  hi  ":
        sessions_module.handle_naming_key(ord(ch))

    result = sessions_module.apply_naming()

    assert result == (1, "hi")


def test_apply_naming_returns_none_when_nothing_is_being_named():
    _reset_module_state()

    assert sessions_module.apply_naming() is None


def test_apply_naming_empty_input_returns_empty_string():
    # main.py falls back to "Slot <N>" for both the live cfg update and
    # config.set_session_name — this function itself just reports
    # exactly what was typed (or not typed), same as help_mode's color
    # editor reporting the raw typed value back to its caller.
    _reset_module_state()
    sessions_module.start_naming(3, "Slot 3")
    for _ in range(len("Slot 3")):
        sessions_module.handle_naming_key(127)

    result = sessions_module.apply_naming()

    assert result == (3, "")


# ---------- expanded_preview ----------

def test_expanded_preview_none_when_nothing_expanded(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions_module, "SESSIONS_DIR", tmp_path)
    _reset_module_state()

    assert expanded_preview() is None


def test_expanded_preview_none_when_expanded_slot_has_no_saved_file(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions_module, "SESSIONS_DIR", tmp_path)
    _reset_module_state()
    sessions_module._expanded_slot = 1

    assert expanded_preview() is None


def test_expanded_preview_groups_app_ids_by_target_region(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions_module, "SESSIONS_DIR", tmp_path)
    save_session(
        [
            {"app_id": "kitty", "cmdline": ["kitty"], "target_region": "3", "floating": False},
            {"app_id": "firefox", "cmdline": ["firefox"], "target_region": "3", "floating": False},
            {"app_id": "obsidian", "cmdline": ["obsidian"], "target_region": "5", "floating": False},
        ],
        tmp_path / "1.toml",
    )
    _reset_module_state()
    sessions_module._expanded_slot = 1

    assert expanded_preview() == {"3": ["kitty", "firefox"], "5": ["obsidian"]}
