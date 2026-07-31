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
switch_module = "Shift+Tab"
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

[clock]
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
    monkeypatch.setattr(config_module, "PRESETS_DIR", presets_dir)


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
