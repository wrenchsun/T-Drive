"""シェイプ作成支援（Maya 依存・Qt なし。docs/14 §5.6、docs/15 §4.5）。

Maya 標準の blendShape・スカルプトの上に載る道具だけを用意する（リグは作らない）。どれも「ターゲットの差分を読む / 書く」共通関数
（`scene.read_target_delta` / `write_target_delta` / `read_item_delta` / `write_item_delta`）の上に作る。

| 機能 | 関数 | 要点 |
|---|---|---|
| この角度で彫る | `check_head_at_reference` / `sculpt_prepare` / `sculpt_enter` / `sculpt_exit` / `finish_sculpt` | 全頂点 0 の `fcs_*` を作り `sculptTarget -e -target <番号>`。ポーズへの記録は session |
| 左右に分ける | `split_lr` | 顔の左右（鏡映の軸。既定 X）の位置で smoothstep の重みを付けて 2 つに分ける。L + R = 元 |
| ミラー | `symmetry_map` / `mirror_shape` | 位置の最近傍で対応表（キャッシュ）。対応が取れない頂点があれば**選択して止める** |
| 中間形・誇張 | `add_inbetween` / `make_exaggeration` | 今のシーンの形を `inputTargetItem[5000 + 重み × 1000]` へ。誇張は別ターゲット `<名前>_Ex` |
| 組み合わせ補正 | `create_combo` | `combinationShape`（2 入力の積）で駆動される `fcs_combo_<a>__<b>` |
| 別メッシュへ写す | `transfer_shapes` | 頂点・順序が同じなら複写、違えば proximityWrap で転写（一時ノードは必ず消す） |
| 整理 | `clean_micro` / `audit` / `missing_standard` | 微小な差分の掃除・空 / 未使用の一覧・不足一覧 |

## Maya 2026 で確かめたこと（2026-10-03、mayapy）
- `cmds.sculptTarget(<blendShape>, edit=True, target=<番号>)` で彫り対象になる（`target=-1` で終了）。**blendShape ノード名だけを渡す**。
  `query=True` は何も返さない（None）ので、状態は `<node>.inputTarget[0].sculptTargetIndex`（-1 = 彫っていない）で読む。
  Move ツール・`cmds.move` で動かした頂点は、そのターゲットの差分に入る（スキンの後の動きは Maya が逆変換して書く）
- **差分が 0 件（`inputPointsTarget` が空）のターゲットを彫り対象にして頂点を動かすと Maya が落ちる**。そのため新しい彫り用ターゲットは
  全頂点（差分 0）で作り、終了時に 0 の頂点を捨てる
- 中間形は `inputTargetItem[5000 + 重み × 1000]`（6000 = 重み 1）へ `inputPointsTarget` / `inputComponentsTarget` を書けば効く
- 組み合わせ: `cmds.combinationShape(blendShape=<node>, combinationTargetIndex=<番号>, driverTargetIndex=[a, b], combineMethod=0)`
  （**edit フラグなしの作成モード**。`-blendShape` が必須）。`combinationShape` ノードができ、`outputWeight → weight[<番号>]` につながる。
  積（0.5 × 0.5 → 0.25）。`combineMethod=0` が積
- proximityWrap はプラグインの読み込み不要（`cmds.deformer(type="proximityWrap")` + `cmds.proximityWrap(w, e=True, addDrivers=[shape])`）

## 約束
- 元からあるターゲット（`FC_*` / `fcs_*` / このツールで作った印のどれでもないもの）は、利用者が**明示して**そのツールを選んだときだけ変える
  （`confirm_original=True`）。元の差分は書き換えず新しい名前で作るツールがほとんど
- どの操作も Maya の Undo 1 回（`undo_chunk`。try / finally で必ず閉じる）
- このモジュールは Maya にコールバック・オブジェクトを登録しない（受け身の関数だけ）
"""

from __future__ import annotations

import contextlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional, Sequence

import numpy as np
from maya import cmds
from maya.api import OpenMaya as om

from . import scene
from .core import naming, space
from .core import validate as V
from .core import profile as profile_mod
from .core.model import Document

MADE_ATTR = "tdFacialMadeShapes"  # blendShape ノードの文字列アトリビュート（JSON: このツールで作ったターゲット名の一覧）
COMBO_INFIX = naming.COMBO_INFIX  # fcs_combo_<a>__<b>
EPS = 1e-7  # cm。これ未満の差分は「差分なし」（書き込みの下限）
PRUNE_AFTER_SCULPT = 1e-6  # cm。彫り終わりに捨てる下限
HEAD_EPS_T = 1e-4
HEAD_EPS_Q = 1e-5

TAG_ORIGINAL = "original"
TAG_FC = "fc"
TAG_SCULPT = "sculpt"
TAG_COMBO = "combo"
TAG_MADE = "made"
TAG_LABEL = {TAG_ORIGINAL: "元から", TAG_FC: "FC_", TAG_SCULPT: "fcs_", TAG_COMBO: "組み合わせ", TAG_MADE: "作ったもの"}

BOTH_MARKERS = ("_LR", "_Both", "_both", "_Mid", "_Center", "_C")  # 左右一体のシェイプ名の末尾（分けるときの基の名前から外す）
NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ShapeError(RuntimeError):
    """道具を使えない（前提が足りない・名前の衝突など）。メッセージはそのまま画面に出せる日本語。code は画面が分岐するための印。"""

    def __init__(self, message: str, code: str = "error") -> None:
        super().__init__(message)
        self.code = code


class AsymmetryStop(ShapeError):
    """ミラーで、対応が取れない頂点があって止めた。unmatched = その頂点番号（ビューポートでは選択済み）。"""

    def __init__(self, message: str, unmatched: Sequence[int]) -> None:
        super().__init__(message, "asymmetric")
        self.unmatched = list(unmatched)


@dataclass
class ShapeResult:
    """道具の結果。画面はこれをそのまま 1 行 + 詳細で見せる。"""

    ok: bool = True
    message: str = ""
    created: list[str] = field(default_factory=list)
    replaced: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> str:
        parts = []
        if self.created:
            parts.append("作成 " + "・".join(self.created))
        if self.replaced:
            parts.append("置き換え " + "・".join(self.replaced))
        if self.removed:
            parts.append("削除 " + "・".join(self.removed))
        head = self.message or "完了"
        return head + ("（" + " / ".join(parts) + "）" if parts else "")


@dataclass
class ShapeCtx:
    """道具が働く先（顔メッシュとその blendShape）と、Document の設定。"""

    mesh: str  # 長い名前
    node: str  # 書き込み先の blendShape
    geo: int
    nverts: int
    prefix: str = naming.DEFAULT_SCULPT_PREFIX
    suffix_l: str = "_L"
    suffix_r: str = "_R"
    axis: int = 0  # 顔の左右の軸（0 = X）
    threshold: float = 0.001  # cm
    base_bone: str = ""  # 基準ボーン（頭）。中間形・誇張形を作るとき、頭が基準姿勢にあるかの確認に使う（S-5）

    def made(self) -> list[str]:
        return get_made(self.node)


