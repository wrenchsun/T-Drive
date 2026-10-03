"""Unity の `.anim`（AnimationClip の YAML）を読む（Maya 非依存・標準ライブラリだけ。docs/15 §4.6、docs/14 §5.4・§8 R-26）。

読むもの:
- `m_FloatCurves`: `attribute: blendShape.<名前>`（例 `blendShape.bs.eye_close_L`）+ `path`（メッシュの階層パス）+ キー（time / value）。
  Unity のブレンドシェイプの値は 0〜100 → ここでは 0〜1 の重みにする（`to_curve_weights`）
- Transform のカーブ（ボーン）: `m_RotationCurves`（クォータニオン）/ `m_EulerCurves`（Euler 角 [度]）/ `m_PositionCurves` / `m_ScaleCurves`。
  `path` は骨格の階層パス（`bone_root/.../bone_head/bone_eye_L`）。値は Unity の系（左手・Y-up・m）の**ローカルの絶対値**（ずれではない）

読まないもの: `m_CompressedRotationCurves`（圧縮された回転。空でないクリップは `AnimClip.warnings` に出す）、`m_EditorCurves`
（編集用の控え。同じ内容の複製）、`m_PPtrCurves`、イベント、アニメーションの設定。

## 補間
`evaluate` は既定で**線形補間**（キーの前後は端の値のまま）。Unity の接線（inSlope / outSlope・tangentMode）は既定では見ない。
ただし outSlope が無限大（`Infinity`。階段状のキー）なら次のキーまで前の値を保つ。`hermite=True` で、Unity と同じ 3 次エルミート補間
（接線を使う）にできる（重みの確認ではほぼ不要。キーが 1 つだけの表情のクリップなら補間はどちらでも同じ）。

## 構文の読み方
Unity の YAML（`%YAML` / `%TAG` の見出し、`--- !u!74 &7400000` の文書区切り）を、小さな行ベースのパーサー（ブロックのマップ・列と
`{x: 0, y: 1}` の 1 行マップ・`[]`）で読む。PyYAML は使わない。インデントの幅・`- ` の位置（キーと同じ桁 / 深い桁）はどちらも読める。
読む対象のセクションだけを解析する（知らない書き方が他のセクションにあっても落ちない）。読めなければ `UnityAnimError`。

## 名前の対応（ARKit などの別の名前 / R-26）
`remap_names`（名前の表で置き換え）と `build_name_mapping`（接頭辞・大文字小文字だけが違う名前を自動で対応づける）。
照合できなかった名前は戻り値の `unmatched` に出す。
"""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Optional, Sequence, Union

Quat = tuple[float, float, float, float]
Vec3 = tuple[float, float, float]

BLENDSHAPE_PREFIX = "blendShape."
WEIGHT_EPS = 1e-6  # |重み| がこれ未満の名前は出さない
UNITY_WEIGHT_SCALE = 100.0  # Unity のブレンドシェイプは 0〜100

# m_RotationOrder（Unity の RotationOrder）。文字は「先に回す軸 → 後に回す軸」の順
ROTATION_ORDERS = {0: "XYZ", 1: "XZY", 2: "YZX", 3: "YXZ", 4: "ZXY", 5: "ZYX"}

KIND_ROTATION = "rotation"  # クォータニオン（x y z w）
KIND_EULER = "euler"  # Euler 角 [度]（x y z）
KIND_POSITION = "position"
KIND_SCALE = "scale"
_SECTIONS = {
    "m_FloatCurves": None,
    "m_RotationCurves": KIND_ROTATION,
    "m_EulerCurves": KIND_EULER,
    "m_PositionCurves": KIND_POSITION,
    "m_ScaleCurves": KIND_SCALE,
}


class UnityAnimError(ValueError):
    """`.anim` として読めない。"""


# ---------------------------------------------------------------------------
# データ
# ---------------------------------------------------------------------------


@dataclass
class Key:
    time: float
    value: tuple[float, ...]  # 1 要素（float カーブ）/ 3 要素（位置・Euler・スケール）/ 4 要素（クォータニオン）
    in_slope: Optional[tuple[float, ...]] = None
    out_slope: Optional[tuple[float, ...]] = None

    @property
    def stepped(self) -> bool:
        """outSlope が無限大（階段状のキー）。"""
        return self.out_slope is not None and any(math.isinf(s) for s in self.out_slope)


