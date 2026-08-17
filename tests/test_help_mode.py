"""Tests for help_mode.py — pure content builders, the color-input
field's key handling/validation, and HelpState (the session-level
layer main.py's loop drives). No curses, no I/O.
"""

from tuicc.help_mode import (
    COLOR_ROLES,
    PAGE_NAMES,
    page_for_digit,
    top_menu_lines,
    help_lines,
    resize_help_lines,
    keybinds_lines,
    shortcuts_lines,
    color_role_lines,
    format_color_value,
    parse_color_input,
    handle_color_input_key,
    HelpState,
    enter,
    select_page,
    move_color_index,
    start_color_edit,
    type_color_key,
    apply_color_edit,
)


# ---------- page_for_digit ----------

def test_page_for_digit_first_and_last_valid():
    assert page_for_digit(ord("1")) == PAGE_NAMES[0]
    assert page_for_digit(ord(str(len(PAGE_NAMES)))) == PAGE_NAMES[-1]


def test_page_for_digit_out_of_range_is_none():
    assert page_for_digit(ord("0")) is None
    assert page_for_digit(ord(str(len(PAGE_NAMES) + 1))) is None


def test_page_for_digit_non_digit_key_is_none():
    assert page_for_digit(ord("a")) is None


# ---------- content builders ----------

def test_top_menu_lines_has_one_entry_per_page():
    lines = top_menu_lines()
    joined = "\n".join(lines)
    for i in range(1, len(PAGE_NAMES) + 1):
        assert f"{i}  " in joined


def test_help_lines_includes_the_faq_and_the_keybinds():
    raw_keys = {"left": "Left", "confirm": "Enter"}
    lines = help_lines(raw_keys, [])
    joined = "\n".join(lines)

    assert "How do I control this thing?" in joined
    assert "My modules look bad" in joined
    assert any("left" in line and "Left" in line for line in lines)
    assert any("confirm" in line and "Enter" in line for line in lines)


def test_help_lines_includes_global_shortcuts_when_present():
    raw_power_menu_actions = [{"label": "Lock", "shortcut": "Ctrl+L"}]
    lines = help_lines({}, raw_power_menu_actions)
    joined = "\n".join(lines)

    assert "Global shortcuts" in joined
    assert any("Lock" in line and "Ctrl+L" in line for line in lines)


def test_resize_help_lines_mentions_the_current_keys():
    joined = "\n".join(resize_help_lines())
    for key in ("F2", "F3", "F4", "F6"):
        assert key in joined


def test_keybinds_lines_shows_the_raw_key_name_not_a_resolved_code():
    # The whole point: no key_label()/resolve_key() round-trip — the
    # string as written in config.toml, verbatim, including a
    # Ctrl+<letter> name key_label() could never recover on its own.
    raw_keys = {"left": "Left", "resize": "F2", "confirm_yes": "Ctrl+Y"}
    lines = keybinds_lines(raw_keys)

    assert any("left" in line and "Left" in line for line in lines)
    assert any("resize" in line and "F2" in line for line in lines)
    assert any("confirm_yes" in line and "Ctrl+Y" in line for line in lines)


def test_shortcuts_lines_shows_the_raw_shortcut_string():
    raw_power_menu_actions = [
        {"label": "Lock", "shortcut": "Ctrl+L"},
        {"label": "Logout", "shortcut": "Ctrl+O"},
    ]
    lines = shortcuts_lines(raw_power_menu_actions)

    assert any("Lock" in line and "Ctrl+L" in line for line in lines)
    assert any("Logout" in line and "Ctrl+O" in line for line in lines)


def test_shortcuts_lines_skips_actions_without_a_shortcut():
    raw_power_menu_actions = [{"label": "Lock", "shortcut": "Ctrl+L"}, {"label": "No shortcut"}]
    lines = shortcuts_lines(raw_power_menu_actions)

    assert len(lines) == 1
    assert "Lock" in lines[0]


def test_shortcuts_lines_empty_when_no_actions():
    assert shortcuts_lines([]) == []


def test_color_role_lines_marks_the_selected_role():
    lines = color_role_lines(selected_index=2, raw_values={})

    selected_line = next(l for l in lines if l.startswith(">"))
    assert COLOR_ROLES[2] in selected_line


def test_color_role_lines_shows_each_roles_current_value():
    raw_values = {"accent": "cyan", "border": [128, 128, 128]}
    lines = color_role_lines(selected_index=0, raw_values=raw_values)

    assert any("accent" in l and "cyan" in l for l in lines)
    assert any("border" in l and "#808080" in l for l in lines)


def test_color_role_lines_editing_shows_typed_text_and_error_inline():
    lines = color_role_lines(
        selected_index=2, raw_values={}, editing=True, edit_text="cy", error="bad color",
    )
    joined = "\n".join(lines)

    assert "cy_" in joined
    assert "bad color" in joined
    # the role being edited no longer shows its old stored value —
    # editing replaces it in place, doesn't show both at once.
    edited_line = next(l for l in lines if l.startswith(">"))
    assert COLOR_ROLES[2] in edited_line
    assert "cy_" in edited_line


def test_color_role_lines_shows_the_preset_message_when_browsing():
    lines = color_role_lines(selected_index=0, raw_values={}, message="Applied Dracula")
    assert "Applied Dracula" in lines


def test_color_role_lines_omits_the_message_line_when_none():
    lines = color_role_lines(selected_index=0, raw_values={})
    assert not any("Applied" in l for l in lines)


def test_color_role_lines_hides_the_message_while_editing():
    # A stale "Applied X" from before Enter was pressed shouldn't leak
    # into the editing view — editing has its own error line instead.
    lines = color_role_lines(selected_index=0, raw_values={}, editing=True, edit_text="cy", message="Applied Dracula")
    assert not any("Applied" in l for l in lines)


