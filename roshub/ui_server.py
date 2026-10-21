#!/usr/bin/env python3
import os
import re
import json
import glob
import time
import signal
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
from core import sync_and_load_projects, get_active_project, set_active_project, LOG_DIR
import diagnostics
import loggroup
import project_detect
import dashboard
import diag_bundle
import runbook
import profile_schema
import hub
import hub_import
import hub_wscache
import hub_manage
import hub_build
hub.new_project_yaml = hub_build.wrap_new(hub.new_project_yaml)
import hub_motion
import sysreport
# roshub-upgrade: v2
# roshub-upgrade: v1

HOME_PATH = os.path.expanduser("~/.roshub/home.html")
REPORT_DIR = os.path.expanduser("~/.roshub/diag")
from diagnostics import ros_env as _ros_env
ROSHUB_PY = os.path.expanduser("~/roshub.py")
ACTIVE_PROCS = {}
FOCUS = {"manual": None}          # None = Auto: follow the project that is actually running
_DETECT = {"t": 0.0, "res": {}}
_PROJECTS = {"data": {}}           # last loaded projects (teleop must not re-read YAML 10x/s)
TELEOP = dashboard.TeleopBridge()
TESTS = {}                            # (project, test id) -> runbook.Runbook
RUNBOOKS = {}                         # project -> runbook.Runbook (one live run per project)


def _rb_evaluator():
    return runbook.make_evaluator(lambda c: dashboard.run_check(c), diagnostics._run)


def get_runbook(project, projects, fresh=False):
    """The project's runbook; rebuilt from YAML unless a run is in progress."""
    p = projects.get(project) or {}
    spec = p.get("runbook") or hub.steps_to_runbook(p.get("steps"))
    rb = RUNBOOKS.get(project)
    running = rb is not None and rb.thread is not None and rb.thread.is_alive()
    if rb is None or (fresh and not running):
        env = dict(_ros_env())
        env.update({k: str(v) for k, v in ((projects.get(project) or {}).get("env") or {}).items()})
        old = rb
        rb = runbook.Runbook(project, spec, env, LOG_DIR, _rb_evaluator())
        if old is not None:          # keep the state of stages that are still running/passed
            rb.state = old.state
            prev = {s.id: s for s in old.stages}
            for s in rb.stages:
                if s.id in prev:
                    p = prev[s.id]
                    s.state, s.message, s.started, s.finished = p.state, p.message, p.started, p.finished
                    s.proc, s.log_path, s.checks = p.proc, p.log_path, p.checks if len(p.checks) == len(s.checks) else s.checks
        RUNBOOKS[project] = rb
    return rb


def runbook_alive_projects():
    alive = {p for p, rb in RUNBOOKS.items() if rb.owns_running_process()}
    alive |= {p for (p, _), rb in TESTS.items() if rb.owns_running_process()}
    return alive


def _test_spec(projects, project, tid):
    for t in (projects.get(project) or {}).get("custom_tests") or []:
        if isinstance(t, dict) and str(t.get("id")) == tid:
            return t
    return None


def get_test(project, tid, projects, fresh=False):
    spec = _test_spec(projects, project, tid)
    if spec is None:
        return None
    rb = TESTS.get((project, tid))
    running = rb is not None and rb.thread is not None and rb.thread.is_alive()
    if rb is None or (fresh and not running):
        env = dict(_ros_env())
        env.update({k: str(v) for k, v in ((projects.get(project) or {}).get("env") or {}).items()})
        old = rb
        rb = runbook.Runbook(f"{project}_test_{tid}", spec.get("stages") or [], env, LOG_DIR, _rb_evaluator())
        if old is not None:
            rb.state = old.state
            prev = {s.id: s for s in old.stages}
            for s in rb.stages:
                if s.id in prev:
                    q = prev[s.id]
                    s.state, s.message, s.started, s.finished = q.state, q.message, q.started, q.finished
                    s.proc, s.log_path = q.proc, q.log_path
                    s.checks = q.checks if len(q.checks) == len(s.checks) else s.checks
        TESTS[(project, tid)] = rb
    return rb


