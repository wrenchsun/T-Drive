"""画面の状態（Presenter 5 種）。Qt にも Maya にも依存しない状態機械（docs/15 §3.4、R-01 R-04 R-11）。

ウィジェット（PySide の薄いビュー）は「描く + 入力を渡す」だけにする。ここが持つのは UE 版の
`SFacialGridPanel` / `SFacialPosePanel` / `SFacialLayerPanel` / `SFacialWorkingSetPanel`（の絞り込み）と、
検証タブの「画面の状態」で、Maya のシーンには触らない。シーンとの橋渡しは次のとおり。

- 読むもの: `EditContext` が持つ `Document`（参照。コピーしない）、`bake_state`（FC_* の名前 → 焼いたときの
  `validate.pose_hash`。None = 不明）、`scene`（`validate.SceneInfo`。None = 不明）、`profile`
- 書くもの: `Document` だけ。シーンの変更が要るときは、結果の `stale_morphs`（消すべき FC_* の名前）や
  `pose_to_apply()` で呼び出し側（Maya 層）に渡す。`bake_state` は**書き換えない**（Maya 層が消した / 焼いたあとに更新して
  `ctx.notify_bake_changed()` を呼ぶ）
- 通知: どの Presenter も `subscribe(callback)` で購読でき、`callback(event: str)` が変更のたびに呼ばれる
  （イベント名: grid / pose / layers / issues。EditContext は document / selection / active_layer / bake / scene /
  profile / clipboard）。購読解除は `subscribe` が返す関数を呼ぶ

Undo（Maya 層の約束）: 共有の Undo スタックはここには無い。Maya 層の session が「コマンド実行の前に Document を
スナップショットする」方式で持つ。そのため**変更するコマンドはどれも「1 回の呼び出し = 1 つの確定した変更」**にしてある:
途中で止まる複数段の操作は無く（例外: `confirm_select` は「保存」+「選択」の 2 手だが 1 回の呼び出し）、
失敗するコマンドは Document を変えずに `ok=False` の結果を返す。変更しないもの（選択・絞り込み・編集中の値・クリップボード）は
スナップショットの対象外（編集中の値はセッションが `PosePresenter` の状態として別に持つ）。

主な約束（UE 版との違いは tests/facial/conformance/README_presenter.md にまとめてある）:
- 点の状態: 空 / キー / 自動生成。色の区分は green（キー）/ cyan（自動生成）/ grey（空）/ orange（選択中）。RGB は持たない
- ベイクの状態: 空でない点について 未ベイク / ベイク後に変更あり / ベイク済み（`bake_state` と `pose_hash` で判定）
- 点の選択は、編集中のポーズに未保存の変更があるとき「確認が要る」結果を返し、保存 / 破棄 / 取りやめを待つ
- 名前の照合は大文字小文字を区別する完全一致（絞り込みの文字列だけは大小無視の部分一致。UE と同じ）
"""

from __future__ import annotations

import contextlib
import copy
import math
from dataclasses import dataclass, field
from typing import Callable, Iterator, Mapping, Optional, Sequence, Union

from . import autofill, fcpose_io, naming, space
from .categories import categorize
from . import validate as V
from .evaluate import (
    KINDA_SMALL_NUMBER,
    GridShape,
    compute_grid_cell,
    lipsync_evaluate,
    lipsync_table_curves,
    point_angles,
)
from .model import (
    LIPSYNC_BASE,
    LIPSYNC_VOLUME_MAX,
    MAX_LAYERS,
    MAX_LIPSYNC_PHONEMES,
    MAX_PERSPECTIVE_KEYS,
    PERSPECTIVE_AXES,
    BoneOffset,
    Document,
    GridPoint,
    Layer,
    LipSync,
    LipSyncEntry,
    Perspective,
    PerspectiveKey,
    PoseDocument,
    SourcePose,
)
from .profile import NamingProfile, clamp_value, curve_matches, effective_limit, has_limit

# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------

STATE_EMPTY = "empty"
STATE_KEY = "key"
STATE_GENERATED = "generated"

COLOR_KEY = "green"
COLOR_GENERATED = "cyan"
COLOR_EMPTY = "grey"
COLOR_SELECTED = "orange"
COLOR_BY_STATE = {STATE_EMPTY: COLOR_EMPTY, STATE_KEY: COLOR_KEY, STATE_GENERATED: COLOR_GENERATED}

BAKE_NONE = "none"  # 空の点（ベイクの対象外）
BAKE_UNKNOWN = "unknown"  # bake_state が渡されていない / asset が空
BAKE_UNBAKED = "unbaked"
BAKE_CHANGED = "changed"
BAKE_BAKED = "baked"
FRAME_NONE = "none"
FRAME_UNBAKED = "unbaked"
FRAME_CHANGED = "changed"

CONFIRM_SAVE = "save"
CONFIRM_DISCARD = "discard"
CONFIRM_CANCEL = "cancel"

SELECT_SELECTED = "selected"
SELECT_SAME = "same_point"  # 同じ点を押し直した: カメラ移動だけ（編集中の値は保つ。UE 準拠）
SELECT_NEEDS_CONFIRM = "needs_confirm"
SELECT_CANCELLED = "cancelled"
SELECT_INVALID = "invalid"

# 点の操作（右クリックのメニュー + ボタン）。actions() がこの名前で可否を返す
ACTION_UNKEY = "unkey"
ACTION_CLEAR = "clear"
ACTION_BAKE_POINT = "bake_point"
ACTION_COPY = "copy"
ACTION_PASTE = "paste"
ACTION_PASTE_MIRRORED = "paste_mirrored"
ACTION_CAMERA_TO_POINT = "camera_to_point"
POINT_ACTIONS = (
    ACTION_UNKEY,
    ACTION_CLEAR,
    ACTION_BAKE_POINT,
    ACTION_COPY,
    ACTION_PASTE,
    ACTION_PASTE_MIRRORED,
    ACTION_CAMERA_TO_POINT,
)

EMOTION_PRESETS = ("Anger", "Contempt", "Disgust", "Fear", "Joy", "Sadness", "Surprise")  # Neutral は常設
MAX_LAYER_COUNT = MAX_LAYERS

# 編集中のポーズ（UE 版 SFacialPosePanel と同じ許容）
CURVE_SAVE_EPS = 1e-4  # 保存で捨てるシェイプの重み（絶対値）
CURVE_DIRTY_EPS = 1e-3  # 「未保存」の判定で同じとみなす重みの差
BONE_T_EPS = 1e-3  # 平行移動（文書の単位）
BONE_R_EPS = 1e-4  # 回転（クォータニオンの成分）
BONE_S_EPS = 1e-4  # スケール（UE 版は持たない。T-Drive の追加）

_LIP_TOKEN = object()  # PosePresenter の読み込み元の指紋で「リップシンクのマス」を表す目印

SEVERITY_ORDER = (V.SEVERITY_ERROR, V.SEVERITY_WARNING, V.SEVERITY_INFO)
SEVERITY_LABEL = {V.SEVERITY_ERROR: "エラー", V.SEVERITY_WARNING: "警告", V.SEVERITY_INFO: "情報"}

Callback = Callable[[str], None]


# ---------------------------------------------------------------------------
# 通知
# ---------------------------------------------------------------------------


class Observable:
    """購読者（callback(event)）の一覧。購読者の例外は握りつぶさない（Maya 層は lifecycle.guarded で包む）。"""

    def __init__(self) -> None:
        self._listeners: list[Callback] = []

    def subscribe(self, callback: Callback) -> Callable[[], None]:
        """購読する。戻り値の関数を呼ぶと解除（二重に呼んでも安全）。"""
        self._listeners.append(callback)

        def unsubscribe() -> None:
            if callback in self._listeners:
                self._listeners.remove(callback)

        return unsubscribe

    def _emit(self, event: str) -> None:
        for cb in list(self._listeners):
            cb(event)


# ---------------------------------------------------------------------------
# 共有の文脈（Presenter 4 種が同じ Document・選択・レイヤーを見る）
# ---------------------------------------------------------------------------


@dataclass
class EditScope:
    """`EditContext.edit()` の結果。ブロックを抜けた後に読む。"""

    selected_changed: bool = False  # 選択中の点（のポーズ）が変わった
    discarded_edits: bool = False  # 未保存の編集中の値を捨てて読み直した


class EditContext(Observable):
    """Document（参照）・アクティブレイヤー・選択・クリップボード・ベイク状態・シーン情報。"""

    def __init__(
        self,
        doc: Document,
        profile: Optional[NamingProfile] = None,
        bake_state: Optional[dict[str, str]] = None,
        scene: Optional[V.SceneInfo] = None,
        bake_exclude: Optional[dict[str, str]] = None,
    ) -> None:
        super().__init__()
        self.bake_exclude = bake_exclude  # FC_* → ベイク時の除外パターンの指紋。None / 記録なし = 不明（今と同じとみなす）
        self.doc = doc
        self.profile = profile
        self.bake_state = bake_state
        self.scene = scene
        self.active_layer = 0
        self.selection: Optional[tuple[int, int]] = None
        self.key_target: Optional[int] = None  # 編集の対象がパース補正のキーのとき、その番号（このとき selection は None）
        self.lip_target: Optional[tuple[str, str]] = None  # 編集の対象がリップシンクのマスのとき、(音素, 感情。基本は "")（このとき selection / key_target は None）
        self.clipboard: Optional[SourcePose] = None
        self.pose: Optional["PosePresenter"] = None  # PosePresenter が自分で登録する

    # --- 参照 ---

    @property
    def layer(self) -> Layer:
        """アクティブレイヤー（範囲外なら最後のレイヤー）。レイヤーが 0 枚の壊れた文書では IndexError。"""
        i = min(max(self.active_layer, 0), len(self.doc.layers) - 1)
        return self.doc.layers[i]

    def in_grid(self, row: int, col: int) -> bool:
        g = self.doc.grid
        return 0 <= row < g.rows and 0 <= col < g.cols

    @property
    def has_target(self) -> bool:
        """編集の対象（格子の点・パース補正のキー・リップシンクのマス）がある。"""
        return self.selection is not None or self.key_target is not None or self.lip_target is not None

    def selected_lip(self) -> Optional[tuple[str, str]]:
        """編集の対象のリップシンクのマス (音素, 感情)。音素が一覧に無い・感情が基本でも既存の感情レイヤーでもなければ None。"""
        l = self.doc.lip_sync
        t = self.lip_target
        if l is None or t is None or t[0] not in l.phonemes:
            return None
        if t[1] != LIPSYNC_BASE and t[1] not in {x.name for x in self.doc.layers[1:]}:
            return None
        return t

    def selected_key(self) -> Optional[int]:
        """編集の対象のパース補正のキーの番号（範囲外・キーが無ければ None）。"""
        p = self.doc.perspective
        k = self.key_target
        return k if p is not None and k is not None and 0 <= k < len(p.keys) else None

    def saved_pose(self) -> Optional[SourcePose]:
        """編集の対象の保存済みポーズ（点が無ければ空のポーズ。対象が無ければ None）。対象がキーならそのキーのポーズの複製。"""
        k = self.selected_key()
        if k is not None:
            key = self.doc.perspective.keys[k]
            return SourcePose(curves=dict(key.curves), bones=copy.deepcopy(key.bones))
        lc = self.selected_lip()
        if lc is not None:  # リップシンクのマス: シェイプだけ（行が無ければ空のポーズ）
            e = self.doc.lip_sync.find_entry(*lc)
            return SourcePose(curves=dict(e.curves)) if e is not None else SourcePose()
        if self.selection is None:
            return None
        p = self.layer.points.get(self.selection)
        return p.pose if p is not None else SourcePose()

    # --- 外から差し替える（Maya 層が呼ぶ）---

    def set_document(self, doc: Document) -> None:
        """別のデータを開いた。選択・アクティブレイヤーは初期化（クリップボードは残す）。"""
        self.doc = doc
        self.active_layer = 0
        self.selection = None
        self.key_target = None
        self.lip_target = None
        self._emit("document")

    def set_bake_state(self, bake_state: Optional[dict[str, str]]) -> None:
        self.bake_state = bake_state
        self._emit("bake")

    def notify_bake_changed(self) -> None:
        """bake_state を（その場で）書き換えたあとに呼ぶ。"""
        self._emit("bake")

    def set_scene(self, scene: Optional[V.SceneInfo]) -> None:
        self.scene = scene
        self._emit("scene")

    def set_profile(self, profile: Optional[NamingProfile]) -> None:
        self.profile = profile
        self._emit("profile")

    def notify_document_changed(self) -> None:
        """Document を（Presenter を通さずに）書き換えたあと（Undo の復元など）に呼ぶ。"""
        self._emit("document")

    # --- Presenter から使う ---

    def set_selection(self, selection: Optional[tuple[int, int]]) -> None:
        self.selection = selection
        self.key_target = None  # 点を選ぶ（外す）と、キーの対象は外れる
        self.lip_target = None
        self._emit("selection")

    def set_key_target(self, index: Optional[int]) -> None:
        """編集の対象をパース補正のキーにする（None で外す）。格子の点の選択は外れる。"""
        self.key_target = index
        self.selection = None
        self.lip_target = None
        self._emit("selection")

    def set_lip_target(self, cell: Optional[tuple[str, str]]) -> None:
        """編集の対象をリップシンクのマス (音素, 感情) にする（None で外す）。点・キーの選択は外れる。"""
        self.lip_target = cell
        self.selection = None
        self.key_target = None
        self._emit("selection")

    def set_active_layer(self, index: int) -> None:
        self.active_layer = index
        self._emit("active_layer")

    def set_clipboard(self, pose: Optional[SourcePose]) -> None:
        self.clipboard = pose
        self._emit("clipboard")

    def _selected_signature(self) -> Optional[tuple]:
        if self.selected_key() is not None:
            return ("key", id(self.doc.perspective.keys[self.key_target]), V.pose_hash(self.saved_pose() or SourcePose()))
        if self.selected_lip() is not None:  # 名前は入れない（音素・感情の改名で「別の対象」にしない）
            return ("lip", V.pose_hash(self.saved_pose() or SourcePose()))
        if self.selection is None:
            return None
        return (self.active_layer, self.selection, V.pose_hash(self.saved_pose() or SourcePose()))

    @contextlib.contextmanager
    def edit(self) -> Iterator[EditScope]:
        """Document を変えるブロックを包む。抜けたら "document" を通知し、選択中の点のポーズが変わっていれば
        PosePresenter が編集中の値を読み直す（未保存の編集があれば捨てて、scope.discarded_edits が True になる）。
        ブロックの中で例外が出ても通知する（途中まで変わっているかもしれないため）。"""
        scope = EditScope()
        before = self._selected_signature()
        discards = self.pose.discard_count if self.pose is not None else 0
        try:
            yield scope
        finally:
            scope.selected_changed = before != self._selected_signature()
            self._emit("document")
            if self.pose is not None:
                scope.discarded_edits = self.pose.discard_count != discards


# ---------------------------------------------------------------------------
# 共通のヘルパー
# ---------------------------------------------------------------------------


def _is_bone_identity(b: BoneOffset) -> bool:
    return (
        all(abs(x) <= BONE_T_EPS for x in b.t)
        and abs(b.r[0]) <= BONE_R_EPS
        and abs(b.r[1]) <= BONE_R_EPS
        and abs(b.r[2]) <= BONE_R_EPS
        and abs(abs(b.r[3]) - 1.0) <= BONE_R_EPS
        and all(abs(x - 1.0) <= BONE_S_EPS for x in b.s)
    )


def _bone_equal(a: BoneOffset, b: BoneOffset) -> bool:
    if not all(abs(x - y) <= BONE_T_EPS for x, y in zip(a.t, b.t)):
        return False
    if not all(abs(x - y) <= BONE_S_EPS for x, y in zip(a.s, b.s)):
        return False
    same = all(abs(x - y) <= BONE_R_EPS for x, y in zip(a.r, b.r))
    flipped = all(abs(x + y) <= BONE_R_EPS for x, y in zip(a.r, b.r))  # q と -q は同じ回転
    return same or flipped


def poses_equal(a: SourcePose, b: SourcePose) -> bool:
    """「未保存の編集がある」の判定（UE 版 HasUnsavedEdits）。名前の和集合で比べ、無い側は 0 / 恒等として扱う。"""
    for name in set(a.curves) | set(b.curves):
        if abs(a.curves.get(name, 0.0) - b.curves.get(name, 0.0)) > CURVE_DIRTY_EPS:
            return False
    ident = BoneOffset()
    for name in set(a.bones) | set(b.bones):
        if not _bone_equal(a.bones.get(name, ident), b.bones.get(name, ident)):
            return False
    return True


def trim_pose(pose: SourcePose) -> SourcePose:
    """保存・書き出しで捨てるもの（ほぼ 0 のシェイプ・恒等のボーン）を除いた複製。"""
    out = SourcePose()
    for name, w in pose.curves.items():
        if abs(w) > CURVE_SAVE_EPS:
            out.curves[name] = w
    for name, b in pose.bones.items():
        if not _is_bone_identity(b):
            out.bones[name] = copy.deepcopy(b)
    return out


def _point_state(layer: Layer, rc: tuple[int, int]) -> str:
    p = layer.points.get(rc)
    if p is None:
        return STATE_EMPTY
    if p.is_key:
        return STATE_KEY
    return STATE_GENERATED if not p.pose.is_empty() else STATE_EMPTY


