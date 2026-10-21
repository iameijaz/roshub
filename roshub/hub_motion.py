"""ROSHub: run the motion probe for a project and remember the result on first success."""
import json
import os
import shlex
import subprocess

import yaml

PROBE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "motion_probe.py")
PROJECTS_DIR = os.path.expanduser("~/.roshub/projects")


def _project_file(name):
    p = os.path.join(PROJECTS_DIR, f"{name}.yaml")
    return p if os.path.isfile(p) else None


def run_check(name, project, opts=None):
    """project: the project's dict (as loaded by ROSHub). opts: optional probe overrides."""
    opts = opts or {}
    meta = project.get("meta", {}) or {}
    env = dict(os.environ, **{k: str(v) for k, v in (project.get("env") or {}).items()})
    saved = meta.get("motion") or {}
    args = ["python3", PROBE]
    topic = opts.get("topic") or saved.get("topic")
    if topic:
        args += ["--topic", topic]
    for k in ("odom", "linear", "angular", "duration"):
        if opts.get(k) not in (None, ""):
            args += [f"--{k}", str(opts[k])]
    src = ""
    ws = meta.get("workspace")
    if ws and os.path.isfile(os.path.join(os.path.expanduser(ws), "install", "setup.bash")):
        src = f"source {shlex.quote(os.path.expanduser(ws))}/install/setup.bash && "
    cmd = f"{src}{' '.join(shlex.quote(a) for a in args)}"
    try:
        r = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, env=env, timeout=30)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "motion probe timed out (30 s)"}
    line = (r.stdout.strip().splitlines() or [""])[-1]
    try:
        res = json.loads(line)
    except json.JSONDecodeError:
        return {"ok": False, "error": "probe failed", "stderr": r.stderr[-800:]}
    if res.get("ok"):
        res["saved"] = _remember(name, res)
    res["first_run"] = not saved.get("verified")
    return res


def _remember(name, res):
    """Store topic + type so later teleop uses the verified settings."""
    path = _project_file(name)
    if not path:
        return False
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    proj = data.get(name)
    if not isinstance(proj, dict):
        return False
    proj.setdefault("meta", {})["motion"] = {"topic": res["topic"], "type": res["type"].split("/")[-1],
                                             "verified": False}
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        yaml.safe_dump(data, f, sort_keys=False, default_flow_style=False, allow_unicode=True)
    os.replace(tmp, path)
    return True


def confirm(name, moved):
    """User answered "did the robot visibly move?" after the first run."""
    path = _project_file(name)
    if not path:
        return {"ok": False, "error": "project file not found"}
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    mot = ((data.get(name) or {}).get("meta") or {}).get("motion")
    if not mot:
        return {"ok": False, "error": "run the move check first"}
    if moved:
        mot["verified"] = True
    else:
        data[name]["meta"].pop("motion")
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        yaml.safe_dump(data, f, sort_keys=False, default_flow_style=False, allow_unicode=True)
    os.replace(tmp, path)
    return {"ok": True, "verified": bool(moved)}
