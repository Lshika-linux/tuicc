"""Tests for the audio backends' pure parsing logic — wpctl.py parses
`wpctl status`'s Sinks section, pactl.py parses `pactl list sinks` +
`pactl get-default-sink`. Both fixtures below are adapted from real
output captured live (see status_worker.py's own R5 section in
VISION.md) — a real single-muted-sink system, extended with a second
sink to exercise the multi-sink/non-default cases too.
"""

from tuicc.audio.wpctl import parse_status_sinks
from tuicc.audio.pactl import parse_list_sinks


# ---------- wpctl: parse_status_sinks ----------

WPCTL_STATUS_SINGLE_SINK = """PipeWire 'pipewire-0' [1.6.5, rafi@node1, cookie:9472091]
 └─ Clients:
        29. wpctl                               [1.6.5, rafi@node1, pid:135694]

Audio
 ├─ Devices:
 │      53. Built-in Audio                      [alsa]
 │
 ├─ Sinks:
 │  *   64. Built-in Audio Analog Stereo        [vol: 0.50 MUTED]
 │
 ├─ Sources:
 │  *   65. Built-in Audio Analog Stereo        [vol: 1.00]
 │
 ├─ Filters:
 │
 └─ Streams:

Video
 ├─ Devices:
 │      38. Integrated Camera                   [v4l2]
"""

WPCTL_STATUS_TWO_SINKS = """Audio
 ├─ Devices:
 │      53. Built-in Audio                      [alsa]
 │
 ├─ Sinks:
 │  *   64. Built-in Audio Analog Stereo        [vol: 0.50 MUTED]
 │      70. USB Headphones                      [vol: 0.80]
 │
 ├─ Sources:
 │  *   65. Built-in Audio Analog Stereo        [vol: 1.00]
"""


def test_wpctl_parses_the_default_muted_sink():
    sinks = parse_status_sinks(WPCTL_STATUS_SINGLE_SINK)

    assert len(sinks) == 1
    sink = sinks[0]
    assert sink.id == "64"
    assert sink.name == "Built-in Audio Analog Stereo"
    assert sink.volume == 50
    assert sink.muted is True
    assert sink.is_default is True


def test_wpctl_parses_multiple_sinks_and_ignores_sources():
    sinks = parse_status_sinks(WPCTL_STATUS_TWO_SINKS)

    assert [s.id for s in sinks] == ["64", "70"]


def test_wpctl_non_default_sink_is_not_muted():
    sinks = parse_status_sinks(WPCTL_STATUS_TWO_SINKS)

    headphones = sinks[1]
    assert headphones.name == "USB Headphones"
    assert headphones.volume == 80
    assert headphones.muted is False
    assert headphones.is_default is False


def test_wpctl_no_sinks_section_returns_empty_list():
    assert parse_status_sinks("Audio\n ├─ Devices:\n") == []


def test_wpctl_empty_output_returns_empty_list():
    assert parse_status_sinks("") == []


# ---------- pactl: parse_list_sinks ----------

PACTL_LIST_SINKS_TWO = """Sink #68
\tState: SUSPENDED
\tName: alsa_output.pci-0000_00_1f.3.analog-stereo
\tDescription: Built-in Audio Analog Stereo
\tDriver: PipeWire
\tSample Specification: s32le 2ch 48000Hz
\tMute: yes
\tVolume: front-left: 32760 /  50% / -18.07 dB,   front-right: 32760 /  50% / -18.07 dB
\t        balance 0.00
\tBase Volume: 65536 / 100% / 0.00 dB

Sink #70
\tState: RUNNING
\tName: bluez_output.AA_BB_CC.1
\tDescription: USB Headphones
\tDriver: PipeWire
\tMute: no
\tVolume: front-left: 65536 / 100% / 0.00 dB,   front-right: 65536 / 100% / 0.00 dB
\t        balance 0.00
"""


def test_pactl_parses_both_sinks():
    sinks = parse_list_sinks(PACTL_LIST_SINKS_TWO, default_sink_name="alsa_output.pci-0000_00_1f.3.analog-stereo")

    assert [s.id for s in sinks] == ["68", "70"]


def test_pactl_uses_description_as_the_display_name():
    sinks = parse_list_sinks(PACTL_LIST_SINKS_TWO, default_sink_name=None)

    assert sinks[0].name == "Built-in Audio Analog Stereo"
    assert sinks[1].name == "USB Headphones"


def test_pactl_muted_and_volume_parsed_correctly():
    sinks = parse_list_sinks(PACTL_LIST_SINKS_TWO, default_sink_name=None)

    assert sinks[0].muted is True
    assert sinks[0].volume == 50
    assert sinks[1].muted is False
    assert sinks[1].volume == 100


def test_pactl_is_default_matches_by_name_not_id():
    sinks = parse_list_sinks(PACTL_LIST_SINKS_TWO, default_sink_name="alsa_output.pci-0000_00_1f.3.analog-stereo")

    assert sinks[0].is_default is True
    assert sinks[1].is_default is False


def test_pactl_no_default_name_marks_nothing_default():
    # get-default-sink failed/returned empty — a sink still parses
    # fine, it just never comes out marked default rather than
    # crashing or guessing.
    sinks = parse_list_sinks(PACTL_LIST_SINKS_TWO, default_sink_name=None)

    assert all(not s.is_default for s in sinks)


def test_pactl_empty_output_returns_empty_list():
    assert parse_list_sinks("", default_sink_name=None) == []
