"""カメラの解決（viewport.py）・顔以外を隠す（hide_others.py）・シェイプの分類のタブ（ui_pose / ui_setup）のスモークテスト（mayapy・画面なし）。

  "C:/Program Files/Autodesk/Maya2026/bin/mayapy.exe" tests/maya/facial_view_hide_cat_smoke.py [セクション名の一部...]

環境変数 TDRIVE_SHOT_DIR があれば、画像をそこへ書く（pose_tabs.png / grid_hide.png / hide_dialog.png / setup_categories.png）。
"""

from __future__ import annotations

import math
import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
os.environ.setdefault("TDRIVE_ROOT", REPO.as_posix())
sys.path.insert(0, str(REPO / "maya" / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6 import QtWidgets  # noqa: E402

APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv[:1])  # maya.standalone より先に作る



def _style() -> None:
    """日本語のフォントと Maya 風の暗い配色（画像を撮るため）。"""
    from PySide6 import QtGui

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
TOL = 1e-3


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(cond), detail))


def fresh(tmp: Path):
    """合成の頭 + 開いたデータ（mini）。"""
    import facial_fixture
    from tdrive import project
    from tdrive_facial import session as S
    from tdrive_facial.core import fcpose_io

    project.set_root(tmp)
    ids = facial_fixture.build_mini_head()
    s = S.current()
    s.close()
    sg = s.scene_grid  # セッションは使い回し: 設定を既定へ戻す
    sg.remove()
    sg.enabled, sg.scale, sg.pickable, sg.on_pick = False, 2.5, True, None
    s.hide_others.remove()
    s.hide_others.enabled, s.hide_others.keep = False, set()
    s.pose_category = s.setup_category = "_all"
    doc0 = facial_fixture.make_doc()
    p = tmp / "facial" / "mini" / "mini.fcpose.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    fcpose_io.save(doc0, p)
    s.open(p)
    return ids, s




def pos(node: str) -> tuple[float, float, float]:
    from maya import cmds

    return tuple(cmds.xform(node, query=True, worldSpace=True, translation=True))  # type: ignore[return-value]


def shot(widget, name: str) -> None:
    d = os.environ.get("TDRIVE_SHOT_DIR")
    if d:
        Path(d).mkdir(parents=True, exist_ok=True)
        APP.processEvents()
        widget.grab().save(str(Path(d) / name))


# ---------------------------------------------------------------------------
# 項目 1: カメラの解決
# ---------------------------------------------------------------------------


class _FakePanels:
    """viewport.cmds の差し替え: getPanel / modelPanel だけ偽物（mayapy にはビューポートが無い）。他は本物の cmds。"""

    def __init__(self, real, focus: str, visible: list[str], cams: dict[str, str]):
        self._real, self.focus, self.visible, self.cams = real, focus, visible, cams

    def getPanel(self, **kw):  # noqa: N802
        if kw.get("withFocus"):
            return self.focus
        if kw.get("typeOf"):
            return "modelPanel" if kw["typeOf"] in self.cams else "scriptedPanel"
        if kw.get("type") == "modelPanel":
            return list(self.cams)
        if kw.get("visiblePanels"):
            return list(self.visible)
        return []

    def modelPanel(self, panel, **kw):  # noqa: N802
        if kw.get("exists"):
            return panel in self.cams
        if kw.get("query") and kw.get("camera"):
            return self.cams[panel]
        raise RuntimeError("unsupported")

    def __getattr__(self, name):
        return getattr(self._real, name)


