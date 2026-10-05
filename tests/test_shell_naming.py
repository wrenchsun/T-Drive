"""tdrive/naming.py（ネームスペースの付け外し）。Maya 不要。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "maya" / "scripts"))

from tdrive import naming  # noqa: E402


def test_norm_and_flat():
    assert naming.norm_ns(" chr: ") == "chr"
    assert naming.norm_ns(None) == ""
    assert naming.norm_ns(":a:b:") == "a:b"
    assert naming.flat("a:b") == "a_b"
    assert naming.prefix("a:b") == "a_b_"
    assert naming.prefix("") == ""


def test_to_scene_short_path_and_attr():
    assert naming.to_scene("mat_body01", "chr") == "chr:mat_body01"
    assert naming.to_scene("mat_body01", "a:b") == "a:b:mat_body01"
    assert naming.to_scene("|grp|mesh", "chr") == "|chr:grp|chr:mesh"
    assert naming.to_scene("bs.eye_close_L", "chr") == "chr:bs.eye_close_L"
    assert naming.to_scene("|g|m.vtx[3]", "chr") == "|chr:g|chr:m.vtx[3]"


def test_to_scene_keeps_already_scene_names_and_empty_ns():
    assert naming.to_scene("chr:mat", "chr") == "chr:mat"
    assert naming.to_scene("mat", "") == "mat"
    assert naming.to_scene("mat", None) == "mat"
    assert naming.to_scene("", "chr") == ""


def test_to_doc_strips_only_the_given_namespace():
    assert naming.to_doc("chr:mat", "chr") == "mat"
    assert naming.to_doc("|chr:g|chr:m", "chr") == "|g|m"
    assert naming.to_doc("chr:bs.w", "chr") == "bs.w"
    assert naming.to_doc("other:mat", "chr") == "other:mat"
    assert naming.to_doc("a:b:mat", "a:b") == "mat"
    assert naming.to_doc("a:b:mat", "a") == "b:mat"  # 外側だけ外す（入れ子の内側は残る）
    assert naming.to_doc("chr:mat", "") == "chr:mat"


def test_round_trip():
    for name in ("mat", "|a|b|c", "bs.weight[0]", "x_y"):
        for ns in ("chr", "a:b"):
            assert naming.to_doc(naming.to_scene(name, ns), ns) == name


def test_ns_of_and_leaf():
    assert naming.ns_of("|chr:g|chr:m") == "chr"
    assert naming.ns_of("|a:b:g|a:b:m.attr") == "a:b"
    assert naming.ns_of("mat") == ""
    assert naming.ns_of("|g|chr:m") == "chr"  # 末尾のノードで決める
    assert naming.leaf("|chr:g|chr:m.attr") == "chr:m"


def test_strip_all_and_doc_short():
    assert naming.strip_all("|chr:grp|chr:mesh") == "|grp|mesh"
    assert naming.strip_all("|a:b:grp|c:mesh.attr") == "|grp|mesh.attr"
    assert naming.strip_all("plain") == "plain"
    assert naming.doc_short("|a|chr:b", "chr") == "b"
    assert naming.doc_short("|a|b", "chr") == "b"
