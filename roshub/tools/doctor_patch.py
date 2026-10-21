#!/usr/bin/env python3
"""Makes Diagnostics project-aware.

Isaac Sim projects keep tools/bridge_doctor.py; every other project uses
tools/project_doctor.py with --project. The Diagnostics tab sends the selected project.
Run:  python3 doctor_patch.py        Undo:  python3 doctor_patch.py --undo
"""
import os
import re
import shutil
import sys

H = os.path.expanduser("~/.roshub")
FILES = ["ui_server.py", "home.html"]

ROUTER_FN = '''

def _run_doctor_for(deep=False, project=""):
    """Isaac projects (or no project) -> bridge_doctor; everything else -> project_doctor."""
    proj = (_PROJECTS["data"] or load_projects()).get(project) if project else None
    sim = ((proj or {}).get("meta") or {}).get("simulator", "")
    pd = os.path.expanduser("~/.roshub/tools/project_doctor.py")
    if not proj or sim == "isaac_sim" or not os.path.isfile(pd):
        return _run_doctor(deep)
    args = ["python3", pd, "--project", project] + ([] if deep else ["--quick"])
    try:
        r = subprocess.run(args, env=dict(_ros_env(), NO_COLOR="1"), capture_output=True, text=True, timeout=60)
        return {"ok": r.returncode == 0, "tool": pd, "output": ANSI.sub("", r.stdout + r.stderr)[-20000:]}
    except subprocess.TimeoutExpired:
        return {"ok": False, "tool": pd, "output": "project doctor timed out after 60 s"}
'''

JS = '''
<script>/* roshub-doctor-project: send the selected Diagnostics project to the doctor */
(() => {
  const inner = api;
  api = function (path, opts) {
    if (typeof path === "string" && path.startsWith("/api/hub/doctor") && !/[?&]project=/.test(path)) {
      const sel = document.querySelector("#view select");
      const proj = (sel && sel.value) || (typeof DIAG_PROJECT !== "undefined" && DIAG_PROJECT) || "";
      if (proj) path += (path.includes("?") ? "&" : "?") + "project=" + encodeURIComponent(proj);
    }
    return inner(path, opts);
  };
})();
</script>
'''


def main():
    if "--undo" in sys.argv:
        for f in FILES:
            b = os.path.join(H, f + ".bak_doctor")
            if os.path.exists(b):
                shutil.copy2(b, os.path.join(H, f)); print("restored", f)
        return
    s = open(os.path.join(H, "ui_server.py")).read()
    h = open(os.path.join(H, "home.html")).read()
    if "_run_doctor_for" in s:
        sys.exit("already patched")
    call = re.search(r'_run_doctor\(qs\.get\("deep", \["0"\]\)\[0\] == "1"\)', s)
    assert call, "doctor call in /api/hub/doctor handler not found"
    s = s[:call.start()] + '_run_doctor_for(qs.get("deep", ["0"])[0] == "1", qs.get("project", [""])[0])' + s[call.end():]
    anchor = re.search(r"^class ReusableServer", s, flags=re.M)
    assert anchor, "class ReusableServer not found"
    s = s[:anchor.start()] + ROUTER_FN.lstrip("\n") + "\n\n" + s[anchor.start():]
    assert "</body>" in h, "home.html: </body> not found"
    h = h.replace("</body>", JS + "</body>", 1)
    for f, text in (("ui_server.py", s), ("home.html", h)):
        p = os.path.join(H, f)
        shutil.copy2(p, p + ".bak_doctor")
        open(p, "w").write(text)
    print("PATCHED ui_server.py, home.html (backups: *.bak_doctor)")


if __name__ == "__main__":
    main()
