#!/usr/bin/env bash
# tuicc installer: clone (or update), set up a venv, seed a
# scratchpad-ready config.toml, install a filled-in WM toggle script,
# and either print or (if you opt in) write+reload the WM config
# block you need. No step here silently mutates your WM config
# without asking first — same "no silent fallbacks" principle as the
# rest of tuicc.
#
# Every prompt below reads from /dev/tty explicitly, not bare stdin.
# Piped through `curl ... | bash`, bash's own stdin IS the pipe
# carrying this script's remaining source — a bare `read` would
# silently "answer" itself with the next line of the script instead
# of asking you, corrupting both the answer and (once enough reads
# have desynced it) the script's own parsing. TUICC_WM/TUICC_TERMINAL/
# TUICC_KEYBIND/TUICC_WRITE_CONFIG env vars skip the matching prompt
# entirely, for scripted/non-interactive installs.
set -euo pipefail

REPO_URL="${TUICC_REPO_URL:-https://github.com/Lshika-linux/tuicc}"
INSTALL_DIR="${TUICC_INSTALL_DIR:-$HOME/.local/share/tuicc}"
BIN_DIR="$HOME/.local/bin"
CONFIG_DIR="$HOME/.config/tuicc"
APP_ID="tuicc_scratch"
DEFAULT_KEYBIND='$mod+Tab'

bold() { printf '\033[1m%s\033[0m\n' "$1"; }
note() { printf '  %s\n' "$1"; }

# Reads one line from the controlling terminal, if there is one.
# Leaves $REPLY empty (not an error) when there isn't — callers decide
# what that means for them (fall back to a default, or treat it as
# "no answer available").
ask() {
    REPLY=""
    # Open /dev/tty on its own fd first, with only THAT attempt's
    # stderr suppressed (the "No such device or address" bash prints
    # when there's no controlling terminal at all, e.g. under setsid).
    # fd 9, not something low like 3 — this script runs from a file
    # (not `bash -c`), and a low fd number risks colliding with
    # whatever fd bash itself is using to read the script's own
    # remaining source.
    #
    # Prompt printed by hand via printf, deliberately NOT `read -p`:
    # under `curl | bash`, bash starts up genuinely non-interactive
    # (its ORIGINAL stdin is the pipe, not a terminal), and `read -p`
    # decides whether to print its prompt at all based on that
    # shell-wide interactive flag — not on whichever fd a given read
    # call was redirected from. Redirecting the read itself from a
    # real /dev/tty (below) doesn't change that flag, so `read -p`
    # stayed silent at every single prompt even though it was
    # correctly waiting for real input the whole time. Printing the
    # prompt ourselves sidesteps that heuristic entirely.
    # Redirections apply left-to-right — `exec 9</dev/tty 2>/dev/null`
    # would try to open fd 9 BEFORE 2>/dev/null takes effect, so a
    # failed open still prints its error on the way there. Testing
    # openability first with a no-op command (`:`), stderr-suppression
    # listed FIRST so it's already active if the open fails, avoids
    # that — its redirects are scoped to just that command and don't
    # persist, unlike exec's.
    if : 2>/dev/null 9</dev/tty; then
        exec 9</dev/tty
        printf '%s' "$1"
        read -r REPLY <&9
        exec 9<&-
    fi
}

for cmd in git python3; do
    command -v "$cmd" >/dev/null 2>&1 || {
        echo "Error: $cmd is required but not found on PATH." >&2
        exit 1
    }
done

# --- 1. Clone or update -----------------------------------------------

if [ -d "$INSTALL_DIR/.git" ]; then
    bold "tuicc already checked out at $INSTALL_DIR — pulling latest..."
    git -C "$INSTALL_DIR" pull --ff-only
elif [ -e "$INSTALL_DIR" ]; then
    echo "Error: $INSTALL_DIR exists and isn't a git checkout of tuicc." >&2
    echo "Move it aside, or set TUICC_INSTALL_DIR to a different path, and re-run." >&2
    exit 1
else
    bold "Cloning tuicc into $INSTALL_DIR..."
    git clone "$REPO_URL" "$INSTALL_DIR"
fi

# --- 2. Virtualenv + dependencies --------------------------------------

