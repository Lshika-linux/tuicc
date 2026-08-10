"""Content and pure logic for the F1 help menu — a keybinds/FAQ page,
a resize-mode reference, and a live color editor for the 8 shared
theme roles.

Deliberately holds everything that isn't main-loop state ownership:
page content, the digit->page mapping, the color-input field's key
handling/validation, AND (via HelpState) the session state itself and
the functions that transform it — mirroring resize_mode.py's
ResizeState/SpawnPickerState split. main.py owns one HelpState
instance and calls into these functions instead of containing the
logic inline; it still decides *when* to call them (which key means
what) and owns cfg/theme_pairs, since applying a color edit touches
main.py's own live curses state.

draw_color_mockup and draw() are the two functions here that touch
curses — same reasoning modules/ files mix pure logic with a real
draw(): the page content (which little box demonstrates which role,
how the colors page's two panes are laid out) is domain knowledge
that belongs with everything else in this file, not smeared across
main.py or a "generic" render_utils primitive that would have to know
about tuicc's specific pages/roles anyway.

Keybinds are folded into the Help page as a plain reference, not a
standalone page or a live rebind UI — key_label() can't recover a name
from a Ctrl+<letter> code (it falls back to "?", see keybinds.py), so
a rebind-and-redisplay flow would show garbage for exactly the
shortcuts most worth rebinding.

The color editor accepts a named color, a "#hex" string, or "inherit"
— not an [R, G, B] list, which isn't practical to type into a single
line. Still fully available by hand-editing config.toml directly.
"""

import curses
from dataclasses import dataclass

from tuicc.theme import resolve_color
from tuicc.render_utils import draw_box_outline, draw_filled_box, draw_text_panel

TOP_MENU = ["Help", "Resize mode", "Colors"]
PAGE_NAMES = ["help", "resize", "colors"]

COLOR_ROLES = [
    "background", "border", "border_selected", "text",
    "accent", "selected", "warning", "urgent",
]


def page_for_digit(key: int) -> str | None:
    """Which page (if any) a digit keypress at the top menu selects."""
    index = key - ord("1")
    if 0 <= index < len(PAGE_NAMES):
        return PAGE_NAMES[index]
    return None


def top_menu_lines() -> list[str]:
    lines = ["tuicc help", ""]
    for i, name in enumerate(TOP_MENU, start=1):
        lines.append(f"{i}  {name}")
    lines.append("")
    lines.append("Esc  close")
    return lines


def keybinds_lines(raw_keys: dict) -> list[str]:
    """raw_keys is config.get_raw_navigation_keys() — the original
    strings ("F1", "Ctrl+L", "Left") as written in config.toml, not
    Config.keybinds' resolved codes (see get_raw_navigation_keys for
    why: key_label() can't reliably reverse those).
    """
    return [f"{action:<15} {key_name}" for action, key_name in raw_keys.items()]


def shortcuts_lines(raw_power_menu_actions: list[dict]) -> list[str]:
    """raw_power_menu_actions is config.get_raw_power_menu_actions() —
    a Ctrl+<letter> shortcut works from anywhere in the running app,
    not just [navigation.keys], so it deserves its own line in the
    same reference rather than being left out because it's a
    different mechanism under the hood.
    """
    return [
        f"{action['shortcut']:<15} {action['label']}"
        for action in raw_power_menu_actions
        if action.get("shortcut")
    ]


def help_lines(raw_keys: dict, raw_power_menu_actions: list[dict]) -> list[str]:
    lines = [
        "How do I control this thing?",
        "These are the configured keybinds (edit in ~/.config/tuicc/config.toml):",
        "",
    ]
    lines.extend(keybinds_lines(raw_keys))
    shortcuts = shortcuts_lines(raw_power_menu_actions)
    if shortcuts:
        lines.append("")
        lines.append("Global shortcuts (work from anywhere, even mid-typing):")
        lines.extend(shortcuts)
    lines.extend([
        "",
        "My modules look bad, what do I do?",
        "I recommend switching to fullscreen, and using edit mode (F2)!",
        "Or visit and edit ~/.config/tuicc/presets/<N>.toml, and apply the",
        "preset in ~/.config/tuicc/config.toml.",
        "",
        "Esc  back to menu",
    ])
    return lines


