"""Best-effort extraction of workspace identity from a sway/i3 config's
own text — see CLAUDE/NOTES/design-decisions.md#workspace-config-parsing
for the full reasoning (GitHub issue #9: tuicc only ever showed slots
"1".."total_workspaces", so a workspace named "20" or "chat" — anything
outside that guessed numeric range — silently never appeared).

sway/i3 have no IPC concept of "the set of workspaces this user plans
to use" — a workspace exists only once it's been visited at least once
(get_tree()/get_workspaces() only ever show what currently exists).
The ONLY place that intent is written down at all is the user's own
config file, as bindsym/for_window/assign directives — and the only
IPC window into that text is get_config() (Connection.get_config(),
message type GET_CONFIG), which returns the fully resolved config sway
actually loaded: comments intact, but `include` directives already
expanded and no filesystem path of our own to guess or follow. Parsing
that text is therefore not a fallback to something better — it's the
only source that exists at all, confirmed live against a real running
session (no get_bindings()/equivalent IPC message exists on either
protocol; the only two windows into keybindings are this static text
and the `binding` IPC EVENT, which only ever reports one binding at a
time, reactively, as it's actually pressed).

Two real, load-bearing simplifications, not accidental gaps:
- Bindings inside a NON-default block (mode "..." { ... }, or anything
  else brace-delimited) are skipped — tracked by a flat brace-depth
  counter, not by recognizing "mode" specifically. A custom mode that
  repurposes number keys for something unrelated to workspace
  switching would otherwise falsely look like part of the main
  navigation scheme. The one real cost: this also skips bindsym lines
  genuinely nested for other reasons (rare in practice — workspace
  bindings are essentially always top-level).
- Bindings set up by a script at runtime (`exec` calling `swaymsg
  bindsym ...` dynamically, e.g. per-monitor profiles) are invisible
  here — they never appear in the static config text at all, and (per
  the docstring above) no IPC call exposes them any other way either.
  This is the genuine, unfixable edge of what's knowable — not a
  parser shortcoming.

Callers should always treat this as a helpful DEFAULT, unioned with
whatever regions actually exist right now (modules/sidebar.py's
_build_slots()) — never as the sole source of truth, so a workspace
this parser missed for any reason still shows up the moment it's
genuinely used.
"""

import re
from dataclasses import dataclass, field


@dataclass
class WmConfigInfo:
    # Ordered, de-duplicated workspace targets found in bindsym
    # workspace/move-to-workspace lines — first-seen order, matching
    # the order a user's own numbered/lettered keybinds would suggest.
    workspace_names: list[str] = field(default_factory=list)
    # app_id/class -> workspace name, from for_window/assign rules.
    # Last-match-wins on a genuine duplicate (matches sway's own
    # for_window semantics: later rules can override earlier ones).
    routing_rules: dict[str, str] = field(default_factory=dict)


_SET_RE = re.compile(r'^\s*set\s+(\$\S+)\s+(.+?)\s*$')
_BINDSYM_KEY_RE = re.compile(r'^(?:bindsym|bindcode)\s+((?:--\S+\s+)*)(\S+)')
_WORKSPACE_TARGET_RE = re.compile(
    r'\bworkspace\s+(?:number\s+)?(?:--no-auto-back-and-forth\s+)?'
    r'("(?:[^"\\]|\\.)*"|\$\S+|\S+)'
)
_CRITERIA_RE = re.compile(r'\[([^\]]*)\]')
_CRITERIA_VALUE_RE = re.compile(r'(?:app_id|class)\s*=\s*"([^"]*)"')
# i3's own `assign` grammar — confirmed against i3's real user guide, not
# assumed: unlike sway's for_window (always `move ... to workspace ...`),
# the word "workspace" itself is OPTIONAL here. `assign [criteria] 2` and
# `assign [criteria] → work` are both real, common, documented forms —
# arguably more common in the wild than the explicit-"workspace" one.
# Matches whatever trails the criteria bracket, with an optional arrow;
# `output ...` is a real, different assign target (a physical monitor,
# not a workspace at all) and must NOT be mistaken for one.
_ASSIGN_BARE_TARGET_RE = re.compile(
    r'\]\s*(?:→|->)?\s*(?:number\s+)?("(?:[^"\\]|\\.)*"|\$\S+|\S+)\s*$'
)


