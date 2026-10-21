#!/usr/bin/env python3
"""Per-project test dashboard backend: resolves the `dashboard:` block of a
project YAML (or builds a sensible default from its contracts), runs the
quick checks, and owns the persistent teleop publisher process.

YAML (all optional, inside a project in ~/.roshub/projects/<file>.yaml):

  dashboard:
    base_frame: base_link
    teleop:
      topics: ["/cmd_vel"]          # first = default; live *cmd_vel topics are added too
      msg_type: twist               # or twist_stamped
      max_linear: 0.5               # m/s
      max_angular: 1.0              # rad/s
    extend_defaults: true           # keep the auto checks and add yours
    checks:
      - {label: "Lidar rate", hz: /scan, min_hz: 5}
      - {label: "Odom pose", echo: /odom, field: pose.pose.position}
      - {label: "map -> base_link", tf: [map, base_link]}
      - {label: "Drive 0.3 m", motion: {lin: 0.2, seconds: 1.5, odom: /odom}}
      - {label: "Nav2 active?", cmd: "ros2 lifecycle get /bt_navigator", timeout: 8}
"""
import os
import re
import json
import time
import signal
import threading
import subprocess

import diagnostics as D
from core import LOG_DIR

HERE = os.path.dirname(os.path.abspath(__file__))
HELPER = os.path.join(HERE, "teleop_helper.py")
HARD_MAX_LIN, HARD_MAX_ANG = 2.0, 3.0


# ----------------------------------------------------------------- config
def resolve(project, pdata, live_topics=None):
    dash = dict(pdata.get("dashboard") or {})
    contracts = pdata.get("contracts") or D.DEFAULT_CONTRACTS
    base = dash.get("base_frame", "base_link")
    tel = dict(dash.get("teleop") or {})
    topics = list(tel.get("topics") or [])
    for t in live_topics or []:
        if t.endswith("cmd_vel") and t not in topics:
            topics.append(t)
    if not topics:
        topics = ["/cmd_vel"]
    tel["topics"] = topics
    tel.setdefault("msg_type", "twist")
    tel["max_linear"] = min(float(tel.get("max_linear", 0.5)), HARD_MAX_LIN)
    tel["max_angular"] = min(float(tel.get("max_angular", 1.0)), HARD_MAX_ANG)

    auto = []
    odom = next((c["topic"] for c in contracts if c.get("expected_type") == "nav_msgs/msg/Odometry"), None)
    for c in contracts:
        t = c["topic"]
        if t.endswith(("tf_static", "cmd_vel")):
            continue
        auto.append({"label": f"Rate {t}", "hz": t})
    auto.append({"label": f"TF odom -> {base}", "tf": ["odom", base]})
    auto.append({"label": f"TF map -> {base}", "tf": ["map", base]})
    if odom:
        auto.append({"label": "Odom pose", "echo": odom, "field": "pose.pose.position"})
        auto.append({"label": "Drive test (0.2 m/s, 1.5 s)", "motion": {"lin": 0.2, "seconds": 1.5, "odom": odom}})
        auto.append({"label": "Turn test (0.5 rad/s, 1.5 s)", "motion": {"ang": 0.5, "seconds": 1.5, "odom": odom}})
    user = list(dash.get("checks") or [])
    checks = (auto + user) if (not user or dash.get("extend_defaults")) else user
    for i, c in enumerate(checks):
        c["idx"] = i
        c["kind"] = next((k for k in ("hz", "echo", "tf", "motion", "cmd") if k in c), "cmd")
    return {"project": project, "teleop": tel, "checks": checks, "base_frame": base,
            "configured": bool(pdata.get("dashboard")), "snippet": snippet(project, tel, odom, base)}


def snippet(project, tel, odom, base):
    return (f"# add under `{project}:` in its file in ~/.roshub/projects/\n"
            f"  dashboard:\n"
            f"    base_frame: {base}\n"
            f"    teleop:\n"
            f"      topics: [\"{tel['topics'][0]}\"]\n"
            f"      msg_type: {tel['msg_type']}\n"
            f"      max_linear: {tel['max_linear']}\n"
            f"      max_angular: {tel['max_angular']}\n"
            f"    extend_defaults: true\n"
            f"    checks:\n"
            f"      - {{label: \"Odom pose\", echo: {odom or '/odom'}, field: pose.pose.position}}\n"
            f"      - {{label: \"Nav2 active?\", cmd: \"ros2 lifecycle get /bt_navigator\", timeout: 8}}\n")


def live_cmd_vel_topics():
    out = D._run("ros2 topic list -t", timeout=8)
    return [m.group(1) for m in re.finditer(r"^(\S*cmd_vel)\s+\[geometry_msgs/msg/Twist(?:Stamped)?\]", out, re.M)]


# ----------------------------------------------------------------- checks
def _ros(cmd, timeout):
    try:
        p = subprocess.run(D.ROS_ENV_PREFIX + cmd, shell=True, executable="/bin/bash", capture_output=True,
                           text=True, timeout=timeout, env=D.ros_env())
        return p.returncode, (p.stdout + "\n" + p.stderr).strip()
    except subprocess.TimeoutExpired as e:
        out = e.stdout.decode(errors="ignore") if isinstance(e.stdout, bytes) else (e.stdout or "")
        return 124, (out or "").strip() + "\n[TIMEOUT]"


