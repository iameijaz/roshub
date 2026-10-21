#!/usr/bin/env python3
"""ROSHub: cached workspace scan (instant Workspaces tab), Rescan button, auto-rescan checkbox.

Writes ~/.roshub/hub_wscache.py; patches ui_server.py + home.html (backups *.bak_wscache).
Run:  python3 wscache_patch.py        Undo:  python3 wscache_patch.py --undo
"""
import os
import re
import shutil
import sys

H = os.path.expanduser("~/.roshub")

MODULE = '''"""ROSHub: cache the (slow) colcon workspace scan on disk."""
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
'''

SERVER_OLD = 'self._json({"workspaces": hub.scan_workspaces(hub_import.extra_workspaces())})'
SERVER_NEW = ('self._json(hub_wscache.get(hub.scan_workspaces, hub_import.extra_workspaces(), '
              'refresh=qs.get("refresh", ["0"])[0] == "1", '
              'auto=None if "auto" not in qs else qs["auto"][0] == "1"))')

JS = r'''
<script>/* roshub-wscache: scan info, Rescan, auto-rescan */
(() => {
  const ago = (t) => { const s = Date.now() / 1000 - t; return s < 90 ? "just now" : s < 3600 ? `${Math.round(s / 60)} min ago` : s < 86400 ? `${Math.round(s / 3600)} h ago` : `${Math.round(s / 86400)} d ago`; };
  const inner = api;
  let last = null;
  api = async function (path, opts) {
    const r = await inner(path, opts);
    if (typeof path === "string" && path.startsWith("/api/hub/workspaces")) { last = r; setTimeout(bar, 0); }
    return r;
  };
  function bar() {
    const host = document.querySelector("#view h1 + p.sub");
    if (!host || !last || !location.hash.startsWith("#/workspaces")) return;
    let b = document.getElementById("wsbar");
    if (!b) { host.insertAdjacentHTML("afterend", `<div id="wsbar" style="display:flex;align-items:center;gap:14px;margin:4px 0 14px;font-size:13px"></div>`); b = document.getElementById("wsbar"); }
    b.innerHTML = `<span class="mut">Scanned ${ago(last.scanned_at)}${last.scan_seconds ? ` · took ${last.scan_seconds} s` : ""}</span>
      <button id="wsRescan">Rescan</button>
      <label style="display:flex;align-items:center;gap:6px;cursor:pointer"><input type="checkbox" id="wsAuto" ${last.auto ? "checked" : ""}> Rescan every time I open this tab</label>`;
    $("#wsRescan").onclick = async () => {
      const btn = $("#wsRescan"); btn.disabled = true; btn.textContent = "Scanning… (can take a while)";
      try { await api("/api/hub/workspaces?refresh=1"); await workspacesView(); } finally { btn.disabled = false; }
    };
    $("#wsAuto").onchange = async (e) => { await inner(`/api/hub/workspaces?auto=${e.target.checked ? 1 : 0}`); last.auto = e.target.checked; toast(e.target.checked ? "Workspaces rescan on every visit" : "Workspaces use the last scan"); };
  }
  new MutationObserver(bar).observe(document.getElementById("view"), { childList: true });
})();
</script>
'''


def main():
    sp, hp = os.path.join(H, "ui_server.py"), os.path.join(H, "home.html")
    if "--undo" in sys.argv:
        for p in (sp, hp):
            if os.path.exists(p + ".bak_wscache"):
                shutil.copy2(p + ".bak_wscache", p); print("restored", os.path.basename(p))
        return
    s, h = open(sp).read(), open(hp).read()
    if "hub_wscache" in s:
        sys.exit("already patched")
    assert s.count(SERVER_OLD) == 1, "ui_server.py: workspace handler not found"
    s = s.replace(SERVER_OLD, SERVER_NEW)
    s, n = re.subn(r"^import hub_import$", "import hub_import\nimport hub_wscache", s, count=1, flags=re.M)
    assert n == 1, "import hub_import line not found"
    assert "</body>" in h
    h = h.replace("</body>", JS + "</body>", 1)
    for p, text in ((sp, s), (hp, h)):
        shutil.copy2(p, p + ".bak_wscache")
        open(p, "w").write(text)
    with open(os.path.join(H, "hub_wscache.py"), "w") as f:
        f.write(MODULE)
    print("PATCHED ui_server.py, home.html; wrote hub_wscache.py (backups: *.bak_wscache)")


if __name__ == "__main__":
    main()
