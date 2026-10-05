"""顔の法線編集（Toon Normal、docs/05 §3.1、T-10）。

楕円体プロキシの法線に顔メッシュの法線を寄せて、影の出方を単純な形（作画的な 2〜3 パターン）に近づける。
"""

from __future__ import annotations

from maya import cmds
from maya.api import OpenMaya as om

from . import preview, smooth_normals

PROXY = "tdFaceNormalProxy"


def create_proxy(meshes: list[str]) -> str:
    """対象メッシュのバウンディングボックスに合わせた楕円体を作る（既にあれば合わせ直す）。"""
    shapes = preview.mesh_shapes(meshes)
    if not shapes:
        raise RuntimeError("メッシュを選択するか、表で部位を選んでください")
    xmin, ymin, zmin, xmax, ymax, zmax = cmds.exactWorldBoundingBox(shapes)
    if not cmds.objExists(PROXY):
        proxy = cmds.sphere(name=PROXY, radius=0.5, sections=16, spans=8, constructionHistory=False)[0]
        shape = cmds.listRelatives(proxy, shapes=True)[0]
        cmds.setAttr(f"{shape}.overrideEnabled", True)
        cmds.setAttr(f"{shape}.overrideShading", False)  # ワイヤー表示
        cmds.setAttr(f"{shape}.overrideColor", 17)
        cmds.setAttr(f"{shape}.castsShadows", False)
        cmds.setAttr(f"{shape}.primaryVisibility", False)
    cmds.xform(PROXY, worldSpace=True, translation=((xmin + xmax) / 2, (ymin + ymax) / 2, (zmin + zmax) / 2), rotation=(0, 0, 0))
    cmds.xform(PROXY, scale=(max(xmax - xmin, 1e-3), max(ymax - ymin, 1e-3), max(zmax - zmin, 1e-3)))
    cmds.select(PROXY, replace=True)
    return PROXY


def ellipsoid_normal(point_ws: om.MPoint, proxy_matrix: om.MMatrix) -> om.MVector:
    """楕円体（単位球 × proxy_matrix）の、point を単位球へ写した方向での外向き法線（ワールド）。"""
    inv = proxy_matrix.inverse()
    q = point_ws * inv  # 単位球の空間
    n_local = om.MVector(q.x, q.y, q.z)
    # 法線は逆転置で変換する: n_ws = inv^T * n_local
    n_ws = n_local * inv.transpose()
    return n_ws.normal()


BACKUP_ATTR = "tdOriginalNormals"  # 最初の転写前の法線（リセットで作者の法線に戻すため）


def _backup(target: str) -> None:
    """最初の転写の前に、フェース頂点ごとの法線（オブジェクト空間）とロック状態を保存する。既にあれば何もしない。"""
    import json

    if cmds.attributeQuery(BACKUP_ATTR, node=target, exists=True):
        return
    dag = om.MSelectionList().add(target).getDagPath(0)
    fn = om.MFnMesh(dag)
    rows = []
    it = om.MItMeshFaceVertex(dag)
    while not it.isDone():
        n = it.getNormal(om.MSpace.kObject)
        rows.append([it.faceId(), it.vertexId(), round(n.x, 6), round(n.y, 6), round(n.z, 6)])
        it.next()
    locked = any(fn.isNormalLocked(i) for i in range(fn.numNormals))
    cmds.addAttr(target, longName=BACKUP_ATTR, dataType="string")
    cmds.setAttr(f"{target}.{BACKUP_ATTR}", json.dumps({"locked": locked, "rows": rows}), type="string")


