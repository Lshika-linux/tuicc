"""Config loader: combines the packaged default with the user's own config.toml.

    src/tuicc/defaults/config.toml (packaged)  ──┐
                                                 ├─> merged Config
    ~/.config/tuicc/config.toml (user) ──────────┘
                                                 │
    src/tuicc/presets/<N>.toml (packaged) ───────┤
    ~/.config/tuicc/presets/<N>.toml (user) ─────┘ (referenced by layout.preset)

If the user's config file is missing, it is created by copying the
packaged default — so there is always a real, editable file at a
predictable location, never a silent in-memory fallback the user
can't see or edit.

Presets follow the exact same pattern, but per-preset-number instead of
a single file: ~/.config/tuicc/presets/<N>.toml is where a preset ACTUALLY
lives once it's been used — copied from the packaged src/tuicc/presets/
templates the first time that number is requested, if the user doesn't
already have their own. This (not a single shared layout file) is
deliberate: [layout] preset stays live-switchable (changing the number in
config.toml picks a different file, any time, not just on first run), a
live resize/save-preset feature has a real per-number file to write to,
and none of it can collide with or corrupt config.toml's hand-written
[theme]/[navigation.keys]/etc — which Python's tomllib can't write back
out anyway without losing comments and formatting.

Beyond layout, this also resolves [theme] colors (via theme.py) and
[navigation.keys] keybindings (via keybinds.py) into ready-to-use
values — config.py is where raw TOML becomes the objects/numbers
the rest of tuicc actually consumes.
"""
import re
import shutil
import tomllib
import tomli_w
from dataclasses import dataclass
from pathlib import Path

from tuicc.layout import Layout, ModuleBox, boxes_to_toml_data
from tuicc.theme import resolve_color
from tuicc.keybinds import resolve_key

PACKAGE_DIR = Path(__file__).parent
DEFAULT_CONFIG_PATH = PACKAGE_DIR / "defaults" / "config.toml"
PACKAGED_PRESETS_DIR = PACKAGE_DIR / "presets"
USER_CONFIG_PATH = Path.home() / ".config" / "tuicc" / "config.toml"
USER_PRESETS_DIR = Path.home() / ".config" / "tuicc" / "presets"


@dataclass
class Config:
    layout: Layout
    preset_number: int
    tab_order: str
    provider_name: str
    total_workspaces: int
    self_app_id: str | None
    return_to_origin: bool
    theme: dict
    keybinds: dict
    quick_actions: list
    clock_time_format: str
    clock_date_format: str
    terminal_apps: set
    browser_apps: set
    browser_title_names: set
    vim_mode: bool
    wifi_backend_name: str
    bluetooth_backend_name: str
    power_menu_actions: list
    global_shortcuts: dict

def ensure_user_config_exists() -> None:
    if not USER_CONFIG_PATH.exists():
        USER_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(DEFAULT_CONFIG_PATH, USER_CONFIG_PATH)


def ensure_preset_exists(preset_number: int) -> Path:
    """Returns the path to preset_number's live, user-editable file —
    ~/.config/tuicc/presets/<N>.toml — copying it there from the packaged
    template the first time this preset number is used. Never touches an
    already-existing user file (so live edits, or a future resize mode's
    saves, are never silently overwritten by this).
    """
    user_path = USER_PRESETS_DIR / f"{preset_number}.toml"
    if user_path.exists():
        return user_path

    packaged_path = PACKAGED_PRESETS_DIR / f"{preset_number}.toml"
    if not packaged_path.exists():
        raise FileNotFoundError(
            f"Preset {preset_number} doesn't exist — no "
            f"'{user_path}' and no built-in template at '{packaged_path}'."
        )

    USER_PRESETS_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy(packaged_path, user_path)
    return user_path