@dataclass
class ShapeInfo:
    """一覧の 1 行。"""

    name: str
    node: str
    index: int
    tag: str
    vertex_count: int  # 差分が 0 でない頂点の数
    items: list[int] = field(default_factory=list)  # 中間形を含む inputTargetItem の番号
    point: Optional[tuple[str, int, int]] = None  # fcs_* のとき (レイヤー名, row, col)
    driven: bool = False  # 組み合わせ補正に駆動されている

    @property
    def empty(self) -> bool:
        return self.vertex_count == 0

    @property
    def tag_label(self) -> str:
        return TAG_LABEL[self.tag]

    @property
    def inbetweens(self) -> list[int]:
        return [i for i in self.items if i != scene.DELTA_ITEM]


# ---------------------------------------------------------------------------
# 共通の道具
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def undo_chunk(name: str):
    """Maya の Undo の 1 区切り（try / finally で必ず閉じる）。入れ子でもよい（外側の 1 回にまとまる）。"""
    cmds.undoInfo(openChunk=True, chunkName=name)
    try:
        yield
    finally:
        cmds.undoInfo(closeChunk=True)




def make_ctx(doc: Document, create: bool = True) -> ShapeCtx:
    """Document の顔メッシュと blendShape から ShapeCtx を作る。create=True で、blendShape が無ければ `tdFacial_<mesh>` を作る。"""
    if doc.target is None or not doc.target.mesh:
        raise ShapeError("対象のメッシュ（セットアップタブ）が設定されていません", "no_mesh")
    try:
        mesh = scene.resolve_mesh(doc.target.mesh)
    except ValueError as e:
        raise ShapeError(str(e), "no_mesh") from e
    node = scene.primary_blend_shape(mesh, create=create)
    if node is None:
        raise ShapeError("顔メッシュに blendShape がありません", "no_blend_shape")
    return ShapeCtx(
        mesh=mesh,
        node=node,
        geo=scene.geometry_index(node, mesh),
        nverts=scene.vertex_count(mesh),
        prefix=doc.sculpt_shapes.prefix if doc.sculpt_shapes is not None else naming.DEFAULT_SCULPT_PREFIX,
        suffix_l=doc.mirror.suffix_l or "_L",
        suffix_r=doc.mirror.suffix_r or "_R",
        axis=space.mirror_axis_index(doc.mirror.bone_axis),
        threshold=doc.bake.delta_threshold if doc.bake is not None else 0.001,
        base_bone=doc.grid.base_bone or "",
    )


def get_made(node: str) -> list[str]:
    """このツールで作ったターゲット名（`fcs_*` / `FC_*` 以外）。"""
    if not cmds.objExists(node) or not cmds.attributeQuery(MADE_ATTR, node=node, exists=True):
        return []
    try:
        v = json.loads(cmds.getAttr(f"{node}.{MADE_ATTR}") or "[]")
    except ValueError:
        return []
    return [str(x) for x in v] if isinstance(v, list) else []


def _set_made(node: str, names: Sequence[str]) -> None:
    if not cmds.attributeQuery(MADE_ATTR, node=node, exists=True):
        cmds.addAttr(node, longName=MADE_ATTR, dataType="string")
    cmds.setAttr(f"{node}.{MADE_ATTR}", json.dumps(sorted(set(names)), ensure_ascii=False), type="string")


def add_made(ctx: ShapeCtx, names: Iterable[str]) -> None:
    """作ったターゲットの印を付ける（`FC_*` / `fcs_*` は名前で分かるので記録しない）。"""
    cur = get_made(ctx.node)
    new = [n for n in names if not (naming.is_fc_name(n) or naming.is_sculpt_name(n, ctx.prefix)) and n not in cur]
    if new:
        _set_made(ctx.node, [*cur, *new])


def remove_made(node: str, names: Iterable[str]) -> None:
    cur = get_made(node)
    gone = set(names)
    if any(n in gone for n in cur):
        _set_made(node, [n for n in cur if n not in gone])


def tag_of(ctx: ShapeCtx, name: str, made: Optional[Iterable[str]] = None) -> str:
    if naming.is_fc_name(name):
        return TAG_FC
    if naming.is_sculpt_name(name, ctx.prefix):
        return TAG_COMBO if name.startswith(ctx.prefix + COMBO_INFIX) else TAG_SCULPT
    return TAG_MADE if name in (set(made) if made is not None else set(ctx.made())) else TAG_ORIGINAL


def check_new_name(name: str) -> None:
    if not name:
        raise ShapeError("名前が空です", "bad_name")
    if not NAME_RE.match(name):
        raise ShapeError(f"名前「{name}」は使えません（英数字と _ だけ。数字から始められません）", "bad_name")


def _dest_check(ctx: ShapeCtx, name: str, allow_original: bool, made: Optional[set[str]] = None) -> bool:
    """書き込み先の名前を確かめる。既にあるなら True（置き換え）。元からあるターゲットは allow_original でなければ止める。"""
    check_new_name(name)
    if name not in scene.target_indices(ctx.node):
        return False
    if tag_of(ctx, name, made) == TAG_ORIGINAL and not allow_original:
        raise ShapeError(
            f"{name} は元からあるシェイプなので上書きしません（上書きするなら確認が必要です）",
            "exists_original",
        )
    return True


def dense_delta(ctx_or_node, name: str, nverts: int, geo: int = 0, item: int = scene.DELTA_ITEM) -> Optional[np.ndarray]:
    """ターゲットの差分を全頂点の (N, 3) で返す（差分が無い頂点は 0）。ターゲットが無ければ None。"""
    node = ctx_or_node.node if isinstance(ctx_or_node, ShapeCtx) else ctx_or_node
    r = scene.read_item_delta(node, name, item, geo)
    if r is None:
        return None
    comps, arr = r
    out = np.zeros((nverts, 3))
    n = min(len(comps), len(arr))
    if n:
        out[np.asarray(comps[:n], dtype=int)] = arr[:n]
    return out


def _write_dense(ctx: ShapeCtx, name: str, dense: np.ndarray, item: int = scene.DELTA_ITEM, threshold: float = EPS) -> int:
    """全頂点の差分から、threshold 以上の頂点だけを書く（無ければターゲットを作る）。書いた頂点数を返す。"""
    idx = np.nonzero(np.linalg.norm(dense, axis=1) >= threshold)[0]
    if item == scene.DELTA_ITEM:
        scene.write_target_delta(ctx.node, name, dense[idx], idx.tolist(), ctx.geo)
    else:
        scene.write_item_delta(ctx.node, name, item, dense[idx], idx.tolist(), ctx.geo)
    return int(len(idx))


