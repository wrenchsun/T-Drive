"""格子のサムネイルを作る（Maya 依存。docs/14 §5.3、F2-6）。キャッシュの鍵・置き場所は `core/thumbs.py`。

グリッドタブの「サムネイルを作り直す」から呼ぶ。アクティブレイヤーの、ポーズのある点それぞれについて、

1. その点のポーズを基準姿勢へ当てる（`session.pose_scope`。選択中の点・編集中の値・土台の表情・文書には触らない）
2. **一時のカメラ**をその点の角度へ動かす（`session.camera_to_point`。ユーザーのカメラは動かさない）
3. 小さな PNG を 1 枚書く（既定は `cmds.playblast` のオフスクリーン。ビューポートのモデルパネルを一時的に一時カメラへ向け、終わりに戻す）

を行い、`<一時フォルダ>/tdrive_facial_thumbs/…` に保存する。終わったら（例外でも）一時カメラを消し、モデルパネルのカメラ・選択を元へ戻し、
編集状態も入る前へ戻す。Maya の Undo には何も積まない（`undoInfo(stateWithoutFlush=False)`）。

**mayapy / batch ではビューポートが無いので作れない**: `can_capture()` が理由つきで False を返し、`capture` は
「この環境ではサムネイルを作れません」と報告する（例外にしない）。`render` を渡すと別の描き方（テスト用の偽物など）に差し替えられる。
"""

from __future__ import annotations

import contextlib
import glob
import os
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from maya import cmds
from tdrive import lifecycle

from . import viewport
from .core import thumbs
from .core import validate as V

UNAVAILABLE = "この環境ではサムネイルを作れません"
FAILED = "サムネイルを作れませんでした"

Render = Callable[[str, Path, int], bool]  # (カメラの transform, 書き出す PNG のパス, 画像の大きさ) → 書けたか


@dataclass
class ThumbReport:
    ok: bool = True
    unavailable: bool = False  # この環境ではビューポートが無くて作れない（撮れる環境で書けなかっただけなら False）
    message: str = ""
    made: list[tuple[int, int, int]] = field(default_factory=list)  # (レイヤー番号, row, col)
    failed: list[str] = field(default_factory=list)
    folder: Optional[Path] = None


def _model_panel() -> Optional[str]:
    """サムネイルを描くモデルパネル（`viewport.pick_panel`: フォーカス中 → 最後にフォーカスがあった → 見えているもの）。無ければ None。"""
    return viewport.pick_panel()


def can_capture() -> tuple[bool, str]:
    """この環境でサムネイルを作れるか。(作れるか, 作れない理由)。batch（mayapy など）・モデルパネルが無いと作れない。"""
    try:
        if cmds.about(batch=True):
            return False, "Maya が batch / mayapy で動いていて、ビューポートがありません"
    except RuntimeError:
        return False, "Maya の状態を調べられません"
    if _model_panel() is None:
        return False, "モデルパネル（ビューポート）がありません"
    return True, ""


