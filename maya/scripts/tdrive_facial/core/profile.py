"""命名規則プロファイル `.fcprofile.json`（Maya 非依存）。形式は docs/14 §4.4、UE 版の `UFacialNamingProfile` に合わせる。

- 持つもの: 標準シェイプ名の一覧（不足チェック用）、ミラー規則（L/R の接尾辞と除外パターン）、シェイプごとの可動域
- 読み込み: 知らないキーは `extra` に保持して書き戻す。欠けたキーは UE 版の既定値（接尾辞 `_L` / `_R`、一覧・可動域は空）。
  `version` が新しいときは ProfileVersionWarning を出して読める所だけ読む
- 書き出し: キーの順は固定（format → version → name → description → standardCurves → mirror → limits → 知らないキー）。
  UTF-8（BOM なし）・改行 `\\n`・末尾に改行 1 つ。数値は整数と等しい浮動小数を整数で書く
- プリセット（ツールに同梱。`tdrive_facial/profiles/*.fcprofile.json`）: ARKit 52、VRChat ビセム、MetaHuman、shizuku
- プロファイルを選ぶと mirror と limits が Document に入る（`apply_to_document`）。UE 版では手で写す必要があった（R-15 の未実装部分）
- 名前の照合は大文字小文字を区別する完全一致。ミラー除外パターンは部分一致（UE 版 `IsMirrorExcluded` と同じ）
"""

from __future__ import annotations

import copy
import json
import math
import os
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional, Union

from .model import Document, SourcePose

FORMAT_PROFILE = "FacialNamingProfile"
SUPPORTED_VERSION = 1
PROFILE_EXTENSION = ".fcprofile.json"

# 可動域が決まっていないシェイプの範囲（R-16）。誇張（最大 2。R-37）はプロファイル / ドキュメントの limits で明示する
DEFAULT_LIMIT: tuple[float, float] = (0.0, 1.0)

Limit = tuple[float, float]


class ProfileError(ValueError):
    """読めない（JSON が壊れている・format が違う）。"""


class ProfileVersionWarning(UserWarning):
    """version がこの実装より新しい。読める所だけ読んだ。"""


@dataclass
class MirrorRule:
    """ミラー規則。Document.mirror の suffix_l / suffix_r / exclude と同じ意味。"""

    suffix_l: str = "_L"
    suffix_r: str = "_R"
    exclude: list[str] = field(default_factory=list)  # 部分一致。一致した名前は鏡映の対象外
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class NamingProfile:
    """`"format": "FacialNamingProfile"`。name は Document.profile に入る識別子（ファイル名の語幹と同じにする）。"""

    version: int = SUPPORTED_VERSION
    name: str = ""
    description: str = ""
    standard_curves: list[str] = field(default_factory=list)
    mirror: MirrorRule = field(default_factory=MirrorRule)
    limits: dict[str, Limit] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)  # 知らないキー（トップレベル）

    @property
    def format(self) -> str:
        return FORMAT_PROFILE


# ---------------------------------------------------------------------------
# 読み込み
# ---------------------------------------------------------------------------


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _strs(v: Any) -> list[str]:
    return [x for x in v if isinstance(x, str)] if isinstance(v, list) else []


def _extra(d: dict, known: tuple[str, ...]) -> dict[str, Any]:
    return {k: copy.deepcopy(v) for k, v in d.items() if k not in known}


_PROFILE_KEYS = ("format", "version", "name", "description", "standardCurves", "mirror", "limits")
_MIRROR_KEYS = ("suffixL", "suffixR", "exclude")


