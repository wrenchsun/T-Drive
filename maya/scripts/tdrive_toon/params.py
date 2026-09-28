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
    # ---------------- P1（v0.2 で追加。既定値では P0 と同じ見た目 = MINOR） ----------------
    # --- 線幅の距離補正 T-18 ---
    Param("_ToonOutlineDistanceScale", FLOAT, 0.0, "Outline", "遠いほど線を細く", 0.0, 1.0),
    Param("_ToonOutlineRefDistance", FLOAT, 2.0, "Outline", "細くし始める距離 (m)", 0.1, 20.0),
    # --- ライト色の影響 T-11（0 = ライトの色に影響されない。目のハイライト等） ---
    Param("_ToonLightColorInfluence", FLOAT, 1.0, "Light", "ライト色の影響", 0.0, 1.0),
    # --- 眉・目を髪の上に T-09（視線方向にカメラへ寄せる。m 単位） ---
    Param("_ToonDepthOffset", FLOAT, 0.0, "Depth", "手前に出す量 (m)", 0.0, 0.2),
    # --- リム T-13（明側のみ） ---
    Param("_ToonRimColor", COLOR, [1.0, 1.0, 1.0, 1.0], "Rim", "リム色"),
    Param("_ToonRimPower", FLOAT, 4.0, "Rim", "リムの鋭さ", 0.5, 16.0),
    Param("_ToonRimStrength", FLOAT, 0.0, "Rim", "リムの強さ", 0.0, 1.0),
    # --- 髪ハイライト T-12（帯のテクスチャ R をカメラの上下でずらす。未設定 = 出ない） ---
    Param("_ToonHairHighlightMap", TEXTURE, None, "Hair", "ハイライトの帯（R）"),
    Param("_ToonHairHighlightColor", COLOR, [1.0, 1.0, 0.95, 1.0], "Hair", "ハイライト色"),
    Param("_ToonHairHighlightShift", FLOAT, 0.0, "Hair", "視線で帯をずらす量", -0.5, 0.5),
    # ---------------- P2（Phase 3。既定値では従来と同じ見た目 = MINOR） ----------------
    # --- 2 影 T-27 ---
    Param("_ToonShade2Color", COLOR, [0.6, 0.52, 0.72, 1.0], "Shadow", "2 影の色"),
    Param("_ToonShade2Threshold", FLOAT, 0.25, "Shadow", "2 影の境界", 0.0, 1.0),
    Param("_ToonShade2Strength", FLOAT, 0.0, "Shadow", "2 影の強さ", 0.0, 1.0),
    # --- 部位別の色補正 T-28 ---
    Param("_ToonSaturation", FLOAT, 1.0, "Color", "彩度", 0.0, 2.0),
    Param("_ToonBrightness", FLOAT, 1.0, "Color", "明るさ", 0.0, 2.0),
    # --- 線の太さの方向依存 T-26 ---
    Param("_ToonOutlineShadowSide", FLOAT, 1.0, "Outline", "影側の線の太さ（倍）", 0.0, 3.0),
    Param("_ToonOutlineBottom", FLOAT, 1.0, "Outline", "下向きの面の線の太さ（倍）", 0.0, 3.0),
    # --- MatCap T-30（未設定 = 黒 = 何も足さない） ---
    Param("_ToonMatCapMap", TEXTURE, None, "MatCap", "MatCap テクスチャ"),
    Param("_ToonMatCapStrength", FLOAT, 0.0, "MatCap", "MatCap の強さ", 0.0, 1.0),
    # --- 奥行き圧縮 T-22（量はキャラクター単位 characterSettings.depthCompression） ---
    Param("_ToonDepthCompressWeight", FLOAT, 0.0, "Depth", "奥行き圧縮の効かせ具合", 0.0, 1.0),
    # --- SDF 顔影マップ T-21（顔の向きはキャラクター単位 characterSettings.faceShadow） ---
    Param("_ToonFaceShadowMap", TEXTURE, None, "FaceShadow", "顔影マップ（SDF、R）"),
    Param("_ToonFaceShadowWeight", FLOAT, 0.0, "FaceShadow", "顔影マップの効かせ具合", 0.0, 1.0),
)

# 名前だけ先に確定しているパラメータ（docs/03 §7.3）。追加時にここから SPECIFIC_PARAMS へ移す
RESERVED_NAMES: tuple[str, ...] = ()

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
