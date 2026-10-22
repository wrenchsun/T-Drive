"""Unity 用の分割出力（docs/20 §6）: モデル `SK_<ModelId>.fbx` とクリップごとの `A_<ModelId>_<動作>.fbx`。

- **開いているシーンは壊さない**: 一時ファイルに書き出して、別プロセスの mayapy（`tools/export_fbx_batch.py` の `mode: "anim"`）が
  タスクごとにそのファイルを開き直し、整形して FBX を書く（ベイクはシーンを壊すので、タスクごとに新しく開く）
- モデル: Toon の `export_in_place` に相乗り（頂点カラー `tdToonMask`・UV `tdSmoothNormal`・参照のフラット化・`_tdToon` を元に戻す）。
  メッシュ + スケルトンのルート以下だけ。アニメーションは含めない・バインドポーズで書く
- アニメーション: ルート以下のジョイントだけ。クリップ範囲で `bakeResults`（コントローラやコンストレイントは出さない）、
  `rootMotion=none` は Hips の XZ をクリップ開始フレームの値に固定、範囲の先頭を 0 フレームへ寄せ、1 テイク（Maya の既定の名前 `Take 001`。D-Drive は最初の埋め込みクリップをファイル名で取り込むので、テイク名は使われない。名前付きのテイクを足すと 2 テイクになる）
- dx11Shader・Render Override は使わない（モデルの出力は Toon のコードを通るので、プレビュー用ノードを扱えるよう、裏のプロセスを起動する
  `tools/export_fbx_batch.py` だけがプラグインを読む。読めなくても出力は続く。開いている Maya の側では何も読まない）
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import time
import traceback
from pathlib import Path
from typing import Any

from maya import cmds, mel

from tdrive import export_job, project

from .core import animset as A
from .core import export_plan as E

TRS = ("tx", "ty", "tz", "rx", "ry", "rz", "sx", "sy", "sz")


# ------------------------------------------------------------------ 集める
def skeleton_joints(root: str) -> list[str]:
    """ルートとその子孫のジョイント（完全パス）。ルートが無ければ空。"""
    if not root or not cmds.objExists(root):
        return []
    full = cmds.ls(root, long=True)[0]
    return [full] + (cmds.listRelatives(full, allDescendents=True, type="joint", fullPath=True) or [])


def skinned_meshes(root: str) -> list[str]:
    """ルート以下のジョイントにスキンされているメッシュ（transform の短い名前）。animset の meshes が空のときの自動設定に使う。"""
    joints = set(skeleton_joints(root))
    out: list[str] = []
    for sc in cmds.ls(type="skinCluster") or []:
        infl = {cmds.ls(j, long=True)[0] for j in (cmds.skinCluster(sc, query=True, influence=True) or [])}
        if not infl & joints:
            continue
        for shape in cmds.skinCluster(sc, query=True, geometry=True) or []:
            parent = (cmds.listRelatives(shape, parent=True) or [shape])[0]
            if parent not in out:
                out.append(parent)
    return out


def fingerprint(clip: dict[str, Any], animset: dict[str, Any]) -> str:
    """クリップの指紋: 属性 + フレームレート + ルート + 範囲内の全アニメーションカーブのキー。変わったかどうかの判定にだけ使う。"""
    h = hashlib.sha1()
    h.update(json.dumps({k: clip[k] for k in ("name", "start", "end", "rootMotion")} | {"fps": animset["frameRate"], "root": animset["skeletonRoot"]},
                        sort_keys=True).encode())
    s, e = clip["start"], clip["end"]
    for curve in sorted(cmds.ls(type="animCurve") or []):
        t = cmds.keyframe(curve, query=True, time=(s, e), timeChange=True) or []
        if not t:
            continue
        v = cmds.keyframe(curve, query=True, time=(s, e), valueChange=True) or []
        h.update(curve.encode())
        h.update(json.dumps([[round(a, 5), round(b, 5)] for a, b in zip(t, v)]).encode())
    return h.hexdigest()


# ------------------------------------------------------------------ 書き出し（別プロセスの mayapy で呼ぶ。開いているシーンを直接壊す）
def _to_bind_pose(joints: list[str]) -> None:
    """ジョイントの入力のつながり（キー・コンストレイント）を切り、バインドポーズへ戻す。"""
    for j in joints:
        for ch in TRS:
            plug = f"{j}.{ch}"
            for src in cmds.listConnections(plug, source=True, destination=False, plugs=True) or []:
                with contextlib.suppress(RuntimeError):
                    cmds.disconnectAttr(src, plug)
    poses = cmds.ls(type="dagPose") or []
    bind = [p for p in poses if cmds.getAttr(f"{p}.bindPose")]
    for p in bind[:1]:
        with contextlib.suppress(RuntimeError):
            cmds.dagPose(joints[0], restore=True, name=p)


def export_model_in_place(animset: dict[str, Any], path: str | Path) -> dict[str, Any]:
    """`SK_<ModelId>.fbx`（メッシュ + スキン + スケルトン、アニメ無し）を書く。破壊的（mayapy 専用）。"""
    from tdrive_toon import export as toon_export

    joints = skeleton_joints(animset["skeletonRoot"])
    if not joints:
        raise RuntimeError(f"スケルトンのルート {animset['skeletonRoot']} がシーンにありません")
    meshes = [m for m in animset["meshes"] if cmds.objExists(m)] or skinned_meshes(animset["skeletonRoot"])
    if not meshes:
        raise RuntimeError("書き出すメッシュがありません（animset の meshes が空で、スキンされたメッシュも見つかりません）")
    cmds.currentUnit(time={30: "ntsc", 60: "ntscf"}[animset["frameRate"]], updateAnimation=False)
    _to_bind_pose(joints)
    return toon_export.export_in_place(meshes, path, roots=[cmds.ls(animset["skeletonRoot"], long=True)[0]], animation=False)


def export_clip_in_place(animset: dict[str, Any], clip: dict[str, Any], path: str | Path) -> dict[str, Any]:
    """`A_<ModelId>_<動作>.fbx`（ジョイントのみ・ベイク済み・1 テイク）を書く。破壊的（mayapy 専用）。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    cmds.loadPlugin("fbxmaya", quiet=True)
    root = animset["skeletonRoot"]
    joints = skeleton_joints(root)
    if not joints:
        raise RuntimeError(f"スケルトンのルート {root} がシーンにありません")
    s, e = int(clip["start"]), int(clip["end"])
    hips = joints[0]
    fixed = {ax: cmds.getAttr(f"{hips}.translate{ax}", time=s) for ax in "XZ"} if clip["rootMotion"] == "none" else {}
    cmds.bakeResults(joints, time=(s, e), simulation=True, sampleBy=1, disableImplicitControl=True, preserveOutsideKeys=False,
                     sparseAnimCurveBake=False, minimizeRotation=True, controlPoints=False, shape=False, attribute=list(TRS))
    cons = cmds.ls(joints, dagObjects=True, type="constraint", long=True) or []
    if cons:  # 空のリストを cmds.delete に渡さない。コンストレイントは書き出さない
        cmds.delete(cons)
    for ax, v in fixed.items():  # rootMotion = none: Hips の XZ をクリップ開始の値に固定（Unity の Original 相当）
        plug = f"{hips}.translate{ax}"
        with contextlib.suppress(RuntimeError):
            cmds.cutKey(plug, clear=True)
            cmds.setAttr(plug, v)
    cmds.keyframe(joints, edit=True, relative=True, timeChange=-s)  # 範囲の先頭を 0 フレームへ
    length = e - s
    cmds.playbackOptions(minTime=0, maxTime=length, animationStartTime=0, animationEndTime=length)
    cmds.currentTime(0, edit=True)
    cmds.select(hips, replace=True)
    for cmd in (
        "FBXResetExport", "FBXExportSkins -v false", "FBXExportShapes -v false", "FBXExportConstraints -v false",
        "FBXExportCameras -v false", "FBXExportLights -v false", "FBXExportInputConnections -v false",
        "FBXExportEmbeddedTextures -v false", "FBXExportUpAxis y", "FBXExportAnimationOnly -v false",
        "FBXExportBakeComplexAnimation -v true", f"FBXExportBakeComplexStart -v 0", f"FBXExportBakeComplexEnd -v {length}",
        "FBXExportBakeComplexStep -v 1", "FBXExportSplitAnimationIntoTakes -clear",
    ):
        mel.eval(cmd)
    mel.eval(f'FBXExport -f "{path.as_posix()}" -s')
    return {"path": str(path), "frames": length + 1, "joints": len(joints)}


