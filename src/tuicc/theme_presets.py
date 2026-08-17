"""Built-in named color schemes, plus the pure cycling logic shared by
the F1 Colors page's F4 (cycle)/F5 (save as new) keys — the same
"cycle_preset"/"new_preset" keybinds resize mode already uses for
layout presets, reused here for theme presets rather than adding a
second pair of config keys: two different contexts (Colors page vs.
resize mode browsing), never active at once, same mental model either
way ("F4 = next preset, F5 = save this as a new one").

Two different kinds of preset, one cycle:
- BUILTIN_THEME_PRESETS below — hardcoded here, not files, since
  there's no reason a user would ever want to edit "Dracula" itself in
  place; picking one is just a fast starting point for the existing
  per-role editor (Enter on a row) to take over from.
- User-saved presets — real files under config.py's
  USER_THEME_PRESETS_DIR (~/.config/tuicc/theme_presets/<N>.toml),
  written by config.save_new_theme_preset(), read back by
  config.load_theme_preset(). Deliberately NOT mirrored here as a
  second in-memory dict — config.py already owns every other on-disk
  preset concern (layout presets, config.toml patching), and this file
  stays disk-free/pure so it's trivially testable, same reasoning
  connectivity/registry.py and every backend package already follow.

No "active theme preset" is tracked anywhere (not in Config, not in
loop_state) — unlike layout's cfg.preset_number, which is a real,
persisted pointer. A single-role edit (the existing color editor)
would have to somehow diverge from "preset N" the moment you touched
one field, needing its own dirty-tracking; instead, next_preset() below
just compares the CURRENT 8 raw values against every entry in the
cycle list to find "where am I now" fresh each time F4 is pressed — no
state to keep in sync, no way for it to go stale.
"""

from tuicc.theme import THEME_ROLES