ART_KIND = {".png": "image", ".jpg": "image", ".jpeg": "image", ".svg": "image", ".md": "markdown", ".csv": "csv"}
ART_CTYPE = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".svg": "image/svg+xml"}


def _artifacts(spec):
    out = []
    for i, a in enumerate((spec or {}).get("artifacts") or []):
        p = os.path.expandvars(os.path.expanduser(str(a.get("path", ""))))
        ex = os.path.isfile(p)
        out.append({"idx": i, "label": a.get("label") or os.path.basename(p), "path": p, "exists": ex,
                    "kind": ART_KIND.get(os.path.splitext(p)[1].lower(), "text"),
                    "mtime": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(os.path.getmtime(p))) if ex else None,
                    "size_kb": round(os.path.getsize(p) / 1024, 1) if ex else None})
    return out



ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _run_fix(project, stage_id, index):
    """Run one `fixes:` command declared in the project YAML (never an arbitrary command from the browser)."""
    projects = _PROJECTS["data"] or load_projects()
    stage = next((s for s in (projects.get(project) or {}).get("runbook") or [] if s.get("id") == stage_id), None)
    fixes = (stage or {}).get("fixes") or []
    if not 0 <= index < len(fixes) or not fixes[index].get("cmd"):
        return {"ok": False, "output": "no such fix"}
    env = dict(_ros_env())
    env.update({k: str(v) for k, v in ((projects.get(project) or {}).get("env") or {}).items()})
    try:
        r = subprocess.run(["bash", "-c", fixes[index]["cmd"]], env=env, capture_output=True, text=True,
                           timeout=float(fixes[index].get("timeout", 90)))
        return {"ok": r.returncode == 0, "output": ANSI.sub("", (r.stdout + r.stderr))[-4000:]}
    except subprocess.TimeoutExpired:
        return {"ok": False, "output": "timed out"}


def _run_doctor(deep=False):
    for p in (os.path.expanduser("~/.roshub/tools/bridge_doctor.py"), os.path.expanduser("~/bridge_doctor.py")):
        if os.path.isfile(p):
            args = [] if deep else ["--quick"]
            try:
                r = subprocess.run(["python3", p] + args, env=dict(_ros_env(), NO_COLOR="1"),
                                   capture_output=True, text=True, timeout=120)
                return {"ok": r.returncode == 0, "tool": p, "output": ANSI.sub("", r.stdout + r.stderr)[-20000:]}
            except subprocess.TimeoutExpired:
                return {"ok": False, "tool": p, "output": "doctor timed out after 120 s"}
    return {"ok": False, "tool": "", "output": "bridge_doctor.py not found in ~/.roshub/tools/"}


def _run_doctor_for(deep=False, project=""):
    """Isaac projects (or no project) -> bridge_doctor; everything else -> project_doctor."""
    proj = (_PROJECTS["data"] or load_projects()).get(project) if project else None
    sim = ((proj or {}).get("meta") or {}).get("simulator", "")
    pd = os.path.expanduser("~/.roshub/tools/project_doctor.py")
    if not proj or sim == "isaac_sim" or not os.path.isfile(pd):
        return _run_doctor(deep)
    args = ["python3", pd, "--project", project] + ([] if deep else ["--quick"])
    try:
        r = subprocess.run(args, env=dict(_ros_env(), NO_COLOR="1"), capture_output=True, text=True, timeout=60)
        return {"ok": r.returncode == 0, "tool": pd, "output": ANSI.sub("", r.stdout + r.stderr)[-20000:]}
    except subprocess.TimeoutExpired:
        return {"ok": False, "tool": pd, "output": "project doctor timed out after 60 s"}


class ReusableServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

def load_projects():
    _PROJECTS["data"] = sync_and_load_projects()
    return _PROJECTS["data"]

def detect_running(projects, max_age=2.0):
    """Process-based detection, cached for 2 s (status polls every 4 s)."""
    if time.time() - _DETECT["t"] > max_age:
        alive = {k for k, p in ACTIVE_PROCS.items() if p is not None and p.poll() is None}
        alive |= runbook_alive_projects()
        _DETECT["res"] = project_detect.detect(projects, launcher_alive=alive)
        _DETECT["t"] = time.time()
    return _DETECT["res"]

