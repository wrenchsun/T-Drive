"""Humanoid 対応表 `anim/<ModelId>/humanoid.json`（docs/20 §2.4。Maya 非依存）。

Unity の Humanoid ボーン名 → Maya のジョイント名。mGear の名前（`arm_L0_0_jnt` など）は Unity の自動マッピングで外れやすいので、
ツールが対応表を持ち、モデル出力時に書き出す（Unity 側の取り込み U-A1 が Avatar 作成に使う）。

- `infer(joints)` が mGear の部位名から**自動推定**する。推定できないものは空欄（検証で知らせる）
- `validate(...)` は必須 15 ボーン（Hips / Spine / Head / 両腕 3 + 両脚 3）が埋まっていること・同じジョイントを 2 回使っていないこと・
  ジョイントがシーンにあること・親子の順序が Humanoid と矛盾しないこと（Hips が Spine の先祖、など）を調べる
- 知らないキーは落とさず書き戻す（互換性: 追加は MINOR、削除・改名は MAJOR）
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any, Iterable

FORMAT = "TDriveHumanoidMap"
VERSION = 1

REQUIRED = [
    "Hips", "Spine", "Head",
    "LeftUpperArm", "LeftLowerArm", "LeftHand", "RightUpperArm", "RightLowerArm", "RightHand",
    "LeftUpperLeg", "LeftLowerLeg", "LeftFoot", "RightUpperLeg", "RightLowerLeg", "RightFoot",
]
OPTIONAL = [
    "Chest", "UpperChest", "Neck", "LeftShoulder", "RightShoulder", "LeftToes", "RightToes", "Jaw", "LeftEye", "RightEye",
]
FINGERS = ["Thumb", "Index", "Middle", "Ring", "Little"]
PHALANGES = ["Proximal", "Intermediate", "Distal"]
ALL_BONES = REQUIRED + OPTIONAL + [f"{s}{f}{p}" for s in ("Left", "Right") for f in FINGERS for p in PHALANGES]

# 親子の順序（祖先 → 子孫）。親子関係が分かるときだけ検査する
ORDER = [
    ("Hips", "Spine"), ("Spine", "Chest"), ("Chest", "UpperChest"), ("Spine", "Neck"), ("Neck", "Head"), ("Spine", "Head"),
    ("Hips", "LeftUpperLeg"), ("LeftUpperLeg", "LeftLowerLeg"), ("LeftLowerLeg", "LeftFoot"), ("LeftFoot", "LeftToes"),
    ("Hips", "RightUpperLeg"), ("RightUpperLeg", "RightLowerLeg"), ("RightLowerLeg", "RightFoot"), ("RightFoot", "RightToes"),
    ("Spine", "LeftUpperArm"), ("LeftUpperArm", "LeftLowerArm"), ("LeftLowerArm", "LeftHand"),
    ("Spine", "RightUpperArm"), ("RightUpperArm", "RightLowerArm"), ("RightLowerArm", "RightHand"),
    ("Head", "Jaw"), ("Head", "LeftEye"), ("Head", "RightEye"),
]

# mGear: finger_L0..L3 = 人差し指..小指（番号は人差し指から）。thumb は別
_MGEAR_FINGER = {"0": "Index", "1": "Middle", "2": "Ring", "3": "Little"}
_SIDE = {"L": "Left", "R": "Right"}


class HumanoidMapError(ValueError):
    pass


def infer(joints: Iterable[str]) -> dict[str, str]:
    """mGear のジョイント名の一覧から対応表（Unity のボーン名 → ジョイント名）を推定する。見つからないボーンは含めない。"""
    names = set(joints)
    bones: dict[str, str] = {}

    def pick(bone: str, *candidates: str) -> None:
        for c in candidates:
            if c in names:
                bones[bone] = c
                return

    pick("Hips", "spine_C0_0_jnt")
    pick("Spine", "spine_C0_1_jnt")
    pick("Chest", "spine_C0_2_jnt")
    pick("UpperChest", "spine_C0_3_jnt")
    pick("Neck", "neck_C0_0_jnt")
    pick("Head", "neck_C0_head_jnt")
    pick("Jaw", "mouth_C0_jaw_jnt")
    for s, side in _SIDE.items():
        pick(f"{side}Shoulder", f"shoulder_{s}0_shoulder_jnt")
        pick(f"{side}UpperArm", f"arm_{s}0_0_jnt")
        pick(f"{side}LowerArm", f"arm_{s}0_4_jnt", f"arm_{s}0_3_jnt")
        pick(f"{side}Hand", f"arm_{s}0_end_jnt")
        pick(f"{side}UpperLeg", f"leg_{s}0_0_jnt")
        pick(f"{side}LowerLeg", f"leg_{s}0_4_jnt", f"leg_{s}0_3_jnt")
        pick(f"{side}Foot", f"leg_{s}0_end_jnt")
        pick(f"{side}Toes", f"foot_{s}0_0_jnt")
        pick(f"{side}Eye", f"eye_{s}0_eye_jnt")
        for n, finger in _MGEAR_FINGER.items():
            for i, ph in enumerate(PHALANGES):
                pick(f"{side}{finger}{ph}", f"finger_{s}{n}_{i}_jnt")
        for i, ph in enumerate(PHALANGES):
            pick(f"{side}Thumb{ph}", f"thumb_{s}0_{i}_jnt")
    return bones


def extras(joints: Iterable[str], bones: dict[str, str], helpers: Iterable[str] = ()) -> list[str]:
    """対応表に入らなかったジョイント（髪・スカート・舌など。Unity では extra ボーンとしてそのまま持ち込まれる）。

    mGear の補助ジョイント（`*_autoBend*` `*UpvRef*`）は除く。
    """
    used = set(bones.values())
    skip = re.compile(r"(autoBend|UpvRef|rollChain)")
    return [j for j in joints if j not in used and not skip.search(j) and j not in set(helpers)]


def new_map(model_id: str, joints: Iterable[str], root: str = "spine_C0_0_jnt") -> dict[str, Any]:
    joints = list(joints)
    bones = infer(joints)
    return {"format": FORMAT, "version": VERSION, "modelId": model_id, "root": root, "bones": bones, "extra": extras(joints, bones)}


def validate(data: dict[str, Any], joints: Iterable[str] | None = None, parents: dict[str, str | None] | None = None) -> list[str]:
    """問題の一覧（空なら OK）。joints = シーンのジョイント名、parents = ジョイント名 → 親のジョイント名（分かるときだけ）。"""
    out: list[str] = []
    bones = data.get("bones") or {}
    for b in REQUIRED:
        if not bones.get(b):
            out.append(f"必須のボーン {b} が空です")
    seen: dict[str, str] = {}
    for b, j in bones.items():
        if b not in ALL_BONES:
            out.append(f"{b} は Humanoid のボーン名ではありません")
        if not j:
            continue
        if j in seen:
            out.append(f"{j} を {seen[j]} と {b} の 2 か所で使っています")
        seen[j] = b
    if joints is not None:
        have = set(joints)
        out += [f"{b} のジョイント {j} がシーンにありません" for b, j in bones.items() if j and j not in have]
    if parents is not None:
        def ancestors(j: str) -> list[str]:
            chain, cur, guard = [], parents.get(j), 0
            while cur and guard < 1000:
                chain.append(cur)
                cur, guard = parents.get(cur), guard + 1
            return chain

        for a, d in ORDER:
            ja, jd = bones.get(a), bones.get(d)
            if ja and jd and jd in parents and ja in parents and ja not in ancestors(jd):
                out.append(f"{a}（{ja}）が {d}（{jd}）の親の系統にありません。Humanoid では {a} が {d} の先祖である必要があります")
    return out


def missing_required(data: dict[str, Any]) -> list[str]:
    bones = data.get("bones") or {}
    return [b for b in REQUIRED if not bones.get(b)]


def set_bone(data: dict[str, Any], bone: str, joint: str) -> None:
    """ボーンにジョイントを入れる（空文字で外す）。ボーン名が不正なら HumanoidMapError。"""
    if bone not in ALL_BONES:
        raise HumanoidMapError(f"{bone} は Humanoid のボーン名ではありません")
    if joint:
        data.setdefault("bones", {})[bone] = joint
    else:
        data.setdefault("bones", {}).pop(bone, None)


# ------------------------------------------------------------------ 読み書き
def loads(text: str) -> dict[str, Any]:
    try:
        raw = json.loads(text)
    except ValueError as exc:
        raise HumanoidMapError(f"humanoid.json を読めません（JSON が壊れています）: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("format") != FORMAT:
        raise HumanoidMapError(f"humanoid.json ではありません（format が {FORMAT} ではない）")
    d = copy.deepcopy(raw)
    d.setdefault("version", VERSION)
    d.setdefault("bones", {})
    d.setdefault("extra", [])
    d.setdefault("root", "spine_C0_0_jnt")
    return d


def dumps(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def load(path: str | Path) -> dict[str, Any]:
    try:
        return loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise HumanoidMapError(f"humanoid.json を開けません: {exc}") from exc


def save(path: str | Path, data: dict[str, Any]) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(dumps(data), encoding="utf-8", newline="\n")
    return p
