"""Tests for config.py's ensure_preset_exists() — the copy-if-missing
mechanism that makes ~/.config/tuicc/presets/<N>.toml the live, editable
file, seeded once from the packaged template.
"""

import tomllib

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
    pick_preset_for_size,
    set_active_preset,
    set_theme_color,
    set_session_name,
    get_raw_theme_values,
    get_raw_navigation_keys,
    get_raw_power_menu_actions,
    _build_session_names,
    _build_control_toggles,
    _build_weather_config,
    _build_sysmon_blocks,
    DEFAULT_SYSMON_BLOCKS,
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


# ---------- pick_preset_for_size ----------
# Same pixel resolution across two machines does not mean the same
# terminal cell grid (font size/DPI/output scale) — see
# CLAUDE/NOTES/design-decisions.md#preset-auto-select-by-size.

def test_pick_preset_for_size_none_when_no_preset_declares_thresholds(tmp_path, monkeypatch):
    packaged_dir = tmp_path / "packaged"
    packaged_dir.mkdir()
    (packaged_dir / "1.toml").write_text("[[box]]\nname = \"a\"\n")

    monkeypatch.setattr(config_module, "PACKAGED_PRESETS_DIR", packaged_dir)
    monkeypatch.setattr(config_module, "USER_PRESETS_DIR", tmp_path / "user")

    assert pick_preset_for_size(200, 50) is None


def test_pick_preset_for_size_returns_the_one_fitting_preset(tmp_path, monkeypatch):
    packaged_dir = tmp_path / "packaged"
    packaged_dir.mkdir()
    (packaged_dir / "1.toml").write_text("[preset]\nmin_cols = 200\nmin_rows = 50\n")

    monkeypatch.setattr(config_module, "PACKAGED_PRESETS_DIR", packaged_dir)
    monkeypatch.setattr(config_module, "USER_PRESETS_DIR", tmp_path / "user")

    assert pick_preset_for_size(220, 55) == 1


def test_pick_preset_for_size_excludes_a_preset_too_big_for_the_grid(tmp_path, monkeypatch):
    packaged_dir = tmp_path / "packaged"
    packaged_dir.mkdir()
    (packaged_dir / "1.toml").write_text("[preset]\nmin_cols = 200\nmin_rows = 50\n")

    monkeypatch.setattr(config_module, "PACKAGED_PRESETS_DIR", packaged_dir)
    monkeypatch.setattr(config_module, "USER_PRESETS_DIR", tmp_path / "user")

    # Grid is narrower than this preset's own min_cols — doesn't fit.
    assert pick_preset_for_size(150, 50) is None


def test_pick_preset_for_size_picks_the_best_fit_not_just_any_fit(tmp_path, monkeypatch):
    packaged_dir = tmp_path / "packaged"
    packaged_dir.mkdir()
    (packaged_dir / "1.toml").write_text("[preset]\nmin_cols = 100\nmin_rows = 30\n")
    (packaged_dir / "2.toml").write_text("[preset]\nmin_cols = 200\nmin_rows = 50\n")

    monkeypatch.setattr(config_module, "PACKAGED_PRESETS_DIR", packaged_dir)
    monkeypatch.setattr(config_module, "USER_PRESETS_DIR", tmp_path / "user")

    # Both technically fit a 250x60 grid — preset 2 (the bigger,
    # better-fitting one) should win, not preset 1 just because it
    # sorts first.
    assert pick_preset_for_size(250, 60) == 2


def test_pick_preset_for_size_ties_break_on_lowest_number(tmp_path, monkeypatch):
    packaged_dir = tmp_path / "packaged"
    packaged_dir.mkdir()
    (packaged_dir / "1.toml").write_text("[preset]\nmin_cols = 100\nmin_rows = 30\n")
    (packaged_dir / "2.toml").write_text("[preset]\nmin_cols = 100\nmin_rows = 30\n")

    monkeypatch.setattr(config_module, "PACKAGED_PRESETS_DIR", packaged_dir)
    monkeypatch.setattr(config_module, "USER_PRESETS_DIR", tmp_path / "user")

    assert pick_preset_for_size(150, 40) == 1


