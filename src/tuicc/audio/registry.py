"""Registry mapping backend names (from config) to backend classes —
same pattern as connectivity/registry.py.
"""

from tuicc.audio.base import AudioBackend
from tuicc.audio.wpctl import WpctlBackend
from tuicc.audio.pactl import PactlBackend

AUDIO_BACKENDS = {
    "wpctl": WpctlBackend,
    "pactl": PactlBackend,
}


def build_audio_backend(name: str) -> AudioBackend:
    if name not in AUDIO_BACKENDS:
        raise ValueError(f"Unknown audio backend: {name!r}. Available: {list(AUDIO_BACKENDS.keys())}")
    return AUDIO_BACKENDS[name]()
