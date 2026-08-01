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

    def mark_self(self) -> None:
        """
        We need to hide tuicc itself from the preview and sidebar..

        Mark the currently-focused window (called once at startup,
        when tuicc's own window is assumed to be the freshly-focused
        one) so get_state() can exclude it from the Windows it reports
        downstream — tuicc never lists itself in its own preview/sidebar.

        NOT abstract: not every WM has an equivalent concept to sway/i3's
        marks. The default here is a no-op — a provider for a WM without
        one simply won't be able to filter tuicc's own window out, which
        is a known degraded case (tuicc may show up as a window in its
        own preview), not a crash or a required feature.

        Who needs to implement this: sway and i3 both do, via native WM
        marks. Implement it for a new provider if its WM has any
        equivalent "tag a window, filter by tag later" concept; if not,
        leave the no-op default and accept the degraded case above.
        """
        pass

    def resolve_pid(self, window_id: str) -> int | None:
        """
        Best-effort process id for the window's owning process, for
        code that needs to relaunch it later (reading /proc/<pid>/cmdline
        to capture the exact command a window was started with — see
        session.py) — NOT part of the per-frame get_state() path, only
        called on demand for a handful of windows at a time, since it's
        allowed to be more expensive than get_state() needs to be.

        NOT abstract, default no-op returning None. Who needs to
        implement this: only a provider whose WM doesn't already put
        pid on Window via get_state(). sway does (its IPC tree exposes
        pid directly, see model.py's Window.pid docstring) — don't
        override this for sway-like providers, it's redundant. i3
        does not, so i3.py overrides this to resolve the window's X11
        id (leaf.window, not leaf.id — see i3's IPC docs) to a pid via
        the standard _NET_WM_PID EWMH property. A future provider for
        a WM with neither get_state()-level pid nor an X11 fallback
        available should just leave this as the no-op default — code
        calling it already treats None as "couldn't find out", not an
        error.
        """
        return None
