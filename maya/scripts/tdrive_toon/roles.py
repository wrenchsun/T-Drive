"""部位ロール定義と、マテリアル名からのロール自動推定ルール（Maya 非依存）。

ロールは「その部位に対するアニメ的な嘘」の既定値を束ねたもの。
部位登録時にプリセットとして Look の base 値へ流し込まれ、以降はエディタで自由に上書きできる。
"""

from __future__ import annotations

import re
from typing import Any

# Blend（Opaque/Cutout/Transparent）はロールでは決めない。部位登録時に元マテリアルの透明設定から継承する
# （同じ「目」でも白目は不透明・瞳は透明など、モデルごとに違うため）。
# renderQueueOffset は D-Drive の Blend 帯（Opaque=2000）からのオフセット。
# 「後から描く部位ほど上に出せる」ための予約値で、眉・目の髪越し表示（Phase 2）で使う。
ROLE_PRESETS: dict[str, dict[str, Any]] = {
    "face": {
        "label": "顔",
        "renderQueueOffset": 0,
        "specific": {"_ToonShadowStrength": 0.35, "_ToonOutlineWidth": 0.6, "_ToonShadeThreshold": 0.45},
    },
    "skin": {
        "label": "肌（体）",
        "renderQueueOffset": 0,
        "specific": {"_ToonShadeColor": [0.93, 0.72, 0.72, 1.0], "_ToonOutlineWidth": 0.9},
    },
    "hair": {
        "label": "髪",
        "renderQueueOffset": 10,
        "specific": {"_ToonOutlineWidth": 1.3},
    },
    "eye": {
        "label": "目",
        "renderQueueOffset": 20,
        "specific": {"_ToonShadowStrength": 0.0, "_ToonOutlineWidth": 0.0},
    },
    "brow": {
        "label": "眉",
        "renderQueueOffset": 30,
        "specific": {"_ToonShadowStrength": 0.0, "_ToonOutlineWidth": 0.0},
    },
    "eyeline": {
        "label": "アイライン・まつ毛",
        "renderQueueOffset": 25,
        "specific": {"_ToonShadowStrength": 0.0, "_ToonOutlineWidth": 0.0},
    },
    "mouth": {
        "label": "口",
        "renderQueueOffset": 0,
        "specific": {"_ToonShadowStrength": 0.2, "_ToonOutlineWidth": 0.4},
    },
    "blush": {
        "label": "頬・紅潮（オーバーレイ）",
        "renderQueueOffset": 40,
        "specific": {"_ToonShadowStrength": 0.0, "_ToonOutlineWidth": 0.0},
    },
    "cloth": {
        "label": "服",
        "renderQueueOffset": 0,
        "specific": {"_ToonOutlineWidth": 1.0},
    },
    "accessory": {
        "label": "装飾",
        "renderQueueOffset": 0,
        "specific": {"_ToonOutlineWidth": 0.8},
    },
    "other": {"label": "その他", "renderQueueOffset": 0, "specific": {}},
}
ROLES = tuple(ROLE_PRESETS)

# マテリアル名 → ロール の推定ルール（上から順に最初に一致したもの）。
# 部位登録の初期値。確定はユーザーがエディタで行う。
AUTO_RULES: tuple[tuple[str, str], ...] = (
    (r"brow|mayu", "brow"),
    (r"eyeline|lash|matsuge", "eyeline"),
    (r"eye|iris|pupil|hitomi", "eye"),
    (r"cheek|blush|hoho", "blush"),
    (r"mouth|teeth|tongue|kuchi", "mouth"),
    (r"face|kao|head", "face"),
    (r"hair|kami", "hair"),
    (r"skin|hada", "skin"),
    (r"body|cloth|fuku|shirt|skirt|pants|shoe", "cloth"),
    (r"acc|ribbon|ring|hat", "accessory"),
)


def guess_role(material_name: str) -> str:
    name = material_name.lower()
    for pattern, role in AUTO_RULES:
        if re.search(pattern, name):
            return role
    return "other"