def sec_01_camera_resolution(tmp: Path) -> None:
    """シェイプエディタなど、モデルパネルでないパネルにフォーカスがあっても、見えているビューのカメラを動かす。"""
    from maya import cmds

    from tdrive_facial import ui_grid
    from tdrive_facial import viewport as VP

    ids, s = fresh(tmp)
    top = cmds.camera(name="topCam", orthographic=True)[0]
    cmds.move(0, 100, 0, top)
    side = cmds.camera(name="sideCam")[0]
    cmds.move(100, 12, 0, side)
    cmds.rotate(0, 90, 0, side)
    persp_pos0, top_pos0, side_pos0 = pos("persp"), pos(top), pos(side)
    real = VP.cmds
    # Maya の既定の並び: 隠れた modelPanel1 = top（正投影）が先頭、見えているのは modelPanel4 = persp
    fake = _FakePanels(real, focus="scriptedPanel1", visible=["modelPanel4", "scriptedPanel1"], cams={"modelPanel1": top, "modelPanel2": side, "modelPanel4": "persp"})
    VP.cmds = fake
    try:
        VP.forget()
        check("フォーカスがモデルパネルでないとき、見えているパネルのカメラ（persp）を選ぶ（以前は先頭の top）", s.camera_transform().split("|")[-1] == "persp", s.camera_transform())
        check("読み出し用の名前が persp", VP.last_camera_name() == "persp")
        s.select_point(1, 2, move_camera=True)
        check("点のクリック（カメラも動かす）: 見えているビューの persp が動く", math.dist(pos("persp"), persp_pos0) > 1.0)
        check("点のクリック: 隠れた top・side のカメラは動かない", math.dist(pos(top), top_pos0) < 1e-6 and math.dist(pos(side), side_pos0) < 1e-6)
        yaw, pitch = s.view_angles()
        ya, pa = s.grid.angles_of(1, 2)
        check("点のクリック: persp の角度がその点の角度", abs(yaw - ya) < 0.2 and abs(pitch - pa) < 0.2, f"{yaw} {pitch} / {ya} {pa}")
        # 赤い点のドラッグ（グリッドタブ）
        tab = ui_grid.GridTab(s)
        tab.resize(480, 900)
        tab.show()
        APP.processEvents()
        tab.drag_begin()
        tab.drag_move(30.0, 10.0)
        tab.drag_end()
        a = s.view_angles()
        check("赤い点のドラッグ: persp が動く（隠れた top は動かない）", abs(a[0] - 30.0) < 0.2 and abs(a[1] - 10.0) < 0.2 and math.dist(pos(top), top_pos0) < 1e-6, f"{a}")
        tab._poll_camera(force=True)
        check("グリッドタブの読み出しにカメラの名前（persp）が出る", "persp" in tab.camera_label.text() and "Yaw 30.0°" in tab.camera_label.text(), tab.camera_label.text())
        # フォーカスがモデルパネルに移ったら、そのパネルのカメラ → そのあとアウトライナへ移っても覚えている
        fake.visible = ["modelPanel2", "modelPanel4"]
        fake.focus = "modelPanel2"
        cam = s.camera_transform().split("|")[-1]
        fake.focus = "outlinerPanel1"
        check("モデルパネルにフォーカスがあるとそのカメラ・外れても最後のパネルを覚えている", cam == side and s.camera_transform().split("|")[-1] == side, cam)
        tab._poll_camera(force=True)
        check("カメラを切り替えると読み出しの名前も変わる", side in tab.camera_label.text(), tab.camera_label.text())
        # 最後のパネルが見えなくなったら、見えているパネルへ
        fake.visible = ["modelPanel4"]
        check("最後のパネルが隠れたら見えているパネルへ", s.camera_transform().split("|")[-1] == "persp")
        from tdrive_facial import thumbnails

        check("サムネイルのモデルパネルも同じ決め方", thumbnails._model_panel() == "modelPanel4")
        s.scene_grid.set_enabled(True)
        s.scene_grid.follow(s.view_angles())
        check("シーンの格子の赤い印が出る（同じカメラの向き）", cmds.objExists("tdFacialGrid_GRP|tdFacialGrid_view"))
        s.scene_grid.set_enabled(False)
        tab.detach()
        tab.deleteLater()
        VP.redraw(force=True)
        check("redraw は batch で何もせず例外を出さない", True)
    finally:
        VP.cmds = real
        VP.forget()
    s.close()


# ---------------------------------------------------------------------------
# 項目 2: 顔以外を隠す
# ---------------------------------------------------------------------------