bold "Setting up virtualenv..."
python3 -m venv "$INSTALL_DIR/.venv"
"$INSTALL_DIR/.venv/bin/pip" install --quiet --upgrade pip
"$INSTALL_DIR/.venv/bin/pip" install --quiet -r "$INSTALL_DIR/requirements.txt"
VENV_PYTHON="$INSTALL_DIR/.venv/bin/python"
MAIN_PY="$INSTALL_DIR/main.py"

# --- 3. Detect (or ask for) the WM -------------------------------------

WM="${TUICC_WM:-}"
if [ -z "$WM" ]; then
    if [ -n "${SWAYSOCK:-}" ]; then
        WM="sway"
    elif [ -n "${I3SOCK:-}" ]; then
        WM="i3"
    elif pgrep -x sway >/dev/null 2>&1; then
        WM="sway"
    elif pgrep -x i3 >/dev/null 2>&1; then
        WM="i3"
    fi
fi

if [ -z "$WM" ]; then
    echo
    echo "Couldn't detect a running sway or i3 session (checked \$SWAYSOCK,"
    echo "\$I3SOCK, and running processes — this is normal if you're"
    echo "installing ahead of time, e.g. from a plain TTY)."
    ask "Which one will you use — sway or i3? "
    WM="$REPLY"
    if [ -z "$WM" ]; then
        echo "Error: no interactive terminal to ask, and \$TUICC_WM isn't set." >&2
        echo "Re-run with TUICC_WM=sway (or TUICC_WM=i3) set." >&2
        exit 1
    fi
fi
case "$WM" in
    sway|i3) ;;
    *)
        echo "Error: expected 'sway' or 'i3', got '$WM'." >&2
        exit 1
        ;;
esac
bold "WM: $WM"

# --- 4. Terminal ------------------------------------------------------

# tuicc itself doesn't care what terminal it runs in — this only
# matters for the toggle script's LAUNCH_CMD, which needs to know how
# YOUR terminal sets a stable app_id/WM_CLASS on launch (so the WM
# criteria below can find the window again). Curated, not exhaustive:
# anything else needs a by-hand LAUNCH_CMD edit after install (see the
# installed toggle script's own comment for the shape to copy).
KNOWN_TERMINALS="kitty alacritty foot wezterm-gui wezterm xterm urxvt"

detect_terminal() {
    local pid=$$ hops=0 comm
    while [ "$pid" != "1" ] && [ "$hops" -lt 12 ]; do
        comm=$(ps -o comm= -p "$pid" 2>/dev/null || true)
        for known in $KNOWN_TERMINALS; do
            [ "$comm" = "$known" ] && { echo "$known"; return 0; }
        done
        pid=$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ')
        [ -z "$pid" ] && break
        hops=$((hops + 1))
    done
    return 1
}

build_launch_cmd_pylist() {
    case "$1" in
        kitty)
            printf '["kitty", "--app-id", "%s", "-e", "%s", "%s"]' "$APP_ID" "$VENV_PYTHON" "$MAIN_PY" ;;
        alacritty)
            printf '["alacritty", "--class", "%s", "-e", "%s", "%s"]' "$APP_ID" "$VENV_PYTHON" "$MAIN_PY" ;;
        foot)
            printf '["foot", "--app-id=%s", "%s", "%s"]' "$APP_ID" "$VENV_PYTHON" "$MAIN_PY" ;;
        wezterm|wezterm-gui)
            printf '["wezterm", "start", "--class", "%s", "--", "%s", "%s"]' "$APP_ID" "$VENV_PYTHON" "$MAIN_PY" ;;
        xterm)
            printf '["xterm", "-class", "%s", "-e", "%s", "%s"]' "$APP_ID" "$VENV_PYTHON" "$MAIN_PY" ;;
        urxvt|rxvt-unicode)
            printf '["urxvt", "-name", "%s", "-e", "%s", "%s"]' "$APP_ID" "$VENV_PYTHON" "$MAIN_PY" ;;
        *)
            return 1 ;;
    esac
}

DETECTED_TERMINAL="$(detect_terminal || true)"
echo
if [ -n "${TUICC_TERMINAL:-}" ]; then
    TERMINAL="$TUICC_TERMINAL"
elif [ -n "$DETECTED_TERMINAL" ]; then
    ask "Terminal to launch tuicc in [detected: $DETECTED_TERMINAL]: "
    TERMINAL="${REPLY:-$DETECTED_TERMINAL}"
