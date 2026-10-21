#!/usr/bin/env python3
"""Detect which ROSHub project is actually running by matching live processes
(/proc/*/cmdline) against each project's step commands. Stdlib only, cheap
enough to run on every /api/status poll."""
import os
import re
import shlex

# Steps every Isaac project shares; they prove nothing about *which* project runs.
SHARED_SIGNATURES = {("rmw_zenoh_cpp", "rmw_zenohd")}
_ROS_CMD = re.compile(r"ros2\s+(launch|run)\s+(\S+)(?:\s+(\S+))?")


def list_processes():
    """[(pid, argv_string)] for every readable process except this one."""
    me = os.getpid()
    out = []
    for d in os.listdir("/proc"):
        if not d.isdigit() or int(d) == me:
            continue
        try:
            with open(f"/proc/{d}/cmdline", "rb") as f:
                raw = f.read()
        except Exception:
            continue
        if not raw:
            continue
        args = raw.replace(b"\0", b" ").decode(errors="ignore").strip()
        out.append((int(d), args))
    return out


def step_signature(cmd):
    """Tokens that must all appear in one process cmdline for this step to count as running.
    None = step is not detectable (build steps, cd/app scripts without a clear binary)."""
    if not cmd:
        return None
    c = cmd.replace('\\"', '"')
    m = _ROS_CMD.search(c)
    if m:
        pkg, target = m.group(2).strip("'\";"), (m.group(3) or "").strip("'\";") or None
        if target and (target.startswith("-") or ":=" in target):
            target = None
        if pkg.endswith((".py", ".xml", ".yaml")):          # ros2 launch /path/file.launch.py
            return (os.path.basename(pkg),)
        return tuple(t for t in (pkg, target) if t)
    if "colcon build" in c:
        return None
    m = re.search(r"\brviz2\b(?:\s+-d\s+(\S+))?", c)
    if m:
        return ("rviz2",) + ((os.path.basename(m.group(1)),) if m.group(1) else ())
    m = re.search(r"python3?\s+(\S+\.py)", c)
    if m:
        return (os.path.basename(m.group(1)),)
    try:
        first = shlex.split(c)[0]
    except Exception:
        first = c.split()[0]
    first = os.path.basename(first)
    if first in ("bash", "sh", "cd", "source", "exec", "export"):
        return None
    return (first,)


def _matches(sig, procs):
    for pid, args in procs:
        if all(tok in args for tok in sig):
            # skip the shell wrappers ROSHub itself spawns ("bash -c source ... && exec ...")
            if args.startswith(("bash -c", "/bin/bash -c", "sh -c")) and "exec" in args:
                continue
            return pid
    return None


def detect(projects, procs=None, launcher_alive=None):
    """Score every project. Returns {name: {state, matched, total, pids, steps}} plus
    '_best' -> name of the most likely running project ('' if none).
    launcher_alive: set of project names whose roshub.py launcher process is alive."""
    procs = list_processes() if procs is None else procs
    launcher_alive = set(launcher_alive or ())
    for pid, args in procs:                    # roshub.py <project> launched from a terminal
        m = re.search(r"roshub\.py\s+(\S+)", args)
        if m and m.group(1) in projects:
            launcher_alive.add(m.group(1))

    result = {}
    best, best_key = "", None
    for name, data in projects.items():
        steps = list(data.get("steps", []) or []) if isinstance(data, dict) else []
        # runbook stages that start background processes count as steps too (deduplicated)
        sigs = {step_signature(st.get("cmd", "")) for st in steps}
        for st in (data.get("runbook") or []) if isinstance(data, dict) else []:
            run = st.get("run") or {} if isinstance(st, dict) else {}
            sig = step_signature(run.get("cmd", "")) if run.get("bg") else None
            if sig and sig not in sigs:
                sigs.add(sig)
                steps.append({"name": st.get("id", "stage"), "cmd": run["cmd"]})
        detail, matched, total, pids = [], 0, 0, []
        for st in steps:
            sig = step_signature(st.get("cmd", ""))
            shared = sig in SHARED_SIGNATURES if sig else False
            pid = _matches(sig, procs) if sig else None
            detail.append({"name": st.get("name", "step"), "running": pid is not None,
                           "pid": pid, "shared": shared, "detectable": sig is not None})
            if sig and not shared:
                total += 1
                if pid:
                    matched += 1
                    pids.append(pid)
        alive = name in launcher_alive
        # projects whose steps are alternative modes (view/teleop/slam/nav...) count as running
        # as soon as any one mode is up, so 'partial' only applies to chained stacks
        modes = total > 1 and all(st.get("cmd", "").count("mode:=") for st in steps
                                  if step_signature(st.get("cmd", "")) and "colcon" not in st.get("cmd", ""))
        if matched == 0 and not alive:
            state = "stopped"
        elif alive or matched == total or modes:
            state = "running"
        else:
            state = "partial"
        result[name] = {"state": state, "matched": matched, "total": total,
                        "pids": pids, "launcher": alive, "steps": detail}
        if state != "stopped":
            key = (alive, matched, matched / total if total else 0, -total)
            if best_key is None or key > best_key:
                best, best_key = name, key
    result["_best"] = best
    return result


if __name__ == "__main__":
    from core import sync_and_load_projects
    res = detect(sync_and_load_projects())
    best = res.pop("_best")
    for n, r in sorted(res.items(), key=lambda kv: kv[1]["state"]):
        if r["state"] != "stopped":
            print(f"{n:<28} {r['state']:<8} {r['matched']}/{r['total']}  pids={r['pids']}")
    print("best guess:", best or "(nothing running)")