@dataclass
class FloatCurve:
    path: str
    attribute: str
    keys: list[Key] = field(default_factory=list)


@dataclass
class TransformCurve:
    path: str
    kind: str  # KIND_*
    keys: list[Key] = field(default_factory=list)
    rotation_order: int = 4  # Euler のときの回転順（Unity の既定 4 = ZXY）


@dataclass
class AnimClip:
    name: str = ""
    sample_rate: float = 60.0
    float_curves: dict[tuple[str, str], FloatCurve] = field(default_factory=dict)  # (path, attribute) → カーブ
    transform_curves: dict[tuple[str, str], TransformCurve] = field(default_factory=dict)  # (path, kind) → カーブ
    warnings: list[str] = field(default_factory=list)

    @property
    def length(self) -> float:
        """最後のキーの時刻 [秒]（カーブが無ければ 0）。"""
        ends = [c.keys[-1].time for c in self.float_curves.values() if c.keys]
        ends += [c.keys[-1].time for c in self.transform_curves.values() if c.keys]
        return max(ends) if ends else 0.0

    @property
    def is_empty(self) -> bool:
        return not self.float_curves and not self.transform_curves

    def blendshape_names(self, mesh_path: Optional[str] = None) -> list[str]:
        """ブレンドシェイプのカーブの名前（`bs.eye_close_L` の形）。"""
        out: list[str] = []
        for (path, attr) in self.float_curves:
            name = _blendshape_name(attr)
            if name is not None and _path_matches(path, mesh_path) and name not in out:
                out.append(name)
        return out

    def mesh_paths(self) -> list[str]:
        """ブレンドシェイプのカーブを持つメッシュのパス。"""
        out: list[str] = []
        for (path, attr) in self.float_curves:
            if _blendshape_name(attr) is not None and path not in out:
                out.append(path)
        return out


@dataclass
class ClipSample:
    """ある時刻の値。float: (path, attribute) → 値（Unity の値のまま）、transforms: (path, kind) → 値の組。"""

    time: float = 0.0
    floats: dict[tuple[str, str], float] = field(default_factory=dict)
    transforms: dict[tuple[str, str], tuple[float, ...]] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 小さな YAML パーサー
# ---------------------------------------------------------------------------

_NUMBER = re.compile(r"^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$")


def _scalar(text: str) -> Any:
    t = text.strip()
    if not t:
        return None
    if (t[0] == t[-1] and t[0] in "'\"") and len(t) >= 2:
        return t[1:-1]
    if _NUMBER.match(t):
        try:
            return int(t) if re.fullmatch(r"[-+]?\d+", t) else float(t)
        except ValueError:
            return t
    low = t.lower()
    if low in ("infinity", "+infinity", "inf", "+inf", ".inf"):
        return math.inf
    if low in ("-infinity", "-inf", "-.inf"):
        return -math.inf
    if low in ("nan", ".nan"):
        return math.nan
    if t in ("true", "True"):
        return True
    if t in ("false", "False"):
        return False
    if t in ("null", "~"):
        return None
    return t


def _split_flow(text: str) -> list[str]:
    """`a: 1, b: {x: 2, y: 3}` → ["a: 1", "b: {x: 2, y: 3}"]（括弧の外のカンマで分ける）。"""
    parts: list[str] = []
    depth = 0
    cur: list[str] = []
    for ch in text:
        if ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    tail = "".join(cur)
    if tail.strip():
        parts.append(tail)
    return parts


def _flow(text: str) -> Any:
    """1 行の値（`{x: 0, y: 1}` / `[1, 2]` / `[]` / スカラー）。"""
    t = text.strip()
    if t.startswith("{") and t.endswith("}"):
        out: dict[str, Any] = {}
        for part in _split_flow(t[1:-1]):
            k, sep, v = part.partition(":")
            if not sep:
                raise UnityAnimError(f"1 行のマップを読めません: {text!r}")
            out[k.strip()] = _flow(v)
        return out
    if t.startswith("[") and t.endswith("]"):
        return [_flow(p) for p in _split_flow(t[1:-1])]
    return _scalar(t)


