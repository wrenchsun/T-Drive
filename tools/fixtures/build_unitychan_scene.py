"""テスト素体シーンの生成（mayapy で実行）: assets/unitychan/unitychan_test.ma

UnityChan 固有の事情はこのスクリプトに閉じ込め、ツール本体（maya/scripts/tdrive_toon）には持ち込まない。
ツールが前提にするのは「標準の Maya マテリアルが割り当てられたメッシュ」だけ。

UnityChan 固有の事情:
- FBX のマテリアルが CgFX 製で Maya 2026 では unknown ノードになり、メッシュとの接続が失われる
  → D-Drive の unitychan.prefab のレンダラー → マテリアル対応（下の MESH_TO_MATERIAL）で lambert を作り直す
- BlendShape のターゲットが非表示メッシュとして入る（マテリアル未割り当てなのでツールの対象外になる）

実行: "C:/Program Files/Autodesk/Maya2026/bin/mayapy.exe" tools/fixtures/build_unitychan_scene.py
"""

from __future__ import annotations

from pathlib import Path

import maya.standalone

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "assets" / "unitychan"
OUT = SRC / "unitychan_test.ma"

# D-Drive Assets/SourceAssets/Data/UnityChan/Prefabs/unitychan.prefab のレンダラー → マテリアル（2026-09-28 時点）
MESH_TO_MATERIAL = {
    "BLW_DEF": "eyeline", "EL_DEF": "eyeline",
    "EYE_DEF": "face", "MTH_DEF": "face", "head_back": "face",
    "eye_base_old": "eyebase", "eye_L_old": "eye_L1", "eye_R_old": "eye_R1",
    "cheek": "mat_cheek",
    "skin": "skin1",
    "hair_front": "hair", "hair_frontside": "hair", "tail": "hair", "tail_bottom": "hair",
    "hair_accce": "body", "hairband": "body", "button": "body", "Leg": "body",
    "Shirts": "body", "shirts_sode": "body", "shirts_sode_BK": "body", "uwagi": "body", "uwagi_BK": "body",
}
# マテリアル → ベースカラーテクスチャ（D-Drive の .mat の _BaseMap と同じ）
MATERIAL_TEXTURE = {
    "face": "face_00.tga", "eyeline": "eyeline_00.tga", "eyebase": "face_00.tga",
    "eye_L1": "eye_iris_L_00.tga", "eye_R1": "eye_iris_R_00.tga", "mat_cheek": "cheek_00.tga",
    "skin1": "skin_01.tga", "hair": "hair_01.tga", "body": "body_01.tga",
}


def build() -> None:
    from maya import cmds

    cmds.file(new=True, force=True)
    cmds.loadPlugin("fbxmaya", quiet=True)
    cmds.file(str(SRC / "unitychan.fbx"), i=True, type="FBX", ignoreVersion=True, mergeNamespacesOnClash=False)

    # CgFX の残骸（unknown ノードと空の SG）を消す
    for n in cmds.ls(type="unknown"):
        cmds.lockNode(n, lock=False)
        cmds.delete(n)

    mats = {}
    for mat, tex in MATERIAL_TEXTURE.items():
        m = cmds.shadingNode("lambert", asShader=True, name=mat)
        cmds.setAttr(f"{m}.diffuse", 1.0)
        f = cmds.shadingNode("file", asTexture=True, isColorManaged=True, name=f"{mat}_tex")
        # $TDRIVE_ROOT は TDriveToon.mod が定義する。どの PC でも同じシーンが開ける
        cmds.setAttr(f"{f}.fileTextureName", f"$TDRIVE_ROOT/assets/unitychan/textures/{tex}", type="string")
        cmds.connectAttr(f"{f}.outColor", f"{m}.color")
        if mat in ("eyeline", "mat_cheek", "eye_L1", "eye_R1"):
            cmds.connectAttr(f"{f}.outTransparency", f"{m}.transparency")
        sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name=f"{mat}SG")
        cmds.connectAttr(f"{m}.outColor", f"{sg}.surfaceShader")
        mats[mat] = sg

    for mesh, mat in MESH_TO_MATERIAL.items():
        found = cmds.ls(mesh, long=True, type="transform")
        if not found:
            raise RuntimeError(f"メッシュが見つからない: {mesh}")
        # transform に割り当てると子孫（EYE_DEF の子の EL_DEF 等）まで上書きされるので、直下のシェイプにだけ割り当てる
        shapes = cmds.listRelatives(found[0], shapes=True, noIntermediate=True, fullPath=True) or []
        cmds.sets(shapes, edit=True, forceElement=mats[mat])

    for sg in cmds.ls(type="shadingEngine"):
        if sg not in mats.values() and sg not in ("initialShadingGroup", "initialParticleSE") and not cmds.sets(sg, q=True):
            cmds.delete(sg)

    cmds.file(rename=str(OUT))
    cmds.file(save=True, type="mayaAscii", force=True)
    print(f"saved: {OUT}")


if __name__ == "__main__":
    maya.standalone.initialize(name="python")
    try:
        build()
    finally:
        maya.standalone.uninitialize()
