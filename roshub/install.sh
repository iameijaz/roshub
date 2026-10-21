#!/usr/bin/env bash
# ROSHub setup: detect this machine, ask only what can't be detected, write ~/.roshub/config.yaml,
# put `roshub` on PATH. Safe to re-run. Never installs packages or uses sudo.
#   ./install.sh            interactive
#   ./install.sh --yes      accept detected defaults (CI / scripted)
#   ./install.sh --update   only refresh the app files and launcher (after an upgrade)
set -u
YES=0; [ "${1:-}" = "--yes" ] && YES=1
HERE="$(cd "$(dirname "$0")" && pwd)"
RH="${ROSHUB_HOME:-$HOME/.roshub}"
ok()   { printf '  \033[32m✓\033[0m %-22s %s\n' "$1" "$2"; }
warn() { printf '  \033[33m!\033[0m %-22s %s\n' "$1" "$2"; }
bad()  { printf '  \033[31m✕\033[0m %-22s %s\n' "$1" "$2"; }
install_files() {   # copy the app into $RH and write the launcher (no questions)
mkdir -p "$RH"
if [ "$HERE" != "$RH" ]; then
  tar -C "$HERE" -cf - --exclude='./projects' --exclude='./config.yaml' --exclude='./ros_env.sh' . | tar -C "$RH" -xf -
fi
mkdir -p "$RH/bin"
cat > "$RH/bin/roshub" <<EOF
#!/usr/bin/env bash
# roshub: start the ROSHub web UI
RH="\${ROSHUB_HOME:-\$HOME/.roshub}"
PORT=\$(grep -oP '^port:\s*\K\d+' "\$RH/config.yaml" 2>/dev/null || echo 8090)
pkill -f "\$RH/ui_server.py" 2>/dev/null && sleep 1
source "\$RH/ros_env.sh" || { echo "roshub: run install.sh (or: roshub setup) first"; exit 1; }
if grep -qi microsoft /proc/version 2>/dev/null; then
  ( for _ in \$(seq 20); do ss -tln | grep -q ":\$PORT " && break; sleep 0.5; done; explorer.exe "http://localhost:\$PORT" >/dev/null 2>&1 ) &
fi
echo "roshub: http://localhost:\$PORT  (Ctrl+C to stop)"
exec python3 "\$RH/ui_server.py" --port "\$PORT"
EOF
chmod +x "$RH/bin/roshub"
# pip installs have their own `roshub` command (roshub/cli.py) that runs this launcher
[ -n "${ROSHUB_PIP:-}" ] || { mkdir -p "$HOME/.local/bin"; cp "$RH/bin/roshub" "$HOME/.local/bin/roshub"; }
}
ask()  { # ask "question" default -> REPLY
  if [ $YES = 1 ]; then REPLY="$2"; return; fi
  read -r -p "  ? $1 [$2]: " REPLY; REPLY="${REPLY:-$2}"; }
[ "${1:-}" = "--update" ] && { install_files; exit 0; }

echo; echo "ROSHub setup"; echo "────────────"

# ---------------------------------------------------------------- system
. /etc/os-release 2>/dev/null; ok "OS" "${PRETTY_NAME:-unknown}"
WSL=no; grep -qi microsoft /proc/version 2>/dev/null && WSL=yes
[ $WSL = yes ] && ok "WSL2" "yes (Windows simulators can connect over localhost)"
DOCKER=no; [ -f /.dockerenv ] && DOCKER=yes && warn "Docker" "running inside a container: GUIs need X/Wayland forwarding"

GPU="none"
if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then
  GPU="nvidia: $(nvidia-smi --query-gpu=name,driver_version --format=csv,noheader | head -1)"
  ok "GPU" "$GPU"
elif [ $WSL = yes ] && [ -e /dev/dxg ]; then
  GPU="wsl-dxg"; ok "GPU" "WSL GPU passthrough (/dev/dxg)"
elif command -v lspci >/dev/null 2>&1 && lspci | grep -qiE 'vga|3d'; then
  GPU="$(lspci | grep -iE 'vga|3d' | head -1 | cut -d: -f3- | xargs)"; ok "GPU" "$GPU"
else
  warn "GPU" "none detected: Gazebo will be slow, Isaac Sim needs an NVIDIA GPU"
fi

PY=$(python3 -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null || echo none)
if python3 -c 'import sys;sys.exit(sys.version_info<(3,10))' 2>/dev/null; then ok "Python" "$PY"; else bad "Python" "$PY (need 3.10+)"; fi
python3 -c 'import yaml' 2>/dev/null && ok "PyYAML" "installed" || bad "PyYAML" "missing: sudo apt install python3-yaml"

# ---------------------------------------------------------------- ROS
DISTROS=$(ls /opt/ros 2>/dev/null | tr '\n' ' ')
if [ -z "$DISTROS" ]; then
  bad "ROS 2" "not found in /opt/ros: install ROS 2 first (docs.ros.org)"; exit 1
