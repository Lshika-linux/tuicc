"""Tests for the MPRIS media backend's pure logic (filter_mpris_names,
parse_metadata) — mpris.py talks to players directly over D-Bus (no CLI
text to parse), so what's tested here is the pure data-shaping logic,
separate from the D-Bus I/O itself, same split test_connectivity.py
uses for iwd.py. Fixtures are real data captured live
(Firefox playing a YouTube tab, org.mpris.MediaPlayer2.firefox — note
mpris:length and mpris:artUrl are genuinely absent, not trimmed for
brevity).
"""

import pytest

import tuicc.media.mpris as mpris_module
from tuicc.media.mpris import MprisBackend, filter_mpris_names, parse_metadata


# ---------- filter_mpris_names ----------

def test_filter_mpris_names_keeps_only_mpris_prefixed():
    names = [
        "org.freedesktop.DBus",
        "org.mpris.MediaPlayer2.firefox.instance_1_416",
        ":1.42",
        "org.mpris.MediaPlayer2.mpv",
    ]
    assert filter_mpris_names(names) == [
        "org.mpris.MediaPlayer2.firefox.instance_1_416",
        "org.mpris.MediaPlayer2.mpv",
    ]


def test_filter_mpris_names_empty_list_returns_empty():
    assert filter_mpris_names([]) == []


def test_filter_mpris_names_no_players_running():
    assert filter_mpris_names(["org.freedesktop.DBus", ":1.1"]) == []


# ---------- parse_metadata ----------
# Real, captured-live fixture: Firefox's own MPRIS Metadata for a
# YouTube tab. mpris:length and mpris:artUrl are absent entirely — not
# every player fills in every field the spec allows.
FIREFOX_METADATA = {
    "mpris:trackid": ("o", "/org/mpris/MediaPlayer2/firefox"),
    "xesam:album": ("s", ""),
    "xesam:artist": ("as", ["낮달"]),
    "xesam:title": ("s", "Lo-Fi for Foxes (Only)"),
    "xesam:url": ("s", "https://www.youtube.com/watch?v=khnr4-ehwKA"),
}


def test_parse_metadata_real_firefox_fixture():
    result = parse_metadata(FIREFOX_METADATA)
    assert result == {
        "title": "Lo-Fi for Foxes (Only)", "artist": "낮달", "album": "",
        "url": "https://www.youtube.com/watch?v=khnr4-ehwKA",
    }


def test_parse_metadata_joins_multiple_artists():
    metadata = {"xesam:artist": ("as", ["Artist One", "Artist Two"])}
    assert parse_metadata(metadata)["artist"] == "Artist One, Artist Two"


def test_parse_metadata_missing_fields_default_to_empty():
    # A player that fills in nothing at all — must not KeyError.
    assert parse_metadata({}) == {"title": "", "artist": "", "album": "", "url": ""}


def test_parse_metadata_empty_artist_list_is_empty_string_not_comma():
    metadata = {"xesam:artist": ("as", [])}
    assert parse_metadata(metadata)["artist"] == ""


# ---------- no-silent-failure: real exceptions must propagate ----------

def test_get_players_propagates_dbus_connection_failure(monkeypatch):
    def _raise(**kwargs):
        raise ConnectionError("SESSION bus unreachable")

    monkeypatch.setattr(mpris_module, "open_dbus_connection", _raise)

    with pytest.raises(ConnectionError):
        MprisBackend().get_players()