def resolve_current_active(projects):
    if FOCUS["manual"] in projects:
        return FOCUS["manual"]
    best = detect_running(projects).get("_best", "")
    if best:
        if get_active_project().get("active_project") != best:
            set_active_project(best)      # keeps diagnostics.get_contracts() on the running project
        return best
    saved = get_active_project().get("active_project", "")
    if saved in projects:
        return saved
    return next(iter(projects.keys()), "")

def normalize_log_line(line):
    return loggroup.fingerprint(line)

def parse_log_counts(file_path):
    counts = {"error": 0, "warn": 0, "info": 0}
    if not os.path.isfile(file_path):
        return counts
    try:
        with open(file_path, "r", errors="ignore") as f:
            for line in f:
                lv = loggroup.parse_level(line)
                if lv in ("ERROR", "FATAL"):
                    counts["error"] += 1
                elif lv == "WARN":
                    counts["warn"] += 1
                elif lv == "INFO":
                    counts["info"] += 1
    except Exception:
        pass
    return counts

def aggregate_project_errors(active_proj, only_active=True):
    """Errors/warnings across the project's step logs, grouped by fingerprint
    (timestamps, PIDs and numbers ignored) with first/last-seen times."""
    os.makedirs(LOG_DIR, exist_ok=True)
    now = time.time()
    paths = []
    for p in glob.glob(os.path.join(LOG_DIR, "*.log")):
        if now - os.path.getmtime(p) > 43200:
            continue
        if only_active and active_proj and not os.path.basename(p).startswith(active_proj + "_"):
            continue
        paths.append(p)

    def label(p):
        b = os.path.basename(p)[:-4]
        return b[len(active_proj) + 1:] if active_proj and b.startswith(active_proj + "_") else b

    items = []
    for g in loggroup.sorted_entries(loggroup.group_files(paths, "WARN", label)):
        items.append({
            "program": g["source"].split(" -> ")[0],
            "source": g["source"],
            "level": "ERROR" if g["level"] == "FATAL" else g["level"],
            "message": g["sample"],
            "norm": g["fingerprint"],
            "count": g["count"],
            "first": g["first_str"],
            "last": g["last_str"],
            "file": ", ".join(g["files"]),
        })
    return items

