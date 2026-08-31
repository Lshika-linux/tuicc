"""Shared window-title condensing logic.

Extracted out of modules/sidebar.py (which had the only copy) so
modules/preview.py can show the same "what's actually running" text
instead of just the raw app_id — without it, a kitty window running
htop and another running cava both showed as plain "kitty" in the
preview module, even though the sidebar was already condensing the
exact same windows' titles down to "htop"/"cava" via this very logic.

Kept top-level (src/tuicc/, not modules/) rather than having preview.py
import across sidebar.py directly — no other pair of modules imports
from each other today, and this keeps that true. Same "pull it out to
a shared top-level file" precedent as windowed_list.py (extracted from
media.py's scrollable-list mechanic once sysmon.py needed it too).
"""

import re


_TITLE_SPLIT_RE = re.compile(r"\s+[-—–|]\s+")


def condense_title(app, title, cfg):
    """Condensed window title — only the part that adds information
    beyond the app's own name. Empty string = nothing useful to show.

    Which app_ids count as "terminal" or "browser" comes from
    [title_condense] in config.toml, not hardcoded here — the shape of
    the heuristic (terminals show the full title, browsers show just
    the site name, everything else shows its first segment) is generic
    across WMs and users, but *which apps* fall into which bucket is
    specific to whoever's config it is.
    """
    app_l = (app or "").lower()
    title = (title or "").strip()
    if not title:
        return ""

    if app_l in cfg.terminal_apps:
        # A bare "~" is the shell's own idle-prompt title (home
        # directory, nothing running) — found live, GitHub issue #8
        # follow-up (2026-08-31): several bare shells crammed into one
        # stacked group's narrow corner labels all showed a
        # meaningless "-~" suffix, read as if it were real info when
        # it's really "nothing to add", the exact same case the
        # generic bucket's own app_id-equals check below already
        # covers — just reached through a different literal value
        # since terminal titles are shown verbatim, not app_id itself.
        if title == "~":
            return ""
        return title

    parts = [p.strip() for p in _TITLE_SPLIT_RE.split(title) if p.strip()]
    if not parts:
        return title

    if app_l in cfg.browser_apps:
        segs = [p for p in parts if p.lower() not in cfg.browser_title_names] or parts
        return segs[-1]

    first = parts[0]
    if first.lower() == app_l:
        return ""
    return first
