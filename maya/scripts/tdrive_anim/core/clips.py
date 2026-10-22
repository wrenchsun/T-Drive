"""クリップの操作（docs/20 §3。Maya 非依存）。名前の組み立て・複製・イベント・属性のコピー / 貼り付け・検索。

辞書（`core/animset.py` の clips[]）を書き換える関数と、書き換えない問い合わせ関数だけ。Undo の区切りは呼び出し側（session）。
"""

from __future__ import annotations

import copy
import json
import re
from typing import Any

from . import animset as A
from .animset import AnimSetError

FOOT_PLANT = "OnFootPlant"
FOOT_SIDES = {"left": 0, "right": 1}  # OnFootPlant の args（0 = 左足、1 = 右足。仮の決め。docs/20 §3.1）
# 属性のコピー / 貼り付けの対象（名前・範囲・イベントは含めない）
ATTR_KEYS = ("loop", "rootMotion", "layer", "crossFade", "stateGroup")
CLIPBOARD_FORMAT = "TDriveClipAttrs"


def compose_name(model_id: str, action: str, direction: str = "", number: int | None = None) -> str:
    """`A_<ModelId>_<動作>[_<方向>][<連番 2 桁>]`。動作は PascalCase の単語。方向は F / B / L / R。"""
    if not re.fullmatch(r"[A-Z][A-Za-z]*", action or ""):
        raise AnimSetError("動作は英字の PascalCase にしてください（例: Run）")
    if direction and direction not in ("F", "B", "L", "R"):
        raise AnimSetError("方向は F / B / L / R のどれかです")
    name = f"A_{model_id}_{action}"
    if direction:
        name += f"_{direction}"
    if number is not None:
        name += f"{int(number):02d}"
    return name


def unique_name(data: dict[str, Any], name: str) -> str:
    """既にある名前なら、末尾の連番を 02, 03 … と進めて重複しない名前にする。"""
    if A.find_clip(data, name) is None:
        return name
    m = re.fullmatch(r"(.*?)(\d{2})?", name)
    stem = m.group(1)
    n = int(m.group(2)) + 1 if m.group(2) else 2
    while A.find_clip(data, f"{stem}{n:02d}") is not None:
        n += 1
    return f"{stem}{n:02d}"


def frame_count(clip: dict[str, Any]) -> int:
    return int(clip["end"]) - int(clip["start"]) + 1


def duration_sec(clip: dict[str, Any], fps: int) -> float:
    """再生時間（秒）。範囲は end を含むので、最終フレームまで含めて (end - start) / fps。"""
    return (int(clip["end"]) - int(clip["start"])) / float(fps)


def from_range(data: dict[str, Any], action: str, start: int, end: int, direction: str = "", number: int | None = None) -> dict[str, Any]:
    """タイムスライダーの範囲から新しいクリップ（既定の属性つき）を作って animset に足す。名前の重複は AnimSetError。"""
    if end < start:
        raise AnimSetError(f"終了（{end}）が開始（{start}）より前です")
    name = compose_name(data["modelId"], action, direction, number)
    return A.add_clip(data, A.default_clip(name, int(start), int(end), data["modelId"]))


def duplicate(data: dict[str, Any], name: str) -> dict[str, Any]:
    src = A.find_clip(data, name)
    if src is None:
        raise AnimSetError(f"クリップがありません: {name}")
    new = copy.deepcopy(src)
    new["name"] = unique_name(data, name)
    data["clips"].insert(data["clips"].index(src) + 1, new)
    return new


def set_range(clip: dict[str, Any], start: int, end: int) -> None:
    if end < start:
        raise AnimSetError(f"終了（{end}）が開始（{start}）より前です")
    clip["start"], clip["end"] = int(start), int(end)


def update(data: dict[str, Any], clip_name: str, /, **fields: Any) -> dict[str, Any]:
    """クリップの属性を変える。名前の変更・重複・範囲の逆転・値の不正は AnimSetError（変更は適用されない）。"""
    clip = A.find_clip(data, clip_name)
    if clip is None:
        raise AnimSetError(f"クリップがありません: {clip_name}")
    trial = copy.deepcopy(clip)
    trial.update(fields)
    if "name" in fields and trial["name"] != clip_name and A.find_clip(data, trial["name"]) is not None:
        raise AnimSetError(f"同じ名前のクリップがあります: {trial['name']}")
    probe = copy.deepcopy(data)
    probe["clips"] = [trial if c["name"] == clip_name else c for c in probe["clips"]]
    errs = [e for e in A.errors(probe) if e.path.startswith("clips[")]
    if errs:
        raise AnimSetError(str(errs[0]))
    clip.clear()
    clip.update(trial)
    return clip


# ------------------------------------------------------------------ イベント
def add_event(clip: dict[str, Any], frame: int, key: str, args: Any = None) -> dict[str, Any]:
    """イベントを足す（フレーム順に並べる）。クリップの範囲の外・空のキーは AnimSetError。"""
    key = (key or "").strip()
    if not key:
        raise AnimSetError("イベントのキーが空です")
    if not int(clip["start"]) <= int(frame) <= int(clip["end"]):
        raise AnimSetError(f"フレーム {frame} はクリップの範囲（{clip['start']}〜{clip['end']}）の外です")
    ev: dict[str, Any] = {"frame": int(frame), "key": key}
    if args is not None:
        ev["args"] = args
    clip["events"].append(ev)
    clip["events"].sort(key=lambda e: (e["frame"], e["key"]))
    return ev


def add_foot_plant(clip: dict[str, Any], frame: int, side: str) -> dict[str, Any]:
    if side not in FOOT_SIDES:
        raise AnimSetError("足は left / right のどちらかです")
    return add_event(clip, frame, FOOT_PLANT, FOOT_SIDES[side])


def remove_event(clip: dict[str, Any], index: int) -> bool:
    if 0 <= index < len(clip["events"]):
        del clip["events"][index]
        return True
    return False


# ------------------------------------------------------------------ 検索・コピー
def search(data: dict[str, Any], text: str) -> list[dict[str, Any]]:
    """名前・レイヤー・イベントのキーに部分一致（大文字小文字を区別しない）するクリップ。空なら全部。"""
    t = (text or "").strip().lower()
    if not t:
        return list(data["clips"])
    out = []
    for c in data["clips"]:
        hay = " ".join([c["name"], c["layer"], c["stateGroup"]] + [e["key"] for e in c["events"]]).lower()
        if t in hay:
            out.append(c)
    return out


def attrs_to_text(clip: dict[str, Any]) -> str:
    """属性（ループ・ルートモーション・レイヤー・クロスフェード・状態グループ）をコピー用の JSON 文字列にする。"""
    return json.dumps({"format": CLIPBOARD_FORMAT, **{k: clip[k] for k in ATTR_KEYS}}, ensure_ascii=False)


def attrs_from_text(text: str) -> dict[str, Any]:
    """コピーした属性を読む。壊れた・別の形式の文字列は AnimSetError（貼り付けを止める。画面は止めない）。"""
    try:
        raw = json.loads(text)
    except ValueError as exc:
        raise AnimSetError("クリップの属性ではありません（貼り付けられません）") from exc
    if not isinstance(raw, dict) or raw.get("format") != CLIPBOARD_FORMAT:
        raise AnimSetError("クリップの属性ではありません（貼り付けられません）")
    return {k: raw[k] for k in ATTR_KEYS if k in raw}
