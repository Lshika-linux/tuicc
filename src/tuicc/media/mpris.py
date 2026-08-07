"""MPRIS backend — talks to whatever media players are running over
D-Bus (org.mpris.MediaPlayer2.*, SESSION bus — player-facing, unlike
iwd/BlueZ's SYSTEM bus). Genuinely simpler than iwd, as VISION.md's R5
predicted: plain request-response (GetAll + a handful of no-argument
methods), no stateful station/adapter path to look up first, no
per-network follow-up call.

Every currently-running MPRIS-capable app (Firefox, mpv, Spotify, ...)
registers its OWN bus name (org.mpris.MediaPlayer2.<app>[.instanceN]) —
there's no single "the media player" the way iwd has one Station, so
get_players() enumerates all of them via org.freedesktop.DBus.ListNames
and returns one Player per name. Real, captured-live fixture (this
session, Firefox playing a YouTube tab): Metadata routinely omits
fields other players might fill in (mpris:length, mpris:artUrl not
present at all here) — parse_metadata()'s .get()-with-default handling
throughout is a direct response to that, not defensive guessing.
"""

from jeepney import DBusAddress, new_method_call
from jeepney.io.blocking import open_dbus_connection

from tuicc.media.base import MediaBackend
from tuicc.media.model import Player

BUS_NAME_DBUS = "org.freedesktop.DBus"
MPRIS_PREFIX = "org.mpris.MediaPlayer2."
OBJECT_PATH = "/org/mpris/MediaPlayer2"
IFACE_ROOT = "org.mpris.MediaPlayer2"
IFACE_PLAYER = "org.mpris.MediaPlayer2.Player"


def filter_mpris_names(names: list[str]) -> list[str]:
    """Pure logic half of listing players: given every bus name
    currently on the session bus (org.freedesktop.DBus.ListNames'
    own output), keep only the ones a real MPRIS player registers.
    Testable without any D-Bus connection.
    """
    return [name for name in names if name.startswith(MPRIS_PREFIX)]


def parse_metadata(metadata: dict) -> dict:
    """metadata is Player.Metadata's own unwrapped a{sv} contents —
    {key: (signature, value)} pairs per MPRIS's xesam:* vocabulary.
    Every field is genuinely optional per the spec, and varies live
    between real players (see this module's own docstring) — .get()
    with a safe default throughout, never a KeyError over a field some
    player simply didn't fill in.
    """
    title = metadata.get("xesam:title", (None, ""))[1]
    artist_list = metadata.get("xesam:artist", (None, []))[1]
    artist = ", ".join(artist_list) if artist_list else ""
    album = metadata.get("xesam:album", (None, ""))[1]
    url = metadata.get("xesam:url", (None, ""))[1]
    return {"title": title, "artist": artist, "album": album, "url": url}


def _get_all(connection, bus_name, interface):
    addr = DBusAddress(OBJECT_PATH, bus_name=bus_name, interface="org.freedesktop.DBus.Properties")
    msg = new_method_call(addr, "GetAll", "s", (interface,))
    reply = connection.send_and_get_reply(msg, timeout=5)
    return reply.body[0]


def _call_player_method(connection, bus_name, member):
    addr = DBusAddress(OBJECT_PATH, bus_name=bus_name, interface=IFACE_PLAYER)
    msg = new_method_call(addr, member)
    connection.send_and_get_reply(msg, timeout=5)


def _build_player(connection, bus_name: str) -> Player:
    identity_props = _get_all(connection, bus_name, IFACE_ROOT)
    player_props = _get_all(connection, bus_name, IFACE_PLAYER)
    metadata = parse_metadata(player_props.get("Metadata", (None, {}))[1])
    return Player(
        bus_name=bus_name,
        identity=identity_props.get("Identity", (None, bus_name))[1],
        desktop_entry=identity_props.get("DesktopEntry", (None, ""))[1],
        playback_status=player_props.get("PlaybackStatus", (None, "Stopped"))[1],
        title=metadata["title"],
        artist=metadata["artist"],
        album=metadata["album"],
        url=metadata["url"],
        can_go_next=player_props.get("CanGoNext", (None, False))[1],
        can_go_previous=player_props.get("CanGoPrevious", (None, False))[1],
        can_pause=player_props.get("CanPause", (None, False))[1],
        can_play=player_props.get("CanPlay", (None, False))[1],
    )


class MprisBackend(MediaBackend):
    def get_players(self) -> list[Player]:
        """No internal try/except — same reasoning as every other R3/R5
        backend (audio/, the fixed connectivity/iwd.py and bluez.py): a
        real failure (session bus unreachable, ...) must propagate to
        status_worker.StatusWorker's poll wrapper, not vanish into a
        silent []. A single misbehaving player mid-enumeration (drops
        off the bus between ListNames and its own GetAll, say) DOES
        currently take the whole call down with it rather than just
        being skipped — acceptable for now, same "known, not this
        pass's scope" tier as StatusWorker's own action-error gap; the
        next poll (5s later) recovers cleanly either way.
        """
        connection = open_dbus_connection(bus="SESSION")
        try:
            dbus_addr = DBusAddress("/org/freedesktop/DBus", bus_name=BUS_NAME_DBUS, interface=BUS_NAME_DBUS)
            msg = new_method_call(dbus_addr, "ListNames")
            reply = connection.send_and_get_reply(msg, timeout=5)
            names = reply.body[0]
            # sorted(): ListNames' own order isn't guaranteed stable
            # between calls — modules/media.py keys its expanded-row
            # state by bus_name specifically to be resilient to
            # reordering, but a deterministic order still avoids rows
            # visibly shuffling position between polls for no reason.
            return [_build_player(connection, name) for name in sorted(filter_mpris_names(names))]
        finally:
            connection.close()

    def play_pause(self, bus_name: str) -> None:
        connection = open_dbus_connection(bus="SESSION")
        try:
            _call_player_method(connection, bus_name, "PlayPause")
        finally:
            connection.close()

    def next(self, bus_name: str) -> None:
        connection = open_dbus_connection(bus="SESSION")
        try:
            _call_player_method(connection, bus_name, "Next")
        finally:
            connection.close()

    def previous(self, bus_name: str) -> None:
        connection = open_dbus_connection(bus="SESSION")
        try:
            _call_player_method(connection, bus_name, "Previous")
        finally:
            connection.close()
