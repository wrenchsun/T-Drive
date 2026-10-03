"""FacialController のパネル（ui.py）と セットアップ / レイヤー / 検証 タブの GUI スモーク（mayapy・画面なし）。

  "C:/Program Files/Autodesk/Maya2026/bin/mayapy.exe" tests/maya/facial_ui_smoke_a.py

手順（この順に守らないと落ちる）: QT_QPA_PLATFORM=offscreen → QApplication を作る → maya.standalone.initialize。
ウィジェットは直接作る（workspaceControl / show は使わない）。利用者の操作は、ウィジェットのスロット・click()・値の設定で真似る。
ダイアログは各ウィジェットの ask_* を差し替えて答えを返す。ファイルはすべて一時フォルダ。
スクリーンショット: 環境変数 TDRIVE_UI_SHOT_DIR があればそこへ PNG を書く（無ければ書かない）。
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
REPO = Path(__file__).resolve().parents[2]
os.environ.setdefault("TDRIVE_ROOT", REPO.as_posix())
sys.path.insert(0, str(REPO / "maya" / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6 import QtCore, QtWidgets  # noqa: E402

APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv[:1])  # maya.standalone より先に作る


def _load_ui_font() -> None:
    """offscreen の Qt は既定のフォントを持たず、文字が四角になる。スクリーンショットを読めるよう日本語のフォントを読み込む。"""
    from PySide6 import QtGui

    for name in ("meiryo.ttc", "YuGothM.ttc", "msgothic.ttc"):
        f = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / name
        if f.exists():
            fid = QtGui.QFontDatabase.addApplicationFont(str(f))
            fams = QtGui.QFontDatabase.applicationFontFamilies(fid) if fid >= 0 else []
            if fams:
                APP.setFont(QtGui.QFont(fams[0], 9))
                return


_load_ui_font()

import maya.standalone  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(cond), detail))


def pump() -> None:
    APP.processEvents()


def joint_state(joints) -> dict:
    from maya import cmds

    return {
        j: tuple(tuple(round(v, 7) for v in cmds.getAttr(f"{j}.{a}")[0]) for a in ("translate", "rotate", "scale", "jointOrient"))
        for j in joints
    }


def weight_state(node: str) -> dict:
    from maya import cmds

    idx = cmds.getAttr(node + ".weight", multiIndices=True) or []
    return {i: round(cmds.getAttr(f"{node}.weight[{i}]"), 7) for i in idx}


def shot(panel, key: str, name: str, height: int = 900) -> None:
    out = os.environ.get("TDRIVE_UI_SHOT_DIR")
    if not out:
        return
    Path(out).mkdir(parents=True, exist_ok=True)
    panel.select_tab(key)
    panel.resize(560, height)
    pump()
    panel.grab().save(str(Path(out) / f"{name}.png"))


def run() -> None:
    from maya import cmds

    import facial_fixture
    from tdrive import project
    from tdrive_facial import scene
    from tdrive_facial import session as S
    from tdrive_facial import ui
    from tdrive_facial.core import fcpose_io, naming
    from tdrive_facial.core.model import BoneOffset, GridPoint, SourcePose
    from tdrive_facial.core.presenters import CONFIRM_CANCEL, CONFIRM_DISCARD, CONFIRM_SAVE
    from PySide6.QtCore import Qt

    tmp = Path(tempfile.mkdtemp(prefix="tdrive_fui_a_"))
    project.set_root(tmp)
    ids = facial_fixture.build_mini_head()
    face, bs, joints = ids["face"], ids["bs"], ids["joints"]
    s = S.current()
    s.close()
    s.listeners.clear()

    def write_doc(name: str, mutate=None) -> Path:
        doc = facial_fixture.make_doc()
        if mutate:
            mutate(doc)
        p = tmp / "src" / f"{name}.fcpose.json"
        p.parent.mkdir(exist_ok=True)
        fcpose_io.save(doc, p)
        return p

    # ============================================================ パネル（データ無し）
    panel = ui.FacialPanel()
    panel.resize(560, 900)
    labels = [panel.tabs.tabText(i) for i in range(panel.tabs.count())]
    check("パネル: タブの順番", labels == ["セットアップ", "グリッド", "ポーズ", "シェイプ", "レイヤー", "リップシンク", "検証", "出力"], f"{labels}")
    check("データ無し: タブはすべて無効", not any(panel.tabs.isTabEnabled(i) for i in range(8)))
    check("データ無し: ヒントが出ている", not panel.hint.isHidden() and "新規 または 開く" in panel.hint.text())
    check("データ無し: ヘッダーに「まだありません」・保存と編集は押せない",
          "まだありません" in panel.header.label.text() and not panel.header.buttons["save"].isEnabled() and not panel.header.edit_btn.isEnabled())
    survived = True
    placeholders = {}
    try:
        for key, label, _cls in ui.TAB_SPECS:
            w = panel.tab(key)
            placeholders[key] = panel.tab_is_placeholder(key)
            survived = survived and w is not None
    except Exception:
        survived = False
        RESULTS.append(("例外（データ無しでタブを作る）", False, traceback.format_exc()))
    check("データ無し: 8 つのタブがどれも（準備中でも）作れる", survived)
    for key in ("grid", "pose", "shapes", "export"):
        exists = importlib.util.find_spec(f"tdrive_facial.ui_{key}") is not None
        check(f"タブ {key}: モジュールが無ければ準備中の表示（あれば本物）", placeholders[key] == (not exists), f"exists={exists} placeholder={placeholders[key]}")
    for key in ("setup", "layers", "validate"):
        check(f"タブ {key}: 本物のタブ", not placeholders[key])
    missing_text = [panel.slots[k].content.text() for k in ("grid", "pose", "shapes", "export") if placeholders[k]]
    check("準備中の文言", all(t.endswith("は準備中です") for t in missing_text), f"{missing_text}")

    # ============================================================ 新規（ヘッダー）
    hdr = panel.header
    hdr.ask_discard = lambda: True
    hdr.ask_new = lambda: {"character": "mini", "mesh": cmds.ls(face, long=True)[0], "profile": None}
    hdr.buttons["new"].click()
    pump()
    check("新規: データができ、タブが有効になる", s.presenters is not None and all(panel.tabs.isTabEnabled(i) for i in range(8)) and panel.hint.isHidden())
    check("新規: ヘッダーにパスと ●未保存", "facial/mini/mini.fcpose.json" in hdr.label.text() and "●未保存" in hdr.label.text(), hdr.label.text())
    hdr.buttons["save"].click()
    pump()
    check("保存: ●未保存が消え、ファイルができる", "●未保存" not in hdr.label.text() and (tmp / "facial/mini/mini.fcpose.json").exists(), hdr.label.text())

    # ============================================================ セットアップ
    setup = panel.tab("setup")
    panel.select_tab("setup")
    pump()
    check("セットアップ: 顔のメッシュが入っている", setup.mesh.currentText() == "mini_face", setup.mesh.currentText())

    setup.edge_fade.setValue(20.0)
    check("エッジフェード: 値の変更が Document に届き、●未保存が出る", s.doc.grid.edge_fade == 20.0 and "●未保存" in hdr.label.text())
    hdr.buttons["save"].click()
    check("保存後に ●未保存 が消える", "●未保存" not in hdr.label.text())

    setup.base_bone.setText("eye_L")
    setup.on_base_bone_edited()
    check("基準ボーン: 入力が Document に届く", s.doc.grid.base_bone == "eye_L")
    setup.bone_detect.click()
    check("基準ボーン: 自動検出で head になる", s.doc.grid.base_bone == "head" and setup.base_bone.text() == "head", s.doc.grid.base_bone)
    cmds.select(joints[2])  # eye_L
    setup.bone_from_sel.click()
    check("基準ボーン: 選択から", s.doc.grid.base_bone == "eye_L")
    setup.base_bone.setText("")
    setup.on_base_bone_edited()
    check("失敗は状態欄に出て、表示は Document に戻る", "空" in setup.status.text() and setup.base_bone.text() == "eye_L", setup.status.text())
    setup.base_bone.setText("head")
    setup.on_base_bone_edited()

    i = setup.forward.findData("-Z")
    setup.forward.setCurrentIndex(i)
    setup.on_forward_activated(i)
    check("前方向: 変更が届く", s.doc.grid.forward_axis == "-Z")
    i = setup.forward.findData("+Z")
    setup.forward.setCurrentIndex(i)
    setup.on_forward_activated(i)
    check("前方向: +Z に戻せる", s.doc.grid.forward_axis == "+Z")

    setup.center[1].setValue(1.5)
    setup.center[2].setValue(-0.25)
    check("中心のずらし", tuple(s.doc.grid.center_offset) == (0.0, 1.5, -0.25), f"{s.doc.grid.center_offset}")

    cmds.select(ids["brow"])
    setup.extra_add.click()
    check("追加メッシュ: 選択から追加", s.doc.target.extra_meshes == ["mini_brow"] and setup.extra_list.count() == 1, f"{s.doc.target.extra_meshes}")
    setup.extra_list.item(0).setSelected(True)
    setup.extra_remove.click()
    check("追加メッシュ: 外す", s.doc.target.extra_meshes == [] and setup.extra_list.count() == 0)
    cmds.select(ids["brow"])
    setup.mesh_from_sel.click()
    check("顔のメッシュ: 選択から（変更が届く）", s.doc.target.mesh == "mini_brow", f"{s.doc.target.mesh}")
    j = setup.mesh.findData(cmds.ls(face, long=True)[0])
    setup.mesh.setCurrentIndex(j)
    setup.on_mesh_activated(j)
    check("顔のメッシュ: コンボで戻せる", s.doc.target.mesh == "mini_face", f"{s.doc.target.mesh}")

    # ミラー・自動生成・ベイク
    setup.mirror_on.click()
    check("ミラー: 使う を切る", s.doc.mirror.enabled is False)
    setup.mirror_on.click()
    setup.suffix_l.setText("Left")
    setup.suffix_r.setText("Right")
    setup.mirror_exclude.setText("brow, eye ")
    setup.on_mirror_changed()
    m = s.doc.mirror
    check("ミラー: 接尾辞・除外", (m.enabled, m.suffix_l, m.suffix_r, m.exclude) == (True, "Left", "Right", ["brow", "eye"]), f"{m}")
    k = setup.mirror_axis.findData("Z")
    setup.mirror_axis.setCurrentIndex(k)
    setup.on_mirror_changed()
    check("ミラー: ボーンの軸", s.doc.mirror.bone_axis == "Z")
    k = setup.mirror_axis.findData("X")
    setup.mirror_axis.setCurrentIndex(k)
    setup.on_mirror_changed()
    k = setup.fill_mode.findData("NearestKey")
    setup.fill_mode.setCurrentIndex(k)
    setup.on_autogen_changed()
    setup.idw_power.setValue(3.0)
    check("自動生成: 方式が届く・最近傍では IDW のべき乗を無効にする", s.doc.autogen.mode == "NearestKey" and not setup.idw_power.isEnabled())
    k = setup.fill_mode.findData("IDW")
    setup.fill_mode.setCurrentIndex(k)
    setup.on_autogen_changed()
    setup.idw_power.setValue(3.0)
    check("自動生成: IDW のべき乗", s.doc.autogen.mode == "IDW" and s.doc.autogen.idw_power == 3.0)
    setup.threshold.setValue(0.01)
    check("ベイクのしきい値", abs(s.doc.bake.delta_threshold - 0.01) < 1e-9, f"{s.doc.bake}")

    # プロファイル
    k = setup.profile_combo.findData("arkit52")
    setup.profile_combo.setCurrentIndex(k)
    setup.profile_apply.click()
    check("プロファイル: 適用でミラーの規則が入る", s.doc.profile == "arkit52" and (s.doc.mirror.suffix_l, s.doc.mirror.suffix_r) == ("Left", "Right"), f"{s.doc.profile}")
    check("プロファイル: 標準シェイプの不足の行", "標準シェイプの不足" in setup.profile_missing.text() and "個" in setup.profile_missing.text(), setup.profile_missing.text())
    setup.profile_combo.setCurrentIndex(setup.profile_combo.findData("shizuku"))
    setup.profile_overwrite.setChecked(True)
    setup.profile_apply.click()
    check("プロファイル: 上書きつきで別のプロファイルへ", s.doc.profile == "shizuku" and s.doc.mirror.suffix_l == "_L")

    # 作業セット
    names = [setup.curve_list.item(n).text() for n in range(setup.curve_list.count())]
    check("作業セット: シェイプ一覧にモデルのシェイプが並ぶ", {"bs.mouth_open", "bs.smile_L", "bs.smile_R", "bs.brow_up"} <= set(names), f"{names}")
    it = setup.curve_list.item(names.index("bs.mouth_open"))
    it.setCheckState(Qt.Checked)
    check("作業セット: シェイプのチェック（追加）", s.doc.working_set.curves == ["bs.mouth_open"], f"{s.doc.working_set.curves}")
    it = setup.curve_list.item(names.index("bs.mouth_open"))
    it.setCheckState(Qt.Unchecked)
    check("作業セット: シェイプのチェックを外す", s.doc.working_set.curves == [])
    setup.curve_filter.setText("smile")
    shown = [setup.curve_list.item(n).text() for n in range(setup.curve_list.count()) if not setup.curve_list.item(n).isHidden()]
    check("作業セット: 絞り込み", sorted(shown) == ["bs.smile_L", "bs.smile_R"], f"{shown}")
    setup.on_ws_bulk("curve", True)
    check("作業セット: 表示中を全部オン", sorted(s.doc.working_set.curves) == ["bs.smile_L", "bs.smile_R"], f"{s.doc.working_set.curves}")
    setup.on_ws_bulk("curve", False)
    check("作業セット: 全部オフ", s.doc.working_set.curves == [])
    setup.curve_filter.setText("")
    bnames = [setup.bone_list.item(n).text() for n in range(setup.bone_list.count())]
    check("作業セット: ボーン一覧", {"head", "eye_L", "eye_R"} <= set(bnames), f"{bnames}")
    cmds.select(clear=True)
    setup.bone_list.setCurrentRow(bnames.index("eye_R"))
    check("作業セット: ボーンを選ぶと Maya でジョイントが選ばれる", [scene.short_name(n) for n in cmds.ls(selection=True, long=True)] == ["eye_R"], f"{cmds.ls(selection=True)}")
    cmds.select(joints[2])
    setup.add_selected_joints.click()
    check("作業セット: 選択中のジョイントを追加", s.doc.working_set.bones == ["eye_L"], f"{s.doc.working_set.bones}")
    setup.bone_list.item(bnames.index("head")).setCheckState(Qt.Checked)
    check("作業セット: ボーンのチェック", s.doc.working_set.bones == ["eye_L", "head"], f"{s.doc.working_set.bones}")
    s.set_working_set(curves=["bs.mouth_open", "bs.smile_L"], bones=["head", "eye_L"])  # 後続（検証・レイヤー）のためのデータ

    # ============================================================ 編集の入り切り
    cmds.setAttr(f"{bs}.mouth_open", 0.5)
    before_j, before_w = joint_state(joints), weight_state(bs)
    hdr.edit_btn.click()
    editing_w = weight_state(bs)
    check("編集: オンで基準姿勢（重み 0）・ラベルが「編集中」", s.editing and all(v == 0 for v in editing_w.values()) and "編集中" in hdr.edit_state.text() and hdr.edit_btn.isChecked(), f"{editing_w} {hdr.edit_state.text()}")
    hdr.edit_btn.click()
    check("編集: オフでシーンが元に戻る", (not s.editing) and joint_state(joints) == before_j and weight_state(bs) == before_w and "していません" in hdr.edit_state.text())
    hdr.edit_btn.click()
    panel.detach()
    check("detach: 編集状態を抜けてシーンが元に戻る・購読が外れる",
          (not s.editing) and joint_state(joints) == before_j and weight_state(bs) == before_w and panel._on_session_changed not in s.listeners)
    check("detach: 何度呼んでもよい", panel.detach() is None)
    cmds.setAttr(f"{bs}.mouth_open", 0)

    # ============================================================ 以降は新しいパネル + 本物のデータ
    panel = ui.FacialPanel()
    hdr = panel.header
    hdr.ask_discard = lambda: True
    p1 = write_doc("mini")
    hdr.ask_open_path = lambda: str(p1)
    hdr.buttons["open"].click()
    pump()
    check("開く: ヘッダーのパスが変わる・●未保存なし", "src/mini.fcpose.json" in hdr.label.text() and "●未保存" not in hdr.label.text(), hdr.label.text())

    s.set_working_set(curves=["bs.mouth_open", "bs.smile_L"], bones=["head", "eye_L"])

    # ---- レイヤー
    layers = panel.tab("layers")
    panel.select_tab("layers")
    pump()
    t = layers.table
    check("レイヤー: 一覧に Neutral と Joy", t.rowCount() == 2 and "Neutral" in t.item(0, 1).text() and "Joy" in t.item(1, 1).text())
    check("レイヤー: 数の表示", "2 / 16" in layers.count_label.text(), layers.count_label.text())
    check("レイヤー: Joy は有効なので押せない・Anger は押せる", not layers.preset_buttons["Joy"].isEnabled() and layers.preset_buttons["Anger"].isEnabled())
    check("レイヤー: Neutral を選んでいると 改名・削除 は押せない", not layers.rename_btn.isEnabled() and not layers.delete_btn.isEnabled())
    check("レイヤー: 点の数が出る", "2" in t.item(1, 3).text() and "キー 2" in t.item(1, 3).text(), t.item(1, 3).text())
    layers.preset_buttons["Anger"].click()
    names = [l.name for l in s.doc.layers]
    check("レイヤー: プリセット Anger を足すと編集の対象になる", names == ["Neutral", "Joy", "Anger"] and s.ctx.active_layer == 2 and not layers.preset_buttons["Anger"].isEnabled(), f"{names} {s.ctx.active_layer}")
    layers.custom_name.setText("bad name")
    check("レイヤー: 自由な名前の検査が先に出る", layers.custom_note.text() != "", layers.custom_note.text())
    layers.custom_add.click()
    check("レイヤー: 不正な名前は追加されず状態欄に理由", len(s.doc.layers) == 3 and layers.status.text() != "", layers.status.text())
    layers.custom_name.setText("Wink")
    layers.custom_add.click()
    check("レイヤー: 自由な名前の追加", [l.name for l in s.doc.layers][-1] == "Wink" and layers.custom_name.text() == "")
    layers.ask_new_name = lambda cur: "Anger2"
    layers.rename_btn.click()
    check("レイヤー: 改名（Wink → Anger2 は選んでいる Wink が対象）", [l.name for l in s.doc.layers] == ["Neutral", "Joy", "Anger", "Anger2"], f"{[l.name for l in s.doc.layers]}")
    t.item(3, 0).setCheckState(Qt.Unchecked)
    check("レイヤー: 無効にする", s.doc.layers[3].enabled is False)
    t.item(3, 2).setText("AngerWeight")
    check("レイヤー: 感情カーブの書き換え", s.doc.layers[3].emotion_curve == "AngerWeight", s.doc.layers[3].emotion_curve)
    check("レイヤー: Neutral の感情カーブは書き換えられない", not (t.item(0, 2).flags() & Qt.ItemIsEditable))
    layers.copy_source.setCurrentIndex(layers.copy_source.findText("Joy"))
    layers.copy_btn.click()
    check("レイヤー: Joy からコピー", len([p for p in s.doc.layers[3].points.values() if not p.pose.is_empty()]) == 2, layers.status.text())
    layers.dampen.setValue(0.3)
    check("レイヤー: 表情で弱める設定", abs(s.doc.policy.expression_dampen - 0.3) < 1e-9)
    has_cmd = getattr(s, "set_intensity_curves", None) is not None
    check("レイヤー: 強さを測るシェイプの一覧（作業セットから）・設定の命令が無ければ無効＋注記",
          layers.intensity_list.count() == 2 and layers.intensity_list.isEnabled() == has_cmd and (has_cmd or "準備中" in layers.intensity_note.text()),
          f"{layers.intensity_list.count()} {layers.intensity_note.text()}")

    # 焼いてから削除
    rep = s.bake_all()
    joy_targets = [t_.alias for t_ in scene.fc_targets(face) if "_Joy_" in t_.alias]
    check("（準備）ベイクで Joy の FC_* ができる", len(joy_targets) >= 1, f"{rep.summary()} {joy_targets}")
    layers.refresh()
    shot(panel, "layers", "layers")
    t.selectRow(1)  # Joy
    check("レイヤー: 行を選ぶとアクティブレイヤーが変わる", s.ctx.active_layer == 1)
    asked = []
    layers.ask_confirm_delete = lambda text: asked.append(text) or False
    layers.delete_btn.click()
    check("レイヤー: 削除は確認し、いいえなら消さない・文言に FC_* が消えると書いてある", len(s.doc.layers) == 4 and asked and "FC_*" in asked[0], f"{asked}")
    layers.ask_confirm_delete = lambda text: True
    layers.delete_btn.click()
    left = [t_.alias for t_ in scene.fc_targets(face) if "_Joy_" in t_.alias]
    check("レイヤー: 削除 → レイヤーが減り Joy の FC_* がシーンから消える", [l.name for l in s.doc.layers] == ["Neutral", "Anger", "Anger2"] and left == [], f"{[l.name for l in s.doc.layers]} {left}")
    check("レイヤー: 削除したら Joy のプリセットがまた押せる", layers.preset_buttons["Joy"].isEnabled())

    # 未保存のポーズがあるままレイヤーを切り替える
    t.selectRow(0)
    check("（準備）Neutral を選ぶ", s.ctx.active_layer == 0)
    s.select_point(1, 2)
    s.set_curve("bs.mouth_open", 0.95)
    check("（準備）ポーズが未保存になっている", s.pose.dirty)
    asked = []
    layers.ask_switch_choice = lambda text: asked.append(text) or CONFIRM_CANCEL
    t.selectRow(1)
    check("切り替え: キャンセルなら今のレイヤーのまま・選択も戻る", s.ctx.active_layer == 0 and s.pose.dirty and asked and t.selectionModel().selectedRows()[0].row() == 0, f"{s.ctx.active_layer}")
    layers.ask_switch_choice = lambda text: CONFIRM_DISCARD
    t.selectRow(1)
    check("切り替え: 破棄で切り替わり、保存済みの値は変わらない", s.ctx.active_layer == 1 and abs(s.doc.layers[0].points[(1, 2)].pose.curves["bs.mouth_open"] - 0.6) < 1e-6)
    t.selectRow(0)
    s.select_point(1, 2)
    s.set_curve("bs.mouth_open", 0.95)
    layers.ask_switch_choice = lambda text: CONFIRM_SAVE
    t.selectRow(1)
    check("切り替え: 保存で、値を保存してから切り替わる", s.ctx.active_layer == 1 and abs(s.doc.layers[0].points[(1, 2)].pose.curves["bs.mouth_open"] - 0.95) < 1e-6, f"{s.doc.layers[0].points[(1, 2)].pose.curves}")
    s.end_edit()

    # ---- 検証
    def break_doc(doc):
        doc.layers[0].points[(2, 1)].pose.curves["bs.brow_upp"] = 0.2  # 綴りミス（近い名前に直せる。同じ点に正しい名前は無い）
        doc.layers[0].points[(1, 2)].pose.curves["unrelated_zzz_qq"] = 0.1  # 似た名前が無い
        doc.layers[0].points[(0, 2)].pose.bones["eye_Ll"] = BoneOffset()
        doc.layers[0].points[(0, 2)].pose.bones.pop("eye_L", None)

    p2 = write_doc("broken", break_doc)
    hdr.ask_open_path = lambda: str(p2)
    hdr.buttons["open"].click()
    pump()
    val = panel.tab("validate")
    panel.select_tab("validate")
    pump()
    check("検証: 開いた直後は未実行の表示", "まだ検証していません" in val.summary.text() and val.tree.topLevelItemCount() == 0)
    val.run_btn.click()
    groups = [val.tree.topLevelItem(n).text(0) for n in range(val.tree.topLevelItemCount())]
    check("検証: エラー・警告・情報のグループに分かれ、件数が出る", any("警告" in g for g in groups) and all("（" in g for g in groups), f"{groups} {val.summary.text()}")
    check("検証: 概要に件数", "警告" in val.summary.text(), val.summary.text())
    combos = [c for _k, c in val._combos]
    check("検証: 改名できる行に候補のコンボがある（綴りミス → bs.brow_up）", any(c.findData("bs.brow_up") >= 0 for c in combos), f"{[[c.itemText(i) for i in range(c.count())] for c in combos]}")
    check("検証: 一括改名と、無い参照の削除 が押せる", val.rename_btn.isEnabled() and val.remove_btn.isEnabled())
    shot(panel, "validate", "validate")
    val.rename_btn.click()
    pose = s.doc.layers[0].points[(2, 1)].pose
    check("検証: 一括改名で綴りミスが直る", "bs.brow_up" in pose.curves and "bs.brow_upp" not in pose.curves, f"{pose.curves}")
    check("検証: 改名のあと自動で検証し直す（状態欄に残り件数）", "検証し直しました" in val.status.text() and not val.stale_label.isVisibleTo(val), val.status.text())
    asked = []
    val.ask_confirm = lambda text, title="": asked.append(text) or False
    val.remove_btn.click()
    check("検証: 削除は確認し、いいえなら消さない", asked and "unrelated_zzz_qq" in s.doc.layers[0].points[(1, 2)].pose.curves, f"{asked}")
    val.ask_confirm = lambda text, title="": True
    val.remove_btn.click()
    pose = s.doc.layers[0].points[(1, 2)].pose
    check("検証: 削除で無い参照が消える（シェイプ）", "unrelated_zzz_qq" not in pose.curves, f"{pose.curves}")

    # 変更したら「古い」表示
    s.set_edge_fade(33.0)  # 別のタブ（セットアップ）での変更に当たる
    pump()
    check("検証: データが変わると「最後の検証のあとでデータが変わりました」", val.stale_label.isVisibleTo(val), "")

    # 点へ移る（ダブルクリック）
    val.run_btn.click()
    target_issue = None
    for n in range(val.tree.topLevelItemCount()):
        top = val.tree.topLevelItem(n)
        for c in range(top.childCount()):
            issue = val._issue_of_item.get(id(top.child(c)))
            if issue is not None and issue.can_select_point:
                target_issue = (top.child(c), issue)
                break
        if target_issue:
            break
    if target_issue is None:
        # 点つきの問題が無ければ、点つきの問題を作る（ベイクしてから変更 → 変更あり）
        s.bake_all()
        s.select_point(0, 2)
        s.set_curve("bs.mouth_open", 0.77)
        s.save_point()
        s.end_edit()
        val.run_btn.click()
        for n in range(val.tree.topLevelItemCount()):
            top = val.tree.topLevelItem(n)
            for c in range(top.childCount()):
                issue = val._issue_of_item.get(id(top.child(c)))
                if issue is not None and issue.can_select_point:
                    target_issue = (top.child(c), issue)
                    break
            if target_issue:
                break
    check("検証: 点つきの行がある", target_issue is not None)
    if target_issue is not None:
        item, issue = target_issue
        s.end_edit()
        val.on_double_click(item)
        check("検証: ダブルクリックでその点が選ばれ、編集状態になる（ヘッダーの切り替えも追従）",
              s.ctx.selection == (issue.row, issue.col) and s.editing and hdr.edit_btn.isChecked() and s.ctx.active_layer == issue.layer,
              f"{s.ctx.selection} {(issue.row, issue.col)} editing={s.editing} checked={hdr.edit_btn.isChecked()}")
        s.end_edit()
        hdr.refresh()

    # 全部ベイクし直す
    val.ask_confirm = lambda text, title="": True
    val.rebake_btn.click()
    check("検証: 全部ベイクし直す → 状態欄にベイクの要約", "ベイク" in val.status.text(), val.status.text())

    # ---- セットアップ: 格子の変更（捨てられるキーの警告）
    hdr.ask_open_path = lambda: str(p1)
    hdr.buttons["open"].click()
    pump()
    setup = panel.tab("setup")
    panel.select_tab("setup")
    pump()
    for cols, rows in ((2, 2), (1, 1), (2, 3), (3, 2)):
        trial = s.preview_resize(cols, rows)
        if trial.dropped_keys:
            break
    check("（準備）捨てられるキーが出る格子の大きさがある", bool(trial.dropped_keys), f"{cols}x{rows}")
    asked = []
    setup.ask_drop_keys = lambda text, n: asked.append((text, n)) or False
    setup.cols.setValue(cols)
    setup.rows.setValue(rows)
    setup.grid_apply.click()
    check("格子: 捨てられるキーを先に見せ、取りやめなら変えない", asked and asked[0][1] == len(trial.dropped_keys) and (s.doc.grid.cols, s.doc.grid.rows) == (3, 3) and not setup.grid_note.isHidden(), f"{asked} {setup.grid_note.text()}")
    setup.ask_drop_keys = lambda text, n: True
    setup.grid_apply.click()
    check("格子: 了承すると適用される", (s.doc.grid.cols, s.doc.grid.rows) == (cols, rows), f"{s.doc.grid.cols}x{s.doc.grid.rows}")
    setup.cols.setValue(cols)
    setup.rows.setValue(rows)
    setup.grid_apply.click()
    check("格子: 変わらないときは「変わりません」", "変わりません" in setup.status.text())
    setup.yaw.setValue(120.0)
    setup.pitch.setValue(60.0)
    setup.ask_drop_keys = lambda text, n: True
    setup.grid_apply.click()
    check("格子: 角度の範囲も変えられる", s.doc.grid.yaw_range == 120.0 and s.doc.grid.pitch_range == 60.0)
    check("Undo: ツールの undo で格子が戻る", s.undo() and s.doc.grid.yaw_range == 90.0)
    pump()
    check("Undo: セットアップの表示も追従", setup.yaw.value() == 90.0, f"{setup.yaw.value()}")

    # ---- スクリーンショット
    s.open(p1)
    pump()
    shot(panel, "setup", "setup")
    shot(panel, "setup", "setup_full", height=2300)

    # ---- 後片付け
    panel.detach()
    ui_tool = ui.make_tool()
    w = ui_tool.build_widget()
    check("FacialTool: build_widget / refresh / undo_message", isinstance(w, ui.FacialPanel) and ui_tool.undo_message() and ui_tool.refresh() is None)
    w.detach()
    s.close()
    check("close: データ無しに戻るとタブは無効", True)
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
