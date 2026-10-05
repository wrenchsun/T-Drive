"""シェイプの分類（命名規則プロファイルの `categories`。Maya 非依存）。ポーズタブ・セットアップタブがタブ / 絞り込みに使う。

規則（パターンは文字列。大文字小文字は区別しない）:
- 照合する名前は **ノード名を除いたターゲット名**（`bs.eye_close_L` → `eye_close_L`）。左右の接尾辞（_L / _R / Left / Right）は規則に関係しない
- `prefix:eye_`   … その文字で始まる
- `contains:Look` … その文字を含む
- `exact:jaw`、または接頭辞なしの文字列 … 完全一致
- 並びの早い分類が優先（最初に当たった分類に入る）。どれにも当たらない名前は最後の「その他」へ

プロファイルに分類が無いときの自動の分け方（`auto_groups`）: 名前の先頭の語（最初の `_` の前。`_` が無ければ camelCase の頭の小文字の並び）でまとめる。
2 本以上の語だけを 1 グループにし、1 本だけの語は「その他」へ寄せる。グループ（その他を含む）が 2〜12 個に収まらなければ分けず「すべて」1 つ。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

OTHER_ID = "_other"
OTHER_LABEL = "その他"
ALL_ID = "_all"
ALL_LABEL = "すべて"
AUTO_PREFIX = "auto:"
AUTO_MIN_GROUPS = 2
AUTO_MAX_GROUPS = 12
AUTO_MIN_MEMBERS = 2


@dataclass
class Category:
    """プロファイルの分類 1 つ。"""

    id: str
    label: str = ""
    patterns: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)  # 知らないキー（書き戻す）


def strip_node(name: str) -> str:
    """`bs.eye_close_L` → `eye_close_L`（ノード名の前置きを外す。無ければそのまま）。"""
    return name.partition(".")[2] if "." in name else name


def pattern_matches(pattern: str, target: str) -> bool:
    """パターン 1 つが、ターゲット名（ノード名なし）に当たるか。大文字小文字は区別しない。空のパターンは当たらない。"""
    t = target.lower()
    p = pattern.strip()
    for key in ("prefix:", "contains:", "exact:"):
        if p.lower().startswith(key):
            body = p[len(key):].lower()
            if not body:
                return False
            if key == "prefix:":
                return t.startswith(body)
            if key == "contains:":
                return body in t
            return t == body
    return bool(p) and t == p.lower()


def category_of(name: str, categories: Iterable[Category]) -> Optional[str]:
    """名前が入る分類の id（最初に当たったもの）。どれにも当たらなければ None。"""
    target = strip_node(name)
    for cat in categories:
        if any(pattern_matches(p, target) for p in cat.patterns):
            return cat.id
    return None


_CAMEL_HEAD = re.compile(r"^[a-z]+")


def head_token(name: str) -> str:
    """自動の分け方の鍵: 最初の `_` の前。`_` が無ければ camelCase の頭（`eyeBlinkLeft` → `eye`）。取れなければ空。"""
    t = strip_node(name)
    if "_" in t:
        return t.split("_", 1)[0]
    m = _CAMEL_HEAD.match(t)
    if m and m.end() < len(t):  # 小文字の並びのあとに大文字が続くときだけ頭として使う
        return m.group(0)
    return ""


def auto_groups(names: list[str]) -> list[tuple[str, str, list[str]]]:
    """プロファイルの分類が無いときの自動の分け方。分けられなければ空のリスト（呼ぶ側が「すべて」1 つにする）。"""
    heads: dict[str, list[str]] = {}
    order: list[str] = []
    singles: list[str] = []
    for n in names:
        h = head_token(n)
        key = h.lower()
        if not key:
            singles.append(n)
            continue
        if key not in heads:
            heads[key] = []
            order.append(key)
        heads[key].append(n)
    groups: list[tuple[str, str, list[str]]] = []
    labels: dict[str, str] = {}
    for n in names:
        h = head_token(n)
        if h and h.lower() not in labels:
            labels[h.lower()] = h
    for key in order:
        if len(heads[key]) >= AUTO_MIN_MEMBERS:
            groups.append((AUTO_PREFIX + key, labels[key], heads[key]))
        else:
            singles.extend(heads[key])
    if len(groups) < AUTO_MIN_GROUPS:
        return []
    if singles:
        rank = {n: i for i, n in enumerate(names)}
        groups.append((OTHER_ID, OTHER_LABEL, sorted(singles, key=rank.__getitem__)))
    if len(groups) > AUTO_MAX_GROUPS:
        return []
    return groups


def categorize(curve_names: Iterable[str], profile: Any = None) -> list[tuple[str, str, list[str]]]:
    """シェイプ名を分類ごとに分ける。戻り: [(分類の id, 表示名, [名前（入力の順）]), …]（空の分類は出さない）。

    - プロファイルに `categories` があればそれ（当たらなかった名前は最後の「その他」）
    - 無ければ自動の分け方（`auto_groups`）。分けられなければ [("_all", "すべて", 全部)]
    - 名前が 1 つも無ければ空のリスト
    """
    names = list(dict.fromkeys(curve_names))
    if not names:
        return []
    cats: list[Category] = list(getattr(profile, "categories", None) or [])
    if cats:
        buckets: dict[str, list[str]] = {c.id: [] for c in cats}
        rest: list[str] = []
        for n in names:
            cid = category_of(n, cats)
            (buckets[cid] if cid is not None else rest).append(n)
        out = [(c.id, c.label or c.id, buckets[c.id]) for c in cats if buckets[c.id]]
        if rest:
            out.append((OTHER_ID, OTHER_LABEL, rest))
        return out
    auto = auto_groups(names)
    return auto if auto else [(ALL_ID, ALL_LABEL, names)]


def is_trivial(groups: list[tuple[str, str, list[str]]]) -> bool:
    """分類が 1 つ以下（タブを出す意味が無い）。"""
    return len(groups) <= 1
