# Trial log: MVP on the dev machine (2026-10-21)

Setup: Windows 11 + WSL2 Ubuntu 24.04, ROS 2 Jazzy, rmw_zenoh_cpp, Gazebo Harmonic.

## Passed end to end
- **TurtleBot3 + Gazebo** (imported from `ROBOTIS-GIT/turtlebot3_simulations`, branch `jazzy`): import → build → start → move check (0.125 m) → deep doctor 17/17.

## Findings that changed ROSHub
| Finding | Fix |
|---|---|
| TB3 on Jazzy expects `TwistStamped` via `ros_gz_bridge`, not `Twist` | Move check auto-detects the type from the subscriber |
| Probe once fell back to another robot's `cmd_vel_nav` (Nav2 internal) and published blind | Skip Nav2/mux internals, refuse when several candidates, never move without odometry |
| "2 Zenoh routers" warning was the `ros2 run` wrapper + daemon | Count the `rmw_zenohd` daemon only |
| Leftover processes from another project polluted the graph | Doctor "Other stacks" check |
| Orphaned Gazebo server kept publishing `/clock` | Doctor flags orphaned/duplicate simulators and >1 `/clock` publisher |
| Gazebo process title is one string (`gz sim -r …`) | Detector handles rewritten titles, ignores shells and `server`/`gui` children |
| Imported project launched before being built | Dependency gate + build on demand (`ensure_built.sh`) |
| `/clock` from another sim turned a failed build green | Ready checks wait until the build finished |
| `clearpath_robot` is real-robot drivers (no sim); `clearpath_common` is description/config | Importer should warn when no simulation launch is found (todo) |
| `IsaacSim-ros_workspaces` holds `humble_ws` + `jazzy_ws` → duplicate packages | Nested-workspace detection picks the distro's workspace; duplicates refused |
| Cancelled Fetch kept cloning on the server | Re-fetch reuses a complete clone of the same repo |
| Workspace scan took 38 s | Cached scan + Rescan + auto-rescan option |
| Projects saved into `~/.roshub.yaml` could not be deleted | Delete handles registry entries too (with backup) |

## Not yet tested
Webots UR5e, MuJoCo arm, Isaac Sim attach with the cleaned build.