class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def _json(self, payload):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode())

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def do_POST(self):
        parsed = urlparse(self.path)
        body = self._body()
        if parsed.path == "/api/profile/validate":
            _, issues = profile_schema.validate(body.get("text", ""))
            self._json({"issues": issues})
        elif parsed.path == "/api/profile/save":
            res = profile_schema.save(body.get("path", ""), body.get("text", ""))
            if res.get("ok"):
                load_projects()
                _DETECT["t"] = 0.0
            self._json(res)
        elif parsed.path == "/api/hub/motion":
            name = body.get("project", "")
            proj = (_PROJECTS["data"] or load_projects()).get(name)
            if not proj:
                self._json({"ok": False, "error": f"unknown project {name}"})
            else:
                if "confirm" in body:
                    res = hub_motion.confirm(name, bool(body["confirm"]))
                    if res.get("ok"):
                        load_projects()
                    self._json(res)
                    return
                res = hub_motion.run_check(name, proj, body.get("opts"))
                if res.get("saved"):
                    load_projects()
                self._json(res)
        elif parsed.path == "/api/hub/delete":
            res = hub_manage.delete(body.get("project", ""), _PROJECTS["data"] or load_projects(),
                                    bool(body.get("remove_clone")))
            if res.get("ok"):
                load_projects()
            self._json(res)
        elif parsed.path == "/api/hub/import":
            try:
                self._json(hub_import.import_source(body, hub.scan_workspaces))
            except (ValueError, RuntimeError) as e:
                self._json({"ok": False, "error": str(e)})
        elif parsed.path == "/api/hub/new":
            name, text = hub.new_project_yaml(body)
            if not profile_schema.NAME_RE.match(name or "-"):
                self._json({"ok": False, "error": "name: use letters, digits and _"})
                return
            if name in (_PROJECTS["data"] or load_projects()):
                self._json({"ok": False, "error": f"project '{name}' already exists"})
                return
            res = profile_schema.save(os.path.join(profile_schema.PROJECTS_DIR, f"{name}.yaml"), text)
            if res.get("ok"):
                load_projects()
                hub.update_state("opened", name)
            self._json(dict(res, name=name, text=text))
        elif parsed.path == "/api/hub/fix":
            self._json(_run_fix(body.get("project", ""), body.get("stage", ""), int(body.get("index", 0))))
        else:
            self.send_response(404)
            self.end_headers()

    def _file(self, path, ctype):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        with open(path, "rb") as f:
            self.wfile.write(f.read())

    def do_GET(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)

        if parsed.path in ("/", "/home"):
            self._file(HOME_PATH, "text/html; charset=utf-8")

        elif parsed.path == "/api/hub/library":
            self._json(hub.library(load_projects()))

        elif parsed.path == "/api/hub/state":
            try:
                hub.update_state(qs.get("action", [""])[0], qs.get("project", [""])[0],
                                 qs.get("value", ["1"])[0] == "1")
                self._json({"ok": True})
            except ValueError as e:
                self._json({"ok": False, "error": str(e)})

        elif parsed.path == "/api/hub/workspaces":
            self._json(hub_wscache.get(hub.scan_workspaces, hub_import.extra_workspaces(), refresh=qs.get("refresh", ["0"])[0] == "1", auto=None if "auto" not in qs else qs["auto"][0] == "1"))

        elif parsed.path == "/api/hub/doctor":
            self._json(_run_doctor_for(qs.get("deep", ["0"])[0] == "1", qs.get("project", [""])[0]))

        elif parsed.path == "/api/hub/report":
            projects = _PROJECTS["data"] or load_projects()
            proj = qs.get("project", [""])[0]
            rb = RUNBOOKS.get(proj)
            path = sysreport.write(proj, projects, rb.as_dict() if rb else None)
            with open(path) as f:
                self._json({"ok": True, "name": os.path.basename(path), "path": path, "text": f.read()})

        elif parsed.path == "/api/hub/report_file":
            name = os.path.basename(qs.get("name", [""])[0])
            p = os.path.join(REPORT_DIR, name)
            if name.endswith(".md") and os.path.isfile(p):
                self.send_response(200)
                self.send_header("Content-Type", "text/markdown; charset=utf-8")
                self.send_header("Content-Disposition", f'attachment; filename="{name}"')
                self.end_headers()
                with open(p, "rb") as f:
                    self.wfile.write(f.read())
            else:
                self.send_response(404)
                self.end_headers()

        elif parsed.path == "/api/status":
            projects = load_projects()
            det = detect_running(projects)
            active_proj = resolve_current_active(projects)
            out = {}
            for k, v in projects.items():
                proc = ACTIVE_PROCS.get(k)
                launched = proc is not None and proc.poll() is None
                d = det.get(k, {})
                state = d.get("state", "stopped")
                if launched and state == "stopped":
                    state = "starting"
                out[k] = {
                    "description": v.get("description", ""),
                    "steps": [s.get("name", "step") for s in v.get("steps", [])],
                    "step_states": d.get("steps", []),
                    "controls": v.get("controls", []),
                    "running": state != "stopped",
                    "state": state,
                    "matched": d.get("matched", 0),
                    "total": d.get("total", 0),
                    "has_dashboard": bool(v.get("dashboard")),
                    "has_runbook": bool(v.get("runbook")),
                    "runbook_state": RUNBOOKS[k].state if k in RUNBOOKS else "idle",
                    "focused": (k == active_proj)
                }
            self._json({
                "active_project": active_proj,
                "auto_focus": FOCUS["manual"] is None,
                "detected": det.get("_best", ""),
                "projects": out
            })

        elif parsed.path == "/api/focus":
            proj = qs.get("project", [""])[0]
            if proj in ("", "__auto__"):
                FOCUS["manual"] = None
                _DETECT["t"] = 0.0
                proj = resolve_current_active(load_projects())
            else:
                FOCUS["manual"] = proj
                set_active_project(proj)
            self._json({"ok": True, "active_project": proj, "auto_focus": FOCUS["manual"] is None})

        elif parsed.path == "/api/reload":
            projects = sync_and_load_projects()
            self._json({"ok": True, "count": len(projects)})

        elif parsed.path == "/api/launch":
            proj = qs.get("project", [""])[0]
            sync_and_load_projects()
            FOCUS["manual"] = None
            _DETECT["t"] = 0.0
            if proj:
                if proj not in ACTIVE_PROCS or ACTIVE_PROCS[proj].poll() is not None:
                    cmd = f"python3 {ROSHUB_PY} {proj}"
                    proc = subprocess.Popen(cmd, shell=True, executable="/bin/bash", env=_ros_env(), preexec_fn=os.setsid)
                    ACTIVE_PROCS[proj] = proc
                    set_active_project(proj, proc.pid)
                else:
                    set_active_project(proj, ACTIVE_PROCS[proj].pid)
            self._json({"ok": True, "active_project": proj})

        elif parsed.path == "/api/stop":
            proj = qs.get("project", [""])[0]
            proc = ACTIVE_PROCS.get(proj)
            if proc and proc.poll() is None:
                os.killpg(os.getpgid(proc.pid), signal.SIGINT)
            self._json({"ok": True})

        elif parsed.path == "/api/project_control":
            proj = qs.get("project", [""])[0]
            ctrl_id = qs.get("id", [""])[0]
            projects = sync_and_load_projects()
            controls = projects.get(proj, {}).get("controls", [])
            target = next((c for c in controls if c.get("id") == ctrl_id), None)
            if not target:
                self._json({"ok": False, "output": f"Control '{ctrl_id}' not found in {proj}"})
                return
            cmd = target.get("cmd", "")
            if target.get("bg", False):
                subprocess.Popen(cmd, shell=True, executable="/bin/bash", env=_ros_env(), preexec_fn=os.setsid)
                self._json({"ok": True, "output": f"Started background process: {target.get('label')}"})
            else:
                try:
                    res = subprocess.run(cmd, shell=True, executable="/bin/bash", env=_ros_env(), capture_output=True, text=True, timeout=12)
                    out = (res.stdout + "\n" + res.stderr).strip() or "Command completed (exit 0)."
                    self._json({"ok": res.returncode == 0, "output": out})
                except Exception as e:
                    self._json({"ok": False, "output": str(e)})

        elif parsed.path == "/api/killros":
            cmd = 'pkill -9 -f "gz sim" || true; pkill -9 -f "ros2" || true; pkill -9 -f "parameter_bridge" || true; pkill -9 -f "robot_state_publisher" || true; pkill -9 -f "rviz2" || true; pkill -9 -f "component_container" || true'
            TELEOP.shutdown()
            for rb in list(RUNBOOKS.values()) + list(TESTS.values()):
                rb.stop_event.set()
            subprocess.run(cmd, shell=True, executable="/bin/bash", env=_ros_env())
            ACTIVE_PROCS.clear()
            _DETECT["t"] = 0.0
            self._json({"ok": True})

        elif parsed.path == "/api/kill_router":
            subprocess.run('pkill -f rmw_zenohd || true', shell=True, executable="/bin/bash", env=_ros_env())
            self._json({"ok": True})

        elif parsed.path == "/api/clear_costmaps":
            cmd = 'source /opt/ros/jazzy/setup.bash && export RMW_IMPLEMENTATION=rmw_zenoh_cpp && ros2 service call /local_costmap/clear_entirely_local_costmap nav2_msgs/srv/ClearEntireCostmap "{request: {}}" && ros2 service call /global_costmap/clear_entirely_global_costmap nav2_msgs/srv/ClearEntireCostmap "{request: {}}"'
            subprocess.Popen(cmd, shell=True, executable="/bin/bash", env=_ros_env())
            self._json({"ok": True})

        elif parsed.path == "/api/exit":
            self._json({"ok": True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()

        elif parsed.path == "/api/logs":
            os.makedirs(LOG_DIR, exist_ok=True)
            only_active = qs.get("only_active", ["1"])[0] == "1"
            projects = sync_and_load_projects()
            active_proj = resolve_current_active(projects)
            files = []
            now = time.time()
            for p in sorted(glob.glob(os.path.join(LOG_DIR, "*.log")), key=os.path.getmtime, reverse=True):
                mtime_val = os.path.getmtime(p)
                if now - mtime_val > 43200:
                    continue
                bname = os.path.basename(p)
                if only_active and active_proj and not bname.startswith(active_proj + "_"):
                    continue
                mtime_str = time.strftime("%H:%M:%S", time.localtime(mtime_val))
                size_kb = round(os.path.getsize(p) / 1024, 1)
                counts = parse_log_counts(p)
                files.append({
                    "name": bname,
                    "mtime": mtime_str,
                    "size_kb": size_kb,
                    "counts": counts
                })
            self._json({"active_project": active_proj, "files": files})

        elif parsed.path == "/api/logs_combined_errors":
            only_active = qs.get("only_active", ["1"])[0] == "1"
            projects = sync_and_load_projects()
            active_proj = resolve_current_active(projects)
            items = aggregate_project_errors(active_proj, only_active)
            self._json({"active_project": active_proj, "items": items})

        elif parsed.path == "/api/log_content":
            fname = os.path.basename(qs.get("file", [""])[0])
            lines_limit = int(qs.get("lines", ["240"])[0])
            fpath = os.path.join(LOG_DIR, fname)
            content = ""
            if os.path.isfile(fpath):
                with open(fpath, "r", errors="replace") as f:
                    content = "".join(f.readlines()[-lines_limit:])
            self._json({"content": content, "file": fname})

        elif parsed.path == "/api/diag/generate":
            projects = load_projects()
            proj = qs.get("project", [""])[0] or resolve_current_active(projects)
            try:
                r = diag_bundle.generate(proj)
                self._json({"ok": True, "name": os.path.basename(r["path"]), "verdict": r["verdict"],
                            "findings": r["findings"], "markdown": r["markdown"]})
            except Exception as e:
                import traceback
                self._json({"ok": False, "error": f"{e}\n{traceback.format_exc()}"})

        elif parsed.path == "/api/diag/list":
            os.makedirs(diag_bundle.DIAG_DIR, exist_ok=True)
            files = []
            for p in sorted(glob.glob(os.path.join(diag_bundle.DIAG_DIR, "*.md")), key=os.path.getmtime, reverse=True)[:40]:
                files.append({"name": os.path.basename(p), "size_kb": round(os.path.getsize(p) / 1024, 1),
                              "mtime": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(os.path.getmtime(p)))})
            self._json({"files": files, "dir": diag_bundle.DIAG_DIR})

        elif parsed.path == "/api/diag/file":
            name = os.path.basename(qs.get("name", [""])[0])
            path = os.path.join(diag_bundle.DIAG_DIR, name)
            if not name.endswith(".md") or not os.path.isfile(path):
                self.send_response(404)
                self.end_headers()
                return
            with open(path, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/markdown; charset=utf-8")
            if qs.get("download", ["0"])[0] == "1":
                self.send_header("Content-Disposition", f'attachment; filename="{name}"')
            self.end_headers()
            self.wfile.write(data)

        elif parsed.path == "/api/dashboard":
            projects = load_projects()
            proj = qs.get("project", [""])[0] or resolve_current_active(projects)
            live = dashboard.live_cmd_vel_topics() if qs.get("live", ["1"])[0] == "1" else []
            self._json(dashboard.resolve(proj, projects.get(proj, {}), live))

        elif parsed.path == "/api/dashboard/check":
            projects = _PROJECTS["data"] or load_projects()
            proj = qs.get("project", [""])[0] or resolve_current_active(projects)
            cfg = dashboard.resolve(proj, projects.get(proj, {}), [])
            topic = qs.get("topic", [""])[0]
            if topic:
                cfg["teleop"]["topics"] = [topic] + [t for t in cfg["teleop"]["topics"] if t != topic]
            idx = int(qs.get("idx", ["-1"])[0])
            if not 0 <= idx < len(cfg["checks"]):
                self._json({"ok": False, "summary": f"no check #{idx}", "output": ""})
                return
            self._json(dashboard.run_check(cfg["checks"][idx], TELEOP, cfg["teleop"]))

        elif parsed.path == "/api/teleop":
            projects = _PROJECTS["data"] or load_projects()
            proj = qs.get("project", [""])[0]
            tel = dashboard.resolve(proj, projects.get(proj, {}), [])["teleop"]
            topic = qs.get("topic", [tel["topics"][0]])[0] or tel["topics"][0]
            if not topic.startswith("/") or not topic.endswith("cmd_vel"):
                self._json({"ok": False, "error": "teleop only publishes to *cmd_vel topics"})
                return
            lin = max(-tel["max_linear"], min(tel["max_linear"], float(qs.get("lin", ["0"])[0])))
            ang = max(-tel["max_angular"], min(tel["max_angular"], float(qs.get("ang", ["0"])[0])))
            mtype = qs.get("type", [tel["msg_type"]])[0] or tel["msg_type"]
            ok = TELEOP.send({"topic": topic, "lin": lin, "ang": ang, "type": mtype})
            self._json({"ok": ok, "lin": lin, "ang": ang, "topic": topic, "helper_alive": TELEOP.alive()})

        elif parsed.path == "/api/teleop_stop":
            self._json({"ok": TELEOP.send({"stop": True})})

        elif parsed.path == "/api/runbook":
            projects = _PROJECTS["data"] or load_projects()
            proj = qs.get("project", [""])[0] or resolve_current_active(projects)
            rb = get_runbook(proj, projects, fresh=True)
            d = rb.as_dict()
            d["defined"] = bool(d.get("stages"))
            self._json(d)

        elif parsed.path == "/api/runbook/start":
            projects = load_projects()
            proj = qs.get("project", [""])[0]
            rb = get_runbook(proj, projects, fresh=True)
            ok = rb.start(qs.get("from", [None])[0] or None)
            if ok and proj:
                hub.update_state("started", proj)
            FOCUS["manual"] = None
            _DETECT["t"] = 0.0
            self._json({"ok": ok, "state": rb.as_dict()})

        elif parsed.path == "/api/runbook/stop":
            rb = RUNBOOKS.get(qs.get("project", [""])[0])
            if rb:
                threading.Thread(target=rb.stop, kwargs={"kill_processes": qs.get("kill", ["1"])[0] == "1"},
                                 daemon=True).start()
            self._json({"ok": rb is not None})

        elif parsed.path in ("/api/runbook/confirm", "/api/runbook/skip"):
            rb = RUNBOOKS.get(qs.get("project", [""])[0])
            sid = qs.get("id", [""])[0]
            ok = bool(rb) and (rb.confirm(sid) if parsed.path.endswith("confirm") else rb.skip(sid))
            self._json({"ok": ok})

        elif parsed.path == "/api/runbook/log":
            name = os.path.basename(qs.get("file", [""])[0])
            path = os.path.join(LOG_DIR, name)
            lines = int(qs.get("lines", ["60"])[0])
            content = ""
            if name.endswith(".log") and os.path.isfile(path):
                with open(path, "r", errors="replace") as f:
                    content = "".join(f.readlines()[-lines:])
            self._json({"content": content})

        elif parsed.path == "/api/tests":
            projects = _PROJECTS["data"] or load_projects()
            proj = qs.get("project", [""])[0] or resolve_current_active(projects)
            out = []
            for t in (projects.get(proj) or {}).get("custom_tests") or []:
                if isinstance(t, dict) and t.get("id"):
                    tid = str(t["id"])
                    rb = get_test(proj, tid, projects, fresh=True)
                    out.append({"id": tid, "title": t.get("title") or tid, "description": t.get("description", ""),
                                "run": rb.as_dict(), "artifacts": _artifacts(t)})
            self._json({"project": proj, "tests": out})

        elif parsed.path == "/api/tests/start":
            projects = load_projects()
            rb = get_test(qs.get("project", [""])[0], qs.get("test", [""])[0], projects, fresh=True)
            ok = bool(rb) and rb.start(qs.get("from", [None])[0] or None)
            _DETECT["t"] = 0.0
            self._json({"ok": ok})

        elif parsed.path == "/api/tests/stop":
            rb = TESTS.get((qs.get("project", [""])[0], qs.get("test", [""])[0]))
            if rb:
                threading.Thread(target=rb.stop, kwargs={"kill_processes": qs.get("kill", ["1"])[0] == "1"},
                                 daemon=True).start()
            self._json({"ok": rb is not None})

        elif parsed.path in ("/api/tests/confirm", "/api/tests/skip"):
            rb = TESTS.get((qs.get("project", [""])[0], qs.get("test", [""])[0]))
            sid = qs.get("id", [""])[0]
            self._json({"ok": bool(rb) and (rb.confirm(sid) if parsed.path.endswith("confirm") else rb.skip(sid))})

        elif parsed.path == "/api/tests/artifact":
            projects = _PROJECTS["data"] or load_projects()
            arts = _artifacts(_test_spec(projects, qs.get("project", [""])[0], qs.get("test", [""])[0]))
            idx = int(qs.get("idx", ["-1"])[0])
            if not 0 <= idx < len(arts) or not arts[idx]["exists"]:
                self.send_response(404)
                self.end_headers()
                return
            fp = arts[idx]["path"]
            self.send_response(200)
            self.send_header("Content-Type", ART_CTYPE.get(os.path.splitext(fp)[1].lower(), "text/plain; charset=utf-8"))
            if qs.get("download", ["0"])[0] == "1":
                self.send_header("Content-Disposition", f'attachment; filename="{os.path.basename(fp)}"')
            self.end_headers()
            with open(fp, "rb") as f:
                self.wfile.write(f.read())

        elif parsed.path == "/api/profile":
            proj = qs.get("project", [""])[0]
            self._json(profile_schema.load_text(proj))

        elif parsed.path == "/api/profile/new":
            name = qs.get("name", [""])[0].strip().lower().replace(" ", "_")
            if not profile_schema.NAME_RE.match(name or "-"):
                self._json({"ok": False, "error": "use lowercase letters, digits and _"})
                return
            if name in (_PROJECTS["data"] or load_projects()):
                self._json({"ok": False, "error": f"project '{name}' already exists"})
                return
            self._json({"ok": True, **profile_schema.load_text(name)})

        elif parsed.path == "/api/analysis/full_matrix":
            self._json(diagnostics.get_full_topic_matrix())

        elif parsed.path == "/api/analysis/topic_hz":
            topic = qs.get("topic", [""])[0]
            self._json(diagnostics.measure_topic_hz(topic))

        elif parsed.path == "/api/analysis/tf_audit":
            self._json(diagnostics.analyze_tf_tree())

        elif parsed.path == "/api/analysis/topics":
            self._json({"topics": diagnostics.get_topics_detailed()})

        elif parsed.path == "/api/analysis/inspect":
            topic = qs.get("topic", [""])[0]
            mode = qs.get("mode", ["info"])[0]
            self._json({"output": diagnostics.inspect_topic(topic, mode)})

        elif parsed.path == "/api/analysis/graph":
            self._json(diagnostics.get_nodes_and_tf())

        elif parsed.path == "/api/analysis/pipeline":
            self._json({"output": diagnostics.audit_nav2_pipeline()})

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="ROSHub web UI")
    ap.add_argument("--port", type=int, default=int(os.environ.get("ROSHUB_PORT", 8090)))
    port = ap.parse_args().port
    sync_and_load_projects()
    print(f"ROSHub running -> http://localhost:{port}")
    httpd = ReusableServer(("0.0.0.0", port), Handler)
    try:
        httpd.serve_forever()
    finally:
        TELEOP.shutdown()
