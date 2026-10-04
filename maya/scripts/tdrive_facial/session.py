"""FacialController の編集セッション（Maya 依存。docs/15 §4.1・§4.2・§4.3、docs/14 §5）。

開いている `.fcpose.json`（Document）・Presenter 4 種・エディタ内 Undo・「どの点のポーズがシーンに当たっているか」を持ち、
データとシーンを同期させる。UI も MCP スクリプトもこのモジュール経由で操作する（UI 無しでも全機能を呼べる。Qt は import しない）。
作りは Toon の `tdrive_toon/session.py`（`current()` / listeners / checkpoint・undo・redo / fileInfo / on_scene_*）に合わせてある。

## データの持ち方
- Document は Maya の系（cm / Y-up / 右手）で持つ。新規作成は系を**明示して**書く（meta・forwardAxis "+Z"・mirror.boneAxis "X"）。
  開いたファイルが Maya の系でなければ `core.space` で変換する。変換したファイルは**元のファイルへ上書きしない**:
  元の meta を `doc.extra["tdOriginalMeta"]` に残し、保存先は既定の場所（`facial/<asset>/<asset>.fcpose.json`）にする
  （UE 版へ戻したいときは出力タブで UE 系へ変換して書き出す）
- 開いているファイルはシーンの fileInfo `tdFacialData`（プロジェクト相対パス）に記録する。SceneOpened で開き直し、SceneSaved で保存する
- Presenter（`grid` / `pose` / `layers` / `validation`）は新規作成・開くたびに**作り直す**。UI は `listeners` の通知で付け直す
  （`generation` が増える）。Undo の復元では作り直さず、同じ Presenter の Document を差し替える（`notify_document_changed`）

## Undo
- エディタ内 Undo は Document のスナップショット（deepcopy）。コマンドは `@undoable`。**「1 回の呼び出し = 1 つの Undo」**で、
  Document が変わらなかったコマンド（失敗・変更なし）は Undo に積まない
- **Maya のシーンの変更は Undo の対象外**: ベイク（`bake_*`）の結果・FC_* の削除・カメラ移動は Maya 自身の Undo に積まれる。
  ベイクは直前に編集状態を抜ける（`end_edit`）ので、ベイクが Maya の Undo の 1 区切り（`tdFacialBake`）になる
- 編集状態（基準姿勢・点のポーズのシーンへの適用）は Undo の対象外。Undo / Redo のあと、選択中の点のポーズをシーンへ当て直す

## 変更の通知は 2 系統
- `listeners`: データ・シーンの情報が変わったとき（`_changed`）。UI は各タブを描き直す（重い）
- `state_listeners`: **軽い通知**。編集状態の出入り・点の選択・アクティブレイヤー・編集中のポーズの値（スライダーのドラッグ 1 回ごと）・
  検証の実行・プレビューの作成 / 削除 / 作り直しで呼ぶ。`_changed` のときも呼ぶ。スライダーのドラッグ中にタブ全体を作り直さないよう、
  ここに登録する側は**ヘッダーの表示・ボタンの有効無効・ラベル**のような軽い更新だけをする

## 編集状態（シーンを基準姿勢に入れる）
`begin_edit` で基準姿勢（バインドポーズ・全シェイプの重み 0）に入り、`select_point` でその点のポーズを当てる。`end_edit` で元の姿勢・
重み・接続へ戻す。**シーンを基準姿勢のまま放置しない**ため、(1) シーンを触るメソッドは例外で必ず編集状態を抜ける、
(2) `edit_guard()`、(3) `close` / 新しいシーン / シーンを開く前（PreFileNewOrOpened）/ ツールのリロード（lifecycle.on_reload）で抜ける。
シーンが入れ替わっていたら（ノードの UUID が違う）元へ「戻さず」に捨てる（新しいシーンへ古い値を書かない）。

## 編集の対象はパース補正のキーにもできる
`select_key(n)` でキー n を編集の対象にする（格子の点の選択は外れ、`ctx.key_target = n`）。ポーズタブのスライダーはそのキーのポーズを編集し、
`save_point` はキーのポーズへ保存する（未保存の編集があるときの「保存 / 破棄 / 取りやめ」は点・レイヤーと同じ）。
シーンへ当たっているものは `applied_point`（点）/ `applied_key`（キー）。

## 編集の対象はリップシンクのマスにもできる
`select_lip_cell(音素, 感情)`（感情の基本は ""）でマスを編集の対象にする（点・キーの選択は外れ、`ctx.lip_target`）。ポーズタブはそのマスの
シェイプだけを編集・保存する（ボーンは当ても保存もしない）。シーンへ当たっているものは `applied_lip`。

## リップシンクの「試す」（シーンだけ。編集状態の間だけ）
`lip_try_start` → `lip_try_set(音素の強さ, 声量, 感情の重み)` で、対応表の計算結果（`lipsync.evaluate`）を元のシェイプの値の上へ当てる。
「元の値」は編集状態が持っている値（土台の表情だけ。ふつうは 0）で、自分の出力を重ねない。文書は変えず、Maya の Undo にも積まない。
ほかの対象の選択・ポーズの当て直し・`lip_try_stop`・編集状態を抜けることで終わる（シーンは編集状態が見せるものへ戻る）。保存・出力のあいだは一時的に抜けて、戻ると続きから。
編集の対象と「試す」は同時に使えない（試しはじめると対象は外れる。未保存のポーズ編集があれば保存 / 破棄 / 取りやめを聞く）。

## 土台の表情（シーンだけの下敷き。2026-10-03 の決定: 加算）
`set_base_expression(名前, {シェイプ名: 重み})` / `clear_base_expression()`。ゲームでは表情アニメがシェイプを動かし、その上にベイクした
FC_*（補正）が足される。シーンでもそれに合わせ、`_apply_buffer` が**土台の値 + ポーズの値**をシーンへ当てる
（土台だけのシェイプは土台の値、ポーズだけのシェイプはポーズの値）。

- 可動域（`profile.effective_limit`）を超えても**丸めずに**当てる（blendShape の重みは 1 を超えられる）。超えたシェイプは `base_state().over_limit`
  に出る（画面が警告する）
- シェイプの重みだけ（ボーンは土台に含まない）。ポーズのバッファにも文書にも入らない（シーンだけ）
- `capture_from_scene` は土台を引く（ポーズの値 = シーンの値 − 土台の値）。「取り込む → 保存 → 当て直す」が土台つきでも元に戻る
- ベイク・出力には入らない（ベイクは編集状態を抜けて、自分で基準姿勢に入る）。編集状態を抜ける（`end_edit`）・閉じる・新しいシーン・
  リロードで外れる。点・レイヤーの切り替え、`_edit_suspended`（プレビューの作り直しなど）では残る
"""

from __future__ import annotations

import contextlib
import copy
import json
import math
import sys
import traceback
from dataclasses import dataclass, field
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Sequence

from maya import cmds
from maya.api import OpenMaya as om
from tdrive import lifecycle, project

from . import bake as bake_mod
from . import pose_apply
from . import preview_rig
from . import scene as scene_mod
from . import scene_grid as scene_grid_mod
from . import shapes
from .core import autofill, base_expr, evaluate, fcpose_io, naming, space
from .core import strength as strength_mod
from .core import profile as profile_mod
from .core import validate as V
from .core.model import (
    FILL_MODES,
    FORWARD_AXES,
    MIRROR_AXES,
    Bake,
    Document,
    GridPoint,
    LodMesh,
    Meta,
    Material,
    Quality,
    SourcePose,
    Target,
)
from .core.presenters import (
    CONFIRM_CANCEL,
    CONFIRM_DISCARD,
    CONFIRM_SAVE,
    SELECT_CANCELLED,
    SELECT_NEEDS_CONFIRM,
    SELECT_SAME,
    SELECT_SELECTED,
    CommandResult,
    IngestReport,
    LayerResult,
    PoseResult,
    PresenterSet,
    RepairReport,
    ResizeResult,
    SelectResult,
)

FILE_INFO_KEY = "tdFacialData"  # シーンに記録する、開いている fcpose のプロジェクト相対パス
ORIGINAL_META_KEY = "tdOriginalMeta"  # 変換して開いたときの元の meta（doc.extra）
MAX_UNDO = 100
FILE_SUFFIX = ".fcpose.json"


_no_undo = scene_mod.no_undo  # この間の Maya のシーンの変更を Undo に積まない（M-5）


class FacialSessionError(RuntimeError):
    """セッションの操作ができない（メッセージはそのまま画面に出せる日本語）。"""


# ---------------------------------------------------------------------------
# パスの決まり
# ---------------------------------------------------------------------------


def default_path(character: str) -> Path:
    """既定の保存先 `<プロジェクト>/facial/<character>/<character>.fcpose.json`。"""
    return project.root() / "facial" / character / f"{character}{FILE_SUFFIX}"


