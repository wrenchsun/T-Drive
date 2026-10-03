"""アニメ・他のデータからの読み込み（Maya 依存。docs/14 §5.3 / §5.4、チケット F2-7・F1-7 の残り）。

画面（ui_pose / ui_grid）はここの関数を呼ぶ。ここは「読んで照合する」層（Unity の .anim・今のフレーム → シーンのシェイプ名・ボーンのずれ）で、
**編集中の値・シーン・文書を変える部分は `FacialSession` のコマンド**（2026-10-03 に session.py へ移した）:

- `pose_from_current_frame` / `pose_from_unity_anim` → 読んだ値を `session.import_pose_curves(...)`（編集中の値へ入れて当てる）へ渡す
- `set_base_expression` / `clear_base_expression` / `base_expression_*` → `session.set_base_expression` 等（土台の表情は session が持つ）
- `capture_from_scene` → `session.capture_from_scene`（土台を引く）。ここは `CaptureResult` に包む薄い関数
- `copy_from_file` → `session.copy_from_file`（`@undoable` のコマンド）

## 現在のフレームを読む（`pose_from_current_frame`）
アニメが付いたシーン（ブレンドシェイプの重みにキー、ジョイントにキーが付いている）の今のフレームの状態を、編集中のポーズへ読み込む。
編集状態（基準姿勢）に入る**前**の姿勢を読むので、編集中なら先に編集状態を抜ける（シーンはもとのアニメの状態に戻る。土台の表情も外れる）。
基準姿勢はバインドポーズ（`scene.enter_reference_pose`）。読んだあとシーンは変わらない（時刻も元に戻す）。

## Unity の `.anim` を読む（`pose_from_unity_anim`）
- シェイプ: `blendShape.bs.mouth_open`（0〜100）→ `bs.mouth_open`（0〜1）。シーンのシェイプへ照合し、見つからなければ未対応に出す
  （完全一致 → ノード名を除いた一致 → 大文字小文字を除いた一致）
- ボーン: クリップのローカルの絶対値（Unity の系 = 左手 / m）を `core.space` で Maya の系（cm / 右手）へ直し、ジョイントの基準姿勢の
  ローカル値からの**ずれ**にする（`pose_apply` と同じ意味: 位置 = 足す、向き = `r · 基準`、スケール = 掛ける）。
  ボーンの名前はカーブのパスの末尾（`bone_eye_L`）でシーンのジョイントを探し、無ければスキップして報告する。
  **Unity のボーンのローカル軸が Maya のジョイントの軸と同じ向きの並べ替えで対応する前提**（FBX 経由でそろった骨格）

## 土台の表情（`set_base_expression` / `clear_base_expression`）
「この表情のとき補正がどう見えるか」を確かめるための、**シーンだけに当てる下敷き**。ポーズにも文書にも入らない。
**ポーズの値に足して**当てる（共通のシェイプは 土台 + ポーズ。可動域を超えても丸めず、`BaseExpressionReport.over_limit` で報告）。
詳細は `session.py` の docstring「土台の表情」と `core/base_expr.py`。ここでは .anim / 重みの辞書 → シーンのシェイプ名への照合だけをする。
"""

from __future__ import annotations

import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional, Sequence, Union

from maya import cmds

from tdrive import lifecycle

from . import pose_apply
from . import scene as scene_mod
from . import session as session_mod
from .core import autofill, fcpose_io, space, unity_anim
from .core.model import BoneOffset
from .core.presenters import IngestReport

SourceLike = Union[str, Path, Mapping[str, float], unity_anim.AnimClip]


# ---------------------------------------------------------------------------
# 結果の型
# ---------------------------------------------------------------------------


