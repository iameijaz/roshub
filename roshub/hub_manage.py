"""ROSHub: delete projects safely (to a trash folder, never rm)."""
import os
import shutil
import time

from hub_import import CLONE_ROOT as CLONES

PROJECTS = os.path.expanduser("~/.roshub/projects")


def delete(name, projects, remove_clone=False):
    """Move projects/<name>.yaml to projects/.trash/. Optionally move its clone to ~/roshub_ws/.trash/."""
    path = os.path.join(PROJECTS, f"{name}.yaml")
    stamp = time.strftime("%Y%m%d_%H%M%S")
    trash = os.path.join(PROJECTS, ".trash")
    os.makedirs(trash, exist_ok=True)
    if os.path.isfile(path):
        shutil.move(path, os.path.join(trash, f"{name}.{stamp}.yaml"))
    else:                                   # defined inside the registry file
        import yaml
        reg = os.path.expanduser("~/.roshub.yaml")
        with open(reg) as f:
            data = yaml.safe_load(f) or {}
        pool = data["projects"] if isinstance(data.get("projects"), dict) and name in data["projects"] else data
        if not isinstance(pool, dict) or name not in pool:
            return {"ok": False, "error": f"no project file or registry entry for '{name}'"}
        with open(os.path.join(trash, f"{name}.{stamp}.yaml"), "w") as f:
            yaml.safe_dump({name: pool.pop(name)}, f, sort_keys=False)
        shutil.copy2(reg, reg + f".bak_{stamp}")
        tmp = reg + ".tmp"
        with open(tmp, "w") as f:
            yaml.safe_dump(data, f, sort_keys=False, default_flow_style=False, allow_unicode=True)
        os.replace(tmp, reg)
    out = {"ok": True, "trashed": os.path.join(trash, f"{name}.{stamp}.yaml")}
    if remove_clone:
        ws = os.path.realpath(os.path.expanduser(((projects.get(name) or {}).get("meta") or {}).get("workspace") or ""))
        # only clones ROSHub made itself; never anything else in your home folder
        if ws and os.path.dirname(ws) == os.path.realpath(CLONES) and os.path.isdir(ws):
            used = [n for n, p in projects.items() if n != name and os.path.realpath(os.path.expanduser(
                ((p or {}).get("meta") or {}).get("workspace") or "")) == ws]
            if used:
                out["clone"] = f"kept: also used by {', '.join(used)}"
            else:
                dest = os.path.join(CLONES, ".trash", f"{os.path.basename(ws)}.{stamp}")
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                shutil.move(ws, dest)
                out["clone"] = f"moved to {dest}"
        else:
            out["clone"] = "kept: not a ROSHub clone under ~/roshub_ws"
    return out
