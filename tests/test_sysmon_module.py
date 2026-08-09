"""Tests for modules/sysmon.py's pure logic — expand/collapse state
machinery (same two-level model sessions.py/media.py established, see
test_sessions_module.py's own docstring for why module-level state is
reset per-test by hand), the NICE input quartet, row/nav_items
building, and handle_row/handle_action. draw() needs a real curses
screen, left untested here, same as every other module.
"""

from types import SimpleNamespace

import tuicc.modules.sysmon as sysmon_module
from tuicc.modules.sysmon import (
    _build_rows, _diagnostics_summary_text, _format_stats_lines,
    _format_window_label, _friendly_app_name, _selected_window_index,
    _window_action_positions, apply_nice_edit, collapse, handle_action,
    handle_row, handle_nice_key, is_editing_nice, is_expanded, nav_items,
    start_nice_edit, visible_window_ids,
)
from tuicc.procmon import WindowStat


def _reset_module_state():
    sysmon_module._expanded_window_id = None
    sysmon_module._nice_target = None
    sysmon_module._nice_input = ""


def _win(window_id="1", app_id="firefox", title="Firefox", pid=100, cpu=10.0, rss_kb=2048):
    return WindowStat(window_id=window_id, app_id=app_id, title=title, pid=pid,
                       cpu_percent=cpu, rss_kb=rss_kb)


# ---------- is_expanded / collapse / _reconcile_expanded_state ----------

def test_is_expanded_reflects_expanded_window_id():
    _reset_module_state()
    assert is_expanded() is False
    sysmon_module._expanded_window_id = "1"
    assert is_expanded() is True


def test_collapse_clears_and_returns_the_window_id():
    _reset_module_state()
    sysmon_module._expanded_window_id = "1"
    assert collapse() == "1"
    assert is_expanded() is False


def test_collapse_when_nothing_expanded_returns_none():
    _reset_module_state()
    assert collapse() is None


def test_reconcile_clears_expanded_state_when_window_closes():
    _reset_module_state()
    sysmon_module._expanded_window_id = "gone"
    _build_rows(_ctx(windows=[_win(window_id="other")]), box_h=20)
    assert is_expanded() is False


def test_reconcile_leaves_expanded_state_when_window_still_present():
    _reset_module_state()
    sysmon_module._expanded_window_id = "1"
    _build_rows(_ctx(windows=[_win(window_id="1")]), box_h=20)
    assert is_expanded() is True


# ---------- NICE input quartet ----------

def test_start_nice_edit_opens_editing():
    _reset_module_state()
    assert is_editing_nice() is False
    start_nice_edit("1", pid=100, current=5)
    assert is_editing_nice() is True


def test_handle_nice_key_accepts_digits_only():
    _reset_module_state()
    start_nice_edit("1", pid=100, current=None)
    handle_nice_key(ord("1"))
    handle_nice_key(ord("0"))
    handle_nice_key(ord("x"))  # ignored, not a digit
    assert sysmon_module._nice_input == "10"


def test_handle_nice_key_backspace_removes_last_char():
    _reset_module_state()
    start_nice_edit("1", pid=100, current=None)
    handle_nice_key(ord("5"))
    handle_nice_key(127)
    assert sysmon_module._nice_input == ""


def test_handle_nice_key_escape_cancels_and_returns_false():
    _reset_module_state()
    start_nice_edit("1", pid=100, current=None)
    still_claiming = handle_nice_key(27)
    assert still_claiming is False
    assert is_editing_nice() is False


def test_apply_nice_edit_success_calls_setpriority(monkeypatch):
    _reset_module_state()
    calls = []
    monkeypatch.setattr(sysmon_module.os, "setpriority", lambda which, pid, value: calls.append((which, pid, value)))
    start_nice_edit("1", pid=100, current=None)
    handle_nice_key(ord("7"))
    result = apply_nice_edit()
    assert result == ("1", 7)
    assert calls == [(sysmon_module.os.PRIO_PROCESS, 100, 7)]
    assert is_editing_nice() is False


