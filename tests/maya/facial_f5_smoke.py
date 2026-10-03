"""FacialController の品質設定・誇張のベイク・距離で決めるレイヤー・補正の除外（Maya 側）のスモーク（mayapy・画面なし）。

  set QT_QPA_PLATFORM=offscreen
  "C:/Program Files/Autodesk/Maya2026/bin/mayapy.exe" tests/maya/facial_f5_smoke.py

確かめること:
- セッション: set_quality / set_layer_weight_source / 除外パターンの追加・削除（検証・元に戻す・改名と削除への追随・保存の往復）
- 誇張のベイク（重み 1 超 → FC_…_Ex）: FC × 1 + Ex × 1 = 作った通りのポーズ（1e-3）、感情レイヤー（Neutral の誇張を引く）、
  誇張が無い点の結果は従来と同じ・古い _Ex の掃除・未ベイク / 孤立の検出
- プレビュー: シャープさ 1 / 2 / 8 × 誇張 0 / 0.5 / 1 で rig = Python（1e-4）、距離で決めるレイヤー、既定のときは式に何も増えない、設定を変えると古い印
- 画面: セットアップの品質・除外、レイヤータブの重みの出どころ、プレビューの誇張スライダー、ポーズタブの除外の行、文言（チケット名・準備中が無い）
スクリーンショット: 環境変数 TDRIVE_UI_SHOT_DIR があれば f5_setup / f5_layers / f5_preview を書く。
ファイルはすべて一時フォルダ（プロジェクトのルートもそこへ向ける）。
"""

from __future__ import annotations

import math
import os
import re
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ.setdefault("MAYA_DISABLE_CER", "1")
REPO = Path(__file__).resolve().parents[2]
os.environ.setdefault("TDRIVE_ROOT", REPO.as_posix())
sys.path.insert(0, str(REPO / "maya" / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6 import QtGui, QtWidgets  # noqa: E402

APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv[:1])  # maya.standalone より先に作る


def _style() -> None:
    for name in ("meiryo.ttc", "YuGothM.ttc", "msgothic.ttc"):
        f = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / name
        if f.exists():
            fid = QtGui.QFontDatabase.addApplicationFont(str(f))
            fams = QtGui.QFontDatabase.applicationFontFamilies(fid) if fid >= 0 else []
            if fams:
                APP.setFont(QtGui.QFont(fams[0], 9))
                break
    APP.setStyle("Fusion")
    p = QtGui.QPalette()
    for role, color in (
        (QtGui.QPalette.Window, "#444444"), (QtGui.QPalette.WindowText, "#d8d8d8"), (QtGui.QPalette.Base, "#2b2b2b"),
        (QtGui.QPalette.AlternateBase, "#3a3a3a"), (QtGui.QPalette.Text, "#d8d8d8"), (QtGui.QPalette.Button, "#5d5d5d"),
        (QtGui.QPalette.ButtonText, "#e0e0e0"), (QtGui.QPalette.Highlight, "#5285a6"), (QtGui.QPalette.HighlightedText, "#ffffff"),
        (QtGui.QPalette.ToolTipBase, "#333333"), (QtGui.QPalette.ToolTipText, "#ffffff"),
    ):
        p.setColor(role, QtGui.QColor(color))
    APP.setPalette(p)


_style()

import maya.standalone  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []
INFO: list[str] = []
W_TOL = 1e-4


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(cond), detail))


def pump() -> None:
    APP.processEvents()


def spherical(center, yaw_deg: float, pitch_deg: float, dist: float = 60.0):
    """forwardAxis +Z・頭の回転なしのとき、Yaw / Pitch に当たるカメラ位置。"""
    y, p = math.radians(yaw_deg), math.radians(pitch_deg)
    return (
        center[0] + dist * math.sin(y) * math.cos(p),
        center[1] + dist * math.sin(p),
        center[2] + dist * math.cos(y) * math.cos(p),
    )


