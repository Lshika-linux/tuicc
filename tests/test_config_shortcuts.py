"""Tests for config.py's global_shortcuts: built from power_menu.action
entries with a shortcut set, with a hard collision check against both
each other and [navigation.keys].

Exercises the real load_config() end to end (via a temp user config +
preset), rather than re-testing the collision algorithm in isolation,
so a change to load_config()'s wiring would actually be caught here.
"""

import pytest

import tuicc.config as config_module
from tuicc.config import load_config
from tuicc.keybinds import resolve_key


BASE_TOML = """
[layout]
preset = 1

[navigation]
tab_order = "columns_first"
vim_mode = false

[navigation.keys]
left = "Left"
right = "Right"
up = "Up"
down = "Down"
tab = "Tab"
previous = "Shift+Tab"
confirm = "Enter"

[wm]
provider = "sway"
total_workspaces = 10

[theme]
background = "inherit"
border = "white"
border_selected = "white"
text = "white"
accent = "cyan"
selected = "blue"
warning = "yellow"
urgent = "red"

[[quick_actions.action]]
label = "test"
icon = ""
command = "true"
confirm = false

{power_menu_block}

[rwb]
time_format = "%H:%M:%S"
date_format = "%a"

[title_condense]
terminal_apps = []
browser_apps = []
browser_title_names = []

[network]
wifi_backend = "iwd"
bluetooth_backend = "bluez"
"""

PRESET_TOML = """
[[box]]
name = "sidebar"
x = 0.0
y = 0.0
w = 1.0
h = 1.0
"""


def _write_config(tmp_path, monkeypatch, power_menu_actions_toml):
    user_config = tmp_path / "config.toml"
    user_config.write_text(BASE_TOML.format(power_menu_block=power_menu_actions_toml))

    presets_dir = tmp_path / "presets"
    presets_dir.mkdir()
    (presets_dir / "1.toml").write_text(PRESET_TOML)

    monkeypatch.setattr(config_module, "USER_CONFIG_PATH", user_config)
    monkeypatch.setattr(config_module, "USER_PRESETS_DIR", presets_dir)


def _action_toml(label, shortcut=None, confirm=False):
    lines = [
        "[[power_menu.action]]",
        f'label = "{label}"',
        'icon = ""',
        'command = "true"',
        f"confirm = {str(confirm).lower()}",
    ]
    if shortcut is not None:
        lines.insert(2, f'shortcut = "{shortcut}"')
    return "\n".join(lines)


def test_shortcuts_resolve_to_correct_key_codes(tmp_path, monkeypatch):
    actions_toml = "\n\n".join([
        _action_toml("Lock", shortcut="Ctrl+L"),
        _action_toml("Logout", shortcut="Ctrl+O"),
    ])
    _write_config(tmp_path, monkeypatch, actions_toml)

    cfg = load_config()

    assert cfg.global_shortcuts[12] == {"target_kind": "power_action", "item_id": "power_menu:0"}
    assert cfg.global_shortcuts[15] == {"target_kind": "power_action", "item_id": "power_menu:1"}


def test_preset_override_wins_over_config_toml_value_without_persisting(tmp_path, monkeypatch):
    # preset_override is main.py's own auto-selected-by-grid-size
    # value (pick_preset_for_size()) — it must win for THIS load, but
    # config.toml's own [layout] preset line (still "1" here) must
    # stay untouched, since this is a session-only override, not a
    # standing preference change (see load_config()'s own docstring).
    actions_toml = _action_toml("Lock", shortcut=None)
    _write_config(tmp_path, monkeypatch, actions_toml)
    (tmp_path / "presets" / "2.toml").write_text(
        '[[box]]\nname = "override_box"\nx = 0.0\ny = 0.0\nw = 1.0\nh = 1.0\n'
    )

    cfg = load_config(preset_override=2)

    assert cfg.layout.boxes[0].name == "override_box"
    assert (tmp_path / "config.toml").read_text() == BASE_TOML.format(power_menu_block=actions_toml)