def _split_key(content: str) -> Optional[tuple[str, str]]:
    """`key: value` / `key:` → (key, value)。マップの行でなければ None（括弧・引用で始まる行は値）。"""
    if content[0] in "{[\"'":
        return None
    i = 0
    n = len(content)
    while i < n:
        if content[i] == ":" and (i + 1 == n or content[i + 1] in " \t"):
            return content[:i].strip(), content[i + 1 :].strip()
        i += 1
    return None


class _Lines:
    """(インデント, 内容) の列。ブロックのマップ・列を再帰で読む。"""

    def __init__(self, raw: Iterable[str]) -> None:
        self.items: list[list] = []
        for line in raw:
            body = line.rstrip()
            if not body.strip() or body.lstrip().startswith("#"):
                continue
            expanded = body.expandtabs(2)
            content = expanded.lstrip(" ")
            self.items.append([len(expanded) - len(content), content])

    def __len__(self) -> int:
        return len(self.items)

    def parse(self, i: int, indent: int) -> tuple[Any, int]:
        """i 行目から、桁 indent のブロックを 1 つ読む。(値, 次の行) を返す。"""
        content = self.items[i][1]
        if content == "-" or content.startswith("- "):
            return self._sequence(i, indent)
        if _split_key(content) is None:
            return _flow(content), i + 1
        return self._mapping(i, indent)

    def _child(self, i: int, indent: int, allow_same_seq: bool) -> tuple[Any, int]:
        """キーの値が次の行以降のブロックのとき。i は次の行（無ければ None を返す）。"""
        if i >= len(self.items):
            return None, i
        ind, content = self.items[i]
        if ind > indent:
            return self.parse(i, ind)
        if allow_same_seq and ind == indent and (content == "-" or content.startswith("- ")):
            return self._sequence(i, ind)  # Unity は `m_FloatCurves:` と同じ桁で `- curve:` を並べる
        return None, i

    def _mapping(self, i: int, indent: int) -> tuple[dict[str, Any], int]:
        out: dict[str, Any] = {}
        n = len(self.items)
        while i < n:
            ind, content = self.items[i]
            if ind != indent or content == "-" or content.startswith("- "):
                break
            kv = _split_key(content)
            if kv is None:
                raise UnityAnimError(f"マップの行を読めません: {content!r}")
            key, rest = kv
            if rest:
                out[key] = _flow(rest)
                i += 1
            else:
                out[key], i = self._child(i + 1, indent, allow_same_seq=True)
        return out, i

    def _sequence(self, i: int, indent: int) -> tuple[list[Any], int]:
        out: list[Any] = []
        n = len(self.items)
        while i < n:
            ind, content = self.items[i]
            if ind != indent or not (content == "-" or content.startswith("- ")):
                break
            rest = content[1:].lstrip(" ")
            if not rest:
                item, i = self._child(i + 1, indent, allow_same_seq=False)
                out.append(item)
                continue
            col = indent + (len(content) - len(rest))
            self.items[i] = [col, rest]  # `- key: v` は、桁 col から始まるマップの 1 行目として読み直す
            item, i = self.parse(i, col)
            out.append(item)
        return out, i


# ---------------------------------------------------------------------------
# .anim の読み込み
# ---------------------------------------------------------------------------


def _documents(text: str) -> list[list[str]]:
    docs: list[list[str]] = []
    cur: Optional[list[str]] = None
    for line in text.splitlines():
        if line.startswith("%"):  # %YAML / %TAG
            continue
        if line.startswith("---"):
            cur = []
            docs.append(cur)
            continue
        if cur is None:  # `---` の無い単独の文書
            cur = []
            docs.append(cur)
        cur.append(line)
    return docs


def _clip_lines(text: str) -> list[str]:
    for doc in _documents(text):
        for k, line in enumerate(doc):
            if line.strip() == "AnimationClip:":
                return doc[k + 1 :]
    raise UnityAnimError("AnimationClip が見つかりません（Unity の .anim ではない / テキスト形式ではない）")


