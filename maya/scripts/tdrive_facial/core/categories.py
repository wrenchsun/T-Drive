"""シェイプの分類（ポーズタブ・セットアップタブのタブ / 絞り込み。Maya 非依存）。

既定の分け方（`prefix_groups`。プロジェクトの命名規則に依らない）:
- 名前の **最初の `_` の前** を分類とする（ノード名 `bs.` は除く。`bs.eye_close_L` → `eye`）。そのままの綴りを表示名にする
- 大文字小文字は区別せずにまとめる（`Eye_x` と `eye_y` は同じタブ。表示名は多いほうの綴り）
- `_` が無い名前・メンバーが 1 本だけの接頭辞は「その他」へ。タブの数に上限は無い（タブの帯は横に送れる）
- 並びは、シェイプの一覧で最初に出てきた順（「その他」は最後）
- 接頭辞のグループが 2 つに満たなければ分けない（呼ぶ側は「すべて」だけにする）

プロファイルの `categories`（任意）は、カスタムの分け方を決めたいプロジェクト向けの上書き。あればそれを使う（既定は上の `_` の規則）。
規則（パターンは文字列。大文字小文字は区別しない）:
- 照合する名前は **ノード名を除いたターゲット名**（`bs.eye_close_L` → `eye_close_L`）。左右の接尾辞は規則に関係しない
- `prefix:eye_`（その文字で始まる）/ `contains:Look`（含む）/ `exact:jaw` または接頭辞なしの文字列（完全一致）
- 並びの早い分類が優先。どれにも当たらない名前は最後の「その他」へ
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

OTHER_ID = "_other"
OTHER_LABEL = "その他"
ALL_ID = "_all"
ALL_LABEL = "すべて"
MIN_GROUPS = 2  # 接頭辞のグループがこれ未満ならタブを出さない


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


def prefix_of(name: str) -> str:
    """最初の `_` の前（ノード名は除く）。`_` が無い・先頭が `_` なら空。"""
    t = strip_node(name)
    return t.split("_", 1)[0] if "_" in t else ""


def prefix_groups(names: list[str]) -> list[tuple[str, str, list[str]]]:
    """既定の分け方。分けられなければ空のリスト（呼ぶ側が「すべて」1 つにする）。id は小文字の接頭辞。"""
    members: dict[str, list[str]] = {}
    spell: dict[str, dict[str, int]] = {}
    singles: list[str] = []
    for n in names:
        p = prefix_of(n)
        if not p:
            singles.append(n)
            continue
        key = p.lower()
        members.setdefault(key, []).append(n)  # dict は挿入順 = 最初に出てきた順
        sp = spell.setdefault(key, {})
        sp[p] = sp.get(p, 0) + 1  # 綴りごとの数（同数なら先に出たほう）
    groups: list[tuple[str, str, list[str]]] = []
    for key, mem in members.items():
        if len(mem) >= 2:
            sp = spell[key]
            groups.append((key, max(sp, key=sp.__getitem__), mem))
        else:
            singles.extend(mem)
    if len(groups) < MIN_GROUPS:
        return []
    if singles:
        rank = {n: i for i, n in enumerate(names)}
        groups.append((OTHER_ID, OTHER_LABEL, sorted(singles, key=rank.__getitem__)))
    return groups


def categorize(curve_names: Iterable[str], profile: Any = None) -> list[tuple[str, str, list[str]]]:
    """シェイプ名を分類ごとに分ける。戻り: [(分類の id, 表示名, [名前（入力の順）]), …]（空の分類は出さない）。

    - プロファイルに `categories` があればそれ（任意の上書き。当たらなかった名前は最後の「その他」）
    - 無ければ `_` の前で分ける（`prefix_groups`）。分けられなければ [("_all", "すべて", 全部)]
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
    auto = prefix_groups(names)
    return auto if auto else [(ALL_ID, ALL_LABEL, names)]


def is_trivial(groups: list[tuple[str, str, list[str]]]) -> bool:
    """分類が 1 つ以下（タブを出す意味が無い）。"""
    return len(groups) <= 1