def run_tasks(args: dict[str, Any]) -> dict[str, Any]:
    """別プロセス（tools/export_fbx_batch.py の mode = anim）から呼ばれる。タスクごとにシーンを開き直し、失敗しても残りを続ける。"""
    cmds.loadPlugin("fbxmaya", quiet=True)
    animset = args["animset"]
    results = []
    progress = Path(args["result"]).with_name("progress.txt")
    for task in args["tasks"]:
        t0 = time.perf_counter()
        rec: dict[str, Any] = {"type": task["type"], "name": task.get("clip", "model"), "out": task["out"], "ok": False}
        try:
            cmds.file(args["scene"], open=True, force=True)
            if task["type"] == "model":
                rec.update(export_model_in_place(animset, task["out"]))
            else:
                clip = A.find_clip(animset, task["clip"])
                if clip is None:
                    raise RuntimeError(f"クリップがありません: {task['clip']}")
                rec.update(export_clip_in_place(animset, clip, task["out"]))
            out = Path(task["out"])
            rec["ok"] = out.is_file() and out.stat().st_size > 0
            rec["size"] = out.stat().st_size if out.is_file() else 0
            if not rec["ok"]:
                rec["error"] = "ファイルが書かれませんでした"
        except Exception as exc:  # noqa: BLE001  1 つの失敗で残りを止めない
            rec["error"] = f"{exc}"
            rec["trace"] = traceback.format_exc()[-1500:]
        rec["seconds"] = round(time.perf_counter() - t0, 2)
        results.append(rec)
        with progress.open("a", encoding="utf-8") as f:
            f.write(f"{rec['name']}\n")
    return {"results": results}


