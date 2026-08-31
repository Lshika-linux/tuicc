"""Tests for modules/preview.py's _window_label()/_corner_label()/
_corner_positions() — the pure "what to draw, and where" logic, kept
separate from _draw_window()'s own curses calls so all three are
testable without a fake stdscr, same split every other module in this
codebase uses for its own draw-adjacent logic.

No test file existed for preview.py before this — draw()/_draw_window()
themselves stay untested (curses-only, same as every other module's
draw()), but all three pure functions here are genuinely testable.
"""

import random
from types import SimpleNamespace

from tuicc.model import Window
from tuicc.modules.preview import (
    _corner_label, _corner_positions, _window_label, _window_screen_rect,
    _allocate_axis, _layout_tiled_windows, _partition_windows, _detail_tier,
    _group_tiled_windows, _place_tab_group, _TabGroupUnit,
)


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
    # Regression: two kitty windows (one running htop, one running
    # cava) both showed as plain "kitty" here even though sidebar.py's
    # own detail line already condensed them correctly. Expected shape:
    # "[app_id] detail", not "app_id - detail" or the detail alone.
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


# ---------- _window_screen_rect ----------

def _rect_window(rect, id="1"):
    return Window(id=id, app_id="kitty", title="", focused=False, rect=rect)


def test_window_screen_rect_basic_conversion_with_one_cell_shave():
    # A window filling the whole region: rw/rh=1.0 -> the box's own
    # full inner width/height (w-2)/(h-2), minus the 1-cell shave.
    win_x, win_y, win_w, win_h = _window_screen_rect(_rect_window((0.0, 0.0, 1.0, 1.0)), x=0, y=0, w=42, h=22)
    assert (win_x, win_y) == (1, 1)
    assert (win_w, win_h) == (39, 19)  # (42-2)-1, (22-2)-1


def test_window_screen_rect_floors_at_one_cell_rather_than_zero():
    # A vanishingly small rw/rh must never round the box away to
    # nothing — the shave floors at 1, not 0 (a 0-width/height box
    # would be invisible, not just tightly separated from a neighbor).
    win_x, win_y, win_w, win_h = _window_screen_rect(_rect_window((0.0, 0.0, 0.001, 0.001)), x=0, y=0, w=42, h=22)
    assert win_w == 1
    assert win_h == 1


def _region_rect_to_relative(region_rect):
    region_x, region_y, region_w, region_h = region_rect

    def rel(rect):
        wx, wy, ww, wh = rect
        return ((wx - region_x) / region_w, (wy - region_y) / region_h, ww / region_w, wh / region_h)

    return rel


def test_window_screen_rect_regression_two_adjacent_windows_no_longer_touch():
    # Live-found regression: a real sway `gaps.inner = 2` split two
    # tiled windows by only 2 real pixels within a 1908px-wide region.
    # Real numbers captured live via `swaymsg -t get_tree`: region rect
    # (6, 6, 1908, 1041); window A (961, 528, 476, 519); window B
    # (1439, 528, 475, 519) — B starts exactly 2px right of where A
    # ends. Box size (141, 36) is this same session's own REAL preview
    # box (a 192x51 terminal, preset 1's own x/y/w/h ratios) — a first
    # fix attempt (shave 1 off round(rw*(w-2))) passed at an arbitrary
    # box size but still produced a 0-column gap at THIS real size;
    # this test pins the exact real-world size that caught it.
    rel = _region_rect_to_relative((6, 6, 1908, 1041))
    window_a = _rect_window(rel((961, 528, 476, 519)))
    window_b = _rect_window(rel((1439, 528, 475, 519)))

    box = (0, 0, 141, 36)
    ax, ay, aw, ah = _window_screen_rect(window_a, *box)
    bx, by, bw, bh = _window_screen_rect(window_b, *box)

    assert bx > ax + aw  # a real gap column survives, not just adjacency


