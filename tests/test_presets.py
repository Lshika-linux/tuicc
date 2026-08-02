"""Tests for config.py's ensure_preset_exists() — the copy-if-missing
mechanism that makes ~/.config/tuicc/presets/<N>.toml the live, editable
file, seeded once from the packaged template.
"""

import pytest

import tuicc.config as config_module
from tuicc.config import (
    ensure_preset_exists,
    next_free_preset_number,
    save_new_preset,
    build_layout_from_preset,
    available_preset_numbers,
    set_active_preset,
)
from tuicc.layout import Layout, ModuleBox


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


# ---------- next_free_preset_number ----------

def test_next_free_preset_number_with_no_presets_is_1(tmp_path, monkeypatch):
    monkeypatch.setattr(config_module, "PACKAGED_PRESETS_DIR", tmp_path / "packaged")
    monkeypatch.setattr(config_module, "USER_PRESETS_DIR", tmp_path / "user")

    assert next_free_preset_number() == 1


def test_next_free_preset_number_is_one_past_the_highest_seen(tmp_path, monkeypatch):
    packaged_dir = tmp_path / "packaged"
    packaged_dir.mkdir()
    (packaged_dir / "1.toml").write_text("")

    user_dir = tmp_path / "user"
    user_dir.mkdir()
    (user_dir / "3.toml").write_text("")

    monkeypatch.setattr(config_module, "PACKAGED_PRESETS_DIR", packaged_dir)
    monkeypatch.setattr(config_module, "USER_PRESETS_DIR", user_dir)

    # Highest seen is 3 (in the user dir), even though packaged only has 1 —
    # both dirs are checked so a save never picks a number a not-yet-copied
    # packaged preset would later claim.
    assert next_free_preset_number() == 4


# ---------- save_new_preset ----------

def _sample_layout():
    return Layout(boxes=[
        ModuleBox(name="sidebar", x=0.0, y=0.0, w=0.26, h=0.6),
        ModuleBox(name="power_menu", x=0.0, y=0.9, w=0.26, h=0.1),
    ])


def test_save_new_preset_writes_a_readable_file(tmp_path, monkeypatch):
    packaged_dir = tmp_path / "packaged"
    packaged_dir.mkdir()
    user_dir = tmp_path / "user"

    monkeypatch.setattr(config_module, "PACKAGED_PRESETS_DIR", packaged_dir)
    monkeypatch.setattr(config_module, "USER_PRESETS_DIR", user_dir)

    preset_number = save_new_preset(_sample_layout())

    assert preset_number == 1
    loaded = build_layout_from_preset(1)
    by_name = {box.name: box for box in loaded.boxes}
    assert by_name["sidebar"].w == 0.26
    assert by_name["power_menu"].y == 0.9
    assert by_name["power_menu"].h == 0.1


def test_save_new_preset_never_overwrites_an_existing_file(tmp_path, monkeypatch):
    packaged_dir = tmp_path / "packaged"
    packaged_dir.mkdir()
    user_dir = tmp_path / "user"
    user_dir.mkdir()
    (user_dir / "1.toml").write_text("# hand-authored, must survive\n")

    monkeypatch.setattr(config_module, "PACKAGED_PRESETS_DIR", packaged_dir)
    monkeypatch.setattr(config_module, "USER_PRESETS_DIR", user_dir)

    preset_number = save_new_preset(_sample_layout())

    assert preset_number == 2
    assert (user_dir / "1.toml").read_text() == "# hand-authored, must survive\n"
    assert (user_dir / "2.toml").exists()


# ---------- available_preset_numbers ----------

def test_available_preset_numbers_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(config_module, "PACKAGED_PRESETS_DIR", tmp_path / "packaged")
    monkeypatch.setattr(config_module, "USER_PRESETS_DIR", tmp_path / "user")

    assert available_preset_numbers() == []


def test_available_preset_numbers_dedups_across_both_dirs(tmp_path, monkeypatch):
    packaged_dir = tmp_path / "packaged"
    packaged_dir.mkdir()
    (packaged_dir / "1.toml").write_text("")
    (packaged_dir / "2.toml").write_text("")

    user_dir = tmp_path / "user"
    user_dir.mkdir()
    (user_dir / "1.toml").write_text("")  # already copied from packaged
    (user_dir / "5.toml").write_text("")  # a resize-mode save

    monkeypatch.setattr(config_module, "PACKAGED_PRESETS_DIR", packaged_dir)
    monkeypatch.setattr(config_module, "USER_PRESETS_DIR", user_dir)

    assert available_preset_numbers() == [1, 2, 5]


# ---------- set_active_preset ----------

def test_set_active_preset_rewrites_only_the_preset_line(tmp_path, monkeypatch):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "[layout]\n"
        "# a hand-written comment that must survive\n"
        "preset = 1\n"
        "\n"
        "[theme]\n"
        "preset = 99\n"  # a different section's own "preset" key — must NOT change
        "accent = \"blue\"\n"
    )
    monkeypatch.setattr(config_module, "USER_CONFIG_PATH", config_path)

    set_active_preset(7)

    result = config_path.read_text()
    assert "preset = 7\n" in result
    assert "# a hand-written comment that must survive\n" in result
    assert "preset = 99\n" in result  # untouched — outside [layout]
    assert "accent = \"blue\"\n" in result
