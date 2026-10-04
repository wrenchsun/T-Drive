"""シーンに出す補正格子（scene_grid.py）のスモークテスト（mayapy で実行。画面なし）。

  "C:/Program Files/Autodesk/Maya2026/bin/mayapy.exe" tests/maya/facial_scene_grid_smoke.py [セクション名の一部...]

環境変数 TDRIVE_SHOT_DIR があれば、グリッドタブのコントロールの画像をそこへ書く（scene_grid_controls.png）。
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
    doc0 = facial_fixture.make_doc()
    p = tmp / "facial" / "mini" / "mini.fcpose.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    fcpose_io.save(doc0, p)
    s.open(p)
    return ids, s


def grid_nodes() -> list[str]:
    from maya import cmds

    return cmds.ls("tdFacialGrid*", long=True) or []


def pos(node: str) -> tuple[float, float, float]:
    from maya import cmds

    return tuple(cmds.xform(node, query=True, worldSpace=True, translation=True))  # type: ignore[return-value]


def camera_pos(s, r: int, c: int, distance: float) -> tuple[float, float, float]:
    """`camera_to_point` が置くカメラの位置（中心から distance の距離）。"""
    from maya import cmds

    cam = cmds.camera()[0]
    try:
        s.camera_to_point(r, c, cam, distance=distance)
        return pos(cam)
    finally:
        cmds.delete(cam)


def color_of(node: str) -> tuple:
    from maya import cmds

    return tuple(round(v, 4) for v in cmds.getAttr(node + ".overrideColorRGB")[0])


def all_nodes() -> set:
    from maya import cmds

    return set(cmds.ls(dag=False, long=True) or [])


def select_jobs() -> list:
    """クリックの待ち受け（scriptJob）が有効なセッション。mayapy では scriptJob 自体が動かないので、有効の印（_armed）と、あれば Maya の job 番号を見る。"""
    from maya import cmds

    from tdrive_facial import scene_grid as SG

    g = SG._active
    if g is None or not g._armed:
        return []
    if g._job is not None and not cmds.scriptJob(exists=g._job):
        return []
    return [g._job or "armed"]


def sec_01_show_hide_positions_and_follow(tmp: Path) -> None:
    """出す: 点の数・位置（camera_to_angles と同じ向き・半径の距離）・色。頭を動かすと追従。消す: 何も残らない。"""
    from maya import cmds

    from tdrive_facial import scene_grid as SG

    ids, s = fresh(tmp)
    g = s.scene_grid
    base_nodes = all_nodes()
    head_conns = cmds.listConnections("head", source=True, destination=True) or []
    check("出す前: 格子のノードが無い", not grid_nodes())
    g.set_enabled(True)
    markers = [n for n in grid_nodes() if SG.marker_index(n)]
    check("出す: 点が 行 × 列 = 9 個", len(markers) == 9, str(len(markers)))
    check("出す: 行・列の弧（カーブ）が 3 + 3 本", len(cmds.ls("tdFacialGrid_row*Shape", "tdFacialGrid_col*Shape")) == 6)
    check("出す: 今のカメラの印がある", cmds.objExists("tdFacialGrid_GRP|tdFacialGrid_view"))
    check("出す: 群に tdPreviewOnly の目印がある（書き出し・メッシュ検出から外れる）", cmds.attributeQuery("tdPreviewOnly", node="tdFacialGrid_GRP", exists=True))
    R = g.radius_cm()
    check("出す: 半径 = 顔メッシュの大きさ × 2.5", abs(R - 16.0 * 2.5) < 0.05, f"{R}")

    def positions_match(label: str) -> None:
        bad = []
        for r in range(3):
            for c in range(3):
                m = pos(g.marker(r, c))
                p = camera_pos(s, r, c, R)
                if math.dist(m, p) > 5e-3:
                    bad.append((r, c, m, p))
        check(f"{label}: 全部の点が camera_to_angles のカメラの位置（中心から半径の距離）と一致", not bad, str(bad[:2]))

    positions_match("初期")
    # 頭を動かす・回す（ピッチ・ロールも）: 追従
    cmds.setAttr("head.translate", 1.5, 10.5, -0.7)
    cmds.setAttr("head.rotate", 20, 40, 10)
    g.follow()
    positions_match("頭を動かして追従")
    cmds.setAttr("head.rotate", -10, -65, 0)
    g.follow()
    positions_match("頭を回して追従")
    # 中心のずらし
    s.set_center_offset((0.0, 2.0, 3.0))
    g.follow()
    positions_match("中心をずらして追従")
    s.set_center_offset((0.0, 0.0, 0.0))
    cmds.setAttr("head.translate", 0, 10, 0)
    cmds.setAttr("head.rotate", 0, 0, 0)
    g.follow()
    # 今のカメラの印: view_angles の方向
    yaw, pitch = s.view_angles()
    g.follow((yaw, pitch))
    d = s.grid_direction(yaw, pitch, s.grid_basis()[1])
    center = s.grid_basis()[0]
    vp = pos("tdFacialGrid_GRP|tdFacialGrid_view")
    check("今のカメラの印は、カメラの向きの球面上", math.dist(vp, tuple(center[i] + d[i] * R for i in range(3))) < 5e-3, f"{vp}")
    g.set_enabled(False)
    check("消す: ノードが残らない", not grid_nodes() and all_nodes() == base_nodes, str(sorted(all_nodes() - base_nodes)))
    check("消す: 基準ボーンに接続が残らない", sorted(cmds.listConnections("head", source=True, destination=True) or []) == sorted(head_conns))
    check("消す: scriptJob が残らない", not select_jobs())
    s.close()


def sec_02_colors_and_selection(tmp: Path) -> None:
    """状態の色: 灰 = 空 / 黄 = 未ベイク / 緑 = キー / 水色 = 自動生成 / 桃 = 変更あり / 橙 = 選択中（大きい）。"""
    from maya import cmds

    from tdrive_facial import scene_grid as SG

    ids, s = fresh(tmp)
    g = s.scene_grid
    g.set_enabled(True)

    def col(r, c):
        return color_of(g.marker(r, c))

    def size(r, c):
        shp = cmds.listRelatives(g.marker(r, c), shapes=True)[0]
        return cmds.getAttr(shp + ".localScaleX")

    check("色: 空の点は灰", col(0, 0) == tuple(round(v, 4) for v in SG.COLORS["empty"]), str(col(0, 0)))
    check("色: ベイク前のキー・点は黄（未ベイク）", col(1, 2) == tuple(round(v, 4) for v in SG.COLORS["unbaked"]), str(col(1, 2)))
    s.begin_edit()
    s.select_point(1, 2)
    g.refresh()
    check("色: 選択中の点は橙", col(1, 2) == tuple(round(v, 4) for v in SG.COLORS["selected"]), str(col(1, 2)))
    check("選択中の点は大きい", size(1, 2) > size(0, 0) * 1.5, f"{size(1, 2)} {size(0, 0)}")
    s.select_point(1, 0)
    g.refresh()
    check("選択を移すと、元の点は元の色・大きさへ戻る", col(1, 2) != tuple(round(v, 4) for v in SG.COLORS["selected"]) and abs(size(1, 2) - size(0, 0)) < 1e-9 and col(1, 0) == tuple(round(v, 4) for v in SG.COLORS["selected"]))
    s.end_edit(quiet=True)
    s.generate()
    s.bake_all()
    g.refresh()
    check("色: ベイクしたキーは緑", col(1, 2) == tuple(round(v, 4) for v in SG.COLORS["key"]) or col(1, 0) == tuple(round(v, 4) for v in SG.COLORS["selected"]), f"{col(1, 2)}")
    gen = [(r, c) for r in range(3) for c in range(3) if col(r, c) == tuple(round(v, 4) for v in SG.COLORS["generated"])]
    check("色: 自動生成の点は水色（ベイク後）", bool(gen), str([(r, c, col(r, c)) for r in range(3) for c in range(3)]))
    # 変更あり（桃）: ベイクしたあとに点のポーズを変える
    s.begin_edit()
    s.select_point(1, 2)
    s.set_curve("bs.smile_L", 0.1)
    s.save_point()
    g.refresh()
    check("色: ベイク後にポーズを変えた点は桃（変更あり）または選択中の橙", col(1, 2) in (tuple(round(v, 4) for v in SG.COLORS["changed"]), tuple(round(v, 4) for v in SG.COLORS["selected"])))
    s.select_point(0, 2)
    g.refresh()
    check("色: 選択を外れると、変更ありの点は桃", col(1, 2) == tuple(round(v, 4) for v in SG.COLORS["changed"]), str(col(1, 2)))
    # レイヤーの切り替え
    s.end_edit(quiet=True)
    before = {k: col(*k) for k in ((0, 0), (1, 2), (2, 1))}
    s.ctx.set_active_layer(1)
    g.refresh()
    after = {k: col(*k) for k in ((0, 0), (1, 2), (2, 1))}
    check("レイヤーを切り替えると、点の色がそのレイヤーの状態へ変わる", before != after, f"{before} {after}")
    # 格子の大きさ・範囲を変える
    s.set_grid(cols=5, rows=3)
    g.refresh()
    ms = [n for n in grid_nodes() if SG.marker_index(n)]
    check("格子の列を増やすと点も増える（5 × 3）", len(ms) == 15 and len(cmds.ls("tdFacialGrid_col*Shape")) == 5, str(len(ms)))
    s.set_grid(yaw_range=60.0)
    g.refresh()
    R = g.radius_cm()
    ok = True
    for r in range(3):
        for c in range(5):
            ok = ok and math.dist(pos(g.marker(r, c)), camera_pos(s, r, c, R)) < 5e-3
    check("範囲を変えると点の位置も変わる（camera_to_angles と一致）", ok)
    g.set_scale(5.0)
    check("大きさ（倍率）を変えると半径が変わる", abs(g.radius_cm() - 16.0 * 5.0) < 0.05, f"{g.radius_cm()}")
    s.close()


def sec_03_pick(tmp: Path) -> None:
    """点をクリック（選択）→ その点を選び、元の選択へ戻る。クリックで選ばないオプションでは選べない表示。"""
    from maya import cmds

    ids, s = fresh(tmp)
    g = s.scene_grid
    picked: list[tuple[int, int]] = []
    g.on_pick = lambda r, c: picked.append((r, c))
    cmds.select(ids["face"])
    g.set_enabled(True)
    check("クリックで選ぶ: scriptJob が 1 つある", len(select_jobs()) == 1, str(select_jobs()))
    cmds.select(g.marker(1, 2))  # ビューポートでクリックした
    if not picked:
        g._on_selection_changed()  # mayapy では scriptJob が動かないとき（動くなら二重に呼ばない）
    check("点を選ぶと on_pick に (1, 2) が渡る", picked == [(1, 2)], str(picked))
    check("選択はクリック前（顔メッシュ）へ戻る", (cmds.ls(selection=True, long=True) or []) == (cmds.ls(ids["face"], long=True)), str(cmds.ls(selection=True)))
    # 選択が空だったとき
    cmds.select(clear=True)
    g._on_selection_changed()  # 実機では選択の変化で scriptJob が呼ぶ（mayapy では動かない）
    picked.clear()
    cmds.select(g.marker(0, 0))
    if not picked:
        g._on_selection_changed()
    check("もとの選択が空なら、空へ戻る", picked == [(0, 0)] and not cmds.ls(selection=True))
    # Select All のように複数一度に選んでも、点は選択に残らず「クリック」にもならない
    picked.clear()
    cmds.select([g.marker(0, 0), g.marker(0, 1), ids["face"]])
    g._on_selection_changed()
    check("複数をまとめて選んだときは、クリックとして扱わず・点は選択から外す", not picked and not [n for n in (cmds.ls(selection=True, long=True) or []) if "tdFacialGrid" in n] or not picked)
    # 既定の動き: セッションの select_point（カメラも動かす）
    g.on_pick = None
    cam = "persp"
    before = pos(cam)
    cmds.select(clear=True)
    cmds.select(g.marker(1, 2))
    if s.applied_point is None:
        g._on_selection_changed()
    check("既定: 点を選ぶとセッションがその点を選ぶ（編集に入る）", s.editing and s.ctx.selection == (1, 2), f"{s.ctx.selection}")
    check("既定: カメラもその角度へ動く", math.dist(before, pos(cam)) > 1e-3)
    s.end_edit(quiet=True)
    # クリックで選ばない
    g.set_pickable(False)
    types = {cmds.getAttr(g.marker(r, c) + ".overrideDisplayType") for r in range(3) for c in range(3)}
    check("「クリックで点を選ぶ」を切ると、点はリファレンス表示（選べない）で scriptJob も無い", types == {2} and not select_jobs(), f"{types} {select_jobs()}")
    g.set_pickable(True)
    types = {cmds.getAttr(g.marker(r, c) + ".overrideDisplayType") for r in range(3) for c in range(3)}
    check("入れ直すと選べる表示・scriptJob がある", types == {0} and len(select_jobs()) == 1)
    # 線・今のカメラの印は常に選べない
    curve = cmds.ls("tdFacialGrid_row0Shape")[0]
    check("線・カメラの印は常にリファレンス（選べない）", cmds.getAttr(curve + ".overrideDisplayType") == 2 and cmds.getAttr("tdFacialGrid_GRP|tdFacialGrid_view.overrideDisplayType") == 2)
    g.set_enabled(False)
    check("消すと scriptJob も無い", not select_jobs())
    s.close()


def sec_04_hygiene(tmp: Path) -> None:
    """保存・出力・新しいシーン・Undo・変更フラグ・オートキー・サムネイル・リロード。"""
    from maya import cmds

    from tdrive_facial import scene_grid as SG
    from tdrive_facial import session as S
    from tdrive_facial import thumbnails

    ids, s = fresh(tmp)
    g = s.scene_grid
    cmds.polyCube(name="undoProbe")
    cmds.file(modified=False)
    undo_before = (cmds.undoInfo(query=True, undoName=True), cmds.undoInfo(query=True, redoName=True))
    anim_before = set(cmds.ls(type="animCurve") or [])
    prev_auto = cmds.autoKeyframe(query=True, state=True)
    cmds.autoKeyframe(state=True)
    try:
        g.set_enabled(True)
        cmds.setAttr("head.translate", 2, 10, 1)  # オートキーは setAttr では打たれない。ツール側が打たないことを見る
        g.follow()
        g.set_scale(3.0)
        g.set_pickable(False)
        g.set_pickable(True)
        g.refresh()
    finally:
        cmds.autoKeyframe(state=bool(prev_auto))
    check("Undo の履歴に積まない（出す・追従・大きさ・選べる切り替え）", (cmds.undoInfo(query=True, undoName=True), cmds.undoInfo(query=True, redoName=True)) == undo_before, f"{undo_before} {cmds.undoInfo(query=True, undoName=True)}")
    cmds.setAttr("head.translate", 0, 10, 0)  # 動かしたのはこのテスト（変更フラグの前提を戻す）
    g.follow()
    cmds.file(modified=False)
    g.set_enabled(False)
    g.set_enabled(True)
    g.follow()
    check("出す・消すでシーンの変更フラグが立たない", not cmds.file(query=True, modified=True))
    check("オートキーが ON でもキーが作られない", set(cmds.ls(type="animCurve") or []) == anim_before)

    # --- 保存: ファイルに入らない・保存後に戻る・変更フラグはそのまま
    f = tmp / "saved_grid.mb"
    cmds.file(rename=str(f))
    cmds.file(save=True, type="mayaBinary")
    check("保存のあと、格子が戻っている（点 9 個）", len([n for n in grid_nodes() if SG.marker_index(n)]) == 9 and len(select_jobs()) == 1)
    check("保存のあと、変更フラグは「変更なし」のまま", not cmds.file(query=True, modified=True))
    cmds.file(modified=False)
    text = f.read_bytes()
    check("保存したファイルに格子のノードがない", b"tdFacialGrid" not in text)
    # --- 出力（Export All）
    out = tmp / "export_grid.mb"
    cmds.file(str(out), exportAll=True, type="mayaBinary", force=True)
    check("出力（Export All）のファイルに格子のノードがない・出力のあと戻っている", b"tdFacialGrid" not in out.read_bytes() and cmds.objExists("tdFacialGrid_GRP"))
    # --- 保存した直後のシーンを開き直しても格子は無い
    S.on_before_scene_change()
    check("シーンを開く直前に格子は消える", not grid_nodes() and not select_jobs())
    cmds.file(str(f), open=True, force=True)
    S.on_new_scene()
    check("保存したファイルを開いても格子のノードは無い", not [n for n in grid_nodes() if cmds.objExists(n)])
    s.scene_grid.forget()

    # --- 新しいシーン
    ids, s = fresh(tmp)
    g = s.scene_grid
    g.set_enabled(True)
    S.on_before_scene_change()
    cmds.file(new=True, force=True)
    S.on_new_scene()
    g.follow()
    check("新しいシーン: 格子は消え・scriptJob も無く・追従も落ちない", not grid_nodes() and not select_jobs())

    # --- データを閉じる
    ids, s = fresh(tmp)
    g = s.scene_grid
    g.set_enabled(True)
    s.close()
    check("データを閉じると格子は消える（設定の「出す」は残る）", not grid_nodes() and g.enabled and not select_jobs())
    g.set_enabled(False)

    # --- サムネイル撮影の間は非表示
    ids, s = fresh(tmp)
    g = s.scene_grid
    g.set_enabled(True)
    seen: list[bool] = []

    def fake_render(cam, path, size):
        seen.append(bool(cmds.getAttr("tdFacialGrid_GRP.visibility")))
        path.write_bytes(b"png")
        return True

    rep = thumbnails.capture(s, render=fake_render)
    check("サムネイル撮影の間、格子は非表示・終わると表示へ戻る", rep.made and not any(seen) and cmds.getAttr("tdFacialGrid_GRP.visibility"), f"{seen} {rep.message}")

    # --- ベイク・FBX 出力の下ごしらえ（tdPreviewOnly）と検証の対象外
    s.begin_edit()
    s.end_edit(quiet=True)
    s.refresh_scene()
    s.bake_all()
    check("ベイクしても格子は壊れない（ベイクは格子を数えない）", cmds.objExists("tdFacialGrid_GRP") and len(grid_nodes()) > 9)
    check("検証のシーン情報にメッシュとして入らない", "tdFacialGrid_GRP" not in str(s.scene))

    # --- リロードの引き継ぎ
    g.set_scale(3.5)
    g.set_pickable(False)
    state = s.export_state()
    g.remove()  # リロードの後片付け
    check("リロードの後片付けで、ノードも scriptJob も残らない", not grid_nodes() and not select_jobs())
    s2 = S.FacialSession()
    s2.import_state(state)
    check("リロードの引き継ぎ: 出す・大きさ・クリックの設定が残り、格子が出る", s2.scene_grid.enabled and abs(s2.scene_grid.scale - 3.5) < 1e-9 and not s2.scene_grid.pickable and cmds.objExists("tdFacialGrid_GRP"))
    check("引き継ぎ: クリックで選ばない設定なので scriptJob は無い", not select_jobs())
    s2.scene_grid.remove()
    s2.close()
    s.close()


def sec_05_ui(tmp: Path) -> None:
    """グリッドタブのコントロール（オフスクリーン）。"""
    from maya import cmds

    from tdrive_facial import ui_grid

    ids, s = fresh(tmp)
    tab = ui_grid.GridTab(s)
    tab.resize(480, 900)
    tab.show()
    APP.processEvents()
    check("UI: 「シーンに格子を表示」は最初オフ・「大きさ」は 2.5 倍・「クリックで点を選ぶ」はオン",
          not tab.show_scene_grid.isChecked() and abs(tab.scene_grid_scale.value() - 2.5) < 1e-9 and tab.scene_grid_pick.isChecked() and not grid_nodes())
    check("UI: 文言", tab.show_scene_grid.text() == "シーンに格子を表示" and tab.scene_grid_pick.text() == "クリックで点を選ぶ" and "シーンファイルには保存されません" in tab.show_scene_grid.toolTip())
    tab.show_scene_grid.setChecked(True)
    check("UI: チェックで格子が出る・追従のタイマーが動く", cmds.objExists("tdFacialGrid_GRP") and tab.timer.isActive())
    r0 = s.scene_grid.radius_cm()
    tab.scene_grid_scale.setValue(5.0)
    check("UI: 大きさを変えると半径が変わる", abs(s.scene_grid.radius_cm() - r0 * 2.0) < 0.05, f"{r0} {s.scene_grid.radius_cm()}")
    tab.scene_grid_pick.setChecked(False)
    check("UI: クリックで選ぶを切ると点が選べない表示", not s.scene_grid.pickable and cmds.getAttr(s.scene_grid.marker(0, 0) + ".overrideDisplayType") == 2)
    tab.scene_grid_pick.setChecked(True)
    # 2D のセルのクリックと同じ道: タブの on_cell_clicked が on_pick
    check("UI: 点のクリックの行き先は 2D のセルのクリックと同じ（on_cell_clicked）", s.scene_grid.on_pick == tab.on_cell_clicked)
    cmds.select(clear=True)
    cmds.select(s.scene_grid.marker(1, 2))
    if s.ctx.selection != (1, 2):
        s.scene_grid._on_selection_changed()
    check("UI: 点をクリックするとその点が選ばれ（編集状態）、点は選択に残らない", s.ctx.selection == (1, 2) and not (cmds.ls(selection=True) or []), f"{s.ctx.selection} {cmds.ls(selection=True)}")
    tab.flush()
    APP.processEvents()
    check("UI: 選んだ点が橙", color_of(s.scene_grid.marker(1, 2)) == (1.0, 0.62, 0.11))
    # タブを隠しても格子は頭に追従（タイマーは止まらない）
    tab.hide()
    APP.processEvents()
    check("UI: タブを隠しても、格子を出しているあいだはタイマーが動く", tab.timer.isActive())
    cmds.setAttr("head.translate", 3, 10, 0)
    tab._tick()
    check("UI: 隠れていても格子は頭に追従", math.dist(pos(s.scene_grid.marker(1, 2)), camera_pos(s, 1, 2, s.scene_grid.radius_cm())) < 5e-3)
    tab.show()
    tab.show_scene_grid.setChecked(False)
    check("UI: チェックを外すと消える", not grid_nodes())
    tab.show_scene_grid.setChecked(True)
    tab.detach()
    check("UI: パネルを閉じる（detach）と格子が消える・設定は残る", not grid_nodes() and s.scene_grid.enabled and not select_jobs())
    tab.deleteLater()
    # 開き直すと出る
    tab2 = ui_grid.GridTab(s)
    check("UI: 開き直すと設定どおり格子が出る・チェックも入っている", cmds.objExists("tdFacialGrid_GRP") and tab2.show_scene_grid.isChecked())
    tab2.detach()
    s.scene_grid.set_enabled(False)
    # スクリーンショット（480 px・合成のモデル）
    shot_dir = os.environ.get("TDRIVE_SHOT_DIR")
    if shot_dir:
        cmds.setAttr("head.translate", 0, 10, 0)
        tab3 = ui_grid.GridTab(s)
        tab3.resize(480, 700)
        tab3.show()
        tab3.show_scene_grid.setChecked(True)
        APP.processEvents()
        Path(shot_dir).mkdir(parents=True, exist_ok=True)
        tab3.grab().save(str(Path(shot_dir) / "scene_grid_controls.png"))
        tab3.detach()
        tab3.deleteLater()
    s.close()


SECTIONS = [(n[4:], f) for n, f in sorted(globals().items()) if n.startswith("sec_") and callable(f)]


def main() -> None:
    wanted = sys.argv[1:]
    for name, fn in SECTIONS:
        if wanted and not any(w in name for w in wanted):
            continue
        tmp = Path(tempfile.mkdtemp(prefix="tdrive_sgrid_"))
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
