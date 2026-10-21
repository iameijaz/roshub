#!/usr/bin/env python3
"""ROSHub diagnostic bundle: one grouped Markdown report covering env, router,
bridge doctor, processes, nodes, topics/contracts, Hz, TF tree and grouped logs.

  python3 ~/.roshub/diag_bundle.py            # writes ~/.roshub/diag/roshub_diag_<project>_<time>.md
  python3 ~/.roshub/diag_bundle.py --print    # also prints it
  (or press "Generate Diagnostic Report" in the ROSHub dashboard)
"""
import os
import re
import sys
import glob
import time
import socket
import argparse
import threading
import subprocess
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import diagnostics as D                      # noqa: E402
import loggroup as LG                        # noqa: E402
import project_detect as PD                  # noqa: E402
from core import sync_and_load_projects, get_active_project, LOG_DIR   # noqa: E402

DIAG_DIR = os.path.expanduser("~/.roshub/diag")
ROS_LOG_DIR = os.path.expanduser("~/.ros/log")
DOCTOR = os.path.join(HERE, "tools", "bridge_doctor.py")
TF_WINDOW = 6.0                               # seconds of /tf collected
HZ_WINDOW = 8                                 # seconds per rate sample
_ROS_SLOTS = threading.BoundedSemaphore(4)    # max parallel ros2 CLI processes (each one is a full rclpy start-up)
_RELAYED = re.compile(r"^\d+\.\d+ \[[A-Za-z_][\w./]*-\d+\] \[")   # launch.log copies of node output
_SENSOR_TYPES = ("sensor_msgs/msg/", "nav_msgs/msg/Odometry")
MAX_LOG_ENTRIES = 80
_TF_RE = re.compile(r"sec:\s*(\d+)\s*\n\s*nanosec:\s*(\d+)\s*\n\s*frame_id:\s*(\S+)\s*\n\s*child_frame_id:\s*(\S+)")
_BASE_FRAMES = ("base_link", "base_footprint", "chassis_link")


# ----------------------------------------------------------------- collectors
def _sh(cmd, timeout):
    with _ROS_SLOTS:
        t = time.time()
        out = D._run(cmd, timeout=timeout)
        return out, time.time() - t


def proc_start(pid):
    """Wall-clock start time of a process (from /proc), or None."""
    try:
        with open("/proc/stat") as f:
            btime = next(int(l.split()[1]) for l in f if l.startswith("btime"))
        with open(f"/proc/{pid}/stat") as f:
            ticks = int(f.read().rsplit(")", 1)[1].split()[19])
        return btime + ticks / os.sysconf("SC_CLK_TCK")
    except Exception:
        return None


def proc_env_mnt(pid):
    """How many /mnt/... entries the process's own ROS paths contain (0 = clean)."""
    try:
        with open(f"/proc/{pid}/environ", "rb") as f:
            env = dict(i.split("=", 1) for i in f.read().decode(errors="ignore").split("\0") if "=" in i)
    except Exception:
        return None
    return sum(p.startswith("/mnt/") for k in ("AMENT_PREFIX_PATH", "PYTHONPATH", "LD_LIBRARY_PATH")
               for p in env.get(k, "").split(":"))


def collect_env():
    env = D.ros_env()
    t = time.time()
    try:
        subprocess.run(["bash", "--noprofile", "--norc", "-c", "source ~/.roshub/ros_env.sh >/dev/null 2>&1"],
                       env={k: env[k] for k in ("HOME", "PATH") if k in env}, timeout=60)
        src_s = time.time() - t
    except Exception:
        src_s = None
    paths = {k: [p for p in env.get(k, "").split(":") if p] for k in ("AMENT_PREFIX_PATH", "PYTHONPATH", "LD_LIBRARY_PATH")}
    return {
        "RMW_IMPLEMENTATION": env.get("RMW_IMPLEMENTATION", "(unset)"),
        "ROS_DOMAIN_ID": env.get("ROS_DOMAIN_ID", "0 (default)"),
        "ROS_DISTRO": env.get("ROS_DISTRO", "?"),
        "ZENOH_CONFIG_OVERRIDE": env.get("ZENOH_CONFIG_OVERRIDE", ""),
        "ZENOH_SESSION_CONFIG_URI": env.get("ZENOH_SESSION_CONFIG_URI", ""),
        "ament": paths["AMENT_PREFIX_PATH"],
        "mnt_paths": sum(p.startswith("/mnt/") for v in paths.values() for p in v),
        "source_seconds": src_s,
    }


