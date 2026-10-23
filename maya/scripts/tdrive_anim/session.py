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
import sys
import traceback
from functools import wraps
from pathlib import Path
from typing import Any, Callable

from maya import cmds
from maya.api import OpenMaya as om

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
        self._path_confirmed = True  # animset の保存先を「ここに書いてよい」と確かめたか（新規で既存のファイルがあるとき False。UX-3）
        self._shot_confirmed = True
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
        # 保存先（path / shot_path）も含める: 「新規」を Undo したのに保存先だけ新しい ModelId のままで、元の中身を別の場所に書く事故を防ぐ（SF-9）
        return {"data": copy.deepcopy(self.data), "shot": copy.deepcopy(self.shot), "path": self.path, "shot_path": self.shot_path,
                "confirmed": self._path_confirmed, "shot_confirmed": self._shot_confirmed}

    def _restore(self, snap: dict[str, Any]) -> None:
        self.data, self.shot = snap["data"], snap["shot"]
        self.path, self.shot_path = snap["path"], snap["shot_path"]
        self._path_confirmed, self._shot_confirmed = snap["confirmed"], snap["shot_confirmed"]
        self.shot_dirty = self.shot_dirty or self.shot is not None

    def _push_undo(self, before: dict[str, Any]) -> None:
        self._undo.append(before)
        del self._undo[:-UNDO_LIMIT]
        self._redo.clear()

    # Maya のシーンを変える操作（命名タブの「適用」「直す」「削除」）は Maya の Undo チャンクで戻す。エディタ内の Undo にはその印（{"maya": チャンク名}）を積み、
    # 取り出したら cmds.undo() / cmds.redo() を呼ぶ（Maya の Undo の先頭がそのチャンクのときだけ。食い違うときはビューポートでの Ctrl+Z を案内する。AN-F-12）
    last_undo_message = ""

    def push_maya_undo(self, chunk: str) -> None:
        """直前に行った Maya の操作（Undo チャンク名 chunk）を、エディタ内の Undo の履歴に積む。"""
        self._push_undo({"maya": chunk})

    def _maya_step(self, chunk: str, redo: bool) -> bool:
        info = cmds.undoInfo(query=True, redoName=True) if redo else cmds.undoInfo(query=True, undoName=True)
        if info != chunk:
            self.last_undo_message = (f"Maya のシーンの操作（{chunk}）を挟んでいます。先にビューポートで Ctrl+Z / Ctrl+Y を押して、Maya 側の操作を順に戻してください"
                                      f"（今の先頭: {info or 'なし'}）")
            return False
        (cmds.redo if redo else cmds.undo)()
        return True

    def undo(self) -> bool:
        self.last_undo_message = ""
        if not self._undo:
            return False
        top = self._undo[-1]
        if "maya" in top:
            if not self._maya_step(top["maya"], redo=False):
                return False
            self._redo.append(self._undo.pop())
            self._changed()
            return True
        self._redo.append(self._snapshot())
        self._restore(self._undo.pop())
        self._changed()
        return True

    def redo(self) -> bool:
        self.last_undo_message = ""
        if not self._redo:
            return False
        top = self._redo[-1]
        if "maya" in top:
            if not self._maya_step(top["maya"], redo=True):
                return False
            self._undo.append(self._redo.pop())
            self._changed()
            return True
        self._undo.append(self._snapshot())
        self._restore(self._redo.pop())
        self._changed()
        return True

    # ------------------------------------------------------------ 作る・開く・保存
    @staticmethod
    def default_path(model_id: str) -> Path:
        return project.anim_dir() / model_id / "animset.json"

    @undoable
    def new(self, model_id: str, skeleton_root: str = "spine_C0_0_jnt", meshes: list[str] | None = None, namespace: str = "") -> dict[str, Any]:
        """新しい animset を作る（保存先は既定の場所。まだ書かない）。ModelId の形が違えば AnimSetError。"""
        if not A.MODEL_ID_PATTERN.match(model_id or ""):
            raise A.AnimSetError("ModelId は英数字の PascalCase（例: MisotenChara）")
        self.data = A.new_animset(model_id, skeleton_root, meshes)
        self.data["namespace"] = namespace
        self.path = self.default_path(model_id)
        self._path_confirmed = False  # 既にファイルがあれば、確かめる（confirm_overwrite）まで保存しない
        self._changed()
        return self.data

    def existing_default(self, model_id: str) -> Path | None:
        """この ModelId の既定の保存先に既にファイルがあればそのパス（「新規」の前に、開くか上書きかを聞くため）。"""
        path = self.default_path(model_id)
        return path if path.exists() else None

    def confirm_overwrite(self) -> None:
        """今の保存先（既存のファイルがある）に上書きしてよいと確かめた。"""
        self._path_confirmed = True

    def close(self) -> None:
        """開いている animset とショットを閉じる（シーンが入れ替わったとき。ファイルには触れない）。Undo の履歴も捨てる。"""
        self.data, self.path, self.dirty = None, None, False
        self.shot, self.shot_path, self.shot_dirty = None, None, False
        self._path_confirmed = self._shot_confirmed = True
        self._undo.clear()
        self._redo.clear()
        self._changed(dirty=False)

    def open(self, path: str | Path) -> dict[str, Any]:
        """ファイルから開く（Undo の履歴は捨てる）。壊れたファイルは AnimSetError。"""
        data = A.load(path)
        self.data, self.path, self.dirty = data, Path(path), False
        self._path_confirmed = True  # 自分で選んだファイルなので、保存先として確かめ済み
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
        if path is None and not self._path_confirmed and target.exists():
            raise A.AnimSetExists(f"{project.to_project_path(target)} が既にあります。上書きすると中身が失われます。"
                                  "「開く…」で既存のものを開くか、別の ModelId にしてください（上書きするときは確認してください）")
        A.save(target, data)
        self.path, self.dirty, self._path_confirmed = target, False, True
        self.remember()
        self._changed(dirty=False)
        return target

    def remember(self) -> None:
        """シーンの fileInfo に animset の場所と ModelId を覚える。別のファイルが既にある既定の場所（確かめていない）は覚えない。"""
        if self.path is None or self.data is None:
            return
        if not self._path_confirmed and self.path.exists():
            return
        cmds.fileInfo(PATH_KEY, project.to_project_path(self.path))
        cmds.fileInfo(MODEL_KEY, self.data["modelId"])

    def remember_shot(self) -> None:
        """シーンの fileInfo にショットの場所を覚える。別のファイルが既にある既定の場所（確かめていない）は覚えない。"""
        if self.shot_path is None or self.shot is None:
            return
        if not self._shot_confirmed and self.shot_path.exists():
            return
        cmds.fileInfo(SHOT_KEY, project.to_project_path(self.shot_path))

    def close_animset(self) -> None:
        """animset だけ閉じる（ショットはそのまま）。"""
        self.data, self.path, self.dirty = None, None, False
        self._path_confirmed = True
        self._undo.clear()
        self._redo.clear()
        self._changed(dirty=False)

    def close_shot(self) -> None:
        self.shot, self.shot_path, self.shot_dirty = None, None, False
        self._shot_confirmed = True
        self._changed(dirty=False)

    # ------------------------------------------------------------ ツールのリロードをまたぐ引き継ぎ（CR-3）
    def export_state(self) -> dict[str, Any] | None:
        """リロード前の状態（未保存の変更・保存先・Undo を含む）。何も開いていなければ None。"""
        if self.data is None and self.shot is None:
            return None
        return {"data": self.data, "path": self.path, "dirty": self.dirty, "shot": self.shot, "shot_path": self.shot_path,
                "shot_dirty": self.shot_dirty, "confirmed": self._path_confirmed, "shot_confirmed": self._shot_confirmed,
                "undo": list(self._undo), "redo": list(self._redo)}

    def import_state(self, state: dict[str, Any] | None) -> bool:
        """`export_state` の結果で、リロード前の状態を作り直す。戻り: 復元したか。"""
        if not state:
            return False
        self.data, self.path, self.dirty = state["data"], state["path"], state["dirty"]
        self.shot, self.shot_path, self.shot_dirty = state["shot"], state["shot_path"], state["shot_dirty"]
        self._path_confirmed, self._shot_confirmed = state["confirmed"], state["shot_confirmed"]
        self._undo, self._redo = list(state["undo"]), list(state["redo"])
        self._changed(dirty=False)
        return True

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
        self._shot_confirmed = False
        self.shot_dirty = True
        self._changed()
        return self.shot

    def existing_shot(self, name: str) -> Path | None:
        """この名前のショットの保存先に既にファイルがあればそのパス。"""
        path = SH.path_for(project.shots_dir(), name)
        return path if path.exists() else None

    def confirm_shot_overwrite(self) -> None:
        self._shot_confirmed = True

    def open_shot(self, path: str | Path) -> dict[str, Any]:
        """ファイルからショットを開く（壊れたファイルは ShotError）。"""
        data = SH.load(path)
        self.shot, self.shot_path, self.shot_dirty = data, Path(path), False
        self._shot_confirmed = True
        self._changed(dirty=False)
        return data

    def save_shot(self, path: str | Path | None = None) -> Path:
        shot = self.require_shot()
        errs = SH.errors(shot)
        if errs:
            raise SH.ShotError("保存できません:" + chr(10) + chr(10).join(str(e) for e in errs))
        target = Path(path) if path else (self.shot_path or SH.path_for(project.shots_dir(), shot["shot"]))
        if path is None and not self._shot_confirmed and target.exists():
            raise SH.ShotError(f"{project.to_project_path(target)} が既にあります。上書きすると中身が失われます。「開く…」で既存のものを開くか、別の名前にしてください")
        SH.save(target, shot)
        self.shot_path, self.shot_dirty, self._shot_confirmed = target, False, True
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
            for m in (st.get("blendTree") or {}).get("motions", []):  # ブレンドツリーのモーション（AN-11）
                if isinstance(m, dict) and m.get("clip") == old:
                    m["clip"] = new
        self._changed()

    @undoable
    def rename_clips(self, pairs: list[tuple[str, str]]) -> list[tuple[str, str]]:
        """クリップ名をまとめて変える（**全体で 1 つの Undo**。命名タブの「適用」）。変えられなかったもの（重複・存在しない名前）は飛ばして、
        変えられたものの (旧, 新) を返す。状態図の参照も追従する。"""
        done = []
        for old, new in pairs:
            try:
                self.rename_clip(old, new)
                done.append((old, new))
            except A.AnimSetError:
                continue
        return done

    @undoable
    def remove_clips(self, names: list[str]) -> int:
        """クリップをまとめて消す（1 つの Undo）。消した数。"""
        n = 0
        for name in names:
            if A.remove_clip(self.require(), name):
                n += 1
        if n:
            self._changed()
        return n

    @undoable
    def duplicate_clips(self, names: list[str]) -> list[dict[str, Any]]:
        """クリップをまとめて複製する（1 つの Undo）。"""
        out = [C.duplicate(self.require(), n) for n in names]
        if out:
            self._changed()
        return out

    @undoable
    def update_clips(self, names: list[str], **fields: Any) -> int:
        """選んだクリップに同じ値をまとめて入れる（1 つの Undo）。値が不正なら AnimSetError で、全体を元に戻す。"""
        before = copy.deepcopy(self.require()["clips"])
        try:
            for n in names:
                C.update(self.require(), n, **fields)
        except A.AnimSetError:
            self.require()["clips"] = before
            raise
        if names:
            self._changed()
        return len(names)

    @undoable
    def remove_events(self, clip_name: str, indices: list[int]) -> int:
        """イベントをまとめて消す（1 つの Undo）。"""
        clip = self._clip(clip_name)
        n = sum(1 for i in sorted(set(indices), reverse=True) if C.remove_event(clip, i))
        if n:
            self._changed()
        return n

    @undoable
    def rescale_time(self, ratio: float) -> int:
        """シーンの fps をキーの時刻を保ったまま変えたとき（24 → 30 fps で 1.25 倍）、クリップとショットの範囲・イベントのフレームを同じ比率で直す（1 つの Undo。AN-F-13）。
        直したクリップの数を返す。"""
        n = 0
        if self.data is not None:
            n = A.rescale_frames(self.data, ratio)
        if self.shot is not None:
            SH.rescale_frames(self.shot, ratio)
            self.shot_dirty = True
        self._changed()
        return n

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
    """SceneOpened: シーンに記録された animset があれば開く。記録の無いシーンでは前のシーンのデータを閉じる（SF-10）。
    未保存の作業があるときは、開かない・閉じない（作業を消さない）で知らせる。"""
    from . import clips

    clips.forget()  # 再生中に別のシーンを開いたとき、前のシーンの再生範囲を書き戻さない（CR-5）
    s = _current
    _open_remembered_shot(s)
    path = AnimSession.remembered_path()
    if not path or not Path(path).exists():
        if s.data is not None:
            if s.dirty:
                lifecycle.report_error(f"未保存の animset（{s.model_id}）を開いたままにしています。このシーンには animset の記録がありません",
                                       "保存するか、不要なら「新規」「開く…」で入れ替えてください", once=False)
            else:
                s.close_animset()
        return
    if s.has_unsaved_work:
        print(f"[T-Drive] 未保存のアニメーション作業台のデータがあるため自動では開きません: {path}")
        return
    try:
        s.open(path)
        print(f"[T-Drive] animset を開きました: {project.to_project_path(path)}")
    except Exception as exc:  # noqa: BLE001  壊れたデータでシーンを開く操作自体は止めない
        s.close_animset()
        lifecycle.report_error(f"animset を開けませんでした（{project.to_project_path(path)}）: {exc}", traceback.format_exc(), once=False)


