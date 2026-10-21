#!/usr/bin/env python3
"""ROSHub: workspaces as cards + a workspace page (projects, scenarios, packages),
and a runbook fix so a step never passes while its workspace is still building.

Patches home.html and runbook.py (backups *.bak_wsui).
Run:  python3 workspace_ui_patch.py        Undo:  python3 workspace_ui_patch.py --undo
"""
import os
import shutil
import sys

H = os.path.expanduser("~/.roshub")
MARK = "roshub-wsui"

# ---------------------------------------------------------------- runbook gate
RB_OLD = '''            all_ok = self._check_all(s, checks) if checks else False'''
RB_NEW = '''            if "ensure_built.sh" in run.get("cmd", "") and not _build_done(s.log_path):
                s.message = "building workspace (ready checks start after the build)"
                if self.clock() - s.started > timeout:
                    return self._finish(s, "failed", f"build did not finish within {timeout:.0f}s")
                self.stop_event.wait(POLL_S)
                continue
            all_ok = self._check_all(s, checks) if checks else False'''
RB_HELPER = '''

def _build_done(log_path):
    """ensure_built.sh prints one of these once the workspace is ready to launch."""
    try:
        with open(log_path, errors="ignore") as f:
            text = f.read()
    except OSError:
        return False
    return "ROSHub: workspace up to date" in text or "ROSHub: build OK" in text


def check_kind(check):'''

# ---------------------------------------------------------------- UI
CSS = '''<style>/* roshub-wsui */
.wscards { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 14px; }
.wscard { display: flex; flex-direction: column; gap: 8px; text-decoration: none; color: inherit; }
.wscard .path { font-size: 12px; color: var(--muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.wsstats { display: flex; gap: 14px; font-size: 13px; color: var(--muted); }
.wsstats b { color: var(--ink); }
.wsrow { display: grid; grid-template-columns: minmax(160px, 220px) 1fr; gap: 12px; padding: 10px 0; border-top: 1px solid var(--line, #eaeef2); }
.scen { display: flex; align-items: center; gap: 10px; padding: 6px 8px; border-radius: 7px; }
.scen:hover { background: var(--chip); }
.scen code { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.scen button { height: 28px; padding: 0 10px; font-size: 12px; }
.wsproj { display: flex; flex-wrap: wrap; gap: 8px; }
.wsproj a { padding: 6px 12px; border-radius: 8px; background: var(--card, #fff); border: 1px solid var(--line, #d0d7de); text-decoration: none; color: var(--ink); font-weight: 500; }
.wsproj a:hover { background: var(--chip); }
</style>'''