else
    echo "Couldn't detect a known terminal in your process tree."
    echo "Known: $KNOWN_TERMINALS"
    ask "Terminal to launch tuicc in: "
    TERMINAL="$REPLY"
fi

LAUNCH_CMD_PYLIST=""
if [ -z "$TERMINAL" ]; then
    note "No terminal chosen — toggle script installed with a placeholder"
    note "LAUNCH_CMD; edit it by hand before using the toggle script."
else
    while [ -n "$TERMINAL" ] && [ -z "$LAUNCH_CMD_PYLIST" ]; do
        if LAUNCH_CMD_PYLIST="$(build_launch_cmd_pylist "$TERMINAL")"; then
            break
        fi
        echo "'$TERMINAL' isn't in the known list ($KNOWN_TERMINALS)."
        ask "Pick one of those, or press Enter to skip and edit LAUNCH_CMD by hand later: "
        TERMINAL="$REPLY"
    done
fi
bold "Terminal: ${TERMINAL:-(none — toggle script needs manual LAUNCH_CMD)}"

# --- 5. Keybind ---------------------------------------------------------

echo
KEYBIND="${TUICC_KEYBIND:-}"
if [ -z "$KEYBIND" ]; then
    ask "Keybind to summon/dismiss/focus tuicc [default: $DEFAULT_KEYBIND]: "
    KEYBIND="${REPLY:-$DEFAULT_KEYBIND}"
fi

# --- 5b. Fullscreen ------------------------------------------------------

# Not a hardcoded default anywhere downstream — this answer sets both
# [wm] fullscreen_only in config.toml AND whether the printed/written
# for_window rule includes `fullscreen enable`, and the installed
# toggle script reads fullscreen_only from config.toml itself at
# runtime (see its own docstring) rather than having this baked in.
echo
FULLSCREEN_ANSWER="${TUICC_FULLSCREEN:-}"
if [ -z "$FULLSCREEN_ANSWER" ]; then
    ask "Show tuicc fullscreen instead of a floating window? [Y/n]: "
    FULLSCREEN_ANSWER="$REPLY"
fi
case "$FULLSCREEN_ANSWER" in
    n|N|no|No) FULLSCREEN_ONLY=false ;;
    *) FULLSCREEN_ONLY=true ;;
esac
bold "Fullscreen: $FULLSCREEN_ONLY"

# --- 6. Seed config.toml, never clobbering one that already exists -------

mkdir -p "$CONFIG_DIR"
if [ -f "$CONFIG_DIR/config.toml" ]; then
    bold "$CONFIG_DIR/config.toml already exists — leaving it untouched."
    note "Make sure [wm] provider = \"$WM\", self_app_id = \"$APP_ID\", and"
    note "fullscreen_only = $FULLSCREEN_ONLY are set there yourself if you want"
    note "the race-free marking and fullscreen behavior this script sets up."
else
    bold "Seeding $CONFIG_DIR/config.toml (provider=$WM, self_app_id=$APP_ID, fullscreen_only=$FULLSCREEN_ONLY)..."
    cp "$INSTALL_DIR/src/tuicc/defaults/config.toml" "$CONFIG_DIR/config.toml"
    sed -i "s/^provider = .*/provider = \"$WM\"/" "$CONFIG_DIR/config.toml"
    sed -i "s/^self_app_id = .*/self_app_id = \"$APP_ID\"/" "$CONFIG_DIR/config.toml"
    sed -i "s/^fullscreen_only = .*/fullscreen_only = $FULLSCREEN_ONLY/" "$CONFIG_DIR/config.toml"
    if [ "$WM" = "i3" ]; then
        # The packaged power-menu defaults assume sway (swaylock,
        # swaymsg exit) — see the comment above [[power_menu.action]]
        # in defaults/config.toml. i3lock/i3-msg are the closest i3
        # equivalents; swap the command yourself if you use a
        # different locker.
        sed -i 's/^command = "swaylock"$/command = "i3lock"/' "$CONFIG_DIR/config.toml"
        sed -i 's/^command = "swaymsg exit"$/command = "i3-msg exit"/' "$CONFIG_DIR/config.toml"
        note "Swapped power-menu Lock/Logout for i3lock/i3-msg exit (the"
        note "packaged defaults assume sway) — edit config.toml if you use"
        note "a different locker."
    fi
fi

