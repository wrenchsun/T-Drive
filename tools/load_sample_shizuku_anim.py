"""shizuku（サンプルモデル）の骨格へ、体のアニメーション FBX を読み込む（docs/14 §11、チケット S-4）。

待機・歩きなどを再生しながら Toon と FacialController のプレビューを確かめるための、確認用のシーンを作る。
`assets/shizuku/shizuku.mb` は**開くだけで上書きしない**。結果は別名の `assets/shizuku/shizuku_anim_<名前>.mb`
（`assets/` は .gitignore。コミットしない）。

使い方（mayapy。画面なし）:
  set MAYA_DISABLE_CER=1
  "C:/Program Files/Autodesk/Maya2026/bin/mayapy.exe" tools/load_sample_shizuku_anim.py anim_idle walk_forward
  "C:/Program Files/Autodesk/Maya2026/bin/mayapy.exe" tools/load_sample_shizuku_anim.py --list        （読めるアニメの一覧）
  "C:/Program Files/Autodesk/Maya2026/bin/mayapy.exe" tools/load_sample_shizuku_anim.py --all         （全部。確認用）

オプション:
  --project <フォルダ>   プロジェクトのルート（既定: このリポジトリ）。assets/shizuku/ はこの下
  --no-save              保存しない（読み込めるかの確認だけ）

Maya の画面の中で、今開いているシーンへ読み込むとき（シーンは保存しない）:
  import sys; sys.path.insert(0, r"<プロジェクト>/tools"); import load_sample_shizuku_anim as L
  L.import_animation("walk_forward")            # 開いている shizuku のシーンへ。タイムラインもクリップの長さになる

方式: FBX の取り込みを「既存の骨格へアニメーションだけ足す」（`FBXImportMode -v exmerge`）にする。ジョイントは同じ名前の既存のものへ
アニメーションのカーブがつながり、ジョイントは増えない・メッシュ・マテリアルは触らない。終わったら、ジョイントの数が変わっていないこと・
アニメーションのカーブがついたジョイントがあることを確かめ、タイムラインの範囲をクリップの長さにする。
mayapy は環境変数 MAYA_DISABLE_CER=1 を付けて起動する（クラッシュ時のダイアログを出さない）。
"""

from __future__ import annotations

import argparse
import os
import sys
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ASSET_DIR = Path("assets") / "shizuku"
SCENE_NAME = "shizuku.mb"


# ---------------------------------------------------------------- パス（Maya 非依存）
def animations_dir(project: Path) -> Path:
    return Path(project) / ASSET_DIR / "animations"


def list_animations(project: Path = REPO) -> list[str]:
    """読める体のアニメーションの名前（拡張子なし）。"""
    d = animations_dir(project)
    return sorted(p.stem for p in d.glob("*.fbx")) if d.exists() else []


def animation_path(name: str, project: Path = REPO) -> Path:
    return animations_dir(project) / f"{name}.fbx"


def output_scene_path(name: str, project: Path = REPO) -> Path:
    return Path(project) / ASSET_DIR / f"shizuku_anim_{name}.mb"


# ---------------------------------------------------------------- Maya
class AnimationImportError(RuntimeError):
    pass


def _joints() -> list[str]:
    from maya import cmds

    return cmds.ls(type="joint", long=True) or []


def _animated_joints(joints: list[str]) -> list[str]:
    from maya import cmds

    out = []
    for j in joints:
        if cmds.listConnections(j, source=True, destination=False, type="animCurve") or cmds.listConnections(j, source=True, destination=False, type="animLayer"):
            out.append(j)
    return out