def test_window_screen_rect_regression_no_zero_gap_across_a_wide_range_of_box_sizes():
    # The real bottom pair from the same live layout, swept across a
    # broad range of plausible preview box sizes — guards against the
    # exact class of bug the (141, 36)-only test above could still
    # miss on its own: an unlucky rounding collision at some OTHER box
    # size the fix doesn't actually handle. This function's own fix
    # (derive win_w from where rx+rw itself rounds to) is used for
    # FLOATING windows only these days — tiled windows go through
    # _layout_tiled_windows() instead, see its own tests below.
    rel = _region_rect_to_relative((6, 6, 1908, 1041))
    window_a = _rect_window(rel((961, 528, 476, 519)))
    window_b = _rect_window(rel((1439, 528, 475, 519)))

    for box_w in range(20, 220, 5):
        for box_h in range(15, 60, 5):
            ax, ay, aw, ah = _window_screen_rect(window_a, 0, 0, box_w, box_h)
            bx, by, bw, bh = _window_screen_rect(window_b, 0, 0, box_w, box_h)
            assert bx > ax + aw


# ---------- _allocate_axis ----------

def test_allocate_axis_equal_sizes_split_evenly():
    assert _allocate_axis([1.0, 1.0], total_cells=21) == [10, 10]  # 21 - 1 gap = 20, split evenly


def test_allocate_axis_proportional_to_real_sizes():
    # 3x1 ratio over 41 available-after-gap cells: 3/4*41=30.75,
    # 1/4*41=10.25 -> floors [30, 10] summing to 40, one cell of
    # remainder left over goes to the larger fractional part (index 0,
    # 0.75 > 0.25) -> [31, 10]. Proportional, not an even 20/20 split.
    assert _allocate_axis([3.0, 1.0], total_cells=42) == [31, 10]


def test_allocate_axis_sum_plus_gaps_always_equals_total_cells():
    # The one property that actually matters for "never drifts" — for
    # a wide range of sizes/total_cells, sum(allocation) + (n-1) gap
    # cells must equal total_cells exactly, never over or under.
    rng = random.Random(0)
    for _ in range(200):
        n = rng.randint(2, 6)
        sizes = [rng.uniform(0.01, 10.0) for _ in range(n)]
        total_cells = rng.randint(n, 300)
        allocation = _allocate_axis(sizes, total_cells)
        assert len(allocation) == n
        if total_cells - (n - 1) >= n:  # only when there's room for gaps at all
            assert sum(allocation) == total_cells - (n - 1)


def test_allocate_axis_degrades_to_one_cell_each_when_no_room_for_real_gaps():
    # 3 siblings, only 3 cells total -> not even room for 2 gap cells
    # plus 1 real cell each. Must still return SOMETHING usable (1
    # each) rather than a negative/zero allocation.
    assert _allocate_axis([1.0, 1.0, 1.0], total_cells=3) == [1, 1, 1]


def test_allocate_axis_gap_zero_reserves_no_cells_at_all():
    # _layout_tiled_windows() calls this with gap=0 for row-splits — a
    # terminal cell is taller than wide, so a real row-gap already
    # reads as visibly thicker on screen than a column-gap of the same
    # "1 cell" size; the two windows' own border LINES already read as
    # separate without a blank row between them. gap=0 means the full
    # total_cells is available for sizes, none reserved as spacing.
    assert _allocate_axis([1.0, 1.0], total_cells=20, gap=0) == [10, 10]


# ---------- _partition_windows ----------

def test_partition_windows_single_window_is_a_leaf():
    window = _rect_window((0.0, 0.0, 1.0, 1.0))
    assert _partition_windows([window], 0.0, 1.0, 0.0, 1.0) == ("leaf", window)


def test_partition_windows_two_side_by_side_windows_split_on_x():
    left = _rect_window((0.0, 0.0, 0.5, 1.0))
    right = _rect_window((0.5, 0.0, 0.5, 1.0))
    tree = _partition_windows([left, right], 0.0, 1.0, 0.0, 1.0)
    assert tree == ("split", "x", [("leaf", left), ("leaf", right)])


def test_partition_windows_two_stacked_windows_split_on_y():
    top = _rect_window((0.0, 0.0, 1.0, 0.5))
    bottom = _rect_window((0.0, 0.5, 1.0, 0.5))
    tree = _partition_windows([top, bottom], 0.0, 1.0, 0.0, 1.0)
    assert tree == ("split", "y", [("leaf", top), ("leaf", bottom)])