def _all_items(ctx: ShapeCtx, name: str) -> list[int]:
    return scene.item_numbers(ctx.node, name, ctx.geo) or [scene.DELTA_ITEM]


def _copy_items(ctx: ShapeCtx, src: str, dst: str, fn) -> int:
    """src の全 item（本体 → 中間形の順）の差分に fn(全頂点の (N, 3)) をかけて dst へ書く。本体の頂点数を返す。"""
    count = 0
    for item in sorted(_all_items(ctx, src), key=lambda i: i != scene.DELTA_ITEM):
        d = dense_delta(ctx, src, ctx.nverts, ctx.geo, item)
        if d is None:
            continue
        c = _write_dense(ctx, dst, fn(d), item)
        if item == scene.DELTA_ITEM:
            count = c
    return count


def rest_points(meshes: Sequence[str] | str, head_now: Optional[dict] = None) -> np.ndarray:
    """基準姿勢（バインドポーズ・全シェイプ 0）の頂点（オブジェクト空間）。複数のメッシュを渡すと最初のメッシュの分。

    head_now（頭とその親の、基準姿勢に入る前のローカル値 `{短い名前: (t, q, s)}`）を渡すと、基準姿勢と比べて動いていれば
    ShapeError("head_moved")。中間形・誇張形が頭の動きを形に焼き込まないため（S-5）。"""
    ms = [meshes] if isinstance(meshes, str) else list(meshes)
    try:
        with scene.enter_reference_pose(ms) as ref:
            for name, (t, q, _s) in (head_now or {}).items():
                b = ref.bones.get(name)
                if b is not None and (
                    max(abs(x - y) for x, y in zip(t, b.t)) > HEAD_EPS_T or abs(scene.quat_dot(q, b.q)) < 1.0 - HEAD_EPS_Q
                ):
                    raise ShapeError(
                        f"頭（{name}）が基準姿勢から動いています。中間形・誇張形はスキンの前の空間で作るため、"
                        "頭は基準姿勢のポーズ（頭のずれを 0）で実行してください（目・口などのボーンのずらしは使えます）",
                        "head_moved",
                    )
            return scene.read_points(ms[0])
    except scene.ReferenceError_ as e:
        raise ShapeError(f"基準姿勢にできません: {e}", "no_reference") from e


def _head_locals(ctx: "ShapeCtx") -> dict:
    """基準ボーンとその親（短い名前）の今のローカル値。基準ボーンが分からなければ空。"""
    if not ctx.base_bone:
        return {}
    joints = scene.mesh_joints([ctx.mesh])
    parents = scene.joint_parents(joints)
    chain = [ctx.base_bone]
    while chain[-1] in parents and parents[chain[-1]] not in chain:
        chain.append(parents[chain[-1]])
    out = {}
    for name in chain:
        path = scene.find_joint(name, joints)
        if path:
            out[name] = scene.read_local(path)
    return out


def current_delta(ctx: ShapeCtx, exclude_own: Optional[str] = None) -> np.ndarray:
    """今のシーンの形 − 基準姿勢の形（全頂点の (N, 3)）。exclude_own を渡すと、そのターゲット自身の重みは 0 にして測る（終わったら戻す）。

    ポーズ（編集状態で当てたシェイプ・ボーンのずらし）がそのまま入る。ベイク（`bake.pose_to_shape`）と同じ「変形後 − 何も当てない形」。
    """
    own_plug = None
    own_value = 0.0
    if exclude_own:
        idx = scene.target_indices(ctx.node).get(exclude_own)
        if idx is not None:
            own_plug = scene.weight_plug(ctx.node, idx)
            if not cmds.listConnections(own_plug, source=True, destination=False):
                own_value = cmds.getAttr(own_plug)
                cmds.setAttr(own_plug, 0.0)
            else:
                own_plug = None
    try:
        snapshot = scene.read_points(ctx.mesh)
        head_now = _head_locals(ctx)
    finally:
        if own_plug is not None:
            cmds.setAttr(own_plug, own_value)
    return snapshot - rest_points(ctx.mesh, head_now)


def select_vertices(mesh: str, ids: Sequence[int]) -> None:
    """頂点をビューポートで選択する（空なら選択を解除）。"""
    ids = sorted(int(i) for i in ids)
    if not ids:
        cmds.select(clear=True)
        return
    cmds.select([f"{mesh}.{r}" for r in scene._compress_components(ids)], replace=True)


# ---------------------------------------------------------------------------
# 一覧
# ---------------------------------------------------------------------------


def list_shapes(ctx: ShapeCtx) -> list[ShapeInfo]:
    """書き込み先の blendShape のターゲット一覧（番号順）。組み合わせ補正に駆動されているものも含む。"""
    made = set(ctx.made())
    driven = scene.driven_indices(ctx.node)
    out: list[ShapeInfo] = []
    for name, idx in sorted(scene.target_indices(ctx.node).items(), key=lambda kv: kv[1]):
        d = dense_delta(ctx, name, ctx.nverts, ctx.geo)
        n = int(np.count_nonzero(np.linalg.norm(d, axis=1) >= EPS)) if d is not None else 0
        sp = naming.parse_sculpt_name(name, ctx.prefix)
        out.append(
            ShapeInfo(
                name=name,
                node=ctx.node,
                index=idx,
                tag=tag_of(ctx, name, made),
                vertex_count=n,
                items=scene.item_numbers(ctx.node, name, ctx.geo),
                point=(sp.layer, sp.row, sp.col) if sp is not None else None,
                driven=idx in driven,
            )
        )
    return out


# ---------------------------------------------------------------------------
# この角度で彫る（F2-1）
# ---------------------------------------------------------------------------


def check_head_at_reference(doc: Document, ref: scene.Reference, pose_bones: dict) -> None:
    """彫れる状態か確かめる。頭（grid.base_bone）とその親が基準姿勢のままであること（ポーズ側の他のボーンのずらしは可）。

    彫るのはスキンの前の空間なので、頭が動いていると Maya の逆変換に頼ることになり、結果がずれる。満たさなければ ShapeError。
    """
    base = doc.grid.base_bone
    if not base or base not in ref.bones:
        return  # 基準ボーンが見つからない（検証が知らせる）。止めない
    parents = scene.joint_parents([b.path for b in ref.bones.values()])
    chain = [base]
    while chain[-1] in parents and parents[chain[-1]] not in chain:
        chain.append(parents[chain[-1]])
    for name in chain:
        off = pose_bones.get(name)
        if off is not None and (
            max(abs(v) for v in off.t) > HEAD_EPS_T
            or max(abs(v) for v in off.r[:3]) > HEAD_EPS_Q
            or max(abs(v - 1.0) for v in off.s) > HEAD_EPS_Q
        ):
            raise ShapeError(
                f"このポーズは頭（{name}）を動かしているので、そのままでは彫れません。"
                "彫る形はスキンの前の空間で作るため、頭は基準姿勢のままにしてください（目・口などのボーンのずらしは使えます）。"
                f"ポーズで {name} のずれを 0 に戻してから押してください",
                "head_moved",
            )
    for name in chain:
        b = ref.bones.get(name)
        if b is None:
            continue
        t, q, _s = scene.read_local(b.path)
        if max(abs(x - y) for x, y in zip(t, b.t)) > HEAD_EPS_T or abs(scene.quat_dot(q, b.q)) < 1.0 - HEAD_EPS_Q:
            raise ShapeError(f"頭（{name}）が基準姿勢にありません（ジョイントの値がロック・接続されていないか確認してください）", "head_moved")


