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
