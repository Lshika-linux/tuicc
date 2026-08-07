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
    called again at runtime (e.g. the help menu's live color editor)
    without re-running curses.start_color()/use_default_colors(), which
    are meant to run once. Re-running curses.init_pair() for an
    already-allocated pair number redefines it in place — every cell
    already drawn with that pair updates on the next refresh, no
    restart needed. Pair numbers are assigned by iterating
    theme_config in order, so as long as the same dict (same role set,
    same order) is passed back in, roles keep the exact pair numbers
    they already had — this is what makes editing ONE role's value not
    require reassigning any of the others.
    """
    pairs = {}
    pair_number = 1

    for role, color_value in theme_config.items():
        curses.init_pair(pair_number, color_value, -1)
        pairs[role] = curses.color_pair(pair_number)
        pair_number += 1

    return pairs


def assign_control_toggle_pairs(control_toggles: list, start_pair_number: int) -> dict:
    """A second, independent round of curses.init_pair() calls — one
    per (toggle_index, state_index) that has an explicit `color` (see
    config.py's _build_control_toggles / VISION.md's R5 control
    module) — same "assign sequential pair numbers, hand back a lookup
    dict" idiom as reassign_theme_pairs() above, just keyed by index
    instead of by a small fixed theme-role name set, since toggle
    colors are arbitrary-length user config, not roles.

    start_pair_number must be one past whatever pair numbers theme
    roles already claimed (len(theme_pairs) + 1 at the call site in
    main.py) so the two ranges can never collide and silently redefine
    each other's colors. States without an explicit `color` are simply
    absent from the returned dict — modules/control.py's draw() falls
    back to a theme default (e.g. "accent"/"text") for those, the same
    "look it up, fall back if absent" pattern _connection_dot already
    uses for connected/disconnected.
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
