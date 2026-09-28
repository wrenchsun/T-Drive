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
MASK_COLOR_SET = "tdToonMask"  # docs/03 §4
SMOOTH_NORMAL_UV = "tdSmoothNormal"  # docs/03 §5

# 現在のプレビュー環境（プロファイル + ツールでの上書き）。Look には保存しない
_env: dict[str, Any] = {"profile": None, "lightDir": (0.4, 0.6, 0.7), "lightColor": (1.0, 1.0, 1.0), "tonemap": 0}


# ---------------------------------------------------------------- 準備


def ensure_plugin() -> None:
    if not cmds.pluginInfo(NODE_TYPE, query=True, loaded=True):
        cmds.loadPlugin(NODE_TYPE, quiet=True)


# ---------------------------------------------------------------- シーン走査


def scene_materials() -> dict[str, list[str]]:
    """メッシュに割り当てられている元マテリアル名 → メッシュ(transform)一覧。割り当ての無いメッシュは対象外。"""
    result: dict[str, set[str]] = {}
    for sg in cmds.ls(type="shadingEngine"):
        shader = _surface_shader(sg)
        if not shader:
            continue
        meshes = set()
        for m in cmds.sets(sg, query=True) or []:
            node = m.split(".")[0]
            if cmds.nodeType(node) == "mesh":
                node = cmds.listRelatives(node, parent=True)[0]
            meshes.add(node)
        if meshes:
            result.setdefault(source_material(shader), set()).update(meshes)
    return {k: sorted(v) for k, v in sorted(result.items())}


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


def enable(materials: dict[str, dict[str, Any]]) -> list[str]:
    """resolve 済みのマテリアル値でプレビューを有効化する。シーンに無かったマテリアル名を返す。"""
    ensure_plugin()
    missing = []
    for mat, values in materials.items():
        if not cmds.objExists(mat):
            missing.append(mat)
            continue
        shader = _ensure_preview_shader(mat)
        apply_values(mat, values)
        _update_mesh_streams(mat, shader)
        _swap(_shading_group(mat), _shading_group(shader))
    apply_environment()
    return missing


def disable() -> None:
    """元マテリアルの割り当てに戻す（プレビューシェーダーは残し、次回すぐ有効化できるようにする）。"""
    for shader in preview_shaders():
        src = source_material(shader)
        if cmds.objExists(src):
            _swap(_shading_group(shader), _shading_group(src))


def delete_all() -> None:
    disable()
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


def _update_mesh_streams(mat: str, shader: str) -> None:
    """頂点カラー tdToonMask を全メッシュが持つときだけ頂点マスクを有効にする（無いメッシュを黒で壊さない）。"""
    meshes = meshes_of(mat)
    has_mask = bool(meshes) and all(MASK_COLOR_SET in (cmds.polyColorSet(m, query=True, allColorSets=True) or []) for m in meshes)
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


def apply_values(mat: str, values: dict[str, Any]) -> None:
    """values に含まれる項目だけを反映する（部分更新可）。"""
    shader = preview_shader_of(mat)
    if not cmds.objExists(shader):
        return
    common = values.get("common", {})
    if "blend" in common or "doubleSided" in common:
        current = {"blend": _get_blend(shader), "doubleSided": cmds.getAttr(f"{shader}.technique") == "OpaqueDoubleSided"}
        current.update({k: common[k] for k in ("blend", "doubleSided") if k in common})
        _set_technique(shader, technique_for(current))
        _set(shader, "AlphaClip", current["blend"] == "Cutout")
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


def _get_blend(shader: str) -> str:
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
    direction, color = environment.character_light(prof)
    _env.update(profile=name, lightDir=direction, lightColor=color, tonemap=environment.tonemap_mode(prof))
    environment.apply_color_management()
    apply_environment()
    return environment.profile_warnings(prof)


def set_light(direction: tuple[float, float, float] | None = None, color: tuple[float, float, float] | None = None) -> None:
    if direction is not None:
        _env["lightDir"] = tuple(direction)
    if color is not None:
        _env["lightColor"] = tuple(color)
    apply_environment()


def apply_environment() -> None:
    for shader in preview_shaders():
        _set(shader, "PreviewLightDir", list(_env["lightDir"]))
        _set(shader, "PreviewLightColor", list(_env["lightColor"]))
        _set(shader, "PreviewTonemap", int(_env["tonemap"]))


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
