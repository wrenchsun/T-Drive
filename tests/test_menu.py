"""tdrive.menu.install(): Tools メニューを再利用 / 作成し、古いトップメニューを消す（E-1）。maya.cmds はモック。"""

import importlib
import sys
import types
from unittest import mock


class FakeCmds:
    def __init__(self, menus=None, items=None, tools_label="Tools", has_tools=True):
        self.menus = dict(menus or {})  # name -> label
        self.items = set(items or [])
        self.deleted = []
        self.created = []  # (kind, name, kwargs)
        self.tools = "MyToolsMenu" if has_tools else None
        if has_tools:
            self.menus["MyToolsMenu"] = tools_label

    def window(self, main, query=True, menuArray=True):
        return list(self.menus)

    def menu(self, name, exists=False, query=False, label=None, **kw):
        if exists:
            return name in self.menus
        if query:
            return self.menus[name]
        self.menus[name] = label
        self.created.append(("menu", name, dict(kw, label=label)))
        return name

    def menuItem(self, name=None, exists=False, **kw):
        if exists:
            return name in self.items
        self.created.append(("menuItem", name, kw))
        return name

    def deleteUI(self, name):
        self.deleted.append(name)

    def setParent(self, *a, **kw):
        pass


def _load(fake):
    maya = types.ModuleType("maya")
    maya.cmds = fake
    maya.mel = types.SimpleNamespace(eval=lambda s: "MayaWindow")
    sys.modules.pop("tdrive.menu", None)
    with mock.patch.dict(sys.modules, {"maya": maya, "maya.cmds": fake, "maya.mel": maya.mel}):
        return importlib.import_module("tdrive.menu")


def _install(fake):
    mod = _load(fake)
    mod.install()
    sys.modules.pop("tdrive.menu", None)
    return mod


def test_reuses_existing_tools_menu():
    fake = FakeCmds()
    _install(fake)
    assert not [c for c in fake.created if c[0] == "menu"]  # Tools を作らない
    subs = [c for c in fake.created if c[0] == "menuItem" and c[2].get("subMenu") and c[2].get("label") == "T-Drive"]
    assert len(subs) == 1 and subs[0][1] == "TDriveMenu"
    assert "MyToolsMenu" not in fake.deleted


def test_creates_tools_menu_when_missing():
    fake = FakeCmds(has_tools=False)
    _install(fake)
    tops = [c for c in fake.created if c[0] == "menu"]
    assert len(tops) == 1 and tops[0][2]["label"] == "Tools"


def test_removes_old_top_level_menus():
    fake = FakeCmds(menus={"TDriveMenu": "T-Drive", "TDriveToonMenu": "T-Drive Toon"}, items={"TDriveMenu"})
    _install(fake)
    assert "TDriveToonMenu" in fake.deleted
    assert fake.deleted.count("TDriveMenu") == 2  # 古いトップメニュー + 前回のサブメニュー
    assert not [c for c in fake.created if c[0] == "menu"]  # Tools は既存を使う


def test_has_all_entries():
    fake = FakeCmds()
    _install(fake)
    labels = {c[2].get("label") for c in fake.created if c[0] == "menuItem"}
    for want in ("エディタ", "プロジェクトを選ぶ…", "Toon", "Toon 表示 ON/OFF", "ツールをリロード", "マニュアルを開く", "更新…", "MCP ポートを開く", "MCP ポートを閉じる"):
        assert want in labels
