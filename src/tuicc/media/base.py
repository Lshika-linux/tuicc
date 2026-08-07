"""The contract media backends must fulfil — playback control via
MPRIS, VISION.md's R5. Unlike wifi/bluetooth/audio, there's exactly one
real choice here (MPRIS is THE standard media-player D-Bus protocol on
Linux, not one option among several) — no registry.py, just the one
MprisBackend implementation.
"""

from abc import ABC, abstractmethod

from tuicc.media.model import Player


class MediaBackend(ABC):
    @abstractmethod
    def get_players(self) -> list[Player]:
        raise NotImplementedError

    @abstractmethod
    def play_pause(self, bus_name: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def next(self, bus_name: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def previous(self, bus_name: str) -> None:
        raise NotImplementedError