def test_apply_nice_edit_out_of_range_keeps_editing_open(monkeypatch):
    _reset_module_state()
    monkeypatch.setattr(sysmon_module.os, "setpriority", lambda *a: (_ for _ in ()).throw(AssertionError("should not be called")))
    start_nice_edit("1", pid=100, current=None)
    handle_nice_key(ord("9"))
    handle_nice_key(ord("9"))  # "99" -> out of 0..19 range
    result = apply_nice_edit()
    assert result is None
    assert is_editing_nice() is True


def test_apply_nice_edit_empty_input_returns_none():
    _reset_module_state()
    start_nice_edit("1", pid=100, current=None)
    assert apply_nice_edit() is None


def test_apply_nice_edit_when_nothing_editing_returns_none():
    _reset_module_state()
    assert apply_nice_edit() is None


# ---------- _selected_window_index / visible_window_ids ----------

def test_selected_window_index_from_row_selection():
    windows = [_win(window_id="a"), _win(window_id="b")]
    assert _selected_window_index(windows, "sysmon:b:row", expanded_window_id=None) == 1


def test_selected_window_index_from_expanded_state_takes_priority():
    windows = [_win(window_id="a"), _win(window_id="b")]
    assert _selected_window_index(windows, "sysmon:b:close", expanded_window_id="a") == 0


def test_visible_window_ids_fits_within_slots():
    _reset_module_state()
    windows = [_win(window_id=str(i)) for i in range(2)]
    assert visible_window_ids(windows, selected_id=None) == {"0", "1"}


def test_visible_window_ids_scrolls_to_keep_selection_visible():
    _reset_module_state()
    windows = [_win(window_id=str(i)) for i in range(6)]
    assert visible_window_ids(windows, selected_id="sysmon:4:row") == {"2", "3", "4"}


# ---------- _window_action_positions ----------

def test_window_action_positions_right_aligned_and_ordered():
    positions = _window_action_positions(x=0, w=40)
    assert [action for action, _cx in positions] == ["close", "kill", "nice"]
    # Each position strictly increases left-to-right.
    xs = [cx for _action, cx in positions]
    assert xs == sorted(xs)


# ---------- _friendly_app_name / _format_window_label ----------

def test_friendly_app_name_matches_desktop_entry_case_insensitively(monkeypatch):
    monkeypatch.setattr(
        sysmon_module.launcher_mode, "get_apps",
        lambda: [("Visual Studio Code", "code", "Code"), ("Firefox", "firefox", "firefox")],
    )
    assert _friendly_app_name("code") == "Visual Studio Code"
    assert _friendly_app_name("firefox") == "Firefox"


def test_friendly_app_name_falls_back_to_raw_app_id_when_no_match(monkeypatch):
    monkeypatch.setattr(sysmon_module.launcher_mode, "get_apps", lambda: [])
    assert _friendly_app_name("some-custom-script") == "some-custom-script"


def test_friendly_app_name_none_or_empty_is_a_placeholder(monkeypatch):
    monkeypatch.setattr(sysmon_module.launcher_mode, "get_apps", lambda: [])
    assert _friendly_app_name(None) == "?"
    assert _friendly_app_name("") == "?"


def test_format_window_label_stats_always_shown_in_full(monkeypatch):
    monkeypatch.setattr(sysmon_module.launcher_mode, "get_apps", lambda: [])
    win = _win(app_id="firefox", cpu=13.0, rss_kb=24 * 1024)
    # Even with almost no room for a name, the "[13% 24M]" stats prefix
    # itself must never be the part that gets cut — this is the actual
    # bug found live (a long window title used to push the CPU/RAM
    # numbers off the edge entirely).
    label = _format_window_label(win, available_w=9)
    assert label.startswith("[13% 24M]")


def test_format_window_label_truncates_long_name_with_ellipsis(monkeypatch):
    monkeypatch.setattr(
        sysmon_module.launcher_mode, "get_apps",
        lambda: [("Visual Studio Code", "code", "code")],
    )
    win = _win(app_id="code", cpu=10.0, rss_kb=1024)
    label = _format_window_label(win, available_w=20)
    assert label.startswith("[10% 1M] ")
    assert label.endswith("…")