def ov(node: str) -> tuple:
    from maya import cmds

    return (cmds.getAttr(node + ".overrideEnabled"), cmds.getAttr(node + ".overrideVisibility"), cmds.getAttr(node + ".overrideDisplayType"), cmds.getAttr(node + ".visibility"))


def sec_02_hide_others(tmp: Path) -> None:
    from maya import cmds

    import facial_fixture
    from tdrive_facial import scene as scene_mod
    from tdrive_facial import session as S

    ids, s = fresh(tmp)
    facial_fixture.add_body(garbage_pose=False)
    h = s.hide_others
    s.refresh_scene()
    # 元から使っている override（リファレンス表示）を持つメッシュ: 戻すとき、そのままでなければならない
    cmds.setAttr("mini_brow.overrideEnabled", 1)
    cmds.setAttr("mini_brow.overrideDisplayType", 2)
    before = {n: ov(n) for n in ("mini_face", "mini_brow", "mini_body", "bs_target_mouth_open")}
    cmds.file(modified=False)
    undo_before = (cmds.undoInfo(query=True, undoName=True), cmds.undoInfo(query=True, redoName=True))
    anim_before = set(cmds.ls(type="animCurve") or [])
    s.scene_grid.set_enabled(True)
    cmds.file(modified=False)
    h.set_enabled(True)
    cands = h.candidates()
    check("候補: 顔以外の表示中のメッシュ（mini_brow・mini_body）。顔・非表示のターゲットは入らない", sorted(c[0] for c in cands) == ["mini_body", "mini_brow"], str(cands))
    check("隠す: mini_brow・mini_body が overrideVisibility = 0（overrideEnabled = 1）", ov("mini_brow")[:2] == (1, False) and ov("mini_body")[:2] == (1, False))
    check("隠す: visibility は触らない", ov("mini_brow")[3] and ov("mini_body")[3])
    check("隠す: 顔のメッシュ・非表示のターゲットは触らない", ov("mini_face") == before["mini_face"] and ov("bs_target_mouth_open") == before["bs_target_mouth_open"])
    check(
        "隠す: ジョイント・シーンの格子は見えたまま",
        all(not cmds.getAttr(j + ".overrideEnabled") or cmds.getAttr(j + ".overrideVisibility") for j in ids["joints"])
        and cmds.getAttr("tdFacialGrid_GRP.visibility")
        and cmds.objExists("tdFacialGrid_GRP"),
    )
    check("隠す: 状態の一行", h.status_text() == "顔以外のメッシュ 2 個を隠しています", h.status_text())
    check(
        "隠す: Undo に積まない・変更フラグを立てない・キーを作らない",
        (cmds.undoInfo(query=True, undoName=True), cmds.undoInfo(query=True, redoName=True)) == undo_before
        and not cmds.file(query=True, modified=True)
        and set(cmds.ls(type="animCurve") or []) == anim_before,
    )
    check("隠しているメッシュも「表示中のメッシュ」として数える（検出が崩れない）", {"mini_brow", "mini_body"} <= {scene_mod.short_name(m) for m in scene_mod.list_visible_meshes()})
    # 隠さないもの（髪）
    h.set_keep({"mini_brow"})
    check("隠さないものに入れると、元の値へ正確に戻る（元の override・表示タイプのまま）", ov("mini_brow") == before["mini_brow"] and ov("mini_body")[:2] == (1, False), f"{ov('mini_brow')} {before['mini_brow']}")
    h.set_keep(set())
    check("「顔以外を全部」: また隠れる", ov("mini_brow")[:2] == (1, False))
    h.show_all()
    check("「全部表示」: 全部戻る", ov("mini_brow") == before["mini_brow"] and ov("mini_body") == before["mini_body"])
    h.set_keep(set())
    # 保存: 全部見えた状態で保存され、戻る
    cmds.file(modified=False)
    s.scene_grid.set_enabled(False)
    h.set_enabled(False)
    cmds.setAttr("mini_brow.overrideEnabled", 0)  # 保存したファイルに ove / ovv が出ないように（元の override を外す）
    cmds.setAttr("mini_brow.overrideDisplayType", 0)
    h.set_enabled(True)
    cmds.file(modified=False)
    f = tmp / "saved_hide.ma"
    cmds.file(rename=str(f))
    cmds.file(save=True, type="mayaAscii")
    text = f.read_text(encoding="utf-8", errors="replace")
    i = text.find('".ove"')
    check("保存: 保存したファイルに非表示の override が入っていない", ".ovv" not in text and '".ove"' not in text, f"{text.count('.ovv')} {text.count('.ove')} {text[max(0, i - 200):i + 60]!r}")
    check("保存のあと: また隠れている・変更フラグは変わらない", ov("mini_brow")[:2] == (1, False) and ov("mini_body")[:2] == (1, False) and not cmds.file(query=True, modified=True))
    out = tmp / "export_hide.ma"
    cmds.file(str(out), exportAll=True, type="mayaAscii", force=True)
    check("出力（Export All）: ファイルに非表示の override が入らない・出力のあと隠れている", ".ovv" not in out.read_text(encoding="utf-8", errors="replace") and ov("mini_body")[:2] == (1, False))
    s.begin_edit()
    cmds.file(save=True, type="mayaAscii")
    check("編集状態で保存: 保存のあとも編集状態・隠れている", s.editing and ov("mini_body")[:2] == (1, False))
    s.end_edit(quiet=True)
    # リロードの引き継ぎ
    st = s.export_state()["hide_others"]
    h.remove()
    h.enabled, h.keep = False, set()
    check("リロード前: 元へ戻る", ov("mini_body")[:2] == (0, True) and ov("mini_brow")[:2] == (0, True), f"{ov('mini_body')} {ov('mini_brow')} {h._applied}")
    h.keep = {"zzz"}
    h.import_state(st)
    check("リロード後: 引き継いだ設定でまた隠れる", h.enabled and ov("mini_body")[:2] == (1, False) and h.keep == set(), str(st))
    h.remove()
    h.enabled = False
    h.import_state({"on": True, "keep": ["mini_brow"]})
    check("リロードの引き継ぎ: 隠さないもの（名前）も戻る", h.keep == {"mini_brow"} and ov("mini_brow")[:2] == (0, True) and ov("mini_body")[:2] == (1, False), f"{h.keep} {ov('mini_brow')} {ov('mini_body')}")
    h.set_keep(set())
    # ロック・つながり
    h.set_enabled(False)
    cmds.setAttr("mini_body.overrideVisibility", lock=True)
    layer = cmds.createDisplayLayer(["mini_brow"], name="hairLayer", noRecurse=True)
    h.set_enabled(True)
    check("ロックされている・表示レイヤーにつながっているメッシュは触らず、飛ばす", h.hidden_names() == [] and sorted(h.skipped) == ["mini_body", "mini_brow"], f"{h.hidden_names()} {h.skipped}")
    check("飛ばしたものを状態の一行に出す", "2 個は隠せませんでした" in h.status_text() and "mini_body" in h.status_text(), h.status_text())
    h.set_enabled(False)
    check("飛ばしたものは何も変わっていない", cmds.getAttr("mini_body.overrideVisibility") and cmds.getAttr("mini_body.overrideVisibility", lock=True))
    cmds.setAttr("mini_body.overrideVisibility", lock=False)
    cmds.delete(layer)
    cmds.setAttr("mini_brow.overrideEnabled", 0)  # 表示レイヤーを消しても値は 1 のまま残る
    # 新しいメッシュが増えたら追従
    h.set_enabled(True)
    cmds.polyCube(name="extraProp")
    h._next_scan = 0.0
    h.follow()
    check("追従: あとから増えたメッシュも隠す", ov("extraProp")[:2] == (1, False) and h.status_text().startswith("顔以外のメッシュ 3 個"), h.status_text())
    cmds.delete("extraProp")
    h._next_scan = 0.0
    h.follow()
    check("追従: 消えたメッシュは数から外れる", len(h.hidden_names()) == 2, str(h.hidden_names()))
    # シーンの入れ替え・データを閉じる
    S.on_before_scene_change()
    check("シーンを開く・作る直前に元へ戻る", ov("mini_body")[:2] == (0, True) and ov("mini_brow")[:2] == (0, True), f"{ov('mini_body')} {ov('mini_brow')}")
    h.apply()
    s.close()
    check("データを閉じると元へ戻る（設定のチェックは残る）", ov("mini_body")[:2] == (0, True) and ov("mini_brow")[:2] == (0, True) and h.enabled)
    ids, s = fresh(tmp)
    h = s.hide_others
    h.set_enabled(True)
    cmds.file(new=True, force=True)
    S.on_new_scene()
    h.follow()
    check("新しいシーン: 落ちない・古い記録は消える", h._applied == {} and not scene_mod.TOOL_HIDDEN)
    h.enabled = False
    s.close()


