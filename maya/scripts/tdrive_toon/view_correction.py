"""カメラ角度の補正 BlendShape（docs/05 §3.2、T-20）。

- 補正シェイプの作成（顔メッシュの複製）と登録（BlendShape ターゲット化）
- カメラ連動のプレビュー: DG ノード（decomposeMatrix + plusMinusAverage + expression）で組むので、
  カメラを回すとそのまま追従する（Python のコールバック不要）。式は envmath.view_correction_weights と同じ
"""

from __future__ import annotations

from maya import cmds

from . import naming, preview

KEYS = ("front", "threeQuarter", "side")
KEY_LABELS = {"front": "正面", "threeQuarter": "3/4", "side": "横"}


def _short(mesh: str) -> str:
    """メッシュの短い名前。参照したキャラクターは `chr:` を外し、ネームスペースを平らにした頭を付ける（`chr_mdl_face02`。ノード名に `:` は使えない）。"""
    return naming.prefix(naming.ns_of(mesh)) + naming.doc_short(mesh, naming.ns_of(mesh))


def doc_short(mesh: str) -> str:
    """Look に書くメッシュ名（ネームスペースなしの短い名前）。"""
    return naming.doc_short(mesh, naming.ns_of(mesh) or None)


def blend_shape_name(mesh: str) -> str:
    return f"tdViewCorrection_{_short(mesh)}"


def target_name(mesh: str, key: str) -> str:
    """彫刻用の作業メッシュのノード名（シーンのノード。ネームスペースなしのルートに置く）。"""
    return f"{_short(mesh)}_vc_{key}"


def alias_name(mesh: str, key: str) -> str:
    """BlendShape のターゲット名（Look に書く名前。ネームスペースなし = どのシーンでも同じ）。"""
    return f"{doc_short(mesh)}_vc_{key}"


def create_target(mesh: str, key: str) -> str:
    """補正シェイプ用に顔メッシュを複製して横に置く（ヒストリ・デフォーマーなしのきれいなメッシュ）。"""
    if key not in KEYS:
        raise ValueError(key)
    name = target_name(mesh, key)
    if cmds.objExists(name):
        raise RuntimeError(f"{name} は既にあります（彫り直すならそれを使ってください）")
    dup = cmds.duplicate(mesh, name=name)[0]
    for s in cmds.listRelatives(dup, shapes=True, fullPath=True) or []:
        if cmds.getAttr(f"{s}.intermediateObject"):
            cmds.delete(s)
    cmds.delete(dup, constructionHistory=True)
    if cmds.listRelatives(dup, parent=True):
        dup = cmds.parent(dup, world=True)[0]
    for attr in ("tx", "ty", "tz", "rx", "ry", "rz", "sx", "sy", "sz"):
        cmds.setAttr(f"{dup}.{attr}", lock=False)
    xmin, _, _, xmax, _, _ = cmds.exactWorldBoundingBox(mesh)
    cmds.move((xmax - xmin) * 1.5 * (KEYS.index(key) + 1), 0, 0, dup, relative=True, worldSpace=True)
    # 彫刻用の作業メッシュの目印（Unity 出力の FBX には入れない。BlendShape として顔メッシュに入る）
    cmds.addAttr(dup, longName=TARGET_ATTR, attributeType="bool", defaultValue=True)
    return dup


TARGET_ATTR = "tdViewCorrectionTarget"


def is_target(transform: str) -> bool:
    return cmds.attributeQuery(TARGET_ATTR, node=transform, exists=True)


def register(mesh: str, key: str, target: str) -> str:
    """target を mesh の補正 BlendShape（なければ作る）に追加し、ターゲット名（エイリアス）を返す。"""
    bs = blend_shape_name(mesh)
    alias = alias_name(mesh, key)
    node_alias = target.split("|")[-1]  # Maya が付けるターゲット名（= 作業メッシュのノード名。参照したキャラクターでは alias と違う）
    if not cmds.objExists(bs):
        # スキンより前（frontOfChain）に入れて、変形前の形に対する補正にする
        cmds.blendShape(target, mesh, name=bs, frontOfChain=True)
        index = 0
    else:
        existing = cmds.aliasAttr(bs, query=True) or []
        if alias in existing:
            return alias
        index = len(cmds.getAttr(f"{bs}.weight", multiIndices=True) or [])
        cmds.blendShape(bs, edit=True, target=(mesh, index, target, 1.0))
    if node_alias != alias:
        cmds.aliasAttr(alias, f"{bs}.weight[{index}]")  # Look には「ネームスペースなし」の名前を書く
    return alias


