"""FacialController の出力（Maya 依存。docs/14 §5.10・§7.3、docs/15 §4.7）。

- `export_unity`: Unity 向け。`<プロジェクト>/facial/<character>/export/unity/` に
    `<character>.fbx`（顔メッシュ + 同じ骨格の表示中のスキンメッシュ + ジョイント。ブレンドシェイプは FC_* を含み、`fcs_*` と
    `tdPreviewOnly` のノード・アニメーションは含まない。単位 cm）と `<character>.fcpose`（Document と同じ JSON。Maya の系）
- `export_ue`: UE 版向けの `.fcpose.json`（Document をそのまま）
- `export_fctrack`: プレビュー用ノードのキーを `<Shot>__<Model>.fctrack`（core/fctrack.py）へ

## 開いているシーンを変えない
`fcs_*` の削除や rig の除去はシーンを壊すので、開いているシーンではしない（Toon の Unity 出力と同じ方式）:
現在のシーンを一時ファイルへ `file -exportAll`（シーン名・未保存の印は変わらない）→ 別プロセスの mayapy
（tools/export_facial_fbx_batch.py）が一時シーンを開いて整形・FBX 書き出し。Undo で戻す方式は FBXExport が Undo 履歴を消すので使えない。

## FBX の設定（固定）
スキン・ブレンドシェイプ・タンジェント・スムージンググループ ON / スムースメッシュ OFF / 入力接続 ON（ターゲットメッシュがつながったままのシェイプを落とさない）/ アニメーション OFF（ベイク OFF）/
Y-up / 単位 cm / テクスチャは埋め込まない。書き出しの前に、FC_* の重みを 0 にする（rig・キーの今の値を FBX の初期値にしない）。
ポーズ（ジョイントの今の姿勢）はシーンのまま（バインドポーズへは戻さない）。
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from maya import cmds, mel
from maya.api import OpenMaya as om
from tdrive import REPO_ROOT, project

from . import preview_rig
from . import scene as scene_mod
from .core import fcpose_io, naming, validate
from .core import fctrack as fctrack_mod
from .core.model import Document

FBX_OPTIONS = (
    "FBXResetExport",
    "FBXExportSkins -v true",
    "FBXExportShapes -v true",
    "FBXExportTangents -v true",
    "FBXExportSmoothingGroups -v true",
    "FBXExportSmoothMesh -v false",
    "FBXExportInputConnections -v true",  # false だと、ターゲットメッシュがつながったままの blendShape のターゲットが落ちる（FBX 由来のモデル）
    "FBXExportAnimationOnly -v false",
    "FBXExportBakeComplexAnimation -v false",
    "FBXExportCameras -v false",
    "FBXExportLights -v false",
    "FBXExportInAscii -v false",
    "FBXExportUpAxis y",
    "FBXExportScaleFactor 1.0",
    "FBXExportConvertUnitString cm",
    "FBXExportEmbeddedTextures -v false",
)
FBX_PROPERTIES = (("Export|IncludeGrp|Animation", False),)  # アニメーションは書かない（無いバージョンでは飛ばす）
FBX_SUFFIX = ".fbx"
FCPOSE_SUFFIX = ".fcpose"
UE_SUFFIX = ".fcpose.json"


class ExportError(RuntimeError):
    """出力できない。メッセージはそのまま画面に出せる日本語。"""


# ---------------------------------------------------------------------------
# 出力先・メッシュ
# ---------------------------------------------------------------------------


def character_name(doc: Document, doc_path: Optional[str | os.PathLike] = None) -> str:
    """出力ファイルの名前に使うキャラクター名（doc.asset。無ければ doc_path のファイル名から）。"""
    if doc.asset:
        return doc.asset
    if doc_path:
        n = Path(doc_path).name
        return n[: -len(UE_SUFFIX)] if n.endswith(UE_SUFFIX) else Path(doc_path).stem
    raise ExportError("アセット名（asset）が無いため出力できません")


def unity_dir(character: str) -> Path:
    """`<プロジェクト>/facial/<character>/export/unity/`。"""
    return project.root() / "facial" / character / "export" / "unity"


def _sculpt_prefix(doc: Document) -> str:
    return doc.sculpt_shapes.prefix if doc.sculpt_shapes is not None else naming.DEFAULT_SCULPT_PREFIX


def collect_meshes(doc: Document) -> list[str]:
    """FBX に入れるメッシュ（transform の長い名前）。顔メッシュ・extraMeshes・LOD のメッシュ（非表示でも入れる）・顔メッシュと同じ骨格にスキンされた表示中のメッシュ。"""
    if doc.target is None or not doc.target.mesh:
        raise ExportError("対象メッシュ（target.mesh）が設定されていません")
    try:
        face = scene_mod.resolve_mesh(doc.target.mesh)
    except ValueError as e:
        raise ExportError(f"対象メッシュが見つかりません: {e}") from e
    out = [face]
    for name in doc.target.all_meshes()[1:]:  # 先頭は顔メッシュ
        try:
            m = scene_mod.resolve_mesh(name)
        except ValueError:
            continue
        if m not in out:
            out.append(m)
    roots = set(scene_mod.skeleton_roots([face]))
    for m in scene_mod.list_visible_meshes():  # tdPreviewOnly・非表示（ターゲットメッシュ）は入らない
        if m in out or not scene_mod.skin_clusters(m):
            continue
        if roots & set(scene_mod.skeleton_roots([m])):
            out.append(m)
    return out


def _blend_nodes(meshes: list[str]) -> list[str]:
    nodes: list[str] = []
    for m in meshes:
        for n in scene_mod.blend_shapes(m):
            if n not in nodes:
                nodes.append(n)
    return nodes


def count_bake_issues(issues) -> tuple[int, int, int, int, int, int]:
    """検証の結果から (未ベイクの点, ベイク後に変更の点, シェイプが無い点, 未ベイクのキー, ベイク後に変更のキー, シェイプが無いキー)。
    キー = パース補正のキー（問題の `key` が付いているもの）。"""
    n = [0] * 6
    for i in issues:
        is_key = i.key is not None
        if i.code == "point_unbaked":
            n[0] += 1
        elif i.code == "point_changed_since_bake":
            n[1] += 1
        elif i.code == "baked_morph_missing":
            n[5 if is_key else 2] += 1
        elif i.code == "perspective_key_unbaked":
            n[3] += 1
        elif i.code == "perspective_key_changed":
            n[4] += 1
    return tuple(n)  # type: ignore[return-value]


def _bake_warnings(doc: Document) -> list[str]:
    try:
        face = scene_mod.resolve_mesh(doc.target.mesh)  # type: ignore[union-attr]
        issues = validate.validate(doc, scene_mod.build_scene_info(doc), bake_state=scene_mod.bake_state_for(face), bake_exclude=scene_mod.bake_exclude_for(face))
    except (ValueError, RuntimeError):
        return ["検証できませんでした（対象メッシュを確かめてください）"]
    out: list[str] = []
    n_unbaked, n_changed, n_missing, k_unbaked, k_changed, k_missing = count_bake_issues(issues)
    if n_unbaked:
        out.append(f"未ベイクの点が {n_unbaked} 点あります（その点の補正は FBX に入りません。ベイクしてから出力してください）")
    if n_changed:
        out.append(f"ベイク後にポーズを変えた点が {n_changed} 点あります（FBX の形は古いままです。ベイクし直してください）")
    if n_missing:
        out.append(f"ベイクの記録はあるのにシェイプが無い点が {n_missing} 点あります")
    if k_unbaked:
        out.append(f"未ベイクのパース補正のキーが {k_unbaked} 個あります（そのキーの補正は FBX に入りません。ベイクしてから出力してください）")
    if k_changed:
        out.append(f"ベイク後に変えたパース補正のキーが {k_changed} 個あります（FBX の形は古いままです。キーを削除したあとの番号の詰まりも含みます。ベイクし直してください）")
    if k_missing:
        out.append(f"ベイクの記録はあるのにシェイプが無いパース補正のキーが {k_missing} 個あります")
    n_orphan = sum(1 for i in issues if i.code == "orphan_target")
    if n_orphan:
        out.append(f"データに無い補正シェイプ（FC_*）が {n_orphan} 個シーンに残っています（元に戻す・やり直すの取り残しなど。そのまま FBX に入ります。ベイクすると掃除されます）")
    return out


# ---------------------------------------------------------------------------
# 別プロセス側（tools/export_facial_fbx_batch.py が呼ぶ。開いているシーンを破壊的に整形する）
# ---------------------------------------------------------------------------


def export_in_place(meshes: list[str], path: str | Path, sculpt_prefix: str = naming.DEFAULT_SCULPT_PREFIX) -> dict[str, Any]:
    """開いているシーンを直接整形して FBX を書き出す（破壊的）。mayapy（tools/export_facial_fbx_batch.py）専用。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    meshes = [m for m in meshes if cmds.objExists(m)]
    if not meshes:
        raise ExportError("書き出すメッシュがありません")
    cmds.loadPlugin("fbxmaya", quiet=True)
    # --- 作業用ノードを消す（プレビューの rig と補助ノード・tdPreviewOnly の transform）
    for rig in preview_rig.list_rigs():
        preview_rig.delete(cmds.getAttr(f"{rig}.{preview_rig.ASSET_ATTR}"))
    only = [
        t
        for t in cmds.ls(type="transform", long=True) or []
        if cmds.attributeQuery(scene_mod.PREVIEW_ONLY_ATTR, node=t, exists=True)
    ]
    only = [t for t in only if cmds.objExists(t) and not any(m == t or m.startswith(t + "|") for m in meshes)]
    if only:
        cmds.delete(only)
    # --- fcs_* を消す・FC_* の重みを 0 にする
    excluded = 0
    for node in _blend_nodes(meshes):
        sculpt = [a for a in scene_mod.target_indices(node) if naming.is_sculpt_name(a, sculpt_prefix)]
        if sculpt:
            excluded += len(scene_mod.delete_targets(node, sculpt, sculpt_prefix))
        for alias, idx in scene_mod.target_indices(node).items():
            if not naming.is_fc_name(alias):
                continue
            plug = scene_mod.weight_plug(node, idx)
            for src in cmds.listConnections(plug, source=True, destination=False, plugs=True) or []:
                cmds.disconnectAttr(src, plug)  # キーに焼いた weight も FBX の初期値は 0
            cmds.setAttr(plug, 0.0)
    counts: dict[str, int] = {}
    fc: dict[str, int] = {}
    fc_ex: dict[str, int] = {}
    for m in meshes:
        aliases = [a for n in scene_mod.blend_shapes(m) for a in scene_mod.target_indices(n)]
        counts[m] = len(aliases)
        fc[m] = sum(1 for a in aliases if naming.is_fc_name(a))
        fc_ex[m] = sum(1 for a in aliases if naming.is_fc_name(a) and a.endswith(naming.EXTREME_SUFFIX))  # 誇張用（_Ex）も FBX に入る
    # --- FBX
    cmds.select(meshes + scene_mod.skeleton_roots(meshes), replace=True)
    for cmd in FBX_OPTIONS:
        mel.eval(cmd)
    for prop, value in FBX_PROPERTIES:
        try:
            mel.eval(f'FBXProperty "{prop}" -v {"true" if value else "false"}')
        except RuntimeError:
            pass
    mel.eval(f'FBXExport -f "{path.as_posix()}" -s')
    return {
        "path": str(path),
        "meshes": [scene_mod.short_name(m) for m in meshes],
        "blendshapes": {scene_mod.short_name(m): n for m, n in counts.items()},
        "fc": {scene_mod.short_name(m): n for m, n in fc.items()},
        "fc_ex": {scene_mod.short_name(m): n for m, n in fc_ex.items()},
        "excluded_fcs": excluded,
    }


