"""Player — the generic shape MPRIS data gets translated into, same
role audio/model.py's AudioSink and connectivity/model.py's
WifiNetwork/BluetoothDevice play for their own domains.
"""

from dataclasses import dataclass


@dataclass
class Player:
    bus_name: str  # e.g. "org.mpris.MediaPlayer2.firefox.instance_1_416"
                    # — the real, unique D-Bus identity; what
                    # play_pause()/next()/previous() actually target.
                    # Two tabs of the same browser, two mpv windows,
                    # etc. each get their own bus_name, never collide.
    identity: str   # human-readable app name (MPRIS's own Identity
                     # property, e.g. "Mozilla firefox") — for display,
                     # never for targeting an action.
    desktop_entry: str  # MPRIS's own DesktopEntry (e.g. "firefox") —
                         # shorter/more uniform than identity, used as
                         # modules/media.py's fallback source label
                         # when url below isn't a real URL to extract a
                         # domain from. "" if the player doesn't set it
                         # (optional per the spec, same as every other
                         # MPRIS field — see mpris.py's own docstring).
    playback_status: str  # "Playing" | "Paused" | "Stopped" — MPRIS's
                            # own three literal values, passed through
                            # verbatim, not re-encoded as a bool.
    title: str
    artist: str  # MPRIS's own xesam:artist is a string LIST (multiple
                  # artists) — already joined with ", " by the time it
                  # gets here (see mpris.py's parse_metadata).
    album: str
    url: str  # MPRIS's own xesam:url, verbatim — modules/media.py
               # extracts a domain out of it (e.g. a browser player's
               # current tab) when it's a real http(s) URL, its own
               # display-policy decision, not this model's.
    can_go_next: bool
    can_go_previous: bool
    can_pause: bool
    can_play: bool
