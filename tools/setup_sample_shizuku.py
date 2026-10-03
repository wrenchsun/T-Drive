"""サンプルモデル shizuku の取り込み（docs/14 §11、チケット S-1）。

配布アバター（kuromaru9）から「メッシュ・ボーン・ブレンドシェイプ・テクスチャ・アニメーション」だけを
プロジェクトフォルダの assets/shizuku/ へ写し、Maya シーン shizuku.mb を作る。
元のシェーダー・マテリアル・コントローラー・Prefab は使わない。assets/shizuku/ は .gitignore（コミットしない）。

  mayapy tools/setup_sample_shizuku.py [--source <kuromaru9 フォルダ>] [--project <プロジェクトフォルダ>] [--force]

  既定: --source = %USERPROFILE%\\Downloads\\kuromaru9
        --project = 環境変数 TDRIVE_PROJECT、無ければツール本体（リポジトリ直下）
書き込むのは <プロジェクト>/assets/shizuku/ の下だけ。何度実行しても同じ結果になる（変わっていないファイルは写さない）。
mayapy は環境変数 MAYA_DISABLE_CER=1 を付けて起動すること（クラッシュ時のダイアログを出さない）。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

ASSET_DIR_NAME = Path("assets") / "shizuku"
FBX_REL = Path("shizuku") / "FBX" / "shizuku.fbx"
TEXTURES_REL = Path("shizuku") / "Textures"
ANIM_DIRS_REL = (Path("shizuku") / "Animations" / "Base", Path("shizuku") / "Animations" / "Gesture")
FACIAL_REL = Path("shizuku") / "Animations" / "FX"
TEXTURE_SUFFIX = "_d"  # 色テクスチャ（*_m はマスク、*_n は法線。ここでは色だけ使う）


# ---------------------------------------------------------------- Maya 非依存（テスト対象）
def default_source() -> Path:
    return Path(os.environ.get("USERPROFILE") or Path.home()) / "Downloads" / "kuromaru9"


def default_project() -> Path:
    """tdrive_toon.project.root() と同じ決め方（TDRIVE_PROJECT > リポジトリ直下）。"""
    env = os.environ.get("TDRIVE_PROJECT")
    return Path(env) if env else REPO


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="shizuku を assets/shizuku/ へ取り込み、Maya シーンを作る")
    ap.add_argument("--source", type=Path, default=None, help="kuromaru9 フォルダ（既定: ダウンロード/kuromaru9）")
    ap.add_argument("--project", type=Path, default=None, help="プロジェクトフォルダ（既定: $TDRIVE_PROJECT、無ければリポジトリ直下）")
    ap.add_argument("--force", action="store_true", help="変わっていないファイルも写し直す")
    a = ap.parse_args(argv)
    a.source = Path(a.source) if a.source else default_source()
    a.project = Path(a.project) if a.project else default_project()
    return a


def validate_source(source: Path) -> list[str]:
    """元フォルダの構成を確かめる。問題の一覧（日本語）を返す。空なら問題なし。"""
    errors: list[str] = []
    if not source.is_dir():
        return [f"元のフォルダが見つかりません: {source}（--source で kuromaru9 フォルダを指定してください）"]
    if not (source / FBX_REL).is_file():
        errors.append(f"モデルが見つかりません: {source / FBX_REL}")
    tex = source / TEXTURES_REL
    if not tex.is_dir() or not any(tex.glob("*.png")):
        errors.append(f"テクスチャ（*.png）が見つかりません: {tex}")
    if not any(any((source / d).glob("*.fbx")) for d in ANIM_DIRS_REL if (source / d).is_dir()):
        errors.append(f"体のアニメーション（*.fbx）が見つかりません: {source / ANIM_DIRS_REL[0]}")
    fx = source / FACIAL_REL
    if not fx.is_dir() or not any(fx.glob("*.anim")):
        errors.append(f"表情のアニメーション（*.anim）が見つかりません: {fx}")
    return errors


def plan_copy(source: Path, dest: Path) -> list[tuple[Path, Path]]:
    """(元, 先) の一覧。.meta・シェーダー・マテリアル・Prefab・コントローラーは含めない。"""
    plan: list[tuple[Path, Path]] = [(source / FBX_REL, dest / "model" / "shizuku.fbx")]
    for f in sorted((source / TEXTURES_REL).glob("*.png")):
        plan.append((f, dest / "textures" / f.name))
    for d in ANIM_DIRS_REL:
        for f in sorted((source / d).glob("*.fbx")) if (source / d).is_dir() else []:
            plan.append((f, dest / "animations" / f.name))
    for f in sorted((source / FACIAL_REL).glob("*.anim")):
        plan.append((f, dest / "facial_anims" / f.name))
    return plan


def is_unchanged(src: Path, dst: Path) -> bool:
    """先が既にあり、サイズと更新時刻が元と同じか（copy2 は更新時刻を引き継ぐ）。"""
    if not dst.is_file():
        return False
    a, b = src.stat(), dst.stat()
    return a.st_size == b.st_size and int(a.st_mtime) == int(b.st_mtime)


def execute_copy(plan: list[tuple[Path, Path]], force: bool = False) -> tuple[list[Path], list[Path]]:
    """計画どおりに写す。(写したもの, 変わっていないので飛ばしたもの) を返す。"""
    copied: list[Path] = []
    skipped: list[Path] = []
    for src, dst in plan:
        if not force and is_unchanged(src, dst):
            skipped.append(dst)
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied.append(dst)
    return copied, skipped


def map_materials(material_names: list[str], texture_names: list[str]) -> dict[str, str | None]:
    """マテリアル名 → 色テクスチャのファイル名（無ければ None）。body01 → body01_d.png。"""
    available = set(texture_names)
    out: dict[str, str | None] = {}
    for m in material_names:
        out[m] = next((f"{c}{TEXTURE_SUFFIX}.png" for c in _name_candidates(m) if f"{c}{TEXTURE_SUFFIX}.png" in available), None)
    return out


def _name_candidates(name: str) -> list[str]:
    """FBX 取り込みで付く接頭辞 mat_ と、重複回避の末尾の数字（faceOption1）を外した名前も試す。"""
    cands = [name]
    base = name[4:] if name.startswith("mat_") else name
    for c in (base, base.rstrip("0123456789")):
        if c and c not in cands:
            cands.append(c)
    return cands


def texture_scene_path(texture_file: str) -> str:
    """シーンに書くテクスチャのパス（$TDRIVE_PROJECT 基準。preview.py と同じ書き方）。"""
    return f"$TDRIVE_PROJECT/{ASSET_DIR_NAME.as_posix()}/textures/{texture_file}"


# ---------------------------------------------------------------- Maya 側
def build_scene(dest: Path, project: Path, texture_names: list[str]) -> dict:
    """shizuku.mb を作り、レポート用の情報を返す。"""
    import maya.cmds as cmds

    # $TDRIVE_PROJECT が指す場所をこのプロセスでも合わせる（テクスチャの読み込み確認用）
    os.environ["TDRIVE_PROJECT"] = project.as_posix()

    cmds.file(new=True, force=True)
    cmds.currentUnit(linear="cm")
    cmds.upAxis(axis="y")
    if not cmds.pluginInfo("fbxmaya", query=True, loaded=True):
        cmds.loadPlugin("fbxmaya", quiet=True)

    mel_path = (dest / "model" / "shizuku.fbx").as_posix()
    import maya.mel as mel

    mel.eval("FBXResetImport;")
    mel.eval("FBXImportMode -v add;")
    mel.eval("FBXImportSkins -v true;")
    mel.eval("FBXImportShapes -v true;")
    mel.eval("FBXImportCameras -v false;")
    mel.eval("FBXImportLights -v false;")
    mel.eval("FBXImportMergeAnimationLayers -v false;")
    mel.eval("FBXImportFillTimeline -v false;")
    mel.eval("FBXImportUnlockNormals -v false;")
    mel.eval('FBXImport -f "%s";' % mel_path)

    # アニメーションは持ち込まない（キーがあれば消す）
    anim_nodes = cmds.ls(type=("animCurveTL", "animCurveTA", "animCurveTU", "animCurveTT")) or []
    if anim_nodes:
        cmds.delete(anim_nodes)

    # 元のマテリアルを lambert + テクスチャへ差し替える（マテリアル名は元のまま）
    # ブレンドシェイプの形状メッシュ（非表示で一緒に取り込まれる）は数えない: 表示されているメッシュだけがキャラクターの部位
    meshes = sorted(
        {
            cmds.listRelatives(s, parent=True, fullPath=False)[0]
            for s in cmds.ls(type="mesh", noIntermediate=True)
            if cmds.getAttr(f"{cmds.listRelatives(s, parent=True, fullPath=True)[0]}.visibility")
        }
    )
    original: dict[str, list[str]] = {}  # マテリアル名 -> そのマテリアルを使う SG
    for sg in cmds.ls(type="shadingEngine"):
        if sg in ("initialShadingGroup", "initialParticleSE"):
            continue
        members = cmds.sets(sg, query=True) or []
        if not members:
            continue
        src = cmds.listConnections(f"{sg}.surfaceShader", source=True, destination=False) or []
        if src:
            original.setdefault(src[0], []).append(sg)

    mapping = map_materials(sorted(original), texture_names)
    for name, sgs in sorted(original.items()):
        tmp = f"{name}__old"
        cmds.rename(name, tmp)
        new = cmds.shadingNode("lambert", asShader=True, name=name)
        # 古いノードの周りのテクスチャ等はまとめて消す
        history = cmds.listHistory(tmp, future=False) or []
        for sg in sgs:
            cmds.connectAttr(f"{new}.outColor", f"{sg}.surfaceShader", force=True)
        junk = [n for n in history if n != tmp and cmds.nodeType(n) in ("file", "place2dTexture", "bump2d")]
        cmds.delete(tmp)
        if junk:
            cmds.delete([n for n in junk if cmds.objExists(n)] or [])
        tex = mapping[name]
        if tex:
            f = cmds.shadingNode("file", asTexture=True, isColorManaged=True, name=f"{name}_color")
            cmds.setAttr(f"{f}.fileTextureName", texture_scene_path(tex), type="string")
            cmds.connectAttr(f"{f}.outColor", f"{new}.color", force=True)
        else:
            cmds.setAttr(f"{new}.color", 0.5, 0.5, 0.5, type="double3")
        mapping.setdefault(name, tex)
        original[name] = sgs

    cmds.file(rename=(dest / "shizuku.mb").as_posix())
    cmds.file(save=True, type="mayaBinary", force=True)

    # レポート
    info: dict = {}
    info["meshes"] = [{"name": m, "vertices": cmds.polyEvaluate(m, vertex=True)} for m in meshes]
    joints = cmds.ls(type="joint") or []
    info["joint_count"] = len(joints)
    info["head_bones"] = [j for j in joints if "head" in j.lower()]
    shapes = {}
    for bs in cmds.ls(type="blendShape") or []:
        targets = cmds.listAttr(f"{bs}.weight", multi=True) or []
        owners = cmds.blendShape(bs, query=True, geometry=True) or []
        shapes[bs] = {"geometry": owners, "targets": targets}
    info["blend_shapes"] = shapes
    info["face_targets"] = next(
        (v["targets"] for v in shapes.values() if any(o.split("|")[-1] in ("mdl_face02", "mdl_face02Shape") for o in v["geometry"])), []
    )
    info["material_texture"] = mapping
    info["up_axis"] = cmds.upAxis(query=True, axis=True)
    info["linear_unit"] = cmds.currentUnit(query=True, linear=True)
    info["namespaces"] = [n for n in (cmds.namespaceInfo(listOnlyNamespaces=True) or []) if n not in ("UI", "shared")]
    return info


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    source, project = args.source, args.project
    errors = validate_source(source)
    if errors:
        print("[S-1] 取り込めません:")
        for e in errors:
            print("  - " + e)
        return 2

    dest = project / ASSET_DIR_NAME
    print(f"[S-1] 元: {source}")
    print(f"[S-1] 先: {dest}")
    plan = plan_copy(source, dest)
    copied, skipped = execute_copy(plan, force=args.force)
    print(f"[S-1] ファイル: 写した {len(copied)} / 変更なしで飛ばした {len(skipped)}")

    textures = [d.name for _, d in plan if d.parent.name == "textures"]

    import maya.standalone

    maya.standalone.initialize(name="python")
    try:
        info = build_scene(dest, project, textures)
    finally:
        maya.standalone.uninitialize()

    report = {
        "source": str(source),
        "project": str(project),
        "scene": str(dest / "shizuku.mb"),
        "meshes": info["meshes"],
        "joint_count": info["joint_count"],
        "head_bones": info["head_bones"],
        "blend_shapes": info["blend_shapes"],
        "face_targets": info["face_targets"],
        "material_texture": info["material_texture"],
        "animations": sorted(d.name for _, d in plan if d.parent.name == "animations"),
        "facial_anims": sorted(d.name for _, d in plan if d.parent.name == "facial_anims"),
        "up_axis": info["up_axis"],
        "linear_unit": info["linear_unit"],
        "namespaces": info["namespaces"],
    }
    (dest / "setup_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"[S-1] メッシュ {len(info['meshes'])}: " + ", ".join(f"{m['name']}({m['vertices']})" for m in info["meshes"]))
    print(f"[S-1] ジョイント {info['joint_count']} / 頭のボーン: {info['head_bones']}")
    print(f"[S-1] blendShape: {list(info['blend_shapes'])} / mdl_face02 のシェイプ {len(info['face_targets'])}")
    for m, t in info["material_texture"].items():
        print(f"[S-1] マテリアル {m} -> {t or '（テクスチャなし・灰色）'}")
    print(f"[S-1] 上方向 {info['up_axis']} / 単位 {info['linear_unit']}")
    print(f"[S-1] シーン: {dest / 'shizuku.mb'}")
    print(f"[S-1] レポート: {dest / 'setup_report.json'}")
    return 0


if __name__ == "__main__":
    code = 1
    try:
        code = main()
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
    except Exception:
        traceback.print_exc()
        code = 1
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)
