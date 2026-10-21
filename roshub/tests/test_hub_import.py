"""Nested-workspace detection on fake repo layouts (no git, no ROS)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import hub_import as hi  # noqa: E402


def _pkg(path):
    os.makedirs(os.path.join(path, "src"))
    open(os.path.join(path, "package.xml"), "w").close()


def test_cpp_packages_with_src_are_not_workspaces(tmp_path):
    # turtlebot3_simulations layout: <ws>/src/<repo>/<pkg>/src/*.cpp
    for pkg in ("turtlebot3_gazebo", "turtlebot3_fake_node"):
        _pkg(tmp_path / "src" / "turtlebot3_simulations" / pkg)
    assert hi.nested_workspace(str(tmp_path)) == (str(tmp_path), None)


def test_repo_with_distro_workspaces_picks_the_matching_one(tmp_path, monkeypatch):
    monkeypatch.setenv("ROS_DISTRO", "jazzy")
    for ws in ("humble_ws", "jazzy_ws"):
        _pkg(tmp_path / "src" / "repo" / ws / "src" / "pkg")
    path, note = hi.nested_workspace(str(tmp_path))
    assert os.path.basename(path) == "jazzy_ws" and "jazzy" in note


def test_ambiguous_workspaces_are_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("ROS_DISTRO", "jazzy")
    for ws in ("a_ws", "b_ws"):
        _pkg(tmp_path / "src" / "repo" / ws / "src" / "pkg")
    with pytest.raises(ValueError, match="several workspaces"):
        hi.nested_workspace(str(tmp_path))