def _same_file(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return a == b


def _stem_of(path: Path) -> str:
    n = path.name
    return n[: -len(FILE_SUFFIX)] if n.endswith(FILE_SUFFIX) else path.stem


def _meta_dict(meta: Meta) -> dict[str, Any]:
    return {"unit": meta.unit, "upAxis": meta.up_axis, "handedness": meta.handedness, "source": meta.source}


def _maya_meta(source: str = "") -> Meta:
    return Meta(unit="cm", up_axis="Y", handedness="right", source=source)


# ---------------------------------------------------------------------------
# コマンドの共通処理（@undoable）
# ---------------------------------------------------------------------------


def undoable(fn=None, *, refresh: bool = False, notify: bool = False, reapply: bool = True):
    """Document を変えるコマンドの印。1 回の呼び出し = 1 つの Undo（入れ子の呼び出しでは外側の 1 回だけ）。

    - 呼ぶ前に Document をスナップショットし、**変わらなかったら Undo に積まない**。例外が出たら Document をスナップショットへ戻す
    - 結果の `stale_morphs`（消すべき FC_*）をシーンから消し、bake_state を更新する
    - refresh=True: 終わったらシーンの情報を取り直す。notify=True: Presenter を通さずに書き換えたので "document" を通知する
    - reapply=True: 選択中の点の保存済みポーズが変わったら、編集中ならシーンへ当て直す
    """

    def deco(func):
        @wraps(func)
        def wrapper(self, *args, **kwargs):
            if self._undo_depth > 0:  # 入れ子: 外側が面倒を見る
                return func(self, *args, **kwargs)
            return self._run_command(lambda: func(self, *args, **kwargs), refresh, notify, reapply)

        return wrapper

    return deco(fn) if fn is not None else deco


def scene_op(func):
    """シーンを触るメソッドの印。例外が出たら編集状態を抜けて（シーンを元へ戻して）から投げ直す。"""

    @wraps(func)
    def wrapper(self, *args, **kwargs):
        try:
            return func(self, *args, **kwargs)
        except BaseException:
            self.end_edit(quiet=True)
            raise

    return wrapper


# ---------------------------------------------------------------------------
# セッション
# ---------------------------------------------------------------------------


class FacialSession:
    def __init__(self) -> None:
        self.presenters: Optional[PresenterSet] = None
        self.path: Optional[Path] = None
        self.dirty = False
        self.generation = 0  # Presenter を作り直した回数（UI が付け直す目印）
        self.listeners: list[Callable[[], None]] = []
        self.state_listeners: list[Callable[[], None]] = []  # 軽い通知（モジュールの docstring「変更の通知は 2 系統」）
        self.preview_warnings: list[str] = []  # 直近のプレビューの作り直しの警告（他から接続されていて配線しなかった等）
        self.last_preview_report: Optional[preview_rig.BuildReport] = None
        self._preview_pending = False  # 編集中に作り直しが要る状態になった（編集を終えたら作り直す）
        self.converted_from: Optional[dict[str, Any]] = None  # 変換して開いたときの元の meta
        self.source_path: Optional[Path] = None  # 開いた元のファイル（変換して開いたとき、上書きしない元）
        self.last_apply: Optional[pose_apply.ApplyReport] = None  # 直近の「点のポーズをシーンへ当てた」結果
        self.last_apply_error: str = ""  # 直近の `import_pose_curves` で、読んだ値をシーンへ当てられなかったときの理由
        self.edit_warnings: list[str] = []  # 基準姿勢に入ったときの警告（バインドポーズが無い等）
        self._path_confirmed = False  # False: 一度も保存・開いていない既定の保存先（既存のファイルを黙って上書きしない）
        self._undo: list[Document] = []
        self._redo: list[Document] = []
        self._undo_depth = 0
        self._ref: Optional[scene_mod.Reference] = None
        self._ref_uuids: dict[str, list[str]] = {}
        self._applied: Optional[tuple[int, int, int]] = None
        self._applied_lip: Optional[tuple[str, str]] = None
        self._sculpt: Optional[SculptState] = None  # 「この角度で彫る」の最中（shapes.py。docs/14 §5.6）
        self._base: Optional[BaseExpression] = None  # 土台の表情（シーンだけの下敷き。モジュールの docstring「土台の表情」）
        self._lip_try: Optional["LipTry"] = None  # リップシンクの「試す」の最中の入力（モジュールの docstring「リップシンクの『試す』」）
        self.last_lip_try: dict[str, float] = {}  # 直近の「試す」でシーンへ当てた、対応表のシェイプの値
        self._head_joint = ""  # 直近に使った基準ボーン（長い名前）
        self.scene_grid = scene_grid_mod.SceneGrid(self)  # シーンのビューポートに出す格子（任意。scene_grid.py）
        self._suspended: Optional["_SuspendedEdit"] = None  # 保存・出力のあいだ編集状態を一時的に抜けているときの、戻す情報（M-1）
        self.last_capture_base_ignored: list[str] = []  # 直近の `capture_from_scene` で、土台のとおりだったので取り込まなかったシェイプ
        self.last_capture_below_base: list[str] = []  # 直近の `capture_from_scene` で、シーンの値が土台より低く、取り込めなかったシェイプ（C-2）

    # ------------------------------------------------------------ 状態
    @property
    def doc(self) -> Optional[Document]:
        return self.presenters.ctx.doc if self.presenters is not None else None

    @property
    def has_unsaved_work(self) -> bool:
        """保存していないものがある: データの変更（dirty）か、点に保存していない編集中の値（スライダー・取り込み）。M-8。"""
        if self.presenters is None:
            return False
        return bool(self.dirty or self.presenters.pose.dirty)

    def require(self) -> Document:
        if self.presenters is None:
            raise FacialSessionError("FacialController のデータが開かれていません（新規作成 or 開く）")
        return self.presenters.ctx.doc

    def _pres(self) -> PresenterSet:
        if self.presenters is None:
            raise FacialSessionError("FacialController のデータが開かれていません（新規作成 or 開く）")
        return self.presenters

    @property
    def grid(self):
        return self._pres().grid

    @property
    def pose(self):
        return self._pres().pose

    @property
    def layers(self):
        return self._pres().layers

    @property
    def validation(self):
        return self._pres().validation

    @property
    def ctx(self):
        return self._pres().ctx

    @property
    def scene(self) -> Optional[V.SceneInfo]:
        """検証・Presenter に渡しているシーンの情報（`refresh_scene` で取り直す）。"""
        return self.presenters.ctx.scene if self.presenters is not None else None

    @property
    def bake_state(self) -> Optional[dict[str, str]]:
        """FC_* の名前 → 焼いたときのポーズのハッシュ（None = 不明）。"""
        return self.presenters.ctx.bake_state if self.presenters is not None else None

    @property
    def profile(self):
        return self.presenters.ctx.profile if self.presenters is not None else None

    def _changed(self, dirty: bool = True) -> None:
        self.dirty = self.dirty or dirty
        if dirty and self.presenters is not None:
            cmds.file(modified=True)  # 閉じるときに保存を聞かれ、保存（Ctrl+S）で fcpose も保存される
        for fn in list(self.listeners):
            try:
                fn()
            except Exception as exc:  # noqa: BLE001  UI 側の不具合で編集を止めない。ただし見えるように通知する
                lifecycle.report_error(f"画面の更新でエラー: {exc}", traceback.format_exc())
        self._notify_state()

    def _notify_state(self) -> None:
        """軽い通知（`state_listeners`）を呼ぶ。購読側の不具合で操作を止めない。"""
        for fn in list(self.state_listeners):
            try:
                fn()
            except RuntimeError:  # Qt の枠が先に破棄された: 購読を外す
                if fn in self.state_listeners:
                    self.state_listeners.remove(fn)
            except Exception as exc:  # noqa: BLE001
                lifecycle.report_error(f"画面の更新でエラー: {exc}", traceback.format_exc())

    # ------------------------------------------------------------ 文書の組み立て
    def _install(self, doc: Document, path: Optional[Path], *, confirmed: bool, dirty: bool) -> None:
        """Document を開いた状態にする（Presenter を作り直し、Undo を捨て、シーンの情報を取り込む）。"""
        self.end_edit(quiet=True)
        prof = None
        if doc.profile:
            asset = doc.asset or (path and _stem_of(path)) or ""
            try:
                prof = profile_mod.find_profile(doc.profile, [profile_mod.project_profiles_dir(project.root(), asset)])
            except Exception:  # noqa: BLE001  プロファイルが読めなくても開く
                prof = None
        self.presenters = PresenterSet(doc, prof)
        self.generation += 1
        self.path = path
        self._path_confirmed = confirmed
        self.dirty = dirty
        self.converted_from = None
        self.source_path = None
        self._undo.clear()
        self._redo.clear()
        self._applied = None
        self.last_apply = None
        self.refresh_scene(notify=False)

    def new(
        self,
        character: str,
        mesh: Optional[str] = None,
        extra_meshes: Optional[Sequence[str]] = None,
        profile: Optional[str] = None,
    ) -> Document:
        """新しいデータを作る（Maya の系を明示して書く）。

        mesh を省くと、選択中の blendShape 付きメッシュ → 表示中の blendShape 付きメッシュの先頭 → 表示中のメッシュの先頭の順に選ぶ。
        基準ボーンはそのメッシュのスキンの骨格から自動検出（`scene.detect_base_bone_for`）。保存先は既定の場所
        （既にファイルがあれば最初の `save()` は FileExistsError にして、黙って上書きしない）。
        """
        character = (character or "").strip()
        if not character:
            raise FacialSessionError("キャラクター名が空です")
        doc = Document(meta=_maya_meta("T-Drive Maya"))
        doc.asset = character
        mesh_name = mesh or self._detect_mesh()
        doc.target = Target(mesh=self._stored_mesh_name(mesh_name) if mesh_name else "")
        if extra_meshes:
            doc.target.extra_meshes = [self._stored_mesh_name(m) for m in extra_meshes]
        doc.grid.forward_axis = "+Z"
        doc.mirror.bone_axis = "X"
        doc.grid.base_bone = (self._detect_base_bone(doc) or "head")
        if profile:
            prof = profile_mod.find_profile(profile, [profile_mod.project_profiles_dir(project.root(), character)])
            if prof is None:
                raise FacialSessionError(f"プロファイル「{profile}」が見つかりません")
            profile_mod.apply_to_document(prof, doc)
        self._install(doc, default_path(character), confirmed=False, dirty=True)
        self._changed()
        return doc

    def open(self, path: str | Path) -> Document:
        """ファイルを開く。Maya の系でなければ変換し（元の meta は doc.extra に残す）、保存先は既定の場所にする（元を上書きしない）。"""
        p = Path(path)
        doc = fcpose_io.load_document(p)
        dirty = False
        converted: Optional[dict[str, Any]] = None
        if space.space_of(doc.meta) != space.MAYA:
            converted = _meta_dict(doc.meta)
            doc = space.convert_document(doc, space.MAYA)
            doc.extra[ORIGINAL_META_KEY] = converted
            doc.meta.source = f"converted to Maya by T-Drive (from {converted['unit']} / {converted['upAxis']}-up / {converted['handedness']}, {converted['source'] or 'unknown'})"
            dirty = True
        if not doc.asset:
            doc.asset = _stem_of(p)
            dirty = True
        if doc.target is None:
            found = self._detect_mesh()
            doc.target = Target(mesh=self._stored_mesh_name(found) if found else "")
            dirty = True
        if converted is not None:
            self._install(doc, default_path(doc.asset), confirmed=False, dirty=dirty)
            self.source_path = p
        else:
            self._install(doc, p, confirmed=True, dirty=dirty)
            self.source_path = None
            self._remember(p)
        self.converted_from = converted
        self._changed(dirty=dirty)
        return doc

    def save(self, path: Optional[str | Path] = None, overwrite: bool = False) -> Path:
        """保存する。path を渡すとそこへ（以後の保存先になる）。一度も保存していない既定の保存先に既存のファイルがあれば FileExistsError。"""
        doc = self.require()
        target = Path(path) if path is not None else (self.path or default_path(doc.asset or "untitled"))
        if self.source_path is not None and _same_file(target, self.source_path):
            # 別の座標系から変換して開いたファイルの元には上書きしない（UE 版などへ戻せなくなる。M-12）
            raise FacialSessionError(f"{target.as_posix()} は変換して開いた元のファイルです。別の名前で保存してください（上書きはできません）")
        if path is not None:
            self.path = Path(path)
            self._path_confirmed = True
        if self.path is None:
            self.path = default_path(doc.asset or "untitled")
        if not self._path_confirmed and self.path.exists() and not overwrite:
            raise FileExistsError(f"{self.path} は既にあります。別の名前で保存する（save_as）か、overwrite=True で上書きしてください")
        fcpose_io.save(doc, self.path)
        self._path_confirmed = True
        self._remember(self.path)
        self.dirty = False
        self._changed(dirty=False)
        return self.path

    def save_as(self, path: str | Path) -> Path:
        return self.save(path)

    def close(self) -> None:
        """データを閉じる（編集状態を抜け、シーンの記録も消す）。未保存の確認は呼ぶ側。"""
        self.end_edit(quiet=True)
        self.scene_grid.remove()
        self.presenters = None
        self.path = None
        self.source_path = None
        self.converted_from = None
        self.dirty = False
        self._path_confirmed = False
        self._undo.clear()
        self._redo.clear()
        self._applied = None
        self.generation += 1
        try:
            cmds.fileInfo(remove=FILE_INFO_KEY)
        except RuntimeError:
            pass
        self._changed(dirty=False)

    def _remember(self, path: Path) -> None:
        cmds.fileInfo(FILE_INFO_KEY, project.to_project_path(path))

    @staticmethod
    def remembered_path() -> Optional[str]:
        v = cmds.fileInfo(FILE_INFO_KEY, query=True)
        return project.from_project_path(v[0]) if v else None

    # ------------------------------------------------------------ メッシュ・基準ボーンの検出
    @staticmethod
    def _stored_mesh_name(mesh: str) -> str:
        """文書に書くメッシュ名: 短い名前で一意ならそれ、そうでなければ長い名前。"""
        long_names = cmds.ls(mesh, long=True) or []
        if not long_names:
            return mesh
        short = scene_mod.short_name(long_names[0])
        return short if len(cmds.ls(short)) == 1 else long_names[0]

    @staticmethod
    def _detect_mesh() -> str:
        """選択中の blendShape 付きメッシュ → 表示中の blendShape 付きメッシュ → 表示中のメッシュ、の先頭。無ければ ""。"""
        selected: list[str] = []
        for n in cmds.ls(selection=True, long=True) or []:
            if cmds.nodeType(n) == "mesh":
                n = (cmds.listRelatives(n, parent=True, fullPath=True) or [n])[0]
            if cmds.listRelatives(n, shapes=True, type="mesh") and n not in selected:
                selected.append(n)
        visible = scene_mod.list_visible_meshes()

        def has_bs(m: str) -> bool:
            try:
                return bool(scene_mod.blend_shapes(m))
            except ValueError:
                return False

        for group in (selected, visible):
            for m in group:
                if has_bs(m):
                    return m
        return visible[0] if visible else ""

    def _detect_base_bone(self, doc: Document) -> str:
        try:
            meshes = self._resolve_meshes(doc)
        except FacialSessionError:
            return ""
        return scene_mod.detect_base_bone_for(meshes)

    def detect_base_bone(self) -> str:
        """今の対象メッシュのスキンの骨格から基準ボーンを検出する（見つからなければ ""）。設定は `set_base_bone`。"""
        return self._detect_base_bone(self.require())

    @staticmethod
    def _resolve_meshes(doc: Document) -> list[str]:
        if doc.target is None or not doc.target.mesh:
            raise FacialSessionError("対象のメッシュ（target.mesh）が設定されていません")
        out: list[str] = []
        lod_only = {m.mesh for m in doc.target.lod_meshes} - {doc.target.mesh, *doc.target.extra_meshes}
        for name in doc.target.all_meshes():
            try:
                m = scene_mod.resolve_mesh(name)
            except ValueError as e:
                if name in lod_only:
                    continue  # LOD のメッシュが無いのは飛ばす（編集は続けられる。検証が知らせ、ベイクは止まる）
                raise FacialSessionError(str(e)) from e
            if m not in out:
                out.append(m)
        return out

    def target_meshes(self) -> list[str]:
        """対象メッシュ（顔 + extraMeshes + LOD のメッシュ。長い名前）。設定が無い・見つからなければ FacialSessionError（LOD のメッシュが無いのだけは飛ばす）。"""
        return self._resolve_meshes(self.require())

    # ------------------------------------------------------------ シーンの情報
    def refresh_scene(self, notify: bool = True, call_listeners: bool = True) -> None:
        """シーンの情報（`scene.build_scene_info`）と bake_state（`scene.bake_state_for`）を取り直して Presenter へ渡す。
        notify=False は Presenter にも通知しない（組み立て中）。call_listeners=False は session.listeners を呼ばない。"""
        if self.presenters is None:
            return
        doc = self.presenters.ctx.doc
        info = scene_mod.build_scene_info(doc)
        state: Optional[dict[str, str]] = None
        excl: Optional[dict[str, str]] = None
        try:
            if doc.target is not None and doc.target.mesh:
                mesh = scene_mod.resolve_mesh(doc.target.mesh)
                state = scene_mod.bake_state_for(mesh)
                excl = scene_mod.bake_exclude_for(mesh)
        except ValueError:
            state = None
            excl = None
        ctx = self.presenters.ctx
        ctx.scene = info  # まとめて入れてから通知する（途中の状態を見せない）
        ctx.bake_state = state
        ctx.bake_exclude = excl
        if notify:
            ctx.set_scene(info)
            ctx.set_bake_state(state)
            if call_listeners:
                self._changed(dirty=False)

    def refresh(self) -> None:
        """シーンの情報を取り直す（Tool.refresh 用）。データが無ければ何もしない。"""
        self.refresh_scene()

    # ------------------------------------------------------------ Undo（エディタ内。Maya の Undo とは別）
    def checkpoint(self) -> None:
        """Document のスナップショットを積む（`@undoable` が自動で呼ぶ）。"""
        if self.presenters is None:
            return
        self._undo.append(copy.deepcopy(self.doc))
        del self._undo[:-MAX_UNDO]
        self._redo.clear()

    def undo(self) -> bool:
        return self._restore(self._undo, self._redo)

    def redo(self) -> bool:
        return self._restore(self._redo, self._undo)

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def _restore(self, src: list, dst: list) -> bool:
        if not src or self.presenters is None:
            return False
        dst.append(copy.deepcopy(self.doc))
        del dst[:-MAX_UNDO]
        snapshot = src.pop()
        self._swap_document(snapshot)
        self.dirty = True
        self._sync_after_restore()
        self._changed()
        return True

    def _swap_document(self, doc: Document) -> None:
        """Presenter はそのままに Document を差し替えて通知する。"""
        ctx = self._pres().ctx
        old_target = self._target_key(ctx.doc)
        ctx.doc = doc
        n = len(doc.layers)
        if n and ctx.active_layer >= n:
            ctx.active_layer = n - 1
        if ctx.selection is not None and not ctx.in_grid(*ctx.selection):
            ctx.selection = None
        if ctx.key_target is not None and ctx.selected_key() is None:  # 元に戻す・やり直しでキーが無くなった
            ctx.key_target = None
        self._prune_lip_target()
        if old_target != self._target_key(doc):
            self.end_edit(quiet=True)  # 対象メッシュが変わった: 基準姿勢は古い対象のもの
        ctx.notify_document_changed()

    def _prune_lip_target(self) -> None:
        """編集の対象のマスの音素・感情が無くなっていたら（元に戻す・レイヤーの削除など）対象を外す。"""
        ctx = self._pres().ctx
        if ctx.lip_target is not None and ctx.selected_lip() is None:
            ctx.set_lip_target(None)

    @staticmethod
    def _target_key(doc: Document) -> tuple:
        t = doc.target
        return (t.mesh, tuple(t.extra_meshes), tuple(m.mesh for m in t.lod_meshes)) if t is not None else ("", (), ())

    def _sync_after_restore(self) -> None:
        self._reapply_safely()
        self.refresh_scene(notify=True, call_listeners=False)
        self._sync_preview()

    def _run_command(self, call: Callable[[], Any], refresh: bool, notify: bool, reapply: bool) -> Any:
        """`@undoable` の本体: スナップショット → 実行 → 変わっていれば Undo に積む → 後始末（FC_* の削除・当て直し・通知）。"""
        pres = self._pres()
        ctx = pres.ctx
        snap = copy.deepcopy(ctx.doc)
        snap_dict = fcpose_io.to_dict(snap)
        before_hash = self._saved_hash()
        self._undo_depth += 1
        try:
            res = call()
        except BaseException:
            self._swap_document(snap)  # 途中まで変わった Document を戻す
            raise
        finally:
            self._undo_depth -= 1
        doc = ctx.doc
        changed = fcpose_io.to_dict(doc) != snap_dict
        if not changed:
            return res
        self._prune_lip_target()
        self._undo.append(snap)
        del self._undo[:-MAX_UNDO]
        self._redo.clear()
        self.dirty = True
        if notify:
            ctx.notify_document_changed()
        stale = list(getattr(res, "stale_morphs", None) or [])
        if stale:
            self._delete_stale(stale)
            refresh = True
        if refresh:
            self.refresh_scene(notify=True, call_listeners=False)
        if reapply and self._saved_hash() != before_hash:
            self._reapply_safely()
        elif self._lip_try is not None:  # 対応表が変わったので、「試す」の結果を計算し直す
            self._reapply_safely()
        self._sync_preview()
        self._changed()
        return res

    def _reapply_safely(self) -> None:
        """編集中なら選択中の点のポーズをシーンへ当て直す。失敗したらシーンを元へ戻して通知する（コマンド自体は成功させる）。"""
        if not self.editing:
            return
        try:
            if self._lip_try is not None:  # 「試す」の最中は、試した結果を当て直す
                self._apply_lip_try()
            else:
                self._apply_buffer()
        except Exception:  # noqa: BLE001
            self.end_edit(quiet=True)
            lifecycle.report_error("ポーズをシーンへ当て直せませんでした（編集状態を抜けました）", traceback.format_exc(), once=False)

    def _saved_hash(self) -> Optional[str]:
        ctx = self._pres().ctx
        if not ctx.has_target:
            return None
        sp = ctx.saved_pose()
        return V.pose_hash(sp) if sp is not None else None

    # ------------------------------------------------------------ 孤立した FC_* の削除
    def _delete_stale(self, names: Iterable[str]) -> list[str]:
        """Document から外れた FC_* をシーンから消し、bake_state からも外す。編集中の基準姿勢の記録からも外す。"""
        doc = self.require()
        want = {n for n in names if naming.is_fc_name(n)}
        removed: list[str] = []
        if not want or doc.target is None or not doc.target.mesh:
            return removed
        try:
            meshes = self._resolve_meshes(doc)
        except FacialSessionError:
            return removed
        for m in meshes:
            for node in scene_mod.blend_shapes(m):
                table = scene_mod.target_indices(node)
                doomed = sorted(n for n in want if n in table)
                if not doomed:
                    continue
                plugs = [scene_mod.weight_plug(node, table[n]) for n in doomed]
                for n in scene_mod.delete_targets(node, doomed):
                    if n not in removed:
                        removed.append(n)
                self._forget_plugs(plugs)
                state = scene_mod.get_bake_state(node)
                if any(n in state for n in doomed):
                    for n in doomed:
                        state.pop(n, None)
                    scene_mod.set_bake_state(node, state)
        return removed

    def _forget_plugs(self, plugs: Iterable[str]) -> None:
        """消したターゲットの weight を、基準姿勢の記録から外す（戻すときに消した要素を作り直さないため）。"""
        ref = self._ref
        if ref is None:
            return
        gone = set(plugs)
        ref.weight_plugs = [p for p in ref.weight_plugs if p not in gone]
        for p in gone:
            ref._saved_weights.pop(p, None)
        ref._disconnected = [(s, d) for s, d in ref._disconnected if d not in gone]

    # ============================================================ 編集状態（シーンを基準姿勢へ）
    @property
    def editing(self) -> bool:
        """基準姿勢に入っているか。シーンが入れ替わっていたら（UUID が違う）元へ戻さずに捨てて False。"""
        ref = self._ref
        if ref is None or not ref.active:
            return False
        if not self._ref_alive():
            self._forget_edit()
            return False
        return True

    @property
    def applied_point(self) -> Optional[tuple[int, int, int]]:
        """シーンに今当たっているポーズの点 (レイヤー番号, row, col)。無ければ（キーが当たっているときも）None。"""
        a = self._applied if self.editing else None
        return a if a is not None and a[0] >= 0 else None

    @property
    def applied_key(self) -> Optional[int]:
        """シーンに今当たっているポーズがパース補正のキーのとき、その番号。無ければ None。"""
        a = self._applied if self.editing else None
        return a[1] if a is not None and a[0] == -1 else None

    @property
    def applied_lip(self) -> Optional[tuple[str, str]]:
        """シーンに今当たっているポーズがリップシンクのマスのとき、(音素, 感情)。無ければ None。"""
        a = self._applied if self.editing else None
        return self._applied_lip if a is not None and a[0] == -2 else None

    @property
    def lip_try_active(self) -> bool:
        """リップシンクの「試す」の最中か（編集状態を抜けたら False）。"""
        return self._lip_try is not None and self.editing

    def lip_try_values(self) -> "LipTry":
        """今の「試す」の入力の複製（試していなければ初期値）。"""
        return copy.deepcopy(self._lip_try) if self._lip_try is not None else LipTry()

    def _ref_alive(self) -> bool:
        ref = self._ref
        assert ref is not None
        try:
            for m in ref.meshes:
                if not cmds.objExists(m) or (cmds.ls(m, uuid=True) or []) != self._ref_uuids.get(m):
                    return False
        except RuntimeError:
            return False
        return True

    def _forget_edit(self) -> None:
        """シーンへは触らずに編集状態を捨てる（シーンが入れ替わった後）。"""
        if self._ref is not None:
            self._ref.active = False
            self._ref._saved_joints.clear()
            self._ref._saved_weights.clear()
            self._ref._disconnected.clear()
        self._ref = None
        self._ref_uuids = {}
        self._applied = None
        self._lip_try = None
        self._base = None
        self._sculpt = None  # シーンが入れ替わった: 彫りの状態も捨てる（新しいシーンの blendShape には触らない）

    def _bone_names(self, doc: Document) -> list[str]:
        names: dict[str, None] = {}
        if doc.grid.base_bone:
            names.setdefault(doc.grid.base_bone, None)
        for layer in doc.layers:
            for pt in layer.points.values():
                for b in pt.pose.bones:
                    names.setdefault(b, None)
        if doc.perspective is not None:
            for key in doc.perspective.keys:
                for b in key.bones:
                    names.setdefault(b, None)
        return list(names)

    def begin_edit(self) -> scene_mod.Reference:
        """基準姿勢（バインドポーズ・全シェイプの重み 0）に入り、Reference を保つ。既に入っていればそれを返す。"""
        doc = self.require()
        if self.editing:
            assert self._ref is not None
            return self._ref
        pose_apply.assert_maya_space(doc)
        meshes = self._resolve_meshes(doc)
        self._suspended = None  # 自分で入り直した: 保存の途中で抜けた記録は不要
        try:
            with _no_undo():
                ref = scene_mod.enter_reference_pose(meshes, extra_joints=self._bone_names(doc))
        except scene_mod.ReferenceError_ as e:
            raise FacialSessionError(f"基準姿勢にできません: {e}") from e
        self._ref = ref
        self._ref_uuids = {m: (cmds.ls(m, uuid=True) or []) for m in ref.meshes}
        self._applied = None
        self.edit_warnings = list(ref.warnings)
        self._notify_state()
        return ref

    def end_edit(self, quiet: bool = False, sync_preview: bool = True) -> None:
        """編集状態を抜けて、基準姿勢に入る前の姿勢・重み・接続へ戻す（入っていなければ何もしない）。quiet=True は例外を握りつぶす。

        プレビュー（rig）があれば、ここで補正が再開する（式の出力接続が戻る）。編集中に格子・ベイクが変わって作り直しが要る状態に
        なっていたら、quiet でなければここで作り直す（sync_preview=False で止められる。ベイクは焼いたあとに自分で作り直す）。"""
        ref = self._ref
        if ref is None:
            return
        if self._sculpt is not None:  # 彫り中に抜けるときは、先にスカルプト対象を解除する（quiet でなければポーズへの記録まで）
            try:
                self.sculpt_end(save=not quiet)
            except Exception:  # noqa: BLE001
                self._sculpt = None
                if not quiet:
                    raise
                lifecycle.report_error("彫りを終えられませんでした", traceback.format_exc(), once=False)
        try:
            if ref.active and self._ref_alive():
                with _no_undo():
                    ref.restore()
            else:
                self._forget_edit()
        except BaseException:
            if not quiet:
                raise
            lifecycle.report_error("基準姿勢から元へ戻せませんでした", traceback.format_exc(), once=False)
        finally:
            ref.active = False
            self._ref = None
            self._ref_uuids = {}
            self._applied = None
            self._lip_try = None
            self._base = None  # 土台の表情は編集状態の間だけ（シーンの重みは上で元へ戻った）
        if not quiet and sync_preview and self._preview_pending:
            self._sync_preview()
        self._notify_state()

    # ------------------------------------------------------------ ツールのリロードをまたぐ引き継ぎ（M-3）
    def export_state(self) -> Optional[dict[str, Any]]:
        """リロードの前: 開いているデータ・保存先・未保存の印・エディタ内 Undo / Redo・選択・編集中の値を、素の dict / list にして返す
        （古いモジュールのクラスのオブジェクトを新しいモジュールへ渡さない）。データが開かれていなければ None。"""
        if self.presenters is None:
            return None
        ctx = self.presenters.ctx
        curves, bones = fcpose_io._pose_parts(SourcePose(dict(self.pose.curves), dict(self.pose.bones)))
        return {
            "doc": fcpose_io.to_dict(self.doc),
            "path": str(self.path) if self.path is not None else None,
            "dirty": self.dirty,
            "path_confirmed": self._path_confirmed,
            "converted_from": copy.deepcopy(self.converted_from),
            "source_path": str(self.source_path) if self.source_path is not None else None,
            "undo": [fcpose_io.to_dict(d) for d in self._undo],
            "redo": [fcpose_io.to_dict(d) for d in self._redo],
            "active_layer": ctx.active_layer,
            "selection": list(ctx.selection) if ctx.selection is not None else None,
            "key_target": ctx.selected_key(),
            "lip_target": list(ctx.selected_lip()) if ctx.selected_lip() is not None else None,
            "buffer": {"curves": curves, "bones": bones},
            "scene_grid": self.scene_grid.export_state(),
        }

    def import_state(self, state: dict[str, Any]) -> bool:
        """`export_state` の結果で、リロード前の状態を作り直す（新しいモジュールのセッションで呼ぶ）。戻り: 復元できたか。"""
        doc = fcpose_io.from_dict(state["doc"])
        path = Path(state["path"]) if state.get("path") else None
        self._install(doc, path, confirmed=bool(state.get("path_confirmed")), dirty=bool(state.get("dirty")))
        self.converted_from = copy.deepcopy(state.get("converted_from"))
        self.source_path = Path(state["source_path"]) if state.get("source_path") else None
        self._undo[:] = [fcpose_io.from_dict(d) for d in state.get("undo", [])]
        self._redo[:] = [fcpose_io.from_dict(d) for d in state.get("redo", [])]
        ctx = self._pres().ctx
        n = len(doc.layers)
        if n:
            ctx.set_active_layer(min(max(int(state.get("active_layer", 0)), 0), n - 1))
        sel = state.get("selection")
        kt = state.get("key_target")
        lt = state.get("lip_target")
        if sel is not None and ctx.in_grid(int(sel[0]), int(sel[1])):
            ctx.set_selection((int(sel[0]), int(sel[1])))
        elif kt is not None and doc.perspective is not None and 0 <= int(kt) < len(doc.perspective.keys):
            ctx.set_key_target(int(kt))
        elif lt is not None and doc.lip_sync is not None:
            ctx.set_lip_target((str(lt[0]), str(lt[1])))
            self._prune_lip_target()
        if ctx.has_target:
            buf = state.get("buffer") or {}
            pose = fcpose_io._read_pose(buf.get("curves"), buf.get("bones"))
            self.pose.curves, self.pose.bones = pose.curves, pose.bones  # 保存していないスライダーの値
        self._changed(dirty=False)
        self.scene_grid.import_state(state.get("scene_grid"))
        return True

    # ------------------------------------------------------------ 保存・出力のあいだだけ編集状態を抜ける（M-1）
    def suspend_edit_for_save(self) -> bool:
        """シーンの保存・出力の直前: 編集状態にいれば、元の姿勢・重み・接続へ戻す（基準姿勢をファイルに書かないため）。

        土台の表情・彫りの状態は控えておく（編集中の値・選んでいる点は Presenter が持っているのでそのまま）。
        戻すのは `resume_edit_after_save`。編集状態でなければ False。"""
        self.scene_grid.suspend()  # シーンに出している格子は、保存・出力に入れない
        if self._suspended is not None:
            return True
        if not self.editing:
            return False
        st = _SuspendedEdit(base=self._base, sculpt=self._sculpt, lip_try=self._lip_try)
        ref = self._ref
        assert ref is not None
        try:
            with _no_undo():
                ref.restore()
        finally:
            ref.active = False
            self._ref = None
            self._ref_uuids = {}
            self._applied = None
            self._lip_try = None
            self._base = None
            self._sculpt = None
        self._suspended = st
        return True

    def resume_edit_after_save(self) -> None:
        """保存・出力のあと: 抜けていた編集状態へ同じ内容で入り直す（点・編集中の値・土台の表情。シーンの変更フラグは保存の直後のまま）。"""
        self.scene_grid.resume()
        st, self._suspended = self._suspended, None
        if st is None or self.presenters is None:
            return
        modified = bool(cmds.file(query=True, modified=True))
        try:
            self.begin_edit()
            self._base = st.base
            self._sculpt = st.sculpt
            self._apply_buffer()
            if st.lip_try is not None:  # 「試す」の最中だった: 同じ入力で続ける
                self._lip_try = st.lip_try
                self._apply_lip_try()
        except Exception:  # noqa: BLE001  入り直せなくても保存は成功させる（シーンは元の姿勢のまま）
            self.end_edit(quiet=True)
            lifecycle.report_error("保存のあと編集状態へ戻れませんでした（編集状態を抜けました）", traceback.format_exc(), once=False)
        finally:
            cmds.file(modified=modified)
        self._notify_state()

    def on_before_save(self) -> None:
        """シーンの保存の直前（kBeforeSave）: 編集状態を一時的に抜け、開いているデータの場所をシーンに記録する（M-1・M-2）。"""
        self.suspend_edit_for_save()
        if self.presenters is None:
            return
        path = self.path or default_path(self.doc.asset or "untitled")
        if self._path_confirmed or not path.exists():  # 一度も保存していない既定の場所に別のファイルがあるときは記録しない
            self._remember(path)

    def on_after_save(self) -> None:
        """シーンの保存の直後（kAfterSave）。"""
        self.resume_edit_after_save()

    @contextlib.contextmanager
    def edit_guard(self):
        """`with session.edit_guard():` — 入るとき begin_edit、出るとき（例外でも）必ず end_edit。"""
        self.begin_edit()
        try:
            yield self
        finally:
            self.end_edit(quiet=True)

    def _layered_pose(self, pose: SourcePose) -> SourcePose:
        """ポーズに土台の表情を足した、シーンへ当てる形。土台が無ければそのまま。"""
        base = self._base.curves if self._base is not None else {}
        if not base:
            return pose
        doc = self.require()
        prof = self.profile
        lay = base_expr.layer(pose.curves, base, lambda n: profile_mod.effective_limit(doc, prof, n))
        return SourcePose(curves=lay.weights, bones=pose.bones)

    @staticmethod
    def _track_weights(ref: scene_mod.Reference, names: Iterable[str]) -> None:
        """当てたシェイプの重みを、編集を抜けるとき 0 へ戻す対象（基準姿勢の記録）に入れる（編集中に作られたターゲットも）。"""
        for name in names:
            for plug in scene_mod.reference_curve_plugs(ref, name):
                if plug not in ref.weight_plugs:
                    ref.weight_plugs.append(plug)
                    ref._saved_weights[plug] = 0.0

    @contextlib.contextmanager
    def pose_scope(self):
        """`with session.pose_scope() as apply:` — 点のポーズを一時的にシーンへ当てる（サムネイルなど、見るだけの処理用）。

        `apply(layer_index, row, col)` は、基準姿勢にその点のポーズだけを当てる（土台の表情は当たらない。点が空なら基準姿勢）。
        **選択中の点・編集中の値（未保存の編集も）・土台の表情・文書には触らない**。with を抜けると、入る前の状態へ戻す:
        編集状態にいなかったなら基準姿勢を抜けてシーンを元へ（`end_edit`）、いたなら編集中の値 + 土台をもう一度当てる。
        彫っている最中は使えない（FacialSessionError）。"""
        doc = self.require()
        if self._sculpt is not None and self.editing:
            raise FacialSessionError("彫っている最中は使えません（「彫り終わる」を押してから）")
        was = self.editing
        ref = self.begin_edit()

        def apply(layer_index: int, row: int, col: int) -> None:
            d = self.require()
            pt = d.layers[layer_index].points.get((row, col))
            pose = pt.pose if pt is not None else SourcePose()
            pose_apply.apply_pose(d, pose, ref)
            self._track_weights(ref, pose.curves)
            self._applied = None  # シーンに当たっているのは選択中の点のポーズではなくなった

        try:
            yield apply
        finally:
            try:
                if was and self.presenters is not None and self.editing:
                    self._apply_buffer()
                else:
                    self.end_edit(quiet=True)
            except Exception:  # noqa: BLE001  後片付けで落とさない（シーンを基準姿勢のまま残さない）
                self.end_edit(quiet=True)
                lifecycle.report_error("一時的に当てたポーズを元へ戻せませんでした（編集状態を抜けました）", traceback.format_exc(), once=False)

    def _apply_buffer(self) -> Optional[pose_apply.ApplyReport]:
        """PosePresenter の編集中の値（= 選択中の点のポーズ）+ 土台の表情をシーンへ当てる。選択も土台も無ければ基準へ戻す。"""
        ref = self._ref
        if ref is None:
            raise FacialSessionError("編集状態に入っていません（begin_edit）")
        pres = self._pres()
        ctx = pres.ctx
        self._lip_try = None  # ポーズを当て直すと、「試す」は終わる（シーンは編集状態が見せるものへ戻る）
        if not ctx.has_target:
            if self._base is None:
                with _no_undo():
                    pose_apply.reset_to_reference(ref)
                self._applied = None
                return None
            pose = SourcePose()
        else:
            pose = pres.pose.pose_to_apply()
        pose = self._layered_pose(pose)
        for name in pose.curves:  # 編集中に作られた / Undo で戻ったターゲットの重みも、編集を抜けるとき 0 へ戻す対象にする
            ref._curve_cache.pop(name, None)
        with _no_undo():
            rep = pose_apply.apply_pose(ctx.doc, pose, ref)
        self._track_weights(ref, pose.curves)
        if ctx.selected_key() is not None:
            self._applied = (-1, ctx.selected_key(), -1)  # キーが当たっている印（applied_key）
        elif ctx.selected_lip() is not None:
            self._applied = (-2, 0, 0)  # リップシンクのマスが当たっている印（applied_lip）
            self._applied_lip = ctx.selected_lip()
        elif ctx.selection is None:
            self._applied = None
        else:
            li = min(max(ctx.active_layer, 0), len(ctx.doc.layers) - 1)
            self._applied = (li, ctx.selection[0], ctx.selection[1])
        self.last_apply = rep
        return rep

    @scene_op
    def apply_buffer_to_scene(self) -> Optional[pose_apply.ApplyReport]:
        """編集中の値（スライダー・取り込んだ値）をシーンへ当てる。編集状態に入っていなければ入る。"""
        self.begin_edit()
        rep = self._apply_buffer()
        self._notify_state()
        return rep

    @scene_op
    def capture_from_scene(self, working_set_only: Optional[bool] = None) -> IngestReport:
        """シーン（Shape Editor・チャンネルボックス・ジョイントの動き）を読み、編集中の値（PosePresenter）へ取り込む。
        working_set_only を省くと PosePresenter の絞り込みの設定に従う。保存はしない（`save_point`）。"""
        if not self.editing:
            raise FacialSessionError("編集状態に入っていません（begin_edit / select_point）")
        pres = self._pres()
        if not pres.ctx.has_target:
            return IngestReport(ok=False, code="no_point", message="点（またはパース補正のキー）が選択されていません")
        wso = pres.pose.working_set_only if working_set_only is None else bool(working_set_only)
        assert self._ref is not None
        doc = pres.ctx.doc
        pose = pose_apply.capture_pose(doc, self._ref, working_set_only=wso)
        curves = pose.curves
        self.last_capture_base_ignored = []
        self.last_capture_below_base = []
        base = self._base.curves if self._base is not None else {}
        if base:  # 土台を引く（ポーズの値 = シーンの値 − 土台の値）。除外・作業セットの外のシェイプは触らず、編集中の値を残す
            ws = set(doc.working_set.curves) if wso and doc.working_set.curves else None
            skip = [n for n in base if n in doc.exclude.curves or (ws is not None and n not in ws)]
            curves, self.last_capture_base_ignored = base_expr.subtract(pose.curves, base, skip=skip)
            for n in skip:
                if n in pres.pose.curves:
                    curves[n] = pres.pose.curves[n]
        # 土台より低くしたシェイプは差分が負になる。ポーズは可動域（0〜）で丸めるので入れられない: 黙って捨てず、知らせる（C-2）
        below = sorted(n for n, v in curves.items() if n in base and v < -base_expr.EPS)
        self.last_capture_below_base = below
        rep = pres.pose.ingest(curves, pose.bones, replace=True, working_set_only=wso)
        rep.below_base = below
        self._notify_state()
        return rep

    # ------------------------------------------------------------ 土台の表情（モジュールの docstring）
    @property
    def base_expression_name(self) -> str:
        """有効な土台の表情の名前。無ければ ""（編集状態を抜けると外れる）。"""
        return self._base.name if self._base is not None and self.editing else ""

    @property
    def base_expression_curves(self) -> dict[str, float]:
        """土台の表情の重み（シーンのシェイプ名 → 重み）。無ければ空。"""
        return dict(self._base.curves) if self._base is not None and self.editing else {}

    def base_state(self) -> "BaseState":
        """土台とポーズの今の重なり（画面の表示用）。土台が無ければ空の BaseState。"""
        if self._base is None or not self.editing or self.presenters is None:
            return BaseState()
        ctx = self.presenters.ctx
        pose = self.pose.pose_to_apply().curves if ctx.has_target else {}
        doc, prof = ctx.doc, self.profile
        lay = base_expr.layer(pose, self._base.curves, lambda n: profile_mod.effective_limit(doc, prof, n))
        return BaseState(
            name=self._base.name,
            curves=dict(self._base.curves),
            shared=lay.shared,
            base_only=lay.base_only,
            over_limit=lay.over_limit,
        )

    @scene_op
    def set_base_expression(self, name: str, curves: dict[str, float]) -> "BaseState":
        """土台の表情を当てる（シーンだけ。編集状態に入り、土台 + 編集中のポーズをシーンへ当てる）。すでにあれば置き換える。
        curves はシーンのシェイプ名 → 重み（`anim_import.resolve_curve_weights` で照合したもの）。0 の値は捨てる。全部 0 なら FacialSessionError。"""
        self.require()
        weights = {str(n): float(w) for n, w in curves.items() if abs(float(w)) > pose_apply.THRESHOLD_W}
        if not weights:
            raise FacialSessionError("土台にできるシェイプがありません（全部 0 です）")
        prev = self._base
        self.begin_edit()
        self._base = BaseExpression(str(name) or "土台", weights)
        try:
            self._apply_buffer()
        except BaseException:
            self._base = prev
            raise  # scene_op が編集状態を抜ける（土台も外れる）
        self._notify_state()
        return self.base_state()

    def clear_base_expression(self, reapply: bool = True) -> bool:
        """土台の表情を外す。あったら True。編集中なら、編集中のポーズだけをシーンへ当て直す（reapply=False で当て直さない）。"""
        if self._base is None:
            return False
        self._base = None
        if reapply and self.presenters is not None and self.editing:
            self._reapply_safely()
        self._notify_state()
        return True

    @scene_op
    def select_point(
        self,
        row: int,
        col: int,
        choice: Optional[str] = None,
        move_camera: bool = False,
        camera: Optional[str] = None,
    ) -> SelectResult:
        """点を選び、その点のポーズをシーンへ当てる（編集状態に入っていなければ入る）。

        編集中のポーズに未保存の変更があると `needs_confirm` を返す（何も変えない）。ビューが「保存 / 破棄 / 取りやめ」を聞き、
        同じ呼び出しを `choice="save" | "discard" | "cancel"` つきで繰り返す。save は `save_point`（Undo 可・FC_* の掃除つき）を通す。
        `choice="cancel"` は **1 回の呼び出しで足りる**（先に choice なしで呼ばなくてよい）: 確認が要る状況なら `cancelled` を返して
        何も変えず、確認が要らない状況（未保存の編集が無い）なら普通に選ぶ。
        """
        try:
            return self._select_point(row, col, choice, move_camera, camera)
        finally:
            self._notify_state()

    def _select_point(self, row: int, col: int, choice: Optional[str], move_camera: bool, camera: Optional[str]) -> SelectResult:
        if self._sculpt is not None and choice != CONFIRM_CANCEL:
            self.sculpt_end()  # 点を移る前に、彫りを終えて今の点へ記録する
        pres = self._pres()
        was_editing = self.editing
        self.begin_edit()
        r = pres.grid.select(row, col)
        if r.status == SELECT_NEEDS_CONFIRM:
            if choice is None:
                return r
            if choice == CONFIRM_SAVE:
                self.save_point()
                r = pres.grid.select(row, col)
                if r.status == SELECT_NEEDS_CONFIRM:  # 保存しても差が残る（丸めの差など）: 捨てて進む
                    r = pres.grid.confirm_select(CONFIRM_DISCARD)
                r.saved = True
            else:  # discard / cancel / 不正な答え
                return_early = choice == CONFIRM_CANCEL
                r = pres.grid.confirm_select(choice)
                if return_early or r.status != SELECT_SELECTED:
                    return r
        if r.status == SELECT_SELECTED or (r.status == SELECT_SAME and (not was_editing or self._applied is None)):
            self._apply_buffer()
        if move_camera and r.camera_jump:
            self.camera_to_point(row, col, camera)
        return r

    @scene_op
    def select_key(
        self,
        index: int,
        choice: Optional[str] = None,
        move_camera: bool = False,
        camera: Optional[str] = None,
    ) -> SelectResult:
        """パース補正のキーを編集の対象にし、そのキーのポーズをシーンへ当てる（編集状態に入っていなければ入る。格子の点の選択は外れる）。

        未保存のポーズ編集があると `needs_confirm`（`select_point` と同じ。`choice="save" | "discard" | "cancel"` つきで繰り返す）。
        move_camera=True: カメラを正面（Yaw 0 / Pitch 0）に置き、軸が距離ならキーの距離へ、画角ならキーの画角へ合わせる（`camera_to_key`）。"""
        try:
            return self._select_key(index, choice, move_camera, camera)
        finally:
            self._notify_state()

    def _select_key(self, index: int, choice: Optional[str], move_camera: bool, camera: Optional[str]) -> SelectResult:
        if self._sculpt is not None and choice != CONFIRM_CANCEL:
            self.sculpt_end()
        pres = self._pres()
        was_editing = self.editing
        self.begin_edit()
        r = pres.perspective.select_key(index)
        if r.status == SELECT_NEEDS_CONFIRM:
            if choice is None:
                return r
            if choice == CONFIRM_SAVE:
                self.save_point()
                r = pres.perspective.select_key(index)
                if r.status == SELECT_NEEDS_CONFIRM:  # 保存しても差が残る（丸めの差など）: 捨てて進む
                    r = pres.perspective.confirm_select_key(CONFIRM_DISCARD)
                r.saved = True
            else:
                return_early = choice == CONFIRM_CANCEL
                r = pres.perspective.confirm_select_key(choice)
                if return_early or r.status != SELECT_SELECTED:
                    return r
        if r.status == SELECT_SELECTED or (r.status == SELECT_SAME and (not was_editing or self._applied is None)):
            self._apply_buffer()
        if move_camera and r.camera_jump:
            self.camera_to_key(index, camera)
        return r

    @scene_op
    def select_lip_cell(self, phoneme: str, emotion: str = "", choice: Optional[str] = None) -> SelectResult:
        """リップシンクのマス（音素 × 感情。基本は ""）を編集の対象にし、その行のシェイプをシーンへ当てる（編集状態に入っていなければ入る。点・キーの選択は外れる）。

        未保存のポーズ編集があると `needs_confirm`（`select_point` と同じ。`choice="save" | "discard" | "cancel"` つきで繰り返す）。
        「試す」の最中ならそれは終わる。"""
        try:
            return self._select_lip_cell(phoneme, emotion, choice)
        finally:
            self._notify_state()

    def _select_lip_cell(self, phoneme: str, emotion: str, choice: Optional[str]) -> SelectResult:
        if self._sculpt is not None and choice != CONFIRM_CANCEL:
            self.sculpt_end()
        pres = self._pres()
        was_editing = self.editing
        self.begin_edit()
        r = pres.lipsync.select_lip_cell(phoneme, emotion)
        if r.status == SELECT_NEEDS_CONFIRM:
            if choice is None:
                return r
            if choice == CONFIRM_SAVE:
                self.save_point()
                r = pres.lipsync.select_lip_cell(phoneme, emotion)
                if r.status == SELECT_NEEDS_CONFIRM:  # 保存しても差が残る: 捨てて進む
                    r = pres.lipsync.confirm_select_lip_cell(CONFIRM_DISCARD)
                r.saved = True
            else:
                return_early = choice == CONFIRM_CANCEL
                r = pres.lipsync.confirm_select_lip_cell(choice)
                if return_early or r.status != SELECT_SELECTED:
                    return r
        if r.status == SELECT_SELECTED or (r.status == SELECT_SAME and (not was_editing or self._applied is None)):
            self._apply_buffer()
        return r

    @scene_op
    def set_active_layer(self, index: int, choice: Optional[str] = None) -> SelectResult:
        """編集対象のレイヤーを切り替え、編集中なら選択中の点のポーズを当て直す。`choice` は select_point と同じ（cancel は 1 回で足りる）。"""
        try:
            return self._set_active_layer(index, choice)
        finally:
            self._notify_state()

    def _set_active_layer(self, index: int, choice: Optional[str]) -> SelectResult:
        if self._sculpt is not None and choice != CONFIRM_CANCEL:
            self.sculpt_end()
        pres = self._pres()
        r = pres.layers.set_active(index)
        if r.status == SELECT_NEEDS_CONFIRM:
            if choice is None:
                return r
            if choice == CONFIRM_SAVE:
                self.save_point()
                r = pres.layers.set_active(index)
                if r.status == SELECT_NEEDS_CONFIRM:
                    r = pres.layers.confirm_set_active(CONFIRM_DISCARD)
                r.saved = True
            else:
                r = pres.layers.confirm_set_active(choice)
                if r.status != SELECT_SELECTED:
                    return r
        if r.status == SELECT_SELECTED and self.editing:
            self._apply_buffer()
        return r

    # ------------------------------------------------------------ 編集中のポーズの値（薄い道具）
    def _pose_op(self, result: PoseResult, apply: bool) -> PoseResult:
        if result.ok and apply and self.editing:
            self._apply_buffer()
        self._notify_state()
        return result

    @scene_op
    def set_curve(self, name: str, value: float, apply: bool = True) -> PoseResult:
        """編集中のシェイプの値を入れ（可動域で丸める）、編集状態ならシーンへ当てる。"""
        return self._pose_op(self.pose.set_curve(name, value), apply)

    @scene_op
    def set_bone(self, name: str, offset, apply: bool = True) -> PoseResult:
        return self._pose_op(self.pose.set_bone(name, offset), apply)

    @scene_op
    def zero_pose(self, apply: bool = True) -> PoseResult:
        return self._pose_op(self.pose.zero(), apply)

    @scene_op
    def reset_bone(self, name: str, apply: bool = True) -> PoseResult:
        """1 本のボーンを戻す。編集中の値からそのボーンの項目を**取り除く**（恒等のずれを残さない）。"""
        return self._pose_op(self.pose.reset_bone(name), apply)

    @scene_op
    def reset_bones(self, apply: bool = True) -> PoseResult:
        return self._pose_op(self.pose.reset_bones(), apply)

    @scene_op
    def mirror_pose(self, apply: bool = True) -> PoseResult:
        return self._pose_op(self.pose.mirror(), apply)

    @scene_op
    def reload_pose(self, apply: bool = True) -> None:
        """編集中の値を捨てて保存済みのポーズを読み直す。"""
        self.pose.reload()
        if apply and self.editing:
            self._apply_buffer()
        self._notify_state()

    @scene_op
    def import_pose_file(self, path: str | Path, working_set_only: bool = False, apply: bool = True) -> IngestReport:
        """単独ポーズ（`poses/*.fcpose.json`）を編集中の値へ読み込む（座標系は変換）。保存はしない。"""
        text = Path(path).read_text(encoding="utf-8-sig")
        rep = self.pose.import_pose(text, working_set_only=working_set_only)
        if rep.ok and apply and self.editing:
            self._apply_buffer()
        self._notify_state()
        return rep

    @scene_op
    def import_pose_curves(
        self,
        curves: dict[str, float],
        bones: Optional[dict] = None,
        *,
        replace: bool = True,
        working_set_only: bool = False,
        keep_bones: bool = False,
        apply: bool = True,
    ) -> IngestReport:
        """読んだ値（アニメ・今のフレームから `anim_import` が作った、シーンのシェイプ名 → 重み / ボーンのずれ）を編集中の値へ入れ、
        apply=True ならシーンへ当てる（編集状態に入る）。保存はしない。keep_bones=True は編集中のボーンをそのまま残す（置き換えで消さない）。
        読み込み自体が失敗したら（ok=False）編集中の値は変わらない。シーンへ当てられなかったときは `last_apply_error` に理由を残す（読み込みは成功）。"""
        self.last_apply_error = ""
        pres = self._pres()
        new_bones = copy.deepcopy(pres.pose.bones) if keep_bones else dict(bones or {})
        rep = pres.pose.ingest(curves, new_bones, replace=replace, working_set_only=working_set_only)
        if rep.ok and apply:
            try:
                self.apply_buffer_to_scene()
            except Exception as exc:  # noqa: BLE001  読み込み自体は成功。シーンへ当てられなかっただけ
                self.last_apply_error = str(exc)
                lifecycle.report_error("読み込んだポーズをシーンへ当てられませんでした", traceback.format_exc(), once=False)
        self._notify_state()
        return rep

    @undoable
    def copy_from_document(
        self,
        src: Document,
        flags: Iterable[str] = ("keys_only",),
        src_layer_index: Optional[int] = None,
        dst_layer_index: Optional[int] = None,
    ) -> autofill.CopyReport:
        """別のデータ（Document）からポーズをコピーする（`autofill.copy_from`。F1-7）。1 回の呼び出し = 1 つの元に戻す。
        flags は "all_layers" / "working_set_only" / "keys_only" の組み合わせ。src_layer_index を省くと宛先のレイヤーと同じ名前（無ければ 0 番）、
        dst_layer_index を省くとアクティブレイヤー。宛先の点は非キーとして上書きする。ValueError / IndexError は呼び出し側へ（文書は元へ戻る）。"""
        dst = self.require()
        mode = autofill.parse_copy_mode(list(flags))  # ValueError: 知らない旗
        di = self.ctx.active_layer if dst_layer_index is None else dst_layer_index
        di = min(max(di, 0), len(dst.layers) - 1)
        if src_layer_index is None:
            dst_name = dst.layers[di].name
            src_layer_index = next((i for i, layer in enumerate(src.layers) if layer.name == dst_name), 0)
        with self.ctx.edit():  # 抜けたら "document" を通知し、選択中の点が変わっていれば編集中の値を読み直す
            return autofill.copy_from(src, self.doc, set(mode), src_layer_index, di)

    def copy_from_file(
        self,
        path: str | Path,
        flags: Iterable[str] = ("keys_only",),
        src_layer_index: Optional[int] = None,
        dst_layer_index: Optional[int] = None,
    ) -> autofill.CopyReport:
        """別のデータ（.fcpose.json）からコピーする（`copy_from_document` と同じ。読めなければ FcposeError / OSError / ValueError）。"""
        self.require()
        src = fcpose_io.load_document(path)
        return self.copy_from_document(src, flags, src_layer_index, dst_layer_index)

    def export_pose_file(self, path: str | Path) -> Optional[Path]:
        """編集中のポーズを単独ポーズのファイルに書く。空なら書かずに None。"""
        text = self.pose.export_text()
        if text is None:
            return None
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="\n")
        return p

    # ============================================================ コマンド（Document の変更 + シーンの後始末）
    @undoable(reapply=False)
    def save_point(self, keep_empty_key: bool = False) -> PoseResult:
        """編集中の値を選択中の点へ保存する（その点がキーになる）。シーンは触らない。"""
        return self.pose.save(keep_empty_key)

    # --- 格子の点 ---
    @undoable
    def unkey(self, row: Optional[int] = None, col: Optional[int] = None) -> CommandResult:
        return self.grid.unkey(row, col)

    @undoable
    def clear_point(self, row: Optional[int] = None, col: Optional[int] = None) -> CommandResult:
        """点を空にする。焼いた FC_* もシーンから消す。"""
        return self.grid.clear_point(row, col)

    @undoable
    def clear_layer(self) -> CommandResult:
        return self.grid.clear_layer()

    @undoable
    def generate(self, all_layers: bool = False):
        """キーから残りの点を自動生成する。"""
        return self.grid.generate(all_layers)

    def copy_pose(self, row: Optional[int] = None, col: Optional[int] = None) -> CommandResult:
        """点のポーズをクリップボードへ（Document は変えない）。"""
        return self.grid.copy_pose(row, col)

    @undoable
    def paste_pose(self, row: Optional[int] = None, col: Optional[int] = None) -> CommandResult:
        return self.grid.paste_pose(row, col)

    @undoable
    def paste_mirrored(self, row: Optional[int] = None, col: Optional[int] = None) -> CommandResult:
        return self.grid.paste_mirrored(row, col)

    def preview_resize(self, cols: int, rows: int, yaw_range: Optional[float] = None, pitch_range: Optional[float] = None) -> ResizeResult:
        return self.grid.preview_resize(cols, rows, yaw_range, pitch_range)

    @undoable
    def resize(self, cols: int, rows: int, yaw_range: Optional[float] = None, pitch_range: Optional[float] = None) -> ResizeResult:
        """格子の分割数（と範囲）を変える。作った点は角度で引き継ぎ、乗らなくなった FC_* は消す。"""
        return self.grid.resize(cols, rows, yaw_range, pitch_range)

    # --- レイヤー ---
    @undoable
    def _add_layer(self, name: str) -> LayerResult:
        return self.layers.add(name)

    def add_layer(self, name: str, select: bool = False) -> LayerResult:
        """レイヤーを足す。select=True なら足したレイヤーをアクティブにする（`LayerResult.switch`。needs_confirm のことがある）。"""
        res = self._add_layer(name)
        if res.ok and select and res.index is not None:
            res.switch = self.set_active_layer(res.index)
        return res

    @undoable
    def rename_layer(self, index: int, name: str) -> LayerResult:
        """改名。旧名の FC_* はシーンから消す（新しい名前の分は未ベイク）。"""
        return self.layers.rename(index, name)

    @undoable
    def delete_layer(self, index: int) -> LayerResult:
        """レイヤーを消す。そのレイヤーの FC_* もシーンから消す。"""
        return self.layers.delete(index)

    @undoable
    def set_layer_enabled(self, index: int, enabled: bool) -> LayerResult:
        return self.layers.set_enabled(index, enabled)

    @undoable
    def set_layer_emotion_curve(self, index: int, curve: str) -> LayerResult:
        return self.layers.set_emotion_curve(index, curve)

    @undoable
    def copy_layer(self, source: int, dest: int) -> LayerResult:
        """他のレイヤーのポーズをコピーする（叩き台作り）。"""
        return self.layers.copy_from(source, dest)

    # --- プロファイル・作業セット ---
    @undoable(notify=True)
    def apply_profile(self, name: str, overwrite: bool = False) -> CommandResult:
        """命名規則プロファイルを選ぶ（ミラーの規則・可動域が Document に入る）。プロジェクトの `facial/<asset>/profiles/` を先に探す。"""
        doc = self.require()
        prof = profile_mod.find_profile(name, [profile_mod.project_profiles_dir(project.root(), doc.asset or "")])
        if prof is None:
            return CommandResult(ok=False, code="not_found", message=f"プロファイル「{name}」が見つかりません")
        res = profile_mod.apply_to_document(prof, doc, overwrite)
        self._pres().ctx.set_profile(prof)
        return CommandResult(
            message=f"プロファイル「{prof.name}」を適用しました（可動域 追加 {res.limits_added} / 置換 {res.limits_overwritten} / 維持 {res.limits_kept}）"
        )

    @undoable(notify=True)
    def set_working_set(self, curves: Optional[Iterable[str]] = None, bones: Optional[Iterable[str]] = None) -> CommandResult:
        """作業セットを置き換える（None の側は触らない）。重複は除く。"""
        ws = self.require().working_set
        if curves is not None:
            ws.curves = list(dict.fromkeys(curves))
        if bones is not None:
            ws.bones = list(dict.fromkeys(bones))
        return CommandResult()

    @undoable(notify=True)
    def add_to_working_set(self, curves: Iterable[str] = (), bones: Iterable[str] = ()) -> CommandResult:
        ws = self.require().working_set
        ws.curves = list(dict.fromkeys([*ws.curves, *curves]))
        ws.bones = list(dict.fromkeys([*ws.bones, *bones]))
        return CommandResult()

    @undoable(notify=True)
    def remove_from_working_set(self, curves: Iterable[str] = (), bones: Iterable[str] = ()) -> CommandResult:
        ws = self.require().working_set
        c, b = set(curves), set(bones)
        ws.curves = [x for x in ws.curves if x not in c]
        ws.bones = [x for x in ws.bones if x not in b]
        return CommandResult()

    @undoable(notify=True)
    def set_intensity_curves(self, names: Iterable[str]) -> CommandResult:
        """表情の強さを測るシェイプ（`intensityCurves`。表情が強いときに補正を弱める計算に使う）を置き換える。重複は除く。
        空にすると作業セットのシェイプ全部が使われる（preview_rig / Unity 側の既定）。"""
        self.require().intensity_curves = [n for n in dict.fromkeys(names) if n]
        return CommandResult()

    @undoable(notify=True)
    def set_exclude(self, curves: Optional[Iterable[str]] = None, bones: Optional[Iterable[str]] = None) -> CommandResult:
        """補正の除外（R-17）を置き換える（None の側は触らない）。"""
        ex = self.require().exclude
        if curves is not None:
            ex.curves = list(dict.fromkeys(c.strip() for c in curves if c and c.strip()))
        if bones is not None:
            ex.bones = list(dict.fromkeys(b.strip() for b in bones if b and b.strip()))
        return CommandResult()

    def add_exclude(self, kind: str, pattern: str) -> CommandResult:
        """補正除外のパターン（名前にこの文字を含むシェイプ / ボーンはベイクで無視される）を足す。kind = "curve" / "bone"。"""
        pattern = (pattern or "").strip()
        if kind not in ("curve", "bone"):
            return CommandResult(ok=False, code="kind", message="種類は curve / bone のどちらかです")
        if not pattern:
            return CommandResult(ok=False, code="empty", message="パターンが空です")
        ex = self.require().exclude
        cur = list(ex.curves if kind == "curve" else ex.bones)
        if pattern in cur:
            return CommandResult(ok=False, code="exists", message=f"「{pattern}」は既にあります")
        return self.set_exclude(**({"curves": [*cur, pattern]} if kind == "curve" else {"bones": [*cur, pattern]}))

    def remove_exclude(self, kind: str, pattern: str) -> CommandResult:
        """補正除外のパターンを外す。"""
        if kind not in ("curve", "bone"):
            return CommandResult(ok=False, code="kind", message="種類は curve / bone のどちらかです")
        ex = self.require().exclude
        cur = list(ex.curves if kind == "curve" else ex.bones)
        if pattern not in cur:
            return CommandResult(ok=False, code="missing", message=f"「{pattern}」はありません")
        left = [p for p in cur if p != pattern]
        return self.set_exclude(**({"curves": left} if kind == "curve" else {"bones": left}))

    def is_excluded(self, kind: str, name: str) -> bool:
        """名前が補正除外パターンに当たるか（ポーズタブの表示用）。kind = "curve" / "bone"。"""
        doc = self.doc
        return doc is not None and pose_apply.is_excluded(doc, kind, name)

    # --- 部位別の強さ（ベイクのとき掛ける。F5-6）---
    def part_strengths(self) -> list[tuple[str, float]]:
        """部位別の強さの一覧（上が優先）。(パターン, 強さ)。"""
        doc = self.doc
        return [(e.pattern, e.strength) for e in strength_mod.entries_of(doc)] if doc is not None else []

    def part_strength_of(self, kind: str, name: str) -> float:
        """名前に掛かる強さ（当たるものが無い・除外されているときは 1）。kind = "curve" / "bone"。ポーズタブの表示用。"""
        doc = self.doc
        if doc is None:
            return 1.0
        excluded = pose_apply.is_excluded(doc, kind, name)
        return 1.0 if excluded else strength_mod.strength_for(strength_mod.effective_entries(doc), name)

    def _part_edit(self, edit: Callable[[Document], Optional[tuple[str, str]]]) -> CommandResult:
        bad = edit(self.require())
        return CommandResult(ok=False, code=bad[0], message=bad[1]) if bad else CommandResult()

    @undoable(notify=True)
    def add_part_strength(self, pattern: str, strength: float = 0.5) -> CommandResult:
        """部位別の強さを一覧の末尾に足す（名前にこの文字を含むシェイプ・ボーンに、ベイクのとき強さを掛ける）。"""
        return self._part_edit(lambda d: strength_mod.add_entry(d, pattern, strength))

    @undoable(notify=True)
    def remove_part_strength(self, index: int) -> CommandResult:
        return self._part_edit(lambda d: strength_mod.remove_entry(d, index))

    @undoable(notify=True)
    def set_part_strength(self, index: int, strength: float) -> CommandResult:
        return self._part_edit(lambda d: strength_mod.set_entry_strength(d, index, strength))

    @undoable(notify=True)
    def move_part_strength(self, index: int, delta: int) -> CommandResult:
        """上へ（delta = -1。優先度が上がる）/ 下へ（+1）。"""
        return self._part_edit(lambda d: strength_mod.move_entry(d, index, delta))

    # --- セットアップ ---
    @undoable(refresh=True, notify=True)
    def set_target(self, mesh: Optional[str] = None, extra_meshes: Optional[Sequence[str]] = None) -> CommandResult:
        """対象のメッシュ（顔）と extraMeshes を設定する（None の側は触らない）。メッシュはシーンに無ければ失敗。編集状態は抜ける。"""
        doc = self.require()
        new_mesh = doc.target.mesh if doc.target else ""
        new_extra = list(doc.target.extra_meshes) if doc.target else []
        try:
            if mesh is not None:
                new_mesh = self._stored_mesh_name(scene_mod.resolve_mesh(mesh)) if mesh else ""
            if extra_meshes is not None:
                new_extra = [self._stored_mesh_name(scene_mod.resolve_mesh(m)) for m in extra_meshes]
        except ValueError as e:
            return CommandResult(ok=False, code="mesh_not_found", message=str(e))
        self.end_edit(quiet=True)
        if doc.target is None:
            doc.target = Target()
        doc.target.mesh = new_mesh
        doc.target.extra_meshes = [m for m in new_extra if m != new_mesh]
        doc.target.lod_meshes = [m for m in doc.target.lod_meshes if m.mesh != new_mesh and m.mesh not in doc.target.extra_meshes]  # 顔・追加に入れたメッシュは LOD から外す
        return CommandResult()

    # --- LOD のメッシュ（F5-7）---
    def lod_meshes(self) -> list[tuple[str, int]]:
        """LOD のメッシュの一覧（文書に書いた名前, LOD 番号）。"""
        doc = self.doc
        return [(m.mesh, m.lod) for m in doc.target.lod_meshes] if doc is not None and doc.target is not None else []

    def next_lod_number(self) -> int:
        """次に割り当てる LOD 番号（今の最大 + 1。無ければ 1）。"""
        return max([lod for _m, lod in self.lod_meshes()], default=0) + 1

    @undoable(refresh=True, notify=True)
    def add_lod_mesh(self, mesh: str, lod: Optional[int] = None) -> CommandResult:
        """LOD のメッシュを足す（顔のメッシュと同じ FC_* をそのメッシュの blendShape に焼く）。lod を省くと次の番号。
        メッシュがシーンに無い・顔 / 追加のメッシュ・登録済みのとき、LOD 番号が 1 未満のときは失敗して文書は変わらない。編集状態は抜ける。"""
        doc = self.require()
        if doc.target is None or not doc.target.mesh:
            return CommandResult(ok=False, code="no_target", message="先に顔のメッシュを設定してください")
        try:
            stored = self._stored_mesh_name(scene_mod.resolve_mesh(mesh))
            long_name = scene_mod.resolve_mesh(stored)
        except ValueError as e:
            return CommandResult(ok=False, code="mesh_not_found", message=str(e))
        if lod is None:
            lod = self.next_lod_number()
        if isinstance(lod, bool) or not isinstance(lod, int) or lod < 1:
            return CommandResult(ok=False, code="lod_range", message="LOD 番号は 1 以上の整数にしてください（LOD0 は顔のメッシュです）")
        for name in doc.target.all_meshes():
            try:
                same = scene_mod.resolve_mesh(name) == long_name
            except ValueError:
                same = name == stored
            if same:
                kind = "LOD のメッシュに入っています" if name in {m.mesh for m in doc.target.lod_meshes} else "顔のメッシュ・追加のメッシュに入っています"
                return CommandResult(ok=False, code="exists", message=f"{scene_mod.short_name(long_name)} は既に{kind}")
        self.end_edit(quiet=True)
        doc.target.lod_meshes.append(LodMesh(mesh=stored, lod=lod))
        return CommandResult(message=f"LOD{lod} に {scene_mod.short_name(long_name)} を足しました。焼くと、顔のメッシュと同じ名前のシェイプがこのメッシュにも作られます")

    @undoable(refresh=True, notify=True)
    def remove_lod_mesh(self, mesh: str) -> CommandResult:
        """LOD のメッシュを外す（文書に書いた名前で指す。シーンに無くても外せる）。そのメッシュに焼いた FC_* はシーンに残る（メッシュを選んで消す）。"""
        doc = self.require()
        have = doc.target.lod_meshes if doc.target is not None else []
        left = [m for m in have if m.mesh != mesh]
        if len(left) == len(have):
            return CommandResult(ok=False, code="missing", message=f"「{mesh}」は LOD のメッシュにありません")
        self.end_edit(quiet=True)
        doc.target.lod_meshes = left  # type: ignore[union-attr]
        return CommandResult()

    @undoable(notify=True)
    def set_lod_number(self, mesh: str, lod: int) -> CommandResult:
        """LOD のメッシュの LOD 番号を変える（1 以上の整数）。"""
        doc = self.require()
        if isinstance(lod, bool) or not isinstance(lod, int) or lod < 1:
            return CommandResult(ok=False, code="lod_range", message="LOD 番号は 1 以上の整数にしてください（LOD0 は顔のメッシュです）")
        for m in doc.target.lod_meshes if doc.target is not None else []:
            if m.mesh == mesh:
                m.lod = lod
                return CommandResult()
        return CommandResult(ok=False, code="missing", message=f"「{mesh}」は LOD のメッシュにありません")

    @undoable(notify=True)
    def set_base_bone(self, name: str) -> CommandResult:
        name = (name or "").strip()
        if not name:
            return CommandResult(ok=False, code="empty", message="基準ボーン名が空です")
        self.require().grid.base_bone = name
        return CommandResult()

    @undoable(notify=True)
    def set_forward_axis(self, axis: str) -> CommandResult:
        """顔の前方向の軸（Maya は Y-up なので ±X / ±Z）。"""
        if axis not in FORWARD_AXES:
            return CommandResult(ok=False, code="invalid", message=f"前方向は {', '.join(FORWARD_AXES)} のどれかです")
        if axis[1] == "Y":
            return CommandResult(ok=False, code="up_axis", message="上軸（Y）は前方向にできません")
        self.require().grid.forward_axis = axis
        return CommandResult()

    @undoable(notify=True)
    def set_center_offset(self, offset: Sequence[float]) -> CommandResult:
        """格子の中心のずらし（基準ボーンのローカル。cm）。"""
        if len(offset) != 3 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in offset):
            return CommandResult(ok=False, code="invalid", message="中心のずらしは 3 つの数値です")
        self.require().grid.center_offset = (float(offset[0]), float(offset[1]), float(offset[2]))
        return CommandResult()

    def set_grid(
        self,
        cols: Optional[int] = None,
        rows: Optional[int] = None,
        yaw_range: Optional[float] = None,
        pitch_range: Optional[float] = None,
    ) -> ResizeResult:
        """格子の列・行・範囲を変える（省いた値は今のまま。`resize` と同じ。作った点は角度で引き継ぐ）。"""
        g = self.require().grid
        return self.resize(
            g.cols if cols is None else cols,
            g.rows if rows is None else rows,
            None if yaw_range is None else yaw_range,
            None if pitch_range is None else pitch_range,
        )

    @undoable(notify=True)
    def set_edge_fade(self, degrees: float) -> CommandResult:
        if not (isinstance(degrees, (int, float)) and math.isfinite(degrees) and degrees >= 0):
            return CommandResult(ok=False, code="invalid", message="端のフェードは 0 以上の数値です")
        self.require().grid.edge_fade = float(degrees)
        return CommandResult()

    @undoable(notify=True)
    def set_mirror(
        self,
        enabled: Optional[bool] = None,
        suffix_l: Optional[str] = None,
        suffix_r: Optional[str] = None,
        exclude: Optional[Iterable[str]] = None,
        bone_axis: Optional[str] = None,
    ) -> CommandResult:
        if bone_axis is not None and bone_axis not in MIRROR_AXES:
            return CommandResult(ok=False, code="invalid", message=f"鏡映の軸は {', '.join(MIRROR_AXES)} のどれかです")
        m = self.require().mirror
        if enabled is not None:
            m.enabled = bool(enabled)
        if suffix_l is not None:
            m.suffix_l = suffix_l
        if suffix_r is not None:
            m.suffix_r = suffix_r
        if exclude is not None:
            m.exclude = list(exclude)
        if bone_axis is not None:
            m.bone_axis = bone_axis
        return CommandResult()

    @undoable(notify=True)
    def set_autogen(self, mode: Optional[str] = None, idw_power: Optional[float] = None) -> CommandResult:
        if mode is not None and mode not in FILL_MODES:
            return CommandResult(ok=False, code="invalid", message=f"自動生成の方式は {', '.join(FILL_MODES)} のどれかです")
        if idw_power is not None and not (isinstance(idw_power, (int, float)) and math.isfinite(idw_power) and idw_power > 0):
            return CommandResult(ok=False, code="invalid", message="IDW のべき乗は 0 より大きい数値です")
        a = self.require().autogen
        if mode is not None:
            a.mode = mode
        if idw_power is not None:
            a.idw_power = float(idw_power)
        return CommandResult()

    @undoable(notify=True)
    def set_bake_options(self, delta_threshold: Optional[float] = None, differential: Optional[bool] = None) -> CommandResult:
        """ベイクのしきい値（cm）と差分ベイクの有無。"""
        if delta_threshold is not None and not (isinstance(delta_threshold, (int, float)) and math.isfinite(delta_threshold) and delta_threshold >= 0):
            return CommandResult(ok=False, code="invalid", message="しきい値は 0 以上の数値です")
        doc = self.require()
        if doc.bake is None:
            doc.bake = Bake()
        if delta_threshold is not None:
            doc.bake.delta_threshold = float(delta_threshold)
        if differential is not None:
            doc.bake.differential = bool(differential)
        return CommandResult()

    @undoable(notify=True)
    def set_policy(
        self,
        expression_dampen: Optional[float] = None,
        interp_speed: Optional[float] = None,
        snap_angle: Optional[float] = None,
        global_alpha: Optional[float] = None,
        fade: Optional[Sequence[float]] = None,
    ) -> CommandResult:
        """ランタイムの方針（表情での弱め・追従の速さ・スナップ角・全体の強さ・距離フェード）。"""
        p = self.require().policy
        if expression_dampen is not None:
            p.expression_dampen = float(expression_dampen)
        if interp_speed is not None:
            p.interp_speed = float(interp_speed)
        if snap_angle is not None:
            p.snap_angle = float(snap_angle)
        if global_alpha is not None:
            p.global_alpha = float(global_alpha)
        if fade is not None:
            p.fade = (float(fade[0]), float(fade[1]))
        return CommandResult()

    @undoable(notify=True)
    def set_quality(
        self,
        sharpness: Optional[float] = None,
        step_fps: Optional[float] = None,
        exaggeration: Optional[float] = None,
        angle_epsilon: Optional[float] = None,
        interpolation: Optional[str] = None,
    ) -> CommandResult:
        """品質（実行時の見え方。R-32 / R-33 / R-37）。None の値は触らない。範囲外・数値でない値は失敗（Document は変わらない）。

        - sharpness（0.01〜64。1 = そのまま）: 大きいほど、キーの角度のそばでそのキーのポーズそのものに寄る。Maya のプレビューにも掛かる
        - step_fps（0〜240。0 = 使わない）: 補正の更新を間引く fps。**Unity でだけ効く**（Maya のプレビューには掛からない）
        - exaggeration（0〜1）: 誇張（`_Ex`）の既定の強さ。プレビューの rig の `exaggeration` の初期値にもなる
        - angle_epsilon（0〜45°）: 角度がこれ未満しか変わらないときは再計算しない（Unity）
        - interpolation（"bilinear" / "catmullRom"）: 補間の種類。Maya のプレビューにも掛かる（式が作り直される）
        プレビューの式は sharpness・interpolation が変わると作り直される（`preview_is_stale`）。exaggeration を変えたときは、キーの無い rig の値も合わせる。
        """
        doc = self.require()
        q = doc.quality if doc.quality is not None else Quality()
        new = {"sharpness": q.sharpness, "step_fps": q.step_fps, "exaggeration": q.exaggeration, "angle_epsilon": q.angle_epsilon}
        limits = {
            "sharpness": (0.01, 64.0, "シャープさは 0.01〜64 の数値です"),
            "step_fps": (0.0, 240.0, "コマ打ちの fps は 0〜240 の数値です（0 = 使わない）"),
            "exaggeration": (0.0, 1.0, "誇張の強さは 0〜1 の数値です"),
            "angle_epsilon": (0.0, 45.0, "角度のしきい値は 0〜45 の数値です"),
        }
        for key, given in (("sharpness", sharpness), ("step_fps", step_fps), ("exaggeration", exaggeration), ("angle_epsilon", angle_epsilon)):
            if given is None:
                continue
            lo, hi, msg = limits[key]
            if not isinstance(given, (int, float)) or isinstance(given, bool) or not math.isfinite(given) or not lo <= given <= hi:
                return CommandResult(ok=False, code="invalid", message=msg)
            new[key] = float(given)
        new_interp = q.interpolation
        if interpolation is not None:
            if interpolation not in evaluate.INTERPOLATIONS:
                return CommandResult(ok=False, code="invalid", message="補間の種類は bilinear / catmullRom のどちらかです")
            new_interp = interpolation
        if all(new[k] == getattr(q, k) for k in new) and new_interp == q.interpolation:
            return CommandResult(code="unchanged")
        if doc.quality is None:
            doc.quality = Quality()
        for k, v in new.items():
            setattr(doc.quality, k, v)
        doc.quality.interpolation = new_interp
        if exaggeration is not None:
            self._sync_preview_exaggeration(float(exaggeration))
        return CommandResult()

    @undoable(notify=True)
    def set_material_link(self, enabled: bool) -> CommandResult:
        """マテリアル連携（`material.mode`。"none" / "propertyBlock"）。オンのとき、プレビューの角度を Toon マテリアルの「顔の角度連動」へつなぐ
        （プレビューの作り直しで反映。`preview_is_stale` が True になる）。Maya では "none" 以外はすべてオン。"""
        doc = self.require()
        mode = "propertyBlock" if enabled else "none"
        cur = doc.material.mode if doc.material is not None else "none"
        if (cur != "none") == bool(enabled):
            return CommandResult(code="unchanged")
        if doc.material is None:
            doc.material = Material()
        doc.material.mode = mode
        return CommandResult()

    def _sync_preview_exaggeration(self, value: float) -> None:
        """rig の `exaggeration` がキーも接続も無ければ、新しい既定値に合わせる（キーがあるときは触らない）。"""
        self._sync_preview_default(preview_rig.EXAGGERATION_ATTR, value)

    def _sync_preview_default(self, attr: str, value: float) -> None:
        """rig の attr がキーも接続も無ければ、新しい既定値に合わせる（キーがあるときは触らない）。"""
        try:
            rig = preview_rig.find_rig(self.require().asset or "")
            if rig is None or not cmds.attributeQuery(attr, node=rig, exists=True):
                return
            plug = f"{rig}.{attr}"
            if (cmds.keyframe(plug, query=True, keyframeCount=True) or 0) > 0 or cmds.listConnections(plug, source=True, destination=False):
                return
            cmds.setAttr(plug, value)
        except RuntimeError:
            pass

    @undoable(notify=True)
    def set_layer_weight_source(
        self,
        index: int,
        source: str,
        start: Optional[float] = None,
        end: Optional[float] = None,
        w_from: Optional[float] = None,
        w_to: Optional[float] = None,
    ) -> LayerResult:
        """感情レイヤーの重みの出どころ（R-35）。source = "direct" / "curve"（Timeline・部品の値から入る）/ "distance"（カメラとの距離で決まる）。
        distance のとき start / end（cm）と w_from / w_to（0〜1）。距離 start で w_from、end で w_to、間は直線。Neutral は不可。
        distance のレイヤーは、プレビューの `emotion_<レイヤー>` を使わず距離で決める（式は作り直される）。"""
        return self.layers.set_weight_source(index, source, start, end, w_from, w_to)

    def layer_weight_source(self, index: int) -> dict:
        """レイヤーの重みの出どころ（{"source": ..., "start", "end", "from", "to"}。無ければ {"source": "curve"}）。"""
        self.require()
        return self.layers.weight_source(index)

    # ============================================================ パース補正（R-34。docs/14 §5.8b）
    # 設定・キーの編集は Presenter（`session.perspective`）の変更系を包んだコマンド（Undo・古い FC_* の削除・プレビューの作り直しつき）。
    @property
    def perspective(self):
        return self._pres().perspective

    def key_label(self, index: int) -> str:
        """パース補正のキーの呼び名（「パース補正のキー 1（30 cm）」。番号は 1 から。シェイプ名の K の番号は 0 から）。"""
        pv = self.perspective.view()
        row = pv.keys[index] if 0 <= index < len(pv.keys) else None
        value = f"（{row.value:g} {pv.value_unit}）" if row is not None else ""
        return f"パース補正のキー {index + 1}{value}"

    def edit_target_label(self) -> str:
        """今の編集の対象の呼び名（「パース補正のキー 1（30 cm）」「レイヤー「Neutral」 点 R1, C2」）。対象が無ければ ""。"""
        if self.presenters is None:
            return ""
        ctx = self.presenters.ctx
        k = ctx.selected_key()
        if k is not None:
            return self.key_label(k)
        lc = ctx.selected_lip()
        if lc is not None:
            return self.lip_label(*lc)
        if ctx.selection is not None:
            return f"レイヤー「{ctx.layer.name}」 点 R{ctx.selection[0]}, C{ctx.selection[1]}"
        return ""

    @staticmethod
    def lip_label(phoneme: str, emotion: str = "") -> str:
        """リップシンクのマスの呼び名（「リップシンクの A（基本）」「リップシンクの A（Joy）」）。"""
        return f"リップシンクの {phoneme}（{emotion or '基本'}）"

    def perspective_view(self):
        """画面用の表示（`PerspectiveView`）。"""
        return self.perspective.view()

    @undoable(notify=True)
    def set_perspective_enabled(self, enabled: bool):
        return self.perspective.set_enabled(enabled)

    @undoable(notify=True)
    def set_perspective_axis(self, axis: str):
        return self.perspective.set_axis(axis)

    @undoable(notify=True)
    def set_perspective_strength(self, strength: float):
        """パース補正の強さ（0〜1）。プレビューの rig の `perspective` がキーも接続も無ければ、その値も合わせる。"""
        r = self.perspective.set_strength(strength)
        if r.ok:
            self._sync_preview_default(preview_rig.PERSPECTIVE_ATTR, float(strength))
        return r

    @undoable(notify=True)
    def add_perspective_key(self, value: Optional[float] = None, pose: Optional[SourcePose] = None):
        """キーを足す（value を省くと `suggest_value`）。ポーズは空か渡したもの。"""
        p = self.perspective
        return p.add_key(p.suggest_value() if value is None else value, pose)

    @undoable(notify=True)
    def set_perspective_key_value(self, index: int, value: float):
        return self.perspective.set_value(index, value)

    @undoable(notify=True)
    def remove_perspective_key(self, index: int):
        """キーを削除する（後ろのキーの番号が詰まる）。選んでいたキーなら編集の対象は外れる。いちばん後ろのシェイプはシーンから消える。"""
        return self.perspective.remove_key(index)

    @undoable(notify=True)
    def set_perspective_key_pose(self, index: int, pose: SourcePose):
        return self.perspective.set_key_pose(index, pose)

    @undoable(notify=True)
    def clear_perspective_key_pose(self, index: int):
        return self.perspective.clear_key_pose(index)

    def perspective_readout(self) -> str:
        """プレビューの今のパース補正の軸の値の表示（"距離 45 cm" / "画角 40°"）。パース補正を配線していないときは ""。"""
        rig = self.preview_rig_node()
        if rig is None or self.presenters is None or not preview_rig.has_perspective_targets(self.doc.asset or ""):
            return ""
        try:
            v = float(cmds.getAttr(f"{rig}.{preview_rig.OUT_PERSPECTIVE_ATTR}"))
        except (RuntimeError, ValueError):
            return ""
        axis = self.doc.perspective.axis if self.doc.perspective is not None else "distance"
        return f"画角 {v:.1f}°" if axis == "fov" else f"距離 {v:.0f} cm"

    def perspective_axis_value(self, camera: Optional[str] = None) -> Optional[float]:
        """今のカメラの軸の値（距離 = cm / 画角 = 度）。パース補正が無い・カメラが無いときは None。"""
        doc = self.require()
        axis = doc.perspective.axis if doc.perspective is not None else "distance"
        try:
            if axis == "fov":
                return preview_rig.camera_fov(self.camera_transform(camera))
            return preview_rig.view_distance(doc, camera)
        except (preview_rig.PreviewRigError, FacialSessionError, RuntimeError):
            return None

    # ============================================================ リップシンクの対応表（R-18。docs/14 §5.8c）
    # 設定・音素・行の編集は Presenter（`session.lipsync`）の変更系を包んだコマンド（Undo つき。ベイクは無い）。
    @property
    def lipsync(self):
        return self._pres().lipsync

    def lipsync_view(self):
        """画面用の表示（`LipSyncView`）。"""
        return self.lipsync.view()

    @undoable(notify=True)
    def set_lipsync_enabled(self, enabled: bool):
        return self.lipsync.set_enabled(enabled)

    @undoable(notify=True)
    def set_lipsync_strength(self, strength: float):
        return self.lipsync.set_strength(strength)

    @undoable(notify=True)
    def set_lipsync_volume(self, min=None, max=None, from_=None, to=None):  # noqa: A002
        return self.lipsync.set_volume(min, max, from_, to)

    @undoable(notify=True)
    def set_lipsync_follow(self, follow: float):
        return self.lipsync.set_follow(follow)

    @undoable(notify=True)
    def add_lip_phoneme(self, name: str):
        return self.lipsync.add_phoneme(name)

    @undoable(notify=True)
    def rename_lip_phoneme(self, old: str, new: str):
        return self.lipsync.rename_phoneme(old, new)

    @undoable(notify=True)
    def remove_lip_phoneme(self, name: str):
        """音素を消す（その音素の行も消える。編集の対象がその音素なら外れ、シーンは基準姿勢へ）。"""
        return self.lipsync.remove_phoneme(name)

    @undoable(notify=True)
    def move_lip_phoneme(self, name: str, to_index: int):
        return self.lipsync.move_phoneme(name, to_index)

    @undoable(notify=True)
    def set_lip_cell_pose(self, phoneme: str, emotion: str, pose: SourcePose, keep_empty: bool = False):
        return self.lipsync.set_cell_pose(phoneme, emotion, pose, keep_empty)

    @undoable(notify=True)
    def clear_lip_cell(self, phoneme: str, emotion: str = ""):
        """マスの行を消す（編集の対象のマスなら、編集中の値は保存済み（空）に読み直される）。"""
        return self.lipsync.clear_cell(phoneme, emotion)

    @undoable(notify=True)
    def create_lipsync_from_profile(self, overwrite: bool = False):
        """文書のプロファイルの `lipSync`（音素 → シェイプ名）から基本の行を作る（シーンのシェイプに合わせる）。"""
        return self.lipsync.create_from_profile(overwrite=overwrite)

    # --- 「試す」（モジュールの docstring「リップシンクの『試す』」）---
    def lip_try_start(self, choice: Optional[str] = None) -> SelectResult:
        """「試す」を始める（編集状態に入っていなければ入る）。編集の対象（点・キー・マス）は外れる。
        未保存のポーズ編集があると `needs_confirm`（`choice="save" | "discard" | "cancel"` つきで繰り返す）。
        すでに試していれば `same_point`。"""
        try:
            return self._lip_try_start(choice)
        except BaseException:
            self.end_edit(quiet=True)
            raise
        finally:
            self._notify_state()

    def _lip_try_start(self, choice: Optional[str]) -> SelectResult:
        pres = self._pres()
        ctx = pres.ctx
        if self._lip_try is not None and self.editing:
            return SelectResult(SELECT_SAME, "try")
        if self._sculpt is not None and choice != CONFIRM_CANCEL:
            self.sculpt_end()
        self.begin_edit()
        saved = False
        if ctx.has_target and pres.pose.dirty:
            if choice is None:
                return SelectResult(
                    SELECT_NEEDS_CONFIRM, "try", message="未保存のポーズ編集があります。保存 / 破棄 / 取りやめのどれかを選んでください"
                )
            if choice == CONFIRM_CANCEL:
                return SelectResult(SELECT_CANCELLED, "try", message="取りやめました")
            if choice == CONFIRM_SAVE:
                self.save_point()
                saved = True
        if ctx.has_target:
            ctx.set_selection(None)  # 編集の対象と「試す」は同時に使えない
        self._lip_try = LipTry()
        self._apply_lip_try()
        return SelectResult(SELECT_SELECTED, "try", saved=saved)

    def lip_try_set(
        self,
        phonemes: Optional[dict[str, float]] = None,
        volume: Optional[float] = None,
        emotions: Optional[dict[str, float]] = None,
    ) -> dict[str, float]:
        """「試す」の入力（音素の強さ・声量。None = 渡さない・感情の重み）を置き換えて、結果をシーンへ当てる。
        戻り = 当てた対応表のシェイプの値（`lipsync.evaluate` の結果）。試していなければ FacialSessionError。"""
        if self._lip_try is None or not self.editing:
            raise FacialSessionError("「試す」を始めていません（lip_try_start）")
        self._lip_try = LipTry(
            phonemes={str(k): float(v) for k, v in (phonemes or {}).items()},
            volume=None if volume is None else float(volume),
            emotions={str(k): float(v) for k, v in (emotions or {}).items()},
        )
        try:
            self._apply_lip_try()
        except BaseException:
            self.end_edit(quiet=True)
            raise
        self._notify_state()
        return dict(self.last_lip_try)

    def lip_try_stop(self) -> bool:
        """「試す」をやめて、シーンを編集状態が見せるもの（土台の表情だけ・ふつうは基準姿勢）へ戻す。試していたら True。"""
        if self._lip_try is None:
            return False
        self._lip_try = None
        self.last_lip_try = {}
        if self.editing and self.presenters is not None:
            self._reapply_safely()
        self._notify_state()
        return True

    def _apply_lip_try(self) -> None:
        """「試す」の結果をシーンへ当てる: 元の値（土台の表情だけ）の上に `lipsync.evaluate` の結果を載せる（自分の出力は重ねない）。"""
        ref = self._ref
        if ref is None or self._lip_try is None:
            raise FacialSessionError("編集状態に入っていません（begin_edit）")
        pres = self._pres()
        t = self._lip_try
        current = dict(self._layered_pose(SourcePose()).curves)
        out = pres.lipsync.evaluate(current, t.phonemes, t.volume, t.emotions)
        curves = dict(current)
        curves.update(out)
        for name in curves:
            ref._curve_cache.pop(name, None)
        with _no_undo():
            rep = pose_apply.apply_pose(pres.ctx.doc, SourcePose(curves=curves), ref)
        self._track_weights(ref, curves)
        self._applied = None
        self.last_apply = rep
        self.last_lip_try = dict(out)

    # ============================================================ ベイク
    def bake_all(self, progress=None) -> bake_mod.BakeReport:
        """全レイヤー・全点を焼く。"""
        return self._bake(None, None, progress)

    def bake_point(self, row: int, col: int, layer: Optional[int] = None, progress=None) -> bake_mod.BakeReport:
        """1 点だけ焼く（layer を省くとアクティブレイヤー）。"""
        if layer is None:
            ctx = self._pres().ctx
            layer = min(max(ctx.active_layer, 0), len(ctx.doc.layers) - 1)
        return self._bake(None, [(layer, row, col)], progress)

    def bake_layer(self, index: int, progress=None) -> bake_mod.BakeReport:
        """1 レイヤー分を焼く。"""
        return self._bake([index], None, progress)

    def bake_perspective_key(self, index: int, progress=None) -> bake_mod.BakeReport:
        """パース補正のキー 1 個だけ焼く（空のキーは焼かず、古いシェイプがあれば掃除する）。"""
        return self._bake(None, [], progress, persp_keys=[index])

    def _bake(self, layers, points, progress, persp_keys=None) -> bake_mod.BakeReport:
        """編集状態を抜けてから焼く（ベイクだけを Maya の Undo の 1 区切りにするため。docs/15 §4.3）。戻したあと bake_state を取り直す。

        - Neutral の点を焼くときは、感情レイヤーの同じ位置の点も一緒に焼き直す（感情は Neutral との差分で焼くため）。報告の `notes` に書く
        - プレビュー（rig）があれば、焼いたあとに作り直す（`notes` / `warnings` に書く）
        """
        doc = self.require()
        self.end_edit(sync_preview=False)
        layers, points, extra = self._expand_neutral(doc, layers, points)
        involved = self._involved_layers(doc, layers, points)
        warns = self._neutral_warnings(doc, involved)
        rebuilt: Optional[preview_rig.BuildReport] = None
        try:
            report = bake_mod.bake(doc, layers=layers, points=points, progress=progress, persp_keys=persp_keys)
        finally:
            self.refresh_scene(notify=True, call_listeners=False)
            rebuilt = self._sync_preview()
            self._changed(dirty=False)
        if extra:
            names = "・".join(dict.fromkeys(doc.layers[li].name for li, _r, _c in extra))
            report.notes.append(
                f"Neutral の点を焼き直したので、感情レイヤー（{names}）の同じ位置の点 {len(extra)} 個も焼き直しました（感情は Neutral との差分で焼くため）"
            )
        if rebuilt is not None:
            report.notes.append("プレビューを作り直しました")
        report.warnings.extend(w for w in self.preview_warnings if w not in report.warnings)
        report.warnings.extend(w for w in warns if w not in report.warnings)
        return report

    @staticmethod
    def _expand_neutral(doc: Document, layers, points):
        """Neutral の点を焼く指定なら、感情レイヤーの同じ位置の点（ポーズが空でないもの）も足す。
        戻り: (layers, points, 足した点の一覧)。足したときは points に明示し、layers は None にする。差分ベイクでないときは足さない。"""
        if (doc.bake is not None and not doc.bake.differential) or len(doc.layers) < 2:
            return layers, points, []
        base = {(li, r, c) for li, _n, r, c, _p in bake_mod._jobs(doc, layers, points)}
        extra: list[tuple[int, int, int]] = []
        for li, r, c in sorted(base):
            if li != 0:
                continue
            for lj in range(1, len(doc.layers)):
                pt = doc.layers[lj].points.get((r, c))
                if pt is not None and not pt.pose.is_empty() and (lj, r, c) not in base and (lj, r, c) not in extra:
                    extra.append((lj, r, c))
        if not extra:
            return layers, points, []
        return None, sorted(base | set(extra)), extra

    # ------------------------------------------------------------ 変更のある点だけベイク
    STALE_CODES = (
        "point_unbaked",
        "point_changed_since_bake",
        "baked_morph_missing",
        "perspective_key_unbaked",
        "perspective_key_changed",
    )

    def _stale_issues(self) -> list:
        doc = self.require()
        self.refresh_scene(notify=False)
        ctx = self.ctx
        issues = V.validate(doc, ctx.scene or V.SceneInfo(), ctx.profile, ctx.bake_state, ctx.bake_exclude)
        return [i for i in issues if i.code in self.STALE_CODES]

    def stale_points(self) -> list[tuple[int, int, int]]:
        """検証が「未ベイク」「ベイク後に変更」「ベイク済みのはずのシェイプが無い」と報告する点 (レイヤー番号, row, col)。
        シーンの情報を取り直してから調べる（Document もシーンも変えない）。パース補正のキーは `stale_perspective_keys`。"""
        out: list[tuple[int, int, int]] = []
        for i in self._stale_issues():
            if i.layer is not None and i.row is not None and i.col is not None:
                p = (i.layer, i.row, i.col)
                if p not in out:
                    out.append(p)
        return sorted(out)

    def stale_perspective_keys(self) -> list[int]:
        """未ベイク・ベイク後に変更（番号が詰まった・除外を変えた）・シェイプが消えたパース補正のキーの番号。"""
        return sorted({i.key for i in self._stale_issues() if i.key is not None})

    def bake_stale(self, progress=None) -> bake_mod.BakeReport:
        """未ベイク・ベイク後に変更・シェイプが消えた点（とパース補正のキー）だけを焼く。Neutral の点が入っていれば、感情レイヤーの同じ位置の点も
        焼き直す（`bake_point` / `bake_layer` と同じ）。対象が無ければ何も焼かず、`notes` にその旨を入れた空の報告を返す。"""
        points = self.stale_points()
        keys = self.stale_perspective_keys()
        if not points and not keys and not self._persp_orphans():
            rep = bake_mod.BakeReport()
            rep.notes.append("焼き直す点はありません（未ベイク・変更あり・シェイプが消えた点・パース補正のキーが 0 個）")
            return rep
        # 古いパース補正のシェイプ（キーを削除した・ポーズを空にした）だけが残っているときも、掃除のために走らせる
        return self._bake(None, points, progress, persp_keys=keys)

    def _persp_orphans(self) -> list[str]:
        """対応するキーが無い / 空のキーのパース補正のシェイプ（`FC_<asset>_Persp_K{n}`）。再ベイクで掃除される。"""
        doc = self.require()
        ctx = self.ctx
        issues = V.validate(doc, ctx.scene or V.SceneInfo(), ctx.profile, ctx.bake_state, ctx.bake_exclude)
        return [i.name for i in issues if i.code == "orphan_target" and "_Persp_K" in i.name]

    @staticmethod
    def _involved_layers(doc: Document, layers, points) -> set[int]:
        jobs = bake_mod._jobs(doc, layers, points)
        return {li for li, *_ in jobs}

    def _neutral_warnings(self, doc: Document, involved: set[int]) -> list[str]:
        """感情レイヤーだけを焼くと、未反映の Neutral との差分がずれる（docs/15 §4.3「注意」）。逆（Neutral を焼く）は自動で感情も焼き直す。"""
        if not involved:
            return []
        out: list[str] = []
        state = self.bake_state or {}

        def pending(layer_index: int) -> bool:
            layer = doc.layers[layer_index]
            for (r, c), pt in layer.points.items():
                if pt.pose.is_empty() or not (0 <= r < doc.grid.rows and 0 <= c < doc.grid.cols):
                    continue
                if state.get(naming.morph_name(doc.asset or "", layer.name, r, c)) != V.pose_hash(pt.pose):
                    return True
            return False

        # Neutral を焼くときの感情レイヤーの焼き直しは `_expand_neutral` が自動で行う（notes に書く）
        if 0 not in involved and pending(0):
            out.append("Neutral に未ベイク / 変更ありの点があります。感情レイヤーだけ焼くと Neutral との差分がずれるため、Neutral も焼いてください")
        return out

    # ============================================================ シェイプ作成支援（shapes.py。docs/14 §5.6）
    # 画面はここだけを呼ぶ。シーンを変える道具はどれも Maya の Undo 1 回（shapes 側の undo_chunk）。Document を変えるもの（ポーズへの記録・
    # 参照の削除・可動域）は @undoable でエディタ内 Undo に積む。終わったらシーンの情報を取り直して通知する（他のタブ・検証が新しいターゲットを見られる）。

    def shape_ctx(self, create: bool = False) -> Optional[shapes.ShapeCtx]:
        """道具が働く先（顔メッシュ・blendShape）。無ければ None（create=True なら blendShape は作り、メッシュが無ければ ShapeError）。"""
        doc = self.require()
        try:
            return shapes.make_ctx(doc, create=create)
        except shapes.ShapeError:
            if create:
                raise
            return None

    def _shape_ctx_or_raise(self) -> shapes.ShapeCtx:
        ctx = self.shape_ctx(create=True)
        assert ctx is not None
        return ctx

    def shape_list(self) -> list[shapes.ShapeInfo]:
        """顔メッシュの blendShape のターゲット一覧（タグ付き）。メッシュ・blendShape が無ければ空。"""
        ctx = self.shape_ctx(create=False)
        return shapes.list_shapes(ctx) if ctx is not None else []

    def _shape_done(self) -> None:
        self.refresh_scene(notify=True, call_listeners=False)
        self._changed(dirty=False)

    def _shape_run(self, fn, *args, **kwargs) -> shapes.ShapeResult:
        """道具を実行して、終わったらシーンの情報を取り直す。ShapeError はそのまま投げる（取り直しはする）。"""
        try:
            return fn(*args, **kwargs)
        finally:
            self._shape_done()

    # --- この角度で彫る（F2-1）---
    @property
    def sculpting(self) -> Optional["SculptState"]:
        """彫りの最中の状態（無ければ None）。"""
        return self._sculpt if self.editing else None

    def _register_weight(self, node: str, idx: int) -> None:
        """編集中に作ったターゲットの weight を、基準姿勢の記録へ足す（編集を抜けるとき 0 へ戻す）。"""
        ref = self._ref
        plug = scene_mod.weight_plug(node, idx)
        if ref is not None and plug not in ref.weight_plugs:
            ref.weight_plugs.append(plug)
            ref._saved_weights[plug] = 0.0

    def sculpt_target_name(self) -> Optional[str]:
        """選択中の点の彫り用ターゲット名 `fcs_<layer>_R{r}_C{c}`（点が選ばれていなければ None）。"""
        doc = self.require()
        ctx = self._pres().ctx
        if ctx.selection is None:
            return None
        li = min(max(ctx.active_layer, 0), len(doc.layers) - 1)
        prefix = doc.sculpt_shapes.prefix if doc.sculpt_shapes is not None else naming.DEFAULT_SCULPT_PREFIX
        return naming.sculpt_name(doc.layers[li].name, ctx.selection[0], ctx.selection[1], prefix)

    def sculpt_begin(self) -> shapes.ShapeResult:
        """選択中の点の「この角度で彫る」を始める: その点のポーズを当てた編集状態で、`fcs_<layer>_R{r}_C{c}`（重み 1）を Maya のスカルプト対象にする。

        頭が基準姿勢でないポーズでは始められない（ShapeError）。点のポーズへの記録は `sculpt_end`。"""
        doc = self.require()
        pres = self._pres()
        if self._sculpt is not None and self.editing:
            raise shapes.ShapeError("既に彫っています。「彫り終わる」を押してから、次を始めてください", "busy")
        if pres.ctx.selection is None:
            raise shapes.ShapeError("格子の点を選んでください（グリッドタブで点をクリック）", "no_point")
        sc = self._shape_ctx_or_raise()
        name = self.sculpt_target_name()
        assert name is not None
        was_editing = self.editing
        try:
            ref = self.begin_edit()
            self._apply_buffer()
            shapes.check_head_at_reference(doc, ref, pres.pose.pose_to_apply().bones)
            with shapes.undo_chunk("tdFacialSculptBegin"):
                idx, created = shapes.sculpt_prepare(sc, name)
                self._register_weight(sc.node, idx)
                curve = scene_mod.curve_name(sc.node, name)
                ref._curve_cache.pop(curve, None)
                pres.pose.set_curve(curve, 1.0)  # 編集中の値に入れておく（当て直しても重み 1 が残る）。保存は sculpt_end
                self._apply_buffer()
                tool = shapes.sculpt_enter(sc, name)
        except shapes.ShapeError:
            if not was_editing:
                self.end_edit(quiet=True)
            raise
        except BaseException:
            self.end_edit(quiet=True)
            raise
        layer_i = min(max(pres.ctx.active_layer, 0), len(doc.layers) - 1)
        self._sculpt = SculptState(name, sc.node, idx, "point", (layer_i, *pres.ctx.selection), created, sc.mesh, tool)
        self._shape_done()
        res = shapes.ShapeResult(message=f"{name} を彫れます（Maya のスカルプト / 移動ツールで形を作ってください）")
        (res.created if created else res.replaced).append(name)
        res.notes.append("終わったら「彫り終わる」を押すと、この点のポーズに記録されます")
        return res

    def sculpt_begin_combo(self, name: str) -> shapes.ShapeResult:
        """組み合わせ補正 `fcs_combo_*` を彫る: 基準姿勢で駆動元の 2 つのシェイプを 1 にして、そのターゲットをスカルプト対象にする。"""
        self.require()
        if self._sculpt is not None and self.editing:
            raise shapes.ShapeError("既に彫っています。「彫り終わる」を押してから、次を始めてください", "busy")
        sc = self._shape_ctx_or_raise()
        drivers = shapes.combo_drivers(sc, name)
        if len(drivers) < 2:
            raise shapes.ShapeError(f"{name} は組み合わせ補正ではありません", "not_combo")
        was_editing = self.editing
        try:
            ref = self.begin_edit()
            pose_apply.reset_to_reference(ref)
            table = scene_mod.target_indices(sc.node)
            for d in drivers:
                cmds.setAttr(scene_mod.weight_plug(sc.node, table[d]), 1.0)
            self._applied = None
            with shapes.undo_chunk("tdFacialSculptBegin"):
                idx, _created = shapes.sculpt_prepare(sc, name)
                tool = shapes.sculpt_enter(sc, name)
        except shapes.ShapeError:
            if not was_editing:
                self.end_edit(quiet=True)
            raise
        except BaseException:
            self.end_edit(quiet=True)
            raise
        self._sculpt = SculptState(name, sc.node, idx, "combo", None, False, sc.mesh, tool, was_editing)
        self._shape_done()
        return shapes.ShapeResult(message=f"{name} を彫れます（{' と '.join(drivers)} を 1 にしています）", replaced=[name])

    def sculpt_end(self, save: bool = True) -> shapes.ShapeResult:
        """彫りを終える: スカルプト対象を解除し、差分の無い頂点を捨てる。点の彫りなら、`fcs_*` = 1 をその点のポーズに記録して保存する
        （点がキーになる。何も彫っていなければ作ったターゲットを消して何も記録しない）。save=False は解除と整理だけ。"""
        st = self._sculpt
        if st is None:
            raise shapes.ShapeError("彫っていません", "not_sculpting")
        self._sculpt = None  # 先に外す（保存の途中で end_edit が呼ばれても二重にならないように）
        sc = self._shape_ctx_or_raise()
        res = shapes.ShapeResult()
        pres = self._pres()
        curve = scene_mod.curve_name(st.node, st.name)
        with shapes.undo_chunk("tdFacialSculptEnd"):
            shapes.sculpt_exit(sc)
            remaining = shapes.finish_sculpt(sc, st.name)
            nothing = remaining == 0 and st.created and st.kind == "point"
            if nothing:
                self._remove_target_now(sc, [st.name])
        if st.kind == "combo":
            res.message = f"{st.name} の彫りを終えました（頂点 {remaining}）"
            res.replaced.append(st.name)
            if st.was_editing and self.editing:
                self._reapply_safely()  # 駆動元を 1 にしていたのをやめ、選択中の点のポーズを当て直す
            elif self.editing:
                self.end_edit(quiet=True)
        elif nothing:
            if pres.ctx.selection is not None:
                pres.pose.remove_curve(curve)
            res.message = "何も彫らなかったので、作った彫り用シェイプを消しました（ポーズは変えていません）"
            res.removed.append(st.name)
        else:
            res.message = f"{st.name} の彫りを終えました（頂点 {remaining}）"
            res.replaced.append(st.name)
            same_point = st.layer_row_col is not None and pres.ctx.selection == st.layer_row_col[1:]
            if save and same_point:
                pres.pose.set_curve(curve, 1.0)
                self.save_point()
                res.notes.append(f"点のポーズに「{curve} = 1」を記録して保存しました。ベイクすると FC_* に含まれます")
            elif not save:
                res.notes.append("ポーズへは記録していません（編集状態を抜けたため）。もう一度「この角度で彫る」を押すと記録できます")
        res.stats = {"vertices": remaining}
        self._shape_done()
        return res

    def _remove_target_now(self, sc: shapes.ShapeCtx, names: Sequence[str]) -> list[str]:
        """fcs_* / FC_* のターゲットをシーンから消し、基準姿勢の記録・作った印・bake_state からも外す。"""
        table = scene_mod.target_indices(sc.node)
        plugs = [scene_mod.weight_plug(sc.node, table[n]) for n in names if n in table]
        gone = shapes.delete_shapes(sc, names)
        self._forget_plugs(plugs)
        state = scene_mod.get_bake_state(sc.node)
        if any(n in state for n in gone):
            for n in gone:
                state.pop(n, None)
            scene_mod.set_bake_state(sc.node, state)
        return gone

    @undoable(notify=True, reapply=False)
    def _strip_pose_refs(self, names: Iterable[str]) -> CommandResult:
        """ポーズ・作業セットから、これらのシェイプへの参照（`bs.name` / `name`）を取り除く。"""
        doc = self.require()
        keys = set(names)

        def hit(k: str) -> bool:
            return k in keys or ("." in k and k.split(".", 1)[1] in keys)

        n = 0
        for layer in doc.layers:
            for pt in layer.points.values():
                for k in [k for k in pt.pose.curves if hit(k)]:
                    del pt.pose.curves[k]
                    n += 1
        doc.working_set.curves = [k for k in doc.working_set.curves if not hit(k)]
        return CommandResult(message=f"ポーズから参照を {n} 個取り除きました")

    def sculpt_delete(self, names: Iterable[str]) -> shapes.ShapeResult:
        """彫り用シェイプ（`fcs_*`・組み合わせ補正を含む）を消し、ポーズ・作業セットからの参照も取り除く。シーン側は Maya の Undo 1 回、
        ポーズ側はエディタ内 Undo で戻せる。`fcs_*` 以外が入っていたら何も消さずに ShapeError。"""
        names = list(dict.fromkeys(names))
        sc = self._shape_ctx_or_raise()
        bad = [n for n in names if not naming.is_sculpt_name(n, sc.prefix)]
        if bad:
            raise shapes.ShapeError(f"{sc.prefix}* 以外は消せません: {bad}", "not_sculpt")
        if self._sculpt is not None and self._sculpt.name in names:
            self.sculpt_end(save=False)
        res = shapes.ShapeResult()
        table = scene_mod.target_indices(sc.node)
        try:
            with shapes.undo_chunk("tdFacialSculptDelete"):  # 重みを 0 にする + ターゲットを消す + 当て直し = Maya の Undo 1 回（検証は上で済んでいる）
                for n in names:  # 編集中に重み 1 のまま消すと、Maya の Undo で戻したとき重み 1 が残る。先に 0 にしておく（同じ区切りの中）
                    plug = scene_mod.weight_plug(sc.node, table[n]) if n in table else None
                    if plug and not cmds.listConnections(plug, source=True, destination=False):
                        cmds.setAttr(plug, 0.0)
                res.removed = self._remove_target_now(sc, names)
                if self.editing and self._pres().ctx.selection is not None:
                    for n in names:
                        self.pose.remove_curve(scene_mod.curve_name(sc.node, n))
                self._strip_pose_refs(names)
                self._reapply_safely()
        finally:
            self._shape_done()
        res.message = f"彫り用シェイプ {len(res.removed)} 個を消しました（ポーズからの参照も外しました）"
        return res

    # --- ポーズをシェイプにする（F2-3a）---
    def shape_from_pose(self, name: str, add_to_working_set: bool = False, capture_scene: bool = False) -> shapes.ShapeResult:
        """編集中のポーズ（選択中の点の編集中の値）を、新しいターゲット 1 本にする（`bake.pose_to_shape`。元のターゲットは上書きしない）。
        capture_scene=True なら、先にシーンの今の状態（Shape Editor・ジョイントの動き）を編集中の値へ取り込む（編集状態のとき）。"""
        doc = self.require()
        pres = self._pres()
        shapes.check_new_name(name)
        if capture_scene:
            if not self.editing:
                raise shapes.ShapeError("シーンから取り込むには編集状態（ヘッダーの「編集」）に入ってください", "not_editing")
            rep = self.capture_from_scene()
            if not rep.ok:
                raise shapes.ShapeError(rep.message or "シーンから取り込めませんでした", rep.code)
        pose = pres.pose.pose_to_apply()
        if pose.is_empty():
            raise shapes.ShapeError("ポーズが空です。格子の点を選んでスライダー・ボーンを動かすか、「シーンの今の状態を取り込む」にしてください", "empty_pose")
        sc = self._shape_ctx_or_raise()
        try:
            with shapes.undo_chunk("tdFacialPoseToShape"):  # 作る + 「作ったもの」の印 = Undo 1 回
                brep = bake_mod.pose_to_shape(doc, pose, name)
                shapes.add_made(sc, [name])
        except bake_mod.BakeError as e:
            raise shapes.ShapeError(str(e), "bake") from e
        finally:
            self._shape_done()
        res = shapes.ShapeResult(
            message=f"ポーズを新しいシェイプ {name} にしました（頂点 {brep.vertex_counts.get(name, 0)}）",
            created=list(brep.created),
            replaced=list(brep.replaced),
            warnings=list(brep.warnings),
        )
        res.stats = {"vertices": brep.vertex_counts.get(name, 0), "culled": brep.culled_vertices}
        if brep.empty:
            res.warnings.append("差分が 1 つも残りませんでした（空のシェイプ）")
        if add_to_working_set:
            self.add_to_working_set(curves=[scene_mod.curve_name(sc.node, name)])
        self._shape_done()
        return res

    # --- 左右・ミラー・中間・誇張・組み合わせ（F2-2 / F2-3）---
    def shape_split_lr(
        self, src: str, name_l: str, name_r: str, width: float = 1.0, centre: float = 0.0, overwrite_originals: bool = False
    ) -> shapes.ShapeResult:
        return self._shape_run(shapes.split_lr, self._shape_ctx_or_raise(), src, name_l, name_r, width, centre, overwrite_originals)

    def shape_mirror(
        self,
        src: str,
        dst: Optional[str] = None,
        tolerance: float = 0.05,
        allow_unmatched: bool = False,
        overwrite_originals: bool = False,
        centre: float = 0.0,
    ) -> shapes.ShapeResult:
        """対応が取れない頂点があると、その頂点をビューポートで選択したうえで `shapes.AsymmetryStop` を投げる（allow_unmatched=True で続ける）。"""
        return self._shape_run(shapes.mirror_shape, self._shape_ctx_or_raise(), src, dst, centre, tolerance, allow_unmatched, overwrite_originals)

    def shape_inbetween(self, name: str, weight: float, confirm_original: bool = False) -> shapes.ShapeResult:
        return self._shape_run(shapes.add_inbetween, self._shape_ctx_or_raise(), name, weight, confirm_original)

    @undoable(notify=True, reapply=False)
    def set_limit(self, curve: str, lo: float, hi: float) -> CommandResult:
        """シェイプの可動域（`limits`）を 1 件置く。誇張（R-37）の準備に使う。"""
        doc = self.require()
        limits = dict(doc.limits or {})
        limits[curve] = (float(lo), float(hi))
        doc.limits = limits
        return CommandResult(message=f"{curve} の可動域を {lo:g}〜{hi:g} にしました")

    def shape_exaggerate(self, name: str) -> shapes.ShapeResult:
        """誇張形 `<name>_Ex` を作り、`name` の可動域を 0〜2 にする（R-37。ポーズの 1 超の分はベイクで FC_…_Ex に焼き分ける）。"""
        sc = self._shape_ctx_or_raise()
        res = self._shape_run(shapes.make_exaggeration, sc, name)
        self.set_limit(scene_mod.curve_name(sc.node, name), 0.0, 2.0)
        return res

    def shape_combo(self, a: str, b: str) -> shapes.ShapeResult:
        return self._shape_run(shapes.create_combo, self._shape_ctx_or_raise(), a, b)

    # --- 別メッシュへ写す（F2-4）---
    def shape_transfer_plan(self, names: Sequence[str], dest_mesh: str) -> shapes.TransferPlan:
        return shapes.plan_transfer(self._shape_ctx_or_raise(), names, dest_mesh)

    def shape_transfer(self, names: Sequence[str], dest_mesh: str, overwrite: Iterable[str] = ()) -> shapes.ShapeResult:
        return self._shape_run(shapes.transfer_shapes, self._shape_ctx_or_raise(), names, dest_mesh, overwrite)

    # --- 整理（F2-5）---
    def shape_clean(self, names: Sequence[str], threshold: float, confirm_original: bool = False) -> shapes.ShapeResult:
        return self._shape_run(shapes.clean_micro, self._shape_ctx_or_raise(), names, threshold, confirm_original)

    def shape_audit(self) -> shapes.Audit:
        sc = self.shape_ctx(create=False)
        return shapes.audit(sc, self.require()) if sc is not None else shapes.Audit()

    def shape_missing_standard(self) -> list[str]:
        sc = self.shape_ctx(create=False)
        return shapes.missing_standard(sc, self.profile) if sc is not None else []

    def shape_delete(self, names: Iterable[str]) -> shapes.ShapeResult:
        """`FC_*` / `fcs_*` のターゲットを消す（それ以外が入っていたら何も消さずに ShapeError）。`fcs_*` はポーズからの参照も外す。"""
        names = list(dict.fromkeys(names))
        sc = self._shape_ctx_or_raise()
        bad = [n for n in names if not (naming.is_fc_name(n) or naming.is_sculpt_name(n, sc.prefix))]
        if bad:
            raise shapes.ShapeError(f"FC_* / {sc.prefix}* 以外は消せません: {bad}", "protected")
        sculpt = [n for n in names if naming.is_sculpt_name(n, sc.prefix)]
        fcs = [n for n in names if naming.is_fc_name(n)]
        res = shapes.ShapeResult()
        if sculpt:
            res.removed.extend(self.sculpt_delete(sculpt).removed)
        if fcs:
            try:
                with shapes.undo_chunk("tdFacialDeleteShapes"):
                    res.removed.extend(self._remove_target_now(sc, fcs))
            finally:
                self._shape_done()
        res.message = f"{len(res.removed)} 個のシェイプを消しました"
        return res

    # ============================================================ 検証
    def validate(self):
        """シーンの情報を取り直してから検証する（`ValidationPresenter.run`）。Issue の一覧を返す。"""
        self.refresh_scene(notify=True)
        return self.validation.run()

    @undoable(refresh=True)
    def apply_renames(self, choices=None) -> RepairReport:
        """選んだ新名で一括改名する（`ValidationPresenter.apply_renames`）。"""
        return self.validation.apply_renames(choices)

    @undoable(refresh=True)
    def remove_missing(self, include_case_mismatch: bool = False) -> RepairReport:
        """モデルに無いシェイプ・ボーンへの参照を消す。"""
        return self.validation.remove_missing(include_case_mismatch)

    # ============================================================ プレビュー（カメラ連動の補正。preview_rig）
    # 編集状態とは共存する: 編集中はシェイプの重みが基準姿勢のために切り離されて補正は止まり、編集を終えると再開する。
    # 作る・消す・キーに焼くは、編集中なら一度編集状態を抜けてから行い、終わったら入り直して編集中の値を当て直す。

    def _preview_rig_or_raise(self) -> str:
        rig = self.preview_rig_node()
        if rig is None:
            raise FacialSessionError("プレビューがありません（先に「プレビューを作る」を押してください）")
        return rig

    def preview_rig_node(self) -> Optional[str]:
        """プレビュー用ノード（rig の transform）。無ければ None。"""
        if self.presenters is None:
            return None
        return preview_rig.find_rig(self.doc.asset or "")

    def preview_exists(self) -> bool:
        return self.preview_rig_node() is not None

    def preview_state(self) -> str:
        """"none"（無い）/ "live"（式が生きていて最新）/ "stale"（格子・ベイクなどが変わって作り直しが要る）/ "keys"（キーに焼いて式を外した）。"""
        if self.presenters is None or not self.preview_exists():
            return "none"
        asset = self.doc.asset or ""
        if not preview_rig.has_expression(asset):
            return "keys"
        return "stale" if preview_rig.is_stale(self.doc) else "live"

    def preview_is_stale(self) -> bool:
        """作ったあとで格子・レイヤー・ベイク（FC_*）・基準ボーン・表情での弱めの設定が変わった（= 作り直しが要る）か。
        プレビューが無ければ False。キーに焼いて式を外したあとは True（`preview_state` は "keys"）。"""
        return self.preview_exists() and preview_rig.is_stale(self.doc)

    @contextlib.contextmanager
    def _edit_suspended(self):
        """編集状態にいれば一度抜け、終わったら入り直して編集中の値を当て直す（基準姿勢のままでは出来ない処理のため）。"""
        was = self.editing
        base = self._base if was else None  # 土台の表情は抜けると外れるので、入り直すときに戻す
        if was:
            self.end_edit(sync_preview=False)
        try:
            yield
        finally:
            if was and self.presenters is not None:
                try:
                    self.begin_edit()
                    self._base = base
                    self._apply_buffer()
                except Exception:  # noqa: BLE001
                    self.end_edit(quiet=True)
                    lifecycle.report_error("編集状態へ戻れませんでした", traceback.format_exc(), once=False)

    def _after_preview_change(self, report: Optional[preview_rig.BuildReport] = None) -> None:
        if report is not None:
            self.last_preview_report = report
            self.preview_warnings = list(report.warnings)
        self._preview_pending = False
        self._notify_state()

    def preview_build(self, camera: Optional[str] = None) -> preview_rig.BuildReport:
        """プレビューを作る（あれば作り直す。rig のキー・値は残す）。camera を省くと、既にあればつないでいるカメラのまま、
        無ければ今のビューのカメラ。FC_*（ベイク）が 1 本も無いときも作れる（配線するターゲットが 0 本）。警告は `BuildReport.warnings`
        （他から接続されていて配線しなかった weight など。`preview_warnings` にも入る）。"""
        doc = self.require()
        asset = doc.asset or ""
        if camera is None and preview_rig.exists(asset):
            try:
                camera = preview_rig.get_camera(asset)
            except preview_rig.PreviewRigError:
                camera = None
        try:
            with self._edit_suspended():
                rep = preview_rig.build_ex(doc, camera)
        except preview_rig.PreviewRigError as exc:
            raise FacialSessionError(str(exc)) from exc
        self._after_preview_change(rep)
        return rep

    def preview_delete(self) -> bool:
        """プレビュー用ノードを消す（駆動していたシェイプの重みは 0 に戻る）。無ければ False。"""
        doc = self.require()
        with self._edit_suspended():
            done = preview_rig.delete(doc.asset or "")
        self.last_preview_report = None
        self.preview_warnings = []
        self._after_preview_change()
        return done

    def preview_set_enabled(self, on: bool) -> None:
        """補正あり / なし（A/B 比較）。キーが打ってあれば、そのキーが優先される。"""
        self._preview_rig_or_raise()
        preview_rig.set_enabled(self.doc.asset or "", bool(on))
        self._notify_state()

    def preview_is_enabled(self) -> bool:
        self._preview_rig_or_raise()
        return preview_rig.is_enabled(self.doc.asset or "")

    def preview_set_camera(self, camera: Optional[str] = None) -> str:
        """プレビューのカメラを入れ替える（式は作り直さない）。camera を省くと今のビューのカメラ。つないだカメラの transform を返す。"""
        self._preview_rig_or_raise()
        try:
            cam = preview_rig.set_camera(self.doc.asset or "", camera)
        except preview_rig.PreviewRigError as exc:
            raise FacialSessionError(str(exc)) from exc
        self._notify_state()
        return cam

    def preview_camera(self) -> Optional[str]:
        """今つながっているカメラの transform（長い名前）。プレビューが無ければ None。"""
        if not self.preview_exists():
            return None
        return preview_rig.get_camera(self.doc.asset or "")

    def preview_set_attr(self, name: str, value) -> float:
        """rig のアトリビュート（alpha / useManual / manualYaw / manualPitch / exaggeration / emotion_<Layer>）を設定する。
        キーが打ってある・他から駆動されているアトリビュートは変えられない（FacialSessionError）。設定した値を返す。"""
        rig = self._preview_rig_or_raise()
        allowed = {*preview_rig.KEYABLE_FIXED, preview_rig.EXAGGERATION_ATTR, preview_rig.PERSPECTIVE_ATTR, *preview_rig.emotion_attrs(self.doc).values()}
        if name not in allowed:
            raise FacialSessionError(f"プレビューのアトリビュート「{name}」は変えられません")
        plug = f"{rig}.{name}"
        if (cmds.keyframe(plug, query=True, keyframeCount=True) or 0) > 0 or cmds.listConnections(plug, source=True, destination=False):
            raise FacialSessionError(f"{name} にはキーが打ってあるため値を変えられません（Maya のチャンネルボックスで変えてください）")
        if name == "useManual":
            cmds.setAttr(plug, bool(value))
            result = float(bool(value))
        else:
            cmds.setAttr(plug, float(value))
            result = float(cmds.getAttr(plug))  # 範囲（0〜1）に丸まった値
        self._notify_state()
        return result

    def preview_status(self) -> PreviewStatus:
        """プレビューの今の状態（rig の値・キーの有無・カメラ・編集中か）。"""
        st = PreviewStatus(state=self.preview_state(), editing=self.editing, warnings=list(self.preview_warnings))
        if st.state == "none":
            return st
        doc = self.doc
        rig = self._preview_rig_or_raise()
        st.rig = rig

        def info(attr: str) -> tuple[float, bool]:
            plug = f"{rig}.{attr}"
            keyed = (cmds.keyframe(plug, query=True, keyframeCount=True) or 0) > 0 or bool(
                cmds.listConnections(plug, source=True, destination=False)
            )
            return float(cmds.getAttr(plug)), keyed

        st.enabled = bool(cmds.getAttr(f"{rig}.enable"))
        st.alpha, st.alpha_keyed = info("alpha")
        st.use_manual = bool(cmds.getAttr(f"{rig}.useManual"))
        st.manual_yaw, ky = info("manualYaw")
        st.manual_pitch, kp = info("manualPitch")
        st.manual_keyed = ky or kp
        st.out_yaw = float(cmds.getAttr(f"{rig}.outYaw"))
        st.out_pitch = float(cmds.getAttr(f"{rig}.outPitch"))
        if cmds.attributeQuery(preview_rig.EXAGGERATION_ATTR, node=rig, exists=True):
            st.exaggeration, st.exaggeration_keyed = info(preview_rig.EXAGGERATION_ATTR)
        st.has_extreme = preview_rig.has_extreme_targets(doc.asset or "")
        if cmds.attributeQuery(preview_rig.PERSPECTIVE_ATTR, node=rig, exists=True):
            st.perspective, st.perspective_keyed = info(preview_rig.PERSPECTIVE_ATTR)
        st.has_perspective = preview_rig.has_perspective_targets(doc.asset or "")
        st.perspective_axis = doc.perspective.axis if doc.perspective is not None else "distance"
        if st.has_perspective and cmds.attributeQuery(preview_rig.OUT_PERSPECTIVE_ATTR, node=rig, exists=True):
            st.perspective_value = float(cmds.getAttr(f"{rig}.{preview_rig.OUT_PERSPECTIVE_ATTR}"))
        st.distance_layers = [doc.layers[i].name for i in preview_rig.distance_layers(doc)]
        if st.distance_layers and cmds.attributeQuery(preview_rig.OUT_DISTANCE_ATTR, node=rig, exists=True):
            st.out_distance = float(cmds.getAttr(f"{rig}.{preview_rig.OUT_DISTANCE_ATTR}"))
        for li, attr in preview_rig.emotion_attrs(doc).items():
            v, keyed = info(attr)
            st.emotions.append((doc.layers[li].name, attr, v, keyed))
        try:
            cam = preview_rig.get_camera(doc.asset or "")
        except preview_rig.PreviewRigError:
            cam = None
        st.camera = scene_mod.short_name(cam) if cam else ""
        st.material_link = preview_rig.material_link_message(doc.asset or "") if preview_rig.material_link_enabled(doc) else ""
        return st

    def preview_weights(self) -> dict[str, float]:
        """rig が今駆動している FC_* の重み（ターゲット名 → 値）。編集中は 0。"""
        self._preview_rig_or_raise()
        return preview_rig.current_weights(self.doc.asset or "")

    def preview_bake_to_keys(self, start: float, end: float, step: float = 1, remove_rig: bool = False) -> dict:
        """時間範囲を step フレームごとに評価して FC_* の weight にキーを打ち、プレビューの式を外す（`preview_rig.bake_to_keys`）。
        既にキーがあるシェイプはキーが重なる。先に `preview_clear_keys` で消すと確実。戻り: bake_to_keys の辞書。"""
        doc = self.require()
        self._preview_rig_or_raise()
        try:
            with self._edit_suspended():
                res = preview_rig.bake_to_keys(doc, start, end, step, remove_rig)
        except preview_rig.PreviewRigError as exc:
            raise FacialSessionError(str(exc)) from exc
        self._after_preview_change()
        return res

    def preview_clear_keys(self) -> int:
        """対象メッシュの FC_* の weight に打たれたキー（animCurve）を消して、weight を 0 に戻す。FC_* 以外には触らない。
        消したアニメーションカーブの数を返す。キーが残っていると、プレビューは「他から接続されている」weight を配線しない。"""
        doc = self.require()
        prefix = doc.sculpt_shapes.prefix if doc.sculpt_shapes is not None else naming.DEFAULT_SCULPT_PREFIX
        with self._edit_suspended():
            curves: list[str] = []
            plugs: list[str] = []
            for m in self.target_meshes():
                for ref in scene_mod.fc_targets(m, prefix):
                    srcs = cmds.listConnections(ref.plug, source=True, destination=False, type="animCurve") or []
                    for c in srcs:
                        if c not in curves:
                            curves.append(c)
                    if srcs:
                        plugs.append(ref.plug)
            if curves:  # 空のリストを cmds.delete に渡さない
                cmds.delete(curves)
            for p in plugs:
                try:
                    cmds.setAttr(p, 0.0)
                except RuntimeError:
                    pass
        self._notify_state()
        return len(curves)

    def _sync_preview(self) -> Optional[preview_rig.BuildReport]:
        """プレビューがあって作り直しが要る（格子・ベイク・レイヤーが変わった）なら、キーを残したまま作り直す。作り直したら BuildReport。
        編集中は保留して（`_preview_pending`）、編集を終えたときに作り直す。キーに焼いて式が無いときは何もしない（式を勝手に戻さない）。
        失敗は例外にせず `preview_warnings` に入れる（ベイク・編集の結果を巻き込まない）。"""
        if self.presenters is None:
            return None
        doc = self.doc
        asset = doc.asset or ""
        try:
            if not asset or not preview_rig.has_expression(asset):
                self._preview_pending = False
                return None
            stale = preview_rig.is_stale(doc)
            if self.editing:
                self._preview_pending = stale
                return None
            self._preview_pending = False
            if not stale:
                return None
            rep = preview_rig.build_ex(doc, preview_rig.get_camera(asset))
        except Exception as exc:  # noqa: BLE001
            self.preview_warnings = [f"プレビューを作り直せませんでした: {exc}"]
            return None
        self.last_preview_report = rep
        self.preview_warnings = list(rep.warnings)
        return rep

    @staticmethod
    def model_cameras() -> list[str]:
        """モデルパネルが今使っているカメラの transform（短い名前。重複なし）。パネルが無ければ persp。"""
        out: list[str] = []
        try:
            for panel in cmds.getPanel(type="modelPanel") or []:
                cam = cmds.modelPanel(panel, query=True, camera=True)
                if not cam:
                    continue
                found = cmds.ls(cam, long=True) or []
                if not found:
                    continue
                node = found[0]
                if cmds.nodeType(node) == "camera":
                    node = (cmds.listRelatives(node, parent=True, fullPath=True) or [node])[0]
                short = scene_mod.short_name(node)
                if short not in out:
                    out.append(short)
        except RuntimeError:
            pass
        if not out and cmds.objExists("persp"):
            out.append("persp")
        return out

    # ============================================================ T-20（Toon のカメラ角度補正）の取り込み（F1-9）
    def import_t20(self, look_or_path=None, overwrite: bool = False) -> T20ImportResult:
        """Toon の Look の `characterSettings.viewCorrection`（T-20）の補正シェイプ（正面 / 3/4 / 横）を、Neutral レイヤーの
        Pitch 0 の行・Yaw 0 / 45 / 90° の列のキー（ポーズ = そのシェイプ 1.0）にする。

        - look_or_path: Look の dict / look.json のパス / 省略（今開いている Toon の Look。無ければ `NoLookError`）
        - T-20 の補正シェイプは blendShape `tdViewCorrection_<メッシュ>` のターゲット。シーンに無いものは飛ばして理由を返す
        - 格子に Yaw 0 / 45 / 90° の列が無ければ（±10° 以内に無い）飛ばす。Yaw 範囲 90°・列数 5 の格子なら全部置ける
        - 既にそこにキーがある点は、ポーズが違えば上書きしない（`existing`。overwrite=True で上書き）
        - 使ったシェイプは作業セットに足す。**T-20 のデータと Look は変更しない**（読むだけ）
        - 左側（Yaw −）は「自動生成」で埋める。FacialController のプレビューを使うあいだは、Toon の viewCorrection のプレビューを
          オフにする（二重に補正される。Toon の設定は自動では変えない）
        """
        vc = self._t20_settings(look_or_path)
        return self._import_t20(vc, bool(overwrite))

    @staticmethod
    def _t20_settings(look_or_path) -> dict[str, Any]:
        look: Optional[dict] = None
        if look_or_path is None:
            try:
                from tdrive_toon import session as toon_session  # 読むだけ

                look = toon_session.current().look
            except Exception:  # noqa: BLE001  Toon が読めない・Look が開かれていない
                look = None
            if look is None:
                raise NoLookError("開いている Toon の Look がありません。look.json を選んでください")
        elif isinstance(look_or_path, dict):
            look = look_or_path
        else:
            p = Path(look_or_path)
            try:
                look = json.loads(p.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError) as exc:
                raise FacialSessionError(f"Look を読めません: {p}（{exc}）") from exc
        cs = look.get("characterSettings") if isinstance(look, dict) else None
        vc = cs.get("viewCorrection") if isinstance(cs, dict) else None
        return dict(vc) if isinstance(vc, dict) else {}

    @undoable(notify=True)
    def _import_t20(self, vc: dict[str, Any], overwrite: bool) -> T20ImportResult:
        doc = self.require()
        mesh = str(vc.get("mesh") or "").strip()
        names = {k: str(vc.get(k) or "").strip() for k, _ in T20_ANGLES}
        if not mesh or not any(names.values()):
            return T20ImportResult(
                ok=False,
                code="no_t20",
                message="この Look には、カメラ角度補正（viewCorrection）の設定がありません（Toon のキャラクタータブで補正シェイプを登録してください）",
            )
        short = mesh.split("|")[-1]
        node = f"{T20_NODE_PREFIX}{short}"
        res = T20ImportResult()
        g = doc.grid
        col_yaws = [self.grid.angles_of(0, c)[0] for c in range(g.cols)]
        row_pitch = [self.grid.angles_of(r, 0)[1] for r in range(g.rows)]
        row = min(range(g.rows), key=lambda r: abs(row_pitch[r]))
        if doc.target is not None and doc.target.mesh and doc.target.mesh.split("|")[-1] != short:
            res.warnings.append(f"T-20 のメッシュ「{short}」と、このデータの対象メッシュ「{doc.target.mesh}」が違います")
        neutral = doc.layers[0]
        used_cols: set[int] = set()
        new_curves: list[str] = []
        for key, angle in T20_ANGLES:
            label = T20_LABELS[key]
            target = names[key]
            if not target:
                res.skipped.append(f"{label}: Look に補正シェイプが登録されていません")
                continue
            curve = f"{node}.{target}"
            if scene_mod.resolve_curve(curve, short) is None:
                res.skipped.append(f"{label}: シーンに {curve} がありません")
                continue
            col = min(range(g.cols), key=lambda c: abs(col_yaws[c] - angle))
            if abs(col_yaws[col] - angle) > T20_SNAP_TOLERANCE:
                res.skipped.append(f"{label}（Yaw {angle:g}°）: 格子にその角度の列がありません")
                continue
            if col in used_cols:
                res.skipped.append(f"{label}: Yaw {col_yaws[col]:g}° の列を別の補正シェイプが使っています")
                continue
            used_cols.add(col)
            pose = SourcePose({curve: 1.0})
            old = neutral.points.get((row, col))
            if old is not None and old.is_key and not overwrite and V.pose_hash(old.pose) != V.pose_hash(pose):
                res.existing.append(f"R{row} C{col}（{label}の位置。Yaw {col_yaws[col]:g}°）")
                continue
            neutral.points[(row, col)] = GridPoint(row, col, True, pose)
            res.imported.append(
                {"key": key, "row": row, "col": col, "yaw": col_yaws[col], "pitch": row_pitch[row], "curve": curve, "snapped": abs(col_yaws[col] - angle) > 0.5}
            )
            new_curves.append(curve)
        parts: list[str] = []
        if res.imported:
            doc.working_set.curves = list(dict.fromkeys([*doc.working_set.curves, *new_curves]))
            where = "、".join(f"{T20_LABELS[d['key']]} → R{d['row']} C{d['col']}（Yaw {d['yaw']:g}°）" for d in res.imported)
            parts.append(f"T-20 の補正シェイプ {len(res.imported)} 個を Neutral のキーにしました: {where}")
        else:
            res.ok = False
            res.code = "nothing"
            parts.append("取り込める補正シェイプがありませんでした")
        if res.skipped:
            parts.append("取り込まなかったもの: " + " / ".join(res.skipped))
            if any("その角度の列がありません" in s for s in res.skipped):
                parts.append("格子を Yaw 範囲 90°・列数 5 にすると、Yaw 0 / 45 / 90° の列ができます")
        if res.existing:
            parts.append("既にキーがあるので上書きしませんでした: " + "、".join(res.existing))
        parts.extend(res.warnings)
        if res.imported:
            parts.append("左側（Yaw −）は「自動生成」で埋めてください")
            parts.append("FacialController のプレビューを使うあいだは、Toon の「カメラ角度補正（viewCorrection）」のプレビューをオフにしてください（二重に補正されます。Toon の設定は変えていません）")
        res.message = "。".join(parts)
        return res

    # ============================================================ カメラ（グリッドタブ用。Qt なし）
    @staticmethod
    def camera_transform(camera: Optional[str] = None) -> str:
        """カメラの transform（長い名前）。省くと今のビュー（フォーカス中の、無ければ最初のモデルパネル。無ければ persp）のカメラ。"""
        if camera is None:
            camera = "persp"
            try:
                panel = cmds.getPanel(withFocus=True)
                if not panel or cmds.getPanel(typeOf=panel) != "modelPanel":
                    panels = cmds.getPanel(type="modelPanel") or []
                    panel = panels[0] if panels else None
                if panel:
                    camera = cmds.modelPanel(panel, query=True, camera=True) or camera
            except RuntimeError:
                pass
        found = cmds.ls(camera, long=True) or []
        if not found:
            raise FacialSessionError(f"カメラ {camera} がシーンにありません")
        node = found[0]
        if cmds.nodeType(node) == "camera":
            node = (cmds.listRelatives(node, parent=True, fullPath=True) or [node])[0]
        return node

    @staticmethod
    def _dag(node: str) -> om.MDagPath:
        sel = om.MSelectionList()
        sel.add(node)
        return sel.getDagPath(0)

    def _head_frame(self, joint: Optional[str] = None) -> tuple[tuple[float, float, float], tuple[float, float, float, float]]:
        """基準ボーンのワールドの位置（cm）と向き（クォータニオン [x, y, z, w]）。joint を渡すと（あれば）その長い名前のジョイントを使う（毎回探さない）。"""
        doc = self.require()
        if joint is None or not cmds.objExists(joint):
            meshes = self._resolve_meshes(doc)
            joint = scene_mod.find_joint(doc.grid.base_bone, scene_mod.mesh_joints(meshes)) if doc.grid.base_bone else None
        if joint is None:
            raise FacialSessionError(f"基準ボーン「{doc.grid.base_bone}」がシーンにありません")
        tm = om.MTransformationMatrix(self._dag(joint).inclusiveMatrix())
        t = tm.translation(om.MSpace.kWorld)
        q = tm.rotation(asQuaternion=True)
        self._head_joint = joint
        return (t.x, t.y, t.z), space.quat_normalize((q.x, q.y, q.z, q.w))

    @staticmethod
    def _axis_vector(axis: str) -> tuple[float, float, float]:
        v = [0.0, 0.0, 0.0]
        v["XYZ".index(axis[1])] = 1.0 if axis[0] == "+" else -1.0
        return (v[0], v[1], v[2])

    def _grid_center(self, joint: Optional[str] = None) -> tuple[tuple[float, float, float], tuple[float, float, float, float]]:
        """格子の中心のワールド位置（基準ボーン + 回した centerOffset）と基準ボーンの向き。"""
        doc = self.require()
        pos, q = self._head_frame(joint)
        off = space.rotate_vector(q, doc.grid.center_offset)
        return (pos[0] + off[0], pos[1] + off[1], pos[2] + off[2]), q

    def grid_basis(self, joint: Optional[str] = None) -> tuple[tuple[float, float, float], float, str]:
        """格子の中心（ワールド・cm）、キャラクターの前方の向き（正準空間の水平 Yaw・度）、使った基準ボーン。
        `camera_to_angles` と、シーンに出す格子（`scene_grid`）が同じ値を使う。"""
        doc = self.require()
        center, q = self._grid_center(joint)
        to_canon = space.converter(space.MAYA, space.CANONICAL)
        fwd = to_canon.direction(space.rotate_vector(q, self._axis_vector(doc.grid.forward_axis)))
        return center, math.degrees(math.atan2(fwd[1], fwd[0])), self._head_joint

    @staticmethod
    def grid_direction(yaw: float, pitch: float, forward_yaw: float) -> tuple[float, float, float]:
        """格子の中心から (Yaw, Pitch) の位置（カメラが置かれる側）への、Maya のワールドの単位ベクトル。"""
        return space.converter(space.CANONICAL, space.MAYA).direction(evaluate.compute_view_direction(forward_yaw, yaw, pitch))

    def view_angles(self, camera: Optional[str] = None) -> tuple[float, float]:
        """カメラの今の (Yaw, Pitch)[度]。基準ボーン（centerOffset・forwardAxis を反映）から見たもの。
        Maya の系 → 正準空間（core.space）→ core.evaluate.compute_view_angles。Yaw 正 = カメラがキャラクターの左側。"""
        doc = self.require()
        pose_apply.assert_maya_space(doc)
        pos, q = self._head_frame()
        cam = self.camera_transform(camera)
        t = om.MTransformationMatrix(self._dag(cam).inclusiveMatrix()).translation(om.MSpace.kWorld)
        return space.compute_view_angles_in_space(
            space.MAYA, pos, q, doc.grid.forward_axis, (t.x, t.y, t.z), doc.grid.center_offset
        )

    def camera_to_point(
        self, row: int, col: int, camera: Optional[str] = None, distance: Optional[float] = None
    ) -> tuple[float, float]:
        """カメラを、その点の角度から基準ボーンの中心（centerOffset 込み）を見る位置へ動かす。
        distance（cm）を省くと今の中心までの距離を保つ。`view_angles` の逆（戻したあと view_angles は点の角度を返す）。
        カメラの transform の translate / rotate を書く（Maya の Undo に積まれる）。点の (Yaw, Pitch) を返す。"""
        doc = self.require()
        pose_apply.assert_maya_space(doc)
        if not self.grid.point_at(row, col):
            raise FacialSessionError(f"点 (R{row}, C{col}) は格子の外です")
        yaw, pitch = self.grid.angles_of(row, col)
        self.camera_to_angles(yaw, pitch, camera, distance)
        return yaw, pitch

    def camera_to_angles(self, yaw: float, pitch: float, camera: Optional[str] = None, distance: Optional[float] = None) -> None:
        """カメラを、基準ボーンの中心（centerOffset 込み）から (Yaw, Pitch) の方向・distance（cm。省くと今の距離）の位置へ動かして中心を向ける。"""
        doc = self.require()
        pose_apply.assert_maya_space(doc)
        center, forward_yaw, _joint = self.grid_basis()
        cam = self.camera_transform(camera)
        cam_dag = self._dag(cam)
        cur = om.MTransformationMatrix(cam_dag.inclusiveMatrix()).translation(om.MSpace.kWorld)
        if distance is None:
            distance = math.dist((cur.x, cur.y, cur.z), center)
            if distance < 1e-3:
                distance = 60.0
        d = self.grid_direction(yaw, pitch, forward_yaw)
        pos = (center[0] + d[0] * distance, center[1] + d[1] * distance, center[2] + d[2] * distance)
        # 視線 = pos → center。カメラは -Z を向く（z 軸 = 中心から自分への向き）
        z = _normalize(d)
        up = (0.0, 1.0, 0.0) if abs(z[1]) < 0.999 else (0.0, 0.0, 1.0)
        x = _normalize(_cross(up, z))
        y = _cross(z, x)
        world = om.MMatrix([x[0], x[1], x[2], 0, y[0], y[1], y[2], 0, z[0], z[1], z[2], 0, pos[0], pos[1], pos[2], 1])
        local = om.MTransformationMatrix(world * cam_dag.exclusiveMatrixInverse())
        t = local.translation(om.MSpace.kTransform)
        order = cmds.getAttr(cam + ".rotateOrder")
        e = local.rotation(asQuaternion=True).asEulerRotation().reorderIt(order)
        ui_len = om.MDistance.uiUnit()
        ui_ang = om.MAngle.uiUnit()
        cmds.setAttr(cam + ".translate", *(om.MDistance(v, om.MDistance.kCentimeters).asUnits(ui_len) for v in (t.x, t.y, t.z)))
        cmds.setAttr(cam + ".rotate", *(om.MAngle(v, om.MAngle.kRadians).asUnits(ui_ang) for v in (e.x, e.y, e.z)))
        shapes = cmds.listRelatives(cam, shapes=True, type="camera") or []
        if shapes:  # tumble の中心を顔にする
            try:
                cmds.setAttr(shapes[0] + ".centerOfInterest", om.MDistance(distance, om.MDistance.kCentimeters).asUnits(ui_len))
            except RuntimeError:
                pass

    def camera_drag_to(self, yaw: float, pitch: float, camera: Optional[str] = None) -> None:
        """グリッドの赤い点のドラッグ用: カメラを (Yaw, Pitch) へ動かす（今の距離のまま中心を向く）。Maya の Undo には積まない。
        正投影のカメラ・位置や回転がロック / 他とつながっているカメラは動かせない（FacialSessionError）。"""
        cam = self.camera_transform(camera)
        shapes = cmds.listRelatives(cam, shapes=True, type="camera") or []
        if shapes and cmds.getAttr(shapes[0] + ".orthographic"):
            raise FacialSessionError("正投影のカメラは、角度を指定して動かせません")
        for a in ("tx", "ty", "tz", "rx", "ry", "rz"):
            plug = f"{cam}.{a}"
            if cmds.getAttr(plug, lock=True) or cmds.connectionInfo(plug, isDestination=True):
                raise FacialSessionError("カメラの位置か回転がロックされている（または他とつながっている）ので、動かせません")
        state = cmds.undoInfo(query=True, state=True)
        cmds.undoInfo(stateWithoutFlush=False)  # ドラッグ中の細かい移動で Undo を埋めない
        try:
            self.camera_to_angles(yaw, pitch, cam)
        except RuntimeError as exc:
            raise FacialSessionError(f"カメラを動かせませんでした: {exc}") from exc
        finally:
            cmds.undoInfo(stateWithoutFlush=bool(state))

    def camera_to_key(self, index: int, camera: Optional[str] = None) -> float:
        """パース補正のキーの位置へカメラを動かす: 顔の正面（Yaw 0 / Pitch 0）に置き、軸が距離ならキーの距離（cm）へ、
        画角（fov）なら今の距離のまま焦点距離をキーの縦の画角に合わせる（フィルムフィットは「縦」にそろえる）。
        カメラの transform とレンズを書く（Maya の Undo に積まれる）。合わせた軸の値（cm / 度）を返す。"""
        doc = self.require()
        p = doc.perspective
        if p is None or not 0 <= index < len(p.keys):
            raise FacialSessionError("パース補正のキーが範囲外です")
        value = float(p.keys[index].value)
        if not V.perspective_value_ok(p.axis, value):
            raise FacialSessionError(f"キーの値 {value:g} は{'画角' if p.axis == 'fov' else '距離'}として使えません")
        cam = self.camera_transform(camera)
        if p.axis == "fov":
            self.camera_to_angles(0.0, 0.0, camera)  # 距離は今のまま
            preview_rig.set_camera_fov(cam, value)
        else:
            self.camera_to_angles(0.0, 0.0, camera, distance=value)
        return value

    # ============================================================ シーンの出来事（userSetup の scriptJob から）
    def on_before_scene_change(self) -> None:
        """新しいシーン / 別のシーンを開く直前（PreFileNewOrOpened）: 編集状態を抜けて、今のシーンを元の姿勢へ戻す。"""
        self._suspended = None
        self.scene_grid.remove()
        self.end_edit(quiet=True)

    def on_new_scene(self) -> None:
        """新しいシーン（NewSceneOpened）: 古いシーンの基準姿勢の記録は捨てる（新しいシーンへ書かない）。データは開いたまま。"""
        self._suspended = None
        self._forget_edit()
        self.scene_grid.forget()
        if self.presenters is not None:
            self.refresh_scene(notify=True)