def _bake_status(ctx: EditContext, layer: Layer, rc: tuple[int, int]) -> str:
    p = layer.points.get(rc)
    if p is None or p.pose.is_empty():
        return BAKE_NONE
    bs = ctx.bake_state
    if bs is None or not ctx.doc.asset:
        return BAKE_UNKNOWN
    morph = naming.morph_name(ctx.doc.asset, layer.name, rc[0], rc[1])
    h = bs.get(morph)
    targets = ctx.scene.targets if ctx.scene is not None else None
    if h is None or (targets is not None and morph not in set(targets)):
        return BAKE_UNBAKED  # 焼いていない、または焼いたはずが無い（再インポートで消えた）
    if h != V.pose_hash(p.pose):
        return BAKE_CHANGED
    be = ctx.bake_exclude
    if be and V.bake_setting_changed(be.get(morph), V.exclude_signature(ctx.doc)):
        return BAKE_CHANGED  # 除外パターン・部位別の強さを変えたあと焼き直していない
    li = next((i for i, l in enumerate(ctx.doc.layers) if l is layer), None)
    if li is not None and V.needs_extreme(ctx.doc, li, rc):  # 誇張用 `_Ex`（重み 1 超）が要るのに焼けていない
        ex = naming.morph_name(ctx.doc.asset, layer.name, rc[0], rc[1], extreme=True)
        if bs.get(ex) != h or (targets is not None and ex not in set(targets)):
            return BAKE_CHANGED
    return BAKE_BAKED


def _baked_names(ctx: EditContext) -> set[str]:
    """全レイヤーの「ベイクの対象になる点」（格子の中で空でない）の FC_* の名前。"""
    asset = ctx.doc.asset or ""
    out: set[str] = set()
    if not asset:
        return out
    for layer in ctx.doc.layers:
        for rc, p in layer.points.items():
            if ctx.in_grid(*rc) and not p.pose.is_empty():
                out.add(naming.morph_name(asset, layer.name, rc[0], rc[1]))
                out.add(naming.morph_name(asset, layer.name, rc[0], rc[1], extreme=True))  # 誇張用（あれば。無い名前は known で除かれる）
    return out


def _known_morphs(ctx: EditContext) -> Optional[set[str]]:
    """シーンにあるはずの FC_*（bake_state ∪ scene.targets）。どちらも不明なら None。"""
    bs, targets = ctx.bake_state, (ctx.scene.targets if ctx.scene is not None else None)
    if bs is None and targets is None:
        return None
    return set(bs or ()) | set(targets or ())


def _stale_since(ctx: EditContext, before: set[str]) -> list[str]:
    """before にあって今は無い（ベイクの対象でなくなった）FC_* のうち、シーンにありそうなもの。Maya 層が消す。"""
    gone = before - _baked_names(ctx)
    known = _known_morphs(ctx)
    if known is None:
        return []
    return sorted(n for n in gone if n in known)


def layer_morph_names(ctx: EditContext, layer: Layer) -> list[str]:
    """レイヤー 1 枚分の FC_* の名前（点・誇張の `_Ex`）で、シーンにありそうなもの。
    bake_state・scene.targets から該当する名前を探し、点のポーズから作った名前と合わせる。どちらも不明なら点の名前だけ。"""
    asset = ctx.doc.asset or ""
    if not asset:
        return []
    names: set[str] = set()
    for rc, p in layer.points.items():
        if not p.pose.is_empty():
            names.add(naming.morph_name(asset, layer.name, rc[0], rc[1]))
            names.add(naming.morph_name(asset, layer.name, rc[0], rc[1], extreme=True))
    known = _known_morphs(ctx)
    if known is None:
        return sorted(n for n in names if not n.endswith(naming.EXTREME_SUFFIX))
    for n in known:
        parsed = naming.parse_name(n, asset)
        if parsed is not None and parsed.kind in (naming.KIND_POINT, naming.KIND_POINT_EX) and parsed.layer == layer.name:
            names.add(n)
    return sorted(n for n in names if n in known)


@dataclass
class CommandResult:
    """点・レイヤーの操作の結果。ok=False のとき Document は変わっていない。code は機械向けの理由（conformance で比べる）。"""

    ok: bool = True
    code: str = ""
    message: str = ""
    stale_morphs: list[str] = field(default_factory=list)  # シーンから消すべき FC_*（Maya 層が消す）
    discarded_edits: bool = False  # 未保存の編集中の値を捨てた


def _fail(code: str, message: str, cls=CommandResult, **kw):
    return cls(ok=False, code=code, message=message, **kw)


def _discard_notice(scope: EditScope) -> str:
    return "（未保存のポーズ編集は破棄されました）" if scope.discarded_edits else ""


# ---------------------------------------------------------------------------
# Presenter 1: グリッド
# ---------------------------------------------------------------------------


@dataclass
class PointView:
    row: int
    col: int
    yaw: float
    pitch: float
    state: str  # STATE_*
    color: str  # COLOR_BY_STATE（選択は見ない）
    display_color: str  # 選択中は COLOR_SELECTED
    selected: bool
    has_pose: bool
    bake: str  # BAKE_*
    frame: str  # FRAME_*（枠: 未ベイク / ベイク後に変更あり）
    morph: str  # FC_<asset>_<layer>_R{row}_C{col}（asset が空なら ""）
    tooltip: str


@dataclass
class GridSummary:
    keys: int = 0
    generated: int = 0
    empty: int = 0
    unbaked: int = 0  # 空でない点で、未ベイク
    changed: int = 0  # ベイク後に変更あり
    baked: int = 0
    total: int = 0

    @property
    def text(self) -> str:
        """UE 版の集計の行（キー / 生成 / 空 / 未ベイク）+ 変更ありの数。"""
        s = f"キー {self.keys} / 生成 {self.generated} / 空 {self.empty} / 未ベイク {self.unbaked}"
        return s + f" / 変更あり {self.changed}" if self.changed else s


@dataclass
class GridView:
    layer: str
    rows: int
    cols: int
    display_rows: list[int]  # 画面の上から下へ並べる行番号（上 = 最大 Pitch = 俯瞰、下 = あおり）
    points: list[PointView]  # 行優先（index = row * cols + col）
    selection: Optional[tuple[int, int]]
    summary: GridSummary
    legend: list[tuple[str, str, str]]  # (状態, 色の区分, 表示名)


def grid_pos_to_angles(col_pos: float, row_pos: float, yaw_range: float, pitch_range: float, cols: int, rows: int) -> tuple[float, float]:
    """格子の連続位置 → (Yaw, Pitch)。`GridPresenter.locate` の位置（赤い点の位置）の逆。
    列・行は 0..n-1 へ収める（端の外は端）。行が大きいほど +Pitch。1 列 / 1 行のときその軸は 0。"""

    def axis(pos: float, rng: float, n: int) -> float:
        if n <= 1:
            return 0.0
        pos = min(max(pos, 0.0), float(n - 1))
        return (pos / (n - 1) * 2.0 - 1.0) * rng

    return axis(col_pos, yaw_range, cols), axis(row_pos, pitch_range, rows)


@dataclass
class CameraMarker:
    """今のカメラの角度（赤い点）。座標は格子の座標（列 0..cols-1・行 0..rows-1。行が大きいほど +Pitch = 画面の上）。"""

    yaw: float
    pitch: float
    col_pos: float  # 連続値（範囲外は外側へ延長）
    row_pos: float
    col_pos_clamped: float  # 格子の内側へ収めた位置（描画用）
    row_pos_clamped: float
    clamped: bool  # 範囲（yawRange / pitchRange）の外にいる
    faded_out: bool  # フェードの幅も超えて補正が 0
    nearest_row: int
    nearest_col: int
    row0: int  # 属するセルの 4 隅
    col0: int
    row1: int
    col1: int
    row_frac: float
    col_frac: float
    fade_scale: float


@dataclass
class SelectResult:
    """点（またはレイヤー）の切り替えの結果。status が needs_confirm のとき、ビューは「保存 / 破棄 / 取りやめ」を
    ユーザーに聞いて `confirm_select` / `confirm_set_active` を呼ぶ（UE 版の YesNoCancel）。"""

    status: str
    kind: str = "point"  # "point" | "layer" | "key" | "lip"
    row: Optional[int] = None  # point: 切り替え先（needs_confirm のときは保留中の点）/ layer: 今の選択
    col: Optional[int] = None
    layer: Optional[int] = None
    yaw: Optional[float] = None  # 点の角度（カメラ移動用）
    pitch: Optional[float] = None
    index: Optional[int] = None  # kind = "key": パース補正のキーの番号
    phoneme: str = ""  # kind = "lip": リップシンクのマスの音素
    emotion: str = ""  # kind = "lip": 感情（基本は ""）
    camera_jump: bool = False  # ビューはカメラをこの点へ動かし、点のポーズをシーンへ当てる
    saved: bool = False  # 確認で「保存」してから切り替えた
    message: str = ""


