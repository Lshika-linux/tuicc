"""Tests for modules/control.py's pure logic (_state_index,
_toggle_dot_color, nav_items). draw() needs a real curses screen, left
untested here, same as every other module (see test_power_menu.py's
own docstring for this repo's established stance on that).
"""

from types import SimpleNamespace

from tuicc.modules.control import (
    _state_index, _toggle_dot_color, _row_kind, _is_binary, _dot_glyph, _row_suffix, nav_items,
)


# ---------- _is_binary ----------

def test_is_binary_true_for_2_states():
    assert _is_binary([{"name": "on"}, {"name": "off"}]) is True


def test_is_binary_false_for_3_or_more_states():
    assert _is_binary([{"name": "a"}, {"name": "b"}, {"name": "c"}]) is False


# ---------- _dot_glyph ----------

def test_dot_glyph_binary_filled_for_first_declared_state():
    assert _dot_glyph(is_binary=True, current_name="on", state_index=0) == "●"


def test_dot_glyph_binary_outline_for_second_declared_state():
    assert _dot_glyph(is_binary=True, current_name="off", state_index=1) == "○"


def test_dot_glyph_binary_outline_when_state_unknown():
    assert _dot_glyph(is_binary=True, current_name=None, state_index=None) == "○"


def test_dot_glyph_cycle_filled_whenever_a_state_is_known():
    # N-way cycles keep the old "known vs unknown" rule — color (not
    # fill) distinguishes which of 3+ states it's actually in.
    assert _dot_glyph(is_binary=False, current_name="balanced", state_index=1) == "●"


def test_dot_glyph_cycle_outline_when_unknown():
    assert _dot_glyph(is_binary=False, current_name=None, state_index=None) == "○"


# ---------- _row_suffix ----------

def test_row_suffix_binary_toggle_has_no_text_suffix():
    # The dot alone already says on/off — a text suffix would be
    # redundant.
    assert _row_suffix("normal", is_binary=True, current_name="on", error=None) == ""
    assert _row_suffix("normal", is_binary=True, current_name="off", error=None) == ""


def test_row_suffix_cycle_shows_the_state_name():
    assert _row_suffix("normal", is_binary=False, current_name="balanced", error=None) == " [balanced]"


def test_row_suffix_error_kinds_outrank_binary_suppression():
    assert _row_suffix("action_error", is_binary=True, current_name="on", error=None) == " [ERROR]"
    assert _row_suffix("poll_error", is_binary=True, current_name=None, error="boom") == " (boom)"


# ---------- _row_kind ----------

def test_row_kind_pending_outranks_everything():
    assert _row_kind(current_name="on", error="poll broke", action_error="cmd broke", pending=True) == "pending"


def test_row_kind_action_error_outranks_poll_error():
    # The last thing the user DID is more relevant than the last thing
    # that was merely observed independently.
    assert _row_kind(current_name="on", error="poll broke", action_error="cmd broke", pending=False) == "action_error"


def test_row_kind_poll_error_only_when_current_name_is_none():
    assert _row_kind(current_name=None, error="poll broke", action_error=None, pending=False) == "poll_error"


def test_row_kind_no_poll_error_reported_if_current_name_is_known():
    # A stale error from before the current (successful) poll must not
    # linger — get_error() already returns None once its own poll
    # succeeds, but this guards the row-kind decision itself too.
    assert _row_kind(current_name="on", error="stale", action_error=None, pending=False) == "normal"


def test_row_kind_normal_when_nothing_is_wrong():
    assert _row_kind(current_name="on", error=None, action_error=None, pending=False) == "normal"
    assert _row_kind(current_name=None, error=None, action_error=None, pending=False) == "normal"


# ---------- _state_index ----------

def test_state_index_finds_the_matching_state():
    states = [{"name": "off"}, {"name": "on"}]
    assert _state_index(states, "on") == 1
    assert _state_index(states, "off") == 0


def test_state_index_none_when_current_name_is_none():
    states = [{"name": "off"}, {"name": "on"}]
    assert _state_index(states, None) is None


def test_state_index_none_when_current_name_matches_nothing():
    states = [{"name": "off"}, {"name": "on"}]
    assert _state_index(states, "unknown") is None


# ---------- _toggle_dot_color ----------

def _ctx_with_colors(colors):
    return SimpleNamespace(control_colors=colors)


def test_toggle_dot_color_uses_the_configured_pair():
    ctx = _ctx_with_colors({(0, 1): 99})
    theme = {"accent": 5}
    assert _toggle_dot_color(ctx, toggle_index=0, state_index=1, theme=theme) == 99


def test_toggle_dot_color_falls_back_to_theme_accent_when_state_index_is_none():
    ctx = _ctx_with_colors({(0, 1): 99})
    theme = {"accent": 5}
    assert _toggle_dot_color(ctx, toggle_index=0, state_index=None, theme=theme) == 5


def test_toggle_dot_color_falls_back_to_theme_accent_when_state_has_no_configured_color():
    ctx = _ctx_with_colors({})  # this state never set `color`
    theme = {"accent": 5}
    assert _toggle_dot_color(ctx, toggle_index=0, state_index=1, theme=theme) == 5


# ---------- nav_items ----------

class _FakeStatus:
    def __init__(self, snapshots, action_errors=None):
        self._snapshots = snapshots
        self._action_errors = action_errors or {}

    def get(self, domain_name):
        return self._snapshots.get(domain_name)

    def get_action_error(self, domain_name):
        return self._action_errors.get(domain_name)