def sec_03_hide_ui(tmp: Path) -> None:
    from maya import cmds

    import facial_fixture
    from tdrive_facial import ui_grid

    ids, s = fresh(tmp)
    facial_fixture.add_body(garbage_pose=False)
    h = s.hide_others
    tab = ui_grid.GridTab(s)
    tab.resize(480, 900)
    tab.show()
    APP.processEvents()
    check(
        "UI: 文言",
        tab.hide_others_cb.text() == "顔以外を隠す"
        and tab.btn_hide_pick.text() == "隠すもの…"
        and "シーンファイルにも残りません" in tab.hide_others_cb.toolTip()
        and "チェックを外す" in tab.hide_others_cb.toolTip(),
    )
    check("UI: 最初はオフ・何も隠れていない", not tab.hide_others_cb.isChecked() and cmds.getAttr("mini_body.visibility") and not cmds.getAttr("mini_body.overrideEnabled"))
    tab.hide_others_cb.setChecked(True)
    check("UI: チェックで隠れる・状態の行が出る・タイマーが動く", cmds.getAttr("mini_body.overrideVisibility") == 0 and "2 個を隠しています" in tab.hide_note.text() and tab.timer.isActive(), tab.hide_note.text())
    dlg = tab.make_hide_dialog()
    names = [dlg.list.item(i).text() for i in range(dlg.list.count())]
    check("ダイアログ: メッシュの一覧（チェックあり = 隠す）", sorted(names) == ["mini_body", "mini_brow"] and not dlg.keep_names(), str(names))
    dlg.set_all(False)
    check("ダイアログ: 「全部表示」で全部のチェックが外れる", sorted(dlg.keep_names()) == ["mini_body", "mini_brow"])
    dlg.set_all(True)
    check("ダイアログ: 「顔以外を全部」で全部にチェック", dlg.keep_names() == [])
    dlg.list.item(names.index("mini_brow")).setCheckState(QtCoreQt().Unchecked)
    dlg.resize(420, 320)
    shot(dlg, "hide_dialog.png")
    tab.run_dialog = lambda d: True
    tab.make_hide_dialog = lambda: dlg
    tab.on_hide_pick()
    check("ダイアログで決める: 隠さないものが覚えられ、反映される", h.keep == {"mini_brow"} and cmds.getAttr("mini_brow.overrideVisibility") == 1 and cmds.getAttr("mini_body.overrideVisibility") == 0, str(h.keep))
    tab.scroll.verticalScrollBar().setValue(0)
    shot(tab, "grid_hide.png")
    tab.detach()
    check("パネルを閉じる: 元へ戻る・設定は残る", not cmds.getAttr("mini_body.overrideEnabled") and h.enabled)
    tab.deleteLater()
    tab2 = ui_grid.GridTab(s)
    check("開き直す: チェックが入っていて、また隠れる", tab2.hide_others_cb.isChecked() and cmds.getAttr("mini_body.overrideVisibility") == 0)
    tab2.hide_others_cb.setChecked(False)
    check("チェックを外すと全部元どおり", not cmds.getAttr("mini_body.overrideEnabled") and tab2.hide_note.isHidden())
    tab2.detach()
    tab2.deleteLater()
    s.close()