# ---------------------------------------------------------------------------
# 結果の型（プレビューの状態・T-20 の取り込み）
# ---------------------------------------------------------------------------

T20_ANGLES = (("front", 0.0), ("threeQuarter", 45.0), ("side", 90.0))  # T-20 の補正シェイプ → 格子に置く Yaw（Pitch は 0）
T20_LABELS = {"front": "正面", "threeQuarter": "3/4", "side": "横"}
T20_SNAP_TOLERANCE = 10.0  # 格子の列の Yaw がこの角度（度）以内なら「その角度の列がある」とみなす
T20_NODE_PREFIX = "tdViewCorrection_"  # T-20（Toon）の補正 blendShape ノード名 `tdViewCorrection_<メッシュ>`


class NoLookError(FacialSessionError):
    """T-20 の取り込みで、開いている Toon の Look が無い（look.json を選ぶ必要がある）。"""


@dataclass
class T20ImportResult(CommandResult):
    """`import_t20` の結果。ok=False のとき Document は変わっていない。"""

    imported: list[dict[str, Any]] = field(default_factory=list)  # {"key": "front", "row", "col", "yaw", "pitch", "curve", "snapped": bool}
    skipped: list[str] = field(default_factory=list)  # 取り込まなかったものと理由
    existing: list[str] = field(default_factory=list)  # 既にキーがあるので上書きしなかった点（overwrite=True で上書き）
    warnings: list[str] = field(default_factory=list)