@dataclass
class AnimImportReport:
    """ポーズへの読み込みの結果。ok=False のとき編集中の値は変わっていない。"""

    ok: bool = True
    code: str = ""
    message: str = ""
    source: str = ""  # クリップの名前 / "今のフレーム"
    time: float = 0.0
    curves_set: int = 0
    bones_set: int = 0
    unmatched_curves: list[str] = field(default_factory=list)  # シーンのシェイプに無い名前
    unmatched_bones: list[str] = field(default_factory=list)  # シーンにジョイントが無いボーン
    ignored: list[str] = field(default_factory=list)  # 作業セットの外で読まなかった名前
    clamped: list[str] = field(default_factory=list)  # 可動域で丸めたシェイプ
    renamed: dict[str, str] = field(default_factory=dict)  # 照合で名前が変わったもの（元 → シーンの名前）
    warnings: list[str] = field(default_factory=list)
    ingest: Optional[IngestReport] = None


@dataclass
class BaseExpressionReport:
    ok: bool = True
    message: str = ""
    name: str = ""
    applied: list[str] = field(default_factory=list)  # シーンに当てたシェイプ
    unmatched: list[str] = field(default_factory=list)
    locked: list[str] = field(default_factory=list)  # 値を書けなかった（つながっている・ロック）
    warnings: list[str] = field(default_factory=list)
    over_limit: dict[str, float] = field(default_factory=dict)  # 土台 + ポーズが可動域を超えたシェイプ → シーンに当てた値


@dataclass
class CaptureResult:
    """土台の表情を除いたシーンからの取り込み。report は IngestReport と同じもの、base_ignored は取り込まなかった土台のシェイプ。"""

    report: IngestReport
    base_ignored: list[str] = field(default_factory=list)
    below_base: list[str] = field(default_factory=list)  # シーンの値が土台より低く、ポーズに入れられなかったシェイプ


@dataclass
class CopyFileResult:
    ok: bool = True
    message: str = ""
    source: str = ""
    flags: list[str] = field(default_factory=list)
    report: Optional[autofill.CopyReport] = None


def _fail(message: str, code: str = "error", **kw) -> AnimImportReport:
    return AnimImportReport(ok=False, code=code, message=message, **kw)


# ---------------------------------------------------------------------------
# 名前の照合
# ---------------------------------------------------------------------------


def _scene_curve_names(meshes: Sequence[str]) -> list[str]:
    out: list[str] = []
    for m in meshes:
        for n in scene_mod.curve_names(m):
            if n not in out:
                out.append(n)
    return out


def resolve_curve_weights(
    weights: Mapping[str, float],
    meshes: Sequence[str],
    name_mapping: Optional[Mapping[str, str]] = None,
) -> tuple[dict[str, float], list[str], dict[str, str]]:
    """読んだ重み（`bs.xxx` の形）をシーンのシェイプ名へ照合する。(照合できた重み, 未対応の名前, 名前が変わったもの) を返す。

    name_mapping があれば先に置き換える（ARKit の名前の表など。R-26）。照合は 完全一致 → ノード名を除いた一致 → 大文字小文字を除いた一致。
    """
    out: dict[str, float] = {}
    unmatched: list[str] = []
    renamed: dict[str, str] = {}
    mapping = dict(name_mapping or {})
    scene_names: Optional[list[str]] = None
    for src, w in weights.items():
        name = mapping.get(src, src)
        hit: Optional[str] = None
        for m in meshes:
            ref = scene_mod.resolve_curve(name, m)
            if ref is not None:
                hit = ref.name
                break
        if hit is None:
            node, target = scene_mod.parse_curve_name(name)
            if node is not None:  # ノード名が違う（`bs.` ではなく `blendShape1.`）
                for m in meshes:
                    ref = scene_mod.resolve_curve(target, m)
                    if ref is not None:
                        hit = ref.name
                        break
        if hit is None:
            if scene_names is None:
                scene_names = _scene_curve_names(meshes)
            m2 = unity_anim.build_name_mapping([name], scene_names, strip_prefixes=("bs.",))
            hit = m2.mapping.get(name)
        if hit is None:
            unmatched.append(src)
            continue
        if hit != src:
            renamed[src] = hit
        if hit not in out or abs(w) > abs(out[hit]):
            out[hit] = w
    return out, unmatched, renamed


