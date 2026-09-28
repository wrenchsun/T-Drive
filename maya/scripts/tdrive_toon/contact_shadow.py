"""接地影（T-29）の Maya プレビュー（4-6）。

足元の地面に板（プレビュー専用）を置き、TDriveContactShadow.fx で足ごとの影を描く。式は ToonCore の Toon_ContactShadow。
- 足の位置は、登録メッシュのスキンのインフルエンスから名前で探したジョイント（左右の Foot / Ankle）に接続する。
  アニメーションで足が動くと影も動く。見つからないときはメッシュの足元中央に 1 つ
- 板は Look にも Unity 出力（FBX）にも入らない（PREVIEW_ONLY_ATTR で部位登録の対象からも外す）
"""

from __future__ import annotations

import re

from maya import cmds

from . import REPO_ROOT, environment
from .preview import PREVIEW_ONLY_ATTR, outside_maya_undo

NODE = "tdContactShadow"
SHADER = "tdContactShadow_fx"
SHADER_FILE = (REPO_ROOT / "maya" / "shaders" / "TDriveContactShadow.fx").as_posix()
FOOT_PATTERNS = {
    "L": (r"(?i)left.*(foot|ankle)", r"(?i)(foot|ankle).*[_.]l$", r"(?i)^l[_.].*(foot|ankle)"),
    "R": (r"(?i)right.*(foot|ankle)", r"(?i)(foot|ankle).*[_.]r$", r"(?i)^r[_.].*(foot|ankle)"),
}


def exists() -> bool:
    return cmds.objExists(NODE)


def find_feet(meshes: list[str]) -> dict[str, str]:
    """メッシュのスキンのインフルエンスから左右の足のジョイント（完全パス）を探す。"""
    joints: set[str] = set()
    for m in meshes:
        for sc in cmds.ls(cmds.listHistory(m, pruneDagObjects=True) or [], type="skinCluster") or []:
            joints.update(cmds.ls(cmds.skinCluster(sc, query=True, influence=True) or [], long=True, type="joint"))
    feet = {}
    for side, patterns in FOOT_PATTERNS.items():
        for pat in patterns:
            hits = [j for j in joints if re.search(pat, j.rsplit("|", 1)[-1].split(":")[-1])]
            if hits:
                feet[side] = min(hits, key=lambda j: j.count("|"))  # 足首に近い（階層が浅い）方。つま先より足
                break
    return feet


@outside_maya_undo
def update(meshes: list[str], settings: dict, visible: bool) -> None:
    """設定（characterSettings.contactShadow、m / 0–1）どおりに板を置く・更新する。visible=False なら隠す。"""
    on = visible and bool(settings.get("enabled")) and bool(meshes)
    if not on:
        if exists():
            cmds.setAttr(f"{NODE}.visibility", False)
        return
    scale = environment.units_per_meter()
    radius = float(settings["radius"]) * scale
    b = cmds.exactWorldBoundingBox(meshes)
    ground = b[1]
    size_x, size_z = (b[3] - b[0]) + radius * 4, (b[5] - b[2]) + radius * 4
    center = ((b[0] + b[3]) / 2, ground, (b[2] + b[5]) / 2)
    _ensure()
    cmds.setAttr(f"{NODE}.visibility", True)
    cmds.xform(NODE, worldSpace=True, translation=center)
    cmds.setAttr(f"{NODE}.scale", size_x, 1.0, size_z)
    _set_attr("ContactRadius", radius)
    _set_attr("ContactStrength", float(settings["strength"]))
    feet = find_feet(meshes)
    for side in ("L", "R"):
        attr = f"{SHADER}.ContactFoot{side}"
        if not _has(f"ContactFoot{side}"):
            continue  # .fx が読めない環境（mayapy など）
        for src in cmds.listConnections(attr, source=True, destination=False, plugs=True) or []:
            cmds.disconnectAttr(src, attr)
        joint = feet.get(side) or feet.get("R" if side == "L" else "L")
        if joint:
            dm = _decompose(side)
            cmds.connectAttr(f"{joint}.worldMatrix[0]", f"{dm}.inputMatrix", force=True)
            cmds.connectAttr(f"{dm}.outputTranslate", attr, force=True)
        else:
            cmds.setAttr(attr, center[0], ground, center[2])  # 足が見つからない: 足元中央に 1 つ


@outside_maya_undo
def delete() -> None:
    nodes = [NODE, SHADER, f"{SHADER}SG", _decompose_name("L"), _decompose_name("R")]
    cmds.delete([n for n in nodes if cmds.objExists(n)])


def _ensure() -> None:
    if exists() and cmds.objExists(SHADER):
        return
    delete()
    plane = cmds.polyPlane(name=NODE, width=1, height=1, subdivisionsX=1, subdivisionsY=1, constructionHistory=False)[0]
    cmds.addAttr(plane, longName=PREVIEW_ONLY_ATTR, attributeType="bool", defaultValue=True)
    for attr in ("castsShadows", "receiveShadows", "primaryVisibility"):
        shape = cmds.listRelatives(plane, shapes=True, fullPath=True)[0]
        cmds.setAttr(f"{shape}.{attr}", attr == "primaryVisibility")
    cmds.setAttr(f"{plane}.overrideEnabled", True)
    cmds.setAttr(f"{plane}.overrideDisplayType", 2)  # Reference: ビューポートで選択できない（誤って動かさない）
    if not cmds.pluginInfo("dx11Shader", query=True, loaded=True):
        cmds.loadPlugin("dx11Shader", quiet=True)
    shader = cmds.shadingNode("dx11Shader", asShader=True, name=SHADER)
    cmds.setAttr(f"{shader}.shader", SHADER_FILE, type="string")
    sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name=f"{SHADER}SG")
    cmds.connectAttr(f"{shader}.outColor", f"{sg}.surfaceShader", force=True)
    cmds.sets(plane, edit=True, forceElement=sg)


def _has(attr: str) -> bool:
    return cmds.objExists(SHADER) and cmds.attributeQuery(attr, node=SHADER, exists=True)


def _set_attr(attr: str, value: float) -> None:
    if _has(attr):
        cmds.setAttr(f"{SHADER}.{attr}", value)


def _decompose_name(side: str) -> str:
    return f"tdContactShadow_foot{side}_dm"


def _decompose(side: str) -> str:
    name = _decompose_name(side)
    if not cmds.objExists(name):
        if not cmds.pluginInfo("matrixNodes", query=True, loaded=True):
            cmds.loadPlugin("matrixNodes", quiet=True)
        name = cmds.createNode("decomposeMatrix", name=name, skipSelect=True)
    return name