def _position(odom):
    rc, out = _ros(f"timeout 5s ros2 topic echo {odom} --once --field pose.pose", 8)
    nums = re.findall(r"(x|y|z|w):\s*(-?[\d.eE+-]+)", out)
    if len(nums) < 7:
        return None
    px, py = float(nums[0][1]), float(nums[1][1])
    qz, qw = float(nums[5][1]), float(nums[6][1])
    import math
    yaw = math.atan2(2 * qw * qz, 1 - 2 * qz * qz)
    return px, py, yaw


def run_check(check, teleop=None, tel_cfg=None):
    k = check.get("kind")
    t0 = time.time()
    if k == "hz":
        rc, out = _ros(f"timeout 7s ros2 topic hz {check['hz']} --window 20", 10)
        rates = re.findall(r"average rate:\s*([0-9.]+)", out)
        r = float(rates[-1]) if rates else 0.0
        ok = r > 0 and r >= float(check.get("min_hz", 0))
        summary = f"{r:.1f} Hz" if rates else "no messages in 7 s"
    elif k == "echo":
        field = f" --field {check['field']}" if check.get("field") else ""
        rc, out = _ros(f"timeout 5s ros2 topic echo {check['echo']} --once{field}", 8)
        ok = rc == 0 and out and "[TIMEOUT]" not in out
        summary = "received" if ok else "no message in 5 s"
        out = out[:1500]
    elif k == "tf":
        p, c = check["tf"]
        rc, out = _ros(f"timeout 6s ros2 run tf2_ros tf2_echo {p} {c}", 9)
        m = re.search(r"Translation:\s*\[([^\]]+)\]", out)
        ok = bool(m)
        summary = f"translation [{m.group(1)}]" if m else f"no transform {p} -> {c}"
        out = out[-1200:]
    elif k == "motion":
        mo = check["motion"]
        odom = mo.get("odom", "/odom")
        if teleop is None or tel_cfg is None:
            return {"ok": False, "summary": "teleop not available", "output": "", "seconds": 0}
        before = _position(odom)
        if before is None:
            return {"ok": False, "summary": f"could not read {odom} before driving", "output": "", "seconds": 0}
        lin = max(-tel_cfg["max_linear"], min(tel_cfg["max_linear"], float(mo.get("lin", 0))))
        ang = max(-tel_cfg["max_angular"], min(tel_cfg["max_angular"], float(mo.get("ang", 0))))
        topic = mo.get("topic", tel_cfg["topics"][0])
        end = time.time() + float(mo.get("seconds", 1.5))
        while time.time() < end:
            teleop.send({"topic": topic, "lin": lin, "ang": ang, "type": tel_cfg["msg_type"]})
            time.sleep(0.1)
        teleop.send({"stop": True})
        time.sleep(0.8)
        after = _position(odom)
        if after is None:
            return {"ok": False, "summary": f"could not read {odom} after driving", "output": "", "seconds": round(time.time() - t0, 1)}
        import math
        dist = math.hypot(after[0] - before[0], after[1] - before[1])
        dyaw = math.degrees(math.atan2(math.sin(after[2] - before[2]), math.cos(after[2] - before[2])))
        moved = dist > 0.02 or abs(dyaw) > 2
        ok = moved
        summary = f"moved {dist:.2f} m, turned {dyaw:+.0f}°" + ("" if moved else "  (robot did NOT move: check cmd_vel subscriber / sim playing)")
        out = f"before x={before[0]:.3f} y={before[1]:.3f} yaw={math.degrees(before[2]):.1f}°\n" \
              f"after  x={after[0]:.3f} y={after[1]:.3f} yaw={math.degrees(after[2]):.1f}°\n" \
              f"command lin={lin} ang={ang} on {topic}"
    else:
        rc, out = _ros(check.get("cmd", "true"), int(check.get("timeout", 10)))
        ok = rc == 0
        summary = f"exit {rc}"
        out = out[-2000:]
    return {"ok": bool(ok), "summary": summary, "output": out, "seconds": round(time.time() - t0, 1)}


# ----------------------------------------------------------------- teleop process
class TeleopBridge:
    def __init__(self):
        self.proc = None
        self.lock = threading.Lock()

    def _start(self):
        os.makedirs(LOG_DIR, exist_ok=True)
        log = open(os.path.join(LOG_DIR, "_roshub_teleop.log"), "a")
        self.proc = subprocess.Popen(["python3", HELPER], stdin=subprocess.PIPE, stdout=log,
                                     stderr=subprocess.STDOUT, text=True, env=D.ros_env(),
                                     preexec_fn=os.setsid, bufsize=1)

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def send(self, cmd):
        line = json.dumps(cmd) + "\n"
        with self.lock:
            for attempt in (1, 2):
                if not self.alive():
                    if cmd.get("stop"):
                        return True        # nothing running, nothing to stop
                    self._start()
                try:
                    self.proc.stdin.write(line)
                    self.proc.stdin.flush()
                    return True
                except (BrokenPipeError, OSError):
                    self.proc = None
        return False

    def shutdown(self):
        with self.lock:
            if self.alive():
                try:
                    self.proc.stdin.write(json.dumps({"stop": True}) + "\n")
                    self.proc.stdin.flush()
                    time.sleep(0.4)
                    os.killpg(os.getpgid(self.proc.pid), signal.SIGINT)
                except Exception:
                    pass
            self.proc = None
