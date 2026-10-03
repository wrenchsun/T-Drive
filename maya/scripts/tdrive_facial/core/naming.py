"""シェイプ名の規則（1 か所）。Maya 非依存。

| もの | 名前 |
|---|---|
| 角度の補正シェイプ | `FC_<asset>_<layer>_R{row}_C{col}` |
| 誇張用（重み 1 超の分） | `FC_<asset>_<layer>_R{row}_C{col}_Ex` |
| パース補正 | `FC_<asset>_Persp_K{n}` |
| 彫り用の補助シェイプ | `fcs_<layer>_R{row}_C{col}`（接頭辞は `sculptShapes.prefix`） |

- 作り直す・消すのは `FC_` で始まるシェイプだけ（モデルに元からある `bs.*` などには触らない）
- 規則を変えるのは MAJOR（docs/15 §7）。tests/facial/test_naming.py のスナップショットが検出する
- 名前の照合は大文字小文字を区別する完全一致
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

FC_PREFIX = "FC_"
DEFAULT_SCULPT_PREFIX = "fcs_"
EXTREME_SUFFIX = "_Ex"
PERSPECTIVE_LAYER = "Persp"  # パース補正の名前に使う語。同名のレイヤーは作れない（名前が衝突する）

KIND_POINT = "point"
KIND_POINT_EX = "point_ex"
KIND_PERSP = "persp"
KIND_SCULPT = "sculpt"

_POINT_TAIL = re.compile(r"^(?P<layer>.+)_R(?P<row>\d+)_C(?P<col>\d+)(?P<ex>_Ex)?$")
_PERSP_TAIL = re.compile(r"^Persp_K(?P<k>\d+)$")


@dataclass(frozen=True)
class ParsedName:
    """名前を分解した結果。kind は KIND_* のどれか。使わない欄は None。"""

    kind: str
    asset: Optional[str] = None
    layer: Optional[str] = None
    row: Optional[int] = None
    col: Optional[int] = None
    index: Optional[int] = None  # パース補正の K 番号


def morph_name(asset: str, layer: str, row: int, col: int, *, extreme: bool = False) -> str:
    """`FC_<asset>_<layer>_R{row}_C{col}`（extreme=True なら末尾に `_Ex`）。"""
    name = f"{FC_PREFIX}{asset}_{layer}_R{int(row)}_C{int(col)}"
    return name + EXTREME_SUFFIX if extreme else name


def perspective_name(asset: str, index: int) -> str:
    """`FC_<asset>_Persp_K{n}`。"""
    return f"{FC_PREFIX}{asset}_{PERSPECTIVE_LAYER}_K{int(index)}"


def sculpt_name(layer: str, row: int, col: int, prefix: str = DEFAULT_SCULPT_PREFIX) -> str:
    """`fcs_<layer>_R{row}_C{col}`。"""
    return f"{prefix}{layer}_R{int(row)}_C{int(col)}"


def is_fc_name(name: str) -> bool:
    """`FC_` で始まる名前か（作り直す・消してよいシェイプの判定。形式の厳密な検査はしない）。"""
    return isinstance(name, str) and name.startswith(FC_PREFIX)


def is_sculpt_name(name: str, prefix: str = DEFAULT_SCULPT_PREFIX) -> bool:
    """彫り用の補助シェイプか（`fcs_` で始まる。Unity へは出さない）。"""
    return isinstance(name, str) and bool(prefix) and name.startswith(prefix)


COMBO_INFIX = "combo_"  # 組み合わせ補正 `fcs_combo_<a>__<b>`（シェイプタブが作る。2 つのシェイプの積で駆動されるのでポーズからは使われない）


def is_combo_name(name: str, prefix: str = DEFAULT_SCULPT_PREFIX) -> bool:
    """組み合わせ補正（`<prefix>combo_…`）か。"""
    return is_sculpt_name(name, prefix) and name.startswith(prefix + COMBO_INFIX)


def parse_name(name: str, asset: Optional[str] = None) -> Optional[ParsedName]:
    """`FC_*` の名前を分解する。規則に合わなければ None。

    asset を渡すと `FC_<asset>_` で始まるものだけを対象にし、残りを layer として扱う
    （layer・asset に `_` が入っていても曖昧にならない）。
    asset を渡さないと、点の名前は「最後の `_R{n}_C{n}` の直前の `_` 区切り 1 語」を layer、
    それより前を asset とみなす（layer に `_` を含むなら asset を渡すこと）。
    """
    if not is_fc_name(name):
        return None
    body = name[len(FC_PREFIX):]
    if asset is not None:
        head = f"{asset}_"
        if not body.startswith(head):
            return None
        return _parse_tail(asset, body[len(head):])
    m = _POINT_TAIL.match(body)
    if m:
        head = m.group("layer")
        if "_" not in head:
            return None  # asset と layer が区切れない
        a, _, layer = head.rpartition("_")
        return ParsedName(
            KIND_POINT_EX if m.group("ex") else KIND_POINT, a, layer, int(m.group("row")), int(m.group("col"))
        )
    m = re.match(r"^(?P<asset>.+)_Persp_K(?P<k>\d+)$", body)
    if m:
        return ParsedName(KIND_PERSP, m.group("asset"), PERSPECTIVE_LAYER, index=int(m.group("k")))
    return None


def _parse_tail(asset: str, tail: str) -> Optional[ParsedName]:
    m = _PERSP_TAIL.match(tail)
    if m:
        return ParsedName(KIND_PERSP, asset, PERSPECTIVE_LAYER, index=int(m.group("k")))
    m = _POINT_TAIL.match(tail)
    if m:
        return ParsedName(
            KIND_POINT_EX if m.group("ex") else KIND_POINT,
            asset,
            m.group("layer"),
            int(m.group("row")),
            int(m.group("col")),
        )
    return None


def parse_sculpt_name(name: str, prefix: str = DEFAULT_SCULPT_PREFIX) -> Optional[ParsedName]:
    """`fcs_<layer>_R{r}_C{c}` を分解する。"""
    if not is_sculpt_name(name, prefix):
        return None
    m = _POINT_TAIL.match(name[len(prefix):])
    if not m or m.group("ex"):
        return None
    return ParsedName(KIND_SCULPT, None, m.group("layer"), int(m.group("row")), int(m.group("col")))
