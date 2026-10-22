"""Unity 用の分割出力（docs/20 §6）: モデル `SK_<ModelId>.fbx` とクリップごとの `A_<ModelId>_<動作>.fbx`。

- **開いているシーンは壊さない**: 一時ファイルに書き出して、別プロセスの mayapy（`tools/export_fbx_batch.py` の `mode: "anim"`）が
  タスクごとにそのファイルを開き直し、整形して FBX を書く（ベイクはシーンを壊すので、タスクごとに新しく開く）
- **合成中は出力しない**（`COMPOSITION_BLOCK`）: 一時シーンにジョイントが合成につながったまま書かれ、合成の動きが焼かれる。裏のプロセスでも、
  シーンを開いた直後に合成を外す（`_prepare_scene`。SF-1）
- モデル: Toon の `export_in_place` に相乗り（頂点カラー `tdToonMask`・UV `tdSmoothNormal`・参照のフラット化・`_tdToon` を元に戻す）。
  メッシュ + スケルトンのルート以下だけ。アニメーションは含めない・バインドポーズで書く
- アニメーション: ルート以下のジョイントだけ。クリップ範囲で `bakeResults`（コントローラやコンストレイントは出さない）、
  `rootMotion=none` は **humanoid.json の Hips**（無ければスケルトンのルート）の XZ をクリップ開始フレームの値に固定、範囲の先頭を 0 フレームへ寄せ、
  1 テイク（Maya の既定の名前 `Take 001`。D-Drive は最初の埋め込みクリップをファイル名で取り込むので、テイク名は使われない。名前付きのテイクを足すと 2 テイクになる）
- **FBX は一時名で書いてから置き換える**（`_write_atomically`）: 書き出し中の失敗・閉じたときの kill で、Unity プロジェクトの以前の正しい FBX を壊さない（SF-13）
- FBX のオプションは単位 cm・バイナリ・FBX 2020 を明示する（Maya の既定が変わっても同じ。DD-11）
- dx11Shader・Render Override は使わない（モデルの出力は Toon のコードを通るので、プレビュー用ノードを扱えるよう、裏のプロセスを起動する
  `tools/export_fbx_batch.py` だけがプラグインを読む。読めなくても出力は続く。開いている Maya の側では何も読まない）
"""

from __future__ import annotations

import contextlib
import copy
import hashlib
import json
import os
import time
import traceback
from pathlib import Path
from typing import Any

from maya import cmds, mel

from tdrive import export_job, project

from . import composition as K
from .core import animset as A
from .core import export_plan as E

TRS = ("tx", "ty", "tz", "rx", "ry", "rz", "sx", "sy", "sz")
EXPORT_VERSION = 2  # 出力のコード・FBX のオプションを変えたら上げる（指紋に入れ、古い FBX が「変更なし」で飛ばされないように。SF-12）
COMPOSITION_BLOCK = "ブレンド / ショットの合成中は出力できません（合成の動きが FBX に焼かれます）。ブレンドタブ・ショットタブで「合成を外す」を押してから出力してください"
# 単位 cm・バイナリ・FBX 2020（Unity が読める上限）を明示する（FBXResetExport の既定に任せない。DD-11）
FBX_COMMON = ("FBXExportConvertUnitString cm", "FBXExportInAscii -v false", "FBXExportFileVersion -v FBX202000")


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
    """クリップの指紋: 属性 + フレームレート + ルート + 出力コードの版 + **範囲内の各フレームの全ジョイントのローカル行列**（評価した結果）。
    変わったかどうかの判定にだけ使う。評価した値なので、接線・範囲外のキー（範囲内の補間が変わる）・コンストレイントやリグ・アニメーションレイヤーの
    重みとミュートの変更も拾う（以前は範囲内のキーの時刻と値だけ見ていた。SF-12）。現在の時刻は元に戻す。"""
    h = hashlib.sha1()
    h.update(json.dumps({k: clip[k] for k in ("name", "start", "end", "rootMotion")} | {"fps": animset["frameRate"], "root": A.root_name(animset),
                                                                                       "v": EXPORT_VERSION}, sort_keys=True).encode())
    joints = skeleton_joints(A.root_name(animset))
    s, e = int(clip["start"]), int(clip["end"])
    t0 = cmds.currentTime(query=True)
    undo_was = cmds.undoInfo(query=True, state=True)
    cmds.undoInfo(stateWithoutFlush=False)
    try:
        for f in range(s, e + 1):
            cmds.currentTime(f, edit=True)
            for j in joints:
                h.update(",".join(f"{x:.4f}" for x in cmds.getAttr(f"{j}.matrix")).encode())
    finally:
        cmds.currentTime(t0, edit=True)
        if undo_was:
            cmds.undoInfo(stateWithoutFlush=True)
    return h.hexdigest()