@dataclass
class _SuspendedEdit:
    """保存のあいだ編集状態を抜けたときの、入り直すための控え。"""

    base: Optional["BaseExpression"] = None
    sculpt: Optional["SculptState"] = None
    lip_try: Optional["LipTry"] = None


@dataclass
class LipTry:
    """リップシンクの「試す」の入力: 音素ごとの強さ（0〜1）・声量（None = 渡さない）・感情レイヤーの重み。"""

    phonemes: dict[str, float] = field(default_factory=dict)
    volume: Optional[float] = None
    emotions: dict[str, float] = field(default_factory=dict)


@dataclass
class BaseExpression:
    """土台の表情（シーンだけの下敷き）。名前と、シーンのシェイプ名 → 重み。"""

    name: str
    curves: dict[str, float]


@dataclass
class BaseState:
    """土台とポーズの今の重なり。name が "" なら土台は無い。"""

    name: str = ""
    curves: dict[str, float] = field(default_factory=dict)  # 土台の重み
    shared: dict[str, float] = field(default_factory=dict)  # ポーズにも値があるシェイプ → 土台の値
    base_only: list[str] = field(default_factory=list)  # ポーズの値が 0 のシェイプ（土台の値だけが当たる）
    over_limit: dict[str, float] = field(default_factory=dict)  # 土台と合わせて可動域を超えたシェイプ → シーンに当てた値

    @property
    def active(self) -> bool:
        return bool(self.name)


