"""
The contract every WM provider must fulfil.

Core code and modules only ever talk to this interface
 — never to any WM-specific tool directly.

 """

from abc import ABC, abstractmethod

from tuicc.model import WMState

class Provider(ABC):
    @abstractmethod
    def get_state(self) -> WMState:
        """Return the current window-manager state as a WMState."""
        raise NotImplementedError

    @abstractmethod
    def focus_region(self, region_id: str) -> None:
        """Switch the WM's focus to the given region (e.g. workspace)."""
        raise NotImplementedError

    @abstractmethod
    def focus_window(self, window_id: str) -> None:
        """Switch the WM's focus to the given window."""
        raise NotImplementedError

    @abstractmethod
    def move_window_to_region(self, window_id: str, region_id: str) -> None:
        """Move the given window to the given region, without changing
        which region is currently visible.
        """
        raise NotImplementedError

    @abstractmethod
    def close_window(self, window_id: str) -> None:
        """Close the given window. Required, not optional like
        mark_self/resolve_pid/set_floating_geometry below — every WM
        worth supporting can close a window; there's no meaningful
        degraded case to fall back to the way there is for marks or
        floating-window geometry.
        """
        raise NotImplementedError

    def mark_self(self, app_id: str | None = None) -> None:
        """Mark tuicc's own window (called once at startup) so get_state()
        can exclude it from the Windows it reports — tuicc never lists
        itself in its own preview/sidebar.

        app_id (from `[wm] self_app_id` in config.toml), if given, lets an
        implementation mark by WM criteria instead of assuming tuicc's own
        window is whichever one is currently focused at call time — see
        CLAUDE/NOTES/known-limitations.md#mark-self-focus-race for the race
        that fallback has with back-to-back instance launches.

        Optional, not abstract (default no-op): only sway/i3 implement it,
        via native WM marks. See CLAUDE/NOTES/design-decisions.md
        #optional-provider-methods for why this whole group defaults to a
        no-op rather than raising.
        """
        pass

    def dismiss_self(self) -> None:
        """Hide tuicc's own window without ending the process — the
        "dismiss" half of tuicc's lifecycle model (see
        CLAUDE/NOTES/design-decisions.md#dismiss-vs-quit). main.py calls
        this instead of exiting; the process stays warm for the next
        summon, and the only real way to end tuicc is Ctrl+C.

        Optional, not abstract (default no-op): sway/i3 implement it by
        targeting the same mark mark_self() applies, which keeps it immune
        to that method's focus-based-fallback race.
        """
        pass

    def focus_self(self, fullscreen: bool = False, force_relayout: bool = False) -> None:
        """Reclaim keyboard focus for tuicc's own window — called by
        pending_moves.process() right after it moves a freshly spawned
        (launcher) or restored (sessions) window into its target region.

        Severe if left unimplemented on a WM that steals focus on map: see
        CLAUDE/NOTES/wm-quirks.md#focus-on-map-stealing — without this,
        every launcher spawn silently steals input from tuicc on the very
        first window, not as an edge case.

        fullscreen (only ever passed True by callers that already read
        `[wm] fullscreen_only`) re-asserts fullscreen state alongside
        reclaiming focus — see CLAUDE/NOTES/wm-quirks.md#fullscreen-drop-on-map
        for why that's needed. force_relayout (only ever passed True by
        pending_moves.process() for a match resolving onto tuicc's own
        region) additionally forces a layout pass for that workspace — see
        CLAUDE/NOTES/wm-quirks.md#fullscreen-suppresses-layout. Both default
        False so a provider/caller ignoring them keeps plain-focus behavior.

        Optional, not abstract (default no-op) — same optionality as
        mark_self()/dismiss_self(), but the degraded case here is severe
        enough to call out explicitly (see
        CLAUDE/NOTES/design-decisions.md#optional-provider-methods). sway/i3
        implement it by focusing the mark mark_self() already applies.
        """
        pass

    def no_focus_next_window(self, pid: int) -> None:
        """Ask the WM not to auto-focus the very next window created by the
        process at pid, before that process is even spawned — called right
        after spawn_detached() returns, pid still fresh, well before the
        process has had a chance to map a window.

        A WM-side, root-cause complement to focus_self(), not a
        replacement — see CLAUDE/NOTES/wm-quirks.md#no-focus-pid-criteria
        for why it's keyed on pid (not class/app_id) and what that costs,
        and #fullscreen-drop-on-map for why preventing the auto-focus also
        prevents tuicc's fullscreen container from dropping to floating.
        Silently a no-op for apps that fork/exec into a child with a
        different pid than spawn_detached() returned — see
        CLAUDE/NOTES/known-limitations.md#fork-exec-pid-mismatch.

        Optional, not abstract (default no-op): sway/i3 implement it via
        `for_window [pid=<pid>] no_focus`.
        """
        pass

    def resolve_pid(self, window_id: str) -> int | None:
        """Best-effort process id for the window's owning process, for code
        that needs to relaunch it later (reading /proc/<pid>/cmdline — see
        session.py). Not part of the per-frame get_state() path; only
        called on demand for a handful of windows, so it may be more
        expensive than get_state() needs to be.

        Optional, not abstract (default no-op returning None): only needed
        for a WM whose get_state() doesn't already put pid on Window. sway
        does (native IPC field, see model.py's Window.pid) so its provider
        doesn't override this; i3 does not, so i3.py resolves the window's
        X11 id to a pid via the _NET_WM_PID EWMH property. Callers already
        treat None as "couldn't find out", not an error.
        """
        return None

    def set_floating_geometry(self, window_id: str, region_id: str, rect: tuple[float, float, float, float]) -> None:
        """Move window_id into floating mode and position/resize it to rect
        (normalized 0..1, same space as Window.rect — relative to
        region_id's own dimensions). Used by session restore to put a saved
        floating window back at its relative position/size, not just on
        the right workspace.

        Deliberately not given region_id's absolute pixel dimensions —
        those never leave a provider's raw WM tree (get_state() doesn't
        expose them, so the rest of tuicc stays resolution-agnostic; see
        model.py's Region). An implementation looks up region_id's current
        absolute rect itself and converts against that, not whatever it
        was when the session was saved, so a different or reconfigured
        monitor still gets a sane result instead of an off-screen window.

        Optional, not abstract (default no-op): sway/i3 both implement it
        (same floating enable / resize set / move position syntax). A WM
        with no floating-window concept leaves the default — the restored
        window lands wherever the WM naturally places a new window.
        """
        pass
