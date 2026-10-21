#!/usr/bin/env python3
"""ROSHub home: project library state, project metadata, workspaces and new-project creation.

Stdlib + python3-yaml only (ADR-001). Nothing here touches a user's workspace or git repo:
per-user state (bookmarks, archive, recents, "don't show again") lives in ~/.roshub/hub_state.json,
and new projects are written as ~/.roshub/projects/<name>.yaml through profile_schema.save().

Project metadata is an optional `meta:` block in the project YAML:

  meta:
    robot: "Husky"                   # browse by robot
    project_type: "single_robot"     # single_robot | fleet | manipulator | mobile_manipulator | other
    simulator: "isaac_sim"           # isaac_sim | gazebo | none (real hardware) | other
    rmw: "rmw_zenoh_cpp"             # default for new projects
    mode: "robust"                   # robust (gated launch files) | prototype (plain commands)
    workspace: "~/husky_ws"
    dashboard_url: "http://localhost:8095"
    prepare: "Open Isaac Sim and load the warehouse scene, but do NOT press Play yet."
    tags: [nav2, slam]

Everything is optional; missing fields are guessed from the project's commands.
"""
import glob
import json
import os
import re
import threading
import time

import yaml

ROOT = os.path.expanduser("~/.roshub")
STATE_PATH = os.path.join(ROOT, "hub_state.json")
_LOCK = threading.RLock()

PROJECT_TYPES = ["single_robot", "fleet", "manipulator", "mobile_manipulator", "other"]
SIMULATORS = ["isaac_sim", "gazebo", "none", "other"]
RMWS = ["rmw_zenoh_cpp", "rmw_fastrtps_cpp", "rmw_cyclonedds_cpp"]
MODES = ["robust", "prototype"]
WORKSPACE_GLOBS = ["~/*_ws", "~/robotics/*_ws", "~/robotics/*/*_ws", "~/ros2_ws", "~/*/ros2_ws"]


# ---------------------------------------------------------------- per-user state
def _default_state():
    return {"bookmarks": [], "archived": [], "opened": {}, "skip_prepare": [], "started": {}}


def load_state():
    with _LOCK:
        try:
            with open(STATE_PATH) as f:
                st = json.load(f)
            base = _default_state()
            base.update({k: v for k, v in st.items() if k in base})
            return base
        except (OSError, ValueError):
            return _default_state()


def _save_state(st):
    os.makedirs(ROOT, exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f, indent=2)
    os.replace(tmp, STATE_PATH)


def update_state(action, project, value=True):
    """action: bookmark | archive | opened | skip_prepare | started"""
    with _LOCK:
        st = load_state()
        if action in ("bookmark", "archive", "skip_prepare"):
            key = {"bookmark": "bookmarks", "archive": "archived", "skip_prepare": "skip_prepare"}[action]
            items = [p for p in st[key] if p != project]
            if value:
                items.append(project)
            st[key] = items
        elif action in ("opened", "started"):
            st[action][project] = time.time()
        else:
            raise ValueError(f"unknown action {action}")
        _save_state(st)
        return st


# ---------------------------------------------------------------- metadata
def _all_cmds(p):
    cmds = [s.get("cmd", "") for s in p.get("steps") or []]
    cmds += [(s.get("run") or {}).get("cmd", "") for s in p.get("runbook") or []]
    return " ".join(cmds).lower()


def infer_meta(name, p):
    """Fill missing meta fields from what the project runs."""
    meta = dict(p.get("meta") or {})
    text = (_all_cmds(p) + " " + str(p.get("description", "")) + " " + name).lower()
    env_rmw = (p.get("env") or {}).get("RMW_IMPLEMENTATION")
    meta.setdefault("rmw", env_rmw or ("rmw_zenoh_cpp" if "zenoh" in text else "rmw_zenoh_cpp"))
    if "simulator" not in meta:
        meta["simulator"] = ("isaac_sim" if "isaac" in text else "gazebo" if ("gazebo" in text or " gz " in text)
                             else "other")
    if "project_type" not in meta:
        multi = any(w in text for w in ("fleet", "multi", "rmf"))
        arm = any(w in text for w in ("moveit", "arm", "gripper", "panda", "ur5"))
        meta["project_type"] = ("mobile_manipulator" if (arm and ("tiago" in text or "base" in text)) else
                                "manipulator" if arm else "fleet" if multi else "single_robot")
    if "robot" not in meta:
        for key, label in (("husky", "Husky"), ("turtlebot", "TurtleBot3"), ("tiago", "TIAGo"),
                           ("youbot", "youBot")):
            if key in text:
                meta["robot"] = label
                break
        else:
            meta["robot"] = "unspecified"
    meta.setdefault("mode", "robust" if p.get("runbook") else "prototype")
    if "dashboard_url" not in meta:
        m = re.search(r"localhost:(\d{4,5})", json.dumps(p.get("controls") or []))
        if m:
            meta["dashboard_url"] = f"http://localhost:{m.group(1)}"
    meta.setdefault("tags", [])
    return meta


def library(projects):
    """Everything the home screen needs, sorted: recent first, archived last."""
    st = load_state()
    out = []
    for name, p in projects.items():
        if not isinstance(p, dict):
            continue
        meta = infer_meta(name, p)
        out.append({
            "name": name,
            "description": p.get("description", ""),
            "meta": meta,
            "bookmarked": name in st["bookmarks"],
            "archived": name in st["archived"],
            "opened": st["opened"].get(name, 0),
            "started": st["started"].get(name, 0),
            "skip_prepare": name in st["skip_prepare"],
            "has_runbook": bool(p.get("runbook")),
            "stages": len(p.get("runbook") or p.get("steps") or []),
        })
    out.sort(key=lambda d: (d["archived"], -max(d["opened"], d["started"]), d["name"]))
    return {"projects": out, "facets": {
        "robot": sorted({d["meta"]["robot"] for d in out}),
        "project_type": sorted({d["meta"]["project_type"] for d in out}),
        "simulator": sorted({d["meta"]["simulator"] for d in out}),
        "rmw": sorted({d["meta"]["rmw"] for d in out}),
    }, "choices": {"project_type": PROJECT_TYPES, "simulator": SIMULATORS, "rmw": RMWS, "mode": MODES}}


