"""Profile validation against the project files in tests/fixtures/."""
import glob
import os
import sys

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import profile_schema as ps  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
PROFILES = sorted(glob.glob(os.path.join(FIXTURES, "*.yaml")))


def test_fixtures_present():
    assert len(PROFILES) >= 3


@pytest.mark.parametrize("path", PROFILES, ids=os.path.basename)
def test_fixture_profiles_have_no_errors(path):
    text = open(path).read()
    if os.path.basename(path).startswith("_roshub"):        # legacy file: projects nested under `projects:`
        text = yaml.safe_dump(yaml.safe_load(text)["projects"], sort_keys=False)
    _, issues = ps.validate(text)
    assert [i for i in issues if i["level"] == "ERROR"] == []


def test_runbook_shape():
    data, issues = ps.validate(open(os.path.join(FIXTURES, "husky_nav2.yaml")).read())
    rb = data["husky_nav2"]["runbook"]
    assert [s["id"] for s in rb] == ["router", "sim", "odom", "nav2", "smoke"]
    assert not [i for i in issues if i["level"] != "INFO" and "runbook" in i["where"] and i["level"] == "ERROR"]


@pytest.mark.parametrize("text,needle", [
    ("p: [1", "YAML syntax"),
    ("p:\n  description: x\n", "needs `steps`"),
    ("p:\n  steps: [{name: a}]\n", "needs a `cmd`"),
    ("p:\n  runbook: [{id: a, ready: [{bogus: 1}]}]\n", "check needs one of"),
    ("p:\n  runbook: [{id: a, manual: x}, {id: a, manual: y}]\n", "duplicate stage id"),
    ("p:\n  runbook: [{id: a}]\n", "does nothing"),
    ("p:\n  steps: [{cmd: x}]\n  controls: [{id: c, cmd: x}, {id: c, cmd: y}]\n", "duplicate control id"),
])
def test_errors(text, needle):
    _, issues = ps.validate(text)
    assert any(needle in i["msg"] and i["level"] == "ERROR" for i in issues), issues


def test_template_is_valid():
    _, issues = ps.validate(ps.TEMPLATE.format(name="my_robot"))
    assert [i for i in issues if i["level"] == "ERROR"] == []


def test_save_refuses_paths_outside_projects_and_invalid_yaml(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "PROJECTS_DIR", str(tmp_path / "projects"))
    r = ps.save(str(tmp_path / "evil.yaml"), "p:\n  steps: [{cmd: x}]\n")
    assert not r["ok"]
    target = tmp_path / "projects" / "p.yaml"
    assert not ps.save(str(target), "p: [")["ok"]
    r = ps.save(str(target), "p:\n  steps: [{name: a, cmd: x}]\n")
    assert r["ok"] and target.read_text().startswith("p:")
    r = ps.save(str(target), "p:\n  steps: [{name: a, cmd: y}]\n")
    assert r["backup"] and os.path.exists(r["backup"])
