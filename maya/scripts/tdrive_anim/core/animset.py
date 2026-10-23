"""`anim/<ModelId>/animset.json` の読み書き・検証（docs/20 §3.3）。Maya 非依存。

- 唯一の真実はこのファイル（Git 管理）。シーンには fileInfo `tdAnimSetPath`（プロジェクト相対）だけを置く
- 辞書のまま持つ（`normalize` で足りないキーを既定値で補う）。**知らないキーは落とさず書き戻す**（新しい版・他のツールが足したキー）
- 互換性（docs/06）: キーの削除・改名は MAJOR、追加は MINOR。`version` が新しいファイルは警告つきで読める所だけ読む
- 検証（`validate`）は構造の検査だけ。名前の規則（`A_<ModelId>_<動作>` など）は `naming_rules`（命名タブ）が受け持つ
"""

from __future__ import annotations

import copy
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .fileio import write_text_atomic

FORMAT = "TDriveAnimSet"
VERSION = 1
FRAME_RATES = (30, 60)
UNITS = ("cm",)
UP_AXES = ("Y",)
ROOT_MOTIONS = ("none", "bake")
LAYERS = ("Base", "UpperBody")
STATE_GROUPS = ("locomotion", "oneshot")
MODEL_ID_PATTERN = re.compile(r"^[A-Z][A-Za-z0-9]*$")
# ループ再生が既定の動作（docs/20 §2.3。Idle / Walk / Run）
LOOP_ACTIONS = ("Idle", "Walk", "Run")
LOCOMOTION_ACTIONS = ("Idle", "Walk", "Run")

DEFAULT_CROSSFADE = 0.1  # D-Drive の DefaultCrossFade（docs/20 §9 Q-2）
DEFAULT_LAYERS = [{"name": "Base"}, {"name": "UpperBody", "maskRoot": "spine_C0_3_jnt"}]
DEFAULT_EXPORT = {"modelDir": "", "animDir": "", "category": "Player"}


class AnimSetError(ValueError):
    """読めない（JSON が壊れている・別の形式）・操作できない（名前の重複）。"""


class AnimSetExists(AnimSetError):
    """新規の animset の保存先に、確かめていない既存のファイルがある（上書きしない）。"""


@dataclass
class Issue:
    level: str  # "error" | "warning"
    path: str  # 例: "clips[1].start"
    message: str

    def __str__(self) -> str:
        return f"{'❌' if self.level == 'error' else '⚠'} {self.path}: {self.message}"


# ------------------------------------------------------------------ 作る
def action_of(clip_name: str, model_id: str = "") -> str:
    """`A_<ModelId>_<動作>[_方向][連番]` から動作の語（Idle / Run …）を取り出す。判別できなければ ""。"""
    prefix = f"A_{model_id}_" if model_id else None
    if prefix and clip_name.startswith(prefix):
        rest = clip_name[len(prefix):]
    else:
        parts = clip_name.split("_", 2)
        rest = parts[2] if len(parts) == 3 and parts[0] == "A" else ""
    m = re.match(r"^([A-Z][a-z]+)", rest)
    return m.group(1) if m else ""


def root_name(data: dict[str, Any]) -> str:
    """シーンでのスケルトンのルートのノード名: リグを参照して読み込んだとき（`namespace` が空でない）は `<ns>:<skeletonRoot>`。"""
    ns = data.get("namespace") or ""
    root = data["skeletonRoot"]
    return f"{ns}:{root}" if ns and not root.startswith(f"{ns}:") else root


def default_clip(name: str, start: int, end: int, model_id: str = "") -> dict[str, Any]:
    """クリップの既定値（docs/20 §3.1）。loop / stateGroup は動作の語彙から決める。"""
    action = action_of(name, model_id)
    return {
        "name": name, "start": start, "end": end,
        "loop": action in LOOP_ACTIONS, "rootMotion": "none", "layer": "Base", "crossFade": DEFAULT_CROSSFADE,
        "events": [], "stateGroup": "locomotion" if action in LOCOMOTION_ACTIONS else "oneshot",
    }


def new_animset(model_id: str, skeleton_root: str = "spine_C0_0_jnt", meshes: list[str] | None = None) -> dict[str, Any]:
    return {
        "format": FORMAT, "version": VERSION, "modelId": model_id, "frameRate": 30, "units": "cm", "upAxis": "Y",
        "skeletonRoot": skeleton_root, "namespace": "", "meshes": list(meshes or []), "clips": [],
        "layers": copy.deepcopy(DEFAULT_LAYERS), "stateGraph": None, "export": dict(DEFAULT_EXPORT),
    }


