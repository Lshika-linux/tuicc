#!/usr/bin/env python3
"""Single-keybind summon/dismiss/focus toggle for tuicc on i3.

Same idea as contrib/sway/tuicc_toggle.py — see that file's docstring
for the full explanation of why the "not focused" case needs to check
which workspace currently holds the window, not just call
`scratchpad show` blindly. The only real differences here: i3-msg
instead of swaymsg, and i3's criteria match on `class`, not `app_id`
(i3 is X11-only — kitty's `--app-id` flag sets the X11 WM_CLASS class
from the same invocation, which is what `window_properties.class`
below reads back).

Setup:
  1. Edit APP_ID/TUICC_MAIN below to match your own setup (or leave
     them as-is if you're following README's documented launch
     command verbatim).
  2. Make this file executable: chmod +x tuicc_toggle.py
  3. Bind a key to it, e.g. (i3 config):
       bindsym $mod+Tab exec --no-startup-id ~/scripts_i3/tuicc_toggle.py
     replacing whatever direct `exec --no-startup-id kitty --app-id ...`
     line README's "Summoning tuicc" section has you start with. Don't
     also keep a static `for_window [class="tuicc_scratch"] move
     scratchpad` rule alongside this script — see the for_window note
     in README's toggle-script paragraph for why that combination
     hides tuicc again immediately after this script's first launch,
     before you ever see it. `floating enable` alone is enough; this
     script does the scratchpad move/show itself.
"""
import json
import subprocess

APP_ID = "tuicc_scratch"
TUICC_MAIN = "/path/to/tuicc/main.py"
LAUNCH_CMD = ["kitty", "--app-id", APP_ID, "-e", "python", TUICC_MAIN]


def find_tuicc(node, workspace_name=None):
    if node.get("type") == "workspace":
        workspace_name = node.get("name")
    window_class = (node.get("window_properties") or {}).get("class")
    if window_class == APP_ID:
        return node, workspace_name
    for child in node.get("nodes", []) + node.get("floating_nodes", []):
        found = find_tuicc(child, workspace_name)
        if found:
            return found
    return None


def main():
    tree = json.loads(subprocess.check_output(["i3-msg", "-t", "get_tree"]))
    result = find_tuicc(tree)

    if result is None:
        subprocess.Popen(LAUNCH_CMD, start_new_session=True)
        return

    node, workspace_name = result
    if node.get("focused"):
        subprocess.run(["i3-msg", f'[class="{APP_ID}"] move scratchpad'])
    elif workspace_name == "__i3_scratch":
        subprocess.run(["i3-msg", f'[class="{APP_ID}"] scratchpad show'])
    else:
        subprocess.run(["i3-msg", f'[class="{APP_ID}"] focus'])


if __name__ == "__main__":
    main()