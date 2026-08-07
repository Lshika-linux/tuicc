"""AudioSink — the generic shape every audio backend translates a
PipeWire/PulseAudio sink into, same role connectivity/model.py's
WifiNetwork/BluetoothDevice play for their own domain.
"""

from dataclasses import dataclass


@dataclass
class AudioSink:
    id: str
    name: str  # human-readable (wpctl/pactl call this "description")
    volume: int  # 0-100, already rounded — backends deal in whatever
                 # precision the underlying tool reports (wpctl: 2
                 # decimal places of a 0..1 float; pactl: a whole
                 # percent already), this is the one normalized shape
                 # modules/control.py's slider actually renders.
    muted: bool
    is_default: bool