def render_with_playblast(cam: str, path: Path, size: int) -> bool:
    """ビューポートのモデルパネルを一時カメラへ向けて、1 フレームをオフスクリーンで PNG にする。終わりにパネルのカメラを戻す。"""
    panel = _model_panel()
    if panel is None:
        return False
    base = path.with_suffix("")
    esc = glob.escape(str(base))

    def _candidates() -> list[str]:  # 古い書き出し先の名前（拡張子なし・<名前>.0001.png など）
        found = [str(path), str(base)] + sorted(glob.glob(esc + ".*.png")) + sorted(glob.glob(esc + "*.png"))
        out: list[str] = []
        for f in found:
            if f not in out and os.path.isfile(f):
                out.append(f)
        return out

    for stale in _candidates():  # 前回の残りを消す（拡張子なしの名前も）
        with contextlib.suppress(OSError):
            os.remove(stale)
    original = cmds.modelPanel(panel, query=True, camera=True)
    returned = None
    try:
        cmds.modelPanel(panel, edit=True, camera=cam)
        returned = cmds.playblast(
            frame=[cmds.currentTime(query=True)],
            format="image",
            compression="png",
            completeFilename=str(path).replace("\\", "/"),  # Maya 2026 は指定の名前のまま書く（拡張子まで含める）
            widthHeight=(size, size),
            percent=100,
            quality=100,
            offScreen=True,
            viewer=False,
            showOrnaments=False,
            forceOverwrite=True,
            editorPanelName=panel,
        )
    finally:
        with contextlib.suppress(RuntimeError):
            cmds.modelPanel(panel, edit=True, camera=original)
    # 正確な path → playblast の戻り値 → 古い名前の順に探して path へ寄せる
    produced = []
    if path.is_file():
        produced.append(str(path))
    if isinstance(returned, str) and returned and os.path.isfile(returned) and returned not in produced:
        produced.append(returned)
    produced += [f for f in _candidates() if f not in produced]
    if produced:
        if os.path.abspath(produced[0]) != os.path.abspath(str(path)):
            os.replace(produced[0], path)
        for extra in produced[1:]:
            if os.path.abspath(extra) != os.path.abspath(str(path)):
                with contextlib.suppress(OSError):
                    os.remove(extra)
    return path.is_file() and path.stat().st_size > 0


@contextlib.contextmanager
def _no_undo():
    """Maya の Undo に積まない（一時カメラ・姿勢の出し入れでユーザーの Undo 履歴を汚さない）。元が無効なら何もしない。"""
    was = cmds.undoInfo(query=True, state=True)
    if was:
        cmds.undoInfo(stateWithoutFlush=False)
    try:
        yield
    finally:
        if was:
            cmds.undoInfo(stateWithoutFlush=True)


def _frame_distance(mesh: str) -> float:
    """顔が 128 px の画面にほどよく収まるカメラの距離（cm）。顔メッシュの大きさから決める。"""
    try:
        x0, y0, z0, x1, y1, z1 = cmds.exactWorldBoundingBox(mesh)
        return max(max(x1 - x0, y1 - y0, z1 - z0) * 2.6, 10.0)
    except (RuntimeError, ValueError):
        return 60.0


def folder_for(session) -> Path:
    """このデータのサムネイルを置くフォルダ（作らない）。"""
    doc = session.require()
    return thumbs.cache_dir(session.path, fallback=f"unsaved:{doc.asset or ''}")


def point_key(session, layer_index: int, row: int, col: int, size: int = thumbs.DEFAULT_SIZE) -> Optional[str]:
    """点（レイヤー番号, row, col）の今のポーズに対するサムネイルの鍵。ポーズが空の点は None。"""
    doc = session.require()
    pt = doc.layers[layer_index].points.get((row, col))
    if pt is None or pt.pose.is_empty():
        return None
    yaw, pitch = session.grid.angles_of(row, col)
    return thumbs.point_key(V.pose_hash(pt.pose), yaw, pitch, size)


def current_thumb(session, layer_index: int, row: int, col: int, size: int = thumbs.DEFAULT_SIZE) -> Optional[Path]:
    """今のポーズのサムネイルがあればそのパス。無い・古い（ポーズが変わった）・ポーズが空なら None。"""
    key = point_key(session, layer_index, row, col, size)
    return thumbs.find_current(folder_for(session), layer_index, row, col, key) if key else None


