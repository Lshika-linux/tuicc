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