def _is_identity(off: BoneOffset) -> bool:
    return (
        max(abs(v) for v in off.t) < pose_apply.THRESHOLD_T
        and max(abs(off.r[0]), abs(off.r[1]), abs(off.r[2])) <= pose_apply.THRESHOLD_R
        and max(abs(v - 1.0) for v in off.s) <= pose_apply.THRESHOLD_S
    )


def _offset_from_local(base: scene_mod.BoneRef, t, q, s) -> BoneOffset:
    """Maya の系のローカル値（位置 cm・向き・スケール）→ 基準からのずれ（`pose_apply.capture_pose` と同じ分解）。"""
    dt = tuple(float(c - b) for c, b in zip(t, base.t))
    dq = scene_mod.quat_normalize(scene_mod.quat_mul(q, scene_mod.quat_inv(base.q)))
    if dq[3] < 0:
        dq = (-dq[0], -dq[1], -dq[2], -dq[3])
    ds = tuple(float(c / b) if abs(b) > 1e-12 else 1.0 for c, b in zip(s, base.s))
    return BoneOffset(t=dt, r=tuple(float(v) for v in dq), s=ds)


def _doc_bone_names(doc) -> list[str]:
    """文書が使っているボーンの名前（骨格に無くても基準姿勢に入れて探す）。"""
    names: dict[str, None] = {}
    if doc.grid.base_bone:
        names.setdefault(doc.grid.base_bone, None)
    for layer in doc.layers:
        for pt in layer.points.values():
            for b in pt.pose.bones:
                names.setdefault(b, None)
    return list(names)


class _Reference:
    """基準姿勢の記録。編集中ならセッションのもの、そうでなければ一時的に入って終わりに戻す（`with` で使う）。"""

    def __init__(self, session, meshes: Sequence[str]) -> None:
        self.session = session
        self.meshes = list(meshes)
        self.ref: Optional[scene_mod.Reference] = None
        self._own = False

    def __enter__(self) -> scene_mod.Reference:
        if self.session.editing:
            self.ref = self.session.begin_edit()  # 入っているので、記録が返るだけ
        else:
            self.ref = scene_mod.enter_reference_pose(self.meshes, extra_joints=_doc_bone_names(self.session.require()))
            self._own = True
        return self.ref

    def __exit__(self, *exc) -> None:
        if self._own and self.ref is not None:
            self.ref.restore()


def _target_meshes(session) -> list[str]:
    meshes = session.target_meshes()
    if not meshes:
        raise session_mod.FacialSessionError("対象のメッシュがありません（セットアップタブで顔のメッシュを選んでください）")
    return meshes


def _finish_ingest(
    session,
    rep: AnimImportReport,
    curves: dict[str, float],
    bones: dict[str, BoneOffset],
    *,
    replace: bool,
    working_set_only: bool,
    keep_bones: bool,
    apply: bool,
) -> AnimImportReport:
    """読んだ値を編集中の値へ入れて（必要なら）シーンへ当てる = `session.import_pose_curves`（コマンド）。"""
    ing = session.import_pose_curves(curves, bones, replace=replace, working_set_only=working_set_only, keep_bones=keep_bones, apply=apply)
    rep.ingest = ing
    if not ing.ok:
        rep.ok = False
        rep.code = ing.code
        rep.message = ing.message
        return rep
    rep.curves_set = ing.curves_set
    rep.bones_set = 0 if keep_bones else ing.bones_set
    rep.ignored = list(ing.ignored)
    rep.clamped = list(ing.clamped)
    if session.last_apply_error:
        rep.warnings.append(f"シーンへ当てられませんでした: {session.last_apply_error}")
    return rep


