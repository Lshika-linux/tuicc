"""Shared helpers for connectivity backends."""

import re

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi(text):
    """Remove ANSI color/style escape sequences. iwctl (and possibly
    other CLI tools) colorize their output for terminal display, but
    those escape codes come through raw when output is captured via
    subprocess rather than rendered by a terminal — they need to be
    gone before any line-based parsing happens.
    """
    return _ANSI_RE.sub("", text)
