"""アニメーション作業台の編集セッション（Maya 依存。docs/20 §3.3・§7）。

開いている `animset.json`（辞書）・エディタ内 Undo・変更通知を持つ。UI も MCP スクリプトもこのモジュール経由で操作する
（UI 無しでも全機能を呼べる。Qt は import しない）。作りは Toon の `tdrive_toon/session.py` に合わせてある。

- データは辞書のまま持つ（`core.animset`）。シーンには fileInfo `tdAnimSetPath`（プロジェクト相対）と `tdAnimModelId` だけを置く。
  SceneOpened で開き、SceneSaved で保存する
- Undo はデータのスナップショット（deepcopy）。コマンドは `@undoable`。**1 回の呼び出し = 1 つの Undo**。
  データが変わらなかった呼び出しは積まない。Maya のシーンの変更（キー・ノード）は Maya 自身の Undo の対象で、ここには積まない
- `listeners`: データが変わったとき UI が描き直す。mayapy / DirectX 11 でない環境でも動く（dx11Shader・Render Override は触らない）
"""

from __future__ import annotations

import copy
import traceback
from functools import wraps
from pathlib import Path
from typing import Any, Callable

from maya import cmds

from tdrive import lifecycle, project

from .core import animset as A

PATH_KEY = "tdAnimSetPath"  # fileInfo: animset.json のプロジェクト相対パス
MODEL_KEY = "tdAnimModelId"  # fileInfo: ModelId
UNDO_LIMIT = 100


def undoable(fn):
    """エディタ内 Undo の区切りを入れる。入れ子の呼び出しでは外側の 1 回だけ。データが変わらなければ積まない。"""

    @wraps(fn)
    def wrapper(self, *args, **kwargs):
        outer = self._undo_depth == 0
        before = copy.deepcopy(self.data) if outer else None
        self._undo_depth += 1
        try:
            result = fn(self, *args, **kwargs)
        finally:
            self._undo_depth -= 1
            if outer and self.data != before:
                self._push_undo(before)
        return result

    return wrapper


