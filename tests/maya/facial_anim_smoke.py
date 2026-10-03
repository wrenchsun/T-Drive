"""FacialController のアニメ読み込み・他のデータからコピーのスモーク（mayapy・画面なし）。

  set QT_QPA_PLATFORM=offscreen
  "C:/Program Files/Autodesk/Maya2026/bin/mayapy.exe" tests/maya/facial_anim_smoke.py

確かめること（tdrive_facial.anim_import とポーズタブ・グリッドタブのダイアログ）:
  - Unity の .anim（このスモークが書く合成のファイル）→ ポーズ（シェイプ・ボーン・未対応の報告・置き換え / 足す・作業セットだけ）
  - 今のフレーム（アニメの付いたシーン）→ ポーズ。読んだあとシーン（姿勢・時刻・接続）は変わらない
  - 土台の表情（加算。2026-10-03）: シーンに「土台 + ポーズ」が当たる・可動域を超えても丸めず報告する・ポーズ / 文書には入らない・
    取り込みは土台を引く（取り込む → 保存 → 当て直すが元に戻る）・点を切り替えても残る・編集を終えると外れる・ベイクに入らない
    ・ポーズタブに「+0.60（土台）」と警告が出る
  - 他のデータからコピー: 旗の組み合わせごと・元に戻す
  - 任意（assets/shizuku/facial_anims があるときだけ。ファイルはコピーも保存もしない）: 実データの .anim が全部読める・shizuku.mb（開くだけ）へ読み込む
ファイルはすべて一時フォルダ（プロジェクトのルートもそこへ向ける）。
"""

from __future__ import annotations

import math
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

from PySide6 import QtWidgets  # noqa: E402

APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv[:1])  # maya.standalone より先に作る

import maya.standalone  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(cond), detail))


def info(text: str) -> None:
    RESULTS.append((f"INFO {text}", True, ""))


def near(a, b, tol=1e-4) -> bool:
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def qnear(a, b, tol=1e-4) -> bool:
    return near(a, b, tol) or near(a, [-x for x in b], tol)


# ---------------------------------------------------------------------------
# 合成の .anim（Unity の書式）
# ---------------------------------------------------------------------------


def _keys_text(keys: list[tuple[float, object]], indent: str) -> str:
    out = []
    for t, v in keys:
        if isinstance(v, (tuple, list)):
            comps = "xyzw"[: len(v)]
            val = "{" + ", ".join(f"{c}: {x!r}" for c, x in zip(comps, v)) + "}"
        else:
            val = repr(v)
        out.append(f"{indent}- serializedVersion: 3\n{indent}  time: {t!r}\n{indent}  value: {val}\n")
    return "".join(out)


def write_anim(path: Path, name: str, floats: dict[str, object], transforms: list[tuple[str, str, tuple]] | None = None, mesh: str = "mini_face") -> Path:
    """floats: {シェイプ名（`bs.mouth_open`）: 値 0〜100 または [(時刻, 値), ...]}。transforms: [(種類, パス, 値の組), ...]。"""
    sections = {"m_RotationCurves": [], "m_EulerCurves": [], "m_PositionCurves": [], "m_ScaleCurves": []}
    kinds = {"rotation": "m_RotationCurves", "euler": "m_EulerCurves", "position": "m_PositionCurves", "scale": "m_ScaleCurves"}
    for kind, bone_path, value in transforms or []:
        keys = value if isinstance(value, list) else [(0, value)]
        sections[kinds[kind]].append(
            "  - curve:\n      serializedVersion: 2\n      m_Curve:\n" + _keys_text(keys, "      ")
            + f"      m_PreInfinity: 2\n      m_PostInfinity: 2\n      m_RotationOrder: 4\n    path: {bone_path}\n"
        )
    text = "%YAML 1.1\n%TAG !u! tag:unity3d.com,2011:\n--- !u!74 &7400000\nAnimationClip:\n  m_ObjectHideFlags: 0\n"
    text += f"  m_Name: {name}\n  serializedVersion: 6\n  m_Legacy: 0\n  m_CompressedRotationCurves: []\n"
    for sec, items in sections.items():
        text += f"  {sec}:" + ("\n" + "".join(items) if items else " []\n")
    text += "  m_FloatCurves:\n"
    for shape, v in floats.items():
        keys = v if isinstance(v, list) else [(0, v)]
        text += (
            "  - curve:\n      serializedVersion: 2\n      m_Curve:\n" + _keys_text(keys, "      ")
            + f"      m_PreInfinity: 2\n      m_PostInfinity: 2\n      m_RotationOrder: 4\n    attribute: blendShape.{shape}\n    path: {mesh}\n    classID: 137\n    script: {{fileID: 0}}\n"
        )
    text += "  m_PPtrCurves: []\n  m_SampleRate: 60\n  m_Events: []\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


# ---------------------------------------------------------------------------
# 本体
# ---------------------------------------------------------------------------