def _unquote(token: str) -> str:
    if len(token) >= 2 and token[0] == '"' and token[-1] == '"':
        return token[1:-1]
    return token


def _substitute(token: str, variables: dict[str, str]) -> str:
    return variables.get(token, token) if token.startswith("$") else token


def _strip_comment(line: str) -> str:
    # sway/i3 config comments start a line with (whitespace +) '#' —
    # unlike shell, '#' can't appear mid-line as a comment marker here
    # (colors are written "#rrggbb", which would make a naive "strip
    # from the first #" wrong), so only whole comment LINES are
    # recognized, not partial-line trailing comments.
    return "" if line.lstrip().startswith("#") else line


def parse_wm_config(config_text: str) -> WmConfigInfo:
    """Pure function: config text in, WmConfigInfo out. No IPC — see
    providers/sway.py's/i3.py's wm_config() for the thin wrapper that
    actually calls Connection.get_config() and hands its text here.
    """
    variables: dict[str, str] = {}
    # key combo (e.g. "Mod4+0") -> workspace target. A LATER bindsym for
    # the exact same key combo replaces an earlier one's contribution
    # entirely, not just adds to it — this is sway's own real
    # last-bind-wins rule for a repeated key (common on NixOS/
    # home-manager setups specifically: the module's own default
    # keybindings and a user's extraConfig can both declare the same
    # key independently, get_config() returns both lines, and only the
    # later one is what the key actually does at runtime — but the
    # general rule itself isn't NixOS-specific, any config that binds
    # the same key twice for any reason hits the same sway behavior).
    # Popped and re-inserted on every match, not just overwritten in
    # place, so a key's dict POSITION also tracks its LAST (winning)
    # declaration, not its first — matters for display order: moving a
    # binding later in the file (exactly what a user would do to
    # deliberately reorder it) has to actually move it later here too.
    bindsym_targets: dict[str, str] = {}
    # criteria value (app_id/class) -> workspace target, same
    # last-wins-and-reorders treatment, mirroring routing_rules below
    # (a later for_window/assign for the same app fully replaces the
    # earlier one, in both value and position).
    for_window_targets: dict[str, str] = {}
    routing_rules: dict[str, str] = {}
    depth = 0

    for raw_line in config_text.splitlines():
        line = _strip_comment(raw_line)
        stripped = line.strip()
        if not stripped:
            depth += line.count("{") - line.count("}")
            continue

        set_match = _SET_RE.match(line)
        if set_match:
            variables[set_match.group(1)] = _unquote(set_match.group(2))
            depth += line.count("{") - line.count("}")
            continue

        if depth == 0 and (stripped.startswith("bindsym") or stripped.startswith("bindcode")):
            key_match = _BINDSYM_KEY_RE.match(stripped)
            target_match = _WORKSPACE_TARGET_RE.search(line)
            if key_match and target_match:
                target = _unquote(_substitute(_unquote(target_match.group(1)), variables))
                if target:
                    key_combo = key_match.group(2)
                    bindsym_targets.pop(key_combo, None)
                    bindsym_targets[key_combo] = target

        elif depth == 0 and (stripped.startswith("for_window") or stripped.startswith("assign")):
            criteria_match = _CRITERIA_RE.search(line)
            target_match = _WORKSPACE_TARGET_RE.search(line)
            raw_target = target_match.group(1) if target_match else None
            if raw_target is None and stripped.startswith("assign") and criteria_match:
                # for_window always spells out "move ... to workspace
                # ...", but assign's own "workspace" keyword is
                # genuinely optional (confirmed against i3's real user
                # guide) — `assign [criteria] 2` and `assign [criteria]
                # → work` are both real, common, documented forms.
                # Excludes `assign [criteria] output <thing>` (a
                # physical monitor, not a workspace at all) — that's
                # always two trailing words, which this single-final-
                # token, end-anchored pattern structurally can't match
                # at all, not just a bolted-on special case.
                bare_match = _ASSIGN_BARE_TARGET_RE.search(line)
                if bare_match:
                    raw_target = bare_match.group(1)
            if raw_target and criteria_match:
                target = _unquote(_substitute(_unquote(raw_target), variables))
                value_match = _CRITERIA_VALUE_RE.search(criteria_match.group(1))
                # Keyed by app_id/class when there's one to key by;
                # falls back to the raw criteria text otherwise (e.g.
                # title= alone) — still a real workspace target, just
                # can't be deduped against a later rule the same way.
                dedupe_key = value_match.group(1) if value_match else criteria_match.group(1)
                if target and value_match:
                    routing_rules[value_match.group(1)] = target
                if target:
                    for_window_targets.pop(dedupe_key, None)
                    for_window_targets[dedupe_key] = target

        depth += line.count("{") - line.count("}")

    workspace_names: list[str] = []
    seen_names: set[str] = set()
    for target in list(bindsym_targets.values()) + list(for_window_targets.values()):
        if target not in seen_names:
            seen_names.add(target)
            workspace_names.append(target)

    return WmConfigInfo(workspace_names=workspace_names, routing_rules=routing_rules)