def _summary(rep: AnimImportReport) -> str:
    text = f"「{rep.source}」から読み込みました: シェイプ {rep.curves_set} 本・ボーン {rep.bones_set} 本"
    if rep.unmatched_curves:
        text += f"。このモデルに無いシェイプ {len(rep.unmatched_curves)} 本（" + "、".join(rep.unmatched_curves[:5]) + ("…" if len(rep.unmatched_curves) > 5 else "") + "）は読みませんでした"
    if rep.unmatched_bones:
        text += f"。このモデルに無いボーン {len(rep.unmatched_bones)} 本（" + "、".join(rep.unmatched_bones[:5]) + ("…" if len(rep.unmatched_bones) > 5 else "") + "）は読みませんでした"
    if rep.ignored:
        text += f"。作業セットの外なので読まなかった名前 {len(rep.ignored)} 個"
    if rep.clamped:
        text += f"。可動域で丸めたシェイプ {len(rep.clamped)} 本"
    for w in rep.warnings:
        text += "。" + w
    return text + "。保存すると点に書かれます"


# ---------------------------------------------------------------------------
# 今のフレームから
# ---------------------------------------------------------------------------


def pose_from_current_frame(
    session,
    frame: Optional[float] = None,
    working_set_only: bool = False,
    include_bones: bool = True,
    replace: bool = True,
    apply: bool = True,
) -> AnimImportReport:
    """アニメの付いたシーンの今のフレーム（`frame` を指定すればそのフレーム）の状態を、編集中のポーズへ読み込む。

    編集状態（基準姿勢）に入る前の姿勢を読む。編集中だったら先に編集状態を抜ける（シーンはアニメの状態に戻り、終わりに当て直す）。
    シェイプ = ブレンドシェイプの重み（FC_* は除く）、ボーン = バインドポーズからの、ジョイントのローカルのずれ。
    読んだあとシーンの姿勢・重み・時刻は元のまま（`apply=True` なら編集中のポーズを当てる = 編集状態へ入る）。
    """
    try:
        doc = session.require()
        if session.ctx.selection is None:
            return _fail("点が選択されていません（グリッドで点を選んでから読み込んでください）", "no_point")
        if session.editing:
            session.end_edit()
        meshes = _target_meshes(session)
        face = meshes[0]
        rep = AnimImportReport(source="今のフレーム")
        old_time = cmds.currentTime(query=True)
        curves: dict[str, float] = {}
        bones: dict[str, BoneOffset] = {}
        try:
            if frame is not None and abs(float(frame) - old_time) > 1e-9:
                cmds.currentTime(float(frame), edit=True)
            rep.time = float(cmds.currentTime(query=True))
            for c in scene_mod.list_curves(face):  # FC_* を除く
                if c.name in doc.exclude.curves:
                    continue
                w = cmds.getAttr(c.plug)
                if abs(w) > pose_apply.THRESHOLD_W:
                    curves[c.name] = float(w)
            if include_bones:
                cur: dict[str, tuple] = {}
                joints = scene_mod.mesh_joints(meshes)
                for j in joints:
                    cur[scene_mod.short_name(j)] = scene_mod.read_local(j)
                with _Reference(session, meshes) as ref:
                    for name, base in ref.bones.items():
                        if name in doc.exclude.bones or name not in cur:
                            continue
                        t, q, s = cur[name]
                        off = _offset_from_local(base, t, q, s)
                        if not _is_identity(off):
                            bones[name] = off
        finally:
            if abs(cmds.currentTime(query=True) - old_time) > 1e-9:
                cmds.currentTime(old_time, edit=True)
        rep = _finish_ingest(
            session, rep, curves, bones, replace=replace, working_set_only=working_set_only,
            keep_bones=not include_bones, apply=apply,
        )
        if rep.ok:
            rep.message = _summary(rep).replace("「今のフレーム」から読み込みました", f"今のフレーム（{rep.time:g}）から読み込みました")
        return rep
    except session_mod.FacialSessionError as exc:
        return _fail(str(exc), "session")
    except scene_mod.ReferenceError_ as exc:
        return _fail(f"基準姿勢（バインドポーズ）にできません: {exc}", "reference")


