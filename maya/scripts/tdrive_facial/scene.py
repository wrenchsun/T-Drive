"""FacialController と Maya のシーンの境目（Maya 依存。docs/15 §4.1・§4.3、docs/14 §5.7・§11）。

- メッシュ・blendShape・ジョイントの一覧、シェイプ名（`bs.eye_close_L`）⇔（ノード, ターゲット番号, エイリアス）の対応
- 基準姿勢（バインドポーズ）へ入る / 戻る（`enter_reference_pose`。try / finally で必ず戻せる）
- 変形後の頂点の読み取り、blendShape ターゲットの差分の読み書き（別メッシュを作らず `inputPointsTarget` /
  `inputComponentsTarget` へ直接書く）
- 検証（`core.validate`）に渡すシーンの事情（`build_scene_info`）

約束:
- 元のマテリアル・元の割り当て・既存のターゲット（`bs.*`）には触らない。作る・消すのは `FC_*` / `fcs_*` だけ
  （`delete_targets` は名前を確かめ、それ以外は例外にする）。ターゲットの番号は詰めず、足すときは「最大 + 1」
- Maya にコールバック・Python のオブジェクトを登録しない（このモジュールは受け身の関数だけ）
- クォータニオンは [x, y, z, w]、積はハミルトン積（`quat_mul(a, b)` = b を先に回し、次に a を回す。
  `.fcpose.json` の BoneOffset と同じ）。Maya の MQuaternion の `*`（左から先に適用）とは順序が逆なので、積は必ずここの関数を使う
"""

from __future__ import annotations

import contextlib
import json
import math
import re
from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

import numpy as np
from maya import cmds
from maya.api import OpenMaya as om

from .core import naming, validate
from .core.model import Document

BAKE_STATE_ATTR = "tdFacialBakeState"
BAKE_EXCLUDE_ATTR = "tdFacialBakeExclude"  # morph 名 → ベイク時の除外パターンの指紋（無い = 不明）
PREVIEW_ONLY_ATTR = "tdPreviewOnly"  # Toon のプレビュー専用メッシュの目印（preview.PREVIEW_ONLY_ATTR と同じ名前）
FRONT_BLEND_SHAPE_PREFIX = "tdFacial_"
DELTA_ITEM = 6000  # inputTargetItem の番号（6000 = 重み 1.0 の形）

_WEIGHT_INDEX = re.compile(r"weight\[(\d+)\]")
_COMPONENT = re.compile(r"\[(\d+)(?::(\d+))?\]")


# ---------------------------------------------------------------------------
# クォータニオン（[x, y, z, w]・ハミルトン積）
# ---------------------------------------------------------------------------

Quat = tuple[float, float, float, float]


def quat_mul(a: Sequence[float], b: Sequence[float]) -> Quat:
    """a · b（b を先に回し、次に a を回す）。"""
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    )


def quat_inv(q: Sequence[float]) -> Quat:
    """単位クォータニオンの逆（共役）。"""
    return (-q[0], -q[1], -q[2], q[3])


def quat_normalize(q: Sequence[float]) -> Quat:
    n = math.sqrt(sum(v * v for v in q))
    if n < 1e-12:
        return (0.0, 0.0, 0.0, 1.0)
    return (q[0] / n, q[1] / n, q[2] / n, q[3] / n)