def ensure_all_packaged_presets_exist() -> None:
    """Materializes EVERY packaged preset into USER_PRESETS_DIR up
    front, not just whichever one [layout] preset currently points at.
    Without this, a preset only gets copied over the first time it's
    actually loaded — fine for load_config()'s own single number, but
    cycle_preset (F4) and anyone browsing ~/.config/tuicc/presets/ by
    hand should see every packaged option as a real, editable file from
    the start, not only after switching to it once. Reuses
    ensure_preset_exists()'s own guarantee: never touches a preset
    number that already has a user file.
    """
    if not PACKAGED_PRESETS_DIR.exists():
        return
    for path in PACKAGED_PRESETS_DIR.glob("*.toml"):
        try:
            preset_number = int(path.stem)
        except ValueError:
            continue
        ensure_preset_exists(preset_number)


def load_toml(path: Path) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def build_layout_from_preset(preset_number: int) -> Layout:
    preset_path = ensure_preset_exists(preset_number)
    data = load_toml(preset_path)

    boxes = []
    for box_data in data["box"]:
        box = ModuleBox(
            name=box_data["name"],
            x=box_data["x"],
            y=box_data["y"],
            w=box_data["w"],
            h=box_data["h"],
        )
        boxes.append(box)

    return Layout(boxes=boxes)


def next_free_preset_number() -> int:
    """The lowest preset number nothing currently uses — checked against
    BOTH packaged and user preset dirs, so a resize-mode save never picks
    a number that a not-yet-materialized packaged preset would later
    claim (ensure_preset_exists() copies packaged -> user lazily, on
    first use of that number).
    """
    existing = []
    for preset_dir in (USER_PRESETS_DIR, PACKAGED_PRESETS_DIR):
        if preset_dir.exists():
            for path in preset_dir.glob("*.toml"):
                try:
                    existing.append(int(path.stem))
                except ValueError:
                    continue
    return max(existing, default=0) + 1


def save_new_preset(layout: Layout) -> int:
    """Writes layout as a brand-new numbered preset file under
    USER_PRESETS_DIR, atomically (same reasoning as session.py's
    save_session). Never touches an existing preset number — picks the
    next free one via next_free_preset_number(), since tomli_w's
    round-trip would silently strip any hand-written comments (like
    preset 1.toml's) a regenerated file can't get back.

    Returns the preset number used. This never touches config.toml
    itself, same "never risk corrupting hand-written config" principle
    documented above for USER_PRESETS_DIR vs a single shared layout
    file — set_active_preset() below is the (surgical, comment-safe)
    companion that actually switches config.toml's [layout] preset
    over to a number this wrote.
    """
    preset_number = next_free_preset_number()
    save_layout_to_preset(layout, preset_number)
    return preset_number


def save_layout_to_preset(layout: Layout, preset_number: int) -> None:
    """Overwrites preset_number's user-dir file in place with layout —
    unlike save_new_preset, reuses the existing number instead of
    minting a new one. Used by resize mode's F3 to update the preset
    you're actively iterating on, so repeated saves during one editing
    session don't pile up a new numbered file each time. Strips any
    hand-written comments the file had (tomli_w round-trip, same
    one-time cost save_new_preset's docstring documents for a
    brand-new file) — accepted because this only fires on a preset the
    user is actively reshaping via resize mode, not a hands-off
    reference file.
    """
    data = boxes_to_toml_data(layout.boxes)

    USER_PRESETS_DIR.mkdir(parents=True, exist_ok=True)
    path = USER_PRESETS_DIR / f"{preset_number}.toml"
    tmp_path = path.with_name(path.name + ".tmp")
    with open(tmp_path, "wb") as f:
        tomli_w.dump(data, f)
    tmp_path.replace(path)