def run() -> None:
    import numpy as np
    from maya import cmds

    import facial_fixture
    from tdrive import project
    from tdrive_facial import export, pose_apply, scene, ui, ui_grid
    from tdrive_facial import preview_rig as pr
    from tdrive_facial import session as S
    from tdrive_facial.core import autofill, evaluate, fcpose_io, naming
    from tdrive_facial.core import fctrack as fct
    from tdrive_facial.core import validate as V
    from tdrive_facial.core.model import Quality

    shot_dir = os.environ.get("TDRIVE_UI_SHOT_DIR")
    if shot_dir:
        Path(shot_dir).mkdir(parents=True, exist_ok=True)

    tmp = Path(tempfile.mkdtemp(prefix="tdrive_f5_"))
    project.set_root(tmp)
    facial_fixture.build_mini_head()
    face = scene.resolve_mesh("mini_face")
    asset = "mini"
    s = S.current()
    s.close()
    s.listeners.clear()
    s.state_listeners.clear()
    S.FacialSession.model_cameras = staticmethod(lambda: ["persp", "pvcam"])
    cam = cmds.rename(cmds.camera(name="pvcam")[0], "pvcam")
    cam_l = cmds.ls(cam, long=True)[0]

    def fill(doc):
        autofill.generate_from_keys(doc)
        return doc

    # ---- 誇張なしの文書（従来の結果の基準）と、誇張ありの文書
    doc_plain = facial_fixture.make_doc()
    doc_plain.bake.delta_threshold = 1e-5
    fill(doc_plain)
    doc_ex = facial_fixture.make_doc()
    doc_ex.bake.delta_threshold = 1e-5
    doc_ex.limits = {"bs.mouth_open": (0.0, 2.0), "bs.brow_up": (0.0, 2.0)}
    from tdrive_facial.core.model import GridPoint, SourcePose

    doc_ex.layers[0].points[(1, 2)].pose.curves["bs.mouth_open"] = 1.5  # Neutral (1,2): 誇張あり
    doc_ex.layers[0].points[(1, 0)].pose.curves["bs.brow_up"] = 1.6  # Neutral (1,0): 誇張あり。Joy (1,0) は自分に誇張なし
    doc_ex.layers[1].points[(1, 2)].pose.curves["bs.brow_up"] = 1.3  # Joy (1,2): 自分にも誇張あり
    doc_ex.layers[1].points[(1, 0)] = GridPoint(1, 0, True, SourcePose({"bs.smile_L": 1.0}, {}))
    fill(doc_ex)
    p_plain = tmp / "src" / "plain.fcpose.json"
    p_ex = tmp / "src" / "mini.fcpose.json"
    p_plain.parent.mkdir()
    fcpose_io.save(doc_plain, p_plain)
    fcpose_io.save(doc_ex, p_ex)

    ws_curves = ["bs.mouth_open", "bs.smile_L", "bs.smile_R", "bs.brow_up"]

    def dense(node: str, alias: str, n: int) -> np.ndarray:
        out = np.zeros((n, 3))
        got = scene.read_target_delta(node, alias)
        if got is not None:
            comps, d = got
            out[list(comps)] = d
        return out

    def pose_delta(doc, pose) -> np.ndarray:
        """基準姿勢で、ポーズを当てたときの頂点の動き（bake と同じ手順）。"""
        ref = scene.enter_reference_pose([face], extra_joints=["eye_L", "eye_R"])
        try:
            base = scene.read_points(face)
            pose_apply.apply_pose(doc, pose, ref)
            return scene.read_points(face) - base
        finally:
            ref.restore()

    # ============================================================ 1. 誇張なし: 従来どおり
    s.open(p_plain)
    s.add_to_working_set(curves=ws_curves, bones=["eye_L", "eye_R"])
    s.set_mirror(bone_axis="X", suffix_l="_L", suffix_r="_R")
    rep0 = s.bake_all()
    node = scene.primary_blend_shape(face)
    names0 = set(scene.target_indices(node))
    n_ex0 = [n for n in names0 if n.endswith("_Ex")]
    check("誇張なし: 18 本を焼き、_Ex は 1 本もない・報告の誇張は 0", len(rep0.created) == 18 and not n_ex0 and rep0.extreme == [] and rep0.extreme_count == 0 and "誇張" not in rep0.summary(), rep0.summary())
    nverts = scene.vertex_count(face)
    plain = {n: dense(node, n, nverts) for n in names0 if naming.is_fc_name(n)}
    # 独立に求めた期待値と一致（Neutral = ポーズの差分、Joy = Joy のポーズの差分 − Neutral の同じ点）
    dmax = 0.0
    for li, layer in enumerate(s.doc.layers):
        for (r, c), pt in layer.points.items():
            if pt.pose.is_empty():
                continue
            want = pose_delta(s.doc, pt.pose)
            if li > 0:
                npt = s.doc.layers[0].points.get((r, c))
                if npt is not None and not npt.pose.is_empty():
                    want = want - pose_delta(s.doc, npt.pose)
            got = plain[naming.morph_name(asset, layer.name, r, c)]
            dmax = max(dmax, float(np.abs(got - want).max()))
    check("誇張なし: 焼いた差分 = ポーズの差分（感情は Neutral を引く）。しきい値 1e-5 内の誤差だけ", dmax < 2e-5, f"{dmax:.2e}")
    s.close()

    # ============================================================ 2. 誇張あり: 焼き分け
    panel = ui.FacialPanel()
    panel.resize(560, 900)
    s.open(p_ex)
    s.add_to_working_set(curves=ws_curves, bones=["eye_L", "eye_R"])
    s.set_mirror(bone_axis="X", suffix_l="_L", suffix_r="_R")
    doc = s.doc
    pump()
    rep = s.bake_all()
    node = scene.primary_blend_shape(face)
    names = set(scene.target_indices(node))
    ex_names = {n for n in names if n.endswith("_Ex")}
    want_ex = set()
    for li, layer in enumerate(doc.layers):
        for (r, c), pt in layer.points.items():
            if V.needs_extreme(doc, li, (r, c)):
                want_ex.add(naming.morph_name(asset, layer.name, r, c, extreme=True))
    key_ex = {
        naming.morph_name(asset, "Neutral", 1, 2, extreme=True),
        naming.morph_name(asset, "Neutral", 1, 0, extreme=True),
        naming.morph_name(asset, "Joy", 1, 2, extreme=True),
        naming.morph_name(asset, "Joy", 1, 0, extreme=True),
    }
    check("誇張: 重み 1 超の点（と、Neutral に誇張がある感情の点）にだけ _Ex ができる", ex_names == want_ex and key_ex <= ex_names, f"{sorted(ex_names)} want {sorted(want_ex)}")
    check("誇張: Neutral にも自分にも誇張が無い点（Joy (0,2) など）には _Ex が無い", naming.morph_name(asset, "Joy", 0, 2, extreme=True) not in names and naming.morph_name(asset, "Neutral", 0, 2, extreme=True) not in names)
    check("誇張: 報告の誇張用の数 = _Ex の数・要約と画面の報告に出る", sorted(rep.extreme) == sorted(ex_names) and rep.extreme_count == len(ex_names) and "誇張用" in rep.summary() and "誇張用のシェイプ" in ui_grid.GridTab.report_text(rep), rep.summary())
    # 誇張のない点は従来と同じ（誇張なしの文書で焼いたものと同じポーズの点）
    same = [naming.morph_name(asset, "Neutral", 0, 2), naming.morph_name(asset, "Neutral", 2, 1), naming.morph_name(asset, "Joy", 0, 2)]
    dsame = max(float(np.abs(dense(node, n, nverts) - plain[n]).max()) for n in same)
    check("誇張: 誇張の無い点の通常シェイプは、誇張なしの文書で焼いたものと同じ", dsame < 1e-9, f"{dsame:.2e}")
    # FC×1 + Ex×1 = 作った通りのポーズ
    def total(layer_name: str, r: int, c: int) -> np.ndarray:
        out = np.zeros((nverts, 3))
        for nm in (naming.morph_name(asset, layer_name, r, c), naming.morph_name(asset, layer_name, r, c, extreme=True)):
            if nm in names:
                out += dense(node, nm, nverts)
        return out

    pt = doc.layers[0].points[(1, 2)].pose
    full = pose_delta(doc, pt)
    clamped = pose_delta(doc, V.clamp_extreme(pt))
    n12 = dense(node, naming.morph_name(asset, "Neutral", 1, 2), nverts)
    check("誇張: 通常シェイプ = 重みを 1 までに丸めたポーズの差分", float(np.abs(n12 - clamped).max()) < 2e-5, f"{np.abs(n12 - clamped).max():.2e}")
    check("誇張: 元のポーズは丸めたものと違う（この点で Ex が意味を持つ）", float(np.abs(full - clamped).max()) > 0.05, f"{np.abs(full - clamped).max():.3f}")
    d1 = float(np.abs(total("Neutral", 1, 2) - full).max())
    check("誇張: Neutral (1,2): FC × 1 + Ex × 1 = 作った通りのポーズ（1e-3）", d1 < 1e-3, f"{d1:.2e}")
    pt10 = doc.layers[0].points[(1, 0)].pose
    d2 = float(np.abs(total("Neutral", 1, 0) - pose_delta(doc, pt10)).max())
    check("誇張: Neutral (1,0): FC × 1 + Ex × 1 = 作った通りのポーズ（1e-3）", d2 < 1e-3, f"{d2:.2e}")
    for (r, c) in ((1, 2), (1, 0)):  # 感情: Neutral の通常 + 誇張 + 感情の通常 + 誇張 = 感情のポーズそのもの
        jp = doc.layers[1].points[(r, c)].pose
        d3 = float(np.abs(total("Neutral", r, c) + total("Joy", r, c) - pose_delta(doc, jp)).max())
        check(f"誇張: Joy ({r},{c}): Neutral の FC + Ex + Joy の FC + Ex = Joy のポーズそのもの（1e-3）", d3 < 1e-3, f"{d3:.2e}")
    check("誇張: 自分に誇張が無い感情の点 Joy (1,0) の Ex = −（Neutral の Ex）", float(np.abs(dense(node, naming.morph_name(asset, "Joy", 1, 0, extreme=True), nverts) + dense(node, naming.morph_name(asset, "Neutral", 1, 0, extreme=True), nverts)).max()) < 2e-5)
    # 状態・検証
    state = scene.get_bake_state(node)
    check("誇張: ベイクの状態に _Ex も入る（点と同じ指紋）", all(n in state for n in ex_names) and state[naming.morph_name(asset, "Neutral", 1, 2, extreme=True)] == state[naming.morph_name(asset, "Neutral", 1, 2)])
    check("検証: 焼いた直後は未ベイク・変更あり・孤立が無い", s.stale_points() == [] and not [i for i in s.validate() if i.code in ("point_unbaked", "point_changed_since_bake", "orphan_target", "baked_morph_missing")], str([(i.code, i.name) for i in s.validate()][:6]))
    # _Ex が消えた（再インポート等）→ 未ベイク扱い
    ex12 = naming.morph_name(asset, "Neutral", 1, 2, extreme=True)
    scene.delete_targets(node, [ex12])
    s.refresh_scene()
    check("検証: 要る _Ex が無いと、その点は焼き直しの対象（stale_points）", (0, 1, 2) in s.stale_points(), str(s.stale_points()))
    rep_b = s.bake_point(1, 2, 0)
    check("再ベイク: 消えた _Ex が戻る・stale が無くなる", ex12 in scene.target_indices(node) and s.stale_points() == [], str(rep_b.summary()))
    # 孤立した _Ex（その点に 1 超が無い）
    stray = naming.morph_name(asset, "Joy", 0, 2, extreme=True)
    scene.write_target_delta(node, stray, np.array([[0.0, 0.5, 0.0]]), [3])
    s.refresh_scene()
    check("検証: 誇張が要らない点の _Ex は孤立として報告される", stray in [i.name for i in s.validate() if i.code == "orphan_target"])
    rep_c = s.bake_point(1, 2, 0)
    check("再ベイク: 孤立した _Ex は掃除され、状態にも残らない", stray in rep_c.removed and stray not in scene.target_indices(node) and stray not in scene.get_bake_state(node), str(rep_c.removed))
    # 古い _Ex の除去: Neutral (1,2) の誇張をやめる
    pose12 = doc.layers[0].points[(1, 2)].pose
    pose12.curves["bs.mouth_open"] = 1.0
    changed = [i for i in s.validate() if i.code == "point_changed_since_bake" and i.layer == 0 and (i.row, i.col) == (1, 2)]
    check("検証: ポーズを変えると（誇張をやめても）その点は変更ありになる", bool(changed))
    rep_d = s.bake_point(1, 2, 0)
    d = naming.morph_name(asset, "Neutral", 1, 2, extreme=True)
    check("再ベイク: 誇張をやめた点の古い _Ex が消える（報告の removed・状態・シーン）", d in rep_d.removed and d not in scene.target_indices(node) and d not in scene.get_bake_state(node), str(rep_d.removed))
    s.bake_layer(1)
    names = set(scene.target_indices(node))
    jex = dense(node, naming.morph_name(asset, "Joy", 1, 2, extreme=True), nverts)
    jp = doc.layers[1].points[(1, 2)].pose
    want_j = pose_delta(doc, jp) - pose_delta(doc, V.clamp_extreme(jp))
    check("再ベイク: Joy (1,2) の Ex = 自分のポーズ − 丸めたポーズ（Neutral の Ex は無い）", float(np.abs(jex - want_j).max()) < 2e-5, f"{np.abs(jex - want_j).max():.2e}")
    pose12.curves["bs.mouth_open"] = 1.5
    s.bake_all()
    names = set(scene.target_indices(node))
    # 補正除外のシェイプは数えない
    ex_doc2 = _with_exclude(doc, ["mouth_open"])
    check("誇張: 補正除外（mouth_open）にすると、Neutral (1,2) のポーズは 1 超なしになる", not V.has_extreme(ex_doc2, doc.layers[0].points[(1, 2)].pose) and V.has_extreme(doc, doc.layers[0].points[(1, 2)].pose))

    # ============================================================ 3. セッション: 品質
    q0 = s.doc.quality
    check("品質: 既定（quality 無し or 既定値）= シャープさ 1・誇張 1・コマ打ち 0", q0 is None or (q0.sharpness == 1.0 and q0.exaggeration == 1.0 and q0.step_fps == 0.0))
    r = s.set_quality(sharpness=2.0, step_fps=12, exaggeration=0.5, angle_epsilon=0.2)
    q = s.doc.quality
    check("set_quality: 4 つの値が入る", r.ok and q.sharpness == 2.0 and q.step_fps == 12.0 and q.exaggeration == 0.5 and q.angle_epsilon == 0.2, str(q))
    check("set_quality: 元に戻す 1 回で戻る（quality が無い状態へ）", s.undo() and (s.doc.quality is None or s.doc.quality.sharpness == 1.0) and s.redo() and s.doc.quality.sharpness == 2.0)
    before = fcpose_io.to_dict(s.doc)
    for bad in ({"sharpness": 0.0}, {"sharpness": 100.0}, {"sharpness": float("nan")}, {"step_fps": -1}, {"exaggeration": 1.5}, {"angle_epsilon": 90.0}, {"sharpness": "x"}):
        rr = s.set_quality(**bad)
        if rr.ok or fcpose_io.to_dict(s.doc) != before:
            check(f"set_quality: 範囲外 {bad} は失敗して文書は変わらない", False, str(rr))
            break
    else:
        check("set_quality: 範囲外・数値でない値（7 通り）は失敗して文書は変わらない", True)
    check("set_quality: 同じ値は「変更なし」（元に戻すに積まない）", s.set_quality(sharpness=2.0).code == "unchanged")
    p_rt = tmp / "rt.fcpose.json"
    fcpose_io.save(s.doc, p_rt)
    d_rt = fcpose_io.load(p_rt)
    check("保存の往復: sharpness / stepFps / exaggeration / angleEpsilon が残る", d_rt.quality is not None and d_rt.quality.sharpness == 2.0 and d_rt.quality.step_fps == 12.0 and d_rt.quality.exaggeration == 0.5 and d_rt.quality.angle_epsilon == 0.2)
    s.set_quality(sharpness=1.0, step_fps=0, exaggeration=1.0, angle_epsilon=0.1)

    # ============================================================ 4. セッション: 重みの出どころ
    check("重みの出どころ: 既定は curve（Timeline・部品の値）", s.layer_weight_source(1)["source"] == "curve")
    bad_results = [
        s.set_layer_weight_source(0, "distance"),
        s.set_layer_weight_source(1, "nonsense"),
        s.set_layer_weight_source(1, "distance", w_from=2.0),
        s.set_layer_weight_source(1, "distance", start=-1.0),
        s.set_layer_weight_source(1, "distance", start=float("inf")),
    ]
    check("重みの出どころ: Neutral・知らない種類・範囲外はどれも失敗して文書は変わらない", all(not b.ok for b in bad_results) and not s.doc.layer_weights, str([b.code for b in bad_results]))
    r = s.set_layer_weight_source(1, "distance", start=40.0, end=120.0, w_from=0.0, w_to=1.0)
    check("重みの出どころ: 距離（開始 / 終了 / から / まで）が入る", r.ok and s.doc.layer_weights["Joy"] == {"source": "distance", "start": 40.0, "end": 120.0, "from": 0.0, "to": 1.0}, str(s.doc.layer_weights))
    check("重みの出どころ: 元に戻す・やり直し", s.undo() and not s.doc.layer_weights and s.redo() and s.doc.layer_weights["Joy"]["source"] == "distance")
    s.set_layer_weight_source(1, "distance", end=200.0)
    check("重みの出どころ: 省いた値は今の値のまま", s.doc.layer_weights["Joy"]["start"] == 40.0 and s.doc.layer_weights["Joy"]["end"] == 200.0)
    s.set_layer_weight_source(1, "distance", end=120.0)
    check("検証: 距離のレイヤーの設定は問題なし", not [i for i in s.validate() if i.code.startswith("layer_weight")])
    s.rename_layer(1, "Happy")
    check("重みの出どころ: レイヤーを改名すると設定が付いていく", "Happy" in s.doc.layer_weights and "Joy" not in s.doc.layer_weights)
    s.rename_layer(1, "Joy")
    s.doc.layer_weights["Ghost"] = {"source": "distance", "start": 1, "end": 2, "from": 0, "to": 1}
    check("検証: 存在しないレイヤーの設定は警告", any(i.code == "layer_weight_unknown_layer" for i in s.validate()))
    del s.doc.layer_weights["Ghost"]
    s.set_layer_weight_source(1, "direct")
    check("重みの出どころ: 直接に戻せる（距離の値は残す）", s.layer_weight_source(1)["source"] == "direct" and s.layer_weight_source(1)["end"] == 120.0)
    s.set_layer_weight_source(1, "distance")

    # ============================================================ 5. セッション: 補正の除外
    s.set_exclude(curves=[], bones=[])
    r1 = s.add_exclude("curve", "smile")
    check("除外: パターンを足せる（部分一致）・空 / 重複 / 種類違いは失敗", r1.ok and s.is_excluded("curve", "bs.smile_L") and not s.is_excluded("curve", "bs.mouth_open") and not s.add_exclude("curve", "smile").ok and not s.add_exclude("curve", " ").ok and not s.add_exclude("x", "a").ok)
    s.add_exclude("bone", "eye_")
    check("除外: ボーンも部分一致（eye_L / eye_R）", s.is_excluded("bone", "eye_L") and s.is_excluded("bone", "eye_R") and not s.is_excluded("bone", "head"))
    check("除外: 元に戻す 1 回で外れる", s.undo() and not s.doc.exclude.bones and s.redo() and s.doc.exclude.bones == ["eye_"])
    issues = [i for i in s.validate() if i.code == "excluded_in_pose"]
    check("検証: 除外に当たる名前がポーズにあると報告（シェイプ・ボーン）", {i.name for i in issues} >= {"bs.smile_L", "bs.smile_R", "eye_L"}, str([i.name for i in issues]))
    # 焼いたとき除外されたものは無視（pose_apply と bake）
    s.bake_all()
    doc = s.doc  # 元に戻す・やり直しで Document が入れ替わるので取り直す
    smile_pose = doc.layers[0].points[(1, 2)].pose  # mouth_open 1.5 + smile_L 0.8
    exp = pose_delta(doc, smile_pose)  # 除外つきで当てた結果（smile_L は効かない）
    got = total("Neutral", 1, 2)
    check("除外: 除外に当たるシェイプ・ボーンは焼かれない（焼いた結果 = 除外つきで当てた結果）", float(np.abs(got - exp).max()) < 1e-3, f"{np.abs(got - exp).max():.2e}")
    s.set_exclude(curves=[], bones=[])
    s.bake_all()
    check("除外を外して焼き直すと smile_L が入る（結果が変わる）", float(np.abs(total("Neutral", 1, 2) - got).max()) > 0.01)
    node = scene.primary_blend_shape(face)

    # ============================================================ 6. プレビュー: 誇張・シャープさ・距離
    cam1 = cmds.camera(name="pvcam1")[0]
    head_c = tuple(cmds.xform("head", query=True, worldSpace=True, translation=True))
    cmds.setAttr(cam1 + ".translate", 0, 10, 60)
    # 既定のとき式に何も増えない（誇張の _Ex が無い文書で）
    s.close()
    s.open(p_plain)
    s.add_to_working_set(curves=ws_curves, bones=["eye_L", "eye_R"])
    s.bake_all()
    rp = pr.build_ex(s.doc, cam1)
    text_plain = cmds.expression(rp.expression, query=True, string=True)
    check("既定: 誇張なし・シャープさ 1・距離なしの式にシャープニング / 誇張 / 距離の記述が増えない", "$b00" not in text_plain and "exaggeration" not in text_plain and "outDistance" not in text_plain and "$dist" not in text_plain and rp.extreme_targets == 0)
    sig_plain = cmds.getAttr(f"{rp.rig}.tdFacialSignature")
    s.set_quality(sharpness=1.0)  # 変更なし
    check("既定: シャープさ 1 に設定しても式は同じ（署名が変わらない）", cmds.getAttr(f"{rp.rig}.tdFacialSignature") == sig_plain and not pr.is_stale(s.doc))
    s.doc.quality = Quality()
    check("既定: quality が既定値で存在しても式は同じ", not pr.is_stale(s.doc))
    s.doc.quality = None
    pr.delete(asset)
    s.close()

    s.open(p_ex)
    s.add_to_working_set(curves=ws_curves, bones=["eye_L", "eye_R"])
    s.set_mirror(bone_axis="X", suffix_l="_L", suffix_r="_R")
    s.set_quality(exaggeration=0.5)
    s.bake_all()
    doc = s.doc
    node = scene.primary_blend_shape(face)
    rep6 = pr.build_ex(doc, cam1)
    rig = rep6.rig
    n_ex = len([n for n in scene.target_indices(node) if n.endswith("_Ex")])
    check("プレビュー: _Ex を配線（報告の誇張の数）・ターゲットの数は _Ex を含まない", rep6.extreme_targets == n_ex and rep6.targets == len([n for n in scene.target_indices(node) if naming.is_fc_name(n) and not n.endswith("_Ex")]), str(rep6.summary()))
    check("プレビュー: キー可アトリビュート exaggeration があり、初期値 = quality.exaggeration（0.5）", cmds.attributeQuery("exaggeration", node=rig, exists=True) and "exaggeration" in (cmds.listAttr(rig, keyable=True) or []) and abs(cmds.getAttr(rig + ".exaggeration") - 0.5) < 1e-9)
    check("プレビュー: exaggeration は .fctrack に出さない（KEYABLE_FIXED / FIXED_CURVES に入れない）", "exaggeration" not in pr.KEYABLE_FIXED and "exaggeration" not in fct.FIXED_CURVES)
    check("プレビュー: 式に誇張の記述があり、署名がある", "exaggeration" in cmds.expression(rep6.expression, query=True, string=True))

    def compare(tag: str, **kw) -> float:
        w = pr.current_weights(asset)
        ev = pr.evaluate_python(doc, cam1, **kw)
        dw = max((abs(w[k] - ev["weights"][k]) for k in w), default=0.0)
        miss = set(ev["weights"]) ^ set(w)
        check(f"{tag}: rig = Python（1e-4）", dw < W_TOL and not miss, f"dw={dw:.2e} miss={miss}")
        return dw

    cmds.setAttr(rig + ".emotion_Joy", 0.6)
    worst = 0.0
    angles = [(0, 0), (20, 10), (-35, -20), (80, 40), (92, 0), (45, 22.5), (10, 5)]
    for sharp in (1.0, 2.0, 8.0):
        s.set_quality(sharpness=sharp)  # 式を作り直す（古い印 → 自動の作り直し）
        check(f"シャープさ {sharp:g}: 設定を変えるとプレビューが自動で作り直される（最新）", not pr.is_stale(doc) and s.preview_state() in ("live",), s.preview_state())
        cmds.setAttr(rig + ".emotion_Joy", 0.6)
        for ex in (0.0, 0.5, 1.0):
            cmds.setAttr(rig + ".exaggeration", ex)
            for yaw, pitch in angles:
                cmds.setAttr(cam1 + ".translate", *spherical(head_c, yaw, pitch, 55.0))
                dw = compare(f"シャープさ {sharp:g}・誇張 {ex:g}・Yaw {yaw} / Pitch {pitch}")
                worst = max(worst, dw)
    INFO.append(f"シャープさ × 誇張 × 角度 (3 × 3 × 7): rig = Python の最大誤差 {worst:.2e}")
    # 意味: 誇張 0 で Ex の重み 0・1 で FC と同じ・シャープさで最寄りの点へ寄る
    s.set_quality(sharpness=1.0)
    cmds.setAttr(rig + ".emotion_Joy", 0.0)
    cmds.setAttr(cam1 + ".translate", *spherical(head_c, 90, 0, 55.0))  # (1,2) の点の上
    n12 = naming.morph_name(asset, "Neutral", 1, 2)
    for ex, want in ((0.0, 0.0), (0.5, 0.5), (1.0, 1.0)):
        cmds.setAttr(rig + ".exaggeration", ex)
        w = pr.current_weights(asset)
        check(f"誇張 {ex:g}: 点の上で Ex の重み = {want}（FC は 1）", abs(w[n12] - 1.0) < 1e-6 and abs(w[n12 + "_Ex"] - want) < 1e-6, f"{w[n12]} {w[n12 + '_Ex']}")
    cmds.setAttr(rig + ".exaggeration", 1.0)
    cmds.setAttr(cam1 + ".translate", *spherical(head_c, 70, 15, 55.0))
    n_names = [naming.morph_name(asset, "Neutral", r, c) for r in range(3) for c in range(3)]
    w1 = pr.current_weights(asset)
    s.set_quality(sharpness=8.0)
    w8 = pr.current_weights(asset)
    top = max(n_names, key=lambda n: w1[n])
    check("シャープさ 8: 最寄りの点の重みが増え・Neutral の重みの合計は 1 のまま", w8[top] > w1[top] + 0.05 and abs(sum(w8[n] for n in n_names) - 1.0) < 1e-6 and abs(sum(w1[n] for n in n_names) - 1.0) < 1e-6, f"{w1[top]:.3f} -> {w8[top]:.3f}")
    s.set_quality(sharpness=1.0)
    # 署名・古い印
    sig1 = cmds.getAttr(f"{rig}.tdFacialSignature")
    doc.quality.sharpness = 3.0  # セッションを通さずに変えた
    check("is_stale: シャープさを変えると古い印（作り直しが要る）", pr.is_stale(doc))
    doc.quality.sharpness = 1.0
    check("is_stale: 戻すと最新", not pr.is_stale(doc) and cmds.getAttr(f"{rig}.tdFacialSignature") == sig1)

    # ---- 距離で決めるレイヤー
    s.set_layer_weight_source(1, "distance", start=40.0, end=120.0, w_from=0.0, w_to=1.0)
    st = s.preview_status()
    check("距離レイヤー: 設定するとプレビューが作り直され、距離の出力 outDistance ができる・状態に出る", cmds.attributeQuery("outDistance", node=rig, exists=True) and st.distance_layers == ["Joy"] and "outDistance" in cmds.expression(cmds.listConnections(rig + ".outDistance", source=True, destination=False, type="expression")[0], query=True, string=True))
    cmds.setAttr(rig + ".useManual", 1)
    cmds.setAttr(rig + ".manualYaw", 90.0)
    cmds.setAttr(rig + ".manualPitch", 0.0)
    cmds.setAttr(rig + ".exaggeration", 1.0)
    j12 = naming.morph_name(asset, "Joy", 1, 2)
    worst_d = 0.0
    for dist in (10.0, 40.0, 60.0, 80.0, 100.0, 120.0, 200.0):
        cmds.setAttr(cam1 + ".translate", *spherical(head_c, 20, 10, dist))
        cmds.setAttr(rig + ".emotion_Joy", 0.3)  # 距離のレイヤーでは使われない
        dw = compare(f"距離 {dist:g} cm（手動角度 Yaw 90°）", manual=(90.0, 0.0))
        worst_d = max(worst_d, dw)
        w = pr.current_weights(asset)
        want = evaluate.layer_weight_from_distance(dist, 40.0, 120.0, 0.0, 1.0)
        check(f"距離 {dist:g} cm: Joy (1,2) の重み = 距離から決めた {want:.3f}（emotion_Joy は無視）", abs(w[j12] - want) < 1e-4, f"{w[j12]} vs {want}")
        check(f"距離 {dist:g} cm: outDistance = カメラまでの距離", abs(cmds.getAttr(rig + ".outDistance") - dist) < 1e-3, str(cmds.getAttr(rig + ".outDistance")))
    check("距離レイヤー: 感情のスライダー代わりの emotion_Joy は残る（キーも値も壊さない）・状態に距離が出る", cmds.attributeQuery("emotion_Joy", node=rig, exists=True) and s.preview_status().out_distance is not None)
    # 近いほど効く（から 1 → まで 0）・段差（開始 = 終了）
    s.set_layer_weight_source(1, "distance", start=40.0, end=120.0, w_from=1.0, w_to=0.25)
    for dist in (30.0, 80.0, 150.0):
        cmds.setAttr(cam1 + ".translate", *spherical(head_c, 20, 10, dist))
        compare(f"距離（から 1 → まで 0.25）{dist:g} cm", manual=(90.0, 0.0))
        check(f"距離（反転）{dist:g} cm: 重み = lerp", abs(pr.current_weights(asset)[j12] - evaluate.layer_weight_from_distance(dist, 40.0, 120.0, 1.0, 0.25)) < 1e-4)
    s.set_layer_weight_source(1, "distance", start=70.0, end=70.0, w_from=0.0, w_to=1.0)
    for dist in (50.0, 70.0, 90.0):
        cmds.setAttr(cam1 + ".translate", *spherical(head_c, 20, 10, dist))
        compare(f"距離（段差 70 cm）{dist:g} cm", manual=(90.0, 0.0))
        check(f"距離（段差）{dist:g} cm: {'to' if dist >= 70.0 else 'from'} の値", abs(pr.current_weights(asset)[j12] - (1.0 if dist >= 70.0 else 0.0)) < 1e-4)
    INFO.append(f"距離レイヤー: rig = Python の最大誤差 {worst_d:.2e}")
    # 直接に戻すと emotion_Joy が効く
    s.set_layer_weight_source(1, "direct")
    cmds.setAttr(rig + ".emotion_Joy", 0.4)
    cmds.setAttr(cam1 + ".translate", *spherical(head_c, 20, 10, 100.0))
    check("直接に戻す: emotion_Joy が効く（0.4）・距離の出力は無くなる", abs(pr.current_weights(asset)[j12] - 0.4) < 1e-4 and not s.preview_status().distance_layers)
    compare("直接に戻した", manual=(90.0, 0.0))
    s.set_layer_weight_source(1, "distance", start=40.0, end=120.0, w_from=0.0, w_to=1.0)
    # exaggeration のキー → キーに焼く（Ex も焼ける）
    cmds.setKeyframe(rig, attribute="exaggeration", time=1, value=0.0)
    cmds.setKeyframe(rig, attribute="exaggeration", time=5, value=1.0)
    check("プレビュー: exaggeration にキーが打てる（セッションからは動かせない）", cmds.keyframe(rig + ".exaggeration", query=True, keyframeCount=True) == 2)
    try:
        s.preview_set_attr("exaggeration", 0.2)
        check("preview_set_attr: キーがある exaggeration は変えられない", False)
    except S.FacialSessionError:
        check("preview_set_attr: キーがある exaggeration は変えられない", True)
    cmds.cutKey(rig, attribute="exaggeration", clear=True)
    cmds.setAttr(rig + ".useManual", 1)
    cmds.setAttr(rig + ".manualYaw", 90.0)
    cmds.setAttr(cam1 + ".translate", *spherical(head_c, 20, 10, 60.0))
    cmds.setAttr(rig + ".exaggeration", 1.0)
    res_keys = s.preview_bake_to_keys(1, 3, 1, remove_rig=False)
    check("キーに焼く: _Ex の weight にもキーが打たれる", any(k.endswith("_Ex") for k in res_keys["keyed"]), str(res_keys["keyed"]))
    s.preview_clear_keys()
    s.preview_build()

    # ============================================================ 7. 画面
    s.set_layer_weight_source(1, "direct")
    setup = panel.tab("setup")
    layers = panel.tab("layers")
    pose = panel.tab("pose")
    grid = panel.tab("grid")
    shapes_tab = panel.tab("shapes")
    export_tab = panel.tab("export")
    pv = grid.preview
    s.set_quality(sharpness=1.0, step_fps=0.0, exaggeration=1.0, angle_epsilon=0.1)
    s.set_exclude(curves=[], bones=[])
    setup.refresh()
    pump()
    check("セットアップ: 品質の欄（シャープさ・コマ打ち fps・誇張の既定の強さ・角度のしきい値）がある", all(hasattr(setup, n) for n in ("quality_sharpness", "quality_step_fps", "quality_exaggeration", "quality_epsilon")) and any(g.title() == "品質（実行時の見え方）" for g in setup.findChildren(QtWidgets.QGroupBox)))
    check("セットアップ: コマ打ち fps のヒントに「Unity で効きます — Maya のプレビューには掛かりません」", "Unity で効きます — Maya のプレビューには掛かりません" in setup.quality_step_fps.toolTip(), setup.quality_step_fps.toolTip())
    check("セットアップ: シャープさのヒント（1 = そのまま・キーのポーズそのものに寄る）", "1 = そのまま" in setup.quality_sharpness.toolTip() and "キーのポーズそのものに寄ります" in setup.quality_sharpness.toolTip())
    check("セットアップ: 誇張の既定の強さのヒント（0〜1）", "0〜1" in setup.quality_exaggeration.toolTip())
    setup.quality_sharpness.setValue(3.0)
    setup.quality_step_fps.setValue(24.0)
    setup.quality_exaggeration.setValue(0.4)
    setup.quality_epsilon.setValue(0.5)
    pump()
    q = s.doc.quality
    check("セットアップ: 値を変えると文書に入る（元に戻すに積まれる）", q is not None and (q.sharpness, q.step_fps, q.exaggeration, q.angle_epsilon) == (3.0, 24.0, 0.4, 0.5) and s.can_undo, str(q))
    check("セットアップ: シャープさを変えるとプレビューが作り直される（最新）", not pr.is_stale(s.doc))
    s.undo()
    s.undo()
    s.undo()
    s.undo()
    setup.refresh()
    check("セットアップ: 元に戻すで表示も戻る", setup.quality_sharpness.value() == 1.0 and setup.quality_step_fps.value() == 0.0)
    # 除外
    setup.exclude_inputs["curve"].setText("smile")
    setup.on_exclude_add("curve")
    setup.exclude_inputs["bone"].setText("eye_")
    setup.on_exclude_add("bone")
    setup.refresh()  # タブは見えているときにだけ通知で描き直される
    pump()
    check("セットアップ: 除外のパターンを足すと一覧に出て文書に入る", [setup.exclude_lists["curve"].item(i).text() for i in range(setup.exclude_lists["curve"].count())] == ["smile"] and s.doc.exclude.bones == ["eye_"], setup.status.text())
    check("セットアップ: ポーズに当たる名前があると注記が出る", "ポーズに入っています" in setup.exclude_note.text(), setup.exclude_note.text())
    setup.on_exclude_add("curve")
    check("セットアップ: 空のまま追加するとエラーの表示", "入力" in setup.status.text())
    setup.exclude_lists["curve"].item(0).setSelected(True)
    setup.on_exclude_remove("curve")
    setup.refresh()
    check("セットアップ: 選んで「外す」と消える", s.doc.exclude.curves == [] and setup.exclude_lists["curve"].count() == 0)
    setup.exclude_inputs["curve"].setText("smile_L")
    setup.on_exclude_add("curve")
    pose.refresh() if hasattr(pose, "refresh") else None
    pump()
    s.select_point(1, 2)
    pump()
    pose_rows = getattr(pose, "_curve_rows", {})
    row = pose_rows.get("bs.smile_L")
    if row is None:
        check("ポーズ: 除外に当たる行は薄く表示（行が見つからない）", False, str(list(pose_rows)[:6]))
    else:
        row.refresh(next(r for r in s.pose.view().curves if r.name == "bs.smile_L"))
        check("ポーズ: 除外に当たる行は薄いスタイル・ツールチップに除外と書く", "italic" in row.label.styleSheet() and "除外" in row.label.toolTip(), f"{row.label.styleSheet()} | {row.label.toolTip()}")
        other = pose_rows.get("bs.mouth_open")
        check("ポーズ: 除外に当たらない行は通常のスタイル", other is not None and "italic" not in other.label.styleSheet())
    s.set_exclude(curves=[], bones=[])

    # レイヤータブ
    layers.refresh()
    s.set_active_layer(1)
    layers.refresh()
    pump()
    check("レイヤー: 重みの出どころの欄（直接 / カメラの距離・開始 / 終了 / から / まで）", layers.src_combo.count() == 2 and layers.src_combo.currentData() == "direct" and not layers.src_start.isEnabled())
    layers.src_combo.setCurrentIndex(1)
    layers.on_source_activated()
    pump()
    check("レイヤー: 「カメラの距離」を選ぶと文書に入り、距離の欄が使える", s.layer_weight_source(1)["source"] == "distance" and layers.src_start.isEnabled() and layers.src_end.isEnabled(), str(s.doc.layer_weights))
    layers.src_start.setValue(30.0)
    layers.src_end.setValue(150.0)
    layers.src_from.setValue(0.2)
    layers.src_to.setValue(0.9)
    pump()
    sp = s.layer_weight_source(1)
    check("レイヤー: 開始 / 終了（cm）・から / まで を変えると文書に入る", (sp["start"], sp["end"], sp["from"], sp["to"]) == (30.0, 150.0, 0.2, 0.9), str(sp))
    layers.src_end.setValue(-5.0 if layers.src_end.minimum() < 0 else 0.0)
    pump()
    check("レイヤー: 欄の範囲外は欄が受け付けない（0 以上 / 重みは 0〜1）", layers.src_end.minimum() == 0.0 and layers.src_from.maximum() == 1.0)
    layers.src_end.setValue(150.0)
    s.set_active_layer(0)
    layers.refresh()
    check("レイヤー: Neutral を選ぶと重みの出どころの欄は使えない（理由を表示）", not layers.src_combo.isEnabled() and "Neutral" in layers.src_note.text())
    s.set_active_layer(1)
    layers.refresh()
    # プレビューのグループ
    s.set_layer_weight_source(1, "distance", start=40.0, end=120.0, w_from=0.0, w_to=1.0)
    panel.select_tab("grid")
    pv.refresh()
    pump()
    check("プレビュー: 誇張のスライダーがある（_Ex があるので動かせる）", pv.ex_slider.isEnabled() and "誇張" in pv.ex_label.text(), pv.ex_value.text())
    pv.ex_slider.setValue(30)
    check("プレビュー: 誇張のスライダーで rig の exaggeration が動く", abs(cmds.getAttr(rig + ".exaggeration") - 0.3) < 1e-6 and "0.30" in pv.ex_value.text(), pv.ex_value.text())
    row_joy = pv._emotion_rows["emotion_Joy"]
    check("プレビュー: 距離で決まるレイヤーの感情スライダーは使えず「距離で決まる」と出る", not row_joy[1].isEnabled() and "距離で決まる" in row_joy[2].text(), row_joy[2].text())
    check("プレビュー: 角度の表示の横に距離が出る", "距離" in pv.angle_label.text() and "cm" in pv.angle_label.text(), pv.angle_label.text())
    # _Ex の無い文書では誇張スライダーは使えない
    # ベイクの報告（画面）
    grid.btn_bake_all.click()
    pump()
    check("グリッド: ベイクの報告に誇張用のシェイプの数が出る", "誇張用" in grid.report.toPlainText(), grid.report.toPlainText())

    # 文言: チケット名・準備中が画面に無い・シェイプタブ・ポーズタブの文言
    bad_pat = re.compile(r"\bF\d(?:-\d+)?\b|FU-\d|F5 から|準備中")
    bad_texts: list[str] = []
    for key in ("setup", "grid", "pose", "shapes", "layers", "validate", "export"):
        w = panel.tab(key)
        for child in w.findChildren(QtWidgets.QWidget):
            for t in (getattr(child, "text", lambda: "")() if hasattr(child, "text") and callable(child.text) else "", child.toolTip(), getattr(child, "title", lambda: "")() if hasattr(child, "title") else ""):
                if isinstance(t, str) and bad_pat.search(t):
                    bad_texts.append(f"{key}: {t[:50]}")
    check("文言: どのタブにもチケット名（F5 など）・準備中が出ていない", not bad_texts, str(bad_texts[:4]))
    hints = " ".join(l.text() for l in shapes_tab.findChildren(QtWidgets.QLabel))
    check("シェイプ: 対象のシェイプの説明にタグ 5 つ（元から / FC_ / fcs_ / 組み合わせ / 作ったもの）", all(t in hints for t in ("元から / FC_（ベイクの結果）/ fcs_（彫り用）/ 組み合わせ", "作ったもの")))
    check("シェイプ: 整理の説明に 空 / 未使用 / 孤立 の定義", all(t in hints for t in ("空 = 差分が無い", "未使用 = どのポーズ・作業セットからも使われていない", "孤立 = 対応する点が無い FC_ / fcs_")))
    check("シェイプ: 組み合わせの説明（ベイクした結果に含まれ、補正そのものは Unity へ出さない）", "2 本を同時に上げたときだけ効く補正です。Maya の中で計算され、ベイクした結果には含まれます（補正そのものは Unity へ出しません）" in hints and "ベイクした結果には含まれます" in shapes_tab.combo_btn.toolTip())
    check("シェイプ: 誇張の説明（重み 1 超は別の誇張用シェイプに焼かれる）・「評価は F5 から」が無い", "誇張用のシェイプ（FC_…_Ex）" in hints and "F5" not in hints and "誇張用のシェイプ" in shapes_tab.ex_btn.toolTip())
    check("シェイプ: 「終わりは上の」ではなくどの箱かを書く", "「彫る」の箱の「彫り終わる」" in shapes_tab.combo_sculpt_btn.toolTip() and "上の" not in shapes_tab.combo_sculpt_btn.toolTip())
    check("シェイプ: 孤立の色とツールチップが合う（赤字と書かない）", "赤字" not in shapes_tab.sculpt_list.toolTip() and "オレンジ" in shapes_tab.sculpt_list.toolTip())
    check("ポーズ: 土台の表情の説明がどれも「ポーズの値に足されて / 足して」で揃う", "ポーズの値に足されて" in pose.base_note.text() and "ポーズの値に足して" in pose.btn_base_pick.toolTip() and "ポーズの値だけ" in pose.btn_base_clear.toolTip())

    # スクリーンショット
    def snap(widget, name: str, w: int, h: int) -> None:
        if not shot_dir:
            return
        widget.resize(w, h)
        widget.layout().activate() if widget.layout() else None
        pump()
        widget.grab().save(str(Path(shot_dir) / f"f5_{name}.png"))

    if shot_dir:
        panel.select_tab("setup")
        snap(panel, "setup", 560, 1500)
        panel.select_tab("layers")
        snap(panel, "layers", 560, 1000)
        panel.select_tab("grid")
        pv.setEnabled(True)
        snap(pv, "preview", 560, 420)

    # 後片付け
    pr.delete(asset)
    panel.detach()
    s.close()
    shutil.rmtree(tmp, ignore_errors=True)


def _with_exclude(doc, curves):
    import copy

    d = copy.deepcopy(doc)
    d.exclude.curves = list(curves)
    return d


if __name__ == "__main__":
    maya.standalone.initialize(name="python")
    try:
        run()
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
