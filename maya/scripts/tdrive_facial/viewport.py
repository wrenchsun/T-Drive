"""どのビューポート（モデルパネル）のカメラを動かす・読むか（FacialController のカメラの使い手は全部ここを通る）。

シェイプエディタ・アウトライナ・スクリプトエディタ・このツール自身にフォーカスがあるとき、「フォーカス中のパネル」はモデルパネルではない。
以前は「最初のモデルパネル」へ落ちていて、4 分割の隠れたパネル（top など）のカメラを動かしてしまい、見えているビューが動かなかった（2026-10-05）。

決め方（上から。見えていないパネルは、見えているパネルがあるあいだ選ばない）:
 1. フォーカス中のパネルがモデルパネルならそれ（覚えておく）
 2. 最後にフォーカスがあったモデルパネル（まだあって、見えているもの）
 3. 見えているモデルパネルの先頭（パースカメラのものを優先）
 4. 全モデルパネルの先頭（パースカメラのものを優先）
Maya を介さずに試せるよう、パネルの問い合わせは `api`（既定は `MayaApi`）に分けてある。
"""

from __future__ import annotations

import contextlib
import time
from typing import Optional

from maya import cmds

_last_panel: Optional[str] = None  # 最後にフォーカスがあったモデルパネル
_last_camera = ""  # 直近に決めたカメラの短い名前（グリッドタブの「カメラ: persp」）
_last_redraw = 0.0
REDRAW_INTERVAL = 0.04  # 秒。ドラッグ中の描き直しの間引き


class MayaApi:
    """パネルの問い合わせ（Maya の cmds）。テストでは同じ形のものに差し替える。"""

    def with_focus(self) -> str:
        return cmds.getPanel(withFocus=True) or ""

    def type_of(self, panel: str) -> str:
        return cmds.getPanel(typeOf=panel) or ""

    def model_panels(self) -> list[str]:
        return list(cmds.getPanel(type="modelPanel") or [])

    def visible_panels(self) -> list[str]:
        return list(cmds.getPanel(visiblePanels=True) or [])

    def exists(self, panel: str) -> bool:
        return bool(cmds.modelPanel(panel, query=True, exists=True))

    def is_perspective(self, panel: str) -> bool:
        """そのパネルのカメラがパース（正投影でない）か。取れなければ False。"""
        try:
            cam = cmds.modelPanel(panel, query=True, camera=True)
            return bool(cam) and not cmds.camera(cam, query=True, orthographic=True)
        except RuntimeError:
            return False


def pick_panel(api=None) -> Optional[str]:
    """動かす・読むモデルパネル（上の決め方）。モデルパネルが 1 つも無ければ None。"""
    global _last_panel
    api = api or MayaApi()
    try:
        models = api.model_panels()
        if not models:
            return None
        visible = [p for p in api.visible_panels() if p in models]
        pool = visible or models  # 見えているものがあれば、見えていないものは選ばない
        focus = api.with_focus()
        if focus and focus in pool and api.type_of(focus) == "modelPanel":
            _last_panel = focus
            return focus
        if _last_panel and _last_panel in pool and api.exists(_last_panel):
            return _last_panel
        for p in pool:
            if api.is_perspective(p):
                return p
        return pool[0]
    except RuntimeError:
        return None


def note_active(api=None) -> None:
    """ツールの操作のたびに呼んでよい: フォーカスがモデルパネルにあれば「最後のパネル」として覚える。"""
    pick_panel(api)


def forget() -> None:
    """記録を捨てる（新しいシーン・リロード）。"""
    global _last_panel, _last_camera
    _last_panel = None
    _last_camera = ""


def panel_camera(panel: Optional[str]) -> Optional[str]:
    """パネルのカメラの transform（長い名前）。取れなければ None。"""
    if not panel:
        return None
    try:
        cam = cmds.modelPanel(panel, query=True, camera=True)
    except RuntimeError:
        return None
    if not cam:
        return None
    found = cmds.ls(cam, long=True) or []
    if not found:
        return None
    node = found[0]
    if cmds.nodeType(node) == "camera":
        node = (cmds.listRelatives(node, parent=True, fullPath=True) or [node])[0]
    return node


def resolve_camera(api=None) -> Optional[str]:
    """今のビューのカメラの transform（長い名前）。パネルが無い・取れなければ None（呼ぶ側が persp にする）。"""
    global _last_camera
    node = panel_camera(pick_panel(api))
    if node:
        _last_camera = node.split("|")[-1]
    return node


def remember_camera(node: str) -> None:
    """パネルから決められなかったとき（persp へ落ちたとき）も、読み出し用の名前を合わせる。"""
    global _last_camera
    _last_camera = node.split("|")[-1]


def last_camera_name() -> str:
    """直近に決めたカメラの短い名前（表示用）。まだ無ければ空。"""
    return _last_camera


def redraw(force: bool = False) -> None:
    """カメラをプログラムから動かしたあと、別のパネルにフォーカスがあっても見えているビューを描き直す（フォーカスは奪わない）。
    ドラッグ中は REDRAW_INTERVAL より速い呼び出しを間引く。batch では何もしない。"""
    global _last_redraw
    now = time.monotonic()
    if not force and now - _last_redraw < REDRAW_INTERVAL:
        return
    _last_redraw = now
    with contextlib.suppress(RuntimeError):
        if cmds.about(batch=True):
            return
        cmds.refresh()  # 変更のあるところだけ描き直す（force は付けない = 軽い）
