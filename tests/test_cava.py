"""Tests for the cava visualizer reader's pure logic (parse_cava_line)
plus the parts of CavaReader that don't need a real cava subprocess —
same split test_media.py uses for mpris.py's D-Bus side: what's tested
here is the pure data-shaping and the synchronous start()-failure path,
not the background thread's actual streaming behavior.
"""

import subprocess

import pytest

import tuicc.media.cava as cava_module
from tuicc.media.cava import CavaReader, parse_cava_line, ASCII_MAX_RANGE


# ---------- parse_cava_line ----------

def test_parse_cava_line_real_fixture():
    # Real shape confirmed live against an actual cava run: semicolon-
    # delimited integers, one per bar.
    assert parse_cava_line("3;7;12;0;16;4") == [3, 7, 12, 0, 16, 4]


def test_parse_cava_line_strips_trailing_newline():
    assert parse_cava_line("1;2;3\n") == [1, 2, 3]


def test_parse_cava_line_empty_line_returns_empty():
    assert parse_cava_line("") == []


def test_parse_cava_line_ignores_trailing_delimiter():
    # cava's bar_delimiter follows every bar including the last one.
    assert parse_cava_line("1;2;3;") == [1, 2, 3]


# ---------- CavaReader.start() — missing binary ----------

def test_start_missing_binary_sets_error_not_exception(monkeypatch, tmp_path):
    monkeypatch.setattr(cava_module, "CONFIG_PATH", tmp_path / "cava.conf")

    def _raise(*args, **kwargs):
        raise FileNotFoundError("no such file")

    monkeypatch.setattr(cava_module.subprocess, "Popen", _raise)

    reader = CavaReader(bars=10)
    reader.start()  # must not raise — cava missing is a real, expected condition

    assert reader.is_running() is False
    assert reader.get_error() is not None
    assert "cava" in reader.get_error().lower()
    assert reader.get_frame() is None


def test_start_missing_binary_does_not_spawn_thread(monkeypatch, tmp_path):
    monkeypatch.setattr(cava_module, "CONFIG_PATH", tmp_path / "cava.conf")
    monkeypatch.setattr(cava_module.subprocess, "Popen", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()))

    reader = CavaReader()
    reader.start()

    assert reader._thread is None


# ---------- CavaReader.stop() — no-op when never started ----------

def test_stop_without_start_does_not_raise():
    reader = CavaReader()
    reader.stop()  # must not raise
    assert reader.is_running() is False


# ---------- CavaReader.start() — no-op if already running ----------

def test_start_is_a_noop_if_already_running(monkeypatch, tmp_path):
    monkeypatch.setattr(cava_module, "CONFIG_PATH", tmp_path / "cava.conf")
    calls = []

    class _FakeProcess:
        def poll(self):
            return None  # still running

    fake = _FakeProcess()

    def _popen(*args, **kwargs):
        calls.append(1)
        return fake

    monkeypatch.setattr(cava_module.subprocess, "Popen", _popen)

    reader = CavaReader()
    reader._process = fake  # pretend it's already running, skip the real thread spin-up
    reader.start()

    assert calls == []  # Popen never called again


# ---------- config file generation ----------

def test_write_config_includes_bars_and_levels(monkeypatch, tmp_path):
    config_path = tmp_path / "cava.conf"
    monkeypatch.setattr(cava_module, "CONFIG_PATH", config_path)

    reader = CavaReader(bars=24)
    reader._write_config()

    content = config_path.read_text()
    assert "bars = 24" in content
    assert f"ascii_max_range = {ASCII_MAX_RANGE}" in content
    assert "method = raw" in content
    assert "data_format = ascii" in content
    assert "channels = mono" in content
