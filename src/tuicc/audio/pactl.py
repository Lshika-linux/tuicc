"""pactl backend — PulseAudio's (also PipeWire's pulse-compatible
layer's) own CLI, the fallback for setups without wpctl specifically.
Parses `pactl list sinks`'s per-sink blocks, and `pactl get-default-
sink` (which returns a Name, not a numeric id) to know which one is
default — the two calls are combined in get_sinks() since neither
alone has the full picture.
"""

import re
import subprocess

from tuicc.audio.base import AudioBackend
from tuicc.audio.model import AudioSink

_NAME_RE = re.compile(r"^\tName:\s*(.+)$", re.MULTILINE)
_DESCRIPTION_RE = re.compile(r"^\tDescription:\s*(.+)$", re.MULTILINE)
_MUTE_RE = re.compile(r"^\tMute:\s*(\w+)$", re.MULTILINE)
_VOLUME_RE = re.compile(r"Volume:.*?(\d+)%")


def parse_list_sinks(text: str, default_sink_name: str | None) -> list[AudioSink]:
    """`pactl list sinks`'s full (not `short`) output -> a list of
    AudioSink — `short` doesn't include Mute/Volume, only `list` does.
    default_sink_name is `pactl get-default-sink`'s own output
    (stripped), compared against each block's Name (not its numeric
    #id — pactl's get-default-sink has no numeric-id equivalent) to
    set is_default; None if the caller couldn't determine it (a sink
    still parses fine, just never comes out marked default).
    """
    sinks = []
    # Splits on "Sink #<id>\n" headers, keeping each id as its own
    # capture group — blocks alternates [id, body, id, body, ...],
    # dropping the empty text.split() leaves before the first header.
    parts = re.split(r"^Sink #(\d+)\n", text, flags=re.MULTILINE)[1:]
    for i in range(0, len(parts), 2):
        sink_id, body = parts[i], parts[i + 1]
        name_match = _NAME_RE.search(body)
        description_match = _DESCRIPTION_RE.search(body)
        mute_match = _MUTE_RE.search(body)
        volume_match = _VOLUME_RE.search(body)
        if name_match is None:
            continue
        name = name_match.group(1).strip()
        sinks.append(AudioSink(
            id=sink_id,
            name=description_match.group(1).strip() if description_match else name,
            volume=int(volume_match.group(1)) if volume_match else 0,
            muted=mute_match.group(1) == "yes" if mute_match else False,
            is_default=(default_sink_name is not None and name == default_sink_name),
        ))
    return sinks


class PactlBackend(AudioBackend):
    def get_sinks(self) -> list[AudioSink]:
        """No internal try/except — same reasoning as WpctlBackend's
        own docstring: a pactl failure must propagate to
        status_worker.StatusWorker's poll wrapper, not vanish.
        """
        sinks_result = subprocess.run(["pactl", "list", "sinks"], capture_output=True, text=True, timeout=5)
        default_result = subprocess.run(["pactl", "get-default-sink"], capture_output=True, text=True, timeout=5)
        default_name = default_result.stdout.strip() or None
        return parse_list_sinks(sinks_result.stdout, default_name)

    def set_default_sink(self, sink_id: str) -> None:
        subprocess.run(["pactl", "set-default-sink", sink_id], capture_output=True, text=True, timeout=5)

    def set_volume(self, sink_id: str, percent: int) -> None:
        subprocess.run(["pactl", "set-sink-volume", sink_id, f"{percent}%"], capture_output=True, text=True, timeout=5)

    def set_mute(self, sink_id: str, muted: bool) -> None:
        subprocess.run(
            ["pactl", "set-sink-mute", sink_id, "1" if muted else "0"],
            capture_output=True, text=True, timeout=5,
        )
