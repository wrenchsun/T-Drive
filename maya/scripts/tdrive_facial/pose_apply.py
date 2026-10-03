"""ポーズをシーンへ当てる / シーンから取り込む（Maya 依存。docs/15 §4.2）。

- 当てる（`apply_pose`）: シェイプ = blendShape の重みを設定。ボーン = 基準のローカル値に `offset × base` で加算
- 取り込む（`capture_pose`）: 重みが 0 でないシェイプ + 基準からのジョイントのずれを BoneOffset に分解
- 基準へ戻す（`reset_to_reference`）

ボーンのずらしの意味（UE 版移植設計 00 §3。`FacialDeltaBaker` と同じ）:
  親ボーン空間での加算。ローカルの位置 = 基準の位置 + t、ローカルの向き = r · 基準の向き（ハミルトン積。親の空間で回す）、
  スケール = 基準のスケール × s。BoneOffset の適用順は Scale → Rotation → Translation。クォータニオンは [x, y, z, w]。
  取り込みはその逆（t = 現在 − 基準、r = 現在 · 基準⁻¹、s = 現在 / 基準）。

座標系は Document の meta のまま（Maya = cm / Y-up / 右手）。ここでは変換しない。Maya の系でなければ ValueError
（変換は呼ぶ側が `core.space` で行う）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from maya import cmds

from . import scene
from .core import space
from .core.model import BoneOffset, Document, SourcePose
from .core.profile import is_mirror_excluded

THRESHOLD_T = 1e-3  # cm。これ未満のずれは取り込まない
THRESHOLD_R = 1e-4  # クォータニオンの xyz 成分。これ以下は恒等とみなす
THRESHOLD_S = 1e-4
THRESHOLD_W = 1e-6  # 重みが 0 でないとみなす下限


@dataclass
class ApplyReport:
    """`apply_pose` の結果。シーンに無い名前は黙って飛ばす（フェイルソフト）が、ここに返す。"""

    missing_curves: list[str] = field(default_factory=list)
    missing_bones: list[str] = field(default_factory=list)
    skipped_excluded: list[str] = field(default_factory=list)
    locked: list[str] = field(default_factory=list)  # 値を書けなかった（ロック・接続されている）もの

    @property
    def ok(self) -> bool:
        return not (self.missing_curves or self.missing_bones or self.locked)


def assert_maya_space(doc: Document) -> None:
    """Document が Maya の系（cm / Y-up / 右手）でなければ ValueError。"""
    spec = space.space_of(doc.meta)
    if spec != space.MAYA:
        raise ValueError(
            f"Document の座標系が Maya の系ではありません（{spec.unit} / {spec.up_axis}-up / {spec.handedness}）。"
            "core.space.convert_document で Maya へ変換してから使ってください"
        )


def _is_excluded_curve(doc: Document, name: str) -> bool:
    """補正除外パターン（部分一致。UE 版・ミラー除外と同じ。空のパターンは無視）に当たるシェイプか。"""
    return is_mirror_excluded(name, doc.exclude.curves)


def _is_excluded_bone(doc: Document, name: str) -> bool:
    return is_mirror_excluded(name, doc.exclude.bones)


def is_excluded(doc: Document, kind: str, name: str) -> bool:
    """画面（ポーズタブ）が使う判定。kind = "curve" / "bone"。"""
    return _is_excluded_curve(doc, name) if kind == "curve" else _is_excluded_bone(doc, name)


def reset_to_reference(ref: scene.Reference) -> None:
    """ジョイントを基準のローカル値へ、基準で扱う全シェイプの重みを 0 へ戻す。"""
    for b in ref.bones.values():
        scene._write_raw(b.path, b.raw)
    for plug in ref.weight_plugs:
        try:
            if cmds.getAttr(plug) != 0.0:
                cmds.setAttr(plug, 0.0)
        except RuntimeError:
            pass


def apply_pose(doc: Document, pose: SourcePose, ref: scene.Reference) -> ApplyReport:
    """ポーズを当てる。先に基準へ戻してから、ポーズのシェイプとボーンだけを動かす（前に当てたものは残らない）。

    doc.exclude のパターン（部分一致）に当たるシェイプ・ボーンは触らない。シーンに無い名前は飛ばして ApplyReport に入れる。
    """
    assert_maya_space(doc)
    rep = ApplyReport()
    reset_to_reference(ref)

    for name, w in pose.curves.items():
        if _is_excluded_curve(doc, name):
            rep.skipped_excluded.append(name)
            continue
        plugs = scene.reference_curve_plugs(ref, name)
        if not plugs:
            rep.missing_curves.append(name)
            continue
        for plug in plugs:
            try:
                cmds.setAttr(plug, float(w))
            except RuntimeError:
                rep.locked.append(plug)

    for name, off in pose.bones.items():
        if _is_excluded_bone(doc, name):
            rep.skipped_excluded.append(name)
            continue
        base = ref.bones.get(name)
        if base is None:
            rep.missing_bones.append(name)
            continue
        t = tuple(b + o for b, o in zip(base.t, off.t))
        q = scene.quat_normalize(scene.quat_mul(scene.quat_normalize(off.r), base.q))
        s = tuple(b * o for b, o in zip(base.s, off.s))
        try:
            scene.write_local(base.path, t, q, s)
        except RuntimeError:
            rep.locked.append(base.path)
    return rep


def capture_pose(doc: Document, ref: scene.Reference, working_set_only: bool = False) -> SourcePose:
    """今のシーンの状態をポーズとして取り込む。

    - シェイプ: ref.meshes[0] の blendShape で重みが 0 でないもの（`bs.eye_close_L` の形の名前）。FC_* は含めない（焼いた結果であってソースではない）
    - ボーン: 基準からのずれ（位置 1e-3 cm、向き 1e-4、スケール 1e-4 以上）を BoneOffset に分解
    - doc.exclude の名前は含めない。working_set_only=True で、作業セットが空でなければその名前だけ
    """
    assert_maya_space(doc)
    ws_curves = set(doc.working_set.curves) if working_set_only and doc.working_set.curves else None
    ws_bones = set(doc.working_set.bones) if working_set_only and doc.working_set.bones else None
    pose = SourcePose()

    face = ref.meshes[0] if ref.meshes else None
    for c in scene.list_curves(face) if face else []:  # FC_* を除く
        name = c.name
        if _is_excluded_curve(doc, name) or (ws_curves is not None and name not in ws_curves):
            continue
        w = cmds.getAttr(c.plug)
        if abs(w) > THRESHOLD_W:
            pose.curves[name] = float(w)

    for name, base in ref.bones.items():
        if _is_excluded_bone(doc, name) or (ws_bones is not None and name not in ws_bones):
            continue
        t, q, s = scene.read_local(base.path)
        dt = tuple(c - b for c, b in zip(t, base.t))
        dq = scene.quat_normalize(scene.quat_mul(q, scene.quat_inv(base.q)))
        if dq[3] < 0:
            dq = (-dq[0], -dq[1], -dq[2], -dq[3])
        ds = tuple(c / b if abs(b) > 1e-12 else 1.0 for c, b in zip(s, base.s))
        if (
            max(abs(v) for v in dt) < THRESHOLD_T
            and max(abs(dq[0]), abs(dq[1]), abs(dq[2])) <= THRESHOLD_R
            and max(abs(v - 1.0) for v in ds) <= THRESHOLD_S
        ):
            continue
        pose.bones[name] = BoneOffset(t=tuple(float(v) for v in dt), r=tuple(float(v) for v in dq), s=tuple(float(v) for v in ds))
    return pose

