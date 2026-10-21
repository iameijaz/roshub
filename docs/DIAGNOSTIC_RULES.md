# Doctor rules

> Status: the project doctor in the MVP implements ENV001/002, TIM001/002, COM001/004 (partial), COM005 (router), TF001, BRG001 (velocity input), plus simulator/orphan/other-stack checks. The rest are planned.


P0 = ship in v1. P1 = v1.x. P2 = later. **B** = brief check (fast), **D** = deep check (samples the live system).

## Environment & workspace
| ID | Pri | Lvl | Detects | How |
|---|---|---|---|---|
| ENV001 | P0 | B | No ROS sourced / wrong distro | `ROS_DISTRO` unset; or `/opt/ros/<a>` and `/opt/ros/<b>` both in `AMENT_PREFIX_PATH`/`PYTHONPATH`/`LD_LIBRARY_PATH` |
| ENV002 | P0 | B | Project workspace built but not sourced | `<ws>/install` packages missing from `AMENT_PREFIX_PATH` |
| ENV003 | P0 | B | Package shadowing | Same package name in >1 prefix of `AMENT_PREFIX_PATH` |
| ENV004 | P1 | B | Stale install (no symlink-install) | `src/<pkg>` launch/config/urdf/py files newer than non-symlink copies in `install/` |
| ENV005 | P1 | B | Missing rosdep deps | `rosdep check --from-paths src --ignore-src` |
| ENV006 | P1 | B | venv vs system Python | `VIRTUAL_ENV`/`CONDA_PREFIX` set while `install/*/lib/*/*` shebang is `/usr/bin/python3`; un-ignored `pyvenv.cfg` inside ws |
| ENV007 | P1 | B | Platform context | Detect WSL2 / Docker; record in report |

## Time & parameters
| ID | Pri | Lvl | Detects | How |
|---|---|---|---|---|
| TIM001 | P0 | B | Mixed `use_sim_time` | Batch `get_parameters` on all nodes; both true and false present |
| TIM002 | P0 | D | Sim time without clock | Any `use_sim_time=true` while `/clock` has 0 pubs or 0 Hz |
| PAR001 | P1 | B | YAML silently ignored | Parse `--params-file` from `/proc/<pid>/cmdline`; top-level keys don't match the node FQN or `/**` |

## Communication
| ID | Pri | Lvl | Detects | How |
|---|---|---|---|---|
| COM001 | P0 | B | QoS incompatibility | For each pub×sub pair: reliability BE→REL, durability VOLATILE→TRANSIENT_LOCAL, deadline; suggest override YAML |
| COM002 | P0 | D | Silent topic | Has pub(s) and sub(s), 0 Hz over sample window |
| COM003 | P0 | B | Namespace / remap mismatch | Orphan pub `/ns/x` + orphan sub `/x` (or vice versa), same type |
| COM004 | P0 | B | Lifecycle node inactive | Publisher node exposes lifecycle services and state ≠ active |
| COM005 | P1 | B | RMW / domain hints | `RMW_IMPLEMENTATION`, `ROS_DOMAIN_ID`, discovery-range vars; Zenoh selected but no router on 7447 |
| COM006 | P1 | B | Small UDP buffers | `net.core.rmem_max` below threshold while large-message topics exist |
| COM007 | P2 | D | Stale daemon graph | `ros2 daemon` view ≠ direct rclpy discovery; offer daemon restart |

## TF
| ID | Pri | Lvl | Detects | How |
|---|---|---|---|---|
| TF001 | P0 | D | Disconnected tree | More than one root in the `/tf` + `/tf_static` graph |
| TF002 | P0 | D | Sensor frame not in TF | `header.frame_id` of scan/image/points/imu/odom absent from tree |
| TF003 | P0 | D | Duplicate parent claims | Same child frame published by >1 node or with >1 parent |
| TF004 | P1 | D | Stale/slow transform | Dynamic link age > tolerance or rate < 10 Hz vs sensor stamps |
| TF005 | P1 | B | Frame name typos | Leading `/`, near-matches (Levenshtein) between used and existing frames |

## Bring-up
| ID | Pri | Lvl | Detects | How |
|---|---|---|---|---|
| BRG001 | P0 | B | `cmd_vel` type mismatch | `*cmd_vel*` topics carrying both Twist and TwistStamped, or orphan pub/sub pairs |
| BRG002 | P0 | B | Controllers not active | `controller_manager/list_controllers` state ≠ active; missing claimed interfaces |
| BRG003 | P1 | B | Controller type not installed | Requested type ∉ `list_controller_types` |
| BRG004 | P1 | D | Bridge publishes nothing | `ros_gz_bridge`/Isaac bridge pub present, 0 Hz (and Gazebo topic absent if `gz` is available) |
| BRG005 | P2 | D | Crashed process | Child of a launch exits non-zero / SIGSEGV |

## Out of v1
Executor deadlock detection (needs tracing), launch supervisor with per-node restart, and multi-host network probing.