def normalize(data: dict[str, Any]) -> dict[str, Any]:
    """足りないキーを既定値で補う（知らないキーは残す）。コピーを返す。"""
    d = copy.deepcopy(data)
    for k, v in new_animset(str(d.get("modelId", ""))).items():
        d.setdefault(k, v)
    exp = dict(DEFAULT_EXPORT)
    exp.update(d.get("export") or {})
    d["export"] = exp
    clips = []
    for c in d.get("clips") or []:
        if not isinstance(c, dict):
            continue
        full = default_clip(str(c.get("name", "")), c.get("start", 0), c.get("end", 0), str(d.get("modelId", "")))
        full.update(c)
        clips.append(full)
    d["clips"] = clips
    return d


# ------------------------------------------------------------------ 読み書き
def loads(text: str) -> dict[str, Any]:
    try:
        raw = json.loads(text)
    except ValueError as exc:
        raise AnimSetError(f"animset.json を読めません（JSON が壊れています）: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("format") != FORMAT:
        raise AnimSetError(f"animset.json ではありません（format が {FORMAT} ではない）")
    bad = type_problems(raw)
    if bad:
        more = f"（ほか {len(bad) - 3} 件）" if len(bad) > 3 else ""
        raise AnimSetError("animset.json の中身の型が壊れています: " + " / ".join(bad[:3]) + more)
    return normalize(raw)


def type_problems(raw: dict[str, Any]) -> list[str]:
    """画面・出力が読む所の値の型が壊れていないか（JSON としては正しくても、clips が文字列など）。壊れた所の一覧（空なら OK）。
    値の中身（範囲の逆転・名前の形）は `validate` が見る。ここは「読んだだけで画面が例外になる」ものだけ。"""
    out: list[str] = []

    def is_list_of_dicts(v: Any) -> bool:
        return isinstance(v, list) and all(isinstance(x, dict) for x in v)

    for key in ("clips", "layers"):
        if key in raw and not is_list_of_dicts(raw[key]):
            out.append(f"{key} はオブジェクトの配列にしてください")
    if "meshes" in raw and not (isinstance(raw["meshes"], list) and all(isinstance(m, str) for m in raw["meshes"])):
        out.append("meshes は文字列の配列にしてください")
    if "export" in raw and not isinstance(raw["export"], dict):
        out.append("export はオブジェクトにしてください")
    if isinstance(raw.get("clips"), list):
        for i, c in enumerate(raw["clips"]):
            if not isinstance(c, dict):
                continue
            for k in ("start", "end"):
                if k in c and not _is_int(c[k]):
                    out.append(f"clips[{i}].{k} は整数にしてください")
            if "name" in c and not isinstance(c["name"], str):
                out.append(f"clips[{i}].name は文字列にしてください")
            if "events" in c and not is_list_of_dicts(c["events"]):
                out.append(f"clips[{i}].events はオブジェクトの配列にしてください")
    if isinstance(raw.get("layers"), list):
        for i, l in enumerate(raw["layers"]):
            if isinstance(l, dict) and "name" in l and not isinstance(l["name"], str):
                out.append(f"layers[{i}].name は文字列にしてください")
    sg = raw.get("stateGraph")
    if sg is not None:
        if not isinstance(sg, dict):
            out.append("stateGraph はオブジェクトにしてください")
        else:
            for key in ("parameters", "states", "transitions"):
                if key in sg and not is_list_of_dicts(sg[key]):
                    out.append(f"stateGraph.{key} はオブジェクトの配列にしてください")
            for i, t in enumerate(sg.get("transitions") or []):
                if isinstance(t, dict) and "conditions" in t and not is_list_of_dicts(t["conditions"]):
                    out.append(f"stateGraph.transitions[{i}].conditions はオブジェクトの配列にしてください")
    return out


def dumps(data: dict[str, Any]) -> str:
    """UTF-8・インデント 2・末尾に改行 1 つ。"""
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def load(path: str | Path) -> dict[str, Any]:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise AnimSetError(f"animset.json を開けません: {exc}") from exc
    return loads(text)


def save(path: str | Path, data: dict[str, Any]) -> Path:
    return write_text_atomic(path, dumps(data))


def newer_version(data: dict[str, Any]) -> bool:
    """このツールより新しい版で書かれたファイルか（読める所だけ読む）。"""
    v = data.get("version", VERSION)
    return isinstance(v, int) and v > VERSION


# ------------------------------------------------------------------ 検証
def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def validate(data: dict[str, Any]) -> list[Issue]:
    """構造の検査。error は保存・出力を止める、warning は表示だけ。"""
    out: list[Issue] = []

    def err(path: str, msg: str) -> None:
        out.append(Issue("error", path, msg))

    def warn(path: str, msg: str) -> None:
        out.append(Issue("warning", path, msg))

    if data.get("format") != FORMAT:
        err("format", f"{FORMAT} ではありません")
    if newer_version(data):
        warn("version", f"このツールより新しい版（{data.get('version')}）のファイルです。読める所だけ読んでいます")
    mid = data.get("modelId")
    if not isinstance(mid, str) or not MODEL_ID_PATTERN.match(mid):
        err("modelId", "ModelId は英数字の PascalCase（例: MisotenChara）")
    if data.get("frameRate") not in FRAME_RATES:
        err("frameRate", f"フレームレートは {' か '.join(map(str, FRAME_RATES))}（D-Drive の規則）")
    if data.get("units") not in UNITS:
        err("units", "単位は cm")
    if data.get("upAxis") not in UP_AXES:
        err("upAxis", "上方向は Y")
    if not isinstance(data.get("skeletonRoot"), str) or not data.get("skeletonRoot"):
        err("skeletonRoot", "スケルトンのルートジョイントが空です")
    if not isinstance(data.get("namespace", ""), str):
        err("namespace", "ネームスペースは文字列（無ければ空）")
    meshes = data.get("meshes")
    if not isinstance(meshes, list) or not all(isinstance(m, str) for m in meshes):
        err("meshes", "メッシュ名の配列にしてください")

    layer_names = {l.get("name") for l in data.get("layers") or [] if isinstance(l, dict)}
    seen: dict[str, int] = {}
    clips = data.get("clips")
    if not isinstance(clips, list):
        err("clips", "配列にしてください")
        clips = []
    for i, c in enumerate(clips):
        p = f"clips[{i}]"
        if not isinstance(c, dict):
            err(p, "オブジェクトにしてください")
            continue
        name = c.get("name")
        if not isinstance(name, str) or not name:
            err(f"{p}.name", "クリップ名が空です")
        elif name in seen:
            err(f"{p}.name", f"名前が重複しています: {name}（{seen[name]} 番目と同じ）")
        else:
            seen[name] = i
        s, e = c.get("start"), c.get("end")
        if not _is_int(s) or not _is_int(e):
            err(f"{p}.start", "開始・終了フレームは整数")
        elif e < s:
            err(f"{p}.end", f"終了（{e}）が開始（{s}）より前です")
        if c.get("rootMotion") not in ROOT_MOTIONS:
            err(f"{p}.rootMotion", f"{' / '.join(ROOT_MOTIONS)} のどちらか")
        if c.get("layer") not in layer_names:
            err(f"{p}.layer", f"レイヤー {c.get('layer')!r} がありません")
        if c.get("stateGroup") not in STATE_GROUPS:
            err(f"{p}.stateGroup", f"{' / '.join(STATE_GROUPS)} のどちらか")
        cf = c.get("crossFade")
        if isinstance(cf, bool) or not isinstance(cf, (int, float)) or cf < 0:
            err(f"{p}.crossFade", "クロスフェードは 0 以上の秒")
        if not isinstance(c.get("loop"), bool):
            err(f"{p}.loop", "true / false")
        for j, ev in enumerate(c.get("events") or []):
            ep = f"{p}.events[{j}]"
            if not isinstance(ev, dict) or not _is_int(ev.get("frame")) or not isinstance(ev.get("key"), str) or not ev.get("key"):
                err(ep, "frame（整数）と key（文字列）が要ります")
            elif _is_int(s) and _is_int(e) and not s <= ev["frame"] <= e:
                warn(ep, f"イベントのフレーム {ev['frame']} がクリップの範囲（{s}〜{e}）の外です")
    return out


def errors(data: dict[str, Any]) -> list[Issue]:
    return [i for i in validate(data) if i.level == "error"]


# ------------------------------------------------------------------ 操作（辞書を書き換える。Undo の区切りは呼び出し側）
def find_clip(data: dict[str, Any], name: str) -> dict[str, Any] | None:
    return next((c for c in data["clips"] if c["name"] == name), None)


def add_clip(data: dict[str, Any], clip: dict[str, Any]) -> dict[str, Any]:
    """名前が重複するときは AnimSetError。"""
    if find_clip(data, clip["name"]) is not None:
        raise AnimSetError(f"同じ名前のクリップがあります: {clip['name']}")
    data["clips"].append(clip)
    return clip


def rescale_frames(data: dict[str, Any], ratio: float) -> int:
    """クリップの範囲とイベントのフレームを ratio 倍する（シーンの fps を、キーの時刻を保ったまま変えたとき。24 → 30 fps なら 1.25 倍。AN-F-13）。
    開始は切り捨て・終了は切り上げ（キーが範囲からこぼれないように）、イベントは四捨五入。変えたクリップの数を返す。"""
    n = 0
    for c in data.get("clips", []):
        s, e = int(math.floor(c["start"] * ratio + 1e-9)), int(math.ceil(c["end"] * ratio - 1e-9))
        if (s, e) != (c["start"], c["end"]):
            n += 1
        c["start"], c["end"] = s, e
        for ev in c.get("events", []):
            ev["frame"] = int(round(ev["frame"] * ratio))
        c["events"] = sorted(c.get("events", []), key=lambda ev: (ev["frame"], ev["key"]))
    return n


def remove_clip(data: dict[str, Any], name: str) -> bool:
    n = len(data["clips"])
    data["clips"] = [c for c in data["clips"] if c["name"] != name]
    return len(data["clips"]) != n
