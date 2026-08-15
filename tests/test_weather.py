"""Tests for weather.py. Pure functions (wmo_code_to_icon, parse_forecast,
the parsing half of resolve_from_ip) tested against real, live-captured
fixtures — not hand-typed guesses, same convention as every provider
fixture in this repo. urllib.request.urlopen is monkeypatched for
resolve_from_ip's HTTP half and resolve_location's caching behavior;
fetch_forecast's and resolve_from_geoclue's actual I/O are not
independently covered here (see CLAUDE/NOTES/design-decisions.md
#weather-location-sources's own note on this accepted gap — same
untestable-without-live-I/O category as app_setup.py/frame_update.py).
"""

import json
from pathlib import Path

import pytest

import tuicc.weather as weather_module
from tuicc.weather import (
    wmo_code_to_icon,
    parse_forecast,
    resolve_from_ip,
    resolve_location,
    get_conditions,
    WeatherNow,
    DayForecast,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _reset_location_cache():
    # weather.py's module-level _location_cache is deliberately
    # process-lifetime state (see resolve_location()'s own docstring
    # for why) — reset between tests so one test's resolution doesn't
    # leak into the next.
    weather_module._location_cache = None
    yield
    weather_module._location_cache = None


class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _fake_urlopen(body: bytes):
    return lambda url, timeout=None: _FakeResponse(body)


# ---------- wmo_code_to_icon ----------
# Classic pre-emoji dingbats (☀/☁/❄/⛈) + VS16 forcing wide/emoji
# presentation, one supplementary-plane pictograph (🌧️, live-confirmed
# not to tofu on the user's real font), and a single-width Geometric
# Shapes glyph (▥) already proven safe elsewhere in this codebase. The
# corruption these were once narrowed to avoid is fixed at its real
# root cause now (main.py's stdscr.redrawln(), scoped to the shared
# preview box's own rows) — not by glyph width, which was never
# actually the fix, only a symptom mask. See
# CLAUDE/NOTES/design-decisions.md#rwb-wide-character-corruption for
# the full story.

def test_wmo_code_clear():
    assert wmo_code_to_icon(0) == ("☀️", "Clear")


def test_wmo_code_partly_cloudy():
    assert wmo_code_to_icon(2) == ("⛅", "Partly cloudy")


def test_wmo_code_rain():
    assert wmo_code_to_icon(63) == ("🌧️", "Moderate rain")


def test_wmo_code_snow():
    assert wmo_code_to_icon(75) == ("❄️", "Heavy snow")


def test_wmo_code_thunderstorm():
    assert wmo_code_to_icon(95) == ("⛈️", "Thunderstorm")


def test_wmo_code_unknown_does_not_crash():
    assert wmo_code_to_icon(12345) == ("?", "Unknown")


# ---------- parse_forecast ----------

def test_parse_forecast_real_fixture():
    # tests/fixtures/openmeteo_sample.json — captured live this
    # session against https://api.open-meteo.com/v1/forecast for
    # Prague (lat=50.0755, lon=14.4378), forecast_days=3. Literal
    # values, not recomputed math (CONTRIBUTING.md's own "writing good
    # tests" rule).
    data = json.loads((FIXTURES / "openmeteo_sample.json").read_text())

    result = parse_forecast(data, "Praha")

    assert result == WeatherNow(
        location_name="Praha",
        temperature_c=20.5,
        apparent_temperature_c=19.3,
        humidity_pct=41,
        wind_speed_kmh=3.2,
        icon="☀️",
        description="Clear",
        days=[
            DayForecast(date="15.08", icon="☀️", high_c=34.1, low_c=17.8),
            DayForecast(date="16.08", icon="🌧️", high_c=32.1, low_c=19.5),
            DayForecast(date="17.08", icon="⛈️", high_c=22.5, low_c=17.7),
        ],
    )


def test_parse_forecast_real_error_fixture_raises():
    # tests/fixtures/openmeteo_error_sample.json — captured live this
    # session with an out-of-range latitude (999).
    data = json.loads((FIXTURES / "openmeteo_error_sample.json").read_text())

    with pytest.raises(ValueError, match="Latitude must be in range"):
        parse_forecast(data, "Praha")


def test_parse_forecast_single_day_has_no_crash_and_one_day_entry():
    data = {
        "current": {
            "temperature_2m": 10.0, "relative_humidity_2m": 50,
            "apparent_temperature": 9.0, "weather_code": 0,
            "wind_speed_10m": 1.0,
        },
        "daily": {
            "time": ["2026-08-15"],
            "temperature_2m_max": [15.0], "temperature_2m_min": [5.0],
            "weather_code": [0],
        },
    }

    result = parse_forecast(data, "Nowhere")

    assert result.days == [DayForecast(date="15.08", icon="☀️", high_c=15.0, low_c=5.0)]


# ---------- resolve_from_ip ----------

def test_resolve_from_ip_real_fixture(monkeypatch):
    # tests/fixtures/ipapi_sample.json — captured live this session
    # against http://ip-api.com/json/.
    body = (FIXTURES / "ipapi_sample.json").read_bytes()
    monkeypatch.setattr(weather_module.urllib.request, "urlopen", _fake_urlopen(body))

    lat, lon, city = resolve_from_ip()

    assert (lat, lon, city) == (50.0687, 14.3947, "Prague")


def test_resolve_from_ip_real_fail_fixture_raises(monkeypatch):
    # tests/fixtures/ipapi_error_sample.json — captured live this
    # session querying a private-range IP (192.168.1.1).
    body = (FIXTURES / "ipapi_error_sample.json").read_bytes()
    monkeypatch.setattr(weather_module.urllib.request, "urlopen", _fake_urlopen(body))

    with pytest.raises(ValueError, match="private range"):
        resolve_from_ip()


# ---------- resolve_location: caching + precedence ----------

class _FakeCfg:
    def __init__(self, lat=None, lon=None, name=None, geoclue=False, ip_approx=False):
        self.weather_lat = lat
        self.weather_lon = lon
        self.weather_name = name
        self.weather_geoclue = geoclue
        self.weather_ip_approx = ip_approx


def test_resolve_location_explicit_lat_lon_wins():
    cfg = _FakeCfg(lat=50.0, lon=14.0, name="Home")

    assert resolve_location(cfg) == (50.0, 14.0, "Home")


def test_resolve_location_ip_approx_path(monkeypatch):
    monkeypatch.setattr(weather_module, "resolve_from_ip", lambda: (1.0, 2.0, "Somewhere"))
    cfg = _FakeCfg(ip_approx=True)

    assert resolve_location(cfg) == (1.0, 2.0, "Somewhere")


def test_resolve_location_name_override_wins_over_resolved_name(monkeypatch):
    monkeypatch.setattr(weather_module, "resolve_from_ip", lambda: (1.0, 2.0, "Somewhere"))
    cfg = _FakeCfg(ip_approx=True, name="My Spot")

    assert resolve_location(cfg) == (1.0, 2.0, "My Spot")


def test_resolve_location_caches_and_does_not_re_resolve(monkeypatch):
    calls = []
    monkeypatch.setattr(weather_module, "resolve_from_ip", lambda: calls.append(1) or (1.0, 2.0, "X"))
    cfg = _FakeCfg(ip_approx=True)

    resolve_location(cfg)
    resolve_location(cfg)
    resolve_location(cfg)

    assert len(calls) == 1  # only the first call actually resolved


def test_resolve_location_geoclue_path(monkeypatch):
    monkeypatch.setattr(weather_module, "resolve_from_geoclue", lambda: (48.0, 16.0, None))
    cfg = _FakeCfg(geoclue=True)

    assert resolve_location(cfg) == (48.0, 16.0, None)


# ---------- get_conditions ----------

def test_get_conditions_wires_location_and_forecast_together(monkeypatch):
    monkeypatch.setattr(weather_module, "resolve_from_ip", lambda: (1.0, 2.0, "Somewhere"))
    monkeypatch.setattr(
        weather_module, "fetch_forecast",
        lambda lat, lon: json.loads((FIXTURES / "openmeteo_sample.json").read_text()),
    )
    cfg = _FakeCfg(ip_approx=True)

    result = get_conditions(cfg)

    assert result.location_name == "Somewhere"
    assert result.temperature_c == 20.5


def test_get_conditions_falls_back_to_coordinates_when_no_name(monkeypatch):
    data = json.loads((FIXTURES / "openmeteo_sample.json").read_text())
    monkeypatch.setattr(weather_module, "fetch_forecast", lambda lat, lon: data)
    cfg = _FakeCfg(lat=50.0, lon=14.0)  # no name set, resolve_from_* not needed (explicit lat/lon)

    result = get_conditions(cfg)

    assert result.location_name == "50.00, 14.00"