# ---------------------------------------------------------------------------
# Unity の .anim から
# ---------------------------------------------------------------------------


def _clip_of(source: Union[str, Path, unity_anim.AnimClip]) -> unity_anim.AnimClip:
    if isinstance(source, unity_anim.AnimClip):
        return source
    return unity_anim.load(source)


def _bones_from_clip(
    session, clip: unity_anim.AnimClip, time: float, meshes: Sequence[str], hermite: bool, rep: AnimImportReport
) -> tuple[dict[str, BoneOffset], bool]:
    """クリップのボーンのカーブ → ずれ。(ずれの辞書, 1 本でもシーンのジョイントに対応したか)。"""
    values = unity_anim.bone_values(clip, time, hermite=hermite)
    if not values:
        return {}, False
    doc = session.require()
    conv = space.converter(space.UNITY, space.MAYA)
    out: dict[str, BoneOffset] = {}
    matched = False
    with _Reference(session, meshes) as ref:
        by_leaf = {n.split(":")[-1]: n for n in ref.bones}
        for leaf, (pos, rot, scl) in values.items():
            name = leaf if leaf in ref.bones else by_leaf.get(leaf.split(":")[-1])
            if name is None:
                rep.unmatched_bones.append(leaf)
                continue
            if name in doc.exclude.bones:
                continue
            matched = True
            base = ref.bones[name]
            t = conv.position(pos) if pos is not None else base.t
            q = conv.quaternion(rot) if rot is not None else base.q
            s = conv.scale_vector(scl) if scl is not None else base.s
            off = _offset_from_local(base, t, q, s)
            if not _is_identity(off):
                out[name] = off
    return out, matched


def pose_from_unity_anim(
    session,
    anim_path: Union[str, Path, unity_anim.AnimClip],
    time: float = 0.0,
    working_set_only: bool = False,
    include_bones: bool = True,
    replace: bool = True,
    apply: bool = True,
    name_mapping: Optional[Mapping[str, str]] = None,
    hermite: bool = False,
) -> AnimImportReport:
    """Unity の `.anim` の `time`（秒）の値を、編集中のポーズへ読み込む（モジュールの docstring「Unity の .anim を読む」）。

    replace=True: 編集中の値を、クリップの値で置き換える（ボーンのカーブがクリップに無い / どれもシーンに無いときは、編集中のボーンは残す）。
    replace=False: クリップが動かすものだけを上書きする（クリップで 0 / 元の姿勢のものは書かない）。
    name_mapping: シェイプ名の置き換え表（ARKit の名前など。`unity_anim.remap_names` と同じ表）。
    apply=True: 読んだあと編集中のポーズをシーンへ当てる（編集状態に入る）。
    """
    try:
        session.require()
        if session.ctx.selection is None:
            return _fail("点が選択されていません（グリッドで点を選んでから読み込んでください）", "no_point")
        try:
            clip = _clip_of(anim_path)
        except unity_anim.UnityAnimError as exc:
            return _fail(f"Unity のアニメとして読めませんでした: {exc}", "parse")
        except OSError as exc:
            return _fail(f"ファイルを開けませんでした: {exc}", "io")
        meshes = _target_meshes(session)
        rep = AnimImportReport(source=clip.name or "アニメ", time=float(time))
        rep.warnings.extend(clip.warnings)
        raw = unity_anim.to_curve_weights(clip, time, hermite=hermite)
        curves, rep.unmatched_curves, rep.renamed = resolve_curve_weights(raw, meshes, name_mapping)
        bones: dict[str, BoneOffset] = {}
        matched = False
        if include_bones:
            bones, matched = _bones_from_clip(session, clip, time, meshes, hermite, rep)
        if not raw and not bones and not clip.transform_curves:
            rep.warnings.append("ブレンドシェイプのカーブがありません")
        rep = _finish_ingest(
            session, rep, curves, bones, replace=replace, working_set_only=working_set_only,
            keep_bones=(not include_bones) or not matched, apply=apply,
        )
        if rep.ok:
            rep.message = _summary(rep)
        return rep
    except session_mod.FacialSessionError as exc:
        return _fail(str(exc), "session")
    except scene_mod.ReferenceError_ as exc:
        return _fail(f"基準姿勢（バインドポーズ）にできません: {exc}", "reference")


