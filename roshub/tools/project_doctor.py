#!/usr/bin/env python3
"""ROSHub project doctor: checks driven by the selected project's YAML.

  project_doctor.py --project tb3_github [--quick] [--sample 3]

Brief (~2 s): environment, middleware/router, workspace build, simulator process,
other ROS stacks still running. Deep (+~4 s): /clock, use_sim_time, QoS mismatches,
velocity topics, TF roots, odometry, move-check status.
Output format matches bridge_doctor so the Diagnostics tab shows it unchanged.
"""
import argparse
import glob
import os
import socket
import sys
import time
from datetime import datetime

import yaml

H = os.path.expanduser("~/.roshub")
RES = []


def R(group, title, status, detail="", fix=""):
    RES.append((group, title, status, str(detail), fix))


# ------------------------------------------------------------------ helpers
def load_project(name):
    for path in sorted(glob.glob(os.path.join(H, "projects", "*.yaml"))) + [os.path.expanduser("~/.roshub.yaml")]:
        try:
            with open(path) as f:
                data = yaml.safe_load(f) or {}
        except (OSError, yaml.YAMLError):
            continue
        for pool in (data, data.get("projects") or {}):
            if isinstance(pool, dict) and isinstance(pool.get(name), dict):
                return pool[name], path
    return None, None


def procs():
    """[(pid, argv)] from /proc - no ps dependency."""
    out = []
    for d in glob.glob("/proc/[0-9]*"):
        try:
            with open(d + "/cmdline", "rb") as f:
                argv = [a.decode(errors="ignore") for a in f.read().split(b"\0") if a]
        except OSError:
            continue
        if argv:
            out.append((int(d[6:]), argv))
    return out


def uptime_s(pid):
    try:
        with open(f"/proc/{pid}/stat") as f:
            start = int(f.read().rsplit(")", 1)[1].split()[19])
        with open("/proc/uptime") as f:
            up = float(f.read().split()[0])
        return up - start / os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError, IndexError):
        return 0


def ppid(pid):
    try:
        with open(f"/proc/{pid}/stat") as f:
            return int(f.read().rsplit(")", 1)[1].split()[1])
    except (OSError, ValueError, IndexError):
        return 0


