"""Weather via Open-Meteo (current conditions + a short daily outlook)
— VISION.md's Context-module test: bounded, read-only, "now + short
outlook, not radar". A single flat module, not a registry-backed
backend package like audio/connectivity — there's exactly one weather
API here, same reasoning as brightness.py's own docstring ("a single
reasonable tool", not several to choose between).

Location resolution is entirely separate from the weather fetch itself
(see resolve_location()'s own docstring for why it's cached, not
re-run every poll) and is always one of three EXPLICIT, mutually
exclusive, opt-in sources — never a silent auto-detect. This was a
real, deliberate values decision, not just an implementation detail:
an auto-detected location that's simply wrong looks exactly as
confident as a correct one (a silent failure, arguably worse than a
visible error), and background-querying your location the way phone
OSes do contradicts tuicc's own "reveals and obeys, doesn't hide or
restrict" identity (VISION.md section 1). See
CLAUDE/NOTES/design-decisions.md#weather-location-sources for the full
reasoning. config.py guarantees, at load time, that at most one source
resolves before this module ever runs — resolve_location() below can
assume exactly one of cfg.weather_lat/weather_geoclue/weather_ip_approx
applies.

No new dependency for the one outbound HTTP call this module makes
(this codebase's first — everything else talks D-Bus or a CLI tool):
stdlib urllib.request/json, matching R4's jeepney-over-dbus-next "don't
add a dependency the stdlib already covers" reasoning in VISION.md.
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from jeepney import DBusAddress, new_method_call
from jeepney.io.blocking import open_dbus_connection

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
# ip-api.com — free, no key. Named and called out explicitly here (and
# in defaults/config.toml's own comment) so ip_approx's mechanism is
# never an undocumented implementation detail the user has to read
# source to discover — see the module docstring above.
IP_API_URL = "http://ip-api.com/json/"

GEOCLUE_BUS = "org.freedesktop.GeoClue2"
# Bounded wait for GeoClue2's Location property to resolve after
# Start() — the same "non-blocking poll with a deadline" idiom this
# codebase already uses for "wait briefly for an async result without
# blocking indefinitely" (control.py's
# _run_detached_detecting_quick_failure's os.waitpid(WNOHANG) loop;
# pending_moves.py's _check_quick_exit, same session). Real WiFi-based
# positioning can take a few seconds; 8s is generous without hanging
# tuicc's own poll cycle indefinitely if GeoClue2 never resolves.
GEOCLUE_WAIT_SECONDS = 8.0

# WMO weather codes — Open-Meteo's own convention (shared with most
# European met services), a small, stable, documented enumeration, not
# something that needs a registry. Unknown code -> ("?", "Unknown"),
# never a crash.
#
# Narrowed to 7 broad categories (not one glyph per WMO code).
#
# Wide/VS16 glyphs, on purpose — see
# CLAUDE/NOTES/design-decisions.md#rwb-wide-character-corruption for
# the full story. Six earlier attempts, including a single-width-only
# icon set, tried to work around a real corruption bug (a stray
# leftover glyph fragment surviving a box's vertical-centering shift
# between frames of different line counts) from the *icon* side. The
# actual root cause turned out to be one level lower: stdscr.instr()
# readback proved curses' own internal screen buffer was already
# correct after every redraw — the defect was the TERMINAL failing to
# honor curses' own default differential-refresh byte stream for that
# specific cell. Fixed for real in main.py, not here:
# stdscr.clearok(True) re-armed every frame forces a full,
# non-differential repaint instead, which a from-scratch isolated
# curses repro script confirmed eliminates the corruption regardless
# of glyph width — meaning single-width icons were never actually
# required, they were only ever masking the symptom.
#
# ☀/☁/❄/⛈ + VS16 (U+FE0F) are the same classic, pre-emoji dingbat-era
# symbols (U+2600 block) everyone already recognizes as sun/cloud/
# snowflake/storm, with VS16 forcing the wide/color emoji presentation
# over the block's default narrow text presentation. ⛅ is width 2 even
# WITHOUT VS16 (an exception to the "U+2600 block = narrow by default"
# pattern the rest of this set relies on — worth re-checking with
# wcwidth() directly, not assumed from the block, before ever adding
# another glyph here). ▥ (fog) stays from the Geometric Shapes block,
# genuinely single-width — no classic dingbat covers "fog" well, and
# nothing about the fix above requires every icon to share one width.
#
# RAIN uses 🌧️ (a supplementary-plane pictograph), not a classic
# dingbat — the very first round of this whole icon saga independently
# flagged this exact glyph (alongside 🌫️/🌦️/🌨️) as rendering missing/
# tofu on the font in use at the time, which is why every icon set
# built afterward through the corruption investigation deliberately
# avoided it (☂️ UMBRELLA stood in instead). Confirmed live, later,
# once the corruption+flicker bug was fully resolved: the user asked
# for "normal rain cloud" over the umbrella and it rendered cleanly —
# meaning that original tofu report doesn't (or no longer does) apply
# here. Font coverage is inherently per-machine/per-font, unlike the
# width-corruption bug above (which was a genuine, universal terminal
# defect) — if a future report ever finds this one showing tofu again
# on a different font, ☂️ is the known-safe fallback that was already
# live-verified working.
_CLEAR = "☀️"
_PARTLY_CLOUDY = "⛅"
_OVERCAST = "☁️"
_FOG = "▥"
_RAIN = "🌧️"
_SNOW = "❄️"
_THUNDERSTORM = "⛈️"

_WMO_ICONS = {
    0: (_CLEAR, "Clear"),
    1: (_CLEAR, "Mainly clear"),
    2: (_PARTLY_CLOUDY, "Partly cloudy"),
    3: (_OVERCAST, "Overcast"),
    45: (_FOG, "Fog"),
    48: (_FOG, "Depositing rime fog"),
    51: (_RAIN, "Light drizzle"),
    53: (_RAIN, "Moderate drizzle"),
    55: (_RAIN, "Dense drizzle"),
    56: (_RAIN, "Freezing drizzle"),
    57: (_RAIN, "Freezing drizzle"),
    61: (_RAIN, "Slight rain"),
    63: (_RAIN, "Moderate rain"),
    65: (_RAIN, "Heavy rain"),
    66: (_RAIN, "Freezing rain"),
    67: (_RAIN, "Freezing rain"),
    71: (_SNOW, "Slight snow"),
    73: (_SNOW, "Moderate snow"),
    75: (_SNOW, "Heavy snow"),
    77: (_SNOW, "Snow grains"),
    80: (_RAIN, "Slight rain showers"),
    81: (_RAIN, "Moderate rain showers"),
    82: (_RAIN, "Violent rain showers"),
    85: (_SNOW, "Slight snow showers"),
    86: (_SNOW, "Heavy snow showers"),
    95: (_THUNDERSTORM, "Thunderstorm"),
    96: (_THUNDERSTORM, "Thunderstorm with hail"),
    99: (_THUNDERSTORM, "Thunderstorm with hail"),
}


def wmo_code_to_icon(code: int) -> tuple[str, str]:
    return _WMO_ICONS.get(code, ("?", "Unknown"))


@dataclass
class DayForecast:
    date: str  # "DD.MM", parsed straight from Open-Meteo's own ISO date
    icon: str
    high_c: float
    low_c: float


@dataclass
class WeatherNow:
    location_name: str
    temperature_c: float
    apparent_temperature_c: float
    humidity_pct: int
    wind_speed_kmh: float
    icon: str
    description: str
    # Today first, then as many further days as fetch_forecast() asked
    # for (3 total, matching the reference layout this was designed
    # against) — never assume a fixed length past index 0.
    days: list[DayForecast]


def _format_day(iso_date: str) -> str:
    # "2026-08-15" -> "15.08" — a plain string split, not strptime:
    # Open-Meteo's own date format is always this exact ISO shape.
    year, month, day = iso_date.split("-")
    return f"{day}.{month}"


def parse_forecast(data: dict, location_name: str) -> WeatherNow:
    """Pure — turns Open-Meteo's raw JSON into WeatherNow. Raises
    ValueError on the API's own real error shape (confirmed live:
    {"error": true, "reason": "Latitude must be in range of -90 to
    90°. Given: 999.0."}) rather than trying to parse a response body
    that was never actually the forecast it looks like.
    """
    if data.get("error"):
        raise ValueError(data.get("reason", "Open-Meteo returned an error"))
    current = data["current"]
    daily = data["daily"]
    icon, desc = wmo_code_to_icon(current["weather_code"])
    days = []
    for i in range(len(daily["time"])):
        day_icon, _ = wmo_code_to_icon(daily["weather_code"][i])
        days.append(DayForecast(
            date=_format_day(daily["time"][i]),
            icon=day_icon,
            high_c=daily["temperature_2m_max"][i],
            low_c=daily["temperature_2m_min"][i],
        ))
    return WeatherNow(
        location_name=location_name,
        temperature_c=current["temperature_2m"],
        apparent_temperature_c=current["apparent_temperature"],
        humidity_pct=current["relative_humidity_2m"],
        wind_speed_kmh=current["wind_speed_10m"],
        icon=icon,
        description=desc,
        days=days,
    )


def fetch_forecast(lat: float, lon: float) -> dict:
    """The one HTTP call this module makes. Raises
    urllib.error.URLError/HTTPError or json.JSONDecodeError on
    failure — let it propagate to StatusWorker's poll wrapper, same as
    brightness.get_percent() lets subprocess failures propagate.
    """
    params = urllib.parse.urlencode({
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m,wind_direction_10m",
        "daily": "temperature_2m_max,temperature_2m_min,weather_code",
        "timezone": "auto",
        "forecast_days": 3,
    })
    with urllib.request.urlopen(f"{OPEN_METEO_URL}?{params}", timeout=10) as resp:
        return json.loads(resp.read())


def resolve_from_ip() -> tuple[float, float, str | None]:
    """ip_approx's entire mechanism — see the module docstring for why
    this is its own named, documented function rather than leaning on
    some weather API's own undocumented internal IP-sniffing. Raises
    on ip-api.com's own real failure shape ({"status": "fail",
    "message": "..."}).
    """
    with urllib.request.urlopen(IP_API_URL, timeout=5) as resp:
        data = json.loads(resp.read())
    if data.get("status") != "success":
        raise ValueError(data.get("message", "ip-api.com lookup failed"))
    return data["lat"], data["lon"], data.get("city")


def _dbus_call(connection, bus_name, path, interface, member, signature="", body=()):
    """Mirrors connectivity/util.py's own dbus_call() (same shape, same
    reasoning) — duplicated rather than imported so weather.py stays a
    fully self-contained flat module, not reaching into a package named
    and scoped for connectivity backends specifically.
    """
    addr = DBusAddress(path, bus_name=bus_name, interface=interface)
    msg = new_method_call(addr, member, signature, body)
    reply = connection.send_and_get_reply(msg, timeout=5)
    return reply.body


def resolve_from_geoclue() -> tuple[float, float, str | None]:
    """GeoClue2 over D-Bus, SYSTEM bus (like iwd.py/bluez.py, not
    SESSION like mpris.py). Genuinely stateful, unlike mpris's flat
    GetAll: Manager.GetClient() -> set DesktopId (some GeoClue2 setups
    enforce a polkit permission check keyed on this; harmless to always
    set) -> Client.Start() -> bounded-wait poll of the Client's own
    Location property (GEOCLUE_WAIT_SECONDS) -> read Latitude/Longitude
    off the resolved Location object -> Client.Stop(). If geoclue2
    isn't installed/running/configured, the initial D-Bus call raises —
    let it propagate, same "no silent guess" precedent as the
    gammastep/Night Light example already in defaults/config.toml (a
    different GeoClue2-touching feature, same philosophy: fail loud,
    don't guess). Not live-verified end-to-end this session (this
    machine's own GeoClue2 setup status is unknown) — implemented
    against the documented D-Bus API; treat as a known gap until
    confirmed live on a machine with GeoClue2 actually configured.
    """
    client_iface = "org.freedesktop.GeoClue2.Client"
    connection = open_dbus_connection(bus="SYSTEM")
    try:
        (client_path,) = _dbus_call(
            connection, GEOCLUE_BUS, "/org/freedesktop/GeoClue2/Manager",
            "org.freedesktop.GeoClue2.Manager", "GetClient",
        )
        _dbus_call(
            connection, GEOCLUE_BUS, client_path,
            "org.freedesktop.DBus.Properties", "Set", "ssv",
            (client_iface, "DesktopId", ("s", "tuicc")),
        )
        _dbus_call(connection, GEOCLUE_BUS, client_path, client_iface, "Start")
        location_path = None
        deadline = time.monotonic() + GEOCLUE_WAIT_SECONDS
        while time.monotonic() < deadline:
            (props,) = _dbus_call(
                connection, GEOCLUE_BUS, client_path,
                "org.freedesktop.DBus.Properties", "GetAll", "s", (client_iface,),
            )
            location = props.get("Location", (None, "/"))[1]
            if location != "/":
                location_path = location
                break
            time.sleep(0.2)
        if location_path is None:
            raise RuntimeError(f"GeoClue2 did not resolve a location within {GEOCLUE_WAIT_SECONDS}s")
        (loc_props,) = _dbus_call(
            connection, GEOCLUE_BUS, location_path,
            "org.freedesktop.DBus.Properties", "GetAll", "s",
            ("org.freedesktop.GeoClue2.Location",),
        )
        lat = loc_props["Latitude"][1]
        lon = loc_props["Longitude"][1]
        _dbus_call(connection, GEOCLUE_BUS, client_path, client_iface, "Stop")
        return lat, lon, None
    finally:
        connection.close()


_location_cache: tuple[float, float, str | None] | None = None


def resolve_location(cfg) -> tuple[float, float, str | None]:
    """Resolves once per process and caches — deliberately NOT re-run
    on every StatusWorker poll tick (get_conditions() below only
    re-fetches the forecast on that cadence, never the location).
    Location doesn't change mid-session for a stationary machine, and
    re-querying GeoClue2/ip-api.com every 15 minutes would itself be
    exactly the kind of background-sniffing tuicc's own identity
    rejects (see the module docstring) — asking once per session is
    categorically different from asking repeatedly in the background.
    Precedence (config.py already guarantees exactly one branch below
    applies by load time): explicit lat/lon > geoclue > ip_approx.
    """
    global _location_cache
    if _location_cache is not None:
        return _location_cache
    if cfg.weather_lat is not None and cfg.weather_lon is not None:
        _location_cache = (cfg.weather_lat, cfg.weather_lon, cfg.weather_name)
    elif cfg.weather_geoclue:
        lat, lon, name = resolve_from_geoclue()
        _location_cache = (lat, lon, cfg.weather_name or name)
    else:
        lat, lon, name = resolve_from_ip()
        _location_cache = (lat, lon, cfg.weather_name or name)
    return _location_cache


def get_conditions(cfg) -> WeatherNow:
    """The StatusWorker Domain.poll target (wired via a closure over
    cfg in app_setup.py, matching how control.toggle domains already
    close over their own per-toggle config values) — called on
    cfg's own poll_interval (900s/15min, proposed in app_setup.py).
    Location resolves once (cached above); the forecast itself
    re-fetches every call.
    """
    lat, lon, name = resolve_location(cfg)
    data = fetch_forecast(lat, lon)
    return parse_forecast(data, name or f"{lat:.2f}, {lon:.2f}")
