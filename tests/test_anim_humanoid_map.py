"""Humanoid 対応表の推定・検証・読み書き（docs/20 §2.4。Maya 非依存）。"""

from pathlib import Path

import pytest

from tdrive_anim.core import humanoid_map as H

JOINTS = (Path(__file__).parent / "fixtures" / "anim" / "misoten_joint_names.txt").read_text(encoding="utf-8").split()


def test_infer_fills_all_required():
    d = H.new_map("MisotenChara", JOINTS)
    assert H.missing_required(d) == []
    b = d["bones"]
    assert b["Hips"] == "spine_C0_0_jnt" and b["Head"] == "neck_C0_head_jnt"
    assert b["LeftUpperArm"] == "arm_L0_0_jnt" and b["LeftLowerArm"] == "arm_L0_4_jnt" and b["LeftHand"] == "arm_L0_end_jnt"
    assert b["RightFoot"] == "leg_R0_end_jnt" and b["LeftToes"] == "foot_L0_0_jnt"
    assert b["LeftIndexProximal"] == "finger_L0_0_jnt" and b["RightLittleDistal"] == "finger_R3_2_jnt" and b["LeftThumbDistal"] == "thumb_L0_2_jnt"
    assert b["Jaw"] == "mouth_C0_jaw_jnt" and b["RightEye"] == "eye_R0_eye_jnt"


def test_validate_clean_with_scene():
    d = H.new_map("MisotenChara", JOINTS)
    assert H.validate(d, joints=JOINTS) == []


def test_extras_exclude_helpers_and_mapped():
    d = H.new_map("MisotenChara", JOINTS)
    assert "ahoge_C0_0_jnt" in d["extra"] and "Skirt_L0_0_jnt" in d["extra"] and "tongue_C0_0_jnt" in d["extra"]
    assert "spine_C0_0_jnt" not in d["extra"] and not any("autoBend" in j or "UpvRef" in j for j in d["extra"])


def test_missing_and_duplicates():
    d = H.new_map("MisotenChara", JOINTS)
    H.set_bone(d, "Spine", "")
    d["bones"]["Chest"] = d["bones"]["Hips"]
    msgs = H.validate(d, joints=JOINTS)
    assert any("Spine" in m and "空" in m for m in msgs)
    assert any("2 か所" in m for m in msgs)
    with pytest.raises(H.HumanoidMapError):
        H.set_bone(d, "Tail", "x")


def test_not_in_scene():
    d = H.new_map("MisotenChara", JOINTS)
    msgs = H.validate(d, joints=[j for j in JOINTS if j != "neck_C0_head_jnt"])
    assert any("neck_C0_head_jnt" in m and "シーンにありません" in m for m in msgs)


def test_hierarchy_order():
    d = {"bones": {"Hips": "a", "Spine": "b"}}
    ok = {"a": None, "b": "a"}
    assert H.validate(d, parents=ok) == [m for m in H.validate(d, parents=ok) if "必須" in m]
    bad = {"a": "b", "b": None}
    assert any("先祖" in m for m in H.validate(d, parents=bad))


def test_roundtrip_keeps_unknown_keys(tmp_path):
    d = H.new_map("MisotenChara", JOINTS)
    d["custom"] = {"a": 1}
    p = H.save(tmp_path / "h" / "humanoid.json", d)
    again = H.load(p)
    assert again == d
    with pytest.raises(H.HumanoidMapError):
        H.loads("{bad")
    with pytest.raises(H.HumanoidMapError):
        H.loads('{"format": "x"}')