def test_partition_windows_reconstructs_the_real_nested_layout():
    # The live-captured 4-window layout: an outer x-split (left | rest),
    # the right side is a y-split (topright / bottom-pair), the bottom
    # pair is itself an x-split — sway/i3's own actual split tree,
    # reconstructed purely from the flat rects (see model.py — Window
    # carries no parent/sibling info at all, this IS all there is).
    rel = _region_rect_to_relative((6, 6, 1908, 1041))
    left = _rect_window(rel((6, 6, 953, 1041)))
    topright = _rect_window(rel((961, 6, 953, 520)))
    botleft = _rect_window(rel((961, 528, 476, 519)))
    botright = _rect_window(rel((1439, 528, 475, 519)))

    tree = _partition_windows([left, topright, botleft, botright], 0.0, 1.0, 0.0, 1.0)

    assert tree == (
        "split", "x", [
            ("leaf", left),
            ("split", "y", [
                ("leaf", topright),
                ("split", "x", [("leaf", botleft), ("leaf", botright)]),
            ]),
        ],
    )


# ---------- _layout_tiled_windows ----------

def test_layout_tiled_windows_empty_list_returns_empty_dict():
    assert _layout_tiled_windows([], 0, 0, 42, 22) == ({}, [], [])


def test_layout_tiled_windows_regression_never_drops_a_window_even_at_deep_nesting():
    # Live-found real crash (this session's own machine, autotiling
    # spiral, 16+ real windows): _partition_windows()'s eps-based
    # boundary matching dropped a window from the tree at deep enough
    # nesting, and nav_items()'s tiled_rects[window.id] lookup raised
    # KeyError, bringing down tuicc's whole main loop. Root cause in
    # the eps tolerance itself wasn't fully nailed down — this test
    # instead pins the OUTCOME that actually matters: no matter how
    # deep the spiral goes, every single window MUST get a real entry
    # in the result, never just fail. Synthetic spiral (each new
    # window splits the previous deepest one in half, alternating
    # axis — the same recursive pattern autotiling produces) up to 59
    # windows, well past the depth (~28) that reproduced the original
    # crash with exact halving.
    windows = [SimpleNamespace(id="0", rect=(0.0, 0.0, 1.0, 1.0))]
    axis = 0
    for i in range(1, 60):
        rx, ry, rw, rh = windows[-1].rect
        if axis == 0:
            half = rw / 2
            windows[-1] = SimpleNamespace(id=windows[-1].id, rect=(rx, ry, half, rh))
            windows.append(SimpleNamespace(id=str(i), rect=(rx + half, ry, rw - half, rh)))
        else:
            half = rh / 2
            windows[-1] = SimpleNamespace(id=windows[-1].id, rect=(rx, ry, rw, half))
            windows.append(SimpleNamespace(id=str(i), rect=(rx, ry + half, rw, rh - half)))
        axis = 1 - axis

        rects, groups, _bars = _layout_tiled_windows(windows, 0, 0, 141, 36)
        grouped_ids = {w.id for _gx, _gy, _gw, _gh, members in groups for w in members}
        missing = [w.id for w in windows if w.id not in rects and w.id not in grouped_ids]
        assert not missing, f"{len(windows)} windows: missing {missing}"


def test_layout_tiled_windows_regression_gap_is_exactly_one_not_just_at_least_one():
    # Live-found: a first fix (_window_screen_rect's own 1-cell shave,
    # applied independently per window) guaranteed "at least 1" but
    # not "exactly 1" — at the real preview box size, the main x-split
    # came out gap=2 while the bottom pair came out gap=1, visibly
    # DIFFERENT gap widths for splits that share the exact same real
    # (2px) sway gap. _layout_tiled_windows() lays out the whole group
    # together specifically to fix that: both must be exactly 1, at
    # the real box size AND (see the sweep test below) everywhere else.
    rel = _region_rect_to_relative((6, 6, 1908, 1041))
    left = _rect_window(rel((6, 6, 953, 1041)), id="left")
    topright = _rect_window(rel((961, 6, 953, 520)), id="topright")
    botleft = _rect_window(rel((961, 528, 476, 519)), id="botleft")
    botright = _rect_window(rel((1439, 528, 475, 519)), id="botright")
    windows = [left, topright, botleft, botright]

    rects, groups, _bars = _layout_tiled_windows(windows, 0, 0, 141, 36)
    assert groups == []  # all 4 windows have real room — nothing collapsed

    gap_main = rects[topright.id][0] - (rects[left.id][0] + rects[left.id][2])
    gap_bot = rects[botright.id][0] - (rects[botleft.id][0] + rects[botleft.id][2])
    assert gap_main == 1
    assert gap_bot == 1