def test_format_window_label_short_name_fits_untouched(monkeypatch):
    monkeypatch.setattr(
        sysmon_module.launcher_mode, "get_apps",
        lambda: [("Firefox", "firefox", "firefox")],
    )
    win = _win(app_id="firefox", cpu=5.0, rss_kb=1024)
    label = _format_window_label(win, available_w=40)
    assert label == "[5% 1M] Firefox"


# ---------- _format_stats_lines ----------

def test_format_stats_lines_shows_unknown_as_question_marks():
    lines = _format_stats_lines(sysinfo_data=None, sensors_data=None)
    assert "CPU ?%" in lines[0]
    assert "RAM ?%" in lines[0]


def test_format_stats_lines_shows_real_values():
    sysinfo_data = {
        "cpu_percent": 23.4, "ram_percent": 61.0,
        "disk": {"percent": 45.0}, "load_average": (0.52, 0.58, 0.59),
        "throttled_recently": False, "swap_in_kb_s": 0.0, "swap_out_kb_s": 0.0,
    }
    sensors_data = {"cpu_temp": (58.0, "coretemp-isa-0000", "Package id 0"), "hottest": (58.0, "coretemp-isa-0000", "Package id 0")}
    lines = _format_stats_lines(sysinfo_data, sensors_data)
    assert "CPU 23%" in lines[0]
    assert "RAM 61%" in lines[0]
    assert "TEMP 58°C" in lines[1]
    assert "CPU (Package id 0)" in lines[1]


def test_format_stats_lines_shows_throttled_flag():
    sysinfo_data = {
        "cpu_percent": None, "ram_percent": None, "disk": None,
        "load_average": None, "throttled_recently": True,
        "swap_in_kb_s": None, "swap_out_kb_s": None,
    }
    lines = _format_stats_lines(sysinfo_data, None)
    assert "THROTTLED" in lines[1]


# ---------- _diagnostics_summary_text ----------

def test_diagnostics_summary_text_none_shows_checking():
    assert _diagnostics_summary_text(None) == "Diagnostics: checking..."


def test_diagnostics_summary_text_shows_summary():
    assert _diagnostics_summary_text({"summary": "All clear"}) == "Diagnostics: All clear"


# ---------- _build_rows / nav_items ----------

class _FakeStatus:
    def __init__(self, snapshots=None, errors=None):
        self._snapshots = snapshots or {}
        self._errors = errors or {}

    def get(self, domain_name):
        return self._snapshots.get(domain_name)

    def get_error(self, domain_name):
        return self._errors.get(domain_name)


def _ctx(windows=None, windows_error=None, sysinfo_data=None, sensors_data=None,
          diagnostics_data=None, selected_id=None):
    return SimpleNamespace(
        status=_FakeStatus(
            snapshots={"windows": windows, "sysinfo": sysinfo_data, "sensors": sensors_data,
                       "diagnostics": diagnostics_data},
            errors={"windows": windows_error},
        ),
        theme={}, selected_id=selected_id, config=None, pending_confirm=None,
    )


def test_build_rows_header_shows_window_count():
    _reset_module_state()
    windows = [_win(window_id="1"), _win(window_id="2")]
    rows = _build_rows(_ctx(windows=windows), box_h=20)
    assert ("header", "Windows [2]") in rows


def test_build_rows_always_three_window_slots():
    _reset_module_state()
    for windows in ([], [_win(window_id="1")], [_win(window_id=str(i)) for i in range(6)]):
        rows = _build_rows(_ctx(windows=windows), box_h=20)
        section = [(k, p) for k, p in rows if k == "window" or (k == "empty_slot" and "window" in p)]
        assert len(section) == 3