def QtCoreQt():  # noqa: N802
    from PySide6 import QtCore

    return QtCore.Qt


# ---------------------------------------------------------------------------
# 項目 3: シェイプの分類のタブ
# ---------------------------------------------------------------------------


EXTRA = ["bs.eye_close_L", "bs.eye_close_R", "bs.eye_wide", "bs.mouth_up", "bs.cheek_puff", "bs.look_left"]


def cat_setup(tmp: Path, with_profile: bool = True):
    from tdrive_facial.core import categories as C
    from tdrive_facial.core import profile as P

    ids, s = fresh(tmp)
    if with_profile:
        prof = P.NamingProfile(
            name="minicat",
            standard_curves=["bs.mouth_open", "bs.smile_L"],
            categories=[
                C.Category("eye", "目", ["prefix:eye_"]),
                C.Category("brow", "眉", ["prefix:brow_"]),
                C.Category("mouth", "口", ["prefix:mouth_", "prefix:smile_"]),
                C.Category("cheek", "頬", ["prefix:cheek_"]),
                C.Category("look", "視線", ["prefix:look_"]),
            ],
        )
        P.save(prof, P.project_profiles_dir(tmp, "mini") / "minicat.fcprofile.json")
        res = s.apply_profile("minicat")
        assert res.ok, res.message
    from maya import cmds

    face, bs = ids["face"], ids["bs"]  # 名前の決まりごとを試すため、本物のターゲット（顔の複製）を足す
    for i, name in enumerate(EXTRA):
        alias = name.split(".", 1)[1]
        dup = cmds.duplicate(face, name=f"cat_target_{alias}")[0]
        cmds.blendShape(bs, edit=True, target=(face, 10 + i, dup, 1.0))
        cmds.aliasAttr(alias, f"{bs}.weight[{10 + i}]")
        cmds.delete(dup)
    s.refresh_scene()
    s.add_to_working_set(curves=["bs.mouth_open", "bs.smile_L", "bs.smile_R", "bs.brow_up", *EXTRA])
    return ids, s


