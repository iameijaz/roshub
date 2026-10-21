"""Topic contract matrix against a recorded `ros2 topic list -v` (no ROS)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import diagnostics as d  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "topic_list_verbose.txt")


def test_contract_verdicts(monkeypatch):
    seen = []

    def fake(cmd, timeout=8):
        seen.append(cmd)
        with open(FIXTURE) as f:
            return f.read()

    monkeypatch.setattr(d, "get_contracts", lambda: d.DEFAULT_CONTRACTS)
    monkeypatch.setattr(d, "_run", fake)
    v = {c["topic"]: c["verdict"] for c in d.get_full_topic_matrix()["contracts"]}
    assert "--no-daemon" in seen[0] and "LD_LIBRARY_PATH" not in d.ROS_ENV_PREFIX
    assert v["/odom"] == "HEALTHY" and v["/clock"] == "NO_SUBSCRIBER" and v["/cmd_vel"] == "MISSING_TOPIC"
