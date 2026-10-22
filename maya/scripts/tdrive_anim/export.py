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
    h.update(json.dumps({k: clip[k] for k in ("name", "start", "end", "rootMotion")} | {"fps": animset["frameRate"], "root": A.root_name(animset)},
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

    root = A.root_name(animset)
    joints = skeleton_joints(root)
    if not joints:
        raise RuntimeError(f"スケルトンのルート {root} がシーンにありません")
    meshes = [m for m in animset["meshes"] if cmds.objExists(m)] or skinned_meshes(root)
    if not meshes:
        raise RuntimeError("書き出すメッシュがありません（animset の meshes が空で、スキンされたメッシュも見つかりません）")
    cmds.currentUnit(time={30: "ntsc", 60: "ntscf"}[animset["frameRate"]], updateAnimation=False)
    _to_bind_pose(joints)
    return toon_export.export_in_place(meshes, path, roots=[cmds.ls(root, long=True)[0]], animation=False)


def export_clip_in_place(animset: dict[str, Any], clip: dict[str, Any], path: str | Path) -> dict[str, Any]:
    """`A_<ModelId>_<動作>.fbx`（ジョイントのみ・ベイク済み・1 テイク）を書く。破壊的（mayapy 専用）。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    cmds.loadPlugin("fbxmaya", quiet=True)
    root = A.root_name(animset)
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
    ns = animset.get("namespace") or ""
    if ns:  # FBX のジョイント名にネームスペースを付けない（SK_<ModelId>.fbx と同じ名前にする）
        with contextlib.suppress(RuntimeError):
            cmds.namespace(removeNamespace=ns, mergeNamespaceWithRoot=True)
        hips = hips.replace(f"{ns}:", "")
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

            self.humanoid = SN.humanoid_for(animset["modelId"], animset["skeletonRoot"], animset.get("namespace", ""))
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


# ------------------------------------------------------------------ ショットの出力（docs/20 §5.3、AN-8）
SHOT_BAKE = (
    "FBXResetExport", "FBXExportSkins -v false", "FBXExportShapes -v false", "FBXExportConstraints -v false", "FBXExportLights -v false",
    "FBXExportInputConnections -v false", "FBXExportEmbeddedTextures -v false", "FBXExportUpAxis y", "FBXExportAnimationOnly -v false",
    "FBXExportBakeComplexAnimation -v true", "FBXExportBakeComplexStep -v 1", "FBXExportSplitAnimationIntoTakes -clear",
)


def _shot_fbx_options(lo: int, hi: int, cameras: bool) -> None:
    """D-Drive のカットシーン FBX のオプション（docs/26 §5.1 手順 3）: Animation ON / Bake（開始〜終了 = ショットの全フレーム、Step 1）/
    Cameras は引数 / Lights OFF / Embed Media OFF / Input Connections OFF / 単位 cm・Y-up。"""
    for cmd in SHOT_BAKE:
        mel.eval(cmd)
    mel.eval(f"FBXExportCameras -v {'true' if cameras else 'false'}")
    mel.eval(f"FBXExportBakeComplexStart -v {lo}")
    mel.eval(f"FBXExportBakeComplexEnd -v {hi}")


def _collect_props(shot: dict[str, Any]) -> list[str]:
    """小道具のノード: shot.json の props にあるもの、無ければシーンの `PRP_*`（td 接頭辞のものは除く）。"""
    named = [p["node"] for p in shot.get("props", []) if p.get("node") and cmds.objExists(p["node"])]
    if named:
        return named
    out = []
    for n in cmds.ls("PRP_*", "*:PRP_*", transforms=True, long=True) or []:
        if n.split("|")[-1].startswith("td") or n in out:
            continue
        out.append(n)
    return out


def build_ubercam(shot: dict[str, Any], name: str = "Ubercam") -> str:
    """ショットのカメラの区間を、1 台のカメラに焼く（Maya の「Ubercam 作成」にあたる。範囲の全フレームで、その時刻のカメラの
    ワールド位置・向き・画角・フィルム・クリップ面を写してキーを打つ。区間の切り替わりはステップ）。カメラが無ければ ValueError。"""
    from .core import shot as SH

    lo, hi = shot["range"]
    cams = [c for c in shot["cameras"] if cmds.objExists(c["node"])]
    if not cams:
        raise ValueError("カメラの区間がありません（ショットタブでカメラを足してください）")
    first = cams[0]["node"]
    first_shape = (cmds.listRelatives(first, shapes=True, type="camera") or [first])[0]
    uber = cmds.camera(name=name)[0]
    ushape = cmds.listRelatives(uber, shapes=True, type="camera")[0]
    attrs = ("focalLength", "horizontalFilmAperture", "verticalFilmAperture", "nearClipPlane", "farClipPlane", "filmFit", "lensSqueezeRatio")
    for a in attrs:
        cmds.setAttr(f"{ushape}.{a}", cmds.getAttr(f"{first_shape}.{a}"))
    animated = ("focalLength", "horizontalFilmAperture", "verticalFilmAperture", "nearClipPlane", "farClipPlane")
    prev = None
    cuts: list[int] = []
    for f in range(lo, hi + 1):
        node = SH.camera_at(shot, f) or prev or first
        if prev is not None and node != prev:
            cuts.append(f - 1)
        prev = node
        shape = (cmds.listRelatives(node, shapes=True, type="camera") or [node])[0]
        cmds.currentTime(f, edit=True)
        cmds.xform(uber, worldSpace=True, matrix=cmds.xform(node, query=True, worldSpace=True, matrix=True))
        cmds.setKeyframe(uber, attribute=["tx", "ty", "tz", "rx", "ry", "rz"], time=f)
        for a in animated:
            cmds.setKeyframe(ushape, attribute=a, time=f, value=cmds.getAttr(f"{shape}.{a}"))
    rot = [f"{uber}.rx", f"{uber}.ry", f"{uber}.rz"]
    with contextlib.suppress(RuntimeError):
        cmds.filterCurve(rot, filter="euler")  # 回転の ±180 のとびを直す
    cmds.keyTangent(uber, inTangentType="linear", outTangentType="linear")
    cmds.keyTangent(ushape, inTangentType="linear", outTangentType="linear")
    for f in cuts:  # カメラの切り替わり: 次のフレームまで前の値を保つ
        cmds.keyTangent(uber, time=(f, f), outTangentType="step")
        cmds.keyTangent(ushape, time=(f, f), outTangentType="step")
    return uber


def export_shot_camera_in_place(shot: dict[str, Any], path: str | Path) -> dict[str, Any]:
    """`<Shot>.fbx`（Ubercam + 小道具 + `EVT_` ロケータ）を書く。破壊的（mayapy 専用）。"""
    from . import shot_scene as SS

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    cmds.loadPlugin("fbxmaya", quiet=True)
    SS.remove(shot["shot"])  # 開いているシーンで反映済みの合成・shot ノードがあれば外してから作り直す
    lo, hi = shot["range"]
    props = _collect_props(shot)
    uber = build_ubercam(shot)
    events = SS.sync_events(shot)
    events = [cmds.parent(e, world=True)[0] for e in events]  # 親（tdShot_ グループ）は書き出さない
    cmds.select([uber] + props + events, replace=True)
    _shot_fbx_options(lo, hi, cameras=True)
    mel.eval(f'FBXExport -f "{path.as_posix()}" -s')
    return {"path": str(path), "camera": uber, "props": len(props), "events": len(events), "frames": hi - lo + 1}


def export_shot_character_in_place(shot: dict[str, Any], index: int, animset: dict[str, Any], animsets: dict[str, dict[str, Any]], path: str | Path) -> dict[str, Any]:
    """`<Shot>__<ModelId>.fbx`（ルートジョイントの階層だけ・ショットの範囲でベイク）を書く。破壊的（mayapy 専用）。"""
    from . import shot_scene as SS

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    cmds.loadPlugin("fbxmaya", quiet=True)
    SS.remove(shot["shot"])
    lo, hi = shot["range"]
    chars = shot["characters"]
    ch = chars[index]
    # 他のキャラクターのスケルトンは消す（ネームスペースを外したとき、同じ名前のジョイントとぶつからないように）
    for j, other in enumerate(chars):
        if j != index and other.get("namespace") != ch.get("namespace"):
            root = SS.character_root(other, animsets.get(other["modelId"], animset))
            if cmds.objExists(root):
                cmds.delete(root)
    SS.compose_characters(shot, animsets=animsets, only=index)
    joints = SS.character_joints(ch, animset)
    cmds.bakeResults(joints, time=(lo, hi), simulation=True, sampleBy=1, disableImplicitControl=True, preserveOutsideKeys=False,
                     sparseAnimCurveBake=False, minimizeRotation=True, controlPoints=False, shape=False, attribute=list(TRS))
    junk = (cmds.ls(joints, dagObjects=True, type="constraint", long=True) or []) + (cmds.ls("tdAnimComp_*") or [])
    if junk:  # 空のリストを cmds.delete に渡さない
        cmds.delete([n for n in junk if cmds.objExists(n)])
    root = SS.character_root(ch, animset)
    ns = ch.get("namespace") or ""
    if ns:  # FBX のジョイント名にネームスペースを付けない（SK_<ModelId>.fbx と同じ名前にする）
        with contextlib.suppress(RuntimeError):
            cmds.namespace(removeNamespace=ns, mergeNamespaceWithRoot=True)
        root = root.split(":", 1)[1]
    cmds.select(root, replace=True)
    _shot_fbx_options(lo, hi, cameras=False)
    mel.eval(f'FBXExport -f "{path.as_posix()}" -s')
    return {"path": str(path), "joints": len(joints), "frames": hi - lo + 1}


def run_shot_tasks(args: dict[str, Any]) -> dict[str, Any]:
    """別プロセス（tools/export_fbx_batch.py の mode = shot）から呼ばれる。タスクごとにシーンを開き直し、失敗しても残りを続ける。"""
    cmds.loadPlugin("fbxmaya", quiet=True)
    shot = args["shot"]
    animsets = args["animsets"]
    results = []
    progress = Path(args["result"]).with_name("progress.txt")
    for task in args["tasks"]:
        t0 = time.perf_counter()
        rec: dict[str, Any] = {"type": task["type"], "name": task["name"], "out": task["out"], "ok": False}
        try:
            cmds.file(args["scene"], open=True, force=True)
            if task["type"] == "camera":
                rec.update(export_shot_camera_in_place(shot, task["out"]))
            else:
                ch = shot["characters"][task["index"]]
                rec.update(export_shot_character_in_place(shot, task["index"], animsets[ch["modelId"]], animsets, task["out"]))
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


class ShotJob(export_job.BatchJob):
    """ショットの出力ジョブ（`<Shot>.fbx` + キャラクターごとの `<Shot>__<ModelId>.fbx`）。生成すると開始 → poll() が True になったら finish()。
    `.fctrack` と `<Shot>.shot.json` は開いている Maya の側で書く（プレビュー用ノードのキーを読むため）。"""

    def __init__(self, shot: dict[str, Any], out_dir: Path, animsets: dict[str, dict[str, Any]]) -> None:
        from .core import shot as SH
        from .core import export_plan as EP

        self.shot = json.loads(json.dumps(shot))
        self.out_dir = Path(out_dir)
        self.animsets = animsets
        self.tasks: list[dict[str, Any]] = [{"type": "camera", "name": self.shot["shot"], "out": str(self.out_dir / EP.shot_file(self.shot["shot"]))}]
        for i, (ch, fname) in enumerate(SH.character_files(self.shot)):
            self.tasks.append({"type": "character", "name": fname, "index": i, "out": str(self.out_dir / EP.shot_file(fname))})
        self.total = len(self.tasks)
        self.extra: list[str] = []  # .fctrack / shot.json のログ
        super().__init__({"mode": "shot", "shot": self.shot, "animsets": animsets, "tasks": self.tasks}, prefix="tdrive_shot_export_")

    def progress(self) -> tuple[int, int]:
        try:
            done = len((self.work / "progress.txt").read_text(encoding="utf-8").splitlines())
        except OSError:
            done = 0
        return done, self.total

    def write_facial_tracks(self) -> list[str]:
        """facialTrack が ON のキャラクターの `.fctrack` を書く（既存の `tdrive_facial.export.export_fctrack`）。ログの行を返す。"""
        from .core import shot as SH

        lines: list[str] = []
        want = [(ch, fname) for ch, fname in SH.character_files(self.shot) if ch.get("facialTrack")]
        if not want:
            return lines
        try:
            from tdrive_facial import export as fexport
            from tdrive_facial import session as fsession
        except Exception as exc:  # noqa: BLE001
            return [f"⚠ .fctrack: FacialController を読み込めません: {exc}"]
        doc = fsession.current().doc
        if doc is None:
            return ["⚠ .fctrack: FacialController のデータが開かれていません（Facial タブでデータを開いてください）"]
        lo, hi = self.shot["range"]
        for ch, fname in want:
            try:
                res = fexport.export_fctrack(doc, self.shot["shot"], ch["modelId"], lo, hi, self.shot["frameRate"], self.out_dir)
                lines.append(f"✔ {res['path']}（キー {res['keys']}）")
                lines += [f"   ⚠ {w}" for w in res["warnings"]]
            except Exception as exc:  # noqa: BLE001  顔の失敗で FBX の出力を止めない
                lines.append(f"⚠ .fctrack ({ch['modelId']}): {exc}")
        return lines

    def finish(self) -> list[dict[str, Any]]:
        """結果を受け取り、`<Shot>.shot.json` と `.fctrack` を書く。タスクごとの結果を返す（失敗があっても書けた分は残る）。"""
        from .core import shot as SH

        res = self.result()["results"]
        if any(r["ok"] for r in res):
            SH.save(self.out_dir / f"{self.shot['shot']}{SH.SUFFIX}", self.shot)
            self.extra = self.write_facial_tracks()
        return res
