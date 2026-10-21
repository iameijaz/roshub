#!/usr/bin/env python3
"""Adds "Start from: GitHub / Folder / Blank" to ROSHub's New project dialog.

Fetch calls /api/hub/import and pre-fills the form; Create still saves via /api/hub/new.
Run:  python3 import_ui_patch.py        Undo:  python3 import_ui_patch.py --undo
"""
import os
import shutil
import sys

H = os.path.expanduser("~/.roshub")
F = os.path.join(H, "home.html")
MARK = "roshub-import-ui"
ANCHOR = '<h3 id="newTitle">New project</h3>'

ROW = ANCHOR + '''
    <fieldset id="imp" class="imp" style="border:1px solid var(--line, #ddd);border-radius:8px;padding:10px 12px;margin:6px 0 12px">
      <legend style="padding:0 4px">Start from</legend>
      <label><input type="radio" name="imp_src" value="github" checked> GitHub URL</label>
      <label style="margin-left:12px"><input type="radio" name="imp_src" value="folder"> Folder</label>
      <label style="margin-left:12px"><input type="radio" name="imp_src" value="blank"> Blank</label>
      <div id="impRow" style="display:flex;gap:8px;margin-top:8px">
        <input id="impVal" aria-label="GitHub URL or folder" style="flex:1" placeholder="https://github.com/owner/repo  (optional /tree/branch)">
        <button type="button" id="impGo">Fetch</button>
      </div>
      <div id="impMsg" role="status" aria-live="polite" style="margin-top:6px;font-size:13px"></div>
    </fieldset>'''

JS = '''
<datalist id="impLaunches"></datalist>
<script>/* roshub-import-ui: GitHub / folder pre-fill for the New project dialog */
(() => {
  const msg = () => $("#impMsg");
  const src = () => (document.querySelector('input[name="imp_src"]:checked') || {}).value;
  const setSel = (id, v) => { const s = $(id); if (s && v && [...s.options].some((o) => o.value === v)) s.value = v; };
  const sync = () => {
    const s = src();
    $("#impRow").style.display = s === "blank" ? "none" : "flex";
    $("#impVal").placeholder = s === "folder" ? "~/my_ws  (workspace, src/, or a package folder)"
                                             : "https://github.com/owner/repo  (optional /tree/branch)";
  };
  document.querySelectorAll('input[name="imp_src"]').forEach((r) => (r.onchange = () => { sync(); msg().textContent = ""; }));
  $("#nf-launch").setAttribute("list", "impLaunches");

  const fetchIt = async () => {
    const v = $("#impVal").value.trim();
    if (!v) { msg().innerHTML = `<span class="bad">Enter a ${src() === "folder" ? "folder" : "GitHub URL"} first.</span>`; return; }
    const btn = $("#impGo"); btn.disabled = true;
    msg().textContent = src() === "github" ? "Cloning (shallow)… this can take a minute." : "Scanning folder…";
    try {
      const body = src() === "github" ? { source: "github", url: v } : { source: "folder", path: v };
      const r = await post("/api/hub/import", body);
      if (!r.ok) { msg().innerHTML = `<span class="bad">✕ ${esc(r.error)}</span>`; return; }
      const f = r.form, g = r.guess, ws = r.workspace;
      $("#nf-name").value = (f.name || "").replace(/[^A-Za-z0-9_]/g, "_");
      $("#nf-desc").value = f.description || "";
      $("#nf-robot").value = f.robot || "";
      $("#nf-ws").value = f.workspace || "";
      $("#nf-launch").value = f.launch || "";
      setSel("#nf-sim", g.simulator);
      $("#impLaunches").innerHTML = (g.launch_options || []).map((l) => `<option value="${esc(l)}">`).join("");
      const miss = r.rosdep_missing;
      msg().innerHTML = `<span class="ok">✓ ${ws.packages.length} package${ws.packages.length === 1 ? "" : "s"}</span>` +
        ` · simulator: ${esc(g.simulator)} · robot: ${esc(g.robot_type)} · ${g.launch_options.length} launch files` +
        (ws.built ? " · built" : ` · <b>not built yet</b>: <code>cd ${esc(ws.path)} && colcon build --symlink-install</code>`) +
        (miss && miss.length ? `<br><span class="bad">Missing deps:</span> ${esc(miss.join(", "))} → <code>rosdep install --from-paths src --ignore-src -y</code>` : "") +
        (r.origin ? `<br><span class="mut">from ${esc(r.origin.url)} @ ${esc((r.origin.commit || "").slice(0, 8))}</span>` : "") +
        `<br>Check the fields below, pick the main launch, then Create.`;
    } catch (e) { msg().innerHTML = `<span class="bad">✕ ${esc(e.message)}</span>`; }
    finally { btn.disabled = false; }
  };
  $("#impGo").onclick = fetchIt;
  $("#impVal").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); fetchIt(); } });

  const inner = openNew;
  openNew = function (preset) {
    inner();
    $("#impVal").value = ""; msg().textContent = ""; $("#impLaunches").innerHTML = "";
    const s = preset && preset.source;
    document.querySelector(`input[name="imp_src"][value="${s || "github"}"]`).checked = true;
    sync();
    if (preset && preset.path) { $("#impVal").value = preset.path; fetchIt(); }
  };
  window.openNewFrom = openNew;     // e.g. openNewFrom({source: "folder", path: "~/husky_ws"})
  $("#newBtn") && ($("#newBtn").onclick = () => openNew());
})();
</script>
'''


def main():
    if "--undo" in sys.argv:
        b = F + ".bak_import_ui"
        if os.path.exists(b):
            shutil.copy2(b, F); print("restored home.html")
        return
    h = open(F).read()
    if MARK in h:
        sys.exit("already patched")
    assert h.count(ANCHOR) == 1, "New project dialog title not found"
    assert 'id="nf-launch"' in h and "function openNew(" in h and "</body>" in h, "dialog fields not found"
    h = h.replace(ANCHOR, ROW, 1).replace("</body>", JS + "</body>", 1)
    shutil.copy2(F, F + ".bak_import_ui")
    open(F, "w").write(h)
    print("PATCHED home.html (backup: home.html.bak_import_ui)")


if __name__ == "__main__":
    main()