def rows_visible(tab) -> list[str]:
    return sorted(n for n, w in tab._curve_rows.items() if not w.label.isHidden())


def sec_04_pose_tabs(tmp: Path) -> None:
    from tdrive_facial import ui_pose

    ids, s = cat_setup(tmp)
    s.begin_edit()
    s.select_point(1, 2)
    tab = ui_pose.PoseTab(s)
    tab.resize(400, 900)
    tab.show()
    APP.processEvents()
    tab.flush()
    labels = [tab.cat_bar.tabText(i) for i in range(tab.cat_bar.count())]
    check("タブ: すべて + 分類（プロファイルの順）+ 値あり。数字つき", labels == ["すべて (10)", "目 (3)", "眉 (1)", "口 (4)", "頬 (1)", "視線 (1)", "値あり (2)"], str(labels))
    rows_before = dict(tab._curve_rows)
    check("すべて: 全部の行が見える", len(rows_visible(tab)) == 10)
    tab.select_category("eye")
    check("目: 目のシェイプだけ見える", rows_visible(tab) == ["bs.eye_close_L", "bs.eye_close_R", "bs.eye_wide"], str(rows_visible(tab)))
    check("タブの切り替えで行を作り直さない（同じウィジェット）", all(tab._curve_rows[n] is w for n, w in rows_before.items()) and len(tab._curve_rows) == len(rows_before))
    tab.select_category("mouth")
    check("口（mouth_ と smile_）", rows_visible(tab) == ["bs.mouth_open", "bs.mouth_up", "bs.smile_L", "bs.smile_R"], str(rows_visible(tab)))
    tab.select_category("_valued")
    check("値あり: 0 でない値のシェイプだけ（この点は mouth_open と smile_L）", rows_visible(tab) == ["bs.mouth_open", "bs.smile_L"], str(rows_visible(tab)))
    check("選んだタブをセッションが覚えている", s.pose_category == "_valued")
    # 絞り込みはタブの中で効く
    tab.select_category("mouth")
    tab.curve_filter.setText("smile")
    tab.flush()
    APP.processEvents()
    check("絞り込みはタブの中で効く（口 × smile）", rows_visible(tab) == ["bs.smile_L", "bs.smile_R"], str(rows_visible(tab)))
    labels = [tab.cat_bar.tabText(i) for i in range(tab.cat_bar.count())]
    check("絞り込み中: タブの数字は当たった数（口 (2)・目 (0)）", "口 (2)" in labels and "目 (0)" in labels, str(labels))
    tab.curve_filter.setText("")
    tab.flush()
    # 未保存の変更の ●
    tab.select_category("eye")
    tab.set_curve("bs.eye_wide", 0.5)
    tab._sync_tab_marks()
    labels = [tab.cat_bar.tabText(i) for i in range(tab.cat_bar.count())]
    check("未保存の変更があるタブに ● が付く（目だけ）", labels[1] == "目 (3)  ●" and "●" not in labels[0] and "●" not in labels[2], str(labels))
    tab.select_category("_valued")
    tab.refresh()
    check("値あり: 動かした行が加わる（3 本）", rows_visible(tab) == ["bs.eye_wide", "bs.mouth_open", "bs.smile_L"], str(rows_visible(tab)))
    # 400〜480 px
    for w in (400, 480):
        tab.resize(w, 900)
        APP.processEvents()
        with_bar = tab.minimumSizeHint().width()
        tab.cat_bar.setVisible(False)
        without = tab.minimumSizeHint().width()
        tab.cat_bar.setVisible(True)
        check(f"{w} px: タブの帯が幅に収まり（矢印で送る）、パネルの最小幅を広げない", tab.cat_bar.width() <= w and with_bar <= without, f"{tab.cat_bar.width()} {with_bar} {without}")
    tab.select_category("_all")
    tab.resize(480, 900)
    shot(tab, "pose_tabs.png")
    # リロードをまたぐ
    tab.select_category("mouth")
    st = s.export_state()
    check("引き継ぎ: 状態に分類のタブがある", st["pose_category"] == "mouth")
    s.pose_category = "_all"
    s.import_state(st)
    tab.detach()
    tab.deleteLater()
    tab2 = ui_pose.PoseTab(s)
    tab2.flush()
    check("開き直しても選んでいたタブ", tab2.current_category() == "mouth", tab2.current_category())
    s.pose_category = "nothing"
    tab3 = ui_pose.PoseTab(s)
    check("覚えていた分類が無いときは「すべて」", tab3.current_category() == "_all")
    tab2.detach()
    tab3.detach()
    s.end_edit(quiet=True)
    s.close()