def fingerprints(animset: dict[str, Any], names: list[str]) -> dict[str, str]:
    return {n: fingerprint(A.find_clip(animset, n), animset) for n in names}


# ------------------------------------------------------------------ 書き出し（別プロセスの mayapy で呼ぶ。開いているシーンを直接壊す）
def _import_references() -> None:
    """参照（リファレンス）を取り込む（ネームスペースは残す。外すのは各出力の最後）。参照のままだと、`removeNamespace` が外せず、
    ノードの削除・名前の変更が効かない（DD-4）。書き出し用の一時シーンだけで呼ぶ。"""
    for _ in range(10):  # 入れ子の参照は、取り込むと外側に出てくる
        refs = cmds.file(query=True, reference=True) or []
        if not refs:
            break
        for r in refs:
            try:
                cmds.file(r, importReference=True)
            except RuntimeError:
                with contextlib.suppress(RuntimeError):
                    cmds.file(r, removeReference=True)  # 読み込まれていない参照は取り込めない


def _prepare_scene() -> None:
    """一時シーンを開いた直後に行う: 合成を外す（合成の動きを焼かない。SF-1）・参照を取り込む（DD-4）。"""
    K.remove()
    _import_references()


def _write_atomically(path: Path, write) -> None:
    """`write(一時ファイルのパス)` で書いてから `path` に置き換える。一時ファイルが書かれなければ例外にして、以前のファイルは触らない（SF-13）。"""
    tmp = path.with_name(f"{path.stem}.writing{path.suffix}")
    if tmp.exists():
        tmp.unlink()  # 前回の中断の残り
    try:
        write(tmp)
        if not tmp.is_file() or tmp.stat().st_size <= 0:
            raise RuntimeError(f"FBX が書かれませんでした（{path.name}）。以前のファイルはそのままです")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            with contextlib.suppress(OSError):
                tmp.unlink()


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

    path = Path(path)
    root = A.root_name(animset)
    joints = skeleton_joints(root)
    if not joints:
        raise RuntimeError(f"スケルトンのルート {root} がシーンにありません")
    meshes = [m for m in animset["meshes"] if cmds.objExists(m)] or skinned_meshes(root)
    if not meshes:
        raise RuntimeError("書き出すメッシュがありません（animset の meshes が空で、スキンされたメッシュも見つかりません）")
    cmds.currentUnit(time={30: "ntsc", 60: "ntscf"}[animset["frameRate"]], updateAnimation=False)
    _to_bind_pose(joints)
    result: dict[str, Any] = {}
    root_node = cmds.ls(root, long=True)[0]
    _write_atomically(path, lambda tmp: result.update(toon_export.export_in_place(meshes, tmp, roots=[root_node], animation=False)))
    result["path"] = str(path)
    return result


