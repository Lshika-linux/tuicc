"""Tests for config.py's ensure_preset_exists() — the copy-if-missing
mechanism that makes ~/.config/tuicc/presets/<N>.toml the live, editable
file, seeded once from the packaged template.
"""

import pytest

import tuicc.config as config_module
from tuicc.config import ensure_preset_exists


def test_copies_from_packaged_when_user_preset_missing(tmp_path, monkeypatch):
    packaged_dir = tmp_path / "packaged"
    packaged_dir.mkdir()
    (packaged_dir / "1.toml").write_text("# a built-in preset\n")

    user_dir = tmp_path / "user"

    monkeypatch.setattr(config_module, "PACKAGED_PRESETS_DIR", packaged_dir)
    monkeypatch.setattr(config_module, "USER_PRESETS_DIR", user_dir)

    result_path = ensure_preset_exists(1)

    assert result_path == user_dir / "1.toml"
    assert result_path.exists()
    assert result_path.read_text() == "# a built-in preset\n"


def test_does_not_overwrite_an_existing_user_preset(tmp_path, monkeypatch):
    packaged_dir = tmp_path / "packaged"
    packaged_dir.mkdir()
    (packaged_dir / "1.toml").write_text("# packaged version\n")

    user_dir = tmp_path / "user"
    user_dir.mkdir()
    (user_dir / "1.toml").write_text("# user's own edited version\n")

    monkeypatch.setattr(config_module, "PACKAGED_PRESETS_DIR", packaged_dir)
    monkeypatch.setattr(config_module, "USER_PRESETS_DIR", user_dir)

    result_path = ensure_preset_exists(1)

    # Must return the user's file untouched, not overwrite it with the
    # packaged one — this is what protects a running resize mode's saves
    # (or plain hand edits) from being silently clobbered.
    assert result_path.read_text() == "# user's own edited version\n"


def test_raises_when_neither_user_nor_packaged_preset_exists(tmp_path, monkeypatch):
    packaged_dir = tmp_path / "packaged"
    packaged_dir.mkdir()  # empty — no 99.toml

    user_dir = tmp_path / "user"

    monkeypatch.setattr(config_module, "PACKAGED_PRESETS_DIR", packaged_dir)
    monkeypatch.setattr(config_module, "USER_PRESETS_DIR", user_dir)

    with pytest.raises(FileNotFoundError):
        ensure_preset_exists(99)


def test_creates_user_presets_dir_if_it_does_not_exist_yet(tmp_path, monkeypatch):
    packaged_dir = tmp_path / "packaged"
    packaged_dir.mkdir()
    (packaged_dir / "2.toml").write_text("# preset two\n")

    # user_dir deliberately not created — not even the parent ~/.config/tuicc/presets
    user_dir = tmp_path / "does" / "not" / "exist" / "yet"

    monkeypatch.setattr(config_module, "PACKAGED_PRESETS_DIR", packaged_dir)
    monkeypatch.setattr(config_module, "USER_PRESETS_DIR", user_dir)

    result_path = ensure_preset_exists(2)

    assert result_path.exists()


def test_different_preset_numbers_are_independent(tmp_path, monkeypatch):
    packaged_dir = tmp_path / "packaged"
    packaged_dir.mkdir()
    (packaged_dir / "1.toml").write_text("# preset one\n")
    (packaged_dir / "2.toml").write_text("# preset two\n")

    user_dir = tmp_path / "user"

    monkeypatch.setattr(config_module, "PACKAGED_PRESETS_DIR", packaged_dir)
    monkeypatch.setattr(config_module, "USER_PRESETS_DIR", user_dir)

    path1 = ensure_preset_exists(1)
    path2 = ensure_preset_exists(2)

    assert path1 != path2
    assert path1.read_text() == "# preset one\n"
    assert path2.read_text() == "# preset two\n"
