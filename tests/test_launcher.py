"""Tests for the launcher's fuzzy matching (_fuzzy_score/filter_apps,
pure functions over strings/lists, no .desktop file scanning involved)
and LauncherState (the typing-mode session layer main.py's loop
drives) — no curses, no I/O.
"""

import tuicc.modules.launcher as launcher
from tuicc.modules.launcher import (
    _fuzzy_score,
    filter_apps,
    LauncherState,
    handle_typing_key,
    enter_typing_mode,
    exit_typing_mode,
    resolve_selected,
    routed_target,
)
from tuicc.wm_config_parser import WmConfigInfo


class _FakeConfig:
    """Just enough of Config to exercise handle_typing_key's key lookups."""
    keybinds = {"left": 1001, "right": 1002}


_cfg = _FakeConfig()


# ---------- _fuzzy_score ----------

def test_fuzzy_score_exact_match():
    assert _fuzzy_score("firefox", "firefox") is not None


def test_fuzzy_score_subsequence_match():
    # "ffx" matches "firefox": f-i-r-e-f-o-x, letters in order but not contiguous
    assert _fuzzy_score("ffx", "firefox") is not None


def test_fuzzy_score_no_match_returns_none():
    assert _fuzzy_score("xyz", "firefox") is None


def test_fuzzy_score_out_of_order_returns_none():
    # "xf" would need an 'x' before an 'f' in "firefox" — it doesn't exist in that order
    assert _fuzzy_score("xf", "firefox") is None


def test_fuzzy_score_case_insensitive():
    assert _fuzzy_score("FIREFOX", "firefox") is not None
    assert _fuzzy_score("firefox", "FIREFOX") is not None


def test_fuzzy_score_empty_query_scores_zero():
    assert _fuzzy_score("", "firefox") == 0


def test_fuzzy_score_tighter_match_scores_lower():
    # "fire" is a contiguous prefix of "firefox" — tightest possible match
    tight = _fuzzy_score("fire", "firefox")
    # "ffx" is spread across the whole word — loosest match
    loose = _fuzzy_score("ffx", "firefox")

    assert tight < loose


def test_fuzzy_score_earlier_match_scores_lower():
    # same letters, but "fire" appears at the very start of "firefox"
    # vs. buried inside "wildfire" — earlier start should score better
    early = _fuzzy_score("fire", "firefox")
    late = _fuzzy_score("fire", "wildfire")

    assert early < late


# ---------- filter_apps ----------

def test_filter_apps_empty_query_returns_all_unfiltered():
    apps = [("Firefox", "firefox", "firefox"), ("kitty", "kitty", "kitty")]

    result = filter_apps("", apps)

    assert result == apps


def test_filter_apps_filters_non_matches():
    apps = [
        ("Firefox", "firefox", "firefox"),
        ("kitty", "kitty", "kitty"),
        ("LibreWolf", "librewolf", "librewolf"),
    ]

    result = filter_apps("fire", apps)

    assert [name for name, _cmd, _app_id in result] == ["Firefox"]


def test_filter_apps_ranks_better_matches_first():
    apps = [("LibreWolf", "librewolf", "librewolf"), ("Wofi", "wofi", "wofi")]

    # "wo" matches both: a tight prefix match in "Wofi" (score 1) vs.
    # a wider, later span in "LibreWolf" (the W...o at positions 5-6,
    # score 6) — Wofi should rank first.
    result = filter_apps("wo", apps)

    assert result[0][0] == "Wofi"


def test_filter_apps_no_matches_returns_empty_list():
    apps = [("Firefox", "firefox", "firefox"), ("kitty", "kitty", "kitty")]

    result = filter_apps("zzz", apps)

    assert result == []


def test_filter_apps_preserves_command_and_app_id_alongside_name():
    apps = [("Firefox", "firefox --private-window", "firefox")]

    result = filter_apps("fire", apps)

    assert result == [("Firefox", "firefox --private-window", "firefox")]


# ---------- scan_desktop_apps: app_id extraction ----------

def _write_desktop_file(d, filename, *, name, exec_cmd, wm_class=None, no_display=False):
    lines = [f"Name={name}", f"Exec={exec_cmd}"]
    if wm_class is not None:
        lines.append(f"StartupWMClass={wm_class}")
    if no_display:
        lines.append("NoDisplay=true")
    (d / filename).write_text("\n".join(lines) + "\n")


