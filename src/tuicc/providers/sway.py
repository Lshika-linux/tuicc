"""Sway provider — translates sway's IPC tree into tuicc's generic model."""

import os
import subprocess
import time

from i3ipc import Connection

from tuicc.model import Window, Region, WMState
from tuicc.providers.base import Provider


# The mark mark_self() applies to tuicc's own window, so parse_tree() can
# filter it back out — otherwise tuicc would list itself as a window in
# its own sidebar/preview.
#
# This is a PREFIX, not a fixed mark, because sway/i3 marks must be
# globally unique across the whole tree — "each identifier can only be
# set on a single window at a time" (sway(5)). A shared literal string
# breaks the moment a second tuicc instance runs: its mark_self() call
# would silently STEAL the mark away from the first instance (marks
# move, they don't duplicate), so the first instance would immediately
# stop filtering itself out. Suffixing with the process's own PID makes
# each instance's mark genuinely unique, so multiple tuicc windows can
# coexist without stealing each other's mark — and since filtering below
# checks the prefix, not an exact match, every tuicc instance still
# correctly excludes every OTHER tuicc window too, not just its own.
MARK_PREFIX = "_tuicc_self_"

# leave_fullscreen_self()'s settle wait — polled, not a blind sleep, and
# bounded generously. A fixed 50ms sleep was tried first and live-found
# to be unreliable specifically on the FIRST call after a fresh launch
# (workspace switched, but tuicc's own window kept rendering as a stale
# fullscreen ghost until an unrelated later event forced a repaint) —
# consistent with cold-connection/first-IPC-round-trip latency being
# higher than any later call over the same, by-then-warm Connection.
# Polling get_tree() until the change is actually confirmed removes the
# guesswork entirely instead of just picking a bigger magic number.
LEAVE_FULLSCREEN_POLL_INTERVAL_SECONDS = 0.01
LEAVE_FULLSCREEN_TIMEOUT_SECONDS = 0.3


def _leaf_to_window(leaf, ws_rect, floating):
    x = (leaf.rect.x - ws_rect.x) / ws_rect.width
    y = (leaf.rect.y - ws_rect.y) / ws_rect.height
    w = leaf.rect.width / ws_rect.width
    h = leaf.rect.height / ws_rect.height

    return Window(
        id=str(leaf.id),
        app_id=leaf.app_id or leaf.window_class or "unknown",
        title=leaf.name or "",
        focused=leaf.focused,
        rect=(x, y, w, h),
        floating=floating,
        pid=leaf.pid,
    )


def _is_tuicc_self(leaf) -> bool:
    return any(m.startswith(MARK_PREFIX) for m in leaf.marks)


def _stale_self_marks(tree) -> list:
    """(window_id, mark) pairs for every _tuicc_self_<pid> mark whose
    embedded pid does NOT match the window it's actually sitting on —
    the checkable signature of mark_self()'s own focus-race fallback
    mismarking an unrelated window (see that method's own docstring,
    and cleanup_stale_self_marks() below). The pid embedded in a
    LEGITIMATE tuicc self-mark is always that tuicc process's own pid
    — since sway reports each window's real owning pid directly
    (unlike i3, see that provider's own comment on this), a real
    mismatch here is unambiguous: no liveness probing needed, no
    guessing. Pure function (walks a tree, returns data, no IPC) so
    it's testable against a synthetic/recorded tree, same as
    parse_tree() itself — cleanup_stale_self_marks() is the thin,
    IPC-issuing wrapper around this.
    """
    stale = []

    def walk(node):
        for mark in node.marks:
            if mark.startswith(MARK_PREFIX):
                try:
                    mark_pid = int(mark[len(MARK_PREFIX):])
                except ValueError:
                    continue  # not this format at all — not ours to touch
                if node.pid != mark_pid:
                    stale.append((str(node.id), mark))
        for child in node.nodes + node.floating_nodes:
            walk(child)

    walk(tree)
    return stale


