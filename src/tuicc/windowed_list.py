"""Shared "fixed N-visible-slots + scrollable via peek nav items" list
mechanic — extracted out of modules/media.py (Now Playing/Output were
the first two sections to get this treatment; see that module's own
docstring for the full live-design-discussion reasoning behind it)
once modules/sysmon.py (VISION.md's R6) needed the exact same
mechanic for its own scrollable window list, not a second copy of it.

The core tension this solves: a box's own height is a static, user-
configured layout ratio, but the number of real items a section has to
show (players, sinks, windows, ...) is a live, uncontrolled runtime
quantity. Fixed N-slot windowing keeps the box's own row budget
predictable regardless of content count — never more rows than N,
never fewer (unfilled slots render as an "[empty - ...]" placeholder).

Every function here is pure — no curses, no module-level state — and
generic over what "count"/"selected_index"/"items"/"label" mean to the
caller; media.py and sysmon.py each supply their own domain objects
and kind/label strings.
"""

VISIBLE_SLOTS = 3


def window_start(count: int, selected_index: int | None, visible_slots: int = VISIBLE_SLOTS) -> int:
    """The 0-indexed start of the `visible_slots`-wide window into a
    `count`-item list, keeping `selected_index` (if any) inside it.

    Recomputed fresh from `selected_index` every call — no persisted
    scroll-offset state, same "derive from what's true right now" idiom
    the rest of tuicc's render loop already uses (see CLAUDE.md's
    "nothing is cached per-frame"), so this can never drift out of sync
    with whatever's actually selected. If nothing in THIS list is
    currently selected, the window is just the first `visible_slots`
    items — a stable, unsurprising default, not "wherever a hidden
    offset happened to leave it last frame".

    When selected_index is within the first visible_slots items, no
    scrolling is needed at all (window starts at 0). Beyond that, the
    window shifts by exactly enough to make selected_index the LAST
    visible slot — not centered, not scrolled further than necessary.
    """
    if count <= visible_slots:
        return 0
    if selected_index is None or selected_index < visible_slots:
        return 0
    return min(selected_index - visible_slots + 1, count - visible_slots)


def section_nav_indices(count: int, selected_index: int | None,
                         visible_slots: int = VISIBLE_SLOTS) -> tuple[int | None, int | None]:
    """(before_index, after_index) — the two data indices, one on each
    side of the current visible_slots window (see window_start), that
    need a "peek" NavItem this frame so Tab/Shift+Tab can reach a
    scrollable section's hidden items at all. Either is None when
    there's nothing to peek in that direction (window already at that
    edge, or count fits within visible_slots with no scrolling needed).

    Why a peek item is safe, not a "select something invisible"
    problem: landing on one updates ctx.selected_id, and — since
    EVERYTHING recomputes fresh every frame, nothing cached (see
    CLAUDE.md) — the very next frame's own window_start immediately
    recomputes around that new selection and the peek index becomes a
    real, drawn slot before the screen ever actually redraws showing it
    as selected. A peek NavItem only exists for the one frame between
    "Tab was pressed" and "the screen redraws" — never something a user
    can actually see selected-but-undrawn.
    """
    if count <= visible_slots:
        return None, None
    start = window_start(count, selected_index, visible_slots)
    end = start + visible_slots
    before = start - 1 if start > 0 else None
    after = end if end < count else None
    return before, after


def section_rows(items: list | None, error: str | None, selected_index: int | None,
                  kind: str, label: str, visible_slots: int = VISIBLE_SLOTS) -> list[tuple]:
    """Exactly `visible_slots` rows for one list section — ALWAYS,
    regardless of how many real items exist right now: real items
    (windowed via window_start when there are more than fit),
    "[empty - <label> N]" placeholders for unfilled slots (N is the
    SLOT's own position, 1-indexed — NOT a running count of how many
    items are missing), or the poll error in the first slot (remaining
    slots still padded the normal way) when the last poll genuinely
    failed. `items=None` with no error (not yet polled) is treated the
    same as a genuinely empty list — same None-vs-[] discipline as
    elsewhere, but there's nothing DIFFERENT to show the user between
    "unknown" and "empty" here (unlike bars.py's dash-vs-omit
    distinction), both just render as empty slots.
    """
    if items is None and error:
        rows = [("error", error)]
        rows += [("empty_slot", f"[empty - {label} {slot + 1}]") for slot in range(1, visible_slots)]
        return rows

    items = items or []
    count = len(items)
    start = window_start(count, selected_index, visible_slots)
    rows = []
    for slot in range(visible_slots):
        idx = start + slot
        if idx < count:
            rows.append((kind, items[idx]))
        else:
            rows.append(("empty_slot", f"[empty - {label} {slot + 1}]"))
    return rows


def header_with_count(title: str, items: list | None) -> str:
    """Header text with the section's total real item count appended —
    "Now Playing [6]" — found live, asked for: with fixed-slot windowing
    only ever showing `visible_slots` of a possibly-longer list at
    once, the header's own count is what makes "there's more than what
    you're looking at right now" legible at a glance, not just
    discoverable by scrolling into it.

    Omitted (bare title, no "[N]") when the count itself is unknown —
    items is None, whether that means "not polled yet" or "the last
    poll failed" — showing "[0]" there would claim something this
    function doesn't actually know, same None-vs-[] discipline the
    rest of this codebase uses.
    """
    if items is None:
        return title
    return f"{title} [{len(items)}]"