def test_self_app_id_and_return_to_origin_default_when_absent_from_wm_section(tmp_path, monkeypatch):
    # BASE_TOML's [wm] section has no self_app_id/return_to_origin keys
    # at all — load_config() must tolerate that (both are optional
    # fields within the otherwise-mandatory [wm] table), not KeyError.
    actions_toml = _action_toml("Lock", shortcut=None)
    _write_config(tmp_path, monkeypatch, actions_toml)

    cfg = load_config()

    assert cfg.self_app_id is None
    assert cfg.return_to_origin is False


def test_new_preset_keybind_defaults_to_f5_when_absent(tmp_path, monkeypatch):
    # BASE_TOML's [navigation.keys] predates new_preset entirely (same
    # sparse-on-purpose fixture as the self_app_id test above) — a real
    # existing user config.toml from before this key existed. Unlike
    # every other keybind (a bare KeyError at USE time, not load time,
    # is the norm — see config.py's own comment), new_preset gets a
    # narrow fallback specifically because it's newly added, so
    # load_config() must resolve it to "F5" rather than just omitting
    # it from cfg.keybinds.
    actions_toml = _action_toml("Lock", shortcut=None)
    _write_config(tmp_path, monkeypatch, actions_toml)

    cfg = load_config()

    assert cfg.keybinds["new_preset"] == resolve_key("F5")


def test_new_preset_keybind_respects_explicit_config_value(tmp_path, monkeypatch):
    user_config = tmp_path / "config.toml"
    user_config.write_text(
        BASE_TOML.replace('confirm = "Enter"', 'confirm = "Enter"\nnew_preset = "F9"').format(
            power_menu_block=_action_toml("Lock", shortcut=None)
        )
    )
    presets_dir = tmp_path / "presets"
    presets_dir.mkdir()
    (presets_dir / "1.toml").write_text(PRESET_TOML)
    monkeypatch.setattr(config_module, "USER_CONFIG_PATH", user_config)
    monkeypatch.setattr(config_module, "USER_PRESETS_DIR", presets_dir)

    cfg = load_config()

    assert cfg.keybinds["new_preset"] == resolve_key("F9")


def test_scan_keybind_defaults_to_s_when_absent(tmp_path, monkeypatch):
    # Same reasoning as test_new_preset_keybind_defaults_to_f5_when_absent
    # above — BASE_TOML's [navigation.keys] predates "scan" too (added
    # for connectivity.py's level-2 wifi/bluetooth browsing).
    actions_toml = _action_toml("Lock", shortcut=None)
    _write_config(tmp_path, monkeypatch, actions_toml)

    cfg = load_config()

    assert cfg.keybinds["scan"] == resolve_key("s")


def test_scan_keybind_respects_explicit_config_value(tmp_path, monkeypatch):
    user_config = tmp_path / "config.toml"
    user_config.write_text(
        BASE_TOML.replace('confirm = "Enter"', 'confirm = "Enter"\nscan = "r"').format(
            power_menu_block=_action_toml("Lock", shortcut=None)
        )
    )
    presets_dir = tmp_path / "presets"
    presets_dir.mkdir()
    (presets_dir / "1.toml").write_text(PRESET_TOML)
    monkeypatch.setattr(config_module, "USER_CONFIG_PATH", user_config)
    monkeypatch.setattr(config_module, "USER_PRESETS_DIR", presets_dir)

    cfg = load_config()

    assert cfg.keybinds["scan"] == resolve_key("r")


def test_action_without_shortcut_is_not_in_global_shortcuts(tmp_path, monkeypatch):
    actions_toml = _action_toml("Lock", shortcut=None)
    _write_config(tmp_path, monkeypatch, actions_toml)

    cfg = load_config()

    assert cfg.global_shortcuts == {}
    assert cfg.power_menu_actions[0]["shortcut"] is None


def test_two_actions_with_same_shortcut_raises(tmp_path, monkeypatch):
    actions_toml = "\n\n".join([
        _action_toml("Lock", shortcut="Ctrl+L"),
        _action_toml("Launch", shortcut="Ctrl+L"),
    ])
    _write_config(tmp_path, monkeypatch, actions_toml)

    with pytest.raises(KeyError):
        load_config()


