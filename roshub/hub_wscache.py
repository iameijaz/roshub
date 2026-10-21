"""ROSHub: cache the (slow) colcon workspace scan on disk."""
import json
import os
import threading
import time

CACHE = os.path.expanduser("~/.roshub/workspaces_cache.json")
_lock = threading.Lock()


def _load():
    try:
        with open(CACHE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"scanned_at": 0, "auto": False, "workspaces": []}


def _save(d):
    tmp = CACHE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f)
    os.replace(tmp, CACHE)


def get(scan, extra, refresh=False, auto=None):
    """scan: hub.scan_workspaces; extra: explicitly added workspace paths.
    Returns the cached list; rescans fully only when asked (or auto is on / no cache yet).
    Extra workspaces missing from the cache are scanned on their own (fast)."""
    with _lock:
        d = _load()
        if auto is not None:
            d["auto"] = bool(auto)
        if refresh or d.get("auto") or not d.get("scanned_at"):
            t = time.time()
            d["workspaces"] = scan(extra)
            d["scanned_at"], d["scan_seconds"] = time.time(), round(time.time() - t, 1)
        else:
            have = {w["path"] for w in d["workspaces"]}
            new = [p for p in extra if os.path.realpath(p) not in have]
            if new:
                d["workspaces"] = [w for w in scan(new) if w["path"] not in have] + d["workspaces"]
        d["workspaces"] = [w for w in d["workspaces"] if os.path.isdir(w["path"])]    # removed folders
        _save(d)
        return d
