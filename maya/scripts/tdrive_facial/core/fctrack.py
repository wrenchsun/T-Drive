"""`.fctrack`（Timeline 用の演出カーブ）のデータ形式（Maya 非依存。docs/14 §7.3、docs/15 §4.7）。

Maya のショットで、プレビュー用ノードの「強さ・感情の重み・角度の固定」に打ったキーをショット単位で持ち出す形式。
Unity 側（`FacialCorrectionTrack` を足すブリッジ）が読む。拡張子は `.fctrack`（中身は JSON）。

```jsonc
{ "format": "FacialTrack", "version": 1, "shot": "S010", "model": "shizuku", "frameRate": 30, "range": [0, 240],
  "curves": { "alpha": [[0.0, 1.0], [2.5, 0.0]], "emotion.Joy": [[1.0, 0.0], [1.5, 1.0]] } }
```

- キーは `[秒, 値]`。秒 = (フレーム − range[0]) / frameRate。時刻の昇順（同じ時刻の 2 つ目は段差）
- カーブの名前: `alpha` / `useManual`（0 か 1）/ `manualYaw` / `manualPitch`（度）/ `emotion.<レイヤー名>`（0〜1）
- 入っているのは「アニメーションしている属性」だけ。接線（補間）は書き出さない（Unity 側は線形。段階は同時刻の 2 キーで表す）
- 知らないキーは `extra` に受けて書き出しで戻す（往復で落とさない）
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from typing import Any, Union

FORMAT_TRACK = "FacialTrack"
SUPPORTED_VERSION = 1
EMOTION_PREFIX = "emotion."
FIXED_CURVES = ("alpha", "useManual", "manualYaw", "manualPitch")
FILE_SUFFIX = ".fctrack"

Key = tuple[float, float]  # (秒, 値)


class FcTrackError(ValueError):
    """読めない / 正しくない `.fctrack`。メッセージはそのまま画面に出せる日本語。"""


@dataclass
class FacialTrack:
    shot: str = ""
    model: str = ""
    frame_rate: float = 30.0
    range: tuple[float, float] = (0.0, 0.0)  # フレーム（開始, 終了）
    curves: dict[str, list[Key]] = field(default_factory=dict)
    version: int = SUPPORTED_VERSION
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def format(self) -> str:
        return FORMAT_TRACK

    @property
    def duration_seconds(self) -> float:
        return (self.range[1] - self.range[0]) / self.frame_rate if self.frame_rate > 0 else 0.0


def emotion_curve_name(layer: str) -> str:
    return f"{EMOTION_PREFIX}{layer}"


def is_valid_curve_name(name: str) -> bool:
    return name in FIXED_CURVES or (name.startswith(EMOTION_PREFIX) and len(name) > len(EMOTION_PREFIX))


def file_name(shot: str, model: str) -> str:
    """`<Shot>__<Model>.fctrack`（D-Drive のカットシーン FBX と同じ名前の付け方）。"""
    return f"{shot}__{model}{FILE_SUFFIX}"


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def validate(track: FacialTrack) -> list[str]:
    """問題の一覧（空 = 正しい）。"""
    errs: list[str] = []
    if track.version != SUPPORTED_VERSION:
        errs.append(f"version {track.version} は未対応です（対応: {SUPPORTED_VERSION}）")
    if not track.shot:
        errs.append("shot が空です")
    if not track.model:
        errs.append("model が空です")
    if not _is_num(track.frame_rate) or track.frame_rate <= 0:
        errs.append(f"frameRate は正の数にしてください: {track.frame_rate!r}")
    r = track.range
    if len(r) != 2 or not all(_is_num(x) for x in r):
        errs.append(f"range は [開始, 終了] の数 2 つにしてください: {r!r}")
    elif r[0] > r[1]:
        errs.append(f"range の開始が終了より後です: {list(r)}")
    for name, keys in track.curves.items():
        if not is_valid_curve_name(name):
            errs.append(f"カーブ名が正しくありません: {name!r}（alpha / useManual / manualYaw / manualPitch / emotion.<レイヤー名>）")
        last = -math.inf
        for i, k in enumerate(keys):
            if len(k) != 2 or not all(_is_num(x) for x in k):
                errs.append(f"{name}[{i}] は [秒, 値] の数 2 つにしてください: {k!r}")
                break
            if k[0] < last:
                errs.append(f"{name}[{i}] の時刻が前のキーより前です（昇順にしてください）")
                break
            last = k[0]
    return errs


def to_dict(track: FacialTrack) -> dict[str, Any]:
    d: dict[str, Any] = {
        "format": FORMAT_TRACK,
        "version": track.version,
        "shot": track.shot,
        "model": track.model,
        "frameRate": _num(track.frame_rate),
        "range": [_num(track.range[0]), _num(track.range[1])],
        "curves": {name: [[float(t), float(v)] for t, v in keys] for name, keys in track.curves.items()},
    }
    for k, v in track.extra.items():
        d.setdefault(k, v)
    return d


def _num(v: float) -> Union[int, float]:
    return int(v) if float(v).is_integer() else float(v)


def from_dict(d: Any) -> FacialTrack:
    """辞書 → FacialTrack。形が違えば FcTrackError（欠けた curves は空）。"""
    if not isinstance(d, dict):
        raise FcTrackError("fctrack の最上位が JSON オブジェクトではありません")
    if d.get("format") != FORMAT_TRACK:
        raise FcTrackError(f'"format" が "{FORMAT_TRACK}" ではありません: {d.get("format")!r}')
    version = d.get("version")
    if not isinstance(version, int) or isinstance(version, bool):
        raise FcTrackError(f"version が整数ではありません: {version!r}")
    rng = d.get("range", [0, 0])
    curves_raw = d.get("curves", {})
    if not isinstance(curves_raw, dict):
        raise FcTrackError("curves がオブジェクトではありません")
    if not isinstance(rng, (list, tuple)) or len(rng) != 2:
        raise FcTrackError(f"range は [開始, 終了] にしてください: {rng!r}")
    curves: dict[str, list[Key]] = {}
    for name, keys in curves_raw.items():
        if not isinstance(keys, list) or any(not isinstance(k, (list, tuple)) for k in keys):
            raise FcTrackError(f"{name} はキーの配列 [[秒, 値], ...] にしてください")
        curves[str(name)] = [tuple(k) for k in keys]  # type: ignore[misc]
    known = ("format", "version", "shot", "model", "frameRate", "range", "curves")
    track = FacialTrack(
        shot=d.get("shot", "") if isinstance(d.get("shot", ""), str) else "",
        model=d.get("model", "") if isinstance(d.get("model", ""), str) else "",
        frame_rate=d.get("frameRate", 30.0),
        range=(rng[0], rng[1]),
        curves=curves,
        version=version,
        extra={k: v for k, v in d.items() if k not in known},
    )
    errs = validate(track)
    if errs:
        raise FcTrackError(errs[0])
    track.range = (float(rng[0]), float(rng[1]))
    track.frame_rate = float(track.frame_rate)
    track.curves = {n: [(float(t), float(v)) for t, v in ks] for n, ks in curves.items()}
    return track


def dumps(track: FacialTrack) -> str:
    """JSON の文字列（カーブごとに 1 行。差分が読みやすい）。"""
    d = to_dict(track)
    curves = d.pop("curves")
    head = json.dumps(d, ensure_ascii=False, indent=2)[:-2]  # 最後の "\n}" を外す
    lines = [f"  {json.dumps(n, ensure_ascii=False)}: {json.dumps(ks)}" for n, ks in curves.items()]
    body = "{\n" + ",\n".join(lines) + "\n  }" if lines else "{}"
    return head + ',\n  "curves": ' + body + "\n}\n"


def loads(text: str) -> FacialTrack:
    from .fcpose_io import FcposeError, parse_json  # 厳密な読み込み（NaN / Infinity・深すぎる入れ子を拒否。C# と同じ）

    try:
        return from_dict(parse_json(text))
    except FcposeError as e:
        raise FcTrackError(f"JSON として読めません: {e}") from e


def load(path: Union[str, os.PathLike]) -> FacialTrack:
    with open(path, "r", encoding="utf-8-sig") as f:
        return loads(f.read())


def save(track: FacialTrack, path: Union[str, os.PathLike]) -> None:
    """ファイルへ書く（UTF-8・BOM なし・改行 \n）。正しくなければ FcTrackError。親フォルダが無ければ作る。"""
    errs = validate(track)
    if errs:
        raise FcTrackError(errs[0])
    parent = os.path.dirname(os.fspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(dumps(track))
