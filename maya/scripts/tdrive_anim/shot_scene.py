"""ショットの Maya 側（docs/20 §5.2）: `tdShot_<Shot>` グループ・Camera Sequencer・イベントのロケータ・キャラクターのクリップ列の合成・再生・動画。

- `td` 接頭辞のノード（`tdShot_*` `tdAnimComp_*` `tdShotHand_*`）は**出力の対象外**（プレビュー用。docs/20 §2.2）
- カメラの区間は Maya 標準の Camera Sequencer（`shot` ノード `tdShot_<Shot>_<n>`）に同期する。ビューポートでの切り替えはシーケンサー再生で確認する
- キャラクターのクリップ列は `composition.compose`（`shot_<Shot>_<ModelId>` の名前）。ショットの範囲の先頭から並べる。手で打つキー（口パク・視線など）は
  合成の上の animLayer `tdShotHand_<Shot>_<ModelId>` に打つ（出力時はベイクされる）
- イベント `EVT_<キー>` は `tdShot_<Shot>` の下のロケータ。**可視性が 0 → 1 に切り替わるフレームがイベントの時刻**（ステップキー。ベイクしても残る。docs/20 に追記）
- dx11Shader・Render Override は使わない
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Any

from maya import cmds

from tdrive import project

from . import composition as K
from .core import animset as A
from .core import sequence as Seq
from .core import shot as SH

GROUP_PREFIX = "tdShot_"
HAND_PREFIX = "tdShotHand_"
DEFAULT_CAMERAS = {"persp", "top", "front", "side"}


def group_name(shot: str) -> str:
    return f"{GROUP_PREFIX}{shot}"


def comp_name(shot: str, suffix: str) -> str:
    return f"shot_{shot}_{suffix}"


# ------------------------------------------------------------------ グループ・状態
def ensure_group(shot: dict[str, Any]) -> str:
    g = group_name(shot["shot"])
    if not cmds.objExists(g):
        g = cmds.group(empty=True, name=g)
    return g


def _remember_playback(g: str) -> None:
    if not cmds.attributeQuery("tdPlayback", node=g, exists=True):
        cmds.addAttr(g, longName="tdPlayback", dataType="string")
        state = {"min": cmds.playbackOptions(query=True, minTime=True), "max": cmds.playbackOptions(query=True, maxTime=True),
                 "start": cmds.playbackOptions(query=True, animationStartTime=True), "end": cmds.playbackOptions(query=True, animationEndTime=True),
                 "loop": cmds.playbackOptions(query=True, loop=True), "time": cmds.currentTime(query=True)}
        cmds.setAttr(f"{g}.tdPlayback", json.dumps(state), type="string")


def apply_range(shot: dict[str, Any]) -> None:
    """再生範囲をショットの範囲にする（元の範囲は覚える。`remove` で戻る）。"""
    g = ensure_group(shot)
    _remember_playback(g)
    lo, hi = shot["range"]
    cmds.playbackOptions(minTime=lo, maxTime=hi, animationStartTime=lo, animationEndTime=hi)
    cmds.currentTime(lo, edit=True)


def scene_cameras() -> list[str]:
    """ユーザーのカメラ（transform。標準の persp / top / front / side は除く）。"""
    out = []
    for shape in cmds.ls(type="camera") or []:
        tr = (cmds.listRelatives(shape, parent=True) or [shape])[0]
        if tr not in DEFAULT_CAMERAS and tr not in out:
            out.append(tr)
    return out


# ------------------------------------------------------------------ Camera Sequencer
def sync_cameras(shot: dict[str, Any]) -> list[str]:
    """カメラの区間を Camera Sequencer の shot ノードに作り直す。存在しないカメラは飛ばす。戻り値: 警告。"""
    name = shot["shot"]
    for sn in cmds.ls(f"{GROUP_PREFIX}{name}_*", type="shot") or []:
        cmds.delete(sn)
    warnings = []
    for i, cam in enumerate(shot["cameras"]):
        if not cmds.objExists(cam["node"]):
            warnings.append(f"カメラ {cam['node']} がシーンにありません")
            continue
        cmds.shot(f"{GROUP_PREFIX}{name}_{i}", startTime=cam["start"], endTime=cam["end"], sequenceStartTime=cam["start"],
                  sequenceEndTime=cam["end"], currentCamera=cam["node"])
    return warnings


def sequencer_shots(shot_name: str) -> list[dict[str, Any]]:
    """このショットの shot ノード（区間とカメラ）。確認・テスト用。"""
    out = []
    for sn in sorted(cmds.ls(f"{GROUP_PREFIX}{shot_name}_*", type="shot") or []):
        out.append({"node": sn, "start": int(cmds.getAttr(f"{sn}.sequenceStartFrame")), "end": int(cmds.getAttr(f"{sn}.sequenceEndFrame")),
                    "camera": (cmds.listConnections(f"{sn}.currentCamera", source=True, destination=False) or [""])[0]})
    return out


# ------------------------------------------------------------------ イベント
def sync_events(shot: dict[str, Any]) -> list[str]:
    """イベントのロケータ `EVT_<キー>` を作り直す（可視性のステップキーが時刻）。作ったロケータ名を返す。"""
    g = ensure_group(shot)
    old = [c for c in (cmds.listRelatives(g, children=True, fullPath=True) or []) if cmds.attributeQuery("tdEventFrame", node=c, exists=True)]
    if old:  # 空のリストを cmds.delete に渡さない
        cmds.delete(old)
    lo = shot["range"][0]
    made = []
    for ev in shot["events"]:
        loc = cmds.spaceLocator(name=f"EVT_{ev['key']}")[0]
        loc = cmds.parent(loc, g)[0]
        cmds.addAttr(loc, longName="tdEventFrame", attributeType="long", defaultValue=int(ev["frame"]))
        cmds.addAttr(loc, longName="tdEventKey", dataType="string")
        cmds.setAttr(f"{loc}.tdEventKey", ev["key"], type="string")
        cmds.setKeyframe(loc, attribute="visibility", time=min(lo, ev["frame"]) - 1, value=0)
        cmds.setKeyframe(loc, attribute="visibility", time=ev["frame"], value=1)
        cmds.keyTangent(loc, attribute="visibility", inTangentType="step", outTangentType="step")
        made.append(loc)
    return made


def event_frames(shot_name: str) -> dict[str, int]:
    """シーンのロケータから読んだイベント（キー → 可視性が 1 になる最初のフレーム）。出力と同じ読み方。"""
    g = group_name(shot_name)
    out = {}
    for c in (cmds.listRelatives(g, children=True, fullPath=True) or []) if cmds.objExists(g) else []:
        if not cmds.attributeQuery("tdEventFrame", node=c, exists=True):
            continue
        times = cmds.keyframe(c, attribute="visibility", query=True, timeChange=True) or []
        vals = cmds.keyframe(c, attribute="visibility", query=True, valueChange=True) or []
        on = [t for t, v in zip(times, vals) if v >= 0.5]
        out[cmds.getAttr(f"{c}.tdEventKey")] = int(min(on)) if on else int(cmds.getAttr(f"{c}.tdEventFrame"))
    return out


# ------------------------------------------------------------------ キャラクター
def animset_for(model_id: str, current: dict[str, Any] | None) -> dict[str, Any]:
    """キャラクターの animset: 開いているものが同じ ModelId ならそれ（未保存の変更を含む）、無ければ anim/<ModelId>/animset.json。"""
    if current is not None and current.get("modelId") == model_id:
        return current
    return A.load(project.anim_dir() / model_id / "animset.json")


def character_root(ch: dict[str, Any], animset: dict[str, Any]) -> str:
    ns = ch.get("namespace") or ""
    root = animset["skeletonRoot"]
    return f"{ns}:{root}" if ns and not root.startswith(f"{ns}:") else root


def character_joints(ch: dict[str, Any], animset: dict[str, Any]) -> list[str]:
    root = character_root(ch, animset)
    if not cmds.objExists(root):
        raise ValueError(f"キャラクター {ch['modelId']} のスケルトン {root} がシーンにありません")
    return [root] + (cmds.listRelatives(root, allDescendents=True, type="joint") or [])


def compose_characters(shot: dict[str, Any], current: dict[str, Any] | None = None) -> list[K.Result]:
    """各キャラクターのクリップ列をショットの範囲の先頭から並べて合成する。列が空・ジョイントが無いキャラクターは飛ばして警告に。"""
    fps = shot["frameRate"]
    lo = shot["range"][0]
    ensure_group(shot)
    results = []
    for ch, fname in SH.character_files(shot):
        if not ch["sequence"]:
            continue
        aset = animset_for(ch["modelId"], current)
        clips = {c["name"]: c for c in aset["clips"]}
        pls = Seq.from_rows(ch["sequence"], clips, fps)
        for p in pls:
            p.start += lo
            p.end += lo
        suffix = fname.split("__", 1)[1]
        results.append(K.compose(comp_name(shot["shot"], suffix), pls, character_joints(ch, aset), fps, playback=False))
    return results


def compositions(shot_name: str) -> list[str]:
    return [n for n in K.active() if n.startswith(f"shot_{shot_name}_")]


def hand_layer(shot_name: str, model_id: str) -> str:
    """手付けキー用の animLayer を作る（あれば何もしない）。キーを打ちたいコントローラを選んで、レイヤーに追加して使う。"""
    name = f"{HAND_PREFIX}{shot_name}_{model_id}"
    if not cmds.objExists(name):
        name = cmds.animLayer(name)
    return name


def build(shot: dict[str, Any], current: dict[str, Any] | None = None) -> dict[str, Any]:
    """ショットをシーンに反映する: グループ・再生範囲・カメラ区間・イベント・キャラクターの合成。戻り値: 結果と警告。"""
    ensure_group(shot)
    try:
        apply_range(shot)
        warnings = sync_cameras(shot)
        events = sync_events(shot)
        comps = compose_characters(shot, current)
    except Exception:
        remove(shot["shot"])  # 途中で失敗したら、ここまでに作ったものを全部戻す（ジョイントを合成につないだまま残さない）
        raise
    return {"events": len(events), "cameras": len(sequencer_shots(shot["shot"])), "compositions": [c.name for c in comps], "warnings": warnings}


def remove(shot_name: str, delete_group: bool = False) -> int:
    """ショットの合成を外し、Camera Sequencer の shot・イベントのロケータを消して、再生範囲を戻す。元のカーブ・カメラは壊さない。外した合成の数。"""
    n = sum(K.remove(c) for c in compositions(shot_name))
    for sn in cmds.ls(f"{GROUP_PREFIX}{shot_name}_*", type="shot") or []:
        cmds.delete(sn)
    g = group_name(shot_name)
    if cmds.objExists(g):
        locs = [c for c in (cmds.listRelatives(g, children=True, fullPath=True) or []) if cmds.attributeQuery("tdEventFrame", node=c, exists=True)]
        if locs:
            cmds.delete(locs)
        if cmds.attributeQuery("tdPlayback", node=g, exists=True):
            with contextlib.suppress(ValueError):
                st = json.loads(cmds.getAttr(f"{g}.tdPlayback"))
                cmds.playbackOptions(minTime=st["min"], maxTime=st["max"], animationStartTime=st["start"], animationEndTime=st["end"], loop=st["loop"])
                cmds.currentTime(st["time"], edit=True)
            cmds.deleteAttr(f"{g}.tdPlayback")
        if delete_group:
            cmds.delete(g)
    return n


# ------------------------------------------------------------------ 再生・動画
def play(shot: dict[str, Any]) -> None:
    lo, hi = shot["range"]
    cmds.playbackOptions(minTime=lo, maxTime=hi, loop="once")
    cmds.currentTime(lo, edit=True)
    with contextlib.suppress(RuntimeError):
        cmds.play(forward=True)


def movie_path(shot_name: str) -> Path:
    return project.capture_dir() / "shots" / f"{shot_name}.mov"


def capture_movie(shot: dict[str, Any]) -> tuple[Path | None, str]:
    """ショットの範囲を playblast で動画にする（`captures/shots/<Shot>.mov`。Git 管理外）。ビューポートが無い環境では作らず理由を返す。"""
    try:
        from tdrive_facial import thumbnails
    except Exception as exc:  # noqa: BLE001
        return None, f"動画の部品を読み込めません: {exc}"
    ok, why = thumbnails.can_capture()
    if not ok:
        return None, f"この環境では動画を撮れません: {why}"
    path = movie_path(shot["shot"])
    path.parent.mkdir(parents=True, exist_ok=True)
    lo, hi = shot["range"]
    try:
        cmds.playblast(startTime=lo, endTime=hi, format="qt", compression="H.264", filename=str(path.with_suffix("")).replace("\\", "/"),
                       forceOverwrite=True, viewer=False, showOrnaments=False, percent=100, offScreen=True, sequenceTime=bool(cmds.ls(type="shot")))
    except RuntimeError as exc:
        return None, f"動画を撮れませんでした: {exc}"
    return (path, "") if path.exists() else (None, "動画のファイルができませんでした（コーデックが無い可能性があります）")
