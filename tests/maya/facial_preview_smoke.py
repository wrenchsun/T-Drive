"""FacialController のカメラ連動プレビュー（preview_rig）と出力（export）のスモークテスト（mayapy で実行。画面なし）。

  "C:/Program Files/Autodesk/Maya2026/bin/mayapy.exe" tests/maya/facial_preview_smoke.py

合成の小さな頭（tests/maya/facial_fixture.py）で確かめる。ファイルはすべて一時フォルダ（プロジェクトのルートもそこへ向ける）。
shizuku（assets/shizuku/shizuku.mb）があれば第 2 部で結合テストを行う（`TDRIVE_FACIAL_SHIZUKU=0` で飛ばす。開くだけで保存しない）。
"""

from __future__ import annotations

import copy
import math
import os
import shutil
import sys
import tempfile
import time
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
os.environ.setdefault("TDRIVE_ROOT", REPO.as_posix())
sys.path.insert(0, str(REPO / "maya" / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import maya.standalone  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []
INFO: list[str] = []
STATS = {"w": 0.0, "a": 0.0, "n": 0}
W_TOL = 1e-4
A_TOL = 1e-3


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(cond), detail))


def ang_diff(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)


def spherical(center, yaw_deg: float, pitch_deg: float, dist: float = 60.0):
    """forwardAxis +Z・頭の回転なしのとき、Yaw / Pitch に当たるカメラ位置（Yaw 正 = +X 側。session.view_angles で確かめる）。"""
    y, p = math.radians(yaw_deg), math.radians(pitch_deg)
    return (
        center[0] + dist * math.sin(y) * math.cos(p),
        center[1] + dist * math.sin(p),
        center[2] + dist * math.cos(y) * math.cos(p),
    )


def fc_plugs(face: str) -> list[str]:
    from tdrive_facial import scene
    from tdrive_facial.core import naming

    return [c.plug for c in scene.list_curves(face, include_fc=True) if naming.is_fc_name(c.alias)]


