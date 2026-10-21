#!/usr/bin/env python3
"""ROSHub motion check: can this robot move?

Finds the velocity topic that actually has a subscriber, publishes the right type
(Twist or TwistStamped with a live stamp) slowly for a short burst, always sends
zero at the end, and measures odometry before/after. Prints one JSON line.

Usage: motion_probe.py [--topic T] [--odom /odom] [--linear 0.1] [--angular 0.0]
                       [--duration 1.5] [--min-move 0.02]
"""
import argparse
import json
import math
import sys
import time

import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data

TWIST = "geometry_msgs/msg/Twist"
STAMPED = "geometry_msgs/msg/TwistStamped"
# Nav2 / mux internals: publishing here bypasses or fights the stack, so never pick them
INTERNAL = ("_nav", "_smoothed", "_teleop_in", "_mux_out", "_unstamped", "_raw")


def yaw_of(q):
    return math.atan2(2 * (q.w * q.z + q.x * q.y), 1 - 2 * (q.y * q.y + q.z * q.z))


def pick_topic(node, wanted=None, wait=3.0):
    """Velocity topics with >=1 subscriber; prefer the one asked for, then the controller's."""
    end, cands = time.time() + wait, []
    while time.time() < end:
        cands, pick_topic.unheard = [], []
        for name, types in node.get_topic_names_and_types():
            vel = [t for t in types if t in (TWIST, STAMPED)]
            if not vel or any(name.endswith(x) for x in INTERNAL):
                continue
            subs = node.get_subscriptions_info_by_topic(name)
            if subs and "cmd_vel" in name:
                cands.append({"topic": name, "type": vel[0], "subscribers": [s.node_name for s in subs]})
            elif not subs:
                pick_topic.unheard.append(name)        # velocity topic nobody listens to
        if cands:
            break
        time.sleep(0.3)
    if wanted:
        hit = [c for c in cands if c["topic"] == wanted]
        return (hit or [None])[0], cands
    # never guess between several robots or controllers: one candidate, or the caller must choose
    return (cands[0] if len(cands) == 1 else None), cands


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic")
    ap.add_argument("--odom", default="/odom")
    ap.add_argument("--linear", type=float, default=0.1)
    ap.add_argument("--angular", type=float, default=0.0)
    ap.add_argument("--duration", type=float, default=1.5)
    ap.add_argument("--min-move", type=float, default=0.02)   # metres or radians
    a = ap.parse_args()
    a.linear = max(-0.2, min(0.2, a.linear))                  # hard safety clamps
    a.angular = max(-0.5, min(0.5, a.angular))
    a.duration = min(a.duration, 3.0)

    rclpy.init()
    node = Node("roshub_motion_probe")
    out = {"ok": False}
    try:
        target, cands = pick_topic(node, a.topic)
        out["candidates"] = cands
        if not target:
            unheard = getattr(pick_topic, "unheard", [])
            out["unheard"] = unheard
            if cands:
                out["error"] = f"{len(cands)} cmd_vel topics have subscribers - choose one with --topic"
            elif unheard:
                out["error"] = (f"velocity topics exist but nothing subscribes: {', '.join(unheard[:4])} - "
                                "the robot's controller/bridge is not running, or it listens under another namespace")
            else:
                out["error"] = ("no velocity topics at all - no robot is spawned or its controller never started "
                                "(the simulator alone has none)")
            return out
        out.update(topic=target["topic"], type=target["type"], subscribers=target["subscribers"])

        from geometry_msgs.msg import Twist, TwistStamped
        from nav_msgs.msg import Odometry
        stamped = target["type"] == STAMPED
        pub = node.create_publisher(TwistStamped if stamped else Twist, target["topic"], 10)

        odom = {"msg": None}
        node.create_subscription(Odometry, a.odom, lambda m: odom.__setitem__("msg", m), qos_profile_sensor_data)

        def snap(timeout=2.0):
            odom["msg"], end = None, time.time() + timeout
            while odom["msg"] is None and time.time() < end:
                rclpy.spin_once(node, timeout_sec=0.1)
            m = odom["msg"]
            if m is None:
                return None
            p = m.pose.pose
            return (p.position.x, p.position.y, yaw_of(p.orientation))

        def send(lin, ang):
            if stamped:
                msg = TwistStamped()
                msg.header.stamp = node.get_clock().now().to_msg()
                msg.header.frame_id = "base_link"
                t = msg.twist
            else:
                msg = t = Twist()
            t.linear.x, t.angular.z = lin, ang
            pub.publish(msg)

        before = snap()
        if before is None:   # never move blind
            out["error"] = f"no messages on {a.odom}; not moving without odometry (set --odom)"
            return out
        try:
            end = time.time() + a.duration
            while time.time() < end:
                send(a.linear, a.angular)
                rclpy.spin_once(node, timeout_sec=0.1)      # ~10 Hz
        finally:
            for _ in range(5):                               # always stop
                send(0.0, 0.0)
                time.sleep(0.05)
        time.sleep(0.4)
        after = snap()

        if before and after:
            dist = math.hypot(after[0] - before[0], after[1] - before[1])
            dyaw = abs(math.atan2(math.sin(after[2] - before[2]), math.cos(after[2] - before[2])))
            out.update(distance_m=round(dist, 3), rotation_rad=round(dyaw, 3),
                       moved=dist >= a.min_move or dyaw >= a.min_move)
            out["ok"] = out["moved"]
            if not out["moved"]:
                out["error"] = (f"published {target['type'].split('/')[-1]} on {target['topic']} "
                                "but odometry did not change - check controller state, "
                                "cmd_vel timeout, or use_sim_time / paused simulation")
        return out
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    res = main()
    print(json.dumps(res))
    sys.exit(0 if res.get("ok") else 1)