class GridPresenter(Observable):
    """アクティブレイヤーの格子: 点の状態・選択・角度・集計・操作。"""

    def __init__(self, ctx: EditContext) -> None:
        super().__init__()
        self.ctx = ctx
        self.pending: Optional[tuple[int, int]] = None  # 確認待ちの点
        ctx.subscribe(self._on_ctx)

    def _on_ctx(self, event: str) -> None:
        if event in ("document", "active_layer"):
            self.pending = None
        self._emit("grid")

    # --- 点 ⇔ 角度 ---

    @property
    def grid(self):
        return self.ctx.doc.grid

    def angles_of(self, row: int, col: int) -> tuple[float, float]:
        """点 (row, col) の (Yaw, Pitch)[度]。"""
        g = self.grid
        return point_angles(g.yaw_range, g.pitch_range, g.cols, g.rows, row, col)

    def point_at(self, row: int, col: int) -> Optional[tuple[int, int]]:
        return (row, col) if self.ctx.in_grid(row, col) else None

    def nearest_point(self, yaw: float, pitch: float) -> tuple[int, int]:
        """角度に一番近い点 (row, col)。範囲の外は端の点。"""
        m = self.locate(yaw, pitch)
        return (m.nearest_row, m.nearest_col)

    def angles_at(self, col_pos: float, row_pos: float) -> tuple[float, float]:
        """格子の連続位置（列・行。locate の逆。範囲外は端へ収める）→ (Yaw, Pitch)[度]。"""
        g = self.grid
        return grid_pos_to_angles(col_pos, row_pos, g.yaw_range, g.pitch_range, g.cols, g.rows)

    def locate(self, yaw: float, pitch: float) -> CameraMarker:
        """カメラの角度 → 格子の中の位置（赤い点）・クランプの有無・属するセル。"""
        g = self.grid

        def axis(angle: float, rng: float, n: int) -> tuple[float, float]:
            if n <= 1:
                return 0.0, 0.0
            pos = ((angle / max(rng, KINDA_SMALL_NUMBER)) + 1.0) * 0.5 * (n - 1)
            return pos, min(max(pos, 0.0), float(n - 1))

        col_pos, col_c = axis(yaw, g.yaw_range, g.cols)
        row_pos, row_c = axis(pitch, g.pitch_range, g.rows)
        shape = GridShape(g.yaw_range, g.pitch_range, g.cols, g.rows, g.edge_fade)
        cell = compute_grid_cell(shape, yaw, pitch)
        return CameraMarker(
            yaw=yaw,
            pitch=pitch,
            col_pos=col_pos,
            row_pos=row_pos,
            col_pos_clamped=col_c,
            row_pos_clamped=row_c,
            clamped=abs(yaw) > g.yaw_range or abs(pitch) > g.pitch_range,
            faded_out=cell.fade_scale <= KINDA_SMALL_NUMBER,
            nearest_row=int(math.floor(row_c + 0.5)),
            nearest_col=int(math.floor(col_c + 0.5)),
            row0=cell.row0,
            col0=cell.col0,
            row1=cell.row1,
            col1=cell.col1,
            row_frac=cell.row_frac,
            col_frac=cell.col_frac,
            fade_scale=cell.fade_scale,
        )

    # --- 表示 ---

    def morph_name(self, row: int, col: int, layer_index: Optional[int] = None) -> str:
        asset = self.ctx.doc.asset or ""
        if not asset:
            return ""
        layer = self.ctx.layer if layer_index is None else self.ctx.doc.layers[layer_index]
        return naming.morph_name(asset, layer.name, row, col)

    def point_view(self, row: int, col: int) -> PointView:
        ctx = self.ctx
        layer = ctx.layer
        rc = (row, col)
        yaw, pitch = self.angles_of(row, col)
        state = _point_state(layer, rc)
        bake = _bake_status(ctx, layer, rc)
        selected = ctx.selection == rc
        p = layer.points.get(rc)
        return PointView(
            row=row,
            col=col,
            yaw=yaw,
            pitch=pitch,
            state=state,
            color=COLOR_BY_STATE[state],
            display_color=COLOR_SELECTED if selected else COLOR_BY_STATE[state],
            selected=selected,
            has_pose=p is not None and not p.pose.is_empty(),
            bake=bake,
            frame={BAKE_UNBAKED: FRAME_UNBAKED, BAKE_CHANGED: FRAME_CHANGED}.get(bake, FRAME_NONE),
            morph=self.morph_name(row, col),
            tooltip=f"Yaw {yaw:.1f}° / Pitch {pitch:.1f}°  (R{row}, C{col})",
        )

    def summary(self) -> GridSummary:
        """アクティブレイヤーの集計（UE 版の「キー / 生成 / 空 / 未ベイク」）。
        未ベイクは**空でない点だけ**数える（UE 版は空の点も数える）。"""
        g = self.grid
        s = GridSummary(total=g.rows * g.cols)
        layer = self.ctx.layer
        for row in range(g.rows):
            for col in range(g.cols):
                rc = (row, col)
                state = _point_state(layer, rc)
                if state == STATE_KEY:
                    s.keys += 1
                elif state == STATE_GENERATED:
                    s.generated += 1
                else:
                    s.empty += 1
                bake = _bake_status(self.ctx, layer, rc)
                if bake == BAKE_UNBAKED:
                    s.unbaked += 1
                elif bake == BAKE_CHANGED:
                    s.changed += 1
                elif bake == BAKE_BAKED:
                    s.baked += 1
        return s

    def view(self) -> GridView:
        g = self.grid
        return GridView(
            layer=self.ctx.layer.name,
            rows=g.rows,
            cols=g.cols,
            display_rows=list(range(g.rows - 1, -1, -1)),
            points=[self.point_view(r, c) for r in range(g.rows) for c in range(g.cols)],
            selection=self.ctx.selection,
            summary=self.summary(),
            legend=[
                (STATE_KEY, COLOR_KEY, "キー"),
                (STATE_GENERATED, COLOR_GENERATED, "自動生成"),
                (STATE_EMPTY, COLOR_EMPTY, "空"),
            ],
        )

    def actions(self, row: Optional[int] = None, col: Optional[int] = None) -> dict[str, bool]:
        """点の操作の可否（右クリックのメニュー）。row / col を省くと選択中の点。選択が無ければ全部 False
        （camera_to_point も）。UE 版: キー解除 = キーのとき、クリア / ベイク / コピー = ポーズがあるとき、貼り付け = クリップボードがあるとき。"""
        rc = self._rc(row, col)
        out = {name: False for name in POINT_ACTIONS}
        if rc is None or not self.ctx.in_grid(*rc):
            return out
        layer = self.ctx.layer
        p = layer.points.get(rc)
        has_pose = p is not None and not p.pose.is_empty()
        clip = self.ctx.clipboard
        out[ACTION_UNKEY] = bool(p is not None and p.is_key)
        out[ACTION_CLEAR] = has_pose or bool(p is not None and p.is_key)
        out[ACTION_BAKE_POINT] = has_pose and bool(self.ctx.doc.asset)
        out[ACTION_COPY] = has_pose
        out[ACTION_PASTE] = clip is not None and not clip.is_empty()
        out[ACTION_PASTE_MIRRORED] = out[ACTION_PASTE] and self.ctx.doc.mirror.enabled
        out[ACTION_CAMERA_TO_POINT] = True
        return out

    def toolbar_actions(self) -> dict[str, bool]:
        """ボタンの可否: 自動生成 = キーが 1 つ以上、キー解除 / クリア = 選択中の点で可、レイヤーのクリア = 点がある。"""
        a = self.actions()
        layer = self.ctx.layer
        return {
            "generate": any(p.is_key for rc, p in layer.points.items() if self.ctx.in_grid(*rc)),
            ACTION_UNKEY: a[ACTION_UNKEY],
            ACTION_CLEAR: a[ACTION_CLEAR],
            "clear_layer": bool(layer.points),
        }

    # --- 選択 ---

    def _rc(self, row: Optional[int], col: Optional[int]) -> Optional[tuple[int, int]]:
        if row is None or col is None:
            return self.ctx.selection
        return (row, col)

    def select(self, row: int, col: int) -> SelectResult:
        """点を選ぶ。編集中のポーズに未保存の変更があれば保留して needs_confirm を返す（別の点へ移るときだけ。
        同じ点の押し直しは camera_jump だけで編集中の値を保つ）。"""
        ctx = self.ctx
        if not ctx.in_grid(row, col):
            return SelectResult(SELECT_INVALID, row=row, col=col, message=f"点 (R{row}, C{col}) は格子の外です")
        yaw, pitch = self.angles_of(row, col)
        if ctx.selection == (row, col):
            return SelectResult(SELECT_SAME, row=row, col=col, yaw=yaw, pitch=pitch, camera_jump=True)
        if ctx.pose is not None and ctx.pose.dirty:
            self.pending = (row, col)
            return SelectResult(
                SELECT_NEEDS_CONFIRM,
                row=row,
                col=col,
                yaw=yaw,
                pitch=pitch,
                message="未保存のポーズ編集があります。保存 / 破棄 / 取りやめのどれかを選んでください",
            )
        self.pending = None
        ctx.set_selection((row, col))
        return SelectResult(SELECT_SELECTED, row=row, col=col, yaw=yaw, pitch=pitch, camera_jump=True)

    def confirm_select(self, choice: str) -> SelectResult:
        """needs_confirm への答え。choice = "save"（保存してから切り替え）/ "discard"（破棄して切り替え）/ "cancel"。"""
        ctx = self.ctx
        if self.pending is None:
            return SelectResult(SELECT_INVALID, message="確認待ちの点がありません")
        row, col = self.pending
        if choice == CONFIRM_CANCEL:
            self.pending = None
            self._emit("grid")
            r, c = ctx.selection if ctx.selection is not None else (None, None)
            return SelectResult(SELECT_CANCELLED, row=r, col=c, message="切り替えを取りやめました")
        if choice not in (CONFIRM_SAVE, CONFIRM_DISCARD):
            return SelectResult(SELECT_INVALID, row=row, col=col, message=f"未知の答え: {choice!r}")
        saved = False
        if choice == CONFIRM_SAVE and ctx.pose is not None:
            saved = ctx.pose.save().ok
        self.pending = None
        ctx.set_selection((row, col))
        yaw, pitch = self.angles_of(row, col)
        return SelectResult(
            SELECT_SELECTED, row=row, col=col, yaw=yaw, pitch=pitch, camera_jump=True, saved=saved
        )

    # --- 操作 ---

    def _target(self, row: Optional[int], col: Optional[int]) -> Union[tuple[int, int], CommandResult]:
        rc = self._rc(row, col)
        if rc is None:
            return _fail("no_selection", "点が選択されていません")
        if not self.ctx.in_grid(*rc):
            return _fail("outside_grid", f"点 (R{rc[0]}, C{rc[1]}) は格子の外です")
        return rc

    def unkey(self, row: Optional[int] = None, col: Optional[int] = None) -> CommandResult:
        """キーを外す（ポーズは残し、自動生成の点になる。次の自動生成で上書きされる）。ポーズが空なら点ごと無くなる。"""
        rc = self._target(row, col)
        if isinstance(rc, CommandResult):
            return rc
        layer = self.ctx.layer
        p = layer.points.get(rc)
        if p is None or not p.is_key:
            return _fail("not_key", "この点はキーではありません")
        with self.ctx.edit() as scope:
            p.is_key = False
            if p.pose.is_empty():
                del layer.points[rc]
        return CommandResult(message="キーを外しました" + _discard_notice(scope), discarded_edits=scope.discarded_edits)

    def clear_point(self, row: Optional[int] = None, col: Optional[int] = None) -> CommandResult:
        """点のポーズを消して空に戻す。ベイク済みの FC_* は stale_morphs で返す（Maya 層が消す）。"""
        rc = self._target(row, col)
        if isinstance(rc, CommandResult):
            return rc
        layer = self.ctx.layer
        if rc not in layer.points:
            return _fail("empty", "この点は空です")
        before = _baked_names(self.ctx)
        with self.ctx.edit() as scope:
            del layer.points[rc]
        return CommandResult(
            message="点をクリアしました" + _discard_notice(scope),
            stale_morphs=_stale_since(self.ctx, before),
            discarded_edits=scope.discarded_edits,
        )

    def clear_layer(self) -> CommandResult:
        """アクティブレイヤーの点を全部消す（格子の外の点も）。"""
        layer = self.ctx.layer
        if not layer.points:
            return _fail("empty", "レイヤーに点がありません")
        before = _baked_names(self.ctx)
        n = len(layer.points)
        with self.ctx.edit() as scope:
            layer.points.clear()
        return CommandResult(
            message=f"レイヤー「{layer.name}」の {n} 点をクリアしました" + _discard_notice(scope),
            stale_morphs=_stale_since(self.ctx, before),
            discarded_edits=scope.discarded_edits,
        )

    def generate(self, all_layers: bool = False) -> "GenerateResult":
        """キーから残りの点を自動生成（autofill.generate_from_keys）。既定はアクティブレイヤーだけ。
        生成した点は未ベイク / 変更あり になる（bake_state との比較で自然にそうなる）。"""
        ctx = self.ctx
        before = _baked_names(ctx)
        probe = autofill.AutofillSummary()
        with ctx.edit() as scope:
            probe = autofill.generate_from_keys(ctx.doc, None if all_layers else min(ctx.active_layer, len(ctx.doc.layers) - 1))
        stale = _stale_since(ctx, before)
        if probe.layers == 0:
            return GenerateResult(
                ok=False,
                code="no_keys",
                message="キーがありません。先にキーを作ってください",
                summary=probe,
            )
        return GenerateResult(
            message=f"{probe.generated} 点を自動生成しました（キー {probe.keys}、ミラーの仮想キー {probe.mirror_keys}）"
            + _discard_notice(scope),
            stale_morphs=stale,
            discarded_edits=scope.discarded_edits,
            summary=probe,
        )

    def copy_pose(self, row: Optional[int] = None, col: Optional[int] = None) -> CommandResult:
        """点のポーズをクリップボードへ（Document は変えない）。"""
        rc = self._target(row, col)
        if isinstance(rc, CommandResult):
            return rc
        p = self.ctx.layer.points.get(rc)
        if p is None or p.pose.is_empty():
            return _fail("empty", "コピーするポーズがありません")
        self.ctx.set_clipboard(copy.deepcopy(p.pose))
        return CommandResult(message="ポーズをコピーしました")

    def _paste(self, rc: tuple[int, int], pose: SourcePose) -> CommandResult:
        layer = self.ctx.layer
        before = _baked_names(self.ctx)
        with self.ctx.edit() as scope:
            p = layer.points.get(rc)
            if p is None:
                layer.points[rc] = GridPoint(rc[0], rc[1], True, pose)
            else:
                p.pose = pose
                p.is_key = True  # 明示的に貼ったポーズはキー（自動生成で上書きされない）
        return CommandResult(
            message="ポーズを貼り付けました" + _discard_notice(scope),
            stale_morphs=_stale_since(self.ctx, before),
            discarded_edits=scope.discarded_edits,
        )

    def paste_pose(self, row: Optional[int] = None, col: Optional[int] = None) -> CommandResult:
        """クリップボードのポーズを点へ貼る。その点はキーになる（UE 版はキーの印を変えない。差は README）。"""
        rc = self._target(row, col)
        if isinstance(rc, CommandResult):
            return rc
        clip = self.ctx.clipboard
        if clip is None or clip.is_empty():
            return _fail("no_clipboard", "コピーしたポーズがありません")
        return self._paste(rc, copy.deepcopy(clip))

    def paste_mirrored(self, row: Optional[int] = None, col: Optional[int] = None) -> CommandResult:
        """クリップボードのポーズを左右反転して貼る（autofill.mirror_pose。除外パターンの名前は入らない）。"""
        rc = self._target(row, col)
        if isinstance(rc, CommandResult):
            return rc
        clip = self.ctx.clipboard
        if clip is None or clip.is_empty():
            return _fail("no_clipboard", "コピーしたポーズがありません")
        if not self.ctx.doc.mirror.enabled:
            return _fail("mirror_disabled", "ミラーが無効です")
        pose = autofill.mirror_pose(clip, self.ctx.doc.mirror)
        if pose.is_empty():
            return _fail("empty_result", "反転した結果が空です（全部が除外パターンに一致）")
        return self._paste(rc, pose)

    def preview_resize(
        self, cols: int, rows: int, yaw_range: Optional[float] = None, pitch_range: Optional[float] = None
    ) -> "ResizeResult":
        """格子サイズ変更の予行（Document は変えない）。捨てられるキーを警告に使う。"""
        bad = self._check_resize(cols, rows, yaw_range, pitch_range)
        if bad is not None:
            return bad
        trial = copy.deepcopy(self.ctx.doc)
        summary = autofill.resize_grid(trial, cols, rows, yaw_range, pitch_range)
        return self._resize_result(summary, dry_run=True)

    def _check_resize(self, cols, rows, yaw_range, pitch_range) -> Optional["ResizeResult"]:
        if not (isinstance(cols, int) and isinstance(rows, int)) or cols < 1 or rows < 1:
            return _fail("invalid_size", "列・行は 1 以上の整数にしてください", ResizeResult)
        for v in (yaw_range, pitch_range):
            if v is not None and not (math.isfinite(v) and v > 0):
                return _fail("invalid_range", "角度の範囲は 0 より大きくしてください", ResizeResult)
        return None

    @staticmethod
    def _resize_result(summary: autofill.ResizeSummary, dry_run: bool = False, **kw) -> "ResizeResult":
        n = len(summary.dropped_keys)
        msg = f"キー {summary.kept_keys} 点を引き継ぎ、{summary.resampled} 点を補間し直しました"
        if n:
            msg += f"。新しい格子に重ならず捨てられるキーが {n} 個あります"
        return ResizeResult(message=msg, summary=summary, dropped_keys=list(summary.dropped_keys), dry_run=dry_run, **kw)

    def resize(
        self, cols: int, rows: int, yaw_range: Optional[float] = None, pitch_range: Optional[float] = None
    ) -> "ResizeResult":
        """格子の分割数（と範囲）を変える（autofill.resize_grid。作った点は角度で引き継ぐ）。
        捨てたキーは dropped_keys（呼ぶ前に preview_resize で警告を出せる）。選択が新しい格子の外なら解除。"""
        bad = self._check_resize(cols, rows, yaw_range, pitch_range)
        if bad is not None:
            return bad
        ctx = self.ctx
        before = _baked_names(ctx)
        with ctx.edit() as scope:
            summary = autofill.resize_grid(ctx.doc, cols, rows, yaw_range, pitch_range)
        if ctx.selection is not None and not ctx.in_grid(*ctx.selection):
            ctx.set_selection(None)
        res = self._resize_result(
            summary, stale_morphs=_stale_since(ctx, before), discarded_edits=scope.discarded_edits
        )
        if scope.discarded_edits:
            res.message += _discard_notice(scope)
        return res


@dataclass
class GenerateResult(CommandResult):
    summary: autofill.AutofillSummary = field(default_factory=autofill.AutofillSummary)


@dataclass
class ResizeResult(CommandResult):
    summary: autofill.ResizeSummary = field(default_factory=autofill.ResizeSummary)
    dropped_keys: list[autofill.DroppedKey] = field(default_factory=list)
    dry_run: bool = False


# ---------------------------------------------------------------------------
# Presenter 2: ポーズ
# ---------------------------------------------------------------------------


@dataclass
class CurveRow:
    name: str
    value: float
    lo: float  # スライダーの範囲（profile.effective_limit）
    hi: float
    edited: bool  # 0 でない
    in_working_set: bool
    missing: bool  # モデルに無い名前（scene が分かるときだけ True になりうる）
    explicit_limit: bool  # 可動域が明示されている（False = 既定の 0〜1）
    category: str = ""  # 分類の id（`categories.categorize`。文字列の絞り込みの前の一覧で決まる）
    changed: bool = False  # 保存済みのポーズと違う（未保存の変更）


@dataclass
class CategoryView:
    """ポーズタブのタブ 1 つ分（シェイプの分類）。"""

    id: str
    label: str
    total: int  # 一覧（文字列の絞り込みの前）のシェイプ数
    count: int  # 文字列の絞り込みに当たった数（タブの数字）
    edited: int  # 0 でない値の数
    changed: int  # 未保存の変更の数


@dataclass
class BoneRow:
    name: str
    t: tuple[float, float, float]
    r: tuple[float, float, float, float]  # クォータニオン [x, y, z, w]
    s: tuple[float, float, float]
    edited: bool  # 恒等でない
    in_working_set: bool
    missing: bool


@dataclass
class PoseView:
    selection: Optional[tuple[int, int]]
    layer: str
    key_index: Optional[int]  # 編集の対象がパース補正のキーのときその番号（このとき selection は None）
    dirty: bool
    status_text: str  # "" または " [未保存]"（UE 版の見出しと同じ）
    can_edit: bool
    can_save: bool
    can_mirror: bool
    working_set_only: bool
    working_set_active: bool  # 絞り込みが実際に効いている（作業セットが空でない）
    curve_filter: str
    bone_filter: str
    curves: list[CurveRow]
    bones: list[BoneRow]
    hidden_curves: int  # 作業セットの外にあって一覧に出ていない、0 でないシェイプの数
    hidden_bones: int
    edited_curves: int
    edited_bones: int
    lip: Optional[tuple[str, str]] = None  # 編集の対象がリップシンクのマスのとき (音素, 感情)（このとき selection / key_index は None。ボーンは保存されない）
    categories: list[CategoryView] = field(default_factory=list)  # 分類ごとのタブ。分けられない（1 つ以下）ときは空


@dataclass
class PoseResult(CommandResult):
    value: Optional[float] = None  # set_curve: 実際に入った値（可動域で丸めた後）
    clamped: bool = False
    becomes_key: bool = False  # save: 点がキーになった
    removed: bool = False  # save: 空のポーズなので点を消した


@dataclass
class IngestReport(CommandResult):
    changed: bool = False
    curves_set: int = 0
    bones_set: int = 0
    removed: int = 0  # replace のとき、シーン側に無くなって編集中の値から消えた数
    ignored: list[str] = field(default_factory=list)  # 作業セットの外で取り込まなかった名前
    unknown: list[str] = field(default_factory=list)  # モデルに無い名前（scene が分かるとき。取り込みはする）
    clamped: list[str] = field(default_factory=list)  # 可動域で丸めたシェイプ
    below_base: list[str] = field(default_factory=list)  # 土台の表情があるとき、シーンの値が土台より低かった（差分が負で、ポーズには入れられない）シェイプ


