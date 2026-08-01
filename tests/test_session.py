"""Tests for session.py — pure logic (_parse_cmdline, capture_window's
assembly, capture_session's region iteration) via a monkeypatched
read_cmdline, plus a real round-trip for save_session/load_session
(plain temp-file I/O, same category as test_presets.py's real file
tests — unlike read_cmdline's /proc access or spawn_detached's process
spawning, this is safe and deterministic to exercise directly).
"""

from pathlib import Path

import tuicc.session as session_module
from tuicc.model import Window, Region, WMState
from tuicc.session import _parse_cmdline, capture_window, capture_session, save_session, load_session


def _window(id, app_id, pid=None, floating=False, rect=(0, 0, 1, 1)):
    return Window(id=id, app_id=app_id, title="", focused=False, rect=rect, floating=floating, pid=pid)


class _FakeProvider:
    def __init__(self, pid_by_window_id=None):
        self._pid_by_window_id = pid_by_window_id or {}

    def resolve_pid(self, window_id):
        return self._pid_by_window_id.get(window_id)


# ---------- _parse_cmdline ----------

def test_parse_cmdline_splits_on_null_bytes():
    raw = b"kitty\x00--title\x00hello\x00"
    assert _parse_cmdline(raw) == ["kitty", "--title", "hello"]


def test_parse_cmdline_empty_bytes_returns_empty_list():
    assert _parse_cmdline(b"") == []


def test_parse_cmdline_ignores_trailing_empty_part():
    # /proc/.../cmdline is null-terminated, so a naive split leaves one
    # trailing empty string that isn't a real argument.
    assert _parse_cmdline(b"firefox\x00") == ["firefox"]


# ---------- capture_window ----------

def test_capture_window_uses_window_pid_directly_when_available(monkeypatch):
    monkeypatch.setattr(session_module, "read_cmdline", lambda pid: ["kitty"] if pid == 111 else None)
    window = _window("1", "kitty", pid=111)

    entry = capture_window(window, "5", _FakeProvider())

    assert entry == {
        "app_id": "kitty",
        "cmdline": ["kitty"],
        "target_region": "5",
        "floating": False,
    }


def test_capture_window_falls_back_to_provider_resolve_pid(monkeypatch):
    monkeypatch.setattr(session_module, "read_cmdline", lambda pid: ["obsidian"] if pid == 222 else None)
    window = _window("1", "obsidian", pid=None)
    provider = _FakeProvider(pid_by_window_id={"1": 222})

    entry = capture_window(window, "3", provider)

    assert entry["cmdline"] == ["obsidian"]
    assert entry["target_region"] == "3"


def test_capture_window_no_pid_anywhere_returns_none(monkeypatch):
    monkeypatch.setattr(session_module, "read_cmdline", lambda pid: ["should not be reached"])
    window = _window("1", "kitty", pid=None)

    assert capture_window(window, "5", _FakeProvider()) is None


def test_capture_window_cmdline_unreadable_returns_none(monkeypatch):
    monkeypatch.setattr(session_module, "read_cmdline", lambda pid: None)
    window = _window("1", "kitty", pid=111)

    assert capture_window(window, "5", _FakeProvider()) is None


def test_capture_window_floating_includes_geometry(monkeypatch):
    monkeypatch.setattr(session_module, "read_cmdline", lambda pid: ["kitty"])
    window = _window("1", "kitty", pid=111, floating=True, rect=(0.35, 0.15, 0.3, 0.4))

    entry = capture_window(window, "9", _FakeProvider())

    assert entry["floating"] is True
    assert entry["x"] == 0.35
    assert entry["y"] == 0.15
    assert entry["w"] == 0.3
    assert entry["h"] == 0.4


def test_capture_window_tiled_omits_geometry(monkeypatch):
    monkeypatch.setattr(session_module, "read_cmdline", lambda pid: ["kitty"])
    window = _window("1", "kitty", pid=111, floating=False)

    entry = capture_window(window, "5", _FakeProvider())

    assert "x" not in entry
    assert "y" not in entry


# ---------- capture_session ----------

def test_capture_session_covers_every_region(monkeypatch):
    monkeypatch.setattr(session_module, "read_cmdline", lambda pid: ["kitty"])
    state = WMState(regions=[
        Region(id="1", name="1", windows=[_window("a", "kitty", pid=1)]),
        Region(id="2", name="2", windows=[_window("b", "kitty", pid=2)]),
    ])

    entries = capture_session(state, _FakeProvider())

    assert [e["target_region"] for e in entries] == ["1", "2"]


def test_capture_session_skips_uncapturable_windows(monkeypatch):
    monkeypatch.setattr(session_module, "read_cmdline", lambda pid: None)
    state = WMState(regions=[
        Region(id="1", name="1", windows=[_window("a", "kitty", pid=1)]),
    ])

    assert capture_session(state, _FakeProvider()) == []


# ---------- save_session / load_session ----------

def test_save_then_load_round_trips_entries(tmp_path):
    entries = [
        {"app_id": "kitty", "cmdline": ["kitty"], "target_region": "5", "floating": False},
        {"app_id": "kitty", "cmdline": ["kitty"], "target_region": "9", "floating": True,
         "x": 0.35, "y": 0.15, "w": 0.3, "h": 0.4},
    ]
    path = tmp_path / "test_session.toml"

    save_session(entries, path)
    loaded = load_session(path)

    assert loaded == entries


def test_save_session_creates_parent_directory(tmp_path):
    path = tmp_path / "nested" / "session.toml"

    save_session([], path)

    assert path.exists()


def test_load_session_missing_window_key_returns_empty_list(tmp_path):
    path = tmp_path / "empty.toml"
    path.write_text("")

    assert load_session(path) == []