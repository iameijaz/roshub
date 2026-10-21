#!/usr/bin/env bash
# End-to-end smoke test: fresh install -> start roshub -> import a folder -> create -> doctor.
# Needs ROS 2 in /opt/ros. Uses a throwaway HOME, so it never touches a real ~/.roshub.
#   tools/ci_smoke.sh                          git checkout: ./install.sh --yes, then roshub
#   ROSHUB_CMD=/path/to/roshub tools/ci_smoke.sh   pip install: roshub setup --yes, then roshub
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
export HOME="$(mktemp -d)"
PORT=8090
B="http://localhost:$PORT"
fail() { echo "FAIL: $*"; [ -f "$HOME/roshub.log" ] && tail -40 "$HOME/roshub.log"; exit 1; }

if [ -n "${ROSHUB_CMD:-}" ]; then
  echo "== roshub setup --yes (pip install)"
  "$ROSHUB_CMD" setup --yes || fail "roshub setup exited $?"
  [ ! -e "$HOME/.local/bin/roshub" ] || fail "pip install must not write ~/.local/bin/roshub"
  START="$ROSHUB_CMD"
else
  echo "== install.sh --yes"
  "$REPO/install.sh" --yes || fail "install.sh exited $?"
  [ -x "$HOME/.local/bin/roshub" ] || fail "roshub launcher not installed"
  START="$HOME/.local/bin/roshub"
fi
grep -q "^port: $PORT" "$HOME/.roshub/config.yaml" || fail "config.yaml missing port"

echo "== start roshub"
"$START" > "$HOME/roshub.log" 2>&1 &
SERVER=$!
trap 'kill $SERVER 2>/dev/null || true' EXIT
for _ in $(seq 30); do curl -sf -o /dev/null "$B/" && break; sleep 1; done
curl -sf "$B/" | grep -q "<title>ROSHub</title>" || fail "home page not served"
curl -sf "$B/api/hub/library" | python3 -c 'import json,sys; assert json.load(sys.stdin)["projects"] == []' \
  || fail "library not empty on a fresh install"

echo "== import a folder (C++ package with src/, the layout that once broke GitHub imports)"
WS="$HOME/demo_ws"
mkdir -p "$WS/src/demo/demo_pkg/src" "$WS/src/demo/demo_pkg/launch"
cat > "$WS/src/demo/demo_pkg/package.xml" <<'EOF'
<?xml version="1.0"?>
<package format="3"><name>demo_pkg</name><version>0.0.1</version><description>demo</description>
<maintainer email="ci@example.com">ci</maintainer><license>MIT</license>
<depend>ros_gz_sim</depend></package>
EOF
touch "$WS/src/demo/demo_pkg/launch/demo_world.launch.py"
curl -sf -X POST -H 'Content-Type: application/json' -d "{\"source\":\"folder\",\"path\":\"$WS\"}" \
  "$B/api/hub/import" > "$HOME/import.json" || fail "import request failed"
python3 - "$HOME/import.json" <<'EOF' || fail "import result wrong: $(cat "$HOME/import.json")"
import json, sys
r = json.load(open(sys.argv[1]))
assert r["ok"], r
assert [p["name"] for p in r["workspace"]["packages"]] == ["demo_pkg"], r["workspace"]
assert r["guess"]["simulator"] == "gazebo", r["guess"]
assert r["guess"]["suggested_launch"] == "demo_pkg demo_world.launch.py", r["guess"]
EOF

echo "== create the project"
python3 - "$HOME/import.json" "$B" <<'EOF' || fail "create failed"
import json, sys, urllib.request
form = json.load(open(sys.argv[1]))["form"]
form.update({"name": "demo", "rmw": "rmw_fastrtps_cpp", "mode": "robust"})
req = urllib.request.Request(sys.argv[2] + "/api/hub/new", json.dumps(form).encode(), {"Content-Type": "application/json"})
r = json.load(urllib.request.urlopen(req))
assert r["ok"], r
EOF
[ -f "$HOME/.roshub/projects/demo.yaml" ] || fail "project file not written"
curl -sf "$B/api/hub/library" | python3 -c 'import json,sys; assert [p["name"] for p in json.load(sys.stdin)["projects"]] == ["demo"]' \
  || fail "project not in library"

echo "== doctor"
curl -sf "$B/api/hub/doctor?deep=0&project=demo" | python3 -c '
import json, sys
r = json.load(sys.stdin)
assert "project_doctor" in r["tool"], r
assert "Workspace built" in r["output"], r["output"]' || fail "doctor did not run"

echo "OK: install, start, import, create and doctor all work"
