#!/usr/bin/env python3
import os
import re
import glob
import time
import subprocess
from core import sync_and_load_projects, get_active_project

ROS_ENV_PREFIX = "source ~/.roshub/ros_env.sh && "

_KEEP = ("HOME", "USER", "LOGNAME", "TERM", "LANG", "DISPLAY", "WAYLAND_DISPLAY",
         "XDG_RUNTIME_DIR", "PULSE_SERVER", "WSL_DISTRO_NAME", "WSL_INTEROP", "WSLENV")
_ROS_ENV_CACHE = None

def ros_env():
    """ros_env.sh sourced in a clean shell, independent of the terminal that started ROSHub.
    Cached; restart ROSHub after editing ros_env.sh."""
    global _ROS_ENV_CACHE
    if _ROS_ENV_CACHE is None:
        base = {k: os.environ[k] for k in _KEEP if k in os.environ}
        base["PATH"] = ("/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:"
                        "/mnt/c/Windows/System32:/mnt/c/Windows/System32/WindowsPowerShell/v1.0")
        try:
            out = subprocess.run(["bash", "--noprofile", "--norc", "-c",
                                  "source ~/.roshub/ros_env.sh >/dev/null 2>&1; env -0"],
                                 env=base, capture_output=True, timeout=30).stdout
            env = dict(i.split("=", 1) for i in out.decode(errors="ignore").split("\0") if "=" in i)
        except Exception:
            env = {}
        _ROS_ENV_CACHE = env or base
    return dict(_ROS_ENV_CACHE)


def _run(cmd, timeout=8):
    try:
        res = subprocess.run(
            ROS_ENV_PREFIX + cmd,
            shell=True,
            executable="/bin/bash",
            capture_output=True,
            text=True,
            timeout=timeout,
            env=ros_env()
        )
        return (res.stdout + "\n" + res.stderr).strip()
    except subprocess.TimeoutExpired:
        return "[TIMEOUT]"
    except Exception as e:
        return f"[ERROR: {e}]"

def classify_topic_origin(topic):
    if topic in ["/clock"] or any(x in topic for x in ["/odom", "/scan", "/camera", "/imu", "/joint_states"]):
        return "Isaac Sim -> ROS 2"
    if topic.endswith("/cmd_vel"):
        return "ROS 2 (Nav2) -> Isaac Sim"
    if topic in ["/tf", "/tf_static"]:
        return "Shared Bridge (Isaac Sim + Nav2)"
    if any(x in topic for x in ["costmap", "plan", "bt_navigator", "amcl", "particle_cloud", "goal_pose", "initialpose", "lifecycle"]):
        return "ROS 2 (Nav2 Stack)"
    return "ROS 2 General"

DEFAULT_CONTRACTS = [
    {"topic": "/clock", "expected_type": "rosgraph_msgs/msg/Clock", "role": "Sim Time Heartbeat (Isaac -> ROS 2)"},
    {"topic": "/tf", "expected_type": "tf2_msgs/msg/TFMessage", "role": "Dynamic Transforms (odom -> base_link)"},
    {"topic": "/tf_static", "expected_type": "tf2_msgs/msg/TFMessage", "role": "Static Robot Links (base_link -> sensors)"},
    {"topic": "/odom", "expected_type": "nav_msgs/msg/Odometry", "role": "Odometry (sim/driver -> Nav2)"},
    {"topic": "/scan", "expected_type": "sensor_msgs/msg/LaserScan", "role": "2D Lidar (sim/driver -> Nav2)"},
    {"topic": "/cmd_vel", "expected_type": "geometry_msgs/msg/Twist", "role": "Velocity Cmd (Nav2 -> Isaac)"},
]

def get_contracts():
    """Per-project `contracts:` list from ~/.roshub/projects/<name>.yaml, else generic single-robot defaults."""
    try:
        proj = sync_and_load_projects().get(get_active_project().get("active_project", ""), {})
        c = proj.get("contracts")
        if isinstance(c, list) and c:
            return c
    except Exception:
        pass
    return DEFAULT_CONTRACTS

def get_matrix_topics():
    return [c["topic"] for c in get_contracts()]

def get_full_topic_matrix():
    """Returns all topics with type, publisher count, subscriber count, origin, and match status."""
    raw = _run("ros2 topic list -v --no-daemon", timeout=20)
    published = {}
    subscribed = {}
    types = {}

    mode = None
    for line in raw.splitlines():
        s = line.strip()
        if s.startswith("Published topics:"):
            mode = "pub"
            continue
        elif s.startswith("Subscribed topics:"):
            mode = "sub"
            continue
        m = re.match(r"^\*\s+([^\s]+)\s+\[([^\]]+)\]\s+(\d+)\s+(publisher|subscriber)", s)
        if m:
            tname, ttype, count, _ = m.group(1), m.group(2), int(m.group(3)), m.group(4)
            types[tname] = ttype
            if mode == "pub":
                published[tname] = count
            elif mode == "sub":
                subscribed[tname] = count

    all_topics = sorted(types.keys())
    matrix = []
    for t in all_topics:
        pubs = published.get(t, 0)
        subs = subscribed.get(t, 0)
        origin = classify_topic_origin(t)
        if pubs > 0 and subs > 0:
            status = "MATCHED"
        elif pubs > 0 and subs == 0:
            status = "PUB_ONLY"
        else:
            status = "SUB_ONLY_MISSING_PUB"
        matrix.append({
            "topic": t,
            "type": types.get(t, "unknown"),
            "pubs": pubs,
            "subs": subs,
            "origin": origin,
            "status": status
        })

    expected_contracts = get_contracts()

    topic_map = {m["topic"]: m for m in matrix}
    contract_results = []
    for c in expected_contracts:
        found = topic_map.get(c["topic"])
        if not found:
            contract_results.append({
                **c,
                "actual_type": "MISSING",
                "pubs": 0,
                "subs": 0,
                "verdict": "MISSING_TOPIC",
                "diagnosis": "Topic not on graph. Check Isaac Sim ROS2 Bridge or Zenoh TCP connection."
            })
        else:
            type_match = (found["type"] == c["expected_type"])
            if not type_match:
                verdict = "TYPE_MISMATCH"
                diag = f"Expected {c['expected_type']}, got {found['type']}"
            elif found["pubs"] == 0:
                verdict = "NO_PUBLISHER"
                diag = "ROS 2 is subscribed, but Isaac Sim / Publisher is not sending data!"
            elif found["subs"] == 0:
                verdict = "NO_SUBSCRIBER"
                diag = "Publisher active, but no consumer node is listening."
            else:
                verdict = "HEALTHY"
                diag = f"Matched ({found['pubs']} pub / {found['subs']} sub)"
            contract_results.append({
                **c,
                "actual_type": found["type"],
                "pubs": found["pubs"],
                "subs": found["subs"],
                "verdict": verdict,
                "diagnosis": diag
            })

    return {"topics": matrix, "contracts": contract_results, "core_topics": get_matrix_topics()}

