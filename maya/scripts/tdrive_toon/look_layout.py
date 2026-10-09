"""ルックタブの並び・見出しの表（Maya / Qt 非依存。docs/05 §4.2、E-2〜E-9）。

どのパラメータがどの機能に属するかは features.py（FEATURE_OF_PARAM / FEATURE_OF_COMMON）から導く。
ここに持つのは UI だけの補助データ（グループ → 代表機能、サブ機能、マニュアルのアンカー）。
パラメータ契約（params.Param）には項目を足さない。
"""

from __future__ import annotations

import re

from . import features, params

# Common（D-Drive MaterialCommon）のうち編集できるもの。(キー, 表示名, 種類, 最小, 最大)
COMMON_ROWS = (
    ("common.albedoTint", "ベース色（乗算）", "color", 0, 0),
    ("common.blend", "ブレンド", "blend", 0, 0),
    ("common.cutoff", "カットオフ（Cutout 時）", "float", 0.0, 1.0),
    ("common.doubleSided", "両面表示", "bool", 0, 0),
    ("renderQueueOffset", "描画順オフセット（Unity のみ）", "int", -100, 100),
)
# テクスチャの差し替え（4-9、docs/05 §4.1）。Common（D-Drive MaterialCommon）のテクスチャと関連する値
TEXTURE_ROWS = (
    ("common.albedo", "ベースマップ（色）", params.TEXTURE, 0, 0),
    ("common.normal", "法線マップ", params.TEXTURE, 0, 0),
    ("common.normalScale", "法線マップの強さ", params.FLOAT, 0.0, 2.0),
    ("common.emission", "発光マップ", params.TEXTURE, 0, 0),
    ("common.emissionColor", "発光色", params.COLOR, 0, 0),
    ("common.emissionIntensity", "発光の強さ", params.FLOAT, 0.0, 10.0),
)
GROUP_LABELS = {
    "Textures": "テクスチャ（差し替え）",
    "Common": "共通", "Shadow": "影", "Mask": "マスク", "Tint": "固定色", "Outline": "線",
    "Light": "ライト", "Depth": "手前に出す（眉・目を髪の上に）", "Rim": "リム", "Hair": "髪ハイライト",
    "Color": "色補正", "MatCap": "MatCap（簡易反射）", "FaceShadow": "顔影マップ（SDF）", "FacialAngle": "顔の角度連動",
}
# パラメータごとの説明（ラベルのツールチップ・検索の対象）
PARAM_HELP = {
    "_ToonFacialAngle": "顔の向きに合わせて線の太さと陰の境目を変える。向きは FacialController のプレビュー（Maya）か "
                        "Runner のマテリアル出力（Unity）が渡す。それが無いときは、オンにしても何も変わらない",
    "_ToonFacialLineWidthSide": "真横を向いたときの線の太さ（倍）。正面は 1 倍で、横に向くほどこの値へ近づく。1 = 変えない",
    "_ToonFacialLineWidthVertical": "真上・真下を向いたときの線の太さ（倍）。1 = 変えない",
    "_ToonFacialShadeOffsetSide": "真横を向いたときの陰の境目のずらし。プラスで陰が広がり、マイナスで狭まる。0 = 変えない",
}

# グループ → 代表機能（見出しのチェック）。None = 代表なし（チェックを出さない）
GROUP_FEATURE: dict[str, str | None] = {
    "Common": None, "Textures": None,
    "Shadow": "shade", "Outline": "outline", "Rim": "rim", "Hair": "hairHighlight", "MatCap": "matCap",
    "Tint": "tint", "Color": "colorCorrect", "FaceShadow": "faceShadowSdf", "FacialAngle": "facialAngle",
    "Light": "lightColorInfluence", "Mask": "vertexMask", "Depth": "depthOffset",
}
# グループ内のサブ機能（チェック付きの小見出し。オフの間はその行を畳む）
SUB_FEATURES: dict[str, tuple[str, ...]] = {
    "Outline": ("outlineSmoothNormal", "outlineDistance", "outlineDirection", "outlineScreenSpace"),
    "Depth": ("seeThroughOutline", "depthCompression"),
    "Shadow": ("shade2", "selfShadow"),
    "Textures": ("normalMap", "emission"),
    "Mask": ("maskMap",),
}
# 「?」で開くマニュアル（look-params.html）のアンカー。サブ機能は個別の節があれば SUB_ANCHOR、無ければグループの節
GROUP_ANCHOR = {
    "Common": "common", "Textures": "textures", "Shadow": "shadow", "Tint": "tint", "Outline": "outline",
    "Mask": "mask", "FacialAngle": "facial-angle",
    "Light": "p1", "Depth": "p1", "Rim": "p1", "Hair": "p1", "Color": "p1", "MatCap": "p1", "FaceShadow": "p1",
}
SUB_ANCHOR = {
    "outlineScreenSpace": "outline-screen-space", "outlineSmoothNormal": "smooth-normal", "selfShadow": "self-shadow",
    "maskMap": "mask-map",
}
# 「かんたん」モードで出す項目（E-9。契約の Param には足さない）。検索中は詳細の全項目から探す
SIMPLE_KEYS = (
    "_ToonShadeColor", "_ToonShadeThreshold", "_ToonShadeFeather", "_ToonShadowStrength",
    "_ToonOutlineColor", "_ToonOutlineWidth", "_ToonTintColor", "_ToonTintStrength",
    "_ToonRimColor", "_ToonRimStrength", "_ToonHairHighlightColor", "common.albedoTint", "common.albedo",
)
MODE_SIMPLE, MODE_DETAIL = "simple", "detail"
MODE_PREF = "tdriveLookMode"
PREVIEW_BADGE = {"full": "○", "partial": "△", "none": "×"}
MANUAL_PAGE = "look-params.html"


