"""Viewport 2.0 プレビュー: 元マテリアルを T-Drive Toon の dx11Shader に差し替え、Look の値を流し込む。

- 元マテリアル・元の割り当ては変更しない。プレビュー用 SG に面を移し、解除時に元の SG へ戻す
- 値の反映は setAttr だけ（シェーダー再コンパイルなし）なのでスライダーに追従できる
- ツールが前提にするのは「標準の Maya マテリアルが割り当てられたメッシュ」だけ（特定モデルの事情は持ち込まない）
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from maya import cmds

from . import REPO_ROOT, environment, envmath, params

SHADER_FILE = (REPO_ROOT / "maya" / "shaders" / "TDriveToon.fx").as_posix()
NODE_TYPE = "dx11Shader"
SUFFIX = "_tdToon"
SOURCE_ATTR = "tdSourceMaterial"  # プレビューシェーダー → 元マテリアル名
PREVIEW_ONLY_ATTR = "tdPreviewOnly"  # プレビュー専用のメッシュ（接地影の板など）。部位登録・出力の対象外
MASK_COLOR_SET = "tdToonMask"  # docs/03 §4
SMOOTH_NORMAL_UV = "tdSmoothNormal"  # docs/03 §5

# 現在のプレビュー環境（プロファイル + ツールでの上書き）。Look には保存しない
_env: dict[str, Any] = {
    "profile": None,
    "lightEuler": (35.0, 180.0),  # Unity と同じ表現（ピッチ x, ヨー y 度）。ヨー 180 = キャラクターの正面から照らす
    "lightDir": envmath.light_dir_to_light_maya((35.0, 180.0, 0.0)),
    "lightColor": (1.0, 1.0, 1.0),
    "tonemap": 0,
    "depthCompression": 0.0,  # T-22 キャラクター単位（Look の characterSettings から session が設定）
    "depthPivot": (0.0, 0.0, 0.0),
    "faceForward": (0.0, 0.0, 1.0),  # T-21 顔の向き（Look の characterSettings.faceShadow から session が設定）
    "faceRight": (-1.0, 0.0, 0.0),
}


# ---------------------------------------------------------------- 準備


def outside_maya_undo(fn):
    """プレビューの見た目の更新を Maya の Undo 履歴に積まない。

    積むと、ビューポートで Ctrl+Z したとき見た目（シェーダーの値・割り当て）だけが戻り、Look の値とずれる。
    Look の値の Undo はエディタ内 Undo（session.undo）が担当する。
    """
    from functools import wraps

    @wraps(fn)
    def wrapper(*args, **kwargs):
        on = cmds.undoInfo(query=True, stateWithoutFlush=True)
        if on:
            cmds.undoInfo(stateWithoutFlush=False)
        try:
            return fn(*args, **kwargs)
        finally:
            if on:
                cmds.undoInfo(stateWithoutFlush=True)

    return wrapper



def ensure_plugin() -> None:
    if not cmds.pluginInfo(NODE_TYPE, query=True, loaded=True):
        cmds.loadPlugin(NODE_TYPE, quiet=True)


# ---------------------------------------------------------------- シーン走査


def scene_materials() -> dict[str, list[str]]:
    """メッシュに割り当てられている元マテリアル名 → メッシュ(transform)の**完全パス**一覧。割り当ての無いメッシュは対象外。

    完全パスで返す: 同名メッシュ（キャラクターの複製・複数読み込み）があっても曖昧にならないように。
    表示では short_name() で末尾だけにする。
    """
    result: dict[str, set[str]] = {}
    for sg in cmds.ls(type="shadingEngine"):
        shader = _surface_shader(sg)
        if not shader:
            continue
        meshes = set()
        for m in cmds.ls(cmds.sets(sg, query=True) or [], long=True, objectsOnly=True):
            if cmds.nodeType(m) == "transform":
                shapes = cmds.listRelatives(m, shapes=True, type="mesh", noIntermediate=True) or []
                if not shapes:
                    continue  # NURBS（顔の法線プロキシ等）やメッシュ以外は対象外
            elif cmds.nodeType(m) == "mesh":
                m = cmds.listRelatives(m, parent=True, fullPath=True)[0]
            else:
                continue
            if cmds.attributeQuery(PREVIEW_ONLY_ATTR, node=m, exists=True):
                continue
            meshes.add(m)
        if meshes:
            result.setdefault(source_material(shader), set()).update(meshes)
    return {k: sorted(v) for k, v in sorted(result.items())}


def mesh_shapes(nodes: list[str]) -> list[str]:
    """transform / mesh の一覧を、描画される（中間オブジェクトでない）mesh シェイプの完全パスにする。"""
    shapes: list[str] = []
    for n in nodes:
        if cmds.nodeType(n) == "mesh":
            shapes += cmds.ls(n, long=True)
        else:
            shapes += cmds.listRelatives(n, shapes=True, noIntermediate=True, fullPath=True, type="mesh") or []
    return shapes


def short_name(path: str) -> str:
    return path.split("|")[-1]


def source_material(shader: str) -> str:
    if cmds.attributeQuery(SOURCE_ATTR, node=shader, exists=True):
        return cmds.getAttr(f"{shader}.{SOURCE_ATTR}")
    return shader


def _surface_shader(sg: str) -> str | None:
    src = cmds.listConnections(f"{sg}.surfaceShader", source=True, destination=False) or []
    return src[0] if src else None


def _shading_group(shader: str) -> str | None:
    sgs = cmds.listConnections(f"{shader}.outColor", type="shadingEngine") or []
    return sgs[0] if sgs else None


def materials_on_selection() -> list[str]:
    """選択中のメッシュ / 面に割り当てられている元マテリアル名。"""
    sel = cmds.ls(selection=True, long=True) or []
    shapes = cmds.ls(sel, dagObjects=True, type="mesh", long=True, noIntermediate=True) or []
    faces = cmds.filterExpand(sel, selectionMask=34) or []
    mats: set[str] = set()
    for sg in cmds.listConnections(shapes, type="shadingEngine") or []:
        if sh := _surface_shader(sg):
            mats.add(source_material(sh))
    for f in faces:
        for sg in cmds.listSets(object=f, type=1) or []:
            if cmds.nodeType(sg) == "shadingEngine" and (sh := _surface_shader(sg)):
                mats.add(source_material(sh))
    return sorted(mats)


def base_texture_of(material: str) -> str | None:
    """元マテリアルのカラーに繋がる file テクスチャのパス（リポジトリ内ならリポジトリ相対）。"""
    for attr in ("color", "baseColor", "base_color", "diffuseColor"):
        if not cmds.attributeQuery(attr, node=material, exists=True):
            continue
        files = cmds.listConnections(f"{material}.{attr}", type="file") or []
        if files:
            return to_repo_path(cmds.getAttr(f"{files[0]}.computedFileTextureNamePattern"))
    return None


def source_blend(material: str) -> str:
    """元マテリアルの透明設定から D-Drive の Blend を推定する（透明度に接続 or 値あり → Transparent）。"""
    for attr in ("transparency", "opacity", "transmission"):
        if not cmds.attributeQuery(attr, node=material, exists=True):
            continue
        if cmds.listConnections(f"{material}.{attr}", source=True, destination=False):
            return "Transparent"
        value = cmds.getAttr(f"{material}.{attr}")
        value = value[0] if isinstance(value, list) else value
        values = value if isinstance(value, tuple) else (value,)
        if attr == "opacity" and any(v < 1.0 for v in values):
            return "Transparent"
        if attr != "opacity" and any(v > 0.0 for v in values):
            return "Transparent"
    return "Opaque"


def meshes_of(material: str) -> list[str]:
    return scene_materials().get(material, [])


def to_repo_path(path: str) -> str:
    p = Path(path)
    try:
        return p.resolve().relative_to(REPO_ROOT.resolve()).as_posix()
    except (ValueError, OSError):
        return p.as_posix()


def from_repo_path(path: str) -> str:
    p = Path(path)
    return (p if p.is_absolute() else REPO_ROOT / p).as_posix()


# ---------------------------------------------------------------- 差し替え


def preview_shader_of(material: str) -> str:
    return f"{material}{SUFFIX}"


def preview_shaders() -> list[str]:
    return cmds.ls(f"*{SUFFIX}", type=NODE_TYPE) or []


def is_active() -> bool:
    return any(cmds.sets(sg, query=True) for s in preview_shaders() if (sg := _shading_group(s)))


@outside_maya_undo
def enable(materials: dict[str, dict[str, Any]]) -> list[str]:
    """resolve 済みのマテリアル値でプレビューを有効化する。シーンに無かったマテリアル名を返す。"""
    ensure_plugin()
    missing = []
    scene = scene_materials()  # マテリアルごとに走査し直さない（全 SG の走査は重い）
    for mat, values in materials.items():
        if not cmds.objExists(mat):
            missing.append(mat)
            continue
        shader = _ensure_preview_shader(mat)
        apply_values(mat, values)
        _update_mesh_streams(shader, scene.get(mat, []), "vertexMask" in values.get("features", ["vertexMask"]))
        _swap(_shading_group(mat), _shading_group(shader))
    apply_environment()
    return missing


@outside_maya_undo
def disable() -> None:
    """元マテリアルの割り当てに戻す（プレビューシェーダーは残し、次回すぐ有効化できるようにする）。"""
    for shader in preview_shaders():
        src = source_material(shader)
        if cmds.objExists(src):
            _swap(_shading_group(shader), _shading_group(src))


@outside_maya_undo
def delete_all() -> None:
    from . import contact_shadow

    disable()
    contact_shadow.delete()
    for shader in preview_shaders():
        nodes = [shader, _shading_group(shader), *(cmds.listConnections(shader, type="file") or [])]
        cmds.delete([n for n in nodes if n and cmds.objExists(n)])


def _swap(src_sg: str | None, dst_sg: str | None) -> None:
    if not (src_sg and dst_sg):
        return
    members = cmds.sets(src_sg, query=True) or []
    if members:
        cmds.sets(members, edit=True, forceElement=dst_sg)


def _ensure_preview_shader(mat: str) -> str:
    name = preview_shader_of(mat)
    if cmds.objExists(name):
        return name
    shader = cmds.shadingNode(NODE_TYPE, asShader=True, name=name)
    cmds.setAttr(f"{shader}.shader", SHADER_FILE, type="string")
    cmds.addAttr(shader, longName=SOURCE_ATTR, dataType="string")
    cmds.setAttr(f"{shader}.{SOURCE_ATTR}", mat, type="string")
    sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name=f"{name}SG")
    cmds.connectAttr(f"{shader}.outColor", f"{sg}.surfaceShader", force=True)
    return shader


def _update_mesh_streams(shader: str, meshes: list[str], vertex_mask_on: bool = True) -> None:
    """頂点カラー tdToonMask を全メッシュが持ち、機能 vertexMask がオンのときだけ頂点マスクを有効にする。"""
    has_mask = vertex_mask_on and bool(meshes) and all(MASK_COLOR_SET in (cmds.polyColorSet(m, query=True, allColorSets=True) or []) for m in meshes)
    _set(shader, "VertexMaskEnabled", has_mask)
    # dx11Shader の頂点ストリームの取得元（既定は color:colorSet / uv:map3）
    _set_string(shader, "Color0_Source", f"color:{MASK_COLOR_SET}")
    _set_string(shader, "TexCoord2_Source", f"uv:{SMOOTH_NORMAL_UV}")


def _set_string(node: str, attr: str, value: str) -> None:
    if cmds.attributeQuery(attr, node=node, exists=True) and cmds.getAttr(f"{node}.{attr}") != value:
        cmds.setAttr(f"{node}.{attr}", value, type="string")


def reload_shader_file(materials: dict[str, dict[str, Any]]) -> list[str]:
    """.fx / ToonCore.hlsl を編集した後に呼ぶ。プレビューノードを作り直して再コンパイルする。

    `dx11Shader -reload` は描画中のノードに対して実行すると Maya がフリーズした（2026-09-28）ため使わない。
    """
    delete_all()
    return enable(materials)


# ---------------------------------------------------------------- 値の反映


def technique_for(common: dict[str, Any]) -> str:
    if common.get("blend") == "Transparent":
        return "Transparent"
    return "OpaqueDoubleSided" if common.get("doubleSided") else "Opaque"


@outside_maya_undo
def apply_values(mat: str, values: dict[str, Any]) -> None:
    """values に含まれる項目だけを反映する（部分更新可）。"""
    shader = preview_shader_of(mat)
    if not cmds.objExists(shader):
        return
    common = values.get("common", {})
    if "blend" in common or "doubleSided" in common:
        # テクニック名からは復元できない（Transparent は両面かどうかを持たない）ので、状態はノードの属性に持つ
        state = _render_state(shader)
        state.update({k: common[k] for k in ("blend", "doubleSided") if k in common})
        _store_render_state(shader, state)
        _set_technique(shader, technique_for(state))
        _set(shader, "AlphaClip", state["blend"] == "Cutout")
    if "cutoff" in common:
        _set(shader, "Cutoff", common["cutoff"])
    if "albedoTint" in common:
        _set(shader, "BaseColor", envmath.srgb_color_to_linear(common["albedoTint"]))
    if "albedo" in common:
        _set_texture(shader, "BaseMap", common["albedo"], srgb=True)
    # normal / emission は P0 の式で使わない（Unity 側でのみ使用）
    for key, value in values.get("specific", {}).items():
        p = params.PARAMS_BY_UNITY.get(key)
        if p is None:
            continue
        if p.kind == params.TEXTURE:
            _set_texture(shader, p.maya, value, srgb=False)
        elif p.kind == params.COLOR:
            # Look の色は sRGB 値（Unity のインスペクターと同じ）。Unity と同じくリニアにしてシェーダーへ渡す
            _set(shader, p.maya, envmath.srgb_color_to_linear(value))
        else:
            _set(shader, p.maya, value)


STATE_ATTR = "tdRenderState"  # {"blend": ..., "doubleSided": ...}（Look の common と同じ値）


def _render_state(shader: str) -> dict[str, Any]:
    if cmds.attributeQuery(STATE_ATTR, node=shader, exists=True):
        raw = cmds.getAttr(f"{shader}.{STATE_ATTR}")
        if raw:
            return json.loads(raw)
    return {"blend": _get_blend(shader), "doubleSided": cmds.getAttr(f"{shader}.technique") == "OpaqueDoubleSided"}


def _store_render_state(shader: str, state: dict[str, Any]) -> None:
    if not cmds.attributeQuery(STATE_ATTR, node=shader, exists=True):
        cmds.addAttr(shader, longName=STATE_ATTR, dataType="string")
    cmds.setAttr(f"{shader}.{STATE_ATTR}", json.dumps(state), type="string")


def _get_blend(shader: str) -> str:
    """状態属性が無い古いノード用の推測（Transparent / Cutout / Opaque）。"""
    if cmds.getAttr(f"{shader}.technique") == "Transparent":
        return "Transparent"
    return "Cutout" if _get(shader, "AlphaClip") else "Opaque"


def apply_value(mat: str, key: str, value: Any) -> None:
    """1 項目だけ反映（スライダー操作用）。key は look.set_value と同じ形式。"""
    if key == "renderQueueOffset":
        return  # Maya では描画順を再現しない（docs/09 §4）
    if key.startswith("common."):
        apply_values(mat, {"common": {key.split(".", 1)[1]: value}})
    else:
        apply_values(mat, {"specific": {key: value}})


def _set_technique(shader: str, technique: str) -> None:
    if cmds.getAttr(f"{shader}.technique") != technique:
        cmds.setAttr(f"{shader}.technique", technique, type="string")


def _get(node: str, attr: str) -> Any:
    return cmds.getAttr(f"{node}.{attr}") if cmds.attributeQuery(attr, node=node, exists=True) else None


def _set(node: str, attr: str, value: Any) -> None:
    if not cmds.attributeQuery(attr, node=node, exists=True):
        return
    plug = f"{node}.{attr}"
    if isinstance(value, bool):
        cmds.setAttr(plug, value)
    elif isinstance(value, (int, float)):
        cmds.setAttr(plug, value)
    elif isinstance(value, (list, tuple)):
        v = [float(x) for x in value]
        # dx11Shader の float4 色（color1x4）は <name>RGB(float3) + <name>A に分かれる
        if cmds.attributeQuery(f"{attr}RGB", node=node, exists=True):
            cmds.setAttr(f"{node}.{attr}RGB", *v[:3], type="double3")
            if len(v) == 4:
                cmds.setAttr(f"{node}.{attr}A", v[3])
        else:
            cmds.setAttr(plug, *v[:3], type="double3")


def _set_texture(shader: str, attr: str, path: str | None, srgb: bool) -> None:
    enabled = f"{attr}Enabled"
    if not path:
        _set(shader, enabled, False)
        return
    if not cmds.attributeQuery(attr, node=shader, exists=True):
        return
    file_node = f"{shader}_{attr}"
    if not cmds.objExists(file_node):
        file_node = cmds.shadingNode("file", asTexture=True, isColorManaged=True, name=file_node)
        cmds.connectAttr(f"{file_node}.outColor", f"{shader}.{attr}", force=True)
    # リポジトリ内のテクスチャは $TDRIVE_ROOT（TDriveToon.mod が定義）基準で書き、どの PC でもシーンが開けるようにする
    name = path if Path(path).is_absolute() else f"$TDRIVE_ROOT/{path}"
    if cmds.getAttr(f"{file_node}.fileTextureName") != name:
        cmds.setAttr(f"{file_node}.fileTextureName", name, type="string")
    # ベースカラーは sRGB（Unity の sRGB テクスチャ）、マスク類はリニア（docs/09 §2）
    space = environment.pick_texture_space(srgb)
    if space:
        cmds.setAttr(f"{file_node}.ignoreColorSpaceFileRules", True)
        cmds.setAttr(f"{file_node}.colorSpace", space, type="string")
    _set(shader, enabled, True)


# ---------------------------------------------------------------- 環境（キャラクターライト / トーンマップ）


def environment_state() -> dict[str, Any]:
    return dict(_env)


def use_profile(name: str) -> list[str]:
    """環境プロファイルを適用する。警告（未対応の値など）を返す。"""
    prof = environment.load_profile(name)
    _direction, color = environment.character_light(prof)
    rx, ry, _rz = prof["characterLight"]["rotation"]
    _env.update(profile=name, lightColor=color, tonemap=environment.tonemap_mode(prof))
    set_light_euler(rx, ry, apply=False)
    environment.apply_color_management()
    apply_environment()
    return environment.profile_warnings(prof)


# ライトのプリセット（Unity のオイラー角 x, y）。キャラクターは +Z を向き、画面右 = Maya +X
LIGHT_PRESETS = {
    "正面上": (35.0, 180.0),
    "右前上": (40.0, 140.0),
    "左前上": (40.0, 220.0),
    "真上": (89.0, 180.0),
    "逆光": (20.0, 0.0),
}


def set_light_euler(pitch: float, yaw: float, apply: bool = True) -> None:
    """キャラクターライトの向きを Unity のオイラー角で指定する。"""
    _env["lightEuler"] = (float(pitch), float(yaw) % 360.0)
    _env["lightDir"] = envmath.light_dir_to_light_maya((float(pitch), float(yaw), 0.0))
    if apply:
        apply_environment()


def reset_light_to_profile() -> None:
    """ライトを環境プロファイル（ゲームと同じ）の値に戻す。"""
    if _env["profile"]:
        rx, ry, _ = environment.load_profile(_env["profile"])["characterLight"]["rotation"]
        set_light_euler(rx, ry)


def save_preview_settings(path: Path, extra: dict[str, Any] | None = None) -> None:
    """プレビュー条件（環境・ライト・カメラ）を保存し、A/B 比較の条件を再現できるようにする。"""
    data = {"profile": _env["profile"], "lightEuler": list(_env["lightEuler"]), **(extra or {})}
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")


def load_preview_settings(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("profile") in environment.list_profiles():
        use_profile(data["profile"])
    if "lightEuler" in data:
        set_light_euler(*data["lightEuler"])
    return data


def set_light(direction: tuple[float, float, float] | None = None, color: tuple[float, float, float] | None = None) -> None:
    if direction is not None:
        _env["lightDir"] = tuple(direction)
    if color is not None:
        _env["lightColor"] = tuple(color)
    apply_environment()


@outside_maya_undo
def apply_environment() -> None:
    for shader in preview_shaders():
        _set(shader, "PreviewLightDir", list(_env["lightDir"]))
        _set(shader, "PreviewLightColor", list(_env["lightColor"]))
        _set(shader, "PreviewTonemap", int(_env["tonemap"]))
        # m で定義したパラメータ（手前に出す量・線の距離補正）をシーン単位へ換算する係数
        _set(shader, "PreviewUnitScale", environment.units_per_meter())
        _set(shader, "PreviewDepthCompression", float(_env["depthCompression"]))
        _set(shader, "PreviewDepthPivot", list(_env["depthPivot"]))
        _set(shader, "PreviewFaceForward", list(_env["faceForward"]))
        _set(shader, "PreviewFaceRight", list(_env["faceRight"]))


def set_face_axes(forward, right) -> None:
    """T-21 顔の正面・右（ワールド）をプレビューに設定する。"""
    _env["faceForward"] = tuple(float(c) for c in forward)
    _env["faceRight"] = tuple(float(c) for c in right)
    apply_environment()


def set_depth_compression(amount: float, pivot: tuple[float, float, float] | None = None) -> None:
    """T-22 奥行き圧縮の量（キャラクター単位）と中心（ワールド）をプレビューに設定する。"""
    _env["depthCompression"] = float(amount)
    if pivot is not None:
        _env["depthPivot"] = tuple(pivot)
    apply_environment()


# ---------------------------------------------------------------- キャプチャ


def capture(path: str, width: int = 1920, height: int = 1080) -> str:
    """現在のビューポートを 1 枚画像で保存する（A/B・パリティ比較用）。"""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    panel = environment.model_panel()
    if panel:
        cmds.setFocus(panel)
    frame = cmds.currentTime(query=True)
    cmds.playblast(
        frame=[frame], format="image", compression="png", completeFilename=path, viewer=False,
        showOrnaments=False, offScreen=True, percent=100, widthHeight=(width, height),
        forceOverwrite=True, clearCache=True,
    )
    return path


# ---------------------------------------------------------------- シーン内メタデータ / デバッグ


def remember_look_path(path: str) -> None:
    cmds.fileInfo("tdriveToonLook", to_repo_path(path))


def remembered_look_path() -> str | None:
    v = cmds.fileInfo("tdriveToonLook", query=True)
    return from_repo_path(v[0]) if v else None


def dump_state() -> str:
    """MCP からのデバッグ用: プレビュー状態を JSON で返す。"""
    return json.dumps(
        {
            "shaders": {s: source_material(s) for s in preview_shaders()},
            "active": is_active(),
            "env": {k: list(v) if isinstance(v, tuple) else v for k, v in _env.items()},
            "parityProblems": environment.parity_problems(),
        },
        ensure_ascii=False,
    )