def test_pick_preset_for_size_ignores_preset_missing_one_of_the_two_keys(tmp_path, monkeypatch):
    packaged_dir = tmp_path / "packaged"
    packaged_dir.mkdir()
    # min_rows missing entirely — a real config mistake, but this isn't
    # config.toml's own load-time validation path (that's [weather]'s
    # job for its own section); silently not participating rather than
    # crashing tuicc's startup over one malformed preset is the
    # consistent choice here.
    (packaged_dir / "1.toml").write_text("[preset]\nmin_cols = 100\n")

    monkeypatch.setattr(config_module, "PACKAGED_PRESETS_DIR", packaged_dir)
    monkeypatch.setattr(config_module, "USER_PRESETS_DIR", tmp_path / "user")

    assert pick_preset_for_size(200, 50) is None


def test_pick_preset_for_size_user_dir_thresholds_win_over_packaged(tmp_path, monkeypatch):
    packaged_dir = tmp_path / "packaged"
    packaged_dir.mkdir()
    (packaged_dir / "1.toml").write_text("[preset]\nmin_cols = 200\nmin_rows = 50\n")

    user_dir = tmp_path / "user"
    user_dir.mkdir()
    # The user's own live-edited copy has been hand-tuned to a smaller
    # threshold — same USER_PRESETS_DIR-wins-if-present precedence
    # ensure_preset_exists() already uses, checked here for the
    # read-only probe path too.
    (user_dir / "1.toml").write_text("[preset]\nmin_cols = 80\nmin_rows = 20\n")

    monkeypatch.setattr(config_module, "PACKAGED_PRESETS_DIR", packaged_dir)
    monkeypatch.setattr(config_module, "USER_PRESETS_DIR", user_dir)

    assert pick_preset_for_size(100, 25) == 1


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


def test_set_theme_color_escapes_a_literal_quote_in_the_value(tmp_path, monkeypatch):
    # A raw f-string embed (the old implementation) breaks config.toml
    # the moment the value itself contains a `"` — must round-trip
    # through tomllib cleanly instead of just "look right".
    config_path = tmp_path / "config.toml"
    config_path.write_text('[theme]\naccent = "blue"\n')
    monkeypatch.setattr(config_module, "USER_CONFIG_PATH", config_path)

    set_theme_color("accent", 'weird "quoted" value')

    result = config_path.read_text()
    assert tomllib.loads(result)["theme"]["accent"] == 'weird "quoted" value'


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


def test_set_session_name_escapes_a_literal_quote_in_the_value(tmp_path, monkeypatch):
    # Typed straight from the sessions module's rename field, which
    # accepts any printable char (see handle_naming_key) — a raw
    # f-string embed (the old implementation) breaks config.toml the
    # moment the name itself contains a `"`.
    config_path = tmp_path / "config.toml"
    config_path.write_text('[sessions]\nname_1 = "Slot 1"\n')
    monkeypatch.setattr(config_module, "USER_CONFIG_PATH", config_path)

    set_session_name(1, 'quoted "name" here')

    result = config_path.read_text()
    assert tomllib.loads(result)["sessions"]["name_1"] == 'quoted "name" here'


def test_set_session_name_can_write_an_empty_value(tmp_path, monkeypatch):
    # Clearing a custom name back to the default (see apply_naming).
    config_path = tmp_path / "config.toml"
    config_path.write_text('[sessions]\nname_1 = "Work"\n')
    monkeypatch.setattr(config_module, "USER_CONFIG_PATH", config_path)

    set_session_name(1, "")

    assert 'name_1 = ""\n' in config_path.read_text()


# ---------- _build_control_toggles ----------

def test_build_control_toggles_missing_section_falls_back_to_empty_list():
    # The packaged default ships every [[control.toggle]] commented
    # out — a fresh install's user_data genuinely has no "control" key
    # at all, not an oversight to guard against.
    assert _build_control_toggles({}) == []