class PosePresenter(Observable):
    """選択中の点のポーズの編集バッファ（UFacialPoseEditBuffer 相当）。保存するまで Document に書かない。"""

    def __init__(self, ctx: EditContext) -> None:
        super().__init__()
        self.ctx = ctx
        ctx.pose = self
        self.curves: dict[str, float] = {}
        self.bones: dict[str, BoneOffset] = {}
        self.working_set_only = True  # 作業セットの絞り込み（作業セットが空なら効かない。UE 準拠）
        self.curve_filter = ""
        self.bone_filter = ""
        self.discard_count = 0  # 未保存の編集を自動で捨てて読み直した回数（EditContext.edit が見る）
        self._base: Optional[tuple] = None
        self._base_pose: Optional[SourcePose] = None
        self._load()
        ctx.subscribe(self._on_ctx)

    # --- 読み込み ---

    def _signature(self) -> Optional[tuple]:
        """読み込み元の指紋（レイヤーのオブジェクト・選択・保存済みポーズのハッシュ）。レイヤーは番号でなくオブジェクトで見る
        （レイヤーの削除で番号が詰まっても、同じレイヤーなら読み直さない）。"""
        sp = self.ctx.saved_pose()
        if sp is None:
            return None
        if self.ctx.selected_lip() is not None:  # リップシンクのマスもレイヤーに属さない（名前でなく「リップシンクの対象」として見る）
            return (_LIP_TOKEN, None, V.pose_hash(sp))
        if self.ctx.selected_key() is not None:  # キーはレイヤーに属さない
            k = self.ctx.doc.perspective.keys[self.ctx.selected_key()]
            return (k, None, V.pose_hash(sp))  # キーは番号でなくオブジェクトで見る（削除で番号が詰まっても同じキーなら読み直さない）
        return (self.ctx.layer, self.ctx.selection, V.pose_hash(sp))

    @staticmethod
    def _same_signature(a: Optional[tuple], b: Optional[tuple]) -> bool:
        if a is None or b is None:
            return a is b
        return a[0] is b[0] and a[1] == b[1] and a[2] == b[2]

    def _load(self) -> None:
        sp = self.ctx.saved_pose()
        self.curves = dict(sp.curves) if sp is not None else {}
        self.bones = copy.deepcopy(sp.bones) if sp is not None else {}
        self._base = self._signature()
        self._base_pose = copy.deepcopy(sp) if sp is not None else None

    def rebase_signature(self) -> None:
        """読み込み元の指紋だけを今に合わせる（編集中の値は触らない）。リップシンクの音素・感情の改名の途中で呼ぶ。"""
        self._base = self._signature()

    def _was_dirty(self) -> bool:
        """読み込んだときの保存済みのポーズに対して未保存の編集があったか（保存済みが差し替わった後でも使える）。"""
        return self._base_pose is not None and not poses_equal(SourcePose(self.curves, self.bones), self._base_pose)

    def _on_ctx(self, event: str) -> None:
        if event == "active_layer" and (self.ctx.selected_key() is not None or self.ctx.selected_lip() is not None):
            pass  # キーを編集中にレイヤーを切り替えても、編集中の値はそのまま
        elif event in ("selection", "active_layer"):
            # 切り替えの確認は呼ぶ側（GridPresenter / PerspectivePresenter / LayerPresenter）が済ませている。「破棄」を選んだ場合はここで捨てる
            if self._was_dirty():
                self.discard_count += 1
            self._load()
        elif event == "document":
            if not self._same_signature(self._signature(), self._base):
                if self._was_dirty() and self.dirty:
                    self.discard_count += 1
                self._load()
        self._emit("pose")

    def reload(self) -> None:
        """「読み直す」: 編集中の値を捨てて、保存済みのポーズを読み直す。"""
        self._load()
        self._emit("pose")

    # --- 状態 ---

    @property
    def has_point(self) -> bool:
        """編集の対象（格子の点・パース補正のキー・リップシンクのマス）がある。"""
        return self.ctx.has_target

    @property
    def dirty(self) -> bool:
        """未保存の編集がある（保存済みのポーズと許容を超えて違う）。値を元に戻せば False に戻る。"""
        sp = self.ctx.saved_pose()
        if sp is None:
            return False
        return not poses_equal(SourcePose(self.curves, self.bones), sp)

    def _working(self, kind: str) -> list[str]:
        ws = self.ctx.doc.working_set
        return list(ws.curves if kind == "curve" else ws.bones)

    def _restricted(self, kind: str, override: Optional[bool] = None) -> bool:
        flag = self.working_set_only if override is None else override
        return flag and bool(self._working(kind))

    def _scene_names(self, kind: str) -> Optional[set[str]]:
        sc = self.ctx.scene
        if sc is None:
            return None
        v = sc.curves if kind == "curve" else sc.bones
        return None if v is None else set(v)

    def _limit(self, name: str) -> tuple[float, float]:
        return effective_limit(self.ctx.doc, self.ctx.profile, name)

    # --- 編集 ---

    def _need_point(self) -> Optional[PoseResult]:
        if not self.has_point:
            return _fail("no_point", "点（またはパース補正のキー・リップシンクのマス）が選択されていません", PoseResult)
        return None

    def set_curve(self, name: str, value: float) -> PoseResult:
        """シェイプの重みを入れる。可動域（profile.effective_limit）で丸める。NaN は下限へ。"""
        bad = self._need_point()
        if bad:
            return bad
        lim = self._limit(name)
        v = clamp_value(float(value), lim)
        self.curves[name] = v
        self._emit("pose")
        return PoseResult(value=v, clamped=(v != value), message="" if v == value else f"可動域 [{lim[0]:g}, {lim[1]:g}] に丸めました")

    def remove_curve(self, name: str) -> PoseResult:
        bad = self._need_point()
        if bad:
            return bad
        self.curves.pop(name, None)
        self._emit("pose")
        return PoseResult()

    def set_bone(self, name: str, offset: BoneOffset) -> PoseResult:
        """ボーンのずれを入れる（親ボーン空間の加算。文書の座標系）。"""
        bad = self._need_point()
        if bad:
            return bad
        self.bones[name] = copy.deepcopy(offset)
        self._emit("pose")
        return PoseResult()

    def bone_offset(self, name: str) -> BoneOffset:
        return copy.deepcopy(self.bones.get(name, BoneOffset()))

    def reset_bone(self, name: str) -> PoseResult:
        """1 本のボーンを戻す（UE 版: 選択ボーンのリセット）。"""
        bad = self._need_point()
        if bad:
            return bad
        self.bones.pop(name, None)
        self._emit("pose")
        return PoseResult()

    def reset_bones(self) -> PoseResult:
        """ボーンを全部戻す。シェイプは触らない。"""
        bad = self._need_point()
        if bad:
            return bad
        self.bones.clear()
        self._emit("pose")
        return PoseResult()

    def zero(self) -> PoseResult:
        """ゼロに戻す（シェイプもボーンも全部）。"""
        bad = self._need_point()
        if bad:
            return bad
        self.curves.clear()
        self.bones.clear()
        self._emit("pose")
        return PoseResult()

    def mirror(self) -> PoseResult:
        """編集中のポーズを左右反転する（シェイプ名・ボーン名の L/R を入れ替え、ボーンのずれを鏡映）。
        除外パターンに一致する名前は反転せずそのまま残す（仮想キーを作る autofill.mirror_pose は除外を落とすが、
        ここは「このポーズを反転」なので消さない）。"""
        bad = self._need_point()
        if bad:
            return bad
        doc = self.ctx.doc
        if not doc.mirror.enabled:
            return _fail("mirror_disabled", "ミラーが無効です", PoseResult)
        cur = SourcePose(self.curves, self.bones)
        out = autofill.mirror_pose(cur, doc.mirror)
        for name, w in cur.curves.items():
            if autofill.is_mirror_excluded(name, doc.mirror):
                out.curves[name] = w
        for name, b in cur.bones.items():
            if autofill.is_mirror_excluded(name, doc.mirror):
                out.bones[name] = copy.deepcopy(b)
        self.curves, self.bones = out.curves, out.bones
        self._emit("pose")
        return PoseResult(message="左右を反転しました")

    # --- 保存 ---

    def trimmed(self) -> SourcePose:
        """保存・書き出しで書くポーズ（ほぼ 0 のシェイプ・恒等のボーンを除く）。"""
        return trim_pose(SourcePose(self.curves, self.bones))

    def save(self, keep_empty_key: bool = False) -> PoseResult:
        """この点へ保存する。保存した点は**キー**になり（自動生成の入力）、ベイク済みなら「変更あり」になる。
        空のポーズ（全部 0）は UE 版と同じく点を消す（keep_empty_key=True なら「補正なし」のキーとして残す）。"""
        bad = self._need_point()
        if bad:
            return bad
        ctx = self.ctx
        k = ctx.selected_key()
        if k is not None:  # 対象がパース補正のキー: そのキーのポーズへ（空でも消さない。空のキーは「補正なし」）
            pose = self.trimmed()
            changed = not poses_equal(ctx.saved_pose() or SourcePose(), pose)
            with ctx.edit():
                key = ctx.doc.perspective.keys[k]
                key.curves, key.bones = pose.curves, pose.bones
            return PoseResult(message="キーのポーズを保存しました" if changed else "キーのポーズは変わっていません")
        lc = ctx.selected_lip()
        if lc is not None:  # 対象がリップシンクのマス: シェイプだけ保存する（ボーンは捨てる）
            pose = self.trimmed()
            note = "（ボーンはリップシンクには保存されません）" if pose.bones else ""
            changed = not poses_equal(ctx.saved_pose() or SourcePose(), SourcePose(curves=pose.curves))
            existing = ctx.doc.lip_sync.find_entry(*lc)
            removed = False
            with ctx.edit():
                if not pose.curves and not keep_empty_key:
                    if existing is not None:
                        ctx.doc.lip_sync.entries.remove(existing)
                        removed = True
                elif existing is None:
                    ctx.doc.lip_sync.entries.append(LipSyncEntry(phoneme=lc[0], emotion=lc[1], curves=dict(pose.curves)))
                else:
                    existing.curves = dict(pose.curves)
            if removed:
                return PoseResult(message="行を消しました（シェイプが空）" + note, removed=True)
            return PoseResult(message=("行を保存しました" if changed else "行は変わっていません") + note)
        rc = ctx.selection
        assert rc is not None
        pose = self.trimmed()
        layer = ctx.layer
        before = _baked_names(ctx)
        removed = False
        with ctx.edit():
            if pose.is_empty() and not keep_empty_key:
                removed = layer.points.pop(rc, None) is not None
            else:
                p = layer.points.get(rc)
                if p is None:
                    layer.points[rc] = GridPoint(rc[0], rc[1], True, pose)
                else:
                    p.pose = pose
                    p.is_key = True
        return PoseResult(
            message="点を消しました（ポーズが空）" if removed else "ポーズを保存しました",
            stale_morphs=_stale_since(ctx, before),
            becomes_key=not removed and rc in layer.points,
            removed=removed,
        )

    # --- シーンとの受け渡し ---

    def pose_to_apply(self) -> SourcePose:
        """シーンへ当てるポーズ（保存されるのと同じ。ほぼ 0 は除く）。ここに無いシェイプ・ボーンは基準（0 / 元の姿勢）へ戻す
        のが Maya 層（pose_apply）の約束。除外・作業セットの外を触らない判断もそちら。"""
        t = self.trimmed()
        if self.ctx.selected_lip() is not None:
            t.bones = {}  # リップシンクのマスはシェイプだけ
        return t

    def ingest(
        self,
        curves: Mapping[str, float],
        bones: Optional[Mapping[str, BoneOffset]] = None,
        *,
        replace: bool = True,
        working_set_only: Optional[bool] = None,
    ) -> IngestReport:
        """シーンから取り込んだ値（Shape Editor・チャンネルボックス・ジョイントの動き）を編集中の値に入れる。

        - replace=True: 取り込む範囲（作業セットが効くならその中、効かなければ全部）の編集中の値を、渡された値で置き換える
          （渡されなかった名前は消える = シーンで 0 / 恒等に戻した）。範囲の外の値は触らない
        - replace=False: 渡された名前だけ上書き（0 / 恒等も書く）
        - 作業セットの外の名前は ignored に入れて取り込まない。シェイプは可動域で丸める（clamped）
        """
        bad = self._need_point()
        if bad:
            return IngestReport(ok=False, code=bad.code, message=bad.message)
        rep = IngestReport()
        bones = bones or {}
        before = (dict(self.curves), copy.deepcopy(self.bones))
        for kind, incoming in (("curve", curves), ("bone", bones)):
            restricted = self._restricted(kind, working_set_only)
            ws = set(self._working(kind))
            known = self._scene_names(kind)
            target = self.curves if kind == "curve" else self.bones
            if replace:
                for name in list(target):
                    if (not restricted or name in ws) and name not in incoming:
                        del target[name]
                        rep.removed += 1
            for name, val in incoming.items():
                if restricted and name not in ws:
                    rep.ignored.append(name)
                    continue
                if known is not None and name not in known:
                    rep.unknown.append(name)
                if kind == "curve":
                    lim = self._limit(name)
                    v = clamp_value(float(val), lim)
                    if v != val:
                        rep.clamped.append(name)
                    if replace and abs(v) <= CURVE_SAVE_EPS:
                        if target.pop(name, None) is not None:
                            rep.removed += 1
                        continue
                    target[name] = v
                    rep.curves_set += 1
                else:
                    if replace and _is_bone_identity(val):
                        target.pop(name, None)
                        continue
                    target[name] = copy.deepcopy(val)
                    rep.bones_set += 1
        rep.changed = not poses_equal(SourcePose(before[0], before[1]), SourcePose(self.curves, self.bones))
        if rep.changed:
            self._emit("pose")
        return rep

    # --- 単独ポーズの書き出し / 読み込み ---

    def export_pose_document(self) -> Optional[PoseDocument]:
        """編集中のポーズを `.fcpose.json`（format = FacialPose）の PoseDocument にする。空なら None（UE 版も書き出さない）。"""
        pose = self.trimmed()
        if pose.is_empty():
            return None
        return PoseDocument(meta=copy.deepcopy(self.ctx.doc.meta), pose=pose)

    def export_text(self) -> Optional[str]:
        doc = self.export_pose_document()
        return None if doc is None else fcpose_io.dumps(doc)

    def import_pose(
        self, source: Union[PoseDocument, str], *, working_set_only: bool = False
    ) -> IngestReport:
        """単独ポーズ（PoseDocument または JSON 文字列）を編集中の値へ読み込む（いったん全部ゼロにしてから。UE 版のアニメ読込と同じ）。
        座標系（meta）が文書と違えばボーンのずれを変換する。保存はしない（未保存の編集になる）。"""
        bad = self._need_point()
        if bad:
            return IngestReport(ok=False, code=bad.code, message=bad.message)
        try:
            pd = fcpose_io.loads(source) if isinstance(source, str) else source
        except (fcpose_io.FcposeError, ValueError) as e:
            return IngestReport(ok=False, code="invalid", message=f"読めません: {e}")
        if not isinstance(pd, PoseDocument):
            return IngestReport(ok=False, code="not_pose", message="ポーズのファイル（format = FacialPose）ではありません")
        pd = space.convert_pose_document(pd, self.ctx.doc.meta)
        before = SourcePose(dict(self.curves), copy.deepcopy(self.bones))
        self.curves.clear()
        self.bones.clear()
        rep = IngestReport()
        for kind, incoming in (("curve", pd.pose.curves), ("bone", pd.pose.bones)):
            restricted = self._restricted(kind, working_set_only)
            ws = set(self._working(kind))
            known = self._scene_names(kind)
            for name, val in incoming.items():
                if restricted and name not in ws:
                    rep.ignored.append(name)
                    continue
                if known is not None and name not in known:
                    rep.unknown.append(name)
                if kind == "curve":
                    v = clamp_value(float(val), self._limit(name))
                    if v != val:
                        rep.clamped.append(name)
                    self.curves[name] = v
                    rep.curves_set += 1
                else:
                    self.bones[name] = copy.deepcopy(val)
                    rep.bones_set += 1
        rep.changed = not poses_equal(before, SourcePose(self.curves, self.bones))
        rep.message = f"シェイプ {rep.curves_set} 本・ボーン {rep.bones_set} 本を読み込みました"
        self._emit("pose")
        return rep

    # --- 絞り込み・一覧 ---

    def set_working_set_only(self, value: bool) -> None:
        self.working_set_only = bool(value)
        self._emit("pose")

    def set_filter(self, text: str) -> None:
        """シェイプの文字列の絞り込み（大文字小文字を無視した部分一致。UE 版と同じ）。"""
        self.curve_filter = text
        self._emit("pose")

    def set_bone_filter(self, text: str) -> None:
        self.bone_filter = text
        self._emit("pose")

    def _listed_names(self, kind: str) -> tuple[list[str], int]:
        """一覧に出す名前（文字列の絞り込み前の全体、作業セットの外に隠れた編集済みの数）。"""
        ws = self._working(kind)
        buf = self.curves if kind == "curve" else self.bones
        scene = self._scene_names(kind)
        if self._restricted(kind):
            names = list(ws)
            wsset = set(ws)
            hidden = sum(
                1
                for n, v in buf.items()
                if n not in wsset and ((abs(v) > CURVE_SAVE_EPS) if kind == "curve" else (not _is_bone_identity(v)))
            )
            return names, hidden
        seen: set[str] = set()
        names: list[str] = []
        for n in sorted(scene or ()) + ws + sorted(buf):
            if n not in seen:
                seen.add(n)
                names.append(n)
        return names, 0

    @staticmethod
    def _match(text: str, name: str) -> bool:
        return not text or text.lower() in name.lower()

    def view(self) -> PoseView:
        ctx = self.ctx
        names_c, hidden_c = self._listed_names("curve")
        names_b, hidden_b = self._listed_names("bone")
        wc, wb = set(self._working("curve")), set(self._working("bone"))
        sc, sb = self._scene_names("curve"), self._scene_names("bone")
        groups = categorize(names_c, ctx.profile)
        cat_of = {n: gid for gid, _label, members in groups for n in members}
        sp = ctx.saved_pose()
        saved = sp.curves if sp is not None else {}

        def _changed(n: str) -> bool:
            return abs(self.curves.get(n, 0.0) - saved.get(n, 0.0)) > CURVE_SAVE_EPS

        curves = [
            CurveRow(
                name=n,
                value=self.curves.get(n, 0.0),
                lo=self._limit(n)[0],
                hi=self._limit(n)[1],
                edited=abs(self.curves.get(n, 0.0)) > CURVE_SAVE_EPS,
                in_working_set=n in wc,
                missing=sc is not None and n not in sc,
                explicit_limit=has_limit(ctx.doc, ctx.profile, n),
                category=cat_of.get(n, ""),
                changed=_changed(n),
            )
            for n in names_c
            if self._match(self.curve_filter, n)
        ]
        ident = BoneOffset()
        bones = []
        for n in names_b:
            if not self._match(self.bone_filter, n):
                continue
            b = self.bones.get(n, ident)
            bones.append(
                BoneRow(
                    name=n,
                    t=b.t,
                    r=b.r,
                    s=b.s,
                    edited=not _is_bone_identity(b),
                    in_working_set=n in wb,
                    missing=sb is not None and n not in sb,
                )
            )
        shown = {r.name for r in curves}
        cat_views = [
            CategoryView(
                id=gid,
                label=label,
                total=len(members),
                count=sum(1 for n in members if n in shown),
                edited=sum(1 for n in members if abs(self.curves.get(n, 0.0)) > CURVE_SAVE_EPS),
                changed=sum(1 for n in members if _changed(n)),
            )
            for gid, label, members in groups
        ]
        if len(cat_views) <= 1:
            cat_views = []
        dirty = self.dirty
        return PoseView(
            selection=ctx.selection,
            layer=ctx.layer.name,
            key_index=ctx.selected_key(),
            lip=ctx.selected_lip(),
            dirty=dirty,
            status_text=" [未保存]" if dirty else "",
            can_edit=self.has_point,
            can_save=self.has_point,
            can_mirror=self.has_point and ctx.doc.mirror.enabled,
            working_set_only=self.working_set_only,
            working_set_active=self._restricted("curve") or self._restricted("bone"),
            curve_filter=self.curve_filter,
            bone_filter=self.bone_filter,
            curves=curves,
            bones=bones,
            hidden_curves=hidden_c,
            hidden_bones=hidden_b,
            edited_curves=sum(1 for v in self.curves.values() if abs(v) > CURVE_SAVE_EPS),
            edited_bones=sum(1 for b in self.bones.values() if not _is_bone_identity(b)),
            categories=cat_views,
        )


# ---------------------------------------------------------------------------
# Presenter 3: レイヤー
# ---------------------------------------------------------------------------


@dataclass
class LayerRow:
    index: int
    name: str
    enabled: bool
    emotion_curve: str
    active: bool
    is_neutral: bool
    can_rename: bool
    can_delete: bool
    can_set_emotion_curve: bool
    points: int  # 点の数（空でない）
    keys: int
    baked: int  # ベイク済み（変更なし）の点


@dataclass
class LayerView:
    layers: list[LayerRow]
    active: int
    count: int
    limit: int
    can_add: bool
    presets: list[tuple[str, bool]]  # (名前, すでにあるか)
    copy_sources: dict[int, list[int]]  # コピー先 index → コピー元にできる index


@dataclass
class NameCheck:
    ok: bool
    name: str  # 前後の空白を除いた名前
    code: str = ""  # "" | empty | reserved | chars | duplicate | case_collision
    message: str = ""


@dataclass
class LayerResult(CommandResult):
    index: Optional[int] = None
    count: int = 0  # copy_from: コピーした点の数
    warnings: list[str] = field(default_factory=list)
    switch: Optional[SelectResult] = None  # add_or_select: アクティブの切り替えの結果（needs_confirm のことがある）


