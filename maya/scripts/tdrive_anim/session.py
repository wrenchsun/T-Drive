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
from .core import clips as C
from .core import shot as SH

PATH_KEY = "tdAnimSetPath"  # fileInfo: animset.json のプロジェクト相対パス
SHOT_KEY = "tdShotPath"  # fileInfo: 開いているショット（shots/<Shot>.shot.json）のプロジェクト相対パス
MODEL_KEY = "tdAnimModelId"  # fileInfo: ModelId
UNDO_LIMIT = 100


def undoable(fn):
    """エディタ内 Undo の区切りを入れる。入れ子の呼び出しでは外側の 1 回だけ。データが変わらなければ積まない。"""

    @wraps(fn)
    def wrapper(self, *args, **kwargs):
        outer = self._undo_depth == 0
        before = self._snapshot() if outer else None
        self._undo_depth += 1
        try:
            result = fn(self, *args, **kwargs)
        finally:
            self._undo_depth -= 1
            if outer and self._snapshot() != before:
                self._push_undo(before)
        return result

    return wrapper


class AnimSession:
    def __init__(self) -> None:
        self.data: dict[str, Any] | None = None
        self.path: Path | None = None
        self.dirty = False
        self.shot: dict[str, Any] | None = None  # 開いているショット（docs/20 §5。animset と同じ Undo にまとめる）
        self.shot_path: Path | None = None
        self.shot_dirty = False
        self.listeners: list[Callable[[], None]] = []
        self._undo: list[dict[str, Any]] = []  # {"data": animset, "shot": ショット} のスナップショット
        self._redo: list[dict[str, Any]] = []
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
    def _snapshot(self) -> dict[str, Any]:
        return {"data": copy.deepcopy(self.data), "shot": copy.deepcopy(self.shot)}

    def _restore(self, snap: dict[str, Any]) -> None:
        self.data, self.shot = snap["data"], snap["shot"]
        self.shot_dirty = self.shot_dirty or self.shot is not None

    def _push_undo(self, before: dict[str, Any]) -> None:
        self._undo.append(before)
        del self._undo[:-UNDO_LIMIT]
        self._redo.clear()

    def undo(self) -> bool:
        if not self._undo:
            return False
        self._redo.append(self._snapshot())
        self._restore(self._undo.pop())
        self._changed()
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        self._undo.append(self._snapshot())
        self._restore(self._redo.pop())
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
        return (self.data is not None and self.dirty) or (self.shot is not None and self.shot_dirty)

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
    def add_clip_from_range(self, action: str, start: int, end: int, direction: str = "", number: int | None = None) -> dict[str, Any]:
        """範囲（タイムスライダーなど）から新しいクリップを足す。名前の重複・範囲の逆転は AnimSetError。"""
        clip = C.from_range(self.require(), action, start, end, direction, number)
        self._changed()
        return clip

    @undoable
    def update_clip(self, clip_name: str, /, **fields: Any) -> dict[str, Any]:
        """クリップの属性を変える（1 回の呼び出し = 1 Undo）。値が不正なら AnimSetError で何も変えない。"""
        clip = C.update(self.require(), clip_name, **fields)
        self._changed()
        return clip

    @undoable
    def duplicate_clip(self, name: str) -> dict[str, Any]:
        clip = C.duplicate(self.require(), name)
        self._changed()
        return clip

    @undoable
    def add_event(self, clip_name: str, frame: int, key: str, args: Any = None) -> dict[str, Any]:
        clip = self._clip(clip_name)
        ev = C.add_event(clip, frame, key, args)
        self._changed()
        return ev

    @undoable
    def add_foot_plant(self, clip_name: str, frame: int, side: str) -> dict[str, Any]:
        ev = C.add_foot_plant(self._clip(clip_name), frame, side)
        self._changed()
        return ev

    @undoable
    def remove_event(self, clip_name: str, index: int) -> bool:
        ok = C.remove_event(self._clip(clip_name), index)
        if ok:
            self._changed()
        return ok

    @undoable
    def paste_attrs(self, names: list[str], text: str) -> int:
        """コピーしたクリップの属性を、選んだクリップへ貼り付ける。別の形式の文字列は AnimSetError。"""
        attrs = C.attrs_from_text(text)
        for name in names:
            C.update(self.require(), name, **attrs)
        if names:
            self._changed()
        return len(names)

    # ------------------------------------------------------------ ショット（docs/20 §5）
    def require_shot(self) -> dict[str, Any]:
        if self.shot is None:
            raise RuntimeError("ショットが開かれていません（新規作成 or 開く）")
        return self.shot

    @undoable
    def new_shot(self, name: str, frame_rate: int = 30, start: int = 0, end: int = 240) -> dict[str, Any]:
        """新しいショット（保存先は shots/<Shot>.shot.json。まだ書かない）。名前・範囲が不正なら ShotError。"""
        self.shot = SH.new_shot(name, frame_rate, start, end)
        self.shot_path = SH.path_for(project.shots_dir(), name)
        self.shot_dirty = True
        self._changed()
        return self.shot

    def open_shot(self, path: str | Path) -> dict[str, Any]:
        """ファイルからショットを開く（壊れたファイルは ShotError）。"""
        data = SH.load(path)
        self.shot, self.shot_path, self.shot_dirty = data, Path(path), False
        self._changed(dirty=False)
        return data

    def save_shot(self, path: str | Path | None = None) -> Path:
        shot = self.require_shot()
        errs = SH.errors(shot)
        if errs:
            raise SH.ShotError("保存できません:" + chr(10) + chr(10).join(str(e) for e in errs))
        target = Path(path) if path else (self.shot_path or SH.path_for(project.shots_dir(), shot["shot"]))
        SH.save(target, shot)
        self.shot_path, self.shot_dirty = target, False
        cmds.fileInfo(SHOT_KEY, project.to_project_path(target))
        self._changed(dirty=False)
        return target

    @staticmethod
    def remembered_shot_path() -> str | None:
        try:
            got = cmds.fileInfo(SHOT_KEY, query=True)
        except RuntimeError:
            return None
        value = got[0] if got else ""
        return project.from_project_path(value) if value else None

    @undoable
    def shot_set(self, key: str, value: Any) -> None:
        self.require_shot()[key] = value
        self.shot_dirty = True
        self._changed()

    @undoable
    def shot_add_camera(self, node: str, start: int, end: int) -> dict[str, Any]:
        cam = SH.add_camera(self.require_shot(), node, start, end)
        self.shot_dirty = True
        self._changed()
        return cam

    @undoable
    def shot_remove_camera(self, index: int) -> bool:
        ok = SH.remove_camera(self.require_shot(), index)
        if ok:
            self.shot_dirty = True
            self._changed()
        return ok

    @undoable
    def shot_add_character(self, model_id: str, namespace: str | None = None, sequence: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        ch = SH.add_character(self.require_shot(), model_id, namespace, sequence)
        self.shot_dirty = True
        self._changed()
        return ch

    @undoable
    def shot_set_sequence(self, index: int, rows: list[dict[str, Any]]) -> None:
        """キャラクターのクリップ列（ブレンドタブの「この列をショットへ」）を入れる。"""
        shot = self.require_shot()
        if not 0 <= index < len(shot["characters"]):
            raise SH.ShotError("キャラクターがありません")
        shot["characters"][index]["sequence"] = copy.deepcopy(rows)
        self.shot_dirty = True
        self._changed()

    @undoable
    def shot_remove_character(self, index: int) -> bool:
        shot = self.require_shot()
        if 0 <= index < len(shot["characters"]):
            del shot["characters"][index]
            self.shot_dirty = True
            self._changed()
            return True
        return False

    @undoable
    def shot_add_event(self, frame: int, key: str) -> dict[str, Any]:
        ev = SH.add_event(self.require_shot(), frame, key)
        self.shot_dirty = True
        self._changed()
        return ev

    @undoable
    def shot_remove_event(self, index: int) -> bool:
        shot = self.require_shot()
        if 0 <= index < len(shot["events"]):
            del shot["events"][index]
            self.shot_dirty = True
            self._changed()
            return True
        return False

    def _clip(self, name: str) -> dict[str, Any]:
        clip = A.find_clip(self.require(), name)
        if clip is None:
            raise A.AnimSetError(f"クリップがありません: {name}")
        return clip

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
    _open_remembered_shot(s)
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


def _open_remembered_shot(s: "AnimSession") -> None:
    path = AnimSession.remembered_shot_path()
    if not path or not Path(path).exists() or s.shot_dirty:
        return
    try:
        s.open_shot(path)
        print(f"[T-Drive] ショットを開きました: {project.to_project_path(path)}")
    except Exception as exc:  # noqa: BLE001  壊れたデータでシーンを開く操作自体は止めない
        print(f"[T-Drive] ショットを開けませんでした: {exc}")


def on_scene_saved() -> None:
    """SceneSaved: シーンの保存に合わせて、編集中の animset とショットも保存する。"""
    s = _current
    if s.shot is not None and s.shot_dirty:
        try:
            print(f"[T-Drive] シーンの保存に合わせてショットを保存しました: {project.to_project_path(s.save_shot())}")
            cmds.file(modified=False)
        except Exception as exc:  # noqa: BLE001
            lifecycle.report_error(f"ショットを保存できませんでした: {exc}", traceback.format_exc(), once=False)
    if s.data is None or not s.dirty:
        return
    try:
        path = s.save()
    except Exception as exc:  # noqa: BLE001  シーンの保存自体は止めない
        lifecycle.report_error(f"animset を保存できませんでした: {exc}", traceback.format_exc(), once=False)
        return
    cmds.file(modified=False)  # fileInfo の記録でシーンが未保存に戻らないように
    print(f"[T-Drive] シーンの保存に合わせて animset を保存しました: {project.to_project_path(path)}")