def quat_dot(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


# ---------------------------------------------------------------------------
# 名前・メッシュ
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def no_undo():
    """この間の Maya のシーンの変更を Maya の Undo に積まない（編集状態の出入り・ポーズの当て込みなど、ユーザーの操作ではないもの。M-5）。"""
    try:
        prev = bool(cmds.undoInfo(query=True, state=True))
        cmds.undoInfo(stateWithoutFlush=False)
    except RuntimeError:
        yield
        return
    try:
        yield
    finally:
        try:
            cmds.undoInfo(stateWithoutFlush=prev)
        except RuntimeError:
            pass


def short_name(path: str) -> str:
    """`|a|b|c` → `c`（名前空間は残す）。"""
    return path.split("|")[-1]


def _parent(node: str) -> Optional[str]:
    p = cmds.listRelatives(node, parent=True, fullPath=True)
    return p[0] if p else None


# 「顔以外を隠す」（hide_others.py）が一時的に overrideVisibility で隠しているノード（長い名前）。本当に隠れているものとは区別する
TOOL_HIDDEN: set[str] = set()


def _is_hidden(transform: str) -> bool:
    """自分または祖先の visibility が切れている / 表示レイヤーで非表示（ツールが「顔以外を隠す」で一時的に隠しているものは数えない）。"""
    n: Optional[str] = transform
    while n:
        if cmds.attributeQuery("visibility", node=n, exists=True) and not cmds.getAttr(n + ".visibility"):
            return True
        if n not in TOOL_HIDDEN and cmds.attributeQuery("overrideEnabled", node=n, exists=True) and cmds.getAttr(n + ".overrideEnabled"):
            if not cmds.getAttr(n + ".overrideVisibility"):
                return True
        n = _parent(n)
    return False


def list_visible_meshes() -> list[str]:
    """表示中のメッシュ（transform の長い名前）。FBX 取り込みで最上位にできる非表示のターゲットメッシュ・Toon のプレビュー専用メッシュは除く。"""
    out: list[str] = []
    seen: set[str] = set()
    for shape in cmds.ls(type="mesh", long=True, noIntermediate=True) or []:
        xf = _parent(shape)
        if not xf or xf in seen:
            continue
        seen.add(xf)
        if _is_hidden(xf) or cmds.attributeQuery(PREVIEW_ONLY_ATTR, node=xf, exists=True):
            continue
        out.append(xf)
    return out


def mesh_shape(mesh: str) -> str:
    """メッシュ（transform または shape）の、中間でない shape（長い名前）。"""
    if cmds.nodeType(mesh) == "mesh":
        return cmds.ls(mesh, long=True)[0]
    shapes = [
        s
        for s in cmds.listRelatives(mesh, shapes=True, fullPath=True, type="mesh") or []
        if not cmds.getAttr(s + ".intermediateObject")
    ]
    if not shapes:
        raise ValueError(f"{mesh} にメッシュの shape がありません")
    return shapes[0]


def resolve_mesh(name: str) -> str:
    """メッシュ名（短い名前でもよい）→ transform の長い名前。見つからない・複数あって決められないときは ValueError。"""
    if not name:
        raise ValueError("メッシュ名が空です")
    found = cmds.ls(name, long=True) or []
    xfs: list[str] = []
    for f in found:
        if cmds.nodeType(f) == "mesh":
            f = _parent(f) or f
        if cmds.listRelatives(f, shapes=True, type="mesh") and f not in xfs:
            xfs.append(f)
    if not xfs:
        raise ValueError(f"メッシュ {name} がシーンにありません")
    if len(xfs) > 1:
        visible = [x for x in xfs if not _is_hidden(x)]
        if len(visible) == 1:
            return visible[0]
        raise ValueError(f"メッシュ名 {name} が複数あります: {xfs}")
    return xfs[0]


def read_points(mesh: str) -> np.ndarray:
    """変形後の頂点（オブジェクト空間）を (N, 3) の配列で返す。"""
    sel = om.MSelectionList()
    sel.add(mesh_shape(mesh))
    pts = om.MFnMesh(sel.getDagPath(0)).getPoints(om.MSpace.kObject)
    return np.array([(p.x, p.y, p.z) for p in pts], dtype=np.float64).reshape(-1, 3)


def vertex_count(mesh: str) -> int:
    return int(cmds.polyEvaluate(mesh_shape(mesh), vertex=True))


# ---------------------------------------------------------------------------
# blendShape
# ---------------------------------------------------------------------------


def skin_clusters(mesh: str) -> list[str]:
    hist = cmds.listHistory(mesh_shape(mesh), pruneDagObjects=True) or []
    return cmds.ls(hist, type="skinCluster") or []


def blend_shapes(mesh: str) -> list[str]:
    """メッシュのヒストリにある blendShape ノード（出力に近い順）。"""
    hist = cmds.listHistory(mesh_shape(mesh), pruneDagObjects=True) or []
    return cmds.ls(hist, type="blendShape") or []


def _blend_shape_before_skin(mesh: str) -> list[str]:
    """スキンより上流（変形前の形に効く）の blendShape。スキンが無ければ全部。"""
    hist = cmds.listHistory(mesh_shape(mesh), pruneDagObjects=True) or []
    skin_pos = min((i for i, n in enumerate(hist) if cmds.nodeType(n) == "skinCluster"), default=-1)
    out = []
    for i, n in enumerate(hist):
        if cmds.nodeType(n) == "blendShape" and (skin_pos < 0 or i > skin_pos):
            out.append(n)
    return out


def primary_blend_shape(mesh: str, create: bool = False) -> Optional[str]:
    """FC_* を足す先の blendShape（スキンより前のもの）。

    create=True で、スキンより前の blendShape が無ければ `tdFacial_<mesh>` をスキンより前（frontOfChain）に作る
    （スキンの後ろの blendShape に FC_* を入れると、補正がスキニングの後に足されて頭の動きと食い違う。S-6）。
    create=False のときは、スキンより前が無ければ出力に近い最初のもの（読むだけ）。無ければ None。
    """
    nodes = _blend_shape_before_skin(mesh)
    if nodes:
        return nodes[0]
    if not create:
        nodes = blend_shapes(mesh)
        return nodes[0] if nodes else None
    name = f"{FRONT_BLEND_SHAPE_PREFIX}{short_name(mesh)}"
    return cmds.blendShape(mesh_shape(mesh), name=name, frontOfChain=True)[0]


def target_indices(node: str) -> dict[str, int]:
    """blendShape ノードのターゲット名（エイリアス）→ weight の番号。エイリアスが無い番号は含めない。"""
    out: dict[str, int] = {}
    al = cmds.aliasAttr(node, query=True) or []
    for alias, plug in zip(al[0::2], al[1::2]):
        m = _WEIGHT_INDEX.fullmatch(plug)
        if m:
            out[alias] = int(m.group(1))
    return out


def _group_indices(node: str) -> list[int]:
    """差分を持つ inputTargetGroup の番号。Undo の後に空の group の要素だけが残ることがあるので、中身のあるものだけ数える。"""
    out: set[int] = set()
    for g in cmds.getAttr(node + ".inputTarget", multiIndices=True) or []:
        for i in cmds.getAttr(f"{node}.inputTarget[{g}].inputTargetGroup", multiIndices=True) or []:
            if cmds.getAttr(f"{node}.inputTarget[{g}].inputTargetGroup[{i}].inputTargetItem", multiIndices=True):
                out.add(i)
    return sorted(out)


def _all_indices(node: str) -> list[int]:
    """使われている weight / group の番号（新しい番号は、この最大 + 1）。"""
    return sorted(set(cmds.getAttr(node + ".weight", multiIndices=True) or []) | set(_group_indices(node)))


def weight_plug(node: str, index: int) -> str:
    return f"{node}.weight[{index}]"


def geometry_index(node: str, mesh: str) -> int:
    """blendShape が mesh を変形している geometry の番号（inputTarget[g]）。見つからなければ 0。"""
    geos = cmds.deformer(node, query=True, geometry=True) or []
    inds = cmds.deformer(node, query=True, geometryIndices=True) or []
    target = mesh_shape(mesh)
    for g, i in zip(geos, inds):
        if (cmds.ls(g, long=True) or [g])[0] == target:
            return int(i)
    if len(geos) > 1:  # 複数のメッシュを変形するノードで、見つからないまま 0 を返すと別のメッシュへ書いてしまう（S-13）
        raise ValueError(f"{node} は {short_name(mesh)} を変形していません")
    return 0


@dataclass(frozen=True)
class CurveRef:
    node: str
    index: int
    alias: str

    @property
    def plug(self) -> str:
        return weight_plug(self.node, self.index)

    @property
    def name(self) -> str:
        return curve_name(self.node, self.alias)


def curve_name(node: str, alias: str) -> str:
    """（ノード, ターゲット名）→ ソースの名前 `bs.eye_close_L`。"""
    return f"{node}.{alias}"


def parse_curve_name(name: str) -> tuple[Optional[str], str]:
    """`bs.eye_close_L` → ("bs", "eye_close_L")。ノード名が付かない名前は (None, 名前)。"""
    if "." in name:
        node, _, target = name.partition(".")
        return node, target
    return None, name


def resolve_curve(name: str, mesh: str) -> Optional[CurveRef]:
    """ソースの名前 → CurveRef。見つからなければ None。

    ノード名付き: そのノードのターゲット。ノード名なし: メッシュの blendShape のうち最初に見つかったもの。
    """
    node, target = parse_curve_name(name)
    if node is not None:
        if not cmds.objExists(node) or cmds.nodeType(node) != "blendShape":
            return None
        idx = target_indices(node).get(target)
        return CurveRef(node, idx, target) if idx is not None else None
    for n in blend_shapes(mesh):
        idx = target_indices(n).get(target)
        if idx is not None:
            return CurveRef(n, idx, target)
    return None


def driven_indices(node: str) -> set[int]:
    """combinationShape（組み合わせ補正。docs/14 §5.6）に重みを駆動されているターゲットの番号。"""
    out: set[int] = set()
    # connections=True は (自分の plug, 相手の plug) の並びで返る
    conns = cmds.listConnections(node, source=True, destination=False, plugs=True, connections=True, type="combinationShape") or []
    table = None
    for dst in conns[0::2]:
        m = _WEIGHT_INDEX.search(dst)
        if m:
            out.add(int(m.group(1)))
            continue
        if table is None:  # エイリアスの名前（`bs.fcs_combo_...`）で返ってくる
            table = target_indices(node)
        idx = table.get(dst.split(".", 1)[-1])
        if idx is not None:
            out.add(idx)
    return out


def list_curves(mesh: str, include_fc: bool = False, include_driven: bool = False) -> list[CurveRef]:
    """メッシュの全 blendShape のターゲット一覧（ノード順 → 番号順）。include_fc=False で FC_* を除く。

    include_driven=False で、combinationShape に駆動されているもの（組み合わせ補正。ポーズから動かせない）も除く。
    """
    out: list[CurveRef] = []
    for n in blend_shapes(mesh):
        driven = set() if include_driven else driven_indices(n)
        for alias, idx in sorted(target_indices(n).items(), key=lambda kv: kv[1]):
            if idx in driven:
                continue
            if include_fc or not naming.is_fc_name(alias):
                out.append(CurveRef(n, idx, alias))
    return out


def curve_names(mesh: str, include_fc: bool = False) -> list[str]:
    """メッシュの全シェイプ名（`bs.eye_close_L` の形）。"""
    return [c.name for c in list_curves(mesh, include_fc)]


# ---------------------------------------------------------------------------
# ジョイント・基準ボーン
# ---------------------------------------------------------------------------


def skeleton_roots(meshes: Iterable[str]) -> list[str]:
    """メッシュのスキンの影響ジョイントをたどった、骨格の根（ジョイントの親が無くなるところ。長い名前）。"""
    roots: list[str] = []
    for mesh in meshes:
        for sk in skin_clusters(mesh):
            infl = cmds.listConnections(sk + ".matrix", source=True, destination=False) or []
            for j in cmds.ls(infl, long=True) or []:
                cur = j
                while True:
                    p = _parent(cur)
                    if p and cmds.nodeType(p) == "joint":
                        cur = p
                    else:
                        break
                if cur not in roots:
                    roots.append(cur)
    return roots


def joints_under(root: str) -> list[str]:
    """root とその子孫のジョイント（長い名前。root が先頭）。"""
    out = [root] if cmds.nodeType(root) == "joint" else []
    out += cmds.listRelatives(root, allDescendents=True, type="joint", fullPath=True) or []
    return out


def mesh_joints(meshes: Iterable[str]) -> list[str]:
    """メッシュの骨格のジョイントすべて（長い名前・重複なし）。"""
    seen: dict[str, None] = {}
    for r in skeleton_roots(meshes):
        for j in joints_under(r):
            seen.setdefault(j, None)
    return list(seen)


def joint_parents(joints: Iterable[str]) -> dict[str, str]:
    """短い名前 → 親の短い名前（親がジョイントでなければ ""）。"""
    out: dict[str, str] = {}
    for j in joints:
        p = _parent(j)
        out[short_name(j)] = short_name(p) if p and cmds.nodeType(p) == "joint" else ""
    return out


def find_joint(name: str, among: Optional[Iterable[str]] = None) -> Optional[str]:
    """短い名前（または長い名前）のジョイント。among（長い名前の集まり）があればその中を優先する。"""
    if among is not None:
        for j in among:
            if j == name or short_name(j) == name:
                return j
    found = cmds.ls(name, type="joint", long=True) or []
    return found[0] if found else None


_BASE_KEYWORDS = ("head", "頭", "atama")
_SEPARATORS = ":_-.| "


def detect_base_bone(names: Sequence[str]) -> str:
    """基準ボーン（頭）の自動検出。UE 版 `AutoDetectBaseBone` の写し。見つからなければ ""。

    名前空間・接頭辞を除いた末尾が `head` の完全一致（head / Head / bone_head / J_Bip_C_Head / mixamorig:Head）が最優先、
    次に末尾一致（…Head）、次に部分一致（Head1 など。forehead のような誤検出は短い名前・階層上位の優先で避ける）。
    names は階層の上位から並べておく（同点のとき先のものを優先する）。
    """
    best_score = 0
    best = ""
    for i, name in enumerate(names):
        low = short_name(name).lower()
        leaf = low
        for sep in _SEPARATORS:
            k = leaf.rfind(sep)
            if k >= 0:
                leaf = leaf[k + 1 :]
        score = 0
        if leaf == "head" or short_name(name) == "頭":
            score = 1000
        else:
            for kw in _BASE_KEYWORDS:
                if low.endswith(kw):
                    score = max(score, 800)
                elif kw in low:
                    score = max(score, 500)
        if score > 0:
            score = score * 100 - len(short_name(name)) - i // 10
        if score > best_score:
            best_score = score
            best = short_name(name)
    return best


def detect_base_bone_for(meshes: Iterable[str]) -> str:
    """メッシュの骨格から基準ボーンを検出する。"""
    return detect_base_bone([short_name(j) for j in mesh_joints(meshes)])


# ---------------------------------------------------------------------------
# ジョイントのローカルトランスフォーム
# ---------------------------------------------------------------------------


def _angle_ui_to_rad(v: float) -> float:
    return om.MAngle(v, om.MAngle.uiUnit()).asRadians()


def _rad_to_angle_ui(v: float) -> float:
    return om.MAngle(v, om.MAngle.kRadians).asUnits(om.MAngle.uiUnit())


def _euler_quat(xyz: Sequence[float], order: int = 0) -> Quat:
    q = om.MEulerRotation(*(_angle_ui_to_rad(a) for a in xyz), order).asQuaternion()
    return (q.x, q.y, q.z, q.w)


def read_local(joint: str) -> tuple[tuple[float, float, float], Quat, tuple[float, float, float]]:
    """ジョイントのローカルの位置・向き（jointOrient・rotateAxis を含む全体）・スケール。"""
    m = om.MMatrix(cmds.getAttr(joint + ".matrix"))
    tm = om.MTransformationMatrix(m)
    t = tm.translation(om.MSpace.kTransform)
    q = tm.rotation(asQuaternion=True)
    s = tm.scale(om.MSpace.kTransform)
    return (t.x, t.y, t.z), quat_normalize((q.x, q.y, q.z, q.w)), (s[0], s[1], s[2])


def write_local(joint: str, t: Sequence[float], q: Sequence[float], s: Sequence[float]) -> None:
    """ローカルの位置（cm）・向き（全体）・スケールをジョイントに設定する（translate / rotate / scale に分解して書く。作業単位が cm 以外でも位置は cm で渡す）。"""
    q_jo = _euler_quat(cmds.getAttr(joint + ".jointOrient")[0])
    q_ra = _euler_quat(cmds.getAttr(joint + ".rotateAxis")[0])
    # 全体 = jointOrient · rotate · rotateAxis（ハミルトン積。Maya の行列 S·RA·R·JO と同じ回転）
    q_r = quat_mul(quat_mul(quat_inv(q_jo), quat_normalize(q)), quat_inv(q_ra))
    order = cmds.getAttr(joint + ".rotateOrder")
    e = om.MQuaternion(*q_r).asEulerRotation().reorderIt(order)
    ui = om.MDistance.uiUnit()
    cmds.setAttr(joint + ".translate", *(om.MDistance(v, om.MDistance.kCentimeters).asUnits(ui) for v in t))
    cmds.setAttr(joint + ".rotate", *(_rad_to_angle_ui(a) for a in (e.x, e.y, e.z)))
    cmds.setAttr(joint + ".scale", *s)


def _read_raw(joint: str) -> dict[str, tuple[float, float, float]]:
    return {a: tuple(cmds.getAttr(f"{joint}.{a}")[0]) for a in ("translate", "rotate", "scale", "jointOrient")}


def _write_raw(joint: str, raw: dict[str, tuple[float, float, float]]) -> bool:
    """控えた値を書き戻す。つながっている・ロックされていて書けなければ False。"""
    ok = True
    for a, v in raw.items():
        try:
            cur = cmds.getAttr(f"{joint}.{a}")[0]
            if max(abs(x - y) for x, y in zip(cur, v)) > 1e-12:
                cmds.setAttr(f"{joint}.{a}", *v)
        except RuntimeError:
            ok = False
    return ok


# ---------------------------------------------------------------------------
# 基準姿勢
# ---------------------------------------------------------------------------


@dataclass
class BoneRef:
    """基準姿勢でのジョイントのローカル値。"""

    path: str
    t: tuple[float, float, float]
    q: Quat
    s: tuple[float, float, float]
    raw: dict = field(default_factory=dict)  # 基準姿勢での translate / rotate / scale / jointOrient の生の値（戻すとき用）


class ReferenceError_(RuntimeError):
    """基準姿勢にできない。"""


@dataclass
class Reference:
    """基準姿勢の記録と、入る前の状態（戻すため）。

    `enter_reference_pose` が作る。`with enter_reference_pose(meshes) as ref:` でも、
    try / finally で `ref.restore()` を呼ぶ形でも使える。
    """

    meshes: list[str] = field(default_factory=list)  # 長い名前。先頭が顔メッシュ
    bones: dict[str, BoneRef] = field(default_factory=dict)  # 短い名前 → BoneRef
    nodes_by_mesh: dict[str, list[str]] = field(default_factory=dict)  # メッシュ → blendShape ノード
    weight_plugs: list[str] = field(default_factory=list)  # 基準姿勢で 0 にした weight（全 blendShape・FC_* を含む）
    has_bind_pose: bool = False
    warnings: list[str] = field(default_factory=list)
    active: bool = False
    _saved_joints: dict = field(default_factory=dict)
    _saved_weights: dict = field(default_factory=dict)
    _disconnected: list = field(default_factory=list)  # (ソース plug, weight plug)
    _curve_cache: dict = field(default_factory=dict)

    def __enter__(self) -> "Reference":
        return self

    def __exit__(self, *exc) -> None:
        self.restore()

    def restore(self) -> None:
        """入る前の姿勢・重み・接続へ戻す（何度呼んでもよい）。"""
        if not self.active:
            return
        self.active = False
        for j, raw in self._saved_joints.items():
            if cmds.objExists(j):
                _write_raw(j, raw)
        for plug, v in self._saved_weights.items():
            try:
                if cmds.objExists(plug):
                    cmds.setAttr(plug, v)
            except RuntimeError:
                pass
        for src, dst in self._disconnected:
            try:
                if cmds.objExists(src) and cmds.objExists(dst) and not cmds.isConnected(src, dst):
                    cmds.connectAttr(src, dst, force=True)
            except RuntimeError:
                self.warnings.append(f"{dst} の接続を元へ戻せませんでした（{src}）")
        self._saved_joints.clear()
        self._saved_weights.clear()
        self._disconnected.clear()


def enter_reference_pose(meshes: Sequence[str], extra_joints: Iterable[str] = ()) -> Reference:
    """基準姿勢（バインドポーズ・全シェイプの重み 0）にして、その記録（Reference）を返す。戻すのは `Reference.restore()`。

    - バインドポーズがあるとき: `dagPose -restore` で戻す。無いとき: 今のジョイントのローカル値を基準として記録し警告する
    - メッシュの blendShape（FC_* を含む全ターゲット）の重みを 0 にする。重みにつながっているもの（アニメ・expression など）は
      一時的に切り離す（戦わないように）。restore() で値・接続とも元へ戻る
    - 基準姿勢にできなければ（ジョイントの値を書けない等）元へ戻して ReferenceError_ を投げる
    """
    ref = Reference(meshes=[cmds.ls(m, long=True)[0] for m in meshes])
    ref.active = True
    try:
        _enter(ref, extra_joints)
    except Exception:
        ref.restore()
        raise
    return ref


def _enter(ref: Reference, extra_joints: Iterable[str]) -> None:
    # --- ジョイント: 控える → バインドポーズへ
    joints = mesh_joints(ref.meshes)
    for j in extra_joints:
        p = find_joint(j, joints)
        if p and p not in joints:
            joints.append(p)
    for j in joints:
        ref._saved_joints[j] = _read_raw(j)

    skins = [sk for m in ref.meshes for sk in skin_clusters(m)]
    poses: list[str] = []
    for sk in skins:
        for p in cmds.listConnections(sk + ".bindPose", source=True, destination=False, type="dagPose") or []:
            if p not in poses:
                poses.append(p)
    if poses:
        ref.has_bind_pose = True
        # まず測る: 顔のメッシュが今すでにバインドの形なら、どのジョイントにも触らない（体など他の部分を壊さない）。
        # 違うときだけ、スキンが持つバインド行列（bindPreMatrix）から顔の影響ジョイントをバインドの位置へ置く。
        # dagPose の restore は使わない（FBX 由来のシーンでは dagPose の中身が実際のバインドと食い違い、体のジョイントを壊すため）
        devs = [_deviation_from_orig(m) for m in ref.meshes if skin_clusters(m)]
        if any(d is None or d > BIND_TOLERANCE for d in devs):
            _place_influences_at_bind(ref, skins, joints)
    elif skins:
        ref.warnings.append("バインドポーズ（dagPose）が無いため、今のジョイントの姿勢を基準にします")
    for j in joints:
        t, q, s = read_local(j)
        ref.bones[short_name(j)] = BoneRef(j, t, q, s, _read_raw(j))

    # --- blendShape の重み: 控える → 切り離す → 0 にする
    for m in ref.meshes:
        nodes = blend_shapes(m)
        ref.nodes_by_mesh[m] = nodes
        for n in nodes:
            driven = driven_indices(n)  # 組み合わせ補正の出力: 切り離さない・0 にしない（元の重みが 0 なら自然に 0 になり、入力のシェイプを動かせば追従する）
            for idx in _all_indices(n):
                if idx in driven:
                    continue
                plug = weight_plug(n, idx)
                if plug in ref.weight_plugs:
                    continue
                ref.weight_plugs.append(plug)
                ref._saved_weights[plug] = cmds.getAttr(plug)
                for src in cmds.listConnections(plug, source=True, destination=False, plugs=True) or []:
                    cmds.disconnectAttr(src, plug)
                    ref._disconnected.append((src, plug))
                try:
                    if cmds.getAttr(plug) != 0.0:
                        cmds.setAttr(plug, 0.0)
                except RuntimeError:
                    raise ReferenceError_(f"{plug} を 0 にできません（ロックされていないか確認してください）")

    # --- 基準姿勢のメッシュが、スキン・変形を通す前の形と一致するか（バインドポーズが無い・バインド後にジョイントを動かした・
    #     他のデフォーマが効いているリグで、頭の動きなどを差分に焼き込まないため。S-3）
    for m in ref.meshes:
        others = _other_deformers(m)
        if others:
            ref.warnings.append(f"{short_name(m)} には blendShape・スキン以外のデフォーマ（{'、'.join(others[:3])}）が効いています。その変形も補正に焼き込まれることがあります")
    bad = []
    for m in ref.meshes:
        dev = _deviation_from_orig(m)
        if dev is not None and dev > BIND_TOLERANCE:
            bad.append(f"{short_name(m)}（最大 {dev:.3f} cm）")
    if bad:
        raise ReferenceError_(
            "基準姿勢の形が、スキン前のメッシュと合いません: " + "、".join(bad)
            + "。バインドポーズ（dagPose）があるか、バインドのあとにジョイントを動かしていないか、他のデフォーマが効いていないか確認してください"
        )


def _place_influences_at_bind(ref: Reference, skins: Sequence[str], joints: list[str]) -> None:
    """スキンの影響ジョイントのワールド行列を、バインド時の位置（bindPreMatrix の逆行列）へ置く。影響でないジョイントは触らない。

    親から順に、親の今のワールド行列との相対でローカル値を決めて write_local で書く（単位・jointOrient・回転順を通す）。
    書く前に必ず ref._saved_joints へ控える。"""
    bind: dict[str, om.MMatrix] = {}
    for sk in skins:
        for i in cmds.getAttr(sk + ".matrix", multiIndices=True) or []:
            src = cmds.listConnections(f"{sk}.matrix[{i}]", source=True, destination=False, type="joint")
            if not src:
                continue
            j = (cmds.ls(src[0], long=True) or [src[0]])[0]
            if j not in bind:
                bind[j] = om.MMatrix(cmds.getAttr(f"{sk}.bindPreMatrix[{i}]")).inverse()
    for j in sorted(bind, key=lambda x: x.count("|")):  # 親が先
        if j not in ref._saved_joints:
            ref._saved_joints[j] = _read_raw(j)
        if j not in joints:
            joints.append(j)
        p = _parent(j)
        if p:
            local = bind[j] * om.MMatrix(cmds.getAttr(p + ".worldInverseMatrix[0]"))
        else:
            local = bind[j]
        tm = om.MTransformationMatrix(local)
        q = tm.rotation(asQuaternion=True)
        t = tm.translation(om.MSpace.kTransform)
        sc = tm.scale(om.MSpace.kTransform)
        try:
            write_local(j, (t.x, t.y, t.z), quat_normalize((q.x, q.y, q.z, q.w)), (sc[0], sc[1], sc[2]))
        except RuntimeError as e:
            raise ReferenceError_(f"バインドポーズへ戻せません（{short_name(j)} の値が固定・接続されていないか確認してください）: {e}")


def _other_deformers(mesh: str) -> list[str]:
    """メッシュのヒストリにある、blendShape・スキン・tweak 以外のデフォーマ（クラスタ・ラティス・ノンリニアなど）。"""
    hist = cmds.listHistory(mesh_shape(mesh), pruneDagObjects=True) or []
    out = []
    for n in hist:
        try:
            inherited = cmds.nodeType(n, inherited=True) or []
        except RuntimeError:
            continue
        if "geometryFilter" in inherited and not ({"skinCluster", "blendShape", "tweak"} & set(inherited)):
            out.append(n)
    return out


BIND_TOLERANCE = 5e-3  # cm。基準姿勢（バインドポーズ・重み 0）の頂点が、スキンを通す前の形からこれ以上ずれていたら止める


def _deviation_from_orig(mesh: str) -> Optional[float]:
    """今の（スキンを通した）頂点と、スキンの影響を切った（envelope 0）頂点の最大のずれ（cm）。

    基準姿勢が本当にバインドポーズなら、スキンは単位行列で、ずれは 0。スキンが無い・envelope をつなげて / ロックされていて切れない
    ときは None（確認しない）。切った envelope は必ず元へ戻す（Undo には積まない）。"""
    skins = skin_clusters(mesh)
    if not skins:
        return None
    after = read_points(mesh)
    saved: dict[str, float] = {}
    with no_undo():
        try:
            for sk in skins:
                plug = sk + ".envelope"
                if cmds.listConnections(plug, source=True, destination=False) or cmds.getAttr(plug, lock=True):
                    return None
                saved[plug] = cmds.getAttr(plug)
                cmds.setAttr(plug, 0.0)
            before = read_points(mesh)
        finally:
            for plug, v in saved.items():
                cmds.setAttr(plug, v)
    if before.shape != after.shape or after.size == 0:
        return None
    return float(np.max(np.linalg.norm(before - after, axis=1)))


def reference_curve_plugs(ref: Reference, name: str) -> list[str]:
    """ソースの名前 → 動かす weight の plug（ref.meshes[0] の blendShape + 他のメッシュで同じターゲット名を持つノード）。見つからなければ空。

    他のメッシュ（`target.extraMeshes`）は、顔メッシュとは別のノードに同じ名前のターゲットを持っていれば同じ重みで動かす。
    """
    if name in ref._curve_cache:
        return ref._curve_cache[name]
    plugs: list[str] = []
    node, target = parse_curve_name(name)
    primary: Optional[CurveRef] = None
    for m in ref.meshes:
        primary = resolve_curve(name, m)
        if primary:
            break
    if primary:
        plugs.append(primary.plug)
        primary_meshes = {m for m, ns in ref.nodes_by_mesh.items() if primary.node in ns}
        for m in ref.meshes:
            if m in primary_meshes:
                continue
            for n in ref.nodes_by_mesh.get(m, []):
                idx = target_indices(n).get(primary.alias)
                if idx is not None and weight_plug(n, idx) not in plugs:
                    plugs.append(weight_plug(n, idx))
    ref._curve_cache[name] = plugs
    return plugs


# ---------------------------------------------------------------------------
# ターゲットの差分の読み書き
# ---------------------------------------------------------------------------


def _item_plug(node: str, group: int, geo: int = 0) -> str:
    return f"{node}.inputTarget[{geo}].inputTargetGroup[{group}].inputTargetItem[{DELTA_ITEM}]"


def _expand_components(comps: Optional[Sequence[str]]) -> list[int]:
    out: list[int] = []
    for c in comps or []:
        m = _COMPONENT.search(c)
        if m:
            a = int(m.group(1))
            b = int(m.group(2)) if m.group(2) else a
            out.extend(range(a, b + 1))
    return out


def _compress_components(indices: Sequence[int]) -> list[str]:
    """昇順の頂点番号 → `vtx[3]` `vtx[5:9]` の並び。"""
    out: list[str] = []
    i = 0
    n = len(indices)
    while i < n:
        j = i
        while j + 1 < n and indices[j + 1] == indices[j] + 1:
            j += 1
        out.append(f"vtx[{indices[i]}]" if i == j else f"vtx[{indices[i]}:{indices[j]}]")
        i = j + 1
    return out


def read_target_delta(node: str, target: str, geo: int = 0) -> Optional[tuple[list[int], np.ndarray]]:
    """ターゲットの差分 → (頂点番号の一覧, (k, 3) の差分)。ターゲットが無ければ None。"""
    idx = target_indices(node).get(target)
    if idx is None:
        return None
    item = _item_plug(node, idx, geo)
    pts = cmds.getAttr(item + ".inputPointsTarget") or []
    comps = _expand_components(cmds.getAttr(item + ".inputComponentsTarget"))
    arr = np.array([p[:3] for p in pts], dtype=np.float64).reshape(-1, 3)
    return comps, arr


def _discard_new_target(node: str, idx: int) -> None:
    """作りかけの番号（weight・group）を消す。何もなければ何もしない。"""
    for g in cmds.getAttr(node + ".inputTarget", multiIndices=True) or []:
        try:
            if idx in (cmds.getAttr(f"{node}.inputTarget[{g}].inputTargetGroup", multiIndices=True) or []):
                cmds.removeMultiInstance(f"{node}.inputTarget[{g}].inputTargetGroup[{idx}]", b=True)
        except RuntimeError:
            pass
    try:
        cmds.removeMultiInstance(weight_plug(node, idx), b=True)
    except RuntimeError:
        pass


def write_target_delta(
    node: str,
    target_name: str,
    deltas: np.ndarray,
    components: Sequence[int],
    geo: int = 0,
) -> tuple[int, bool]:
    """ターゲットの差分を直接書く（別メッシュを作らない）。(weight の番号, 新しく作ったか) を返す。

    - 無ければ番号の末尾（既存の最大 + 1）に作り、エイリアスをターゲット名にする。あれば差分だけを置き換える
    - 既存の他のターゲットの番号・エイリアス・差分には触らない（詰めない）
    - components は昇順の頂点番号、deltas は (k, 3)（オブジェクト空間）。k = 0 でもよい（空のターゲット）
    """
    comps = [int(c) for c in components]
    arr = np.asarray(deltas, dtype=np.float64).reshape(-1, 3)
    if len(comps) != len(arr):
        raise ValueError("components と deltas の数が違います")
    table = target_indices(node)
    created = target_name not in table
    if created:
        used = _all_indices(node)
        idx = (max(used) + 1) if used else 0
        cmds.setAttr(weight_plug(node, idx), 0.0)
    else:
        idx = table[target_name]
    item = _item_plug(node, idx, geo)
    try:
        cmds.setAttr(item + ".inputPointsTarget", len(arr), *[(float(x), float(y), float(z), 1.0) for x, y, z in arr], type="pointArray")
        ranges = _compress_components(comps)
        cmds.setAttr(item + ".inputComponentsTarget", len(ranges), *ranges, type="componentList")
        if created:
            cmds.aliasAttr(target_name, weight_plug(node, idx))
    except BaseException:
        if created:  # 新しく作りかけた番号を消す（名前の無いターゲットを残さない。S-11）
            _discard_new_target(node, idx)
        raise
    return idx, created


def item_plug(node: str, group: int, item: int = DELTA_ITEM, geo: int = 0) -> str:
    """inputTargetItem[item] の plug（item = 5000 + 重み × 1000。6000 = 重み 1 の形、それ未満は中間形）。"""
    return f"{node}.inputTarget[{geo}].inputTargetGroup[{group}].inputTargetItem[{item}]"


def item_numbers(node: str, target: str, geo: int = 0) -> list[int]:
    """ターゲットが持つ inputTargetItem の番号（中間形 + 6000）。ターゲットが無ければ空。"""
    idx = target_indices(node).get(target)
    if idx is None:
        return []
    return sorted(cmds.getAttr(f"{node}.inputTarget[{geo}].inputTargetGroup[{idx}].inputTargetItem", multiIndices=True) or [])


def read_item_delta(node: str, target: str, item: int, geo: int = 0) -> Optional[tuple[list[int], np.ndarray]]:
    """`read_target_delta` の、item を指定できる版（中間形の読み取り用）。"""
    idx = target_indices(node).get(target)
    if idx is None:
        return None
    plug = item_plug(node, idx, item, geo)
    pts = cmds.getAttr(plug + ".inputPointsTarget") or []
    comps = _expand_components(cmds.getAttr(plug + ".inputComponentsTarget"))
    return comps, np.array([p[:3] for p in pts], dtype=np.float64).reshape(-1, 3)


def write_item_delta(node: str, target: str, item: int, deltas: np.ndarray, components: Sequence[int], geo: int = 0) -> None:
    """既にあるターゲットの inputTargetItem[item]（中間形など）の差分を直接書く。ターゲットが無ければ ValueError。"""
    idx = target_indices(node).get(target)
    if idx is None:
        raise ValueError(f"ターゲット {target} が {node} にありません")
    comps = [int(c) for c in components]
    arr = np.asarray(deltas, dtype=np.float64).reshape(-1, 3)
    if len(comps) != len(arr):
        raise ValueError("components と deltas の数が違います")
    plug = item_plug(node, idx, item, geo)
    cmds.setAttr(plug + ".inputPointsTarget", len(arr), *[(float(x), float(y), float(z), 1.0) for x, y, z in arr], type="pointArray")
    ranges = _compress_components(comps)
    cmds.setAttr(plug + ".inputComponentsTarget", len(ranges), *ranges, type="componentList")


def _empty_target(node: str, idx: int) -> None:
    """ターゲットのすべての item（本体・中間形）の頂点の差分を空にする。要素ごと消す前に行うと、Undo で中身が戻る（消した要素の復元は中身を持たない）。"""
    for g in cmds.getAttr(node + ".inputTarget", multiIndices=True) or []:
        if idx not in (cmds.getAttr(f"{node}.inputTarget[{g}].inputTargetGroup", multiIndices=True) or []):
            continue
        items = cmds.getAttr(f"{node}.inputTarget[{g}].inputTargetGroup[{idx}].inputTargetItem", multiIndices=True) or []
        for item in items:
            plug = f"{node}.inputTarget[{g}].inputTargetGroup[{idx}].inputTargetItem[{item}]"
            cmds.setAttr(plug + ".inputPointsTarget", 0, type="pointArray")
            cmds.setAttr(plug + ".inputComponentsTarget", 0, type="componentList")


def delete_targets(node: str, names: Iterable[str], sculpt_prefix: str = naming.DEFAULT_SCULPT_PREFIX) -> list[str]:
    """ターゲットを消す。**`FC_*` / `fcs_*`（sculpt_prefix）だけ**。それ以外の名前が 1 つでも入っていたら何も消さずに ValueError。

    番号は詰めない（残りのターゲットの番号は変わらない）。無い名前は飛ばす。消した名前を返す。
    """
    names = list(names)
    bad = [n for n in names if not (naming.is_fc_name(n) or naming.is_sculpt_name(n, sculpt_prefix))]
    if bad:
        raise ValueError(f"FC_* / {sculpt_prefix}* 以外のターゲットは消せません: {bad}")
    table = target_indices(node)
    removed: list[str] = []
    for n in names:
        idx = table.get(n)
        if idx is None:
            continue
        combos = list(dict.fromkeys(cmds.listConnections(weight_plug(node, idx), source=True, destination=False, type="combinationShape") or []))
        if combos:
            cmds.delete(combos)  # 組み合わせ補正の節（このターゲット専用）
        _empty_target(node, idx)  # 先に中身を空にする（Undo で要素だけでなく頂点の差分も戻る。S-7）
        cmds.aliasAttr(f"{node}.{n}", remove=True)
        for g in cmds.getAttr(node + ".inputTarget", multiIndices=True) or []:
            if idx in (cmds.getAttr(f"{node}.inputTarget[{g}].inputTargetGroup", multiIndices=True) or []):
                cmds.removeMultiInstance(f"{node}.inputTarget[{g}].inputTargetGroup[{idx}]", b=True)
        cmds.removeMultiInstance(weight_plug(node, idx), b=True)
        removed.append(n)
    return removed


def fc_targets(mesh: str, sculpt_prefix: str = naming.DEFAULT_SCULPT_PREFIX) -> list[CurveRef]:
    """メッシュの blendShape にある FC_* / fcs_* のターゲット。"""
    return [
        c
        for c in list_curves(mesh, include_fc=True)
        if naming.is_fc_name(c.alias) or naming.is_sculpt_name(c.alias, sculpt_prefix)
    ]


# ---------------------------------------------------------------------------
# ベイクの状態
# ---------------------------------------------------------------------------


def get_bake_state(node: str) -> dict[str, str]:
    """blendShape ノードの `tdFacialBakeState`（morph 名 → 焼いたときのポーズのハッシュ）。無い・壊れていれば空。"""
    if not cmds.objExists(node) or not cmds.attributeQuery(BAKE_STATE_ATTR, node=node, exists=True):
        return {}
    try:
        data = json.loads(cmds.getAttr(f"{node}.{BAKE_STATE_ATTR}") or "{}")
    except ValueError:
        return {}
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def set_bake_state(node: str, state: dict[str, str]) -> None:
    if not cmds.attributeQuery(BAKE_STATE_ATTR, node=node, exists=True):
        cmds.addAttr(node, longName=BAKE_STATE_ATTR, dataType="string")
    cmds.setAttr(f"{node}.{BAKE_STATE_ATTR}", json.dumps(state, sort_keys=True, ensure_ascii=False), type="string")


def get_bake_exclude(node: str) -> dict[str, str]:
    """blendShape ノードの `tdFacialBakeExclude`（morph 名 → ベイク時の除外パターンの指紋）。無い・壊れていれば空（= 不明）。"""
    if not cmds.objExists(node) or not cmds.attributeQuery(BAKE_EXCLUDE_ATTR, node=node, exists=True):
        return {}
    try:
        data = json.loads(cmds.getAttr(f"{node}.{BAKE_EXCLUDE_ATTR}") or "{}")
    except ValueError:
        return {}
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def set_bake_exclude(node: str, record: dict[str, str]) -> None:
    if not cmds.attributeQuery(BAKE_EXCLUDE_ATTR, node=node, exists=True):
        cmds.addAttr(node, longName=BAKE_EXCLUDE_ATTR, dataType="string")
    cmds.setAttr(f"{node}.{BAKE_EXCLUDE_ATTR}", json.dumps(record, sort_keys=True, ensure_ascii=False), type="string")


def bake_exclude_for(mesh: str) -> dict[str, str]:
    """メッシュの blendShape ノードすべての除外パターンの記録を合わせたもの（検証に渡す）。"""
    out: dict[str, str] = {}
    for n in reversed(blend_shapes(mesh)):
        out.update(get_bake_exclude(n))
    return out


def bake_state_for(mesh: str) -> dict[str, str]:
    """メッシュの blendShape ノードすべての状態を合わせたもの（検証に渡す）。"""
    out: dict[str, str] = {}
    for n in reversed(blend_shapes(mesh)):
        out.update(get_bake_state(n))
    return out


# ---------------------------------------------------------------------------
# 検証に渡すシーンの事情
# ---------------------------------------------------------------------------


def build_scene_info(doc: Document) -> validate.SceneInfo:
    """`validate.validate` に渡す SceneInfo を、doc.target のメッシュから集める。

    メッシュが無い・見つからないときは各欄が None（その検査は飛ばされる）。
    curves は FC_* を除くシェイプ名（fcs_* は含む）、targets は FC_* / fcs_*、bones・bone_parents は顔メッシュのスキンの骨格。
    """
    info = validate.SceneInfo()
    if doc.target is None or not doc.target.mesh:
        return info
    try:
        mesh = resolve_mesh(doc.target.mesh)
    except ValueError:
        return info
    prefix = doc.sculpt_shapes.prefix if doc.sculpt_shapes is not None else naming.DEFAULT_SCULPT_PREFIX
    info.curves = curve_names(mesh)
    joints = mesh_joints([mesh])
    info.bones = [short_name(j) for j in joints]
    info.bone_parents = joint_parents(joints)
    tgs = fc_targets(mesh, prefix)
    info.targets = [t.alias for t in tgs]
    infos: dict[str, validate.TargetInfo] = {}
    for t in tgs:
        if naming.is_fc_name(t.alias):
            n = len(cmds.getAttr(_item_plug(t.node, t.index, geometry_index(t.node, mesh)) + ".inputPointsTarget") or [])
            infos[t.alias] = validate.TargetInfo(vertex_count=n, empty=(n == 0))
    info.target_info = infos
    info.mesh_infos = _mesh_infos(doc, prefix) if doc.target.lod_meshes else None  # LOD のメッシュが無ければ集めない（検査も飛ばす）
    return info


def _mesh_infos(doc: Document, prefix: str) -> dict[str, validate.MeshInfo]:
    """文書に書いたメッシュ（顔・extraMeshes・LOD）→ シーンでの事情。"""
    out: dict[str, validate.MeshInfo] = {}
    for name in doc.target.all_meshes() if doc.target is not None else []:
        try:
            m = resolve_mesh(name)
        except ValueError:
            out[name] = validate.MeshInfo(exists=False)
            continue
        out[name] = validate.MeshInfo(
            exists=True,
            resolved=m,
            targets=[t.alias for t in list_curves(m)],
            fc_targets=[t.alias for t in fc_targets(m, prefix) if naming.is_fc_name(t.alias)],
        )
    return out