def test_layout_tiled_windows_row_splits_get_zero_gap_column_splits_get_one():
    # Same real layout — topright sits directly above the bottom pair
    # (a y-split, row-adjacency): must show ZERO gap rows, its own
    # bottom border immediately followed by the bottom pair's own top
    # border on the very next row. left/topright (an x-split, column-
    # adjacency) keeps its real 1-column gap, unchanged.
    rel = _region_rect_to_relative((6, 6, 1908, 1041))
    left = _rect_window(rel((6, 6, 953, 1041)), id="left")
    topright = _rect_window(rel((961, 6, 953, 520)), id="topright")
    botleft = _rect_window(rel((961, 528, 476, 519)), id="botleft")
    botright = _rect_window(rel((1439, 528, 475, 519)), id="botright")
    windows = [left, topright, botleft, botright]

    rects, groups, _bars = _layout_tiled_windows(windows, 0, 0, 141, 36)
    assert groups == []

    gap_col = rects["topright"][0] - (rects["left"][0] + rects["left"][2])
    gap_row = rects["botleft"][1] - (rects["topright"][1] + rects["topright"][3])
    assert gap_col == 1
    assert gap_row == 0


def test_layout_tiled_windows_regression_gap_is_exactly_one_across_a_wide_range_of_box_sizes():
    # Same real layout, swept across a broad range of plausible preview
    # box sizes (20-220 cols, 15-60 rows) — this exact sweep caught the
    # "at least 1, but inconsistent" bug the single-size test above
    # could have missed by luck. 0 failures required, not "mostly".
    rel = _region_rect_to_relative((6, 6, 1908, 1041))
    left = _rect_window(rel((6, 6, 953, 1041)), id="left")
    topright = _rect_window(rel((961, 6, 953, 520)), id="topright")
    botleft = _rect_window(rel((961, 528, 476, 519)), id="botleft")
    botright = _rect_window(rel((1439, 528, 475, 519)), id="botright")
    windows = [left, topright, botleft, botright]

    for box_w in range(20, 220, 4):
        for box_h in range(15, 60, 4):
            rects, groups, _bars = _layout_tiled_windows(windows, 0, 0, box_w, box_h)
            grouped_ids = {w.id for _gx, _gy, _gw, _gh, members in groups for w in members}
            # At small box sizes some of these windows legitimately
            # collapse into a group instead (see _detail_tier()) — the
            # "gap == 1" invariant only applies to windows that got
            # their own individual rect, skip the rest for this size.
            if {"left", "topright"} & grouped_ids or {"botleft", "botright"} & grouped_ids:
                continue
            gap_main = rects["topright"][0] - (rects["left"][0] + rects["left"][2])
            gap_bot = rects["botright"][0] - (rects["botleft"][0] + rects["botleft"][2])
            assert gap_main == 1
            assert gap_bot == 1


