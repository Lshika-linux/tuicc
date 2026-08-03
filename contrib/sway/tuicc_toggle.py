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

Setup:
  1. Edit APP_ID/TUICC_MAIN below to match your own setup (or leave
     them as-is if you're following README's documented launch
     command verbatim).
  2. Make this file executable: chmod +x tuicc_toggle.py
  3. Bind a key to it, e.g. (sway config):
       bindsym $mod+Tab exec ~/scripts_sway/tuicc_toggle.py
     replacing whatever direct `exec kitty --app-id ... -e ...` line
     README's "Summoning tuicc" section has you start with.

If you've ALSO added `fullscreen enable` to your own `for_window`
rule for tuicc's window (README's documented setup doesn't, but it's
a common tweak for a truly-fullscreen feel) — note that sway can't
hold a container in genuine fullscreen state while it's hidden in the
scratchpad, so `move scratchpad` silently drops it back to plain
floating; showing it again won't restore fullscreen on its own. This
matters beyond looks: while a container is truly fullscreen, sway
won't let anything else on that workspace steal keyboard focus, which
is what makes spawning from tuicc's launcher reliable after the first
summon (see main.py's `focus_self()` docstring for the same problem
from tuicc's own side — that fix helps, but real fullscreen prevents
the steal outright instead of just correcting it afterward). If you
use fullscreen, chain `, fullscreen enable` onto BOTH the
`scratchpad show` and the plain `focus` swaymsg calls below.
"""
import json
import subprocess

APP_ID = "tuicc_scratch"
TUICC_MAIN = "/path/to/tuicc/main.py"
LAUNCH_CMD = ["kitty", "--app-id", APP_ID, "-e", "python", TUICC_MAIN]


def find_tuicc(node, workspace_name=None):
    if node.get("type") == "workspace":
        workspace_name = node.get("name")
    if node.get("app_id") == APP_ID:
        return node, workspace_name
    for child in node.get("nodes", []) + node.get("floating_nodes", []):
        found = find_tuicc(child, workspace_name)
        if found:
            return found
    return None


def main():
    tree = json.loads(subprocess.check_output(["swaymsg", "-t", "get_tree"]))
    result = find_tuicc(tree)

    if result is None:
        subprocess.Popen(LAUNCH_CMD, start_new_session=True)
        return

    node, workspace_name = result
    if node.get("focused"):
        subprocess.run(["swaymsg", f'[app_id="{APP_ID}"] move scratchpad'])
    elif workspace_name == "__i3_scratch":
        subprocess.run(["swaymsg", f'[app_id="{APP_ID}"] scratchpad show'])
    else:
        subprocess.run(["swaymsg", f'[app_id="{APP_ID}"] focus'])


if __name__ == "__main__":
    main()
