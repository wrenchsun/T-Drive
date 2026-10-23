"""クリップの Maya 側（docs/20 §3.2）: 再生範囲・ループ再生・キーの範囲・サムネイル。

- 再生は Maya 標準の `playbackOptions` + `play` だけ（dx11Shader・Render Override は使わない）。**元の再生範囲・時刻・ループ設定は止めたときに戻す**
- サムネイルは FacialController の `thumbnails.render_with_playblast`（1 フレームのオフスクリーン playblast）を流用する。
  ビューポートの無い環境（mayapy）では作れない理由を返し、例外にしない
"""

from __future__ import annotations

import contextlib
import hashlib
import tempfile
from pathlib import Path
from typing import Any

from maya import cmds

from tdrive import lifecycle

from . import playstate

_OWNER = "clip"
_playing = False  # 再生を始める前の状態は playstate が持つ（合成・ショットと共有。止めたら、最後の 1 人が元へ戻す）


# ------------------------------------------------------------------ 再生範囲
def playback_range() -> tuple[int, int]:
    """タイムスライダーの再生範囲（min, max）。"""
    return int(cmds.playbackOptions(query=True, minTime=True)), int(cmds.playbackOptions(query=True, maxTime=True))


def set_slider_range(start: int, end: int) -> None:
    """再生範囲とアニメーション範囲をクリップの範囲にする（戻さない。「範囲をタイムスライダーに」）。"""
    cmds.playbackOptions(minTime=start, maxTime=end, animationStartTime=start, animationEndTime=end)
    cmds.currentTime(start, edit=True)


def keyed_range(root: str) -> tuple[int, int] | None:
    """ルート以下のジョイントにキーがある範囲（無ければ None）。「現在の範囲から追加」のヒント用。"""
    if not root or not cmds.objExists(root):
        return None
    nodes = [root] + (cmds.listRelatives(root, allDescendents=True, type="joint", fullPath=True) or [])
    times = cmds.keyframe(nodes, query=True, timeChange=True) or []
    return (int(min(times)), int(max(times))) if times else None


# ------------------------------------------------------------------ 再生
_applied: tuple | None = None  # 直近の ▶ で当てた範囲 / ループ（これと違えば、ユーザーが変えた）


def _range_state() -> tuple:
    return (cmds.playbackOptions(query=True, minTime=True), cmds.playbackOptions(query=True, maxTime=True), cmds.playbackOptions(query=True, loop=True))


def is_playing() -> bool:
    return _playing


def play(clip: dict[str, Any]) -> None:
    """クリップの範囲でループ再生する。すでに再生中なら、元の状態を保ったまま範囲だけ切り替える。"""
    global _playing, _applied
    if _playing and _applied is not None and _range_state() != _applied:
        playstate.rebase(_OWNER)  # 前回の ▶ のあとにユーザーが範囲 / ループを変えていた: 「直前の値」を元にする（AN-F-42）
    if not _playing:
        playstate.acquire(_OWNER)
        _playing = True
        lifecycle.on_reload(stop)  # リロードのとき、再生範囲を戻す
    # ループのクリップは最後のフレームを再生範囲に入れない（最後と最初が同じポーズのループで 1 フレーム止まって見えるのを防ぐ。Unity のループと同じ周期 = 長さ。SP-8）
    last = clip["end"] - 1 if clip["loop"] and clip["end"] > clip["start"] else clip["end"]
    cmds.playbackOptions(minTime=clip["start"], maxTime=last, animationStartTime=clip["start"], animationEndTime=clip["end"],
                         loop="continuous" if clip["loop"] else "once")
    cmds.currentTime(clip["start"], edit=True)
    _applied = _range_state()
    with contextlib.suppress(RuntimeError):
        cmds.play(forward=True)


def forget() -> None:
    """再生前の状態の控えを捨てる（別のシーンを開いた・新しいシーンにしたとき。前のシーンの範囲・時刻を書き戻さない）。"""
    global _playing, _applied
    _playing = False
    _applied = None
    playstate.forget()


def stop() -> None:
    """再生を止めて、元の再生範囲・時刻・ループ設定に戻す。再生中でなければ何もしない。"""
    global _playing, _applied
    with contextlib.suppress(RuntimeError):
        cmds.play(state=False)
    if not _playing:
        return
    _playing = False
    _applied = None
    playstate.release_and_restore(_OWNER)


# ------------------------------------------------------------------ サムネイル
def thumb_dir(model_id: str) -> Path:
    return Path(tempfile.gettempdir()) / "tdrive_anim_thumbs" / model_id


def thumb_path(model_id: str, clip: dict[str, Any]) -> Path:
    """キャッシュの鍵: 名前 + 範囲（範囲を変えると古い画像は使われない）。ポーズ（キー）を直しただけでは変わらないので、古い画像は右クリックの「サムネイルを作り直す」で撮り直す（UX-15）。"""
    key = hashlib.sha1(f"{clip['name']}|{clip['start']}|{clip['end']}".encode()).hexdigest()[:10]
    return thumb_dir(model_id) / f"{clip['name']}_{key}.png"


def can_thumbnail() -> tuple[bool, str]:
    try:
        from tdrive_facial import thumbnails
    except Exception as exc:  # noqa: BLE001  FacialController が読めなくてもクリップは使える
        return False, f"サムネイルの部品を読み込めません: {exc}"
    return thumbnails.can_capture()


def make_thumbnail(model_id: str, clip: dict[str, Any], size: int = 96, force: bool = False) -> tuple[Path | None, str]:
    """クリップの先頭フレームの画像を 1 枚作る。(パス, 理由)。作れない環境では (None, 理由)。Maya の Undo・選択・時刻は変えない。
    `force` = 作ってある画像があっても撮り直す。"""
    ok, why = can_thumbnail()
    if not ok:
        return None, "この環境ではサムネイルを作れません: " + why
    from tdrive_facial import thumbnails, viewport

    path = thumb_path(model_id, clip)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and not force:
        return path, ""
    if path.is_file():
        path.unlink()
    panel = viewport.pick_panel()
    cam = cmds.modelPanel(panel, query=True, camera=True)
    t0 = cmds.currentTime(query=True)
    was = cmds.undoInfo(query=True, state=True)
    try:
        cmds.undoInfo(stateWithoutFlush=False)
        cmds.currentTime(clip["start"], edit=True)
        ok = thumbnails.render_with_playblast(cam, path, size)
    finally:
        cmds.currentTime(t0, edit=True)
        if was:
            cmds.undoInfo(stateWithoutFlush=True)
    return (path, "") if ok else (None, "サムネイルを作れませんでした")
