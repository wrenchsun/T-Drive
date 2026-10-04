"""シーンのビューポートに出す補正格子（任意。docs/14 §5.3 の「ビューポートに格子の球を表示して点をクリック」。UE 版の
`FFacialPreviewViewportClient::Draw` と同じ情報: 基準ボーン（+ centerOffset）を中心にした球面の上に、点を Yaw × Pitch の位置へ並べ、
行・列を灰色の弧で結び、点を状態で色分けし、選択中の点を大きく描く）。

プラグインなしで Maya の普通のノードだけで作る:

    tdFacialGrid_GRP            transform。格子の中心へ置き、キャラクターの前方（Yaw）に合わせて回す。`tdPreviewOnly` 付き
      ├ tdFacialGrid_R{r}_C{c}  点 1 つにつき locator 1 つ（状態で色が変わる。選択中は大きい）
      ├ tdFacialGrid_row{r}     行の弧（次数 1 のカーブ。1 セルを 6 分割 = UE 版と同じ）
      ├ tdFacialGrid_col{c}     列の弧
      └ tdFacialGrid_view       今のビューのカメラの方向（2D の格子の赤い点）

- **点の位置は `camera_to_angles` と同じ式**（`session.grid_basis` / `grid_direction`）。点は群の中に前方 Yaw 0 の向きで固定し、
  頭が動く・回ったときは群の移動と Y 回転だけを更新する（接続・コンストレイントのノードは作らない。消すと何も残らない）
- 色: 灰 = 空 / 緑 = キー / 水色 = 自動生成 / 黄 = 未ベイク / 桃 = ベイク後に変更あり / 橙（大）= 選択中 / 赤 = 今のカメラ。
  黄・桃は緑・水色より優先（ベイクが要る点を目立たせる）
- **シーンには保存しない**: 保存・出力の直前に消し（`suspend`）、直後に戻す（`resume`）。ツールのリロード・パネルを閉じる・データを閉じる・
  新しいシーン / シーンを開くで消える。Maya の Undo には積まない（`scene.no_undo`）。変更フラグも元へ戻す
- クリック: 点を選ぶと、SelectionChanged の scriptJob（格子が出ていて「クリックで点を選ぶ」が入っているあいだだけ）が点の番号を読み、
  選択を元（クリック前）へ戻して、`on_pick(row, col)`（グリッドタブの 2D のセルのクリックと同じ道。未保存の確認・カメラ移動つき）を呼ぶ。
  クリックで選べないとき（オプションが切れているとき）は、点の表示を「リファレンス」にして選べなくする
- 追従: `follow()`（グリッドタブの約 10 回 / 秒のタイマーから）が、頭の動き・今のカメラの角度を反映する。新しい idle の scriptJob は作らない
"""

from __future__ import annotations

import contextlib
import math
import re
import time
import traceback
from typing import Callable, Optional

from maya import cmds
from maya.api import OpenMaya as om
from tdrive import lifecycle

from . import scene as scene_mod
from .core.presenters import FRAME_CHANGED, FRAME_UNBAKED, STATE_GENERATED, STATE_KEY

GROUP = "tdFacialGrid_GRP"
MARKER_FMT = "tdFacialGrid_R{r}_C{c}"
VIEW_MARKER = "tdFacialGrid_view"
GRID_ATTR = "tdFacialGrid"  # 群の目印（bool）
_MARKER_RE = re.compile(r"^tdFacialGrid_R(\d+)_C(\d+)$")

SEG_PER_CELL = 6  # 弧の 1 セルあたりの分割（UE 版と同じ）
DEFAULT_SCALE = 2.5  # 半径 = 顔メッシュの大きさ × この倍率（UE 版の既定 60 cm ≒ 顔の 2.4 倍）
SCALE_MIN, SCALE_MAX = 0.5, 10.0
FALLBACK_SIZE_CM = 20.0  # 顔メッシュの大きさが取れないとき
MARKER_SIZE = 0.05  # 点の大きさ（半径に対する割合）
SELECTED_FACTOR = 1.8  # 選択中・今のカメラの点は大きく

COLORS = {
    "empty": (0.55, 0.57, 0.62),
    "key": (0.25, 0.80, 0.32),
    "generated": (0.17, 0.62, 0.78),
    "unbaked": (1.0, 0.88, 0.30),
    "changed": (1.0, 0.48, 0.85),
    "selected": (1.0, 0.62, 0.11),
    "view": (1.0, 0.23, 0.19),
    "line": (0.45, 0.45, 0.48),
}
SUSPEND_TIMEOUT = 15.0  # 保存・出力が失敗して「直後」の通知が来なかったとき、これだけ経ったら出し直す（秒）
RETRY_SECONDS = 2.0  # 出せなかったとき、自動で出し直す最短の間隔
_PLACE_EPS = 1e-4


