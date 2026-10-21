#!/usr/bin/env bash
# ROSHub: make sure a colcon workspace is ready before a project launches.
#   ensure_built.sh <workspace>
# 1. dependency gate (rosdep)  2. build only if needed  3. clean env (no chained overlays)
# Exit codes: 0 ready, 2 bad workspace, 3 missing deps, 4 build failed.
ws="${1:?usage: ensure_built.sh <workspace>}"
ws="${ws/#\~/$HOME}"
distro="${ROS_DISTRO:-jazzy}"
[ -d "$ws/src" ] || { echo "ROSHub: no src/ in $ws"; exit 2; }

if command -v rosdep >/dev/null; then
  missing=$(rosdep check --from-paths "$ws/src" --ignore-src -r 2>&1 | grep -oP '^apt\s+\K\S+' | sort -u)
  if [ -n "$missing" ]; then
    echo "ROSHub: missing dependencies ($(echo "$missing" | wc -l)):"
    echo "$missing" | sed 's/^/  - /'
    echo
    echo "Install them in a terminal, then press 'Retry this step':"
    echo "  cd $ws && rosdep install --from-paths src --ignore-src -r -y"
    exit 3
  fi
fi

stamp="$ws/install/.roshub_built"
if [ -f "$ws/install/setup.bash" ] && [ -f "$stamp" ] &&
   [ -z "$(find "$ws/src" -newer "$stamp" -type f -not -path '*/.git/*' -print -quit)" ]; then
  echo "ROSHub: workspace up to date"
  exit 0
fi

echo "ROSHub: building $ws in a clean environment (first build can take several minutes)"
env -i HOME="$HOME" USER="$USER" PATH="/usr/local/bin:/usr/bin:/bin" TERM=dumb \
  bash -c "source /opt/ros/$distro/setup.bash && cd '$ws' && colcon build --symlink-install --event-handlers console_direct+" \
  || { echo "ROSHub: build FAILED - see the errors above"; exit 4; }
touch "$stamp"
echo "ROSHub: build OK"
