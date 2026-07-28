"""Theme: resolves config color values into curses color numbers.

    [theme] section in config.toml (per-role values)  ──┐
                                                        ├─> Config.theme
    NAMED_COLORS / hex / RGB, all via resolve_color() ──┘   (role -> curses color number)

Accepts three formats per role: a named color ("cyan"), a hex string
("#7dd3fc", approximated to the nearest of curses's 256-color
palette), an [R, G, B] list (same approximation), or the literal
string "inherit" (curses's default terminal color, -1).

TO BE DECIDED!!!
This file only resolves color values into numbers curses understands
(0-255, or -1 for inherit). It does not call curses.init_pair() or
curses.start_color() — that wiring doesn't exist yet as of writing
this, and needs a decision on where it lives (main.py at startup,
or a dedicated setup file) before modules can actually draw in color.
"""


NAMED_COLORS = {
    "black": 0,
    "red": 1,
    "green": 2,
    "yellow": 3,
    "blue": 4,
    "magenta": 5,
    "cyan": 6,
    "white": 7,
}

CUBE_LEVELS = [0, 95, 135, 175, 215, 255]


def resolve_color(value):
    if value == "inherit":
        return -1

    if isinstance(value, str) and value.startswith("#"):
        return hex_to_curses_color(value)

    if isinstance(value, list):
        return rgb_to_curses_color(tuple(value))

    if isinstance(value, str) and value in NAMED_COLORS:
        return NAMED_COLORS[value]

    raise ValueError(
        f"Invalid color value: {value!r}. Expected 'inherit', a named "
        f"color ({', '.join(NAMED_COLORS.keys())}), a hex string like "
        f"'#7dd3fc', or an [R, G, B] list."
    )


def hex_to_curses_color(hex_string: str) -> int:
    hex_string = hex_string.lstrip("#")
    r = int(hex_string[0:2], 16)
    g = int(hex_string[2:4], 16)
    b = int(hex_string[4:6], 16)
    return rgb_to_curses_color((r, g, b))


def nearest_level_index(value: int) -> int:
    best_index = 0
    best_distance = abs(value - CUBE_LEVELS[0])

    for i, level in enumerate(CUBE_LEVELS):
        distance = abs(value - level)
        if distance < best_distance:
            best_distance = distance
            best_index = i

    return best_index


def rgb_to_curses_color(rgb: tuple[int, int, int]) -> int:
    r, g, b = rgb

    r_index = nearest_level_index(r)
    g_index = nearest_level_index(g)
    b_index = nearest_level_index(b)

    return 16 + (r_index * 36) + (g_index * 6) + b_index