def connect_driver(mesh: str, targets: dict[str, str], camera: str | None = None) -> str:
    """カメラの向きで補正ウェイトを動かす DG ネットワークを組む（既存があれば作り直す）。expression ノード名を返す。

    targets: {"front": エイリアス, ...}（空文字 = その角度は使わない）
    """
    disconnect_driver(mesh)
    bs = blend_shape_name(mesh)
    if not cmds.objExists(bs):
        raise RuntimeError("補正シェイプが登録されていません")
    camera = camera or _current_camera()
    cam_xform = camera if cmds.nodeType(camera) == "transform" else cmds.listRelatives(camera, parent=True)[0]
    tag = _short(mesh)
    dm = cmds.createNode("decomposeMatrix", name=f"tdVC_camPos_{tag}")
    cmds.connectAttr(f"{cam_xform}.worldMatrix[0]", f"{dm}.inputMatrix")
    pma = cmds.createNode("plusMinusAverage", name=f"tdVC_toCam_{tag}")
    cmds.setAttr(f"{pma}.operation", 2)  # subtract
    cmds.connectAttr(f"{dm}.outputTranslate", f"{pma}.input3D[0]")
    xmin, ymin, zmin, xmax, ymax, zmax = cmds.exactWorldBoundingBox(mesh)
    cmds.setAttr(f"{pma}.input3D[1]", (xmin + xmax) / 2, (ymin + ymax) / 2, (zmin + zmax) / 2, type="double3")

    lines = [
        "// T-Drive: カメラ角度の補正ウェイト（envmath.view_correction_weights と同じ式）",
        f"float $vx = {pma}.output3Dx;",
        f"float $vz = {pma}.output3Dz;",
        "float $len = sqrt($vx * $vx + $vz * $vz);",
        "float $a = 0;",
        "if ($len > 0.000001) $a = rad_to_deg(acos(clamp(-1, 1, $vz / $len)));",
        "float $t1 = clamp(0, 1, $a / 45.0); $t1 = $t1 * $t1 * (3 - 2 * $t1);",
        "float $t2 = clamp(0, 1, ($a - 45.0) / 45.0); $t2 = $t2 * $t2 * (3 - 2 * $t2);",
        "float $front = 1 - $t1;",
        "float $tq = ($a <= 45.0) ? $t1 : 1 - $t2;",
        "float $side = ($a >= 45.0) ? $t2 : 0;",
    ]
    for key, var in (("front", "$front"), ("threeQuarter", "$tq"), ("side", "$side")):
        alias = targets.get(key) or ""
        if alias:
            lines.append(f"{bs}.{alias} = {var};")
    return cmds.expression(name=f"tdVC_expr_{tag}", string="\n".join(lines), alwaysEvaluate=False)


def disconnect_driver(mesh: str) -> None:
    tag = _short(mesh)
    for node in (f"tdVC_expr_{tag}", f"tdVC_toCam_{tag}", f"tdVC_camPos_{tag}"):
        if cmds.objExists(node):
            cmds.delete(node)


def _current_camera() -> str:
    from . import environment

    panel = environment.model_panel()
    if not panel:
        raise RuntimeError("ビューポートが見つかりません")
    return cmds.modelPanel(panel, query=True, camera=True)


def weights(mesh: str) -> dict[str, float]:
    """現在の補正ウェイト（確認・テスト用）。"""
    bs = blend_shape_name(mesh)
    out = {}
    for alias in cmds.aliasAttr(bs, query=True)[::2] if cmds.objExists(bs) else []:
        out[alias] = cmds.getAttr(f"{bs}.{alias}")
    return out


def mesh_for_selection() -> str:
    shapes = preview.mesh_shapes(cmds.ls(selection=True, long=True) or [])
    if not shapes:
        raise RuntimeError("顔のメッシュを選択してください")
    return cmds.listRelatives(shapes[0], parent=True, fullPath=True)[0]
