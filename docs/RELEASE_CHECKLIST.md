# Release checklist (v0.1.0)

## On the dev machine (before leaving)
- [ ] Copy the MVP **without git history**: `rsync -a --exclude .git --exclude '*.bak*' --exclude projects --exclude logs --exclude diag ~/.roshub/ roshub-oss/import/roshub_mvp/`
- [ ] Copy only `tb3_github.yaml` as a reference profile into `import/`
- [ ] Record the demo footage (docs/launch/DEMO_VIDEO.md) while the simulators are installed here
- [ ] Run the benchmark sessions (docs/launch/BENCHMARK.md)

## On the new machine with Claude Code
- [ ] `git init` in `roshub-oss/` (fresh history)
- [ ] `curl -o LICENSE https://www.apache.org/licenses/LICENSE-2.0.txt`
- [ ] Ask Claude Code: *"Follow CLAUDE.md. Start with step 1 of the strip-down list."* and work through the list
- [ ] Private-name grep is empty: the grep in `CLAUDE.local.md`
- [ ] `pytest` green; CI green
- [ ] Fresh-machine test: clone, `./install.sh`, import TB3 from GitHub, Start, move check, deep doctor
- [ ] Screenshots + demo GIF in README; fill benchmark numbers or remove the section

## Publish
- [ ] `gh repo create roshub --public --source . --description "Run, test and debug ROS 2 projects from one page" --push`
- [ ] Topics: `ros2`, `robotics`, `diagnostics`, `gazebo`, `isaac-sim`, `developer-tools`
- [ ] Tag `v0.1.0`, GitHub release with the changelog
- [ ] Enable Discussions and issue templates
- [ ] Post: r/ROS, ROS Discourse ("Projects" category), LinkedIn (docs/launch/POSTS.md)