def sculpt_active_index(node: str, geo: int = 0) -> int:
    """今スカルプト対象のターゲット番号（-1 = 彫っていない）。`sculptTarget -q` は何も返さないので属性で読む。"""
    try:
        return int(cmds.getAttr(f"{node}.inputTarget[{geo}].sculptTargetIndex"))
    except (RuntimeError, ValueError):
        return -1


def sculpt_prepare(ctx: ShapeCtx, name: str, zero: bool = False) -> tuple[int, bool]:
    """彫り用のターゲットを用意する。無ければ全頂点（差分 0）で作る。あれば全頂点の形（差分 0 の頂点も含む）に書き直す。

    全頂点で持たせるのは、差分 0 件のターゲットを彫り対象にして頂点を動かすと Maya が落ちるため。終了時に `finish_sculpt` が 0 の頂点を捨てる。
    zero=True で既にあっても差分を捨てて作り直す。(番号, 新しく作ったか) を返す。
    """
    table = scene.target_indices(ctx.node)
    created = name not in table
    d = None if (created or zero) else dense_delta(ctx, name, ctx.nverts, ctx.geo)
    if d is None:
        d = np.zeros((ctx.nverts, 3))
    cur = scene.read_target_delta(ctx.node, name, ctx.geo) if not created else None
    if created or cur is None or len(cur[0]) != ctx.nverts or zero:
        scene.write_target_delta(ctx.node, name, d, list(range(ctx.nverts)), ctx.geo)
    return scene.target_indices(ctx.node)[name], created


def sculpt_enter(ctx: ShapeCtx, name: str, select_mesh: bool = True) -> bool:
    """ターゲットを Maya のスカルプト対象にする。Maya のツールの画面があれば Sculpt Geometry ツールも有効にする（無ければ飛ばす）。

    戻り: ツールを有効にしたか。
    """
    idx = scene.target_indices(ctx.node).get(name)
    if idx is None:
        raise ShapeError(f"ターゲット {name} がありません", "no_target")
    cmds.sculptTarget(ctx.node, edit=True, target=idx)
    if select_mesh:
        cmds.select(ctx.mesh, replace=True)
    return activate_sculpt_tool()


def activate_sculpt_tool() -> bool:
    """Sculpt Geometry ツールを有効にする。バッチ（mayapy）・ツールの画面が無いときは何もしない（False）。"""
    try:
        if cmds.about(batch=True):
            return False
        from maya import mel

        mel.eval("SculptGeometryTool")
        return True
    except Exception:  # noqa: BLE001  ツールが開けなくても彫り自体は動く
        return False


def sculpt_exit(ctx_or_node, geo: Optional[int] = None) -> None:
    """スカルプト対象を解除する（`sculptTarget -e -target -1`）。彫っていなくてもよい。"""
    node = ctx_or_node.node if isinstance(ctx_or_node, ShapeCtx) else ctx_or_node
    if cmds.objExists(node):
        cmds.sculptTarget(node, edit=True, target=-1)


def finish_sculpt(ctx: ShapeCtx, name: str) -> int:
    """彫り終わり: 差分が 0 の頂点を捨てて書き直す。残った頂点の数を返す（0 = 何も彫っていない）。"""
    d = dense_delta(ctx, name, ctx.nverts, ctx.geo)
    if d is None:
        return 0
    return _write_dense(ctx, name, d, threshold=PRUNE_AFTER_SCULPT)


# ---------------------------------------------------------------------------
# 左右に分ける（F2-2a）
# ---------------------------------------------------------------------------


def base_name(name: str) -> str:
    """左右一体のシェイプ名から、末尾の「一体」の印（`_LR` `_Both` など）を外した名前。"""
    for m in BOTH_MARKERS:
        if name.endswith(m) and len(name) > len(m):
            return name[: -len(m)]
    return name


