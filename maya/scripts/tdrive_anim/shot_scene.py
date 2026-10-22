"""ショットの Maya 側（docs/20 §5.2）: `tdShot_<Shot>` グループ・Camera Sequencer・イベントのロケータ・キャラクターのクリップ列の合成・再生・動画。

- `td` 接頭辞のノード（`tdShot_*` `tdAnimComp_*` `tdShotHand_*`）は**出力の対象外**（プレビュー用。docs/20 §2.2）
- カメラの区間は Maya 標準の Camera Sequencer（`shot` ノード `tdShot_<Shot>_<n>`）に同期する。ビューポートでの切り替えはシーケンサー再生で確認する
- キャラクターのクリップ列は `composition.compose`（`shot_<Shot>_<ModelId>` の名前）。ショットの範囲の先頭から並べる。手で打つキー（口パク・視線など）は
  合成の上の animLayer `tdShotHand_<Shot>_<ModelId>` に打つ予定だったが、**準備中**（合成中はコントローラのキーがジョイントに届かない。docs/21 SP-2）
- イベント `EVT_<キー>` は `tdShot_<Shot>` の下のロケータ。**カスタムアトリビュート `ddEvent`（整数）が 0 → 1 に切り替わるフレームがイベントの時刻**
  （D-Drive の「EVT_ ロケーターにアトリビュートを足してキーを打つ」方式。ステップキー。docs/26 §5.5）
- dx11Shader・Render Override は使わない
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Any

from maya import cmds

from tdrive import project

from . import clips as MC
from . import composition as K
from . import playstate
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


def _owner(shot_name: str) -> str:
    return "shot:" + shot_name


def _remember_playback(g: str, shot_name: str) -> None:
    if not cmds.attributeQuery("tdPlayback", node=g, exists=True):
        cmds.addAttr(g, longName="tdPlayback", dataType="string")
        cmds.setAttr(f"{g}.tdPlayback", json.dumps(playstate.acquire(_owner(shot_name))), type="string")


def apply_range(shot: dict[str, Any]) -> None:
    """再生範囲をショットの範囲にする（元の範囲は覚える。`remove` で戻る）。"""
    MC.stop()  # クリップ再生中なら先に止めて元の範囲へ戻す（控えが混ざらないように。SF-8）
    g = ensure_group(shot)
    _remember_playback(g, shot["shot"])
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
    """イベントのロケータ `EVT_<キー>` を作り直す（`ddEvent` が 0 → 1 に切り替わるフレームが時刻）。作ったロケータ名を返す。"""
    g = ensure_group(shot)
    old = [c for c in (cmds.listRelatives(g, children=True, fullPath=True) or []) if cmds.attributeQuery("tdEventKey", node=c, exists=True)]
    if old:  # 空のリストを cmds.delete に渡さない
        cmds.delete(old)
    lo = shot["range"][0]
    made = []
    for ev in shot["events"]:
        loc = cmds.spaceLocator(name=f"EVT_{ev['key']}")[0]
        loc = cmds.parent(loc, g)[0]
        cmds.addAttr(loc, longName="tdEventKey", dataType="string")
        cmds.setAttr(f"{loc}.tdEventKey", ev["key"], type="string")
        cmds.addAttr(loc, longName="ddEvent", attributeType="long", defaultValue=0, keyable=True)
        if ev["frame"] > lo:  # ショットの範囲の外にキーを置かない（同じショットの全ファイルで開始〜終了を揃える）
            cmds.setKeyframe(loc, attribute="ddEvent", time=lo, value=0)
        cmds.setKeyframe(loc, attribute="ddEvent", time=ev["frame"], value=1)
        cmds.keyTangent(loc, attribute="ddEvent", inTangentType="step", outTangentType="step")
        made.append(loc)
    return made


def event_locators(shot_name: str) -> list[str]:
    g = group_name(shot_name)
    if not cmds.objExists(g):
        return []
    return [c for c in (cmds.listRelatives(g, children=True, fullPath=True) or []) if cmds.attributeQuery("tdEventKey", node=c, exists=True)]


def event_frames(shot_name: str) -> dict[str, int]:
    """シーンのロケータから読んだイベント（キー → `ddEvent` が 1 になる最初のフレーム）。Unity 側と同じ読み方。"""
    out = {}
    for c in event_locators(shot_name):
        times = cmds.keyframe(c, attribute="ddEvent", query=True, timeChange=True) or []
        vals = cmds.keyframe(c, attribute="ddEvent", query=True, valueChange=True) or []
        on = [t for t, v in zip(times, vals) if v >= 0.5]
        if on:
            out[cmds.getAttr(f"{c}.tdEventKey")] = int(min(on))
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


def compose_characters(shot: dict[str, Any], current: dict[str, Any] | None = None, animsets: dict[str, dict[str, Any]] | None = None,
                       only: int | None = None) -> list[K.Result]:
    """各キャラクターのクリップ列をショットの範囲の先頭から並べて合成する。列が空のキャラクターは飛ばす。
    `animsets`（ModelId → animset）を渡すとそれを使う（別プロセスの書き出し用）。`only` はそのインデックスのキャラクターだけ。"""
    fps = shot["frameRate"]
    lo = shot["range"][0]
    ensure_group(shot)
    results = []
    for i, (ch, fname) in enumerate(SH.character_files(shot)):
        if not ch["sequence"] or (only is not None and i != only):
            continue
        aset = animsets[ch["modelId"]] if animsets else animset_for(ch["modelId"], current)
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
    """手付けキー用の animLayer を作る（あれば何もしない）。**準備中**（docs/21 SP-2）: 合成はジョイントのつながりを切り離して
    サンプルしたカーブでつなぎ直すので、コントローラに打ったレイヤーのキーはジョイントに届かず、出力にも入らない。UI からは呼ばない。"""
    name = f"{HAND_PREFIX}{shot_name}_{model_id}"
    if not cmds.objExists(name):
        name = cmds.animLayer(name)
    return name


def is_reflected(shot_name: str) -> bool:
    """このショットがシーンに作ったもの（合成・shot ノード・イベントのロケータ・再生範囲の控え）が残っているか。"""
    g = group_name(shot_name)
    return bool(compositions(shot_name) or cmds.ls(f"{GROUP_PREFIX}{shot_name}_*", type="shot")
                or (cmds.objExists(g) and (event_locators(shot_name) or cmds.attributeQuery("tdPlayback", node=g, exists=True))))


def build(shot: dict[str, Any], current: dict[str, Any] | None = None) -> dict[str, Any]:
    """ショットをシーンに反映する: グループ・再生範囲・カメラ区間・イベント・キャラクターの合成。戻り値: 結果と警告。
    **Maya の Undo には積まない**（グループ・shot ノード・ロケータ・合成が別々に戻って半端になるため。戻すのは「合成を外す」。SF-4 / SF-6）。"""
    with K.undo_off():
        return _build(shot, current)


def _build(shot: dict[str, Any], current: dict[str, Any] | None) -> dict[str, Any]:
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
    with K.undo_off():
        return _remove(shot_name, delete_group)


def _remove(shot_name: str, delete_group: bool) -> int:
    n = sum(K.remove(c) for c in compositions(shot_name))
    for sn in cmds.ls(f"{GROUP_PREFIX}{shot_name}_*", type="shot") or []:
        cmds.delete(sn)
    g = group_name(shot_name)
    if cmds.objExists(g):
        locs = event_locators(shot_name)
        if locs:
            cmds.delete(locs)
        if cmds.attributeQuery("tdPlayback", node=g, exists=True):
            with contextlib.suppress(ValueError, KeyError):
                playstate.release_and_restore(_owner(shot_name), json.loads(cmds.getAttr(f"{g}.tdPlayback")))
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