def resize_help_lines() -> list[str]:
    return [
        "Resize mode — edit tuicc's own layout from inside tuicc.",
        "",
        "F2       open edit mode — a persistent session, stays open",
        "         until you leave it",
        "",
        "While browsing (no module being edited yet):",
        "arrows/Tab/Shift+Tab   pick which module is active, same as",
        "                       normal navigation outside edit mode",
        "Enter    start editing the active module (it turns red)",
        "Del      remove the active module (asks y/n first)",
        "Escape   leave edit mode entirely, keeping whatever was",
        "         already committed",
        "",
        "While editing a module:",
        "arrows   resize (w/h) the box, or move it (x/y) — 'm' toggles",
        "         which one arrows currently control",
        "Del      remove this box (asks y/n first)",
        "Enter    keep the change (in memory) and return to browsing —",
        "         pick another module to edit next, or leave",
        "Escape   revert the change — or, on a box you just spawned,",
        "         remove it entirely (there's no 'before' to revert to)",
        "         — either way, returns to browsing",
        "",
        "F6       spawn a module that isn't currently in the layout,",
        "         centered, dropped straight into move mode",
        "F3       overwrite the active preset in place with the",
        "         current layout, and leave edit mode",
        "F4       cycle through every preset that exists, and leave",
        "         edit mode",
        "F5       save the current layout as a brand-new preset (never",
        "         overwrites an existing one), switch to it, and leave",
        "         edit mode — fork a layout you like before changing it",
        "",
        "F1/F3/F4/F5/F6 all work from either level of edit mode — they",
        "commit whatever's in progress first, same as Enter would.",
        "",
        "Esc  back to menu",
    ]


def color_role_lines(
    selected_index: int,
    raw_values: dict,
    editing: bool = False,
    edit_text: str = "",
    error: str | None = None,
) -> list[str]:
    """The whole Colors page as one role-per-row list, each row showing
    its current value — editing happens inline, on this same list (the
    selected row's value swaps for the live-typed text, everything
    else stays put), not a separate page you navigate away to.
    """
    lines = []
    for i, role in enumerate(COLOR_ROLES):
        marker = "> " if i == selected_index else "  "
        if editing and i == selected_index:
            value = f"{edit_text}_"
        else:
            value = format_color_value(raw_values.get(role))
        lines.append(f"{marker}{role:<16} {value}")

    lines.append("")
    if editing:
        lines.append("type a named color, #hex, or 'inherit'")
        if error:
            lines.append(error)
        lines.append("Enter  apply")
        lines.append("Esc    cancel")
    else:
        lines.append("Enter  edit")
        lines.append("Esc    back to menu")
    return lines


def format_color_value(value) -> str:
    """A raw [theme] value (from config.get_raw_theme_values()[role]) as
    text — an [R, G, B] list becomes a hex string (the field itself
    only accepts named/hex/inherit input, so showing the list literally
    would prefill something you couldn't just re-submit unedited);
    anything else is already a plain string, used as-is. None (role
    missing from config.toml) formats to empty.
    """
    if value is None:
        return ""
    if isinstance(value, list):
        r, g, b = value
        return f"#{r:02x}{g:02x}{b:02x}"
    return str(value)


def parse_color_input(text: str) -> tuple[int | None, str | None]:
    """(curses_color, None) on success, or (None, error message) — a
    bad value is something to show the user, never a crash.
    """
    text = text.strip()
    if not text:
        return None, "Type a color name, #hex, or 'inherit', then Enter."
    try:
        return resolve_color(text), None
    except ValueError as e:
        return None, str(e)


def handle_color_input_key(key: int, text: str) -> tuple[str, bool]:
    """Same shape as launcher.py's handle_typing_key, scoped to this
    one field — Enter is deliberately NOT handled here (same reasoning
    as handle_typing_key: applying the value needs cfg/theme state
    this function doesn't have), so the caller checks for confirm
    before falling back to this for everything else.
    """
    if key == 27:  # Escape
        return "", False
    if key in (curses.KEY_BACKSPACE, 127, 8):
        return text[:-1], True
    if 32 <= key <= 126:
        return text + chr(key), True
    return text, True