def export_clip_in_place(animset: dict[str, Any], clip: dict[str, Any], path: str | Path, hips: str = "") -> dict[str, Any]:
    """`A_<ModelId>_<動作>.fbx`（ジョイントのみ・ベイク済み・1 テイク）を書く。破壊的（mayapy 専用）。
    `hips` = humanoid.json の Hips のジョイント名（ネームスペースなし）。rootMotion=none はこのジョイントの XZ を固定する（無ければスケルトンのルート。SP-9）。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    cmds.loadPlugin("fbxmaya", quiet=True)
    root = A.root_name(animset)
    joints = skeleton_joints(root)
    if not joints:
        raise RuntimeError(f"スケルトンのルート {root} がシーンにありません")
    s, e = int(clip["start"]), int(clip["end"])
    ns = animset.get("namespace") or ""
    top = joints[0]
    motion = f"{ns}:{hips}" if ns and hips and ":" not in hips else hips
    motion_node = cmds.ls(motion, long=True)[0] if motion and cmds.objExists(motion) else top
    fixed = {ax: cmds.getAttr(f"{motion_node}.translate{ax}", time=s) for ax in "XZ"} if clip["rootMotion"] == "none" else {}
    cmds.bakeResults(joints, time=(s, e), simulation=True, sampleBy=1, disableImplicitControl=True, preserveOutsideKeys=False,
                     sparseAnimCurveBake=False, minimizeRotation=True, controlPoints=False, shape=False, attribute=list(TRS))
    cons = cmds.ls(joints, dagObjects=True, type="constraint", long=True) or []
    if cons:  # 空のリストを cmds.delete に渡さない。コンストレイントは書き出さない
        cmds.delete(cons)
    for ax, v in fixed.items():  # rootMotion = none: Hips の XZ をクリップ開始の値に固定（Unity の Original 相当）
        plug = f"{motion_node}.translate{ax}"
        with contextlib.suppress(RuntimeError):
            cmds.cutKey(plug, clear=True)
            cmds.setAttr(plug, v)
    cmds.keyframe(joints, edit=True, relative=True, timeChange=-s)  # 範囲の先頭を 0 フレームへ
    length = e - s
    cmds.playbackOptions(minTime=0, maxTime=length, animationStartTime=0, animationEndTime=length)
    cmds.currentTime(0, edit=True)
    if ns:  # FBX のジョイント名にネームスペースを付けない（SK_<ModelId>.fbx と同じ名前にする）
        with contextlib.suppress(RuntimeError):
            cmds.namespace(removeNamespace=ns, mergeNamespaceWithRoot=True)
        top = animset["skeletonRoot"].rsplit(":", 1)[-1]
    cmds.select(top, replace=True)
    for cmd in (
        "FBXResetExport", *FBX_COMMON, "FBXExportSkins -v false", "FBXExportShapes -v false", "FBXExportConstraints -v false",
        "FBXExportCameras -v false", "FBXExportLights -v false", "FBXExportInputConnections -v false",
        "FBXExportEmbeddedTextures -v false", "FBXExportUpAxis y", "FBXExportAnimationOnly -v false",
        "FBXExportBakeComplexAnimation -v true", "FBXExportBakeComplexStart -v 0", f"FBXExportBakeComplexEnd -v {length}",
        "FBXExportBakeComplexStep -v 1", "FBXExportSplitAnimationIntoTakes -clear",
    ):
        mel.eval(cmd)
    _write_atomically(path, lambda tmp: mel.eval(f'FBXExport -f "{tmp.as_posix()}" -s'))
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
            _prepare_scene()
            if task["type"] == "model":
                rec.update(export_model_in_place(animset, task["out"]))
            else:
                clip = A.find_clip(animset, task["clip"])
                if clip is None:
                    raise RuntimeError(f"クリップがありません: {task['clip']}")
                rec.update(export_clip_in_place(animset, clip, task["out"], args.get("hips", "")))
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
                 animset_path: Path | None = None, fingerprints_: dict[str, str] | None = None) -> None:
        if K.active():
            raise RuntimeError(COMPOSITION_BLOCK)
        self.animset = json.loads(json.dumps(animset))  # 出力中に編集しても食い違わないよう、開始時の内容を持つ
        self.model_dir, self.anim_dir = Path(model_dir), Path(anim_dir)
        self.animset_path = animset_path
        self.fingerprints = {n: (fingerprints_ or {}).get(n) or fingerprint(A.find_clip(animset, n), animset) for n in clips}
        self.tasks: list[dict[str, Any]] = []
        if model:
            self.tasks.append({"type": "model", "out": str(self.model_dir / E.model_file(animset["modelId"]))})
        for n in clips:
            self.tasks.append({"type": "clip", "clip": n, "out": str(self.anim_dir / E.clip_file(n))})
        from . import scene_naming as SN

        humanoid = SN.humanoid_for(animset["modelId"], animset["skeletonRoot"], animset.get("namespace", ""))
        self.humanoid: dict[str, Any] | None = humanoid if model else None
        hips = (humanoid.get("bones") or {}).get("Hips", "")
        self.total = len(self.tasks)
        super().__init__({"mode": "anim", "animset": self.animset, "tasks": self.tasks, "hips": hips}, prefix="tdrive_anim_export_")

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
        if all(r["ok"] for r in res):
            self.dispose()  # 成功したら作業フォルダ（シーンのコピー）を消す。失敗したときはログのために残す（SF-15）
        return res


def project_config() -> dict[str, Any]:
    return project.read_config()


# ------------------------------------------------------------------ ショットの出力（docs/20 §5.3、AN-8）
SHOT_BAKE = (
    "FBXResetExport", *FBX_COMMON, "FBXExportSkins -v false", "FBXExportShapes -v false", "FBXExportConstraints -v false", "FBXExportLights -v false",
    "FBXExportInputConnections -v false", "FBXExportEmbeddedTextures -v false", "FBXExportUpAxis y", "FBXExportAnimationOnly -v false",
    "FBXExportBakeComplexAnimation -v true", "FBXExportBakeComplexStep -v 1", "FBXExportSplitAnimationIntoTakes -clear",
)


def _shot_fbx_options(lo: int, hi: int, cameras: bool) -> None:
    """D-Drive のカットシーン FBX のオプション（docs/26 §5.1 手順 3）: Animation ON / Bake（開始〜終了 = ショットの全フレーム、Step 1）/
    Cameras は引数 / Lights OFF / Embed Media OFF / Input Connections OFF / 単位 cm・Y-up。
    再生範囲もショットの範囲にする（テイクの長さが再生範囲から取られても lo〜hi になる。DD-10）。"""
    cmds.playbackOptions(minTime=lo, maxTime=hi, animationStartTime=lo, animationEndTime=hi)
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
    ワールド位置・向き・画角・フィルム・クリップ面を写してキーを打つ。区間の切り替わりはステップ）。カメラが無ければ ValueError。
    D-Drive のトラック名はカメラの GameObject 名なので、名前が `Ubercam1` などにならないよう、シーンに同名のノードがあれば先に改名する（DD-13）。"""
    from .core import shot as SH

    lo, hi = shot["range"]
    cams = [c for c in shot["cameras"] if cmds.objExists(c["node"])]
    if not cams:
        raise ValueError("カメラの区間がありません（ショットタブでカメラを足してください）")
    first = cams[0]["node"]
    first_shape = (cmds.listRelatives(first, shapes=True, type="camera") or [first])[0]
    for existing in cmds.ls(name, f"{name}Shape", long=True) or []:  # 一時シーンなので改名してよい
        cmds.rename(existing, "tdOld_" + existing.split("|")[-1])
    uber = cmds.camera(name=name)[0]
    if uber != name:  # Maya が番号を付けることがある（Ubercam1）。D-Drive のトラック名になるので、名前を戻す
        uber = cmds.rename(uber, name)
    if uber != name:
        raise RuntimeError(f"Ubercam の名前が {uber} になりました（D-Drive のトラック名が変わります）")
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