def test_scan_desktop_apps_uses_startup_wm_class_when_present(tmp_path, monkeypatch):
    _write_desktop_file(tmp_path, "firefox.desktop", name="Firefox", exec_cmd="firefox %u", wm_class="firefox")
    monkeypatch.setattr(launcher, "DESKTOP_DIRS", [str(tmp_path)])
    launcher._apps_cache = None

    apps = launcher.scan_desktop_apps()

    assert apps == [("Firefox", "firefox", "firefox")]


def test_scan_desktop_apps_falls_back_to_filename_without_wm_class(tmp_path, monkeypatch):
    _write_desktop_file(tmp_path, "obsidian.desktop", name="Obsidian", exec_cmd="obsidian %u")
    monkeypatch.setattr(launcher, "DESKTOP_DIRS", [str(tmp_path)])
    launcher._apps_cache = None

    apps = launcher.scan_desktop_apps()

    assert apps == [("Obsidian", "obsidian", "obsidian")]


# ---------- handle_typing_key ----------

def test_handle_typing_key_escape_clears_and_exits_typing_mode():
    state = LauncherState(typing_mode=True, search_query="firefo", search_selected_index=2)

    handle_typing_key(state, 27, _cfg)

    assert (state.search_query, state.search_selected_index, state.typing_mode) == ("", 0, False)


def test_handle_typing_key_backspace_removes_last_char():
    state = LauncherState(typing_mode=True, search_query="firefox", search_selected_index=3)

    handle_typing_key(state, 127, _cfg)

    assert (state.search_query, state.search_selected_index, state.typing_mode) == ("firefo", 0, True)


def test_handle_typing_key_backspace_on_empty_query_exits_typing_mode():
    state = LauncherState(typing_mode=True, search_query="", search_selected_index=0)

    handle_typing_key(state, 127, _cfg)

    assert (state.search_query, state.search_selected_index, state.typing_mode) == ("", 0, False)


def test_handle_typing_key_left_moves_selection_down():
    state = LauncherState(typing_mode=True, search_query="fire", search_selected_index=2)

    handle_typing_key(state, _cfg.keybinds["left"], _cfg)

    assert (state.search_query, state.search_selected_index, state.typing_mode) == ("fire", 1, True)


def test_handle_typing_key_left_does_not_go_below_zero():
    state = LauncherState(typing_mode=True, search_query="fire", search_selected_index=0)

    handle_typing_key(state, _cfg.keybinds["left"], _cfg)

    assert (state.search_query, state.search_selected_index, state.typing_mode) == ("fire", 0, True)


def test_handle_typing_key_right_moves_selection_up():
    state = LauncherState(typing_mode=True, search_query="fire", search_selected_index=1)

    handle_typing_key(state, _cfg.keybinds["right"], _cfg)

    assert (state.search_query, state.search_selected_index, state.typing_mode) == ("fire", 2, True)


def test_handle_typing_key_printable_char_appends_and_resets_index():
    state = LauncherState(typing_mode=True, search_query="fire", search_selected_index=3)

    handle_typing_key(state, ord("x"), _cfg)

    assert (state.search_query, state.search_selected_index, state.typing_mode) == ("firex", 0, True)


def test_handle_typing_key_unhandled_key_leaves_state_unchanged():
    state = LauncherState(typing_mode=True, search_query="fire", search_selected_index=1)

    handle_typing_key(state, 999999, _cfg)

    assert (state.search_query, state.search_selected_index, state.typing_mode) == ("fire", 1, True)


# ---------- handle_typing_key: return value (still_claiming) ----------
# VISION.md's R2 input_claim shape — main.py's dispatch reads this
# return value directly to decide whether to release the claim,
# instead of re-checking state.typing_mode afterward.

def test_handle_typing_key_returns_false_on_escape():
    state = LauncherState(typing_mode=True, search_query="firefo")

    assert handle_typing_key(state, 27, _cfg) is False


def test_handle_typing_key_returns_false_on_backspace_at_empty_query():
    state = LauncherState(typing_mode=True, search_query="")

    assert handle_typing_key(state, 127, _cfg) is False


def test_handle_typing_key_returns_true_on_backspace_with_remaining_text():
    state = LauncherState(typing_mode=True, search_query="firefox")

    assert handle_typing_key(state, 127, _cfg) is True


