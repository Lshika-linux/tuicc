"""Tests for wm_config_parser.py — see its own module docstring for why
this exists (GitHub issue #9) and what it deliberately can't cover
(runtime/exec-generated bindings).
"""

from tuicc.wm_config_parser import parse_wm_config, get_wm_config


def test_plain_numeric_bindings():
    config = """
bindsym Mod4+1 workspace number 1
bindsym Mod4+2 workspace number 2
bindsym Mod4+Shift+1 move container to workspace number 1
"""
    result = parse_wm_config(config)
    assert result.workspace_names == ["1", "2"]
    assert result.routing_rules == {}


def test_real_config_captured_live_from_this_session():
    """Confirmed live against this session's own real swayfx config
    (2026-08-30) — see chat history. Reproduced here as a fixture so
    the exact repro doesn't depend on a live WM.
    """
    config = """
font pango:Inter 12.000000
workspace_layout default
workspace_auto_back_and_forth no
bindsym Mod4+0 workspace number 10
bindsym Mod4+1 workspace number 1
bindsym Mod4+Shift+0 move container to workspace number 10
bindsym Mod4+Shift+1 move container to workspace number 1
"""
    result = parse_wm_config(config)
    assert result.workspace_names == ["10", "1"]


def test_quoted_workspace_name_with_spaces():
    config = 'bindsym Mod4+1 workspace "1: web"'
    result = parse_wm_config(config)
    assert result.workspace_names == ["1: web"]


def test_variable_substitution():
    config = """
set $ws1 "1: web"
set $ws2 chat
bindsym Mod4+1 workspace $ws1
bindsym Mod4+2 workspace $ws2
"""
    result = parse_wm_config(config)
    assert result.workspace_names == ["1: web", "chat"]


def test_comment_lines_ignored():
    config = """
# bindsym Mod4+9 workspace number 9
bindsym Mod4+1 workspace number 1
"""
    result = parse_wm_config(config)
    assert result.workspace_names == ["1"]


def test_hex_colors_not_mistaken_for_comments():
    config = """
client.focused #ffffff #ffffff #ffffff #ffffff #ffffff
bindsym Mod4+1 workspace number 1
"""
    result = parse_wm_config(config)
    assert result.workspace_names == ["1"]


def test_bindings_inside_mode_block_skipped():
    """The exact concern this module's own docstring calls out: a
    custom mode repurposing number keys must not look like part of the
    main navigation scheme.
    """
    config = """
bindsym Mod4+1 workspace number 1
mode "resize" {
    bindsym Escape mode default
    bindsym 1 workspace number 99
}
bindsym Mod4+2 workspace number 2
"""
    result = parse_wm_config(config)
    assert result.workspace_names == ["1", "2"]
    assert "99" not in result.workspace_names


def test_for_window_rule_builds_routing_map_and_joins_workspace_names():
    config = 'for_window [app_id="discord"] move container to workspace chat'
    result = parse_wm_config(config)
    assert result.routing_rules == {"discord": "chat"}
    assert result.workspace_names == ["chat"]


def test_assign_rule_with_class_criteria():
    config = 'assign [class="Discord"] workspace chat'
    result = parse_wm_config(config)
    assert result.routing_rules == {"Discord": "chat"}


# ---------- i3's own assign grammar ----------
# Confirmed against i3's real user guide (not assumed): unlike sway's
# for_window, the word "workspace" is genuinely OPTIONAL for assign —
# "assign <criteria> [→] [workspace] [number] <target>". The bare and
# arrow forms are arguably the more common idiom in real i3 configs.

def test_assign_bare_target_no_workspace_keyword():
    config = 'assign [class="URxvt"] 2'
    result = parse_wm_config(config)
    assert result.routing_rules == {"URxvt": "2"}
    assert result.workspace_names == ["2"]


def test_assign_arrow_bare_target():
    config = 'assign [class="URxvt"] → 2'
    result = parse_wm_config(config)
    assert result.routing_rules == {"URxvt": "2"}


def test_assign_arrow_named_target():
    config = 'assign [class="URxvt"] → work'
    result = parse_wm_config(config)
    assert result.routing_rules == {"URxvt": "work"}


def test_assign_arrow_number_keyword_target():
    config = 'assign [class="URxvt"] → number 2'
    result = parse_wm_config(config)
    assert result.routing_rules == {"URxvt": "2"}


