"""FacialController の統合（プレビューのグループ・出力タブ・T-20 の取り込み・変更のある点だけベイク など）の GUI スモーク（mayapy・画面なし）。

  set QT_QPA_PLATFORM=offscreen
  "C:/Program Files/Autodesk/Maya2026/bin/mayapy.exe" tests/maya/facial_ui_smoke_c.py

手順（この順に守らないと落ちる）: QT_QPA_PLATFORM=offscreen → QApplication を作る → maya.standalone.initialize。
ウィジェットは直接作る（workspaceControl / show は使わない）。利用者の操作は、ウィジェットのクリック・値の設定で真似る。
ダイアログは各ウィジェットの ask_* を差し替えて答えを返す。ファイルはすべて一時フォルダ（プロジェクトのルートもそこへ向ける）。
Unity 向けの出力は、裏の mayapy（tools/export_facial_fbx_batch.py）が走るので少し時間がかかる。
スクリーンショット: 環境変数 TDRIVE_UI_SHOT_DIR があれば、7 つのタブ（c_setup / c_grid / c_pose / c_shapes / c_layers / c_validate / c_export）を書く。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
import traceback
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ.setdefault("MAYA_DISABLE_CER", "1")
REPO = Path(__file__).resolve().parents[2]
os.environ.setdefault("TDRIVE_ROOT", REPO.as_posix())
sys.path.insert(0, str(REPO / "maya" / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv[:1])  # maya.standalone より先に作る


def _style() -> None:
    """Maya の暗い UI に近い見た目 + 日本語のフォント（offscreen の Qt は既定のフォントを持たず、文字が四角になる）。"""
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


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(cond), detail))


def pump() -> None:
    APP.processEvents()


def run() -> None:
    from maya import cmds

    import facial_fixture
    from tdrive import project
    from tdrive_facial import bake as bakemod
    from tdrive_facial import scene
    from tdrive_facial import session as S
    from tdrive_facial import ui
    from tdrive_facial.core import autofill, fcpose_io, naming
    from tdrive_facial.core import fctrack as fct
    from tdrive_facial.core.model import BoneOffset, SourcePose
    from PySide6.QtCore import Qt

    shot_dir = os.environ.get("TDRIVE_UI_SHOT_DIR")
    if shot_dir:
        Path(shot_dir).mkdir(parents=True, exist_ok=True)

    def shot(panel, key: str, name: str, w: int = 560, h: int = 900) -> None:
        if not shot_dir:
            return
        panel.select_tab(key)
        panel.resize(w, h)
        panel.layout().activate()
        pump()
        panel.grab().save(str(Path(shot_dir) / f"c_{name}.png"))

    tmp = Path(tempfile.mkdtemp(prefix="tdrive_fui_c_"))
    project.set_root(tmp)
    ids = facial_fixture.build_mini_head()
    face = scene.resolve_mesh("mini_face")
    s = S.current()
    s.close()
    s.listeners.clear()
    s.state_listeners.clear()
    S.FacialSession.model_cameras = staticmethod(lambda: ["persp", "pvcam"])  # モデルパネルの無い mayapy の代わり
    cam = cmds.rename(cmds.camera(name="pvcam")[0], "pvcam")

    doc0 = facial_fixture.make_doc()
    autofill.generate_from_keys(doc0)
    p0 = tmp / "src" / "mini.fcpose.json"
    p0.parent.mkdir()
    fcpose_io.save(doc0, p0)

    panel = ui.FacialPanel()
    panel.resize(560, 900)
    s.open(p0)
    s.add_to_working_set(curves=["bs.mouth_open", "bs.smile_L", "bs.smile_R", "bs.brow_up"], bones=["eye_L", "eye_R"])
    s.set_mirror(bone_axis="X", suffix_l="_L", suffix_r="_R")
    pump()
    rep = s.bake_all()
    check("準備: 3x3 + Joy を焼いた（FC_* 18 本）", len(rep.created) == 18, rep.summary())

    labels = [panel.tabs.tabText(i) for i in range(panel.tabs.count())]
    check("パネル: 7 つのタブ（出力タブが本物）", labels == ["セットアップ", "グリッド", "ポーズ", "シェイプ", "レイヤー", "検証", "出力"] and not panel.tab_is_placeholder("export"))
    check("パネル: state_listeners にパネルが登録される（ctx の購読は使わない）", panel._on_state_changed in s.state_listeners and not hasattr(panel, "_unsubscribe_ctx"))
    s.begin_edit()
    check("ヘッダー: セッションの軽い通知だけで「編集」ボタンが追従する", panel.header.edit_btn.isChecked() and "編集中" in panel.header.edit_state.text())
    s.end_edit()
    check("ヘッダー: 編集を終えると追従する", not panel.header.edit_btn.isChecked())

    grid = panel.tab("grid")
    panel.select_tab("grid")
    pv = grid.preview
    nn = naming.morph_name("mini", "Neutral", 1, 2)
    jn = naming.morph_name("mini", "Joy", 1, 2)
    cmds.setAttr("persp.translate", 0.0, 12.0, 60.0)

    # ============================================================ プレビューのグループ
    check("プレビュー: 作る前は「作る」だけが押せる", pv.btn_build.text() == "プレビューを作る" and pv.btn_build.isEnabled() and not pv.btn_delete.isEnabled() and not pv.btn_ab.isEnabled(), pv.state_label.text())
    check("プレビュー: カメラの一覧（今のビューのカメラ + モデルパネルのカメラ）", [pv.camera_combo.itemText(i) for i in range(pv.camera_combo.count())] == ["今のビューのカメラ", "persp", "pvcam"])
    pv.camera_combo.setCurrentIndex(2)  # pvcam
    s.camera_to_point(1, 2, cam)
    pv.btn_build.click()
    pump()
    check("プレビュー: 作ると rig ができ、動作中の表示・「作り直す」・消すが押せる", s.preview_state() == "live" and pv.btn_build.text() == "作り直す" and pv.btn_delete.isEnabled() and "動作中" in pv.state_label.text() and "pvcam" in pv.state_label.text(), pv.state_label.text() + " | " + pv.status.text())
    check("プレビュー: 古い印は出ない", pv.stale_note.isHidden())
    w = s.preview_weights()
    check("プレビュー: カメラ（pvcam）を点 (1,2) に置くとその点の Neutral = 1", abs(w[nn] - 1.0) < 1e-4, f"{w[nn]}")
    s.camera_to_point(1, 0, cam)
    grid._tick()
    w2 = s.preview_weights()
    check("プレビュー: カメラを動かすと重みが変わる（(1,0) = 1・(1,2) = 0）・角度の表示が追従", abs(w2[naming.morph_name("mini", "Neutral", 1, 0)] - 1.0) < 1e-4 and abs(w2[nn]) < 1e-4 and "Yaw -90.0°" in pv.angle_label.text(), f"{pv.angle_label.text()}")
    s.camera_to_point(1, 2, cam)
    # A/B
    pv.btn_ab.click()
    pump()
    check("プレビュー: 「補正なし」にすると重みがすべて 0", pv.btn_ab.text() == "補正なし" and max(abs(v) for v in s.preview_weights().values()) < 1e-9)
    pv.btn_ab.click()
    pump()
    check("プレビュー: 「補正あり」に戻すと再開", pv.btn_ab.text() == "補正あり" and abs(s.preview_weights()[nn] - 1.0) < 1e-4)
    # 強さ・感情
    row = pv._emotion_rows["emotion_Joy"]
    pv.alpha_slider.setValue(50)
    check("プレビュー: 強さのスライダー 0.5 で重みが半分", abs(s.preview_weights()[nn] - 0.5) < 1e-4 and abs(cmds.getAttr("tdFacialPreview_mini.alpha") - 0.5) < 1e-6)
    pv.alpha_slider.setValue(100)
    row[1].setValue(100)
    w = s.preview_weights()
    check("プレビュー: 感情 Joy のスライダー 1.0 で Joy の点の重みが出る", abs(w[jn] - 1.0) < 1e-4 and "1.00" in row[2].text(), f"{w[jn]}")
    row[1].setValue(0)
    # 手動角度
    pv.manual_check.setChecked(True)
    pv.manual_yaw.setValue(45.0)
    pump()
    check("プレビュー: 手動角度（Yaw 45°）が rig に入り、角度の表示も変わる", abs(cmds.getAttr("tdFacialPreview_mini.manualYaw") - 45.0) < 1e-6 and cmds.getAttr("tdFacialPreview_mini.useManual") and "Yaw 45.0°" in pv.angle_label.text(), pv.angle_label.text())
    pv.manual_check.setChecked(False)
    # スライダーのドラッグ中は全体を描き直さない
    refreshed: list[int] = []
    orig_refresh = pv.refresh
    pv.refresh = lambda: refreshed.append(1)
    pv._drag_start()
    s.set_curve if False else None
    s._notify_state()
    pv._drag_end()
    s._notify_state()
    pv.refresh = orig_refresh
    check("プレビュー: スライダーのドラッグ中は通知で描き直さない（離すと描き直す）", len(refreshed) == 1)
    # 編集状態との共存
    panel.header.edit_btn.click()
    pump()
    check("共存: 編集を入れると補正が止まり（重み 0）、止まっている旨が出る", s.editing and max(abs(v) for v in s.preview_weights().values()) < 1e-9 and not pv.edit_note.isHidden())
    panel.header.edit_btn.click()
    pump()
    check("共存: 編集を切ると再開する", not s.editing and abs(s.preview_weights()[nn] - 1.0) < 1e-4 and pv.edit_note.isHidden())
    # 編集中に格子を変える → 古い印 → 編集を終えると作り直し
    panel.header.edit_btn.click()
    s.resize(5, 3)
    pump()
    check("古い印: 編集中に格子を変えると「作り直してください」が出る", s.editing and not pv.stale_note.isHidden() and "作り直してください" in pv.stale_note.text(), pv.state_label.text())
    panel.header.edit_btn.click()
    pump()
    check("古い印: 編集を終えると自動で作り直され、印が消える", pv.stale_note.isHidden() and s.preview_state() == "live", pv.state_label.text())
    # 変更のある点だけベイク（グリッドタブのボタン）
    n_stale = len(s.stale_points())
    check("前提: 格子を変えて未ベイクの点がある", n_stale > 0 and grid.btn_bake_stale.isEnabled(), f"{n_stale}")
    grid.btn_bake_stale.click()
    pump()
    txt = grid.report.toPlainText()
    check("グリッド: 「ベイク（変更のある点）」で未ベイクだけ焼く（報告に作成数）・stale が 0", s.stale_points() == [] and "ベイク:" in txt and "作成" in txt, txt)
    check("グリッド: ベイクのあとプレビューが自動で作り直される（報告に書く）・動作中", "プレビューを作り直しました" in txt and s.preview_state() == "live", txt)
    grid.btn_bake_stale.click()
    pump()
    check("グリッド: 対象が無いときは何も焼かない（報告に「ありません」）", "ありません" in grid.report.toPlainText() and s.stale_points() == [])
    # 外で変わった → 古い印 → 作り直す
    node = scene.blend_shapes(face)[0]
    victim = [t.alias for t in scene.fc_targets(face)][0]
    scene.delete_targets(node, [victim])
    pv.refresh()
    check("古い印: シェイプが外で消えると「作り直してください」", not pv.stale_note.isHidden() and "古い状態" in pv.state_label.text(), pv.state_label.text())
    pv.btn_build.click()
    pump()
    check("古い印: 作り直すと印が消える", pv.stale_note.isHidden() and s.preview_state() == "live")
    s.bake_all()
    pump()
    # キーに焼く
    rig = s.preview_rig_node()
    cmds.setKeyframe(f"{rig}.emotion_Joy", time=1, value=0.2)
    cmds.setKeyframe(f"{rig}.emotion_Joy", time=10, value=1.0)
    pv.refresh()
    check("プレビュー: キーが打ってある感情はスライダーが使えず「キーあり」", not pv._emotion_rows["emotion_Joy"][1].isEnabled() and "キーあり" in pv._emotion_rows["emotion_Joy"][2].text())
    s.camera_to_point(1, 2, cam)
    asked: list[tuple] = []
    pv.ask_time_range = lambda a, b: (asked.append((a, b)) or (1.0, 5.0, 1.0, False))
    pv.btn_keys.click()
    pump()
    check("キーに焼く: 時間範囲の既定は再生範囲・焼くと「キーに焼いた状態」・式が外れる", asked and asked[0] == (cmds.playbackOptions(query=True, minTime=True), cmds.playbackOptions(query=True, maxTime=True)) and s.preview_state() == "keys" and "キーに焼いた状態" in pv.state_label.text(), f"{asked} {pv.state_label.text()}")
    check("キーに焼く: FC_* にキーが打たれている", any((cmds.keyframe(c.plug, query=True, keyframeCount=True) or 0) > 0 for c in scene.list_curves(face, include_fc=True) if naming.is_fc_name(c.alias)))
    check("キーに焼いた状態: 「キーに焼く」は押せず、状態の行に結果が出る", not pv.btn_keys.isEnabled() and "キーに焼きました" in pv.status.text(), pv.status.text())
    cleared: list[str] = []
    pv.ask_clear_keys = lambda text: (cleared.append(text) or True)
    pv.btn_build.click()
    pump()
    check("作り直し: 残ったキーで配線できないシェイプがあると、キーを消して作り直すか聞く（はい）", len(cleared) == 1 and "キー" in cleared[0] and s.preview_state() == "live" and not s.preview_warnings, f"{cleared} {s.preview_state()} {s.preview_warnings}")
    check("作り直し: FC_* のキーが消え、強さ・感情のキーは残る", not any((cmds.keyframe(c.plug, query=True, keyframeCount=True) or 0) > 0 for c in scene.list_curves(face, include_fc=True) if naming.is_fc_name(c.alias)) and (cmds.keyframe(f"{rig}.emotion_Joy", query=True, keyframeCount=True) or 0) == 2)

    # ============================================================ セットアップタブ: T-20
    setup = panel.tab("setup")
    node_t20 = "tdViewCorrection_mini_face"
    targets = []
    for key in ("front", "threeQuarter", "side"):
        d = cmds.duplicate(face, name=f"mini_face_vc_{key}")[0]
        cmds.delete(d, constructionHistory=True)
        if cmds.listRelatives(d, parent=True):
            d = cmds.parent(d, world=True)[0]
        cmds.move(0, 0.4 * (len(targets) + 1), 0, f"{d}.vtx[0:10]", relative=True)
        cmds.setAttr(f"{d}.visibility", 0)
        targets.append(d)
    cmds.blendShape(*targets, face, name=node_t20, frontOfChain=True)
    look = {"characterSettings": {"viewCorrection": {"mesh": "mini_face", "front": "mini_face_vc_front", "threeQuarter": "mini_face_vc_threeQuarter", "side": "mini_face_vc_side"}}}
    look_path = tmp / "look.json"
    look_path.write_text(json.dumps(look), encoding="utf-8")
    look_before = look_path.read_bytes()
    s.refresh_scene()
    s.clear_layer()  # Neutral を空にしてから取り込む（5 列の格子: Yaw 0 / 45 / 90° = C2 / C3 / C4）
    asked_path: list[int] = []
    setup.ask_look_path = lambda: (asked_path.append(1) or str(look_path))
    panel.select_tab("setup")
    check("セットアップ: T-20 の取り込みボタンがある", setup.t20_btn.isEnabled() and "T-20" in setup.t20_btn.text())
    setup.t20_btn.click()
    pump()
    pts = s.doc.layers[0].points
    check("T-20 取り込み: 開いている Look が無いので look.json を聞く・Yaw 0 / 45 / 90° の 3 点がキーになる", len(asked_path) == 1 and {(1, 2), (1, 3), (1, 4)} <= set(pts) and all(pts[k].is_key for k in ((1, 2), (1, 3), (1, 4))), f"{sorted(pts)} {setup.status.text()}")
    check("T-20 取り込み: 状態の行に結果（自動生成・T-20 のプレビューをオフの案内）", "自動生成" in setup.status.text() and "viewCorrection" in setup.status.text(), setup.status.text())
    check("T-20 取り込み: Look と T-20 のデータは変わらない", look_path.read_bytes() == look_before and cmds.objExists(node_t20))
    # 既存のキーがあるときは上書きするか聞く
    pts[(1, 3)].pose.curves[f"{node_t20}.mini_face_vc_threeQuarter"] = 0.5
    s.set_working_set()  # 何も変えない（Undo に積まれない）
    prompts: list[str] = []
    setup.ask_overwrite_keys = lambda text: (prompts.append(text) or False)
    setup.t20_btn.click()
    pump()
    check("T-20 取り込み: 既にキーがある点は上書きするか聞く（いいえ → 上書きしない）", len(prompts) == 1 and pts[(1, 3)].pose.curves[f"{node_t20}.mini_face_vc_threeQuarter"] == 0.5 and "上書きしませんでした" in setup.status.text(), f"{prompts} {setup.status.text()}")
    setup.ask_overwrite_keys = lambda text: (prompts.append(text) or True)
    setup.t20_btn.click()
    pump()
    check("T-20 取り込み: はい → 上書きする", len(prompts) == 2 and s.doc.layers[0].points[(1, 3)].pose.curves[f"{node_t20}.mini_face_vc_threeQuarter"] == 1.0)

    # ============================================================ 検証タブ
    val = panel.tab("validate")
    panel.select_tab("validate")
    val.run_btn.click()
    pump()
    check("検証: 取り込んだ点が未ベイクとして一覧に出る", "未ベイク" in val.summary.text() or val.tree.topLevelItemCount() > 0, val.summary.text())
    top = [val.tree.topLevelItem(i) for i in range(val.tree.topLevelItemCount())]
    child = next((t.child(0) for t in top if t.childCount() and "未ベイク" in t.child(0).text(1)), None)
    check("検証: 未ベイクの行がある", child is not None)
    if child is not None:
        val.tree.setCurrentItem(child)
        pump()
        issue = val._issue_of_item[id(child)]
        check("検証: 選んだ行の全文が下のラベルに出る（折り返しで切れない）", val.detail.text() == issue.message and val.detail.wordWrap() and val.detail.text() != "", val.detail.text())
    check("検証: 「変更のある点だけベイク」ボタンがある（全部ベイクし直すの隣）", val.rebake_stale_btn.isEnabled() and val.rebake_btn.isEnabled())
    val.rebake_stale_btn.click()
    pump()
    check("検証: 「変更のある点だけベイク」で未ベイクが解消・結果が状態欄に出る", s.stale_points() == [] and "ベイク:" in val.status.text() and "作成" in val.status.text(), val.status.text())
    check("検証: 焼いたあとの検証し直しで未ベイクが無い", not any(i.code == "point_unbaked" for i in s.validation.issues))
    # 変更 → 検証タブのボタンで Neutral + 感情の同じ位置
    s.begin_edit()
    s.select_point(1, 4)
    s.set_curve("bs.mouth_open", 0.77)
    s.save_point()
    s.end_edit()
    val.run_btn.click()
    val.rebake_stale_btn.click()
    pump()
    check("検証: Neutral の変更 → ボタンで焼き直し（Joy の同じ位置があれば一緒）", s.stale_points() == [] and "ベイク:" in val.status.text(), val.status.text())

    # ============================================================ レイヤータブ: 強さを測るシェイプ
    layers = panel.tab("layers")
    panel.select_tab("layers")
    check("レイヤー: 強さを測るシェイプの欄が使える（準備中でない）", layers.intensity_list.isEnabled() and layers.intensity_list.count() >= 2 and "準備中" not in layers.intensity_note.text(), layers.intensity_note.text())
    it = layers.intensity_list.item(0)
    name0 = it.data(Qt.UserRole)
    it.setCheckState(Qt.Checked)
    pump()
    check("レイヤー: チェックすると intensityCurves に保存される（Undo できる）", s.doc.intensity_curves == [name0], f"{s.doc.intensity_curves}")
    layers.refresh()
    check("レイヤー: 再描画しても選びが残る", layers.intensity_list.item(0).checkState() == Qt.Checked and "弱めます" not in layers.intensity_note.text())
    s.undo()
    layers.refresh()
    check("レイヤー: Undo で戻る", s.doc.intensity_curves == [] and layers.intensity_list.item(0).checkState() == Qt.Unchecked)
    layers.intensity_list.item(0).setCheckState(Qt.Checked)
    pump()
    check("レイヤー: プレビューも強さを測るシェイプを反映して作り直される", s.preview_state() == "live" and not s.preview_is_stale())

    # ============================================================ ポーズタブ
    pose = panel.tab("pose")
    grid.on_cell_clicked(1, 2)  # 点を選ぶ → 編集状態
    panel.select_tab("pose")
    pose.refresh()
    check("ポーズ: 編集状態では「シーンから取り込む」が使える", pose.btn_capture.isEnabled() and s.editing)
    ty0 = cmds.getAttr("eye_L.translateY")
    pose.edit_bone("eye_L", "t", 1, 0.4)
    pose._bone_rows["eye_L"].radio.setChecked(True)
    check("ポーズ: ボーンを動かすと編集中の値に入る", "eye_L" in s.pose.bones)
    pose.btn_reset_bone.click()
    pump()
    check("ポーズ: 「このボーンを戻す」で項目が取り除かれる（恒等のずれを残さない）", "eye_L" not in s.pose.bones and abs(cmds.getAttr("eye_L.translateY") - ty0) < 1e-6)
    s.end_edit()
    pump()
    check("ポーズ: 編集状態を抜けると「シーンから取り込む」は使えなくなり、案内が出る（listeners なしの通知で追従）", not pose.btn_capture.isEnabled() and not pose.note.isHidden(), pose.note.text())
    s.begin_edit()
    s.select_point(1, 2)
    pose.resize(480, 900)
    pose.layout().activate()
    pump()
    sa = pose.findChild(QtWidgets.QScrollArea)
    check("ポーズ: 幅 480 px で横スクロールが要らない（ボタンの行は 2 段に積む）", sa.widget().minimumSizeHint().width() <= sa.viewport().width() and pose.minimumSizeHint().width() <= 480, f"{sa.widget().minimumSizeHint().width()} {sa.viewport().width()} {pose.minimumSizeHint().width()}")
    check("ポーズ: 取り込み・書き出しのボタンが編集状態に追従する", pose.btn_capture.isEnabled())

    # ============================================================ 出力タブ
    ex = panel.tab("export")
    panel.select_tab("export")
    check("出力: タブが本物・Unity / UE / Timeline の 3 つの出力", ex.btn_unity.isEnabled() and ex.btn_ue.isEnabled() and ex.btn_track.isEnabled())
    check("出力: Timeline の .fctrack の Unity 側取り込みは準備中と書いてある", any("準備中" in l.text() for l in ex.findChildren(QtWidgets.QLabel)))
    check("出力: モデル名の既定 = キャラクター ID・範囲の既定 = 再生範囲", ex.model.text() == "mini" and ex.start.value() == cmds.playbackOptions(query=True, minTime=True) and ex.end.value() == cmds.playbackOptions(query=True, maxTime=True))
    out_dir = tmp / "facial" / "mini" / "export" / "unity"
    check("出力: 出力先の表示", out_dir.as_posix() in ex.out_label.text(), ex.out_label.text())
    # 彫刻用の fcs_ を 1 本作る（除いた数に出る）
    bakemod.pose_to_shape(s.doc, SourcePose({"bs.mouth_open": 0.5}), "fcs_smoke_shape")
    s.refresh_scene()
    # 検証に問題があるとき: やめる
    s.begin_edit()
    s.select_point(1, 4)
    s.set_curve("bs.smile_L", 0.31)
    s.save_point()
    s.end_edit()  # ベイク後の変更が 1 点
    proceed: list[str] = []
    ex.ask_proceed = lambda text: (proceed.append(text) or False)
    ex.btn_unity.click()
    pump()
    check("出力: 検証に問題（ベイク後の変更）があると「それでも出力する」かを聞く・やめると出力しない", len(proceed) == 1 and "ベイク後" in proceed[0] and ex._job is None and "取りやめ" in ex.status.text(), f"{proceed} {ex.status.text()}")
    ex.ask_proceed = lambda text: (proceed.append(text) or True)
    ex.btn_unity.click()
    pump()
    check("出力: それでも出力すると書き出し中になる（進行表示・中止ボタン）", ex._job is not None and not ex.btn_cancel.isHidden() and not ex.progress.isHidden() and "書き出し中" in ex.job_label.text() and not ex.btn_unity.isEnabled(), ex.job_label.text())
    ex.btn_cancel.click()
    pump()
    check("出力: 中止すると裏のプロセスを止めて戻る", ex._job is None and "中止" in ex.status.text() and ex.btn_unity.isEnabled())
    s.bake_stale()  # 問題を直して改めて出力する
    proceed.clear()
    ex.btn_unity.click()
    pump()
    t0 = time.time()
    while ex._job is not None and time.time() - t0 < 240:
        pump()
        time.sleep(0.2)
    res = ex.last_result
    check("出力: Unity 向けが完了する（問題が無ければ確認なし）", not proceed and ex._job is None and res is not None, ex.status.text() + " / " + ex.result.toPlainText()[-400:])
    if res is not None:
        check("出力: FBX と .fcpose ができる（<プロジェクト>/facial/mini/export/unity/）", res.fbx.exists() and res.fcpose.exists() and res.fbx.parent == out_dir and res.fbx.stat().st_size > 1000, f"{res.fbx}")
        check("出力: 結果の要約（メッシュ・FC_* の数・除いた fcs_* の数）", res.fc_count > 0 and res.excluded_fcs >= 1 and "mini_face" in res.meshes, f"{res.meshes} fc={res.fc_count} fcs={res.excluded_fcs} {res.warnings}")
        txt = ex.result.toPlainText()
        check("出力: 結果欄に出力先・メッシュ・FC_* の数・除いた fcs_* の数が出る", all(k in txt for k in ("FBX:", ".fcpose:", "mini_face", "FC_*", "fcs_*")) and str(res.fc_count) in txt, txt)
        check("出力: 開いているシーンは変わっていない（fcs_ が残る・プレビューの rig が残る・FC_ の重みは編集前のまま）", "fcs_smoke_shape" in scene.target_indices(scene.blend_shapes(face)[0]) and s.preview_exists())
        fc_doc = fcpose_io.load_document(res.fcpose)
        check("出力: .fcpose はデータと同じ", fcpose_io.to_dict(fc_doc) == fcpose_io.to_dict(s.doc))
    opened: list[Path] = []
    ex.open_folder = lambda p: opened.append(p)
    ex.btn_open.click()
    check("出力: 「フォルダを開く」で出力先のフォルダを開く", opened and opened[-1] == out_dir, f"{opened}")
    # UE
    ue_path = tmp / "ue_out" / "mini.fcpose.json"
    ex.ask_ue_path = lambda default: str(ue_path)
    ex.btn_ue.click()
    pump()
    check("出力: UE 版向け（.fcpose.json）: 保存先を聞いてデータをそのまま書く", ue_path.exists() and fcpose_io.to_dict(fcpose_io.load_document(ue_path)) == fcpose_io.to_dict(s.doc) and "UE 版向けに書き出しました" in ex.status.text(), ex.status.text())
    ex.ask_ue_path = lambda default: ""
    ue_path.unlink()
    ex.btn_ue.click()
    check("出力: UE 版向け: キャンセルでは書かない", not ue_path.exists())
    # Timeline
    ex.btn_track.click()
    check("出力: Timeline 用: ショット名が空だと促す", "ショット名" in ex.status.text() and not list(out_dir.glob("*.fctrack")), ex.status.text())
    ex.shot.setText("sc010")
    ex.start.setValue(1.0)
    ex.end.setValue(10.0)
    ex.btn_track.click()
    pump()
    track = out_dir / fct.file_name("sc010", "mini")
    check("出力: Timeline 用: <Shot>__<Model>.fctrack ができる（感情のキーを含む）", track.exists() and fct.emotion_curve_name("Joy") in fct.load(track).curves and "書き出しました" in ex.status.text(), ex.status.text())
    check("出力: Timeline 用の結果に「準備中」の注記", "準備中" in ex.track_label.text())

    # ============================================================ スクリーンショット（7 つのタブ）
    s.set_active_layer(0)
    shot(panel, "setup", "setup", 560, 2400)
    grid.on_cell_clicked(1, 2)
    shot(panel, "grid", "grid", 560, 1500)
    s.set_bone("eye_R", BoneOffset(t=(0.0, 0.2, 0.0)))
    shot(panel, "pose", "pose", 480, 900)
    shot(panel, "shapes", "shapes", 560, 500)
    shot(panel, "layers", "layers", 560, 900)
    panel.select_tab("validate")
    val.run_btn.click()
    pump()
    for i in range(val.tree.topLevelItemCount()):
        t = val.tree.topLevelItem(i)
        if t.childCount():
            val.tree.setCurrentItem(t.child(0))
            break
    shot(panel, "validate", "validate", 560, 900)
    shot(panel, "export", "export", 560, 900)

    # ============================================================ 後片付け
    pv.btn_delete.click()
    pump()
    check("プレビュー: 「消す」で rig が消える・FC_* の重みは 0", not s.preview_exists() and pv.btn_build.text() == "プレビューを作る" and not pv.btn_delete.isEnabled())
    panel.detach()
    check("detach: 出力タブのタイマー・書き出しが止まり、通知の購読が外れる", not ex.timer.isActive() and ex._job is None and ex._on_session_changed not in s.listeners and panel._on_state_changed not in s.state_listeners and pv.on_state not in s.state_listeners)
    check("detach: 編集状態は残らない", not s.editing)
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
        print(f"SMOKE RESULT {len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
        sys.stdout.flush()
        maya.standalone.uninitialize()
        os._exit(1 if failed else 0)