def run_mini() -> None:
    from maya import cmds

    import facial_fixture
    from tdrive import project
    from tdrive_facial import bake as bakemod
    from tdrive_facial import export, preview_rig as pr, scene
    from tdrive_facial import session as S
    from tdrive_facial.core import autofill, evaluate, fcpose_io, naming
    from tdrive_facial.core import fctrack as fct
    from tdrive_facial.core.model import Layer, SourcePose

    tmp = Path(tempfile.mkdtemp(prefix="tdrive_fprev_"))
    project.set_root(tmp)
    ids = facial_fixture.build_mini_head()
    face = scene.resolve_mesh("mini_face")
    asset = "mini"
    doc0 = facial_fixture.make_doc()
    autofill.generate_from_keys(doc0)
    doc_path = tmp / "mini.fcpose.json"
    fcpose_io.save(doc0, doc_path)
    s = S.current()
    s.open(doc_path)
    doc = s.doc
    rep = s.bake_all()
    check("準備: 3x3 + Joy を焼いた（FC_* 18 本）", len(rep.created) == 18, rep.summary())
    cam1 = cmds.camera(name="pvcam1")[0]
    cam2 = cmds.camera(name="pvcam2")[0]
    cmds.setAttr(cam1 + ".translate", 0, 10, 60)
    head_pos = lambda: tuple(cmds.xform("head", query=True, worldSpace=True, translation=True))  # noqa: E731

    def compare(tag: str, camera: str = cam1, **kw) -> tuple[float, float]:
        """rig の重み・角度 = Python の計算・session.view_angles（重み 1e-4、角度 1e-3°）。"""
        w = pr.current_weights(asset)
        ev = pr.evaluate_python(doc, camera, **kw)
        dw = max((abs(w[k] - ev["weights"][k]) for k in w), default=0.0)
        miss = set(ev["weights"]) ^ set(w)
        ya, pa = pr.current_angles(asset)
        if kw.get("manual") is None and not kw.get("use_manual") and not cmds.getAttr(f"{pr.find_rig(asset)}.useManual"):
            sy, sp = s.view_angles(camera)
            da = max(ang_diff(ya, sy), abs(pa - sp))
        else:
            da = max(ang_diff(ya, ev["yaw"]), abs(pa - ev["pitch"]))
        STATS["w"], STATS["a"], STATS["n"] = max(STATS["w"], dw), max(STATS["a"], da), STATS["n"] + 1
        check(f"{tag}: 重み = Python（1e-4）・角度 = view_angles（1e-3°）", dw < W_TOL and da < A_TOL and not miss, f"dw={dw:.2e} da={da:.2e} miss={miss}")
        return dw, da

    # ------------------------------------------------------------------ 組む・構造
    before_nodes = set(cmds.ls(long=True))
    report = pr.build_ex(doc, cam1)
    rig = report.rig
    check("build: rig の transform ができる（tdFacialPreview_<asset>・tdPreviewOnly）", rig == "tdFacialPreview_mini" and pr.exists(asset) and cmds.getAttr(rig + ".tdPreviewOnly"))
    kattrs = set(cmds.listAttr(rig, keyable=True) or [])
    check(
        "build: キー可アトリビュート enable / alpha / useManual / manualYaw / manualPitch / emotion_Joy",
        {"enable", "alpha", "useManual", "manualYaw", "manualPitch", "emotion_Joy"} <= kattrs and "emotion_Neutral" not in kattrs,
        str(sorted(kattrs)),
    )
    check("build: 出力 outYaw / outPitch がある（キー不可）", cmds.attributeQuery("outYaw", node=rig, exists=True) and "outYaw" not in kattrs)
    created = set(cmds.ls(long=True)) - before_nodes
    types = {cmds.nodeType(n) for n in created if cmds.objExists(n)}
    check("build: 標準ノードだけ（transform / decomposeMatrix / expression）・プラグインのノードなし", types <= {"transform", "decomposeMatrix", "expression"} and "expression" in types, str(types))
    check("build: expression は 1 個・alwaysEvaluate なし", len(cmds.ls(type="expression")) == 1 and not cmds.expression(report.expression, query=True, alwaysEvaluate=True))
    INFO.append(f"mini 3x3 x 2 レイヤー: ターゲット {report.targets}、式 {report.expression_chars} 文字")
    check("build: 18 ターゲットを配線・警告なし", report.targets == 18 and not report.warnings, str(report))
    check("is_stale: 作った直後は False", not pr.is_stale(doc))
    check("camera: 今つながっているカメラ", pr.get_camera(asset) == cmds.ls(cam1, long=True)[0])

    # ------------------------------------------------------------------ 符号（Yaw 正 = +X 側 = キャラクターの左側 / Pitch 正 = 上）
    c = head_pos()
    cmds.setAttr(cam1 + ".translate", *spherical(c, 30, 20))
    sy, sp = s.view_angles(cam1)
    check("符号: カメラを +X・上へ置くと session.view_angles は Yaw 正・Pitch 正（30° / 20°）", abs(sy - 30) < 1e-6 and abs(sp - 20) < 1e-6, f"{sy} {sp}")
    ya, pa = pr.current_angles(asset)
    check("符号: rig の outYaw / outPitch も同じ符号（30° / 20°）", abs(ya - 30) < A_TOL and abs(pa - 20) < A_TOL, f"{ya} {pa}")
    cmds.setAttr(cam1 + ".translate", *spherical(c, -40, -10))
    ya, pa = pr.current_angles(asset)
    check("符号: -X・下は Yaw 負・Pitch 負（-40° / -10°）", abs(ya + 40) < A_TOL and abs(pa + 10) < A_TOL, f"{ya} {pa}")

    # ------------------------------------------------------------------ 24 以上のカメラ位置（基本: forward +Z・頭の回転なし）
    combos = [
        (0, 0), (30, 0), (-30, 0), (45, 0), (90, 0), (-90, 0), (100, 0), (-105, 0), (120, 0), (180, 0),
        (0, 20), (0, -20), (0, 45), (0, 52), (0, -50), (0, 70), (30, 20), (-30, -20), (90, 45), (100, 50),
        (-100, -50), (60, 30), (20, -40), (150, 30), (10, 5), (0, 0.0003), (-0.0003, 0),
    ]
    for yaw, pitch in combos:
        cmds.setAttr(cam1 + ".translate", *spherical(c, yaw, pitch, 55.0))
        compare(f"カメラ Yaw {yaw} / Pitch {pitch}")
    # 格子の点の上（そこでは Neutral の点の重み 1・他 0）
    for r in range(3):
        for col in range(3):
            s.camera_to_point(r, col, cam1)
            compare(f"点 R{r} C{col} の上")
            w = pr.current_weights(asset)
            name = naming.morph_name(asset, "Neutral", r, col)
            others = max((v for k, v in w.items() if k != name and not k.startswith("FC_mini_Joy")), default=0.0)
            check(f"点 R{r} C{col}: Neutral の点 = 1・ほかの Neutral = 0・感情 0 なら Joy = 0", abs(w.get(name, -1) - 1.0) < 1e-6 and others < 1e-6 and max(v for k, v in w.items() if "Joy" in k) < 1e-9, f"{w.get(name)} {others}")
    # 点の間（4 点の中央）・端のフェードの途中
    cmds.setAttr(cam1 + ".translate", *spherical(c, 45, 22.5, 55.0))
    compare("点の間（4 点の中央）")
    w = pr.current_weights(asset)
    quad = [naming.morph_name(asset, "Neutral", r, col) for r in (1, 2) for col in (1, 2)]
    check("点の間: 4 点が 0.25 ずつ", all(abs(w[q] - 0.25) < 1e-6 for q in quad), str([w[q] for q in quad]))
    cmds.setAttr(cam1 + ".translate", *spherical(c, 97.5, 0, 55.0))
    w = pr.current_weights(asset)
    check("端のフェード: 範囲 + フェード幅の半分で 0.5 倍", abs(w[naming.morph_name(asset, "Neutral", 1, 2)] - 0.5) < 1e-3, str(w[naming.morph_name(asset, "Neutral", 1, 2)]))
    cmds.setAttr(cam1 + ".translate", *spherical(c, 0, 0, 55.0))

    # ------------------------------------------------------------------ 感情・alpha・enable・手動角度
    s.camera_to_point(1, 2, cam1)
    joy_attr = "emotion_Joy"
    for v in (0.0, 0.5, 1.0):
        cmds.setAttr(f"{rig}.{joy_attr}", v)
        compare(f"感情 Joy = {v}")
        w = pr.current_weights(asset)
        check(f"感情 Joy = {v}: 点の上の Joy の重み = {v}", abs(w[naming.morph_name(asset, "Joy", 1, 2)] - v) < 1e-6, str(w[naming.morph_name(asset, "Joy", 1, 2)]))
    cmds.setAttr(f"{rig}.{joy_attr}", 0.7)
    cmds.setAttr(f"{rig}.alpha", 0.4)
    compare("alpha 0.4・感情 0.7")
    w = pr.current_weights(asset)
    check("alpha: Neutral の重みが 0.4 倍", abs(w[naming.morph_name(asset, "Neutral", 1, 2)] - 0.4) < 1e-6)
    cmds.setAttr(f"{rig}.alpha", 1.0)
    pr.set_enabled(asset, False)
    w = pr.current_weights(asset)
    check("enable オフ: すべて 0（角度は出る）", max(abs(v) for v in w.values()) < 1e-12 and not pr.is_enabled(asset) and abs(pr.current_angles(asset)[0] - 30.0) < 100)
    compare("enable オフ")
    pr.set_enabled(asset, True)
    compare("enable オン")
    cmds.setAttr(f"{rig}.useManual", 1)
    cmds.setAttr(f"{rig}.manualYaw", -30.0)
    cmds.setAttr(f"{rig}.manualPitch", 10.0)
    compare("手動角度 (-30, 10)")
    ya, pa = pr.current_angles(asset)
    check("手動角度: outYaw / outPitch = 指定値（カメラを動かしても変わらない）", abs(ya + 30) < 1e-9 and abs(pa - 10) < 1e-9)
    cmds.setAttr(cam1 + ".translate", 300, 200, -400)
    check("手動角度: カメラを動かしても重みは同じ", abs(pr.current_angles(asset)[0] + 30) < 1e-9)
    compare("手動角度（カメラ移動後）")
    cmds.setAttr(f"{rig}.useManual", 0)
    cmds.setAttr(f"{rig}.{joy_attr}", 0.0)

    # ------------------------------------------------------------------ カメラの入れ替え
    cmds.setAttr(cam1 + ".translate", *spherical(c, 40, 10))
    cmds.setAttr(cam2 + ".translate", *spherical(c, -60, -20))
    got = pr.set_camera(asset, cam2)
    check("set_camera: つなぎ替えたカメラが返る・get_camera も同じ", got == cmds.ls(cam2, long=True)[0] and pr.get_camera(asset) == got)
    compare("カメラ入れ替え後（cam2）", camera=cam2)
    cmds.setAttr(cam1 + ".translate", *spherical(c, 80, 40))
    compare("入れ替え後に cam1 を動かしても影響なし（cam2 のまま）", camera=cam2)
    cmds.setAttr(cam2 + ".translate", *spherical(c, 10, 5))
    compare("入れ替え後に cam2 を動かすと追従", camera=cam2)
    pr.set_camera(asset, cam1)
    compare("cam1 へ戻す")

    # ------------------------------------------------------------------ 頭の回転・forwardAxis・centerOffset（組み直しが要る）
    variants = [
        ("頭 Y 35°（forward +Z）", 35.0, "+Z", (0.0, 0.0, 0.0)),
        ("頭 Y 160°", 160.0, "+Z", (0.0, 0.0, 0.0)),
        ("forward -Z・頭 Y 20°", 20.0, "-Z", (0.0, 0.0, 0.0)),
        ("forward +X・頭 Y -50°", -50.0, "+X", (0.0, 0.0, 0.0)),
        ("forward -X", 0.0, "-X", (0.0, 0.0, 0.0)),
        ("centerOffset (0, 2, 1.5)・頭 Y 35°", 35.0, "+Z", (0.0, 2.0, 1.5)),
        ("centerOffset (3, -1, 4)・forward +X・頭 Y 120°", 120.0, "+X", (3.0, -1.0, 4.0)),
    ]
    offs = [(0, 0, 60), (40, 0, 40), (-40, 10, 30), (60, 0, 0), (0, 50, 40), (-30, -30, 50), (25, 25, -35), (0, 70, 5)]
    for label, ry, axis, off in variants:
        cmds.setAttr("head.rotateY", ry)
        doc.grid.forward_axis = axis
        doc.grid.center_offset = off
        stale = pr.is_stale(doc)
        check(f"{label}: forwardAxis / centerOffset を変えると is_stale = True（頭の回転だけなら False）", stale == (axis != "+Z" or off != (0.0, 0.0, 0.0)), f"stale={stale}")
        pr.build(doc, cam1)
        check(f"{label}: 作り直すと is_stale = False", not pr.is_stale(doc))
        h = head_pos()
        for i, o in enumerate(offs):
            cmds.setAttr(cam1 + ".translate", h[0] + o[0], h[1] + o[1], h[2] + o[2])
            compare(f"{label} 位置 {i}")
    cmds.setAttr("head.rotateY", 0)
    doc.grid.forward_axis = "+Z"
    doc.grid.center_offset = (0.0, 0.0, 0.0)
    pr.build(doc, cam1)

    # ------------------------------------------------------------------ 表情での弱め（R-10・プレビューのみ）
    doc.policy.expression_dampen = 0.5
    doc.intensity_curves = ["bs.mouth_open", "bs.smile_L"]
    check("弱め: 設定を変えると is_stale = True", pr.is_stale(doc))
    pr.build(doc, cam1)
    s.camera_to_point(1, 2, cam1)
    for mo, sm in ((0.0, 0.0), (0.5, 0.0), (0.6, 0.7), (1.0, 1.0)):
        cmds.setAttr("bs.mouth_open", mo)
        cmds.setAttr("bs.smile_L", sm)
        compare(f"弱め: mouth_open {mo} + smile_L {sm}")
    w = pr.current_weights(asset)
    check("弱め: 強さ 1 で 1 - 0.5 = 0.5 倍", abs(w[naming.morph_name(asset, "Neutral", 1, 2)] - 0.5) < 1e-6, str(w[naming.morph_name(asset, "Neutral", 1, 2)]))
    cmds.setAttr("bs.mouth_open", 0)
    cmds.setAttr("bs.smile_L", 0)
    doc.intensity_curves = []
    pr.build(doc, cam1)

    # ------------------------------------------------------------------ 焼き直し（rig があるまま）・基準姿勢との共存
    s.camera_to_point(1, 2, cam1)
    exprs = cmds.ls(type="expression")
    plugs = fc_plugs(face)
    rep2 = s.bake_all()
    check("再ベイク（rig あり）: 置き換えのみ", not rep2.created and len(rep2.replaced) == 18, rep2.summary())
    srcs = [cmds.listConnections(p, source=True, destination=False) or [] for p in plugs]
    check("再ベイク: 式の出力接続が元へ戻っている（全 FC_*）", all(x and x[0] in exprs for x in srcs), str(sum(1 for x in srcs if x)))
    check("再ベイク: is_stale = False・重み = Python", not pr.is_stale(doc))
    compare("再ベイク後")
    w_before = pr.current_weights(asset)
    s.begin_edit()
    during = [cmds.getAttr(p) for p in plugs]
    during_src = [cmds.listConnections(p, source=True, destination=False) for p in plugs]
    check("基準姿勢（begin_edit）: FC_* の重みは 0・式の出力は一時的に切れる", max(abs(v) for v in during) == 0.0 and not any(during_src))
    check("基準姿勢: rig 自体（アトリビュート・式）は残る", cmds.objExists(rig) and len(cmds.ls(type="expression")) == 1 and not pr.is_stale(doc))
    s.end_edit()
    srcs = [cmds.listConnections(p, source=True, destination=False) or [] for p in plugs]
    check("end_edit: 式の出力接続が戻る", all(x and x[0] in exprs for x in srcs))
    w_after = pr.current_weights(asset)
    check("end_edit: 重みが元と同じ", max(abs(w_after[k] - w_before[k]) for k in w_before) < 1e-9, "")
    cmds.setAttr(cam1 + ".translate", *spherical(c, -35, 15))
    compare("end_edit のあと、カメラを動かすと追従")
    # 編集中にポーズを当てても rig が戦わない
    s.begin_edit()
    s.select_point(1, 2)
    smile_while = cmds.getAttr("bs.smile_L")
    s.end_edit()
    check("基準姿勢中のポーズ当て: 例外なし（点 (1,2) の smile_L が当たった）", smile_while > 0.0, str(smile_while))
    compare("編集のあと")

    # ------------------------------------------------------------------ 格子の変更（3x3 → 5x3）・新しい点を焼く
    res = s.resize(5, 3)
    check("resize 5x3: 成功", res.ok, getattr(res, "message", ""))
    # 変更（意図した挙動変更）: session 経由の resize / ベイクは rig を自動で作り直すので、is_stale は False のまま。古くなる検出は rig の署名で別に確かめる
    check("resize: session 経由なら rig が自動で作り直される（is_stale = False・署名が変わった）", not pr.is_stale(doc) and cmds.getAttr(pr.find_rig(asset) + ".tdFacialSignature") != "")
    s.generate(all_layers=True)
    rep3 = s.bake_all()
    check("5x3 を焼いた", len(rep3.created) + len(rep3.replaced) >= 20, rep3.summary())
    check("焼いたあと（新しい点が増えた）も自動で作り直される（is_stale = False・ターゲットが増えた）", not pr.is_stale(doc) and len(pr.current_weights(asset)) >= 20, str(len(pr.current_weights(asset))))
    r3 = pr.build_ex(doc, cam1)
    check("作り直し: is_stale = False・ターゲット = 焼いた FC_* 全部", not pr.is_stale(doc) and r3.targets == len(fc_plugs(face)), f"{r3.targets} {len(fc_plugs(face))}")
    check("作り直し: rig の transform・感情アトリビュートは同じ（キーを残す）", r3.rig == rig)
    INFO.append(f"mini 5x3 x 2 レイヤー: ターゲット {r3.targets}、式 {r3.expression_chars} 文字")
    cmds.setAttr(f"{rig}.{joy_attr}", 0.6)
    for yaw, pitch in ((0, 0), (22.5, 0), (45, 22.5), (67.5, -22.5), (-45, 10), (-95, 0), (30, 55), (100, 20), (0, 20), (-10, -45)):
        cmds.setAttr(cam1 + ".translate", *spherical(c, yaw, pitch, 55.0))
        compare(f"5x3: Yaw {yaw} / Pitch {pitch}")
    for r in range(3):
        for col in range(5):
            s.camera_to_point(r, col, cam1)
            compare(f"5x3: 点 R{r} C{col} の上")
    cmds.setAttr(f"{rig}.{joy_attr}", 0.0)

    # ------------------------------------------------------------------ 評価時間
    def time_eval(camera: str, plug: str, n: int = 300, mesh: bool = False) -> tuple[float, int]:
        """1 評価 = カメラを動かして FC_* の重み（mesh=True なら顔メッシュの頂点 = スキン + blendShape の変形後）を読む。(ms, 重みの種類数)"""
        seen = set()
        cmds.setAttr(camera + ".translate", 0.0, c[1], c[2] + 60.0)  # 正面から横へ動かす
        t0 = time.perf_counter()
        for i in range(n):
            cmds.setAttr(camera + ".tx", (i % 50) * 1.3)
            if mesh:
                cmds.xform(face + ".vtx[10]", query=True, worldSpace=True, translation=True)
            v = cmds.getAttr(plug)
            seen.add(round(v, 6))
        return (time.perf_counter() - t0) / n * 1000.0, len(seen)

    pr.delete(asset)
    plug0 = scene.weight_plug(*_node_idx(scene, face, naming.morph_name(asset, 'Neutral', 1, 2)))  # カメラの動く範囲で重みが変わる点
    base, _ = time_eval(cam1, plug0)
    base_m, _ = time_eval(cam1, plug0, mesh=True)
    pr.build(doc, cam1)
    t5, seen5 = time_eval(cam1, plug0)
    t5m, _ = time_eval(cam1, plug0, mesh=True)
    INFO.append(
        f"評価時間 5x3 x 2 レイヤー（{r3.targets} ターゲット）: 重みの取得 {t5 - base:.3f} ms / 評価（rig なし {base:.3f} ms を引いた値。{t5:.3f} ms）、"
        f"顔メッシュの頂点まで {t5m - base_m:.3f} ms / 評価（rig なし {base_m:.3f} ms）、重みの種類 {seen5}"
    )
    check("評価時間: 5x3 x 2 レイヤーが 1 評価 5 ms 未満・ループ中に重みが変わっている", t5 - base < 5.0 and seen5 > 3, f"{t5 - base:.3f} {seen5}")
    pr.delete(asset)
    # 合成の 9x5 x 4 レイヤー（FC_* を 180 本足す）
    sdoc = copy.deepcopy(doc)
    sdoc.asset = "synth"
    sdoc.grid.cols, sdoc.grid.rows = 9, 5
    sdoc.layers = [Layer("Neutral"), Layer("A"), Layer("B"), Layer("C")]
    node = scene.primary_blend_shape(face)
    geo = scene.geometry_index(node, face)
    import numpy as np

    made = []
    for lay in sdoc.layers:
        for r in range(5):
            for col in range(9):
                nm = naming.morph_name("synth", lay.name, r, col)
                scene.write_target_delta(node, nm, np.array([[0.01, 0.0, 0.0]]), [0], geo)
                made.append(nm)
    rs = pr.build_ex(sdoc, cam1)
    plug_s = scene.weight_plug(node, scene.target_indices(node)[naming.morph_name('synth', 'Neutral', 2, 5)])
    ts, seen_s = time_eval(cam1, plug_s)
    tsm, _ = time_eval(cam1, plug_s, mesh=True)
    INFO.append(
        f"評価時間 9x5 x 4 レイヤー（{rs.targets} ターゲット、式 {rs.expression_chars} 文字）: 重みの取得 {ts - base:.3f} ms / 評価（{ts:.3f} ms）、"
        f"顔メッシュの頂点まで {tsm - base_m:.3f} ms / 評価、重みの種類 {seen_s}"
    )
    check("評価時間: 合成 9x5 x 4 レイヤーが 1 評価 10 ms 未満・重みが変わっている", ts - base < 10.0 and seen_s > 3, f"{ts - base:.3f} {seen_s}")
    check("合成 9x5 x 4 レイヤー: 配線 180 ターゲット・重み = Python", rs.targets == 180, str(rs))
    cmds.setAttr(f"{rs.rig}.emotion_A", 0.3)
    cmds.setAttr(f"{rs.rig}.emotion_C", 1.0)
    for yaw, pitch in ((17, 9), (-63, -30), (91, 44)):
        cmds.setAttr(cam1 + ".translate", *spherical(c, yaw, pitch, 55.0))
        w = pr.current_weights("synth")
        ev = pr.evaluate_python(sdoc, cam1)
        dw = max(abs(w[k] - ev["weights"][k]) for k in w)
        check(f"合成 9x5 x 4 レイヤー: Yaw {yaw} / Pitch {pitch} で重み = Python", dw < W_TOL and abs(sum(w.values())) > 0, f"{dw}")
    pr.delete("synth")
    scene.delete_targets(node, made)
    check("合成のターゲットを片付けた", not [a for a in scene.target_indices(node) if a.startswith("FC_synth")])

    # ------------------------------------------------------------------ delete: rig のノードだけ消える・重みは 0・接続なし
    nodes0 = set(cmds.ls(long=True))
    pr.build(doc, cam1)
    cmds.setAttr(cam1 + ".translate", *spherical(c, 10, 5))
    nonzero = max(abs(v) for v in pr.current_weights(asset).values())
    check("delete 前: 重みが 0 でない", nonzero > 0.1, str(nonzero))
    check("delete: True", pr.delete(asset) and not pr.exists(asset) and not pr.delete(asset))
    nodes1 = set(cmds.ls(long=True))
    check("delete: 作ったノードだけ消え、ほかは 1 つも増減しない", nodes1 == nodes0, str(sorted(nodes1 ^ nodes0)))
    plugs = fc_plugs(face)
    check("delete: FC_* の重みは 0・接続なし", all(cmds.getAttr(p) == 0.0 and not cmds.listConnections(p, source=True, destination=False) for p in plugs))
    check("delete: 元からあるシェイプ（bs.*）・FC_* ターゲット自体は残る", len(plugs) == r3.targets and cmds.objExists("bs.mouth_open"))

    # ------------------------------------------------------------------ キーに焼く（R-19 / F2-8）
    rep_b = pr.build_ex(doc, cam1)
    rig = rep_b.rig
    cmds.setAttr(f"{rig}.{joy_attr}", 0.0)
    for f, (yaw, pitch) in ((1, (0, 0)), (8, (35, 10)), (16, (-70, -20)), (24, (100, 30))):
        cmds.currentTime(f, edit=True)
        cmds.setAttr(cam1 + ".translate", *spherical(c, yaw, pitch, 55.0))
        cmds.setKeyframe(cam1, attribute="translate", time=f)
    cmds.setKeyframe(rig, attribute=joy_attr, time=1, value=0.0)
    cmds.setKeyframe(rig, attribute=joy_attr, time=24, value=1.0)
    cmds.setKeyframe(rig, attribute="alpha", time=1, value=1.0)
    cmds.setKeyframe(rig, attribute="alpha", time=24, value=0.5)
    expected: dict[int, dict[str, float]] = {}
    worst = 0.0
    for f in range(1, 25):
        cmds.currentTime(f, edit=True)
        ev = pr.evaluate_python(doc, cam1)
        w = pr.current_weights(asset)
        worst = max(worst, max(abs(w[k] - ev["weights"][k]) for k in w))
        expected[f] = ev["weights"]
    check("アニメーション中: 24 フレームで rig = Python（1e-4）", worst < W_TOL, f"{worst}")
    check("アニメーション中: 重みが時間で変わる（動いている）", max(abs(expected[1][k] - expected[16][k]) for k in expected[1]) > 0.2)
    result = pr.bake_to_keys(doc, 1, 24)
    check("bake_to_keys: 24 フレーム・キーを打ったターゲットがある", len(result["frames"]) == 24 and len(result["keyed"]) > 0, str(result["keyed"][:3]))
    check("bake_to_keys: 式・補助ノードが消え rig の transform は残る（強さ・感情のキーを残す）", pr.exists(asset) and not cmds.ls(type="expression") and not cmds.ls(type="decomposeMatrix") and pr.is_stale(doc))
    worst = 0.0
    for f in range(1, 25):
        cmds.currentTime(f, edit=True)
        for name, v in expected[f].items():
            worst = max(worst, abs(cmds.getAttr(scene.weight_plug(*_node_idx(scene, face, name))) - v))
    check("bake_to_keys: 式が無くても 24 フレームの重み = 焼く前（1e-4）", worst < 2e-4, f"{worst}")
    cmds.currentTime(5, edit=True)
    before = {n: cmds.getAttr(scene.weight_plug(*_node_idx(scene, face, n))) for n in expected[1]}
    cmds.setAttr(cam1 + ".translate", 500, 500, 500) if not cmds.listConnections(cam1 + ".translateX", source=True, destination=False) else None
    after = {n: cmds.getAttr(scene.weight_plug(*_node_idx(scene, face, n))) for n in expected[1]}
    check("bake_to_keys: カメラを動かしても重みは変わらない（もうつながっていない）", before == after)
    pr.delete(asset)
    check("焼いたあとの delete: キーは残る（値は同じ）", max(abs(cmds.getAttr(scene.weight_plug(*_node_idx(scene, face, n))) - expected[5][n]) for n in expected[5]) < 2e-4)
    # step=2・remove_rig=True
    for p in fc_plugs(face):
        cmds.cutKey(p, clear=True)
        cmds.setAttr(p, 0.0)
    cmds.cutKey(cam1, clear=True)
    cmds.currentTime(1, edit=True)
    cmds.setAttr(cam1 + ".translate", *spherical(c, 20, 10, 55.0))
    pr.build(doc, cam1)
    r_b = pr.bake_to_keys(doc, 1, 10, step=3, remove_rig=True)
    check("bake_to_keys(step=3, remove_rig=True): フレーム 1・4・7・10・rig も消える", r_b["frames"] == [1.0, 4.0, 7.0, 10.0] and not pr.exists(asset), str(r_b["frames"]))
    for p in fc_plugs(face):
        cmds.cutKey(p, clear=True)
        cmds.setAttr(p, 0.0)
    cmds.currentTime(1, edit=True)

    # ------------------------------------------------------------------ 出力: fctrack
    rig = pr.build(doc, cam1)
    cmds.setKeyframe(rig, attribute="alpha", time=1, value=1.0)
    cmds.setKeyframe(rig, attribute="alpha", time=25, value=0.0)
    cmds.setKeyframe(rig, attribute=joy_attr, time=13, value=0.0)
    cmds.setKeyframe(rig, attribute=joy_attr, time=19, value=1.0)
    cmds.setKeyframe(rig, attribute="manualYaw", time=1, value=-20.0)
    cmds.setKeyframe(rig, attribute="manualYaw", time=49, value=20.0)
    cmds.setKeyframe(rig, attribute="useManual", time=1, value=0.0)
    cmds.setKeyframe(rig, attribute="useManual", time=13, value=1.0)
    for a in ("alpha", joy_attr, "manualYaw", "useManual"):
        cmds.keyTangent(rig, attribute=a, inTangentType="linear", outTangentType="linear")
    cmds.currentUnit(time="film")
    out_ft = tmp / "ft"
    res_ft = export.export_fctrack(doc, "S010", "mini", 1, 49, out_dir=out_ft)
    p_ft = Path(res_ft["path"])
    check("fctrack: ファイル名 <Shot>__<Model>.fctrack", p_ft.name == "S010__mini.fctrack" and p_ft.exists(), str(p_ft))
    tr = fct.load(p_ft)
    check("fctrack: format / version / shot / model / frameRate / range", tr.shot == "S010" and tr.model == "mini" and tr.frame_rate == 24.0 and tr.range == (1.0, 49.0) and tr.version == 1, str((tr.frame_rate, tr.range)))
    check("fctrack: アニメーションしている属性だけ（alpha / emotion.Joy / manualYaw / useManual）", sorted(tr.curves) == ["alpha", "emotion.Joy", "manualYaw", "useManual"], str(sorted(tr.curves)))
    def near_keys(got, want):
        return len(got) == len(want) and all(abs(a[0] - b[0]) < 1e-6 and abs(a[1] - b[1]) < 1e-6 for a, b in zip(got, want))
    check("fctrack: alpha = フレームの時刻で（(frame − start) / fps 秒）", near_keys(tr.curves["alpha"], [(0.0, 1.0), (24 / 24.0, 0.0)]), str(tr.curves["alpha"]))
    check("fctrack: emotion.Joy は 13・19 フレーム → 0.5 秒・0.75 秒", near_keys(tr.curves["emotion.Joy"], [(0.5, 0.0), (0.75, 1.0)]), str(tr.curves["emotion.Joy"]))
    check("fctrack: manualYaw / useManual", near_keys(tr.curves["manualYaw"], [(0.0, -20.0), (2.0, 20.0)]) and near_keys(tr.curves["useManual"], [(0.0, 0.0), (0.5, 1.0)]))
    check("fctrack: 線形の接線なら警告なし・キー数 = 合計", not res_ft["warnings"] and res_ft["keys"] == 2 + 2 + 2 + 2, str(res_ft))
    res_ft2 = export.export_fctrack(doc, "S020", "mini", 13, 25, frame_rate=30, out_dir=out_ft)
    tr2 = fct.load(res_ft2["path"])
    check("fctrack: 範囲で絞る・frame_rate を渡せる（start より前のキーなら start の値を足す）", tr2.frame_rate == 30.0 and tr2.curves["alpha"][0][0] == 0.0 and abs(tr2.curves["alpha"][-1][0] - 12 / 30.0) < 1e-6, str(tr2.curves.get("alpha")))
    cmds.keyTangent(rig, attribute="alpha", inTangentType="auto", outTangentType="auto")
    res_ft3 = export.export_fctrack(doc, "S030", "mini", 1, 49, out_dir=out_ft)
    check("fctrack: 接線が step / linear でないと警告（接線は書かない）", any("接線" in w for w in res_ft3["warnings"]), str(res_ft3["warnings"]))
    cmds.currentUnit(time="film")
    try:
        export.export_fctrack(doc, "", "mini", 1, 2, out_dir=out_ft)
        raised = False
    except export.ExportError:
        raised = True
    check("fctrack: ショット名が空ならエラー", raised)
    for a in ("alpha", joy_attr, "manualYaw", "useManual"):
        cmds.cutKey(rig, attribute=a, clear=True)

    # ------------------------------------------------------------------ 出力: UE 版向け
    p_ue = export.export_ue(doc, tmp / "ue")
    d_ue, d_doc = fcpose_io.to_dict(fcpose_io.load_document(p_ue)), fcpose_io.to_dict(doc)
    check("export_ue: <asset>.fcpose.json（Document そのまま）", p_ue.name == "mini.fcpose.json" and d_ue == d_doc, str([k for k in set(d_ue) | set(d_doc) if d_ue.get(k) != d_doc.get(k)]))

    # ------------------------------------------------------------------ 出力: Unity（FBX）
    node = scene.primary_blend_shape(face)
    pose_report = bakemod.pose_to_shape(doc, SourcePose({"bs.smile_L": 1.0, "bs.brow_up": 0.4}), "fcs_Joy_R1_C1")
    check("準備: 彫り用の fcs_* ターゲットを作った", "fcs_Joy_R1_C1" in scene.target_indices(node), str(pose_report.created))
    pr.build(doc, cam1)  # rig があるまま出力する
    cmds.setAttr(cam1 + ".translate", *spherical(c, 10, 5))
    state_before = (cmds.file(query=True, sceneName=True), cmds.file(query=True, modified=True))
    targets_before = scene.curve_names(face, include_fc=True)
    fc_before = len([t for t in targets_before if t.split(".")[-1].startswith("FC_")])
    nodes_before = set(cmds.ls(long=True))
    weights_before = pr.current_weights(asset)
    out_unity = tmp / "unity_out"
    t0 = time.perf_counter()
    res_u = export.export_unity(doc, s.path, out_dir=out_unity)
    dt = time.perf_counter() - t0
    INFO.append(f"export_unity: {dt:.1f} 秒（別プロセス）、メッシュ {res_u['meshes']}、ブレンドシェイプ {res_u['blendshapes']}、FC_* {res_u['fc_count']}、除いた fcs_* {res_u['excluded_fcs']}")
    check("export_unity: <character>.fbx と <character>.fcpose ができる", Path(res_u["fbx"]).exists() and Path(res_u["fbx"]).name == "mini.fbx" and Path(res_u["fcpose"]).name == "mini.fcpose" and Path(res_u["fbx"]).stat().st_size > 1000)
    check("export_unity: メッシュ = 顔 + 同じ骨格のメッシュ（非表示のターゲットメッシュは入らない）", sorted(res_u["meshes"]) == ["mini_brow", "mini_face"], str(res_u["meshes"]))
    check("export_unity: 要約の FC_* 数 = シーンの FC_* 数・除いた fcs_* = 1", res_u["fc_count"] == fc_before and res_u["excluded_fcs"] == 1, f"{res_u['fc_count']} vs {fc_before}, {res_u['excluded_fcs']}")
    check("export_unity: 警告なし（全部ベイク済み）", not res_u["warnings"], str(res_u["warnings"]))
    state_after = (cmds.file(query=True, sceneName=True), cmds.file(query=True, modified=True))
    check("元のシーン: 名前・未保存の印が同じ", state_after == state_before, f"{state_before} -> {state_after}")
    check("元のシーン: ターゲット一覧（fcs_* を含む）・ノード・rig の重みが同じ", scene.curve_names(face, include_fc=True) == targets_before and "bs.fcs_Joy_R1_C1" in targets_before and set(cmds.ls(long=True)) == nodes_before and pr.current_weights(asset) == weights_before and pr.exists(asset))
    check("元のシーン: rig はまだ動く（式あり・is_stale = False）", not pr.is_stale(doc))
    check(".fcpose = Document と同じ内容（Maya の系）", fcpose_io.to_dict(fcpose_io.load_document(res_u["fcpose"])) == fcpose_io.to_dict(doc))
    check("出力の一時ファイルが残っていない", not [p for p in Path(tempfile.gettempdir()).glob("tdrive_facial_export_*") if (p / "args.json").exists() and str(out_unity) in (p / "args.json").read_text(encoding="utf-8")])
    # 未ベイクの警告
    doc_u = copy.deepcopy(doc)
    from tdrive_facial.core.model import GridPoint

    doc_u.layers[0].points[(0, 0)] = GridPoint(0, 0, True, SourcePose({"bs.smile_L": 0.2}))  # 焼いたあとにポーズを変えた点
    sad = Layer(name="Sad", emotion_curve="Sad")
    sad.points[(1, 1)] = GridPoint(1, 1, True, SourcePose({"bs.brow_up": 0.5}))  # まだ焼いていない点
    doc_u.layers.append(sad)
    warns = export._bake_warnings(doc_u)
    check("警告: 未ベイクの点・ベイク後に変更した点があると警告する", any("未ベイク" in w for w in warns) and any("ベイク後" in w for w in warns), str(warns))

    # 出力した FBX を新しいシーンへ取り込んで確かめる
    cmds.file(rename=str(tmp / "orig.mb"))
    cmds.file(save=True, type="mayaBinary")
    cmds.file(new=True, force=True)
    cmds.loadPlugin("fbxmaya", quiet=True)
    from maya import mel

    mel.eval("FBXResetImport")
    mel.eval("FBXImportMode -v add")
    mel.eval(f'FBXImport -f "{Path(res_u["fbx"]).as_posix()}"')
    nodes = cmds.ls(type="blendShape")
    aliases: list[str] = []
    for n in nodes:
        aliases += [a for a in (cmds.aliasAttr(n, query=True) or [])[0::2]]
    fc_in = [a for a in aliases if a.startswith("FC_")]
    check("FBX 取り込み: FC_* が元と同じ本数（顔メッシュ）", len(fc_in) == fc_before, f"{len(fc_in)} vs {fc_before}")
    check("FBX 取り込み: fcs_* が無い", not [a for a in aliases if a.startswith("fcs_")], str([a for a in aliases if a.startswith("fcs_")]))
    check("FBX 取り込み: 元のシェイプ（mouth_open など）も入っている", {"mouth_open", "smile_L", "smile_R", "brow_up"} <= set(aliases), str(sorted(a for a in aliases if not a.startswith("FC_"))))
    check("FBX 取り込み: プレビュー用ノード（tdFacialPreview）が無い", not cmds.ls("*tdFacialPreview*") and not cmds.ls(type="expression"))
    check("FBX 取り込み: ジョイント（root / head / eye_L / eye_R）が入っている", {"root", "head", "eye_L", "eye_R"} <= {j.split("|")[-1] for j in (cmds.ls(type="joint", long=True) or [])})
    check("FBX 取り込み: メッシュ mini_face / mini_brow・ターゲットメッシュは無い", {"mini_face", "mini_brow"} <= {m.split("|")[-1] for m in cmds.ls(type="transform")} and not cmds.ls("bs_target_mouth_open"))
    check("FBX 取り込み: アニメーションが無い", not cmds.ls(type="animCurve"), str(cmds.ls(type="animCurve")))
    nonzero = [(n, a) for n in nodes for a in (cmds.aliasAttr(n, query=True) or [])[0::2] if a.startswith("FC_") and abs(cmds.getAttr(f"{n}.{a}")) > 1e-9]
    check("FBX 取り込み: FC_* の初期の重みは 0", not nonzero, str(nonzero[:3]))
    check("FBX 取り込み: 単位 cm", cmds.currentUnit(query=True, linear=True) in ("cm", "centimeter"), cmds.currentUnit(query=True, linear=True))
    cmds.file(new=True, force=True)
    shutil.rmtree(tmp, ignore_errors=True)


