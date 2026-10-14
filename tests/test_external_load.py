"""tdrive/external_load.py（外部ツールの読み込み: sys.path・import・create_tool・失敗時の読み込めなかったタブ・unload）。Maya 不要。

shell は Maya を import するので、register / unavailable を差し替えて検査する（実物は tests/test_maya_external_load_smoke.py）。
"""

import json
import sys
from pathlib import Path

import pytest

from tdrive import external_load

FIX = Path(__file__).resolve().parent / "fixtures"
DUMMY = FIX / "external_tool"
BROKEN = FIX / "external_tool_broken"


class Unavailable:
    def __init__(self, tool_id, label, text):
        self.id, self.label, self.text = tool_id, label, text


@pytest.fixture(autouse=True)
def _clean():
    external_load.unload()
    yield
    external_load.unload()


def _tools_json(tmp_path, entries):
    p = tmp_path / "tools.json"
    p.write_text(json.dumps({"version": 1, "tools": entries}), encoding="utf-8")
    return p


def _run(p):
    registered = []
    ids = external_load.load_external_tools(p, register=registered.append, unavailable=Unavailable)
    return ids, registered


def test_loads_dummy_tool_and_adds_sys_path_once(tmp_path):
    p = _tools_json(tmp_path, [{"id": "tdrive_dummy", "dir": str(DUMMY)}])
    scripts = str(DUMMY / "maya" / "scripts")
    ids, registered = _run(p)
    assert ids == ["tdrive_dummy"]
    assert [t.id for t in registered] == ["tdrive_dummy"] and registered[0].label == "Dummy"
    assert sys.path.count(scripts) == 1
    _run(p)  # もう一度読んでも重複して足さない
    assert sys.path.count(scripts) == 1


def test_broken_tool_becomes_unavailable_and_others_survive(tmp_path):
    p = _tools_json(tmp_path, [{"id": "tdrive_broken", "dir": str(BROKEN)}, {"id": "tdrive_dummy", "dir": str(DUMMY)}])
    ids, registered = _run(p)
    assert ids == ["tdrive_broken", "tdrive_dummy"]
    broken, dummy = registered
    assert isinstance(broken, Unavailable) and broken.id == "tdrive_broken" and "create_tool failed on purpose" in broken.text
    assert dummy.id == "tdrive_dummy" and not isinstance(dummy, Unavailable)


def test_missing_create_tool_and_missing_module(tmp_path):
    pkg = tmp_path / "t" / "maya" / "scripts" / "tdrive_nocreate"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "t" / "tool.json").write_text(json.dumps({"id": "tdrive_nocreate", "name": "NoCreate", "entry": "tdrive_nocreate"}), encoding="utf-8")
    (tmp_path / "u").mkdir()
    (tmp_path / "u" / "tool.json").write_text(json.dumps({"id": "tdrive_nomod", "name": "NoMod", "entry": "tdrive_nomod.tool"}), encoding="utf-8")
    p = _tools_json(tmp_path, [{"id": "tdrive_nocreate", "dir": str(tmp_path / "t")}, {"id": "tdrive_nomod", "dir": str(tmp_path / "u")}])
    _, registered = _run(p)
    assert all(isinstance(t, Unavailable) for t in registered) and len(registered) == 2
    assert "create_tool" in registered[0].text


def test_id_mismatch_from_create_tool(tmp_path):
    pkg = tmp_path / "t" / "maya" / "scripts" / "tdrive_idmis"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("class T:\n    id = 'other'\n    label = 'x'\n\n\ndef create_tool():\n    return T()\n", encoding="utf-8")
    (tmp_path / "t" / "tool.json").write_text(json.dumps({"id": "tdrive_idmis", "name": "IdMis", "entry": "tdrive_idmis"}), encoding="utf-8")
    _, registered = _run(_tools_json(tmp_path, [{"id": "tdrive_idmis", "dir": str(tmp_path / "t")}]))
    assert len(registered) == 1 and isinstance(registered[0], Unavailable) and registered[0].id == "tdrive_idmis"


def test_skipped_tools_disabled_has_no_tab_others_do(tmp_path):
    p = _tools_json(tmp_path, [{"id": "tdrive_off", "dir": str(DUMMY), "enabled": False}, {"id": "tdrive_gone", "dir": str(tmp_path / "nothing")}])
    ids, registered = _run(p)
    assert ids == ["tdrive_gone"]
    assert len(registered) == 1 and isinstance(registered[0], Unavailable) and "checkout" in registered[0].text


def test_no_tools_json_is_quiet(tmp_path):
    ids, registered = _run(tmp_path / "tools.json")
    assert ids == [] and registered == []


def test_unload_removes_modules_and_sys_path(tmp_path):
    p = _tools_json(tmp_path, [{"id": "tdrive_dummy", "dir": str(DUMMY)}])
    _run(p)
    assert "tdrive_dummy.tool" in sys.modules
    external_load.unload()
    assert "tdrive_dummy" not in sys.modules and "tdrive_dummy.tool" not in sys.modules
    assert str(DUMMY / "maya" / "scripts") not in sys.path
    _, registered = _run(p)  # 再読み込みできる
    assert registered[0].id == "tdrive_dummy"
