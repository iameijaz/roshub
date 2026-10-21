#!/usr/bin/env python3
"""ROSHub runbook: guided, gated bring-up of a project.

A project YAML may declare a `runbook:` — ordered stages. Each stage optionally starts
something, then waits until all its `ready` checks pass (or times out):

  runbook:
    - id: router
      title: "Zenoh router"
      reuse: true                         # if the ready checks already pass, don't start anything
      run: {cmd: "ros2 run rmw_zenoh_cpp rmw_zenohd", bg: true}
      ready: [{port: 7447}]
      timeout: 15
    - id: isaac
      title: "Isaac Sim playing"
      manual: "Open the warehouse scene in Isaac Sim, press Play"
      ready: [{hz: /clock, min_hz: 5}]
      timeout: 600
    - id: nav2
      run: {cmd: "ros2 launch husky_nav2 navigation.launch.py", bg: true}
      ready:
        - {nodes: amcl, min: 3}
        - {echo: /amcl_pose, latched: true}
      timeout: 120
    - id: smoke
      run: {cmd: "ros2 action send_goal /navigate_to_pose nav2_msgs/action/NavigateToPose '{pose: {header: {frame_id: map}}}'"}    # foreground: must exit 0
      ready: [{log: "Goal accepted"}]
      optional: true                      # failure doesn't stop the runbook

Check kinds: hz, echo, tf, cmd (shared with the test dashboard) and
  log:   regex that must appear `count` times in this stage's log
  nodes: substring that must match at least `min` nodes in `ros2 node list`
  port:  TCP port that must accept connections on localhost

Stage states: pending → starting → waiting → passed | reused | failed | skipped
Background processes started by a runbook belong to it and are stopped by "Stop".
"""
import os
import re
import signal
import socket
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor

KINDS = ("hz", "echo", "tf", "cmd", "log", "nodes", "port")


def _child_setup():
    """New process group (so Stop reaches launch's children) with default signal handling,
    even when ROSHub itself was started with SIGINT ignored (nohup, background job)."""
    os.setsid()
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    signal.signal(signal.SIGTERM, signal.SIG_DFL)


POLL_S = 2.0


def _build_done(log_path):
    """ensure_built.sh prints one of these once the workspace is ready to launch."""
    try:
        with open(log_path, errors="ignore") as f:
            text = f.read()
    except OSError:
        return False
    return "ROSHub: workspace up to date" in text or "ROSHub: build OK" in text


def check_kind(check):
    return next((k for k in KINDS if k in check), None)


def describe(check):
    k = check_kind(check)
    if k == "hz":
        return f"{check['hz']} ≥ {check.get('min_hz', 0.1)} Hz"
    if k == "echo":
        return f"message on {check['echo']}"
    if k == "tf":
        return f"TF {check['tf'][0]} → {check['tf'][1]}"
    if k == "log":
        n = int(check.get("count", 1))
        return f"log shows '{check['log']}'" + (f" ×{n}" if n > 1 else "")
    if k == "nodes":
        return f"≥{check.get('min', 1)} nodes matching '{check['nodes']}'"
    if k == "port":
        return f"port {check['port']} open"
    return check.get("label") or f"`{check.get('cmd', '')}` exits 0"


class Stage:
    def __init__(self, spec, index):
        self.spec = spec
        self.id = str(spec.get("id") or f"stage{index + 1}")
        self.title = spec.get("title") or self.id
        self.state = "pending"
        self.started = None
        self.finished = None
        self.message = ""
        self.checks = [{"desc": describe(c), "ok": None, "summary": ""} for c in spec.get("ready") or []]
        self.proc = None
        self.log_path = None
        self.confirmed = False      # manual stages: user pressed "Done"

    def as_dict(self):
        return {"id": self.id, "title": self.title, "state": self.state, "message": self.message,
                "manual": self.spec.get("manual"), "optional": bool(self.spec.get("optional")),
                "cmd": (self.spec.get("run") or {}).get("cmd", ""), "bg": bool((self.spec.get("run") or {}).get("bg")),
                "elapsed": round((self.finished or time.time()) - self.started, 1) if self.started else None,
                "timeout": float(self.spec.get("timeout", 60)), "checks": self.checks,
                "log": os.path.basename(self.log_path) if self.log_path else None,
                "hint": self.spec.get("hint", ""),
                "fixes": [{"label": f.get("label", "Fix")} for f in self.spec.get("fixes") or []],
                "pid": self.proc.pid if self.proc and self.proc.poll() is None else None}