def include_events(shot: dict[str, Any]) -> bool:
    """`<Shot>.fbx` に `EVT_` ロケータを入れるか。既定は**入れない**: D-Drive には `EVT_` / `ddEvent` を読むコードが無く、
    `EVT_Hit` という小物のトラックとして取り込まれて警告が出る（docs/21 DD-2）。D-Drive が対応したら、ショットの export.includeEvents を true に。"""
    return bool((shot.get("export") or {}).get("includeEvents", False))


def export_shot_camera_in_place(shot: dict[str, Any], path: str | Path) -> dict[str, Any]:
    """`<Shot>.fbx`（Ubercam + 小道具 + 設定すれば `EVT_` ロケータ）を書く。破壊的（mayapy 専用）。"""
    from . import shot_scene as SS

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    cmds.loadPlugin("fbxmaya", quiet=True)
    SS.remove(shot["shot"])  # 開いているシーンで反映済みの合成・shot ノードがあれば外してから作り直す
    lo, hi = shot["range"]
    props = _collect_props(shot)
    uber = build_ubercam(shot)
    events: list[str] = []
    if include_events(shot):
        events = SS.sync_events(shot)
        events = [cmds.parent(e, world=True)[0] for e in events]  # 親（tdShot_ グループ）は書き出さない
    cmds.select([uber] + props + events, replace=True)
    _shot_fbx_options(lo, hi, cameras=True)
    _write_atomically(path, lambda tmp: mel.eval(f'FBXExport -f "{tmp.as_posix()}" -s'))
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
    existing = [n for n in junk if cmds.objExists(n)]  # 先に絞り込む（コンストレイントの削除に巻き込まれて先に消えたものがある。CR-2）
    if existing:  # 空のリストを cmds.delete に渡さない
        cmds.delete(existing)
    root = SS.character_root(ch, animset)
    ns = ch.get("namespace") or ""
    if ns:  # FBX のジョイント名にネームスペースを付けない（SK_<ModelId>.fbx と同じ名前にする）
        with contextlib.suppress(RuntimeError):
            cmds.namespace(removeNamespace=ns, mergeNamespaceWithRoot=True)
        root = root.rsplit(":", 1)[-1]  # 入れ子のネームスペース（A:B:）でも最後の名前（DD-12）
    cmds.select(root, replace=True)
    _shot_fbx_options(lo, hi, cameras=False)
    _write_atomically(path, lambda tmp: mel.eval(f'FBXExport -f "{tmp.as_posix()}" -s'))
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
            _prepare_scene()
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

        if K.active():
            raise RuntimeError(COMPOSITION_BLOCK)
        self.shot = json.loads(json.dumps(shot))
        self.out_dir = Path(out_dir)
        self.animsets = animsets
        self.tasks: list[dict[str, Any]] = [{"type": "camera", "name": self.shot["shot"], "out": str(self.out_dir / EP.shot_file(self.shot["shot"]))}]
        for i, (ch, fname) in enumerate(SH.character_files(self.shot)):
            self.tasks.append({"type": "character", "name": fname, "index": i, "out": str(self.out_dir / EP.shot_file(fname))})
        self.total = len(self.tasks)
        self.extra: list[str] = []  # .fctrack / shot.json のログ
        self._facial_doc = self._snapshot_facial_doc()  # 出力中に顔のキーを触っても、開始時の内容で .fctrack を書く（SF-21）
        super().__init__({"mode": "shot", "shot": self.shot, "animsets": animsets, "tasks": self.tasks}, prefix="tdrive_shot_export_")

    def _snapshot_facial_doc(self) -> Any:
        if not any(ch.get("facialTrack") for ch in self.shot["characters"]):
            return None
        try:
            from tdrive_facial import session as fsession

            doc = fsession.current().doc
            return copy.deepcopy(doc) if doc is not None else None
        except Exception:  # noqa: BLE001  複製できなければ、書くときに今のデータを使う
            return None

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
        doc = self._facial_doc if self._facial_doc is not None else fsession.current().doc
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
        if all(r["ok"] for r in res):
            self.dispose()  # 成功したら作業フォルダを消す（SF-15）
        return res