def test_build_control_toggles_parses_a_2_state_toggle():
    user_data = {
        "control": {
            "toggle": [{
                "label": "Night Light",
                "shell_true": False,
                "state": [
                    {"name": "on", "status_command": "pgrep -x gammastep", "command": "gammastep"},
                    {"name": "off", "command": "pkill -x gammastep"},
                ],
            }],
        },
    }

    result = _build_control_toggles(user_data)

    assert result == [{
        "label": "Night Light",
        "shell_true": False,
        "states": [
            {"name": "on", "status_command": "pgrep -x gammastep", "command": "gammastep", "color": None},
            {"name": "off", "status_command": None, "command": "pkill -x gammastep", "color": None},
        ],
    }]


def test_build_control_toggles_parses_an_n_state_cycle_with_colors():
    # A 3-way cycle (Performance Mode) is the exact same contract as a
    # 2-state toggle, just more states — advancing is
    # (index + 1) % len(states) regardless of N.
    user_data = {
        "control": {
            "toggle": [{
                "label": "Performance Mode",
                "shell_true": True,
                "state": [
                    {"name": "power-saver", "status_command": "a", "command": "set a", "color": "blue"},
                    {"name": "balanced", "status_command": "b", "command": "set b", "color": "white"},
                    {"name": "performance", "command": "set c", "color": "red"},
                ],
            }],
        },
    }

    result = _build_control_toggles(user_data)

    assert [s["name"] for s in result[0]["states"]] == ["power-saver", "balanced", "performance"]
    assert [s["color"] for s in result[0]["states"]] == [4, 7, 1]  # NAMED_COLORS via resolve_color()


def test_build_control_toggles_shell_true_defaults_to_false():
    user_data = {
        "control": {
            "toggle": [{
                "label": "X",
                "state": [
                    {"name": "on", "status_command": "a", "command": "b"},
                    {"name": "off", "command": "c"},
                ],
            }],
        },
    }

    assert _build_control_toggles(user_data)[0]["shell_true"] is False


def test_build_control_toggles_rejects_a_single_state_entry():
    user_data = {"control": {"toggle": [{"label": "X", "state": [{"name": "on", "command": "c"}]}]}}

    with pytest.raises(ValueError):
        _build_control_toggles(user_data)


def test_build_control_toggles_rejects_a_missing_status_command_on_a_non_last_state():
    user_data = {
        "control": {
            "toggle": [{
                "label": "X",
                "state": [
                    {"name": "on", "command": "b"},  # missing status_command, not the last state
                    {"name": "off", "command": "c"},
                ],
            }],
        },
    }

    with pytest.raises(ValueError):
        _build_control_toggles(user_data)


# ---------- _build_weather_config ----------

def test_build_weather_config_missing_section_is_off_with_no_crash():
    # [weather] ships commented out by default — a fresh install's
    # user_data genuinely has no "weather" key at all.
    assert _build_weather_config({}) == {
        "lat": None, "lon": None, "name": None, "code": None, "geoclue": False, "ip_approx": False,
    }


def test_build_weather_config_explicit_lat_lon():
    user_data = {"weather": {"lat": 50.0755, "lon": 14.4378, "name": "Praha"}}

    result = _build_weather_config(user_data)

    assert result == {
        "lat": 50.0755, "lon": 14.4378, "name": "Praha", "code": None,
        "geoclue": False, "ip_approx": False,
    }


def test_build_weather_config_geoclue_only():
    user_data = {"weather": {"geoclue": True}}

    result = _build_weather_config(user_data)

    assert result == {
        "lat": None, "lon": None, "name": None, "code": None,
        "geoclue": True, "ip_approx": False,
    }


def test_build_weather_config_ip_approx_only():
    user_data = {"weather": {"ip_approx": True}}

    result = _build_weather_config(user_data)

    assert result == {
        "lat": None, "lon": None, "name": None, "code": None,
        "geoclue": False, "ip_approx": True,
    }


