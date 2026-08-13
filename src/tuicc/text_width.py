"""Terminal display-width helpers — CJK/fullwidth-aware, plain stdlib.

Every module that truncates text to fit a fixed column budget used to
do it with plain Python string slicing (`text[:n]`), which counts
*characters*, not *terminal columns*. That's correct for ASCII, but
East Asian "wide"/"fullwidth" characters (CJK, fullwidth punctuation,
etc.) render as 2 terminal columns each while still counting as a
single Python character — a string with such characters could measure
as "fits in `n` columns" by len() while actually needing up to 2x that
many columns, overflowing past a box's own border (found live: Spotify
track titles with Japanese text in sidebar.py/media.py).

Uses `unicodedata.east_asian_width()` (stdlib, no new dependency) —
classifies each codepoint as Wide/Fullwidth (2 columns) or
Narrow/Halfwidth/Neutral/Ambiguous (1 column). "Ambiguous" is treated
as narrow, matching how most terminals render it in a non-CJK locale —
the same simplification real-world tools like `wcwidth` default to
without an explicit East-Asian locale hint, and adequate here since
tuicc doesn't have per-user locale context to do better.
"""

import unicodedata


def char_width(ch: str) -> int:
    """One character's own display width — 2 for Wide/Fullwidth, 1 for
    everything else (including Ambiguous, see module docstring).
    """
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def display_width(text: str) -> int:
    """Total terminal columns `text` occupies when rendered."""
    return sum(char_width(ch) for ch in text)


def truncate_to_width(text: str, max_width: int) -> str:
    """`text` cut down to fit within max_width terminal columns —
    unlike `text[:max_width]`, never counts a wide character as only
    one column, and never splits a wide character in half (a character
    that would only partially fit is dropped entirely, same as it
    simply not being drawn at all — there's no such thing as half a
    glyph on a terminal grid).
    """
    if max_width <= 0:
        return ""
    out = []
    used = 0
    for ch in text:
        w = char_width(ch)
        if used + w > max_width:
            break
        out.append(ch)
        used += w
    return "".join(out)