# --- 7. Install the WM-specific toggle script ------------------------------

TOGGLE_SRC="$INSTALL_DIR/contrib/$WM/tuicc_toggle.py"
TOGGLE_DST="$BIN_DIR/tuicc_toggle.py"
if [ ! -f "$TOGGLE_SRC" ]; then
    echo "Error: expected $TOGGLE_SRC to exist — tuicc checkout looks incomplete." >&2
    exit 1
fi

mkdir -p "$BIN_DIR"
bold "Installing toggle script to $TOGGLE_DST..."
if [ -n "$LAUNCH_CMD_PYLIST" ]; then
    sed \
        -e "s#^TUICC_MAIN = .*#TUICC_MAIN = \"$MAIN_PY\"#" \
        -e "s#^LAUNCH_CMD = .*#LAUNCH_CMD = $LAUNCH_CMD_PYLIST#" \
        "$TOGGLE_SRC" > "$TOGGLE_DST"
else
    sed "s#^TUICC_MAIN = .*#TUICC_MAIN = \"$MAIN_PY\"#" "$TOGGLE_SRC" > "$TOGGLE_DST"
    note "LAUNCH_CMD left as-is in $TOGGLE_DST — edit it by hand before using this script."
fi
chmod +x "$TOGGLE_DST"

# --- 8. Build the WM config block (used for both printing and writing) -----

if [ "$FULLSCREEN_ONLY" = "true" ]; then
    FLOAT_RULE="floating enable, fullscreen enable"
else
    FLOAT_RULE="floating enable"
fi

if [ "$WM" = "sway" ]; then
    WM_CONFIG_PATH="$HOME/.config/sway/config"
    BLOCK=$(cat <<EOF
# tuicc (added by install.sh)
for_window [app_id="$APP_ID"] $FLOAT_RULE
for_window [class="$APP_ID"] $FLOAT_RULE
bindsym $KEYBIND exec $TOGGLE_DST
EOF
)
else
    WM_CONFIG_PATH="$HOME/.config/i3/config"
    BLOCK=$(cat <<EOF
# tuicc (added by install.sh)
for_window [class="$APP_ID"] $FLOAT_RULE
bindsym $KEYBIND exec --no-startup-id $TOGGLE_DST
EOF
)
fi

echo
bold "WM config block for $WM_CONFIG_PATH:"
echo
echo "$BLOCK"
echo
note "No 'move scratchpad' rule needed — $TOGGLE_DST does that itself"
note "on the first launch; a static rule would hide tuicc before you"
note "ever see it."

# --- 9. Offer to write it in + reload, but only if you say so --------------

echo
WRITE_ANSWER="${TUICC_WRITE_CONFIG:-}"
if [ -z "$WRITE_ANSWER" ]; then
    ask "Append this block to $WM_CONFIG_PATH and reload $WM now? [y/N]: "
    WRITE_ANSWER="$REPLY"
fi
case "$WRITE_ANSWER" in
    y|Y|yes|Yes)
        if [ -f "$WM_CONFIG_PATH" ] && grep -qF "$APP_ID" "$WM_CONFIG_PATH"; then
            bold "$WM_CONFIG_PATH already mentions '$APP_ID' — not appending again."
            note "Check it by hand; the block above is what install.sh would have added."
        else
            mkdir -p "$(dirname "$WM_CONFIG_PATH")"
            {
                echo
                echo "$BLOCK"
            } >> "$WM_CONFIG_PATH"
            bold "Appended to $WM_CONFIG_PATH."
            if [ "$WM" = "sway" ] && [ -n "${SWAYSOCK:-}" ]; then
                swaymsg reload >/dev/null
                bold "sway reloaded — no manual reload needed."
            elif [ "$WM" = "i3" ] && [ -n "${I3SOCK:-}" ]; then
                i3-msg reload >/dev/null
                bold "i3 reloaded — no manual reload needed."
            else
                note "Not currently inside a live $WM session, so it wasn't reloaded —"
                note "it'll take effect next time $WM starts, or run '$WM reload' yourself."
            fi
        fi
        ;;
    *)
        note "Not written — paste the block above into $WM_CONFIG_PATH and reload"
        note "$WM yourself whenever you're ready."
        ;;
esac

echo
bold "Done. Press $KEYBIND (after reloading, if you pasted it by hand) to summon tuicc."