# ---------- parse_color_input ----------

def test_parse_color_input_named_color():
    color, error = parse_color_input("cyan")

    assert error is None
    assert color is not None


def test_parse_color_input_hex():
    color, error = parse_color_input("#7dd3fc")

    assert error is None
    assert color is not None


def test_parse_color_input_inherit():
    color, error = parse_color_input("inherit")

    assert error is None
    assert color == -1


def test_parse_color_input_empty_is_an_error_not_a_crash():
    color, error = parse_color_input("   ")

    assert color is None
    assert error is not None


def test_parse_color_input_garbage_is_an_error_not_a_crash():
    color, error = parse_color_input("not-a-color")

    assert color is None
    assert error is not None


# ---------- handle_color_input_key ----------

def test_handle_color_input_key_escape_clears_and_exits():
    text, still_editing = handle_color_input_key(27, "cya")

    assert (text, still_editing) == ("", False)


def test_handle_color_input_key_backspace_removes_last_char():
    text, still_editing = handle_color_input_key(127, "cyan")

    assert (text, still_editing) == ("cya", True)


def test_handle_color_input_key_printable_char_appends():
    text, still_editing = handle_color_input_key(ord("n"), "cya")

    assert (text, still_editing) == ("cyan", True)


# ---------- format_color_value ----------

def test_format_color_value_passes_a_named_color_through():
    assert format_color_value("cyan") == "cyan"


def test_format_color_value_passes_a_hex_string_through():
    assert format_color_value("#7dd3fc") == "#7dd3fc"


def test_format_color_value_passes_inherit_through():
    assert format_color_value("inherit") == "inherit"


def test_format_color_value_converts_an_rgb_list_to_hex():
    assert format_color_value([125, 211, 252]) == "#7dd3fc"


def test_format_color_value_none_is_empty_string():
    assert format_color_value(None) == ""


# ---------- HelpState: enter / select_page ----------

def test_enter_activates_with_a_clean_slate():
    state = HelpState(page="colors", color_index=3, color_editing=True, color_input="x", color_message="Applied Nord")

    enter(state)

    assert state.active is True
    assert state.page is None
    assert state.color_index == 0
    assert state.color_editing is False
    assert state.color_input == ""
    assert state.color_error is None
    assert state.color_message is None


def test_select_page_sets_a_valid_page():
    state = HelpState()

    select_page(state, ord("2"))

    assert state.page == PAGE_NAMES[1]


def test_select_page_ignores_an_invalid_digit():
    state = HelpState()

    select_page(state, ord("9"))

    assert state.page is None


def test_select_page_resets_color_index():
    state = HelpState(color_index=5)

    select_page(state, ord(str(PAGE_NAMES.index("colors") + 1)))

    assert state.color_index == 0


def test_select_page_resets_color_message():
    # A stale "Applied X" from a previous visit to the Colors page
    # shouldn't reappear on the next one.
    state = HelpState(color_message="Applied Nord")

    select_page(state, ord(str(PAGE_NAMES.index("colors") + 1)))

    assert state.color_message is None


# ---------- HelpState: colors browsing ----------

def test_move_color_index_wraps_forward():
    state = HelpState(color_index=len(COLOR_ROLES) - 1)

    move_color_index(state, 1)

    assert state.color_index == 0


def test_move_color_index_wraps_backward():
    state = HelpState(color_index=0)

    move_color_index(state, -1)

    assert state.color_index == len(COLOR_ROLES) - 1


def test_start_color_edit_prefills_from_raw_values():
    state = HelpState(color_index=COLOR_ROLES.index("accent"))

    start_color_edit(state, {"accent": "cyan"})

    assert state.color_editing is True
    assert state.color_input == "cyan"
    assert state.color_error is None


def test_start_color_edit_missing_role_prefills_empty():
    state = HelpState(color_index=COLOR_ROLES.index("accent"))

    start_color_edit(state, {})

    assert state.color_input == ""


# ---------- HelpState: color editing ----------

def test_type_color_key_appends_a_character():
    state = HelpState(color_editing=True, color_input="cya")

    type_color_key(state, ord("n"))

    assert state.color_input == "cyan"
    assert state.color_editing is True


def test_type_color_key_escape_exits_editing():
    state = HelpState(color_editing=True, color_input="cya")

    type_color_key(state, 27)

    assert state.color_editing is False
    assert state.color_input == ""


# ---------- type_color_key: return value (still_claiming) ----------
# VISION.md's R2 input_claim shape — main.py's dispatch reads this
# return value directly to decide whether to release the claim,
# instead of re-checking state.color_editing afterward.

def test_type_color_key_returns_true_while_still_editing():
    state = HelpState(color_editing=True, color_input="cya")

    assert type_color_key(state, ord("n")) is True


def test_type_color_key_returns_false_on_escape():
    state = HelpState(color_editing=True, color_input="cya")

    assert type_color_key(state, 27) is False


def test_apply_color_edit_success_returns_role_color_and_typed_value():
    state = HelpState(color_index=COLOR_ROLES.index("accent"), color_editing=True, color_input=" cyan ")

    result = apply_color_edit(state)

    assert result is not None
    role, color, typed_value = result
    assert role == "accent"
    assert typed_value == "cyan"
    assert state.color_editing is False
    assert state.color_input == ""


def test_apply_color_edit_failure_sets_error_and_stays_editing():
    state = HelpState(color_index=0, color_editing=True, color_input="not-a-color")

    result = apply_color_edit(state)

    assert result is None
    assert state.color_editing is True
    assert state.color_error is not None
