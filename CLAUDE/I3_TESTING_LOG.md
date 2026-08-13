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
