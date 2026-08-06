"""Tests for config.py's ensure_preset_exists() — the copy-if-missing
mechanism that makes ~/.config/tuicc/presets/<N>.toml the live, editable
file, seeded once from the packaged template.
"""

import pytest

import tuicc.config as config_module
from tuicc.config import (
    ensure_preset_exists,
    ensure_all_packaged_presets_exist,
    next_free_preset_number,
    save_new_preset,
    save_layout_to_preset,
    build_layout_from_preset,
    available_preset_numbers,
    set_active_preset,
    set_theme_color,
    set_session_name,
    get_raw_theme_values,
    get_raw_navigation_keys,
    get_raw_power_menu_actions,
    _build_session_names,
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


# ---------- save_layout_to_preset ----------

def test_save_layout_to_preset_overwrites_the_given_number(tmp_path, monkeypatch):
    packaged_dir = tmp_path / "packaged"
    packaged_dir.mkdir()
    user_dir = tmp_path / "user"
    user_dir.mkdir()
    (user_dir / "3.toml").write_text("# old contents\n")

    monkeypatch.setattr(config_module, "PACKAGED_PRESETS_DIR", packaged_dir)
    monkeypatch.setattr(config_module, "USER_PRESETS_DIR", user_dir)

    save_layout_to_preset(_sample_layout(), 3)

    loaded = build_layout_from_preset(3)
    by_name = {box.name: box for box in loaded.boxes}
    assert by_name["sidebar"].w == 0.26
    assert by_name["power_menu"].h == 0.1
    assert not (user_dir / "4.toml").exists()  # no new number minted


def test_save_layout_to_preset_creates_the_file_if_missing(tmp_path, monkeypatch):
    packaged_dir = tmp_path / "packaged"
    packaged_dir.mkdir()
    user_dir = tmp_path / "user"

    monkeypatch.setattr(config_module, "PACKAGED_PRESETS_DIR", packaged_dir)
    monkeypatch.setattr(config_module, "USER_PRESETS_DIR", user_dir)

    save_layout_to_preset(_sample_layout(), 5)

    assert (user_dir / "5.toml").exists()


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


# ---------- ensure_all_packaged_presets_exist ----------

def test_ensure_all_packaged_presets_exist_copies_every_packaged_number(tmp_path, monkeypatch):
    packaged_dir = tmp_path / "packaged"
    packaged_dir.mkdir()
    (packaged_dir / "1.toml").write_text("# preset one\n")
    (packaged_dir / "2.toml").write_text("# preset two\n")

    user_dir = tmp_path / "user"

    monkeypatch.setattr(config_module, "PACKAGED_PRESETS_DIR", packaged_dir)
    monkeypatch.setattr(config_module, "USER_PRESETS_DIR", user_dir)

    ensure_all_packaged_presets_exist()

    assert (user_dir / "1.toml").read_text() == "# preset one\n"
    assert (user_dir / "2.toml").read_text() == "# preset two\n"


def test_ensure_all_packaged_presets_exist_never_overwrites_a_user_file(tmp_path, monkeypatch):
    packaged_dir = tmp_path / "packaged"
    packaged_dir.mkdir()
    (packaged_dir / "1.toml").write_text("# packaged version\n")

    user_dir = tmp_path / "user"
    user_dir.mkdir()
    (user_dir / "1.toml").write_text("# user's own edited version\n")

    monkeypatch.setattr(config_module, "PACKAGED_PRESETS_DIR", packaged_dir)
    monkeypatch.setattr(config_module, "USER_PRESETS_DIR", user_dir)

    ensure_all_packaged_presets_exist()

    assert (user_dir / "1.toml").read_text() == "# user's own edited version\n"


def test_ensure_all_packaged_presets_exist_handles_missing_packaged_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config_module, "PACKAGED_PRESETS_DIR", tmp_path / "does-not-exist")
    monkeypatch.setattr(config_module, "USER_PRESETS_DIR", tmp_path / "user")

    ensure_all_packaged_presets_exist()  # must not raise


# ---------- set_theme_color ----------

