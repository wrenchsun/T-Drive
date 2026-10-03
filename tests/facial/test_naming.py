"""FC_* などシェイプ名の規則（naming.py）。規則を変えるのは MAJOR（docs/15 §7）なのでスナップショットで固定する。"""

import pytest

from tdrive_facial.core import naming


def test_name_snapshots():
    assert naming.morph_name("shizuku", "Neutral", 1, 4) == "FC_shizuku_Neutral_R1_C4"
    assert naming.morph_name("shizuku", "Joy", 0, 0) == "FC_shizuku_Joy_R0_C0"
    assert naming.morph_name("shizuku", "Joy", 2, 3, extreme=True) == "FC_shizuku_Joy_R2_C3_Ex"
    assert naming.perspective_name("shizuku", 2) == "FC_shizuku_Persp_K2"
    assert naming.sculpt_name("Neutral", 1, 2) == "fcs_Neutral_R1_C2"
    assert naming.sculpt_name("Neutral", 1, 2, prefix="sc_") == "sc_Neutral_R1_C2"


def test_is_fc_name_is_case_sensitive_prefix_only():
    assert naming.is_fc_name("FC_a_Neutral_R0_C0")
    assert naming.is_fc_name("FC_anything")
    assert not naming.is_fc_name("fc_a_Neutral_R0_C0")
    assert not naming.is_fc_name("bs.mouth_smile_L")
    assert not naming.is_fc_name("fcs_Neutral_R0_C0")
    assert not naming.is_fc_name(None)  # type: ignore[arg-type]


def test_is_sculpt_name():
    assert naming.is_sculpt_name("fcs_Neutral_R0_C0")
    assert not naming.is_sculpt_name("FC_a_Neutral_R0_C0")
    assert not naming.is_sculpt_name("fcs_x", prefix="")


@pytest.mark.parametrize(
    "name,expected",
    [
        ("FC_shizuku_Neutral_R1_C4", naming.ParsedName(naming.KIND_POINT, "shizuku", "Neutral", 1, 4)),
        ("FC_shizuku_Joy_R12_C3_Ex", naming.ParsedName(naming.KIND_POINT_EX, "shizuku", "Joy", 12, 3)),
        ("FC_shizuku_Persp_K3", naming.ParsedName(naming.KIND_PERSP, "shizuku", "Persp", index=3)),
    ],
)
def test_parse_name_without_asset(name, expected):
    assert naming.parse_name(name) == expected


def test_parse_name_with_underscored_asset_and_layer():
    n = "FC_my_asset_Very_Sad_R0_C1"
    assert naming.parse_name(n, asset="my_asset") == naming.ParsedName(naming.KIND_POINT, "my_asset", "Very_Sad", 0, 1)
    # asset を渡さないと layer は最後の 1 語
    assert naming.parse_name(n) == naming.ParsedName(naming.KIND_POINT, "my_asset_Very", "Sad", 0, 1)
    assert naming.parse_name("FC_other_Joy_R0_C0", asset="my_asset") is None


@pytest.mark.parametrize("name", ["bs.jaw_open", "FC_", "FC_a_Neutral", "FC_a_Neutral_R1", "FC_Neutral_R1_C1", "FC_a_Neutral_R1_C1_Ex_Ex"])
def test_parse_name_rejects_non_matching(name):
    assert naming.parse_name(name) is None


def test_round_trip_generated_names():
    for asset in ("a", "shizuku", "my_asset"):
        for layer in ("Neutral", "Joy", "Very_Sad"):
            for ex in (False, True):
                name = naming.morph_name(asset, layer, 3, 7, extreme=ex)
                p = naming.parse_name(name, asset=asset)
                assert (p.asset, p.layer, p.row, p.col) == (asset, layer, 3, 7)
                assert p.kind == (naming.KIND_POINT_EX if ex else naming.KIND_POINT)
    p = naming.parse_name(naming.perspective_name("my_asset", 5), asset="my_asset")
    assert p.kind == naming.KIND_PERSP and p.index == 5


def test_parse_sculpt_name():
    assert naming.parse_sculpt_name("fcs_Neutral_R1_C2") == naming.ParsedName(naming.KIND_SCULPT, None, "Neutral", 1, 2)
    assert naming.parse_sculpt_name("sc_Joy_R0_C0", prefix="sc_").layer == "Joy"
    assert naming.parse_sculpt_name("FC_a_Neutral_R1_C2") is None
    assert naming.parse_sculpt_name("fcs_Neutral_R1_C2_Ex") is None


# ---------------------------------------------------------------- docs/19 C-1 / S-8: 持ち主の判定
@pytest.mark.parametrize(
    "name,verdict",
    [
        ("FC_Chara_Neutral_R1_C2", naming.OWNER_OWN),
        ("FC_Chara_Neutral_R1_C2_Ex", naming.OWNER_OWN),
        ("FC_Chara_Persp_K0", naming.OWNER_OWN),
        ("FC_Chara_Gone_R1_C2", naming.OWNER_OWN),  # 消えたレイヤー（_ なし）= 自分のものの孤立
        ("FC_Chara_Smile_Big_R1_C2", naming.OWNER_OWN),  # 自分のレイヤー（_ あり）
        ("FC_Chara_Alt_Joy_R1_C2", naming.OWNER_OTHER),  # asset Chara_Alt のレイヤー Joy かもしれない
        ("FC_Chara_Alt_Persp_K0", naming.OWNER_OTHER),
        ("FC_Chara_garbage", naming.OWNER_GARBAGE),
        ("FC_Charb_Neutral_R1_C2", naming.OWNER_FOREIGN),
        ("FC_Chara2_Neutral_R1_C2", naming.OWNER_FOREIGN),
        ("bs.smile", naming.OWNER_FOREIGN),
    ],
)
def test_owner_of_fc_name(name, verdict):
    got, _parsed = naming.owner_of(name, "Chara", ["Neutral", "Smile_Big"])
    assert got == verdict