def test_assign_ascii_arrow_alternative():
    # Not every editor/keyboard makes the real → easy to type; i3
    # itself only documents →, but -> costs nothing extra to accept.
    config = 'assign [class="URxvt"] -> 2'
    result = parse_wm_config(config)
    assert result.routing_rules == {"URxvt": "2"}


def test_assign_output_target_is_not_a_workspace():
    # A real, different assign form — routes to a physical monitor,
    # not a workspace. Must never be mistaken for one.
    config = 'assign [class="URxvt"] output right'
    result = parse_wm_config(config)
    assert result.routing_rules == {}
    assert result.workspace_names == []


def test_for_window_without_criteria_value_ignored():
    """A for_window rule targeting something other than app_id/class
    (e.g. title=) contributes no routing entry — nothing to key it by
    — but its workspace target still counts as a real, intended
    workspace.
    """
    config = 'for_window [title="^Foo$"] move container to workspace chat'
    result = parse_wm_config(config)
    assert result.routing_rules == {}
    assert result.workspace_names == ["chat"]


def test_later_bindsym_for_same_key_combo_wins_and_reorders():
    """Sway's own real rule for a duplicate key, not tuicc guessing —
    common on NixOS/home-manager setups specifically: the module's own
    default keybindings (workspace 10) and a user's extraConfig
    override (renamed to "Chat", moved later in the file) both declare
    Mod4+0 independently. get_config() returns both lines; only the
    later one is what the key actually does. The winning value's
    POSITION should also reflect where the winning line is (last),
    not the stale one's — moving a binding later in the file is how a
    user would deliberately reorder it.
    """
    config = """
bindsym Mod4+0 workspace number 10
bindsym Mod4+1 workspace number 1
bindsym Mod4+2 workspace number 2
bindsym Mod4+0 workspace "Chat"
"""
    result = parse_wm_config(config)
    assert result.workspace_names == ["1", "2", "Chat"]


def test_later_bindsym_for_same_key_combo_wins_with_leading_whitespace():
    """The exact real-world case caught live (2026-08-30): NixOS/
    home-manager's own extraConfig preserves the Nix multi-line
    string's original tab indentation, so the LATER (winning) bindsym
    line is genuinely indented — a first version of this parser
    matched _BINDSYM_KEY_RE against the raw, un-stripped line and
    silently failed to extract a key combo for any indented bindsym at
    all, so the override never took effect and both "10" and "Chat"
    showed up side by side. Confirmed live against the real config
    this bug was found on before writing this fixture.
    """
    config = (
        'bindsym Mod4+0 workspace number 10\n'
        'bindsym Mod4+1 workspace number 1\n'
        '\t  bindsym --to-code Mod4+0 workspace "Chat"\n'
    )
    result = parse_wm_config(config)
    assert result.workspace_names == ["1", "Chat"]


def test_later_for_window_rule_overrides_earlier_for_same_app():
    config = """
for_window [app_id="discord"] move container to workspace chat
for_window [app_id="discord"] move container to workspace social
"""
    result = parse_wm_config(config)
    assert result.routing_rules == {"discord": "social"}


def test_empty_config_returns_empty_info():
    result = parse_wm_config("")
    assert result.workspace_names == []
    assert result.routing_rules == {}


def test_unrelated_workspace_directives_not_mistaken_for_bindings():
    """workspace_layout/workspace_auto_back_and_forth start with
    "workspace" but aren't bindsym/for_window/assign lines at all —
    must never contribute a phantom target.
    """
    config = """
workspace_layout default
workspace_auto_back_and_forth no
"""
    result = parse_wm_config(config)
    assert result.workspace_names == []


class _FakeConfigReply:
    def __init__(self, config):
        self.config = config


class _FakeConn:
    def __init__(self, config_text=None, raises=False):
        self._config_text = config_text
        self._raises = raises

    def get_config(self):
        if self._raises:
            raise RuntimeError("no get_config support")
        return _FakeConfigReply(self._config_text)


def test_get_wm_config_happy_path():
    conn = _FakeConn(config_text="bindsym Mod4+1 workspace number 1")
    result = get_wm_config(conn)
    assert result.workspace_names == ["1"]


def test_get_wm_config_returns_none_on_failure():
    conn = _FakeConn(raises=True)
    assert get_wm_config(conn) is None