@dataclass
class SculptState:
    """「この角度で彫る」の最中の状態。"""

    name: str  # 彫っているターゲット名
    node: str
    index: int
    kind: str  # "point"（点の fcs_*）| "combo"（組み合わせ補正）
    layer_row_col: Optional[tuple[int, int, int]]  # point のとき (レイヤー番号, row, col)
    created: bool  # この彫りで新しく作ったターゲットか
    mesh: str
    tool: bool  # Sculpt Geometry ツールを有効にしたか（バッチでは False）
    was_editing: bool = True  # 始める前から編集状態だったか（組み合わせ補正を終えたとき、編集状態へ戻すか抜けるかの判断）


@dataclass
class PreviewStatus:
    """プレビュー（rig）の今の状態（UI 用）。state: "none" / "live" / "stale" / "keys"（`preview_state`）。"""

    state: str = "none"
    rig: str = ""
    camera: str = ""
    enabled: bool = True
    alpha: float = 1.0
    alpha_keyed: bool = False
    use_manual: bool = False
    manual_yaw: float = 0.0
    manual_pitch: float = 0.0
    manual_keyed: bool = False
    out_yaw: float = 0.0
    out_pitch: float = 0.0
    exaggeration: float = 1.0  # 誇張（`_Ex`）の強さ（rig の exaggeration）
    exaggeration_keyed: bool = False
    has_extreme: bool = False  # 配線している `_Ex` があるか（無いと誇張のスライダーは効かない）
    perspective: float = 1.0  # パース補正の強さ（rig の perspective）
    perspective_keyed: bool = False
    has_perspective: bool = False  # 配線しているパース補正のシェイプがあるか（無いとスライダーは効かない）
    perspective_axis: str = "distance"  # パース補正の軸
    perspective_value: Optional[float] = None  # 今の軸の値（距離 cm / 画角 度）。パース補正を配線しているときだけ
    out_distance: Optional[float] = None  # カメラと格子の中心の距離（cm）。距離で重みを決めるレイヤーがあるときだけ
    distance_layers: list[str] = field(default_factory=list)  # 重みをカメラの距離で決めているレイヤー（emotion_ は使われない）
    emotions: list[tuple[str, str, float, bool]] = field(default_factory=list)  # (レイヤー名, rig のアトリビュート名, 値, キーあり)
    editing: bool = False  # 編集中は補正が止まっている
    warnings: list[str] = field(default_factory=list)
    material_link: str = ""  # マテリアル連携の一行（連携がオフのときは空）


