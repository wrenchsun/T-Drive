"""アウトライン用スムーズ法線のベイク（docs/03 §5、T-06）。

同じ位置の頂点の法線を平均した「スムーズ法線」を、フェース頂点ごとの接空間（UV map1 の接線）で表し、
八面体エンコードした 2 値を UV Set `tdSmoothNormal` に書く。Unity では TEXCOORD2 として読む。

スキンなどのヒストリがあるメッシュは、変形前（Orig）のシェイプに書く（変形後に書くと再評価で消えるため）。
"""

from __future__ import annotations

from maya import cmds
from maya.api import OpenMaya as om

from . import normals, preview

UV_SET = preview.SMOOTH_NORMAL_UV
TANGENT_UV_SET = "map1"


def _shapes(meshes: list[str]) -> list[str]:
    out = []
    for m in meshes:
        if cmds.nodeType(m) == "mesh":
            out.append(m)
        else:
            out += cmds.listRelatives(m, shapes=True, noIntermediate=True, fullPath=True, type="mesh") or []
    return out


def write_target(shape: str) -> str:
    """書き込み先のシェイプ。デフォーマの上流にある Orig（中間オブジェクト）があればそちら。"""
    for node in cmds.listHistory(shape, pruneDagObjects=False) or []:
        if node == shape or cmds.nodeType(node) != "mesh":
            continue
        if cmds.getAttr(f"{node}.intermediateObject"):
            return cmds.ls(node, long=True)[0]
    return shape


def bake(meshes: list[str], tolerance: float = 1e-3) -> dict[str, int]:
    """メッシュごとにスムーズ法線を焼く。{書き込み先シェイプ: フェース頂点数} を返す。"""
    done = {}
    for shape in _shapes(meshes):
        target = write_target(shape)
        done[target] = _bake_one(target, tolerance)
    return done


def _bake_one(shape: str, tolerance: float) -> int:
    dag = om.MSelectionList().add(shape).getDagPath(0)
    fn = om.MFnMesh(dag)
    uv_sets = fn.getUVSetNames()
    tangent_set = TANGENT_UV_SET if TANGENT_UV_SET in uv_sets else uv_sets[0]
    positions = [(p.x, p.y, p.z) for p in fn.getPoints(om.MSpace.kObject)]

    # フェース頂点ごとの 頂点番号・法線・接線・従法線
    rows = []
    it = om.MItMeshFaceVertex(dag)
    while not it.isDone():
        n = it.getNormal(om.MSpace.kObject)
        t = it.getTangent(om.MSpace.kObject, tangent_set)
        b = it.getBinormal(om.MSpace.kObject, tangent_set)
        rows.append((it.vertexId(), (n.x, n.y, n.z), (t.x, t.y, t.z), (b.x, b.y, b.z)))
        it.next()

    smooth = normals.smooth_by_position(positions, ((vid, n) for vid, n, _t, _b in rows), tolerance)
    us, vs = om.MFloatArray(), om.MFloatArray()
    for vid, n, t, b in rows:
        e = normals.octahedral_encode(normals.to_tangent_space(smooth[vid], n, t, b))
        us.append(e[0])
        vs.append(e[1])

    # MItMeshFaceVertex はフェース順・フェース内頂点順に回るので、UV 番号は通し番号でよい
    counts, _ = fn.getVertices()
    if UV_SET not in uv_sets:
        fn.createUVSet(UV_SET)
    else:
        fn.clearUVs(UV_SET)
    fn.setUVs(us, vs, UV_SET)
    fn.assignUVs(counts, om.MIntArray(range(len(rows))), UV_SET)
    return len(rows)


def has_smooth_normals(mesh: str) -> bool:
    """評価済みメッシュに UV Set があるか。

    cmds.polyUVSet -q は変形後シェイプの名前一覧アトリビュートを見るため、Orig に焼いた直後は古い値を返すことがある。
    描画・書き出しが使う評価済みデータ（MFnMesh）で判定する。
    """
    return all(
        UV_SET in om.MFnMesh(om.MSelectionList().add(s).getDagPath(0)).getUVSetNames() for s in _shapes([mesh])
    )
