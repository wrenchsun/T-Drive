"""Unity 向け FBX の書き出し（docs/05 §7、1-11）。

Unity は UV・頂点カラーを「名前でなく順番」で読むため、書き出す FBX では並びを整える:
  - 頂点カラー: tdToonMask だけにする（Unity は先頭のセットしか使わない。無ければ白 = 何もしない で作る）
  - UV: [0] map1 / [1] 予備（無ければ map1 の複製）/ [2] tdSmoothNormal  → Unity の uv0 / uv1 / uv2（TEXCOORD2）

整える操作はシーンを壊すので、開いているシーンでは行わない:
  現在のシーンを一時ファイルに書き出し → 別プロセスの mayapy で開いて整形・FBX 書き出し（tools/export_fbx_batch.py）。
  ※ Undo で戻す方式は FBXExport が Undo 履歴を消すため使えない（2026-09-28 検証）。
プレビュー中の割り当ては元マテリアルに戻してから書き出す（Unity のマテリアルスロットは元マテリアル名で対応付ける）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from maya import cmds, mel
from maya.api import OpenMaya as om

from . import REPO_ROOT, mask, naming, preview, smooth_normals

SPARE_UV = "tdUVSpare"
FBX_OPTIONS = (
    "FBXResetExport",
    "FBXExportSkins -v true",
    "FBXExportShapes -v true",
    "FBXExportTangents -v true",
    "FBXExportSmoothingGroups -v true",
    "FBXExportSmoothMesh -v false",
    "FBXExportInputConnections -v false",
    "FBXExportAnimationOnly -v false",
    "FBXExportBakeComplexAnimation -v false",
    "FBXExportUpAxis y",
    "FBXExportEmbeddedTextures -v false",
)


def _uv_sets(shape: str) -> list[str]:
    return list(om.MFnMesh(om.MSelectionList().add(shape).getDagPath(0)).getUVSetNames())


def _prepare(shape: str, warnings: list[str]) -> None:
    name = shape.split("|")[-1]
    if mask.MASK not in (cmds.polyColorSet(shape, query=True, allColorSets=True) or []):
        mask.init([shape])
    for cs in cmds.polyColorSet(shape, query=True, allColorSets=True) or []:
        if cs != mask.MASK:
            cmds.polyColorSet(shape, delete=True, colorSet=cs)
    cmds.polyColorSet(shape, currentColorSet=True, colorSet=mask.MASK)

    sets = _uv_sets(shape)
    if preview.SMOOTH_NORMAL_UV not in sets:
        warnings.append(f"{name}: スムーズ法線が未ベイク（Unity では _ToonOutlineSmoothNormal を 0 にするか、焼いてから書き出す）")
        return
    if "map1" not in sets:
        warnings.append(f"{name}: UV Set map1 が無い（Unity の uv0 が不定になる）")
        return
    others = [s for s in sets if s not in ("map1", preview.SMOOTH_NORMAL_UV)]
    spare = others[0] if others else None
    if spare is None:
        cmds.polyUVSet(shape, copy=True, uvSet="map1", newUVSet=SPARE_UV)
        spare = SPARE_UV
    for extra in others[1:]:
        cmds.polyUVSet(shape, delete=True, uvSet=extra)  # uv3 以降は使わない
    _move_to(shape, "map1", 0)
    _move_to(shape, spare, 1)
    _move_to(shape, preview.SMOOTH_NORMAL_UV, 2)


def _move_to(shape: str, uv_set: str, index: int) -> None:
    sets = _uv_sets(shape)
    if sets.index(uv_set) != index:
        cmds.polyUVSet(shape, reorder=True, uvSet=uv_set, newUVSet=sets[index])


def _skeleton_roots(shapes: list[str]) -> list[str]:
    roots = set()
    for shape in shapes:
        for sc in cmds.ls(cmds.listHistory(shape) or [], type="skinCluster"):
            for j in cmds.skinCluster(sc, query=True, influence=True) or []:
                roots.add("|" + cmds.ls(j, long=True)[0].split("|")[1])
    return sorted(roots)


def _flatten_references() -> None:
    """参照（リファレンス）を取り込み、ネームスペースをなくす。書き出し用の一時シーンだけで呼ぶ（FBX の名前・マテリアル名にネームスペースを入れないため。
    使っているシーンでは呼ばない。FacialController の書き出しにも同じ処理がある）。"""
    for _ in range(10):  # 入れ子の参照は、取り込むと外側に出てくる
        refs = cmds.file(query=True, reference=True) or []
        if not refs:
            break
        for r in refs:
            try:
                cmds.file(r, importReference=True)
            except RuntimeError:
                try:
                    cmds.file(r, removeReference=True)  # 読み込まれていない参照は取り込めない
                except RuntimeError:
                    pass
    spaces = [n for n in cmds.namespaceInfo(listOnlyNamespaces=True, recurse=True) or [] if n not in ("UI", "shared")]
    for n in sorted(spaces, key=lambda x: -x.count(":")):  # 深いものから
        try:
            cmds.namespace(removeNamespace=n, mergeNamespaceWithRoot=True)
        except RuntimeError:
            pass


def export_in_place(meshes: list[str], path: str | Path) -> dict[str, object]:
    """開いているシーンを直接整形して書き出す（破壊的）。mayapy（tools/export_fbx_batch.py）専用。

    参照したキャラクター（ネームスペース付き）は、先に元のマテリアルへ戻してから取り込み、ネームスペースを外す
    （FBX の名前は `mdl_face02`・マテリアル名は `mat_body01` のようにネームスペースなし。ネームスペースなしのシーンと同じ出力）。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    cmds.loadPlugin("fbxmaya", quiet=True)
    preview.disable()  # 取り込む前に（元のマテリアルの場所はネームスペース付きの名前で探すため）
    if cmds.file(query=True, reference=True) or any(":" in m for m in meshes):
        _flatten_references()
        meshes = [m if cmds.objExists(m) else naming.strip_all(m) for m in meshes]
    shapes = preview.mesh_shapes([m for m in meshes if cmds.objExists(m)])
    if not shapes:
        raise RuntimeError("書き出すメッシュがありません")
    warnings: list[str] = []
    for shape in shapes:
        _prepare(shape, warnings)
    transforms = sorted({cmds.listRelatives(s, parent=True, fullPath=True)[0] for s in shapes})
    cmds.select(transforms + _skeleton_roots(shapes), replace=True)
    for cmd in FBX_OPTIONS:
        mel.eval(cmd)
    mel.eval(f'FBXExport -f "{path.as_posix()}" -s')
    return {"path": str(path), "meshes": len(shapes), "warnings": warnings}