def test_layout_tiled_windows_collapsed_group_matches_its_sibling_scale():
    # A first version of the "too small" handling bounded just the
    # collapsed leaves' own (already tiny, post-split) rects — found
    # live, that placeholder ended up much SMALLER than the real space
    # that whole branch actually occupies in the tiling, visibly not
    # matching the scale of whatever real sibling sits next to it (a
    # user-reported regression, not a guess).
    #
    # This test builds the exact real-world shape that surfaced it:
    # left | (topright / bottomright-spiral) — the same layout the
    # gap-consistency tests above use, except the bottom-right quarter
    # is a deep spiral instead of one window. At a box small enough
    # that even a plain quarter-cell is itself below the "letter"
    # threshold, BOTH topright (a single window) and the spiral's own
    # root collapse into their own one-window/one-group placeholders —
    # true siblings, same split level, same allocated cell size. If
    # the spiral's group only bounded its own tiny deepest leaves
    # (the ORIGINAL bug), it would come out far smaller than topright's
    # own group despite being drawn in the exact same spot a same-sized
    # single window would have occupied.
    left = SimpleNamespace(id="left", rect=(0.0, 0.0, 0.5, 1.0))
    topright = SimpleNamespace(id="topright", rect=(0.5, 0.0, 0.5, 0.5))
    spiral = [SimpleNamespace(id="s0", rect=(0.5, 0.5, 0.5, 0.5))]
    axis = 0
    for i in range(1, 20):
        rx, ry, rw, rh = spiral[-1].rect
        if axis == 0:
            half = rw / 2
            spiral[-1] = SimpleNamespace(id=spiral[-1].id, rect=(rx, ry, half, rh))
            spiral.append(SimpleNamespace(id=f"s{i}", rect=(rx + half, ry, rw - half, rh)))
        else:
            half = rh / 2
            spiral[-1] = SimpleNamespace(id=spiral[-1].id, rect=(rx, ry, rw, half))
            spiral.append(SimpleNamespace(id=f"s{i}", rect=(rx, ry + half, rw, rh - half)))
        axis = 1 - axis

    rects, groups, _bars = _layout_tiled_windows([left, topright] + spiral, 0, 0, 13, 6)

    assert "left" in rects  # roomy enough to stay a real window
    assert "topright" not in rects  # too small on its own -> its own group
    assert len(groups) == 2  # topright's own group, and the spiral's

    topright_group = next(g for g in groups if g[4] == [topright])
    spiral_group = next(g for g in groups if g[4] != [topright])

    assert spiral_group[4] == spiral  # every spiral window is in the ONE group
    # Same width/height as topright (true siblings, same split level,
    # same allocated cell) — positions differ (topright is the TOP
    # half, the spiral is the BOTTOM half of the same column).
    assert (spiral_group[2], spiral_group[3]) == (topright_group[2], topright_group[3])


# ---------- _group_tiled_windows (GitHub issue #8) ----------

def _tab_window(id_, rect, group_id=None, group_layout=None, active=False):
    return Window(
        id=id_, app_id="app" + id_, title="", focused=False, rect=rect,
        tab_group_id=group_id, tab_group_layout=group_layout, tab_active=active,
    )


def test_group_tiled_windows_collapses_a_real_group_into_one_unit():
    a = _tab_window("a", (0.0, 0.0, 1.0, 1.0), group_id="9", group_layout="stacked", active=True)
    b = _tab_window("b", (0.0, 0.0, 1.0, 1.0), group_id="9", group_layout="stacked", active=False)

    units = _group_tiled_windows([a, b])

    assert len(units) == 1
    unit = units[0]
    assert isinstance(unit, _TabGroupUnit)
    assert unit.group_id == "9"
    assert unit.layout == "stacked"
    assert unit.members == [a, b]
    assert unit.rect == (0.0, 0.0, 1.0, 1.0)


def test_group_tiled_windows_passes_through_ungrouped_windows_unchanged():
    plain = _tab_window("p", (0.0, 0.0, 0.5, 1.0))
    units = _group_tiled_windows([plain])
    assert units == [plain]


def test_group_tiled_windows_lone_group_member_passed_through_as_plain_window():
    # A group_id with only ONE real member on screen right now (a real
    # case — see tab_groups.tab_info_by_leaf_id()'s own "nested groups"
    # docstring) gets no special treatment: nothing to distinguish it
    # from, so wrapping it would just be a full-detail box with extra
    # steps.
    lone = _tab_window("lone", (0.0, 0.0, 1.0, 1.0), group_id="9", group_layout="stacked", active=True)
    units = _group_tiled_windows([lone])
    assert units == [lone]


def test_group_tiled_windows_two_distinct_groups_stay_separate():
    a1 = _tab_window("a1", (0.0, 0.0, 0.5, 1.0), group_id="1", group_layout="stacked", active=True)
    a2 = _tab_window("a2", (0.0, 0.0, 0.5, 1.0), group_id="1", group_layout="stacked", active=False)
    b1 = _tab_window("b1", (0.5, 0.0, 0.5, 1.0), group_id="2", group_layout="tabbed", active=True)
    b2 = _tab_window("b2", (0.5, 0.0, 0.5, 1.0), group_id="2", group_layout="tabbed", active=False)

    units = _group_tiled_windows([a1, a2, b1, b2])

    assert len(units) == 2
    assert {u.group_id for u in units} == {"1", "2"}


