"""FacialController のスモーク用の合成モデル（外部ファイルなし。mayapy から import して使う）。

`build_mini_head()` が作るもの:
- `mini_face`（球。頂点 302）を root → head → eye_L / eye_R のジョイントへスキン（目は小さな範囲だけ重み付け）
- blendShape ノード `bs`（スキンより前）に 4 本: mouth_open / smile_L / smile_R / brow_up（頂点のグループをずらして作る）
- FBX 取り込みを真似た、最上位の非表示メッシュ `bs_target_mouth_open`（blendShape につながったままのターゲットメッシュ）
- `mini_brow`（まつ毛・眉を想定した別メッシュ。head と eye_L に軽くスキン。blendShape なし → ベイクで `tdFacial_mini_brow` ができる）

`make_doc()` が作る Document は Maya の系（cm / Y-up / 右手）、3 × 3 の格子、キー数点と Joy レイヤー。
"""

from __future__ import annotations

import math

from maya import cmds

EYE_L = (2.8, 12.96, 7.4)  # 球の表面（中心 (0, 12, 0)、半径 8）
EYE_R = (-2.8, 12.96, 7.4)
EYE_RADIUS = 2.5


def _vertex_positions(mesh: str) -> list[tuple[float, float, float]]:
    flat = cmds.xform(f"{mesh}.vtx[*]", query=True, worldSpace=True, translation=True)
    return [tuple(flat[i : i + 3]) for i in range(0, len(flat), 3)]


def _make_target(base: str, name: str, select, offset) -> str:
    """base を複製し、select(位置) が True の頂点を offset だけずらしたメッシュを作る。"""
    dup = cmds.duplicate(base, name=name)[0]
    ids = [i for i, p in enumerate(_vertex_positions(base)) if select(p)]
    if ids:
        cmds.move(*offset, [f"{dup}.vtx[{i}]" for i in ids], relative=True, worldSpace=True)
    return dup


def build_mini_head() -> dict:
    """新しいシーンを作って合成の頭を組み、名前の辞書を返す。"""
    cmds.file(new=True, force=True)
    cmds.undoInfo(state=True)
    cmds.select(clear=True)
    root = cmds.joint(name="root", position=(0, 0, 0))
    head = cmds.joint(name="head", position=(0, 10, 0))
    eye_l = cmds.joint(name="eye_L", position=EYE_L)
    cmds.select(head)
    eye_r = cmds.joint(name="eye_R", position=EYE_R)
    cmds.setAttr(f"{eye_l}.rotateOrder", 1)  # yzx（回転順の分解も確かめる）
    cmds.setAttr(f"{eye_l}.jointOrient", 0, 30, 10)  # 親でなく自分の向き（jointOrient の分解も確かめる）
    cmds.select(clear=True)

    face = cmds.polySphere(name="mini_face", radius=8, subdivisionsX=20, subdivisionsY=16, axis=(0, 1, 0))[0]
    cmds.move(0, 12, 0, face, absolute=True)
    cmds.makeIdentity(face, apply=True, translate=True)
    skin = cmds.skinCluster(root, head, eye_l, eye_r, face, toSelectedBones=True, maximumInfluences=3, name="mini_skin")[0]
    cmds.skinPercent(skin, face, transformValue=[(head, 1.0)])
    for i, p in enumerate(_vertex_positions(face)):
        for eye, pos in ((eye_l, EYE_L), (eye_r, EYE_R)):
            d = math.dist(p, pos)
            if d < EYE_RADIUS:
                w = 1.0 - d / EYE_RADIUS
                cmds.skinPercent(skin, f"{face}.vtx[{i}]", transformValue=[(head, 1.0 - w), (eye, w)])

    def front(p):  # 顔の前面
        return p[2] > 3.0

    t_mouth = _make_target(face, "bs_target_mouth_open", lambda p: front(p) and 8.0 < p[1] < 10.5 and abs(p[0]) < 3.5, (0, -1.5, 0.3))
    t_sl = _make_target(face, "bs_target_smile_L", lambda p: front(p) and 8.5 < p[1] < 11.5 and p[0] > 1.5, (0.5, 0.8, 0))
    t_sr = _make_target(face, "bs_target_smile_R", lambda p: front(p) and 8.5 < p[1] < 11.5 and p[0] < -1.5, (-0.5, 0.8, 0))
    t_brow = _make_target(face, "bs_target_brow_up", lambda p: front(p) and p[1] > 15.0, (0, 1.0, 0))
    bs = cmds.blendShape(t_mouth, t_sl, t_sr, t_brow, face, name="bs", frontOfChain=True)[0]
    for alias, i in (("mouth_open", 0), ("smile_L", 1), ("smile_R", 2), ("brow_up", 3)):
        cmds.aliasAttr(alias, f"{bs}.weight[{i}]")
    # FBX 取り込みのように、ターゲットメッシュは最上位に残し非表示にする（1 本だけ残し、他は消す。差分は blendShape が持つ）
    cmds.delete(t_sl, t_sr, t_brow)
    cmds.setAttr(f"{t_mouth}.visibility", 0)

    brow = cmds.polyPlane(name="mini_brow", width=6, height=2, subdivisionsX=8, subdivisionsY=2, axis=(0, 0, 1))[0]
    cmds.move(2.8, 15.5, 8.2, brow, absolute=True)
    cmds.makeIdentity(brow, apply=True, translate=True)
    cmds.skinCluster(head, eye_l, brow, toSelectedBones=True, maximumInfluences=2, name="mini_brow_skin")

    cmds.select(clear=True)
    return {"face": face, "brow": brow, "bs": bs, "joints": [root, head, eye_l, eye_r], "hidden_target": t_mouth, "skin": skin}


