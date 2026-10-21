#!/usr/bin/env python3
"""ROSHub: Delete button, "Add as project" on workspaces, readable failure banners.

Writes ~/.roshub/hub_manage.py and patches ui_server.py + home.html (backups *.bak_manage).
Run:  python3 manage_patch.py        Undo:  python3 manage_patch.py --undo
"""
import os
import re
import shutil
import sys

H = os.path.expanduser("~/.roshub")
MARK = "roshub-manage"

MANAGE = '''"""ROSHub: delete projects safely (to a trash folder, never rm)."""
import os
import shutil
import time

PROJECTS = os.path.expanduser("~/.roshub/projects")
CLONES = os.path.expanduser("~/roshub_ws")


def delete(name, projects, remove_clone=False):
    """Move projects/<name>.yaml to projects/.trash/. Optionally move its clone to ~/roshub_ws/.trash/."""
    path = os.path.join(PROJECTS, f"{name}.yaml")
    if not os.path.isfile(path):
        if name in projects:
            return {"ok": False, "error": f"'{name}' is defined inside ~/.roshub.yaml, not its own file - archive it instead"}
        return {"ok": False, "error": f"no project file for '{name}'"}
    stamp = time.strftime("%Y%m%d_%H%M%S")
    trash = os.path.join(PROJECTS, ".trash")
    os.makedirs(trash, exist_ok=True)
    shutil.move(path, os.path.join(trash, f"{name}.{stamp}.yaml"))
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
'''

JS = r'''
<script>/* roshub-manage: Delete, Add as project, readable failures */
(() => {
  /* --- Delete next to Archive on project cards --- */
  const drawInner = drawCards;
  drawCards = function () {
    drawInner.apply(this, arguments);
    for (const b of document.querySelectorAll("[data-arch]")) {
      if (b.nextElementSibling && b.nextElementSibling.dataset && b.nextElementSibling.dataset.del) continue;
      const name = b.dataset.arch;
      b.insertAdjacentHTML("afterend", `<button class="ghost" data-del="${esc(name)}" title="Move to trash">Delete</button>`);
      b.nextElementSibling.onclick = async () => {
        const p = (LIB.projects || []).find((x) => x.name === name) || { meta: {} };
        if (!confirm(`Delete project "${name}"?\n\nIts file moves to ~/.roshub/projects/.trash/ (you can restore it from there).`)) return;
        const ws = (p.meta && p.meta.workspace) || "";
        const clone = /\/roshub_ws\//.test(ws) && confirm(`Also move the cloned source folder to trash?\n\n${ws}\n\nCancel keeps it.`);
        const r = await post("/api/hub/delete", { project: name, remove_clone: clone });
        if (!r.ok) return toast(r.error || "could not delete");
        toast(`Deleted ${name}${r.clone ? " · clone " + r.clone : ""}`);
        homeView();
      };
    }
  };

  /* --- "Add as project" on each workspace --- */
  const wsInner = workspacesView;
  workspacesView = async function () {
    await wsInner.apply(this, arguments);
    const known = new Set(((LIB && LIB.projects) || []).map((p) => p.meta && p.meta.workspace).filter(Boolean));
    for (const d of document.querySelectorAll("details.ws")) {
      const sm = d.querySelector("summary"), m = sm && sm.textContent.match(/·\s*(\/\S+)\s*·/);
      if (!m || d.querySelector("[data-addws]")) continue;
      const path = m[1];
      const used = [...known].some((w) => w.replace(/^~/, "") && path.endsWith(w.replace(/^~/, "")));
      sm.insertAdjacentHTML("beforeend", ` <button data-addws="${esc(path)}" style="margin-left:8px" ${used ? "class='ghost'" : "class='primary'"}
        title="Open the New project dialog with this folder">${used ? "+ Another project" : "+ Add as project"}</button>`);
      sm.querySelector("[data-addws]").onclick = (e) => { e.preventDefault(); e.stopPropagation(); openNewFrom({ source: "folder", path }); };
    }
  };

  /* --- readable runbook failures --- */
  const HINT = {
    3: "Missing dependencies. Open the log for the install command, run it in a terminal, then press Retry this step.",
    4: "Build failed. Open the log: the first compiler/CMake error is the real cause.",
    2: "Workspace folder is wrong or has no src/. Fix the workspace in the project profile.",
  };
  const decorate = () => {
    for (const el of document.querySelectorAll("#view *")) {
      if (el.children.length || el.dataset.hinted) continue;
      const m = el.textContent.match(/exited \(code (\d+)\)/);
      if (m && HINT[m[1]]) {
        el.dataset.hinted = "1";
        el.insertAdjacentHTML("afterend", `<div class="bad" style="font-weight:600;margin-top:4px">${HINT[m[1]]}</div>`);
      }
    }
  };
  new MutationObserver(decorate).observe(document.getElementById("view"), { childList: true, subtree: true });
})();
</script>
'''


def main():
    files = ["ui_server.py", "home.html"]
    if "--undo" in sys.argv:
        for f in files:
            b = os.path.join(H, f + ".bak_manage")
            if os.path.exists(b):
                shutil.copy2(b, os.path.join(H, f)); print("restored", f)
        return
    s = open(os.path.join(H, "ui_server.py")).read()
    h = open(os.path.join(H, "home.html")).read()
    if MARK in h or "hub_manage" in s:
        sys.exit("already patched")
    n = 0
    s, n = re.subn(r"^import hub_import$", "import hub_import\nimport hub_manage", s, count=1, flags=re.M)
    assert n == 1, "import hub_import line not found"
    m = re.search(r'^( *)elif parsed\.path == "/api/hub/import":', s, flags=re.M)
    assert m, "/api/hub/import handler not found"
    i = m.group(1)
    block = "".join(i + line + "\n" for line in (
        'elif parsed.path == "/api/hub/delete":',
        '    res = hub_manage.delete(body.get("project", ""), _PROJECTS["data"] or load_projects(),',
        '                            bool(body.get("remove_clone")))',
        '    if res.get("ok"):',
        '        load_projects()',
        '    self._json(res)'))
    s = s[:m.start()] + block + s[m.start():]
    for fn in ("function drawCards(", "async function workspacesView(", "</body>"):
        assert fn in h, f"home.html: {fn} not found"
    assert "openNewFrom" in h, "home.html: run import_ui_patch.py first (openNewFrom missing)"
    h = h.replace("</body>", JS + "</body>", 1)
    for f, text in (("ui_server.py", s), ("home.html", h)):
        p = os.path.join(H, f)
        shutil.copy2(p, p + ".bak_manage")
        open(p, "w").write(text)
    with open(os.path.join(H, "hub_manage.py"), "w") as f:
        f.write(MANAGE)
    print("PATCHED ui_server.py, home.html; wrote hub_manage.py (backups: *.bak_manage)")


if __name__ == "__main__":
    main()
