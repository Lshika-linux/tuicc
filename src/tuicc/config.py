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
from tuicc.windowed_list import VISIBLE_SLOTS as DEFAULT_VISIBLE_SLOTS

PACKAGE_DIR = Path(__file__).parent
DEFAULT_CONFIG_PATH = PACKAGE_DIR / "defaults" / "config.toml"
PACKAGED_PRESETS_DIR = PACKAGE_DIR / "presets"
USER_CONFIG_PATH = Path.home() / ".config" / "tuicc" / "config.toml"
USER_PRESETS_DIR = Path.home() / ".config" / "tuicc" / "presets"

# Must match modules/sessions.py's own SLOT_COUNT — duplicated rather
# than imported, since config.py sits below modules/ in the dependency
# order (modules import from config, never the reverse). Session slot
# count isn't exposed as configurable today, same as it wasn't before
# per-slot names existed.
SESSION_SLOT_COUNT = 3


@dataclass
class Config:
    layout: Layout
    preset_number: int
    tab_order: str
    provider_name: str
    total_workspaces: int
    self_app_id: str | None
    return_to_origin: bool
    fullscreen_only: bool
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
    audio_backend_name: str
    power_menu_actions: list
    global_shortcuts: dict
    session_names: dict
    control_toggles: list
    sysmon_blocks: list
    sysmon_visible_slots: int
    media_visible_slots: int
    connectivity_visible_slots: int

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
    worked around instead of just avoided). Shared by set_active_preset,
    set_theme_color, and set_session_name — the places in the codebase
    that patch config.toml directly rather than regenerating it.

    If `key` isn't found inside `[section]`, it's appended to the end
    of that section instead of silently doing nothing — needed for
    set_session_name, where [sessions] (added well after [layout]/
    [theme] existed) may simply not be in an existing user's
    config.toml yet. If `[section]` itself isn't found at all, a new
    section is appended at the end of the file. set_active_preset/
    set_theme_color never actually exercise this path in practice —
    [layout]/[theme] are sections load_config() already requires just
    to start up, so a config that loads successfully always has both —
    but it's not worth a separate function just to special-case that.

    Atomic write (tmp + replace), same pattern as save_new_preset/
    session.py's save_session.
    """
    lines = USER_CONFIG_PATH.read_text().splitlines(keepends=True)

    in_section = False
    section_found = False
    section_end = len(lines)
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("["):
            if in_section:
                section_end = i
            in_section = (stripped == f"[{section}]")
            if in_section:
                section_found = True
            continue
        if in_section and re.match(rf"^{re.escape(key)}\s*=", stripped):
            lines[i] = replacement_line
            break
    else:
        if section_found:
            lines.insert(section_end, replacement_line)
        else:
            if lines and not lines[-1].endswith("\n"):
                lines.append("\n")
            lines.append(f"\n[{section}]\n")
            lines.append(replacement_line)

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


def set_session_name(slot: int, value: str) -> None:
    """Switches config.toml's [sessions] name_<slot> over to value —
    the sessions module's rename action. value may be empty (clearing
    a custom name back to the "Slot <N>" default — see load_config's
    session_names).
    """
    _patch_config_line("sessions", f"name_{slot}", f'name_{slot} = "{value}"\n')


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


def _build_session_names(user_data: dict) -> dict:
    """{1: name, 2: name, 3: name} — falsy (missing key entirely, or an
    empty string from clearing a rename back out) both fall back to
    "Slot <N>". [sessions] itself may not exist at all in a config.toml
    predating this feature, so .get() at every level, same "missing key
    -> sane default, never a crash" treatment fullscreen_only gets in
    load_config() below. A separate function (not inlined into
    load_config) purely so it's testable without a full config.toml
    fixture, same reasoning resolve_color()/resolve_key() are their own
    functions instead of being written inline where they're used.
    """
    sessions_data = user_data.get("sessions", {})
    return {
        n: sessions_data.get(f"name_{n}") or f"Slot {n}"
        for n in range(1, SESSION_SLOT_COUNT + 1)
    }


def _build_control_toggles(user_data: dict) -> list:
    """[[control.toggle]] -> a list of {"label", "shell_true", "states"}
    dicts, each "states" entry {"name", "status_command", "command",
    "color"}. .get("control", {}).get("toggle", []) rather than direct
    indexing (unlike quick_actions/power_menu above): the packaged
    default ships every example commented out, so a fresh install's
    control.toggle is legitimately absent, not an oversight to guard
    against — direct indexing would KeyError on the very install this
    feature ships to by default.

    A 2-state entry ("off"/"on") and a 3+-state one ("power-saver"/
    "balanced"/"performance") are the exact same shape here — see
    VISION.md's R5 section for why this ended up unified into one
    contract instead of a separate binary "toggle" + N-state "cycle".
    Advancing through states is `(current_index + 1) % len(states)`
    regardless of how many there are.

    Every state but the LAST must have its own status_command (exit
    0 = "currently in this state", checked in declaration order, first
    match wins) — the last state's is optional: if every earlier
    state's probe came back false, the current state must be whichever
    one is left, a sound conclusion from exhaustive checking, not a
    guess. A non-last state missing status_command, or any state
    missing name/command, is a real config mistake -> raise, not a
    silently invented default (this file's own long-standing
    no-silent-fallback rule, see CONTRIBUTING.md).
    """
    toggles = []
    for toggle_data in user_data.get("control", {}).get("toggle", []):
        label = toggle_data["label"]
        state_list = toggle_data["state"]
        if len(state_list) < 2:
            raise ValueError(
                f"control.toggle {label!r} has {len(state_list)} state(s) — "
                f"needs at least 2 (an on/off toggle is the 2-state case)"
            )
        states = []
        for i, state_data in enumerate(state_list):
            is_last = i == len(state_list) - 1
            status_command = state_data.get("status_command")
            if status_command is None and not is_last:
                raise ValueError(
                    f"control.toggle {label!r} state {state_data.get('name')!r} "
                    f"is missing status_command — only the LAST state may omit it "
                    f"(implied by elimination if no earlier state matches)"
                )
            states.append({
                "name": state_data["name"],
                "status_command": status_command,
                "command": state_data["command"],
                "color": resolve_color(state_data["color"]) if "color" in state_data else None,
            })
        toggles.append({
            "label": label,
            "shell_true": toggle_data.get("shell_true", False),
            "states": states,
        })
    return toggles


# Every metric modules/sysmon.py's own stats grid knows how to render —
# _build_sysmon_blocks (below) validates every [[sysmon.block]] entry's
# "metric" against this set, loudly (ValueError), rather than letting a
# typo'd metric name silently vanish from the grid.
SYSMON_METRICS = {"cpu", "ram", "disk", "load", "cputemp", "hot", "swap"}

# Today's packaged layout, reproduced exactly — used whenever [sysmon]
# has no [[block]] entries at all (a fresh install, or an existing
# config.toml predating this landing), so nothing changes visually for
# anyone who hasn't opted into customizing it. load/swap deliberately
# have no warning/urgent (None) — see modules/sysmon.py's own module
# docstring for why those two aren't threshold-colored at all.
DEFAULT_SYSMON_BLOCKS = [
    {"metric": "cpu", "enabled": True, "column": 1, "row": 1, "warning": 70, "urgent": 90, "label": None},
    {"metric": "cputemp", "enabled": True, "column": 1, "row": 2, "warning": 75, "urgent": 90, "label": None},
    {"metric": "hot", "enabled": True, "column": 1, "row": 3, "warning": 75, "urgent": 90, "label": None},
    {"metric": "ram", "enabled": True, "column": 2, "row": 1, "warning": 75, "urgent": 90, "label": None},
    {"metric": "disk", "enabled": True, "column": 2, "row": 2, "warning": 80, "urgent": 95, "label": None},
    {"metric": "load", "enabled": True, "column": 3, "row": 1, "warning": None, "urgent": None, "label": None},
    {"metric": "swap", "enabled": True, "column": 3, "row": 2, "warning": None, "urgent": None, "label": None},
]


def _build_sysmon_blocks(user_data: dict) -> list:
    """[[sysmon.block]] -> a list of {"metric", "enabled", "column",
    "row", "warning", "urgent", "label"} dicts — the System module's
    own single source of truth for which stat blocks show at all, where
    each one sits, and what counts as warning/urgent for it. Found
    live, asked for directly: "ten config by měl být to jedno místo kde
    nastavuješ všechno ohledně boxíku" (the config should be the one
    place you configure everything about that box).

    Falls back to DEFAULT_SYSMON_BLOCKS (above) when [sysmon] has no
    [[block]] entries — same .get()-with-fallback reasoning [audio]'s
    own section established for a config section landing after initial
    release; an existing config.toml predating this must not lose its
    whole System module over a missing section.

    "row" is really just this block's own ORDER within its column, not
    a literal shared row index across every column — columns don't
    have to have the same number of blocks (today's default column 1
    has 3, columns 2/3 have 2 each), so gaps/non-contiguous row numbers
    are fine; only the relative order within one column matters.
    warning/urgent are optional (None, the default, when a metric isn't
    given either) — a block with neither never gets threshold-colored,
    always plain text.
    """
    raw_blocks = user_data.get("sysmon", {}).get("block")
    if not raw_blocks:
        return [dict(b) for b in DEFAULT_SYSMON_BLOCKS]

    blocks = []
    claimed_positions = {}  # (column, row) -> metric that already claimed it, for the collision check below
    for entry in raw_blocks:
        metric = entry.get("metric")
        if metric not in SYSMON_METRICS:
            raise ValueError(
                f"[[sysmon.block]] has metric={metric!r} — must be one of {sorted(SYSMON_METRICS)}"
            )
        enabled = entry.get("enabled", True)
        column = entry.get("column", 1)
        row = entry.get("row", 1)
        if enabled:
            position = (column, row)
            if position in claimed_positions:
                raise ValueError(
                    f"[[sysmon.block]] metric={metric!r} collides with "
                    f"metric={claimed_positions[position]!r} at column={column}, row={row} — "
                    f"each enabled block needs its own (column, row)"
                )
            claimed_positions[position] = metric
        blocks.append({
            "metric": metric,
            "enabled": enabled,
            "column": column,
            "row": row,
            "warning": entry.get("warning"),
            "urgent": entry.get("urgent"),
            "label": entry.get("label"),
        })
    return blocks


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
    fullscreen_only = user_data["wm"].get("fullscreen_only", False)

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

    session_names = _build_session_names(user_data)

    clock_time_format = user_data["clock"]["time_format"]
    clock_date_format = user_data["clock"]["date_format"]
    terminal_apps = set(user_data["title_condense"]["terminal_apps"])
    browser_apps = set(user_data["title_condense"]["browser_apps"])
    browser_title_names = set(user_data["title_condense"]["browser_title_names"])
    vim_mode = user_data["navigation"]["vim_mode"]
    wifi_backend_name = user_data["network"]["wifi_backend"]
    bluetooth_backend_name = user_data["network"]["bluetooth_backend"]
    # .get() with a fallback, not direct indexing like [network]'s own
    # wifi_backend/bluetooth_backend above — [audio] is new (R5), an
    # existing config.toml predating it genuinely lacks the section
    # entirely, and load_config() must not hard-crash on every launch
    # for every config seeded before this landed. "wpctl" matches the
    # packaged default and audio/wpctl.py's own stated primary-backend
    # reasoning (PipeWire/WirePlumber is what every current sway/i3
    # desktop actually runs).
    audio_backend_name = user_data.get("audio", {}).get("audio_backend", "wpctl")
    control_toggles = _build_control_toggles(user_data)
    sysmon_blocks = _build_sysmon_blocks(user_data)
    # How many rows each fixed-slot-plus-scroll list shows at once
    # before it starts scrolling (windowed_list.py's own VISIBLE_SLOTS,
    # DEFAULT_VISIBLE_SLOTS here) — sysmon's window list and media's
    # Now Playing/Output lists each get their own independent value
    # (different boxes, different natural heights), not one shared
    # global. Found live, asked for directly, same session as
    # [[sysmon.block]]: "Počet viditelných řádků, visible slots a to
    # same i pro media" (the visible-row count, and the same for media
    # too).
    sysmon_visible_slots = user_data.get("sysmon", {}).get("visible_slots", DEFAULT_VISIBLE_SLOTS)
    media_visible_slots = user_data.get("media", {}).get("visible_slots", DEFAULT_VISIBLE_SLOTS)
    # connectivity.py's WiFi/Bluetooth sections (VISION.md's R4 follow-
    # up — the box had no scrolling at all before this, WiFi hard-capped
    # at a fixed row count with a static "+N more" line, Bluetooth not
    # capped at all) share ONE value between the two sections, same as
    # media.py's Now Playing/Output do — not one independent value per
    # section the way sysmon/media's own OWN boxes get, since both
    # sections live in the same box here.
    connectivity_visible_slots = user_data.get("connectivity", {}).get("visible_slots", DEFAULT_VISIBLE_SLOTS)

    return Config(
        layout=layout,
        preset_number=preset_number,
        tab_order=tab_order_mode,
        provider_name=provider_name,
        total_workspaces=total_workspaces,
        self_app_id=self_app_id,
        return_to_origin=return_to_origin,
        fullscreen_only=fullscreen_only,
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
        audio_backend_name=audio_backend_name,
        power_menu_actions=power_menu_actions,
        global_shortcuts=global_shortcuts,
        session_names=session_names,
        control_toggles=control_toggles,
        sysmon_blocks=sysmon_blocks,
        sysmon_visible_slots=sysmon_visible_slots,
        media_visible_slots=media_visible_slots,
        connectivity_visible_slots=connectivity_visible_slots,
    )