def test_build_weather_config_code_is_a_plain_passthrough():
    user_data = {"weather": {"lat": 50.0, "lon": 14.0, "code": "PRG"}}

    result = _build_weather_config(user_data)

    assert result["code"] == "PRG"


def test_build_weather_config_rejects_lat_without_lon():
    user_data = {"weather": {"lat": 50.0}}

    with pytest.raises(ValueError):
        _build_weather_config(user_data)


def test_build_weather_config_rejects_lon_without_lat():
    user_data = {"weather": {"lon": 14.0}}

    with pytest.raises(ValueError):
        _build_weather_config(user_data)


def test_build_weather_config_rejects_section_present_with_no_source():
    user_data = {"weather": {"name": "Praha"}}  # a name alone resolves nothing

    with pytest.raises(ValueError):
        _build_weather_config(user_data)


# ---------- _build_sysmon_blocks ----------

def test_build_sysmon_blocks_missing_section_falls_back_to_default_layout():
    # A fresh install, or an existing config.toml predating this
    # section — must reproduce today's packaged layout exactly, not an
    # empty System module.
    result = _build_sysmon_blocks({})
    assert result == DEFAULT_SYSMON_BLOCKS
    assert result is not DEFAULT_SYSMON_BLOCKS  # a copy, not the same list object


def test_build_sysmon_blocks_empty_block_list_also_falls_back_to_default():
    # [sysmon] present but with no [[block]] entries at all (e.g. a
    # user who added the section for some other reason) — same
    # fallback as the section being fully absent.
    assert _build_sysmon_blocks({"sysmon": {}}) == DEFAULT_SYSMON_BLOCKS


def test_build_sysmon_blocks_parses_a_custom_layout():
    user_data = {"sysmon": {"block": [
        {"metric": "cpu", "column": 1, "row": 1, "warning": 60, "urgent": 85},
        {"metric": "load", "column": 2, "row": 1},
    ]}}

    result = _build_sysmon_blocks(user_data)

    assert result == [
        {"metric": "cpu", "enabled": True, "column": 1, "row": 1, "warning": 60, "urgent": 85, "label": None},
        {"metric": "load", "enabled": True, "column": 2, "row": 1, "warning": None, "urgent": None, "label": None},
    ]


def test_build_sysmon_blocks_enabled_defaults_to_true():
    user_data = {"sysmon": {"block": [{"metric": "cpu", "column": 1, "row": 1}]}}
    assert _build_sysmon_blocks(user_data)[0]["enabled"] is True


def test_build_sysmon_blocks_enabled_false_is_respected():
    user_data = {"sysmon": {"block": [{"metric": "cpu", "column": 1, "row": 1, "enabled": False}]}}
    assert _build_sysmon_blocks(user_data)[0]["enabled"] is False


def test_build_sysmon_blocks_custom_label_is_kept():
    user_data = {"sysmon": {"block": [{"metric": "cpu", "column": 1, "row": 1, "label": "Processor"}]}}
    assert _build_sysmon_blocks(user_data)[0]["label"] == "Processor"


def test_build_sysmon_blocks_rejects_unknown_metric():
    user_data = {"sysmon": {"block": [{"metric": "gpu", "column": 1, "row": 1}]}}
    with pytest.raises(ValueError):
        _build_sysmon_blocks(user_data)


def test_build_sysmon_blocks_rejects_two_enabled_blocks_at_the_same_position():
    user_data = {"sysmon": {"block": [
        {"metric": "cpu", "column": 1, "row": 1},
        {"metric": "ram", "column": 1, "row": 1},
    ]}}
    with pytest.raises(ValueError):
        _build_sysmon_blocks(user_data)


def test_build_sysmon_blocks_allows_a_disabled_block_at_an_occupied_position():
    # A disabled block never actually renders, so it can't really
    # "collide" with anything — only enabled blocks need unique
    # positions.
    user_data = {"sysmon": {"block": [
        {"metric": "cpu", "column": 1, "row": 1},
        {"metric": "ram", "column": 1, "row": 1, "enabled": False},
    ]}}
    result = _build_sysmon_blocks(user_data)
    assert len(result) == 2