def side_weights(x: np.ndarray, centre: float, width: float) -> np.ndarray:
    """+軸側（L）の重み（0〜1）。中央 ± width/2 で smoothstep。width = 0 は段差（ちょうど中央は 0.5）。R の重みは 1 − これ。"""
    if width <= 0:
        return np.where(x > centre, 1.0, np.where(x < centre, 0.0, 0.5))
    t = np.clip((x - (centre - width / 2.0)) / width, 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def split_lr(
    ctx: ShapeCtx,
    src: str,
    name_l: str,
    name_r: str,
    width: float = 1.0,
    centre: float = 0.0,
    overwrite_originals: bool = False,
    rest: Optional[np.ndarray] = None,
) -> ShapeResult:
    """左右一体のシェイプ src を `name_l` / `name_r` に分ける（src は変えない）。L + R = src（中間形も同じ重みで分ける）。

    顔の左右の軸は文書の鏡映の軸（`doc.mirror.bone_axis`。既定 X）で、メッシュのオブジェクト空間の基準姿勢の頂点の位置で見る
    （頭の向きは考えない: メッシュの軸が顔の左右に合っている前提）。
    +軸の側が L。width は中央のぼかし幅（cm。中央 ± width/2 で smoothstep）。
    """
    if src not in scene.target_indices(ctx.node):
        raise ShapeError(f"シェイプ {src} がありません", "no_target")
    if name_l == name_r or src in (name_l, name_r):
        raise ShapeError("左・右の名前は、元の名前とも互いにも別にしてください", "bad_name")
    res = ShapeResult()
    made = set(ctx.made())
    rep = [_dest_check(ctx, n, overwrite_originals, made) for n in (name_l, name_r)]
    with undo_chunk("tdFacialSplitLR"):
        rp = rest if rest is not None else rest_points(ctx.mesh)
        wl = side_weights(rp[:, ctx.axis], centre, width)
        counts = {}
        for n, w in ((name_l, wl), (name_r, 1.0 - wl)):
            counts[n] = _copy_items(ctx, src, n, lambda d, w=w: d * w[:, None])
        add_made(ctx, [name_l, name_r])
        for n, was in zip((name_l, name_r), rep):
            (res.replaced if was else res.created).append(n)
        res.message = f"{src} を {name_l} / {name_r} に分けました（中央のぼかし {width:g} cm）"
        res.stats = {"width": width, "centre": centre, "axis": "XYZ"[ctx.axis], "counts": counts}
        ax = "XYZ"[ctx.axis]
        res.notes.append(f"顔の左右はメッシュのオブジェクト空間の {ax}（+{ax} = L）として分けています（セットアップタブの「ボーンの反転軸」）")
        return res


# ---------------------------------------------------------------------------
# ミラー（F2-2b）
# ---------------------------------------------------------------------------


@dataclass
class SymmetryMap:
    mapping: np.ndarray  # (N,) 鏡の位置にある頂点の番号。無ければ -1
    unmatched: list[int]
    axis: int = 0
    centre: float = 0.0
    tolerance: float = 0.05

    @property
    def matched(self) -> int:
        return int(len(self.mapping) - len(self.unmatched))


_SYM_CACHE: dict[tuple, SymmetryMap] = {}


def clear_symmetry_cache() -> None:
    _SYM_CACHE.clear()


def mesh_signature(mesh: str, rest: np.ndarray) -> tuple:
    """キャッシュの鍵: メッシュ名 + 頂点数 + 外接ボックス（丸め）+ 頂点位置の和・二乗和（内側の頂点だけの編集も検出する）。"""
    lo = np.round(rest.min(axis=0), 4)
    hi = np.round(rest.max(axis=0), 4)
    return (mesh, int(len(rest)), tuple(lo.tolist()), tuple(hi.tolist()), round(float(rest.sum()), 4), round(float((rest * rest).sum()), 4))


def symmetry_map(
    mesh: str,
    rest: np.ndarray,
    axis: int = 0,
    centre: float = 0.0,
    tolerance: float = 0.05,
    use_cache: bool = True,
) -> SymmetryMap:
    """位置の最近傍で、各頂点の鏡（`centre` を挟んだ反対側の同じ位置）にある頂点を探す。許容誤差 tolerance（cm）の中に無ければ「対応なし」。

    キャッシュは（メッシュ名・頂点数・外接ボックス・軸・中心・許容誤差）が同じ間だけ使う。
    """
    key = (*mesh_signature(mesh, rest), axis, round(float(centre), 6), round(float(tolerance), 6))
    if use_cache and key in _SYM_CACHE:
        return _SYM_CACHE[key]
    n = len(rest)
    cell = max(float(tolerance), 1e-6)
    cells = np.floor(rest / cell).astype(np.int64)
    table: dict[tuple, list[int]] = {}
    for i, c in enumerate(map(tuple, cells.tolist())):
        table.setdefault(c, []).append(i)
    mirrored = rest.copy()
    mirrored[:, axis] = 2.0 * centre - rest[:, axis]
    mcells = np.floor(mirrored / cell).astype(np.int64)
    mapping = np.full(n, -1, dtype=np.int64)
    offs = [(a, b, c) for a in (-1, 0, 1) for b in (-1, 0, 1) for c in (-1, 0, 1)]
    for i in range(n):
        cx, cy, cz = mcells[i].tolist()
        best, best_d = -1, tolerance
        for a, b, c in offs:
            for j in table.get((cx + a, cy + b, cz + c), ()):
                d = float(np.linalg.norm(rest[j] - mirrored[i]))
                if d <= best_d:
                    best, best_d = j, d
        mapping[i] = best
    sm = SymmetryMap(mapping, [int(i) for i in np.nonzero(mapping < 0)[0]], axis, float(centre), float(tolerance))
    if use_cache:
        _SYM_CACHE[key] = sm
    return sm


def _mirror_delta(d: np.ndarray, mapping: np.ndarray, flip: np.ndarray) -> np.ndarray:
    """各頂点 j の差分 = 鏡の位置の頂点の差分を軸で反転したもの（対応の無い頂点は 0）。"""
    out = np.zeros_like(d)
    ok = mapping >= 0
    out[ok] = d[mapping[ok]] * flip
    return out


def mirror_shape(
    ctx: ShapeCtx,
    src: str,
    dst: Optional[str] = None,
    centre: float = 0.0,
    tolerance: float = 0.05,
    allow_unmatched: bool = False,
    overwrite_originals: bool = False,
    rest: Optional[np.ndarray] = None,
) -> ShapeResult:
    """src（`_L` か `_R`）の差分を鏡映して、反対側のシェイプ dst を作る / 更新する（src は変えない）。

    dst を省くと、鏡映の名前規則（`profile.mirror_name`）で決める（`smile_L` → `smile_R`）。対応が取れない頂点があるときは、その頂点を
    ビューポートで選択して AsymmetryStop で止める（allow_unmatched=True のときだけ、対応の無い頂点は 0 のまま進める）。
    """
    if src not in scene.target_indices(ctx.node):
        raise ShapeError(f"シェイプ {src} がありません", "no_target")
    dst = dst or profile_mod.mirror_name(src, ctx.suffix_l, ctx.suffix_r)
    if dst == src:
        raise ShapeError(
            f"{src} は名前の末尾が {ctx.suffix_l} / {ctx.suffix_r} ではないため、反対側の名前を決められません（相手の名前を指定してください）",
            "not_sided",
        )
    was = _dest_check(ctx, dst, overwrite_originals)
    with undo_chunk("tdFacialMirror"):
        rp = rest if rest is not None else rest_points(ctx.mesh)
        sm = symmetry_map(ctx.mesh, rp, ctx.axis, centre, tolerance)
        res = ShapeResult()
        if sm.unmatched:
            select_vertices(ctx.mesh, sm.unmatched)
            if not allow_unmatched:
                raise AsymmetryStop(
                    f"左右の対応が取れない頂点が {len(sm.unmatched)} 個あります（ビューポートで選択しました）。"
                    "このまま進めると、その頂点は鏡映されず 0 になります",
                    sm.unmatched,
                )
            res.warnings.append(f"対応が取れない頂点 {len(sm.unmatched)} 個は 0 のままです（選択してあります）")
        flip = np.ones(3)
        flip[ctx.axis] = -1.0
        written = _copy_items(ctx, src, dst, lambda d: _mirror_delta(d, sm.mapping, flip))
        add_made(ctx, [dst])
        (res.replaced if was else res.created).append(dst)
        res.message = f"{src} を鏡映して {dst} を{'更新' if was else '作成'}しました（対応 {sm.matched} / {len(rp)} 頂点）"
        res.stats = {"matched": sm.matched, "unmatched": len(sm.unmatched), "vertices": written, "tolerance": tolerance}
        return res


# ---------------------------------------------------------------------------
# 中間・誇張（F2-3b）
# ---------------------------------------------------------------------------


def inbetween_item(weight: float) -> int:
    """重み（0〜1 の間）→ inputTargetItem の番号（5000 + 重み × 1000）。"""
    if not (0.0 < weight < 1.0):
        raise ShapeError("中間形の重みは 0 より大きく 1 より小さい値にしてください（例: 0.5）", "bad_weight")
    item = 5000 + int(round(weight * 1000))
    if not (5000 < item < scene.DELTA_ITEM):  # 0.9995 以上は 6000（本体）、0.0005 以下は 5000（重み 0）になってしまう（S-12）
        raise ShapeError("中間形の重みは 0.001〜0.999 の間にしてください（本体の形を上書きしてしまうため）", "bad_weight")
    return item


def add_inbetween(
    ctx: ShapeCtx,
    name: str,
    weight: float,
    confirm_original: bool = False,
    delta: Optional[np.ndarray] = None,
) -> ShapeResult:
    """既存シェイプ name に、重み weight のときの形（中間形）を足す。形は「今のシーンの形 − 基準」（name 自身の重みは 0 で測る）。

    元からあるシェイプへは confirm_original=True のときだけ。既に同じ重みの中間形があれば置き換える。
    """
    if name not in scene.target_indices(ctx.node):
        raise ShapeError(f"シェイプ {name} がありません", "no_target")
    if tag_of(ctx, name) == TAG_ORIGINAL and not confirm_original:
        raise ShapeError(f"{name} は元からあるシェイプです。中間形を足すと、元のシェイプの動きが変わります（確認が必要です）", "original_needs_confirm")
    with undo_chunk("tdFacialInbetween"):
        item = inbetween_item(weight)
        d = delta if delta is not None else current_delta(ctx, exclude_own=name)
        had = item in scene.item_numbers(ctx.node, name, ctx.geo)
        n = _write_dense(ctx, name, d, item, threshold=max(EPS, 0.0))
        res = ShapeResult(message=f"{name} に重み {weight:g} の中間形を{'置き換え' if had else '追加'}しました（頂点 {n}）")
        res.replaced.append(name)
        res.stats = {"item": item, "vertices": n}
        if n == 0:
            res.warnings.append("今のシーンの形が基準と同じでした（差分が 0 の中間形になります）。ポーズを当ててから実行してください")
        return res


def make_exaggeration(ctx: ShapeCtx, name: str, delta: Optional[np.ndarray] = None) -> ShapeResult:
    """誇張形 `<name>_Ex` を作る（R-37）。形 = 今のシーンの形（name 自身の重みは 0 で測る）− name の差分。

    つまり `_Ex` は「name を 1 にした形に足す分」。可動域の上限を 2 に開く設定は呼ぶ側（session.set_limit）が行う。
    """
    if name not in scene.target_indices(ctx.node):
        raise ShapeError(f"シェイプ {name} がありません", "no_target")
    if name.endswith(naming.EXTREME_SUFFIX):
        raise ShapeError("誇張形（_Ex）の誇張形は作れません", "bad_name")
    ex = name + naming.EXTREME_SUFFIX
    was = _dest_check(ctx, ex, False)
    with undo_chunk("tdFacialExaggerate"):
        base = dense_delta(ctx, name, ctx.nverts, ctx.geo)
        cur = delta if delta is not None else current_delta(ctx, exclude_own=name)
        n = _write_dense(ctx, ex, cur - base)
        add_made(ctx, [ex])
        res = ShapeResult(message=f"誇張形 {ex} を{'置き換え' if was else '作成'}しました（頂点 {n}）。{name} の可動域を 0〜2 にします")
        (res.replaced if was else res.created).append(ex)
        res.stats = {"vertices": n, "extreme": ex}
        res.notes.append("ポーズでこのシェイプを 1 より大きくした分は、ベイクのとき誇張用のシェイプ（FC_…_Ex）に分けて焼かれます")
        return res


# ---------------------------------------------------------------------------
# 組み合わせ補正（F2-3c）
# ---------------------------------------------------------------------------


def combo_name(ctx: ShapeCtx, a: str, b: str) -> str:
    return f"{ctx.prefix}{COMBO_INFIX}{a}__{b}"


def combo_drivers(ctx: ShapeCtx, name: str) -> list[str]:
    """組み合わせ補正ターゲットの駆動元（ターゲット名）。"""
    idx = scene.target_indices(ctx.node).get(name)
    if idx is None:
        return []
    try:
        drv = cmds.combinationShape(blendShape=ctx.node, query=True, allDrivers=True, combinationTargetIndex=idx) or []
    except RuntimeError:
        return []
    return [d.split(".", 1)[-1] for d in drv]


def create_combo(ctx: ShapeCtx, a: str, b: str) -> ShapeResult:
    """2 つのシェイプ a, b を同時に上げたときだけ効く補正ターゲット `fcs_combo_<a>__<b>`（重み = a × b）を作る。差分は 0（彫って作る）。

    Maya の combinationShape ノードで駆動する（ポーズからは直接動かさない）。ベイクは結果を FC_* に焼くので Unity へは持ち出さない。
    """
    table = scene.target_indices(ctx.node)
    for n in (a, b):
        if n not in table:
            raise ShapeError(f"シェイプ {n} がありません", "no_target")
    if a == b:
        raise ShapeError("別々の 2 つのシェイプを選んでください", "bad_name")
    driven = scene.driven_indices(ctx.node)
    for n in (a, b):
        if table[n] in driven:
            raise ShapeError(f"{n} は組み合わせ補正なので、駆動元にはできません", "bad_name")
        if naming.is_fc_name(n):
            raise ShapeError(f"{n} は焼いたシェイプ（FC_）なので、駆動元にはできません", "bad_name")
    name = combo_name(ctx, a, b)
    check_new_name(name)
    if name in table:
        raise ShapeError(f"{name} は既にあります（彫り直すなら「組み合わせ補正を彫る」）", "exists")
    with undo_chunk("tdFacialCombo"):
        idx, _ = sculpt_prepare(ctx, name, zero=True)
        cmds.combinationShape(blendShape=ctx.node, combinationTargetIndex=idx, driverTargetIndex=[table[a], table[b]], combineMethod=0)
        for n in cmds.listConnections(scene.weight_plug(ctx.node, idx), source=True, destination=False, type="combinationShape") or []:
            cmds.rename(n, f"tdFacialCombo_{name}"[:60])
            break
        res = ShapeResult(message=f"組み合わせ補正 {name} を作りました（{a} と {b} が同時に上がるときだけ効きます）")
        res.created.append(name)
        res.notes.append("形はこれから彫ります。ベイクは結果を FC_* に焼くので、Unity へは出しません（Maya 側の評価だけ）")
        return res


# ---------------------------------------------------------------------------
# 別メッシュへ写す（F2-4）
# ---------------------------------------------------------------------------


def _topology(mesh: str) -> tuple[np.ndarray, np.ndarray]:
    sel = om.MSelectionList()
    sel.add(scene.mesh_shape(mesh))
    counts, conns = om.MFnMesh(sel.getDagPath(0)).getVertices()
    return np.array(counts, dtype=np.int64), np.array(conns, dtype=np.int64)


def same_topology(mesh_a: str, mesh_b: str) -> bool:
    """頂点の数・面の構成・順序が同じか（同じなら差分をそのまま複写できる）。"""
    ca, va = _topology(mesh_a)
    cb, vb = _topology(mesh_b)
    return len(ca) == len(cb) and len(va) == len(vb) and bool(np.array_equal(ca, cb)) and bool(np.array_equal(va, vb))


@dataclass
class TransferPlan:
    same_topology: bool
    dest_mesh: str
    dest_node: Optional[str]  # まだ blendShape が無ければ None（写すときに tdFacial_<mesh> を作る）
    names: list[str]
    collisions: list[str]  # 写し先に同じ名前があるもの
    protected: list[str]  # 写し先の元からあるターゲットと衝突するもの（確認が要る）


def plan_transfer(ctx: ShapeCtx, names: Sequence[str], dest_mesh: str) -> TransferPlan:
    """写す前の調べ（シーンは変えない）: 頂点の同じ・違い、写し先の名前の衝突。"""
    dest = scene.resolve_mesh(dest_mesh)
    if dest == ctx.mesh:
        raise ShapeError("写し先は顔メッシュとは別のメッシュにしてください", "bad_dest")
    node = scene.primary_blend_shape(dest, create=False)
    coll: list[str] = []
    prot: list[str] = []
    if node is not None:
        dctx = ShapeCtx(dest, node, scene.geometry_index(node, dest), scene.vertex_count(dest), ctx.prefix)
        table = scene.target_indices(node)
        made = set(get_made(node))
        for n in names:
            if n in table:
                coll.append(n)
                if tag_of(dctx, n, made) == TAG_ORIGINAL:
                    prot.append(n)
    return TransferPlan(same_topology(ctx.mesh, dest), dest, node, list(names), coll, prot)


def _wrap_deltas(ctx: ShapeCtx, deltas: dict[str, np.ndarray], dest: str) -> dict[str, np.ndarray]:
    """違うトポロジへ: 顔メッシュの複製（ドライバー）を各シェイプの形にし、写し先の複製を proximityWrap で追従させて差分を読む。

    一時ノード（複製 2 つ・proximityWrap）は必ず消す。基準姿勢で行い、終わったら元の姿勢へ戻す。
    """
    out: dict[str, np.ndarray] = {}
    temps: list[str] = []
    wrap: Optional[str] = None
    try:
        with scene.enter_reference_pose([ctx.mesh, dest]):
            drv = cmds.duplicate(ctx.mesh, name="tdFacialXfer_drv")[0]
            temps.append(drv)
            dup = cmds.duplicate(dest, name="tdFacialXfer_dst")[0]
            temps.append(dup)
            drv_shape = scene.mesh_shape(drv)
            if scene.vertex_count(drv) != ctx.nverts:
                raise ShapeError("ドライバーの複製の頂点数が顔メッシュと違います", "dup_mismatch")
            wrap = cmds.deformer(dup, type="proximityWrap", name="tdFacialXfer_wrap")[0]
            cmds.proximityWrap(wrap, edit=True, addDrivers=[drv_shape])
            sel = om.MSelectionList()
            sel.add(drv_shape)
            fn = om.MFnMesh(sel.getDagPath(0))
            rest_src = np.array([(p.x, p.y, p.z) for p in fn.getPoints(om.MSpace.kObject)])
            rest_dst = scene.read_points(dup)
            for name, d in deltas.items():
                pts = rest_src + d
                fn.setPoints(om.MPointArray([om.MPoint(*map(float, v)) for v in pts]), om.MSpace.kObject)
                out[name] = scene.read_points(dup) - rest_dst
    finally:
        doomed = []
        if wrap is not None and cmds.objExists(wrap):
            doomed.append(wrap)
        doomed.extend(t for t in temps if cmds.objExists(t))
        if doomed:
            for t in doomed:
                if cmds.objExists(t):
                    cmds.delete(t)
    return out


def transfer_shapes(
    ctx: ShapeCtx,
    names: Sequence[str],
    dest_mesh: str,
    overwrite: Iterable[str] = (),
    threshold: Optional[float] = None,
) -> ShapeResult:
    """顔メッシュのシェイプを別メッシュの blendShape（無ければ `tdFacial_<mesh>` を作る）へ、同じ名前で写す。

    頂点・順序が同じ → 差分を複写。違う → proximityWrap で転写（写し先の頂点ごとの差分。しきい値未満は捨てる）。
    写し先に同じ名前があるものは、overwrite に入れたものだけ上書きする（元からあるものは名前を overwrite に入れる＝確認済み、の意味）。
    入れていなければ飛ばして `notes` に出す。
    """
    plan = plan_transfer(ctx, names, dest_mesh)
    over = set(overwrite)
    dest = plan.dest_mesh
    thr = ctx.threshold if threshold is None else threshold
    res = ShapeResult()
    todo = []
    for n in names:
        if n not in scene.target_indices(ctx.node):
            res.warnings.append(f"{n} は顔メッシュにないので飛ばしました")
        elif n in plan.collisions and n not in over:
            res.notes.append(f"{n} は写し先に同じ名前があるので飛ばしました")
        else:
            todo.append(n)
    if not todo:
        raise ShapeError("写せるシェイプがありません（名前の衝突・選択なし）。" + " ".join(res.notes), "nothing")
    with undo_chunk("tdFacialTransfer"):
        dnode = plan.dest_node or scene.primary_blend_shape(dest, create=True)
        dctx = ShapeCtx(dest, dnode, scene.geometry_index(dnode, dest), scene.vertex_count(dest), ctx.prefix, ctx.suffix_l, ctx.suffix_r, ctx.axis, ctx.threshold)
        deltas: dict[str, dict[int, np.ndarray]] = {}
        for n in todo:
            deltas[n] = {item: dense_delta(ctx, n, ctx.nverts, ctx.geo, item) for item in _all_items(ctx, n)}
        method = "copy" if plan.same_topology else "wrap"
        if plan.same_topology:
            moved = deltas
        else:
            keys = [(n, it) for n, per in deltas.items() for it in per]
            flat = _wrap_deltas(ctx, {f"{n}\0{it}": deltas[n][it] for n, it in keys}, dest)
            moved = {n: {it: flat[f"{n}\0{it}"] for it in per} for n, per in deltas.items()}
        stats = {}
        thr_w = thr if method == "wrap" else EPS
        for n in todo:
            was = n in plan.collisions
            cnt = _write_dense(dctx, n, moved[n][scene.DELTA_ITEM], threshold=thr_w)
            for it, d in moved[n].items():
                if it != scene.DELTA_ITEM:
                    _write_dense(dctx, n, d, it, threshold=thr_w)
            add_made(dctx, [n])
            (res.replaced if was else res.created).append(n)
            main = moved[n][scene.DELTA_ITEM]
            stats[n] = {"max": float(np.linalg.norm(main, axis=1).max()) if len(main) else 0.0, "vertices": cnt}
        res.message = f"{len(todo)} 個のシェイプを {scene.short_name(dest)} へ{'複写' if method == 'copy' else '転写'}しました"
        res.stats = {"method": method, "per_shape": stats, "dest_node": dnode, "dest": dest}
        if method == "wrap":
            res.notes.append("頂点が違うため、近接（proximityWrap）で転写しました。形を確認してください")
        return res


# ---------------------------------------------------------------------------
# 整理（F2-5）
# ---------------------------------------------------------------------------


def clean_micro(ctx: ShapeCtx, names: Sequence[str], threshold: float, confirm_original: bool = False) -> ShapeResult:
    """差分の長さが threshold（cm）未満の頂点を捨てる（中間形も）。元からあるシェイプは confirm_original=True のときだけ。"""
    if threshold <= 0:
        raise ShapeError("しきい値は 0 より大きくしてください", "bad_threshold")
    with undo_chunk("tdFacialCleanMicro"):
        res = ShapeResult()
        made = set(ctx.made())
        total = 0
        per: dict[str, int] = {}
        for n in names:
            if n not in scene.target_indices(ctx.node):
                continue
            if tag_of(ctx, n, made) == TAG_ORIGINAL and not confirm_original:
                res.notes.append(f"{n} は元からあるシェイプなので飛ばしました（確認が必要です）")
                continue
            removed = 0
            for item in _all_items(ctx, n):
                r = scene.read_item_delta(ctx.node, n, item, ctx.geo)
                if r is None:
                    continue
                before = len(r[0])
                d = dense_delta(ctx, n, ctx.nverts, ctx.geo, item)
                kept = _write_dense(ctx, n, d, item, threshold=threshold)
                removed += before - kept
            per[n] = removed
            total += removed
            if removed:
                res.replaced.append(n)
        res.message = f"微小な差分を {total} 頂点ぶん掃除しました（しきい値 {threshold:g} cm、{len(per)} 個のシェイプ）"
        res.stats = {"removed": total, "per_shape": per}
        return res


def referenced_names(doc: Document) -> set[str]:
    """ポーズ・作業セットが参照しているシェイプ名（`bs.name` の形も `name` の形も）。"""
    refs: set[str] = set(doc.working_set.curves)
    for layer in doc.layers:
        for pt in layer.points.values():
            refs.update(pt.pose.curves)
    out = set(refs)
    out.update(r.split(".", 1)[1] for r in refs if "." in r)
    return out


@dataclass
class Audit:
    empty: list[str] = field(default_factory=list)
    unreferenced: list[str] = field(default_factory=list)  # ポーズ・作業セットのどれからも使われていない（元からのものは情報だけ）
    orphan_sculpt: list[str] = field(default_factory=list)  # どの点のポーズも参照していない fcs_*
    orphan_fc: list[str] = field(default_factory=list)  # 格子に対応する点がない FC_*
    deletable: list[str] = field(default_factory=list)  # 消してよいもの（fcs_* / FC_* だけ）


def audit(ctx: ShapeCtx, doc: Document) -> Audit:
    """空のシェイプ・未使用のシェイプ・孤立した fcs_* / FC_* の一覧（シーンもデータも変えない）。"""
    from . import bake as bake_mod  # 循環を避ける

    refs = referenced_names(doc)
    live_pts = bake_mod._live_points(doc)
    live = {(doc.layers[li].name, r, c) for li, r, c in live_pts}
    live_ex = {(doc.layers[li].name, r, c) for li, r, c in live_pts if V.needs_extreme(doc, li, (r, c))}  # 誇張用 _Ex が要る点
    out = Audit()
    drivers: set[str] = set()
    infos = list_shapes(ctx)
    for s in infos:
        if s.tag == TAG_COMBO:
            drivers.update(combo_drivers(ctx, s.name))
    for s in infos:
        if s.empty and not s.driven:
            out.empty.append(s.name)
        if s.tag == TAG_COMBO or s.name in drivers:
            continue
        if s.tag == TAG_FC:
            if bake_mod._is_orphan(doc, s.name, live, live_ex):
                out.orphan_fc.append(s.name)
            continue
        if s.name not in refs:
            out.unreferenced.append(s.name)
            if s.tag == TAG_SCULPT:
                out.orphan_sculpt.append(s.name)
    out.deletable = sorted(set(out.orphan_sculpt) | set(out.orphan_fc) | {n for n in out.empty if tag_of(ctx, n) in (TAG_FC, TAG_SCULPT, TAG_COMBO)})
    return out


def missing_standard(ctx: ShapeCtx, profile) -> list[str]:
    """プロファイルの標準シェイプのうち、顔メッシュの blendShape に無いもの。"""
    if profile is None:
        return []
    # Setup / 検証と同じ規則（完全名 `<ノード>.<ターゲット>` で照合。ノード名なしの標準シェイプはどのノードのターゲットにも一致）
    return profile_mod.missing_standard_curves(profile, [scene.curve_name(ctx.node, t) for t in scene.target_indices(ctx.node)])


def delete_shapes(ctx: ShapeCtx, names: Sequence[str]) -> list[str]:
    """`FC_*` / `fcs_*` のターゲットを消す（それ以外が入っていたら何も消さずに ValueError。組み合わせ補正の節も消える）。"""
    bad = [n for n in names if not (naming.is_fc_name(n) or naming.is_sculpt_name(n, ctx.prefix))]
    if bad:
        raise ValueError(f"FC_* / {ctx.prefix}* 以外のターゲットは消せません: {bad}")
    with undo_chunk("tdFacialDeleteShapes"):
        # 要素を消すだけでは、Undo で戻したときに差分の中身が戻らない（Maya の removeMultiInstance の Undo は子の値を戻さない）。
        # 先に中身を空にしておくと、その setAttr の Undo が中身を戻す
        table = scene.target_indices(ctx.node)
        for n in names:
            idx = table.get(n)
            if idx is None:
                continue
            for item in scene.item_numbers(ctx.node, n, ctx.geo):
                plug = scene.item_plug(ctx.node, idx, item, ctx.geo)
                cmds.setAttr(plug + ".inputPointsTarget", 0, type="pointArray")
                cmds.setAttr(plug + ".inputComponentsTarget", 0, type="componentList")
        gone = scene.delete_targets(ctx.node, names, ctx.prefix)
        remove_made(ctx.node, gone)
        return gone
