#!/usr/bin/env python3
"""ROSHub LLM / issue report: one Markdown file with everything someone needs to help you.

  python3 ~/.roshub/sysreport.py [project]        -> writes ~/.roshub/diag/report_<project>_<time>.md

Sections: system (OS, WSL, kernel, CPU, RAM, GPU/driver), ROS (distro, RMW, key package versions),
Windows side (Isaac Sim, Zenoh client config), project (YAML, bring-up state, failing stage logs),
and the bridge doctor result. Secrets are masked: any env var or YAML value whose key looks like a
token/password/key, plus the user's home path and e-mail-like strings.
Stdlib only; every probe has a timeout, and a failing probe is reported, never fatal.
"""
import datetime
import glob
import json
import os
import platform
import re
import subprocess
import sys

ROOT = os.path.expanduser("~/.roshub")
LOG_DIR = os.path.expanduser("~/.roshub_logs")
OUT_DIR = os.path.join(ROOT, "diag")
HOME = os.path.expanduser("~")
SECRET_KEY = re.compile(r"(token|secret|passw|api[_-]?key|auth|credential)", re.I)
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
ROS_PKGS = ["ros-jazzy-rmw-zenoh-cpp", "ros-jazzy-navigation2", "ros-jazzy-nav2-bringup", "ros-jazzy-rmf-fleet-adapter",
            "ros-jazzy-rmf-traffic", "ros-jazzy-rclpy", "ros-jazzy-tf2-ros", "python3-yaml"]


def sh(cmd, timeout=10):
    try:
        r = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, timeout=timeout)
        return (r.stdout + r.stderr).strip()
    except subprocess.TimeoutExpired:
        return f"(timed out after {timeout}s)"
    except Exception as e:  # noqa: BLE001
        return f"(failed: {e})"


def scrub(text):
    text = text.replace(HOME, "~")
    text = EMAIL.sub("<email>", text)
    return re.sub(r"(?im)^(\s*[\w.-]*(?:token|secret|passw|api[_-]?key|auth|credential)[\w.-]*\s*[:=]\s*).+$",
                  r"\1<masked>", text)


def system_info():
    mem = sh("free -h | awk '/Mem:/ {print $2\" total, \"$7\" available\"}'")
    wsl = sh("wsl.exe --version 2>/dev/null | tr -d '\\0' | head -3") if os.path.exists("/mnt/c") else "not WSL"
    gpu = sh("nvidia-smi --query-gpu=name,driver_version,memory.total,power.limit --format=csv,noheader", 15)
    if "not found" in gpu or "failed" in gpu:
        gpu = sh("/usr/lib/wsl/lib/nvidia-smi --query-gpu=name,driver_version,memory.total,power.limit "
                 "--format=csv,noheader", 15)
    return [
        ("OS", sh("lsb_release -ds 2>/dev/null || cat /etc/os-release | head -1")),
        ("Kernel", platform.release()),
        ("WSL", wsl or "unknown"),
        ("CPU", sh("lscpu | sed -n 's/^Model name:\\s*//p'") + f" ({os.cpu_count()} threads)"),
        ("RAM", mem),
        ("GPU", gpu or "nvidia-smi unavailable"),
        ("Python", sys.version.split()[0]),
        ("Disk (home)", sh("df -h ~ | awk 'NR==2 {print $4\" free of \"$2}'")),
    ]


def ros_info():
    rows = [
        ("ROS_DISTRO", os.environ.get("ROS_DISTRO", "(not sourced in ROSHub's shell)")),
        ("RMW_IMPLEMENTATION", os.environ.get("RMW_IMPLEMENTATION", "(default)")),
        ("ROS_DOMAIN_ID", os.environ.get("ROS_DOMAIN_ID", "0")),
        ("Zenoh router :7447", "listening" if ":7447 " in sh("ss -tln") else "NOT listening"),
    ]
    vers = sh("dpkg-query -W -f='${Package} ${Version}\\n' " + " ".join(ROS_PKGS) + " 2>/dev/null")
    return rows, vers


