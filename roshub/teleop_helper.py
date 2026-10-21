#!/usr/bin/env python3
"""Persistent velocity publisher for the ROSHub test dashboard.

ui_server.py starts this once (with the ROS env) and writes one JSON line per
command to its stdin:
    {"topic": "/cmd_vel", "lin": 0.3, "ang": 0.0, "type": "twist"}
    {"stop": true}
Safety:
  * publishes at 10 Hz only while commands keep arriving
  * if no command arrives for DEADMAN seconds it sends zero velocity 3x, then goes
    silent, so it never fights Nav2 for /cmd_vel when you're not driving
"""
import sys
import json
import time
import threading

import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist, TwistStamped

DEADMAN = 0.5
RATE_HZ = 10.0


class Teleop(Node):
    def __init__(self):
        super().__init__("roshub_teleop")
        self.pubs = {}
        self.lock = threading.Lock()
        self.cmd = None            # (topic, type, lin, ang)
        self.last = 0.0
        self.zero_left = 0
        self.create_timer(1.0 / RATE_HZ, self.tick)

    def pub_for(self, topic, mtype):
        key = (topic, mtype)
        if key not in self.pubs:
            cls = TwistStamped if mtype == "twist_stamped" else Twist
            self.pubs[key] = self.create_publisher(cls, topic, 10)
            self.get_logger().info(f"publishing {cls.__name__} on {topic}")
        return self.pubs[key]

    def send(self, topic, mtype, lin, ang):
        pub = self.pub_for(topic, mtype)
        if mtype == "twist_stamped":
            m = TwistStamped()
            m.header.stamp = self.get_clock().now().to_msg()
            m.twist.linear.x, m.twist.angular.z = float(lin), float(ang)
        else:
            m = Twist()
            m.linear.x, m.angular.z = float(lin), float(ang)
        pub.publish(m)

    def command(self, d):
        with self.lock:
            if d.get("stop"):
                if self.cmd:
                    self.zero_left = 3
                self.last = 0.0
                return
            self.cmd = (d.get("topic", "/cmd_vel"), d.get("type", "twist"),
                        float(d.get("lin", 0.0)), float(d.get("ang", 0.0)))
            self.last = time.time()
            self.zero_left = 3
            self.send(*self.cmd)          # immediate response, timer keeps it alive

    def tick(self):
        with self.lock:
            if not self.cmd:
                return
            topic, mtype, lin, ang = self.cmd
            if time.time() - self.last < DEADMAN:
                self.send(topic, mtype, lin, ang)
            elif self.zero_left > 0:
                self.send(topic, mtype, 0.0, 0.0)
                self.zero_left -= 1
                if self.zero_left == 0:
                    self.cmd = None


def main():
    rclpy.init()
    node = Teleop()

    def reader():
        for line in sys.stdin:
            try:
                node.command(json.loads(line))
            except Exception as e:
                node.get_logger().warn(f"bad command {line.strip()!r}: {e}")
        node.command({"stop": True})       # stdin closed: ui_server went away
        time.sleep(0.5)
        rclpy.shutdown()

    threading.Thread(target=reader, daemon=True).start()
    try:
        rclpy.spin(node)
    except Exception:
        pass


if __name__ == "__main__":
    main()