def test_set_theme_color_rewrites_only_the_target_role(tmp_path, monkeypatch):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "[layout]\n"
        "preset = 1\n"  # a same-named key outside [theme] — must NOT change
        "\n"
        "[theme]\n"
        "# a hand-written comment that must survive\n"
        "accent = \"cyan\"\n"
        "border = \"white\"\n"
    )
    monkeypatch.setattr(config_module, "USER_CONFIG_PATH", config_path)

    set_theme_color("accent", "magenta")

    result = config_path.read_text()
    assert 'accent = "magenta"\n' in result
    assert 'border = "white"\n' in result  # untouched — different key
    assert "preset = 1\n" in result  # untouched — different section
    assert "# a hand-written comment that must survive\n" in result


def test_set_theme_color_does_not_match_a_role_name_prefix(tmp_path, monkeypatch):
    # "border" and "border_selected" are real sibling roles — editing
    # one must not accidentally match the other via a bare prefix.
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "[theme]\n"
        "border = \"white\"\n"
        "border_selected = \"cyan\"\n"
    )
    monkeypatch.setattr(config_module, "USER_CONFIG_PATH", config_path)

    set_theme_color("border", "red")

    result = config_path.read_text()
    assert 'border = "red"\n' in result
    assert 'border_selected = "cyan"\n' in result


# ---------- get_raw_theme_values ----------

def test_get_raw_theme_values_returns_every_role(tmp_path, monkeypatch):
    config_path = tmp_path / "config.toml"
    config_path.write_text('[theme]\naccent = "cyan"\nborder = [128, 128, 128]\n')
    monkeypatch.setattr(config_module, "USER_CONFIG_PATH", config_path)

    values = get_raw_theme_values()

    assert values["accent"] == "cyan"
    assert values["border"] == [128, 128, 128]


def test_get_raw_theme_values_missing_theme_section_returns_empty_dict(tmp_path, monkeypatch):
    config_path = tmp_path / "config.toml"
    config_path.write_text("[layout]\npreset = 1\n")
    monkeypatch.setattr(config_module, "USER_CONFIG_PATH", config_path)

    assert get_raw_theme_values() == {}


def test_get_raw_theme_values_missing_file_returns_empty_dict(tmp_path, monkeypatch):
    monkeypatch.setattr(config_module, "USER_CONFIG_PATH", tmp_path / "does-not-exist.toml")

    assert get_raw_theme_values() == {}


# ---------- get_raw_navigation_keys ----------

def test_get_raw_navigation_keys_returns_the_raw_strings(tmp_path, monkeypatch):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "[navigation.keys]\n"
        'left = "Left"\n'
        'confirm_yes = "Ctrl+Y"\n'
    )
    monkeypatch.setattr(config_module, "USER_CONFIG_PATH", config_path)

    keys = get_raw_navigation_keys()

    assert keys["left"] == "Left"
    assert keys["confirm_yes"] == "Ctrl+Y"


def test_get_raw_navigation_keys_missing_section_returns_empty_dict(tmp_path, monkeypatch):
    config_path = tmp_path / "config.toml"
    config_path.write_text("[layout]\npreset = 1\n")
    monkeypatch.setattr(config_module, "USER_CONFIG_PATH", config_path)

    assert get_raw_navigation_keys() == {}


# ---------- get_raw_power_menu_actions ----------

def test_get_raw_power_menu_actions_returns_the_raw_entries(tmp_path, monkeypatch):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "[[power_menu.action]]\n"
        'label = "Lock"\n'
        'shortcut = "Ctrl+L"\n'
        'command = "swaylock"\n'
    )
    monkeypatch.setattr(config_module, "USER_CONFIG_PATH", config_path)

    actions = get_raw_power_menu_actions()

    assert actions == [{"label": "Lock", "shortcut": "Ctrl+L", "command": "swaylock"}]


def test_get_raw_power_menu_actions_missing_section_returns_empty_list(tmp_path, monkeypatch):
    config_path = tmp_path / "config.toml"
    config_path.write_text("[layout]\npreset = 1\n")
    monkeypatch.setattr(config_module, "USER_CONFIG_PATH", config_path)

    assert get_raw_power_menu_actions() == []


# ---------- _build_session_names ----------

