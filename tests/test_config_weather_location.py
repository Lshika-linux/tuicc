"""Tests for [weather]'s fourth location source, location_file (GitHub
issue #10) — see design-decisions.md#weather-location-sources and
_build_weather_config()'s own docstring for the full reasoning.

Same "real Config, built from the actual shipped defaults/config.toml,
only USER_CONFIG_PATH/USER_PRESETS_DIR redirected" pattern
test_fresh_install_smoke.py's own _load_packaged_default_config() and
test_config_workspace_mode.py already established. [weather] itself
ships entirely commented out in the packaged default, so these append
a real section rather than replacing a live line.
"""

import pytest

from tuicc import config as config_module


def _load_config_with_weather(tmp_path, monkeypatch, weather_toml):
    text = config_module.DEFAULT_CONFIG_PATH.read_text()
    text += "\n[weather]\n" + weather_toml + "\n"

    user_config = tmp_path / "config.toml"
    user_config.write_text(text)
    monkeypatch.setattr(config_module, "USER_CONFIG_PATH", user_config)
    monkeypatch.setattr(config_module, "USER_PRESETS_DIR", tmp_path / "presets")
    return config_module.load_config()


def test_location_file_resolves_lat_lon(tmp_path, monkeypatch):
    location_file = tmp_path / "location.toml"
    location_file.write_text('lat = 50.0755\nlon = 14.4378\n')

    cfg = _load_config_with_weather(
        tmp_path, monkeypatch, f'location_file = "{location_file}"\n',
    )
    assert cfg.weather_lat == 50.0755
    assert cfg.weather_lon == 14.4378


def test_location_file_expands_tilde(tmp_path, monkeypatch):
    # ~/.config/tuicc isn't writable in a test sandbox, but the
    # expansion itself is what's being checked here — point it at a
    # real HOME redirected into tmp_path instead of the real one.
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    (fake_home / "location.toml").write_text('lat = 1.0\nlon = 2.0\n')
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setattr(config_module.Path, "home", classmethod(lambda cls: fake_home))

    cfg = _load_config_with_weather(
        tmp_path, monkeypatch, 'location_file = "~/location.toml"\n',
    )
    assert (cfg.weather_lat, cfg.weather_lon) == (1.0, 2.0)


def test_location_file_missing_raises(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="does not exist"):
        _load_config_with_weather(
            tmp_path, monkeypatch, f'location_file = "{tmp_path / "nope.toml"}"\n',
        )


def test_location_file_missing_lat_lon_raises(tmp_path, monkeypatch):
    location_file = tmp_path / "location.toml"
    location_file.write_text('lat = 50.0755\n')  # lon missing

    with pytest.raises(ValueError, match="must set both lat and lon"):
        _load_config_with_weather(
            tmp_path, monkeypatch, f'location_file = "{location_file}"\n',
        )


def test_location_file_combined_with_inline_lat_lon_raises(tmp_path, monkeypatch):
    location_file = tmp_path / "location.toml"
    location_file.write_text('lat = 50.0755\nlon = 14.4378\n')

    with pytest.raises(ValueError, match="more than one location source"):
        _load_config_with_weather(
            tmp_path, monkeypatch,
            f'location_file = "{location_file}"\nlat = 1.0\nlon = 2.0\n',
        )


def test_location_file_combined_with_geoclue_raises(tmp_path, monkeypatch):
    # The real gap this closes: not just location_file vs lat/lon, but
    # any two of the four sources set together — the original three
    # (lat/lon, geoclue, ip_approx) never actually enforced this
    # against each other either, despite design-decisions.md already
    # documenting it as the intent.
    location_file = tmp_path / "location.toml"
    location_file.write_text('lat = 50.0755\nlon = 14.4378\n')

    with pytest.raises(ValueError, match="more than one location source"):
        _load_config_with_weather(
            tmp_path, monkeypatch,
            f'location_file = "{location_file}"\ngeoclue = true\n',
        )


def test_lat_lon_combined_with_ip_approx_raises(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="more than one location source"):
        _load_config_with_weather(
            tmp_path, monkeypatch, 'lat = 1.0\nlon = 2.0\nip_approx = true\n',
        )