def _normalize(v: Sequence[float]) -> tuple[float, float, float]:
    n = math.sqrt(sum(c * c for c in v))
    return (v[0] / n, v[1] / n, v[2] / n) if n > 1e-12 else (0.0, 0.0, 1.0)


def _cross(a: Sequence[float], b: Sequence[float]) -> tuple[float, float, float]:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


# ---------------------------------------------------------------------------
# シングルトンとシーンの出来事
# ---------------------------------------------------------------------------

_current = FacialSession()


def current() -> FacialSession:
    return _current


def on_scene_opened() -> None:
    """SceneOpened イベント（userSetup.py で登録）: シーンに記録された fcpose があれば開く。

    編集中のデータに未保存の変更があるときは上書きしない（作業を消さない）。シーンが入れ替わったので、編集状態は（戻さずに）捨てる。
    """
    s = _current
    s._forget_edit()
    s.scene_grid.forget()
    path = FacialSession.remembered_path()
    if not path or not Path(path).exists():
        if s.presenters is not None:
            s.refresh_scene(notify=True)  # 開いたままのデータ: 新しいシーンの事情を取り直す
        return
    if s.has_unsaved_work:
        print(f"[T-Drive] 未保存の FacialController のデータがあるため自動では開きません: {path}")
        return
    try:
        s.open(path)
        print(f"[T-Drive] FacialController のデータを開きました: {project.to_project_path(path)}")
    except Exception as exc:  # noqa: BLE001  壊れたデータでシーンを開く操作自体は止めない
        print(f"[T-Drive] FacialController のデータを開けませんでした: {exc}")


