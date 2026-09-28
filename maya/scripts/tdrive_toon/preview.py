"""Viewport 2.0 プレビュー: 元マテリアルを T-Drive Toon の GLSLShader に差し替え、Look の値を流し込む。

- 元マテリアルは削除しない。プレビュー用 SG へ面を移し、元の割り当ては JSON で記録して復元できる。
- Look 値の反映は setAttr だけなので、スライダー操作に追従できる速さで動く。
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from maya import cmds

from . import REPO_ROOT, params

SHADER_FILE = (REPO_ROOT / "maya" / "shaders" / "TDriveToon.ogsfx").as_posix()
SUFFIX = "_tdToon"
SOURCE_ATTR = "tdSourceMaterial"  # プレビューシェーダー → 元マテリアル名
MEMBERS_ATTR = "tdSourceMembers"  # 元 SG のメンバー（復元用 JSON）

# プレビュー環境（Look には保存しない。Unity ではシーンのライト等に相当）
_env = {"lightYaw": 35.0, "lightPitch": 35.0, "lightColor": [1.0, 1.0, 1.0], "refHeight": 1080.0}


# ---------------------------------------------------------------- 準備


def ensure_plugin() -> None:
    if not cmds.pluginInfo("glslShader", query=True, loaded=True):
        cmds.loadPlugin("glslShader", quiet=True)


def rendering_engine_warning() -> str | None:
    """GLSLShader は OpenGL でしか描画されない。DirectX 11 なら警告文を返す。"""
    engine = cmds.optionVar(query="vp2RenderingEngine") if cmds.optionVar(exists="vp2RenderingEngine") else ""
    if engine and "OpenGL" not in engine:
        return (
            "Viewport 2.0 のレンダリングエンジンが OpenGL ではありません。\n"
            "Windows > Settings/Preferences > Preferences > Display > Viewport 2.0 > Rendering engine を\n"
            "「OpenGL - Core Profile」にして Maya を再起動してください。"
        )
    return None


# ---------------------------------------------------------------- シーン走査


def scene_materials() -> dict[str, list[str]]:
    """メッシュに割り当てられている元マテリアル名 → メッシュ(transform)一覧。"""
    result: dict[str, set[str]] = {}
    for sg in cmds.ls(type="shadingEngine"):
        shader = _surface_shader(sg)
        if not shader:
            continue
        mat = source_material(shader)
        members = cmds.sets(sg, query=True) or []
        meshes = {m.split(".")[0] for m in members}
        meshes = {cmds.listRelatives(m, parent=True, fullPath=False)[0] if cmds.nodeType(m) == "mesh" else m for m in meshes}
        if meshes:
            result.setdefault(mat, set()).update(meshes)
    return {k: sorted(v) for k, v in sorted(result.items())}


def source_material(shader: str) -> str:
    if cmds.attributeQuery(SOURCE_ATTR, node=shader, exists=True):
        return cmds.getAttr(f"{shader}.{SOURCE_ATTR}")
    return shader


def _surface_shader(sg: str) -> str | None:
    src = cmds.listConnections(f"{sg}.surfaceShader", source=True, destination=False) or []
    return src[0] if src else None


def _shading_group(shader: str) -> str | None:
    sgs = cmds.listConnections(shader, type="shadingEngine") or []
    return sgs[0] if sgs else None


def materials_on_selection() -> list[str]:
    """選択中のメッシュ / 面に割り当てられている元マテリアル名。"""
    sel = cmds.ls(selection=True, long=True) or []
    shapes = cmds.ls(sel, dagObjects=True, type="mesh", long=True) or []
    faces = cmds.filterExpand(sel, selectionMask=34) or []
    mats: set[str] = set()
    for sg in cmds.listConnections(shapes, type="shadingEngine") or []:
        if (sh := _surface_shader(sg)):
            mats.add(source_material(sh))
    for f in faces:
        for sg in cmds.listSets(object=f, type=1) or []:
            if cmds.nodeType(sg) == "shadingEngine" and (sh := _surface_shader(sg)):
                mats.add(source_material(sh))
    return sorted(mats)


def base_texture_of(material: str) -> str | None:
    """元マテリアルのカラーに繋がる file テクスチャのパス（リポジトリ内ならリポジトリ相対）。"""
    for attr in ("color", "baseColor", "diffuseColor"):
        if not cmds.attributeQuery(attr, node=material, exists=True):
            continue
        files = cmds.listConnections(f"{material}.{attr}", type="file") or []
        if files:
            return to_repo_path(cmds.getAttr(f"{files[0]}.fileTextureName"))
    return None


def to_repo_path(path: str) -> str:
    p = Path(path)
    try:
        return p.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return p.as_posix()


def from_repo_path(path: str) -> str:
    p = Path(path)
    return (p if p.is_absolute() else REPO_ROOT / p).as_posix()


# ---------------------------------------------------------------- 差し替え


def preview_shader_of(material: str) -> str:
    return f"{material}{SUFFIX}"


def is_active() -> bool:
    return bool(cmds.ls(f"*{SUFFIX}", type="GLSLShader")) and any(
        cmds.sets(_shading_group(s), query=True) for s in cmds.ls(f"*{SUFFIX}", type="GLSLShader") if _shading_group(s)
    )


def enable(materials: dict[str, dict[str, Any]]) -> list[str]:
    """resolve 済みのマテリアル値でプレビューを有効化。作れなかったマテリアル名を返す。"""
    ensure_plugin()
    failed = []
    for mat, values in materials.items():
        if not cmds.objExists(mat):
            failed.append(mat)
            continue
        shader = _ensure_preview_shader(mat)
        apply_values(mat, values)
        _swap(mat, shader)
    apply_environment()
    return failed


def disable() -> None:
    """元マテリアルの割り当てに戻す（プレビューシェーダーは残し、次回すぐ有効化できるようにする）。"""
    for shader in cmds.ls(f"*{SUFFIX}", type="GLSLShader"):
        sg = _shading_group(shader)
        src = source_material(shader)
        src_sg = _shading_group(src) if cmds.objExists(src) else None
        if not (sg and src_sg):
            continue
        members = cmds.sets(sg, query=True) or []
        if members:
            cmds.sets(members, edit=True, forceElement=src_sg)


def delete_all() -> None:
    disable()
    for shader in cmds.ls(f"*{SUFFIX}", type="GLSLShader"):
        sg = _shading_group(shader)
        files = [n for n in (cmds.listConnections(shader, type="file") or [])]
        cmds.delete([n for n in (shader, sg, *files) if n and cmds.objExists(n)])


def _ensure_preview_shader(mat: str) -> str:
    name = preview_shader_of(mat)
    if cmds.objExists(name):
        return name
    shader = cmds.shadingNode("GLSLShader", asShader=True, name=name)
    cmds.setAttr(f"{shader}.shader", SHADER_FILE, type="string")
    cmds.addAttr(shader, longName=SOURCE_ATTR, dataType="string")
    cmds.setAttr(f"{shader}.{SOURCE_ATTR}", mat, type="string")
    sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name=f"{name}SG")
    cmds.connectAttr(f"{shader}.outColor", f"{sg}.surfaceShader", force=True)
    return shader


def _swap(mat: str, shader: str) -> None:
    src_sg = _shading_group(mat)
    dst_sg = _shading_group(shader)
    members = (cmds.sets(src_sg, query=True) or []) if src_sg else []
    if members:
        cmds.sets(members, edit=True, forceElement=dst_sg)


def reload_shader_file() -> None:
    """ogsfx を編集した後に呼ぶ。全プレビューシェーダーを再コンパイルする。"""
    for shader in cmds.ls(f"*{SUFFIX}", type="GLSLShader"):
        cmds.setAttr(f"{shader}.shader", "", type="string")
        cmds.setAttr(f"{shader}.shader", SHADER_FILE, type="string")


# ---------------------------------------------------------------- 値の反映


def apply_values(mat: str, values: dict[str, Any]) -> None:
    """values に含まれる項目だけを反映する（部分更新可）。"""
    shader = preview_shader_of(mat)
    if not cmds.objExists(shader):
        return
    for field, value in values.get("common", {}).items():
        _apply_common(shader, field, value)
    for key, value in values.get("specific", {}).items():
        p = params.PARAMS_BY_UNITY.get(key)
        if p is None:
            continue
        if p.kind == params.TEXTURE:
            _set_texture(shader, p.maya, value)
        else:
            _set(shader, p.maya, value)


def apply_value(mat: str, key: str, value: Any) -> None:
    """1 項目だけ反映（スライダー操作用）。key は look.set_value と同じ形式。"""
    if key == "renderQueueOffset":
        return  # Maya プレビューでは描画順を再現しない（Phase 2）
    if key.startswith("common."):
        apply_values(mat, {"common": {key.split(".", 1)[1]: value}})
    else:
        apply_values(mat, {"specific": {key: value}})


def _apply_common(shader: str, field: str, value: Any) -> None:
    if field == "blend":
        _set(shader, "technique", "Transparent" if value == "Transparent" else "Opaque")
        _set(shader, "AlphaClip", value == "Cutout")
    elif field == "cutoff":
        _set(shader, "Cutoff", value)
    elif field == "albedoTint":
        _set(shader, "BaseColor", value)
    elif field == "albedo":
        _set_texture(shader, "BaseMap", value)
    # normal / emission / doubleSided は Maya プレビュー対象外（Unity 側でのみ使用）


def _set(node: str, attr: str, value: Any) -> None:
    plug = f"{node}.{attr}"
    if attr == "technique":
        if cmds.getAttr(plug) != value:
            cmds.setAttr(plug, value, type="string")
        return
    if not cmds.attributeQuery(attr, node=node, exists=True):
        return
    if isinstance(value, bool):
        cmds.setAttr(plug, value)
    elif isinstance(value, (int, float)):
        cmds.setAttr(plug, float(value))
    elif isinstance(value, list):
        children = cmds.attributeQuery(attr, node=node, listChildren=True) or []
        if len(children) >= 3:
            cmds.setAttr(plug, *[float(v) for v in value[: len(children)]], type=f"double{len(children)}" if len(children) > 3 else "double3")
        # float4 の色は Maya 側で color(float3) + <name>A に分かれることがある
        for alpha in (f"{attr}A", f"{attr}_A", f"{attr}Alpha"):
            if len(value) == 4 and cmds.attributeQuery(alpha, node=node, exists=True):
                cmds.setAttr(f"{node}.{alpha}", float(value[3]))


def _set_texture(shader: str, attr: str, path: str | None) -> None:
    if not cmds.attributeQuery(attr, node=shader, exists=True):
        return
    enabled = f"{attr}Enabled"
    file_node = f"{shader}_{attr}"
    if not path:
        if cmds.attributeQuery(enabled, node=shader, exists=True):
            cmds.setAttr(f"{shader}.{enabled}", False)
        return
    if not cmds.objExists(file_node):
        file_node = cmds.shadingNode("file", asTexture=True, isColorManaged=True, name=file_node)
        cmds.connectAttr(f"{file_node}.outColor", f"{shader}.{attr}", force=True)
    full = from_repo_path(path)
    if cmds.getAttr(f"{file_node}.fileTextureName") != full:
        cmds.setAttr(f"{file_node}.fileTextureName", full, type="string")
    if attr != "BaseMap":
        # マスク類はデータなので色管理しない
        cmds.setAttr(f"{file_node}.ignoreColorSpaceFileRules", True)
        cmds.setAttr(f"{file_node}.colorSpace", "Raw", type="string")
    if cmds.attributeQuery(enabled, node=shader, exists=True):
        cmds.setAttr(f"{shader}.{enabled}", True)


# ---------------------------------------------------------------- 環境（ライト / カメラ）


def environment() -> dict[str, Any]:
    return dict(_env)


def set_environment(**kwargs: Any) -> None:
    _env.update(kwargs)
    apply_environment()


def light_dir() -> list[float]:
    yaw, pitch = math.radians(_env["lightYaw"]), math.radians(_env["lightPitch"])
    return [math.sin(yaw) * math.cos(pitch), math.sin(pitch), math.cos(yaw) * math.cos(pitch)]


def apply_environment() -> None:
    d = light_dir()
    for shader in cmds.ls(f"*{SUFFIX}", type="GLSLShader"):
        _set(shader, "PreviewLightDir", d)
        _set(shader, "PreviewLightColor", _env["lightColor"])
        _set(shader, "PreviewViewportHeight", _env["refHeight"])


CAMERA_PRESETS = {"正面": 0.0, "3/4": 35.0, "横": 90.0, "後ろ": 180.0, "3/4 左": -35.0}
CAMERA_NAME = "tdPreviewCam"


def frame_camera(yaw_deg: float, target: str = "head", fov_fit: float = 1.4) -> str:
    """キャラクター（全メッシュの bbox、target=head なら上端付近）を指定ヨー角から見るカメラを作る。"""
    meshes = cmds.ls(type="mesh", noIntermediate=True, long=True)
    if not meshes:
        raise RuntimeError("メッシュがありません")
    xmin, ymin, zmin, xmax, ymax, zmax = cmds.exactWorldBoundingBox(meshes)
    height = ymax - ymin
    if target == "head":
        cy, span = ymax - height * 0.1, height * 0.2
    else:
        cy, span = (ymin + ymax) / 2, height
    cx, cz = (xmin + xmax) / 2, (zmin + zmax) / 2
    if not cmds.objExists(CAMERA_NAME):
        cam, _ = cmds.camera(name=CAMERA_NAME)
        cmds.rename(cam, CAMERA_NAME)
    cam = CAMERA_NAME
    shape = cmds.listRelatives(cam, shapes=True)[0]
    cmds.setAttr(f"{shape}.focalLength", 50)
    vfov = 2 * math.atan(cmds.getAttr(f"{shape}.verticalFilmAperture") * 25.4 / 2 / 50)
    dist = span * fov_fit / 2 / math.tan(vfov / 2)
    yaw = math.radians(yaw_deg)
    cmds.xform(cam, worldSpace=True, translation=(cx + math.sin(yaw) * dist, cy, cz + math.cos(yaw) * dist))
    cmds.xform(cam, worldSpace=True, rotation=(0, yaw_deg, 0))
    panel = _model_panel()
    if panel:
        cmds.modelPanel(panel, edit=True, camera=cam)
    return cam


def _model_panel() -> str | None:
    panel = cmds.getPanel(withFocus=True)
    if panel and cmds.getPanel(typeOf=panel) == "modelPanel":
        return panel
    panels = cmds.getPanel(type="modelPanel") or []
    visible = set(cmds.getPanel(visiblePanels=True) or [])
    for p in panels:
        if p in visible:
            return p
    return panels[0] if panels else None


def capture(path: str, width: int = 960, height: int = 1080) -> str:
    """現在のビューポートを 1 枚画像で保存する（A/B 比較用）。"""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    panel = _model_panel()
    frame = cmds.currentTime(query=True)
    kwargs = dict(
        frame=[frame], format="image", compression="png", completeFilename=path,
        viewer=False, showOrnaments=False, offScreen=True, percent=100,
        widthHeight=(width, height), forceOverwrite=True, clearCache=True,
    )
    if panel:
        cmds.setFocus(panel)
    cmds.playblast(**kwargs)
    return path


# ---------------------------------------------------------------- シーン内メタデータ


def remember_look_path(path: str) -> None:
    cmds.fileInfo("tdriveToonLook", to_repo_path(path))


def remembered_look_path() -> str | None:
    v = cmds.fileInfo("tdriveToonLook", query=True)
    return from_repo_path(v[0]) if v else None


def dump_state() -> str:
    """MCP からのデバッグ用: プレビュー状態を JSON で返す。"""
    shaders = cmds.ls(f"*{SUFFIX}", type="GLSLShader")
    return json.dumps(
        {
            "shaders": {s: source_material(s) for s in shaders},
            "active": is_active(),
            "env": _env,
            "warning": rendering_engine_warning(),
        },
        ensure_ascii=False,
    )
