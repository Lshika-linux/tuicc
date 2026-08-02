"""Keybinding resolution: maps config key names to curses key codes.

Special names (arrows, Tab, Enter...) map to their curses constants.
A single character maps via ord() — so users can bind e.g. "h"/"j"/
"k"/"l" instead of arrow keys, and uppercase letters work as an
implicit Shift (curses reports "A" and "a" as different codes on
their own, no special handling needed).

Shift+Tab is the one Shift combination handled explicitly, via
curses.KEY_BTAB — a standard, reliably supported terminal code.
General "any key + Shift" isn't attempted: curses doesn't expose
modifier state separately from the key itself for most keys, and
support for e.g. Shift+arrow varies by terminal.

"Ctrl+<letter>" IS supported, unlike Shift — terminals report it
reliably as a plain ASCII control code (Ctrl+A=1 ... Ctrl+Z=26, i.e.
ord(letter.upper()) - 64), with no modifier-detection ambiguity.
Ctrl combos with anything other than a single letter (digits,
punctuation, arrows) are not attempted, for the same reliability
reason Shift combos mostly aren't.

Kept pure (no live curses screen needed to resolve a constant), same
reasoning as theme.py vs theme_setup.py.
"""

import curses


SPECIAL_KEYS = {
    "Left": curses.KEY_LEFT,
    "Right": curses.KEY_RIGHT,
    "Up": curses.KEY_UP,
    "Down": curses.KEY_DOWN,
    "Tab": ord("\t"),
    "Shift+Tab": curses.KEY_BTAB,
    "Enter": 10,
    "Escape": 27,
    "Space": ord(" "),
    "Delete": curses.KEY_DC,
    # F-keys: their own namespace, separate from both Ctrl+ (reserved
    # for power_menu-style global system actions) and plain characters
    # (reserved for the launcher's "start typing from anywhere"
    # fallback) — the right category for a mode toggle like resize.
    **{f"F{n}": curses.KEY_F0 + n for n in range(1, 13)},
}


_KEY_NAMES_BY_CODE = {code: name for name, code in SPECIAL_KEYS.items()}


def key_label(code: int) -> str:
    """Reverse of resolve_key: a short display string for a resolved
    key code, so UI text can show what a user actually bound instead
    of a hardcoded label (e.g. the Y/N confirm-dialog hint reflecting
    the real confirm_yes/confirm_no keys). Best-effort: Ctrl+ combos
    and anything outside SPECIAL_KEYS/printable ASCII have no way to
    recover the original name from the code alone, so they fall back
    to "?" rather than guessing.
    """
    if code in _KEY_NAMES_BY_CODE:
        return _KEY_NAMES_BY_CODE[code]
    if 32 <= code <= 126:
        return chr(code).upper()
    return "?"


def resolve_key(name: str) -> int:
    if name in SPECIAL_KEYS:
        return SPECIAL_KEYS[name]

    if name.startswith("Ctrl+"):
        letter = name[len("Ctrl+"):]
        if len(letter) == 1 and letter.isalpha():
            return ord(letter.upper()) - 64
        raise ValueError(
            f"Unknown key name: {name!r}. Ctrl+ combos are only supported "
            f"for a single letter, e.g. \"Ctrl+L\"."
        )

    if len(name) == 1:
        return ord(name)

    raise ValueError(
        f"Unknown key name: {name!r}. Expected one of "
        f"{list(SPECIAL_KEYS.keys())}, a single character, or \"Ctrl+<letter>\"."
    )
