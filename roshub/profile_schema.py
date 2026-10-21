#!/usr/bin/env python3
"""Validation + file handling for ROSHub project profiles (the YAML in ~/.roshub/projects/).

validate(text) -> (data, issues) where issues are dicts {level: ERROR|WARN, where, msg}.
Errors block saving; warnings don't.
"""
import glob
import os
import re
import shutil
import subprocess
import time

import yaml

import runbook

PROJECTS_DIR = os.path.expanduser("~/.roshub/projects")
ROSHUB_YAML = os.path.expanduser("~/.roshub.yaml")

TOP_KEYS = {"description", "tile_windows", "env", "steps", "controls", "contracts", "dashboard", "runbook",
            "workspace", "package", "source_urdf", "profile_file", "meta", "custom_tests"}
STEP_KEYS = {"name", "cmd", "wait_for_topic", "delay"}
CONTROL_KEYS = {"id", "label", "style", "description", "cmd", "bg"}
STAGE_KEYS = {"id", "title", "run", "ready", "timeout", "reuse", "manual", "optional", "env", "hint", "fixes"}
CHECK_KEYS = set(runbook.KINDS) | {"min_hz", "field", "timeout", "count", "min", "latched", "label", "name"}
NAME_RE = re.compile(r"^[a-z0-9_]+$")

TEMPLATE = """{name}:
  description: "What this project runs"
  tile_windows: false
  steps:                       # prototype mode: plain commands (roshub.py launcher)
    - name: "sim"
      cmd: "ros2 launch my_pkg sim.launch.py"
  runbook:                     # used by the Bring-up tab: gated, step by step
    - id: sim
      title: "Simulation"
      run: {{cmd: "ros2 launch my_pkg sim.launch.py", bg: true}}
      ready:
        - {{hz: /clock, min_hz: 5}}
      timeout: 60
  contracts:
    - {{topic: /clock, expected_type: rosgraph_msgs/msg/Clock, role: "Sim time"}}
"""


def _issue(issues, level, where, msg):
    issues.append({"level": level, "where": where, "msg": msg})


def validate(text):
    issues = []
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        where = f"line {mark.line + 1}, column {mark.column + 1}" if mark else "file"
        _issue(issues, "ERROR", where, f"YAML syntax: {getattr(e, 'problem', None) or e}")
        return None, issues
    if not isinstance(data, dict) or not data:
        _issue(issues, "ERROR", "file", "must be a mapping: `<project_name>: {...}`")
        return None, issues
    for name, p in data.items():
        w = str(name)
        if not NAME_RE.match(w):
            _issue(issues, "WARN", w, "project names work best as lowercase_with_underscores")
        if not isinstance(p, dict):
            _issue(issues, "ERROR", w, "project body must be a mapping")
            continue
        for k in p:
            if k not in TOP_KEYS:
                _issue(issues, "WARN", f"{w}.{k}", "unknown key (ignored by ROSHub)")
        if not p.get("steps") and not p.get("runbook"):
            _issue(issues, "ERROR", w, "needs `steps` (Launch button) or `runbook` (Bring-up tab), or both")
        for i, s in enumerate(p.get("steps") or []):
            where = f"{w}.steps[{i}]"
            if not isinstance(s, dict) or not s.get("cmd"):
                _issue(issues, "ERROR", where, "each step needs a `cmd`")
                continue
            if not s.get("name"):
                _issue(issues, "WARN", where, "step without a `name` (its log file will be unnamed)")
            for k in s:
                if k not in STEP_KEYS:
                    _issue(issues, "WARN", f"{where}.{k}", "unknown step key")
        ids = [c.get("id") for c in (p.get("controls") or []) if isinstance(c, dict)]
        for i, c in enumerate(p.get("controls") or []):
            where = f"{w}.controls[{i}]"
            if not isinstance(c, dict) or not c.get("id") or not c.get("cmd"):
                _issue(issues, "ERROR", where, "each control needs `id` and `cmd`")
                continue
            if ids.count(c["id"]) > 1:
                _issue(issues, "ERROR", where, f"duplicate control id '{c['id']}'")
            if c.get("style") not in (None, "primary", "sec", "danger"):
                _issue(issues, "WARN", f"{where}.style", "style is primary | sec | danger")
        _validate_runbook(w, p.get("runbook"), issues)
        _validate_tests(w, p.get("custom_tests"), issues)
        for i, c in enumerate(p.get("contracts") or []):
            if not isinstance(c, dict) or not c.get("topic"):
                _issue(issues, "ERROR", f"{w}.contracts[{i}]", "each contract needs a `topic`")
        dash = p.get("dashboard")
        if dash is not None and not isinstance(dash, dict):
            _issue(issues, "ERROR", f"{w}.dashboard", "must be a mapping")
    return data, issues


