# Contributing to ROSHub

Thanks for helping. ROSHub is small on purpose, so the bar for a change is: does it make bring-up or debugging of a ROS 2 project clearer?

## Reporting a bug

1. Open **Diagnostics**, select the project, run the **deep** check.
2. Press **Report** and attach the Markdown file. It has paths and host names masked, but read it before posting.
3. Use the bug report template. Say what you expected and what happened.

## Proposing a doctor rule

Most valuable contribution. Open an issue with:

- **Symptom** as a user sees it (and the exact error text, if any)
- **Root cause**
- **Detection**: how to check it automatically, without changing the system
- **Fix** the user should apply

Rules live in `roshub/doctor/` and are listed in [docs/DIAGNOSTIC_RULES.md](docs/DIAGNOSTIC_RULES.md). A rule must be read-only, finish in under ~1 s (quick) or ~5 s (deep), and give one clear next step.

## Development setup

```bash
git clone https://github.com/<you>/roshub && cd roshub
python3 -m venv .venv --system-site-packages && source .venv/bin/activate
pip install pyyaml pytest
pytest -q roshub/tests
python3 roshub/ui_server.py --port 8091          # run from the checkout, next to an installed one
tools/ci_smoke.sh                                # what CI runs: fresh install → start → import → create → doctor (throwaway HOME)
```

## Pull requests

- One change per PR; describe the user-visible effect.
- Add or update tests. Doctor rules get a test with a fake graph/process list.
- No new runtime dependencies without discussion (the server is stdlib + PyYAML).
- UI: keep it keyboard-usable, labelled for screen readers, readable in light and dark.
- Never commit personal paths, host names or tokens.

## Safety rules (non-negotiable)

- Nothing from an imported repo runs at import time.
- ROSHub never installs packages or uses sudo by itself.
- Motion tests: low speed, short duration, always send zero at the end, never move without odometry, never pick between several robots.
- Deleting moves things to a trash folder; it never `rm`s user data.

## Code of conduct

Be kind and specific. Assume good intent. Maintainers may close issues that are hostile or off-topic.
