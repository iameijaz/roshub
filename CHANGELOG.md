# Changelog

All notable changes to **ROSHub** are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0-alpha] - 2026-10-21

First public pre-release.

### Added

- Local web dashboard at `localhost:8090` for managing, running and diagnosing ROS 2 projects from one page. Minimal dependencies: Python stdlib + PyYAML.
- Project import from GitHub URLs or local folders, with static package and launch-file discovery, multi-workspace detection (e.g. `humble_ws/` + `jazzy_ws/`), simulator auto-detection and duplicate package-name guards.
- Machine-wide cache of local `colcon` workspaces and runnable launch files for instant indexing.
- Readiness-gated bring-up runbook: step-by-step project startup with clean-environment `colcon` builds, dependency checks and live readiness gates (`/clock`, ports, topics).
- "Can it move?" closed-loop probe: discovers velocity subscribers, negotiates `Twist` vs `TwistStamped`, drives at 0.1 m/s for 1.5 s and verifies motion on `/odom`. It never moves without odometry, always sends zero velocity at the end, and refuses to pick between several robots.
- Project Doctor with Quick (~2 s) and Deep (~5 s) modes, covering build state, environment overlays, RMW/Zenoh routers, orphaned or duplicate simulators, QoS mismatches, `/clock`, `/odom` and TF trees. Rules are documented in `docs/DIAGNOSTIC_RULES.md`.
- One-click diagnostic export: a Markdown report for pasting into GitHub issues or LLM debugging sessions.
- Example profiles (TurtleBot3 + Gazebo Harmonic, end-to-end verified; Isaac Sim attach), unit and smoke test suites, and `tools/ci_smoke.sh`.

### Known limitations

- Verified on Ubuntu 24.04 + ROS 2 Jazzy (WSL2 and native) with Gazebo Harmonic; CI runs on the `ros:jazzy` image. Humble is expected to work but is not tested in CI.
- Isaac Sim attach profile assumes Isaac Sim running on the Windows host with the ROS 2 bridge enabled.
- Alpha: APIs, profile format and dashboard layout may change without notice.

[Unreleased]: https://github.com/iameijaz/roshub/compare/v0.1.0-alpha...HEAD
[0.1.0-alpha]: https://github.com/iameijaz/roshub/releases/tag/v0.1.0-alpha
