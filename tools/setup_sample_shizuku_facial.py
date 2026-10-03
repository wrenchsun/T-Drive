"""shizuku（サンプルモデル）の FacialController データを手続きで作り、ベイク・出力まで通す（mayapy。画面なし）。

  "C:/Program Files/Autodesk/Maya2026/bin/mayapy.exe" tools/setup_sample_shizuku_facial.py [--project <プロジェクトのルート>]

やること（何度実行してもよい。`assets/shizuku/shizuku.mb` は開くだけで上書きしない）:
  1. `assets/shizuku/shizuku.mb` を開き、tdrive_facial.session で shizuku 用のデータ（顔 mdl_face02、基準ボーン bone_head 自動検出、
     プロファイル shizuku、5x3・Yaw 90 / Pitch 45）を作る
  2. Neutral のキーを手続きで入れる（下の KEYS）→ ミラーで反対側を自動生成 → 感情レイヤー Joy にキーを入れる
  3. 全点ベイク → 検証 → `<project>/facial/shizuku/shizuku.fcpose.json` を保存（曲線名と数値だけ。絶対パスを含まない。コミットしてよい）
  4. ベイク済みのシーンを `assets/shizuku/shizuku_facial.mb` に別名で保存（git 管理外のフォルダ）
  5. Unity 向けの出力 `<project>/facial/shizuku/export/unity/`（shizuku.fbx + shizuku.fcpose。git 管理外）

向きの決め方: 顔は +Z を向く。キャラクターの左 = +X（bone_eye_L が +X 側にある）。Yaw 正 = カメラが +X 側 = キャラクターの左側面が見える。
`bs.mouth_left` は頂点を +X へ動かす（実測: 平均 +0.36cm）ので、Yaw 正の位置では「口がカメラ側へ寄る」補正として mouth_left を入れる。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
os.environ.setdefault("TDRIVE_ROOT", REPO.as_posix())
sys.path.insert(0, str(REPO / "maya" / "scripts"))

import maya.standalone  # noqa: E402

CHARACTER = "shizuku"
FACE = "mdl_face02"
PROFILE = "shizuku"
SCENE_IN = REPO / "assets" / "shizuku" / "shizuku.mb"
SCENE_OUT = REPO / "assets" / "shizuku" / "shizuku_facial.mb"

# 目のボーンの小さな回転（クォータニオン [x, y, z, w]。Y 軸まわり）
def _qy(deg: float):
    import math

    h = math.radians(deg) / 2.0
    return (0.0, math.sin(h), 0.0, math.cos(h))


# (row, col) → (曲線, ボーン)。row 0 = 下から見上げる（-Pitch）/ 2 = 上から見下ろす（+Pitch）、col 2 = 正面、4 = Yaw +90（キャラクターの左側面）
# ミラーでの自動生成は「列 2 より右（+Yaw）」から左へ。値は控えめ（0〜1）。
NEUTRAL_KEYS = {
    (1, 2): ({"bs.jaw_open": 0.03}, {}),  # 正面: ほぼ空（空のキーだと焼いたシェイプが無くなるのでごく小さく）
    (1, 3): ({"bs.mouth_left": 0.35}, {}),  # Yaw +45: 口をカメラ側（+X）へ
    (1, 4): (  # Yaw +90: もっと口を寄せ、目のボーンをカメラ側へ少し回す
        {"bs.mouth_left": 0.6, "bs.mouth_smile_L": 0.15},
        {"bone_eye_L": {"r": _qy(8.0)}, "bone_eye_R": {"r": _qy(8.0)}},
    ),
    (2, 2): ({"bs.jaw_open": 0.12, "bs.brow_innerUp": 0.3}, {}),  # 高い位置から（+Pitch）: 顎を少し開き眉の内側を上げる
    (0, 2): ({"bs.brow_down": 0.3, "bs.eye_wide": 0.2}, {}),  # 低い位置から（-Pitch）: 眉を下げ目を少し見開く
}
# 感情レイヤー Joy（Neutral との差分で焼かれる）
JOY_KEYS = {
    (1, 2): {"bs.mouth_smile_L": 0.6, "bs.mouth_smile_R": 0.6, "bs.eye_happy_L": 0.5, "bs.eye_happy_R": 0.5},
    (1, 4): {"bs.mouth_smile_L": 0.4, "bs.eye_happy_L": 0.3},
}


def _no_abs_paths(path: Path) -> list[str]:
    """fcpose.json に絶対パスらしい文字列（ドライブ文字・UNC・リポジトリのパス）が無いか確かめる。見つけたものを返す。"""
    import re

    text = path.read_text(encoding="utf-8")
    bad = re.findall(r'"[A-Za-z]:[\\/][^"]*"|"\\\\[^"]*"', text)
    if REPO.as_posix().lower() in text.lower().replace("\\\\", "/").replace("\\", "/"):
        bad.append(REPO.as_posix())
    return bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=REPO.as_posix(), help="プロジェクトのルート（facial/ をここに作る。既定: リポジトリ）")
    args = ap.parse_args()
    project_root = Path(args.project).resolve()

    from maya import cmds

    from tdrive import project

    project.set_root(project_root)
    from tdrive_facial import export, session
    from tdrive_facial.core import model

    if not SCENE_IN.exists():
        print(f"ERROR {SCENE_IN} がありません（先に tools/setup_sample_shizuku.py を実行してください）")
        return 2

    cmds.file(SCENE_IN.as_posix(), open=True, force=True)  # 開くだけ。shizuku.mb は上書きしない
    session.on_new_scene()  # 前回のセッションの状態を持ち越さない
    s = session.current()
    if s.presenters is not None:
        s.close()

    doc = s.new(CHARACTER, mesh=FACE, profile=PROFILE)
    print(f"document: asset={doc.asset} mesh={doc.target.mesh} baseBone={doc.grid.base_bone} profile={doc.profile}")
    s.set_grid(cols=5, rows=3, yaw_range=90.0, pitch_range=45.0)
    if doc.grid.base_bone != "bone_head":
        s.set_base_bone("bone_head")

    # --- Neutral のキー ---
    for (row, col), (curves, bones) in NEUTRAL_KEYS.items():
        r = s.select_point(row, col)
        if r.status not in ("selected", "same_point"):
            raise RuntimeError(f"select_point({row},{col}) {r}")
        for name, v in curves.items():
            res = s.set_curve(name, v, apply=False)
            if not res.ok:
                raise RuntimeError(f"set_curve {name}: {res}")
        for bname, comp in bones.items():
            res = s.set_bone(bname, model.BoneOffset(t=tuple(comp.get("t", (0.0, 0.0, 0.0))), r=tuple(comp["r"])), apply=False)
            if not res.ok:
                raise RuntimeError(f"set_bone {bname}: {res}")
        res = s.save_point()
        if not res.ok:
            raise RuntimeError(f"save_point({row},{col}): {res}")
    s.end_edit(quiet=True)
    gen = s.generate()
    print(f"generate: {gen}")

    # --- 感情レイヤー Joy ---
    lr = s.add_layer("Joy", select=True)
    if not lr.ok:
        raise RuntimeError(f"add_layer: {lr}")
    s.set_layer_emotion_curve(lr.index, "emo_joy")
    for (row, col), curves in JOY_KEYS.items():
        s.select_point(row, col)
        for name, v in curves.items():
            s.set_curve(name, v, apply=False)
        res = s.save_point()
        if not res.ok:
            raise RuntimeError(f"Joy save_point({row},{col}): {res}")
    s.end_edit(quiet=True)
    s.generate(all_layers=True)

    # --- ベイク ---
    t0 = time.perf_counter()
    rep = s.bake_all()
    bake_s = time.perf_counter() - t0
    print(rep.summary())
    for w in rep.warnings:
        print(f"  bake warning: {w}")

    # --- 検証 ---
    issues = s.validate()
    counts: dict[str, int] = {}
    for i in issues:
        sev = str(getattr(i, "severity", "?"))
        counts[sev] = counts.get(sev, 0) + 1
    print(f"validation: {len(issues)} issues {counts}")
    for i in issues:
        print(f"  [{getattr(i, 'severity', '?')}] {getattr(i, 'code', '')} {getattr(i, 'message', i)}")

    # --- 保存 ---
    out_json = session.default_path(CHARACTER)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    s.save(out_json, overwrite=True)
    bad = _no_abs_paths(out_json)
    print(f"saved: {out_json}（絶対パスらしい文字列: {bad or 'なし'}）")
    if bad:
        raise RuntimeError(f"fcpose.json に絶対パスが入っています: {bad}")

    cmds.file(rename=SCENE_OUT.as_posix())
    cmds.file(save=True, type="mayaBinary", force=True)
    print(f"saved scene: {SCENE_OUT}")

    # --- Unity 向けの出力 ---
    t0 = time.perf_counter()
    res = export.export_unity(s.require(), out_json)
    exp_s = time.perf_counter() - t0
    fbx = Path(res["fbx"])
    print(f"export: {export.UnityExportResult.__name__} fbx={fbx} fcpose={res['fcpose']}")
    print(f"export warnings: {res['warnings']}")

    n_fc = len(rep.created) + len(rep.replaced)
    layer_points = {l.name: len(l.points) for l in s.require().layers}
    print("--- SUMMARY ---")
    print(f"layers/points: {layer_points}")
    print(f"targets created: {len(rep.created)} replaced: {len(rep.replaced)} removed: {len(rep.removed)} empty: {len(rep.empty)} (FC_ {n_fc})")
    print(f"vertex total: {rep.total_vertices} culled: {rep.culled_vertices}")
    print(f"bake seconds: {bake_s:.2f}")
    print(f"export seconds: {exp_s:.1f} (reported {res['seconds']:.1f})")
    print(f"FBX size: {fbx.stat().st_size / 1024 / 1024:.2f} MB  blendshapes: {res['blendshapes']}  fc_count: {res['fc_count']}")
    print(f"validation: {counts or 'no issues'}")
    return 0


if __name__ == "__main__":
    maya.standalone.initialize(name="python")
    code = 1
    try:
        code = main()
    except Exception:
        traceback.print_exc()
    finally:
        sys.stdout.flush()
        sys.stderr.flush()
        try:
            maya.standalone.uninitialize()
        finally:
            os._exit(code)