def _sections(lines: list[str]) -> tuple[dict[str, list[str]], dict[str, str]]:
    """AnimationClip の直下のキー → (その中身の行, 同じ行に書かれた値)。"""
    blocks: dict[str, list[str]] = {}
    inline: dict[str, str] = {}
    expanded = [ln.expandtabs(2) for ln in lines if ln.strip() and not ln.lstrip().startswith("#")]
    if not expanded:
        return blocks, inline
    base = len(expanded[0]) - len(expanded[0].lstrip(" "))
    cur: Optional[list[str]] = None
    for e in expanded:
        content = e.lstrip(" ")
        ind = len(e) - len(content)
        if ind < base:
            break  # AnimationClip の外（次の文書の取り残し）
        if ind == base and not (content == "-" or content.startswith("- ")):
            kv = _split_key(content)
            if kv is None:
                cur = None
                continue
            key, rest = kv
            cur = blocks.setdefault(key, [])
            if rest:
                inline[key] = rest
        elif cur is not None:
            cur.append(e)
    return blocks, inline


def _vec(v: Any, names: str) -> Optional[tuple[float, ...]]:
    if isinstance(v, dict):
        try:
            return tuple(float(v[c]) for c in names)
        except (KeyError, TypeError, ValueError):
            return None
    if isinstance(v, (int, float)) and len(names) == 1:
        return (float(v),)
    return None


def _keys(curve_node: Any, names: str) -> list[Key]:
    keys_raw = []
    if isinstance(curve_node, dict):
        keys_raw = curve_node.get("m_Curve") or []
    out: list[Key] = []
    for k in keys_raw:
        if not isinstance(k, dict) or "time" not in k:
            continue
        val = _vec(k.get("value"), names)
        if val is None:
            continue
        out.append(
            Key(
                float(k["time"]),
                val,
                _vec(k.get("inSlope"), names),
                _vec(k.get("outSlope"), names),
            )
        )
    out.sort(key=lambda key: key.time)
    return out


def _parse_section(lines: list[str]) -> list[Any]:
    ls = _Lines(lines)
    if not len(ls):
        return []
    node, _ = ls.parse(0, ls.items[0][0])
    return node if isinstance(node, list) else []


def loads(text: str, name: str = "") -> AnimClip:
    """`.anim` のテキスト → AnimClip。"""
    blocks, inline = _sections(_clip_lines(text))
    clip = AnimClip(name=name)
    if inline.get("m_Name"):
        clip.name = str(_scalar(inline["m_Name"]) or name)
    sr = _scalar(inline["m_SampleRate"]) if "m_SampleRate" in inline else None
    if isinstance(sr, (int, float)) and sr > 0:
        clip.sample_rate = float(sr)
    if blocks.get("m_CompressedRotationCurves"):
        clip.warnings.append("m_CompressedRotationCurves（圧縮された回転）は読みません")
    for sec, kind in _SECTIONS.items():
        lines = blocks.get(sec)
        if not lines:  # 無い / `[]`
            continue
        try:
            entries = _parse_section(lines)
        except UnityAnimError as exc:
            raise UnityAnimError(f"{sec} を読めません: {exc}") from exc
        for e in entries:
            if not isinstance(e, dict):
                continue
            path = str(e.get("path") or "")
            cn = e.get("curve")
            if kind is None:
                attr = str(e.get("attribute") or "")
                clip.float_curves[(path, attr)] = FloatCurve(path, attr, _keys(cn, "v"))
            else:
                order = int(cn["m_RotationOrder"]) if isinstance(cn, dict) and isinstance(cn.get("m_RotationOrder"), int) else 4
                clip.transform_curves[(path, kind)] = TransformCurve(
                    path, kind, _keys(cn, "xyzw" if kind == KIND_ROTATION else "xyz"), order
                )
    return clip


def load(path: Union[str, os.PathLike]) -> AnimClip:
    """`.anim` ファイルを読む（UTF-8。BOM 付きも可）。名前が無ければファイル名の語幹。"""
    p = os.fspath(path)
    with open(p, "r", encoding="utf-8-sig") as f:
        text = f.read()
    stem = os.path.splitext(os.path.basename(p))[0]
    return loads(text, name=stem)


