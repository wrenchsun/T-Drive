"""Look の名前 ⇔ シーンのノード名（ネームスペース。Maya 非依存）。

Look（マテリアル名・メッシュ名・BlendShape のターゲット名など）の名前は常に「ネームスペースなし」で持つ。
参照したキャラクター（`chr:mat_body01`）でも、キャラクターのファイルそのものでも、Unity でも同じ Look を使うため。
シーンのノードを `cmds` で引くときは必ず `to_scene`、シーンの名前を Look へ書くときは `to_doc` / `doc_short` を通す。

今のネームスペースはシーンごとの設定（fileInfo `tdriveToonNamespace`。session が読み書きする）。このモジュールは今の値だけを持つ。
"""

from __future__ import annotations

from tdrive import naming as _n

NAMESPACE_KEY = "tdriveToonNamespace"
FACIAL_NAMESPACE_KEY = "tdFacialNamespace"  # FacialController が選んだネームスペース（読むだけ。既定の候補に使う）

norm_ns = _n.norm_ns
flat = _n.flat
prefix = _n.prefix
ns_of = _n.ns_of
strip_all = _n.strip_all

_NS = ""


def namespace() -> str:
    """今使っているネームスペース（`chr` や入れ子の `a:b`。無いときは ""）。"""
    return _NS


def set_namespace(ns: str) -> str:
    """今のネームスペースを変える（メモリだけ。シーンへの記録は session）。正規化した値を返す。"""
    global _NS
    _NS = _n.norm_ns(ns)
    return _NS


def to_scene(name: str, ns: str | None = None) -> str:
    """Look の名前 → シーンのノード名。`|a|b` の長い名前は各階層に、`node.attr` はノードだけに付ける。付いているものはそのまま。"""
    return _n.to_scene(name, _NS if ns is None else ns)


def to_doc(name: str, ns: str | None = None) -> str:
    """シーンのノード名 → Look の名前（今のネームスペースを外す。他のネームスペースのものはそのまま）。"""
    return _n.to_doc(name, _NS if ns is None else ns)


def doc_short(path: str, ns: str | None = None) -> str:
    """`|a|chr:b` → `b`（末尾の短い名前をデータの名前にしたもの）。"""
    return _n.doc_short(path, _NS if ns is None else ns)


def tool_node(name: str, ns: str | None = None) -> str:
    """このツールがシーンに作るノードの名前（`<ネームスペースを平らにしたもの>_<名前>`。ネームスペースなしは名前のまま）。"""
    return _n.prefix(_NS if ns is None else ns) + name