def measure_topic_hz(topic):
    out = _run(f"timeout 3.5s ros2 topic hz {topic}", timeout=5)
    m = re.search(r"average rate:\s*([0-9.]+)", out)
    if m:
        return {"topic": topic, "hz": f"{float(m.group(1)):.1f} Hz", "raw": out}
    return {"topic": topic, "hz": "0.0 Hz (No msgs)", "raw": out}

def analyze_tf_tree():
    """Samples /tf and /tf_static and checks if map -> odom -> base_link exist."""
    tf_raw = _run("timeout 6s ros2 topic echo /tf --once", timeout=8)
    tfs_raw = _run("timeout 6s ros2 topic echo /tf_static --once", timeout=8)

    def extract_edges(text):
        parents = re.findall(r"frame_id:\s*([^\s\n]+)", text)
        children = re.findall(r"child_frame_id:\s*([^\s\n]+)", text)
        edges = []
        for p, c in zip(parents, children):
            p_clean = p.strip("'\"")
            c_clean = c.strip("'\"")
            edges.append(f"{p_clean} -> {c_clean}")
        return sorted(list(set(edges)))

    dyn_edges = extract_edges(tf_raw)
    stat_edges = extract_edges(tfs_raw)
    all_edges = dyn_edges + stat_edges
    all_text = " ".join(all_edges)

    issues = []
    if not dyn_edges:
        issues.append("CRITICAL: No dynamic transforms received on /tf! Isaac Sim is either paused, or its ROS 2 Bridge is not connected to WSL 2 Zenoh on port 7447.")
    if "odom" not in all_text:
        issues.append("MISSING 'odom' FRAME: Neither Isaac Sim nor odometry publishers are broadcasting the 'odom -> base_link' transform on /tf. This causes local_costmap 'Invalid frame ID odom' errors.")
    if "map" not in all_text:
        issues.append("MISSING 'map' FRAME: AMCL has not published 'map -> odom' yet. Set the initial pose (RViz '2D Pose Estimate' or /initialpose) once /tf and /scan are active.")

    if not issues:
        issues.append("ALL CORE FRAMES DETECTED: 'map', 'odom', and 'base_link' transforms are active.")

    return {
        "dynamic_edges": dyn_edges,
        "static_edges": stat_edges,
        "issues": issues,
        "raw_tf_sample": tf_raw[:1500]
    }

def get_topics_detailed():
    return get_full_topic_matrix()["topics"]

def inspect_topic(topic, mode="info"):
    if mode == "info":
        return _run(f"ros2 topic info -v {topic} --no-daemon", timeout=15)
    elif mode == "hz":
        return _run(f"timeout 4s ros2 topic hz {topic}", timeout=6)
    elif mode == "echo":
        return _run(f"timeout 4s ros2 topic echo {topic} --once", timeout=6)
    return "Unknown mode"

def get_nodes_and_tf():
    nodes = _run("ros2 node list --no-daemon", timeout=15)
    clock = _run("timeout 5s ros2 topic echo /clock --once", timeout=7)
    tf_info = analyze_tf_tree()
    return {
        "nodes": nodes,
        "clock": clock,
        "tf_sample": "\n".join(tf_info["issues"]) + "\n\nDynamic Edges:\n" + "\n".join(tf_info["dynamic_edges"])
    }

def audit_nav2_pipeline():
    mat = get_full_topic_matrix()
    tf_info = analyze_tf_tree()
    lines = ["=== 1. ISAAC SIM <-> ROS 2 CONTRACT MATCH ==="]
    for c in mat["contracts"]:
        lines.append(f"[{c['verdict']}] {c['topic']} ({c['actual_type']}) -> {c['diagnosis']}")
    lines.append("\n=== 2. TF TREE DIAGNOSTICS ===")
    for i in tf_info["issues"]:
        lines.append(f"- {i}")
    if tf_info["dynamic_edges"]:
        lines.append("Active /tf edges: " + ", ".join(tf_info["dynamic_edges"]))
    if tf_info["static_edges"]:
        lines.append("Active /tf_static edges: " + ", ".join(tf_info["static_edges"]))
    return "\n".join(lines)