# ---------------------------------------------------------------------------
# 評価
# ---------------------------------------------------------------------------


def _hermite(a: Key, b: Key, t: float, i: int) -> float:
    dt = b.time - a.time
    p0, p1 = a.value[i], b.value[i]
    m0 = (a.out_slope[i] if a.out_slope else 0.0) * dt
    m1 = (b.in_slope[i] if b.in_slope else 0.0) * dt
    if math.isinf(m0) or math.isinf(m1) or math.isnan(m0) or math.isnan(m1):
        return p0
    t2, t3 = t * t, t * t * t
    return (2 * t3 - 3 * t2 + 1) * p0 + (t3 - 2 * t2 + t) * m0 + (-2 * t3 + 3 * t2) * p1 + (t3 - t2) * m1


def _eval_keys(keys: Sequence[Key], time: float, hermite: bool) -> Optional[tuple[float, ...]]:
    if not keys:
        return None
    if time <= keys[0].time:
        return keys[0].value
    if time >= keys[-1].time:
        return keys[-1].value
    lo, hi = 0, len(keys) - 1
    while hi - lo > 1:  # 二分探索
        mid = (lo + hi) // 2
        if keys[mid].time <= time:
            lo = mid
        else:
            hi = mid
    a, b = keys[lo], keys[hi]
    if a.stepped:
        return a.value
    span = b.time - a.time
    if span <= 0:
        return b.value
    t = (time - a.time) / span
    if hermite and a.out_slope is not None and b.in_slope is not None:
        return tuple(_hermite(a, b, t, i) for i in range(len(a.value)))
    return tuple(x + (y - x) * t for x, y in zip(a.value, b.value))


def evaluate(clip: AnimClip, time: float, hermite: bool = False) -> ClipSample:
    """時刻 `time`（秒）の全カーブの値。既定は線形補間（接線は見ない。`hermite=True` で Unity と同じ 3 次エルミート）。
    クォータニオンは補間後に正規化する。キーの範囲の外は端の値。"""
    out = ClipSample(time=float(time))
    for key, c in clip.float_curves.items():
        v = _eval_keys(c.keys, time, hermite)
        if v is not None:
            out.floats[key] = v[0]
    for key, c in clip.transform_curves.items():
        v = _eval_keys(c.keys, time, hermite)
        if v is None:
            continue
        if c.kind == KIND_ROTATION:
            v = _quat_normalize(v)
        out.transforms[key] = v
    return out


# ---------------------------------------------------------------------------
# 重み・ボーン
# ---------------------------------------------------------------------------


def _blendshape_name(attribute: str) -> Optional[str]:
    if attribute.startswith(BLENDSHAPE_PREFIX) and len(attribute) > len(BLENDSHAPE_PREFIX):
        return attribute[len(BLENDSHAPE_PREFIX) :]
    return None


def _path_matches(path: str, mesh_path: Optional[str]) -> bool:
    if mesh_path is None:
        return True
    mp = mesh_path.strip("/")
    p = path.strip("/")
    return p == mp or p.rsplit("/", 1)[-1] == mp or p.endswith("/" + mp)


def to_curve_weights(
    clip: AnimClip, time: float = 0.0, mesh_path: Optional[str] = None, hermite: bool = False
) -> dict[str, float]:
    """時刻 `time` のブレンドシェイプの重み {シェイプ名: 0〜1}。`blendShape.bs.eye_close_L` → `bs.eye_close_L`、
    Unity の値 0〜100 → 0〜1（範囲の丸めはしない。可動域は読み込み側が丸める）。|重み| < 1e-6 の名前は出さない。

    mesh_path: カーブの `path` の絞り込み（完全一致 / 末尾の名前 / 末尾のパスが一致）。省くと全メッシュ。
    同じ名前が複数のメッシュにあって省いたときは、0 でない値を持つ最初のものを採る。"""
    sample = evaluate(clip, time, hermite=hermite)
    out: dict[str, float] = {}
    seen: set[str] = set()
    for (path, attr), v in sample.floats.items():
        name = _blendshape_name(attr)
        if name is None or not _path_matches(path, mesh_path):
            continue
        w = v / UNITY_WEIGHT_SCALE
        if name in seen and abs(out.get(name, 0.0)) >= WEIGHT_EPS:
            continue  # 先に見つかった 0 でない値を残す
        seen.add(name)
        if abs(w) >= WEIGHT_EPS:
            out[name] = w
        else:
            out.pop(name, None)
    return out


