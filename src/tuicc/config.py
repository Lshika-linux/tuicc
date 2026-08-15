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
    rwb_time_format: str
    rwb_date_format: str
    weather_lat: float | None
    weather_lon: float | None
    weather_name: str | None
    # A short display badge (e.g. "PRG") for the compact box line —
    # deliberately NOT auto-derived from weather_name (a real
    # IATA-style code needs a whole cities database this project isn't
    # going to embed for a cosmetic detail) and NOT named weather_code:
    # that name is already Open-Meteo's own JSON field for the WMO
    # condition code, parsed inside weather.py — a different thing
    # entirely, kept unambiguous even though they're unrelated.
    weather_city_code: str | None
    weather_geoclue: bool
    weather_ip_approx: bool
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
    USER_PRESETS_DIR, atomically. Never touches an existing preset
    number — picks the next free one via next_free_preset_number(),
    since tomli_w's round-trip would silently strip any hand-written
    comments a regenerated file can't get back. Returns the preset
    number used; never touches config.toml itself — set_active_preset()
    is the comment-safe companion that switches config.toml's [layout]
    preset over to a number this wrote.
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


def pick_preset_for_size(cols: int, rows: int) -> int | None:
    """Best-fitting preset for a terminal grid this size, among presets
    that opt in via their own [preset] min_cols/min_rows table — a
    preset with no such table (every packaged preset, today) never
    participates, so this returns None and load_config()'s own
    [layout] preset value wins unchanged for anyone who hasn't set
    this up. Exists because two machines can report the identical
    pixel resolution and still hand curses a completely different
    cell grid (font size, DPI/output scale) — see
    CLAUDE/NOTES/design-decisions.md#preset-auto-select-by-size for
    the fuller reasoning.

    "Best fit" = the largest min_cols*min_rows product among presets
    that still actually fit this grid (cols >= min_cols and
    rows >= min_rows) — not the closest by absolute difference (could
    pick one that doesn't actually fit) and not merely the smallest
    fitting one (would under-use a screen a bigger preset was designed
    for). Ties broken by preset number, lowest wins, purely for
    determinism.

    Read-only — unlike ensure_preset_exists(), never copies a packaged
    preset into USER_PRESETS_DIR just to probe it; load_config()'s own
    ensure_all_packaged_presets_exist() call already guarantees every
    packaged preset is materialized before this ever needs to run.
    """
    best_number = None
    best_score = -1
    for number in available_preset_numbers():
        user_path = USER_PRESETS_DIR / f"{number}.toml"
        packaged_path = PACKAGED_PRESETS_DIR / f"{number}.toml"
        path = user_path if user_path.exists() else packaged_path
        if not path.exists():
            continue
        preset_meta = load_toml(path).get("preset")
        if not preset_meta:
            continue
        min_cols = preset_meta.get("min_cols")
        min_rows = preset_meta.get("min_rows")
        if min_cols is None or min_rows is None:
            continue
        if cols < min_cols or rows < min_rows:
            continue
        score = min_cols * min_rows
        if score > best_score:
            best_score = score
            best_number = number
    return best_number


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
    """Rewrites only the first `key = ...` line found inside `[section]`
    in USER_CONFIG_PATH, leaving every other line — comments included
    — byte-for-byte untouched. Not a tomllib/tomli_w round-trip, which
    would silently strip comments. Shared by set_active_preset,
    set_theme_color, set_session_name. If `key` (or even `[section]`)
    isn't found, it's appended rather than silently doing nothing —
    needed for set_session_name, since [sessions] may not exist in an
    older config.toml yet. Atomic write (tmp + replace).
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
    # tomli_w.dumps, not an f-string: value is free text (help_mode's
    # color editor lets you type any printable char) and needs real
    # TOML escaping — a literal `"` would otherwise terminate the
    # string early and corrupt config.toml on next load.
    _patch_config_line("theme", role, tomli_w.dumps({role: value}))


def set_session_name(slot: int, value: str) -> None:
    """Switches config.toml's [sessions] name_<slot> over to value —
    the sessions module's rename action. value may be empty (clearing
    a custom name back to the "Slot <N>" default — see load_config's
    session_names).
    """
    # See set_theme_color's matching comment — same free-text-needs-
    # real-escaping reasoning (session names accept the same printable
    # range, including `"`).
    key = f"name_{slot}"
    _patch_config_line("sessions", key, tomli_w.dumps({key: value}))


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


def _build_weather_config(user_data: dict) -> dict:
    """[weather] -> {"lat", "lon", "name", "code", "geoclue", "ip_approx"}.
    .get()-with-default (not direct indexing): [weather] is new and
    optional, ships commented out by default — a config predating it
    genuinely lacks the section, and load_config() must not crash on
    every launch for every config seeded before this landed (same
    reasoning as [audio]'s own audio_backend_name above). But once the
    section IS present, it must resolve to a real, usable location —
    see CLAUDE/NOTES/design-decisions.md#weather-location-sources for
    why there's deliberately no silent auto-detect fallback between
    the three explicit sources (CONTRIBUTING.md's no-silent-fallback
    rule, same style _build_control_toggles below already follows).
    `code` (the compact box line's optional display badge) has no
    validation of its own — purely cosmetic, a personal preference the
    user types themselves (CONTRIBUTING.md's "no hardcoded personal
    preferences" rule — this project doesn't guess or derive one).
    """
    weather_data = user_data.get("weather")
    if weather_data is None:
        return {"lat": None, "lon": None, "name": None, "code": None, "geoclue": False, "ip_approx": False}
    lat = weather_data.get("lat")
    lon = weather_data.get("lon")
    name = weather_data.get("name")
    code = weather_data.get("code")
    geoclue = weather_data.get("geoclue", False)
    ip_approx = weather_data.get("ip_approx", False)
    if (lat is None) != (lon is None):
        raise ValueError("[weather] lat and lon must both be set together")
    if lat is None and not geoclue and not ip_approx:
        raise ValueError(
            "[weather] is configured but no location source is set — "
            "set lat and lon, or geoclue = true, or ip_approx = true"
        )
    return {"lat": lat, "lon": lon, "name": name, "code": code, "geoclue": geoclue, "ip_approx": ip_approx}


def _build_control_toggles(user_data: dict) -> list:
    """[[control.toggle]] -> a list of {"label", "shell_true", "states"}
    dicts. .get()-with-default (not direct indexing): the packaged
    default ships every example commented out, so a fresh install's
    control.toggle is legitimately absent. A 2-state on/off toggle and
    a 3+-state cycle share one contract here — see CLAUDE/VISION.md's
    R5 section for why. Every state but the last must have its own
    status_command (checked in order, first match wins; the last is
    implied by elimination). A non-last state missing status_command,
    or any state missing name/command, raises rather than inventing a
    default (see CONTRIBUTING.md's no-silent-fallback rule).
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
    "row", "warning", "urgent", "label"} dicts. See
    CLAUDE/NOTES/design-decisions.md#sysmon-stats-grid for why this is
    config-driven rather than constants in modules/sysmon.py. Falls
    back to DEFAULT_SYSMON_BLOCKS when [sysmon] has no [[block]]
    entries. "row" is this block's order within its column, not a
    literal shared index — columns can have different block counts, so
    gaps are fine. warning/urgent default to None (never
    threshold-colored) when a metric doesn't set either.
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


def load_config(preset_override: int | None = None) -> Config:
    """preset_override, when given, wins over [layout] preset in
    config.toml for THIS load only — never written back to the file.
    This is deliberately not the same thing as F4/F5's own
    set_active_preset()/save_new_preset(), which do persist: an
    auto-selected preset (see pick_preset_for_size()) reflects
    "what fits THIS machine's screen right now", not a standing user
    preference, and silently overwriting a manually-chosen preset
    number in config.toml just because tuicc happened to launch on a
    smaller/bigger grid this one time would be exactly the kind of
    hidden-default behavior this codebase's own principles reject.
    """
    ensure_user_config_exists()
    ensure_all_packaged_presets_exist()

    user_data = load_toml(USER_CONFIG_PATH)

    preset_number = preset_override if preset_override is not None else user_data["layout"]["preset"]
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
    # new_preset was added after [navigation.keys] otherwise stopped
    # gaining entries — ensure_user_config_exists() only ever copies the
    # packaged default ONCE, never merges newly-added keys into an
    # existing user config.toml, so anyone who set theirs up before this
    # key existed would hit a bare KeyError the first time main.py reads
    # cfg.keybinds["new_preset"], same as every other keybind access in
    # this codebase (no .get() fallback anywhere else either — this one
    # gets a narrow exception specifically because it's new, not because
    # the general pattern should be defensive everywhere). Same
    # unconfigurable-but-working fallback shape as [wm]'s self_app_id/
    # return_to_origin/fullscreen_only above.
    keybinds.setdefault("new_preset", resolve_key("F5"))

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

    rwb_time_format = user_data["rwb"]["time_format"]
    rwb_date_format = user_data["rwb"]["date_format"]
    weather_config = _build_weather_config(user_data)
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
    # global.
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
        rwb_time_format=rwb_time_format,
        rwb_date_format=rwb_date_format,
        weather_lat=weather_config["lat"],
        weather_lon=weather_config["lon"],
        weather_name=weather_config["name"],
        weather_city_code=weather_config["code"],
        weather_geoclue=weather_config["geoclue"],
        weather_ip_approx=weather_config["ip_approx"],
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
