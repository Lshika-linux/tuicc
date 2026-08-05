#!/usr/bin/env bash
# tuicc installer: clone (or update), set up a venv, seed a
# scratchpad-ready config.toml, install a filled-in WM toggle script,
# and either print or (if you opt in) write+reload the WM config
# block you need. No step here silently mutates your WM config
# without asking first — same "no silent fallbacks" principle as the
# rest of tuicc.
set -euo pipefail

REPO_URL="${TUICC_REPO_URL:-https://github.com/Lshika-linux/tuicc}"
INSTALL_DIR="${TUICC_INSTALL_DIR:-$HOME/.local/share/tuicc}"
BIN_DIR="$HOME/.local/bin"
CONFIG_DIR="$HOME/.config/tuicc"
APP_ID="tuicc_scratch"
DEFAULT_KEYBIND='$mod+Tab'

bold() { printf '\033[1m%s\033[0m\n' "$1"; }
note() { printf '  %s\n' "$1"; }

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

WM=""
if [ -n "${SWAYSOCK:-}" ]; then
    WM="sway"
elif [ -n "${I3SOCK:-}" ]; then
    WM="i3"
elif pgrep -x sway >/dev/null 2>&1; then
    WM="sway"
elif pgrep -x i3 >/dev/null 2>&1; then
    WM="i3"
fi

if [ -z "$WM" ]; then
    echo
    echo "Couldn't detect a running sway or i3 session (checked \$SWAYSOCK,"
    echo "\$I3SOCK, and running processes — this is normal if you're"
    echo "installing ahead of time, e.g. from a plain TTY)."
    read -rp "Which one will you use — sway or i3? " WM
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
if [ -n "$DETECTED_TERMINAL" ]; then
    read -rp "Terminal to launch tuicc in [detected: $DETECTED_TERMINAL]: " TERMINAL
    TERMINAL="${TERMINAL:-$DETECTED_TERMINAL}"
else
    echo "Couldn't detect a known terminal in your process tree."
    echo "Known: $KNOWN_TERMINALS"
    read -rp "Terminal to launch tuicc in: " TERMINAL
fi

LAUNCH_CMD_PYLIST=""
while [ -z "$LAUNCH_CMD_PYLIST" ]; do
    if LAUNCH_CMD_PYLIST="$(build_launch_cmd_pylist "$TERMINAL")"; then
        break
    fi
    echo "'$TERMINAL' isn't in the known list ($KNOWN_TERMINALS)."
    read -rp "Pick one of those, or Ctrl+C to install anyway and edit LAUNCH_CMD by hand later: " TERMINAL
    [ -z "$TERMINAL" ] && { note "Skipping toggle-script setup — install.sh will still finish the rest."; break; }
done
bold "Terminal: ${TERMINAL:-(none — toggle script needs manual LAUNCH_CMD)}"

# --- 5. Keybind ---------------------------------------------------------

echo
read -rp "Keybind to summon/dismiss/focus tuicc [default: $DEFAULT_KEYBIND]: " KEYBIND
KEYBIND="${KEYBIND:-$DEFAULT_KEYBIND}"

# --- 6. Seed config.toml, never clobbering one that already exists -------

mkdir -p "$CONFIG_DIR"
if [ -f "$CONFIG_DIR/config.toml" ]; then
    bold "$CONFIG_DIR/config.toml already exists — leaving it untouched."
    note "Make sure [wm] provider = \"$WM\" and self_app_id = \"$APP_ID\" are set"
    note "there yourself if you want the race-free marking this script sets up."
else
    bold "Seeding $CONFIG_DIR/config.toml (provider=$WM, self_app_id=$APP_ID)..."
    cp "$INSTALL_DIR/src/tuicc/defaults/config.toml" "$CONFIG_DIR/config.toml"
    sed -i "s/^provider = .*/provider = \"$WM\"/" "$CONFIG_DIR/config.toml"
    sed -i "s/^self_app_id = .*/self_app_id = \"$APP_ID\"/" "$CONFIG_DIR/config.toml"
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

if [ "$WM" = "sway" ]; then
    WM_CONFIG_PATH="$HOME/.config/sway/config"
    BLOCK=$(cat <<EOF
# tuicc (added by install.sh)
for_window [app_id="$APP_ID"] floating enable
for_window [class="$APP_ID"] floating enable
bindsym $KEYBIND exec $TOGGLE_DST
EOF
)
else
    WM_CONFIG_PATH="$HOME/.config/i3/config"
    BLOCK=$(cat <<EOF
# tuicc (added by install.sh)
for_window [class="$APP_ID"] floating enable
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
WRITE_ANSWER=""
read -rp "Append this block to $WM_CONFIG_PATH and reload $WM now? [y/N]: " WRITE_ANSWER
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