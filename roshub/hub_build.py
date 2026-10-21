"""ROSHub: build-before-launch for projects whose workspace has a src/.

Every new project (and migrated old ones) runs tools/ensure_built.sh before its launch:
dependency gate, build only when needed, clean environment.
"""
import os

import yaml


# ------------------------------------------------------- build-before-launch
ENSURE = os.path.expanduser("~/.roshub/tools/ensure_built.sh")


def with_build_step(text):
    """Project YAML text -> same project whose launch first runs ensure_built.sh (deps + build)."""
    data = yaml.safe_load(text) or {}
    changed = False
    for proj in data.values():
        if not isinstance(proj, dict):
            continue
        ws = (proj.get("meta") or {}).get("workspace")
        if not ws or not os.path.isdir(os.path.join(os.path.expanduser(ws), "src")):
            continue                               # installed packages only: nothing to build
        src = f"source {ws}/install/setup.bash && "
        for st in proj.get("runbook") or []:
            run = st.get("run") or {}
            cmd = run.get("cmd", "")
            if cmd.startswith(src) and "ensure_built.sh" not in cmd:
                run["cmd"] = f"bash {ENSURE} {ws} && {cmd}"
                st["timeout"] = max(int(st.get("timeout") or 0), 1800)    # first build can be slow
                changed = True
    return yaml.safe_dump(data, sort_keys=False, default_flow_style=False, allow_unicode=True) if changed else text


def wrap_new(orig):
    """Wrap hub.new_project_yaml so every new project gets the build step."""
    def new_project_yaml(form):
        name, text = orig(form)
        return name, with_build_step(text)
    new_project_yaml.__wrapped__ = orig
    return new_project_yaml


def migrate(path):
    """Add the build step to an existing project file (keeps a .bak)."""
    with open(path) as f:
        text = f.read()
    new = with_build_step(text)
    if new == text:
        return False
    import shutil
    shutil.copy2(path, path + ".bak_build")
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(new)
    os.replace(tmp, path)
    return True


if __name__ == "__main__":                         # python3 hub_build.py migrate <project.yaml>...
    import sys
    if len(sys.argv) > 2 and sys.argv[1] == "migrate":
        for p in sys.argv[2:]:
            print(("migrated " if migrate(p) else "unchanged ") + p)