class AnimSession:
    def __init__(self) -> None:
        self.data: dict[str, Any] | None = None
        self.path: Path | None = None
        self.dirty = False
        self.listeners: list[Callable[[], None]] = []
        self._undo: list[dict[str, Any] | None] = []
        self._redo: list[dict[str, Any] | None] = []
        self._undo_depth = 0

    # ------------------------------------------------------------ 状態
    @property
    def model_id(self) -> str:
        return self.data["modelId"] if self.data else ""

    def require(self) -> dict[str, Any]:
        if self.data is None:
            raise RuntimeError("animset が開かれていません（新規作成 or 開く）")
        return self.data

    def _changed(self, dirty: bool = True) -> None:
        if dirty:
            self.dirty = True
        for fn in list(self.listeners):
            try:
                fn()
            except Exception:  # noqa: BLE001  1 つの購読者の失敗で残りを止めない
                lifecycle.report_error("アニメーション作業台の画面更新で例外", traceback.format_exc())

    def issues(self) -> list[A.Issue]:
        return A.validate(self.data) if self.data else []

    # ------------------------------------------------------------ Undo（データのスナップショット）
    def _push_undo(self, before: dict[str, Any] | None) -> None:
        self._undo.append(before)
        del self._undo[:-UNDO_LIMIT]
        self._redo.clear()

    def undo(self) -> bool:
        if not self._undo:
            return False
        self._redo.append(copy.deepcopy(self.data))
        self.data = self._undo.pop()
        self._changed()
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        self._undo.append(copy.deepcopy(self.data))
        self.data = self._redo.pop()
        self._changed()
        return True

    # ------------------------------------------------------------ 作る・開く・保存
    @staticmethod
    def default_path(model_id: str) -> Path:
        return project.anim_dir() / model_id / "animset.json"

    @undoable
    def new(self, model_id: str, skeleton_root: str = "spine_C0_0_jnt", meshes: list[str] | None = None) -> dict[str, Any]:
        """新しい animset を作る（保存先は既定の場所。まだ書かない）。ModelId の形が違えば AnimSetError。"""
        if not A.MODEL_ID_PATTERN.match(model_id or ""):
            raise A.AnimSetError("ModelId は英数字の PascalCase（例: MisotenChara）")
        self.data = A.new_animset(model_id, skeleton_root, meshes)
        self.path = self.default_path(model_id)
        self._changed()
        return self.data

    def open(self, path: str | Path) -> dict[str, Any]:
        """ファイルから開く（Undo の履歴は捨てる）。壊れたファイルは AnimSetError。"""
        data = A.load(path)
        self.data, self.path, self.dirty = data, Path(path), False
        self._undo.clear()
        self._redo.clear()
        self._changed(dirty=False)
        return data

    def save(self, path: str | Path | None = None) -> Path:
        """保存して、シーンの fileInfo に場所を覚える。構造の error があれば保存しない。"""
        data = self.require()
        errs = A.errors(data)
        if errs:
            raise A.AnimSetError("保存できません:\n" + "\n".join(str(e) for e in errs))
        target = Path(path) if path else (self.path or self.default_path(data["modelId"]))
        A.save(target, data)
        self.path, self.dirty = target, False
        self.remember()
        self._changed(dirty=False)
        return target

    def remember(self) -> None:
        """シーンの fileInfo に animset の場所と ModelId を覚える。"""
        if self.path is None or self.data is None:
            return
        cmds.fileInfo(PATH_KEY, project.to_project_path(self.path))
        cmds.fileInfo(MODEL_KEY, self.data["modelId"])

    @staticmethod
    def remembered_path() -> str | None:
        try:
            got = cmds.fileInfo(PATH_KEY, query=True)
        except RuntimeError:
            return None
        value = got[0] if got else ""
        return project.from_project_path(value) if value else None

    @property
    def has_unsaved_work(self) -> bool:
        return self.data is not None and self.dirty

    # ------------------------------------------------------------ 操作（データ）
    @undoable
    def set_value(self, key: str, value: Any) -> None:
        """トップレベルの値を変える（frameRate / skeletonRoot / meshes / export …）。"""
        self.require()[key] = value
        self._changed()

    @undoable
    def add_clip(self, clip: dict[str, Any]) -> dict[str, Any]:
        out = A.add_clip(self.require(), clip)
        self._changed()
        return out

    @undoable
    def rename_clip(self, old: str, new: str) -> None:
        """クリップ名を変える（状態図が参照していればそちらも追従）。重複・存在しない名前は AnimSetError。"""
        d = self.require()
        clip = A.find_clip(d, old)
        if clip is None:
            raise A.AnimSetError(f"クリップがありません: {old}")
        if new != old and A.find_clip(d, new) is not None:
            raise A.AnimSetError(f"同じ名前のクリップがあります: {new}")
        clip["name"] = new
        for st in (d.get("stateGraph") or {}).get("states", []):
            if st.get("clip") == old:
                st["clip"] = new
        self._changed()

    @undoable
    def remove_clip(self, name: str) -> bool:
        ok = A.remove_clip(self.require(), name)
        if ok:
            self._changed()
        return ok


# ---------------------------------------------------------------------------
# シングルトンとシーンの出来事
# ---------------------------------------------------------------------------
_current = AnimSession()


def current() -> AnimSession:
    return _current


def on_scene_opened() -> None:
    """SceneOpened: シーンに記録された animset があれば開く。未保存の作業があるときは上書きしない。"""
    s = _current
    path = AnimSession.remembered_path()
    if not path or not Path(path).exists():
        return
    if s.has_unsaved_work:
        print(f"[T-Drive] 未保存のアニメーション作業台のデータがあるため自動では開きません: {path}")
        return
    try:
        s.open(path)
        print(f"[T-Drive] animset を開きました: {project.to_project_path(path)}")
    except Exception as exc:  # noqa: BLE001  壊れたデータでシーンを開く操作自体は止めない
        print(f"[T-Drive] animset を開けませんでした: {exc}")


def on_scene_saved() -> None:
    """SceneSaved: シーンの保存に合わせて、編集中の animset も保存する。"""
    s = _current
    if s.data is None or not s.dirty:
        return
    try:
        path = s.save()
    except Exception as exc:  # noqa: BLE001  シーンの保存自体は止めない
        lifecycle.report_error(f"animset を保存できませんでした: {exc}", traceback.format_exc(), once=False)
        return
    cmds.file(modified=False)  # fileInfo の記録でシーンが未保存に戻らないように
    print(f"[T-Drive] シーンの保存に合わせて animset を保存しました: {project.to_project_path(path)}")