def collect_doctor():
    if not os.path.isfile(DOCTOR):
        return {"available": False}
    try:
        p = subprocess.run(["python3", DOCTOR], capture_output=True, text=True, timeout=120, env=D.ros_env())
        out = (p.stdout + p.stderr).strip()
    except subprocess.TimeoutExpired:
        out = "[TIMEOUT after 120 s]"
    lines = out.splitlines()
    issues, i = [], 0
    while i < len(lines):
        ln = lines[i]
        if "[FAIL]" in ln or "[WARN]" in ln:
            block = [ln.strip()]
            while i + 1 < len(lines) and lines[i + 1].strip().startswith("↳"):
                i += 1
                block.append("   " + lines[i].strip())
            issues.append("\n".join(block))
        i += 1
    summary = next((l.strip() for l in reversed(lines) if "pass," in l and "fail" in l), "")
    return {"available": True, "issues": issues, "summary": summary, "raw": out}


def parse_node_list(raw):
    nodes = [l.strip() for l in raw.splitlines() if l.strip().startswith("/")]
    dup = sorted({n for n in nodes if nodes.count(n) > 1})
    return nodes, dup


def parse_tf(raw, static):
    edges = {}
    for m in _TF_RE.finditer(raw):
        sec, nsec, parent, child = int(m.group(1)), int(m.group(2)), m.group(3).strip("'\""), m.group(4).strip("'\"")
        stamp = sec + nsec / 1e9
        e = edges.setdefault((parent, child), {"count": 0, "last": 0.0, "static": static})
        e["count"] += 1
        e["last"] = max(e["last"], stamp)
    return edges


def analyze_tf(ns, dyn_raw, stat_raw, clock_sec):
    edges = parse_tf(dyn_raw, False)
    for k, v in parse_tf(stat_raw, True).items():
        edges.setdefault(k, v)
    parents_of = {}
    for (p, c) in edges:
        parents_of.setdefault(c, set()).add(p)
    frames = {f for e in edges for f in e}
    children = set(parents_of)
    roots = sorted(frames - children)
    issues = []
    if not any(not e["static"] for e in edges.values()):
        issues.append(("CRIT", f"{ns}tf: no dynamic transforms in {TF_WINDOW:.0f}s (Isaac paused/not playing, or bridge down; "
                               "if Isaac is playing, rerun the report: the sample window may have been too short)"))
    for c, ps in sorted(parents_of.items()):
        if len(ps) > 1:
            issues.append(("ERROR", f"frame '{c}' has {len(ps)} parents ({', '.join(sorted(ps))}): two publishers fight over it"))
    if len(roots) > 1:
        issues.append(("WARN", f"TF tree is split into {len(roots)} trees (roots: {', '.join(roots)})"))
    base = next((b for b in _BASE_FRAMES if any(b == f.split('/')[-1] for f in frames)), None)
    base_full = next((f for f in frames if base and f.split('/')[-1] == base), None)

    def chain(frame):
        out, seen = [frame], {frame}
        while frame in parents_of:
            frame = sorted(parents_of[frame])[0]
            if frame in seen:
                out.append(frame + " (LOOP)")
                break
            seen.add(frame)
            out.append(frame)
        return list(reversed(out))
    chain_str = " -> ".join(chain(base_full)) if base_full else "(no base_link/base_footprint frame found)"
    names = {f.split('/')[-1] for f in frames}
    for need, why in (("odom", "odometry (Isaac) is not broadcasting odom -> base_link"),
                      ("map", "AMCL/SLAM has not published map -> odom yet (seed the initial pose)")):
        if need not in names:
            issues.append(("WARN" if need == "map" else "ERROR", f"missing '{need}' frame: {why}"))
    if clock_sec is not None:
        for (p, c), e in edges.items():
            if not e["static"] and e["last"] and clock_sec - e["last"] > 2.0:
                issues.append(("WARN", f"{p} -> {c} is stale: last stamp {e['last']:.1f}s, /clock {clock_sec:.1f}s"))

    def render(frame, depth, seen):
        lines = []
        for (p, c), e in sorted(edges.items()):
            if p != frame or c in seen:
                continue
            seen.add(c)
            rate = "static" if e["static"] else f"{e['count'] / TF_WINDOW:.1f} Hz"
            lines.append("    " * depth + f"└─ {c}  ({rate})")
            lines += render(c, depth + 1, seen)
        return lines
    tree = []
    for r in roots:
        tree.append(r)
        tree += render(r, 1, {r})
    return {"ns": ns, "edges": edges, "roots": roots, "chain": chain_str, "tree": tree, "issues": issues}