def _mayapy() -> Path:
    exe = Path(sys.executable).with_name("mayapy.exe")
    if exe.exists():
        return exe
    return Path(os.environ.get("MAYA_LOCATION", r"C:\Program Files\Autodesk\Maya2026")) / "bin" / "mayapy.exe"


class ExportJob:
    """別プロセスでの FBX 書き出し。start() → poll() が True になったら result()。"""

    def __init__(self, meshes: list[str], path: str | Path) -> None:
        self.work = Path(tempfile.mkdtemp(prefix="tdrive_export_"))
        self.out = Path(path)
        self.result_path = self.work / "result.json"
        scene = self.work / "scene.mb"
        # シーン名・未保存フラグを変えずに内容だけ書き出す（メインスレッドで行う）
        cmds.file(str(scene), exportAll=True, type="mayaBinary", force=True, preserveReferences=True)
        args = self.work / "args.json"
        payload = {"scene": str(scene), "meshes": meshes, "out": str(path), "result": str(self.result_path)}
        args.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        env = dict(os.environ, TDRIVE_ROOT=REPO_ROOT.as_posix(), PYTHONIOENCODING="utf-8", MAYA_DISABLE_CER="1")  # 裏の mayapy のクラッシュ画面を出さない
        self.log = open(self.work / "log.txt", "wb")
        self.proc = subprocess.Popen(
            [str(_mayapy()), str(REPO_ROOT / "tools" / "export_fbx_batch.py"), str(args)],
            stdout=self.log, stderr=subprocess.STDOUT, env=env,
            # mayapy はコンソールアプリなので、指定しないと黒いウィンドウが書き出し中ずっと出る（閉じると失敗する）
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    def poll(self) -> bool:
        return self.proc.poll() is not None

    def result(self) -> dict[str, object]:
        self.log.close()
        if not self.result_path.exists():
            tail = (self.work / "log.txt").read_bytes().decode("utf-8", "replace")[-3000:]
            raise RuntimeError("FBX 書き出しに失敗しました:\n" + tail)
        return json.loads(self.result_path.read_text(encoding="utf-8"))


def export_fbx(meshes: list[str], path: str | Path, timeout: float = 600.0) -> dict[str, object]:
    """開いているシーンを変更せずに Unity 向け FBX を書き出す（完了まで待つ。MCP・テスト用）。"""
    job = ExportJob(meshes, path)
    try:
        job.proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        job.proc.kill()  # 裏の mayapy を残さない
        raise
    return job.result()
