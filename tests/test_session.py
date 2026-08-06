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
from tuicc.session import (
    _parse_cmdline,
    _parse_environ,
    capture_window,
    capture_session,
    save_session,
    load_session,
)


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


# ---------- _parse_environ ----------

def test_parse_environ_splits_key_value_pairs():
    raw = b"HOME=/home/rafi\x00WAYLAND_DISPLAY=wayland-1\x00"
    assert _parse_environ(raw) == {"HOME": "/home/rafi", "WAYLAND_DISPLAY": "wayland-1"}


def test_parse_environ_empty_bytes_returns_empty_dict():
    assert _parse_environ(b"") == {}


def test_parse_environ_value_containing_equals_sign_kept_whole():
    # partition() splits on the FIRST "=" only — a value that itself
    # contains "=" (common: base64 padding, URLs with query strings)
    # must stay intact, not get truncated at the first equals sign.
    raw = b"SOME_URL=https://x.example/?a=b\x00"
    assert _parse_environ(raw) == {"SOME_URL": "https://x.example/?a=b"}


def test_parse_environ_drops_sensitive_looking_keys():
    raw = (
        b"HOME=/home/rafi\x00"
        b"GITHUB_TOKEN=ghp_abc123\x00"
        b"AWS_SECRET_ACCESS_KEY=xyz\x00"
        b"API_KEY=xyz\x00"
        b"DB_PASSWORD=hunter2\x00"
        b"SSH_AUTH_SOCK=/tmp/ssh.sock\x00"
    )
    assert _parse_environ(raw) == {"HOME": "/home/rafi"}


def test_parse_environ_sensitive_marker_match_is_case_insensitive():
    raw = b"My_Secret_Value=x\x00HOME=/home/rafi\x00"
    assert _parse_environ(raw) == {"HOME": "/home/rafi"}


# ---------- read_environ ----------

def test_read_environ_unreadable_pid_returns_none():
    # No process anywhere is ever really pid 1 owned by this test's
    # UID with a readable /proc/1/environ — same "definitely fails"
    # assumption read_cmdline's own tests would make, if it had one.
    from tuicc.session import read_environ
    assert read_environ(1) is None


# ---------- capture_window ----------

def test_capture_window_uses_window_pid_directly_when_available(monkeypatch):
    monkeypatch.setattr(session_module, "read_cmdline", lambda pid: ["kitty"] if pid == 111 else None)
    # Deterministic regardless of what's actually running under pid 111
    # on the machine running this test — see the "env" tests below for
    # read_environ's own behavior, exercised separately.
    monkeypatch.setattr(session_module, "read_environ", lambda pid: None)
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
    monkeypatch.setattr(session_module, "read_environ", lambda pid: None)
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


def test_capture_window_includes_env_when_readable(monkeypatch):
    monkeypatch.setattr(session_module, "read_cmdline", lambda pid: ["electron", "app.asar"])
    monkeypatch.setattr(session_module, "read_environ", lambda pid: {"NODE_PATH": "/nix/store/.../node_modules"})
    window = _window("1", "obsidian", pid=111)

    entry = capture_window(window, "2", _FakeProvider())

    assert entry["env"] == {"NODE_PATH": "/nix/store/.../node_modules"}


def test_capture_window_omits_env_key_entirely_when_unreadable(monkeypatch):
    # Unlike cmdline, environ being uncapturable doesn't sink the whole
    # entry — cmdline alone is still enough to attempt a relaunch, just
    # without the reliability env gives it (see spawn_detached()'s
    # docstring for the real-world case where that matters).
    monkeypatch.setattr(session_module, "read_cmdline", lambda pid: ["kitty"])
    monkeypatch.setattr(session_module, "read_environ", lambda pid: None)
    window = _window("1", "kitty", pid=111)

    entry = capture_window(window, "5", _FakeProvider())

    assert "env" not in entry


def test_capture_window_floating_includes_geometry(monkeypatch):
    monkeypatch.setattr(session_module, "read_cmdline", lambda pid: ["kitty"])
    monkeypatch.setattr(session_module, "read_environ", lambda pid: None)
    window = _window("1", "kitty", pid=111, floating=True, rect=(0.35, 0.15, 0.3, 0.4))

    entry = capture_window(window, "9", _FakeProvider())

    assert entry["floating"] is True
    assert entry["x"] == 0.35
    assert entry["y"] == 0.15
    assert entry["w"] == 0.3
    assert entry["h"] == 0.4


def test_capture_window_tiled_omits_geometry(monkeypatch):
    monkeypatch.setattr(session_module, "read_cmdline", lambda pid: ["kitty"])
    monkeypatch.setattr(session_module, "read_environ", lambda pid: None)
    window = _window("1", "kitty", pid=111, floating=False)

    entry = capture_window(window, "5", _FakeProvider())

    assert "x" not in entry
    assert "y" not in entry


# ---------- capture_session ----------

def test_capture_session_covers_every_region(monkeypatch):
    monkeypatch.setattr(session_module, "read_cmdline", lambda pid: ["kitty"])
    monkeypatch.setattr(session_module, "read_environ", lambda pid: None)
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