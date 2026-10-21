"""Runbook engine with fake checks and processes (no ROS)."""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import runbook as rb  # noqa: E402

rb.POLL_S = 0.02


class FakeProc:
    def __init__(self, rc=None, exits_after=None):
        self.pid = os.getpid()
        self.returncode = rc
        self._exit_at = time.time() + exits_after if exits_after is not None else None

    def poll(self):
        if self._exit_at is not None and time.time() >= self._exit_at:
            if self.returncode is None:
                self.returncode = 0
            return self.returncode
        return None if self._exit_at is None else self.returncode


def make(stages, ready_after=None, procs=None, tmp="/tmp"):
    """ready_after: {check_name: seconds after start when it starts passing}"""
    t0 = time.time()
    ready_after = ready_after or {}
    started = []

    def evaluate(check, stage):
        name = check.get("name")
        ok = name in ready_after and time.time() - t0 >= ready_after[name]
        return {"ok": ok, "summary": "ok" if ok else "not yet"}

    def popen(args, **kw):
        started.append(args[2])
        return (procs or {}).get(args[2], FakeProc())

    r = rb.Runbook("p", stages, {}, tmp, evaluate, popen=popen)
    r._kill = lambda s: None
    return r, started


def wait(r, t=5):
    end = time.time() + t
    while r.state == "running" and time.time() < end:
        time.sleep(0.02)
    return r.state


def test_stages_run_in_order_and_gate_on_checks(tmp_path):
    r, started = make([
        {"id": "a", "run": {"cmd": "A", "bg": True}, "ready": [{"cmd": "x", "name": "a1"}], "timeout": 5},
        {"id": "b", "run": {"cmd": "B", "bg": True}, "ready": [{"cmd": "x", "name": "b1"}], "timeout": 5},
    ], ready_after={"a1": 0.1, "b1": 0.2}, tmp=str(tmp_path))
    r.start()
    assert wait(r) == "done"
    assert started == ["A", "B"]
    assert [s.state for s in r.stages] == ["passed", "passed"]


def test_reuse_skips_starting_when_already_ready(tmp_path):
    r, started = make([{"id": "router", "reuse": True, "run": {"cmd": "R", "bg": True},
                        "ready": [{"port": 7447, "name": "p"}]}], ready_after={"p": 0}, tmp=str(tmp_path))
    r.start()
    assert wait(r) == "done"
    assert started == [] and r.stages[0].state == "reused"


def test_timeout_fails_and_stops_runbook(tmp_path):
    r, started = make([
        {"id": "a", "run": {"cmd": "A", "bg": True}, "ready": [{"cmd": "x", "name": "never"}], "timeout": 0.2},
        {"id": "b", "run": {"cmd": "B"}},
    ], tmp=str(tmp_path))
    r.start()
    assert wait(r) == "failed"
    assert r.stages[0].state == "failed" and "not ready after" in r.stages[0].message
    assert r.stages[1].state == "pending" and started == ["A"]


def test_optional_failure_continues(tmp_path):
    r, _ = make([
        {"id": "a", "ready": [{"cmd": "x", "name": "never"}], "timeout": 0.1, "optional": True},
        {"id": "b", "ready": [{"cmd": "x", "name": "ok"}], "timeout": 2},
    ], ready_after={"ok": 0}, tmp=str(tmp_path))
    r.start()
    assert wait(r) == "done"
    assert [s.state for s in r.stages] == ["failed", "passed"]


def test_background_process_dying_fails_stage(tmp_path):
    r, _ = make([{"id": "a", "run": {"cmd": "A", "bg": True}, "ready": [{"cmd": "x", "name": "never"}], "timeout": 5}],
                procs={"A": FakeProc(rc=1, exits_after=0.05)}, tmp=str(tmp_path))
    r.start()
    assert wait(r) == "failed"
    assert "exited" in r.stages[0].message


def test_foreground_exit_code(tmp_path):
    r, _ = make([{"id": "a", "run": {"cmd": "A"}}], procs={"A": FakeProc(rc=3, exits_after=0)}, tmp=str(tmp_path))
    r.start()
    assert wait(r) == "failed" and "code 3" in r.stages[0].message
    r2, _ = make([{"id": "a", "run": {"cmd": "A"}}], procs={"A": FakeProc(rc=0, exits_after=0)}, tmp=str(tmp_path))
    r2.start()
    assert wait(r2) == "done"


def test_manual_stage_needs_confirmation(tmp_path):
    r, _ = make([{"id": "isaac", "manual": "press Play", "timeout": 5}], tmp=str(tmp_path))
    r.start()
    time.sleep(0.1)
    assert r.state == "running" and r.stages[0].state == "waiting"
    r.confirm("isaac")
    assert wait(r) == "done"


def test_resume_from_stage_keeps_earlier_ones(tmp_path):
    r, started = make([
        {"id": "a", "run": {"cmd": "A", "bg": True}},
        {"id": "b", "run": {"cmd": "B", "bg": True}, "ready": [{"cmd": "x", "name": "late"}], "timeout": 0.1},
    ], tmp=str(tmp_path))
    r.start()
    assert wait(r) == "failed"
    r.stages[1].spec["timeout"] = 5
    r.evaluate = lambda c, s: {"ok": True, "summary": "ok"}
    r.start(from_id="b")
    assert wait(r) == "done"
    assert started == ["A", "B", "B"] and r.stages[0].state == "passed"


def test_stop_ends_waiting_stage(tmp_path):
    r, _ = make([{"id": "a", "ready": [{"cmd": "x", "name": "never"}], "timeout": 60}], tmp=str(tmp_path))
    r.start()
    time.sleep(0.1)
    r.stop()
    assert r.state == "stopped"


def test_describe_checks():
    assert rb.describe({"hz": "/clock", "min_hz": 5}) == "/clock ≥ 5 Hz"
    assert rb.describe({"log": "registered", "count": 3}) == "log shows 'registered' ×3"
    assert rb.describe({"nodes": "amcl", "min": 3}) == "≥3 nodes matching 'amcl'"
