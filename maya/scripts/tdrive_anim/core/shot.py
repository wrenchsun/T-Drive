"""`shots/<Shot>.shot.json` の読み書き・検証（docs/20 §5.1）。Maya 非依存。

ショットは D-Drive の CutsceneData 1 つにあたる Maya 側の単位。キャラクターをまたぐので、`animset.json`（キャラクターごと）とは別に
プロジェクト直下の `shots/` に置く。知らないキーは落とさず書き戻す（`animset.py` と同じ流儀）。互換性は docs/06: キーの削除・改名は MAJOR、追加は MINOR。

```
{ "format": "TDriveShot", "version": 1, "shot": "Opening01", "frameRate": 30, "range": [0, 240],
  "cameras": [{"name": "CAM_Main", "node": "CAM_Main", "start": 0, "end": 120}, ...],
  "characters": [{"modelId": "MisotenChara", "namespace": "MisotenChara", "sequence": [<遷移列の行>], "facialTrack": true}],
  "props": [{"modelId": "Mic", "node": "PRP_Mic"}],
  "events": [{"frame": 60, "key": "Hit"}],
  "export": {"dir": "", "category": "Opening"} }
```
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

from .fileio import write_text_atomic

from .animset import FRAME_RATES, Issue, MODEL_ID_PATTERN

FORMAT = "TDriveShot"
VERSION = 1
SHOT_PATTERN = re.compile(r"^[A-Z][A-Za-z0-9]*$")
SUFFIX = ".shot.json"
DEFAULT_EXPORT = {"dir": "", "category": "Cutscene", "includeEvents": False}  # includeEvents: <Shot>.fbx に EVT_ を入れるか（D-Drive 未対応のため既定は入れない。docs/21 DD-2）


class ShotError(ValueError):
    pass


def new_shot(name: str, frame_rate: int = 30, start: int = 0, end: int = 240) -> dict[str, Any]:
    """新しいショット。名前の形・フレームレート・範囲が不正なら ShotError。"""
    if not SHOT_PATTERN.match(name or "") or "__" in name:
        raise ShotError("ショット名は英数字の PascalCase（例: Opening01。「__」は使えません）")
    if frame_rate not in FRAME_RATES:
        raise ShotError(f"フレームレートは {' か '.join(map(str, FRAME_RATES))}")
    if end < start:
        raise ShotError(f"終了（{end}）が開始（{start}）より前です")
    return {
        "format": FORMAT, "version": VERSION, "shot": name, "frameRate": frame_rate, "range": [int(start), int(end)],
        "cameras": [], "characters": [], "props": [], "events": [], "export": dict(DEFAULT_EXPORT),
    }


def normalize(data: dict[str, Any]) -> dict[str, Any]:
    d = copy.deepcopy(data)
    base = new_shot(d.get("shot") if SHOT_PATTERN.match(str(d.get("shot", ""))) else "Shot", 30)
    for k, v in base.items():
        d.setdefault(k, v)
    exp = dict(DEFAULT_EXPORT)
    exp.update(d.get("export") or {})
    d["export"] = exp
    for ch in d.get("characters") or []:
        if isinstance(ch, dict):
            ch.setdefault("sequence", [])
            ch.setdefault("facialTrack", False)
            ch.setdefault("namespace", ch.get("modelId", ""))
    return d


def loads(text: str) -> dict[str, Any]:
    try:
        raw = json.loads(text)
    except ValueError as exc:
        raise ShotError(f"shot.json を読めません（JSON が壊れています）: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("format") != FORMAT:
        raise ShotError(f"shot.json ではありません（format が {FORMAT} ではない）")
    return normalize(raw)


def dumps(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def load(path: str | Path) -> dict[str, Any]:
    try:
        return loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise ShotError(f"shot.json を開けません: {exc}") from exc


def save(path: str | Path, data: dict[str, Any]) -> Path:
    return write_text_atomic(path, dumps(data))


def path_for(shots_dir: str | Path, name: str) -> Path:
    return Path(shots_dir) / f"{name}{SUFFIX}"


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def validate(data: dict[str, Any]) -> list[Issue]:
    """構造の検査。error は保存・出力を止める、warning は表示だけ。"""
    out: list[Issue] = []

    def err(p: str, m: str) -> None:
        out.append(Issue("error", p, m))

    def warn(p: str, m: str) -> None:
        out.append(Issue("warning", p, m))

    if data.get("format") != FORMAT:
        err("format", f"{FORMAT} ではありません")
    if isinstance(data.get("version"), int) and data["version"] > VERSION:
        warn("version", f"このツールより新しい版（{data['version']}）のファイルです。読める所だけ読んでいます")
    name = data.get("shot")
    if not isinstance(name, str) or not SHOT_PATTERN.match(name) or "__" in name:
        err("shot", "ショット名は英数字の PascalCase（例: Opening01。「__」は使えません）")
    if data.get("frameRate") not in FRAME_RATES:
        err("frameRate", f"フレームレートは {' か '.join(map(str, FRAME_RATES))}（D-Drive の規則。1 ショット内は同じ）")
    rng = data.get("range")
    if not (isinstance(rng, list) and len(rng) == 2 and all(_is_int(x) for x in rng)):
        err("range", "[開始, 終了] の整数 2 つにしてください")
        lo = hi = None
    else:
        lo, hi = rng
        if hi < lo:
            err("range", f"終了（{hi}）が開始（{lo}）より前です")
    seen: set[str] = set()
    for i, c in enumerate(data.get("cameras") or []):
        p = f"cameras[{i}]"
        if not isinstance(c, dict) or not isinstance(c.get("node"), str) or not c.get("node"):
            err(p, "node（カメラのノード名）が要ります")
            continue
        if not (_is_int(c.get("start")) and _is_int(c.get("end"))) or c["end"] < c["start"]:
            err(p, "区間の開始・終了は整数で、終了が開始以上")
        elif lo is not None and (c["start"] < lo or c["end"] > hi):
            warn(p, f"区間 {c['start']}〜{c['end']} がショットの範囲（{lo}〜{hi}）の外にはみ出しています")
        if c["node"] in seen:
            warn(p, f"カメラ {c['node']} が複数の区間にあります（別の区間として扱います）")
        seen.add(c["node"])
    cams = [c for c in (data.get("cameras") or []) if isinstance(c, dict) and _is_int(c.get("start")) and _is_int(c.get("end"))]
    cams.sort(key=lambda c: c["start"])
    for a, b in zip(cams, cams[1:]):
        if b["start"] <= a["end"]:
            warn("cameras", f"カメラの区間が重なっています（{a['node']} {a['start']}〜{a['end']} と {b['node']} {b['start']}〜{b['end']}）。後のカメラが優先されます")
    ids: set[str] = set()
    namespaces: dict[str, str] = {}
    for i, ch in enumerate(data.get("characters") or []):
        p = f"characters[{i}]"
        if not isinstance(ch, dict) or not isinstance(ch.get("modelId"), str) or not MODEL_ID_PATTERN.match(ch.get("modelId", "")):
            err(p, "modelId は英数字の PascalCase")
            continue
        if ch["modelId"] in ids:
            warn(p, f"キャラクター {ch['modelId']} が複数います（2 体目は _2 を付けて出力します）")
        ids.add(ch["modelId"])
        ns = ch.get("namespace") if isinstance(ch.get("namespace"), str) else ""
        if ns in namespaces:  # 同じネームスペース = 同じスケルトン。D-Drive の _2 は別のリグが前提（DD-5）
            err(f"{p}.namespace", f"ネームスペース {ns!r} が {namespaces[ns]} と同じです（同じジョイントを指します）。2 体目は別のネームスペースにしてください")
        else:
            namespaces[ns] = f"characters[{i}]"
        if not isinstance(ch.get("sequence"), list):
            err(f"{p}.sequence", "遷移列は配列にしてください")
    keys: dict[str, int] = {}
    for i, e in enumerate(data.get("events") or []):
        p = f"events[{i}]"
        if not isinstance(e, dict) or not _is_int(e.get("frame")) or not isinstance(e.get("key"), str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", e.get("key", "")):
            err(p, "frame（整数）と key（英数字とアンダースコア）が要ります")
        else:
            if lo is not None and not lo <= e["frame"] <= hi:
                warn(p, f"イベントのフレーム {e['frame']} がショットの範囲（{lo}〜{hi}）の外です")
            elif lo is not None and e["frame"] == lo:
                warn(p, f"イベント {e['key']} がショットの先頭フレーム（{lo}）にあります。0 → 1 の変化が無いので、D-Drive が拾えないことがあります（1 フレーム後ろにずらすのが安全です）")
            keys[e["key"]] = keys.get(e["key"], 0) + 1
            if keys[e["key"]] == 2:
                warn(p, f"イベントのキー {e['key']} が複数あります。ロケータが EVT_{e['key']}1 のように番号付きになり、D-Drive では別のキーになります（DD-8）")
    return out


def fps_problems(data: dict[str, Any], animsets: dict[str, dict[str, Any]]) -> list[str]:
    """キャラクターの animset のフレームレートがショットと違うもの（クリップの範囲はフレーム番号のまま並ぶので、中身がずれる。DD-6）。"""
    out = []
    for ch in data.get("characters") or []:
        a = animsets.get(ch.get("modelId"))
        if a is not None and a.get("frameRate") != data.get("frameRate"):
            out.append(f"キャラクター {ch['modelId']} の animset は {a.get('frameRate')} fps、ショットは {data.get('frameRate')} fps です"
                       "（クリップの範囲はフレーム番号のまま並ぶので、同じにしてください）")
    return out


def errors(data: dict[str, Any]) -> list[Issue]:
    return [i for i in validate(data) if i.level == "error"]


# ------------------------------------------------------------------ 操作（辞書を書き換える）
def add_camera(data: dict[str, Any], node: str, start: int, end: int, name: str | None = None) -> dict[str, Any]:
    if end < start:
        raise ShotError(f"区間の終了（{end}）が開始（{start}）より前です")
    cam = {"name": name or node.rsplit(":", 1)[-1], "node": node, "start": int(start), "end": int(end)}
    data["cameras"].append(cam)
    data["cameras"].sort(key=lambda c: c["start"])
    return cam


def remove_camera(data: dict[str, Any], index: int) -> bool:
    if 0 <= index < len(data["cameras"]):
        del data["cameras"][index]
        return True
    return False


def add_character(data: dict[str, Any], model_id: str, namespace: str | None = None, sequence: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    if not MODEL_ID_PATTERN.match(model_id or ""):
        raise ShotError("modelId は英数字の PascalCase")
    ch = {"modelId": model_id, "namespace": model_id if namespace is None else namespace, "sequence": copy.deepcopy(sequence or []), "facialTrack": False}
    data["characters"].append(ch)
    return ch


def add_event(data: dict[str, Any], frame: int, key: str) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", key or ""):
        raise ShotError("イベントのキーは英数字とアンダースコア（先頭は英字）")
    lo, hi = data["range"]
    if not lo <= frame <= hi:
        raise ShotError(f"フレーム {frame} はショットの範囲（{lo}〜{hi}）の外です")
    ev = {"frame": int(frame), "key": key}
    data["events"].append(ev)
    data["events"].sort(key=lambda e: (e["frame"], e["key"]))
    return ev


def character_files(data: dict[str, Any]) -> list[tuple[dict[str, Any], str]]:
    """キャラクター → 出力ファイル名の末尾（`<Shot>__<ModelId>`、同じ ModelId の 2 体目は `_2`、3 体目は `_3`）。"""
    count: dict[str, int] = {}
    out = []
    for ch in data["characters"]:
        mid = ch["modelId"]
        count[mid] = count.get(mid, 0) + 1
        suffix = "" if count[mid] == 1 else f"_{count[mid]}"
        out.append((ch, f"{data['shot']}__{mid}{suffix}"))
    return out


def camera_at(data: dict[str, Any], frame: int) -> str | None:
    """その時刻のカメラ（区間が重なるときは後のもの）。無ければ None。"""
    hit = None
    for c in data["cameras"]:
        if c["start"] <= frame <= c["end"]:
            hit = c["node"]
    return hit