def test_handle_typing_key_returns_true_on_printable_char():
    state = LauncherState(typing_mode=True, search_query="fire")

    assert handle_typing_key(state, ord("x"), _cfg) is True


def test_handle_typing_key_returns_true_on_unhandled_key():
    state = LauncherState(typing_mode=True, search_query="fire")

    assert handle_typing_key(state, 999999, _cfg) is True


# ---------- enter_typing_mode / exit_typing_mode ----------

def test_enter_typing_mode_saves_previous_selection():
    state = LauncherState()

    enter_typing_mode(state, "sidebar:1", "sidebar")

    assert (state.saved_selected_id, state.saved_active_module) == ("sidebar:1", "sidebar")
    assert (state.typing_mode, state.search_query, state.search_selected_index) == (True, "", 0)


def test_enter_typing_mode_seeds_query_with_typed_character():
    state = LauncherState()

    enter_typing_mode(state, "sidebar:1", "sidebar", "f")

    assert (state.typing_mode, state.search_query, state.search_selected_index) == (True, "f", 0)


def test_exit_typing_mode_resets_editable_fields_but_not_saved_selection():
    # saved_selected_id/saved_active_module are read by the caller right
    # after exit_typing_mode returns, to restore selected_id/active_module
    # — they must survive this call untouched.
    state = LauncherState(
        typing_mode=True, search_query="fire", search_selected_index=2,
        saved_selected_id="sidebar:1", saved_active_module="sidebar",
        manual_target=True,
    )

    exit_typing_mode(state)

    assert (state.typing_mode, state.search_query, state.search_selected_index) == (False, "", 0)
    assert (state.saved_selected_id, state.saved_active_module) == ("sidebar:1", "sidebar")
    assert state.manual_target is False


def test_enter_typing_mode_resets_a_stale_manual_target():
    # A leftover value from a previous typing session must never leak
    # into a new one — see LauncherState.manual_target's own
    # docstring.
    state = LauncherState(manual_target=True)

    enter_typing_mode(state, "sidebar:1", "sidebar")

    assert state.manual_target is False


# ---------- resolve_selected ----------

def test_resolve_selected_returns_command_and_app_id_for_selection(monkeypatch):
    monkeypatch.setattr(launcher, "_apps_cache", [
        ("Firefox", "firefox", "firefox"),
        ("kitty", "kitty", "kitty"),
    ])
    state = LauncherState(search_query="", search_selected_index=1)

    result = resolve_selected(state)

    assert result == ("kitty", "kitty")


def test_resolve_selected_no_matches_returns_none(monkeypatch):
    monkeypatch.setattr(launcher, "_apps_cache", [("Firefox", "firefox", "firefox")])
    state = LauncherState(search_query="zzz", search_selected_index=0)

    result = resolve_selected(state)

    assert result is None


# ---------- routed_target ----------
# GitHub issue #9's routing-rule follow-on — see wm_config_parser.py.

def test_routed_target_returns_workspace_for_matching_rule(monkeypatch):
    monkeypatch.setattr(launcher, "_apps_cache", [("Discord", "discord", "discord")])
    state = LauncherState(search_query="disc", search_selected_index=0)
    wm_config = WmConfigInfo(routing_rules={"discord": "chat"})

    assert routed_target(state, wm_config) == "chat"


def test_routed_target_none_when_no_rule_matches(monkeypatch):
    monkeypatch.setattr(launcher, "_apps_cache", [("Firefox", "firefox", "firefox")])
    state = LauncherState(search_query="fire", search_selected_index=0)
    wm_config = WmConfigInfo(routing_rules={"discord": "chat"})

    assert routed_target(state, wm_config) is None


def test_routed_target_none_when_wm_config_is_none(monkeypatch):
    monkeypatch.setattr(launcher, "_apps_cache", [("Discord", "discord", "discord")])
    state = LauncherState(search_query="disc", search_selected_index=0)

    assert routed_target(state, None) is None


def test_routed_target_none_when_no_search_results(monkeypatch):
    monkeypatch.setattr(launcher, "_apps_cache", [("Firefox", "firefox", "firefox")])
    state = LauncherState(search_query="zzz", search_selected_index=0)
    wm_config = WmConfigInfo(routing_rules={"discord": "chat"})

    assert routed_target(state, wm_config) is None