def transfer(meshes: list[str], weight: float = 1.0, selected_vertices: dict[str, list[int]] | None = None) -> dict[str, int]:
    """楕円体の法線へ weight の割合で寄せる。{書き込み先: 頂点数}。selected_vertices があればその頂点だけ。"""
    if not cmds.objExists(PROXY):
        raise RuntimeError("先に「プロキシ作成」をしてください")
    pm = om.MMatrix(cmds.xform(PROXY, query=True, worldSpace=True, matrix=True))
    import json

    done = {}
    for shape in preview.mesh_shapes(meshes):
        if selected_vertices is not None and shape not in selected_vertices:
            continue  # 「選択した頂点だけ」で、このメッシュには選択頂点が無い
        preview.require_writable([shape], "顔の法線")  # 参照したキャラクターには書けない
        src = om.MFnMesh(om.MSelectionList().add(shape).getDagPath(0))
        target = smooth_normals.write_target(shape)
        _backup(target)
        # 混ぜる元は「最初の転写前」の法線（バックアップ）。今の法線から混ぜると転写を繰り返すたびに強さが重なる。
        # フェース頂点ごとに扱うので、ハードエッジ（割れた法線）も強さ 0 なら元のまま保たれる
        backup = json.loads(cmds.getAttr(f"{target}.{BACKUP_ATTR}"))
        dag = om.MSelectionList().add(target).getDagPath(0)
        to_world = dag.inclusiveMatrix().inverse().transpose()  # 法線はワールド行列の逆転置で変換する
        points = src.getPoints(om.MSpace.kWorld)
        only = set(selected_vertices[shape]) if selected_vertices is not None else None
        faces, verts, normals = om.MIntArray(), om.MIntArray(), om.MVectorArray()
        for face_id, vid, x, y, z in backup["rows"]:
            if only is not None and vid not in only:
                continue
            orig = (om.MVector(x, y, z) * to_world).normal()
            n = orig * (1.0 - weight) + ellipsoid_normal(points[vid], pm) * weight
            faces.append(face_id)
            verts.append(vid)
            normals.append(n.normal() if n.length() > 1e-8 else orig)
        # 変形前シェイプは同じ transform の下にあるので、ワールド空間での書き込みがそのまま使える
        om.MFnMesh(dag).setFaceVertexNormals(normals, faces, verts, om.MSpace.kWorld)
        done[target] = len(verts)
    return done


def reset(meshes: list[str]) -> list[str]:
    """最初の転写前の法線に戻す（作者のカスタム法線も復元する）。バックアップが無いメッシュは何もしない。"""
    import json

    done = []
    for shape in preview.mesh_shapes(meshes):
        target = smooth_normals.write_target(shape)
        if not cmds.attributeQuery(BACKUP_ATTR, node=target, exists=True):
            continue
        preview.require_writable([target], "顔の法線")
        data = json.loads(cmds.getAttr(f"{target}.{BACKUP_ATTR}"))
        dag = om.MSelectionList().add(target).getDagPath(0)
        rows = data["rows"]
        faces, verts = om.MIntArray([r[0] for r in rows]), om.MIntArray([r[1] for r in rows])
        normals = om.MVectorArray([om.MVector(r[2], r[3], r[4]) for r in rows])
        # まずバックアップの値そのものを書き戻す（作者のカスタム法線も含めて確実に元の見た目）
        om.MFnMesh(dag).setFaceVertexNormals(normals, faces, verts, om.MSpace.kObject)
        if not data["locked"]:
            # 元がロックされていなければ解除を試し、解除で値が変わる場合（FBX 由来の法線等）は書き戻した値のままにする
            om.MFnMesh(dag).unlockFaceVertexNormals(faces, verts)
            if _max_deviation(dag, rows) > 1e-5:
                om.MFnMesh(dag).setFaceVertexNormals(normals, faces, verts, om.MSpace.kObject)
        cmds.deleteAttr(f"{target}.{BACKUP_ATTR}")
        done.append(target)
    return done


def _max_deviation(dag: om.MDagPath, rows: list[list[float]]) -> float:
    """今のフェース頂点法線とバックアップの差の最大値（1 - cos）。"""
    fn = om.MFnMesh(dag)
    worst = 0.0
    for face_id, vid, x, y, z in rows:
        n = fn.getFaceVertexNormal(face_id, vid, om.MSpace.kObject)
        worst = max(worst, 1.0 - n.normal() * om.MVector(x, y, z).normal())
    return worst


def selected_vertex_ids() -> dict[str, list[int]]:
    """選択中の頂点を {メッシュのシェイプ完全パス: [頂点番号]} で返す（頂点選択が無ければ空）。"""
    out: dict[str, list[int]] = {}
    sel = om.MGlobal.getActiveSelectionList()
    for i in range(sel.length()):
        try:
            dag, comp = sel.getComponent(i)
        except RuntimeError:
            continue
        if comp.isNull() or comp.apiType() != om.MFn.kMeshVertComponent:
            continue
        if dag.apiType() == om.MFn.kTransform:
            dag.extendToShape()
        out.setdefault(dag.fullPathName(), []).extend(om.MFnSingleIndexedComponent(comp).getElements())
    return out