class LayerPresenter(Observable):
    """感情レイヤーの一覧・追加・改名・有効 / 無効・削除・コピー。レイヤー 0 は Neutral（改名・削除・感情カーブの設定は不可）。"""

    def __init__(self, ctx: EditContext) -> None:
        super().__init__()
        self.ctx = ctx
        self.pending: Optional[int] = None
        ctx.subscribe(self._on_ctx)

    def _on_ctx(self, event: str) -> None:
        if event == "document":
            self.pending = None
            # 外から文書が変わった（Undo など）: アクティブが範囲外なら収める
            n = len(self.ctx.doc.layers)
            if n and self.ctx.active_layer >= n:
                self.ctx.active_layer = n - 1
        self._emit("layers")

    @property
    def doc(self) -> Document:
        return self.ctx.doc

    # --- 名前 ---

    def check_name(self, name: str, ignore_index: Optional[int] = None) -> NameCheck:
        """レイヤー名の検査（validate.py のレイヤー名の規則と同じ基準）: 空 / パース補正の予約語 / 英数字と _ 以外 /
        重複 / 大文字小文字だけの違い。validate では chars と case_collision は警告だが、新しく付ける名前は止める。"""
        n = (name or "").strip()
        if not n:
            return NameCheck(False, n, "empty", "名前が空です")
        if n == naming.PERSPECTIVE_LAYER:
            return NameCheck(False, n, "reserved", f"「{n}」はパース補正の名前と衝突するため使えません")
        if not V._LAYER_NAME_OK.match(n):
            return NameCheck(False, n, "chars", "英数字と _ だけ使えます（ターゲット名・FBX で問題になります）")
        for i, layer in enumerate(self.doc.layers):
            if i == ignore_index:
                continue
            if layer.name == n:
                return NameCheck(False, n, "duplicate", f"レイヤー名「{n}」はすでにあります")
            if layer.name.lower() == n.lower():
                return NameCheck(
                    False, n, "case_collision", f"「{layer.name}」と大文字小文字だけの違いです（シェイプ名が衝突します）"
                )
        return NameCheck(True, n)

    # --- 表示 ---

    @property
    def can_add(self) -> bool:
        return len(self.doc.layers) < MAX_LAYER_COUNT

    def view(self) -> LayerView:
        ctx = self.ctx
        rows = []
        for i, layer in enumerate(self.doc.layers):
            neutral = i == 0
            pts = [(rc, p) for rc, p in layer.points.items() if not p.pose.is_empty()]
            baked = sum(1 for rc, p in pts if ctx.in_grid(*rc) and _bake_status(ctx, layer, rc) == BAKE_BAKED)
            rows.append(
                LayerRow(
                    index=i,
                    name=layer.name,
                    enabled=layer.enabled,
                    emotion_curve=layer.emotion_curve,
                    active=i == ctx.active_layer,
                    is_neutral=neutral,
                    can_rename=not neutral,
                    can_delete=not neutral,
                    can_set_emotion_curve=not neutral,
                    points=len(pts),
                    keys=sum(1 for p in layer.points.values() if p.is_key),
                    baked=baked,
                )
            )
        n = len(self.doc.layers)
        have = {l.name for l in self.doc.layers}
        return LayerView(
            layers=rows,
            active=ctx.active_layer,
            count=n,
            limit=MAX_LAYER_COUNT,
            can_add=self.can_add,
            presets=[(p, p in have) for p in EMOTION_PRESETS],
            copy_sources={i: [j for j in range(n) if j != i] for i in range(n)},
        )

    # --- アクティブレイヤー ---

    def set_active(self, index: int) -> SelectResult:
        """編集対象のレイヤーを切り替える。未保存のポーズ編集があれば needs_confirm（confirm_set_active で答える）。"""
        ctx = self.ctx
        sel = ctx.selection if ctx.selection is not None else (None, None)
        if not 0 <= index < len(self.doc.layers):
            return SelectResult(SELECT_INVALID, "layer", sel[0], sel[1], index, message="レイヤーが範囲外です")
        if index == ctx.active_layer:
            return SelectResult(SELECT_SAME, "layer", sel[0], sel[1], index)
        if ctx.pose is not None and ctx.pose.dirty and ctx.selected_key() is None and ctx.selected_lip() is None:  # キー・リップシンクのマスの編集はアクティブレイヤーに関係しない
            self.pending = index
            return SelectResult(
                SELECT_NEEDS_CONFIRM,
                "layer",
                sel[0],
                sel[1],
                index,
                message="未保存のポーズ編集があります。保存 / 破棄 / 取りやめのどれかを選んでください",
            )
        self.pending = None
        ctx.set_active_layer(index)
        return SelectResult(SELECT_SELECTED, "layer", sel[0], sel[1], index)

    def confirm_set_active(self, choice: str) -> SelectResult:
        ctx = self.ctx
        sel = ctx.selection if ctx.selection is not None else (None, None)
        if self.pending is None:
            return SelectResult(SELECT_INVALID, "layer", sel[0], sel[1], message="確認待ちのレイヤーがありません")
        index = self.pending
        if choice == CONFIRM_CANCEL:
            self.pending = None
            self._emit("layers")
            return SelectResult(SELECT_CANCELLED, "layer", sel[0], sel[1], ctx.active_layer)
        if choice not in (CONFIRM_SAVE, CONFIRM_DISCARD):
            return SelectResult(SELECT_INVALID, "layer", sel[0], sel[1], index, message=f"未知の答え: {choice!r}")
        saved = False
        if choice == CONFIRM_SAVE and ctx.pose is not None:
            saved = ctx.pose.save().ok
        self.pending = None
        ctx.set_active_layer(index)
        return SelectResult(SELECT_SELECTED, "layer", sel[0], sel[1], index, saved=saved)

    # --- 追加・改名・設定・削除・コピー ---

    def add(self, name: str) -> LayerResult:
        """レイヤーを足す（アクティブは変えない）。名前は check_name の規則。上限 16（Neutral を含む）。"""
        if not self.can_add:
            return _fail("limit", f"レイヤーは Neutral を含めて最大 {MAX_LAYER_COUNT} 枚です", LayerResult)
        chk = self.check_name(name)
        if not chk.ok:
            return _fail(chk.code, chk.message, LayerResult)
        with self.ctx.edit():
            self.doc.layers.append(Layer(name=chk.name))
        return LayerResult(index=len(self.doc.layers) - 1, message=f"レイヤー「{chk.name}」を追加しました")

    def add_or_select(self, name: str) -> LayerResult:
        """プリセット・自由名のボタンの動き（UE 版 SelectOrAddLayerByName）: 同じ名前があればそれを選び、無ければ足して選ぶ。"""
        n = (name or "").strip()
        for i, layer in enumerate(self.doc.layers):
            if layer.name == n:
                sw = self.set_active(i)
                return LayerResult(index=i, code="exists", message=f"レイヤー「{n}」を選びました", switch=sw)
        res = self.add(n)
        if not res.ok or res.index is None:
            return res
        res.switch = self.set_active(res.index)
        return res

    def _need_editable(self, index: int, what: str) -> Optional[LayerResult]:
        if not 0 <= index < len(self.doc.layers):
            return _fail("range", "レイヤーが範囲外です", LayerResult)
        if index == 0:
            return _fail("neutral", f"Neutral は{what}できません", LayerResult)
        return None

    def rename(self, index: int, name: str) -> LayerResult:
        """改名。ベイク済みの FC_<asset>_<旧名>_… は stale_morphs（Maya 層が消す。新しい名前の分は未ベイクになる）。
        warnings には UE 版と同じ注意（レイヤー名で参照している EmotionWeights 側の更新）を入れる。"""
        bad = self._need_editable(index, "改名")
        if bad:
            return bad
        layer = self.doc.layers[index]
        chk = self.check_name(name, ignore_index=index)
        if not chk.ok:
            return _fail(chk.code, chk.message, LayerResult, index=index)
        if chk.name == layer.name:
            return LayerResult(code="unchanged", index=index, message="名前は変わりません")
        old = layer.name
        stale = layer_morph_names(self.ctx, layer)
        ctx = self.ctx
        with ctx.edit():
            layer.name = chk.name
            lw = self.doc.layer_weights
            if lw and old in lw:  # 重みの出どころ（layerWeights）はレイヤー名がキー: 名前に付いていく
                lw[chk.name] = lw.pop(old)
            if self.doc.lip_sync is not None:  # リップシンクの行の感情も名前に付いていく
                for e in self.doc.lip_sync.entries:
                    if e.emotion == old:
                        e.emotion = chk.name
                if ctx.lip_target is not None and ctx.lip_target[1] == old:
                    ctx.lip_target = (ctx.lip_target[0], chk.name)
                    if ctx.pose is not None:
                        ctx.pose.rebase_signature()  # 編集中の値は同じマスのものとして残す
        return LayerResult(
            index=index,
            message=f"レイヤー名を「{old}」から「{chk.name}」にしました",
            stale_morphs=stale,
            warnings=[f"レイヤー名で感情の重みを参照している側（コンポーネントの EmotionWeights など）は「{old}」のままだと効かなくなります"],
        )

    def set_enabled(self, index: int, enabled: bool) -> LayerResult:
        """有効 / 無効（無効にするとランタイムの評価から外れる）。Neutral も切れる（UE 版と同じ）。"""
        if not 0 <= index < len(self.doc.layers):
            return _fail("range", "レイヤーが範囲外です", LayerResult)
        with self.ctx.edit():
            self.doc.layers[index].enabled = bool(enabled)
        return LayerResult(index=index)

    def set_emotion_curve(self, index: int, curve: str) -> LayerResult:
        """感情の重みの入力元の名前（emotionCurve）。Neutral は不可。バインド済みの名前を変える・消すときは warnings に注意を入れる。"""
        bad = self._need_editable(index, "感情カーブを設定")
        if bad:
            return bad
        layer = self.doc.layers[index]
        new = (curve or "").strip()
        if new == layer.emotion_curve:
            return LayerResult(code="unchanged", index=index)
        warnings = []
        if layer.emotion_curve:
            warnings.append(
                f"感情カーブ「{layer.emotion_curve}」とのバインドを解除します" if not new
                else f"感情カーブを「{layer.emotion_curve}」から「{new}」に変えます。「{layer.emotion_curve}」を参照している側は無効になります"
            )
        with self.ctx.edit():
            layer.emotion_curve = new
        return LayerResult(index=index, warnings=warnings)

    WEIGHT_SOURCES = ("direct", "curve", "distance")
    DISTANCE_DEFAULTS = {"start": 100.0, "end": 300.0, "from": 0.0, "to": 1.0}  # cm・重み（近いと 0、遠いと 1）

    def weight_source(self, index: int) -> dict:
        """レイヤーの重みの出どころ（layerWeights の 1 件の複製）。無ければ {"source": "curve"}（既定。Timeline / 部品の値から入る）。"""
        lw = self.doc.layer_weights or {}
        spec = lw.get(self.doc.layers[index].name) if 0 <= index < len(self.doc.layers) else None
        return dict(spec) if spec else {"source": "curve"}

    def set_weight_source(
        self,
        index: int,
        source: str,
        start: Optional[float] = None,
        end: Optional[float] = None,
        w_from: Optional[float] = None,
        w_to: Optional[float] = None,
    ) -> LayerResult:
        """感情レイヤーの重みの出どころ（R-35）。source = "direct" / "curve"（Timeline・部品の値から入る）/ "distance"（カメラとの距離で決まる）。
        distance のとき start / end（cm）と w_from / w_to（0〜1）: 距離 start で w_from、end で w_to、間は直線（範囲の外は端の値）。
        省いた値は今の値、無ければ既定（100 cm → 300 cm、0 → 1）。Neutral は不可。入力が正しくなければ Document は変えない。"""
        bad = self._need_editable(index, "重みの出どころを設定")
        if bad:
            return bad
        if source not in self.WEIGHT_SOURCES:
            return _fail("source", f"重みの出どころは direct / curve / distance のどれかです: {source!r}", LayerResult, index=index)
        layer = self.doc.layers[index]
        spec = dict((self.doc.layer_weights or {}).get(layer.name) or {})
        spec["source"] = source
        if source == "distance":
            vals = {}
            for key, given in (("start", start), ("end", end), ("from", w_from), ("to", w_to)):
                v = given if given is not None else spec.get(key, self.DISTANCE_DEFAULTS[key])
                if not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v):
                    return _fail("invalid", f"{key} は数値で指定してください", LayerResult, index=index)
                vals[key] = float(v)
            if vals["start"] < 0 or vals["end"] < 0:
                return _fail("invalid", "距離は 0 以上（cm）で指定してください", LayerResult, index=index)
            if not (0.0 <= vals["from"] <= 1.0 and 0.0 <= vals["to"] <= 1.0):
                return _fail("invalid", "重みは 0〜1 で指定してください", LayerResult, index=index)
            spec.update(vals)
        with self.ctx.edit():
            if self.doc.layer_weights is None:
                self.doc.layer_weights = {}
            self.doc.layer_weights[layer.name] = spec
        return LayerResult(index=index)

    def delete(self, index: int) -> LayerResult:
        """レイヤーを消す。そのレイヤーの FC_*（ベイク済み）は stale_morphs で返す（Maya 層がシーンから消す）。
        アクティブレイヤーの扱い: 消したのがアクティブなら同じ番号（最後なら一つ前）、前のレイヤーを消したら番号を 1 つ詰める。"""
        bad = self._need_editable(index, "削除")
        if bad:
            return bad
        ctx = self.ctx
        layer = self.doc.layers[index]
        stale = layer_morph_names(ctx, layer)
        name = layer.name
        n_after = len(self.doc.layers) - 1
        active = ctx.active_layer
        if index == active:
            new_active = min(index, n_after - 1)
        elif index < active:
            new_active = active - 1
        else:
            new_active = active
        with ctx.edit() as scope:
            del self.doc.layers[index]
            if self.doc.layer_weights:
                self.doc.layer_weights.pop(name, None)  # そのレイヤーの重みの出どころも消す
            if self.doc.lip_sync is not None:  # そのレイヤーの感情のリップシンクの行も消す
                self.doc.lip_sync.entries = [e for e in self.doc.lip_sync.entries if e.emotion != name]
            ctx.active_layer = new_active  # 通知はブロックを抜けるときの "document" 1 回（同じレイヤーなら編集中の値は残る）
        discarded = scope.discarded_edits
        return LayerResult(
            index=index,
            message=f"レイヤー「{name}」を削除しました" + ("（未保存のポーズ編集は破棄されました）" if discarded else ""),
            stale_morphs=stale,
            discarded_edits=discarded,
            warnings=[f"レイヤー名「{name}」を参照している側（EmotionWeights など）の設定は無効になります"],
        )

    def copy_from(self, source: int, dest: int) -> LayerResult:
        """他のレイヤーのポーズをこのレイヤーへ 1:1 でコピー（叩き台作り。UE 版 CopyLayerPoses）。
        空でない点だけ（キー / 自動生成の別も引き継ぐ）。コピー元に無い点は触らず、コピー先の同じ位置は上書き。
        コピー先は未ベイク / 変更ありになる。"""
        n = len(self.doc.layers)
        if not (0 <= source < n and 0 <= dest < n):
            return _fail("range", "レイヤーが範囲外です", LayerResult)
        if source == dest:
            return _fail("same", "コピー元とコピー先が同じです", LayerResult)
        src, dst = self.doc.layers[source], self.doc.layers[dest]
        pts = [(rc, p) for rc, p in src.points.items() if not p.pose.is_empty() and self.ctx.in_grid(*rc)]
        if not pts:
            return _fail("empty", "コピーするポーズがありません", LayerResult)
        with self.ctx.edit() as scope:
            for rc, p in pts:
                dst.points[rc] = copy.deepcopy(p)
        return LayerResult(
            index=dest,
            count=len(pts),
            message=f"「{src.name}」から「{dst.name}」へ {len(pts)} 点をコピーしました" + _discard_notice(scope),
            discarded_edits=scope.discarded_edits,
        )


# ---------------------------------------------------------------------------
# Presenter: パース補正（R-34。docs/14 §5.8b）
# ---------------------------------------------------------------------------

PERSPECTIVE_AXIS_LABEL = {"distance": "距離（cm）", "fov": "画角（度）"}
PERSPECTIVE_BAKE_TEXT = {
    BAKE_NONE: "ポーズなし（シェイプは作りません）",
    BAKE_UNKNOWN: "ベイクの状態は不明",
    BAKE_UNBAKED: "未ベイク",
    BAKE_CHANGED: "ベイク後に変更あり（再ベイクしてください）",
    BAKE_BAKED: "ベイク済み",
}
PERSPECTIVE_REMOVE_NOTE = (
    "キーを削除すると、後ろのキーの番号（シェイプ FC_<アセット>_Persp_K{n} の n）が 1 つずつ詰まります。"
    "詰まった分と、いちばん後ろのシェイプは古いままになるので、削除したあとは再ベイクしてください"
)


@dataclass
class PerspectiveKeyRow:
    index: int  # 配列の番号 = シェイプの番号 K{n} の n（0 始まり。表示は +1 でもよい）
    value: float
    shape: str  # このキーのシェイプ名（asset が空・ポーズが空なら ""）
    has_pose: bool  # curves か bones がある（無いキー = 「補正なし」の範囲を作る）
    curves: int
    bones: int
    bake: str  # BAKE_*
    bake_text: str
    weight_hint: str  # このキーの値の範囲の説明（一覧の補助）。値が正しくないときは ""
    valid: bool  # 値が軸に合っていて、同じ値のキーが先にない
    tooltip: str