def _open_remembered_shot(s: "AnimSession") -> None:
    path = AnimSession.remembered_shot_path()
    if not path or not Path(path).exists():
        if s.shot is not None and not s.shot_dirty:
            s.close_shot()
        return
    if s.shot_dirty:
        return
    try:
        s.open_shot(path)
        print(f"[T-Drive] ショットを開きました: {project.to_project_path(path)}")
    except Exception as exc:  # noqa: BLE001  壊れたデータでシーンを開く操作自体は止めない
        s.close_shot()
        lifecycle.report_error(f"ショットを開けませんでした（{project.to_project_path(path)}）: {exc}", traceback.format_exc(), once=False)


def on_new_scene() -> None:
    """NewSceneOpened: 新しいシーンには前のシーンの animset・ショットを持ち越さない。未保存の変更があれば捨てることを知らせる。"""
    from . import clips

    clips.forget()
    s = _current
    if s.has_unsaved_work:
        lifecycle.report_error("新しいシーンにしたので、未保存だった animset / ショットの変更は破棄しました",
                               f"animset: {s.path}  shot: {s.shot_path}", once=False)
    if s.data is not None or s.shot is not None:
        s.close()


def on_before_save() -> None:
    """kBeforeSave: 保存される .ma/.mb に animset とショットの場所を入れる（SceneSaved の後では保存済みのファイルに入らない。SF-14）。"""
    s = _current
    s.remember()
    s.remember_shot()