def test_group_tiled_windows_missing_tab_group_id_field_treated_as_ungrouped():
    # Several existing pure-layout tests in this file exercise
    # _layout_tiled_windows() (which calls this internally) against
    # minimal SimpleNamespace(id, rect) fixtures that predate
    # tab_group_id — those must keep working unchanged.
    plain = SimpleNamespace(id="x", rect=(0.0, 0.0, 1.0, 1.0))
    units = _group_tiled_windows([plain])
    assert units == [plain]


# ---------- _place_tab_group (GitHub issue #8) ----------

def _make_unit(layout, members):
    unit = _TabGroupUnit("g", layout)
    unit.members = list(members)
    unit.rect = members[0].rect
    return unit


def test_place_tab_group_stacked_gives_each_inactive_member_a_row_and_active_a_content_box():
    active = _tab_window("active", (0, 0, 1, 1), "g", "stacked", active=True)
    b = _tab_window("b", (0, 0, 1, 1), "g", "stacked", active=False)
    c = _tab_window("c", (0, 0, 1, 1), "g", "stacked", active=False)
    unit = _make_unit("stacked", [active, b, c])

    result, bars = {}, []
    _place_tab_group(unit, win_x=10, win_y=20, win_w=30, win_h=10, result=result, tab_group_bars=bars)

    assert len(bars) == 2  # b and c, not active
    assert {m.id for m, _rect, _is_active in bars} == {"b", "c"}
    # Rows are contiguous, one cell tall, stacked top to bottom.
    rows = sorted(rect[1] for _m, rect, _a in bars)
    assert rows == [20, 21]
    for _m, (bx, by, bw, bh), is_active in bars:
        assert (bx, bw, bh) == (10, 30, 1)
        assert is_active is False

    # Active member gets a real content box below both bars.
    assert result["active"] == (10, 22, 30, 8)


def test_place_tab_group_tabbed_splits_width_among_inactive_members_with_a_gap():
    active = _tab_window("active", (0, 0, 1, 1), "g", "tabbed", active=True)
    b = _tab_window("b", (0, 0, 1, 1), "g", "tabbed", active=False)
    c = _tab_window("c", (0, 0, 1, 1), "g", "tabbed", active=False)
    unit = _make_unit("tabbed", [active, b, c])

    result, bars = {}, []
    _place_tab_group(unit, win_x=0, win_y=5, win_w=21, win_h=10, result=result, tab_group_bars=bars)

    assert len(bars) == 2
    for _m, (bx, by, bw, bh), is_active in bars:
        assert by == 5  # the whole strip is one row, at the cell's own top
        assert bh == 1
        assert is_active is False
    # Two segments, each with real width, separated by a real gap
    # column — not glued together (same "exactly one gap" discipline
    # _allocate_axis() already enforces for ordinary x-splits).
    xs = sorted(rect[0] for _m, rect, _a in bars)
    ws = [rect[2] for _m, rect, _a in bars]
    assert xs[1] > xs[0] + ws[0]

    # Active member's content box fills whatever's left below the strip.
    assert result["active"] == (0, 6, 21, 9)


def test_place_tab_group_degenerate_no_room_for_content_still_places_active():
    # Exactly as many rows as inactive members — no room left for the
    # active member's own content box. It must still get a real,
    # distinct nav target rather than silently vanishing.
    active = _tab_window("active", (0, 0, 1, 1), "g", "stacked", active=True)
    b = _tab_window("b", (0, 0, 1, 1), "g", "stacked", active=False)
    unit = _make_unit("stacked", [active, b])

    result, bars = {}, []
    _place_tab_group(unit, win_x=0, win_y=0, win_w=10, win_h=1, result=result, tab_group_bars=bars)

    assert "active" not in result
    ids_with_bars = {m.id for m, _rect, _is_active in bars}
    assert ids_with_bars == {"active", "b"}
    active_bar = next((m, rect, is_active) for m, rect, is_active in bars if m.id == "active")
    assert active_bar[2] is True  # flagged so draw() can color it distinctly


