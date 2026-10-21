import time
import json
#!/usr/bin/env python3
import os
import glob
import yaml
import threading

ROSHUB_YAML = os.path.expanduser("~/.roshub.yaml")
PROJECTS_DIR = os.path.expanduser("~/.roshub/projects")
LOG_DIR = os.path.expanduser("~/.roshub_logs")

def _sync_unlocked():
    os.makedirs(PROJECTS_DIR, exist_ok=True)
    os.makedirs(LOG_DIR, exist_ok=True)

    base_data = {"projects": {}}
    read_ok = not os.path.exists(ROSHUB_YAML)
    if os.path.exists(ROSHUB_YAML):
        try:
            with open(ROSHUB_YAML, "r") as f:
                loaded = yaml.safe_load(f)
                if isinstance(loaded, dict) and "projects" in loaded:
                    base_data = loaded
                    read_ok = True
        except Exception:
            pass

    # Merge every modular file from ~/.roshub/projects/*.yaml
    for path in sorted(glob.glob(os.path.join(PROJECTS_DIR, "*.yaml"))):
        try:
            with open(path, "r") as f:
                proj_data = yaml.safe_load(f) or {}
            if isinstance(proj_data, dict):
                base_data["projects"].update(proj_data)
        except Exception as e:
            print(f"[roshub] Warning:跳过 invalid YAML {path}: {e}")

    if read_ok:   # never overwrite a file we failed to read: that would drop its projects
        tmp = ROSHUB_YAML + ".tmp"
        with open(tmp, "w") as f:
            yaml.dump(base_data, f, sort_keys=False, default_flow_style=False)
        os.replace(tmp, ROSHUB_YAML)   # atomic: readers never see a half-written file

    return base_data.get("projects", {})

_SYNC_LOCK = threading.RLock()

def sync_and_load_projects():
    with _SYNC_LOCK:
        return _sync_unlocked()

if __name__ == "__main__":
    projs = sync_and_load_projects()
    print(f"Synced {len(projs)} projects: {', '.join(projs.keys())}")

STATE_FILE = os.path.expanduser("~/.roshub/active_state.json")

def set_active_project(project_name, pid=None):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    payload = {
        "active_project": project_name,
        "pid": pid,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")
    }
    with open(STATE_FILE, "w") as f:
        json.dump(payload, f, indent=2)

def get_active_project():
    if not os.path.isfile(STATE_FILE):
        return {"active_project": "", "pid": None, "updated_at": ""}
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {"active_project": "", "pid": None, "updated_at": ""}