# ---------------------------------------------------------------------------
# 土台の表情
# ---------------------------------------------------------------------------


def base_expression_name(session) -> str:
    """有効な土台の表情の名前。無ければ ""（編集状態を抜けていれば土台も無い）。"""
    return session.base_expression_name


def base_expression_curves(session) -> dict[str, float]:
    return session.base_expression_curves


def set_base_expression(
    session,
    source: SourceLike,
    time: float = 0.0,
    name_mapping: Optional[Mapping[str, str]] = None,
    hermite: bool = False,
) -> BaseExpressionReport:
    """土台の表情を当てる（シーンへの下敷き。モジュールの docstring「土台の表情」）。

    source: Unity の `.anim` のパス（`time` 秒の値）/ `AnimClip` / {シェイプ名: 重み}。編集状態に入る（基準姿勢の上に土台を重ねる）。
    編集中のポーズ・文書は変えない。すでに土台があれば置き換える。ポーズの値に足して当てる。"""
    try:
        session.require()
        meshes = _target_meshes(session)
        if isinstance(source, Mapping):
            raw = {str(k): float(v) for k, v in source.items()}
            label = "土台"
            warnings: list[str] = []
        else:
            try:
                clip = _clip_of(source)
            except unity_anim.UnityAnimError as exc:
                return BaseExpressionReport(ok=False, message=f"Unity のアニメとして読めませんでした: {exc}")
            except OSError as exc:
                return BaseExpressionReport(ok=False, message=f"ファイルを開けませんでした: {exc}")
            raw = unity_anim.to_curve_weights(clip, time, hermite=hermite)
            label = clip.name or "アニメ"
            warnings = list(clip.warnings)
        weights, unmatched, _renamed = resolve_curve_weights(raw, meshes, name_mapping)
        weights = {n: w for n, w in weights.items() if abs(w) > pose_apply.THRESHOLD_W}
        if not weights:
            return BaseExpressionReport(ok=False, message="土台にできるシェイプがありませんでした（このモデルに無い名前か、全部 0 です）", unmatched=unmatched)
        try:
            state = session.set_base_expression(label, weights)
        except session_mod.FacialSessionError as exc:
            return BaseExpressionReport(ok=False, message=str(exc), name=label)
        except Exception as exc:  # noqa: BLE001
            lifecycle.report_error("土台の表情を当てられませんでした", traceback.format_exc(), once=False)
            return BaseExpressionReport(ok=False, message=f"土台の表情を当てられませんでした: {exc}", name=label)
    except session_mod.FacialSessionError as exc:
        return BaseExpressionReport(ok=False, message=str(exc))
    last = session.last_apply
    locked = list(last.locked) if last is not None else []
    msg = f"土台の表情「{label}」を当てました（シェイプ {len(weights)} 本）。ポーズの値に足して表示します。編集を終えると外れます"
    if unmatched:
        msg += f"。このモデルに無いシェイプ {len(unmatched)} 本は当てていません"
    if state.over_limit:
        msg += f"。土台と合わせて可動域を超えているシェイプ {len(state.over_limit)} 本（" + "、".join(sorted(state.over_limit)[:5]) + ("…" if len(state.over_limit) > 5 else "") + "）"
    return BaseExpressionReport(
        message=msg, name=label, applied=sorted(weights), unmatched=unmatched, locked=locked, warnings=warnings,
        over_limit=dict(state.over_limit),
    )


def clear_base_expression(session, reapply: bool = True) -> bool:
    """土台の表情を外す。あったら True。編集中なら編集中のポーズだけをシーンへ当て直す（reapply=False で当て直さない）。"""
    return session.clear_base_expression(reapply=reapply)