def capture(
    session,
    layer_index: Optional[int] = None,
    size: int = thumbs.DEFAULT_SIZE,
    render: Optional[Render] = None,
    progress: Optional[Callable[[int, int], None]] = None,
) -> ThumbReport:
    """アクティブレイヤー（`layer_index`）の、ポーズのある点のサムネイルを作り直す。失敗は ThumbReport に入れる（例外にしない）。

    render を省くと `render_with_playblast`（ビューポートが必要。無ければ「この環境ではサムネイルを作れません」）。"""
    rep = ThumbReport()
    try:
        doc = session.require()
    except Exception as exc:  # noqa: BLE001
        return ThumbReport(ok=False, message=str(exc))
    if render is None:
        ok, why = can_capture()
        if not ok:
            return ThumbReport(ok=False, unavailable=True, message=f"{UNAVAILABLE}（{why}）")
        render = render_with_playblast
    li = session.ctx.active_layer if layer_index is None else layer_index
    li = min(max(li, 0), len(doc.layers) - 1)
    targets = [(r, c) for (r, c), pt in sorted(doc.layers[li].points.items()) if not pt.pose.is_empty()]
    if not targets:
        return ThumbReport(ok=True, message="ポーズのある点がありません（作るサムネイルはありません）")
    folder = folder_for(session)
    rep.folder = folder
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return ThumbReport(ok=False, message=f"サムネイルの置き場所を作れませんでした: {exc}", folder=folder)
    try:
        mesh = session.target_meshes()[0]
    except Exception:  # noqa: BLE001
        mesh = ""
    distance = _frame_distance(mesh) if mesh else 60.0
    saved_selection = cmds.ls(selection=True, long=True) or []
    cam: Optional[str] = None
    grid = getattr(session, "scene_grid", None)
    try:
        with (grid.hidden() if grid is not None else contextlib.nullcontext()), _no_undo():  # シーンの格子も写り込まない
            cmds.select(clear=True)  # 選択の表示が写り込まない
            cam = cmds.camera(name="tdFacialThumbCam")[0]
            with session.pose_scope() as apply:
                for i, (r, c) in enumerate(targets):
                    key = point_key(session, li, r, c, size)
                    if key is None:
                        continue
                    path = thumbs.thumb_path(folder, li, r, c, key)
                    try:
                        apply(li, r, c)
                        session.camera_to_point(r, c, cam, distance=distance)
                        got = render(cam, path, size)
                    except Exception as exc:  # noqa: BLE001  1 点の失敗で全部を止めない
                        got = False
                        rep.failed.append(f"R{r} C{c}: {exc}")
                        lifecycle.report_error(f"サムネイルを作れませんでした（R{r} C{c}）", traceback.format_exc(), once=False)
                    if got and path.is_file():
                        for old in thumbs.stale_files(folder, li, r, c, key):
                            with contextlib.suppress(OSError):
                                old.unlink()
                        rep.made.append((li, r, c))
                    elif not any(f.startswith(f"R{r} C{c}") for f in rep.failed):
                        rep.failed.append(f"R{r} C{c}: 画像を書けませんでした")
                    if progress is not None:
                        progress(i + 1, len(targets))
    except Exception as exc:  # noqa: BLE001
        rep.ok = False
        rep.message = f"サムネイルを作れませんでした: {exc}"
        lifecycle.report_error("サムネイルの作成でエラー", traceback.format_exc(), once=False)
    finally:
        with _no_undo():  # 一時カメラの削除も Undo に積まない（積むと Ctrl+Z でカメラが復活する。S-10）
            if cam is not None:
                with contextlib.suppress(RuntimeError):
                    if cmds.objExists(cam):
                        cmds.delete(cam)
            keep = [n for n in saved_selection if cmds.objExists(n)]
            with contextlib.suppress(RuntimeError):
                if keep:
                    cmds.select(keep, replace=True)
                else:
                    cmds.select(clear=True)
    if rep.ok:
        if rep.made:
            rep.message = f"サムネイルを {len(rep.made)} 枚作りました" + (f"（作れなかった点 {len(rep.failed)} 個）" if rep.failed else "")
        elif rep.failed:
            rep.ok = False  # 撮れる環境（ビューポートあり）で書けなかった。「この環境では」とは言わない
            rep.message = f"{FAILED}（{rep.failed[0]}）"
    return rep