def sec_05_pose_tabs_fallback(tmp: Path) -> None:
    """プロファイルが無いとき: 名前の先頭の語で自動に分ける。"""
    from tdrive_facial import ui_pose

    ids, s = cat_setup(tmp, with_profile=False)
    s.begin_edit()
    s.select_point(1, 2)
    tab = ui_pose.PoseTab(s)
    tab.flush()
    labels = [tab.cat_bar.tabText(i) for i in range(tab.cat_bar.count())]
    check(
        "プロファイル無し: 先頭の語で自動に分ける（eye / mouth / smile…）",
        labels[0] == "すべて (10)" and any(l.startswith("eye (3)") for l in labels) and any(l.startswith("mouth (") for l in labels) and labels[-1].startswith("値あり"),
        str(labels),
    )
    tab.select_category("auto:eye")
    check("自動の分類でも出し分けられる", rows_visible(tab) == ["bs.eye_close_L", "bs.eye_close_R", "bs.eye_wide"], str(rows_visible(tab)))
    tab.detach()
    s.end_edit(quiet=True)
    s.close()
    ids, s = fresh(tmp)
    s.add_to_working_set(curves=["bs.mouth_open", "bs.smile_L", "bs.smile_R", "bs.brow_up"])
    s.begin_edit()
    s.select_point(1, 2)
    tab = ui_pose.PoseTab(s)
    tab.flush()
    labels = [tab.cat_bar.tabText(i) for i in range(tab.cat_bar.count())]
    check("分けられないとき（合成モデルの 4 本）: 「すべて」と「値あり」だけ", labels == ["すべて (4)", "値あり (2)"], str(labels))
    tab.detach()
    s.end_edit(quiet=True)
    s.close()