def _leading_number(name: str) -> str | None:
    """The leading run of digits in name (e.g. "8" from "8:VIII"), or
    None if name doesn't start with one at all (a plain name like
    "chat" isn't number-addressable). Matches sway/i3's own `workspace
    number <target>` semantics: <target>'s leading digits are what it
    matches an EXISTING workspace by, or names a NEW one with in full
    if none exists yet — see resolve_workspace_target()'s own docstring
    for why that distinction is what this whole module exists to fix.
    """
    m = re.match(r"^(\d+)", name)
    return m.group(1) if m else None


def resolve_workspace_target(bare_id: str, candidate_names: list[str] | None) -> str:
    """bare_id (a plain workspace number — always what Region.id and a
    saved session's own target_region actually are, see
    providers/sway.py's parse_tree()) resolved to its full configured
    name (e.g. "8:VIII") when candidate_names — wm_config.workspace_names
    or a manual [wm] workspace_names list, same resolution either way —
    has one whose OWN leading number matches. bare_id unchanged if
    candidate_names is empty/None or nothing matches (nothing configured
    to respect, or bare_id isn't purely numeric to begin with).

    Why this matters, not just cosmetic: sway/i3's `workspace number
    <target>` command matches an EXISTING workspace by <target>'s
    leading digits (whatever the rest of its real name is) if one's
    already there, or CREATES a new one named exactly <target> if not.
    Passing the bare number through unresolved works fine for FOCUSING
    an already-existing workspace, but if tuicc is the first thing to
    ever target that number in a session (a launcher spawn or session
    restore onto a workspace nobody's switched to yet), sway creates it
    under the bare number, not the user's configured full name — found
    live, confirmed against a real sway config using numbered+named
    workspaces (`bindsym $mod+8 workspace number 8:VIII`).
    """
    if not candidate_names:
        return bare_id
    for name in candidate_names:
        if _leading_number(name) == bare_id:
            return name
    return bare_id


def get_wm_config(conn) -> WmConfigInfo | None:
    """Thin IPC-issuing wrapper shared by SwayProvider/I3Provider (both
    just delegate their own wm_config() to this — identical either way,
    since it's plain i3ipc.Connection.get_config(), not WM-specific).
    None on any failure (missing get_config support, a malformed
    reply, whatever) — degrades exactly like Provider.resolve_pid()'s
    own None default: callers already union this against real state,
    so returning None just means "no extra defaults today," never a
    crash.
    """
    try:
        reply = conn.get_config()
        return parse_wm_config(reply.config)
    except Exception:
        return None
