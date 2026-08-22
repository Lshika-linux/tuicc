"""Tests for rwb.py's pure weather-display logic (_weather_line/
_build_weather_preview) — draw()/nav_items() themselves stay untested
(curses-only, same convention every other module's draw() follows).

The central regression this file exists to guard: a fresh install
running the packaged default config as-is ([weather] commented out)
must not crash. It did — see status_worker.py's own test file for the
underlying KeyError this reproduces at the module level.
"""

from dataclasses import dataclass, field
from types import SimpleNamespace

from tuicc.modules.rwb import _weather_line, _build_weather_preview, required_fh, _weather_configured
from tuicc.weather import WeatherNow, DayForecast


def _cfg(geoclue=False, ip_approx=False, lat=None):
    return SimpleNamespace(weather_geoclue=geoclue, weather_ip_approx=ip_approx, weather_lat=lat)


# ---------- required_fh ----------
# Row 3 is reserved either way now — real weather when configured, an
# opt-in hint (draw()'s own row-3 handling) when not — so required_fh()
# is a plain constant regardless of whether weather is configured.

def test_required_fh_is_constant_without_weather():
    assert required_fh(_cfg()) == 3 + 2


def test_required_fh_is_constant_with_lat_lon_configured():
    assert required_fh(_cfg(lat=50.08)) == 3 + 2


def test_required_fh_is_constant_with_geoclue_configured():
    assert required_fh(_cfg(geoclue=True)) == 3 + 2


def test_required_fh_is_constant_with_ip_approx_configured():
    assert required_fh(_cfg(ip_approx=True)) == 3 + 2


# ---------- _weather_configured ----------

def test_weather_configured_false_by_default():
    assert _weather_configured(_cfg()) is False


def test_weather_configured_true_with_lat():
    assert _weather_configured(_cfg(lat=50.08)) is True


def test_weather_configured_true_with_geoclue():
    assert _weather_configured(_cfg(geoclue=True)) is True


def test_weather_configured_true_with_ip_approx():
    assert _weather_configured(_cfg(ip_approx=True)) is True


class _FakeStatus:
    """Just enough of CombinedStatus's shape to exercise rwb.py's own
    domain_names()-gated calls — registered_domains defaults to empty,
    matching a fresh install where "weather" was never added to
    app_setup.py's Domain list at all (see that file's own conditional
    registration).
    """
    def __init__(self, registered_domains=(), snapshot=None, error=None):
        self._registered = set(registered_domains)
        self._snapshot = snapshot
        self._error = error

    def domain_names(self):
        return self._registered

    def get(self, name):
        return self._snapshot

    def get_error(self, name):
        return self._error


@dataclass
class _FakeConfig:
    weather_city_code: str | None = None


@dataclass
class _FakeCtx:
    theme: dict = field(default_factory=dict)
    status: object = None
    config: object = field(default_factory=_FakeConfig)


def _weather():
    return WeatherNow(
        location_name="Praha", temperature_c=18.0, apparent_temperature_c=16.0,
        humidity_pct=60, wind_speed_kmh=10.0, icon="☀️", description="Clear",
        days=[DayForecast(date="18.08", icon="☀️", high_c=20.0, low_c=10.0)],
    )


# ---------- the actual regression: weather never registered ----------

def test_weather_line_does_not_crash_when_weather_domain_was_never_registered():
    # The exact packaged-default state: [weather] commented out, so
    # app_setup.py never adds a "weather" Domain at all.
    ctx = _FakeCtx(status=_FakeStatus(registered_domains=()))

    text, color = _weather_line(ctx)

    assert (text, color) == (None, 0)


def test_weather_preview_does_not_crash_when_weather_domain_was_never_registered():
    ctx = _FakeCtx(status=_FakeStatus(registered_domains=()))

    assert _build_weather_preview(ctx) is None


def test_weather_line_none_when_status_itself_is_none():
    ctx = _FakeCtx(status=None)

    assert _weather_line(ctx) == (None, 0)


# ---------- weather registered and configured ----------

def test_weather_line_shows_icon_and_temperature_when_configured():
    ctx = _FakeCtx(status=_FakeStatus(registered_domains=("weather",), snapshot=_weather()))

    text, _color = _weather_line(ctx)

    assert "☀️" in text
    assert "18" in text


def test_weather_line_includes_city_code_when_set():
    ctx = _FakeCtx(
        status=_FakeStatus(registered_domains=("weather",), snapshot=_weather()),
        config=_FakeConfig(weather_city_code="PRG"),
    )

    text, _color = _weather_line(ctx)

    assert text.startswith("PRG ")


def test_weather_line_shows_urgent_warning_on_error():
    ctx = _FakeCtx(status=_FakeStatus(registered_domains=("weather",), error="API unreachable"))

    text, _color = _weather_line(ctx)

    assert text == "⚠ unavailable"


def test_weather_preview_shows_detail_lines_when_configured():
    ctx = _FakeCtx(status=_FakeStatus(registered_domains=("weather",), snapshot=_weather()))

    lines = _build_weather_preview(ctx)

    assert lines is not None
    assert any("Praha" in text for text, _color in lines)


def test_weather_preview_shows_error_when_configured_but_erroring():
    ctx = _FakeCtx(status=_FakeStatus(registered_domains=("weather",), error="API unreachable"))

    lines = _build_weather_preview(ctx)

    assert lines is not None
    assert any("API unreachable" in text for text, _color in lines)