def on_scene_saved() -> None:
    """SceneSaved イベント（userSetup.py で登録）: シーンの保存（Ctrl+S）で、編集中のデータも保存する。"""
    s = _current
    if s.presenters is None or not s.dirty:
        return
    try:
        path = s.save()
    except Exception as exc:  # noqa: BLE001  シーンの保存自体は止めない
        lifecycle.report_error(f"FacialController のデータを保存できませんでした: {exc}", traceback.format_exc(), once=False)
        return
    cmds.file(modified=False)  # fileInfo の記録でシーンが未保存に戻らないように
    print(f"[T-Drive] シーンの保存に合わせて FacialController のデータを保存しました: {project.to_project_path(path)}")
    if not cmds.about(batch=True):
        cmds.inViewMessage(amg="T-Drive: FacialController のデータも保存しました", pos="topCenter", fade=True)


def on_before_scene_change() -> None:
    """PreFileNewOrOpened イベント: シーンを閉じる前に、編集状態を抜けて元の姿勢へ戻す。"""
    _current.on_before_scene_change()


def on_new_scene() -> None:
    """NewSceneOpened イベント。"""
    _current.on_new_scene()


# --- Maya のシーンのメッセージ（保存・出力・終了）。M-14: 登録の控えは sys に持つ（importlib.reload でも消えない）
_SCENE_CALLBACKS_ATTR = "_tdrive_facial_scene_callbacks"