def from_dict(d: Any) -> NamingProfile:
    """JSON の dict → NamingProfile。format が違う・dict でないときは ProfileError。"""
    if not isinstance(d, dict):
        raise ProfileError("JSON のトップレベルがオブジェクトではありません")
    if d.get("format") != FORMAT_PROFILE:
        raise ProfileError(f'"format" が "{FORMAT_PROFILE}" ではありません: {d.get("format")!r}')
    version = d.get("version", SUPPORTED_VERSION)
    version = int(version) if _is_num(version) else SUPPORTED_VERSION
    if version > SUPPORTED_VERSION:
        warnings.warn(
            f"プロファイルの version {version} はこの実装（{SUPPORTED_VERSION}）より新しい。読める所だけ読みます",
            ProfileVersionWarning,
            stacklevel=2,
        )
    m = d.get("mirror")
    m = m if isinstance(m, dict) else {}
    default = MirrorRule()
    mirror = MirrorRule(
        suffix_l=m["suffixL"] if isinstance(m.get("suffixL"), str) else default.suffix_l,
        suffix_r=m["suffixR"] if isinstance(m.get("suffixR"), str) else default.suffix_r,
        exclude=_strs(m.get("exclude")),
        extra=_extra(m, _MIRROR_KEYS),
    )
    limits: dict[str, Limit] = {}
    raw = d.get("limits")
    if isinstance(raw, dict):
        for k, v in raw.items():
            if isinstance(v, list) and len(v) >= 2 and _is_num(v[0]) and _is_num(v[1]):
                limits[k] = (float(v[0]), float(v[1]))
    return NamingProfile(
        version=version,
        name=d["name"] if isinstance(d.get("name"), str) else "",
        description=d["description"] if isinstance(d.get("description"), str) else "",
        standard_curves=_strs(d.get("standardCurves")),
        mirror=mirror,
        limits=limits,
        extra=_extra(d, _PROFILE_KEYS),
    )


def loads(text: str) -> NamingProfile:
    try:
        return from_dict(json.loads(text.lstrip("﻿")))
    except json.JSONDecodeError as e:
        raise ProfileError(f"JSON として読めません: {e}") from e


def load(path: Union[str, os.PathLike]) -> NamingProfile:
    return loads(Path(path).read_text(encoding="utf-8-sig"))


# ---------------------------------------------------------------------------
# 書き出し（決まった順・決まった表記）
# ---------------------------------------------------------------------------


def to_dict(profile: NamingProfile) -> dict[str, Any]:
    out: dict[str, Any] = {
        "format": FORMAT_PROFILE,
        "version": profile.version,
        "name": profile.name,
        "description": profile.description,
        "standardCurves": list(profile.standard_curves),
        "mirror": {
            "suffixL": profile.mirror.suffix_l,
            "suffixR": profile.mirror.suffix_r,
            "exclude": list(profile.mirror.exclude),
            **copy.deepcopy(profile.mirror.extra),
        },
        "limits": {k: [v[0], v[1]] for k, v in profile.limits.items()},
    }
    for k, v in profile.extra.items():
        out[k] = copy.deepcopy(v)
    return out


def _fmt_number(v: Union[int, float]) -> str:
    if isinstance(v, int):
        return str(v)
    if not math.isfinite(v):
        raise ValueError(f"NaN / Inf は書けません: {v}")
    return str(int(v)) if v == int(v) and abs(v) < 1e15 else repr(v)


def _scalar(v: Any) -> str:
    if isinstance(v, bool) or v is None or isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    return _fmt_number(v)


def _dump(v: Any, level: int, out: list[str]) -> None:
    pad = "  " * level
    if isinstance(v, dict):
        if not v:
            out.append("{}")
            return
        out.append("{\n")
        items = list(v.items())
        for i, (k, x) in enumerate(items):
            out.append(f"{pad}  {json.dumps(k, ensure_ascii=False)}: ")
            _dump(x, level + 1, out)
            out.append(",\n" if i < len(items) - 1 else "\n")
        out.append(f"{pad}}}")
    elif isinstance(v, (list, tuple)):
        if not v:
            out.append("[]")
        elif all(_is_num(x) for x in v):
            out.append("[" + ", ".join(_scalar(x) for x in v) + "]")  # 数値だけの配列は 1 行
        else:
            out.append("[\n")
            for i, x in enumerate(v):
                out.append(f"{pad}  ")
                _dump(x, level + 1, out)
                out.append(",\n" if i < len(v) - 1 else "\n")
            out.append(f"{pad}]")
    else:
        out.append(_scalar(v))


