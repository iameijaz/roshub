<div align="center">

<img src="docs/brand/roshub-logo-128.png" alt="ROSHub Logo" width="96" />

# ROSHub

**Run, test, and debug your ROS 2 projects from one local page.**

[![CI](https://github.com/iameijaz/roshub/actions/workflows/ci.yml/badge.svg)](https://github.com/iameijaz/roshub/actions/workflows/ci.yml)
[![Status: Alpha v0.1](https://img.shields.io/badge/status-alpha_v0.1-orange)](CHANGELOG.md)
[![ROS 2: Jazzy | Humble](https://img.shields.io/badge/ROS_2-Jazzy_%7C_Humble-22314E?logo=ros&logoColor=white)](https://github.com/iameijaz/roshub)
[![Python: 3.10+ (stdlib + PyYAML)](https://img.shields.io/badge/python-3.10%2B_(stdlib)-3776AB?logo=python&logoColor=white)](https://github.com/iameijaz/roshub)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

[**Quick Start**](#-quick-start) · [**What It Does**](#-what-it-does) · [**Diagnostic Rules**](docs/DIAGNOSTIC_RULES.md) · [**Trial Log**](docs/TRIAL_LOG.md) · [**Why It Exists**](#-why-roshub-exists-by-the-numbers) · [**Contributing**](CONTRIBUTING.md)

</div>

---

## Why ROS 2 Needs a Pre-Flight Layer

**ROS 2 fails quietly.**
* A topic is listed in `ros2 topic list`, but zero data arrives (`BEST_EFFORT` vs `RELIABLE` QoS mismatch).
* A workspace was never built, or an older overlay is shadowing your package.
* A second simulator from an hour ago is still running in the background and publishing a competing `/clock`.
* The robot ignores `cmd_vel` because Jazzy's controller expects `TwistStamped` while your teleop node publishes `Twist`.

**ROSHub** brings a project up step by step with readiness gates, checks each layer in seconds, runs a closed-loop motion probe to verify the robot actually moves, and tells you **which layer is wrong and the exact command to fix it**—all from a zero-bloat local UI (`http://localhost:8090`).

> **Status: Alpha (`v0.1`).** Tested end-to-end on **Ubuntu 24.04 + ROS 2 Jazzy** (native and WSL2) with **Gazebo Harmonic**. **NVIDIA Isaac Sim** works in attach mode. Webots and MuJoCo profiles are included in [`examples/`](examples/) as starting points. See [`docs/TRIAL_LOG.md`](docs/TRIAL_LOG.md) for verification details.

---

## 🚀 Quick Start

Zero heavy web frameworks—just Python 3.10+ `stdlib` and `PyYAML`.

```bash
git clone https://github.com/iameijaz/roshub.git && cd roshub
./install.sh                 # puts `roshub` on your PATH
source /opt/ros/jazzy/setup.bash
roshub                       # opens http://localhost:8090
```

### Try It in 60 Seconds (TurtleBot3 + Gazebo)
1. Click **`+ New project`** → choose **GitHub URL**.
2. Paste:
   ```text
   https://github.com/ROBOTIS-GIT/turtlebot3_simulations.git
   ```
3. Press **Fetch** → **Create** → **Start**.

> 🔒 **Safe by Design:** ROSHub **never** runs code from a repo at import time and **never** installs packages behind your back. Missing dependencies are detected and listed with the exact `rosdep` command for you to run.

---

## 🛠️ What It Does

| Feature | How It Works |
| :--- | :--- |
| **🗂️ Projects Library** | Search, bookmark, archive, or delete (to trash) your ROS 2 projects. Filter by robot, simulator, or middleware (`rmw_zenoh_cpp`, CycloneDDS, FastDDS). |
| **📥 Add from GitHub or Folder** | Paste a repo URL or pick a local folder (`hub_import.py`). ROSHub clones it, discovers packages and launch files, guesses the simulator, and pre-fills the project. Recognizes multi-workspace repos (e.g. `humble_ws/` + `jazzy_ws/`) and refuses duplicate package names *before* the build fails. |
| **📦 Workspaces Cache** | Indexes every `colcon` workspace on your machine (`hub_wscache.py`), cached for instant loading. Open any workspace to see the projects that use it and every launch file you can run, with a 1-click **Create project** button next to each. |
| **🚦 Bring-Up Runbook** | **Start** runs ordered steps with live readiness gates (`runbook.py`: `/clock` ticking, port open, topic publishing…). Checks dependencies and builds the workspace (only when needed, in a clean environment) before launching. A step cannot pass while its workspace is still building. |
| **🛞 "Can It Move?" Probe** | Closed-loop motion verification (`motion_probe.py`). Finds the velocity topic that actually has an active subscriber, sends the message type it expects (`Twist` or `TwistStamped`), drives `0.1 m/s` for `1.5 s`, and verifies displacement against `/odom`. Never moves without odometry and never guesses between multiple robots. |
| **🩺 Project Doctor (2s / 5s)** | **Quick (~2 s)** and **Deep (~5 s)** diagnostic sweeps (`project_doctor.py` & `diagnostics.py`): checks environment, workspace build state, middleware & Zenoh router, simulator process, orphaned/duplicate simulators, other stacks still running, QoS mismatches, velocity input, `/clock`, `/odom`, and the TF tree. See [`docs/DIAGNOSTIC_RULES.md`](docs/DIAGNOSTIC_RULES.md). |
| **📋 1-Click Diagnostic Report** | Exports the full diagnosis and system snapshot (`diag_bundle.py` / `sysreport.py`) as clean Markdown—ready to paste straight into an LLM chat or a GitHub issue. |

---

## 📊 Why ROSHub Exists (By the Numbers)

Empirical studies of ROS and ROS 2 repositories and Q&A forums show that bring-up, topology, and configuration mismatches consume a massive share of engineering time:

* **33.9% of ROS component interaction bugs** are caused by interaction topology errors (topic naming, namespaces, pub/sub establishment: **19.0%**), parameter mishandling (**5.8%**), and node/executor registration (**9.1%**) (*Chen et al., 2025, n=121 bugs across 10 ROS repositories*).
* **54% of ROS Answers misconfiguration categories** (27 of 50 fine-grained categories) have **zero coverage** from existing static or runtime analysis tools (*Canelas et al., ACM ISSTA 2024, n=228 sampled from 67,189 posts*).
* **154 frame inconsistencies** and 45 convention violations were uncovered across **180 public ROS repositories** in a single automated TF check (*Kate et al., ACM ESEC/FSE 2021*).
* **Up to 72% faster debugging:** Providing ROS developers with structured diagnostic assistance increased debugging task success from **17.9% to 92.9%** and reduced debugging time by up to **72%** (*Katuwandeniya et al., IEEE RA-L 2026*).
* **70.7% of ROS forum questions go unanswered:** Out of 1,540 questions on Robotics Stack Exchange in 2025 (94.1% tagged `ros2`), only **29.3%** received an answer (*Open Robotics 2025 ROS Metrics Report*).

---

## 🧭 How It Compares

`rqt`, `RViz`, `Foxglove`, and `ros2 doctor` are great at visualizing data once your stack is running. 

**ROSHub focuses on the step before:** *is the project actually up, can the robot move, and if not, which layer is broken?* Use them together.

| Capability | `ros2 doctor` | `rqt` / `Foxglove` | **ROSHub** |
| :--- | :---: | :---: | :---: |
| Ordered bring-up runbook with readiness gates | ❌ | ❌ | ✅ |
| Clean-env `colcon` build & duplicate package guard | ❌ | ❌ | ✅ |
| Orphaned simulator & competing `/clock` detection | ❌ | ❌ | ✅ |
| Silent QoS + `Twist` vs `TwistStamped` diagnosis | Partial | Manual | ✅ |
| Closed-loop `0.1 m/s` odometry-verified motion probe | ❌ | ❌ | ✅ |
| 1-click Markdown diagnostic report for LLMs / GitHub | ❌ | ❌ | ✅ |

---

## 💻 Requirements

* **OS & ROS:** Ubuntu 22.04 / 24.04 with **ROS 2 Humble or Jazzy** (Jazzy is the primary target), native or WSL2
* **Python:** Python 3.10+ (uses Python `stdlib` HTTP server + `PyYAML`)
* **Optional:** `rmw_zenoh_cpp` (default middleware for new projects), Gazebo Harmonic, NVIDIA Isaac Sim, Webots, MuJoCo

---

## 📂 Examples & Architecture Docs

* **[`examples/`](examples/)**: Pre-configured project profiles including verified **TurtleBot3 + Gazebo** (`turtlebot3_gazebo.yaml`, `tb3_github.from_mvp.yaml`), **NVIDIA Isaac Sim Attach Mode** (`isaac_attach.yaml`), **MuJoCo Arm** (`mujoco_arm.yaml`), and **Webots UR5e** (`webots_ur5e.yaml`).
* **[`docs/DIAGNOSTIC_RULES.md`](docs/DIAGNOSTIC_RULES.md)**: Full reference of quick (~2 s) and deep (~5 s) diagnostic checks.
* **[`docs/TRIAL_LOG.md`](docs/TRIAL_LOG.md)**: End-to-end bring-up trial logs and test notes.
---

This project is still in Alpha Phase, and a support for Docker Containers, and Pixi is planned for future releases.

---

## 🤝 Contributing

* **Bug reports** with a 1-click **ROSHub diagnostic report** attached are the highest-signal contribution you can send.
* **New Doctor Rules** are very welcome—use the [**Doctor Rule Issue Template**](.github/ISSUE_TEMPLATE/doctor_rule.md) or see [`CONTRIBUTING.md`](CONTRIBUTING.md).

## 📄 License

[MIT](LICENSE) © [Ijaz Ahmed](https://github.com/iameijaz) — use it however you like.