def available_preset_numbers() -> list[int]:
    """Every preset number that actually exists somewhere — packaged or
    user — sorted. Used by resize mode's preset-cycling (F4) to know
    what "next" means; a number present in both dirs only counts once.
    """
    numbers = set()
    for preset_dir in (USER_PRESETS_DIR, PACKAGED_PRESETS_DIR):
        if preset_dir.exists():
            for path in preset_dir.glob("*.toml"):
                try:
                    numbers.add(int(path.stem))
                except ValueError:
                    continue
    return sorted(numbers)


def _patch_config_line(section: str, key: str, replacement_line: str) -> None:
    """Rewrites ONLY the first `key = ...` line found inside `[section]`
    in USER_CONFIG_PATH, leaving every other line — including comments
    and their exact formatting — byte-for-byte untouched. Not a
    tomllib-parse-then-tomli_w-dump round-trip, which would silently
    strip every comment in a file the user hand-edits (see this
    module's docstring on why presets are separate per-number files in
    the first place — this is where that same constraint has to be
    worked around instead of just avoided). Shared by set_active_preset
    and set_theme_color, the two places in the codebase that patch
    config.toml directly rather than regenerating it.

    Atomic write (tmp + replace), same pattern as save_new_preset/
    session.py's save_session.
    """
    lines = USER_CONFIG_PATH.read_text().splitlines(keepends=True)

    in_section = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("["):
            in_section = (stripped == f"[{section}]")
            continue
        if in_section and re.match(rf"^{re.escape(key)}\s*=", stripped):
            lines[i] = replacement_line
            break

    tmp_path = USER_CONFIG_PATH.with_name(USER_CONFIG_PATH.name + ".tmp")
    tmp_path.write_text("".join(lines))
    tmp_path.replace(USER_CONFIG_PATH)


def set_active_preset(preset_number: int) -> None:
    """Switches config.toml's [layout] preset over to preset_number."""
    _patch_config_line("layout", "preset", f"preset = {preset_number}\n")


def set_theme_color(role: str, value: str) -> None:
    """Switches config.toml's [theme] role over to value (a named
    color, a hex string, or "inherit" — the formats the help menu's
    live color editor accepts; an [R, G, B] list isn't practical to
    type into a single-line field and isn't supported here, only by
    hand-editing config.toml directly).
    """
    _patch_config_line("theme", role, f'{role} = "{value}"\n')


def get_raw_theme_values() -> dict:
    """Every [theme] role's CURRENT raw value straight from
    config.toml, all at once — strings ("cyan", "#7dd3fc", "inherit")
    or [R, G, B] lists, exactly as written, not Config.theme's already-
    resolved curses color numbers. Used by the help menu's color editor
    to show what every role actually is right now, and to prefill the
    one being edited. {} if config.toml or its [theme] section is
    missing (e.g. an out-of-date config.toml predating a role a newer
    default added) — a role simply won't show a current value, not a
    crash.
    """
    try:
        return load_toml(USER_CONFIG_PATH)["theme"]
    except (FileNotFoundError, KeyError):
        return {}


def get_raw_navigation_keys() -> dict:
    """Every [navigation.keys] binding's raw string value from
    config.toml (e.g. "F1", "Ctrl+L", "Left"), used by the help menu's
    keybinds reference. Deliberately not built from Config.keybinds'
    already-resolved codes via keybinds.key_label() — that can't
    recover a Ctrl+<letter> name from its resolved code at all (see
    keybinds.py's own docstring on this), so it would show "?" for
    exactly the shortcuts most worth listing. Reading the original
    string straight from the file sidesteps that class of bug entirely
    instead of working around it after the fact. {} if config.toml or
    this section is missing.
    """
    try:
        return load_toml(USER_CONFIG_PATH)["navigation"]["keys"]
    except (FileNotFoundError, KeyError):
        return {}


def get_raw_power_menu_actions() -> list[dict]:
    """The [[power_menu.action]] entries straight from config.toml,
    each with its own "label"/"shortcut" exactly as written — same
    reasoning as get_raw_navigation_keys, for the same reason (a
    shortcut is typically "Ctrl+<letter>"). [] if config.toml or this
    section is missing.
    """
    try:
        return load_toml(USER_CONFIG_PATH)["power_menu"]["action"]
    except (FileNotFoundError, KeyError):
        return []


