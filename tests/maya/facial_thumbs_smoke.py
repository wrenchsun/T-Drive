"""FacialController の格子のサムネイル（F2-6）と、土台の表情つきのポーズタブの見た目のスモーク（mayapy・画面なし）。

  set QT_QPA_PLATFORM=offscreen
  "C:/Program Files/Autodesk/Maya2026/bin/mayapy.exe" tests/maya/facial_thumbs_smoke.py

mayapy（batch）にはビューポートが無いので playblast では撮れない。確かめること:
  - 撮れない環境: 「この環境ではサムネイルを作れません」と報告して例外にしない・シーン / カメラ / 選択 / 編集状態は何も変わらない
  - 撮り方を差し替えた（偽の描画）流れ: ポーズを当てる → 一時カメラを点の角度へ → 画像を書く。ユーザーのカメラは動かない・一時カメラは消える・
    選択 / 編集中の値（未保存も）/ 編集状態 / 土台の表情が元へ戻る・Maya の Undo に積まれない・置き場所は一時フォルダ（プロジェクトの外）
  - 鍵: ポーズが変わった点の古いサムネイルは出ない（作り直すと古いファイルは消える）
  - セルの描画: 注入した QPixmap がラベルの後ろに敷かれる（チェックを切ると敷かれない）
  - ビューポートがあれば本物の playblast も試す（mayapy では飛ばす）
スクリーンショットは TDRIVE_UI_SHOT_DIR があればそこへ（グリッドタブ + サムネイル、ポーズタブ + 土台の表情）。
"""

from __future__ import annotations

import os
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

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])  # maya.standalone より先に作る

import maya.standalone  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(cond), detail))


def info(text: str) -> None:
    RESULTS.append((f"INFO {text}", True, ""))


def dark_style() -> None:
    for font in ("meiryo.ttc", "YuGothR.ttc", "msgothic.ttc"):
        fid = QtGui.QFontDatabase.addApplicationFont(f"C:/Windows/Fonts/{font}")
        fams = QtGui.QFontDatabase.applicationFontFamilies(fid) if fid >= 0 else []
        if fams:
            app.setFont(QtGui.QFont(fams[0], 9))
            break
    app.setStyle("Fusion")
    p = QtGui.QPalette()
    for role, color in (
        (QtGui.QPalette.Window, "#444444"), (QtGui.QPalette.WindowText, "#d8d8d8"), (QtGui.QPalette.Base, "#2b2b2b"),
        (QtGui.QPalette.AlternateBase, "#3a3a3a"), (QtGui.QPalette.Text, "#d8d8d8"), (QtGui.QPalette.Button, "#5d5d5d"),
        (QtGui.QPalette.ButtonText, "#e0e0e0"), (QtGui.QPalette.Highlight, "#5285a6"), (QtGui.QPalette.HighlightedText, "#ffffff"),
        (QtGui.QPalette.ToolTipBase, "#333333"), (QtGui.QPalette.ToolTipText, "#ffffff"),
    ):
        p.setColor(role, QtGui.QColor(color))
    app.setPalette(p)


def face_pixmap(color: str, size: int = 128) -> QtGui.QPixmap:
    """顔の代わりの目印の画像（色の地 + 白い目と口）。"""
    img = QtGui.QImage(size, size, QtGui.QImage.Format_RGB32)
    img.fill(QtGui.QColor(color))
    p = QtGui.QPainter(img)
    p.setBrush(QtGui.QColor("#ffffff"))
    p.setPen(QtCore.Qt.NoPen)
    p.drawEllipse(QtCore.QPointF(size * 0.35, size * 0.4), size * 0.07, size * 0.07)
    p.drawEllipse(QtCore.QPointF(size * 0.65, size * 0.4), size * 0.07, size * 0.07)
    p.drawRoundedRect(QtCore.QRectF(size * 0.3, size * 0.65, size * 0.4, size * 0.08), 4, 4)
    p.end()
    return QtGui.QPixmap.fromImage(img)


