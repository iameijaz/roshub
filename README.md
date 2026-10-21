<p align="center"><img src="docs/brand/roshub-logo.svg" width="140" alt="ROSHub logo"></p>

# ROSHub

**Run, test and debug your ROS 2 projects from one page.**

ROS 2 fails quietly. A topic is listed but no data arrives. A workspace was never built. A second simulator is still running and publishing `/clock`. The robot ignores `cmd_vel` because it expects `TwistStamped`. ROSHub brings a project up step by step, checks each step, and tells you *which layer* is wrong and what to do about it.

> **Status: alpha (v0.1).** Tested on Ubuntu 24.04 + ROS 2 Jazzy, native and WSL2, with Gazebo Harmonic. Isaac Sim works in attach mode. Webots and MuJoCo examples are untested.

<!-- TODO: demo GIF here (docs/launch/DEMO_VIDEO.md) -->

## What it does

| | |
|---|---|
| **Projects** | A library of your ROS 2 projects: search, filter by robot, simulator or middleware, bookmark, archive, delete (to trash). |
| **Add from GitHub or a folder** | Paste a repo URL or pick a folder. ROSHub clones it, finds the packages and launch files, guesses the simulator, and pre-fills the project. It recognises repos holding several workspaces (e.g. `humble_ws/` + `jazzy_ws/`) and refuses duplicate package names before the build does. |
| **Workspaces** | Every colcon workspace on the machine, cached for instant loading. Open one to see the projects that use it and every launch file you can run, with **Create project** next to each. |
| **Bring-up runbook** | Start runs ordered steps with readiness gates (`/clock` ticking, port open, topic publishing…). Dependencies are checked and the workspace is built (only when needed, in a clean environment) before launching. A step can't pass while its workspace is still building. |
| **Can it move?** | Finds the velocity topic that really has a subscriber, sends the message type it expects (`Twist` or `TwistStamped`), drives 0.1 m/s for 1.5 s and checks odometry. It never moves without odometry and never guesses between several robots. |
| **Project doctor** | Quick (~2 s) and deep (~5 s) checks for the selected project: environment, workspace build, middleware and Zenoh router, simulator process, orphaned or duplicate simulators, other stacks still running, QoS mismatches, velocity input, `/clock`, `/odom`, TF tree. |
| **Report** | One click exports the diagnosis as Markdown for an LLM chat or a GitHub issue. |

## Quick start

```bash
git clone https://github.com/<you>/roshub && cd roshub
./install.sh                 # puts `roshub` on your PATH
source /opt/ros/jazzy/setup.bash
roshub                       # opens http://localhost:8090
```

Then **+ New project**, choose **GitHub URL**, paste for example
`https://github.com/ROBOTIS-GIT/turtlebot3_simulations/tree/jazzy`, press **Fetch**, then **Create**, then **Start**.

ROSHub never runs code from a repo at import time and never installs anything without you: missing dependencies are listed with the exact `rosdep` command to run.

## Requirements

- Ubuntu 22.04/24.04 with ROS 2 Humble or Jazzy (Jazzy is the main target)
- Python 3.10+ (stdlib server; PyYAML)
- Optional: `rmw_zenoh_cpp` (the default middleware for new projects), Gazebo, Isaac Sim, Webots

## Examples

See [`examples/`](examples/). The TurtleBot3 + Gazebo example is verified end to end; the others are starting points.

## How it compares

rqt, RViz, Foxglove and `ros2 doctor` are great at showing data. ROSHub focuses on the step before: is the project actually up, and if not, which layer is broken. Use them together.

## Contributing

Bug reports with a ROSHub report attached are the most useful thing you can send. New doctor rules are very welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[Apache-2.0](LICENSE)
