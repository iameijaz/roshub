"""Add projects to ROSHub from a local folder or a GitHub URL.

Flow: import_source() -> a workspace entry (same shape as hub.scan_workspaces())
plus guesses -> the UI shows it for review -> the existing /api/hub/new saves it.
Nothing from the repo is executed and nothing is installed here.
"""
import os
import re
import subprocess

import yaml

EXTRA_FILE = os.path.expanduser("~/.roshub/workspaces_extra.yaml")


def _clone_root():
    """clone_root from ~/.roshub/config.yaml (asked by install.sh), else ~/roshub_ws."""
    try:
        with open(os.path.expanduser("~/.roshub/config.yaml")) as f:
            return os.path.expanduser(str((yaml.safe_load(f) or {}).get("clone_root") or "~/roshub_ws"))
    except (OSError, yaml.YAMLError):
        return os.path.expanduser("~/roshub_ws")


CLONE_ROOT = _clone_root()
_GH_RE = re.compile(r"^(?:https://github\.com/|git@github\.com:)([\w.-]+)/([\w.-]+?)(?:\.git)?"
                    r"(?:/tree/([\w./-]+))?/?$")

SIM_HINTS = [("isaac_sim", ("isaac", "isaac_ros")), ("gazebo", ("ros_gz", "gazebo_ros", "gz_ros2")),
             ("webots", ("webots_ros2",)), ("mujoco", ("mujoco",))]
ARM_HINTS = ("joint_trajectory_controller", "moveit", "ros2_control", "controller_manager")
MOBILE_HINTS = ("diff_drive_controller", "nav2", "navigation2", "slam_toolbox", "cmd_vel")


# ------------------------------------------------------------- extra workspaces
def extra_workspaces():
    try:
        with open(EXTRA_FILE) as f:
            return list((yaml.safe_load(f) or {}).get("paths", []))
    except FileNotFoundError:
        return []


def remember_workspace(path):
    paths = extra_workspaces()
    path = os.path.realpath(os.path.expanduser(path))
    if path not in paths:
        paths.append(path)
        os.makedirs(os.path.dirname(EXTRA_FILE), exist_ok=True)
        tmp = EXTRA_FILE + ".tmp"
        with open(tmp, "w") as f:
            yaml.safe_dump({"paths": paths}, f)
        os.replace(tmp, EXTRA_FILE)          # atomic, same as the registry fix
    return path


# ------------------------------------------------------------------ resolving
def workspace_root(path):
    """Accept a workspace root, a src/ dir, a package dir or a repo inside src/."""
    p = os.path.realpath(os.path.expanduser(path))
    if not os.path.isdir(p):
        raise ValueError(f"not a folder: {path}")
    cur = p
    for _ in range(6):                       # walk up looking for <ws>/src
        if os.path.isdir(os.path.join(cur, "src")) and os.path.basename(cur) != "src":
            return cur
        if os.path.basename(cur) == "src":
            return os.path.dirname(cur)
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    raise ValueError(f"no colcon workspace (a folder with src/) found at or above {path}")


def parse_github(url):
    m = _GH_RE.match(url.strip())
    if not m:
        raise ValueError("use https://github.com/<owner>/<repo> or git@github.com:<owner>/<repo>.git")
    owner, repo, branch = m.groups()
    return owner, repo, branch