# ------------------------------------------------------------------ 呼び出す側（開いている Maya）
class SplitJob(export_job.BatchJob):
    """分割出力のジョブ。生成すると開始 → poll() が True になったら finish()。"""

    def __init__(self, animset: dict[str, Any], model_dir: Path, anim_dir: Path, clips: list[str], model: bool = True,
                 animset_path: Path | None = None) -> None:
        self.animset = json.loads(json.dumps(animset))  # 出力中に編集しても食い違わないよう、開始時の内容を持つ
        self.model_dir, self.anim_dir = Path(model_dir), Path(anim_dir)
        self.animset_path = animset_path
        self.fingerprints = {n: fingerprint(A.find_clip(animset, n), animset) for n in clips}
        self.tasks: list[dict[str, Any]] = []
        if model:
            self.tasks.append({"type": "model", "out": str(self.model_dir / E.model_file(animset["modelId"]))})
        for n in clips:
            self.tasks.append({"type": "clip", "clip": n, "out": str(self.anim_dir / E.clip_file(n))})
        self.humanoid: dict[str, Any] | None = None
        if model:
            from . import scene_naming as SN

            self.humanoid = SN.humanoid_for(animset["modelId"], animset["skeletonRoot"])
        self.total = len(self.tasks)
        super().__init__({"mode": "anim", "animset": self.animset, "tasks": self.tasks}, prefix="tdrive_anim_export_")

    def progress(self) -> tuple[int, int]:
        """(終わったタスク数, 全タスク数)。"""
        try:
            done = len((self.work / "progress.txt").read_text(encoding="utf-8").splitlines())
        except OSError:
            done = 0
        return done, self.total

    def finish(self) -> list[dict[str, Any]]:
        """結果を受け取り、付帯 JSON（humanoid.json / animset.json）と「変更のあるものだけ」の記録を書く。タスクごとの結果を返す。"""
        res = self.result()["results"]
        if any(r["type"] == "model" and r["ok"] for r in res):
            self.model_dir.mkdir(parents=True, exist_ok=True)
            if self.humanoid is not None:
                from .core import humanoid_map as H

                H.save(self.model_dir / "humanoid.json", self.humanoid)
            A.save(self.model_dir / "animset.json", self.animset)
        if self.animset_path is not None:
            sp = E.state_path(self.animset_path)
            state = E.load_state(sp)
            for r in res:
                if r["type"] == "clip" and r["ok"]:
                    E.record(state, r["name"], self.fingerprints[r["name"]], self.anim_dir)
            E.save_state(sp, state)
        return res


def project_config() -> dict[str, Any]:
    return project.read_config()