def _quat_normalize(q: Sequence[float]) -> Quat:
    n = math.sqrt(sum(c * c for c in q))
    if n < 1e-12:
        return (0.0, 0.0, 0.0, 1.0)
    return (q[0] / n, q[1] / n, q[2] / n, q[3] / n)


def _quat_mul(a: Sequence[float], b: Sequence[float]) -> Quat:
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    )


def euler_to_quat(euler_deg: Sequence[float], order: int = 4) -> Quat:
    """Unity の Euler 角 [度] → クォータニオン [x, y, z, w]。order = m_RotationOrder（既定 4 = ZXY。`Quaternion.Euler` と同じ）。
    順は「先に回す軸 → 後に回す軸」（ZXY = Z を最初、次に X、最後に Y。q = qy · qx · qz）。"""
    half = [math.radians(a) * 0.5 for a in euler_deg[:3]]
    axes = {
        "X": (math.sin(half[0]), 0.0, 0.0, math.cos(half[0])),
        "Y": (0.0, math.sin(half[1]), 0.0, math.cos(half[1])),
        "Z": (0.0, 0.0, math.sin(half[2]), math.cos(half[2])),
    }
    q: Quat = (0.0, 0.0, 0.0, 1.0)
    for ax in ROTATION_ORDERS.get(order, "ZXY"):
        q = _quat_mul(axes[ax], q)
    return _quat_normalize(q)


BoneValue = tuple[Optional[Vec3], Optional[Quat], Optional[Vec3]]  # (位置, 回転, スケール)。カーブが無い要素は None


def leaf_name(path: str) -> str:
    return path.strip("/").rsplit("/", 1)[-1]


def bone_values_by_path(clip: AnimClip, time: float = 0.0, hermite: bool = False) -> dict[str, BoneValue]:
    """時刻 `time` のボーンのローカルの絶対値（Unity の系・m）。キー = カーブの path（階層パス）。
    回転はクォータニオン（`m_RotationCurves`）を優先し、無ければ Euler（`m_EulerCurves`）から作る。"""
    sample = evaluate(clip, time, hermite=hermite)
    paths: dict[str, None] = {}
    for (path, _kind) in sample.transforms:
        paths.setdefault(path, None)
    out: dict[str, BoneValue] = {}
    for path in paths:
        pos = sample.transforms.get((path, KIND_POSITION))
        scl = sample.transforms.get((path, KIND_SCALE))
        rot: Optional[Quat] = None
        q = sample.transforms.get((path, KIND_ROTATION))
        if q is not None:
            rot = _quat_normalize(q)
        else:
            e = sample.transforms.get((path, KIND_EULER))
            if e is not None:
                order = clip.transform_curves[(path, KIND_EULER)].rotation_order
                rot = euler_to_quat(e, order)
        out[path] = (
            (pos[0], pos[1], pos[2]) if pos is not None else None,
            rot,
            (scl[0], scl[1], scl[2]) if scl is not None else None,
        )
    return out


def bone_values(clip: AnimClip, time: float = 0.0, hermite: bool = False) -> dict[str, BoneValue]:
    """`bone_values_by_path` のキーを、パスの末尾の名前（ボーン名）にしたもの。同じ名前が複数あれば後のものが残る。"""
    return {leaf_name(p): v for p, v in bone_values_by_path(clip, time, hermite).items()}


# ---------------------------------------------------------------------------
# 名前の対応（R-26。ARKit など、シェイプの名前が違う場合）
# ---------------------------------------------------------------------------


@dataclass
class NameMapping:
    """`build_name_mapping` の結果。mapping: 元の名前 → 先の名前。unmatched: 対応づけられなかった元の名前。ambiguous: 候補が複数あった元の名前。"""

    mapping: dict[str, str] = field(default_factory=dict)
    unmatched: list[str] = field(default_factory=list)
    ambiguous: dict[str, list[str]] = field(default_factory=dict)