def clone(url, dest_root=CLONE_ROOT):
    owner, repo, branch = parse_github(url)
    ws = os.path.join(dest_root, repo)
    target = os.path.join(ws, "src", repo)
    https = f"https://github.com/{owner}/{repo}.git"
    if os.path.exists(target) and os.listdir(target):
        r = subprocess.run(["git", "-C", target, "remote", "get-url", "origin"], capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip().rstrip("/").removesuffix(".git") == https.removesuffix(".git"):
            commit = subprocess.run(["git", "-C", target, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
            if commit:                      # complete clone of the same repo: reuse it
                return ws, {"url": https, "branch": branch, "commit": commit, "reused": True}
        raise ValueError(f"already exists and is not a clone of {https}: {target} - "
                         "delete it or choose Folder to import it as it is")
    os.makedirs(os.path.dirname(target), exist_ok=True)
    https = f"https://github.com/{owner}/{repo}.git"
    cmd = ["git", "clone", "--depth", "1"] + (["--branch", branch] if branch else []) + [https, target]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300,
                       env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))
    if r.returncode != 0:
        raise RuntimeError(f"git clone failed: {r.stderr.strip()[-400:]}")
    commit = subprocess.run(["git", "-C", target, "rev-parse", "HEAD"],
                            capture_output=True, text=True).stdout.strip()
    return ws, {"url": https, "branch": branch, "commit": commit}


def nested_workspace(ws):
    """A repo that holds several colcon workspaces (humble_ws/, jazzy_ws/...) -> the one for this distro."""
    import glob as _g
    distro = os.environ.get("ROS_DISTRO", "")
    inner = sorted({os.path.dirname(d) for d in _g.glob(os.path.join(ws, "src", "*", "*", "src"))
                    + _g.glob(os.path.join(ws, "src", "*", "src")) + _g.glob(os.path.join(ws, "*", "src"))
                    if os.path.realpath(os.path.dirname(d)) != os.path.realpath(ws)
                    and not os.path.isfile(os.path.join(os.path.dirname(d), "package.xml"))})  # C++ package src/, not a workspace
    if not inner:
        return ws, None
    mine = [d for d in inner if distro and distro in os.path.basename(d).lower()]
    if len(mine) == 1:
        return mine[0], f"repo holds {len(inner)} workspaces; using {os.path.basename(mine[0])} for {distro}"
    raise ValueError(f"repo holds several workspaces ({', '.join(os.path.basename(d) for d in inner)}) "
                     f"and none matches ROS_DISTRO={distro or '?'} - use Folder and pick one")


# ------------------------------------------------------------------- guessing
def _deps(pkg_path):
    try:
        with open(os.path.join(pkg_path, "package.xml"), errors="ignore") as f:
            return set(re.findall(r"<(?:\w+_)?depend>\s*([^<\s]+)\s*<", f.read()))
    except OSError:
        return set()


def guess(ws_entry):
    deps = set()
    for p in ws_entry.get("packages", []):
        deps |= _deps(p["path"])
    blob = " ".join(sorted(deps))
    sim = next((s for s, keys in SIM_HINTS if any(k in blob for k in keys)), "none")
    arm = any(k in blob for k in ARM_HINTS)
    mobile = any(k in blob for k in MOBILE_HINTS)
    robot_type = "arm" if arm and not mobile else "mobile" if mobile else "unknown"
    launches = [f"{p['name']} {os.path.basename(l)}" for p in ws_entry.get("packages", [])
                for l in p.get("launch", [])]
    return {"simulator": sim, "robot_type": robot_type, "launch_options": launches,
            "suggested_launch": pick_launch(launches)}


def pick_launch(launches):
    """Skip helper launches, then prefer the ones that look like a full bring-up."""
    helpers = ("empty", "rviz", "spawn", "state_publisher", "description", "dqn")
    main = [l for l in launches if not any(h in l for h in helpers)] or launches
    for k in ("world", "sim", "bringup", "demo"):
        for l in main:
            if k in l:
                return l
    return main[0] if main else ""


def rosdep_missing(ws):
    try:
        r = subprocess.run(["rosdep", "check", "--from-paths", os.path.join(ws, "src"), "--ignore-src", "-q"],
                           capture_output=True, text=True, timeout=60)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None                          # rosdep not available: unknown, not "fine"
    return sorted(set(re.findall(r"apt\s+(\S+)", r.stdout + r.stderr)))


# --------------------------------------------------------------------- entry
def import_source(body, scan):
    """body: {"source": "folder", "path": ...} or {"source": "github", "url": ...}.
    scan: hub.scan_workspaces. Returns a review payload for the UI."""
    src = body.get("source")
    origin = None
    if src == "github":
        ws, origin = clone(body.get("url", ""))
    elif src == "folder":
        ws = workspace_root(body.get("path", ""))
    else:
        raise ValueError("source must be 'folder' or 'github'")
    ws = os.path.realpath(ws)
    ws, nested_note = nested_workspace(ws)
    entry = next((w for w in scan([ws]) if w["path"] == ws), None)
    if not entry or not entry["packages"]:
        raise ValueError(f"no ROS packages (package.xml) found in {ws}/src")
    remember_workspace(ws)
    g = guess(entry)
    form = {"name": entry["name"].removesuffix("_ws"), "workspace": ws, "launch": g["suggested_launch"],
            "simulator": g["simulator"], "robot": entry["name"], "project_type": g["robot_type"],
            "description": f"{entry['name']} (imported)"}
    if nested_note:
        g["note"] = nested_note
    names = [p["name"] for p in entry["packages"]]
    dups = sorted({n for n in names if names.count(n) > 1})
    if dups:
        raise ValueError(f"duplicate package names in one workspace ({', '.join(dups[:5])}) - colcon cannot build it; "
                         "the repo probably holds copies for several distros: use Folder and pick the right sub-workspace")
    return {"ok": True, "workspace": entry, "guess": g, "origin": origin,
            "rosdep_missing": rosdep_missing(ws), "form": form}
