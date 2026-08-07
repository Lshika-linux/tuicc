"""The contract audio backends must fulfil — routing & volume via
PipeWire (or PulseAudio directly, for the pactl fallback), VISION.md's
R5.
"""

from abc import ABC, abstractmethod

from tuicc.audio.model import AudioSink


class AudioBackend(ABC):
    @abstractmethod
    def get_sinks(self) -> list[AudioSink]:
        raise NotImplementedError

    @abstractmethod
    def set_default_sink(self, sink_id: str) -> None:
        """Route audio to sink_id — this is what modules/media.py's
        output switching ("music plays — route it to headphones", see
        VISION.md's R5 media section) actually calls; volume itself
        stays modules/control.py's concern, a system property, not a
        player one.
        """
        raise NotImplementedError

    @abstractmethod
    def set_volume(self, sink_id: str, percent: int) -> None:
        raise NotImplementedError

    @abstractmethod
    def set_mute(self, sink_id: str, muted: bool) -> None:
        raise NotImplementedError