def _fake_ctx(toggles, snapshots=None, action_errors=None):
    return SimpleNamespace(
        config=SimpleNamespace(control_toggles=toggles),
        status=_FakeStatus(snapshots or {}, action_errors),
        theme={},
    )


def test_nav_items_one_row_per_toggle():
    toggles = [
        {"label": "Night Light", "shell_true": False,
         "states": [{"name": "on", "command": "turn-on"}, {"name": "off", "command": "turn-off"}]},
        {"label": "Airplane Mode", "shell_true": True,
         "states": [{"name": "on", "command": "turn-on"}, {"name": "off", "command": "turn-off"}]},
    ]
    ctx = _fake_ctx(toggles)
    box = (0, 0, 20, 10)

    items = nav_items(box, ctx, "control")

    assert [item.id for item in items] == ["control:0", "control:1"]
    assert items[0].target_kind == "control_toggle"
    assert items[0].rect[1] == 1  # y + 1, first row inside the border


def test_nav_items_preview_shows_current_and_next_state():
    toggles = [{"label": "Night Light", "shell_true": False,
                "states": [{"name": "on", "command": "turn-on"}, {"name": "off", "command": "turn-off"}]}]
    ctx = _fake_ctx(toggles, snapshots={"toggle:0": "on"})
    box = (0, 0, 20, 10)

    items = nav_items(box, ctx, "control")

    preview = items[0].preview_text[0][0]
    assert "on" in preview and "off" in preview


def test_nav_items_preview_shows_the_command_that_will_run():
    # A line between the
    # description and the SHELL=TRUE warning, showing exactly what
    # will execute — same "show the real command" idea as
    # quick_actions.py's own preview_text.
    toggles = [{"label": "Night Light", "shell_true": False,
                "states": [{"name": "on", "command": "gammastep"},
                           {"name": "off", "command": "pkill -x gammastep"}]}]
    ctx = _fake_ctx(toggles, snapshots={"toggle:0": "on"})
    box = (0, 0, 20, 10)

    items = nav_items(box, ctx, "control")

    lines = [text for text, _color in items[0].preview_text]
    assert lines[0].startswith(">")  # description line, unchanged
    assert lines[1] == "$ pkill -x gammastep"  # the next state's command


def test_nav_items_preview_flags_shell_true():
    toggles = [{"label": "Mic Mute", "shell_true": True,
                "states": [{"name": "on", "command": "turn-on"}, {"name": "off", "command": "turn-off"}]}]
    ctx = _fake_ctx(toggles)
    box = (0, 0, 20, 10)

    items = nav_items(box, ctx, "control")

    assert any("SHELL=TRUE" in text for text, _color in items[0].preview_text)


def test_nav_items_preview_includes_the_action_error_when_present():
    # See CLAUDE/VISION.md's R3 section — a failed command (gammastep
    # exiting immediately for lack of GeoClue2) must reach the preview
    # pane, not stay invisible.
    toggles = [{"label": "Night Light", "shell_true": False,
                "states": [{"name": "on", "command": "gammastep"}, {"name": "off", "command": "pkill -x gammastep"}]}]
    ctx = _fake_ctx(toggles, action_errors={"toggle:0": "Error: GeoClue2 provider is not installed!"})
    box = (0, 0, 20, 10)

    items = nav_items(box, ctx, "control")

    assert any("GeoClue2" in text for text, _color in items[0].preview_text)


def test_nav_items_preview_splits_a_multiline_action_error_into_separate_entries():
    # gammastep's real stderr is 3 separate "Error: ..." lines.
    # draw_centered_lines (render_utils.py) positions each
    # preview_text ENTRY as its own row via x/y math — a single entry
    # holding an embedded "\n" makes curses jump to column 0 of the
    # next real terminal row on that "\n", escaping the preview box
    # entirely instead of staying centered like every other line.
    multiline_error = (
        "Error: GeoClue2 provider is not installed!\n"
        "Error: Failed to start provider: geoclue2\n"
        "Error: Latitude and longitude must be set."
    )
    toggles = [{"label": "Night Light", "shell_true": False,
                "states": [{"name": "on", "command": "gammastep"}, {"name": "off", "command": "pkill -x gammastep"}]}]
    ctx = _fake_ctx(toggles, action_errors={"toggle:0": multiline_error})
    box = (0, 0, 20, 10)

    items = nav_items(box, ctx, "control")

    error_lines = [text for text, _color in items[0].preview_text if text.startswith("⚠")]
    assert error_lines == [
        "⚠ Error: GeoClue2 provider is not installed!",
        "⚠ Error: Failed to start provider: geoclue2",
        "⚠ Error: Latitude and longitude must be set.",
    ]
    assert all("\n" not in text for text, _color in items[0].preview_text)


def test_nav_items_preview_has_no_error_line_when_nothing_failed():
    toggles = [{"label": "Night Light", "shell_true": False,
                "states": [{"name": "on", "command": "gammastep"}, {"name": "off", "command": "pkill -x gammastep"}]}]
    ctx = _fake_ctx(toggles)
    box = (0, 0, 20, 10)

    items = nav_items(box, ctx, "control")

    assert not any("⚠" in text for text, _color in items[0].preview_text)


def test_nav_items_stops_at_the_box_bottom():
    toggles = [
        {"label": f"Toggle {i}", "shell_true": False, "states": [{"name": "on", "command": "turn-on"}, {"name": "off", "command": "turn-off"}]}
        for i in range(10)
    ]
    ctx = _fake_ctx(toggles)
    box = (0, 0, 20, 4)  # h=4 -> only 2 inner rows fit

    items = nav_items(box, ctx, "control")

    assert len(items) == 2
