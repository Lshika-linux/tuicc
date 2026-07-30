"""The contracts wifi and bluetooth backends must fulfil.

Two separate ABCs in one file, not two separate files: they're
independent contracts (a wifi backend never needs to know a bluetooth
backend exists), but small enough that splitting them into their own
files/folders, i feel would be more ceremony than the code warrants. What
must stay separate is *implementations* (iwd.py never imports
bluez.py) — that's the boundary that actually matters, not file
count.
"""

from abc import ABC, abstractmethod

from tuicc.connectivity.model import WifiNetwork, BluetoothDevice


class WifiBackend(ABC):
    @abstractmethod
    def get_networks(self) -> list[WifiNetwork]:
        raise NotImplementedError

    @abstractmethod
    def connect(self, ssid: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def disconnect(self) -> None:
        raise NotImplementedError


class BluetoothBackend(ABC):
    @abstractmethod
    def get_devices(self) -> list[BluetoothDevice]:
        raise NotImplementedError

    @abstractmethod
    def connect(self, device_id: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def disconnect(self, device_id: str) -> None:
        raise NotImplementedError
