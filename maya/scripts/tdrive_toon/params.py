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

# D-Drive の Specific に入る Toon 固有パラメータ
SPECIFIC_PARAMS: tuple[Param, ...] = (
    # --- 影（2 階調） ---
    Param("_ToonShadeColor", COLOR, [0.78, 0.72, 0.86, 1.0], "Shadow", "影色"),
    Param("_ToonShadeThreshold", FLOAT, 0.5, "Shadow", "影の境界", 0.0, 1.0),
    Param("_ToonShadeFeather", FLOAT, 0.02, "Shadow", "境界ぼかし", 0.001, 0.5),
    Param("_ToonShadowStrength", FLOAT, 1.0, "Shadow", "影の強さ", 0.0, 1.0),
    # R=影バイアス(0.5 中立 / 0 常に影 / 1 常に明) G=輪郭線幅倍率 B=頬・耳の色マスク A=リムマスク
    Param("_ToonMaskMap", TEXTURE, None, "Shadow", "Toon マスク (RGBA)"),
    # --- リム ---
    Param("_ToonRimColor", COLOR, [1.0, 1.0, 1.0, 1.0], "Rim", "リム色"),
    Param("_ToonRimPower", FLOAT, 4.0, "Rim", "リムの鋭さ", 0.5, 16.0),
    Param("_ToonRimStrength", FLOAT, 0.0, "Rim", "リムの強さ", 0.0, 1.0),
    # --- 固定色（頬・耳） ---
    Param("_ToonBlushColor", COLOR, [1.0, 0.45, 0.45, 1.0], "Blush", "頬色"),
    Param("_ToonBlushIntensity", FLOAT, 0.0, "Blush", "頬の強さ", 0.0, 1.0),
    # --- アウトライン（背面法線押し出し） ---
    Param("_ToonOutlineColor", COLOR, [0.28, 0.2, 0.2, 1.0], "Outline", "線色"),
    Param("_ToonOutlineBaseMix", FLOAT, 0.5, "Outline", "線色にベース色を混ぜる", 0.0, 1.0),
    # 線幅の単位は「1080p 画面での px」。距離によらず画面上で一定（Unity 側も同じ式）
    Param("_ToonOutlineWidth", FLOAT, 1.0, "Outline", "線幅 (px@1080p)", 0.0, 10.0),
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