def run() -> None:
    from maya import cmds

    import facial_fixture
    from tdrive import project
    from tdrive_facial import scene, thumbnails
    from tdrive_facial import session as S
    from tdrive_facial import ui_grid, ui_pose
    from tdrive_facial.core import fcpose_io, thumbs
    from tdrive_facial.core.model import SourcePose

    dark_style()
    shot_dir = os.environ.get("TDRIVE_UI_SHOT_DIR")
    if shot_dir:
        Path(shot_dir).mkdir(parents=True, exist_ok=True)

    tmp = Path(tempfile.mkdtemp(prefix="tdrive_fthumb_"))
    project.set_root(tmp)
    ids = facial_fixture.build_mini_head()
    bs, face = ids["bs"], ids["face"]
    s = S.current()
    s.close()
    s.listeners.clear()
    s.state_listeners.clear()
    grid = ui_grid.GridTab(s)
    pose = ui_pose.PoseTab(s)

    def pump() -> None:
        app.processEvents()
        grid.flush()
        pose.flush()

    def shot(widget: QtWidgets.QWidget, name: str, w: int, h: int) -> None:
        if not shot_dir:
            return
        widget.resize(w, h)
        widget.layout().activate()
        pump()
        widget.grab().save(str(Path(shot_dir) / name))

    path = tmp / "facial" / "mini" / "mini.fcpose.json"
    path.parent.mkdir(parents=True)
    fcpose_io.save(facial_fixture.make_doc(), path)
    s.open(path)
    s.add_to_working_set(curves=["bs.mouth_open", "bs.smile_L", "bs.smile_R", "bs.brow_up"], bones=["eye_L", "eye_R"])
    s.set_mirror(bone_axis="X", suffix_l="_L", suffix_r="_R")
    grid.resize(560, 900)
    pose.resize(560, 900)
    grid.layout().activate()
    pose.layout().activate()
    pump()

    # ------------------------------------------------------------ 撮れない環境（mayapy）
    ok, why = thumbnails.can_capture()
    check("mayapy（batch）ではサムネイルを撮れない（理由つき）", not ok and "batch" in why, why)
    check("ボタン・チェックがある（チェックの既定はオフ）", grid.btn_thumbs.text() == "サムネイルを作り直す" and grid.show_thumbs.text() == "サムネイルを表示" and not grid.show_thumbs.isChecked())

    def same_state(a: dict, b: dict, tol: float = 1e-9) -> bool:
        """シーンの状態の比較（浮動小数の丸めの違い 1e-9 は同じとみなす）。"""
        def flat(x):
            if isinstance(x, dict):
                return [v for k in sorted(x) for v in flat(x[k])]
            if isinstance(x, (list, tuple)):
                return [v for e in x for v in flat(e)]
            return [x]
        fa, fb = flat(a), flat(b)
        return len(fa) == len(fb) and all((abs(p - q) <= tol) if isinstance(p, float) and isinstance(q, float) else p == q for p, q in zip(fa, fb))

    def scene_state() -> dict:
        return {
            "w": {n: cmds.getAttr(f"{bs}.{n}") for n in ("mouth_open", "smile_L", "smile_R", "brow_up")},
            "eye": scene.read_local("eye_L"),
            "persp": (cmds.xform("persp", q=True, ws=True, t=True), cmds.xform("persp", q=True, ws=True, ro=True)),
            "sel": cmds.ls(selection=True, long=True),
            "cams": sorted(cmds.ls(type="camera")),
            "editing": s.editing,
            "undo": cmds.undoInfo(query=True, undoName=True),
        }

    cmds.select(face)  # ユーザーの選択
    before = scene_state()
    rep = thumbnails.capture(s)
    check("撮れない環境: 「この環境ではサムネイルを作れません」と報告する（例外にしない・ok=False・unavailable）",
          not rep.ok and rep.unavailable and "この環境ではサムネイルを作れません" in rep.message and not rep.made, rep.message)
    check("撮れない環境: シーン・カメラ・選択・編集状態は何も変わらない", same_state(scene_state(), before), f"{scene_state()} != {before}")
    grid.btn_thumbs.click()
    pump()
    check("撮れない環境: ボタンを押すと状態の行に理由が出る・落ちない", "この環境ではサムネイルを作れません" in grid.status.text() and not grid.show_thumbs.isChecked(), grid.status.text())

    # ------------------------------------------------------------ 偽の描画で全体の流れ（本物の playblast の代わり）
    seen: list[dict] = []

    def fake_render(cam: str, out: Path, size: int) -> bool:
        w = {n: cmds.getAttr(f"{bs}.{n}") for n in ("mouth_open", "smile_L", "smile_R", "brow_up")}
        persp_t = cmds.xform("persp", q=True, ws=True, t=True)
        seen.append({"cam": cam, "w": w, "persp": persp_t, "editing": s.editing, "exists": cmds.objExists(cam)})
        img = QtGui.QImage(size, size, QtGui.QImage.Format_RGB32)
        img.fill(QtGui.QColor.fromHsv(int(w["mouth_open"] * 200) % 360, 160, 200))
        return img.save(str(out), "PNG")

    grid.thumb_render = fake_render
    s.select_point(1, 2)  # キー: mouth_open 0.6 / smile_L 0.8
    pump()
    s.set_curve("bs.brow_up", 0.35)  # 未保存の編集（編集中の値）
    s.set_base_expression("土台", {"bs.smile_R": 0.7})
    cmds.select(face)
    before = scene_state()
    buf_before = dict(s.pose.curves)
    dirty_before = s.pose.dirty
    doc_before = fcpose_io.to_dict(s.doc)
    n_cams = len(cmds.ls(type="camera"))
    persp_before = cmds.xform("persp", q=True, ws=True, t=True)
    rep = thumbnails.capture(s, render=fake_render)
    layer_pts = [(r, c) for (r, c), pt in s.doc.layers[0].points.items() if not pt.pose.is_empty()]
    check("偽の描画: ポーズのある点の数だけ作る", rep.ok and len(rep.made) == len(layer_pts) == len(seen) and len(layer_pts) >= 4, f"{rep.message} {len(seen)} {layer_pts}")
    check("偽の描画: 一時カメラで撮る（ユーザーのカメラ persp は動かない・撮っている間だけ一時カメラがある）",
          all(x["cam"] != "persp" and x["exists"] and x["persp"] == persp_before for x in seen) and len({x["cam"] for x in seen}) == 1)
    check("偽の描画: 撮るときは基準姿勢へ点のポーズだけが当たっている（編集中の値・土台は写らない。(1,2) は mouth_open 0.6・smile_L 0.8・smile_R 0）",
          any(abs(x["w"]["mouth_open"] - 0.6) < 1e-4 and abs(x["w"]["smile_L"] - 0.8) < 1e-4 and abs(x["w"]["smile_R"]) < 1e-9 and abs(x["w"]["brow_up"]) < 1e-9 for x in seen) and all(x["editing"] for x in seen),
          f"{[x['w'] for x in seen]}")
    check("偽の描画: 一時カメラは消え、カメラの数は元どおり", not cmds.objExists(seen[0]["cam"]) and len(cmds.ls(type="camera")) == n_cams and not cmds.ls("tdFacialThumbCam*"))
    after = scene_state()
    check("偽の描画: 選択・ユーザーのカメラ・編集状態・Maya の Undo の先頭は元どおり", after["sel"] == before["sel"] and after["persp"] == before["persp"] and after["editing"] and after["undo"] == before["undo"] and after["cams"] == before["cams"], f"{after} != {before}")
    check("偽の描画: 編集中の値（未保存の brow_up 0.35）・点の選択・土台の表情・シーンの重みが元へ戻る",
          s.pose.curves == buf_before and s.pose.dirty == dirty_before and s.ctx.selection == (1, 2) and s.base_expression_name == "土台"
          and all(abs(after["w"][k] - before["w"][k]) < 1e-9 for k in before["w"]), f"{after['w']} vs {before['w']} {s.pose.curves}")
    check("偽の描画: 文書は変わらない", fcpose_io.to_dict(s.doc) == doc_before)
    folder = thumbnails.folder_for(s)
    tempdir = Path(tempfile.gettempdir()).resolve()
    check("置き場所: 一時フォルダの下（プロジェクト・リポジトリの中ではない）",
          tempdir in folder.resolve().parents and thumbs.ROOT_NAME in str(folder) and REPO.resolve() not in folder.resolve().parents and tmp.resolve() not in folder.resolve().parents,
          str(folder))
    files = sorted(p.name for p in folder.glob("*.png"))
    check("置き場所: 点ごとにファイルができる（L0_R1_C2_<鍵>.png の形）", len(files) == len(layer_pts) and any(f.startswith("L0_R1_C2_") for f in files), f"{files}")
    check("鍵: 点のサムネイルが見つかる", all(thumbnails.current_thumb(s, 0, r, c) is not None for r, c in layer_pts))

    # 編集状態でなかった場合も元へ戻る
    s.end_edit()
    cmds.select(clear=True)
    before = scene_state()
    rep2 = thumbnails.capture(s, render=fake_render)
    check("編集状態でなかったとき: 撮ったあと編集状態を抜けてシーンは元へ（重み・ジョイント・選択）", rep2.ok and not s.editing and same_state(scene_state(), before), f"{scene_state()} {before}")

    # 失敗した点があっても続ける・一時カメラは消える
    calls = {"n": 0}

    def flaky(cam: str, out: Path, size: int) -> bool:
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("描けない")
        return fake_render(cam, out, size)

    rep3 = thumbnails.capture(s, render=flaky)
    check("1 点の失敗: 残りは作る・失敗は報告・一時カメラは消える", rep3.ok and len(rep3.failed) == 1 and len(rep3.made) == len(layer_pts) - 1 and not cmds.ls("tdFacialThumbCam*") and not s.editing, f"{rep3.message} {rep3.failed}")

    # 撮れる環境で全部の点が書けなかった: 「この環境では…」とは言わない（2026-10-05）
    rep4 = thumbnails.capture(s, render=lambda cam, out, size: False)
    check("全点で書けなかった: 「サムネイルを作れませんでした」（「この環境では」ではない・unavailable でない）",
          not rep4.ok and not rep4.unavailable and rep4.message.startswith("サムネイルを作れませんでした") and "この環境では" not in rep4.message, rep4.message)

    # ------------------------------------------------------------ 古いサムネイルは出ない
    thumbnails.capture(s, render=fake_render)
    k_old = thumbnails.point_key(s, 0, 1, 2)
    p_old = thumbnails.current_thumb(s, 0, 1, 2)
    s.select_point(1, 2)
    s.set_curve("bs.smile_L", 0.5)
    s.save_point()  # ポーズを変えて保存
    check("鍵: ポーズが変わった点の古いサムネイルは出ない（ほかの点は出る）", thumbnails.current_thumb(s, 0, 1, 2) is None and thumbnails.point_key(s, 0, 1, 2) != k_old and thumbnails.current_thumb(s, 0, 1, 0) is not None)
    grid.show_thumbs.setChecked(True)
    pump()
    check("セル: 古い点には敷かず、新しい点には敷く（読み込み）", grid.thumb_for(1, 2) is None and grid.thumb_for(1, 0) is not None and not grid.thumb_for(1, 0).isNull())
    thumbnails.capture(s, render=fake_render)
    check("作り直すと古いファイルは消える（同じ点は 1 枚だけ）", p_old is not None and not p_old.exists() and len([f for f in folder.glob("L0_R1_C2_*.png")]) == 1)
    n_before = len(list(folder.glob("*.png")))
    check("鍵: 作り直したあとは出る", thumbnails.current_thumb(s, 0, 1, 2) is not None and n_before == len(layer_pts))
    s.undo()
    s.end_edit()
    grid.show_thumbs.setChecked(False)

    # ------------------------------------------------------------ セルの描画（注入した QPixmap）
    s.select_point(1, 2)
    s.end_edit()
    pump()
    cv = grid.canvas
    v = grid.current_view()

    def cell_img(row: int, col: int) -> QtGui.QImage:
        cv._hover = None
        img = cv.grab().toImage()
        r = cv.cell_rect(v, v.display_rows.index(row), col)
        return img, r

    def px(row: int, col: int, dx: float, dy: float) -> QtGui.QColor:
        img, r = cell_img(row, col)
        return img.pixelColor(int(r.left() + r.width() * dx), int(r.top() + r.height() * dy))

    plain = px(1, 2, 0.2, 0.25)
    grid.load_thumbnails = lambda: {(1, 2): face_pixmap("#c04060"), (1, 0): face_pixmap("#4060c0"), (0, 2): face_pixmap("#40c060")}
    grid.show_thumbs.setChecked(True)
    pump()
    with_thumb = px(1, 2, 0.2, 0.25)
    check("セル: チェックを入れると注入した画像が敷かれる（色が地の色から変わる）", with_thumb != plain and with_thumb.red() > with_thumb.green(), f"{plain.name()} -> {with_thumb.name()}")
    empty_cell = px(0, 0, 0.2, 0.25)
    check("セル: 画像の無い点は今までの色のまま（注入しなかった (0,0) は空の灰）", empty_cell == QtGui.QColor(ui_grid.FILL[ui_grid.STATE_EMPTY]), empty_cell.name())
    img_c, rc = cell_img(1, 2)
    white = 0
    for yy in range(int(rc.top() + rc.height() * 0.70), int(rc.bottom() - 4)):  # 下寄りのラベルの帯（画像の上に白い文字が描かれている）
        for xx in range(int(rc.left() + rc.width() * 0.3), int(rc.left() + rc.width() * 0.7)):
            c = img_c.pixelColor(xx, yy)
            if c.red() > 200 and c.green() > 200 and c.blue() > 200:
                white += 1
    check("セル: ラベル（R1 C2）は画像の後ろではなく上に描かれる（画像の下部に白い文字の画素がある）", white >= 10, f"white={white}")
    grid.show_thumbs.setChecked(False)
    pump()
    check("セル: チェックを切ると敷かれない（元の色）", px(1, 2, 0.2, 0.25) == plain)
    grid.show_thumbs.setChecked(True)
    pump()
    shot(grid, "grid_thumbnails.png", 560, 900)
    grid.show_thumbs.setChecked(False)
    del grid.load_thumbnails  # 注入を外す

    # ------------------------------------------------------------ 本物の playblast（ビューポートがあるときだけ）
    ok, why = thumbnails.can_capture()
    if ok:
        real = thumbnails.capture(s)
        check("本物の playblast: 撮れる", real.ok and len(real.made) > 0, real.message)
    else:
        info(f"本物の playblast は飛ばす（{why}）。実機の Maya で「サムネイルを作り直す」を確認すること")

    # ------------------------------------------------------------ ポーズタブ + 土台の表情（スクリーンショット）
    s.select_point(1, 0)  # smile_R 1.0 / brow_up 0.5
    pump()
    s.set_base_expression("base_happy", {"bs.smile_R": 0.7, "bs.brow_up": 0.4, "bs.mouth_open": 0.5})
    s.set_working_set(curves=["bs.smile_L", "bs.smile_R", "bs.brow_up"], bones=["eye_L"])  # mouth_open は一覧に出ない → 「土台の表情だけのシェイプ」
    pump()
    pose.refresh()
    pose.base_only_btn.setChecked(False)
    check("ポーズタブ: 一覧に出ない土台のシェイプは折りたたみの 1 行（本数・名前はツールチップ）",
          not pose.base_only_btn.isHidden() and "土台の表情だけのシェイプ（1 本）" in pose.base_only_btn.text() and "bs.mouth_open" in pose.base_only_btn.toolTip() and pose.base_only_list.isHidden(), pose.base_only_btn.text())
    pose.base_only_btn.setChecked(True)
    check("ポーズタブ: 開くと名前と値が読み取り専用で出る", not pose.base_only_list.isHidden() and "bs.mouth_open +0.50" in pose.base_only_list.text(), pose.base_only_list.text())
    check("ポーズタブ: 可動域を超えた行（smile_R 1.0 + 0.7）は警告・(brow_up 0.5 + 0.4 = 0.9) は警告なし",
          "bs.smile_R" in pose.base_warn.text() and "ffb74d" in pose._curve_rows["bs.smile_R"].label.styleSheet() and "ffb74d" not in pose._curve_rows["bs.brow_up"].label.styleSheet()
          and pose._curve_rows["bs.brow_up"].base_label.text() == "+0.40（土台）", pose.base_warn.text())
    shot(pose, "pose_base_expression.png", 560, 900)

    s.end_edit()
    pose.detach()
    grid.detach()
    s.close()
    shutil.rmtree(folder, ignore_errors=True)
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
