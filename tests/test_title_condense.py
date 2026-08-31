"""Tests for title_condense.py's condense_title() — extracted from
sidebar.py, which had zero direct test coverage of this despite being
pure, easily-testable logic; closing that gap here while also
confirming the extraction changed nothing behaviorally.
"""

from types import SimpleNamespace

from tuicc.title_condense import condense_title


def _cfg(terminal_apps=(), browser_apps=(), browser_title_names=()):
    return SimpleNamespace(
        terminal_apps=set(terminal_apps),
        browser_apps=set(browser_apps),
        browser_title_names=set(browser_title_names),
    )


def test_empty_title_returns_empty_string():
    assert condense_title("kitty", "", _cfg()) == ""
    assert condense_title("kitty", None, _cfg()) == ""


def test_terminal_app_shows_full_title_untouched():
    cfg = _cfg(terminal_apps=["kitty"])
    assert condense_title("kitty", "htop", cfg) == "htop"
    assert condense_title("kitty", "nvim ~/tuicc/main.py", cfg) == "nvim ~/tuicc/main.py"


def test_terminal_app_matching_is_case_insensitive_on_app_id():
    cfg = _cfg(terminal_apps=["kitty"])
    assert condense_title("Kitty", "htop", cfg) == "htop"


def test_terminal_app_bare_shell_prompt_returns_empty_string():
    # A bare "~" (shell's own idle prompt, home directory) carries no
    # real "what's running" information — same "nothing to add" case
    # as a title equal to app_id, just a different literal value.
    cfg = _cfg(terminal_apps=["kitty"])
    assert condense_title("kitty", "~", cfg) == ""


def test_terminal_app_non_home_directory_paths_are_not_treated_as_bare():
    # Only a LITERAL "~" is the bare-prompt case — a real path (even
    # one starting with ~) is genuine, distinguishing information.
    cfg = _cfg(terminal_apps=["kitty"])
    assert condense_title("kitty", "~/tuicc", cfg) == "~/tuicc"


def test_browser_app_drops_the_browser_name_segment_shows_last_remaining():
    cfg = _cfg(browser_apps=["firefox"], browser_title_names=["mozilla firefox"])
    title = "Issue #42 - tuicc - GitHub - Mozilla Firefox"
    assert condense_title("firefox", title, cfg) == "GitHub"


def test_browser_app_with_no_matching_browser_name_segment_shows_last_segment():
    cfg = _cfg(browser_apps=["firefox"], browser_title_names=["mozilla firefox"])
    assert condense_title("firefox", "GitHub - tuicc", cfg) == "tuicc"


def test_browser_app_single_segment_title_shows_it_unchanged():
    cfg = _cfg(browser_apps=["firefox"], browser_title_names=["mozilla firefox"])
    assert condense_title("firefox", "New Tab", cfg) == "New Tab"


def test_generic_app_shows_first_segment():
    cfg = _cfg()
    assert condense_title("thunderbird", "Inbox - Mozilla Thunderbird", cfg) == "Inbox"


def test_generic_app_first_segment_equal_to_app_id_returns_empty():
    # Title added literally nothing beyond the app's own name.
    cfg = _cfg()
    assert condense_title("firefox", "firefox - something", cfg) == ""


def test_generic_app_single_segment_title_not_split_shows_it_unless_it_equals_app_id():
    cfg = _cfg()
    # No " - "/" — "/" | " separator at all -> one segment, compared
    # against app_id as an exact (not substring) match.
    assert condense_title("thunderbird", "Mozilla Thunderbird", cfg) == "Mozilla Thunderbird"


def test_generic_app_title_with_only_whitespace_around_separators_is_stripped():
    cfg = _cfg()
    assert condense_title("app", "  First  —  Second  ", cfg) == "First"