def remap_names(
    weights: Mapping[str, float], mapping: Mapping[str, str], keep_unmapped: bool = True
) -> tuple[dict[str, float], list[str]]:
    """重みの名前を表で置き換える。(置き換えた重み, 表に無かった名前) を返す。
    keep_unmapped=True なら表に無い名前はそのまま残す（後でシーンと照合して未対応を報告する）。False なら捨てる。
    2 つの名前が同じ先に当たったときは、大きいほうの重みを採る。"""
    out: dict[str, float] = {}
    missing: list[str] = []
    for name, w in weights.items():
        dst = mapping.get(name)
        if dst is None:
            missing.append(name)
            if not keep_unmapped:
                continue
            dst = name
        if dst not in out or abs(w) > abs(out[dst]):
            out[dst] = w
    return out, missing


def _strip_prefix(name: str, prefixes: Sequence[str], fold: bool = False) -> str:
    for p in prefixes:
        if not p:
            continue
        if (name.lower().startswith(p.lower())) if fold else name.startswith(p):
            return name[len(p) :]
    return name


def build_name_mapping(
    source_names: Iterable[str],
    target_names: Iterable[str],
    *,
    strip_prefixes: Sequence[str] = ("bs.",),
    case_insensitive: bool = True,
) -> NameMapping:
    """元の名前の集まり（アニメのカーブ名）を、先の名前の集まり（シーンのシェイプ名）へ対応づける。
    完全一致 → 接頭辞（`bs.` など。strip_prefixes）を除いた完全一致 → 大文字小文字を無視した一致、の順に探す。
    候補が複数になる名前は ambiguous に入れて対応づけない。見つからない名前は unmatched。"""
    targets = list(dict.fromkeys(target_names))
    target_set = set(targets)

    def keyed(name: str, fold: bool, strip: bool) -> str:
        n = _strip_prefix(name, strip_prefixes, fold) if strip else name
        return n.lower() if fold else n

    stages = [(False, False), (False, True)] + ([(True, True)] if case_insensitive else [])
    index: dict[tuple[bool, bool], dict[str, list[str]]] = {}
    for fold, strip in stages:
        d: dict[str, list[str]] = {}
        for t in targets:
            d.setdefault(keyed(t, fold, strip), []).append(t)
        index[(fold, strip)] = d

    res = NameMapping()
    for src in dict.fromkeys(source_names):
        if src in target_set:
            res.mapping[src] = src
            continue
        found: list[str] = []
        for fold, strip in stages[1:]:
            found = index[(fold, strip)].get(keyed(src, fold, strip), [])
            if found:
                break
        if len(found) == 1:
            res.mapping[src] = found[0]
        elif len(found) > 1:
            res.ambiguous[src] = list(found)
        else:
            res.unmatched.append(src)
    return res


def build_mapping_from_profile(
    source_names: Iterable[str], profile: Any, target_names: Iterable[str]
) -> NameMapping:
    """命名規則プロファイル（`core.profile.NamingProfile`）の標準シェイプ名を手がかりに、元の名前を先の名前へ対応づける。
    元の名前が profile.standard_curves のどれかと（大文字小文字を除いて）一致するなら、その標準名で先の名前を探す。
    標準名と先の名前が接頭辞・大文字小文字だけの違いなら対応する。それ以外は `build_name_mapping` と同じ。
    名前の意味の対応（ARKit の jawOpen → mouth_open など）は自動では作らない（`remap_names` の表で渡す）。"""
    std = {s.lower(): s for s in getattr(profile, "standard_curves", []) or []}
    canon = {}
    srcs = list(dict.fromkeys(source_names))
    for s in srcs:
        key = _strip_prefix(s, ("bs.",), True).lower()
        canon[s] = std.get(key, s)
    inner = build_name_mapping(list(dict.fromkeys(canon.values())), target_names)
    res = NameMapping()
    for s in srcs:
        c = canon[s]
        if c in inner.mapping:
            res.mapping[s] = inner.mapping[c]
        elif c in inner.ambiguous:
            res.ambiguous[s] = inner.ambiguous[c]
        else:
            res.unmatched.append(s)
    return res