def _validate_runbook(w, rb, issues):
    if rb is None:
        return
    if not isinstance(rb, list):
        _issue(issues, "ERROR", f"{w}.runbook", "must be a list of stages")
        return
    seen = set()
    for i, s in enumerate(rb):
        where = f"{w}.runbook[{i}]"
        if not isinstance(s, dict):
            _issue(issues, "ERROR", where, "stage must be a mapping")
            continue
        sid = s.get("id")
        if not sid:
            _issue(issues, "ERROR", where, "stage needs an `id`")
        elif sid in seen:
            _issue(issues, "ERROR", where, f"duplicate stage id '{sid}'")
        seen.add(sid)
        for k in s:
            if k not in STAGE_KEYS:
                _issue(issues, "WARN", f"{where}.{k}", "unknown stage key")
        run = s.get("run")
        if run is not None and (not isinstance(run, dict) or not run.get("cmd")):
            _issue(issues, "ERROR", f"{where}.run", "run needs `cmd` (and optional `bg: true`)")
        if not run and not s.get("manual") and not s.get("ready"):
            _issue(issues, "ERROR", where, "stage does nothing: give it `run`, `manual` or `ready`")
        if s.get("reuse") and not s.get("ready"):
            _issue(issues, "WARN", where, "`reuse` needs `ready` checks to decide whether it is already up")
        if run and run.get("bg") and not s.get("ready"):
            _issue(issues, "WARN", where, "background process without `ready` checks passes as soon as it starts")
        try:
            float(s.get("timeout", 60))
        except (TypeError, ValueError):
            _issue(issues, "ERROR", f"{where}.timeout", "timeout must be a number of seconds")
        for j, c in enumerate(s.get("ready") or []):
            cw = f"{where}.ready[{j}]"
            if not isinstance(c, dict) or runbook.check_kind(c) is None:
                _issue(issues, "ERROR", cw, f"check needs one of: {', '.join(runbook.KINDS)}")
                continue
            for k in c:
                if k not in CHECK_KEYS:
                    _issue(issues, "WARN", f"{cw}.{k}", "unknown check key")
            if "tf" in c and (not isinstance(c["tf"], list) or len(c["tf"]) != 2):
                _issue(issues, "ERROR", cw, "tf check is [parent, child]")


# ---------------------------------------------------------------- files
def find_source(project):
    """(path, kind) of the file that defines `project`: a projects/*.yaml file, or ~/.roshub.yaml."""
    for path in sorted(glob.glob(os.path.join(PROJECTS_DIR, "*.yaml"))):
        try:
            with open(path) as f:
                d = yaml.safe_load(f) or {}
            if project in d:
                return path, "project_file"
        except Exception:
            continue
    return ROSHUB_YAML, "legacy"