def sec_06_setup_categories(tmp: Path) -> None:
    from PySide6 import QtWidgets

    from tdrive_facial import ui_setup

    ids, s = cat_setup(tmp)
    s.set_working_set(curves=list(EXTRA), bones=[])  # 目・口・頬・視線の一部が入っている
    setup = ui_setup.SetupTab(s)
    setup.resize(480, 1400)
    setup.show()
    APP.processEvents()
    setup.refresh()
    combo = setup.curve_cat
    labels = [combo.itemText(i) for i in range(combo.count())]
    check("作業セット: 分類の選択肢（数字 = 入れた数 / 全体）", labels[0] == "すべて" and "目 (3/3)" in labels and "口 (1/4)" in labels, str(labels))

    def shown() -> list[str]:
        return sorted(setup.curve_list.item(i).text() for i in range(setup.curve_list.count()) if not setup.curve_list.item(i).isHidden())

    combo.setCurrentIndex(combo.findData("eye"))
    check("目を選ぶと目のシェイプだけが一覧に出る", shown() == ["bs.eye_close_L", "bs.eye_close_R", "bs.eye_wide"], str(shown()))
    check("選んだ分類をセッションが覚えている", s.setup_category == "eye")
    setup.on_ws_bulk("curve", False)
    setup.refresh()
    check("表示中を全部オフ: 見えている分類（目）だけ外れる", sorted(s.doc.working_set.curves) == sorted(n for n in EXTRA if not n.startswith("bs.eye_")), str(s.doc.working_set.curves))
    labels = [combo.itemText(i) for i in range(combo.count())]
    check("数字が更新される（目 (0/3)）", "目 (0/3)" in labels, str(labels))
    setup.on_ws_bulk("curve", True)
    setup.refresh()
    check("表示中を全部オン: 見えている分類（目）だけ作業セットへ", sorted(s.doc.working_set.curves) == sorted(EXTRA), str(s.doc.working_set.curves))
    combo.setCurrentIndex(combo.findData("mouth"))
    setup.curve_filter.setText("smile")
    check("分類 × 文字列の絞り込み", shown() == ["bs.smile_L", "bs.smile_R"], str(shown()))
    setup.curve_filter.setText("")
    check("ボタンの文言", any(b.text() == "表示中を全部オフ" for b in setup.findChildren(QtWidgets.QPushButton)))
    combo.setCurrentIndex(0)
    check("すべてへ戻すと全部出る", len(shown()) >= 10)
    combo.setCurrentIndex(combo.findData("look"))
    sa = setup.findChild(QtWidgets.QScrollArea)
    sa.verticalScrollBar().setValue(sa.verticalScrollBar().maximum())
    shot(setup, "setup_categories.png")
    st = s.export_state()
    check("引き継ぎ: 作業セットの分類も", st["setup_category"] == "look")
    setup2 = ui_setup.SetupTab(s)
    check("開き直すと同じ分類", setup2.curve_cat.currentData() == "look", str(setup2.curve_cat.currentData()))
    s.close()


SECTIONS = [(n[4:], f) for n, f in sorted(globals().items()) if n.startswith("sec_") and callable(f)]


def main() -> None:
    wanted = sys.argv[1:]
    for name, fn in SECTIONS:
        if wanted and not any(w in name for w in wanted):
            continue
        tmp = Path(tempfile.mkdtemp(prefix="tdrive_vhc_"))
        try:
            fn(tmp)
        except Exception:
            RESULTS.append((f"{name}: 例外", False, traceback.format_exc()))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


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
        print(f"SMOKE RESULT {len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
        sys.stdout.flush()
        maya.standalone.uninitialize()
        os._exit(1 if failed else 0)