def _self_still_fullscreen(tree, mark: str) -> bool:
    """True only if the marked window is found AND still fullscreen_mode
    == 1. False both when it's genuinely not fullscreen anymore and
    when the mark isn't found at all (nothing to wait for either way).
    Pure, tree-walking — same style/traversal as _stale_self_marks()
    above, so leave_fullscreen_self()'s poll loop can be driven by a
    plain get_tree() call without any IPC of its own.
    """
    def walk(node):
        if mark in node.marks:
            return node.fullscreen_mode == 1
        for child in node.nodes + node.floating_nodes:
            found = walk(child)
            if found is not None:
                return found
        return None

    return bool(walk(tree))


def parse_tree(tree) -> WMState:
    """Convert an i3ipc tree into tuicc's generic WMState.
    
    Pure function: no IPC, no side effects. Takes an i3ipc Con node so it
    can be tested against recorded fixtures without a running compositor.
    """
    focused_leaf = tree.find_focused()
    focused_ws_num = None
    if focused_leaf:
        ws = focused_leaf.workspace()
        if ws:
            focused_ws_num = ws.num

    regions = []
    for workspace in tree.workspaces():
        windows = []
        ws_rect = workspace.rect

        for leaf in workspace.leaves():
            if _is_tuicc_self(leaf):
                continue
            windows.append(_leaf_to_window(leaf, ws_rect, floating=False))

        for leaf in workspace.floating_nodes:
            if _is_tuicc_self(leaf):
                continue
            windows.append(_leaf_to_window(leaf, ws_rect, floating=True))

        regions.append(Region(
            id=str(workspace.num),
            name=workspace.name,
            windows=windows,
            focused=(workspace.num == focused_ws_num),
        ))

    focused_region_id = str(focused_ws_num) if focused_ws_num is not None else None
    return WMState(regions=regions, focused_region_id=focused_region_id)


