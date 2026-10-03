"""ベイク: 格子の点ごとのポーズを blendShape ターゲット `FC_*` にする（Maya 依存。docs/14 §5.7、docs/15 §4.3）。

```
基準姿勢にする（バインドポーズ・全シェイプの重み 0）
N = 何も当てない状態の変形後の頂点
for 点: ポーズを当てる → P = 変形後の頂点 → D = P − N
        感情レイヤーは D −= D_neutral（同じ点。differential のとき）
        長さ < deltaThreshold の頂点を捨てる（スパース化）
        ターゲット FC_<asset>_<layer>_R{r}_C{c} を作る / 置き換える（差分を直接書く）
元の姿勢へ戻す → tdFacialBakeState を更新 → 孤立した FC_* を消す → 要約
```

- 全体が Maya の Undo 1 回（`undoInfo -openChunk`）。例外が出ても、基準姿勢へ入る前のジョイント・重み・接続へ必ず戻す（try / finally）
- 作る・置き換える・消すのは `FC_*` だけ。モデルに元からあるターゲットの番号・エイリアス・差分は変えない
- 差分は「スキンの後」の形で取るが、書き込み先（blendShape）はスキンの前。バインドポーズではスキンの行列が単位なので一致する
  （基準姿勢でしか焼かない理由）。基準姿勢にできなければ焼かずに止める
- 感情レイヤーの差分は、Neutral の「しきい値で捨てる前」の差分を引いてから捨てる（UE 版と同じ順）
- 法線は焼かない
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional, Sequence

import numpy as np
from maya import cmds

from . import pose_apply, scene
from .core import naming, validate
from .core.model import Document, SourcePose

DEFAULT_THRESHOLD = 0.001  # cm（doc.bake が無いとき）

Progress = Callable[[int, int, str], None]


class BakeError(RuntimeError):
    """ベイクできない（前提が足りない）。メッセージはそのまま画面に出せる日本語。"""


@dataclass
class BakeReport:
    """ベイクの要約。"""

    created: list[str] = field(default_factory=list)  # 新しく作ったターゲット
    replaced: list[str] = field(default_factory=list)  # 置き換えたターゲット
    removed: list[str] = field(default_factory=list)  # 孤立していて消したターゲット
    empty: list[str] = field(default_factory=list)  # 差分が 1 つも残らなかったターゲット
    vertex_counts: dict[str, int] = field(default_factory=dict)  # ターゲット名 → 差分を持つ頂点の数（メッシュの合計）
    total_vertices: int = 0  # 全ターゲット・全メッシュの差分を持つ頂点の合計
    culled_vertices: int = 0  # しきい値で捨てた頂点の合計
    seconds: float = 0.0
    missing_curves: list[str] = field(default_factory=list)  # シーンに無くて飛ばしたシェイプ名
    missing_bones: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # 自動でしたことのお知らせ（Neutral の焼き直しに伴う感情レイヤーの焼き直し・プレビューの作り直し）
    meshes: list[str] = field(default_factory=list)

    @property
    def targets(self) -> list[str]:
        return self.created + self.replaced

    def summary(self) -> str:
        return (
            f"ベイク: 作成 {len(self.created)} / 置き換え {len(self.replaced)} / 削除 {len(self.removed)}、"
            f"頂点 {self.total_vertices}（しきい値で捨てた {self.culled_vertices}）、{self.seconds:.2f} 秒"
        )


# ---------------------------------------------------------------------------
# 準備
# ---------------------------------------------------------------------------


def _resolve_meshes(doc: Document) -> list[str]:
    """顔メッシュ + extraMeshes（長い名前）。足りなければ BakeError。"""
    if not doc.asset:
        raise BakeError("アセット名（asset）が設定されていないためベイクできません")
    if doc.target is None or not doc.target.mesh:
        raise BakeError("焼く先のメッシュ（target.mesh）が設定されていないためベイクできません")
    out: list[str] = []
    for name in [doc.target.mesh, *doc.target.extra_meshes]:
        try:
            m = scene.resolve_mesh(name)
        except ValueError as e:
            raise BakeError(f"焼く先のメッシュが見つかりません: {e}") from e
        if m not in out:
            out.append(m)
    return out


def _threshold(doc: Document) -> float:
    return doc.bake.delta_threshold if doc.bake is not None else DEFAULT_THRESHOLD


def _enter_reference(meshes: Sequence[str], doc: Document) -> scene.Reference:
    try:
        return scene.enter_reference_pose(meshes, extra_joints=_bone_names(doc))
    except scene.ReferenceError_ as e:
        raise BakeError(f"基準姿勢にできないためベイクできません: {e}") from e


def _bone_names(doc: Document) -> list[str]:
    names: dict[str, None] = {}
    for layer in doc.layers:
        for pt in layer.points.values():
            for b in pt.pose.bones:
                names.setdefault(b, None)
    return list(names)


def _jobs(
    doc: Document,
    layers: Optional[Iterable[int]],
    points: Optional[Iterable[tuple[int, int, int]]],
) -> list[tuple[int, str, int, int, SourcePose]]:
    """焼く対象: (レイヤー番号, レイヤー名, row, col, ポーズ)。格子の中で、ポーズが空でない点。Neutral → 感情レイヤーの順。"""
    only_layers = set(layers) if layers is not None else None
    only_points = set(points) if points is not None else None
    out = []
    for li, layer in enumerate(doc.layers):
        if only_layers is not None and li not in only_layers:
            continue
        for (r, c), pt in sorted(layer.points.items()):
            if not (0 <= r < doc.grid.rows and 0 <= c < doc.grid.cols) or pt.pose.is_empty():
                continue
            if only_points is not None and (li, r, c) not in only_points:
                continue
            out.append((li, layer.name, r, c, pt.pose))
    return out


def _sparse(delta: np.ndarray, threshold: float) -> tuple[np.ndarray, np.ndarray, int]:
    """(N, 3) の差分 → (残す頂点の番号, その差分, 捨てた頂点の数)。長さ < threshold を捨てる。"""
    length = np.linalg.norm(delta, axis=1)
    keep = length >= threshold
    culled = int(np.count_nonzero((length > 1e-12) & ~keep))
    idx = np.nonzero(keep)[0]
    return idx, delta[idx], culled


def _is_orphan(doc: Document, name: str, live: set[tuple[str, int, int]]) -> bool:
    """この asset の FC_* で、格子に対応する点が無いもの（`validate` の orphan_target と同じ判定）。"""
    asset = doc.asset or ""
    if not name.startswith(f"{naming.FC_PREFIX}{asset}_"):
        return False
    p = naming.parse_name(name, asset)
    if p is None:
        return True
    if p.kind in (naming.KIND_POINT, naming.KIND_POINT_EX):
        return (p.layer, p.row, p.col) not in live
    if p.kind == naming.KIND_PERSP:
        n_persp = len(doc.perspective.keys) if doc.perspective is not None else 0
        return (p.index or 0) >= n_persp
    return False


# ---------------------------------------------------------------------------
# 本体
# ---------------------------------------------------------------------------


def bake(
    doc: Document,
    layers: Optional[Iterable[int]] = None,
    points: Optional[Iterable[tuple[int, int, int]]] = None,
    progress: Optional[Progress] = None,
) -> BakeReport:
    """ドキュメントの点を `FC_*` ターゲットにする。

    layers: 焼くレイヤーの番号（None = 全部）。points: 焼く点 (レイヤー番号, row, col)（None = 全部）。
    孤立した FC_*（格子に対応する点が無いこの asset のもの）は、部分的な指定でも掃除する。
    progress(完了数, 全体数, ターゲット名) を点ごとに呼ぶ（任意）。
    """
    pose_apply.assert_maya_space(doc)
    meshes = _resolve_meshes(doc)
    jobs = _jobs(doc, layers, points)
    rep = BakeReport(meshes=list(meshes))
    t0 = time.perf_counter()
    thr = _threshold(doc)
    differential = doc.bake.differential if doc.bake is not None else True
    asset = doc.asset or ""

    cmds.undoInfo(openChunk=True, chunkName="tdFacialBake")
    try:
        ref = _enter_reference(meshes, doc)
        rep.warnings.extend(ref.warnings)
        results: list[tuple[str, int, int, int, SourcePose, dict[str, tuple[np.ndarray, np.ndarray]]]] = []
        try:
            base = {m: scene.read_points(m) for m in meshes}
            neutral_cache: dict[tuple[int, int], dict[str, np.ndarray]] = {}

            def deform(pose: SourcePose) -> dict[str, np.ndarray]:
                ar = pose_apply.apply_pose(doc, pose, ref)
                _merge(rep.missing_curves, ar.missing_curves)
                _merge(rep.missing_bones, ar.missing_bones)
                return {m: scene.read_points(m) - base[m] for m in meshes}

            def neutral_delta(r: int, c: int) -> Optional[dict[str, np.ndarray]]:
                if (r, c) not in neutral_cache:
                    pt = doc.layers[0].points.get((r, c)) if doc.layers else None
                    neutral_cache[(r, c)] = deform(pt.pose) if pt is not None and not pt.pose.is_empty() else None
                return neutral_cache[(r, c)]

            for i, (li, lname, r, c, pose) in enumerate(jobs):
                name = naming.morph_name(asset, lname, r, c)
                if progress:
                    progress(i, len(jobs), name)
                delta = deform(pose)
                if li == 0:
                    neutral_cache[(r, c)] = delta
                elif differential:
                    nd = neutral_delta(r, c)
                    if nd is not None:
                        delta = {m: delta[m] - nd[m] for m in meshes}
                sparse = {}
                culled = 0
                for m in meshes:
                    idx, d, cl = _sparse(delta[m], thr)
                    sparse[m] = (idx, d)
                    culled += cl
                rep.culled_vertices += culled
                results.append((name, li, r, c, pose, sparse))
            if progress:
                progress(len(jobs), len(jobs), "")
        finally:
            ref.restore()
        rep.warnings.extend(w for w in ref.warnings if w not in rep.warnings)

        # --- 書き込み（基準姿勢でなくてもよい。FC_* は重み 0 のまま作る）
        nodes = {m: scene.primary_blend_shape(m, create=True) for m in meshes}
        geo = {m: scene.geometry_index(nodes[m], m) for m in meshes}
        state = {m: scene.get_bake_state(nodes[m]) for m in meshes}
        for name, li, r, c, pose, sparse in results:
            total = 0
            for m in meshes:
                idx, d = sparse[m]
                _, created = scene.write_target_delta(nodes[m], name, d, idx.tolist(), geo[m])
                state[m][name] = validate.pose_hash(pose)
                total += len(idx)
                if m == meshes[0]:
                    (rep.created if created else rep.replaced).append(name)
            rep.vertex_counts[name] = total
            rep.total_vertices += total
            if total == 0:
                rep.empty.append(name)

        # --- 孤立した FC_* の掃除（FC_ の名前だけ）
        live = {(doc.layers[li].name, r, c) for li, r, c in _live_points(doc)}
        for m in meshes:
            doomed = [t.alias for t in scene.list_curves(m, include_fc=True) if t.node == nodes[m] and _is_orphan(doc, t.alias, live)]
            if doomed:
                for n in scene.delete_targets(nodes[m], doomed):
                    state[m].pop(n, None)
                    if n not in rep.removed:
                        rep.removed.append(n)
            # 状態は消えたターゲットのぶんも掃除する
            for n in [k for k in state[m] if k not in scene.target_indices(nodes[m])]:
                state[m].pop(n, None)
            scene.set_bake_state(nodes[m], state[m])
    finally:
        cmds.undoInfo(closeChunk=True)
    rep.seconds = time.perf_counter() - t0
    return rep


def bake_point(doc: Document, layer_index: int, row: int, col: int, progress: Optional[Progress] = None) -> BakeReport:
    """1 点だけ焼く（孤立した FC_* の掃除は `bake` と同じく行う）。"""
    return bake(doc, points=[(layer_index, row, col)], progress=progress)


def _live_points(doc: Document) -> list[tuple[int, int, int]]:
    return [(li, r, c) for li, _n, r, c, _p in _jobs(doc, None, None)]


def _merge(dst: list[str], src: Iterable[str]) -> None:
    for s in src:
        if s not in dst:
            dst.append(s)


def pose_to_shape(doc: Document, pose: SourcePose, name: str) -> BakeReport:
    """ポーズ 1 つを、新しいターゲット 1 本（顔メッシュ + extraMeshes の同じ名前）にする。

    bake と同じ差分の手順（基準姿勢 → 変形後 − 何も当てない形 → しきい値で捨てる）。感情の差分・孤立の掃除・ベイクの状態は扱わない。
    名前は `fcs_*`（sculptShapes.prefix）・`FC_*` か、まだ存在しない自由な名前。**既にある名前で FC_* / fcs_* 以外のターゲットは
    上書きしない**（BakeError）。
    """
    pose_apply.assert_maya_space(doc)
    if not name:
        raise BakeError("ターゲット名が空です")
    meshes = _resolve_meshes(doc)
    prefix = doc.sculpt_shapes.prefix if doc.sculpt_shapes is not None else naming.DEFAULT_SCULPT_PREFIX
    if not (naming.is_fc_name(name) or naming.is_sculpt_name(name, prefix)):
        for m in meshes:
            if any(name == t for n in scene.blend_shapes(m) for t in scene.target_indices(n)):
                raise BakeError(f"{name} は既にあるターゲットで、FC_* / {prefix}* ではないため上書きしません")
    if pose.is_empty():
        raise BakeError("ポーズが空です（何も動かしていません）")

    rep = BakeReport(meshes=list(meshes))
    t0 = time.perf_counter()
    thr = _threshold(doc)
    cmds.undoInfo(openChunk=True, chunkName="tdFacialPoseToShape")
    try:
        ref = _enter_reference(meshes, doc)
        rep.warnings.extend(ref.warnings)
        try:
            base = {m: scene.read_points(m) for m in meshes}
            ar = pose_apply.apply_pose(doc, pose, ref)
            _merge(rep.missing_curves, ar.missing_curves)
            _merge(rep.missing_bones, ar.missing_bones)
            sparse = {}
            for m in meshes:
                idx, d, cl = _sparse(scene.read_points(m) - base[m], thr)
                sparse[m] = (idx, d)
                rep.culled_vertices += cl
        finally:
            ref.restore()
        total = 0
        for m in meshes:
            node = scene.primary_blend_shape(m, create=True)
            idx, d = sparse[m]
            _, created = scene.write_target_delta(node, name, d, idx.tolist(), scene.geometry_index(node, m))
            total += len(idx)
            if m == meshes[0]:
                (rep.created if created else rep.replaced).append(name)
        rep.vertex_counts[name] = total
        rep.total_vertices = total
        if total == 0:
            rep.empty.append(name)
    finally:
        cmds.undoInfo(closeChunk=True)
    rep.seconds = time.perf_counter() - t0
    return rep