def test_build_session_names_uses_configured_values():
    user_data = {"sessions": {"name_1": "Work", "name_2": "Gaming", "name_3": "Slot 3"}}

    assert _build_session_names(user_data) == {1: "Work", 2: "Gaming", 3: "Slot 3"}


def test_build_session_names_missing_section_falls_back_to_slot_n():
    # config.toml predating this feature — no [sessions] at all.
    assert _build_session_names({}) == {1: "Slot 1", 2: "Slot 2", 3: "Slot 3"}


def test_build_session_names_missing_individual_key_falls_back_to_slot_n():
    user_data = {"sessions": {"name_1": "Work"}}

    assert _build_session_names(user_data) == {1: "Work", 2: "Slot 2", 3: "Slot 3"}


def test_build_session_names_empty_value_falls_back_to_slot_n():
    # Clearing a rename back to "" (see apply_naming's docstring) must
    # redisplay as the default next load, not a literal blank name.
    user_data = {"sessions": {"name_2": ""}}

    assert _build_session_names(user_data) == {1: "Slot 1", 2: "Slot 2", 3: "Slot 3"}


# ---------- set_session_name ----------

def test_set_session_name_rewrites_only_the_target_slot(tmp_path, monkeypatch):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "[sessions]\n"
        "# a hand-written comment that must survive\n"
        'name_1 = "Slot 1"\n'
        'name_2 = "Slot 2"\n'
    )
    monkeypatch.setattr(config_module, "USER_CONFIG_PATH", config_path)

    set_session_name(2, "Gaming")

    result = config_path.read_text()
    assert 'name_1 = "Slot 1"\n' in result
    assert 'name_2 = "Gaming"\n' in result
    assert "# a hand-written comment that must survive\n" in result


def test_set_session_name_appends_missing_key_within_existing_section(tmp_path, monkeypatch):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "[sessions]\n"
        'name_1 = "Slot 1"\n'
        "\n"
        "[network]\n"
        'wifi_backend = "iwd"\n'
    )
    monkeypatch.setattr(config_module, "USER_CONFIG_PATH", config_path)

    set_session_name(2, "Gaming")

    result = config_path.read_text()
    assert 'name_1 = "Slot 1"\n' in result
    assert 'name_2 = "Gaming"\n' in result
    # Landed inside [sessions], not accidentally inside [network] below it.
    sessions_pos = result.index("[sessions]")
    network_pos = result.index("[network]")
    name_2_pos = result.index('name_2 = "Gaming"')
    assert sessions_pos < name_2_pos < network_pos
    assert 'wifi_backend = "iwd"\n' in result


def test_set_session_name_appends_missing_key_when_section_is_last_in_file(tmp_path, monkeypatch):
    # No subsequent "[" line to bound the insertion point against —
    # section_end must fall back to end-of-file, not lose the line.
    config_path = tmp_path / "config.toml"
    config_path.write_text('[sessions]\nname_1 = "Slot 1"\n')
    monkeypatch.setattr(config_module, "USER_CONFIG_PATH", config_path)

    set_session_name(2, "Gaming")

    assert config_path.read_text() == '[sessions]\nname_1 = "Slot 1"\nname_2 = "Gaming"\n'


def test_set_session_name_appends_a_brand_new_section_when_missing_entirely(tmp_path, monkeypatch):
    # The exact upgrade case set_session_name's docstring calls out —
    # an existing user's config.toml predating [sessions] entirely.
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "[layout]\n"
        "preset = 1\n"
    )
    monkeypatch.setattr(config_module, "USER_CONFIG_PATH", config_path)

    set_session_name(1, "Work")

    result = config_path.read_text()
    assert "[sessions]\n" in result
    assert 'name_1 = "Work"\n' in result
    assert "preset = 1\n" in result  # untouched


def test_set_session_name_can_write_an_empty_value(tmp_path, monkeypatch):
    # Clearing a custom name back to the default (see apply_naming).
    config_path = tmp_path / "config.toml"
    config_path.write_text('[sessions]\nname_1 = "Work"\n')
    monkeypatch.setattr(config_module, "USER_CONFIG_PATH", config_path)

    set_session_name(1, "")

    assert 'name_1 = ""\n' in config_path.read_text()