class SwayProvider(Provider):
    def __init__(self, conn=None):
        self.conn = conn or Connection()

    def focus_region(self, region_id: str) -> None:
        self.conn.command(f"workspace {region_id}")

    def leave_fullscreen_self(self) -> None:
        """See base.py's docstring for the calling contract. swayfx 0.6/
        sway 1.12: switching workspace focus away from a fullscreen (or
        plain floating, pinned-to-full-output) container leaves the
        DESTINATION workspace fully invisible — real windows, correct
        `rect`, `visible: true` in `get_tree` — until something forces
        a real redraw. Root cause, confirmed directly against source
        (not just theorized): scenefx's fork of wlr_scene.c drops the
        whole-output damage call upstream wlroots fires when a frame
        exits direct scan-out — see CLAUDE/NOTES/wm-quirks.md
        #workspace-switch-fullscreen-invisible for the full
        investigation, including two rejected mitigations (bouncing
        through a third workspace, nudging the pointer — neither worked
        reliably). What DOES work, live-confirmed by Rafi: turn
        fullscreen off on the about-to-be-hidden window and let that
        settle BEFORE ever switching workspace, so the actual switch is
        a normal composited-to-composited transition rather than the
        buggy scanout-exit one.

        The settle wait is polled (_self_still_fullscreen(), bounded by
        LEAVE_FULLSCREEN_TIMEOUT_SECONDS), not a blind sleep — a fixed
        50ms sleep was tried first and live-found to be unreliable
        specifically on the FIRST call after a fresh tuicc launch
        (workspace switched, but tuicc's own window kept rendering as a
        stale fullscreen ghost until an unrelated later event forced a
        repaint — plausibly a cold-connection/first-IPC-round-trip
        latency spike that later, warmed-up calls don't hit). Polling
        the real tree until the change is actually confirmed removes
        that guesswork instead of just picking a bigger magic number.
        Still a real, deliberate blocking call on the main thread — the
        ordering (send the command, THEN confirm, THEN let focus_region
        run) is what matters, not any particular sleep duration.
        """
        mark = f"{MARK_PREFIX}{os.getpid()}"
        self.conn.command(f"[con_mark={mark}] fullscreen disable")
        deadline = time.monotonic() + LEAVE_FULLSCREEN_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if not _self_still_fullscreen(self.conn.get_tree(), mark):
                return
            time.sleep(LEAVE_FULLSCREEN_POLL_INTERVAL_SECONDS)

    def focus_window(self, window_id: str) -> None:
        self.conn.command(f"[con_id={window_id}] focus")

    def move_window_to_region(self, window_id: str, region_id: str) -> None:
        self.conn.command(f"[con_id={window_id}] move container to workspace number {region_id}")

    def close_window(self, window_id: str) -> None:
        self.conn.command(f"[con_id={window_id}] kill")

    def mark_self(self, app_id: str | None = None) -> None:
        mark = f"{MARK_PREFIX}{os.getpid()}"
        if app_id:
            # Marks by WM criteria — no focus-timing assumption at all,
            # so this is immune to the race the fallback below has.
            self.conn.command(f'[app_id="{app_id}"] mark --add {mark}')
            return
        # KNOWN LIMITATION (fallback only, when no self_app_id is
        # configured): assumes tuicc's own window is whichever one is
        # currently focused at call time — see
        # CLAUDE/NOTES/known-limitations.md#mark-self-focus-race for the
        # concrete failure mode and why setting self_app_id sidesteps it
        # entirely (see defaults/config.toml's own comment on it).
        self.conn.command(f"mark --add {mark}")

    def cleanup_stale_self_marks(self) -> None:
        tree = self.conn.get_tree()
        for window_id, mark in _stale_self_marks(tree):
            self.conn.command(f"[con_id={window_id}] unmark {mark}")

    def dismiss_self(self) -> None:
        self.conn.command(f"[con_mark={MARK_PREFIX}{os.getpid()}] move scratchpad")

    def focus_self(self, fullscreen: bool = False, force_relayout: bool = False) -> None:
        if fullscreen and force_relayout:
            # See Provider.focus_self()'s docstring: forces sway to run
            # a real layout pass for tuicc's own workspace, which is
            # otherwise suppressed the entire time tuicc stays
            # fullscreen — the fix for a sibling window landing there
            # and never getting a real rect computed for it.
            action = "focus, fullscreen disable, fullscreen enable"
        elif fullscreen:
            action = "focus, fullscreen enable"
        else:
            action = "focus"
        self.conn.command(f"[con_mark={MARK_PREFIX}{os.getpid()}] {action}")

    def no_focus_next_window(self, pid: int) -> None:
        self.conn.command(f"for_window [pid={pid}] no_focus")

    def get_state(self) -> WMState:
        return parse_tree(self.conn.get_tree())

    def set_floating_geometry(self, window_id: str, region_id: str, rect: tuple[float, float, float, float]) -> None:
        workspace = next((w for w in self.conn.get_tree().workspaces() if str(w.num) == region_id), None)
        if workspace is None:
            return

        rx, ry, rw, rh = rect
        x = round(workspace.rect.x + rx * workspace.rect.width)
        y = round(workspace.rect.y + ry * workspace.rect.height)
        w = round(rw * workspace.rect.width)
        h = round(rh * workspace.rect.height)

        self.conn.command(
            f"[con_id={window_id}] floating enable, "
            f"resize set {w}px {h}px, move position {x}px {y}px"
        )

    def copy_to_clipboard(self, text: str) -> bool:
        """wl-copy — the standard Wayland clipboard CLI (wl-clipboard),
        not a sway-specific tool, but sway is always Wayland so this is
        the right choice for this provider. Missing binary/any
        subprocess failure both just return False — see Provider's own
        docstring for why this degrades rather than raising.
        """
        try:
            subprocess.run(["wl-copy"], input=text.encode(), check=True)
            return True
        except (OSError, subprocess.CalledProcessError):
            return False
