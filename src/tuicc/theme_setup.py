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

    Every role's own pair carries the SAME resolved "background" color
    on its background component (bg_color below) — not -1. Found live,
    the hard way, across two wrong attempts:

    1. First attempt put every role's color on fg with bg=-1 (the
       original, pre-existing code) — "background" itself was then a
       pair with its OWN color stranded on fg, never shown anywhere.
    2. Second attempt (this function's own earlier revision) special-
       cased ONLY "background" to put its color on bg, leaving every
       OTHER role's bg at -1 — apply_background()'s bkgd() call then
       correctly filled genuinely blank/untouched cells with the real
       color, but every piece of actual TEXT (drawn with an explicit
       role pair, not pair 0) still rendered its OWN background as
       SGR 49 — confirmed via a raw pty capture: `\\x1b[49m` right
       before the text's own color code, NOT the bkgd() fill's
       `\\x1b[48;5;NNm`. SGR 49 means "the terminal's own hardwired
       default", a DIFFERENT thing from "whatever this window's bkgd()
       currently paints" — an explicit pair's own -1 component does
       NOT inherit bkgd()'s color, only pair-0/truly-untouched cells
       do (see apply_background()'s own docstring, still accurate for
       THAT half of the picture). Live-reported: every string of text
       showed a visibly mismatched dark box sized exactly to itself,
       everywhere, the instant "background" resolved to a real color
       instead of "inherit" — this is what that dark box was.

    Giving every role's pair the same real bg_color closes this for
    good: text blends into the same fill bkgd() already paints for
    blank cells, with zero extra call sites to touch. When bg_color is
    -1 ("inherit", today's Default) this is byte-for-byte identical to
    the ORIGINAL pre-existing behavior — every pair still gets bg=-1 —
    so nothing regresses for anyone who hasn't touched a preset.
    """
    pairs = {}
    pair_number = 1
    bg_color = theme_config.get("background", -1)

    for role, color_value in theme_config.items():
        if role == "background":
            curses.init_pair(pair_number, -1, color_value)
        else:
            curses.init_pair(pair_number, color_value, bg_color)
        pairs[role] = curses.color_pair(pair_number)
        pair_number += 1

    return pairs


def apply_background(stdscr, theme_pairs: dict) -> None:
    """The other half of making "background" actually visible — the
    first half is reassign_theme_pairs() baking the same color into
    every OTHER role's own pair (see its own docstring for the full,
    two-wrong-attempts story). That covers anything drawn with an
    explicit theme-role pair. This call covers what that can't: cells
    drawn with pair 0 specifically — draw_filled_box's own color=0
    default, stdscr.erase() — which curses treats as "no explicit
    attribute at all" and fills with the window's own bkgd() character,
    a genuinely different mechanism than an explicit pair's own -1
    component (which renders as the terminal's raw default, confirmed
    via a raw pty capture — see reassign_theme_pairs()'s own docstring).
    Pair 0 is reserved by curses (init_pair() can't redefine it), so
    this is the only way to give THOSE cells a real color too.

    Missing "background" (a config predating this role) falls back to
    curses.color_pair(0) — a real terminal-default reset, not left
    holding a stale color from a previous call.
    """
    stdscr.bkgd(" ", theme_pairs.get("background", curses.color_pair(0)))


def assign_control_toggle_pairs(control_toggles: list, start_pair_number: int, bg_color: int = -1) -> dict:
    """A second, independent round of curses.init_pair() calls, keyed
    by (toggle_index, state_index) for entries with an explicit
    `color` (see config.py's _build_control_toggles). start_pair_number
    must be one past whatever theme roles already claimed
    (len(theme_pairs) + 1 in main.py) so the two ranges never collide.
    States without an explicit `color` are simply absent from the
    result — modules/control.py's draw() falls back to a theme default.

    bg_color is the same "background" role color reassign_theme_pairs()
    puts on every OTHER role's own pair — a control.toggle state color
    is drawn as real text (an explicit pair), so it needs the same fix
    for the same reason: -1 renders as the terminal's own raw default,
    not whatever the theme's background actually is, once that's a
    real color instead of "inherit". Defaults to -1 so a caller that
    doesn't know/care about the background role yet keeps today's
    behavior unchanged.
    """
    pairs = {}
    pair_number = start_pair_number
    for i, toggle in enumerate(control_toggles):
        for j, state in enumerate(toggle["states"]):
            if state["color"] is None:
                continue
            curses.init_pair(pair_number, state["color"], bg_color)
            pairs[(i, j)] = curses.color_pair(pair_number)
            pair_number += 1
    return pairs
