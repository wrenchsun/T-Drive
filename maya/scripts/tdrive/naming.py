"""ネームスペースの付け外し（Maya 非依存の純関数。ツール共通）。

参照（リファレンス）したキャラクターのノードは `chr:mdl_face02`（入れ子は `a:b:node`）のように名前の頭にネームスペースが付く。
データ（Look・.fcpose など）の名前は常に「ネームスペースなし」で持ち、シーンのノードを引くときだけ付ける:
  データの名前 →（to_scene）→ シーンの名前 →（to_doc）→ データの名前

扱う名前は 3 種類（どれも同じ関数でよい）: 短い名前 `mat`、DAG パス `|a|b|c`（各階層に付く）、アトリビュート付き `node.attr`（`.` より後ろは触らない）。
"""

from __future__ import annotations

import re

_FLAT = re.compile(r"[^0-9A-Za-z_]")


def norm_ns(ns: str | None) -> str:
    """前後の空白と `:` を落とす（`" chr: "` → `"chr"`）。None は ""。"""
    return (ns or "").strip().strip(":")


def flat(ns: str) -> str:
    """ノード名に埋められる形（`a:b` → `a_b`）。このツールがシーンに作るノードの名前に使う。"""
    return _FLAT.sub("_", norm_ns(ns))


def _split_attr(name: str) -> tuple[str, str]:
    node, dot, attr = name.partition(".")
    return node, dot + attr


def ns_of(node: str) -> str:
    """ノード（DAG パスでも短い名前でも `node.attr` でも）のネームスペース。無ければ ""。末尾のノードで決める。"""
    leaf = _split_attr(node)[0].split("|")[-1]
    return leaf.rpartition(":")[0] if ":" in leaf else ""


def leaf(node: str) -> str:
    """`|a|chr:b.attr` → `chr:b`（末尾のノードの短い名前。アトリビュートは外す）。"""
    return _split_attr(node)[0].split("|")[-1]


def _map(part: str, ns: str) -> str:
    if not part or part.startswith(ns + ":"):
        return part  # すでにシーンの名前
    return f"{ns}:{part}"


def to_scene(name: str, ns: str | None) -> str:
    """データの名前 → シーンの名前（ネームスペースを付ける）。付いているものはそのまま。ns が空なら何もしない。"""
    ns = norm_ns(ns)
    if not ns or not name:
        return name
    node, attr = _split_attr(name)
    return "|".join(_map(p, ns) for p in node.split("|")) + attr


def to_doc(name: str, ns: str | None) -> str:
    """シーンの名前 → データの名前（ns を外す。他のネームスペースのものはそのまま）。ns が空なら何もしない。"""
    ns = norm_ns(ns)
    if not ns or not name:
        return name
    pre = ns + ":"
    node, attr = _split_attr(name)
    return "|".join(p[len(pre):] if p.startswith(pre) else p for p in node.split("|")) + attr


def strip_all(name: str) -> str:
    """どのネームスペースも外す（`|chr:grp|chr:mesh.attr` → `|grp|mesh.attr`）。書き出し用の一時シーンで名前をそろえるときに使う。"""
    node, attr = _split_attr(name)
    return "|".join(p.rpartition(":")[2] for p in node.split("|")) + attr


def doc_short(path: str, ns: str | None) -> str:
    """`|a|chr:b` → `b`（末尾の短い名前から ns を外した、データの名前）。"""
    return to_doc(leaf(path), ns)


def prefix(ns: str | None) -> str:
    """このツールがシーンに作るノードの名前の頭（`chr` → `chr_`、ネームスペースなしは ""）。複数のキャラクターで名前が重ならないようにする。"""
    f = flat(ns or "")
    return f"{f}_" if f else ""