def capture_from_scene(session, working_set_only: Optional[bool] = None) -> CaptureResult:
    """「シーンから取り込む」。土台の表情が有効なら、`session.capture_from_scene` が土台の値を引く（ポーズの値 = シーンの値 − 土台の値）。
    base_ignored は、土台のとおり（変化なし）だったので取り込まなかった土台のシェイプ。"""
    rep = session.capture_from_scene(working_set_only=working_set_only)
    return CaptureResult(rep, list(session.last_capture_base_ignored), list(session.last_capture_below_base))


# ---------------------------------------------------------------------------
# 他のデータからコピー
# ---------------------------------------------------------------------------


def copy_from_file(
    session,
    src_path: Union[str, Path],
    mode_flags: Union[str, Sequence[str]] = ("keys_only",),
    src_layer_index: Optional[int] = None,
    dst_layer_index: Optional[int] = None,
) -> CopyFileResult:
    """別の FacialController のデータ（.fcpose.json）からポーズをコピーする（`session.copy_from_file` = `@undoable` のコマンド）。

    mode_flags: "all_layers"（名前が同じレイヤー同士。宛先に無ければ新規作成）/ "working_set_only"（宛先の作業セットの名前だけ）/
    "keys_only"（ソースのキーだけ。省くとソースの自動生成の点も）の組み合わせ。座標系が違えばボーンのずれを変換する。
    1 つのレイヤーだけのとき: src_layer_index を省くと宛先のアクティブレイヤーと同じ名前（無ければ 0 番）、dst_layer_index を省くとアクティブレイヤー。
    宛先の点は**非キーとして上書き**する（宛先にあったキーも上書き）。1 回の呼び出し = 1 つの元に戻す。
    コピーは点のポーズを書き換えるだけで、点が空になることは無いので、消すべき古い FC_* は出ない。シーンのベイクは変更のある点を焼き直す。"""
    try:
        session.require()
        try:
            flags = sorted(autofill.parse_copy_mode(mode_flags))
        except ValueError as exc:
            return CopyFileResult(ok=False, message=str(exc))
        try:
            src = fcpose_io.load_document(src_path)
        except (fcpose_io.FcposeError, OSError, ValueError) as exc:
            return CopyFileResult(ok=False, message=f"ファイルを読めませんでした: {exc}", source=str(src_path))
        name = Path(str(src_path)).name
        try:
            rep = session.copy_from_document(src, flags, src_layer_index, dst_layer_index)
        except (ValueError, IndexError) as exc:
            return CopyFileResult(ok=False, message=f"コピーできませんでした: {exc}", source=name, flags=flags)
        return CopyFileResult(ok=True, message=_copy_message(name, rep, flags), source=name, flags=flags, report=rep)
    except session_mod.FacialSessionError as exc:
        return CopyFileResult(ok=False, message=str(exc))


def _copy_message(name: str, rep: autofill.CopyReport, flags: Sequence[str]) -> str:
    if rep.copied_points == 0:
        text = f"「{name}」からコピーできる点がありませんでした"
        if "keys_only" in flags:
            text += "（「キーだけ」を切ると、自動生成の点もコピーします）"
    else:
        text = f"「{name}」からコピーしました: レイヤー {rep.copied_layers} 枚・点 {rep.copied_points} 個（キーではなく自動生成の点になります）"
    if rep.created_layers:
        text += "。新しく作ったレイヤー: " + "、".join(rep.created_layers)
    if rep.skipped_layers:
        text += "。コピーするポーズが無くて飛ばしたレイヤー: " + "、".join(rep.skipped_layers)
    if rep.layer_limit_reached:
        text += "。レイヤーの上限で作れなかった: " + "、".join(rep.layer_limit_reached)
    if rep.copied_points:
        text += "。ベイクはまだです（「ベイク（変更のある点）」で反映）。元に戻すときは「元に戻す」を押してください"
    return text