# ---------------------------------------------------------------------------
# Unity 向け（メインのプロセス）
# ---------------------------------------------------------------------------


def _mayapy() -> Path:
    exe = Path(sys.executable).with_name("mayapy.exe")
    if exe.exists():
        return exe
    return Path(os.environ.get("MAYA_LOCATION", r"C:\Program Files\Autodesk\Maya2026")) / "bin" / "mayapy.exe"


@dataclass
class UnityExportResult:
    fbx: Path
    fcpose: Path
    meshes: list[str] = field(default_factory=list)
    blendshapes: dict[str, int] = field(default_factory=dict)  # メッシュ → FBX に入ったブレンドシェイプの数
    fc_by_mesh: dict[str, int] = field(default_factory=dict)  # メッシュ → FBX に入った FC_* の数
    lod_meshes: dict[str, int] = field(default_factory=dict)  # LOD のメッシュ（短い名前）→ LOD 番号（FBX に入ったものだけ）
    fc_count: int = 0  # FBX に入った FC_*（顔メッシュ。誇張用の _Ex を含む）
    fc_ex_count: int = 0  # うち誇張用（_Ex）
    excluded_fcs: int = 0  # 除いた fcs_*
    warnings: list[str] = field(default_factory=list)
    seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d["fbx"], d["fcpose"] = str(self.fbx), str(self.fcpose)
        return d

    def summary(self) -> str:
        ex_note = f"（うち誇張用 {self.fc_ex_count}）" if self.fc_ex_count else ""
        return (
            f"出力: {self.fbx.name}（メッシュ {len(self.meshes)}、FC_* {self.fc_count}{ex_note}、除いた fcs_* {self.excluded_fcs}）+ {self.fcpose.name}"
            f"、{self.seconds:.1f} 秒" + (f"、警告 {len(self.warnings)} 件" if self.warnings else "")
        )