def parse_hz(out):
    m = re.findall(r"average rate:\s*([0-9.]+)", out)
    return float(m[-1]) if m else None


def evaluate_contracts(contracts, topic_rows):
    tmap = {t["topic"]: t for t in topic_rows}
    res = []
    for c in contracts:
        t = tmap.get(c["topic"])
        if not t:
            res.append({**c, "actual": "MISSING", "pubs": 0, "subs": 0, "verdict": "MISSING_TOPIC"})
            continue
        if c.get("expected_type") and t["type"] != c["expected_type"]:
            v = "TYPE_MISMATCH"
        elif t["pubs"] == 0:
            v = "NO_PUBLISHER"
        elif t["subs"] == 0:
            v = "NO_SUBSCRIBER"
        else:
            v = "HEALTHY"
        res.append({**c, "actual": t["type"], "pubs": t["pubs"], "subs": t["subs"], "verdict": v})
    return res


def ros_relevant_procs():
    """[(pid, args, mnt_count, started_at)] for ROS processes, excluding this report's own ros2 calls."""
    keys = ("ros2", "rviz2", "component_container", "rmw_zenohd", "gz sim", "_node", "roshub", "nav2", "/opt/ros/")
    own = ("ros2 topic hz", "ros2 topic echo", "ros2 node list", "ros2 topic list", "diag_bundle", "ros2cli.daemon")
    out = []
    for pid, args in PD.list_processes():
        if any(k in args for k in keys) and not any(o in args for o in own):
            mnt = proc_env_mnt(pid)
            if "/mnt/" in args:
                mnt = (mnt or 0) + 1
            out.append((pid, args if len(args) < 170 else args[:167] + "...", mnt, proc_start(pid)))
    return sorted(out)


def _collapse(lines):
    """Context lines with consecutive repeats (same fingerprint) folded into one."""
    out, prev, n = [], None, 0
    for l in lines + [None]:
        fp = LG.fingerprint(l) if l is not None else None
        if fp is not None and fp == prev:
            n += 1
            continue
        if n:
            out.append(f"    ... repeated {n} more time{'s' if n > 1 else ''}")
        n = 0
        if l is not None:
            out.append(l.rstrip("\n"))
        prev = fp
    return "\n".join(out[-16:])


def collect_logs(project, since=None):
    """Logs of the CURRENT session only: files touched after `since` (start of the running stack)."""
    now = time.time()
    since = since or now - 2 * 3600
    step_logs = [p for p in glob.glob(os.path.join(LOG_DIR, "*.log")) if os.path.getmtime(p) >= since]
    mine = [p for p in step_logs if project and os.path.basename(p).startswith(project + "_")]
    step_logs = mine or step_logs

    def step_label(p):
        b = os.path.basename(p)[:-4]
        return b[len(project) + 1:] if project and b.startswith(project + "_") else b

    ros_logs = glob.glob(os.path.join(ROS_LOG_DIR, "*.log")) + glob.glob(os.path.join(ROS_LOG_DIR, "*", "launch.log"))
    ros_logs = sorted((p for p in ros_logs if os.path.getmtime(p) >= since),
                      key=os.path.getmtime, reverse=True)[:40]

    def ros_label(p):
        b = os.path.basename(p)
        if b == "launch.log":
            return "launch"
        return re.sub(r"_\d+_\d+\.log$", "", b)

    groups = LG.group_files(step_logs, "WARN", step_label)
    node_logs = [p for p in ros_logs if os.path.basename(p) != "launch.log"]
    launch_logs = [p for p in ros_logs if os.path.basename(p) == "launch.log"]
    LG.merge_groups(groups, LG.group_files(node_logs, "WARN", ros_label))
    # launch.log repeats every node line; keep only launch's own lines when the node logs are here
    LG.merge_groups(groups, LG.group_files(launch_logs, "WARN", ros_label, skip=_RELAYED if node_logs else None))
    info = LG.group_files(step_logs, "INFO", step_label)
    noisy = sorted((g for g in info.values() if g["level"] == "INFO"), key=lambda g: -g["count"])[:8]

    crashes = []
    for p in step_logs:
        try:
            with open(p, "r", errors="ignore") as f:
                lines = f.readlines()
        except Exception:
            continue
        idx = [i for i, l in enumerate(lines) if re.search(r"process has died|exited with failure|Traceback|Segmentation fault", l)]
        if idx:
            i = idx[-1]
            crashes.append((step_label(p), _collapse(lines[max(0, i - 25):i + 4])))
    return {"entries": LG.merge_variants(LG.sorted_entries(groups)), "since": since, "noisy": LG.sorted_entries({id(g): g for g in noisy}),
            "step_files": [os.path.basename(p) for p in step_logs],
            "ros_files": [os.path.relpath(p, ROS_LOG_DIR) for p in ros_logs], "crashes": crashes[:5]}