def listening(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def newest(path, depth=5):
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


def project_cmds(p):
    cmds = [s.get("cmd", "") for s in p.get("steps") or []]
    cmds += [((st.get("run") or {}).get("cmd", "")) for st in p.get("runbook") or []]
    return [c for c in cmds if c]


# ------------------------------------------------------------------ brief
def brief(name, p):
    meta = p.get("meta") or {}
    env = p.get("env") or {}
    sim = meta.get("simulator") or "none"
    want_rmw = env.get("RMW_IMPLEMENTATION") or meta.get("rmw") or os.environ.get("RMW_IMPLEMENTATION") or "rmw_fastrtps_cpp"
    g = "Environment"
    distro = os.environ.get("ROS_DISTRO")
    R(g, "ROS sourced", "PASS" if distro else "FAIL", distro or "ROS_DISTRO not set", "" if distro else "source /opt/ros/<distro>/setup.bash")
    paths = os.environ.get("AMENT_PREFIX_PATH", "").split(":")
    distros = sorted({x.split("/")[3] for x in paths if x.startswith("/opt/ros/") and len(x.split("/")) > 3})
    R(g, "Single ROS distro", "PASS" if len(distros) <= 1 else "FAIL", ", ".join(distros) or "-",
      "" if len(distros) <= 1 else "open a fresh shell; do not source two distros")
    have_rmw = os.environ.get("RMW_IMPLEMENTATION", "rmw_fastrtps_cpp (default)")
    R(g, "Middleware matches project", "PASS" if have_rmw.startswith(want_rmw) else "WARN",
      f"project {want_rmw}, server {have_rmw}", "" if have_rmw.startswith(want_rmw) else
      "nodes started by ROSHub use the project's env; CLI tools in your shell may not see them")
    for k, v in env.items():
        if k != "RMW_IMPLEMENTATION":
            R(g, f"Project env {k}", "PASS", v)

    ws = meta.get("workspace")
    g = "Workspace"
    if ws:
        ws = os.path.expanduser(ws)
        setup = os.path.join(ws, "install", "setup.bash")
        if not os.path.isdir(os.path.join(ws, "src")):
            R(g, "Workspace exists", "FAIL", ws, "check meta.workspace in the project")
        elif not os.path.isfile(setup):
            R(g, "Workspace built", "FAIL", "no install/setup.bash", f"cd {ws} && colcon build --symlink-install")
        else:
            src_t, inst_t = newest(os.path.join(ws, "src")), newest(os.path.join(ws, "install"), 6)
            stale = src_t > inst_t + 2
            R(g, "Workspace built", "WARN" if stale else "PASS",
              "src/ changed after the last build" if stale else setup,
              f"cd {ws} && colcon build --symlink-install" if stale else "")
    else:
        R(g, "Workspace", "SKIP", "project has no meta.workspace (uses installed packages)")

    P = procs()
    if want_rmw == "rmw_zenoh_cpp":
        g = "Zenoh router"
        routers = [pid for pid, a in P if os.path.basename(a[0]) == "rmw_zenohd"]   # the daemon, not the ros2 wrapper
        R(g, "Router running", "PASS" if routers else "FAIL", f"pid {', '.join(map(str, routers))}" if routers else "not running",
          "" if routers else "ros2 run rmw_zenoh_cpp rmw_zenohd")
        if len(routers) > 1:
            R(g, "Single router", "WARN", f"{len(routers)} routers", "kill all but one; two routers split the graph")
        R(g, "Listening on TCP 7447", "PASS" if listening(7447) else "FAIL", "yes" if listening(7447) else "no")

    g = f"Simulator ({sim})"
    sim_bins = {"gazebo": ("gz", "gzserver", "gazebo"), "webots": ("webots-bin", "webots"),
                "mujoco": ("mujoco", "simulate")}
    if sim == "isaac_sim":
        R(g, "Isaac Sim", "SKIP", "use the Isaac bridge doctor for Isaac projects")
    elif sim in sim_bins:
        def is_sim(a):
            toks = " ".join(a).split()             # gz rewrites its title: argv[0] can be "gz sim -r ..."
            if sim == "gazebo" and len(toks) > 1 and os.path.basename(toks[0]) == "gz" and toks[1] == "sim":
                return len(toks) < 3 or toks[2] not in ("server", "gui")   # count the parent, not its children
            b0 = os.path.basename(a[0])
            if b0 in ("bash", "sh", "zsh", "dash", "timeout", "nohup"):
                return False                      # a shell whose command line mentions the sim
            if b0 in sim_bins[sim]:
                return True
            if sim == "gazebo":                   # `ruby .../gz sim ...` wrapper or gz-sim-server
                return any(os.path.basename(x) == "gz" and nxt == "sim" for x, nxt in zip(a, a[1:])) \
                    or b0.startswith("gz-sim") or b0 == "gzserver"
            return False
        hits = [pid for pid, a in P if is_sim(a)]
        if not hits and sim == "gazebo" and any(any("/clock@" in x for x in a) for _, a in P):
            R(g, "Simulator process", "PASS", "running (a /clock bridge is up; process not recognised)")
            hits = None
        orphans = [pid for pid in (hits or []) if ppid(pid) == 1]
        live = [pid for pid in (hits or []) if pid not in orphans]
        if hits is not None: R(g, "Simulator process", "PASS" if live else "FAIL",
          f"pid {live[0]}, up {uptime_s(live[0]) / 60:.0f} min" if live else "not running",
          "" if live else "press Start on the project")
        if len(live) > 1:
            R(g, "Single simulator", "WARN", f"{len(live)} running: pids {', '.join(map(str, live))}",
              "two simulators publish two /clock streams - stop the other project")
        if orphans:
            R(g, "Orphaned simulator", "WARN",
              f"pid {', '.join(map(str, orphans))} (its launch has exited, up {uptime_s(orphans[0]) / 3600:.1f} h)",
              f"left over from an earlier run: kill {' '.join(map(str, orphans))}")
        if sim == "gazebo":
            br = [pid for pid, a in P if any("parameter_bridge" in x or "ros_gz_bridge" in x for x in a)]
            R(g, "ros_gz_bridge", "PASS" if br else "WARN", f"pid {br[0]}" if br else "not running",
              "" if br else "without the bridge, Gazebo topics never reach ROS")
    else:
        R(g, "Simulator", "SKIP", "none declared")

    g = "Other stacks"
    mine = " ".join(project_cmds(p))
    others = []
    for pid, a in P:
        if len(a) > 3 and a[1].endswith("/ros2") and a[2] in ("launch", "run") and a[3] != "rmw_zenoh_cpp":
            if a[3] not in mine:
                others.append((pid, " ".join(a[2:5]), uptime_s(pid)))
    if others:
        lst = "; ".join(f"{pid} {c} ({t / 3600:.1f} h)" for pid, c, t in others[:4])
        R(g, "Only this project running", "WARN", f"{len(others)} other: {lst}",
          "stop the other project: its nodes share the graph (wrong cmd_vel, extra TF, mixed clocks)")
    else:
        R(g, "Only this project running", "PASS", "no other ros2 launch/run")


# ------------------------------------------------------------------ deep
def deep(name, p, sample):
    g = "ROS graph"
    try:
        import rclpy
        from rclpy.node import Node
        from rclpy.qos import QoSReliabilityPolicy as Rel, qos_profile_sensor_data
    except ImportError:
        R(g, "rclpy", "SKIP", "not importable - source ROS first")
        return
    meta = p.get("meta") or {}
    rclpy.init()
    node = Node("roshub_project_doctor")
    try:
        time.sleep(1.0)
        for _ in range(10):
            rclpy.spin_once(node, timeout_sec=0.1)
        topics = node.get_topic_names_and_types()
        names = {t for t, _ in topics}
        R(g, "Topics visible", "PASS" if topics else "FAIL", f"{len(topics)} topics, {len(node.get_node_names())} nodes",
          "" if topics else "nothing discovered: project not started, or RMW / domain differs")

        bad = []
        for t, _ in topics:
            pubs = node.get_publishers_info_by_topic(t)
            subs = node.get_subscriptions_info_by_topic(t)
            for s in subs:
                for pb in pubs:
                    if pb.qos_profile.reliability == Rel.BEST_EFFORT and s.qos_profile.reliability == Rel.RELIABLE:
                        bad.append(f"{t} ({pb.node_name} BE -> {s.node_name} RELIABLE)")
        R(g, "QoS compatible", "WARN" if bad else "PASS", "; ".join(bad[:3]) + (" …" if len(bad) > 3 else "") if bad else "no reliability mismatches",
          "subscriber must use sensor-data (best-effort) QoS" if bad else "")

        vel = [t for t, ty in topics if "cmd_vel" in t and any(x.endswith(("Twist", "TwistStamped")) for x in ty)
               and node.get_subscriptions_info_by_topic(t)
               and not t.endswith(("_nav", "_smoothed", "_teleop_in", "_mux_out", "_unstamped", "_raw"))]
        R(g, "Velocity input", "PASS" if len(vel) == 1 else "WARN" if vel else "FAIL",
          ", ".join(vel) or "no cmd_vel with a subscriber",
          "" if len(vel) == 1 else "several robots/controllers listening - set the topic" if vel else "controller or bridge not running")
        mot = meta.get("motion")
        R(g, "Move check", "PASS" if mot and mot.get("verified") else "WARN",
          f"{mot['type']} on {mot['topic']}" + ("" if mot.get("verified") else " (unconfirmed)") if mot else "never run",
          "" if mot and mot.get("verified") else "open the project page and run 'Can it move?'")

        # live samples
        from rosgraph_msgs.msg import Clock
        from tf2_msgs.msg import TFMessage
        from nav_msgs.msg import Odometry
        n = {"clock": 0, "odom": 0}
        edges = set()
        if "/clock" in names:
            node.create_subscription(Clock, "/clock", lambda m: n.__setitem__("clock", n["clock"] + 1), qos_profile_sensor_data)
        if "/odom" in names:
            node.create_subscription(Odometry, "/odom", lambda m: n.__setitem__("odom", n["odom"] + 1), qos_profile_sensor_data)
        tfcb = lambda m: edges.update((tr.header.frame_id.lstrip("/"), tr.child_frame_id.lstrip("/")) for tr in m.transforms)
        from rclpy.qos import QoSProfile, DurabilityPolicy
        node.create_subscription(TFMessage, "/tf", tfcb, 50)
        node.create_subscription(TFMessage, "/tf_static", tfcb,
                                 QoSProfile(depth=50, durability=DurabilityPolicy.TRANSIENT_LOCAL))
        end = time.time() + sample
        while time.time() < end:
            rclpy.spin_once(node, timeout_sec=0.1)

        g = "Time & TF"
        clock_pubs = [pi.node_name for pi in node.get_publishers_info_by_topic("/clock")] if "/clock" in names else []
        if len(clock_pubs) > 1:
            R(g, "Single /clock source", "FAIL", f"{len(clock_pubs)} publishers: {', '.join(clock_pubs)}",
              "two simulators (or bridges) are running - time jumps break TF and Nav2; stop the other one")
        sim_time = meta.get("simulator", "none") != "none"
        hz = n["clock"] / sample
        if sim_time or "/clock" in names:
            R(g, "/clock ticking", "PASS" if hz >= 1 else "FAIL", f"{hz:.0f} Hz",
              "" if hz >= 1 else "simulation paused, or the clock bridge is missing")
        if "/odom" in names:
            oh = n["odom"] / sample
            R(g, "/odom publishing", "PASS" if oh > 0 else "FAIL", f"{oh:.0f} Hz")
        children = {c for _, c in edges}
        roots = sorted({par for par, _ in edges} - children)
        R(g, "TF tree connected", "PASS" if len(roots) == 1 else "WARN" if roots else "FAIL",
          f"{len(edges)} links, root{'s' if len(roots) != 1 else ''}: {', '.join(roots) or '-'}",
          "" if len(roots) == 1 else "several roots = disconnected trees (missing publisher, or another robot's TF)"
          if roots else "no TF received")
    finally:
        node.destroy_node()
        rclpy.shutdown()


# ------------------------------------------------------------------ report
def report(name, deep_on):
    w = print
    w(f"ROSHub Project Doctor   {name}   {datetime.now():%Y-%m-%d %H:%M:%S}\n")
    last = None
    for g, title, st, detail, fix in RES:
        if g != last:
            w(f"── {g}"); last = g
        w(f"  [{st}] {title:<34} {detail}")
        if fix and st in ("WARN", "FAIL"):
            w(f"         ↳ {fix}")
    c = {k: sum(1 for r in RES if r[2] == k) for k in ("PASS", "WARN", "FAIL", "SKIP")}
    w(f"\n{'ISSUES FOUND' if c['WARN'] + c['FAIL'] else 'ALL GOOD'}   "
      f"{c['PASS']} pass, {c['WARN']} warn, {c['FAIL']} fail, {c['SKIP']} skip")
    first = next((r for r in RES if r[2] == "FAIL"), None) or next((r for r in RES if r[2] == "WARN"), None)
    if first:
        w(f"CONCLUSION: {first[1]} - {first[3]}")
        if first[4]:
            w(f"NEXT STEP:  {first[4]}")
    elif not deep_on:
        w("CONCLUSION: basics look fine. Run the deep check with the project running.")
    return 1 if c["FAIL"] else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--sample", type=float, default=3.0)
    a = ap.parse_args()
    p, src = load_project(a.project)
    if not p:
        print(f"project '{a.project}' not found in ~/.roshub/projects or ~/.roshub.yaml"); return 2
    brief(a.project, p)
    if not a.quick:
        deep(a.project, p, a.sample)
    return report(a.project, not a.quick)


if __name__ == "__main__":
    sys.exit(main())