def draw_color_mockup(stdscr, box, theme_pairs):
    """A small static mockup of tuicc's own UI — one inactive module
    box (border) next to one active module box (border_selected)
    containing sample text/selected/warning/urgent lines, plus an
    accent-colored floating-window rectangle inside it, and a
    background-colored strip along the bottom — so editing a role on
    the roles list is visible in context immediately, not just as an
    abstract role name changing in isolation.

    Every piece is drawn in its own try/except, not one shared around
    the whole function — a narrow terminal clipping one element (e.g.
    the inner "win" box) must not also blank out everything drawn
    before it; a single early curses.error used to abort the rest of
    the mockup silently, which is worse than a slightly cramped result.
    """
    x, y, w, h = box

    def put(row, col, text, color):
        try:
            stdscr.addstr(row, col, text, color)
        except curses.error:
            pass

    if w < 14 or h < 6:
        put(y, x, "(too narrow for a preview)"[:max(w, 0)], theme_pairs.get("text", 0))
        return

    # Bottom row reserved for the "background" strip (it has no shape
    # of its own — a fill, not an outline/text color, so it needs a
    # row that isn't also a box's own border) — the two module boxes
    # get the rest, when there's enough height to spare one.
    has_bg_row = h >= 7
    boxes_h = h - 1 if has_bg_row else h

    left_w = max(w // 2, 1)
    right_x = x + left_w
    right_w = max(w - left_w, 1)

    draw_box_outline(stdscr, y, x, boxes_h, left_w, theme_pairs.get("border", 0), title="module")
    sample_lines = [
        ("text", theme_pairs.get("text", 0)),
        ("> selected", theme_pairs.get("selected", 0)),
        ("warning!", theme_pairs.get("warning", 0)),
        ("urgent? y/n", theme_pairs.get("urgent", 0)),
    ]
    for i, (text, color) in enumerate(sample_lines):
        row = y + 1 + i
        if row >= y + boxes_h - 1:
            break
        put(row, x + 2, text[:max(left_w - 4, 0)], color)

    draw_box_outline(stdscr, y, right_x, boxes_h, right_w, theme_pairs.get("border_selected", 0), title="active")
    if right_w >= 7 and boxes_h >= 5:
        win_h = min(boxes_h - 3, 4)
        win_w = right_w - 4
        draw_box_outline(stdscr, y + 2, right_x + 2, win_h, win_w, theme_pairs.get("accent", 0), title="win")

    if has_bg_row:
        bg_row = y + boxes_h
        draw_filled_box(stdscr, bg_row, x, 1, w, theme_pairs.get("background", 0))
        put(bg_row, x + 1, "background"[:max(w - 2, 0)], theme_pairs.get("background", 0))


@dataclass
class HelpState:
    active: bool = False
    page: str | None = None
    color_index: int = 0
    color_editing: bool = False
    color_input: str = ""
    color_error: str | None = None


def enter(state: HelpState) -> None:
    state.active = True
    state.page = None
    state.color_index = 0
    state.color_editing = False
    state.color_input = ""
    state.color_error = None


def select_page(state: HelpState, key: int) -> None:
    page = page_for_digit(key)
    if page is not None:
        state.page = page
        state.color_index = 0


def move_color_index(state: HelpState, delta: int) -> None:
    state.color_index = (state.color_index + delta) % len(COLOR_ROLES)


def start_color_edit(state: HelpState, raw_theme_values: dict) -> None:
    role = COLOR_ROLES[state.color_index]
    state.color_editing = True
    state.color_input = format_color_value(raw_theme_values.get(role))
    state.color_error = None


def type_color_key(state: HelpState, key: int) -> bool:
    """Returns still_claiming (== still_editing) — see
    launcher.py's handle_typing_key docstring for why main.py's
    dispatch reads this directly now instead of re-checking
    state.color_editing afterward.
    """
    state.color_input, still_editing = handle_color_input_key(key, state.color_input)
    state.color_editing = still_editing
    if not still_editing:
        state.color_input = ""
        state.color_error = None
    return still_editing


def apply_color_edit(state: HelpState) -> tuple[str, int, str] | None:
    """On success, (role, curses_color, typed_value) — the caller
    (main.py) applies curses_color to cfg.theme/reassign_theme_pairs
    and persists typed_value via config.set_theme_color, since those
    touch cfg/live curses state that belongs to main.py, not this
    module. None on failure — state.color_error is set for display,
    editing stays active so the user can just fix it and retry.
    """
    color, error = parse_color_input(state.color_input)
    if error is not None:
        state.color_error = error
        return None
    role = COLOR_ROLES[state.color_index]
    typed_value = state.color_input.strip()
    state.color_editing = False
    state.color_input = ""
    state.color_error = None
    return role, color, typed_value


def draw(stdscr, term_width, term_height, theme_pairs, state: HelpState,
         raw_keys: dict, raw_power_menu_actions: list, raw_theme_values: dict) -> None:
    """The whole help-menu panel for the current frame — main.py calls
    this instead of draw_all() while state.active is true, same "one
    call, no layout logic at the call site" shape as draw_color_mockup.
    """
    panel_margin = 4
    panel_box = (
        panel_margin, panel_margin,
        max(term_width - panel_margin * 2, 1),
        max(term_height - panel_margin * 2, 1),
    )

    if state.page == "colors":
        px, py, pw, ph = panel_box
        left_w = pw // 2
        left_box = (px, py, left_w, ph)
        right_box = (px + left_w, py, pw - left_w, ph)

        content = color_role_lines(
            state.color_index, raw_theme_values,
            editing=state.color_editing, edit_text=state.color_input, error=state.color_error,
        )
        lines = [(line, theme_pairs.get("text", 0)) for line in content]
        draw_text_panel(stdscr, left_box, lines, theme_pairs.get("border_selected", 0), title="Colors")
        draw_color_mockup(stdscr, right_box, theme_pairs)
        return

    title = "Help"
    if state.page is None:
        content = top_menu_lines()
    elif state.page == "help":
        content = help_lines(raw_keys, raw_power_menu_actions)
    else:  # "resize"
        content = resize_help_lines()
        title = "Resize mode"
    lines = [(line, theme_pairs.get("text", 0)) for line in content]
    draw_text_panel(stdscr, panel_box, lines, theme_pairs.get("border_selected", 0), title=title)