def load_text(project):
    """Text to edit for one project. Legacy projects (only in ~/.roshub.yaml) are offered as a
    new projects/<name>.yaml so editing never rewrites the shared legacy file."""
    path, kind = find_source(project)
    if kind == "project_file":
        with open(path) as f:
            return {"path": path, "kind": kind, "text": f.read()}
    try:
        with open(ROSHUB_YAML) as f:
            data = (yaml.safe_load(f) or {}).get("projects", {})
    except Exception:
        data = {}
    if project in data:
        text = yaml.safe_dump({project: data[project]}, sort_keys=False, default_flow_style=False)
        return {"path": os.path.join(PROJECTS_DIR, f"{project}.yaml"), "kind": "migrate", "text": text,
                "note": "This project lives only in ~/.roshub.yaml. Saving writes it to its own file in "
                        "~/.roshub/projects/, which then takes precedence."}
    return {"path": os.path.join(PROJECTS_DIR, f"{project}.yaml"), "kind": "new", "text": TEMPLATE.format(name=project)}


def save(path, text):
    """Validate, back up the old file, write, and commit if ~/.roshub is a git repo."""
    path = os.path.abspath(os.path.expanduser(path))
    if not path.startswith(os.path.abspath(PROJECTS_DIR) + os.sep) or not path.endswith(".yaml"):
        return {"ok": False, "issues": [{"level": "ERROR", "where": "path",
                                         "msg": "profiles can only be saved as ~/.roshub/projects/*.yaml"}]}
    data, issues = validate(text)
    if any(i["level"] == "ERROR" for i in issues):
        return {"ok": False, "issues": issues}
    os.makedirs(PROJECTS_DIR, exist_ok=True)
    backup = None
    if os.path.isfile(path):
        bdir = os.path.join(PROJECTS_DIR, ".bak")
        os.makedirs(bdir, exist_ok=True)
        backup = os.path.join(bdir, f"{os.path.basename(path)[:-5]}.{time.strftime('%Y%m%d_%H%M%S')}.yaml")
        shutil.copy2(path, backup)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(text if text.endswith("\n") else text + "\n")
    os.replace(tmp, path)
    committed = _git_commit(path, f"roshub: edit {os.path.basename(path)} ({', '.join(data)})")
    return {"ok": True, "issues": issues, "path": path, "backup": backup, "committed": committed,
            "projects": list(data)}


def _git_commit(path, msg):
    root = os.path.expanduser("~/.roshub")
    if not os.path.isdir(os.path.join(root, ".git")):
        return False
    try:
        subprocess.run(["git", "-C", root, "add", os.path.relpath(path, root)], check=True, capture_output=True, timeout=10)
        r = subprocess.run(["git", "-C", root, "commit", "-q", "-m", msg], capture_output=True, timeout=10)
        return r.returncode == 0
    except Exception:
        return False


TEST_KEYS = {"id", "title", "description", "stages", "artifacts"}


def _validate_tests(w, tests, issues):
    """custom_tests: named experiments; `stages` use the runbook stage format."""
    if tests is None:
        return
    if not isinstance(tests, list):
        _issue(issues, "ERROR", f"{w}.custom_tests", "must be a list of tests")
        return
    seen = set()
    for i, t in enumerate(tests):
        where = f"{w}.custom_tests[{i}]"
        if not isinstance(t, dict) or not t.get("id"):
            _issue(issues, "ERROR", where, "each test needs an `id`")
            continue
        if t["id"] in seen:
            _issue(issues, "ERROR", where, f"duplicate test id '{t['id']}'")
        seen.add(t["id"])
        for k in t:
            if k not in TEST_KEYS:
                _issue(issues, "WARN", f"{where}.{k}", "unknown test key")
        if not t.get("stages"):
            _issue(issues, "ERROR", where, "test needs `stages` (runbook stage format)")
        _validate_runbook(where, t.get("stages"), issues)
        for j, a in enumerate(t.get("artifacts") or []):
            if not isinstance(a, dict) or not a.get("path"):
                _issue(issues, "ERROR", f"{where}.artifacts[{j}]", "artifact needs a `path`")
