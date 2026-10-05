"""どのビューポートのカメラを動かすか（tdrive_facial/viewport.py）の決め方。Maya なし（パネルの問い合わせを偽物に差し替える）。

2026-10-05 の不具合: シェイプエディタにフォーカスがあると「最初のモデルパネル」（4 分割の隠れた top など）のカメラを動かしていた。
"""

import sys
import types

import pytest

_added = []
try:
    import maya.cmds  # noqa: F401
except ImportError:  # Maya なしで viewport を読み込めるように、読み込む間だけ偽の maya を置く（pick_panel は cmds を使わない）
    fake = types.ModuleType("maya")
    fake.cmds = types.ModuleType("maya.cmds")
    sys.modules["maya"], sys.modules["maya.cmds"] = fake, fake.cmds
    _added = ["maya", "maya.cmds"]
try:
    from tdrive_facial import viewport as V
finally:
    for _n in _added:  # 他のテスト（core が maya を読まない確認）に漏らさない
        sys.modules.pop(_n, None)


class FakeApi:
    def __init__(self, models, visible, focus="", types=None, persp=None, gone=()):
        self.models, self.visible, self.focus = list(models), list(visible), focus
        self.types = types or {}
        self.persp = persp if persp is not None else set(models)
        self.gone = set(gone)

    def with_focus(self):
        return self.focus

    def type_of(self, p):
        return self.types.get(p, "modelPanel" if p in self.models else "scriptedPanel")

    def model_panels(self):
        return self.models

    def visible_panels(self):
        return self.visible

    def exists(self, p):
        return p not in self.gone

    def is_perspective(self, p):
        return p in self.persp


@pytest.fixture(autouse=True)
def _reset():
    V.forget()
    yield
    V.forget()


# Maya の既定: 4 分割用の modelPanel1〜3（top / side / front）は隠れ、見えているのは modelPanel4（persp）
FOUR = ["modelPanel1", "modelPanel2", "modelPanel3", "modelPanel4"]


def test_non_model_focus_picks_visible_panel_not_first_hidden():
    api = FakeApi(FOUR, ["modelPanel4", "scriptedPanel1"], focus="scriptedPanel1", persp={"modelPanel4"})
    assert V.pick_panel(api) == "modelPanel4"  # 以前は modelPanel1（隠れた top）を選んでいた


def test_focus_on_model_panel_wins_and_is_remembered():
    api = FakeApi(FOUR, ["modelPanel2", "modelPanel4"], focus="modelPanel2")
    assert V.pick_panel(api) == "modelPanel2"
    api.focus = "outlinerPanel1"  # あとでアウトライナにフォーカスが移っても、最後のモデルパネル
    assert V.pick_panel(api) == "modelPanel2"
    api.focus = ""
    assert V.pick_panel(api) == "modelPanel2"


def test_last_panel_ignored_when_it_became_hidden_or_gone():
    api = FakeApi(FOUR, ["modelPanel2", "modelPanel4"], focus="modelPanel2")
    V.pick_panel(api)
    api.focus, api.visible = "scriptedPanel1", ["modelPanel4"]  # 2 が隠れた（レイアウトを変えた）
    assert V.pick_panel(api) == "modelPanel4"
    V.forget()
    api2 = FakeApi(FOUR, ["modelPanel3", "modelPanel4"], focus="modelPanel3", persp={"modelPanel4"})
    V.pick_panel(api2)
    api2.focus, api2.gone = "", {"modelPanel3"}
    assert V.pick_panel(api2) == "modelPanel4"


def test_visible_prefers_perspective():
    api = FakeApi(FOUR, ["modelPanel1", "modelPanel4"], focus="", persp={"modelPanel4"})
    assert V.pick_panel(api) == "modelPanel4"
    api = FakeApi(FOUR, ["modelPanel1", "modelPanel2"], focus="", persp=set())  # パースが無ければ見えているものの先頭
    assert V.pick_panel(api) == "modelPanel1"


def test_focused_hidden_model_panel_is_not_used_when_a_visible_one_exists():
    api = FakeApi(FOUR, ["modelPanel4"], focus="modelPanel1")
    assert V.pick_panel(api) == "modelPanel4"


def test_nothing_visible_falls_back_to_any_model_panel():
    api = FakeApi(FOUR, [], focus="", persp={"modelPanel3", "modelPanel4"})
    assert V.pick_panel(api) == "modelPanel3"


def test_no_model_panels_returns_none():
    assert V.pick_panel(FakeApi([], [], focus="scriptedPanel1")) is None


def test_note_active_remembers_focus():
    api = FakeApi(FOUR, ["modelPanel1", "modelPanel4"], focus="modelPanel1")
    V.note_active(api)
    api.focus = "scriptedPanel2"
    assert V.pick_panel(api) == "modelPanel1"