fi
DEF=$(ls /opt/ros | grep -E '^(jazzy|kilted|rolling|humble)$' | head -1); DEF=${DEF:-$(ls /opt/ros | head -1)}
if [ $(ls /opt/ros | wc -l) -gt 1 ]; then ask "Several ROS distros installed ($DISTROS). Use which?" "$DEF"; DISTRO=$REPLY; else DISTRO=$DEF; fi
[ -f "/opt/ros/$DISTRO/setup.bash" ] || { bad "ROS 2" "/opt/ros/$DISTRO/setup.bash missing"; exit 1; }
ok "ROS 2" "$DISTRO"
# shellcheck disable=SC1090
set +u; source "/opt/ros/$DISTRO/setup.bash"; set -u   # ROS setup files read unset variables
command -v rosdep >/dev/null && ok "rosdep" "installed" || warn "rosdep" "missing: sudo apt install python3-rosdep (needed for dependency checks)"
command -v colcon >/dev/null && ok "colcon" "installed" || warn "colcon" "missing: sudo apt install python3-colcon-common-extensions"
command -v git >/dev/null && ok "git" "installed" || warn "git" "missing: needed to import from GitHub"

# ---------------------------------------------------------------- middleware
HAVE=""
for r in rmw_zenoh_cpp rmw_fastrtps_cpp rmw_cyclonedds_cpp; do [ -d "/opt/ros/$DISTRO/share/$r" ] && HAVE="$HAVE $r"; done
ok "Middlewares found" "${HAVE:- none}"
CUR="${RMW_IMPLEMENTATION:-}"
[ -n "$CUR" ] && ok "RMW in this shell" "$CUR"
echo "    Zenoh: most reliable discovery, needs a router (ROSHub starts it). Best for WSL2 and Wi-Fi."
echo "    Fast DDS: ROS 2 default, no router.  Cyclone DDS: popular on real robots."
DEF_RMW=${CUR:-$(echo "$HAVE" | grep -q zenoh && echo rmw_zenoh_cpp || echo rmw_fastrtps_cpp)}
ask "Default middleware for new projects" "$DEF_RMW"; RMW=$REPLY
echo "$HAVE" | grep -qw "$RMW" || warn "Middleware" "$RMW not installed: sudo apt install ros-$DISTRO-${RMW//_/-}"
ask "ROS_DOMAIN_ID (0-101; keep 0 unless other robots share your network)" "${ROS_DOMAIN_ID:-0}"; DOMAIN=$REPLY

# ---------------------------------------------------------------- simulators
SIMS=""
command -v gz >/dev/null && SIMS="$SIMS gazebo" && ok "Gazebo" "$(gz sim --version 2>/dev/null | head -1 || echo found)"
[ -d "/opt/ros/$DISTRO/share/ros_gz_bridge" ] && ok "ros_gz_bridge" "installed" || { echo "$SIMS" | grep -q gazebo && warn "ros_gz_bridge" "missing: sudo apt install ros-$DISTRO-ros-gz"; }
command -v webots >/dev/null && SIMS="$SIMS webots" && ok "Webots" "found"
python3 -c 'import mujoco' 2>/dev/null && SIMS="$SIMS mujoco" && ok "MuJoCo" "python package found"
if [ $WSL = yes ] && ls /mnt/c/isaacsim* /mnt/*/isaacsim* >/dev/null 2>&1; then SIMS="$SIMS isaac_sim"; ok "Isaac Sim" "found on Windows (use attach mode)"; fi
[ -z "$SIMS" ] && warn "Simulators" "none found: projects can still run real robots or plain nodes"

# ---------------------------------------------------------------- folders
ask "Folder for repos imported from GitHub" "$HOME/roshub_ws"; CLONES=$REPLY
ask "Port for the ROSHub web page" "8090"; PORT=$REPLY

# ---------------------------------------------------------------- write config + install
mkdir -p "$RH/projects" "$CLONES"
cat > "$RH/config.yaml" <<EOF
# written by install.sh on $(date -I); re-run ./install.sh to change
ros_distro: $DISTRO
default_rmw: $RMW
ros_domain_id: $DOMAIN
port: $PORT
clone_root: $CLONES
detected:
  os: "${PRETTY_NAME:-unknown}"
  wsl: $WSL
  docker: $DOCKER
  gpu: "$GPU"
  python: "$PY"
  middlewares: [$(echo $HAVE | sed 's/ /, /g')]
  simulators: [$(echo $SIMS | sed 's/ /, /g')]
EOF
cat > "$RH/ros_env.sh" <<EOF
# sourced by ROSHub before every step (written by install.sh)
source /opt/ros/$DISTRO/setup.bash
export RMW_IMPLEMENTATION=$RMW
export ROS_DOMAIN_ID=$DOMAIN
EOF
ok "Config" "$RH/config.yaml"
install_files

[ -n "${ROSHUB_PIP:-}" ] || case ":$PATH:" in *":$HOME/.local/bin:"*) ok "Command" "roshub";; *) warn "Command" "add ~/.local/bin to PATH: echo 'export PATH=\$HOME/.local/bin:\$PATH' >> ~/.bashrc";; esac

echo; echo "Done. Start with:  roshub"
echo "Then: + New project → GitHub URL → https://github.com/ROBOTIS-GIT/turtlebot3_simulations/tree/$DISTRO"
