#!/usr/bin/env python3
"""Adds the "Can it move?" card to every ROSHub project page.

Patches (with .bak_move backups):
  hub_motion.py  - results are saved unconfirmed; confirm() sets verified
  ui_server.py   - POST /api/hub/motion accepts {"project", "confirm": true|false}
  home.html      - wraps projectView() and appends the card
Run:   python3 move_card_patch.py          Undo:  python3 move_card_patch.py --undo
"""
import os
import shutil
import sys

H = os.path.expanduser("~/.roshub")
FILES = ["hub_motion.py", "ui_server.py", "home.html"]
MARK = "roshub-move-card"

MOTION_CONFIRM = '''

def confirm(name, moved):
    """User answered "did the robot visibly move?" after the first run."""
    path = _project_file(name)
    if not path:
        return {"ok": False, "error": "project file not found"}
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    mot = ((data.get(name) or {}).get("meta") or {}).get("motion")
    if not mot:
        return {"ok": False, "error": "run the move check first"}
    if moved:
        mot["verified"] = True
    else:
        data[name]["meta"].pop("motion")
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        yaml.safe_dump(data, f, sort_keys=False, default_flow_style=False, allow_unicode=True)
    os.replace(tmp, path)
    return {"ok": True, "verified": bool(moved)}
'''

SERVER_OLD = '''        res = hub_motion.run_check(name, proj, body.get("opts"))'''
SERVER_NEW = '''        if "confirm" in body:
            res = hub_motion.confirm(name, bool(body["confirm"]))
            if res.get("ok"):
                load_projects()
            self._json(res)
            return
        res = hub_motion.run_check(name, proj, body.get("opts"))'''

CARD_JS = r'''
<script>/* roshub-move-card: "Can it move?" card on each project page */
(() => {
  const inner = projectView;
  projectView = async function (name) {
    await inner(name);
    const view = $("#view");
    const put = () => { if (!view.querySelector("#moveCard")) view.insertAdjacentHTML("beforeend", moveCardHtml(name)), wireMoveCard(name); };
    put();
    if (view._moveObs) view._moveObs.disconnect();      // views that redraw keep the card
    view._moveObs = new MutationObserver(() => { if (location.hash.startsWith("#/p/")) put(); });
    view._moveObs.observe(view, { childList: true });
  };
  window.addEventListener("hashchange", () => { const v = $("#view"); if (v && v._moveObs && !location.hash.startsWith("#/p/")) v._moveObs.disconnect(); });

  function saved(name) {
    const p = (LIB && LIB.projects || []).find((x) => x.name === name);
    return p && p.meta && p.meta.motion;
  }
  function badge(m) {
    if (!m) return `<span class="badge">not checked yet</span>`;
    return `<span class="badge">${esc(m.topic)}</span><span class="badge">${esc(m.type)}</span>` +
      `<span class="badge ${m.verified ? "ok" : ""}">${m.verified ? "✓ verified" : "unconfirmed"}</span>`;
  }
  window.moveCardHtml = (name) => `
    <section class="card" id="moveCard" aria-labelledby="moveTitle" style="margin-top:16px">
      <div class="head"><h2 id="moveTitle" class="grow" style="margin:0;font-size:16px">Can it move?</h2></div>
      <div class="desc">Drives slowly (0.1 m/s for 1.5 s, then stops) on the velocity topic that has a subscriber,
        using the message type it expects, and checks that odometry changed. Start the project first.</div>
      <div class="badges" id="moveBadges">${badge(saved(name))}</div>
      <div class="foot"><span class="grow" id="moveMsg" role="status" aria-live="polite"></span>
        <button class="primary" id="moveBtn">Run move check</button></div>
      <div id="moveAsk" hidden style="margin-top:10px">
        <strong>Did the robot visibly move in the simulator?</strong>
        <button id="moveYes">Yes, it moved</button> <button class="ghost" id="moveNo">No</button>
      </div>
    </section>`;
  window.wireMoveCard = (name) => {
    const msg = $("#moveMsg"), btn = $("#moveBtn"), ask = $("#moveAsk");
    btn.onclick = async () => {
      btn.disabled = true; ask.hidden = true; msg.textContent = "Checking… keep clear of the robot.";
      try {
        const r = await post("/api/hub/motion", { project: name });
        if (r.ok) {
          msg.innerHTML = `<span class="ok">✓ Moved ${r.distance_m} m, ${r.rotation_rad} rad</span> · ${esc(r.type.split("/").pop())} on ${esc(r.topic)}`;
          $("#moveBadges").innerHTML = badge({ topic: r.topic, type: r.type.split("/").pop(), verified: !r.first_run });
          ask.hidden = !r.first_run;
        } else {
          const c = (r.candidates || []).map((x) => x.topic).join(", ");
          msg.innerHTML = `<span class="bad">✕ ${esc(r.error || "failed")}</span>${c ? ` · candidates: ${esc(c)}` : ""}`;
        }
      } catch (e) { msg.innerHTML = `<span class="bad">✕ ${esc(e.message)}</span>`; }
      btn.disabled = false;
    };
    const answer = async (moved) => {
      const r = await post("/api/hub/motion", { project: name, confirm: moved });
      ask.hidden = true;
      if (!r.ok) return toast(r.error || "could not save");
      LIB = await api("/api/hub/library");
      $("#moveBadges").innerHTML = badge(saved(name));
      toast(moved ? "Saved: teleop will use this topic and type" : "Cleared. Check the topic and controller, then run again");
    };
    $("#moveYes").onclick = () => answer(true);
    $("#moveNo").onclick = () => answer(false);
  };
})();
</script>
'''


def main():
    if "--undo" in sys.argv:
        for f in FILES:
            b = os.path.join(H, f + ".bak_move")
            if os.path.exists(b):
                shutil.copy2(b, os.path.join(H, f)); print("restored", f)
        return
    src = {f: open(os.path.join(H, f)).read() for f in FILES}
    if MARK in src["home.html"]:
        sys.exit("already patched")
    m = src["hub_motion.py"]
    assert '"verified": True}' in m, "hub_motion.py: _remember line not found"
    m = m.replace('"verified": True}', '"verified": False}', 1) + MOTION_CONFIRM
    s = src["ui_server.py"]
    assert SERVER_OLD in s, "ui_server.py: motion handler not found"
    s = s.replace(SERVER_OLD, SERVER_NEW, 1)
    h = src["home.html"]
    assert "async function projectView(" in h and "</body>" in h, "home.html: projectView or </body> not found"
    h = h.replace("</body>", CARD_JS + "</body>", 1)
    for f, text in (("hub_motion.py", m), ("ui_server.py", s), ("home.html", h)):
        p = os.path.join(H, f)
        shutil.copy2(p, p + ".bak_move")
        open(p, "w").write(text)
    print("PATCHED hub_motion.py, ui_server.py, home.html (backups: *.bak_move)")


if __name__ == "__main__":
    main()