def run() -> None:
    from maya import cmds

    import facial_fixture
    from tdrive import project
    from tdrive_facial import anim_import
    from tdrive_facial import scene
    from tdrive_facial import session as S
    from tdrive_facial import ui_grid, ui_pose
    from tdrive_facial.core import autofill, fcpose_io, space, unity_anim
    from tdrive_facial.core.model import BoneOffset, GridPoint, Layer, PoseDocument, SourcePose

    tmp = Path(tempfile.mkdtemp(prefix="tdrive_fanim_"))
    project.set_root(tmp)
    ids = facial_fixture.build_mini_head()
    bs = ids["bs"]
    s = S.current()
    s.close()
    s.listeners.clear()
    s.state_listeners.clear()

    grid = ui_grid.GridTab(s)
    pose = ui_pose.PoseTab(s)

    def pump() -> None:
        APP.processEvents()
        grid.flush()
        pose.flush()

    doc0 = facial_fixture.make_doc()  # 3 x 3 の格子・キー 4 点・Joy レイヤー
    p0 = tmp / "dst" / "mini.fcpose.json"
    p0.parent.mkdir()
    fcpose_io.save(doc0, p0)
    s.open(p0)
    s.add_to_working_set(curves=["bs.mouth_open", "bs.smile_L", "bs.smile_R", "bs.brow_up"], bones=["eye_L", "eye_R"])
    s.set_mirror(bone_axis="X", suffix_l="_L", suffix_r="_R")
    pump()
    check("準備: ボタンがある（アニメから読み込む / 土台の表情 / 他のデータからコピー）",
          pose.btn_anim.text() == "アニメから読み込む…" and pose.btn_base_pick.text() == "選ぶ…" and pose.btn_base_clear.text() == "外す"
          and grid.btn_copy_from.text() == "他のデータからコピー…")
    check("準備: 点を選ぶ前は読み込みも土台も押せない", not pose.btn_anim.isEnabled() and not pose.btn_base_pick.isEnabled())
    rep0 = anim_import.pose_from_unity_anim(s, write_anim(tmp / "a" / "x.anim", "x", {"bs.mouth_open": 50}))
    check("点が無いときは読み込みが失敗として返る（落ちない）", not rep0.ok and rep0.code == "no_point", rep0.message)

    # ------------------------------------------------------------ 合成 .anim → ポーズ（ポーズタブのダイアログ）
    s.select_point(1, 1)
    pump()
    check("点を選ぶと読み込みボタンが押せる", pose.btn_anim.isEnabled() and pose.btn_base_pick.isEnabled() and not pose.btn_base_clear.isEnabled())
    base_l = scene.read_local("eye_L")  # バインドポーズ（まだ何も動かしていない）
    m2u = space.converter(space.MAYA, space.UNITY)
    h = math.radians(20.0) / 2
    q_off = (0.0, math.sin(h), 0.0, math.cos(h))  # 親の空間での Y 軸まわり 20°
    t_off = (0.1, 0.0, 0.05)
    target_t = tuple(b + o for b, o in zip(base_l[0], t_off))
    target_q = scene.quat_mul(q_off, base_l[1])
    u_pos = m2u.position(target_t)
    u_rot = m2u.quaternion(target_q)
    anim1 = write_anim(
        tmp / "anims" / "face_test.anim",
        "face_test",
        {"bs.mouth_open": 80, "bs.smile_L": 40, "BS.SMILE_r": 25, "bs.no_such_shape": 100, "bs.zero_shape": 0},
        [
            ("position", "root/head/eye_L", u_pos),
            ("rotation", "root/head/eye_L", u_rot),
            ("rotation", "root/head/no_such_bone", (0.0, 0.0, 0.0, 1.0)),
        ],
    )

    def dialog_answer(path=None, ws=None, bones=None, add=None, time=None):
        def run_dialog(dlg) -> bool:
            if path is not None:
                dlg.set_path(str(path))
            if ws is not None:
                dlg.cb_ws.setChecked(ws)
            if bones is not None:
                dlg.cb_bones.setChecked(bones)
            if add is not None:
                dlg.rb_add.setChecked(add)
                dlg.rb_replace.setChecked(not add)
            if time is not None:
                dlg.time.setValue(time)
            return True

        return run_dialog

    dlg = pose.make_anim_dialog()
    o = dlg.options()
    check("ダイアログ: 初期値は「今のフレーム」・置き換え・ボーンも読む", o["source"] == "frame" and o["replace"] and o["include_bones"] and not dlg.path_row.isEnabled())
    dlg.set_path(str(anim1))
    check("ダイアログ: ファイルを指定すると .anim の選択になり、時刻を指定できる", dlg.options()["source"] == "file" and dlg.time.isEnabled() and dlg.options()["path"] == str(anim1))
    dlg.deleteLater()

    pose.run_dialog = dialog_answer(anim1, ws=False)
    pose.btn_anim.click()
    pump()
    c = s.pose.curves
    check(".anim → ポーズ: シェイプが 0〜1 で入る", abs(c.get("bs.mouth_open", 0) - 0.8) < 1e-6 and abs(c.get("bs.smile_L", 0) - 0.4) < 1e-6, f"{c}")
    check(".anim → ポーズ: 大文字小文字違いの名前も照合される", abs(c.get("bs.smile_R", 0) - 0.25) < 1e-6, f"{c}")
    check(".anim → ポーズ: 0 の値・シーンに無いシェイプは入らない", "bs.no_such_shape" not in c and "bs.zero_shape" not in c)
    off = s.pose.bones.get("eye_L")
    check(".anim → ポーズ: ボーンは基準姿勢からのずれ（位置 +(0.1, 0, 0.05)・回転 Y 20°）に直る",
          off is not None and near(off.t, t_off, 1e-4) and qnear(off.r, q_off, 1e-4), f"{off}")
    check(".anim → ポーズ: 状態の行に未対応のシェイプ・ボーンが出る",
          "bs.no_such_shape" in pose.status.toolTip() and "no_such_bone" in pose.status.toolTip() and "読み込みました" in pose.status.text(), f"{pose.status.text()} / {pose.status.toolTip()}")
    check(".anim → ポーズ: シーンへ当たる（編集状態・ウェイプ・ジョイント）",
          s.editing and abs(cmds.getAttr(f"{bs}.mouth_open") - 0.8) < 1e-4 and near(scene.read_local("eye_L")[0], target_t, 1e-4) and qnear(scene.read_local("eye_L")[1], target_q, 1e-4),
          f"{cmds.getAttr(bs + '.mouth_open')} {scene.read_local('eye_L')}")
    check(".anim → ポーズ: 未保存の編集になる（文書は変わらない）", s.pose.dirty and (1, 1) not in s.doc.layers[0].points)

    # 足す / ボーンを読まない / 作業セットだけ
    s.set_curve("bs.brow_up", 0.5)
    anim2 = write_anim(tmp / "anims" / "only_mouth.anim", "only_mouth", {"bs.mouth_open": 30})
    pose.ask_confirm = lambda text: True
    pose.run_dialog = dialog_answer(anim2, add=True)
    pose.btn_anim.click()
    pump()
    check("足す: クリップが動かすシェイプだけ上書きし、ほかの編集中の値は残る",
          abs(s.pose.curves["bs.mouth_open"] - 0.3) < 1e-6 and abs(s.pose.curves["bs.brow_up"] - 0.5) < 1e-6 and abs(s.pose.curves["bs.smile_L"] - 0.4) < 1e-6 and "eye_L" in s.pose.bones, f"{s.pose.curves} {pose.status.text()}")
    pose.run_dialog = dialog_answer(anim2, bones=False)
    pose.btn_anim.click()
    pump()
    check("置き換え + ボーンを読まない: シェイプは置き換わり、編集中のボーンは残る",
          set(s.pose.curves) == {"bs.mouth_open"} and "eye_L" in s.pose.bones, f"{s.pose.curves} {list(s.pose.bones)}")
    asked: list[str] = []
    pose.ask_confirm = lambda text: (asked.append(text), False)[1]
    pose.run_dialog = dialog_answer(anim1)
    pose.btn_anim.click()
    check("置き換え: 未保存の編集があれば確認し、いいえなら何も変えない", len(asked) == 1 and set(s.pose.curves) == {"bs.mouth_open"} and "取りやめ" in pose.status.text(), pose.status.text())
    pose.ask_confirm = lambda text: True
    s.set_working_set(curves=["bs.mouth_open"], bones=[])
    pose.run_dialog = dialog_answer(anim1, ws=True)
    pose.btn_anim.click()
    pump()
    check("作業セットだけ: 外のシェイプは読まない", set(s.pose.curves) == {"bs.mouth_open"} and abs(s.pose.curves["bs.mouth_open"] - 0.8) < 1e-6, f"{s.pose.curves}")
    check("作業セットだけ: 読まなかった名前が状態の行に出る", "作業セットの外" in pose.status.text(), pose.status.text())
    s.set_working_set(curves=["bs.mouth_open", "bs.smile_L", "bs.smile_R", "bs.brow_up"], bones=["eye_L", "eye_R"])

    bad = tmp / "anims" / "broken.anim"
    bad.write_text("{ not unity }", encoding="utf-8")
    pose.run_dialog = dialog_answer(bad)
    pose.btn_anim.click()
    check("壊れたファイルは状態の行にエラーを出す（落ちない）", "読めませんでした" in pose.status.text(), pose.status.text())
    pose.run_dialog = dialog_answer(tmp / "anims" / "missing.anim")
    pose.btn_anim.click()
    check("無いファイルもエラーを出す", "開けませんでした" in pose.status.text(), pose.status.text())

    # ARKit の名前（R-26）: 名前の表で置き換える
    anim_ar = write_anim(tmp / "anims" / "arkit.anim", "arkit", {"jawOpen": 60, "mouthSmileLeft": 20, "other": 5})
    s.pose.set_curve("bs.mouth_open", 0.0)
    rep = anim_import.pose_from_unity_anim(s, anim_ar, name_mapping={"jawOpen": "bs.mouth_open", "mouthSmileLeft": "bs.smile_L"}, include_bones=False)
    check("ARKit 名: 表で置き換えて読む・表に無い名前は未対応に出る",
          rep.ok and abs(s.pose.curves.get("bs.mouth_open", 0) - 0.6) < 1e-6 and abs(s.pose.curves.get("bs.smile_L", 0) - 0.2) < 1e-6 and rep.unmatched_curves == ["other"], f"{s.pose.curves} {rep.unmatched_curves}")

    # 保存すると点に書かれる（ここまでは文書が変わらない）
    s.reload_pose()
    s.end_edit()
    check("端: 読み直して編集を終えると、シーンのウェイプ・ジョイントは元へ戻る",
          abs(cmds.getAttr(f"{bs}.mouth_open")) < 1e-9 and near(scene.read_local("eye_L")[0], base_l[0]) and qnear(scene.read_local("eye_L")[1], base_l[1]))

    # ------------------------------------------------------------ 今のフレーム（アニメの付いたシーン）
    cmds.setKeyframe(f"{bs}.mouth_open", v=0.0, t=1, itt="linear", ott="linear")
    cmds.setKeyframe(f"{bs}.mouth_open", v=1.0, t=10, itt="linear", ott="linear")
    cmds.setKeyframe("eye_L", at="rotateX", v=0.0, t=1, itt="linear", ott="linear")
    cmds.setKeyframe("eye_L", at="rotateX", v=40.0, t=10, itt="linear", ott="linear")
    cmds.currentTime(5)
    s.select_point(1, 1)
    s.end_edit()  # 選択だけ残して、編集状態を抜ける（アニメの状態のシーン）
    cur_w = cmds.getAttr(f"{bs}.mouth_open")
    cur_local = scene.read_local("eye_L")
    check("前提: アニメが評価されている（フレーム 5）", abs(cur_w - 4.0 / 9.0) < 1e-4 and not s.editing, f"{cur_w}")
    conn_before = cmds.listConnections(f"{bs}.mouth_open", source=True, destination=False)
    rep = anim_import.pose_from_current_frame(s, frame=7, apply=False)
    check("今のフレーム: 指定したフレームの値を読む・シェイプ", rep.ok and abs(s.pose.curves.get("bs.mouth_open", 0) - 6.0 / 9.0) < 1e-4, f"{rep.message} {s.pose.curves}")
    off = s.pose.bones.get("eye_L")
    check("今のフレーム: ボーンはバインドポーズからのずれ（X 軸の回転）", off is not None and abs(ui_pose.quat_to_euler_xyz(off.r)[0]) > 5.0, f"{off}")
    check("今のフレーム: 読んだあとシーンは変わらない（時刻・ウェイプ・ジョイント・接続・編集状態）",
          cmds.currentTime(query=True) == 5 and abs(cmds.getAttr(f"{bs}.mouth_open") - cur_w) < 1e-9 and near(scene.read_local("eye_L")[0], cur_local[0]) and qnear(scene.read_local("eye_L")[1], cur_local[1])
          and cmds.listConnections(f"{bs}.mouth_open", source=True, destination=False) == conn_before and not s.editing)
    pose.run_dialog = dialog_answer()  # 既定: 今のフレーム
    pose.ask_confirm = lambda text: True
    pose.btn_anim.click()
    pump()
    check("今のフレーム（ダイアログ）: 今のフレーム 5 の値・編集状態へ入ってシーンへ当たる",
          abs(s.pose.curves.get("bs.mouth_open", 0) - 4.0 / 9.0) < 1e-4 and s.editing and abs(cmds.getAttr(f"{bs}.mouth_open") - cur_w) < 1e-4
          and qnear(scene.read_local("eye_L")[1], cur_local[1], 1e-4), f"{pose.status.text()} {s.pose.curves}")
    s.reload_pose()
    s.end_edit()
    check("今のフレーム: 編集を終えるとアニメが戻る（接続が復元）", cmds.listConnections(f"{bs}.mouth_open", source=True, destination=False) == conn_before and abs(cmds.getAttr(f"{bs}.mouth_open") - cur_w) < 1e-9)
    anims = cmds.ls(type="animCurve")
    if anims:
        cmds.delete(anims)
    cmds.setAttr(f"{bs}.mouth_open", 0.0)
    cmds.setAttr("eye_L.rotateX", 0.0)
    cmds.currentTime(1)

    # ------------------------------------------------------------ 土台の表情
    base_anim = write_anim(tmp / "anims" / "base_happy.anim", "base_happy", {"bs.smile_R": 70, "bs.brow_up": 90, "bs.no_such_shape": 100})
    s.select_point(1, 2)  # キー: mouth_open 0.6 / smile_L 0.8
    pump()
    doc_before = fcpose_io.to_dict(s.doc)
    pose_before = dict(s.pose.curves)
    pose.run_dialog = lambda dlg: (dlg.set_path(str(base_anim)), True)[1]
    pose.btn_base_pick.click()
    pump()
    check("土台: ラベルに名前が出て、外すボタンが押せる", "base_happy" in pose.base_label.text() and pose.btn_base_clear.isEnabled(), pose.base_label.text())
    check("土台: シーンに当たる（smile_R 0.7・brow_up 0.9）", abs(cmds.getAttr(f"{bs}.smile_R") - 0.7) < 1e-4 and abs(cmds.getAttr(f"{bs}.brow_up") - 0.9) < 1e-4, f"{cmds.getAttr(bs + '.smile_R')} {cmds.getAttr(bs + '.brow_up')}")
    check("土台: 編集中のポーズ・文書には入らない", s.pose.curves == pose_before and fcpose_io.to_dict(s.doc) == doc_before and not s.pose.dirty, f"{s.pose.curves}")
    check("土台: ポーズのシェイプは今までどおりシーンに出る", abs(cmds.getAttr(f"{bs}.mouth_open") - 0.6) < 1e-4)
    check("土台: 状態の行に当てた旨・存在しないシェイプは未対応", "土台の表情" in pose.status.text() and "bs.no_such_shape" in pose.status.toolTip(), f"{pose.status.text()} {pose.status.toolTip()}")
    pose.btn_capture.click()
    pump()
    check("土台: 「シーンから取り込む」は土台のシェイプを取り込まない（ポーズのシェイプ・値は残る）",
          "bs.smile_R" not in s.pose.curves and "bs.brow_up" not in s.pose.curves and abs(s.pose.curves.get("bs.mouth_open", 0) - 0.6) < 1e-4 and "土台" in pose.status.text(), f"{s.pose.curves} {pose.status.text()}")
    cmds.setAttr(f"{bs}.smile_L", 0.0)  # 土台に無いシェイプをシーンで変えたら取り込む
    cmds.setAttr(f"{bs}.mouth_open", 0.25)
    pose.btn_capture.click()
    check("土台: 土台に無いシェイプの変更は取り込む", abs(s.pose.curves.get("bs.mouth_open", 0) - 0.25) < 1e-4 and "bs.smile_L" not in s.pose.curves, f"{s.pose.curves}")
    s.reload_pose()
    s.select_point(1, 0)  # キー: smile_R 1.0 / brow_up 0.5（土台と同じシェイプをポーズが持つ）
    pump()
    # ※ 2026-10-03 に挙動が変わった（以前: 同じシェイプはポーズの値が優先 → 今: 土台 + ポーズの合計をそのまま当てる）
    check("土台: 点を切り替えても残る・同じシェイプは土台 + ポーズの合計が当たる（smile_R 0.7+1.0・brow_up 0.9+0.5。丸めない）",
          abs(cmds.getAttr(f"{bs}.smile_R") - 1.7) < 1e-4 and abs(cmds.getAttr(f"{bs}.brow_up") - 1.4) < 1e-4 and anim_import.base_expression_name(s) == "base_happy",
          f"{cmds.getAttr(bs + '.smile_R')} {cmds.getAttr(bs + '.brow_up')}")
    st = s.base_state()
    check("土台: 可動域（0〜1）を超えたシェイプが報告される（smile_R 1.7・brow_up 1.4）・共通のシェイプは shared に土台の値",
          set(st.over_limit) == {"bs.smile_R", "bs.brow_up"} and abs(st.over_limit["bs.smile_R"] - 1.7) < 1e-6 and set(st.shared) == {"bs.smile_R", "bs.brow_up"}
          and abs(st.shared["bs.smile_R"] - 0.7) < 1e-6, f"{st}")
    pose.flush()
    rows = pose._curve_rows
    check("ポーズタブ: 行は今までどおりポーズの値を出し、「+0.70（土台）」の小さなラベルが付く",
          abs(rows["bs.smile_R"].spin.value() - 1.0) < 1e-6 and rows["bs.smile_R"].base_label.text() == "+0.70（土台）" and not rows["bs.smile_R"].base_label.isHidden()
          and "bs.mouth_open" in rows and rows["bs.mouth_open"].base_label.isHidden(), f"{rows['bs.smile_R'].base_label.text()}")
    check("ポーズタブ: 可動域を超えた行は警告の色・ツールチップに「土台と合わせて可動域を超えています（1.70）」",
          "ffb74d" in rows["bs.smile_R"].label.styleSheet() and "土台と合わせて可動域を超えています（1.70）" in rows["bs.smile_R"].base_label.toolTip()
          and "土台と合わせて可動域を超えています（1.70）" in rows["bs.smile_R"].label.toolTip() and "ffb74d" not in rows["bs.mouth_open"].label.styleSheet())
    check("ポーズタブ: 可動域を超えたシェイプの一覧が土台の行の下に出る", not pose.base_warn.isHidden() and "bs.smile_R" in pose.base_warn.text(), pose.base_warn.text())
    check("ポーズタブ: 土台の説明文", pose.base_note.text() == "表情を下敷きとして当てます。ポーズの値に足して表示されます（データには入りません）", pose.base_note.text())
    s.select_point(0, 2)  # キー: mouth_open 0.3 / eye_L（smile_R・brow_up は持たない）
    pump()
    check("土台: ポーズが持たないシェイプは土台の値に戻る", abs(cmds.getAttr(f"{bs}.smile_R") - 0.7) < 1e-4 and abs(cmds.getAttr(f"{bs}.brow_up") - 0.9) < 1e-4 and abs(cmds.getAttr(f"{bs}.mouth_open") - 0.3) < 1e-4)
    s.set_curve("bs.smile_R", 0.2)  # スライダー相当: 土台に足される
    check("土台: スライダーで同じシェイプを動かすと土台に足した値が出る（0.7+0.2。可動域内なので警告なし）",
          abs(cmds.getAttr(f"{bs}.smile_R") - 0.9) < 1e-4 and "bs.smile_R" not in s.base_state().over_limit)
    s.set_curve("bs.smile_R", 0.0)
    check("土台: 0 に戻すと土台の値だけが出る", abs(cmds.getAttr(f"{bs}.smile_R") - 0.7) < 1e-4)
    s.reload_pose()
    layer_sel = s.set_active_layer(1)
    pump()
    check("土台: レイヤーを切り替えても残る", layer_sel is not None and abs(cmds.getAttr(f"{bs}.brow_up") - 0.9) < 1e-4, f"{cmds.getAttr(bs + '.brow_up')}")
    s.set_active_layer(0)

    # ---- 取り込み → 保存 → 当て直し（土台つき）で元に戻る
    s.select_point(1, 0)  # ポーズ: smile_R 1.0 / brow_up 0.5（土台 smile_R 0.7 / brow_up 0.9）
    pump()
    cmds.setAttr(f"{bs}.smile_R", 1.3)  # スライダーを動かした相当: ポーズ 0.6 + 土台 0.7
    cmds.setAttr(f"{bs}.brow_up", 1.4 + 0.0)  # ポーズ 0.5 + 土台 0.9 のまま
    cmds.setAttr(f"{bs}.smile_L", 0.35)  # 土台に無いシェイプ
    res = anim_import.capture_from_scene(s, working_set_only=False)
    check("土台つきの取り込み: シーンの値 − 土台の値がポーズの値になる（smile_R 0.6・brow_up 0.5・smile_L 0.35）",
          res.report.ok and abs(s.pose.curves.get("bs.smile_R", 0) - 0.6) < 1e-4 and abs(s.pose.curves.get("bs.brow_up", 0) - 0.5) < 1e-4
          and abs(s.pose.curves.get("bs.smile_L", 0) - 0.35) < 1e-4, f"{s.pose.curves}")
    s.save_point()
    saved = dict(s.doc.layers[0].points[(1, 0)].pose.curves)
    check("土台つきの保存: 点には土台を含まない値が書かれる", abs(saved.get("bs.smile_R", 0) - 0.6) < 1e-4 and abs(saved.get("bs.smile_L", 0) - 0.35) < 1e-4 and "bs.mouth_open" not in saved, f"{saved}")
    s.select_point(0, 2)
    s.select_point(1, 0)  # 当て直し
    check("土台つきの当て直し: 元のシーンの値に戻る（smile_R 1.3 = 可動域を超えても丸めない・smile_L 0.35）",
          abs(cmds.getAttr(f"{bs}.smile_R") - 1.3) < 1e-4 and abs(cmds.getAttr(f"{bs}.smile_L") - 0.35) < 1e-4 and abs(cmds.getAttr(f"{bs}.brow_up") - 1.4) < 1e-4,
          f"{cmds.getAttr(bs + '.smile_R')} {cmds.getAttr(bs + '.smile_L')}")
    # 土台のシェイプが変わっていなければ取り込まない（土台のまま）
    s.select_point(0, 2)
    res = anim_import.capture_from_scene(s, working_set_only=False)
    check("土台つきの取り込み: 土台のとおりのシェイプは取り込まず、報告に出る",
          "bs.smile_R" not in s.pose.curves and "bs.brow_up" not in s.pose.curves and set(res.base_ignored) == {"bs.smile_R", "bs.brow_up"}, f"{s.pose.curves} {res.base_ignored}")
    s.undo()  # 保存を戻す（文書を元へ）
    s.select_point(1, 0)

    # ---- ベイクに土台は入らない（土台を当てた状態で焼いても、無い状態と同じ FC_ の差分）
    import numpy as np

    def fc_deltas() -> dict:
        out = {}
        for t in scene.fc_targets(ids["face"]):
            d = scene.read_target_delta(t.node, t.alias)
            out[t.alias] = None if d is None else (list(d[0]), np.round(d[1], 6))
        return out

    face_name = ids["face"]
    s.clear_base_expression()
    s.end_edit()
    rep_a = s.bake_point(1, 2)
    plain = fc_deltas()
    cmds.undo()  # ベイクを戻す（Maya の Undo 1 区切り）
    anim_import.set_base_expression(s, {"bs.smile_R": 0.7, "bs.brow_up": 0.9, "bs.mouth_open": 0.4})
    check("ベイク前提: 土台が当たっている（選択中の点 (1,0) の smile_R 1.0 に土台 0.7 が足される）", s.editing and abs(cmds.getAttr(f"{bs}.smile_R") - 1.7) < 1e-4)
    rep_b = s.bake_point(1, 2)
    based = fc_deltas()
    same = set(plain) == set(based) and len(plain) > 0 and all(
        (plain[k] is None and based[k] is None) or (plain[k] is not None and based[k] is not None and plain[k][0] == based[k][0] and np.allclose(plain[k][1], based[k][1], atol=1e-5))
        for k in plain
    )
    check("ベイク: 土台の表情を当てて焼いても、無いときと同じ FC_* の差分（土台は焼かれない）", same and len(rep_b.created) == len(rep_a.created), f"{sorted(plain)} {sorted(based)}")
    check("ベイク: 焼いたあと、FC_* の重みは 0 でシーンに土台は残らない（編集状態を抜けて土台も外れる）",
          s.base_expression_name == "" and not s.editing and all(abs(cmds.getAttr(t.plug)) < 1e-9 for t in scene.fc_targets(face_name)) and abs(cmds.getAttr(f"{bs}.smile_R")) < 1e-9)
    cmds.undo()
    s.select_point(0, 2)
    anim_import.set_base_expression(s, base_anim)
    pose.btn_base_clear.click()
    pump()
    check("土台: 外す → 土台のシェイプが 0 に戻り、ポーズのシェイプは残る・ラベルは「なし」",
          abs(cmds.getAttr(f"{bs}.smile_R")) < 1e-9 and abs(cmds.getAttr(f"{bs}.brow_up")) < 1e-9 and abs(cmds.getAttr(f"{bs}.mouth_open") - 0.3) < 1e-4 and pose.base_label.text() == "なし" and not pose.btn_base_clear.isEnabled(),
          f"{pose.base_label.text()}")
    check("土台: 外したあと、取り込みは普通に戻る", anim_import.base_expression_name(s) == "" and not anim_import.capture_from_scene(s).base_ignored)
    # 編集を終えると外れる
    res = anim_import.set_base_expression(s, {"bs.smile_R": 0.6})
    check("土台: 重みの辞書からも当てられる（編集状態に入る）", res.ok and s.editing and abs(cmds.getAttr(f"{bs}.smile_R") - 0.6) < 1e-4, res.message)
    s.end_edit()
    pump()
    check("土台: 編集を終えるとシーンから外れ、記録も消える", abs(cmds.getAttr(f"{bs}.smile_R")) < 1e-9 and anim_import.base_expression_name(s) == "" and pose.base_label.text() == "なし" and not s.base_expression_curves and not s.base_state().active)
    # パネルを閉じると外れる
    anim_import.set_base_expression(s, {"bs.brow_up": 0.4})
    check("土台: 設定", s.editing and anim_import.base_expression_name(s) == "土台")
    anim_import.set_base_expression(s, {"bs.smile_R": 0.5})
    check("土台: 置き換えると前の土台は消える", abs(cmds.getAttr(f"{bs}.brow_up")) < 1e-9 and abs(cmds.getAttr(f"{bs}.smile_R") - 0.5) < 1e-4 and len(s.state_listeners) == len({id(f) for f in s.state_listeners}))
    n_listeners = len(s.state_listeners)
    res = anim_import.set_base_expression(s, {"bs.no_such": 1.0})
    check("土台: 当てられるシェイプが無ければ失敗（今の土台は変えない）", not res.ok and abs(cmds.getAttr(f"{bs}.smile_R") - 0.5) < 1e-4 and len(s.state_listeners) == n_listeners, res.message)
    pose.detach()
    check("土台: パネルを閉じる（detach）と外れる", anim_import.base_expression_name(s) == "" and abs(cmds.getAttr(f"{bs}.smile_R")) < 1e-9 and not s.base_expression_curves)
    s.end_edit()
    pose = ui_pose.PoseTab(s)  # 以降のために作り直す

    # ------------------------------------------------------------ 他のデータからコピー（グリッドタブのダイアログ）
    src = facial_fixture.make_doc()
    src.layers[0].points[(1, 2)].pose = SourcePose({"bs.mouth_open": 0.9, "bs.smile_L": 0.7, "bs.brow_up": 0.4}, {"eye_L": BoneOffset(r=(0.0, 0.0, 0.0, 1.0))})
    anger = Layer(name="Anger", emotion_curve="Anger")
    anger.points[(1, 2)] = GridPoint(1, 2, True, SourcePose({"bs.brow_up": 0.8, "bs.mouth_open": 0.1}))
    src.layers.append(anger)
    src_file = tmp / "src" / "other.fcpose.json"
    src_file.parent.mkdir()
    fcpose_io.save(src, src_file)
    pose_file = tmp / "src" / "just_pose.fcpose.json"
    pose_file.write_text(fcpose_io.dumps(PoseDocument(meta=src.meta, pose=SourcePose({"bs.mouth_open": 0.5}))), encoding="utf-8")

    s.end_edit()
    pump()
    snap0 = fcpose_io.to_dict(s.doc)
    n_layers0 = len(s.doc.layers)

    def copy_via_dialog(path, all_layers=False, ws=False, keys=True):
        def run(dlg) -> bool:
            dlg.set_path(str(path))
            dlg.cb_all.setChecked(all_layers)
            dlg.cb_ws.setChecked(ws)
            dlg.cb_keys.setChecked(keys)
            return True

        grid.run_dialog = run
        grid.on_copy_from()
        pump()

    dlg = grid.make_copy_dialog()
    check("コピーのダイアログ: 初期値は「キーだけ」だけ・ファイルが空の間は OK が押せない", dlg.flags() == ["keys_only"] and not dlg.buttons.button(QtWidgets.QDialogButtonBox.Ok).isEnabled())
    dlg.set_path(str(src_file))
    check("コピーのダイアログ: ファイルを指定すると OK が押せる", dlg.buttons.button(QtWidgets.QDialogButtonBox.Ok).isEnabled())
    dlg.deleteLater()

    copy_via_dialog(src_file, keys=True)
    lay = s.doc.layers[0]
    p12 = lay.points.get((1, 2))
    check("コピー（キーだけ）: 今のレイヤーにコピーされ、点は自動生成（非キー）になる", p12 is not None and not p12.is_key and len(lay.points) >= 8 and len(s.doc.layers) == n_layers0, f"{grid.status.text()}")
    check("コピー（キーだけ）: 宛先にあったキー（mouth_open 0.6）がコピー元の値（0.9・brow_up 0.4）で上書きされる",
          abs(p12.pose.curves.get("bs.mouth_open", 0) - 0.9) < 1e-3 and abs(p12.pose.curves.get("bs.brow_up", 0) - 0.4) < 1e-3, f"{p12.pose.curves}")
    check("コピー（キーだけ）: 状態の行にコピーした数が出る", "コピーしました" in grid.status.text() and "元に戻す" in grid.status.text(), grid.status.text())
    check("コピー: 元に戻す（Undo）で 1 回で戻る", s.can_undo and s.undo() and fcpose_io.to_dict(s.doc) == snap0)

    copy_via_dialog(src_file, all_layers=True, keys=True)
    names = [l.name for l in s.doc.layers]
    check("コピー（全レイヤー）: 名前が同じレイヤー同士・無いレイヤーは新規作成", "Anger" in names and "Joy" in names and len(names) == n_layers0 + 1, f"{names}")
    anger_l = next(l for l in s.doc.layers if l.name == "Anger")
    check("コピー（全レイヤー）: 新しいレイヤーに点が入る", len(anger_l.points) > 0 and "新しく作ったレイヤー" in grid.status.text(), grid.status.text())
    s.undo()
    check("コピー（全レイヤー）: 元に戻すとレイヤーも消える", fcpose_io.to_dict(s.doc) == snap0 and len(s.doc.layers) == n_layers0)

    s.set_working_set(curves=["bs.mouth_open"], bones=[])
    snap_ws = fcpose_io.to_dict(s.doc)
    copy_via_dialog(src_file, ws=True, keys=True)
    used = {n for p in s.doc.layers[0].points.values() if not p.is_key for n in p.pose.curves}
    check("コピー（作業セットだけ）: 作業セットのシェイプだけがコピーされる", used == {"bs.mouth_open"}, f"{used}")
    s.undo()
    check("コピー（作業セットだけ）: 元に戻せる", fcpose_io.to_dict(s.doc) == snap_ws)
    s.set_working_set(curves=["bs.mouth_open", "bs.smile_L", "bs.smile_R", "bs.brow_up"], bones=["eye_L", "eye_R"])
    snap1 = fcpose_io.to_dict(s.doc)

    # 自動生成の点があるコピー元（キーだけを切ると、自動生成の点もコピーされる）
    src_gen = fcpose_io.load_document(src_file)
    autofill.generate_from_keys(src_gen)
    gen_file = tmp / "src" / "generated.fcpose.json"
    fcpose_io.save(src_gen, gen_file)
    copy_via_dialog(gen_file, keys=True)
    n_keys_only = sum(1 for p in s.doc.layers[0].points.values())
    s.undo()
    copy_via_dialog(gen_file, keys=False)
    n_all = sum(1 for p in s.doc.layers[0].points.values())
    check("コピー（キーだけを切る）: 自動生成の点もコピーされて点が増える", n_all >= n_keys_only and "コピーしました" in grid.status.text(), f"{n_keys_only} {n_all}")
    s.undo()
    copy_via_dialog(gen_file, all_layers=True, ws=True, keys=False)
    check("コピー（3 つとも）: 通る", "コピーしました" in grid.status.text() and not grid.status.text().startswith("ファイル"), grid.status.text())
    s.undo()
    check("コピー: 何度やっても元に戻せる", fcpose_io.to_dict(s.doc) == snap1)

    copy_via_dialog(tmp / "src" / "nothing.fcpose.json")
    check("コピー: 無いファイルはエラーを状態の行に出す（データは変わらない）", "読めませんでした" in grid.status.text() and fcpose_io.to_dict(s.doc) == snap1, grid.status.text())
    copy_via_dialog(pose_file)
    check("コピー: 単独のポーズのファイルは受けつけない（データは変わらない）", "読めませんでした" in grid.status.text() and fcpose_io.to_dict(s.doc) == snap1, grid.status.text())
    res = anim_import.copy_from_file(s, src_file, ["bogus"])
    check("コピー: 知らない旗は失敗（落ちない）", not res.ok and "未知" in res.message, res.message)
    res = anim_import.copy_from_file(s, src_file, "keys_only")
    check("コピー: 文字列の旗でも通る・結果に CopyReport が付く", res.ok and res.report is not None and res.report.copied_points > 0 and not s.editing)
    s.undo()

    # コピーしたデータをベイクして反映（古い FC_* は出ない）
    copy_via_dialog(src_file, keys=True)
    rep = s.bake_stale()
    check("コピー → 変更のある点だけベイクで反映できる", len(rep.created) > 0, rep.summary())
    s.undo()  # ベイクの Undo は Maya の Undo。ここでは文書だけ戻す

    # ------------------------------------------------------------ 任意: 実データ
    real = REPO / "assets" / "shizuku" / "facial_anims"
    if real.exists():
        files = sorted(real.glob("*.anim"))
        ok_n, total_curves, fails = 0, 0, []
        for f in files:
            try:
                clip = unity_anim.load(f)
                ok_n += 1
                total_curves += len(clip.float_curves)
            except Exception as exc:  # noqa: BLE001
                fails.append(f"{f.name}: {exc!r}")
        info(f"実データの .anim: {ok_n}/{len(files)} 本を読めた・浮動小数カーブ合計 {total_curves}・失敗 {fails}")
        check("実データ: .anim が全部読める（例外なし）", not fails and ok_n == len(files) and ok_n > 0, "; ".join(fails))
    shizuku = REPO / "assets" / "shizuku" / "shizuku.mb"
    happy = real / "face_happy01.anim"
    if shizuku.exists() and happy.exists():
        pose.detach()
        grid.detach()
        s.close()
        cmds.file(shizuku.as_posix(), open=True, force=True)  # 開くだけ（保存しない）
        S.on_new_scene()
        s = S.current()
        s.listeners.clear()
        s.state_listeners.clear()
        s.new("shizuku_smoke", mesh="mdl_face02")
        s.select_point(0, 0)
        rep = anim_import.pose_from_unity_anim(s, happy, 0.0, apply=False)
        names = sorted(unity_anim.to_curve_weights(unity_anim.load(happy), 0.0))
        info(f"face_happy01.anim @0s → shizuku: 読んだシェイプ {len(names)} 本・照合できた {rep.curves_set} 本・未対応 {rep.unmatched_curves}・名前が変わったもの {rep.renamed}")
        check("実データ: face_happy01 を shizuku へ読める（全シェイプが照合される）", rep.ok and rep.curves_set == len(names) > 0 and not rep.unmatched_curves, rep.message)
        eye = anim_import.pose_from_unity_anim(s, real / "face_eyeMove.anim", 1.45, apply=False)
        eb = {n: (tuple(round(v, 4) for v in o.t), tuple(round(v, 4) for v in o.r)) for n, o in s.pose.bones.items()}
        info(f"face_eyeMove.anim @1.45s → shizuku: ボーン {eye.bones_set} 本・未対応 {eye.unmatched_bones}・ずれ {eb}")
        check("実データ: face_eyeMove の目のボーン（bone_eyescale）を、基準姿勢からのずれとして読める（Unity の値と Maya の基準が同じ座標になる）", eye.ok and eye.bones_set >= 2 and not eye.unmatched_bones, f"{eye.message} {eb}")
        s.end_edit(quiet=True)
        s.close()
        cmds.file(new=True, force=True)


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
