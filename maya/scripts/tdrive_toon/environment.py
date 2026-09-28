"""Unity 環境プロファイル（looks/_env/*.json）の読み込みと、Maya 側の描画環境合わせ（docs/09）。

MS2026 はプロトタイプで値が変わるため、ここには値を書かない。値はすべてプロファイルから読む。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from maya import cmds

from . import REPO_ROOT, envmath

PROFILE_DIR = REPO_ROOT / "looks" / "_env"
TONEMAP_MODES = {"None": 0, "Neutral": 1}  # ToonCore の TOON_TONEMAP_*。ACES は未対応（docs/09）

# OCIO 設定によって名前が違うので候補を順に探す
_RENDERING_SPACE = ("Linear Rec.709 (sRGB)", "scene-linear Rec.709-sRGB", "Utility - Linear - sRGB")
_VIEW_TRANSFORM = ("Un-tone-mapped (sRGB)", "Un-tone-mapped", "sRGB gamma (legacy)")
TEXTURE_SRGB = ("sRGB Encoded Rec.709 (sRGB)", "sRGB", "Utility - sRGB - Texture")
TEXTURE_RAW = ("Raw",)


def list_profiles() -> list[str]:
    return sorted(p.stem for p in PROFILE_DIR.glob("*.json"))


def default_profile() -> str | None:
    """プロファイル未選択時に使うもの（一覧の先頭）。値をコードに書かないため名前も固定しない。"""
    names = list_profiles()
    return names[0] if names else None


def load_profile(name: str) -> dict[str, Any]:
    prof = json.loads((PROFILE_DIR / f"{name}.json").read_text(encoding="utf-8"))
    prof.setdefault("name", name)
    return prof


def profile_warnings(prof: dict[str, Any]) -> list[str]:
    w = []
    if prof.get("colorSpace") != "Linear":
        w.append(f"colorSpace={prof.get('colorSpace')} は未対応（Linear のみ）")
    if prof.get("tonemapping") not in TONEMAP_MODES:
        w.append(f"tonemapping={prof.get('tonemapping')} は未対応。プレビューはトーンマップ無しで表示（要 Unity で確認）")
    return w


def tonemap_mode(prof: dict[str, Any]) -> int:
    return TONEMAP_MODES.get(prof.get("tonemapping", "None"), 0)


def character_light(prof: dict[str, Any]) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """(表面→光源 方向 [Maya 座標], 色 × 強度)。"""
    cl = prof["characterLight"]
    direction = envmath.light_dir_to_light_maya(tuple(cl["rotation"]))
    # Unity の Light.color も sRGB 値としてリニア化され、強度が掛かる
    color = tuple(c * cl.get("intensity", 1.0) for c in envmath.srgb_color_to_linear(cl["color"]))
    return direction, color


# ---------------------------------------------------------------- 色管理


def _pick(candidates: tuple[str, ...], available: list[str]) -> str | None:
    for c in candidates:
        if c in available:
            return c
    return None


def pick_texture_space(srgb: bool) -> str | None:
    names = cmds.colorManagementPrefs(query=True, inputSpaceNames=True) or []
    return _pick(TEXTURE_SRGB if srgb else TEXTURE_RAW, names)


def apply_color_management() -> dict[str, str | None]:
    """Unity(Linear + sRGB 出力) に合わせる: リニア Rec.709 のレンダリング空間 + トーンマップ無しのビュー変換。

    トーンマップはシェーダー最終段で Unity と同じ関数を掛ける（ToonCore）ので、ビュー変換では掛けない。
    """
    cmds.colorManagementPrefs(edit=True, cmEnabled=True)
    rs = _pick(_RENDERING_SPACE, cmds.colorManagementPrefs(query=True, renderingSpaceNames=True) or [])
    vt = _pick(_VIEW_TRANSFORM, cmds.colorManagementPrefs(query=True, viewTransformNames=True) or [])
    if rs:
        cmds.colorManagementPrefs(edit=True, renderingSpaceName=rs)
    if vt:
        cmds.colorManagementPrefs(edit=True, viewTransformName=vt)
    return {"renderingSpace": rs, "viewTransform": vt}


# ---------------------------------------------------------------- パリティ状態


def parity_status() -> list[tuple[str, str, str, bool]]:
    """(項目, 期待値, 現在値, OK) の一覧。UI の警告表示と MCP 検証に使う。"""
    rows = []
    engine = cmds.optionVar(query="vp2RenderingEngine") if cmds.optionVar(exists="vp2RenderingEngine") else ""
    rows.append(("VP2 レンダリングエンジン", "DirectX11", engine, "DirectX" in engine))
    cm = bool(cmds.colorManagementPrefs(query=True, cmEnabled=True))
    rows.append(("カラーマネジメント", "ON", "ON" if cm else "OFF", cm))
    rs = cmds.colorManagementPrefs(query=True, renderingSpaceName=True)
    rows.append(("レンダリング空間", " / ".join(_RENDERING_SPACE[:1]), rs, rs in _RENDERING_SPACE))
    vt = cmds.colorManagementPrefs(query=True, viewTransformName=True)
    rows.append(("ビュー変換", _VIEW_TRANSFORM[0], vt, vt in _VIEW_TRANSFORM))
    return rows


def parity_problems() -> list[str]:
    return [f"{name}: {actual}（期待: {expected}）" for name, expected, actual, ok in parity_status() if not ok]


# ---------------------------------------------------------------- カメラ

CAMERA_NAME = "tdPreviewCam"
CAMERA_PRESETS = {"正面": 0.0, "3/4": 35.0, "横": 90.0, "後ろ": 180.0, "3/4 左": -35.0}


def frame_camera(prof: dict[str, Any], yaw_deg: float, target: str = "head", fit: float = 1.3) -> str:
    """プロファイルの縦 FOV・Near/Far を持つカメラで、キャラクターを指定ヨー角から見る。"""
    meshes = [m for m in cmds.ls(type="mesh", noIntermediate=True, long=True) if _visible(m)]
    if not meshes:
        raise RuntimeError("表示中のメッシュがありません")
    xmin, ymin, zmin, xmax, ymax, zmax = cmds.exactWorldBoundingBox(meshes)
    height = ymax - ymin
    if target == "head":
        center, span = ((xmin + xmax) / 2, ymax - height * 0.1, (zmin + zmax) / 2), height * 0.2
    else:
        center, span = ((xmin + xmax) / 2, (ymin + ymax) / 2, (zmin + zmax) / 2), height

    cam_prof = prof["camera"]
    if not cmds.objExists(CAMERA_NAME):
        cam, _shape = cmds.camera()
        cmds.rename(cam, CAMERA_NAME)
    shape = cmds.listRelatives(CAMERA_NAME, shapes=True)[0]
    cmds.setAttr(f"{shape}.filmFit", 2)  # Vertical = Unity と同じ縦 FOV 基準
    vfa = cmds.getAttr(f"{shape}.verticalFilmAperture")
    cmds.setAttr(f"{shape}.focalLength", envmath.focal_length_for_vertical_fov(cam_prof["verticalFov"], vfa))
    cmds.setAttr(f"{shape}.nearClipPlane", cam_prof["near"] * envmath.UNITY_TO_MAYA_LENGTH)
    cmds.setAttr(f"{shape}.farClipPlane", cam_prof["far"] * envmath.UNITY_TO_MAYA_LENGTH)

    import math

    distance = span * fit / 2 / math.tan(math.radians(cam_prof["verticalFov"]) / 2)
    pos, rot = envmath.orbit_camera(center, distance, yaw_deg)
    cmds.xform(CAMERA_NAME, worldSpace=True, translation=pos, rotation=rot)
    panel = model_panel()
    if panel:
        cmds.modelPanel(panel, edit=True, camera=CAMERA_NAME)
    return CAMERA_NAME


def _visible(shape: str) -> bool:
    path = shape
    while path:
        if not cmds.getAttr(f"{path}.visibility"):
            return False
        parent = cmds.listRelatives(path, parent=True, fullPath=True)
        path = parent[0] if parent else None
    return True


def prepare_panel(panel: str | None = None) -> str | None:
    """プレビュー用にモデルパネルを整える（テクスチャ表示 ON。OFF だと dx11Shader が使われない。docs/09 §6）。"""
    panel = panel or model_panel()
    if panel:
        cmds.modelEditor(panel, edit=True, displayTextures=True, displayAppearance="smoothShaded")
    return panel


def capture_panel_settings(panel: str) -> None:
    """キャプチャ時だけ邪魔な表示（ジョイント・グリッド・HUD 等）を消す。"""
    cmds.modelEditor(
        panel, edit=True, joints=False, grid=False, locators=False, nurbsCurves=False, handles=False,
        ikHandles=False, deformers=False, manipulators=False, selectionHiliteDisplay=False, headsUpDisplay=False,
    )


def model_panel() -> str | None:
    panel = cmds.getPanel(withFocus=True)
    if panel and cmds.getPanel(typeOf=panel) == "modelPanel":
        return panel
    visible = set(cmds.getPanel(visiblePanels=True) or [])
    panels = cmds.getPanel(type="modelPanel") or []
    for p in panels:
        if p in visible:
            return p
    return panels[0] if panels else None