class UnityExportJob:
    """別プロセスでの FBX 書き出し。生成すると開始 → poll() が True になったら result()。UI は poll をタイマーで呼ぶ。"""

    def __init__(self, doc: Document, doc_path: Optional[str | os.PathLike] = None, out_dir: Optional[str | os.PathLike] = None) -> None:
        self.character = character_name(doc, doc_path)
        self.out_dir = Path(out_dir) if out_dir else unity_dir(self.character)
        self.fbx = self.out_dir / f"{self.character}{FBX_SUFFIX}"
        self.fcpose = self.out_dir / f"{self.character}{FCPOSE_SUFFIX}"
        self._t0 = time.perf_counter()
        self.meshes = collect_meshes(doc)
        self.warnings = _bake_warnings(doc)
        self._doc = copy.deepcopy(doc)  # 出力中にデータを編集しても、FBX と .fcpose が食い違わないよう開始時の内容を持つ（M-7）
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.work = Path(tempfile.mkdtemp(prefix="tdrive_facial_export_"))
        self._log = None
        try:
            self._start(doc)
        except BaseException:
            self._cleanup()  # 開始に失敗したら、一時フォルダ・ログを残さない（S-14）
            raise

    def _start(self, doc: Document) -> None:
        self.result_path = self.work / "result.json"
        scene = self.work / "scene.mb"
        # シーン名・未保存の印を変えずに内容だけ書き出す（メインスレッドで行う）
        cmds.file(str(scene), exportAll=True, type="mayaBinary", force=True, preserveReferences=True)
        payload = {
            "scene": str(scene),
            "meshes": self.meshes,
            "out": str(self.fbx),
            "result": str(self.result_path),
            "sculptPrefix": _sculpt_prefix(doc),
        }
        args = self.work / "args.json"
        args.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        env = dict(os.environ, TDRIVE_ROOT=REPO_ROOT.as_posix(), PYTHONIOENCODING="utf-8", MAYA_DISABLE_CER="1")  # 裏の mayapy のクラッシュ画面を出さない
        self._log = open(self.work / "log.txt", "wb")
        self.proc = subprocess.Popen(
            [str(_mayapy()), str(REPO_ROOT / "tools" / "export_facial_fbx_batch.py"), str(args)],
            stdout=self._log,
            stderr=subprocess.STDOUT,
            env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),  # 指定しないと黒いウィンドウが出る（閉じると失敗する）
        )

    def poll(self) -> bool:
        return self.proc.poll() is not None

    def cancel(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait()
        self._cleanup()

    def _cleanup(self) -> None:
        try:
            if self._log is not None:
                self._log.close()
        except OSError:
            pass
        shutil.rmtree(self.work, ignore_errors=True)

    def result(self) -> UnityExportResult:
        """完了後に呼ぶ。`.fcpose` を書いて要約を返す。失敗は ExportError（ログの末尾つき）。"""
        self._log.close()
        if not self.result_path.exists():
            tail = (self.work / "log.txt").read_bytes().decode("utf-8", "replace")[-3000:]
            err = self.work / "result.error.txt"
            if err.exists():
                tail = err.read_text(encoding="utf-8", errors="replace")[-3000:] + "\n" + tail
            self._cleanup()
            raise ExportError("FBX の書き出しに失敗しました:\n" + tail)
        res = json.loads(self.result_path.read_text(encoding="utf-8"))
        self._cleanup()
        if not self.fbx.exists():
            raise ExportError(f"FBX ができていません: {self.fbx}")
        fcpose_io.save(self._doc, self.fcpose)
        face = scene_mod.short_name(self.meshes[0])
        out = UnityExportResult(
            fbx=self.fbx,
            fcpose=self.fcpose,
            meshes=list(res["meshes"]),
            blendshapes=dict(res["blendshapes"]),
            fc_by_mesh={k: int(v) for k, v in res.get("fc", {}).items()},
            lod_meshes=self._lod_in(res["meshes"]),
            fc_count=int(res["fc"].get(face, 0)),
            fc_ex_count=int(res.get("fc_ex", {}).get(face, 0)),
            excluded_fcs=int(res["excluded_fcs"]),
            warnings=list(self.warnings),
            seconds=time.perf_counter() - self._t0,
        )
        if out.fc_count == 0:
            out.warnings.append("FBX に FC_* が 1 本も入っていません（ベイクしてから出力してください）")
        for name, lod in out.lod_meshes.items():
            if out.fc_by_mesh.get(name, 0) == 0:
                out.warnings.append(f"LOD{lod} のメッシュ「{name}」に FC_* が入っていません（ベイクしてから出力してください）")
        return out

    def _lod_in(self, exported: list[str]) -> dict[str, int]:
        """FBX に入った LOD のメッシュ（短い名前）→ LOD 番号。"""
        out: dict[str, int] = {}
        for m in self._doc.target.lod_meshes if self._doc.target is not None else []:
            try:
                short = scene_mod.short_name(scene_mod.resolve_mesh(m.mesh))
            except ValueError:
                continue
            if short in exported:
                out[short] = m.lod
        return out


def export_unity(
    doc: Document, doc_path: Optional[str | os.PathLike] = None, out_dir: Optional[str | os.PathLike] = None, timeout: float = 600.0
) -> dict[str, Any]:
    """開いているシーンを変更せずに Unity 向けの一式（FBX + `.fcpose`）を書く（完了まで待つ。MCP・テスト用。UI は `UnityExportJob`）。

    out_dir を省くと `<プロジェクト>/facial/<character>/export/unity/`。戻りは `UnityExportResult.to_dict()`
    （fbx / fcpose のパス・meshes・blendshapes・fc_count・excluded_fcs・warnings・seconds）。
    """
    job = UnityExportJob(doc, doc_path, out_dir)
    try:
        job.proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        job.cancel()  # 裏の mayapy を残さない
        raise ExportError(f"FBX の書き出しが {timeout:.0f} 秒で終わりませんでした") from None
    return job.result().to_dict()


# ---------------------------------------------------------------------------
# UE 版向け・Timeline 用
# ---------------------------------------------------------------------------


def export_ue(doc: Document, path: str | os.PathLike) -> Path:
    """UE 版向けの `.fcpose.json`（Document をそのまま。座標系は meta に書いてある）。path がフォルダ（拡張子なし）なら `<asset>.fcpose.json`。"""
    p = Path(path)
    if p.is_dir() or p.suffix == "":  # 拡張子の無いパスはフォルダとみなす
        p = p / f"{character_name(doc)}{UE_SUFFIX}"
    fcpose_io.save(doc, p)
    return p


def frame_rate_of_scene() -> float:
    """シーンの時間の単位 → 毎秒のフレーム数。"""
    return float(om.MTime(1.0, om.MTime.kSeconds).asUnits(om.MTime.uiUnit()))


def _animated_keys(plug: str, start: float, end: float) -> tuple[list[tuple[float, float]], list[str]]:
    """plug のキー（start〜end のもの。start より前にしかキーが無いときは start の値を足す）と、接線の種類（step / linear 以外）。"""
    times = cmds.keyframe(plug, query=True, timeChange=True) or []
    if not times:
        return [], []
    values = cmds.keyframe(plug, query=True, valueChange=True) or []
    keys = [(float(t), float(v)) for t, v in zip(times, values) if start - 1e-9 <= t <= end + 1e-9]
    if not any(abs(t - start) < 1e-9 for t, _ in keys) and any(t < start for t in times):
        keys.insert(0, (float(start), float(cmds.getAttr(plug, time=start))))
    kinds = set(cmds.keyTangent(plug, query=True, outTangentType=True) or [])
    kinds |= set(cmds.keyTangent(plug, query=True, inTangentType=True) or [])
    bad = sorted(k for k in kinds if k not in ("step", "stepnext", "linear"))
    return keys, bad


def read_fctrack(
    doc: Document, shot: str, model: str, start: float, end: float, frame_rate: Optional[float] = None
) -> tuple[fctrack_mod.FacialTrack, list[str]]:
    """プレビュー用ノードのキー可アトリビュートのカーブ（アニメーションしているものだけ）を FacialTrack にする。(track, 警告)"""
    asset = doc.asset or ""
    rig = preview_rig.find_rig(asset)
    if rig is None:
        raise ExportError("プレビュー用ノードがありません（プレビューを作って、強さ・感情にキーを打ってください）")
    if end < start:
        raise ExportError("終了フレームが開始フレームより前です")
    fps = float(frame_rate) if frame_rate else frame_rate_of_scene()
    attrs: dict[str, str] = {name: name for name in preview_rig.KEYABLE_FIXED}
    attrs[preview_rig.EXAGGERATION_ATTR] = "exaggeration"  # 誇張の強さ（キーがあるときだけ出る）
    attrs[preview_rig.PERSPECTIVE_ATTR] = "perspective"  # パース補正の強さ（同じくキーがあるときだけ出る）
    for li, attr in preview_rig.emotion_attrs(doc).items():
        attrs[attr] = fctrack_mod.emotion_curve_name(doc.layers[li].name)
    curves: dict[str, list[fctrack_mod.Key]] = {}
    warnings: list[str] = []
    tangent_warned = False
    for attr, curve in attrs.items():
        if not cmds.attributeQuery(attr, node=rig, exists=True):
            continue
        keys, bad = _animated_keys(f"{rig}.{attr}", start, end)
        if not keys:
            continue
        if curve in ("exaggeration", "perspective"):
            keys = [(t, min(1.0, max(0.0, v))) for t, v in keys]  # 0〜1 に収める
        curves[curve] = [((t - start) / fps, v) for t, v in keys]
        if bad and not tangent_warned:
            tangent_warned = True
            warnings.append(
                f"接線（{', '.join(bad)}）は書き出しません。キーの値だけを渡し、Unity では線形に補間されます（段階にしたいときは接線を step にしてください）"
            )
    track = fctrack_mod.FacialTrack(shot=shot, model=model, frame_rate=fps, range=(float(start), float(end)), curves=curves)
    return track, warnings


def export_fctrack(
    doc: Document,
    shot: str,
    model: str,
    start: float,
    end: float,
    frame_rate: Optional[float] = None,
    out_dir: Optional[str | os.PathLike] = None,
) -> dict[str, Any]:
    """`<Shot>__<Model>.fctrack` を書く。out_dir を省くと `<プロジェクト>/facial/<character>/export/unity/`。
    戻り: {"path", "curves": [名前...], "keys": 合計キー数, "frameRate", "range", "warnings"}。"""
    if not shot or not model:
        raise ExportError("ショット名とモデル名を入れてください")
    track, warnings = read_fctrack(doc, shot, model, start, end, frame_rate)
    directory = Path(out_dir) if out_dir else unity_dir(character_name(doc))
    path = directory / fctrack_mod.file_name(shot, model)
    fctrack_mod.save(track, path)
    if not track.curves:
        warnings.append("アニメーションしている属性がありません（プレビュー用ノードの強さ・感情・手動角度にキーを打ってください）")
    return {
        "path": str(path),
        "curves": list(track.curves),
        "keys": sum(len(v) for v in track.curves.values()),
        "frameRate": track.frame_rate,
        "range": list(track.range),
        "warnings": warnings,
    }
