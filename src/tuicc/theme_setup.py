"""One-time curses color pair setup, run once at startup.

Kept separate from theme.py (which stays pure and curses-free, testable
without a running screen) and render_utils.py (drawing helpers called
every frame, not once at startup).
"""

import curses


def setup_theme(theme_config: dict) -> dict:
    curses.start_color()
    curses.use_default_colors()
    return reassign_theme_pairs(theme_config)


def reassign_theme_pairs(theme_config: dict) -> dict:
    """The pair-assignment loop, split out of setup_theme() so it can be
    called again at runtime (e.g. the live color editor) without
    re-running curses.start_color()/use_default_colors(). Re-running
    curses.init_pair() for an already-allocated number redefines it in
    place, no restart needed. Pair numbers follow theme_config's own
    iteration order, so passing the same dict back in keeps every
    role's pair number stable — editing one role never reassigns
    another's.
    """
    pairs = {}
    pair_number = 1

    for role, color_value in theme_config.items():
        curses.init_pair(pair_number, color_value, -1)
        pairs[role] = curses.color_pair(pair_number)
        pair_number += 1

    return pairs


def assign_control_toggle_pairs(control_toggles: list, start_pair_number: int) -> dict:
    """A second, independent round of curses.init_pair() calls, keyed
    by (toggle_index, state_index) for entries with an explicit
    `color` (see config.py's _build_control_toggles). start_pair_number
    must be one past whatever theme roles already claimed
    (len(theme_pairs) + 1 in main.py) so the two ranges never collide.
    States without an explicit `color` are simply absent from the
    result — modules/control.py's draw() falls back to a theme default.
    """
    pairs = {}
    pair_number = start_pair_number
    for i, toggle in enumerate(control_toggles):
        for j, state in enumerate(toggle["states"]):
            if state["color"] is None:
                continue
            curses.init_pair(pair_number, state["color"], -1)
            pairs[(i, j)] = curses.color_pair(pair_number)
            pair_number += 1
    return pairs
