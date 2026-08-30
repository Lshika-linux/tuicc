# i3 Testing Log

This file closes a specific loop: the primary development work on
tuicc happens against a sway sandbox with no real i3 hardware access,
so every i3-specific behavior (some genuinely different from sway —
`no_focus`/`resize set` semantics, X11 `_NET_WM_PID` timing, real-app
quirks like fork/exec pid mismatches) can only be *verified*, not
*tested*, by that session. This file is how a Claude Code session
running directly on real i3 hardware reports back — dated entries,
committed and pushed, so a `git pull` on any other machine picks them
up and can act on them.

**If you are that i3-hardware session:** work through
`CLAUDE/GUIDE.md`'s "Expected behavior — verification checklist" section
item by item. For each item, actually do the thing described (spawn
apps, save/load sessions, trigger confirm dialogs, watch a live
`get_tree` poll) — don't just read the code and reason about whether
it *should* work. Where you find and fix a real bug, fix it following
this repo's existing conventions (`CONTRIBUTING.md`, and match the
density/style of comments already in the file you're touching) and
run the full test suite before committing. Where you find something
you can't fix confidently in one session (unclear root cause, needs a
design decision, touches multiple files) — leave it unfixed, note it
clearly here, and don't guess at a fix that isn't well-understood.

**Specifically flagged for verification right now (2026-08-30, GitHub issue #9):** `Provider.wm_config()` (`wm_config_parser.py`) — parses the WM's own config text (via `Connection.get_config()`) for real workspace names and `for_window`/`assign` routing rules, replacing the old blind "1".."total_workspaces" sidebar slot guess. Implemented identically on `I3Provider` as `SwayProvider` (same `i3ipc.get_config()` call, not WM-specific), and i3's own `assign` grammar (the `workspace` keyword being optional — `assign [criteria] 2`, `→ 2`, `→ work`, `→ number 2`, and excluding `output ...`) was checked against i3's real user guide and covered with unit tests — but none of this has run against an actual i3 process. Concretely worth checking on real i3 hardware:
- Does `i3ipc.Connection.get_config()` actually work against real i3 the way it does against swayfx (confirmed live there), including comment/`include` handling matching what this file's own docstring assumes?
- Sidebar shows your real workspace names/numbers (not just "1".."10") — especially if your i3 config uses non-default numbering or named workspaces.
- If you have (or can add) a real `assign` rule using the bare/arrow form (no explicit "workspace" keyword) — does tuicc's launcher correctly auto-route to it and show the "Routing rule detected" hint?
- Up/Down while typing in the launcher still correctly cycles your real workspace list (`shift_workspace_id()`), not the old numeric range.

Append a new entry below using this template — newest entry at the
top, don't rewrite or delete older ones (this is a log, not a living
doc; stale entries are still useful history, not clutter):

```markdown
## YYYY-MM-DD — <short description of what this session focused on>

**Machine/setup:** i3 version, distro, provider config relevant details
(fullscreen_only value, self_app_id, terminal used, etc.)

**Checklist items covered:** which subsections of CLAUDE/GUIDE.md's
verification checklist you actually exercised this session — be
specific, "session restore" isn't as useful as "session restore with
5 windows: firefox, X, Y, Z, W".

**Findings:**
- Item — PASS/FAIL/PARTIAL — concrete detail (what you did, what
  happened, timestamps/log excerpts if it's a timing-sensitive bug,
  reproducibility — did you try it 3x like CLAUDE/GUIDE.md asks?).

**Fixed this session (commit refs):** — anything you diagnosed AND
fixed AND tested, with the commit hash(es).

**Still open / needs the other session's input:** — anything you
found but couldn't confidently resolve alone; be concrete about what
you tried and what's still unclear, so the next session (i3 or sway)
doesn't have to re-derive your reasoning from scratch.
```

---

*(No entries yet — this file was created alongside CLAUDE/GUIDE.md's
verification checklist. The first i3-hardware session to run through
it should add the first entry above this line.)*