def test_shortcut_colliding_with_navigation_key_raises(tmp_path, monkeypatch):
    # "Enter" is already bound to [navigation.keys].confirm in BASE_TOML.
    actions_toml = _action_toml("Lock", shortcut="Enter")
    _write_config(tmp_path, monkeypatch, actions_toml)

    with pytest.raises(KeyError):
        load_config()


def test_invalid_shortcut_syntax_raises(tmp_path, monkeypatch):
    actions_toml = _action_toml("Lock", shortcut="Ctrl+1")
    _write_config(tmp_path, monkeypatch, actions_toml)

    with pytest.raises(ValueError):
        load_config()


# ---------- sysmon_blocks ----------
# _build_sysmon_blocks() itself is unit-tested directly against plain
# dicts in test_presets.py (same style as that file's own
# _build_control_toggles tests) — this one confirms load_config()'s
# own end-to-end wiring actually calls it and stores the result.

def test_sysmon_blocks_default_when_section_absent(tmp_path, monkeypatch):
    # [sysmon] is absent from BASE_TOML entirely — same "existing
    # config predating this section" case every other test above
    # already exercises for [wm]'s optional self_app_id/
    # return_to_origin keys.
    actions_toml = _action_toml("Lock", shortcut=None)
    _write_config(tmp_path, monkeypatch, actions_toml)

    cfg = load_config()

    assert cfg.sysmon_blocks == config_module.DEFAULT_SYSMON_BLOCKS


# ---------- sysmon_visible_slots / media_visible_slots ----------
# sysmon's window list and media's Now Playing/Output lists each get
# their own independent visible-row-count value.

def test_visible_slots_default_to_3_when_sections_absent(tmp_path, monkeypatch):
    # Neither [sysmon] nor [media] nor [connectivity] appear in
    # BASE_TOML at all.
    actions_toml = _action_toml("Lock", shortcut=None)
    _write_config(tmp_path, monkeypatch, actions_toml)

    cfg = load_config()

    assert cfg.sysmon_visible_slots == 3
    assert cfg.media_visible_slots == 3
    assert cfg.connectivity_visible_slots == 3


def test_visible_slots_read_from_config_when_present(tmp_path, monkeypatch):
    actions_toml = _action_toml("Lock", shortcut=None)
    user_config = tmp_path / "config.toml"
    user_config.write_text(
        BASE_TOML.format(power_menu_block=actions_toml)
        + "\n[sysmon]\nvisible_slots = 5\n\n[media]\nvisible_slots = 2\n\n[connectivity]\nvisible_slots = 4\n"
    )
    presets_dir = tmp_path / "presets"
    presets_dir.mkdir()
    (presets_dir / "1.toml").write_text(PRESET_TOML)
    monkeypatch.setattr(config_module, "USER_CONFIG_PATH", user_config)
    monkeypatch.setattr(config_module, "USER_PRESETS_DIR", presets_dir)

    cfg = load_config()

    assert cfg.sysmon_visible_slots == 5
    assert cfg.media_visible_slots == 2
    assert cfg.connectivity_visible_slots == 4


def test_sysmon_visible_slots_independent_of_sysmon_blocks(tmp_path, monkeypatch):
    # [sysmon] can set visible_slots without needing any [[block]]
    # entries at all — the two are unrelated keys under the same table,
    # and _build_sysmon_blocks() must still fall back to the packaged
    # block layout.
    actions_toml = _action_toml("Lock", shortcut=None)
    user_config = tmp_path / "config.toml"
    user_config.write_text(BASE_TOML.format(power_menu_block=actions_toml) + "\n[sysmon]\nvisible_slots = 7\n")
    presets_dir = tmp_path / "presets"
    presets_dir.mkdir()
    (presets_dir / "1.toml").write_text(PRESET_TOML)
    monkeypatch.setattr(config_module, "USER_CONFIG_PATH", user_config)
    monkeypatch.setattr(config_module, "USER_PRESETS_DIR", presets_dir)

    cfg = load_config()

    assert cfg.sysmon_visible_slots == 7
    assert cfg.sysmon_blocks == config_module.DEFAULT_SYSMON_BLOCKS
