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
    Recomputed fresh every call, no persisted scroll-offset state (see
    CLAUDE/GUIDE.md's "nothing is cached per-frame"). No selection ->
    window starts at 0. Selection beyond the first `visible_slots` ->
    window shifts by exactly enough to make it the last visible slot.
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
    scrollable section's hidden items. Either is None when there's
    nothing to peek in that direction. Safe, not "select something
    invisible": landing on one updates ctx.selected_id, and since
    nothing is cached (see CLAUDE/GUIDE.md), the next frame's
    window_start immediately recomputes around it before the screen
    ever redraws it as selected-but-undrawn.
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
    """Exactly `visible_slots` rows for one list section, always: real
    items (windowed via window_start), "[empty - <label> N]"
    placeholders for unfilled slots (N is the slot's own 1-indexed
    position, not a count of what's missing), or the poll error in the
    first slot (rest still padded normally) on a genuine poll failure.
    `items=None` with no error is treated as a genuinely empty list —
    both just render as empty slots here.
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
    "Now Playing [6]" — so "there's more than what's visible" is
    legible at a glance under fixed-slot windowing. Omitted (bare
    title) when the count is unknown (items is None) — "[0]" would
    claim something this function doesn't actually know.
    """
    if items is None:
        return title
    return f"{title} [{len(items)}]"