def _q_axis(axis: tuple[float, float, float], deg: float) -> tuple[float, float, float, float]:
    h = math.radians(deg) / 2
    n = math.sqrt(sum(a * a for a in axis))
    s = math.sin(h) / n
    return (axis[0] * s, axis[1] * s, axis[2] * s, math.cos(h))


def make_doc():
    """合成の頭用の Document（Maya の系）。キー: (1,2) 口とえくぼ / (1,0) 反対側 / (0,2) 目のボーン + 口 / (2,1) 目 R の移動。Joy は 2 点。"""
    from tdrive_facial.core.model import (
        Bake,
        BoneOffset,
        Document,
        GridPoint,
        Layer,
        Meta,
        SourcePose,
        Target,
    )

    doc = Document(meta=Meta(unit="cm", up_axis="Y", handedness="right", source="smoke"))
    doc.asset = "mini"
    doc.target = Target(mesh="mini_face")
    doc.bake = Bake(delta_threshold=0.001, differential=True)
    doc.grid.rows = 3
    doc.grid.cols = 3
    doc.grid.base_bone = "head"
    doc.grid.forward_axis = "+Z"
    doc.mirror.bone_axis = "X"

    def key(r, c, curves=None, bones=None):
        return GridPoint(r, c, True, SourcePose(dict(curves or {}), dict(bones or {})))

    neutral = doc.layers[0]
    for pt in (
        key(1, 2, {"bs.mouth_open": 0.6, "bs.smile_L": 0.8}),
        key(1, 0, {"bs.smile_R": 1.0, "bs.brow_up": 0.5}),
        key(0, 2, {"bs.mouth_open": 0.3}, {"eye_L": BoneOffset(t=(0.0, 0.0, 0.0), r=_q_axis((0, 1, 0), 20.0))}),
        key(2, 1, None, {"eye_R": BoneOffset(t=(0.2, 0.0, 0.1), r=_q_axis((1, 0, 0), -15.0))}),
    ):
        neutral.points[(pt.row, pt.col)] = pt
    joy = Layer(name="Joy", emotion_curve="Joy")
    for pt in (
        key(1, 2, {"bs.smile_L": 1.0, "bs.smile_R": 1.0, "bs.brow_up": 0.7}),
        key(0, 2, {"bs.smile_L": 1.0}, {"eye_L": BoneOffset(r=_q_axis((0, 1, 0), 10.0))}),
    ):
        joy.points[(pt.row, pt.col)] = pt
    doc.layers.append(joy)
    return doc