def feature_of_key(key: str) -> str | None:
    """ルックタブの行（キー）が属する機能。属さない（常に有効な）行は None。"""
    if key.startswith("common."):
        return features.FEATURE_OF_COMMON.get(key.split(".", 1)[1])
    return features.FEATURE_OF_PARAM.get(key)


def build_groups() -> dict[str, list[tuple]]:
    """グループ名 → 行 (キー, 表示名, 種類, 最小, 最大) の並び（表示順）。"""
    groups: dict[str, list[tuple]] = {"Common": list(COMMON_ROWS), "Textures": list(TEXTURE_ROWS)}
    for p in params.SPECIFIC_PARAMS:
        kind = "toggle01" if p.toggle else p.kind  # 0/1 の真偽値はチェックボックス（値は 1.0 / 0.0）
        groups.setdefault(p.group, []).append((p.unity, p.label, kind, p.min, p.max))
    return groups


def split_group(group: str, rows: list[tuple]) -> tuple[list[tuple], list[tuple[str, list[tuple]]]]:
    """グループの行を (本体の行, [(サブ機能 ID, その行)...]) に分ける。サブ機能の順は SUB_FEATURES の順。"""
    subs = SUB_FEATURES.get(group, ())
    main = [r for r in rows if feature_of_key(r[0]) not in subs]
    return main, [(fid, [r for r in rows if feature_of_key(r[0]) == fid]) for fid in subs
                  if any(feature_of_key(r[0]) == fid for r in rows)]


def anchor_of(group: str, fid: str | None = None) -> str:
    return SUB_ANCHOR.get(fid) or GROUP_ANCHOR.get(group, "")


def matches(query: str, key: str, label: str) -> bool:
    """検索（E-3）: 表示名・Unity 名（key）・説明（PARAM_HELP）に部分一致（大文字小文字・前後の空白は無視）。"""
    q = query.strip().casefold()
    if not q:
        return True
    return any(q in text.casefold() for text in (label, key, PARAM_HELP.get(key, "")))


def row_passes(key: str, label: str, query: str, mode: str = MODE_DETAIL) -> bool:
    """機能のオン/オフ以外の理由（検索・モード）でこの行を出すか。検索中は両モードから探す（モードは無視）。"""
    if query.strip():
        return matches(query, key, label)
    return mode != MODE_SIMPLE or key in SIMPLE_KEYS


# ---------------------------------------------------------------- 色の文字列（E-7。Qt 非依存）
_HEX_RE = re.compile(r"^#?([0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")


def format_hex(rgba) -> str:
    """0〜1 の RGBA → `#RRGGBB`（アルファが 1 のとき）/ `#RRGGBBAA`。"""
    vals = [int(round(min(max(float(x), 0.0), 1.0) * 255)) for x in rgba]
    if len(vals) < 4 or vals[3] == 255:
        return "#%02X%02X%02X" % tuple(vals[:3])
    return "#%02X%02X%02X%02X" % tuple(vals[:4])


def format_floats(rgba) -> str:
    """0〜1 の数値（`r, g, b, a`。Unity のインスペクターと同じ値）。"""
    return ", ".join(f"{round(float(x), 4):g}" for x in rgba)


def parse_color(text: str, alpha: float = 1.0):
    """`#RRGGBB` / `#RRGGBBAA`（# は省略可）/ `r, g, b[, a]`（0〜1。カンマ・空白・括弧区切り）→ [r, g, b, a]。読めなければ None。
    アルファが書かれていなければ alpha を使う。"""
    t = (text or "").strip()
    m = _HEX_RE.match(t)
    if m and (t.startswith("#") or len(t) in (6, 8)):
        h = m.group(1)
        vals = [int(h[i:i + 2], 16) / 255 for i in range(0, len(h), 2)]
        return [round(v, 4) for v in vals] + ([float(alpha)] if len(vals) == 3 else [])
    parts = [x for x in re.split(r"[,\s;()\[\]]+", t) if x]
    if len(parts) not in (3, 4):
        return None
    try:
        vals = [float(x) for x in parts]
    except ValueError:
        return None
    if any(v != v or v < 0.0 or v > 1.0 for v in vals):
        return None
    return [round(v, 4) for v in vals] + ([float(alpha)] if len(vals) == 3 else [])


# ---------------------------------------------------------------- 値の収集（E-4。Qt 非依存）
def collect_values(values_of, keys) -> dict:
    """keys の値を平らな辞書にする。values_of(key) は選択中の各マテリアルの値のリスト（部位は複数）。
    混在のときは先頭のマテリアルの値。値の無い（空リスト・取れない）キーは入れない。"""
    import copy

    out: dict = {}
    for key in keys:
        vals = values_of(key)
        if vals:
            out[key] = copy.deepcopy(vals[0])
    return out


def keys_of_groups(groups, names) -> list[str]:
    """グループ名の並び → それらに属する行のキー（表示順）。知らない名前は無視。"""
    table = groups if isinstance(groups, dict) else build_groups()
    return [r[0] for g in names if g in table for r in table[g]]


# ---------------------------------------------------------------- 記憶（optionVar。Maya が無ければ記憶しない）
def pref_get(name: str, default):
    try:
        from maya import cmds

        if cmds.optionVar(exists=name):
            return cmds.optionVar(q=name)
    except Exception:  # noqa: BLE001  Maya 外・optionVar が使えない環境
        pass
    return default


def pref_set(name: str, value) -> None:
    try:
        from maya import cmds

        if isinstance(value, str):
            cmds.optionVar(stringValue=(name, value))
        else:
            cmds.optionVar(intValue=(name, int(value)))
    except Exception:  # noqa: BLE001
        pass


def section_pref(group: str) -> str:
    return f"tdriveLookSection_{group}"
