# ROSHub Example Profiles

These YAML profiles demonstrate how ROSHub configures ordered bring-up steps, readiness gates, simulator attachments, and diagnostic expectations across different robot stacks.

| File | Stack / Simulator | Status | Notes |
| :--- | :--- | :--- | :--- |
| **[`turtlebot3_gazebo.yaml`](turtlebot3_gazebo.yaml)** | TurtleBot3 + Gazebo Harmonic | ✅ **Verified E2E** | Tested on Ubuntu 24.04 (native & WSL2) + ROS 2 Jazzy. Full bring-up, `/clock`, TF, and closed-loop motion probe verified. |
| **[`tb3_github.from_mvp.yaml`](tb3_github.from_mvp.yaml)** | TurtleBot3 (GitHub Import) | ✅ **Verified E2E** | Reference profile generated via ROSHub's GitHub URL import flow (`ROBOTIS-GIT/turtlebot3_simulations`). |
| **[`isaac_attach.yaml`](isaac_attach.yaml)** | NVIDIA Isaac Sim (Attach Mode) | 🟢 **Working** | Attaches ROSHub diagnostics and readiness gates to a running Isaac Sim instance publishing `/clock` and ROS 2 bridge topics. |
| **[`webots_ur5e.yaml`](webots_ur5e.yaml)** | Universal Robots UR5e + Webots | 🧪 **Starting Point** | Template profile for Webots manipulator bring-up (untested in v0.1—PRs welcome!). |
| **[`mujoco_arm.yaml`](mujoco_arm.yaml)** | Manipulator Arm + MuJoCo | 🧪 **Starting Point** | Template profile for MuJoCo ROS 2 integration (untested in v0.1—PRs welcome!). |

## How to Use an Example

1. Start ROSHub (`roshub` → `http://localhost:8090`).
2. Import or copy any `.yaml` profile into your ROSHub projects directory, or use **Add from GitHub** in the UI to generate one automatically.
3. If you verify or improve the `webots_ur5e.yaml` or `mujoco_arm.yaml` profiles on your setup, please open a PR with your updated YAML and a ROSHub diagnostic report!