JS = r'''
<script>/* roshub-wsui: workspace cards and workspace pages */
(() => {
  const short = (p) => p.replace(/^\/home\/[^/]+/, "~");
  const projectsFor = (ws) => ((LIB && LIB.projects) || []).filter((p) => {
    const w = (p.meta && p.meta.workspace) || "";
    return w && (w === ws.path || w.replace(/^~/, "") && ws.path.endsWith(w.replace(/^~/, "")));
  });
  let WS = null;
  const loadWs = async () => {
    if (!LIB) LIB = await api("/api/hub/library");
    WS = (await api("/api/hub/workspaces")).workspaces || [];
    return WS;
  };

  /* --- list: cards --- */
  workspacesView = async function () {
    $("#view").innerHTML = `<h1>Workspaces</h1><p class="sub">colcon workspaces on this machine. Open one to see its projects and the launch files you can run.</p><div id="wsl" class="mut">Scanning…</div>`;
    const list = await loadWs();
    $("#wsl").className = "wscards";
    $("#wsl").innerHTML = list.map((w) => {
      const launches = w.packages.reduce((n, p) => n + p.launch.length, 0);
      const unbuilt = w.packages.filter((p) => !p.built).length, stale = w.packages.filter((p) => p.stale).length;
      const names = w.packages.map((p) => p.name), dups = new Set(names.filter((n, i) => names.indexOf(n) !== i));
      const projs = projectsFor(w).length;
      const state = dups.size ? `<span class="badge bad">duplicate packages</span>`
        : !w.built ? `<span class="badge">not built</span>`
        : stale ? `<span class="badge bad">${stale} changed since build</span>`
        : unbuilt ? `<span class="badge">${unbuilt} not built</span>` : `<span class="badge ok">built</span>`;
      return `<a class="card wscard" href="#/w/${encodeURIComponent(w.name)}">
        <div class="head"><span class="name grow">${esc(w.name)}</span></div>
        <div class="path" title="${esc(w.path)}">${esc(short(w.path))}</div>
        <div class="wsstats"><span><b>${w.packages.length}</b> packages</span><span><b>${launches}</b> launch files</span><span><b>${projs}</b> project${projs === 1 ? "" : "s"}</span></div>
        <div class="badges">${state}</div></a>`;
    }).join("") || `<div class="empty">No workspaces with a <code>src/</code> folder found. Use <b>+ New project</b> to import one from GitHub or a folder.</div>`;
  };

  /* --- detail page --- */
  async function workspaceView(name) {
    for (const a of document.querySelectorAll("nav.tabs a")) a.classList.toggle("on", a.dataset.tab === "workspaces");
    const list = WS || await loadWs();
    const w = list.find((x) => x.name === name) || (await loadWs()).find((x) => x.name === name);
    if (!w) { $("#view").innerHTML = `<div class="empty">No workspace called <b>${esc(name)}</b>. <a href="#/workspaces">All workspaces</a></div>`; return; }
    const projs = projectsFor(w);
    const names = w.packages.map((p) => p.name), dups = [...new Set(names.filter((n, i) => names.indexOf(n) !== i))];
    const withLaunch = w.packages.filter((p) => p.launch.length);
    $("#view").innerHTML = `
      <p><a href="#/workspaces">← Workspaces</a></p>
      <div class="phead"><div class="grow"><h1 style="margin:0">${esc(w.name)}</h1>
        <p class="sub" style="margin:4px 0 0">${esc(short(w.path))} · ${w.packages.length} packages · ${w.built ? "built" : "not built"}</p></div>
        <button class="primary" id="wsNew">+ New project</button></div>
      ${dups.length ? `<div class="empty bad" style="margin:12px 0">Duplicate package names (${esc(dups.slice(0, 4).join(", "))}${dups.length > 4 ? "…" : ""}): colcon cannot build this as one workspace. It probably holds copies for several ROS distros. Use a sub-folder as the workspace instead.</div>` : ""}
      <h2 style="font-size:17px;margin:22px 0 8px">Projects using this workspace</h2>
      <div class="wsproj">${projs.map((p) => `<a href="#/p/${encodeURIComponent(p.name)}">${esc(p.name)}</a>`).join("") || `<span class="mut">None yet. Pick a scenario below to create one.</span>`}</div>
      <h2 style="font-size:17px;margin:22px 0 8px">Scenarios <span class="mut" style="font-size:13px;font-weight:400">· launch files you can run</span></h2>
      <div class="card" style="padding:6px 12px">${withLaunch.map((p) => `<div class="wsrow"><div><b>${esc(p.name)}</b><br>
          <span class="${p.stale ? "bad" : p.built ? "ok" : "mut"}" style="font-size:12px">${p.stale ? "changed since build" : p.built ? "built" : "not built"}</span></div>
          <div>${p.launch.map((l) => `<div class="scen"><code title="${esc(l)}">${esc(l.split("/").pop())}</code>
            <button data-scen="${esc(p.name + " " + l.split("/").pop())}">Create project</button></div>`).join("")}</div></div>`).join("")
        || `<p class="mut">No launch files found.</p>`}</div>
      <details class="sect" style="margin-top:18px"><summary>All packages (${w.packages.length})</summary>
        ${w.packages.map((p) => `<div class="wsrow"><div><b>${esc(p.name)}</b></div><div class="mut" style="font-size:12px">${esc(short(p.path))}${p.launch.length ? "" : " · no launch files"}</div></div>`).join("")}
      </details>`;
    const create = (launch) => {
      openNewFrom({ source: "folder", path: w.path });
      if (!launch) return;
      const t0 = Date.now(), tick = setInterval(() => {               // wait for the folder fetch to fill the form
        if (/✓/.test(($("#impMsg") || {}).textContent || "") || Date.now() - t0 > 8000) {
          clearInterval(tick); $("#nf-launch").value = launch;
          const pkg = launch.split(" ")[0], file = launch.split(" ")[1].replace(/\.launch\.(py|xml|yaml)$/, "");
          if (!$("#nf-name").dataset.touched) $("#nf-name").value = `${pkg}_${file}`.replace(/[^A-Za-z0-9_]/g, "_").slice(0, 48);
        }
      }, 150);
    };
    $("#wsNew").onclick = () => create("");
    for (const b of document.querySelectorAll("[data-scen]")) b.onclick = () => create(b.dataset.scen);
  }

  /* --- routing: #/w/<name> --- */
  window.removeEventListener("hashchange", route);
  const route0 = route;
  route = async function () {
    const h = location.hash.slice(1);
    if (h.startsWith("/w/")) {
      clearInterval(poll); poll = null;
      try { return await workspaceView(decodeURIComponent(h.slice(3))); }
      catch (e) { $("#view").innerHTML = `<div class="empty bad">Could not load the workspace: ${esc(e.message)}</div>`; return; }
    }
    return route0.apply(this, arguments);
  };
  window.addEventListener("hashchange", route);
  if (location.hash.startsWith("#/w/")) route();
})();
</script>
'''


def main():
    hp, rp = os.path.join(H, "home.html"), os.path.join(H, "runbook.py")
    if "--undo" in sys.argv:
        for p in (hp, rp):
            if os.path.exists(p + ".bak_wsui"):
                shutil.copy2(p + ".bak_wsui", p); print("restored", os.path.basename(p))
        return
    h, r = open(hp).read(), open(rp).read()
    if MARK in h or "_build_done" in r:
        sys.exit("already patched")
    assert r.count(RB_OLD) == 1, "runbook.py: ready-check line not found"
    assert r.count("\ndef check_kind(check):") == 1, "runbook.py: check_kind not found"
    r = r.replace(RB_OLD, RB_NEW).replace("\n\ndef check_kind(check):", RB_HELPER, 1)
    for need in ("async function workspacesView(", "async function route(", "openNewFrom", "</head>", "</body>"):
        assert need in h, f"home.html: {need} not found (apply the earlier patches first)"
    h = h.replace("</head>", CSS + "\n</head>", 1).replace("</body>", JS + "</body>", 1)
    for p, text in ((hp, h), (rp, r)):
        shutil.copy2(p, p + ".bak_wsui")
        open(p, "w").write(text)
    print("PATCHED home.html, runbook.py (backups: *.bak_wsui)")


if __name__ == "__main__":
    main()
