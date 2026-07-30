"""Tests for the launcher's fuzzy matching — _fuzzy_score and
filter_apps are pure functions over strings/lists, no .desktop file
scanning involved.
"""

from tuicc.modules.launcher import _fuzzy_score, filter_apps


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
    apps = [("Firefox", "firefox"), ("kitty", "kitty")]

    result = filter_apps("", apps)

    assert result == apps


def test_filter_apps_filters_non_matches():
    apps = [("Firefox", "firefox"), ("kitty", "kitty"), ("LibreWolf", "librewolf")]

    result = filter_apps("fire", apps)

    assert [name for name, _cmd in result] == ["Firefox"]


def test_filter_apps_ranks_better_matches_first():
    apps = [("LibreWolf", "librewolf"), ("Wofi", "wofi")]

    # "wo" matches both: a tight prefix match in "Wofi" (score 1) vs.
    # a wider, later span in "LibreWolf" (the W...o at positions 5-6,
    # score 6) — Wofi should rank first.
    result = filter_apps("wo", apps)

    assert result[0][0] == "Wofi"


def test_filter_apps_no_matches_returns_empty_list():
    apps = [("Firefox", "firefox"), ("kitty", "kitty")]

    result = filter_apps("zzz", apps)

    assert result == []


def test_filter_apps_preserves_command_alongside_name():
    apps = [("Firefox", "firefox --private-window")]

    result = filter_apps("fire", apps)

    assert result == [("Firefox", "firefox --private-window")]
