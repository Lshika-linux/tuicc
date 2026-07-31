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
        """
        pass
