"""Tests for modules/preview.py's _window_label()/_corner_label()/
_corner_positions() — the pure "what to draw, and where" logic, kept
separate from _draw_window()'s own curses calls so all three are
testable without a fake stdscr, same split every other module in this
codebase uses for its own draw-adjacent logic.

No test file existed for preview.py before this — draw()/_draw_window()
themselves stay untested (curses-only, same as every other module's
draw()), but all three pure functions here are genuinely testable.
"""

from types import SimpleNamespace

from tuicc.model import Window
from tuicc.modules.preview import _corner_label, _corner_positions, _window_label


def _cfg(terminal_apps=(), browser_apps=(), browser_title_names=()):
    return SimpleNamespace(
        terminal_apps=set(terminal_apps),
        browser_apps=set(browser_apps),
        browser_title_names=set(browser_title_names),
    )


def _window(app_id, title):
    return Window(id="1", app_id=app_id, title=title, focused=False, rect=(0.0, 0.0, 1.0, 1.0))


# ---------- _window_label ----------

def test_terminal_window_shows_bracketed_app_id_and_what_is_actually_running():
    # The exact bug reported live: two kitty windows (one running
    # htop, one running cava) both showed as plain "kitty" here even
    # though sidebar.py's own detail line already condensed them
    # correctly. Asked for live: "[app_id] detail", not "app_id -
    # detail" or the detail alone.
    cfg = _cfg(terminal_apps=["kitty"])
    assert _window_label(_window("kitty", "htop"), cfg) == "[kitty] htop"
    assert _window_label(_window("kitty", "cava"), cfg) == "[kitty] cava"


def test_two_terminal_windows_with_different_titles_get_different_labels():
    cfg = _cfg(terminal_apps=["kitty"])
    a = _window_label(_window("kitty", "htop"), cfg)
    b = _window_label(_window("kitty", "nvim main.py"), cfg)
    assert a != b


def test_generic_app_shows_bracketed_app_id_and_condensed_detail():
    cfg = _cfg()
    window = _window("code", "main.py - tuicc - Visual Studio Code")
    assert _window_label(window, cfg) == "[code] main.py"


def test_falls_back_to_plain_unbracketed_app_id_when_condensed_title_is_empty():
    # Generic (non-terminal, non-browser) app whose title is just its
    # own name again — condense_title() returns "", _window_label()
    # must still show SOMETHING real rather than a dangling "[app] ".
    cfg = _cfg()
    assert _window_label(_window("firefox", "firefox"), cfg) == "firefox"


def test_falls_back_to_plain_unbracketed_app_id_when_title_is_empty():
    cfg = _cfg()
    assert _window_label(_window("mpv", ""), cfg) == "mpv"


def test_falls_back_to_plain_unbracketed_app_id_when_detail_equals_app_id():
    # A fresh terminal whose title still just IS its own app_id (no
    # command run yet) — showing "[kitty] kitty" would be redundant,
    # same reasoning condense_title()'s own generic bucket already
    # applies, extended here to the terminal bucket too (which
    # condense_title() itself doesn't special-case this way).
    cfg = _cfg(terminal_apps=["kitty"])
    assert _window_label(_window("kitty", "kitty"), cfg) == "kitty"
    assert _window_label(_window("kitty", "Kitty"), cfg) == "kitty"


def test_browser_window_shows_bracketed_app_id_and_condensed_site_name():
    cfg = _cfg(browser_apps=["firefox"], browser_title_names=["mozilla firefox"])
    window = _window("firefox", "Issue #42 - tuicc - GitHub - Mozilla Firefox")
    assert _window_label(window, cfg) == "[firefox] GitHub"


# ---------- _corner_label ----------

def test_corner_label_uses_first_letter_uppercased():
    assert _corner_label(_window("kitty", "htop")) == "[K]"
    assert _corner_label(_window("firefox", "GitHub")) == "[F]"
    assert _corner_label(_window("code", "main.py")) == "[C]"


def test_corner_label_uppercases_an_already_lowercase_app_id():
    assert _corner_label(_window("mpv", "")) == "[M]"


def test_corner_label_ignores_title_entirely():
    # Unlike _window_label(), the corner label is app-identity only —
    # two windows of the same app always get the same corner letter,
    # regardless of what's running inside.
    assert _corner_label(_window("kitty", "htop")) == _corner_label(_window("kitty", "cava"))


def test_corner_label_empty_app_id_falls_back_to_question_mark():
    assert _corner_label(_window("", "something")) == "[?]"


# ---------- _corner_positions ----------

def test_corner_positions_returns_four_positions():
    positions = _corner_positions(win_y=10, win_x=20, win_h=10, win_w=20, label_len=5)
    assert len(positions) == 4


def test_corner_positions_top_left_inset_one_cell_from_the_border():
    positions = _corner_positions(win_y=10, win_x=20, win_h=10, win_w=20, label_len=5)
    assert positions[0] == (11, 21)


def test_corner_positions_right_side_is_label_end_flush_against_the_inset():
    # win_x=20, win_w=20 -> right border column is win_x+win_w-1=39,
    # inner-right column is 38 -> a 5-char label starting at col 34
    # ends exactly there (34+5-1=38).
    positions = _corner_positions(win_y=10, win_x=20, win_h=10, win_w=20, label_len=5)
    top_right = positions[1]
    assert top_right == (11, 34)


def test_corner_positions_bottom_row_is_inset_one_cell_from_the_bottom_border():
    # win_y=10, win_h=10 -> bottom border row is win_y+win_h-1=19,
    # inner-bottom row is 18.
    positions = _corner_positions(win_y=10, win_x=20, win_h=10, win_w=20, label_len=5)
    bottom_left = positions[2]
    assert bottom_left == (18, 21)


def test_corner_positions_all_four_distinct_for_a_reasonably_sized_box():
    positions = _corner_positions(win_y=0, win_x=0, win_h=10, win_w=20, label_len=5)
    assert len(set(positions)) == 4


def test_corner_positions_tiny_box_top_and_bottom_coincide_without_raising():
    # win_h=3 -> exactly one interior row (border, content, border) —
    # top and bottom inset to that same single row. Degenerate, but
    # must still return four (possibly-duplicate) positions rather
    # than raising; the caller's own curses.error guard handles
    # drawing at (or past) the box's own edge for anything smaller.
    positions = _corner_positions(win_y=0, win_x=0, win_h=3, win_w=20, label_len=5)
    assert positions[0][0] == positions[2][0]