def _node_idx(scene, face: str, alias: str):
    for c in scene.list_curves(face, include_fc=True):
        if c.alias == alias:
            return c.node, c.index
    raise KeyError(alias)


# ---------------------------------------------------------------- 第 2 部: shizuku（あるときだけ）


def run_shizuku() -> None:
    from maya import cmds

    from tdrive import project
    from tdrive_facial import bake as bakemod
    from tdrive_facial import preview_rig as pr
    from tdrive_facial import scene
    from tdrive_facial.core import autofill
    from tdrive_facial.core.model import BoneOffset, Document, GridPoint, Meta, SourcePose, Target

    tmp = Path(tempfile.mkdtemp(prefix="tdrive_fprev_sz_"))
    project.set_root(tmp)
    mb = REPO / "assets" / "shizuku" / "shizuku.mb"
    cmds.file(mb.as_posix(), open=True, force=True)  # 開くだけ。保存しない
    face = scene.resolve_mesh("mdl_face02")
    base = scene.detect_base_bone_for([face])
    doc = Document(meta=Meta(unit="cm", up_axis="Y", handedness="right", source="smoke"))
    doc.asset = "shizuku"
    doc.target = Target(mesh="mdl_face02")
    doc.grid.cols, doc.grid.rows = 5, 3
    doc.grid.base_bone = base
    doc.grid.forward_axis = "+Z"
    doc.mirror.bone_axis = "X"
    doc.layers[0].points[(1, 2)] = GridPoint(1, 2, True, SourcePose({"bs.mouth_left": 0.4}, {"bone_eye_L": BoneOffset(r=(0.0, 0.0871557, 0.0, 0.9961947))}))
    doc.layers[0].points[(1, 4)] = GridPoint(1, 4, True, SourcePose({"bs.eye_close_L": 0.8, "bs.mouth_left": 0.6}, {"bone_eye_L": BoneOffset(t=(0.0, 0.05, 0.0), r=(0.0, 0.1736482, 0.0, 0.9848078))}))
    autofill.generate_from_keys(doc)
    rep = bakemod.bake(doc)
    check("shizuku: 5x3 を焼いた", len(rep.created) == len(doc.layers[0].points), rep.summary())
    t0 = time.perf_counter()
    r = pr.build_ex(doc, "persp")
    INFO.append(f"shizuku 5x3 x 1 レイヤー: ターゲット {r.targets}、式 {r.expression_chars} 文字、組み立て {time.perf_counter() - t0:.2f} 秒")
    check("shizuku: rig を組めた（15 ターゲット）", r.targets == len(doc.layers[0].points) and not pr.is_stale(doc), str(r))
    head = tuple(cmds.xform(scene.find_joint(base, scene.mesh_joints([face])), query=True, worldSpace=True, translation=True))
    worst = 0.0
    wa = 0.0
    for yaw, pitch in ((0, 0), (30, 10), (-60, 20), (90, 0), (100, -10), (-20, -30), (150, 0), (45, 45)):
        pos = spherical(head, yaw, pitch, 120.0)
        cmds.setAttr("persp.translate", *pos)
        w = pr.current_weights("shizuku")
        ev = pr.evaluate_python(doc, "persp")
        worst = max(worst, max(abs(w[k] - ev["weights"][k]) for k in w))
        ya, pa = pr.current_angles("shizuku")
        wa = max(wa, ang_diff(ya, ev["yaw"]), abs(pa - ev["pitch"]))
    check("shizuku: persp で 8 位置、rig = Python（重み 1e-4・角度 1e-3°）", worst < W_TOL and wa < A_TOL, f"dw={worst:.2e} da={wa:.2e}")
    plug0 = next(iter(pr._read_json(r.rig, pr.TARGETS_ATTR, {}).values()))[0]
    n = 200
    t0 = time.perf_counter()
    for i in range(n):
        cmds.setAttr("persp.tx", 20.0 + (i % 50) * 1.7)
        cmds.getAttr(plug0)
    INFO.append(f"shizuku: 評価 {(time.perf_counter() - t0) / n * 1000:.3f} ms / 評価（カメラ移動 + 重みの取得。メッシュの変形評価は含まない）")
    # FBX 由来のモデル（ターゲットメッシュがつながったままの blendShape）の出力: 元のシェイプ + FC_* が全部入る
    from maya import mel

    from tdrive_facial import export

    node = scene.primary_blend_shape(face)
    n_orig = len([a for a in scene.target_indices(node) if not a.startswith("FC_")])
    n_fc = len([a for a in scene.target_indices(node) if a.startswith("FC_")])
    res = export.export_unity(doc, None, out_dir=tmp / "u")
    INFO.append(f"shizuku: export_unity {res['seconds']:.1f} 秒、メッシュ {len(res['meshes'])}、顔のブレンドシェイプ {res['blendshapes'].get('mdl_face02')}（元 {n_orig} + FC_* {n_fc}）、警告 {res['warnings']}")
    check("shizuku: export_unity の要約 = 元のシェイプ + FC_*（FC_* の数が同じ・警告なし）", res["fc_count"] == n_fc and res["blendshapes"].get("mdl_face02") == n_orig + n_fc and not res["warnings"], str(res["blendshapes"]))
    pr_exists = pr.exists("shizuku")
    cmds.file(new=True, force=True)  # shizuku を閉じる（保存しない）
    cmds.loadPlugin("fbxmaya", quiet=True)
    mel.eval("FBXResetImport")
    mel.eval(f'FBXImport -f "{Path(res["fbx"]).as_posix()}"')
    counts = {n: len(cmds.aliasAttr(n, query=True) or []) // 2 for n in cmds.ls(type="blendShape")}
    check("shizuku: FBX 取り込み: 顔の blendShape に 元のシェイプ + FC_* が全部ある・プレビュー用ノードなし", pr_exists and (n_orig + n_fc) in counts.values() and not cmds.ls("*tdFacialPreview*"), str(counts))
    cmds.file(new=True, force=True)
    shutil.rmtree(tmp, ignore_errors=True)


def main() -> None:
    run_mini()
    mb = REPO / "assets" / "shizuku" / "shizuku.mb"
    if mb.exists() and os.environ.get("TDRIVE_FACIAL_SHIZUKU", "1") != "0":
        run_shizuku()
    else:
        INFO.append("shizuku: なし / TDRIVE_FACIAL_SHIZUKU=0 のため第 2 部を飛ばした")
    INFO.append(f"rig = Python の最大誤差: 重み {STATS['w']:.2e}、角度 {STATS['a']:.2e}°（{STATS['n']} 回の比較）")


if __name__ == "__main__":
    maya.standalone.initialize(name="python")
    try:
        main()
    except Exception:
        RESULTS.append(("例外", False, traceback.format_exc()))
    finally:
        failed = [r for r in RESULTS if not r[1]]
        for name, ok, detail in RESULTS:
            print(f"SMOKE {'PASS' if ok else 'FAIL'} {name}" + (f"\n    {detail}" if not ok and detail else ""))
        for line in INFO:
            print(f"SMOKE INFO {line}")
        print(f"SMOKE RESULT {len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
        sys.stdout.flush()
        maya.standalone.uninitialize()
        os._exit(1 if failed else 0)
