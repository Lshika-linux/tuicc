#!/usr/bin/env python3
"""Single-keybind summon/dismiss/focus toggle for tuicc on sway.

Bind ONE key to this script instead of tuicc's plain launch command
(see README's "Summoning tuicc" section) and it picks the right action
based on tuicc's current state:

  - not running at all          -> launch it
  - running and currently focused -> dismiss it (move to scratchpad)
  - running but not focused        -> bring it to focus (un-hiding it
                                       from the scratchpad if that's
                                       where it's currently parked, or
                                       just switching to it otherwise)

The "not focused" case needs to know WHICH of those two situations
it's in — sway's own `scratchpad show` command toggles based on
"is this window currently shown", not "is it focused", so blindly
calling it on a window that's already visible-but-unfocused would
hide it again instead of focusing it. Checking which workspace
currently contains the window (sway's scratchpad lives under the
pseudo-workspace "__i3_scratch") disambiguates the two cases.

tuicc doesn't require any particular terminal — LAUNCH_CMD below just
needs to run one that can set a stable app_id/WM_CLASS on launch (most
can). Native-Wayland terminals (kitty, alacritty, foot, wezterm...)
report as `app_id`; an XWayland one (xterm, urxvt...) reports as
`class` instead — sway's `for_window [app_id=...]` criteria won't
match the latter at all, `[class=...]` won't match the former, and a
`for_window` block can't OR the two together. So this script (and the
matching for_window setup below) always checks/targets BOTH — one of
each pair matches, the other is a harmless no-op, and neither this
script nor your WM config needs to know or care which kind of client
you picked.

Setup:
  1. Edit APP_ID/TUICC_MAIN/LAUNCH_CMD below to match your own setup
     (install.sh does this for you, filling in LAUNCH_CMD for
     whichever terminal you choose).
  2. Make this file executable: chmod +x tuicc_toggle.py
  3. Bind a key to it, e.g. (sway config):
       for_window [app_id="tuicc_scratch"] floating enable
       for_window [class="tuicc_scratch"] floating enable
       bindsym $mod+Tab exec ~/scripts_sway/tuicc_toggle.py
     replacing whatever direct `exec kitty --app-id ... -e ...` line
     README's "Summoning tuicc" section has you start with. Don't also
     keep a static `for_window ... move scratchpad` rule alongside
     this script — that rule hides tuicc the instant it maps, before
     this script's first launch ever gets to show it. `floating
     enable` alone is enough; this script does the scratchpad
     move/show itself.

Fullscreen is config-driven, not hardcoded here: this script reads
`[wm] fullscreen_only` from tuicc's own ~/.config/tuicc/config.toml
(install.sh sets this for you, matching whatever you answer its own
fullscreen prompt) and, if true, chains `, fullscreen enable` onto the
"scratchpad show"/"focus" actions below (never onto "move scratchpad"
— you're hiding it there, not showing it). Why re-assert it at all,
rather than just adding `fullscreen enable` to the for_window rule
above and leaving it at that: sway drops a container back to plain
floating the instant ANY new window is mapped anywhere in the session
if it happens to land on tuicc's own workspace first — which new
windows typically do, briefly, before anything gets a chance to move
them elsewhere (see main.py's pending_moves.py for the tuicc-side half
of this same fix). Without re-asserting it here too, a fullscreen
tuicc silently drops to floating on your first launcher spawn or
session restore after every summon. This also matters beyond looks:
while a container is truly fullscreen, sway won't let anything else on
that workspace steal keyboard focus, which is what makes spawning from
tuicc's launcher reliable (see main.py's `focus_self()` docstring for
the same problem from tuicc's own side — that fix helps, but real
fullscreen prevents the steal outright instead of just correcting it
afterward). If `fullscreen_only` is false (the default) or your
config.toml can't be read for any reason, this script just does plain
floating show/focus — same as if fullscreen didn't exist here at all.
"""
import json
import subprocess
import tomllib
from pathlib import Path

APP_ID = "tuicc_scratch"
TUICC_MAIN = "/path/to/tuicc/main.py"
LAUNCH_CMD = ["kitty", "--app-id", APP_ID, "-e", "python", TUICC_MAIN]
CONFIG_PATH = Path.home() / ".config" / "tuicc" / "config.toml"


def _fullscreen_only() -> bool:
    """Same default (False) and same "no config file yet = defaults"
    behavior as tuicc's own config.py — this script deliberately
    doesn't create or require config.toml to exist.
    """
    try:
        with open(CONFIG_PATH, "rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return False
    return bool(data.get("wm", {}).get("fullscreen_only", False))


def find_tuicc(node, workspace_name=None):
    if node.get("type") == "workspace":
        workspace_name = node.get("name")
    window_class = (node.get("window_properties") or {}).get("class")
    if node.get("app_id") == APP_ID or window_class == APP_ID:
        return node, workspace_name
    for child in node.get("nodes", []) + node.get("floating_nodes", []):
        found = find_tuicc(child, workspace_name)
        if found:
            return found
    return None


def _run_for_both_criteria(action: str) -> None:
    # Exactly one of these ever matches a real window (see the module
    # docstring) — the other selects nothing and is a no-op, not an
    # error, so running both unconditionally is safe and simpler than
    # figuring out in advance which one applies.
    subprocess.run(["swaymsg", f'[app_id="{APP_ID}"] {action}'])
    subprocess.run(["swaymsg", f'[class="{APP_ID}"] {action}'])


def main():
    tree = json.loads(subprocess.check_output(["swaymsg", "-t", "get_tree"]))
    result = find_tuicc(tree)

    if result is None:
        subprocess.Popen(LAUNCH_CMD, start_new_session=True)
        return

    node, workspace_name = result
    suffix = ", fullscreen enable" if _fullscreen_only() else ""
    if node.get("focused"):
        _run_for_both_criteria("move scratchpad")
    elif workspace_name == "__i3_scratch":
        _run_for_both_criteria(f"scratchpad show{suffix}")
    else:
        _run_for_both_criteria(f"focus{suffix}")


if __name__ == "__main__":
    main()
