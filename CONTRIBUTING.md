# Contributing to ROSHub

Thanks for helping make ROS 2 bring-up and debugging less painful! 

ROSHub is designed to stay **fast, deterministic, and zero-bloat** (Python 3.10+ standard library + `PyYAML`). Here is how you can contribute effectively.

---

## 1. Reporting Bugs (Include a ROSHub Report!)

The fastest way to get a bug fixed is to click **Report** inside ROSHub (`http://localhost:8090`) and paste the generated Markdown diagnostic bundle into a [**Bug Report Issue**](.github/ISSUE_TEMPLATE/bug_report.md). That gives us the exact environment, RMW, workspace state, and failing gate without back-and-forth guessing.

---

## 2. Proposing or Adding a New `Project Doctor` Rule

Every ROS 2 developer has lost hours to a silent failure mode. If you hit a failure that ROSHub didn't catch:
1. Open a [**New Doctor Rule**](.github/ISSUE_TEMPLATE/doctor_rule.md) issue describing:
   * **Symptom:** What silently failed? (e.g., node running, topic listed, robot doesn't move)
   * **Root cause:** Which layer broke? (build, overlay, RMW/Zenoh, QoS, `/clock`, TF, controller, orphaned process)
   * **How to detect it deterministically:** What command, topic info, or process check proves it?
   * **Remediation:** What exact command or change fixes it?
2. Or implement it directly in `roshub/diagnostics.py` / `roshub/tools/project_doctor.py` and document the rule ID in [`docs/DIAGNOSTIC_RULES.md`](docs/DIAGNOSTIC_RULES.md).

---

## 3. Running the Tests Locally

Before opening a Pull Request, run the unit test suite and CI smoke script:

```bash
# Run unit tests (diagnostics, import, profile schema, runbook)
python3 -m unittest discover -s roshub/tests -p "test_*.py"

# Run the CI smoke test
bash tools/ci_smoke.sh
```

---

## 4. Design Guardrails for PRs

To keep ROSHub trustworthy and lightweight:
* **No heavy runtime dependencies:** Stick to Python 3.10+ `stdlib` and `PyYAML`.
* **Never execute untrusted repo code on import:** `hub_import.py` and `project_detect.py` must only parse files statically (`package.xml`, launch files, directories).
* **Never install packages silently:** Always surface the exact `rosdep` or `apt` command for the user to run.
* **Never move a robot blindly:** Any changes to `motion_probe.py` must preserve the odometry requirement and multi-robot ambiguity guards.
