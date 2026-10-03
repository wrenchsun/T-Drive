"""FacialController の部位別の強さ（F5-6。ベイクのとき掛ける）のスモーク（mayapy・画面なし）。

  set QT_QPA_PLATFORM=offscreen
  "C:/Program Files/Autodesk/Maya2026/bin/mayapy.exe" tests/maya/facial_part_smoke.py

確かめること:
- ベイク: シェイプのパターン（smile 0.5）= 焼いた差分が重み半分のポーズの差分と一致 / ボーンのパターン（eye_L 0.5）= ずらし半分 /
  感情レイヤーは掛けたあとのポーズ同士の差分 / パース補正のキーにも掛かる / 除外が優先 / 上の行が優先
- 編集中のシーン・保存データのポーズは元のまま
- 自動の「変更あり」: 強さを変えると全点が変更あり・文言・焼き直すと消える・従来の記録（強さの指紋なし）は変更なし・強さを足すと変更あり
- セッション: 足す・外す・上下・値の変更（Undo / やり直し）・失敗では変わらない・保存の往復
- 画面: セットアップタブの「部位別の強さ」の表（足す・外す・上へ / 下へ・強さのスピンボックス）・文言
スクリーンショット: 環境変数 TDRIVE_UI_SHOT_DIR があれば part_setup を書く（幅 480 px）。
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
    from tdrive_facial import pose_apply, scene, ui
    from tdrive_facial import session as S
    from tdrive_facial.core import fcpose_io, naming
    from tdrive_facial.core import validate as V
    from tdrive_facial.core.model import BoneOffset, SourcePose

    tmp = Path(tempfile.mkdtemp(prefix="tdrive_part_"))
    project.set_root(tmp)
    facial_fixture.build_mini_head()
    face = scene.resolve_mesh("mini_face")
    asset = "mini"
    s = S.current()
    s.close()
    s.listeners.clear()
    s.state_listeners.clear()

    doc0 = facial_fixture.make_doc()
    doc0.bake.delta_threshold = 1e-5
    p_doc = tmp / "src" / "mini.fcpose.json"
    p_doc.parent.mkdir()
    fcpose_io.save(doc0, p_doc)
    ws_curves = ["bs.mouth_open", "bs.smile_L", "bs.smile_R", "bs.brow_up"]

    s.open(p_doc)
    s.add_to_working_set(curves=ws_curves, bones=["eye_L", "eye_R"])
    s.set_mirror(bone_axis="X", suffix_l="_L", suffix_r="_R")

    nverts = scene.vertex_count(face)

    def dense(node: str, alias: str) -> np.ndarray:
        out = np.zeros((nverts, 3))
        got = scene.read_target_delta(node, alias)
        if got is not None:
            comps, d = got
            out[list(comps)] = d
        return out

    def pose_delta(pose) -> np.ndarray:
        """強さなし・除外なしの文書で、ポーズを当てたときの頂点の動き（独立した期待値の計算）。"""
        plain = facial_fixture.make_doc()
        ref = scene.enter_reference_pose([face], extra_joints=["eye_L", "eye_R", "head"])
        try:
            base = scene.read_points(face)
            pose_apply.apply_pose(plain, pose, ref)
            return scene.read_points(face) - base
        finally:
            ref.restore()

    def q_axis(axis, deg):
        return facial_fixture._q_axis(axis, deg)

    def baked(layer: str, r: int, c: int) -> np.ndarray:
        return dense(scene.primary_blend_shape(face), naming.morph_name(asset, layer, r, c))

    def err(a, b) -> float:
        return float(np.abs(a - b).max())

    # ============================================================ 1. ベイク: シェイプのパターン
    s.bake_all()
    node = scene.primary_blend_shape(face)
    base_total = {k: baked(*k) for k in (("Neutral", 1, 2), ("Neutral", 0, 2), ("Neutral", 2, 1), ("Joy", 1, 2), ("Joy", 0, 2))}
    check("前提: 強さなしの焼き = ポーズの差分", err(base_total[("Neutral", 1, 2)], pose_delta(s.doc.layers[0].points[(1, 2)].pose)) < 2e-5)
    state_plain = scene.get_bake_exclude(node)
    check("前提: 強さなしの記録は従来と同じ 12 桁（+ なし）", state_plain and all("+" not in v and len(v) == 12 for v in state_plain.values()), str(list(state_plain.values())[:2]))

    r = s.add_part_strength("smile", 0.5)
    check("追加: 「smile」0.5 が一覧に入る", r.ok and s.part_strengths() == [("smile", 0.5)], str(s.part_strengths()))
    all_pts = sorted({(li, rr, cc) for li, _l, (rr, cc), _pt in V._bake_candidates(s.doc)})
    check("自動: 足した直後は全点が変更あり（stale_points / 検証）", s.stale_points() == all_pts and {(i.layer, i.row, i.col) for i in s.validate() if i.code == "point_changed_since_bake"} == set(all_pts), str(s.stale_points()))
    msgs = [i.message for i in s.validate() if i.code == "point_changed_since_bake"]
    check("自動: 文言 = 部位別の強さを変えたあと、焼き直していません", msgs and all("部位別の強さを変えたあと、焼き直していません" in x and "除外" not in x for x in msgs), msgs[:1] and msgs[0])
    s.bake_all()
    check("自動: 焼き直すと変更なし", s.stale_points() == [] and not [i for i in s.validate() if i.code == "point_changed_since_bake"], str(s.stale_points()))
    rec = scene.get_bake_exclude(node)
    check("記録: 強さがあると 12 桁 + 強さの指紋になり、全部のシェイプが今の指紋と同じ", rec and all(v == V.exclude_signature(s.doc) and "+" in v for v in rec.values()), str(list(rec.values())[:1]))

    # Neutral (1,2) = mouth_open 0.6 + smile_L 0.8 → smile のぶんが半分
    n12 = baked("Neutral", 1, 2)
    want = pose_delta(SourcePose({"bs.mouth_open": 0.6, "bs.smile_L": 0.4}, {}))
    check("シェイプ: smile 0.5 → 焼いた差分 = smile_L が 0.4 のポーズの差分（1e-5）", err(n12, want) < 2e-5 and err(n12, base_total[("Neutral", 1, 2)]) > 0.05, f"{err(n12, want):.2e}")
    check("シェイプ: 当たらない点（mouth_open + ボーン）は強さなしと同じ", err(baked("Neutral", 0, 2), base_total[("Neutral", 0, 2)]) < 1e-9)
    # 感情: Joy (1,2) = smile_L 1, smile_R 1, brow_up 0.7。掛けたあとの Joy − 掛けたあとの Neutral
    joy = baked("Joy", 1, 2)
    want_j = pose_delta(SourcePose({"bs.smile_L": 0.5, "bs.smile_R": 0.5, "bs.brow_up": 0.7}, {})) - pose_delta(SourcePose({"bs.mouth_open": 0.6, "bs.smile_L": 0.4}, {}))
    check("感情: Joy の差分 = 掛けたあとの Joy − 掛けたあとの Neutral（1e-5）", err(joy, want_j) < 2e-5, f"{err(joy, want_j):.2e}")
    check("感情: 掛ける前の差分（Joy − Neutral）とは違う", err(joy, base_total[("Joy", 1, 2)]) > 0.05)

    # 編集中のシーン・データは元のまま
    check("元のまま: 保存データのポーズは書き換えない", s.doc.layers[0].points[(1, 2)].pose.curves == {"bs.mouth_open": 0.6, "bs.smile_L": 0.8}, str(s.doc.layers[0].points[(1, 2)].pose.curves))
    s.select_point(1, 2)
    sl = cmds.getAttr("bs.smile_L")
    check("元のまま: 編集中のシーン（スライダー）は元の重み 0.8", abs(sl - 0.8) < 1e-6, str(sl))
    s.end_edit(quiet=True)

    # ============================================================ 2. ボーンのパターン・優先順位・除外
    s.remove_part_strength(0)
    check("外す: 一覧が空になる・全点が変更あり", s.part_strengths() == [] and s.stale_points() == all_pts)
    s.add_part_strength("eye_L", 0.5)  # (0,2): eye_L の Y 回転 20 度 → 10 度
    s.bake_all()
    got = baked("Neutral", 0, 2)
    want_b = pose_delta(SourcePose({"bs.mouth_open": 0.3}, {"eye_L": BoneOffset(t=(0.0, 0.0, 0.0), r=q_axis((0, 1, 0), 10.0))}))
    check("ボーン: eye_L 0.5 → ずらしは回転 20° が 10°（球面補間）の差分と一致（1e-5）", err(got, want_b) < 2e-5 and err(got, base_total[("Neutral", 0, 2)]) > 0.01, f"{err(got, want_b):.2e}")
    check("ボーン: 当たらないボーン（eye_R）の点は変わらない", err(baked("Neutral", 2, 1), base_total[("Neutral", 2, 1)]) < 1e-9)
    s.set_part_strength(0, 0.0)
    s.bake_all()
    got0 = baked("Neutral", 0, 2)
    want0 = pose_delta(SourcePose({"bs.mouth_open": 0.3}, {}))
    check("ボーン: 強さ 0 → ずらしなしの差分", err(got0, want0) < 2e-5, f"{err(got0, want0):.2e}")
    s.set_part_strength(0, 0.5)
    s.add_part_strength("eye", 0.0)  # 下の行: eye_R に当たる（eye_L は上の行が優先）
    s.bake_all()
    check("優先: eye_L は上の行（0.5）・eye_R は下の行（0 = 動かない）", err(baked("Neutral", 0, 2), want_b) < 2e-5 and err(baked("Neutral", 2, 1), pose_delta(SourcePose({}, {}))) < 2e-5)
    s.move_part_strength(1, -1)  # eye が上 → eye_L も 0
    check("優先: 並べ替えで入れ替わる", s.part_strengths() == [("eye", 0.0), ("eye_L", 0.5)])
    s.bake_all()
    check("優先: 上の行（eye 0）が eye_L にも効く", err(baked("Neutral", 0, 2), want0) < 2e-5)
    s.add_exclude("bone", "eye_L")
    s.set_part_strength(0, 1.0)
    s.set_part_strength(1, 0.1)
    s.bake_all()
    check("除外が優先: 除外したボーンは強さに関わらず焼かれない", err(baked("Neutral", 0, 2), want0) < 2e-5, f"{err(baked('Neutral', 0, 2), want0):.2e}")
    s.set_exclude(curves=[], bones=[])
    while s.part_strengths():
        s.remove_part_strength(0)

    # ============================================================ 3. パース補正のキー
    s.add_part_strength("smile", 0.5)
    s.set_perspective_enabled(True)
    pa = SourcePose({"bs.mouth_open": 0.5, "bs.smile_L": 0.8}, {"eye_R": BoneOffset(t=(0.2, 0.0, 0.0))})
    s.add_perspective_key(30.0, pa)
    s.add_perspective_key(80.0)
    s.bake_all()
    kd = dense(node, "FC_mini_Persp_K0")
    want_k = pose_delta(SourcePose({"bs.mouth_open": 0.5, "bs.smile_L": 0.4}, {"eye_R": BoneOffset(t=(0.2, 0.0, 0.0))}))
    check("パース補正: キーのポーズにも強さが掛かる（smile_L 0.4 の差分と一致）", err(kd, want_k) < 2e-5 and err(kd, pose_delta(pa)) > 0.02, f"{err(kd, want_k):.2e}")
    check("パース補正: 焼き直し後は変更なし", s.stale_perspective_keys() == [] and s.stale_points() == [])
    s.set_part_strength(0, 0.25)
    check("パース補正: 強さを変えるとキーも変更あり（文言も同じ）", s.stale_perspective_keys() == [0] and any(i.code == "perspective_key_changed" and "部位別の強さを変えたあと、焼き直していません" in i.message for i in s.validate()))
    s.bake_stale()
    check("パース補正: 変更のある点・キーだけ焼き直すと消える", s.stale_perspective_keys() == [] and s.stale_points() == [])

    # ============================================================ 4. 従来の記録・除外との組み合わせ
    s.remove_part_strength(0)
    s.bake_all()
    for n in scene.blend_shapes(face):
        rec = scene.get_bake_exclude(n)
        scene.set_bake_exclude(n, {k: v.split("+")[0] for k, v in rec.items()})  # 従来の版が書いた記録（強さの指紋なし）
    s.refresh_scene(notify=False)
    check("従来の記録: 強さなしの文書では変更なし（既存のシーンが一斉に変更ありにならない）", s.stale_points() == [] and s.stale_perspective_keys() == [])
    s.add_part_strength("smile", 0.5)
    check("従来の記録: 強さを足すと変更あり", s.stale_points() == all_pts)
    s.remove_part_strength(0)
    check("従来の記録: 外すと記録と同じに戻り変更なし", s.stale_points() == [])
    s.add_exclude("curve", "smile")
    s.add_part_strength("eye", 0.5)
    both = [i.message for i in s.validate() if i.code == "point_changed_since_bake"]
    check("文言: 除外と強さの両方を変えたら 1 つの文言", both and all("補正から除外するもの・部位別の強さを変えたあと、焼き直していません" in x for x in both), both[:1] and both[0])
    s.remove_part_strength(0)
    only_ex = [i.message for i in s.validate() if i.code == "point_changed_since_bake"]
    check("文言: 除外だけなら従来の文言", only_ex and all("補正から除外するものを変えたあと、焼き直していません" in x and "部位別" not in x for x in only_ex))
    s.set_exclude(curves=[], bones=[])
    s.bake_all()
    for n in scene.blend_shapes(face):
        scene.set_bake_exclude(n, {})
    s.add_part_strength("smile", 0.5)
    s.refresh_scene(notify=False)
    check("記録なし: 不明 = 今と同じとみなし変更なしにならない", s.stale_points() == [])
    s.remove_part_strength(0)

    # ============================================================ 5. セッション API
    d_before = fcpose_io.to_dict(s.doc)
    bads = [s.add_part_strength("", 0.5), s.add_part_strength("  ", 0.5), s.add_part_strength("x", 1.5), s.add_part_strength("x", -0.1), s.add_part_strength("x", float("nan")), s.remove_part_strength(3), s.set_part_strength(0, 0.2), s.move_part_strength(4, -1)]
    check("API: 空・範囲外・NaN・存在しない行は失敗して文書は変わらない", not any(b.ok for b in bads) and fcpose_io.to_dict(s.doc) == d_before, str([b.code for b in bads]))
    s.add_part_strength("a", 0.1)
    check("API: 重複は失敗", not s.add_part_strength("a", 0.2).ok and s.part_strengths() == [("a", 0.1)])
    s.add_part_strength("b", 0.2)
    s.add_part_strength("c", 0.3)
    s.move_part_strength(2, -1)
    check("API: 上へ", [p for p, _ in s.part_strengths()] == ["a", "c", "b"])
    s.move_part_strength(0, -1)
    s.move_part_strength(2, 1)
    check("API: 端では動かず失敗にもならない", [p for p, _ in s.part_strengths()] == ["a", "c", "b"])
    s.set_part_strength(1, 0.75)
    check("API: 強さを変える", s.part_strengths()[1] == ("c", 0.75))
    check("API: 元に戻す・やり直し", s.undo() and s.part_strengths()[1] == ("c", 0.3) and s.redo() and s.part_strengths()[1] == ("c", 0.75))
    check("API: part_strength_of は当たらないものは 1、当たるものは強さ", s.part_strength_of("curve", "q.zzz") == 1.0 and s.part_strength_of("curve", "bs.a_x") == 0.1)
    s.add_exclude("curve", "a_")
    check("API: 除外されている名前は 1", s.part_strength_of("curve", "bs.a_x") == 1.0)
    s.set_exclude(curves=[], bones=[])
    p_rt = tmp / "src" / "rt.fcpose.json"
    fcpose_io.save(s.doc, p_rt)
    back = fcpose_io.load(p_rt)
    check("保存の往復: partStrength が残る", [(e.pattern, e.strength) for e in back.bake.part_strength] == [("a", 0.1), ("c", 0.75), ("b", 0.2)])
    while s.part_strengths():
        s.remove_part_strength(0)
    check("後片付け: 空にできる", s.part_strengths() == [])

    # ============================================================ 6. 画面
    panel = ui.FacialPanel()
    panel.resize(480, 900)
    pump()
    setup = panel.tab("setup")
    setup.refresh()
    titles = [g.title() for g in setup.findChildren(QtWidgets.QGroupBox)]
    check("画面: 「部位別の強さ」の箱が「補正から除外するもの」の下にある", "部位別の強さ" in titles and titles.index("部位別の強さ") == titles.index("補正から除外するもの") + 1, str(titles))
    hint_text = " ".join(l.text() for l in setup.findChildren(QtWidgets.QLabel))
    check("画面: 説明（部分一致・上の行が優先・ベイクのときだけ・編集中は元の値・ゲーム中は変えられない）", all(t in hint_text for t in ("ベイクのときだけ", "上の行が優先", "編集中のビューには、ポーズに入れた元の値", "焼いたあとのプレビューで確かめてください", "ゲームの実行中に変えることはできません", "部分一致")), hint_text[-400:])
    setup.part_input.setText("smile")
    setup.on_part_add()
    setup.part_input.setText("eye")
    setup.on_part_add()
    setup.refresh()
    tbl = setup.part_table
    check("画面: 追加すると表に出て文書に入る（強さ 0.5 から）", tbl.rowCount() == 2 and tbl.item(0, 0).text() == "smile" and abs(tbl.cellWidget(0, 1).value() - 0.5) < 1e-9 and s.part_strengths() == [("smile", 0.5), ("eye", 0.5)], setup.status.text())
    tbl.cellWidget(1, 1).setValue(0.2)
    check("画面: スピンボックスで強さを変えると文書に入る（表は作り直さない）", s.part_strengths()[1] == ("eye", 0.2) and tbl.rowCount() == 2 and abs(tbl.cellWidget(1, 1).value() - 0.2) < 1e-9, str(s.part_strengths()))
    tbl.selectRow(1)
    setup.on_part_move(-1)
    check("画面: 選んで「上へ」で入れ替わり、動かした行が選ばれたまま", s.part_strengths() == [("eye", 0.2), ("smile", 0.5)] and tbl.item(0, 0).text() == "eye" and abs(tbl.cellWidget(0, 1).value() - 0.2) < 1e-9 and setup._part_row() == 0, str(s.part_strengths()))
    setup.on_part_move(1)
    check("画面: 「下へ」で戻る", s.part_strengths() == [("smile", 0.5), ("eye", 0.2)] and setup._part_row() == 1)
    setup.part_input.setText("smile")
    setup.on_part_add()
    check("画面: 重複はエラーの表示", "既にあります" in setup.status.text() and len(s.part_strengths()) == 2, setup.status.text())
    setup.part_input.setText("")
    setup.on_part_add()
    check("画面: 空のまま追加するとエラーの表示", "入力" in setup.status.text())
    tbl.selectRow(0)
    setup.on_part_remove()
    check("画面: 選んで「外す」と消える", s.part_strengths() == [("eye", 0.2)] and tbl.rowCount() == 1 and tbl.item(0, 0).text() == "eye")
    tbl.clearSelection()
    setup.on_part_remove()
    check("画面: 選ばずに「外す」は案内を出して何も消さない", "選んでください" in setup.status.text() and len(s.part_strengths()) == 1)
    s.undo()
    setup.refresh()
    check("画面: 元に戻す（セッション）で表も戻る", tbl.rowCount() == 2)
    bad_pat = re.compile(r"F\d|準備中|チケット")
    texts = [setup.part_add.text(), setup.part_remove.text(), setup.part_up.text(), setup.part_down.text(), hint_text]
    check("画面: 文言にチケット名・準備中が無い（部位別の強さの箱）", not any(bad_pat.search(t) for t in texts[:4]) and "準備中" not in hint_text)

    shot_dir = os.environ.get("TDRIVE_UI_SHOT_DIR")
    if shot_dir:
        Path(shot_dir).mkdir(parents=True, exist_ok=True)
        panel.select_tab("setup")
        panel.resize(480, 1500)
        pump()
        setup.grab().save(str(Path(shot_dir) / "part_setup.png"))

    panel.detach()
    s.close()
    shutil.rmtree(tmp, ignore_errors=True)


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