def import_animation(name: str, project: Path = REPO) -> dict:
    """開いているシーン（shizuku）の骨格へ、`animations/<name>.fbx` のアニメーションを足す。シーンは保存しない。

    戻り値: {"joints_before", "joints_after", "animated_joints", "range": (開始, 終了), "warnings": [...]}。
    ジョイントの数が変わった・アニメーションがつかなかったときは AnimationImportError。"""
    from maya import cmds, mel

    fbx = animation_path(name, project)
    if not fbx.exists():
        raise AnimationImportError(f"アニメーションがありません: {fbx}")
    if not cmds.pluginInfo("fbxmaya", query=True, loaded=True):
        cmds.loadPlugin("fbxmaya", quiet=True)
    before = _joints()
    if not before:
        raise AnimationImportError("シーンにジョイントがありません（shizuku のシーンを開いてから実行してください）")
    nodes_before = set(cmds.ls(dag=True, long=True) or [])
    warnings: list[str] = []

    mel.eval("FBXResetImport")
    mel.eval("FBXImportMode -v exmerge")  # 既存の骨格へアニメーションだけ足す
    mel.eval("FBXImportFillTimeline -v false")
    mel.eval("FBXImportConstraints -v false")
    mel.eval("FBXImportCameras -v false")
    mel.eval("FBXImportLights -v false")
    mel.eval("FBXImportSkins -v false")
    mel.eval("FBXImportShapes -v false")
    mel.eval("FBXImportUnlockNormals -v false")
    mel.eval("FBXImportMergeAnimationLayers -v true")
    mel.eval('FBXImport -f "%s"' % fbx.as_posix())

    after = _joints()
    if len(after) != len(before):
        raise AnimationImportError(f"ジョイントの数が変わりました（{len(before)} → {len(after)}）。名前が合わないジョイントが新しく作られた可能性があります")
    new_nodes = [n for n in (cmds.ls(dag=True, long=True) or []) if n not in nodes_before]
    if new_nodes:
        warnings.append(f"新しいノードが {len(new_nodes)} 個できました（例: {new_nodes[:3]}）")
    animated = _animated_joints(after)
    if not animated:
        raise AnimationImportError("どのジョイントにもアニメーションがつきませんでした（骨格の名前が FBX と合っていない可能性があります）")
    times = cmds.keyframe(animated, query=True, timeChange=True) or []
    if not times:
        raise AnimationImportError("アニメーションのキーがありません")
    start, end = min(times), max(times)
    cmds.playbackOptions(minTime=start, maxTime=end, animationStartTime=start, animationEndTime=end)
    cmds.currentTime(start)
    return {
        "joints_before": len(before),
        "joints_after": len(after),
        "animated_joints": len(animated),
        "range": (start, end),
        "warnings": warnings,
    }


def build_scene(name: str, project: Path = REPO, save: bool = True) -> dict:
    """`shizuku.mb` を開き（上書きしない）、アニメーションを足し、`shizuku_anim_<name>.mb` として別名で保存する。"""
    from maya import cmds

    src = Path(project) / ASSET_DIR / SCENE_NAME
    if not src.exists():
        raise AnimationImportError(f"{src} がありません（先に tools/setup_sample_shizuku.py を実行してください）")
    cmds.file(src.as_posix(), open=True, force=True)
    result = import_animation(name, project)
    out = output_scene_path(name, project)
    result["scene"] = out
    if save:
        cmds.file(rename=out.as_posix())  # 保存先を別名にしてから保存（shizuku.mb は触らない）
        cmds.file(save=True, type="mayaBinary", force=True)
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="shizuku の骨格へ体のアニメーションを読み込む（S-4）")
    ap.add_argument("names", nargs="*", help="アニメーションの名前（拡張子なし）。例: anim_idle walk_forward")
    ap.add_argument("--project", default=REPO.as_posix())
    ap.add_argument("--list", action="store_true", help="読めるアニメーションの一覧を出して終わる")
    ap.add_argument("--all", action="store_true", help="全部のアニメーションを順に確かめる")
    ap.add_argument("--no-save", action="store_true", help="保存しない")
    args = ap.parse_args(argv)
    project = Path(args.project).resolve()
    names = list_animations(project)
    if args.list:
        print("\n".join(names) if names else f"（{animations_dir(project)} にアニメーションがありません）")
        return 0
    targets = names if args.all else args.names
    if not targets:
        ap.error("アニメーションの名前を指定してください（--list で一覧）")
    unknown = [n for n in targets if n not in names]
    if unknown:
        print(f"ERROR 見つかりません: {unknown}（--list で一覧）")
        return 2

    os.environ.setdefault("MAYA_DISABLE_CER", "1")
    import maya.standalone

    maya.standalone.initialize(name="python")
    failed = 0
    try:
        for n in targets:
            try:
                r = build_scene(n, project, save=not args.no_save)
                print(
                    f"OK   {n}: ジョイント {r['joints_after']}（変化なし）・アニメーション付き {r['animated_joints']} 本・"
                    f"{r['range'][0]:g}〜{r['range'][1]:g} フレーム" + (f" → {r['scene'].name}" if not args.no_save else "")
                    + "".join(f"\n     注意: {w}" for w in r["warnings"])
                )
            except Exception as exc:  # noqa: BLE001  1 本の失敗で全体を止めない
                failed += 1
                print(f"FAIL {n}: {exc}")
                if not isinstance(exc, AnimationImportError):
                    traceback.print_exc()
    finally:
        sys.stdout.flush()
        maya.standalone.uninitialize()
    return 1 if failed else 0


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    os._exit(code)  # mayapy の終了処理で固まらないように
