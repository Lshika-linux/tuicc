"""Config loader: combines the packaged default with the user's own config.toml.

    src/tuicc/defaults/config.toml (packaged)  ──┐
                                                 ├─> merged Config
    ~/.config/tuicc/config.toml (user) ──────────┘
                                                 │
    src/tuicc/presets/<N>.toml ──────────────────┘ (referenced by layout.preset)

If the user's config file is missing, it is created by copying the
packaged default — so there is always a real, editable file at a
predictable location, never a silent in-memory fallback the user
can't see or edit.

Beyond layout, this also resolves [theme] colors (via theme.py) and
[navigation.keys] keybindings (via keybinds.py) into ready-to-use
values — config.py is where raw TOML becomes the objects/numbers
the rest of tuicc actually consumes.
"""
import shutil
import tomllib
from dataclasses import dataclass
from pathlib import Path

from tuicc.layout import Layout, ModuleBox
from tuicc.theme import resolve_color
from tuicc.keybinds import resolve_key

PACKAGE_DIR = Path(__file__).parent
DEFAULT_CONFIG_PATH = PACKAGE_DIR / "defaults" / "config.toml"
PRESETS_DIR = PACKAGE_DIR / "presets"
USER_CONFIG_PATH = Path.home() / ".config" / "tuicc" / "config.toml"


@dataclass
class Config:
    layout: Layout
    tab_order: str
    provider_name: str
    total_workspaces: int
    theme: dict
    keybinds: dict
    quick_actions: list
    clock_time_format: str
    clock_date_format: str
    terminal_apps: set
    browser_apps: set
    browser_title_names: set
    vim_mode: bool
    
def ensure_user_config_exists() -> None:
    if not USER_CONFIG_PATH.exists():
        USER_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(DEFAULT_CONFIG_PATH, USER_CONFIG_PATH)


def load_toml(path: Path) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def build_layout_from_preset(preset_number: int) -> Layout:
    preset_path = PRESETS_DIR / f"{preset_number}.toml"
    data = load_toml(preset_path)

    boxes = []
    for box_data in data["box"]:
        box = ModuleBox(
            name=box_data["name"],
            rect=(box_data["x"], box_data["y"], box_data["w"], box_data["h"]),
        )
        boxes.append(box)

    return Layout(boxes=boxes)


def load_config() -> Config:
    ensure_user_config_exists()

    user_data = load_toml(USER_CONFIG_PATH)

    preset_number = user_data["layout"]["preset"]
    layout = build_layout_from_preset(preset_number)

    tab_order_mode = user_data["navigation"]["tab_order"]
    provider_name = user_data["wm"]["provider"]
    total_workspaces = user_data["wm"]["total_workspaces"]

    theme = {}
    for role, value in user_data["theme"].items():
        theme[role] = resolve_color(value)

    keybinds = {}
    for action, key_name in user_data["navigation"]["keys"].items():
        keybinds[action] = resolve_key(key_name)

    quick_actions = []
    for action_data in user_data["quick_actions"]["action"]:
        quick_actions.append({
            "label": action_data["label"],
            "icon": action_data.get("icon", ""),
            "command": action_data["command"],
            "confirm": action_data.get("confirm", False),
        })

    clock_time_format = user_data["clock"]["time_format"]
    clock_date_format = user_data["clock"]["date_format"]
    terminal_apps = set(user_data["title_condense"]["terminal_apps"])
    browser_apps = set(user_data["title_condense"]["browser_apps"])
    browser_title_names = set(user_data["title_condense"]["browser_title_names"])
    vim_mode = user_data["navigation"]["vim_mode"]
    
    return Config(
        layout=layout,
        tab_order=tab_order_mode,
        provider_name=provider_name,
        total_workspaces=total_workspaces,
        theme=theme,
        keybinds=keybinds,
        quick_actions=quick_actions,
        clock_time_format=clock_time_format,
        clock_date_format=clock_date_format,
        terminal_apps=terminal_apps,
        browser_apps=browser_apps,
        browser_title_names=browser_title_names,
        vim_mode=vim_mode,
    )