def windows_info():
    rows = []
    for p in ("/mnt/c/isaacsim/VERSION", "/mnt/c/isaacsim/version.txt"):
        if os.path.isfile(p):
            rows.append(("Isaac Sim", open(p, errors="ignore").read().strip()[:80]))
            break
    else:
        rows.append(("Isaac Sim", "version file not found under C:\\isaacsim"))
    running = sh("tasklist.exe 2>/dev/null | grep -i -E 'isaac|kit' | head -3")
    rows.append(("Isaac process", running or "not running (or tasklist.exe unavailable)"))
    cfg = "/mnt/c/ros2_config/zenoh_session.json5"
    rows.append(("Zenoh client config", "present" if os.path.isfile(cfg) else "missing"))
    return rows, (open(cfg, errors="ignore").read()[:1500] if os.path.isfile(cfg) else "")


def project_info(project, projects, runbook_state):
    yml = ""
    if project and project in projects:
        import yaml
        yml = yaml.safe_dump({project: projects[project]}, sort_keys=False, default_flow_style=False)
    logs = []
    for st in (runbook_state or {}).get("stages", []):
        if st.get("state") in ("failed", "waiting", "starting") and st.get("log"):
            p = os.path.join(LOG_DIR, st["log"])
            if os.path.isfile(p):
                with open(p, errors="replace") as f:
                    tail = f.readlines()[-60:]
                logs.append((st["id"], st.get("message", ""), "".join(tail)))
    return yml, logs


def doctor():
    for p in (os.path.join(ROOT, "tools", "bridge_doctor.py"), os.path.join(HOME, "bridge_doctor.py")):
        if os.path.isfile(p):
            out = sh(f"python3 {p} --quick --json 2>/dev/null || python3 {p} --json 2>/dev/null", 60)
            try:
                return p, json.loads(out[out.index("{"):])
            except ValueError:
                return p, {"raw": out[-3000:]}
    return None, {}


def build(project="", projects=None, runbook_state=None, include_doctor=True):
    projects = projects or {}
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    L = [f"# ROSHub report: {project or 'system'}", "", f"Generated {now}. Paste this whole file into an LLM, or "
         "attach it to a GitHub issue. Home paths and secrets are masked.", ""]

    def table(title, rows):
        L.extend([f"## {title}", "", "| Item | Value |", "|---|---|"])
        L.extend(f"| {k} | {str(v).replace('|', '/').replace(chr(10), '<br>')} |" for k, v in rows)
        L.append("")

    table("System", system_info())
    rows, vers = ros_info()
    table("ROS 2", rows)
    L.extend(["### Package versions", "```", vers or "(dpkg-query found none)", "```", ""])
    wrows, zcfg = windows_info()
    table("Windows side", wrows)
    if zcfg:
        L.extend(["### Zenoh client config (Windows)", "```json5", zcfg, "```", ""])
    if project:
        yml, logs = project_info(project, projects, runbook_state)
        L.extend([f"## Project `{project}`", ""])
        if runbook_state:
            L.extend(["### Bring-up state", "", f"Overall: **{runbook_state.get('state')}**", "",
                      "| Stage | State | Message |", "|---|---|---|"])
            L.extend(f"| {s['title']} | {s['state']} | {s.get('message', '').replace('|', '/')} |"
                     for s in runbook_state.get("stages", []))
            L.append("")
        for sid, msg, tail in logs:
            L.extend([f"### Log tail: stage `{sid}` ({msg})", "```", tail.rstrip(), "```", ""])
        if yml:
            L.extend(["### Project YAML", "```yaml", yml.rstrip(), "```", ""])
    if include_doctor:
        path, res = doctor()
        L.extend(["## Bridge doctor", ""])
        if not path:
            L.append("bridge_doctor.py not found.")
        else:
            L.extend([f"`{os.path.basename(path)}`", "```json", json.dumps(res, indent=1)[:12000], "```"])
        L.append("")
    L.extend(["## What I need help with", "", "_Describe what you expected and what happened instead._", ""])
    return scrub("\n".join(L))


def write(project="", projects=None, runbook_state=None):
    os.makedirs(OUT_DIR, exist_ok=True)
    name = f"report_{project or 'system'}_{datetime.datetime.now():%Y%m%d_%H%M%S}.md"
    path = os.path.join(OUT_DIR, name)
    with open(path, "w") as f:
        f.write(build(project, projects, runbook_state))
    return path


if __name__ == "__main__":
    sys.path.insert(0, ROOT)
    from core import sync_and_load_projects
    proj = sys.argv[1] if len(sys.argv) > 1 else ""
    print(write(proj, sync_and_load_projects()))