# ----------------------------------------------------------------- report
def generate(project=None, write=True):
    t0 = time.time()
    projects = sync_and_load_projects()
    det = PD.detect(projects)
    best = det.pop("_best")
    focused = get_active_project().get("active_project", "")
    project = project or best or focused
    pdata = projects.get(project, {})
    contracts = pdata.get("contracts") or D.DEFAULT_CONTRACTS

    with ThreadPoolExecutor(max_workers=10) as ex:
        f_env = ex.submit(collect_env)
        f_doc = ex.submit(collect_doctor)
        f_mat = ex.submit(D.get_full_topic_matrix)
        f_nodes = ex.submit(_sh, "ros2 node list --no-daemon", 20)
        f_clock = ex.submit(_sh, "timeout 5s ros2 topic echo /clock --once", 8)
        starts = [proc_start(p) for p in det.get(project, {}).get("pids", [])]
        starts = [t for t in starts if t]
        since = (min(starts) - 120) if starts else None
        f_logs = ex.submit(collect_logs, project, since)
        mat = f_mat.result()
        topic_rows = mat["topics"]
        tf_topics = sorted({t["topic"].rsplit("/tf", 1)[0] for t in topic_rows
                            if t["type"] == "tf2_msgs/msg/TFMessage" and t["topic"].endswith(("/tf", "/tf_static"))})[:4]
        f_tf = {ns: (ex.submit(_sh, f"timeout {TF_WINDOW}s ros2 topic echo {ns}/tf", TF_WINDOW + 4),
                     ex.submit(_sh, f"timeout {TF_WINDOW}s ros2 topic echo {ns}/tf_static "
                                    "--qos-durability transient_local --qos-reliability reliable", TF_WINDOW + 4))
                for ns in tf_topics}
        hz_topics = [c["topic"] for c in contracts if not c["topic"].endswith("tf_static")]
        f_hz = {t: ex.submit(_sh, f"timeout {HZ_WINDOW}s ros2 topic hz {t} --window 20", HZ_WINDOW + 4) for t in hz_topics}
        env, doc = f_env.result(), f_doc.result()
        nodes_raw, _ = f_nodes.result()
        clock_raw, _ = f_clock.result()
        logs = f_logs.result()
        m = re.search(r"sec:\s*(\d+)\s*\n\s*nanosec:\s*(\d+)", clock_raw)
        clock_sec = int(m.group(1)) + int(m.group(2)) / 1e9 if m else None
        tfs = [analyze_tf(ns + "/", f_tf[ns][0].result()[0], f_tf[ns][1].result()[0], clock_sec) for ns in tf_topics]
        hz = {t: parse_hz(f.result()[0]) for t, f in f_hz.items()}

    nodes, dup_nodes = parse_node_list(nodes_raw)
    cres = evaluate_contracts(contracts, topic_rows)
    procs = ros_relevant_procs()
    router_up = any("rmw_zenohd" in p[1] for p in procs) or _port_open(7447)
    tf_pubs = next((t["pubs"] for t in topic_rows if t["topic"] == "/tf"), 0)
    dirty = [p for p in procs if p[2]]

    # ---- findings (what the reader should look at first)
    F = []
    if not router_up:
        F.append(("CRIT", "Zenoh router (rmw_zenohd) is not running / port 7447 closed"))
    if clock_sec is None:
        F.append(("CRIT", "/clock not received in 5 s: Isaac Sim is paused, not playing, or not connected"))
    if env["mnt_paths"]:
        F.append(("WARN", f"{env['mnt_paths']} ROS paths point into /mnt (Windows drive): every ros2 command will be slow"))
    if env["source_seconds"] and env["source_seconds"] > 3:
        F.append(("WARN", f"sourcing ros_env.sh takes {env['source_seconds']:.1f}s (expected < 1 s)"))
    if env["RMW_IMPLEMENTATION"] != "rmw_zenoh_cpp":
        F.append(("ERROR", f"RMW_IMPLEMENTATION is {env['RMW_IMPLEMENTATION']}, not rmw_zenoh_cpp"))
    for line in doc.get("issues", []):
        F.append(("ERROR" if "[FAIL]" in line else "WARN", "bridge_doctor: " + line.splitlines()[0].replace("[FAIL]", "").replace("[WARN]", "").strip()))
    if dirty:
        ex_pid, ex_args = dirty[0][0], dirty[0][1][:90]
        F.append(("ERROR", f"{len(dirty)} running ROS process(es) were started with the OLD /mnt workspace "
                           f"(e.g. pid {ex_pid}: {ex_args}). Restart them from a fresh terminal"))
    for c in cres:
        if c["verdict"] != "HEALTHY":
            sev = "ERROR" if c["verdict"] in ("MISSING_TOPIC", "TYPE_MISMATCH", "NO_PUBLISHER") else "WARN"
            note = ""
            if c["topic"].endswith("cmd_vel") and c["verdict"] == "NO_PUBLISHER":
                sev = "INFO"   # nobody driving right now is normal
            if c["topic"].endswith("tf_static") and c["verdict"] in ("NO_PUBLISHER", "MISSING_TOPIC") and tf_pubs:
                sev, note = "INFO", " (normal when the sim publishes every frame on /tf, as some Isaac Sim scenes do)"
            F.append((sev, f"contract {c['topic']}: {c['verdict']} (pub {c['pubs']} / sub {c['subs']}, type {c['actual']}){note}"))
    for t, v in hz.items():
        if v is None and not t.endswith("cmd_vel"):
            F.append(("WARN", f"{t}: no messages sampled in {HZ_WINDOW} s"))
    for t in topic_rows:
        if t["status"] == "SUB_ONLY_MISSING_PUB" and t["type"].startswith(_SENSOR_TYPES):
            F.append(("WARN", f"{t['topic']} ({t['type'].split('/')[-1]}): {t['subs']} node(s) subscribe but nothing publishes. "
                              "A sensor input the stack expects is missing (Isaac publisher disabled, or a config naming the wrong topic)"))
    for tf in tfs:
        F += tf["issues"]
    if dup_nodes:
        F.append(("WARN", f"{len(dup_nodes)} duplicate node names (two copies running?): {', '.join(dup_nodes[:6])}"))
    n_err = sum(1 for e in logs["entries"] if e["level"] in ("ERROR", "FATAL"))
    if n_err:
        F.append(("ERROR", f"{n_err} distinct ERROR messages in the logs (see Logs)"))
    for name, _ in logs["crashes"]:
        F.append(("ERROR", f"step '{name}' crashed or exited (see Crash context)"))
    order = {"CRIT": 0, "ERROR": 1, "WARN": 2, "INFO": 3}
    F.sort(key=lambda f: order.get(f[0], 9))

    # ---- markdown
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    R = []
    w = R.append
    worst = F[0][0] if F else "OK"
    verdict = {"CRIT": "🔴 BROKEN", "ERROR": "🟠 PROBLEMS", "WARN": "🟡 WARNINGS", "INFO": "🟢 HEALTHY", "OK": "🟢 HEALTHY"}[worst]
    w(f"# ROSHub Diagnostic Report: `{project or 'no project'}`")
    w("")
    w(f"- **Generated:** {stamp} on `{socket.gethostname()}` (took {time.time() - t0:.0f}s)")
    w(f"- **Project:** `{project}` (detected running: `{best or 'none'}`, UI focus: `{focused or 'none'}`)")
    w(f"- **Verdict:** {verdict}  ({sum(f[0] in ('CRIT','ERROR') for f in F)} errors, {sum(f[0]=='WARN' for f in F)} warnings)")
    w(f"- **Sim time (/clock):** {f'{clock_sec:.1f} s' if clock_sec is not None else 'NOT RECEIVED'}")
    w("")
    w("## 1. Findings (read this first)")
    w("")
    if F:
        for sev, txt in F:
            w(f"- **{sev}** {txt}")
    else:
        w("- No problems found.")
    w("")

    w("## 2. Environment")
    w("")
    w(f"| Key | Value |\n|---|---|")
    for k in ("RMW_IMPLEMENTATION", "ROS_DOMAIN_ID", "ROS_DISTRO", "ZENOH_CONFIG_OVERRIDE", "ZENOH_SESSION_CONFIG_URI"):
        if env.get(k):
            w(f"| {k} | `{env[k]}` |")
    w(f"| ros_env.sh source time | {env['source_seconds']:.2f} s |" if env["source_seconds"] is not None else "| ros_env.sh source time | ? |")
    w(f"| /mnt paths in env | {env['mnt_paths']} |")
    w(f"| AMENT_PREFIX_PATH | {len(env['ament'])} entries |")
    w("")
    w("<details><summary>AMENT_PREFIX_PATH</summary>\n\n```\n" + "\n".join(env["ament"]) + "\n```\n</details>")
    w("")

    w("## 3. Router & bridge doctor")
    w("")
    w(f"- rmw_zenohd: {'running' if router_up else '**NOT RUNNING**'}")
    if doc.get("available"):
        w(f"- bridge_doctor: {doc['summary'] or '(no summary line)'}")
        for line in doc["issues"]:
            w("  ```\n  " + line.replace("\n", "\n  ") + "\n  ```")
    else:
        w("- bridge_doctor: not found at `~/.roshub/tools/bridge_doctor.py`")
    w("")

    w("## 4. Project detection & processes")
    w("")
    w("| Project | State | Steps matched |\n|---|---|---|")
    for n, r in sorted(det.items(), key=lambda kv: (kv[1]["state"] == "stopped", kv[0])):
        if r["state"] != "stopped" or n == project:
            steps = ", ".join(f"{s['name']}{'✓' if s['running'] else '✗'}" for s in r["steps"])
            w(f"| `{n}` | {r['state']} | {steps} |")
    w("")
    w(f"<details><summary>{len(procs)} ROS-related processes</summary>\n\n```")
    for pid, a, mnt, started in procs:
        when = time.strftime("%H:%M", time.localtime(started)) if started else "?"
        w(f"{pid:>7}  {when}  {'OLD-ENV ' if mnt else ''}{a}")
    w("```\n</details>")
    w("")

    w(f"## 5. Nodes ({len(nodes)})")
    w("")
    if dup_nodes:
        w("**Duplicates:** " + ", ".join(f"`{n}`" for n in dup_nodes))
        w("")
    byns = {}
    for n in nodes:
        ns = n.rsplit("/", 1)[0] or "/"
        byns.setdefault(ns, []).append(n.rsplit("/", 1)[1])
    for ns, ns_nodes in sorted(byns.items()):
        w(f"- `{ns}` ({len(ns_nodes)}): " + ", ".join(sorted(set(ns_nodes))))
    if not nodes:
        w("- (no nodes listed: " + (nodes_raw[:200] or "empty output") + ")")
    w("")

    w("## 6. Bridge contracts & rates")
    w("")
    w("| Topic | Verdict | Type | Pub/Sub | Rate |\n|---|---|---|---|---|")
    for c in cres:
        r = hz.get(c["topic"])
        rate = "—" if c["topic"].endswith("tf_static") else (f"{r:.1f} Hz" if r is not None else "no msgs")
        w(f"| `{c['topic']}` | {c['verdict']} | `{c['actual']}` | {c['pubs']}/{c['subs']} | {rate} |")
    w("")

    w(f"## 7. TF tree")
    w("")
    if not tfs:
        w("- No `/tf` topic on the graph.")
    for tf in tfs:
        w(f"### `{tf['ns']}tf`  ({len(tf['edges'])} edges, {len(tf['roots'])} root{'s' if len(tf['roots']) != 1 else ''})")
        w("")
        w(f"- **Chain to base:** `{tf['chain']}`")
        for sev, txt in tf["issues"]:
            w(f"- **{sev}** {txt}")
        w("")
        w("```\n" + ("\n".join(tf["tree"]) or "(empty)") + "\n```")
        w("")

    w(f"## 8. Topics ({len(topic_rows)})")
    w("")
    missing = [t for t in topic_rows if t["status"] == "SUB_ONLY_MISSING_PUB"]
    if missing:
        w("**Subscribed but nobody publishes** (often the real bug):")
        w("")
        for t in missing:
            w(f"- `{t['topic']}` `{t['type']}` ({t['subs']} sub)")
        w("")
    byorigin = {}
    for t in topic_rows:
        byorigin.setdefault(t["origin"], []).append(t)
    w("<details><summary>All topics by origin</summary>\n")
    for o, rows in sorted(byorigin.items()):
        w(f"\n**{o}** ({len(rows)})\n\n| Topic | Type | Pub | Sub |\n|---|---|---|---|")
        for t in rows:
            w(f"| `{t['topic']}` | `{t['type']}` | {t['pubs']} | {t['subs']} |")
    w("\n</details>")
    w("")

    w("## 9. Logs (grouped: same message with different timestamps/numbers = one row)")
    w("")
    w(f"Only this session: files written since {time.strftime('%H:%M', time.localtime(logs['since']))} "
      "(start of the running stack). Older runs are left out.")
    w("")
    w(f"Sources: ROSHub step logs {', '.join(f'`{f}`' for f in logs['step_files']) or '(none)'}; "
      f"{len(logs['ros_files'])} files from `~/.ros/log`.")
    w("")
    ents = logs["entries"]
    if not ents:
        w("- No warnings or errors.")
    else:
        w("| Level | Count | First–Last | Source | Message (latest sample) |\n|---|---|---|---|---|")
        for e in ents[:MAX_LOG_ENTRIES]:
            msg = e["sample"].replace("|", "\\|")
            msg = msg if len(msg) < 240 else msg[:237] + "..."
            var = ""
            if e.get("variants"):
                v = e["variants"]
                var = f" **{len(v)} variants:** " + ", ".join(f"`{x}`" for x in v[:8]) + (f" +{len(v) - 8}" if len(v) > 8 else "")
            w(f"| {e['level']} | {e['count']} | {e['first_str']}–{e['last_str']} | `{e['source']}` | `{msg}`{var} |")
        if len(ents) > MAX_LOG_ENTRIES:
            w(f"\n_{len(ents) - MAX_LOG_ENTRIES} more groups omitted._")
    w("")
    if logs["noisy"]:
        w("**Noisiest INFO messages** (spam that can hide real problems):")
        w("")
        for e in logs["noisy"]:
            w(f"- {e['count']}× `{e['source']}`: `{e['sample'][:160]}`")
        w("")
    if logs["crashes"]:
        w("### Crash context")
        for name, ctx in logs["crashes"]:
            w(f"\n**{name}**\n\n```\n{ctx}\n```")
        w("")

    w("## 10. Appendix")
    w("")
    w("<details><summary>bridge_doctor full output</summary>\n\n```\n" + (doc.get("raw", "") or "(not run)") + "\n```\n</details>")
    w("")
    w("<details><summary>/clock sample</summary>\n\n```\n" + clock_raw[:600] + "\n```\n</details>")

    md = "\n".join(R) + "\n"
    path = None
    if write:
        os.makedirs(DIAG_DIR, exist_ok=True)
        path = os.path.join(DIAG_DIR, f"roshub_diag_{project or 'none'}_{time.strftime('%Y%m%d_%H%M%S')}.md")
        with open(path, "w") as f:
            f.write(md)
    return {"path": path, "markdown": md, "verdict": verdict, "findings": F, "project": project}


def _port_open(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", help="project to report on (default: detected running project)")
    ap.add_argument("--print", action="store_true", help="also print the report")
    a = ap.parse_args()
    r = generate(a.project)
    if a.print:
        print(r["markdown"])
    print(f"{r['verdict']}  ->  {r['path']}")
