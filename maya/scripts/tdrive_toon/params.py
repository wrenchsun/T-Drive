"""シェーダーパラメータ契約（Maya プレビュー / Look JSON / Unity シェーダーの共通定義）。

このファイルが「どのパラメータが存在するか」の唯一の定義。
- `unity` : Unity 側シェーダープロパティ名。D-Drive MaterialData の Specific[].Property にそのまま入る
- `maya`  : Maya プレビューシェーダー (ogsfx) の uniform 名 = GLSLShader ノードのアトリビュート名
- 固有パラメータは必ず `_Toon` 接頭辞にする。D-Drive の MaterialCommonNaming が
  共通チャンネル / 描画ステートとして予約している名前（_BaseMap, _ZTest, _Cull 等）と衝突させないため。

互換性: `unity` 名の変更・削除は MAJOR（docs/06_release_versioning.md）。追加は MINOR。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

FLOAT = "float"
COLOR = "color"
TEXTURE = "texture"


@dataclass(frozen=True)
class Param:
    unity: str
    kind: str
    default: Any
    group: str
    label: str
    min: float = 0.0
    max: float = 1.0

    @property
    def maya(self) -> str:
        return self.unity.lstrip("_")


# D-Drive MaterialCommon に対応する項目。Look JSON では "common" に入る。
COMMON_FIELDS = {
    "albedo": None,  # テクスチャパス（リポジトリ相対）
    "albedoTint": [1.0, 1.0, 1.0, 1.0],
    "normal": None,
    "normalScale": 1.0,
    "emission": None,
    "emissionColor": [0.0, 0.0, 0.0, 1.0],
    "emissionIntensity": 0.0,
    "blend": "Opaque",  # Opaque / Cutout / Transparent（D-Drive BlendType）
    "cutoff": 0.5,
    "doubleSided": False,
}
BLEND_TYPES = ("Opaque", "Cutout", "Transparent")
# D-Drive MaterialData.BaseRenderQueue と同じ値
BASE_RENDER_QUEUE = {"Opaque": 2000, "Cutout": 2450, "Transparent": 3000}

# D-Drive の Specific に入る Toon 固有パラメータ（docs/03_shader_spec.md §7.2 と一致させる）
SPECIFIC_PARAMS: tuple[Param, ...] = (
    # --- 影（2 階調）T-01 / T-04 ---
    Param("_ToonShadeColor", COLOR, [0.78, 0.72, 0.86, 1.0], "Shadow", "影色"),
    Param("_ToonShadeThreshold", FLOAT, 0.5, "Shadow", "影の境界", 0.0, 1.0),
    Param("_ToonShadeFeather", FLOAT, 0.02, "Shadow", "境界ぼかし", 0.001, 0.5),
    Param("_ToonShadowStrength", FLOAT, 1.0, "Shadow", "影の強さ", 0.0, 1.0),
    # --- マスク T-19。頂点カラーに乗算。null = 白（何もしない） ---
    Param("_ToonMaskMap", TEXTURE, None, "Mask", "Toon マスク (RGBA)"),
    # --- 固定色 T-08（頂点カラー A を黒く塗った所に乗る） ---
    Param("_ToonTintColor", COLOR, [1.0, 0.6, 0.6, 1.0], "Tint", "固定色"),
    Param("_ToonTintStrength", FLOAT, 0.0, "Tint", "固定色の強さ", 0.0, 1.0),
    # --- アウトライン T-05 / T-06。線幅は「1080p 換算 px」= 画面高さに対する割合 ---
    Param("_ToonOutlineColor", COLOR, [0.28, 0.2, 0.2, 1.0], "Outline", "線色"),
    Param("_ToonOutlineBaseMix", FLOAT, 0.5, "Outline", "線色にベース色を混ぜる", 0.0, 1.0),
    Param("_ToonOutlineWidth", FLOAT, 1.0, "Outline", "線幅 (px@1080p)", 0.0, 10.0),
    Param("_ToonOutlineSmoothNormal", FLOAT, 1.0, "Outline", "スムーズ法線を使う (0/1)", 0.0, 1.0),
)

# 名前だけ先に確定している P1 以降のパラメータ（docs/03 §7.3）。追加時にここから SPECIFIC_PARAMS へ移す
RESERVED_NAMES = (
    "_ToonDepthOffset",
    "_ToonRimColor", "_ToonRimPower", "_ToonRimStrength",
    "_ToonHairHighlightMap", "_ToonHairHighlightColor", "_ToonHairHighlightShift",
    "_ToonOutlineDistanceScale",
    "_ToonFaceShadowMap", "_ToonFaceForward", "_ToonFaceRight",
    "_ToonShade2Color", "_ToonShade2Threshold",
)

PARAMS_BY_UNITY = {p.unity: p for p in SPECIFIC_PARAMS}
GROUPS = tuple(dict.fromkeys(p.group for p in SPECIFIC_PARAMS))

# Unity 側シェーダー名（MS2026 に新規作成予定。docs/05_unity_port_plan.md）
UNITY_SHADER = "MS2026/Toon"


def default_specific() -> dict[str, Any]:
    return {p.unity: _copy(p.default) for p in SPECIFIC_PARAMS if p.default is not None}


def _copy(v: Any) -> Any:
    return list(v) if isinstance(v, list) else v


def validate_value(param: Param, value: Any) -> str | None:
    """値が契約に合わなければエラーメッセージを返す。"""
    if param.kind == FLOAT:
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return f"{param.unity}: 数値が必要"
    elif param.kind == COLOR:
        if not (isinstance(value, list) and len(value) == 4 and all(isinstance(c, (int, float)) for c in value)):
            return f"{param.unity}: [r, g, b, a] が必要"
    elif param.kind == TEXTURE:
        if value is not None and not isinstance(value, str):
            return f"{param.unity}: テクスチャパス文字列か null が必要"
    return None