class SceneGridError(RuntimeError):
    """シーンに格子を出せない（データが無い・基準ボーンが無い等）。"""


def point_state_key(pv) -> str:
    """点（PointView）の色の区分: selected > changed > unbaked > key / generated / empty。"""
    if pv.selected:
        return "selected"
    if pv.frame == FRAME_CHANGED:
        return "changed"
    if pv.frame == FRAME_UNBAKED:
        return "unbaked"
    if pv.state == STATE_KEY:
        return "key"
    if pv.state == STATE_GENERATED:
        return "generated"
    return "empty"


def _ui(v_cm: float) -> float:
    return om.MDistance(v_cm, om.MDistance.kCentimeters).asUnits(om.MDistance.uiUnit())


def marker_index(node: str) -> Optional[tuple[int, int]]:
    """ノード名（長い名前でも）が格子の点なら (row, col)。群の中のものだけ。"""
    parts = node.split("|")
    m = _MARKER_RE.match(parts[-1])
    if m and (len(parts) < 2 or parts[-2] == GROUP):
        return int(m.group(1)), int(m.group(2))
    return None


@contextlib.contextmanager
def _quiet():
    """シーンへ書くあいだ: Undo に積まない・変更フラグを元へ戻す・オートキーを切る（キーを作らない）。"""
    try:
        modified = bool(cmds.file(query=True, modified=True))
        auto = bool(cmds.autoKeyframe(query=True, state=True))
    except RuntimeError:
        modified, auto = True, False
    try:
        if auto:
            cmds.autoKeyframe(state=False)
        with scene_mod.no_undo():
            yield
    finally:
        with contextlib.suppress(RuntimeError):
            if auto:
                cmds.autoKeyframe(state=True)
            if not modified:
                cmds.file(modified=False)


@contextlib.contextmanager
def _keep_selection():
    """この間にノードを作る・消して選択が変わっても、ユーザーの選択を元へ戻す（createNode は作ったものを選んでしまう）。"""
    before = cmds.ls(selection=True, long=True) or []
    try:
        yield
    finally:
        with contextlib.suppress(RuntimeError):
            if (cmds.ls(selection=True, long=True) or []) != before:
                keep = [n for n in before if cmds.objExists(n)]
                if keep:
                    cmds.select(keep, replace=True)
                else:
                    cmds.select(clear=True)


# ---------------------------------------------------------------------------
# 選択の scriptJob（格子が出ていてクリックで選べるあいだだけ）
# ---------------------------------------------------------------------------

_active: Optional["SceneGrid"] = None


@lifecycle.guarded()
def _cb_selection_changed() -> None:
    g = _active
    if g is not None:
        g._on_selection_changed()


lifecycle.keep_alive(_cb_selection_changed)


def _reload_cleanup() -> None:
    """ツールのリロード前: scriptJob を外し、シーンの格子を消す。"""
    g = _active
    if g is not None:
        g.remove()


lifecycle.on_reload(_reload_cleanup)


