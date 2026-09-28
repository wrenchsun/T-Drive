"""機能（Feature）の定義（Maya 非依存。docs/11 §1）。

機能 = 技術とそれに属するパラメータ・キャラクター設定を束ねた、オン/オフの単位。
オフの機能は「効果なしの値」で解決する（パラメータ契約の既定値、または off_values）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SH, ME, CO, RF = "Sh", "Me", "Co", "RF"  # シェーダー / メッシュ / コンポーネント / Renderer Feature


@dataclass(frozen=True)
class Feature:
    id: str
    label: str
    techniques: tuple[str, ...]
    params: tuple[str, ...] = ()  # Specific（_Toon*）
    settings: tuple[str, ...] = ()  # characterSettings のトップレベルキー
    impl: tuple[str, ...] = (SH,)
    maya_preview: str = "full"  # full / partial / none
    default_on: bool = False
    required: bool = False
    off_values: dict[str, Any] = field(default_factory=dict)  # 既定値が「効果あり」のパラメータだけ


FEATURES: tuple[Feature, ...] = (
    Feature("shade", "2 階調影", ("T-01", "T-02", "T-04"),
            ("_ToonShadeColor", "_ToonShadeThreshold", "_ToonShadeFeather", "_ToonShadowStrength"),
            default_on=True, required=True),
    Feature("outline", "輪郭線", ("T-05",), ("_ToonOutlineColor", "_ToonOutlineBaseMix", "_ToonOutlineWidth"),
            default_on=True, off_values={"_ToonOutlineWidth": 0.0}),
    Feature("outlineSmoothNormal", "線のスムーズ法線", ("T-06",), ("_ToonOutlineSmoothNormal",), impl=(SH, ME),
            default_on=True, off_values={"_ToonOutlineSmoothNormal": 0.0}),
    Feature("outlineDistance", "距離で線を細く", ("T-18",), ("_ToonOutlineDistanceScale", "_ToonOutlineRefDistance")),
    Feature("outlineDirection", "線の太さの方向依存", ("T-26",), ("_ToonOutlineShadowSide", "_ToonOutlineBottom")),
    Feature("vertexMask", "頂点カラーの Toon マスク", ("T-03", "T-07"), (), (), (SH, ME), default_on=True),
    Feature("maskMap", "Toon マスクテクスチャ", ("T-19",), ("_ToonMaskMap",)),
    Feature("tint", "固定色（頬・耳・口内）", ("T-08",), ("_ToonTintColor", "_ToonTintStrength"), default_on=True),
    Feature("shade2", "2 影", ("T-27",), ("_ToonShade2Color", "_ToonShade2Threshold", "_ToonShade2Strength")),
    Feature("lightColorInfluence", "ライト色の影響", ("T-11",), ("_ToonLightColorInfluence",)),
    Feature("rim", "リム", ("T-13",), ("_ToonRimColor", "_ToonRimPower", "_ToonRimStrength")),
    Feature("hairHighlight", "髪ハイライト", ("T-12",),
            ("_ToonHairHighlightMap", "_ToonHairHighlightColor", "_ToonHairHighlightShift")),
    Feature("matCap", "MatCap", ("T-30",), ("_ToonMatCapMap", "_ToonMatCapStrength")),
    Feature("colorCorrect", "色補正", ("T-28",), ("_ToonSaturation", "_ToonBrightness")),
    Feature("depthOffset", "手前に出す", ("T-09",), ("_ToonDepthOffset",)),
    Feature("depthCompression", "奥行き圧縮", ("T-22",), ("_ToonDepthCompressWeight",), ("depthCompression",), (SH, CO)),
    Feature("faceShadowSdf", "SDF 顔影マップ", ("T-21",), ("_ToonFaceShadowMap", "_ToonFaceShadowWeight"), ("faceShadow",), (SH, CO)),
    Feature("lightStabilize", "ライトの安定化", ("T-17",), (), ("light",), (CO,)),
    Feature("stencil", "髪越し表示（ステンシル）", ("T-24",), (), ("stencil",), (SH, CO), "none"),
    Feature("innerLine", "画面上のインナーライン", ("T-23",), (), ("innerLine",), (RF,), "none"),  # 4-7 で partial
    Feature("contactShadow", "接地影", ("T-29",), (), ("contactShadow",), (RF,), "none"),  # 4-6 で partial
    Feature("viewCorrection", "カメラ角度補正", ("T-20",), (), ("viewCorrection",), (ME, CO)),
    Feature("expressions", "表情パラメータ", ("T-25",), (), ("expressions",), (CO,)),
)

BY_ID = {f.id: f for f in FEATURES}
FEATURE_OF_PARAM = {p: f.id for f in FEATURES for p in f.params}
FEATURE_OF_SETTING = {s: f.id for f in FEATURES for s in f.settings}


def default_flags() -> dict[str, bool]:
    return {f.id: f.default_on for f in FEATURES}


def off_value(param: str, default: Any) -> Any:
    """オフの機能のパラメータを解決する値（効果なし）。"""
    f = BY_ID[FEATURE_OF_PARAM[param]]
    return f.off_values.get(param, default)