@dataclass
class PerspectiveView:
    present: bool  # doc.perspective がある
    enabled: bool
    axis: str
    axis_label: str
    value_unit: str  # "cm" | "度"
    strength: float
    keys: list[PerspectiveKeyRow]
    count: int
    limit: int
    can_add: bool
    suggested_value: float  # 「キーを足す」の初期値の提案
    remove_note: str
    summary: str
    selected: Optional[int] = None  # 編集の対象のキーの番号


@dataclass
class PerspectiveResult(CommandResult):
    index: Optional[int] = None


class PerspectivePresenter(Observable):
    """パース補正の設定（使う / 軸 / 強さ）とキー（値・ポーズ）の編集。どれも「1 回の呼び出し = 1 つの確定した変更」で、
    失敗は Document を変えない（Undo は他の Presenter と同じ、呼び出し側のスナップショット方式）。
    キーの番号 = シェイプ番号なので、削除は本当の削除（後ろのキーの番号が詰まる）。詰まったあとの古いシェイプは
    validate が「ベイク後に変更」「孤立」として報告し、再ベイクで直る。"""

    def __init__(self, ctx: EditContext) -> None:
        super().__init__()
        self.ctx = ctx
        self.pending: Optional[int] = None  # 未保存の編集があって確認待ちのキー
        ctx.subscribe(self._on_ctx)

    def _on_ctx(self, event: str) -> None:
        if event in ("document", "bake", "scene", "selection"):
            self._emit("perspective")

    # --- 編集の対象（キーを選ぶ）---

    @property
    def selected(self) -> Optional[int]:
        """編集の対象のキーの番号（無ければ None）。"""
        return self.ctx.selected_key()

    def select_key(self, index: int) -> SelectResult:
        """キーを編集の対象にする（格子の点の選択は外れる）。別の対象の編集中のポーズに未保存の変更があれば needs_confirm
        （点と同じ。`confirm_select_key` で答える）。同じキーを選び直しても編集中の値は保つ。"""
        bad = self._range(index)
        if bad:
            return SelectResult(SELECT_INVALID, "key", index=index, message=bad.message)
        ctx = self.ctx
        if ctx.selected_key() == index:
            return SelectResult(SELECT_SAME, "key", index=index, camera_jump=True)
        if ctx.pose is not None and ctx.pose.dirty:
            self.pending = index
            return SelectResult(
                SELECT_NEEDS_CONFIRM,
                "key",
                index=index,
                message="未保存のポーズ編集があります。保存 / 破棄 / 取りやめのどれかを選んでください",
            )
        self.pending = None
        ctx.set_key_target(index)
        return SelectResult(SELECT_SELECTED, "key", index=index, camera_jump=True)

    def confirm_select_key(self, choice: str) -> SelectResult:
        """needs_confirm への答え。choice = "save" / "discard" / "cancel"。"""
        ctx = self.ctx
        if self.pending is None:
            return SelectResult(SELECT_INVALID, "key", message="確認待ちのキーがありません")
        index = self.pending
        if choice == CONFIRM_CANCEL:
            self.pending = None
            self._emit("perspective")
            return SelectResult(SELECT_CANCELLED, "key", index=ctx.selected_key(), message="切り替えを取りやめました")
        if choice not in (CONFIRM_SAVE, CONFIRM_DISCARD):
            return SelectResult(SELECT_INVALID, "key", index=index, message=f"未知の答え: {choice!r}")
        saved = False
        if choice == CONFIRM_SAVE and ctx.pose is not None:
            saved = ctx.pose.save().ok
        self.pending = None
        if self._range(index):  # 保存の途中でキーが無くなった
            return SelectResult(SELECT_INVALID, "key", index=index, message="パース補正のキーが範囲外です")
        ctx.set_key_target(index)
        return SelectResult(SELECT_SELECTED, "key", index=index, camera_jump=True, saved=saved)

    def deselect_key(self) -> None:
        """編集の対象のキーを外す（編集中の値は捨てる。呼ぶ側が先に確認する）。"""
        if self.ctx.key_target is not None:
            self.ctx.set_key_target(None)

    @property
    def doc(self) -> Document:
        return self.ctx.doc

    def _ensure(self) -> Perspective:
        if self.doc.perspective is None:
            self.doc.perspective = Perspective()
        return self.doc.perspective

    @property
    def axis(self) -> str:
        p = self.doc.perspective
        return p.axis if p is not None else "distance"

    def _range(self, index: int) -> Optional[PerspectiveResult]:
        p = self.doc.perspective
        if p is None or not 0 <= index < len(p.keys):
            return _fail("range", "パース補正のキーが範囲外です", PerspectiveResult)
        return None

    def check_value(self, value, ignore_index: Optional[int] = None) -> PerspectiveResult:
        """キーの値の検査（有限の数・軸に合う範囲・ほかのキーと重ならない）。ok なら index は None。"""
        axis = self.axis
        try:
            v = float(value)
        except (TypeError, ValueError):
            return _fail("value", "値は数で入力してください", PerspectiveResult)
        if not V.perspective_value_ok(axis, v):
            msg = "画角は 0 より大きく 180 より小さい度にしてください" if axis == "fov" else "距離は 0 より大きい数（cm）にしてください"
            return _fail("value", msg, PerspectiveResult)
        p = self.doc.perspective
        for i, k in enumerate(p.keys if p is not None else ()):
            if i != ignore_index and k.value == v:
                return _fail("duplicate", f"値 {v:g} のキーがすでにあります（同じ値のキーは使えません）", PerspectiveResult, index=i)
        return PerspectiveResult()

    def suggest_value(self) -> float:
        """「キーを足す」の初期値の提案（今のキーと重ならない値）。距離 = 30 cm から 50 cm ずつ、画角 = 30° から 15° ずつ。"""
        p = self.doc.perspective
        used = {k.value for k in p.keys} if p is not None else set()
        step, v = (15.0, 30.0) if self.axis == "fov" else (50.0, 30.0)
        while v in used:
            v += step
        return v

    # --- 設定 ---

    def set_enabled(self, enabled: bool) -> PerspectiveResult:
        with self.ctx.edit():
            self._ensure().enabled = bool(enabled)
        return PerspectiveResult()

    def set_axis(self, axis: str) -> PerspectiveResult:
        """軸を変える（distance / fov）。キーの値の数字はそのまま残る（単位が変わるので、値を見直す）。"""
        if axis not in PERSPECTIVE_AXES:
            return _fail("axis", f"軸は {' / '.join(PERSPECTIVE_AXES)} のどちらかです", PerspectiveResult)
        if axis == self.axis and self.doc.perspective is not None:
            return PerspectiveResult(code="unchanged")
        with self.ctx.edit():
            self._ensure().axis = axis
        p = self.doc.perspective
        msg = f"軸を「{PERSPECTIVE_AXIS_LABEL[axis]}」にしました。キーの値の数字はそのままなので、値を見直してください" if p.keys else ""
        if any(not V.perspective_value_ok(axis, k.value) for k in p.keys):
            msg += "（軸に合わない値のキーがあります）"
        return PerspectiveResult(message=msg)

    def set_strength(self, strength: float) -> PerspectiveResult:
        """パース補正の強さ（0〜1）。範囲外は失敗。"""
        try:
            s = float(strength)
        except (TypeError, ValueError):
            return _fail("strength", "強さは数で入力してください", PerspectiveResult)
        if not (math.isfinite(s) and 0.0 <= s <= 1.0):
            return _fail("strength", "強さは 0〜1 にしてください", PerspectiveResult)
        with self.ctx.edit():
            self._ensure().strength = s
        return PerspectiveResult()

    # --- キー ---

    @property
    def can_add(self) -> bool:
        p = self.doc.perspective
        return (len(p.keys) if p is not None else 0) < MAX_PERSPECTIVE_KEYS

    def add_key(self, value: float, pose: Optional[SourcePose] = None) -> PerspectiveResult:
        """キーを足す（配列の最後。番号 = 今のキーの数。ポーズは空か、渡したポーズの複製）。最大 8 個。"""
        if not self.can_add:
            return _fail("limit", f"パース補正のキーは最大 {MAX_PERSPECTIVE_KEYS} 個です", PerspectiveResult)
        chk = self.check_value(value)
        if not chk.ok:
            return chk
        key = PerspectiveKey(value=float(value))
        if pose is not None:
            t = trim_pose(pose)
            key.curves, key.bones = t.curves, t.bones
        with self.ctx.edit():
            self._ensure().keys.append(key)
        return PerspectiveResult(index=len(self.doc.perspective.keys) - 1, message=f"パース補正のキー（値 {key.value:g}）を足しました")

    def set_value(self, index: int, value: float) -> PerspectiveResult:
        """キーの値を変える（番号・ポーズは変わらない = 焼き直し不要）。"""
        bad = self._range(index)
        if bad:
            return bad
        chk = self.check_value(value, ignore_index=index)
        if not chk.ok:
            if chk.index is None:
                chk.index = index
            return chk
        key = self.doc.perspective.keys[index]
        if key.value == float(value):
            return PerspectiveResult(code="unchanged", index=index)
        with self.ctx.edit():
            key.value = float(value)
        return PerspectiveResult(index=index)

    def remove_key(self, index: int) -> PerspectiveResult:
        """キーを本当に削除する。後ろのキーの番号が 1 つ詰まるので、それらのベイク済みシェイプは古くなる
        （validate が「ベイク後に変更」で報告。いちばん後ろの番号のシェイプは孤立になり stale_morphs に入る）。再ベイクで直る。"""
        bad = self._range(index)
        if bad:
            return bad
        p = self.doc.perspective
        n_before = len(p.keys)
        value = p.keys[index].value
        last = naming.perspective_name(self.doc.asset or "", n_before - 1) if self.doc.asset else ""
        known = _known_morphs(self.ctx)
        stale = [last] if last and known is not None and last in known else []
        ctx = self.ctx
        target = ctx.key_target
        with ctx.edit():
            if target is not None:  # 選んでいたキーが消えた / 後ろのキーの番号が詰まった（先に合わせる。編集中の値は同じキーなら残る）
                ctx.key_target = None if target == index else (target - 1 if target > index else target)
            del p.keys[index]
        if target == index:
            ctx.set_key_target(None)  # 選んでいたキーを消した: 対象なし（編集中の値は消える）
        later = n_before - 1 - index
        msg = f"パース補正のキー（値 {value:g}）を削除しました"
        if later > 0:
            msg += f"。後ろの {later} 個のキーの番号が詰まったので、再ベイクしてください"
        return PerspectiveResult(index=index, message=msg, stale_morphs=stale)

    # --- キーのポーズ ---

    def key_pose(self, index: int) -> SourcePose:
        """キーのポーズの複製（編集用）。範囲外は空のポーズ。"""
        p = self.doc.perspective
        if p is None or not 0 <= index < len(p.keys):
            return SourcePose()
        k = p.keys[index]
        return SourcePose(curves=dict(k.curves), bones=copy.deepcopy(k.bones))

    def set_key_pose(self, index: int, pose: SourcePose) -> PerspectiveResult:
        """キーのポーズを置き換える（ほぼ 0 のシェイプ・恒等のボーンは捨てる）。変わらなければ code="unchanged"（Document は変えない）。
        ベイク済みなら「ベイク後に変更」になる。"""
        bad = self._range(index)
        if bad:
            return bad
        key = self.doc.perspective.keys[index]
        t = trim_pose(pose)
        if poses_equal(key.pose, t):
            return PerspectiveResult(code="unchanged", index=index)
        with self.ctx.edit():
            key.curves, key.bones = t.curves, t.bones
        return PerspectiveResult(index=index)

    def clear_key_pose(self, index: int) -> PerspectiveResult:
        """キーのポーズを空にする（「補正なし」のキーにする。シェイプは要らなくなり、焼いたものは孤立 → 再ベイクで掃除）。"""
        return self.set_key_pose(index, SourcePose())

    # --- 表示 ---

    def bake_status(self, index: int) -> str:
        """キーのベイクの状態（BAKE_*）。空のキーは BAKE_NONE、bake_state / asset が無ければ BAKE_UNKNOWN。"""
        p = self.doc.perspective
        if p is None or not 0 <= index < len(p.keys) or p.keys[index].is_empty():
            return BAKE_NONE
        ctx = self.ctx
        bs = ctx.bake_state
        if bs is None or not self.doc.asset:
            return BAKE_UNKNOWN
        morph = naming.perspective_name(self.doc.asset, index)
        targets = ctx.scene.targets if ctx.scene is not None else None
        h = bs.get(morph)
        if h is None or (targets is not None and morph not in set(targets)):
            return BAKE_UNBAKED
        if h != V.perspective_key_hash(p.keys[index]):
            return BAKE_CHANGED
        be = ctx.bake_exclude
        if be and V.bake_setting_changed(be.get(morph), V.exclude_signature(self.doc)):
            return BAKE_CHANGED
        return BAKE_BAKED

    def _hint(self, index: int) -> str:
        p = self.doc.perspective
        vals = [k.value for k in p.keys]
        v = vals[index]
        if not V.perspective_value_ok(p.axis, v):
            return ""
        if any(vals[j] == v for j in range(index)):
            return "同じ値のキーが先にあり、このキーは使われません"
        unit = "°" if p.axis == "fov" else " cm"
        ok = sorted({x for x in vals if V.perspective_value_ok(p.axis, x)})
        if len(ok) == 1:
            return "いつもこのキーのポーズ"
        pos = ok.index(v)
        lo = f"{ok[pos - 1]:g}{unit}" if pos > 0 else None
        hi = f"{ok[pos + 1]:g}{unit}" if pos < len(ok) - 1 else None
        if lo is None:
            return f"{v:g}{unit} より小さい範囲はこのキーのまま、{hi} に向けて次のキーと混ざる"
        if hi is None:
            return f"{v:g}{unit} より大きい範囲はこのキーのまま、{lo} に向けて前のキーと混ざる"
        return f"{lo} と {hi} の間で、前後のキーと混ざる"

    def view(self) -> PerspectiveView:
        p = self.doc.perspective
        axis = p.axis if p is not None else "distance"
        rows: list[PerspectiveKeyRow] = []
        asset = self.doc.asset or ""
        seen: set[float] = set()
        for i, k in enumerate(p.keys if p is not None else ()):
            ok = V.perspective_value_ok(axis, k.value) and k.value not in seen
            seen.add(k.value)
            bake = self.bake_status(i)
            has_pose = not k.is_empty()
            tip = (
                f"キー {i + 1}（シェイプ番号 K{i}）。値は軸の値、ポーズは基準の姿勢に足す形です。"
                + ("ポーズが空のキーは「補正なし」の範囲を作ります（シェイプは作りません）。" if not has_pose else "")
                + "削除すると後ろのキーの番号が詰まるので、再ベイクが要ります。"
            )
            rows.append(
                PerspectiveKeyRow(
                    index=i,
                    value=k.value,
                    shape=naming.perspective_name(asset, i) if asset and has_pose else "",
                    has_pose=has_pose,
                    curves=len(k.curves),
                    bones=len(k.bones),
                    bake=bake,
                    bake_text=PERSPECTIVE_BAKE_TEXT[bake],
                    weight_hint=self._hint(i),
                    valid=ok,
                    tooltip=tip,
                )
            )
        n = len(rows)
        enabled = bool(p.enabled) if p is not None else False
        if n == 0:
            summary = "キーがありません（パース補正は何もしません）"
        else:
            summary = f"キー {n} / {MAX_PERSPECTIVE_KEYS}" + ("" if enabled else "（使わない設定です）")
        return PerspectiveView(
            present=p is not None,
            enabled=enabled,
            axis=axis,
            axis_label=PERSPECTIVE_AXIS_LABEL.get(axis, axis),
            value_unit="度" if axis == "fov" else "cm",
            strength=p.strength if p is not None else 1.0,
            keys=rows,
            count=n,
            limit=MAX_PERSPECTIVE_KEYS,
            can_add=self.can_add,
            suggested_value=self.suggest_value(),
            remove_note=PERSPECTIVE_REMOVE_NOTE,
            summary=summary,
            selected=self.selected,
        )


# ---------------------------------------------------------------------------
# Presenter: リップシンクの対応表（R-18。docs/14 §5.8c）
# ---------------------------------------------------------------------------

LIPSYNC_BASE_LABEL = "基本"


def _lip_cell_ok(doc: Document, cell: Optional[tuple[str, str]]) -> bool:
    """マス (音素, 感情) が編集の対象にできるか（音素が一覧にあり、感情が基本か Neutral 以外の既存のレイヤー）。"""
    l = doc.lip_sync
    if l is None or cell is None or cell[0] not in l.phonemes:
        return False
    return cell[1] == LIPSYNC_BASE or cell[1] in {x.name for x in doc.layers[1:]}


@dataclass
class LipSyncColumn:
    name: str  # 感情レイヤー名（基本は ""）
    label: str  # 表示名（基本は「基本」）
    is_base: bool


@dataclass
class LipSyncCell:
    phoneme: str
    emotion: str  # 基本は ""
    has_entry: bool  # 行がある（空の行も「ある」。基本の行が無いマスは「なし」）
    curves: int  # 行のシェイプの数
    valid: bool  # 行があって問題が無い（同じマスの行が 2 つ・有限でない値は False）。行が無いマスは True
    selected: bool  # 編集の対象
    tooltip: str


@dataclass
class LipSyncRow:
    phoneme: str
    index: int
    valid: bool  # 名前が空でなく重複していない
    entries: int  # この音素の行の数（基本 + 感情）
    cells: list[LipSyncCell]  # columns と同じ並び


