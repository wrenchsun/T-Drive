"""プロジェクトフォルダ（docs/13 §1）。"""

import os
from pathlib import Path

import pytest

from tdrive_toon import REPO_ROOT, project


@pytest.fixture(autouse=True)
def _reset():
    saved = os.environ.get(project.ENV)
    yield
    project.set_root(None)
    if saved is None:
        os.environ.pop(project.ENV, None)
    else:
        os.environ[project.ENV] = saved


def test_default_is_tool_repo_for_development():
    project.set_root(None)
    os.environ.pop(project.ENV, None)
    assert project.root() == REPO_ROOT and project.is_tool_repo()
    assert project.looks_dir() == REPO_ROOT / "looks"


def test_chosen_folder_holds_data_and_env_var(tmp_path):
    project.set_root(tmp_path)
    assert project.looks_dir() == tmp_path.resolve() / "looks"
    assert project.export_dir() == tmp_path.resolve() / "build" / "unity"
    assert os.environ[project.ENV] == tmp_path.resolve().as_posix()  # シーンの $TDRIVE_PROJECT/… が解決できる
    assert not project.is_tool_repo()


def test_paths_are_relative_to_project(tmp_path):
    project.set_root(tmp_path)
    tex = tmp_path / "textures" / "a.png"
    assert project.to_project_path(tex) == "textures/a.png"
    assert Path(project.from_project_path("textures/a.png")) == tex.resolve()
    outside = REPO_ROOT / "assets" / "x.png"
    assert project.to_project_path(outside) == outside.as_posix()  # プロジェクト外は絶対パスのまま


def test_project_profiles_override_bundled(tmp_path):
    project.set_root(tmp_path)
    dirs = project.profile_dirs()
    assert dirs[0] == tmp_path.resolve() / "looks" / "_env" and dirs[-1] == REPO_ROOT / "looks" / "_env"


def test_config_roundtrip(tmp_path):
    project.set_root(tmp_path)
    assert project.read_config() == {}
    project.write_config(lastAppliedToolVersion="0.1.0")
    assert project.read_config()["lastAppliedToolVersion"] == "0.1.0"
    assert (tmp_path / ".tdrive" / "project.json").exists()