def load_config() -> Config:
    ensure_user_config_exists()
    ensure_all_packaged_presets_exist()

    user_data = load_toml(USER_CONFIG_PATH)

    preset_number = user_data["layout"]["preset"]
    layout = build_layout_from_preset(preset_number)

    tab_order_mode = user_data["navigation"]["tab_order"]
    provider_name = user_data["wm"]["provider"]
    total_workspaces = user_data["wm"]["total_workspaces"]
    self_app_id = user_data["wm"].get("self_app_id") or None
    return_to_origin = user_data["wm"].get("return_to_origin", False)

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
            "shell_true": action_data.get("shell_true", False),
            # Whether this action dismisses tuicc after it runs (or, if
            # confirm=true, after the confirmation is answered yes) —
            # default True matches every quick action's behavior before
            # this field existed (mirrors power_menu, always dismiss).
            "exit_after": action_data.get("exit_after", True),
        })

    power_menu_actions = []
    for action_data in user_data["power_menu"]["action"]:
        power_menu_actions.append({
            "label": action_data["label"],
            "shortcut": action_data.get("shortcut"),
            "icon": action_data.get("icon", ""),
            "command": action_data["command"],
            "confirm": action_data.get("confirm", False),
            "confirm_text": action_data.get("confirm_text"),
            "shell_true": action_data.get("shell_true", False),
        })

    # Global shortcuts: a key bound to an action's shortcut works from
    # anywhere in the running app, not just when that action is selected.
    # Built here (not per-module) because the collision check below needs
    # to see every binding — navigation keys and every module's shortcuts —
    # at once, in one place, rather than each module checking in isolation
    # and missing conflicts with the others.
    global_shortcuts = {}
    used_by = {code: name for name, code in keybinds.items()}  # key_code -> name of what already claimed it
    for i, action in enumerate(power_menu_actions):
        if action["shortcut"] is None:
            continue
        key_code = resolve_key(action["shortcut"])
        item_id = f"power_menu:{i}"
        if key_code in used_by:
            raise KeyError(
                f"shortcut {action['shortcut']!r} for power_menu action "
                f"'{action['label']}' collides with '{used_by[key_code]}' — "
                f"each key can only be bound once"
            )
        used_by[key_code] = item_id
        global_shortcuts[key_code] = {"target_kind": "power_action", "item_id": item_id}

    clock_time_format = user_data["clock"]["time_format"]
    clock_date_format = user_data["clock"]["date_format"]
    terminal_apps = set(user_data["title_condense"]["terminal_apps"])
    browser_apps = set(user_data["title_condense"]["browser_apps"])
    browser_title_names = set(user_data["title_condense"]["browser_title_names"])
    vim_mode = user_data["navigation"]["vim_mode"]
    wifi_backend_name = user_data["network"]["wifi_backend"]
    bluetooth_backend_name = user_data["network"]["bluetooth_backend"]

    return Config(
        layout=layout,
        preset_number=preset_number,
        tab_order=tab_order_mode,
        provider_name=provider_name,
        total_workspaces=total_workspaces,
        self_app_id=self_app_id,
        return_to_origin=return_to_origin,
        theme=theme,
        keybinds=keybinds,
        quick_actions=quick_actions,
        clock_time_format=clock_time_format,
        clock_date_format=clock_date_format,
        terminal_apps=terminal_apps,
        browser_apps=browser_apps,
        browser_title_names=browser_title_names,
        vim_mode=vim_mode,
        wifi_backend_name=wifi_backend_name,
        bluetooth_backend_name=bluetooth_backend_name,
        power_menu_actions=power_menu_actions,
        global_shortcuts=global_shortcuts,
    )
