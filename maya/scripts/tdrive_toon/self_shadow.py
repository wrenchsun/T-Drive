"""セルフシャドウ（T-43）の Maya プレビュー（4-11、docs/03 §2.3・docs/05 §6）。

VP2 のシャドウマップを使う:
- キャラクターライト（プレビューのライトの向き）と同じ向きの影用ライト tdPreviewShadowLight を置く（プレビュー専用。出力の対象外）
- プレビューのシェーダー（TDriveToon.fx）の Light 0 にそのライトを結び付ける（dx11Shader -connectLight）
- ビューポートを「全てのライト」+「影」にする（元の設定は覚えておき、オフにしたら戻す）

影の見た目（シャドウマップの解像度・ぼけ）は Unity と完全には一致しない（機能の Maya プレビューは △）。
"""

from __future__ import annotations

import json

from maya import cmds
import maya.api.OpenMaya as om

from .preview import PREVIEW_ONLY_ATTR, outside_maya_undo, preview_shaders

LIGHT = "tdPreviewShadowLight"
SAVED_ATTR = "tdSavedPanels"  # パネル → 元の表示設定（displayLights / shadows）。ツールのリロードでも消えないようノードに持つ


def _saved() -> dict:
    if not exists() or not cmds.attributeQuery(SAVED_ATTR, node=LIGHT, exists=True):
        return {}
    try:
        return json.loads(cmds.getAttr(f"{LIGHT}.{SAVED_ATTR}") or "{}")
    except ValueError:
        return {}


def _store(saved: dict) -> None:
    if exists():
        cmds.setAttr(f"{LIGHT}.{SAVED_ATTR}", json.dumps(saved), type="string")


def exists() -> bool:
    return cmds.objExists(LIGHT)


def _rotation_for(to_light) -> tuple[float, float, float]:
    """「表面 → 光源」方向から、ディレクショナルライト（-Z 方向へ照らす）の回転（度）。"""
    d = om.MVector(*to_light).normal()
    q = om.MVector(0.0, 0.0, 1.0).rotateTo(d)  # ライトの +Z を光源の方向へ = -Z が照らす向き
    e = q.asEulerRotation()
    return tuple(om.MAngle(a).asDegrees() for a in (e.x, e.y, e.z))


@outside_maya_undo
def ensure() -> str:
    if exists():
        return LIGHT
    shape = cmds.directionalLight(name=f"{LIGHT}Shape", intensity=1.0)
    xf = cmds.listRelatives(shape, parent=True)[0]
    xf = cmds.rename(xf, LIGHT)
    shape = cmds.listRelatives(xf, shapes=True)[0]
    cmds.setAttr(f"{shape}.useDepthMapShadows", True)
    cmds.setAttr(f"{shape}.emitSpecular", False)
    cmds.addAttr(xf, longName=PREVIEW_ONLY_ATTR, attributeType="bool", defaultValue=True)
    cmds.addAttr(xf, longName=SAVED_ATTR, dataType="string")
    cmds.setAttr(f"{xf}.hiddenInOutliner", True)
    cmds.setAttr(f"{xf}.overrideEnabled", True)
    cmds.setAttr(f"{xf}.overrideDisplayType", 2)  # 選べない（誤って動かさない）
    return LIGHT


@outside_maya_undo
def set_direction(to_light) -> None:
    """キャラクターライトの向きに合わせる（プレビューのライトを動かすたびに呼ばれる）。"""
    if exists():
        cmds.setAttr(f"{LIGHT}.rotate", *_rotation_for(to_light))


def connect_shaders() -> None:
    """プレビューの全シェーダーの Light 0 に影用ライトを結び付ける。"""
    if not exists():
        return
    shape = cmds.listRelatives(LIGHT, shapes=True, fullPath=True)[0]
    for sh in preview_shaders():
        try:
            cmds.dx11Shader(sh, connectLight=("Light 0", shape))  # -edit は無い（dx11Shader -connectLight "Light 0" <ライト> <シェーダー>）
        except (RuntimeError, TypeError):
            pass  # .fx が読めていない（mayapy など）


def _apply_panel(panel: str, on: bool) -> None:
    saved = _saved()
    if on:
        if panel not in saved:
            saved[panel] = {
                "displayLights": cmds.modelEditor(panel, query=True, displayLights=True),
                "shadows": cmds.modelEditor(panel, query=True, shadows=True),
            }
            _store(saved)
        cmds.modelEditor(panel, edit=True, displayLights="all", shadows=True)
    elif panel in saved:
        prev = saved.pop(panel)
        _store(saved)
        if cmds.modelPanel(panel, exists=True):
            cmds.modelEditor(panel, edit=True, displayLights=prev["displayLights"], shadows=prev["shadows"])


@outside_maya_undo
def update(panel: str | None, on: bool, to_light) -> None:
    """機能 selfShadow がオンで Toon 表示のとき on=True。影用ライトを置いて結び付け、パネルの表示を切り替える。"""
    if on:
        ensure()
        set_direction(to_light)
        connect_shaders()
        cmds.setAttr(f"{LIGHT}.visibility", True)
        if panel:
            _apply_panel(panel, True)
    else:
        for p in list(_saved()):
            _apply_panel(p, False)
        if exists():
            cmds.setAttr(f"{LIGHT}.visibility", False)  # 消すと他のライトの結び付きがずれるので隠すだけ


@outside_maya_undo
def delete() -> None:
    for p in list(_saved()):
        _apply_panel(p, False)
    if exists():
        cmds.delete(LIGHT)