# ---------------------------------------------------------------- workspaces
def scan_workspaces(extra=()):
    """colcon workspaces under the usual places: packages, launch files, build state, src/install drift."""
    seen, out = set(), []
    paths = [p for g in WORKSPACE_GLOBS for p in glob.glob(os.path.expanduser(g))] + list(extra)
    for ws in paths:
        ws = os.path.realpath(os.path.expanduser(ws))
        if ws in seen or not os.path.isdir(os.path.join(ws, "src")):
            continue
        seen.add(ws)
        pkgs = []
        for xml in glob.glob(os.path.join(ws, "src", "**", "package.xml"), recursive=True):
            if "/build/" in xml or "/install/" in xml:
                continue
            d = os.path.dirname(xml)
            try:
                with open(xml, errors="ignore") as f:
                    m = re.search(r"<name>\s*([^<\s]+)\s*</name>", f.read())
            except OSError:
                continue
            if not m:
                continue
            name = m.group(1)
            launch = sorted(os.path.relpath(x, d) for x in glob.glob(os.path.join(d, "**", "*.launch.*"), recursive=True))
            inst = os.path.join(ws, "install", name)
            newest_src = _newest(d)
            built_t = _newest(inst) if os.path.isdir(inst) else 0
            pkgs.append({"name": name, "path": d, "launch": launch[:40], "built": built_t > 0,
                         "stale": bool(built_t and newest_src > built_t + 2)})
        mtime = _newest(os.path.join(ws, "src"), depth=2)
        out.append({"name": os.path.basename(ws), "path": ws, "built": os.path.isdir(os.path.join(ws, "install")),
                    "packages": sorted(pkgs, key=lambda p: p["name"]), "modified": mtime})
    out.sort(key=lambda w: -w["modified"])
    return out


def _newest(path, depth=6):
    """Newest mtime of the files under `path` (skips build/install/log and dot dirs)."""
    best, base = 0.0, path.rstrip("/").count("/")
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("build", "install", "log", "__pycache__")]
        if root.count("/") - base >= depth:
            dirs[:] = []
        for fn in files:
            try:
                best = max(best, os.path.getmtime(os.path.join(root, fn)))
            except OSError:
                pass
    return best


# ---------------------------------------------------------------- new project
def new_project_yaml(form):
    """Build the YAML text for a new project from the 'New project' form."""
    name = re.sub(r"[^a-z0-9_]", "_", form.get("name", "").strip().lower()).strip("_")
    rmw = form.get("rmw") or "rmw_zenoh_cpp"
    mode = form.get("mode") or "robust"
    ws = form.get("workspace") or "~/ros2_ws"
    launch = (form.get("launch") or "").strip()            # "pkg file.launch.py args"
    meta = {k: form[k] for k in ("robot", "project_type", "simulator", "dashboard_url", "prepare")
            if form.get(k)}
    meta.update({"rmw": rmw, "mode": mode, "workspace": ws,
                 "tags": [t.strip() for t in (form.get("tags") or "").split(",") if t.strip()]})
    src = f"source {ws}/install/setup.bash"
    stages = []
    if rmw == "rmw_zenoh_cpp":
        stages.append({"id": "router", "title": "Zenoh router", "reuse": True,
                       "run": {"cmd": "ros2 run rmw_zenoh_cpp rmw_zenohd", "bg": True},
                       "ready": [{"port": 7447}], "timeout": 20})
    if meta.get("simulator") == "isaac_sim":
        stages.append({"id": "sim", "title": "Isaac Sim playing",
                       "manual": "Press Play in Isaac Sim now. This step passes by itself once /clock is ticking.",
                       "ready": [{"hz": "/clock", "min_hz": 1}], "timeout": 900})
    if launch:
        run = {"cmd": f"{src} && ros2 launch {launch}", "bg": True}
        st = {"id": "launch", "title": f"ros2 launch {launch.split()[0]}", "run": run, "timeout": 180}
        if mode == "robust":
            st["ready"] = [{"hz": "/clock", "min_hz": 1}] if meta.get("simulator") != "none" else [{"nodes": "/", "min": 1}]
        stages.append(st)
    if not stages:
        stages.append({"id": "start", "title": "Start", "manual": "Edit this project's runbook to add its steps."})
    data = {name: {"description": form.get("description") or f"{meta.get('robot', 'Robot')} project",
                   "meta": meta, "env": {"RMW_IMPLEMENTATION": rmw}, "runbook": stages}}
    return name, yaml.safe_dump(data, sort_keys=False, default_flow_style=False, allow_unicode=True)


def steps_to_runbook(steps):
    """Prototype projects only list `steps:` (plain commands). Run them through the same engine:
    each step starts in the background; `wait_for_topic` becomes a gate before it."""
    stages = []
    for i, s in enumerate(steps or []):
        name = s.get("name") or f"step{i + 1}"
        if s.get("wait_for_topic"):
            stages.append({"id": f"wait_{name}", "title": f"Wait for {s['wait_for_topic']}",
                           "ready": [{"echo": s["wait_for_topic"]}], "timeout": 300})
        if s.get("cmd"):
            stages.append({"id": name, "title": name.replace("_", " "), "run": {"cmd": s["cmd"], "bg": True},
                           "timeout": 10})
    return stages
