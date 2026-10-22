"""アニメーション作業台のスモーク用の小さなリグ（ジョイント 6 本 + 立方体スキン）。MisotenChara 本体はリポジトリに入れない。

mayapy から import して使う（`build()`）。単独でも実行できる:

  "C:/Program Files/Autodesk/Maya2026/bin/mayapy.exe" tests/fixtures/anim/make_mini_rig.py <出力.ma>

名前は MisotenChara の mGear に合わせる（`<部位>_<側><番号>_<n>_jnt`、ルート `spine_C0_0_jnt`）。
  rig
    jnt_org > spine_C0_0_jnt > spine_C0_1_jnt > spine_C0_2_jnt > spine_C0_3_jnt > arm_L0_0_jnt / arm_R0_0_jnt
    miso_geo > MisotenChara_Rig5_IdolGirl_Body_Chest（立方体。全ジョイントへスキン）
mGear の読み込み時の接頭辞つきの名前を再現しているので、命名タブの検査（SK_<ModelId>_<部位>）の対象になる。
"""

from __future__ import annotations

import sys

JOINTS = ["spine_C0_0_jnt", "spine_C0_1_jnt", "spine_C0_2_jnt", "spine_C0_3_jnt", "arm_L0_0_jnt", "arm_R0_0_jnt"]
MESH = "MisotenChara_Rig5_IdolGirl_Body_Chest"
ROOT = "spine_C0_0_jnt"


def build(fps_unit: str = "film") -> dict:
    """新しいシーンに組む。時間単位は MisotenChara と同じ film（24 fps）で始める（命名タブの修正対象）。"""
    from maya import cmds

    cmds.file(new=True, force=True)
    cmds.currentUnit(time=fps_unit)
    cmds.group(empty=True, name="rig")
    cmds.group(empty=True, name="jnt_org", parent="rig")
    cmds.group(empty=True, name="miso_geo", parent="rig")
    cmds.select(clear=True)
    j0 = cmds.joint(name=JOINTS[0], position=(0, 90, 0))
    prev = j0
    for name, y in zip(JOINTS[1:4], (110, 130, 150)):
        prev = cmds.joint(name=name, position=(0, y, 0))
    top = prev
    cmds.select(top)
    cmds.joint(name=JOINTS[4], position=(15, 150, 0))
    cmds.select(top)
    cmds.joint(name=JOINTS[5], position=(-15, 150, 0))
    cmds.parent(j0, "jnt_org")
    cube = cmds.polyCube(name=MESH, width=30, height=70, depth=20)[0]
    cmds.move(0, 120, 0, cube)
    cmds.makeIdentity(cube, apply=True, translate=True)
    cmds.delete(cube, constructionHistory=True)
    cmds.parent(cube, "miso_geo")
    cmds.skinCluster(JOINTS, cube, toSelectedBones=True, name="skinCluster_body")
    cmds.select(clear=True)
    return {"root": ROOT, "joints": list(JOINTS), "mesh": cube}


def add_materials(mesh: str = MESH) -> dict:
    """MisotenChara 風の素材名をメッシュに付ける（命名タブのスモーク用）。

    Hair（面 0〜2）と IdolGirl_Other2（面 3〜5）、割り当てなしの Unused、空のネームスペース ffffff、
    Hair のプレビュー用シェーダーの代役 Hair_tdToon（dx11 ではなく lambert に tdSourceMaterial を付けたもの）。
    """
    from maya import cmds

    out = {}
    for mat, faces in (("Hair", "0:2"), ("IdolGirl_Other2", "3:5")):
        shader = cmds.shadingNode("lambert", asShader=True, name=mat)
        sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name=mat + "SG")
        cmds.connectAttr(shader + ".outColor", sg + ".surfaceShader", force=True)
        cmds.sets(f"{mesh}.f[{faces}]", edit=True, forceElement=sg)
        out[mat] = sg
    unused = cmds.shadingNode("lambert", asShader=True, name="Unused")
    usg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name="UnusedSG")
    cmds.connectAttr(unused + ".outColor", usg + ".surfaceShader", force=True)
    cmds.namespace(add="ffffff")
    fake = cmds.shadingNode("lambert", asShader=True, name="Hair_tdToon")
    cmds.addAttr(fake, longName="tdSourceMaterial", dataType="string")
    cmds.setAttr(fake + ".tdSourceMaterial", "Hair", type="string")
    return out


def key_motion(start: int = 0, end: int = 30, amount: float = 20.0) -> None:
    """ルートを X 方向に動かし、腕を回すキーを打つ（ベイク・ブレンドの確認用）。"""
    from maya import cmds

    for f, t in ((start, 0.0), (end, 1.0)):
        cmds.setKeyframe(ROOT, attribute="translateX", time=f, value=amount * t)
        cmds.setKeyframe("arm_L0_0_jnt", attribute="rotateZ", time=f, value=60.0 * t)
        cmds.setKeyframe("spine_C0_1_jnt", attribute="rotateX", time=f, value=-20.0 * t)


if __name__ == "__main__":
    import os

    import maya.standalone

    maya.standalone.initialize(name="python")
    try:
        from maya import cmds

        build()
        if len(sys.argv) > 1:
            cmds.file(rename=sys.argv[1])
            cmds.file(save=True, type="mayaAscii")
    finally:
        maya.standalone.uninitialize()
        os._exit(0)