def on_scene_saved() -> None:
    """SceneSaved: シーンの保存に合わせて、編集中の animset とショットも保存する（場所の記録は kBeforeSave で済んでいる）。"""
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


# ---------------------------------------------------------------------------
# Maya のシーンのメッセージ（保存の直前）。登録の控えは sys に持つ（importlib.reload でも消えない）
# ---------------------------------------------------------------------------
_CALLBACKS_ATTR = "_tdrive_anim_scene_callbacks"


@lifecycle.guarded()
def _cb_before_save(_client=None) -> None:
    on_before_save()
    from . import composition

    composition.suspend_for_save()  # 合成中のシーンを保存するときは、保存の間だけジョイントを元のつながりへ戻す（SF-19）


@lifecycle.guarded()
def _cb_after_save(_client=None) -> None:
    from . import composition

    composition.resume_after_save()


def remove_scene_callbacks() -> None:
    """登録済みの MSceneMessage のコールバックを外す（古いモジュールが登録したものも。何度呼んでもよい）。"""
    for cid in list(getattr(sys, _CALLBACKS_ATTR, [])):
        try:
            om.MMessage.removeCallback(cid)
        except Exception:  # noqa: BLE001  既に無い
            pass
    setattr(sys, _CALLBACKS_ATTR, [])


def install_scene_callbacks() -> None:
    """保存の直前に animset / ショットの場所をシーンへ記録するコールバックを登録する。登録し直しても重ならない。"""
    remove_scene_callbacks()
    lifecycle.keep_alive(_cb_before_save)
    lifecycle.keep_alive(_cb_after_save)
    setattr(sys, _CALLBACKS_ATTR, [om.MSceneMessage.addCallback(om.MSceneMessage.kBeforeSave, _cb_before_save),
                                   om.MSceneMessage.addCallback(om.MSceneMessage.kAfterSave, _cb_after_save)])


def _reload_cleanup() -> None:
    remove_scene_callbacks()
    _current.listeners.clear()


lifecycle.on_reload(_reload_cleanup)
install_scene_callbacks()