def dumps(profile: NamingProfile) -> str:
    out: list[str] = []
    _dump(to_dict(profile), 0, out)
    return "".join(out) + "\n"


def save(profile: NamingProfile, path: Union[str, os.PathLike]) -> None:
    """UTF-8（BOM なし）・改行 `\\n` で書く。親フォルダは作る。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(dumps(profile))


# ---------------------------------------------------------------------------
# 同梱プリセットの探索
# ---------------------------------------------------------------------------


def builtin_profiles_dir() -> Path:
    """同梱プリセットのフォルダ（パッケージからの相対: tdrive_facial/profiles）。"""
    return Path(__file__).resolve().parent.parent / "profiles"


def project_profiles_dir(project_root: Union[str, os.PathLike], character: str) -> Path:
    """プロジェクトのプロファイル置き場 `facial/<character>/profiles/`。"""
    return Path(project_root) / "facial" / character / "profiles"


def _profile_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.iterdir() if p.is_file() and p.name.endswith(PROFILE_EXTENSION))


def builtin_profiles() -> dict[str, NamingProfile]:
    """同梱プリセット全部（name → プロファイル。ファイル名順）。"""
    out: dict[str, NamingProfile] = {}
    for f in _profile_files(builtin_profiles_dir()):
        prof = load(f)
        out[prof.name or f.name[: -len(PROFILE_EXTENSION)]] = prof
    return out


def find_profile(name: str, search_dirs: Iterable[Union[str, os.PathLike]] = ()) -> Optional[NamingProfile]:
    """名前でプロファイルを探す。search_dirs（プロジェクト側）を先に、無ければ同梱プリセット。見つからなければ None。

    各フォルダで `<name>.fcprofile.json` を先に試し、無ければ中の `name` が一致するファイルを探す。
    読めないファイルは飛ばす（フェイルソフト）。
    """
    if not name:
        return None
    dirs = [Path(d) for d in search_dirs] + [builtin_profiles_dir()]
    for d in dirs:
        direct = d / (name + PROFILE_EXTENSION)
        candidates = ([direct] if direct.is_file() else []) + [f for f in _profile_files(d) if f != direct]
        for f in candidates:
            try:
                prof = load(f)
            except (ProfileError, OSError):
                continue
            if prof.name == name or (f == direct and not prof.name):
                return prof
    return None


# ---------------------------------------------------------------------------
# ミラーの名前規則（UE 版 UFacialCorrectionAsset::MirrorCurveName / IsMirrorExcluded と同じ）
# ---------------------------------------------------------------------------


def mirror_name(name: str, suffix_l: str, suffix_r: str) -> str:
    """末尾の L ⇔ R 接尾辞を入れ替えた名前。どちらにも一致しない・接尾辞が空なら元のまま（大文字小文字は区別）。"""
    if not suffix_l or not suffix_r:
        return name
    if name.endswith(suffix_l):
        return name[: len(name) - len(suffix_l)] + suffix_r
    if name.endswith(suffix_r):
        return name[: len(name) - len(suffix_r)] + suffix_l
    return name


def is_mirror_excluded(name: str, patterns: Iterable[str]) -> bool:
    """除外パターン（部分一致。空パターンは無視）のどれかに一致するか。"""
    return any(p and p in name for p in patterns)


# ---------------------------------------------------------------------------
# Document への適用・可動域
# ---------------------------------------------------------------------------


@dataclass
class ApplyResult:
    limits_added: int = 0  # プロファイルから新しく入った可動域
    limits_overwritten: int = 0  # 既にあった値を置き換えた数（overwrite=True のとき）
    limits_kept: int = 0  # ドキュメント側の値が勝って入らなかった数


def apply_to_document(profile: NamingProfile, doc: Document, overwrite: bool = False) -> ApplyResult:
    """プロファイルの mirror と limits を Document へ入れ、doc.profile を設定する（R-15）。

    - mirror: 接尾辞と除外パターンをプロファイルの値にする（プロファイルを選んだ = 規則を取り込む操作。
      `enabled` と `boneAxis` はプロファイルの持ち物ではないので触らない）
    - limits: ドキュメントに既にある値（キャラクター単位の上書き）が勝つ。overwrite=True ならプロファイルの値で置き換える
      （プロファイルに無い、ドキュメント側だけの項目は残す）
    - プロファイルの limits が空で doc.limits が None のときは None のまま（JSON に limits を増やさない）
    """
    result = ApplyResult()
    doc.profile = profile.name
    doc.mirror.suffix_l = profile.mirror.suffix_l
    doc.mirror.suffix_r = profile.mirror.suffix_r
    doc.mirror.exclude = list(profile.mirror.exclude)
    if not profile.limits:
        return result
    if doc.limits is None:
        doc.limits = {}
    for curve, rng in profile.limits.items():
        if curve in doc.limits:
            if overwrite:
                if tuple(doc.limits[curve]) != tuple(rng):
                    result.limits_overwritten += 1
                doc.limits[curve] = (rng[0], rng[1])
            else:
                result.limits_kept += 1
        else:
            doc.limits[curve] = (rng[0], rng[1])
            result.limits_added += 1
    return result


def effective_limit(doc: Optional[Document], profile: Optional[NamingProfile], curve: str) -> Limit:
    """シェイプの可動域。ドキュメントの limits → プロファイルの limits → DEFAULT_LIMIT（0, 1）の順。"""
    if doc is not None and doc.limits and curve in doc.limits:
        lo, hi = doc.limits[curve]
        return (float(lo), float(hi))
    if profile is not None and curve in profile.limits:
        lo, hi = profile.limits[curve]
        return (float(lo), float(hi))
    return DEFAULT_LIMIT


def has_limit(doc: Optional[Document], profile: Optional[NamingProfile], curve: str) -> bool:
    """可動域が明示されているか（False = 既定の 0〜1）。"""
    in_doc = bool(doc is not None and doc.limits and curve in doc.limits)
    in_profile = bool(profile is not None and curve in profile.limits)
    return in_doc or in_profile


def clamp_value(value: float, limit: Limit) -> float:
    lo, hi = limit
    if lo > hi:
        lo, hi = hi, lo
    if value != value:  # NaN は下限へ
        return lo
    return min(max(value, lo), hi)


def clamp_pose(pose: SourcePose, doc: Optional[Document], profile: Optional[NamingProfile]) -> SourcePose:
    """ポーズのシェイプの重みを可動域に収めた新しい SourcePose（R-16。ボーンは複製のみ）。

    可動域が無いシェイプは 0〜1。limits が上限を 1 より大きくしていれば（誇張。最大 2）その値まで許す。
    """
    out = SourcePose()
    for name, w in pose.curves.items():
        out.curves[name] = clamp_value(w, effective_limit(doc, profile, name))
    out.bones = copy.deepcopy(pose.bones)
    return out


def curve_matches(entry: str, available: Iterable[str]) -> bool:
    """プロファイルの標準シェイプ 1 件が、シーンのシェイプ名（`<ノード>.<ターゲット>`）にあるか。

    規則（Setup / 検証 / シェイプタブで共通）: ノード名つきの項目（`bs.jawOpen`）は完全一致、ノード名なしの項目（`jawOpen`）はどのノードの
    同じ名前のターゲットにも一致する（大文字小文字は区別）。`available` にノード名の無い名前（`jawOpen`）が混じっていてもそのターゲットとして数える。
    """
    have = set(available)
    if entry in have:
        return True
    if "." in entry:
        return False
    return any(a.partition(".")[2] == entry for a in have if "." in a)


def missing_standard_curves(profile: NamingProfile, available_names: Iterable[str]) -> list[str]:
    """プロファイルの標準シェイプのうち、モデルに無いもの（一覧の順。大文字小文字を区別）。

    available_names は `<ノード>.<ターゲット>` の名前（`curve_matches` の規則）。"""
    have = set(available_names)
    return [n for n in profile.standard_curves if not curve_matches(n, have)]