@lifecycle.guarded()
def _cb_before_save(_client=None) -> None:
    _current.on_before_save()


@lifecycle.guarded()
def _cb_after_save(_client=None) -> None:
    _current.on_after_save()


@lifecycle.guarded()
def _cb_before_export(_client=None) -> None:
    _current.suspend_edit_for_save()  # 出力にも基準姿勢が入るので、保存と同じに抜ける（データの場所は記録しない）


@lifecycle.guarded()
def _cb_after_export(_client=None) -> None:
    _current.resume_edit_after_save()


@lifecycle.guarded()
def _cb_maya_exiting(_client=None) -> None:
    _current.end_edit(quiet=True)


def remove_scene_callbacks() -> None:
    """登録済みの MSceneMessage のコールバックを外す（古いモジュールが登録したものも。何度呼んでもよい）。"""
    for cid in list(getattr(sys, _SCENE_CALLBACKS_ATTR, [])):
        try:
            om.MMessage.removeCallback(cid)
        except Exception:  # noqa: BLE001  既に無い
            pass
    setattr(sys, _SCENE_CALLBACKS_ATTR, [])


def install_scene_callbacks() -> None:
    """保存・出力の直前に編集状態を抜け、直後に戻すコールバックを登録する（M-1）。登録し直しても重ならない。"""
    remove_scene_callbacks()
    ids = []
    for msg, fn in (
        (om.MSceneMessage.kBeforeSave, _cb_before_save),
        (om.MSceneMessage.kAfterSave, _cb_after_save),
        (om.MSceneMessage.kBeforeExport, _cb_before_export),
        (om.MSceneMessage.kAfterExport, _cb_after_export),
        (om.MSceneMessage.kMayaExiting, _cb_maya_exiting),
    ):
        lifecycle.keep_alive(fn)
        ids.append(om.MSceneMessage.addCallback(msg, fn))
    setattr(sys, _SCENE_CALLBACKS_ATTR, ids)


def _reload_cleanup() -> None:
    """ツールのリロード前: シーンを基準姿勢のまま残さない（古いモジュールが解放される前に戻す）。コールバックも外す。"""
    remove_scene_callbacks()
    _current.end_edit(quiet=True)
    _current.listeners.clear()


lifecycle.on_reload(_reload_cleanup)
install_scene_callbacks()