def test_build_rows_includes_stats_lines_and_diagnostics():
    _reset_module_state()
    rows = _build_rows(_ctx(windows=[]), box_h=20)
    kinds = [k for k, _p in rows]
    assert kinds.count("stats_line") == 2
    assert "diagnostics" in kinds


def test_nav_items_collapsed_window_is_a_single_row_item():
    _reset_module_state()
    ctx = _ctx(windows=[_win(window_id="1")])
    box = (0, 0, 40, 12)

    items = nav_items(box, ctx, "sysmon")

    row_items = [it for it in items if it.target_kind == "sysmon_row"]
    assert [it.id for it in row_items] == ["sysmon:1:row"]


def test_nav_items_expanded_window_shows_three_actions():
    _reset_module_state()
    sysmon_module._expanded_window_id = "1"
    ctx = _ctx(windows=[_win(window_id="1")])
    box = (0, 0, 40, 12)

    items = nav_items(box, ctx, "sysmon")

    action_ids = [it.id for it in items if it.target_kind == "sysmon_action"]
    assert action_ids == ["sysmon:1:close", "sysmon:1:kill", "sysmon:1:nice"]


def test_nav_items_includes_diagnostics_row():
    _reset_module_state()
    ctx = _ctx(windows=[], diagnostics_data={"summary": "All clear", "failed_units": [], "oom_events": [], "general_errors": []})
    box = (0, 0, 40, 12)

    items = nav_items(box, ctx, "sysmon")

    diag_items = [it for it in items if it.target_kind == "sysmon_diagnostics"]
    assert len(diag_items) == 1
    assert diag_items[0].preview_text == [("No issues found", 0)]


# ---------- handle_row / handle_action ----------

class _FakeProvider:
    def __init__(self):
        self.closed = []

    def close_window(self, window_id):
        self.closed.append(window_id)


class _FakeActionCtx:
    def __init__(self, windows=None, provider=None):
        self.provider = provider or _FakeProvider()
        self.status = _FakeStatus(snapshots={"windows": windows or []})
        self.reselect_item_id = None


def test_handle_row_expands_the_window():
    _reset_module_state()
    ctx = SimpleNamespace(reselect_item_id=None)
    item = SimpleNamespace(focus_target="1")

    should_dismiss, pending = handle_row(ctx, item, cfg=None)

    assert sysmon_module._expanded_window_id == "1"
    assert should_dismiss is False
    assert pending is None
    assert ctx.reselect_item_id == "sysmon:1:close"


def test_handle_action_close_calls_provider_and_collapses():
    _reset_module_state()
    sysmon_module._expanded_window_id = "1"
    provider = _FakeProvider()
    ctx = _FakeActionCtx(provider=provider)
    item = SimpleNamespace(focus_target="1:close")

    should_dismiss, pending = handle_action(ctx, item, cfg=None)

    assert provider.closed == ["1"]
    assert is_expanded() is False
    assert should_dismiss is False
    assert pending is None


def test_handle_action_kill_returns_pending_confirm():
    _reset_module_state()
    ctx = _FakeActionCtx(windows=[_win(window_id="1", pid=4242)])
    item = SimpleNamespace(focus_target="1:kill")

    should_dismiss, pending = handle_action(ctx, item, cfg=None)

    assert should_dismiss is False
    assert pending["command"] == "kill -9 4242"
    assert pending["shell_true"] is False
    assert pending["module"] == "sysmon"


def test_handle_action_kill_with_unknown_pid_is_a_noop():
    _reset_module_state()
    ctx = _FakeActionCtx(windows=[_win(window_id="1", pid=None)])
    item = SimpleNamespace(focus_target="1:kill")

    should_dismiss, pending = handle_action(ctx, item, cfg=None)

    assert should_dismiss is False
    assert pending is None


def test_handle_action_nice_starts_editing():
    _reset_module_state()
    ctx = _FakeActionCtx(windows=[_win(window_id="1", pid=4242)])
    item = SimpleNamespace(focus_target="1:nice")

    handle_action(ctx, item, cfg=None)

    assert is_editing_nice() is True
    assert sysmon_module._nice_target["pid"] == 4242
