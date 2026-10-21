# Contributing to ROSHub

Thanks for helping make ROS 2 bring-up and debugging less painful!

ROSHub is designed to stay **fast, deterministic and lightweight**: Python 3.10+ standard library plus `PyYAML`, nothing else at runtime. This guide covers how to contribute effectively.

## 1. Getting set up

```bash
git clone https://github.com/iameijaz/roshub.git
cd roshub
./install.sh                     # checks your setup (lists any missing packages) and puts `roshub` on your PATH
source /opt/ros/jazzy/setup.bash # or humble
roshub                           # opens the dashboard at http://localhost:8090

```

If you are editing the code and want to run the server directly from the checkout without installing to your `PATH`:

```bash
python3 -m pip install pyyaml
python3 roshub/ui_server.py      # opens http://localhost:8090

```

You'll need a sourced ROS 2 installation (Humble or Jazzy) for anything beyond the unit tests.

## 2. Reporting bugs: include a ROSHub report

The fastest way to get a bug fixed is to click **Report** in the ROSHub dashboard (`http://localhost:8090`) and paste the generated Markdown diagnostic bundle into a LLM. That gives us the exact environment, RMW, workspace state and failing gate without back-and-forth guessing.

If the dashboard won't start at all, include your OS, ROS 2 distro, Python version and the full terminal output instead.

## 3. Proposing or adding a Project Doctor rule

Every ROS 2 developer has lost hours to a silent failure mode. If you hit one that ROSHub didn't catch, you have two options.

**Option A: open a Github issue** describing:

* **Symptom:** what silently failed (e.g. node running, topic listed, robot doesn't move).
* **Root cause:** which layer broke (build, overlay, RMW/Zenoh, QoS, `/clock`, TF, controller, orphaned process).
* **Deterministic detection:** which command, topic info or process check proves it.
* **Remediation:** the exact command or change that fixes it.

**Option B: implement it yourself** in `roshub/diagnostics.py` or `roshub/tools/project_doctor.py`, add a test for it, and document the rule ID in [`docs/DIAGNOSTIC_RULES.md`](docs/DIAGNOSTIC_RULES.md).

## 4. Running the tests

Before opening a pull request, run the unit tests and the CI smoke script from the repository root:

```bash
# Unit tests (diagnostics, import, profile schema, runbook)
python3 -m pip install pytest pyyaml
python3 -m pytest -q roshub/tests

# CI smoke test
bash tools/ci_smoke.sh

```

## 5. Design guardrails for pull requests

To keep ROSHub trustworthy and lightweight:

* **No heavy runtime dependencies.** Stick to the Python 3.10+ standard library and `PyYAML`. Test-only or dev-only tools are fine if they stay out of the runtime path.
* **Never execute untrusted repo code on import.** `hub_import.py` and `project_detect.py` must only parse files statically (`package.xml`, launch files, directory layout).
* **Never install packages silently.** Always show the user the exact `rosdep` or `apt` command to run.
* **Never move a robot blindly.** Changes to `motion_probe.py` must keep the odometry requirement and the multi-robot ambiguity guards.

## 6. Pull request checklist

* [ ] Unit tests and `tools/ci_smoke.sh` pass locally.
* [ ] New behaviour has a test in `roshub/tests/`.
* [ ] User-visible changes are added under `[Unreleased]` in [`CHANGELOG.md`](CHANGELOG.md).
* [ ] New or changed Doctor rules are documented in [`docs/DIAGNOSTIC_RULES.md`](docs/DIAGNOSTIC_RULES.md).
* [ ] No new runtime dependencies.

## License

By contributing, you agree that your contributions are licensed under the same license as the project (see [`LICENSE`](LICENSE)).