class Runbook:
    """One runbook run. `evaluate(check, stage)` -> dict(ok, summary) is injected (tests use fakes)."""

    def __init__(self, project, stages, env, log_dir, evaluate, popen=subprocess.Popen, clock=time.time):
        self.project = project
        self.stages = [Stage(s, i) for i, s in enumerate(stages)]
        self.env = env
        self.log_dir = log_dir
        self.evaluate = evaluate
        self.popen = popen
        self.clock = clock
        self.thread = None
        self.stop_event = threading.Event()
        self.lock = threading.RLock()
        self.state = "idle"          # idle | running | done | failed | stopped
        self.current = None

    # ------------------------------------------------------------ control
    def start(self, from_id=None):
        with self.lock:
            if self.thread and self.thread.is_alive():
                return False
            begin = 0
            if from_id:
                begin = next((i for i, s in enumerate(self.stages) if s.id == from_id), 0)
            for s in self.stages[begin:]:      # earlier stages keep their state ("resume from here")
                self._reset(s)
            self.stop_event.clear()
            self.state = "running"
            self.thread = threading.Thread(target=self._run, args=(begin,), daemon=True)
            self.thread.start()
            return True

    def stop(self, kill_processes=True):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=15)
        if kill_processes:
            for s in reversed(self.stages):
                self._kill(s)
        with self.lock:
            if self.state == "running" or (kill_processes and self.state in ("done", "failed")):
                self.state = "stopped"

    def confirm(self, stage_id):
        for s in self.stages:
            if s.id == stage_id:
                s.confirmed = True
                return True
        return False

    def skip(self, stage_id):
        for s in self.stages:
            if s.id == stage_id and s.state in ("pending", "waiting", "starting", "failed"):
                s.state, s.message, s.finished = "skipped", "skipped by user", self.clock()
                return True
        return False

    def as_dict(self):
        with self.lock:
            return {"project": self.project, "state": self.state, "current": self.current,
                    "stages": [s.as_dict() for s in self.stages]}

    def owns_running_process(self):
        return any(s.proc is not None and s.proc.poll() is None for s in self.stages)

    # ------------------------------------------------------------ engine
    def _reset(self, s):
        s.state, s.message, s.started, s.finished, s.confirmed = "pending", "", None, None, False
        for c in s.checks:
            c["ok"], c["summary"] = None, ""

    def _run(self, begin):
        failed = False
        for s in self.stages[begin:]:
            if self.stop_event.is_set():
                break
            if s.state == "skipped":
                continue
            with self.lock:
                self.current = s.id
            ok = self._run_stage(s)
            if not ok and not s.spec.get("optional"):
                failed = True
                break
        with self.lock:
            self.current = None
            if self.stop_event.is_set():
                self.state = "stopped"
            else:
                self.state = "failed" if failed else "done"

    def _run_stage(self, s):
        s.started = self.clock()
        timeout = float(s.spec.get("timeout", 60))
        checks = s.spec.get("ready") or []
        if s.spec.get("reuse") and checks and self._check_all(s, checks):
            s.state, s.message, s.finished = "reused", "already up: nothing started", self.clock()
            return True
        run = s.spec.get("run") or {}
        s.log_path = os.path.join(self.log_dir, f"{self.project}_rb_{s.id}.log")
        if run.get("cmd"):
            s.state = "starting"
            log = open(s.log_path, "w")
            env = dict(self.env)
            env.update({k: str(v) for k, v in (s.spec.get("env") or {}).items()})
            try:
                s.proc = self.popen(["bash", "-c", run["cmd"]], env=env, stdout=log, stderr=subprocess.STDOUT,
                                    preexec_fn=_child_setup)
            except Exception as e:
                return self._finish(s, "failed", f"could not start: {e}")
            if not run.get("bg"):
                # foreground: wait for exit (still honouring stop and timeout), exit code must be 0
                while s.proc.poll() is None:
                    if self.stop_event.is_set():
                        self._kill(s)
                        return self._finish(s, "failed", "stopped")
                    if self.clock() - s.started > timeout:
                        self._kill(s)
                        return self._finish(s, "failed", f"command did not finish within {timeout:.0f}s")
                    time.sleep(0.2)
                if s.proc.returncode != 0:
                    return self._finish(s, "failed", f"command exited with code {s.proc.returncode}")
        elif not s.spec.get("manual"):
            open(s.log_path, "w").close()
        s.state = "waiting"
        s.message = s.spec.get("manual") or ("waiting for ready checks" if checks else "")
        if not checks and not s.spec.get("manual"):
            return self._finish(s, "passed", "started" if run.get("bg") else "done")
        while not self.stop_event.is_set():
            if run.get("bg") and s.proc is not None and s.proc.poll() is not None:
                return self._finish(s, "failed", f"process exited (code {s.proc.returncode}) before it was ready; "
                                                 f"see {os.path.basename(s.log_path)}")
            if "ensure_built.sh" in run.get("cmd", "") and not _build_done(s.log_path):
                s.message = "building workspace (ready checks start after the build)"
                if self.clock() - s.started > timeout:
                    return self._finish(s, "failed", f"build did not finish within {timeout:.0f}s")
                self.stop_event.wait(POLL_S)
                continue
            all_ok = self._check_all(s, checks) if checks else False
            if s.spec.get("manual") and not checks and s.confirmed:
                return self._finish(s, "passed", "confirmed")
            if all_ok:
                return self._finish(s, "passed", "ready")
            if self.clock() - s.started > timeout:
                bad = [c["desc"] for c in s.checks if not c["ok"]]
                return self._finish(s, "failed", f"not ready after {timeout:.0f}s: " + "; ".join(bad))
            self.stop_event.wait(POLL_S)
        return self._finish(s, "failed", "stopped")

    def _check_all(self, s, checks):
        with ThreadPoolExecutor(max_workers=4) as ex:
            results = list(ex.map(lambda c: self._safe_eval(c, s), checks))
        for slot, r in zip(s.checks, results):
            slot["ok"], slot["summary"] = bool(r.get("ok")), r.get("summary", "")
        return all(r.get("ok") for r in results)

    def _safe_eval(self, check, stage):
        try:
            return self.evaluate(check, stage)
        except Exception as e:
            return {"ok": False, "summary": f"check error: {e}"}

    def _finish(self, s, state, msg):
        s.state, s.message, s.finished = state, msg, self.clock()
        return state == "passed"

    def _kill(self, s):
        p = s.proc
        if p is None or p.poll() is not None:
            return
        # SIGINT lets ros2 launch shut its nodes down cleanly; escalate if it doesn't
        try:
            pgid = os.getpgid(p.pid)
        except ProcessLookupError:
            return
        for sig, wait in ((signal.SIGINT, 5.0), (signal.SIGTERM, 3.0), (signal.SIGKILL, 1.0)):
            try:
                os.killpg(pgid, sig)
            except (ProcessLookupError, PermissionError):
                return
            end = time.time() + wait
            while time.time() < end:
                if p.poll() is not None:
                    return
                time.sleep(0.1)