def test_place_tab_group_lone_inactive_member_missing_gets_whole_cell_as_content():
    # Sanity check for the ordinary, common case: two-member group,
    # plenty of room — the active member's content box should be the
    # full remaining cell, not some odd sliver.
    active = _tab_window("active", (0, 0, 1, 1), "g", "tabbed", active=True)
    b = _tab_window("b", (0, 0, 1, 1), "g", "tabbed", active=False)
    unit = _make_unit("tabbed", [active, b])

    result, bars = {}, []
    _place_tab_group(unit, win_x=0, win_y=0, win_w=20, win_h=8, result=result, tab_group_bars=bars)

    assert len(bars) == 1
    assert result["active"] == (0, 1, 20, 7)


# ---------- _layout_tiled_windows integration (GitHub issue #8) ----------

def test_layout_tiled_windows_stacked_group_never_overlaps_with_its_sibling():
    # The literal original bug: a stacked/tabbed group's members all
    # report the SAME rect, which used to make _partition_windows()
    # place them all on top of EACH OTHER (no cut could ever separate
    # identical rects). Confirms the fix at the level draw() actually
    # calls: a real sibling window elsewhere in the tree still gets its
    # own, non-overlapping space, and the group as a whole occupies
    # only ITS OWN half.
    left = _tab_window("left", (0.0, 0.0, 0.5, 1.0))
    stacked_a = _tab_window("sa", (0.5, 0.0, 0.5, 1.0), "g", "stacked", active=True)
    stacked_b = _tab_window("sb", (0.5, 0.0, 0.5, 1.0), "g", "stacked", active=False)

    rects, groups, bars = _layout_tiled_windows([left, stacked_a, stacked_b], 0, 0, 42, 22)

    assert "left" in rects
    assert "sa" in rects  # the group's active member gets a real content box
    left_x, _ly, left_w, _lh = rects["left"]
    active_x, _ay, active_w, _ah = rects["sa"]
    assert active_x >= left_x + left_w  # no horizontal overlap with its sibling

    bar_ids = {m.id for m, _rect, _is_active in bars}
    assert bar_ids == {"sb"}  # the inactive member got its own bar, not a full box


def test_layout_tiled_windows_tabbed_group_bars_stay_within_the_groups_own_cell():
    stacked_a = _tab_window("ta", (0.0, 0.0, 1.0, 1.0), "g", "tabbed", active=True)
    stacked_b = _tab_window("tb", (0.0, 0.0, 1.0, 1.0), "g", "tabbed", active=False)
    stacked_c = _tab_window("tc", (0.0, 0.0, 1.0, 1.0), "g", "tabbed", active=False)

    rects, groups, bars = _layout_tiled_windows([stacked_a, stacked_b, stacked_c], 0, 0, 30, 12)

    assert groups == []  # roomy enough — no too-small collapse
    assert "ta" in rects
    assert {m.id for m, _rect, _is_active in bars} == {"tb", "tc"}
    for _m, (bx, by, bw, bh), _is_active in bars:
        assert 1 <= bx  # inside the box's own left border
        assert bx + bw <= 29  # inside the box's own right border


# ---------- _detail_tier ----------

def test_detail_tier_full_when_both_dimensions_have_real_room():
    assert _detail_tier(win_w=20, win_h=10) == "full"


def test_detail_tier_letter_when_too_small_for_full_but_not_for_a_single_tag():
    assert _detail_tier(win_w=6, win_h=3) == "letter"


def test_detail_tier_none_when_too_small_even_for_a_single_tag():
    assert _detail_tier(win_w=2, win_h=1) == "none"


def test_detail_tier_either_dimension_alone_below_threshold_downgrades():
    # A window can be wide but short (or tall but narrow) — either
    # dimension missing the "full" bar drops it to "letter", same
    # logic for "letter" -> "none".
    assert _detail_tier(win_w=20, win_h=3) == "letter"  # wide enough, not tall enough
    assert _detail_tier(win_w=6, win_h=10) == "letter"  # tall enough, not wide enough
    assert _detail_tier(win_w=20, win_h=2) == "none"    # wide, but too short even for a tag
    assert _detail_tier(win_w=2, win_h=10) == "none"    # tall, but too narrow even for a tag
