"""Tests for the launcher's fuzzy matching — _fuzzy_score and
filter_apps are pure functions over strings/lists, no .desktop file
scanning involved.
"""

import tuicc.modules.launcher as launcher
from tuicc.modules.launcher import _fuzzy_score, filter_apps, handle_typing_key, enter_typing_mode


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

def test_handle_typing_key_escape_clears_and_exits():
    query, index, still_typing = handle_typing_key(27, _cfg, "firefo", 2)

    assert (query, index, still_typing) == ("", 0, False)


def test_handle_typing_key_backspace_removes_last_char():
    query, index, still_typing = handle_typing_key(127, _cfg, "firefox", 3)

    assert (query, index, still_typing) == ("firefo", 0, True)


def test_handle_typing_key_backspace_on_empty_query_exits():
    query, index, still_typing = handle_typing_key(127, _cfg, "", 0)

    assert (query, index, still_typing) == ("", 0, False)


def test_handle_typing_key_left_moves_selection_down():
    query, index, still_typing = handle_typing_key(_cfg.keybinds["left"], _cfg, "fire", 2)

    assert (query, index, still_typing) == ("fire", 1, True)


def test_handle_typing_key_left_does_not_go_below_zero():
    query, index, still_typing = handle_typing_key(_cfg.keybinds["left"], _cfg, "fire", 0)

    assert (query, index, still_typing) == ("fire", 0, True)


def test_handle_typing_key_right_moves_selection_up():
    query, index, still_typing = handle_typing_key(_cfg.keybinds["right"], _cfg, "fire", 1)

    assert (query, index, still_typing) == ("fire", 2, True)


def test_handle_typing_key_printable_char_appends_and_resets_index():
    query, index, still_typing = handle_typing_key(ord("x"), _cfg, "fire", 3)

    assert (query, index, still_typing) == ("firex", 0, True)


def test_handle_typing_key_unhandled_key_leaves_state_unchanged():
    query, index, still_typing = handle_typing_key(999999, _cfg, "fire", 1)

    assert (query, index, still_typing) == ("fire", 1, True)


# ---------- enter_typing_mode ----------

def test_enter_typing_mode_saves_previous_selection():
    result = enter_typing_mode("sidebar:1", "sidebar", "")

    assert result == ("sidebar:1", "sidebar", True, "", 0, "launcher")


def test_enter_typing_mode_seeds_query_with_typed_character():
    result = enter_typing_mode("sidebar:1", "sidebar", "f")

    assert result == ("sidebar:1", "sidebar", True, "f", 0, "launcher")
