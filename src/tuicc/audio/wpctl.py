"""wpctl backend — WirePlumber's CLI, the primary audio backend
(PipeWire/WirePlumber is what every current sway/i3 desktop actually
runs). Parses `wpctl status`'s "Sinks:" section rather than talking to
PipeWire's own D-Bus-adjacent protocol directly — there's no stable
D-Bus interface for it the way iwd/BlueZ have (PipeWire uses its own
native IPC protocol, not D-Bus), so a CLI text pattern (same category
as bluez.py's bluetoothctl parsing) is the pragmatic choice here, not
an inconsistency with iwd.py's D-Bus approach.
"""

import re
import subprocess

from tuicc.audio.base import AudioBackend
from tuicc.audio.model import AudioSink

# Matches a sink row anywhere in the line, on purpose — wpctl's own
# box-drawing/indentation prefix (`│`, ` ├─`, spacing) isn't parsed
# structurally, only used by parse_status_sinks below as a "still
# inside the Sinks section" boundary. Each row looks like:
#   *   64. Built-in Audio Analog Stereo        [vol: 0.50 MUTED]
# the leading `*` (present only on the default sink) is optional.
_SINK_ROW_RE = re.compile(r"(\*)?\s*(\d+)\.\s+(.+?)\s+\[vol:\s*([\d.]+)(\s+MUTED)?\]")


def parse_status_sinks(text: str) -> list[AudioSink]:
    """The "Sinks:" section of `wpctl status` output -> a list of
    AudioSink. Stops at the next section header ("Sources:"/
    "Filters:"/"Streams:"/"Devices:" — wpctl prints several sections,
    Sinks is only one of them) so a system with sources or other
    devices listed doesn't get their rows misparsed as sinks too.
    """
    sinks = []
    in_sinks = False
    for line in text.splitlines():
        if "Sinks:" in line:
            in_sinks = True
            continue
        if not in_sinks:
            continue
        if any(marker in line for marker in ("Sources:", "Filters:", "Streams:", "Devices:")):
            break
        match = _SINK_ROW_RE.search(line)
        if match is None:
            continue
        star, sink_id, name, volume, muted = match.groups()
        sinks.append(AudioSink(
            id=sink_id,
            name=name.strip(),
            volume=round(float(volume) * 100),
            muted=muted is not None,
            is_default=star is not None,
        ))
    return sinks


class WpctlBackend(AudioBackend):
    def get_sinks(self) -> list[AudioSink]:
        """No internal try/except here — same reasoning as
        connectivity/iwd.py and bluez.py's own R3 follow-up fix: a
        failure (wpctl missing, wireplumber not running) needs to
        propagate so status_worker.StatusWorker's poll wrapper can
        record it as a real last_error, not vanish into a silent [].
        """
        result = subprocess.run(["wpctl", "status"], capture_output=True, text=True, timeout=5)
        return parse_status_sinks(result.stdout)

    def set_default_sink(self, sink_id: str) -> None:
        subprocess.run(["wpctl", "set-default", sink_id], capture_output=True, text=True, timeout=5)

    def set_volume(self, sink_id: str, percent: int) -> None:
        subprocess.run(["wpctl", "set-volume", sink_id, f"{percent}%"], capture_output=True, text=True, timeout=5)

    def set_mute(self, sink_id: str, muted: bool) -> None:
        subprocess.run(
            ["wpctl", "set-mute", sink_id, "1" if muted else "0"],
            capture_output=True, text=True, timeout=5,
        )