class SceneGrid:
    """シーンの格子の出し入れ。`FacialSession.scene_grid`。設定（出す / 大きさ / クリックで選ぶ）はセッションが持ち、文書には保存しない。"""

    def __init__(self, session) -> None:
        self.session = session
        self.enabled = False  # 「シーンに格子を表示」
        self.scale = DEFAULT_SCALE  # 半径の倍率（顔メッシュの大きさに対して）
        self.pickable = True  # 「クリックで点を選ぶ」
        self.on_pick: Optional[Callable[[int, int], None]] = None  # 点をクリックしたとき（グリッドタブが 2D のセルのクリックの処理を渡す）
        self.last_error = ""
        self._suspended = False
        self._busy = False
        self._markers: dict[tuple[int, int], str] = {}
        self._sig: Optional[tuple] = None
        self._colors: dict[str, tuple] = {}  # ノード → (色の区分, 大きさの倍率)
        self._size_cache: Optional[tuple[str, float]] = None  # (顔メッシュ名, 大きさ cm)
        self._radius = 0.0
        self._joint: Optional[str] = None
        self._placed: Optional[tuple] = None
        self._view_at: Optional[tuple[float, float]] = None
        self._last_sel: list[str] = []
        self._job: Optional[int] = None  # scriptJob の番号（mayapy など batch では scriptJob が動かず None）
        self._armed = False  # クリックで選ぶ待ち受けが有効（batch でも立つ。テスト・MCP からは `_on_selection_changed` を直接呼べる）
        self._next_try = 0.0
        self._suspended_at = 0.0

    # ------------------------------------------------------------------ 状態
    def exists(self) -> bool:
        return bool(cmds.objExists(GROUP))

    def marker(self, row: int, col: int) -> Optional[str]:
        """点のノード名（短い名前）。出ていなければ None。"""
        n = self._markers.get((row, col))
        return n if n and cmds.objExists(n) else None

    def radius_cm(self) -> float:
        return self._radius

    def export_state(self) -> dict:
        """ツールのリロードをまたぐ引き継ぎ（シーンには保存しない）。"""
        return {"show": self.enabled, "scale": self.scale, "pickable": self.pickable}

    def import_state(self, state: Optional[dict]) -> None:
        if not state:
            return
        self.scale = self._clamp(state.get("scale", DEFAULT_SCALE))
        self.pickable = bool(state.get("pickable", True))
        if state.get("show"):
            try:
                self.set_enabled(True)
            except Exception:  # noqa: BLE001  出せなくても引き継ぎは成功（設定は残る。出せるようになったら追従で出る）
                self.enabled = True

    @staticmethod
    def _clamp(v) -> float:
        try:
            return min(max(float(v), SCALE_MIN), SCALE_MAX)
        except (TypeError, ValueError):
            return DEFAULT_SCALE

    # ------------------------------------------------------------------ 出す・消す
    def set_enabled(self, on: bool) -> None:
        """出す / 消す。出せなければ SceneGridError（設定は「出さない」へ戻る）。"""
        if on:
            self.enabled = True
            try:
                self._build()
            except Exception:
                self.enabled = False
                raise
        else:
            self.enabled = False
            self.remove()

    def set_scale(self, value: float) -> None:
        self.scale = self._clamp(value)
        if self.enabled and self.exists() and not self._suspended:
            self.refresh()

    def set_pickable(self, on: bool) -> None:
        self.pickable = bool(on)
        if self.exists():
            with _quiet():
                self._apply_pickable()
            self._sync_job()

    def remove(self) -> None:
        """シーンから消し、scriptJob を外す（設定の「出す」は変えない）。データを閉じる・シーンの入れ替え・リロードで呼ぶ。"""
        self._suspended = False
        self._sync_job(force_off=True)
        self._delete_nodes()
        self._size_cache = None

    def forget(self) -> None:
        """新しいシーン / シーンを開いた直後: 古いシーンの記録を捨てる（あれば迷い込んだノードも消す）。"""
        self.remove()

    def suspend(self) -> None:
        """保存・出力の直前: 格子をシーンから外す。`resume` で同じものを戻す。"""
        if self.exists() and self.enabled:
            self._suspended = True
            self._suspended_at = time.monotonic()
            self._sync_job(force_off=True)
            self._delete_nodes()
        elif self.exists():  # 設定が切れているのに残っている（迷い込み）
            self._delete_nodes()

    def resume(self) -> None:
        """保存・出力の直後。"""
        if not self._suspended:
            return
        self._suspended = False
        if self.enabled:
            try:
                self._build()
            except Exception:  # noqa: BLE001  保存は成功させる。追従が出し直す
                self.last_error = traceback.format_exc().splitlines()[-1]

    @contextlib.contextmanager
    def hidden(self):
        """この間だけ格子を非表示にする（サムネイルの撮影など）。"""
        node = GROUP if self.exists() else None
        if node is not None:
            with _quiet():
                cmds.setAttr(node + ".visibility", False)
        try:
            yield
        finally:
            if node is not None and cmds.objExists(node):
                with _quiet():
                    cmds.setAttr(node + ".visibility", True)

    def _delete_nodes(self) -> None:
        stray = [n for n in cmds.ls("tdFacialGrid_GRP*", type="transform", long=True) or [] if cmds.attributeQuery(GRID_ATTR, node=n, exists=True)]
        if stray:
            with _quiet(), _keep_selection():
                cmds.delete(stray)
        self._markers = {}
        self._sig = None
        self._colors = {}
        self._placed = None
        self._view_at = None

    # ------------------------------------------------------------------ 作る
    def _face_size_cm(self, doc) -> float:
        meshes = self.session._resolve_meshes(doc)
        if not meshes:
            return FALLBACK_SIZE_CM
        if self._size_cache is not None and self._size_cache[0] == meshes[0]:
            return self._size_cache[1]
        try:
            b = cmds.exactWorldBoundingBox(meshes[0])
            size = max(b[3] - b[0], b[4] - b[1], b[5] - b[2])
            size = om.MDistance(size, om.MDistance.uiUnit()).asCentimeters()
        except (RuntimeError, ValueError):
            size = FALLBACK_SIZE_CM
        if not size > 1e-6:
            size = FALLBACK_SIZE_CM
        self._size_cache = (meshes[0], size)
        return size

    def _signature(self, doc) -> tuple:
        g = self.session.grid
        pts = tuple(g.angles_of(r, c) for r in range(doc.grid.rows) for c in range(doc.grid.cols))
        return (doc.grid.rows, doc.grid.cols, pts, round(self._radius, 4))

    def _build(self) -> None:
        """格子を作り直す（今ある格子は消す）。出せなければ SceneGridError（何も残さない）。"""
        s = self.session
        try:
            doc = s.require()
            rows, cols = doc.grid.rows, doc.grid.cols
            if rows < 1 or cols < 1:
                raise SceneGridError("格子の大きさが 0 です")
            center, fy, joint = s.grid_basis()
        except SceneGridError:
            raise
        except Exception as exc:  # noqa: BLE001  データ・基準ボーンが無い等
            raise SceneGridError(str(exc)) from exc
        self._joint = joint
        self._delete_nodes()
        self._radius = self._face_size_cm(doc) * self.scale
        view = s.grid.view()
        try:
            with _quiet(), _keep_selection():
                self._create(doc, rows, cols, view)
                self._place(center, fy, force=True)
        except Exception:
            self._delete_nodes()
            raise
        self._sig = self._signature(doc)
        self._sync_job()
        self.last_error = ""

    def _create(self, doc, rows: int, cols: int, view) -> None:
        s = self.session
        r_cm = self._radius
        grp = cmds.createNode("transform", name=GROUP)
        cmds.addAttr(grp, longName=GRID_ATTR, attributeType="bool", defaultValue=True)
        cmds.addAttr(grp, longName=scene_mod.PREVIEW_ONLY_ATTR, attributeType="bool", defaultValue=True)  # 書き出し・メッシュの検出から外す目印
        grp = cmds.ls(grp, long=True)[0]
        size = r_cm * MARKER_SIZE

        def local(yaw: float, pitch: float) -> tuple[float, float, float]:
            d = s.grid_direction(yaw, pitch, 0.0)  # 前方 Yaw 0 の向き（群の Y 回転で前方へ合わせる）
            return (d[0] * r_cm, d[1] * r_cm, d[2] * r_cm)

        # --- 線（行・列の弧）。API で作る（cmds.curve は選択を変えてしまう）
        dag = om.MSelectionList()
        dag.add(grp)
        parent_obj = dag.getDependNode(0)

        def arc(name: str, pairs: list[tuple[tuple[float, float], tuple[float, float]]]) -> None:
            pts: list[om.MPoint] = []
            for i, (a, b) in enumerate(pairs):
                for seg in range(0 if i == 0 else 1, SEG_PER_CELL + 1):
                    t = seg / SEG_PER_CELL
                    p = local(a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
                    pts.append(om.MPoint(*p))
            if len(pts) < 2:
                return
            fn = om.MFnNurbsCurve()
            shape = fn.create(pts, om.MDoubleArray(list(range(len(pts)))), 1, om.MFnNurbsCurve.kOpen, False, False, parent_obj)
            om.MFnDependencyNode(shape).setName(name + "Shape")
            path = om.MFnDagNode(shape).fullPathName()
            self._style(path, "line", reference=True)

        ang = s.grid.angles_of
        for r in range(rows):
            arc(f"tdFacialGrid_row{r}", [(ang(r, c), ang(r, c + 1)) for c in range(cols - 1)])
        for c in range(cols):
            arc(f"tdFacialGrid_col{c}", [(ang(r, c), ang(r + 1, c)) for r in range(rows - 1)])

        # --- 点
        def locator(name: str, pos: tuple[float, float, float], color: str, scale: float, reference: bool) -> str:
            xf = cmds.createNode("transform", name=name, parent=grp)
            shape = cmds.createNode("locator", name=name + "Shape", parent=xf)
            cmds.setAttr(xf + ".translate", *(_ui(v) for v in pos))
            cmds.setAttr(shape + ".localScale", scale, scale, scale)
            self._style(xf, color, reference)
            self._style(shape, color, reference)
            return xf

        by_pos = {(pv.row, pv.col): pv for pv in view.points} if view is not None else {}
        for r in range(rows):
            for c in range(cols):
                pv = by_pos.get((r, c))
                key = point_state_key(pv) if pv is not None else "empty"
                f = SELECTED_FACTOR if key == "selected" else 1.0
                yaw, pitch = ang(r, c)
                xf = locator(MARKER_FMT.format(r=r, c=c), local(yaw, pitch), key, _ui(size * f), reference=not self.pickable)
                self._markers[(r, c)] = xf
                self._colors[xf] = (key, f)
        locator(VIEW_MARKER, local(0.0, 0.0), "view", _ui(size * SELECTED_FACTOR), reference=True)
        self._view_at = None

    @staticmethod
    def _style(node: str, color: str, reference: bool) -> None:
        cmds.setAttr(node + ".overrideEnabled", True)
        cmds.setAttr(node + ".overrideRGBColors", 1)
        cmds.setAttr(node + ".overrideColorRGB", *COLORS[color])
        cmds.setAttr(node + ".overrideDisplayType", 2 if reference else 0)  # 2 = リファレンス（クリックで選べない）

    def _apply_pickable(self) -> None:
        for xf in self._markers.values():
            if cmds.objExists(xf):
                shapes = cmds.listRelatives(xf, shapes=True, fullPath=True) or []
                for n in [xf, *shapes]:
                    cmds.setAttr(n + ".overrideDisplayType", 0 if self.pickable else 2)

    # ------------------------------------------------------------------ 置く・更新
    def _theta(self, forward_yaw: float) -> float:
        """前方 Yaw 0 で作った点を、キャラクターの前方へ回す Maya の Y 回転（度）。"""
        a = self.session.grid_direction(0.0, 0.0, 0.0)
        b = self.session.grid_direction(0.0, 0.0, forward_yaw)
        return math.degrees(math.atan2(a[2] * b[0] - a[0] * b[2], a[0] * b[0] + a[2] * b[2]))

    def _place(self, center, forward_yaw: float, force: bool = False) -> bool:
        theta = self._theta(forward_yaw)
        key = (*center, theta)
        old = self._placed
        if not force and old is not None and max(abs(a - b) for a, b in zip(key[:3], old[:3])) < _PLACE_EPS and abs(theta - old[3]) < 1e-3:
            return False
        cmds.setAttr(GROUP + ".translate", *(_ui(v) for v in center))
        cmds.setAttr(GROUP + ".rotate", 0.0, om.MAngle(theta, om.MAngle.kDegrees).asUnits(om.MAngle.uiUnit()), 0.0)
        self._placed = key
        return True

    def refresh(self) -> None:
        """格子の見た目（点の色・選択・線・大きさ）を今のデータに合わせる。形（行列・角度・半径）が変わっていれば作り直す。"""
        if not self.enabled or self._suspended or self.session.presenters is None:
            return
        try:
            if not self.exists() or any(not cmds.objExists(n) for n in self._markers.values()):
                self._build()
                return
            doc = self.session.require()
            self._radius = self._face_size_cm(doc) * self.scale
            if self._signature(doc) != self._sig:
                self._build()
                return
            view = self.session.grid.view()
            with _quiet():
                for pv in view.points:
                    xf = self._markers.get((pv.row, pv.col))
                    if xf is None:
                        continue
                    key = point_state_key(pv)
                    f = SELECTED_FACTOR if key == "selected" else 1.0
                    if self._colors.get(xf) == (key, f):
                        continue
                    self._colors[xf] = (key, f)
                    shapes = cmds.listRelatives(xf, shapes=True, fullPath=True) or []
                    for n in [xf, *shapes]:
                        cmds.setAttr(n + ".overrideColorRGB", *COLORS[key])
                    if shapes:
                        s = _ui(self._radius * MARKER_SIZE * f)
                        cmds.setAttr(shapes[0] + ".localScale", s, s, s)
            self.last_error = ""
        except Exception as exc:  # noqa: BLE001  画面の更新の失敗で編集を止めない
            self.last_error = str(exc)
            lifecycle.report_error("シーンの格子を更新できませんでした", traceback.format_exc(), once=True)

    def follow(self, angles: Optional[tuple[float, float]] = None) -> None:
        """頭の動き・今のカメラの角度を反映する（グリッドタブの約 10 回 / 秒のタイマーから）。消えていれば（出す設定のとき）出し直す。
        angles = 今のカメラの (Yaw, Pitch)（タブが読んだもの。省くとここで読む）。"""
        if self._suspended and time.monotonic() - self._suspended_at > SUSPEND_TIMEOUT:
            self.resume()
        if not self.enabled or self._suspended or self.session.presenters is None:
            return
        if not self.exists():
            now = time.monotonic()
            if now < self._next_try:
                return
            try:
                self._build()
            except Exception as exc:  # noqa: BLE001  新しいシーンなど、まだ出せない
                self.last_error = str(exc)
                self._next_try = now + RETRY_SECONDS
            return
        try:
            center, fy, _j = self.session.grid_basis(self._joint)
            if angles is None:
                try:
                    angles = self.session.view_angles()
                except Exception:  # noqa: BLE001  カメラが無い等
                    angles = None
            moved = self._placed is None or self._place_needed(center, fy)
            view_changed = angles is not None and (
                self._view_at is None or abs(self._view_at[0] - angles[0]) > 0.01 or abs(self._view_at[1] - angles[1]) > 0.01
            )
            if not (moved or view_changed):
                return
            with _quiet():
                if moved:
                    self._place(center, fy, force=True)
                if view_changed and cmds.objExists(self._view_path()):
                    d = self.session.grid_direction(angles[0], angles[1], 0.0)
                    cmds.setAttr(self._view_path() + ".translate", *(_ui(v * self._radius) for v in d))
                    self._view_at = (angles[0], angles[1])
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)

    def _place_needed(self, center, forward_yaw: float) -> bool:
        old = self._placed
        if old is None:
            return True
        theta = self._theta(forward_yaw)
        return max(abs(a - b) for a, b in zip(center, old[:3])) >= _PLACE_EPS or abs(theta - old[3]) >= 1e-3

    @staticmethod
    def _view_path() -> str:
        return f"{GROUP}|{VIEW_MARKER}"

    # ------------------------------------------------------------------ クリックで選ぶ
    def _sync_job(self, force_off: bool = False) -> None:
        """格子が出ていて、クリックで選べるあいだだけ SelectionChanged の scriptJob を持つ。"""
        global _active
        want = not force_off and self.enabled and self.pickable and self.exists() and not self._suspended
        if want and not self._armed:
            self._last_sel = cmds.ls(selection=True, long=True) or []
            _active = self
            self._armed = True
            self._job = cmds.scriptJob(event=["SelectionChanged", _cb_selection_changed])
        elif not want and self._armed:
            if self._job is not None:
                with contextlib.suppress(RuntimeError):
                    cmds.scriptJob(kill=self._job, force=True)
            self._job = None
            self._armed = False
            if _active is self:
                _active = None

    def _restore_selection(self, names: list[str]) -> None:
        keep = [n for n in names if cmds.objExists(n)]
        with _quiet():
            if keep:
                cmds.select(keep, replace=True)
            else:
                cmds.select(clear=True)

    def _on_selection_changed(self) -> None:
        if self._busy or not self.exists():
            return
        sel = cmds.ls(selection=True, long=True) or []
        picked = [n for n in sel if marker_index(n) is not None]
        if not picked:
            self._last_sel = sel
            return
        prev = set(self._last_sel)
        added = [n for n in sel if n not in prev]
        target = marker_index(added[0]) if len(added) == 1 and added[0] in picked else None  # 1 つだけ新しく選ばれたときだけ「クリック」
        self._restore_selection(self._last_sel)  # 点は選択に残さない（クリック前の選択へ戻す）
        if target is not None:
            self._dispatch(*target)

    def _dispatch(self, row: int, col: int) -> None:
        def run() -> None:
            try:
                fn = self.on_pick or self._default_pick
                fn(row, col)
            except Exception:  # noqa: BLE001
                lifecycle.report_error(f"シーンの格子の点 R{row} C{col} を選べませんでした", traceback.format_exc(), once=False)

        try:
            batch = bool(cmds.about(batch=True))
        except RuntimeError:
            batch = True
        if batch:
            run()
        else:  # 選択の通知が終わってから（確認のダイアログを出すため）
            from maya import utils

            utils.executeDeferred(run)

    def _default_pick(self, row: int, col: int) -> None:
        self.session.select_point(row, col, move_camera=True)
