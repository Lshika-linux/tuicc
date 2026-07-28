"""One-time curses color pair setup, run once at startup.

Kept separate from theme.py (which stays pure and curses-free, testable
without a running screen) and render_utils.py (drawing helpers called
every frame, not once at startup).
"""

import curses


def setup_theme(theme_config: dict) -> dict:
    curses.start_color()
    curses.use_default_colors()

    pairs = {}
    pair_number = 1

    for role, color_value in theme_config.items():
        curses.init_pair(pair_number, color_value, -1)
        pairs[role] = curses.color_pair(pair_number)
        pair_number += 1

    return pairs