def add_lod1(name: str = "mini_face_LOD1", node: str = "bs_lod1", divisions: tuple = (10, 8), aliases: tuple = ()) -> dict:
    """`build_mini_head()` のあとに、LOD1 のメッシュを足す: 頂点数の違う球（72）を同じジョイントへスキンし、
    自分の blendShape ノード（`node`。スキンより前）に **同じ名前のターゲット**（mouth_open / smile_L / smile_R / brow_up。LOD1 の頂点で作る）を持たせる。
    ターゲット用のメッシュは消す（差分は blendShape が持つ）。divisions = 球の分割（頂点数が変わる）、aliases を渡すとその名前のターゲットだけ持たせる。"""
    lod = cmds.polySphere(name=name, radius=8, subdivisionsX=divisions[0], subdivisionsY=divisions[1], axis=(0, 1, 0))[0]
    cmds.move(0, 12, 0, lod, absolute=True)
    cmds.makeIdentity(lod, apply=True, translate=True)
    skin = cmds.skinCluster("root", "head", "eye_L", "eye_R", lod, toSelectedBones=True, maximumInfluences=3, name=f"{name}_skin")[0]
    cmds.skinPercent(skin, lod, transformValue=[("head", 1.0)])
    for i, p in enumerate(_vertex_positions(lod)):
        for eye, pos in (("eye_L", EYE_L), ("eye_R", EYE_R)):
            d = math.dist(p, pos)
            if d < EYE_RADIUS * 1.6:  # 頂点が粗いので、目の範囲を少し広げる
                w = 1.0 - d / (EYE_RADIUS * 1.6)
                cmds.skinPercent(skin, f"{lod}.vtx[{i}]", transformValue=[("head", 1.0 - w), (eye, w)])

    def front(p):
        return p[2] > 3.0

    specs = (
        ("mouth_open", lambda p: front(p) and 8.0 < p[1] < 11.5 and abs(p[0]) < 4.5, (0, -1.5, 0.3)),
        ("smile_L", lambda p: front(p) and 8.0 < p[1] < 12.5 and p[0] > 1.0, (0.5, 0.8, 0)),
        ("smile_R", lambda p: front(p) and 8.0 < p[1] < 12.5 and p[0] < -1.0, (-0.5, 0.8, 0)),
        ("brow_up", lambda p: front(p) and p[1] > 14.0, (0, 1.0, 0)),
    )
    specs = tuple(sp for sp in specs if not aliases or sp[0] in aliases)
    targets = [_make_target(lod, f"{node}_target_{a}", sel, off) for a, sel, off in specs]
    bs = cmds.blendShape(*targets, lod, name=node, frontOfChain=True)[0]
    for i, (alias, _s, _o) in enumerate(specs):
        cmds.aliasAttr(alias, f"{bs}.weight[{i}]")
    cmds.delete(*targets)
    cmds.select(clear=True)
    return {"lod": lod, "bs": bs, "skin": skin}


def add_body(garbage_pose: bool = True) -> dict:
    """`build_mini_head()` のあとに、体のメッシュ `mini_body`（root・spine・arm_L・arm_R にスキン。head・目は影響しない）を足す。

    顔のスキンが指す bindPose を、**中身がでたらめな dagPose**（FBX 由来のシーンの再現。全ジョイントがメンバー）に付け替える。
    体のジョイントのバインド値は、顔のスキンのバインド行列（bindPreMatrix）には入っていない。"""
    cmds.select("root")
    spine = cmds.joint(name="spine", position=(0, 4, 0))
    arm_l = cmds.joint(name="arm_L", position=(6, 6, 0))
    cmds.select(spine)
    arm_r = cmds.joint(name="arm_R", position=(-6, 6, 0))
    cmds.select(clear=True)
    body = cmds.polyCylinder(name="mini_body", radius=3, height=8, subdivisionsX=12, subdivisionsY=6, axis=(0, 1, 0))[0]
    cmds.move(0, 4, 0, body, absolute=True)
    cmds.makeIdentity(body, apply=True, translate=True)
    skin = cmds.skinCluster("root", spine, arm_l, arm_r, body, toSelectedBones=True, maximumInfluences=2, name="mini_body_skin")[0]
    for i, p in enumerate(_vertex_positions(body)):
        w = min(1.0, max(0.0, p[1] / 8.0))
        cmds.skinPercent(skin, f"{body}.vtx[{i}]", transformValue=[("root", 1.0 - w), (spine, w * 0.5), (arm_l if p[0] > 0 else arm_r, w * 0.5)])
    out = {"body": body, "body_skin": skin, "body_joints": [spine, arm_l, arm_r], "bad_pose": None}
    if garbage_pose:
        allj = ["root", "head", "eye_L", "eye_R", spine, arm_l, arm_r]
        bad = cmds.dagPose(allj, save=True, bindPose=True, name="badBindPose")
        bad = bad[0] if isinstance(bad, (list, tuple)) else bad
        n = cmds.getAttr(bad + ".members", size=True)
        for i in range(n):
            cmds.setAttr(f"{bad}.xformMatrix[{i}]", 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 3.0 + i, 20.0 - i, -7.0 + 2 * i, 1, type="matrix")
            cmds.setAttr(f"{bad}.worldMatrix[{i}]", 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 41.0 + i, 0.3, -11.0 + i, 1, type="matrix")
        face_skin = "mini_skin"
        for old in cmds.listConnections(face_skin + ".bindPose", source=True, destination=False, plugs=True) or []:
            cmds.disconnectAttr(old, face_skin + ".bindPose")
        cmds.connectAttr(bad + ".message", face_skin + ".bindPose", force=True)
        out["bad_pose"] = bad
    cmds.select(clear=True)
    return out


def add_display_layer(meshes=("mini_brow", "mini_body"), name: str = "meshLayer") -> str:
    """メッシュの transform を表示レイヤーに入れる（実モデルと同じ: transform の drawOverride が layer.drawInfo から来る。shape は自由）。"""
    present = [m for m in meshes if cmds.objExists(m)]
    return cmds.createDisplayLayer(present, name=name, noRecurse=True)