@dataclass
class LipSyncView:
    present: bool  # doc.lip_sync がある
    enabled: bool
    strength: float
    volume_min: float
    volume_max: float
    volume_from: float
    volume_to: float
    follow: float
    columns: list[LipSyncColumn]  # 基本 + Neutral 以外の感情レイヤー
    rows: list[LipSyncRow]
    phonemes: list[str]
    count: int
    limit: int
    can_add: bool
    summary: str
    selected: Optional[tuple[str, str]] = None  # 編集の対象のマス
    orphan_entries: int = 0  # 音素の一覧・感情レイヤーのどちらかに無い行の数（表には出ない。検証が知らせる）


@dataclass
class LipSyncResult(CommandResult):
    phoneme: str = ""
    emotion: str = ""
    created: int = 0  # create_from_profile: 作った（置き換えた）基本の行の数
    kept: list[str] = field(default_factory=list)  # create_from_profile: すでに基本の行があって残した音素
    missing: list[tuple[str, str]] = field(default_factory=list)  # create_from_profile: モデルに無くて入れなかった (音素, シェイプ名)
    ambiguous: list[tuple[str, str, str]] = field(default_factory=list)  # (音素, プロファイルの名前, 選んだシェイプ) 同じ名前が複数のノードにあり、先頭を選んだ


class LipSyncPresenter(Observable):
    """リップシンクの対応表（使う / 強さ / 声量 / 追従 / 音素の一覧 / 行のポーズ）の編集と、表の状態。
    どれも「1 回の呼び出し = 1 つの確定した変更」で、失敗は Document を変えない（Undo は呼び出し側のスナップショット方式）。
    マスを選ぶと編集の対象になる（点・パース補正のキーと同じ流れ。保存はシェイプだけ）。"""

    def __init__(self, ctx: EditContext) -> None:
        super().__init__()
        self.ctx = ctx
        self.pending: Optional[tuple[str, str]] = None  # 未保存の編集があって確認待ちのマス
        ctx.subscribe(self._on_ctx)

    def _on_ctx(self, event: str) -> None:
        if event in ("document", "scene", "selection", "profile"):
            self._emit("lipsync")

    @property
    def doc(self) -> Document:
        return self.ctx.doc

    def _ensure(self) -> LipSync:
        if self.doc.lip_sync is None:
            self.doc.lip_sync = LipSync()
        return self.doc.lip_sync

    # --- 編集の対象（マスを選ぶ）---

    @property
    def selected(self) -> Optional[tuple[str, str]]:
        return self.ctx.selected_lip()

    def columns(self) -> list[LipSyncColumn]:
        out = [LipSyncColumn(LIPSYNC_BASE, LIPSYNC_BASE_LABEL, True)]
        out += [LipSyncColumn(l.name, l.name, False) for l in self.doc.layers[1:]]
        return out

    def _cell_check(self, phoneme: str, emotion: str) -> Optional[LipSyncResult]:
        if self.doc.lip_sync is None or phoneme not in self.doc.lip_sync.phonemes:
            return _fail("phoneme", f"音素「{phoneme}」が一覧にありません", LipSyncResult, phoneme=phoneme, emotion=emotion)
        if emotion != LIPSYNC_BASE and emotion not in {x.name for x in self.doc.layers[1:]}:
            return _fail("emotion", f"感情「{emotion}」のレイヤーがありません", LipSyncResult, phoneme=phoneme, emotion=emotion)
        return None

    def select_lip_cell(self, phoneme: str, emotion: str = LIPSYNC_BASE) -> SelectResult:
        """マスを編集の対象にする（点・キーの選択は外れる）。別の対象の編集中のポーズに未保存の変更があれば needs_confirm
        （`confirm_select_lip_cell` で答える）。同じマスを選び直しても編集中の値は保つ。"""
        bad = self._cell_check(phoneme, emotion)
        if bad:
            return SelectResult(SELECT_INVALID, "lip", phoneme=phoneme, emotion=emotion, message=bad.message)
        ctx = self.ctx
        if ctx.selected_lip() == (phoneme, emotion):
            return SelectResult(SELECT_SAME, "lip", phoneme=phoneme, emotion=emotion)
        if ctx.pose is not None and ctx.pose.dirty:
            self.pending = (phoneme, emotion)
            return SelectResult(
                SELECT_NEEDS_CONFIRM,
                "lip",
                phoneme=phoneme,
                emotion=emotion,
                message="未保存のポーズ編集があります。保存 / 破棄 / 取りやめのどれかを選んでください",
            )
        self.pending = None
        ctx.set_lip_target((phoneme, emotion))
        return SelectResult(SELECT_SELECTED, "lip", phoneme=phoneme, emotion=emotion)

    def confirm_select_lip_cell(self, choice: str) -> SelectResult:
        """needs_confirm への答え。choice = "save" / "discard" / "cancel"。"""
        ctx = self.ctx
        if self.pending is None:
            return SelectResult(SELECT_INVALID, "lip", message="確認待ちのマスがありません")
        phoneme, emotion = self.pending
        if choice == CONFIRM_CANCEL:
            self.pending = None
            self._emit("lipsync")
            cur = ctx.selected_lip() or ("", "")
            return SelectResult(SELECT_CANCELLED, "lip", phoneme=cur[0], emotion=cur[1], message="切り替えを取りやめました")
        if choice not in (CONFIRM_SAVE, CONFIRM_DISCARD):
            return SelectResult(SELECT_INVALID, "lip", phoneme=phoneme, emotion=emotion, message=f"未知の答え: {choice!r}")
        saved = False
        if choice == CONFIRM_SAVE and ctx.pose is not None:
            saved = ctx.pose.save().ok
        self.pending = None
        if self._cell_check(phoneme, emotion):  # 保存の途中でマスが無くなった
            return SelectResult(SELECT_INVALID, "lip", phoneme=phoneme, emotion=emotion, message="そのマスはもうありません")
        ctx.set_lip_target((phoneme, emotion))
        return SelectResult(SELECT_SELECTED, "lip", phoneme=phoneme, emotion=emotion, saved=saved)

    def deselect_lip_cell(self) -> None:
        """編集の対象のマスを外す（編集中の値は捨てる。呼ぶ側が先に確認する）。"""
        if self.ctx.lip_target is not None:
            self.ctx.set_lip_target(None)

    # --- 設定 ---

    def set_enabled(self, enabled: bool) -> LipSyncResult:
        with self.ctx.edit():
            self._ensure().enabled = bool(enabled)
        return LipSyncResult()

    def set_strength(self, strength: float) -> LipSyncResult:
        """全体の強さ（0〜1）。範囲外は失敗。"""
        try:
            v = float(strength)
        except (TypeError, ValueError):
            return _fail("strength", "強さは数で入力してください", LipSyncResult)
        if not (math.isfinite(v) and 0.0 <= v <= 1.0):
            return _fail("strength", "強さは 0〜1 にしてください", LipSyncResult)
        with self.ctx.edit():
            self._ensure().strength = v
        return LipSyncResult()

    def set_volume(
        self,
        min: Optional[float] = None,  # noqa: A002
        max: Optional[float] = None,  # noqa: A002
        from_: Optional[float] = None,
        to: Optional[float] = None,
    ) -> LipSyncResult:
        """声量の設定。省いた値は今のまま。min / max は 0 以上、from_ / to（倍率）は 0〜2。max <= min は許す（しきい値になる）。"""
        cur = self.doc.lip_sync.volume if self.doc.lip_sync is not None else LipSync().volume
        vals = {"min": cur.min, "max": cur.max, "from_": cur.from_, "to": cur.to}
        for key, given in (("min", min), ("max", max), ("from_", from_), ("to", to)):
            if given is None:
                continue
            try:
                v = float(given)
            except (TypeError, ValueError):
                return _fail("volume", "声量の設定は数で入力してください", LipSyncResult)
            if not math.isfinite(v):
                return _fail("volume", "声量の設定は有限の数にしてください", LipSyncResult)
            vals[key] = v
        if vals["min"] < 0.0 or vals["max"] < 0.0:
            return _fail("volume", "声量の範囲（最小・最大）は 0 以上にしてください", LipSyncResult)
        if not (0.0 <= vals["from_"] <= LIPSYNC_VOLUME_MAX and 0.0 <= vals["to"] <= LIPSYNC_VOLUME_MAX):
            return _fail("volume", f"声量の倍率は 0〜{LIPSYNC_VOLUME_MAX:g} にしてください", LipSyncResult)
        with self.ctx.edit():
            v = self._ensure().volume
            v.min, v.max, v.from_, v.to = vals["min"], vals["max"], vals["from_"], vals["to"]
        return LipSyncResult()

    def set_follow(self, follow: float) -> LipSyncResult:
        """追従の速さ（1/秒。0 = 即時）。負の数・有限でない数は失敗。"""
        try:
            v = float(follow)
        except (TypeError, ValueError):
            return _fail("follow", "追従の速さは数で入力してください", LipSyncResult)
        if not (math.isfinite(v) and v >= 0.0):
            return _fail("follow", "追従の速さは 0 以上にしてください（0 = 即時）", LipSyncResult)
        with self.ctx.edit():
            self._ensure().follow = v
        return LipSyncResult()

    # --- 音素 ---

    @property
    def can_add(self) -> bool:
        l = self.doc.lip_sync
        return (len(l.phonemes) if l is not None else 0) < MAX_LIPSYNC_PHONEMES

    def check_phoneme_name(self, name: str, ignore: Optional[str] = None) -> NameCheck:
        """音素名の検査: 前後の空白を除いて空でない・ほかの音素と重ならない（大文字小文字は区別する。何でも使える文字列）。"""
        n = (name or "").strip()
        if not n:
            return NameCheck(False, n, "empty", "名前が空です")
        l = self.doc.lip_sync
        if any(p == n and p != ignore for p in (l.phonemes if l is not None else ())):
            return NameCheck(False, n, "duplicate", f"音素「{n}」はすでにあります")
        return NameCheck(True, n)

    def add_phoneme(self, name: str) -> LipSyncResult:
        """音素を足す（最後。最大 32）。リップシンクのデータが無ければ作る。"""
        if not self.can_add:
            return _fail("limit", f"音素は最大 {MAX_LIPSYNC_PHONEMES} 個です", LipSyncResult)
        chk = self.check_phoneme_name(name)
        if not chk.ok:
            return _fail(chk.code, chk.message, LipSyncResult)
        with self.ctx.edit():
            self._ensure().phonemes.append(chk.name)
        return LipSyncResult(phoneme=chk.name, message=f"音素「{chk.name}」を足しました")

    def rename_phoneme(self, old: str, new: str) -> LipSyncResult:
        """音素の名前を変える（その音素の行も付いていく。編集の対象が同じ音素なら対象も付いていく）。"""
        l = self.doc.lip_sync
        if l is None or old not in l.phonemes:
            return _fail("phoneme", f"音素「{old}」が一覧にありません", LipSyncResult, phoneme=old)
        chk = self.check_phoneme_name(new, ignore=old)
        if not chk.ok:
            return _fail(chk.code, chk.message, LipSyncResult, phoneme=old)
        if chk.name == old:
            return LipSyncResult(code="unchanged", phoneme=old, message="名前は変わりません")
        ctx = self.ctx
        with ctx.edit():
            l.phonemes[l.phonemes.index(old)] = chk.name
            for e in l.entries:
                if e.phoneme == old:
                    e.phoneme = chk.name
            if ctx.lip_target is not None and ctx.lip_target[0] == old:
                ctx.lip_target = (chk.name, ctx.lip_target[1])
                if ctx.pose is not None:
                    ctx.pose.rebase_signature()
        return LipSyncResult(
            phoneme=chk.name,
            message=f"音素の名前を「{old}」から「{chk.name}」にしました",
        )

    def remove_phoneme(self, name: str) -> LipSyncResult:
        """音素を消す（その音素の行も消える。編集の対象がその音素なら対象なしになり、編集中の値は消える）。"""
        l = self.doc.lip_sync
        if l is None or name not in l.phonemes:
            return _fail("phoneme", f"音素「{name}」が一覧にありません", LipSyncResult, phoneme=name)
        ctx = self.ctx
        was_target = ctx.lip_target is not None and ctx.lip_target[0] == name
        n_rows = sum(1 for e in l.entries if e.phoneme == name)
        with ctx.edit():
            l.phonemes = [p for p in l.phonemes if p != name]
            l.entries = [e for e in l.entries if e.phoneme != name]
        if was_target:
            ctx.set_lip_target(None)
        msg = f"音素「{name}」を削除しました" + (f"（行 {n_rows} 個も消えました）" if n_rows else "")
        return LipSyncResult(phoneme=name, message=msg)

    def move_phoneme(self, name: str, to_index: int) -> LipSyncResult:
        """音素を並べ替える（to_index = 移動後の位置。範囲外は端に収める）。"""
        l = self.doc.lip_sync
        if l is None or name not in l.phonemes:
            return _fail("phoneme", f"音素「{name}」が一覧にありません", LipSyncResult, phoneme=name)
        cur = l.phonemes.index(name)
        to = max(0, min(len(l.phonemes) - 1, int(to_index)))
        if to == cur:
            return LipSyncResult(code="unchanged", phoneme=name)
        with self.ctx.edit():
            l.phonemes.pop(cur)
            l.phonemes.insert(to, name)
        return LipSyncResult(phoneme=name)

    # --- マスのポーズ ---

    def cell_pose(self, phoneme: str, emotion: str = LIPSYNC_BASE) -> SourcePose:
        """マスのポーズの複製（編集用。シェイプだけ）。行が無い・マスが範囲外なら空のポーズ。"""
        l = self.doc.lip_sync
        e = l.find_entry(phoneme, emotion) if l is not None else None
        return SourcePose(curves=dict(e.curves)) if e is not None else SourcePose()

    def set_cell_pose(self, phoneme: str, emotion: str, pose: SourcePose, keep_empty: bool = False) -> LipSyncResult:
        """マスのポーズを置き換える（シェイプだけ。ボーンは捨てる。ほぼ 0 のシェイプも捨てる）。空のポーズは行を消す
        （keep_empty=True なら「全部 0」の行として残す。感情の行で「基本を使わず口を閉じる」を表したいとき）。
        変わらなければ code="unchanged"（Document は変えない）。"""
        bad = self._cell_check(phoneme, emotion)
        if bad:
            return bad
        l = self.doc.lip_sync
        curves = trim_pose(SourcePose(curves=dict(pose.curves))).curves
        e = l.find_entry(phoneme, emotion)
        if e is None and not curves and not keep_empty:
            return LipSyncResult(code="unchanged", phoneme=phoneme, emotion=emotion)
        if e is not None and curves and poses_equal(e.pose, SourcePose(curves=curves)):
            return LipSyncResult(code="unchanged", phoneme=phoneme, emotion=emotion)
        with self.ctx.edit():
            if not curves and not keep_empty:
                if e is not None:
                    l.entries.remove(e)
            elif e is None:
                l.entries.append(LipSyncEntry(phoneme=phoneme, emotion=emotion, curves=curves))
            else:
                e.curves = curves
        return LipSyncResult(phoneme=phoneme, emotion=emotion)

    def clear_cell(self, phoneme: str, emotion: str = LIPSYNC_BASE) -> LipSyncResult:
        """マスの行を消す（無ければ unchanged）。"""
        l = self.doc.lip_sync
        e = l.find_entry(phoneme, emotion) if l is not None else None
        if e is None:
            return LipSyncResult(code="unchanged", phoneme=phoneme, emotion=emotion)
        with self.ctx.edit():
            l.entries.remove(e)
        return LipSyncResult(phoneme=phoneme, emotion=emotion, message="行を消しました")

    # --- プロファイルから作る ---

    def _resolve_shape(self, name: str, available: Optional[Sequence[str]]) -> tuple[Optional[str], bool]:
        """プロファイルのシェイプ名をシーンの名前にする（curve_matches と同じ規則）。戻り = (名前 / 無ければ None, 複数候補から先頭を選んだか)。
        available が None（シーンの情報なし）のときは、書かれた名前のまま使う。"""
        if available is None:
            return name, False
        have = sorted(set(available))
        if name in have:
            return name, False
        if "." in name:
            return None, False
        hits = [a for a in have if "." in a and a.partition(".")[2] == name]
        if not hits:
            return (name, False) if name in have else (None, False)
        return hits[0], len(hits) > 1

    def create_from_profile(
        self,
        profile: Optional[NamingProfile] = None,
        available: Optional[Sequence[str]] = None,
        overwrite: bool = False,
    ) -> LipSyncResult:
        """プロファイルの `lipSync`（音素 → シェイプ名）から基本の行を作る。profile を省くと ctx.profile、available を省くと ctx.scene.curves。
        - 音素が一覧に無ければ足す（上限は超えない）。基本の行がすでにある音素は残す（overwrite=True なら置き換える。感情の行は触らない）
        - シェイプ名はシーンの名前に合わせる（ノード名なしの名前はどのノードの同名ターゲットにも一致。複数なら先頭を選んで ambiguous に入れる）。
          モデルに無いシェイプは入れず missing に入れる。シーンの情報が無ければ書かれた名前のまま
        - リップシンクのデータが無ければ作る（使う = オン）。1 つも作れなければ失敗（Document は変えない）"""
        prof = profile if profile is not None else self.ctx.profile
        if prof is None or not prof.lip_sync:
            return _fail("no_profile", "プロファイルにリップシンクの対応（音素 → シェイプ）がありません", LipSyncResult)
        if available is None and self.ctx.scene is not None and self.ctx.scene.curves is not None:
            available = list(self.ctx.scene.curves)
        l = self.doc.lip_sync
        existing = list(l.phonemes) if l is not None else []
        plan: list[tuple[str, dict[str, float]]] = []
        kept: list[str] = []
        missing: list[tuple[str, str]] = []
        ambiguous: list[tuple[str, str, str]] = []
        new_phonemes = list(existing)
        for phoneme, shapes in prof.lip_sync.items():
            if phoneme not in new_phonemes:
                if len(new_phonemes) >= MAX_LIPSYNC_PHONEMES:
                    continue
                new_phonemes.append(phoneme)
            has_base = l is not None and l.find_entry(phoneme, LIPSYNC_BASE) is not None
            if has_base and not overwrite:
                kept.append(phoneme)
                continue
            curves: dict[str, float] = {}
            for shape, w in shapes.items():
                name, amb = self._resolve_shape(shape, available)
                if name is None:
                    missing.append((phoneme, shape))
                    continue
                if amb:
                    ambiguous.append((phoneme, shape, name))
                curves[name] = curves.get(name, 0.0) + w
            if curves:
                plan.append((phoneme, curves))
        if not plan and not (set(new_phonemes) - set(existing)):
            return _fail("nothing", "作れる行がありませんでした（シェイプがモデルに無いか、すでに行があります）", LipSyncResult, kept=kept, missing=missing)
        with self.ctx.edit():
            lip = self._ensure()
            if l is None:
                lip.enabled = True
            lip.phonemes = new_phonemes
            for phoneme, curves in plan:
                e = lip.find_entry(phoneme, LIPSYNC_BASE)
                if e is None:
                    lip.entries.append(LipSyncEntry(phoneme=phoneme, emotion=LIPSYNC_BASE, curves=curves))
                else:
                    e.curves = curves
        msg = f"プロファイル「{prof.name}」から基本の行を {len(plan)} 個作りました"
        if missing:
            msg += f"（モデルに無いシェイプ {len(missing)} 個は入れていません）"
        return LipSyncResult(created=len(plan), kept=kept, missing=missing, ambiguous=ambiguous, message=msg)

    # --- 「試す」---

    def limit_map(self) -> dict[str, tuple[float, float]]:
        """対応表に出てくるシェイプの可動域（ドキュメント → プロファイル → 0〜1）。lipsync_apply の limits に渡す。"""
        return {c: effective_limit(self.doc, self.ctx.profile, c) for c in lipsync_table_curves(self.doc.lip_sync)}

    def evaluate(
        self,
        current: Mapping[str, float],
        phoneme_weights: Mapping[str, float],
        volume: Optional[float] = None,
        emotion_weights: Optional[Mapping[str, float]] = None,
    ) -> dict[str, float]:
        """「試す」の計算（シーンへ当てる値 `{シェイプ名: 値}`）。current = 試す前のシェイプの値（呼ぶ側が保持する元の値）。
        使わない・行なしなら {}。データもシーンも変えない。"""
        return lipsync_evaluate(self.doc.lip_sync, current, phoneme_weights, volume, emotion_weights, self.limit_map())

    # --- 表示 ---

    def view(self) -> LipSyncView:
        doc = self.doc
        l = doc.lip_sync
        cols = self.columns()
        sel = self.selected
        rows: list[LipSyncRow] = []
        seen_ph: set[str] = set()
        counts: dict[tuple[str, str], int] = {}
        for e in l.entries if l is not None else ():
            counts[(e.phoneme, e.emotion)] = counts.get((e.phoneme, e.emotion), 0) + 1
        col_names = {c.name for c in cols}
        orphan = 0
        for e in l.entries if l is not None else ():
            if e.phoneme not in (l.phonemes if l is not None else ()) or e.emotion not in col_names:
                orphan += 1
        for i, ph in enumerate(l.phonemes if l is not None else ()):
            cells = []
            n_entries = 0
            for c in cols:
                e = l.find_entry(ph, c.name)
                has = e is not None
                n_entries += 1 if has else 0
                ok = True
                if has:
                    ok = counts.get((ph, c.name), 0) == 1 and all(math.isfinite(v) for v in e.curves.values())
                if has:
                    tip = f"音素「{ph}」× {c.label}: シェイプ {len(e.curves)} 個"
                elif c.is_base:
                    tip = f"音素「{ph}」の基本の行はまだありません。選んでポーズを作って保存してください"
                else:
                    tip = f"音素「{ph}」× {c.label}: 行なし（基本のまま）"
                cells.append(
                    LipSyncCell(
                        phoneme=ph,
                        emotion=c.name,
                        has_entry=has,
                        curves=len(e.curves) if e is not None else 0,
                        valid=ok,
                        selected=sel == (ph, c.name),
                        tooltip=tip,
                    )
                )
            valid = bool(ph.strip()) and ph not in seen_ph
            seen_ph.add(ph)
            rows.append(LipSyncRow(phoneme=ph, index=i, valid=valid, entries=n_entries, cells=cells))
        n = len(rows)
        enabled = bool(l.enabled) if l is not None else False
        if l is None or n == 0:
            summary = "音素がありません（リップシンクは何もしません）"
        else:
            total = sum(r.entries for r in rows)
            summary = f"音素 {n} / {MAX_LIPSYNC_PHONEMES}・行 {total}" + ("" if enabled else "（使わない設定です）")
        vol = l.volume if l is not None else LipSync().volume
        base = LipSync()
        return LipSyncView(
            present=l is not None,
            enabled=enabled,
            strength=l.strength if l is not None else base.strength,
            volume_min=vol.min,
            volume_max=vol.max,
            volume_from=vol.from_,
            volume_to=vol.to,
            follow=l.follow if l is not None else base.follow,
            columns=cols,
            rows=rows,
            phonemes=list(l.phonemes) if l is not None else [],
            count=n,
            limit=MAX_LIPSYNC_PHONEMES,
            can_add=self.can_add,
            summary=summary,
            selected=sel,
            orphan_entries=orphan,
        )