# Every value here is a raw [theme] string — exactly the format
# config.set_theme_colors() writes to config.toml and help_mode.py's
# color editor already accepts (named color, "#hex", or "inherit").
# Declaration order is cycle order — "Default" first so F4 from a
# freshly-installed config immediately shows movement into "Dracula"
# next, and cycling all the way around always lands back on it.
#
# Every "background" value below is each scheme's own real, official
# background hex, RGB channels uniformly scaled by 0.55 — darkened,
# NOT desaturated. Live-reported directly, in two rounds: once
# background actually painted the whole screen (see theme_setup.py's
# own docstring for that story), several of these read as too loud for
# something that's supposed to recede — a first attempt fixed that by
# cutting HSV saturation instead, which was explicitly rejected live as
# looking washed-out/gray, "jak že tuicc je schované za šedou záclonou"
# (like tuicc is hidden behind a grey curtain) — losing exactly the
# color identity each scheme exists to have. Scaling every RGB channel
# by the same factor is the fix that actually does what was asked:
# S = (max-min)/max is invariant under a uniform scale (both max and
# min shrink by the same factor, the ratio doesn't change) while
# V = max drops directly — hue and saturation stay exactly what each
# scheme's own real palette defines, only brightness goes down. LEGACY
# is the one deliberate exception — its own #000000 was Rafi's explicit
# ask, already as dark as it gets, nothing to scale.
BUILTIN_THEME_PRESETS = {
    "Default": {
        "background": "inherit",
        "border": "#808080",
        "border_selected": "white",
        "text": "white",
        "accent": "cyan",
        "selected": "blue",
        "warning": "yellow",
        "urgent": "red",
    },
    "Dracula": {
        "background": "#16171e",
        "border": "#6272a4",
        "border_selected": "#bd93f9",
        "text": "#f8f8f2",
        "accent": "#8be9fd",
        "selected": "#50fa7b",
        "warning": "#f1fa8c",
        "urgent": "#ff5555",
    },
    "Nord": {
        "background": "#191d23",
        "border": "#4c566a",
        "border_selected": "#88c0d0",
        "text": "#e5e9f0",
        "accent": "#81a1c1",
        "selected": "#a3be8c",
        "warning": "#ebcb8b",
        "urgent": "#bf616a",
    },
    "Solarized Dark": {
        "background": "#00181e",
        "border": "#586e75",
        "border_selected": "#268bd2",
        "text": "#93a1a1",
        "accent": "#2aa198",
        "selected": "#859900",
        "warning": "#b58900",
        "urgent": "#dc322f",
    },
    "Gruvbox": {
        "background": "#161616",
        "border": "#504945",
        "border_selected": "#fe8019",
        "text": "#ebdbb2",
        "accent": "#83a598",
        "selected": "#b8bb26",
        "warning": "#fabd2f",
        "urgent": "#fb4934",
    },
    "One Dark": {
        "background": "#16181d",
        "border": "#5c6370",
        "border_selected": "#61afef",
        "text": "#abb2bf",
        "accent": "#56b6c2",
        "selected": "#98c379",
        "warning": "#e5c07b",
        "urgent": "#e06c75",
    },
    "Rose Pine": {
        "background": "#0e0d14",
        "border": "#6e6a86",
        "border_selected": "#c4a7e7",
        "text": "#e0def4",
        "accent": "#9ccfd8",
        "selected": "#ebbcba",
        "warning": "#f6c177",
        "urgent": "#eb6f92",
    },
    "Catppuccin Mocha": {
        "background": "#101019",
        "border": "#585b70",
        "border_selected": "#cba6f7",
        "text": "#cdd6f4",
        "accent": "#89dceb",
        "selected": "#a6e3a1",
        "warning": "#f9e2af",
        "urgent": "#f38ba8",
    },
    "Tokyo Night": {
        "background": "#0e0f15",
        "border": "#414868",
        "border_selected": "#7aa2f7",
        "text": "#c0caf5",
        "accent": "#7dcfff",
        "selected": "#9ece6a",
        "warning": "#e0af68",
        "urgent": "#f7768e",
    },
    # Rafi's own request, verbatim: "ten můj theme ze switcheru.py jen
    # FF0000 FFFF00 FFFFFF 000000" — exactly those 4 colors, no others,
    # distributed for real contrast rather than one flat pair: black
    # background, white for anything read as body text/normal border,
    # yellow for "notice this" (accent/selected/warning), red for
    # "this is active/urgent" (border_selected/urgent) — a red glow on
    # the currently-active module, not just on errors.
    "LEGACY": {
        "background": "#000000",
        "border": "#ffffff",
        "border_selected": "#ff0000",
        "text": "#ffffff",
        "accent": "#ffff00",
        "selected": "#ffff00",
        "warning": "#ffff00",
        "urgent": "#ff0000",
    },
}


def preset_cycle_list(user_presets: dict) -> list:
    """Every built-in scheme (declared order above), then every saved
    user preset by ascending number, as (label, values) pairs — the
    full ordered sequence F4 cycles through. user_presets is
    {preset_number: raw_theme_dict}, from config.py's on-disk reads —
    passed in rather than read here so this stays disk-free and
    testable with a plain dict fixture.
    """
    items = [(name, values) for name, values in BUILTIN_THEME_PRESETS.items()]
    for number in sorted(user_presets):
        items.append((f"Preset {number}", user_presets[number]))
    return items


def next_preset(current_values: dict, cycle_list: list):
    """(label, values) to apply next, given the CURRENT raw [theme]
    values and the full cycle_list from preset_cycle_list(). Finds
    where "now" sits by comparing current_values against each entry
    over THEME_ROLES only (ignoring any extra/missing keys either side
    might have) — if nothing matches (a hand-tweaked color, or a role
    predating this feature), starts from the first entry rather than
    guessing which one is "closest". None if cycle_list is empty
    (shouldn't happen in practice — BUILTIN_THEME_PRESETS is never
    empty — but a caller shouldn't crash if it somehow were).
    """
    if not cycle_list:
        return None
    index = -1
    for i, (_name, values) in enumerate(cycle_list):
        if all(current_values.get(role) == values.get(role) for role in THEME_ROLES):
            index = i
            break
    next_index = (index + 1) % len(cycle_list)
    return cycle_list[next_index]