# ---------------------------------------------------------------- real checks (ROSHub)
def make_evaluator(run_dashboard_check, ros_run):
    """Evaluator for live use. run_dashboard_check(check) handles hz/echo/tf/cmd;
    ros_run(cmd, timeout) -> text runs a ROS CLI command."""

    def evaluate(check, stage):
        k = check_kind(check)
        if k == "log":
            n = int(check.get("count", 1))
            try:
                with open(stage.log_path, errors="ignore") as f:
                    found = len(re.findall(check["log"], f.read()))
            except (OSError, TypeError):
                found = 0
            return {"ok": found >= n, "summary": f"{found}/{n} matches"}
        if k == "nodes":
            out = ros_run("ros2 node list", 15)
            n = sum(1 for line in out.splitlines() if check["nodes"] in line)
            need = int(check.get("min", 1))
            return {"ok": n >= need, "summary": f"{n}/{need} nodes"}
        if k == "port":
            try:
                with socket.create_connection(("127.0.0.1", int(check["port"])), timeout=1):
                    return {"ok": True, "summary": "open"}
            except OSError:
                return {"ok": False, "summary": "closed"}
        if k == "echo" and check.get("latched"):
            c = dict(check, kind="cmd", timeout=8,
                     cmd=f"timeout 6s ros2 topic echo {check['echo']} --once "
                         "--qos-durability transient_local --qos-reliability reliable | grep -q .")
            r = run_dashboard_check(c)
            return {"ok": r["ok"], "summary": "received" if r["ok"] else "no message"}
        r = run_dashboard_check(dict(check, kind=k or "cmd"))
        return {"ok": r["ok"], "summary": r.get("summary", "")}
    return evaluate