# ---------------------------------------------------------------------------
# Presenter 4: 検証
# ---------------------------------------------------------------------------

_FIX_RENAME = "rename"
_FIX_REMOVE = "remove"
_FIX_NONE = "none"


@dataclass
class IssueRow:
    index: int  # issues の中の位置（ソート後）
    severity: str
    code: str
    message: str
    layer: Optional[int]
    layer_name: str
    row: Optional[int]
    col: Optional[int]
    name: str
    suggestion: str
    candidates: tuple[str, ...]
    kind: str  # "curve" | "bone" | ""（改名・削除の対象の種類）
    fix: str  # "rename"（候補で改名）| "remove"（参照を消す）| "none"
    can_select_point: bool  # row / col があり、点へ移動できる
    key: Optional[int] = None  # パース補正のキーの番号（キーの問題のとき）
    can_select_key: bool = False  # key があり、そのキーを編集の対象にできる
    lip: Optional[tuple[str, str]] = None  # リップシンクの行 (音素, 感情) の問題のとき
    can_select_lip: bool = False  # lip があり、そのマスを編集の対象にできる（音素が一覧にあり、感情が基本か既存のレイヤー）


@dataclass
class IssueGroup:
    severity: str
    label: str
    issues: list[IssueRow]


@dataclass
class RenameProposal:
    kind: str  # "curve" | "bone"
    old: str
    candidates: tuple[str, ...]
    new: str  # 今の選び（初めは第一候補。空 = 改名しない）
    count: int  # 検出の件数


@dataclass
class ValidationView:
    ran: bool
    groups: list[IssueGroup]
    counts: dict[str, int]
    total: int
    ok: bool  # エラーも警告も無い
    summary: str
    proposals: list[RenameProposal]
    can_remove_missing: bool  # モデルの情報が渡されている


@dataclass
class RepairReport(CommandResult):
    curve: V.RenameReport = field(default_factory=V.RenameReport)
    bone: V.RenameReport = field(default_factory=V.RenameReport)
    total: int = 0
    removed: int = 0
    issues_before: int = 0
    issues_after: int = 0


def _fix_of(issue: V.Issue) -> tuple[str, str]:
    """(対象の種類, 直し方)。"""
    code = issue.code
    if code in ("curve_case_mismatch", "bone_case_mismatch"):
        return code.split("_", 1)[0], _FIX_RENAME
    if code in ("curve_missing", "bone_missing"):
        kind = code.split("_", 1)[0]
        return kind, (_FIX_RENAME if issue.suggestion else _FIX_REMOVE)
    if code == "base_bone_missing":
        return "bone", (_FIX_RENAME if issue.suggestion else _FIX_NONE)
    return "", _FIX_NONE


class ValidationPresenter(Observable):
    """検証の実行・並べ替え・候補・一括改名・消えた参照の削除（docs/14 §5.9）。"""

    def __init__(self, ctx: EditContext) -> None:
        super().__init__()
        self.ctx = ctx
        self.issues: list[V.Issue] = []
        self.ran = False
        self.choices: dict[tuple[str, str], str] = {}  # (kind, 旧名) → 新名
        self.stale = False  # 結果が古い（文書・シーン・ベイクが変わった）。自動では再実行しない（重いことがある）
        ctx.subscribe(self._on_ctx)

    def _on_ctx(self, event: str) -> None:
        if event in ("document", "scene", "bake", "profile"):
            self.stale = True
        self._emit("issues")

    # --- 実行 ---

    @staticmethod
    def _sort_key(i: V.Issue):
        return (
            SEVERITY_ORDER.index(i.severity) if i.severity in SEVERITY_ORDER else len(SEVERITY_ORDER),
            -1 if i.layer is None else i.layer,
            -1 if i.row is None else i.row,
            -1 if i.col is None else i.col,
            i.code,
            i.name,
        )

    def run(self) -> list[V.Issue]:
        """検証する（Document もシーンも変えない）。重さ順（エラー → 警告 → 情報）、同じ重さの中は レイヤー → 行 → 列 → コード。"""
        ctx = self.ctx
        found = V.validate(ctx.doc, ctx.scene or V.SceneInfo(), ctx.profile, ctx.bake_state, ctx.bake_exclude)
        self.issues = sorted(found, key=self._sort_key)  # sorted は安定（同じキーは検出順）
        self.ran = True
        self.stale = False
        # 候補の選びは、残っている提案だけ引き継ぐ
        old = self.choices
        self.choices = {}
        for p in self._proposals():
            self.choices[(p.kind, p.old)] = old.get((p.kind, p.old), p.new)
        self._emit("issues")
        return self.issues

    def _proposals(self) -> list[RenameProposal]:
        seen: dict[tuple[str, str], RenameProposal] = {}
        for i in self.issues:
            kind, fix = _fix_of(i)
            if fix != _FIX_RENAME or not i.name:
                continue
            key = (kind, i.name)
            if key in seen:
                seen[key].count += 1
                continue
            first = i.suggestion or (i.candidates[0] if i.candidates else "")
            seen[key] = RenameProposal(kind, i.name, tuple(i.candidates) or ((first,) if first else ()), first, 1)
        return list(seen.values())

    def suggestions(self) -> list[RenameProposal]:
        """改名の提案（種類 + 旧名ごとに 1 つ。候補は近い順。new は今の選び）。"""
        out = self._proposals()
        for p in out:
            p.new = self.choices.get((p.kind, p.old), p.new)
        return out

    def set_choice(self, kind: str, old: str, new: str) -> bool:
        """提案の新名を選ぶ / 手で入れる（空 = 改名しない）。提案に無い (kind, old) は False。"""
        if (kind, old) not in {(p.kind, p.old) for p in self._proposals()}:
            return False
        self.choices[(kind, old)] = (new or "").strip()
        self._emit("issues")
        return True

    # --- 表示 ---

    def view(self) -> ValidationView:
        counts = {s: 0 for s in SEVERITY_ORDER}
        rows: list[IssueRow] = []
        doc = self.ctx.doc
        for n, i in enumerate(self.issues):
            counts[i.severity] = counts.get(i.severity, 0) + 1
            kind, fix = _fix_of(i)
            rows.append(
                IssueRow(
                    index=n,
                    severity=i.severity,
                    code=i.code,
                    message=i.message,
                    layer=i.layer,
                    layer_name=doc.layers[i.layer].name if i.layer is not None and i.layer < len(doc.layers) else "",
                    row=i.row,
                    col=i.col,
                    name=i.name,
                    suggestion=i.suggestion,
                    candidates=tuple(i.candidates),
                    kind=kind,
                    fix=fix,
                    can_select_point=i.layer is not None and i.row is not None and i.col is not None,
                    key=i.key,
                    can_select_key=i.key is not None and doc.perspective is not None and 0 <= i.key < len(doc.perspective.keys),
                    lip=i.lip,
                    can_select_lip=_lip_cell_ok(doc, i.lip),
                )
            )
        groups = [
            IssueGroup(s, SEVERITY_LABEL[s], [r for r in rows if r.severity == s])
            for s in SEVERITY_ORDER
            if counts.get(s)
        ]
        sc = self.ctx.scene
        if not self.ran:
            summary = "未実行"
        elif not rows:
            summary = "問題は見つかりませんでした"
        else:
            summary = " / ".join(f"{SEVERITY_LABEL[s]} {counts[s]}" for s in SEVERITY_ORDER)
        return ValidationView(
            ran=self.ran,
            groups=groups,
            counts=counts,
            total=len(rows),
            ok=self.ran and counts[V.SEVERITY_ERROR] == 0 and counts[V.SEVERITY_WARNING] == 0,
            summary=summary,
            proposals=self.suggestions(),
            can_remove_missing=sc is not None and (sc.curves is not None or sc.bones is not None),
        )

    # --- 修復 ---

    def apply_renames(self, choices: Optional[Mapping[tuple[str, str], str]] = None) -> RepairReport:
        """選んだ新名で一括改名する（validate.rename_report。シェイプとボーンで 1 回ずつ、合わせて 1 つの変更）。
        choices を渡すとそれを先に set_choice へ反映する。済んだら検証をやり直す。"""
        if choices:
            for (kind, old), new in choices.items():
                self.set_choice(kind, old, new)
        maps: dict[str, dict[str, str]] = {"curve": {}, "bone": {}}
        for p in self.suggestions():
            if p.new and p.new != p.old:
                maps[p.kind][p.old] = p.new
        if not maps["curve"] and not maps["bone"]:
            return _fail("nothing", "改名する項目がありません", RepairReport, issues_before=len(self.issues), issues_after=len(self.issues))
        before = len(self.issues)
        with self.ctx.edit() as scope:
            rc = V.rename_report(self.ctx.doc, maps["curve"], "curve")
            rb = V.rename_report(self.ctx.doc, maps["bone"], "bone")
        self.run()
        total = rc.total + rb.total
        coll = rc.collisions + rb.collisions
        msg = f"{total} か所を改名しました" + (f"（名前が衝突するため見送った所 {coll} か所）" if coll else "")
        return RepairReport(
            message=msg + _discard_notice(scope),
            curve=rc,
            bone=rb,
            total=total,
            issues_before=before,
            issues_after=len(self.issues),
            discarded_edits=scope.discarded_edits,
        )

    def remove_missing(self, include_case_mismatch: bool = False) -> RepairReport:
        """モデルに無いシェイプ・ボーンへの参照を消す（validate.remove_missing_references）。
        大小違いだけの名前は改名で直すものなので既定では残す。モデルの情報（scene）が無ければ何もしない。"""
        sc = self.ctx.scene
        before = len(self.issues)
        if sc is None or (sc.curves is None and sc.bones is None):
            return _fail("no_scene", "モデルの情報が無いため消せません", RepairReport, issues_before=before, issues_after=before)
        with self.ctx.edit() as scope:
            n = V.remove_missing_references(self.ctx.doc, sc, include_case_mismatch)
        self.run()
        return RepairReport(
            message=f"{n} 個の参照を消しました" + _discard_notice(scope),
            removed=n,
            total=n,
            issues_before=before,
            issues_after=len(self.issues),
            discarded_edits=scope.discarded_edits,
        )


# ---------------------------------------------------------------------------
# まとめ
# ---------------------------------------------------------------------------


class PresenterSet:
    """Presenter 6 種（pose / grid / layers / validation / perspective / lipsync）+ 共有の EditContext（ビューを作る側が 1 つ持つ）。"""

    def __init__(
        self,
        doc: Document,
        profile: Optional[NamingProfile] = None,
        bake_state: Optional[dict[str, str]] = None,
        scene: Optional[V.SceneInfo] = None,
    ) -> None:
        self.ctx = EditContext(doc, profile, bake_state, scene)
        self.pose = PosePresenter(self.ctx)
        self.grid = GridPresenter(self.ctx)
        self.layers = LayerPresenter(self.ctx)
        self.validation = ValidationPresenter(self.ctx)
        self.perspective = PerspectivePresenter(self.ctx)
        self.lipsync = LipSyncPresenter(self.ctx)


__all__: Sequence[str] = (
    "Observable",
    "EditContext",
    "EditScope",
    "PresenterSet",
    "GridPresenter",
    "PosePresenter",
    "LayerPresenter",
    "ValidationPresenter",
    "PerspectivePresenter",
    "PerspectiveView",
    "PerspectiveKeyRow",
    "PerspectiveResult",
    "LipSyncPresenter",
    "LipSyncView",
    "LipSyncRow",
    "LipSyncCell",
    "LipSyncColumn",
    "LipSyncResult",
    "CommandResult",
    "GenerateResult",
    "ResizeResult",
    "SelectResult",
    "PointView",
    "GridView",
    "GridSummary",
    "CameraMarker",
    "PoseView",
    "CurveRow",
    "BoneRow",
    "PoseResult",
    "IngestReport",
    "LayerView",
    "LayerRow",
    "LayerResult",
    "NameCheck",
    "ValidationView",
    "IssueRow",
    "IssueGroup",
    "RenameProposal",
    "RepairReport",
    "poses_equal",
    "trim_pose",
    "layer_morph_names",
    "EMOTION_PRESETS",
    "POINT_ACTIONS",
)
