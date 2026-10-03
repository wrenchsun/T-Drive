"""FacialController のコードレビュー（docs/19）の修正を確かめるスモークテスト（mayapy で実行。画面なし）。

  "C:/Program Files/Autodesk/Maya2026/bin/mayapy.exe" tests/maya/facial_review_smoke.py [セクション名の一部...]

各セクションは「指摘の場面を再現 → 期待どおりになる」を確かめる。ファイルはすべて一時フォルダ。
"""

from __future__ import annotations

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

APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv[:1])  # maya.standalone より先に作る（画面を使う節のため）

import maya.standalone  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(cond), detail))


def joint_state(joints) -> dict:
    from maya import cmds

    return {
        j: tuple(tuple(round(v, 6) for v in cmds.getAttr(f"{j}.{a}")[0]) for a in ("translate", "rotate", "scale", "jointOrient"))
        for j in joints
    }


def weight_state(node: str) -> dict:
    from maya import cmds

    idx = cmds.getAttr(node + ".weight", multiIndices=True) or []
    return {i: round(cmds.getAttr(f"{node}.weight[{i}]"), 6) for i in idx}


def fresh(tmp: Path):
    """合成の頭 + 開いたデータ（mini）。user の元の姿勢（ジョイントのずれ・シェイプの重み・キー）を作って返す。"""
    from maya import cmds

    import facial_fixture
    from tdrive import project
    from tdrive_facial import session as S
    from tdrive_facial.core import fcpose_io

    project.set_root(tmp)
    ids = facial_fixture.build_mini_head()
    s = S.current()
    s.close()
    doc0 = facial_fixture.make_doc()
    p = tmp / "facial" / "mini" / "mini.fcpose.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    fcpose_io.save(doc0, p)
    s.open(p)
    return ids, s


# ---------------------------------------------------------------------------
# M-1 / M-2: 編集中の保存
# ---------------------------------------------------------------------------


def sec_m1_save_while_editing(tmp: Path) -> None:
    from maya import cmds

    from tdrive_facial import session as S

    ids, s = fresh(tmp)
    bs, joints = ids["bs"], ids["joints"]
    eye_l = "eye_L"
    cmds.setAttr(f"{eye_l}.rotate", 5, 6, 7)
    cmds.setAttr("head.translate", 0.1, 10.2, 0.0)
    cmds.setAttr(f"{bs}.weight[0]", 0.25)
    cmds.setAttr(f"{bs}.weight[1]", 0.4)
    cmds.setKeyframe(f"{bs}.weight[2]", time=1, value=0.3)  # 元の接続（アニメ）
    cmds.setKeyframe(f"{bs}.weight[2]", time=10, value=0.9)
    cmds.currentTime(5)
    orig_joints = joint_state(joints)
    orig_w = weight_state(bs)
    orig_src = cmds.listConnections(f"{bs}.weight[2]", source=True, destination=False, plugs=True)

    s.begin_edit()
    s.select_point(1, 2)
    s.set_curve("bs.mouth_open", 0.9)  # 未保存のスライダーの値
    s.set_curve("bs.smile_L", 0.35)
    s.set_base_expression("smile", {"bs.smile_R": 0.5})
    editing_joints = joint_state(joints)
    editing_w = weight_state(bs)
    check("M-1 前提: 編集中は基準姿勢（元の姿勢と違う）", editing_joints != orig_joints and editing_w != orig_w)
    buf_before = dict(s.pose.pose_to_apply().curves)
    point_before = s.applied_point

    f = tmp / "saved_a.mb"
    cmds.file(rename=str(f))
    cmds.file(save=True, type="mayaBinary")

    check("M-1: 保存のあとも編集状態のまま・同じ点", s.editing and s.applied_point == point_before, f"{s.editing} {s.applied_point}")
    check("M-1: 保存のあと編集中の値（未保存のスライダー）が保たれシーンに当たっている",
          dict(s.pose.pose_to_apply().curves) == buf_before and weight_state(bs) == editing_w and joint_state(joints) == editing_joints,
          f"{weight_state(bs)} vs {editing_w}")
    check("M-1: 保存のあと土台の表情が保たれる", s.base_expression_name == "smile")
    check("M-1: 保存のあとシーンは「変更なし」のまま（保存で変更フラグが立たない）", not cmds.file(query=True, modified=True))

    s.end_edit(quiet=True)
    check("M-1: 編集を抜けると元の姿勢へ戻る", joint_state(joints) == orig_joints and weight_state(bs) == orig_w)

    # 保存したファイルを開き直して、元の姿勢・重み・接続が入っていること
    cmds.file(str(f), open=True, force=True)
    s._forget_edit()
    got_j = joint_state(joints)
    check("M-1: 保存したファイルのジョイントは元の姿勢", got_j == orig_joints, f"{got_j}\n{orig_joints}")
    got_w = weight_state(bs)
    check("M-1: 保存したファイルの重みは元どおり（接続されたもの以外）", got_w[0] == orig_w[0] and got_w[1] == orig_w[1], f"{got_w} {orig_w}")
    src = cmds.listConnections(f"{bs}.weight[2]", source=True, destination=False, plugs=True)
    check("M-1: 保存したファイルでアニメの接続が残っている", src == orig_src, f"{src} {orig_src}")
    check("M-1: 保存したファイルに FacialController の記録がある", bool(cmds.fileInfo(S.FILE_INFO_KEY, query=True)))
    s.close()


def sec_m1_export_while_editing(tmp: Path) -> None:
    """シーンの出力（Export All）にも基準姿勢が入らない。出力のあとも編集状態のまま。"""
    from maya import cmds

    ids, s = fresh(tmp)
    bs, joints = ids["bs"], ids["joints"]
    cmds.setAttr("eye_L.rotate", 5, 6, 7)
    cmds.setAttr(f"{bs}.weight[0]", 0.25)
    orig_j, orig_w = joint_state(joints), weight_state(bs)
    s.begin_edit()
    s.select_point(1, 2)
    s.set_curve("bs.mouth_open", 0.9)
    f = tmp / "exp.ma"
    cmds.file(str(f), exportAll=True, type="mayaAscii", force=True)
    check("M-1 出力: 出力のあとも編集状態のまま", s.editing and s.applied_point is not None)
    s.end_edit(quiet=True)
    cmds.file(str(f), open=True, force=True)
    s._forget_edit()
    check("M-1 出力: 出力したファイルは元の姿勢・重み", joint_state(joints) == orig_j and weight_state(bs)[0] == orig_w[0], f"{joint_state(joints)['eye_L']} {weight_state(bs)}")
    s.close()


def sec_m1_callbacks_registered_once(tmp: Path) -> None:
    """コールバックの登録は重ならない（再 import・登録し直しでも 1 組）。後片付けで外れる。M-14。"""
    import importlib
    import sys

    from tdrive_facial import session as S

    ids = list(getattr(sys, S._SCENE_CALLBACKS_ATTR))
    importlib.reload(S)
    ids2 = list(getattr(sys, S._SCENE_CALLBACKS_ATTR))
    check("M-14: importlib.reload しても登録が重ならない（古い登録が外れ、5 個のまま）", len(ids2) == 5, f"{ids} {ids2}")
    S.remove_scene_callbacks()
    check("M-14: 後片付けで外れる", getattr(sys, S._SCENE_CALLBACKS_ATTR) == [])
    S.install_scene_callbacks()


def sec_m2_new_data_recorded_in_saved_scene(tmp: Path) -> None:
    from maya import cmds

    from tdrive_facial import session as S

    import facial_fixture
    from tdrive import project

    project.set_root(tmp)
    facial_fixture.build_mini_head()
    s = S.current()
    s.close()
    s.new("mini2")
    f = tmp / "saved_b.mb"
    cmds.file(rename=str(f))
    cmds.file(save=True, type="mayaBinary")
    cmds.file(new=True, force=True)
    cmds.file(str(f), open=True, force=True)
    info = cmds.fileInfo(S.FILE_INFO_KEY, query=True)
    check("M-2: 新規作成したデータの場所が、保存したシーンの中に記録される", info == ["facial/mini2/mini2.fcpose.json"], f"{info}")
    s.close()


def sec_c1_bake_keeps_other_assets_targets(tmp: Path) -> None:
    """C-1 / S-8: asset mini の焼き直しの掃除が、同じ blendShape の別アセット（mini_Alt）の FC_* を消さない。自分の孤立は消す。"""
    import numpy as np
    from maya import cmds

    from tdrive_facial import scene

    ids, s = fresh(tmp)
    bs = ids["bs"]
    s.bake_all()
    node = scene.primary_blend_shape(scene.resolve_mesh("mini_face"))
    delta = np.array([[0.0, 0.1, 0.0]])
    for n in ("FC_mini_Alt_Joy_R1_C1", "FC_mini_Alt_Persp_K0", "FC_mini_Alt_Neutral_R0_C0_Ex", "FC_mini_Gone_R0_C0"):
        scene.write_target_delta(node, n, delta, [0], 0)
    s.bake_all()
    names = set(scene.target_indices(node))
    check("C-1: 別アセット（mini_Alt）の FC_* は掃除で消えない", {"FC_mini_Alt_Joy_R1_C1", "FC_mini_Alt_Persp_K0", "FC_mini_Alt_Neutral_R0_C0_Ex"} <= names, str(sorted(names)))
    check("C-1: 自分のアセットの孤立（消えたレイヤー）は掃除される", "FC_mini_Gone_R0_C0" not in names)
    check("C-1: 自分のターゲットは残る", "FC_mini_Neutral_R1_C2" in names)
    s.close()


def sec_m3_reload_keeps_unsaved_facial_data(tmp: Path) -> None:
    """M-3: 「ツールをリロード」（maya/mcp_scripts/reload_tdrive.py）で、未保存の FacialController のデータ・Undo・選択・編集中の値が残る。"""
    import runpy
    import sys

    from maya import cmds

    from tdrive import project
    from tdrive_facial.core import fcpose_io

    ids, s = fresh(tmp)
    s.set_grid(cols=7)  # 未保存の変更（Undo にも積まれる）
    s.set_edge_fade(12)
    s.begin_edit()
    s.select_point(1, 2)
    s.set_curve("bs.mouth_open", 0.77)  # 保存していないスライダーの値
    s.end_edit()
    before = fcpose_io.to_dict(s.doc)
    n_undo, path = len(s._undo), s.path
    sel, layer_i = s.ctx.selection, s.ctx.active_layer
    check("M-3 前提: 未保存・Undo あり・編集中の値がある", s.dirty and n_undo >= 2 and abs(s.pose.curves.get("bs.mouth_open", 0) - 0.77) < 1e-9, str(s.pose.curves))

    runpy.run_path(str(REPO / "maya" / "mcp_scripts" / "reload_tdrive.py"))
    project.set_root(tmp)
    from tdrive_facial import session as S2  # リロード後の新しいモジュール

    s2 = S2.current()
    check("M-3: リロード後もデータが開いている（新しいモジュールのセッション）", s2 is not s and s2.presenters is not None)
    if s2.presenters is None:
        return
    check("M-3: 文書の内容が同じ", fcpose_io.to_dict(s2.doc) == before)
    check("M-3: 未保存の印・保存先が残る", s2.dirty and s2.path == path)
    check("M-3: 選択中の点・レイヤーが残る", s2.ctx.selection == sel and s2.ctx.active_layer == layer_i)
    check("M-3: 編集中（保存していない）のスライダーの値が残る", abs(s2.pose.curves.get("bs.mouth_open", 0) - 0.77) < 1e-9, str(s2.pose.curves))
    check("M-3: エディタ内 Undo の履歴が残り、戻せる", len(s2._undo) == n_undo and s2.undo() and s2.undo() and s2.doc.grid.cols == 3, f"{len(s2._undo)}")
    s2.redo()
    s2.redo()
    s2.close()


def sec_m4_restore_does_not_delete_own_control(tmp: Path) -> None:
    """M-4: 古いレイアウトの枠（TDriveToonEditorWorkspaceControl）から復元されたとき、復元中の枠自身を消さない。"""
    from tdrive import shell

    deleted: list[str] = []

    class FakeCmds:
        @staticmethod
        def workspaceControl(name, exists=False, **_kw):
            return exists  # どの枠も「ある」

        @staticmethod
        def deleteUI(name, **_kw):
            deleted.append(name)

    real = shell.cmds
    shell.cmds = FakeCmds
    try:
        for own in (shell.OLD_CONTROL_NAME, "MayaWindow|" + shell.OLD_CONTROL_NAME, shell.OLD_CONTROL_NAME.lower()):
            deleted.clear()
            shell._control = own
            shell._remove_old_control()
            check(f"M-4: 復元中の枠（{own}）は消さない", deleted == [], str(deleted))
        deleted.clear()
        shell._control = None
        shell._remove_old_control(own=[shell.OLD_CONTROL_NAME])  # 枠の名前が分からない（None）ときも、自分が入っている枠として渡せば消さない
        check("M-4: 自分が入る枠の名前を渡せば消さない", deleted == [], str(deleted))
        deleted.clear()
        shell._control = shell.CONTROL_NAME
        shell._remove_old_control()
        check("M-4: 新しい枠から復元されたときは、旧エディタの枠を消す", deleted == [shell.OLD_CONTROL_NAME], str(deleted))
    finally:
        shell.cmds = real
        shell._control = None


def _bone_roundtrip(setup) -> tuple[float, float, float]:
    """ボーンのずらしを当てて読み戻した誤差（位置 cm・向き 1 - |dot|・スケール）。"""
    from tdrive_facial import pose_apply, scene
    from tdrive_facial.core.model import BoneOffset, SourcePose

    import facial_fixture

    facial_fixture.build_mini_head()
    setup()
    ref = scene.enter_reference_pose(["mini_face"], extra_joints=["eye_L", "head"])
    try:
        b = ref.bones["eye_L"]
        off = BoneOffset(t=(0.2, 0.0, 0.1), r=facial_fixture._q_axis((1, 0, 0), 25.0), s=(1.1, 1.0, 0.9))
        pose_apply.apply_pose(facial_fixture.make_doc(), SourcePose({}, {"eye_L": off}), ref)
        t, q, s = scene.read_local(b.path)
        exp_t = tuple(x + y for x, y in zip(b.t, off.t))
        exp_q = scene.quat_normalize(scene.quat_mul(scene.quat_normalize(off.r), b.q))
        exp_s = tuple(x * y for x, y in zip(b.s, off.s))
        return (
            max(abs(x - y) for x, y in zip(t, exp_t)),
            1.0 - abs(sum(a * c for a, c in zip(q, exp_q))),
            max(abs(x - y) for x, y in zip(s, exp_s)),
        )
    finally:
        ref.restore()


def sec_s1_non_cm_scene_units(tmp: Path) -> None:
    """S-1: 作業単位が m / inch のシーンでも、ボーンの位置のずらしが cm で当たる。"""
    from maya import cmds

    try:
        for unit in ("m", "inch", "mm"):
            err = _bone_roundtrip(lambda u=unit: cmds.currentUnit(linear=u))
            check(f"S-1: 作業単位 {unit} でボーンの位置のずらしが正しい（誤差 cm）", err[0] < 1e-4 and err[1] < 1e-6, str(err))
    finally:
        cmds.currentUnit(linear="cm")


def sec_s2_parent_and_own_scale(tmp: Path) -> None:
    """S-2: 親・自分にスケールがあっても、位置・向き・スケールを読み書きして変わらない（レビューの指摘は再現しなかった。回帰の確認）。"""
    from maya import cmds

    cases = {
        "親に一様スケール": lambda: cmds.setAttr("head.scale", 1.2, 1.2, 1.2),
        "親に非一様スケール": lambda: cmds.setAttr("head.scale", 1.2, 1.0, 0.8),
        "親に非一様スケール + segmentScaleCompensate オフ": lambda: (cmds.setAttr("head.scale", 1.2, 1.0, 0.8), cmds.setAttr("eye_L.segmentScaleCompensate", 0)),
        "自分に非一様スケール + 回転 + rotateAxis": lambda: (cmds.setAttr("eye_L.scale", 1.2, 1.0, 0.8), cmds.setAttr("eye_L.rotate", 20, 30, 40), cmds.setAttr("eye_L.rotateAxis", 10, 20, 5)),
        "親も自分も非一様スケール": lambda: (cmds.setAttr("head.scale", 1.2, 1.0, 0.8), cmds.setAttr("eye_L.scale", 1.2, 1.0, 0.8), cmds.setAttr("eye_L.rotate", 20, 30, 40)),
    }
    for label, setup in cases.items():
        err = _bone_roundtrip(setup)
        check(f"S-2: {label}で当てたずらしが正しい", err[0] < 1e-4 and err[1] < 1e-6 and err[2] < 1e-6, str(err))


def sec_s3_reference_must_match_bind(tmp: Path) -> None:
    """S-3: バインドポーズが無い・バインド後に動かしたリグでは、基準姿勢がスキン前の形と違う。そのまま焼かず・編集に入らず止める。"""
    from maya import cmds

    from tdrive_facial import bake, scene
    from tdrive_facial import session as S

    ids, s = fresh(tmp)
    poses = cmds.ls(type="dagPose")
    cmds.delete(poses)  # バインドポーズ無し
    cmds.setAttr("head.rotate", 0, 15, 0)  # バインド後に頭を動かした
    before = joint_state(ids["joints"])
    try:
        scene.enter_reference_pose(["mini_face"])
        entered = True
    except scene.ReferenceError_ as e:
        entered = False
        msg = str(e)
    check("S-3: バインド後に動かしたリグ（バインドポーズ無し）では基準姿勢に入れない（スキン前の形と違う）", not entered)
    check("S-3: 止めたあと、ジョイントは元のまま", joint_state(ids["joints"]) == before)
    try:
        s.bake_all()
        baked = True
    except bake.BakeError:
        baked = False
    check("S-3: ベイクも止まる（FC_* を作らない）", not baked and not [n for n in scene.target_indices(ids["bs"]) if n.startswith("FC_")])
    try:
        s.begin_edit()
        began = True
    except S.FacialSessionError:
        began = False
    check("S-3: 編集に入れない（基準姿勢にできません）", not began and not s.editing)
    # バインドポーズが無くても、動かしていなければ（スキンが単位行列）今までどおり入れる
    cmds.setAttr("head.rotate", 0, 0, 0)
    ref = scene.enter_reference_pose(["mini_face"])
    check("S-3: バインドポーズが無くても動かしていなければ警告つきで入れる", ref.active and bool(ref.warnings))
    ref.restore()
    s.close()


def sec_s5_inbetween_exaggerate_need_head_at_reference(tmp: Path) -> None:
    """S-5: 頭が基準姿勢から動いているポーズでは、中間形・誇張形を作らない（頭の動きを形に焼き込まない）。目だけの動きは使える。"""
    from tdrive_facial import shapes
    from tdrive_facial.core.model import BoneOffset

    import facial_fixture

    ids, s = fresh(tmp)

    def code_of(fn):
        try:
            fn()
        except shapes.ShapeError as e:
            return e.code
        return None

    s.begin_edit()
    s.select_point(1, 2)
    s.set_curve("bs.smile_L", 0.8)
    s.set_bone("head", BoneOffset(t=(0.0, 0.5, 0.0), r=facial_fixture._q_axis((0, 1, 0), 20.0)))
    check("S-5: 頭を動かしたポーズでは中間形を作れない", code_of(lambda: s.shape_inbetween("smile_L", 0.5, confirm_original=True)) == "head_moved")
    check("S-5: 頭を動かしたポーズでは誇張形を作れない", code_of(lambda: s.shape_exaggerate("smile_L")) == "head_moved")
    check("S-5: 止めたあと編集状態のまま・頭はポーズのまま", s.editing)
    s.reset_bone("head")
    s.set_bone("eye_L", BoneOffset(r=facial_fixture._q_axis((0, 1, 0), 10.0)))
    check("S-5: 目のボーンだけ動かしたポーズでは中間形を作れる", code_of(lambda: s.shape_inbetween("smile_L", 0.5, confirm_original=True)) is None)
    s.end_edit(quiet=True)
    s.close()


def sec_s6_fc_goes_before_skin(tmp: Path) -> None:
    """S-6: スキンより前の blendShape が無いモデルでは、FC_* をスキンの後ろのノードに入れず、前に作る。以前の版が後ろに焼いたものは、焼き直しで消す。"""
    from maya import cmds

    from tdrive_facial import scene

    ids, s = fresh(tmp)
    face = ids["face"]
    # スキンの後ろにだけ blendShape があるモデルにする
    cmds.delete(ids["bs"])
    tgt = cmds.duplicate(face, name="post_target")[0]
    cmds.move(0, 1, 0, f"{tgt}.vtx[0]", relative=True)
    post = cmds.blendShape(tgt, face, name="bs_post")[0]  # 既定はスキンの後ろ
    cmds.aliasAttr("mouth_open", f"{post}.weight[0]")
    hist = cmds.listHistory(face, pruneDagObjects=True)
    check("S-6 前提: blendShape がスキンの後ろにある", hist.index(post) < hist.index(ids["skin"]), str(hist))
    # 以前の版のように、スキンの後ろのノードに FC_* がある状態
    import numpy as np

    scene.write_target_delta(post, "FC_mini_Neutral_R1_C2", np.array([[0.0, 0.1, 0.0]]), [0], 0)
    scene.write_target_delta(post, "FC_mini_Gone_R0_C0", np.array([[0.0, 0.1, 0.0]]), [0], 0)
    doc = s.doc
    for lay in doc.layers:
        for pt in lay.points.values():
            pt.pose.curves = {"mouth_open": 0.5} if pt.pose.curves else {}
    s.refresh_scene()
    s.bake_all()
    node = scene.primary_blend_shape(scene.resolve_mesh(face))
    hist = cmds.listHistory(face, pruneDagObjects=True)
    check("S-6: FC_* はスキンより前のノードに入る", hist.index(node) > hist.index(ids["skin"]) and node != post, f"{node} {hist}")
    check("S-6: 焼いた FC_* がそのノードにある", "FC_mini_Neutral_R1_C2" in scene.target_indices(node))
    old = [t for t in scene.target_indices(post) if t.startswith("FC_")]
    check("S-6: スキンの後ろのノードの古い FC_*（焼き直した名前・孤立）は消える（二重に効かない）", old == [], str(old))
    check("S-6: 元のシェイプ（mouth_open）は後ろのノードに残る", "mouth_open" in scene.target_indices(post))
    s.close()


def sec_m5_maya_undo_not_polluted(tmp: Path) -> None:
    """M-5: 編集の出入り・スライダー・ポーズの当て込みが Maya の Undo に積まれない。編集オフのあとの Ctrl+Z で、元の作業だけが戻る。"""
    from maya import cmds

    ids, s = fresh(tmp)
    bs = ids["bs"]
    cmds.undoInfo(state=True)
    cmds.flushUndo()
    cmds.setAttr("eye_R.rotateX", 3.0)  # ユーザーの作業（Undo に積まれる）
    cmds.setAttr(f"{bs}.weight[0]", 0.3)
    name_before = cmds.undoInfo(query=True, undoName=True)
    s.begin_edit()
    s.select_point(1, 2)
    for v in (0.2, 0.5, 0.8):
        s.set_curve("bs.smile_L", v)  # スライダー
    s.select_point(0, 2)
    s.end_edit()
    name_after = cmds.undoInfo(query=True, undoName=True)
    check("M-5: 編集の出入り・スライダーのあとも、Undo の先頭はユーザーの作業のまま", name_after == name_before, f"{name_before!r} -> {name_after!r}")
    cmds.undo()
    check("M-5: Ctrl+Z で戻るのは直前のユーザーの作業（シェイプの重み）だけ", abs(cmds.getAttr(f"{bs}.weight[0]")) < 1e-9 and abs(cmds.getAttr("eye_R.rotateX") - 3.0) < 1e-6)
    cmds.undo()
    check("M-5: もう一度戻すと、その前のユーザーの作業（ジョイント）", abs(cmds.getAttr("eye_R.rotateX")) < 1e-6)
    s.close()


def sec_c2_capture_below_base_is_reported(tmp: Path) -> None:
    """C-2: 土台の表情があるとき、シーンで土台より下げた値は黙って捨てず、取り込めなかったと知らせる。"""
    from maya import cmds

    ids, s = fresh(tmp)
    bs = ids["bs"]
    s.begin_edit()
    s.select_point(1, 2)
    s.set_base_expression("smile", {"bs.smile_L": 0.6})
    cmds.setAttr(f"{bs}.weight[1]", 0.2)  # 土台（0.6）より下げた
    rep = s.capture_from_scene()
    check("C-2: 土台より低くしたシェイプが below_base に出る", rep.below_base == ["bs.smile_L"] and s.last_capture_below_base == ["bs.smile_L"], f"{rep.below_base}")
    s.end_edit(quiet=True)
    s.close()


def sec_m9_confirm_defaults_to_no(tmp: Path) -> None:
    """M-9: 「はい / いいえ」の確認は、既定のボタンが「いいえ」（Enter で確定しない）。"""
    from PySide6 import QtWidgets

    from tdrive_facial import ui

    seen = {}
    real = QtWidgets.QMessageBox.question

    def fake(parent, title, text, buttons=QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, default=QtWidgets.QMessageBox.NoButton):
        seen["default"] = default
        return QtWidgets.QMessageBox.No

    QtWidgets.QMessageBox.question = staticmethod(fake)
    try:
        ok = ui.ask_yes_no(None, "削除しますか")
    finally:
        QtWidgets.QMessageBox.question = real
    check("M-9: ask_yes_no の既定のボタンは「いいえ」", seen.get("default") == QtWidgets.QMessageBox.No and ok is False, str(seen))


def sec_m8_slider_edits_count_as_unsaved(tmp: Path) -> None:
    """M-8: スライダーで編集中（まだ点に保存していない）の値も「未保存」として扱う（開く・新規・シーンを開くで確認なしに消えない）。"""
    from tdrive_facial import session as S

    ids, s = fresh(tmp)
    check("M-8 前提: 開いた直後は未保存なし", not s.has_unsaved_work)
    s.begin_edit()
    s.select_point(1, 2)
    s.set_curve("bs.smile_L", 0.33)
    check("M-8: 編集中の値があると has_unsaved_work", s.has_unsaved_work and not s.dirty)
    S.on_scene_opened()  # 別のシーンが開かれても、編集中の値がある間は自動で開き直さない
    check("M-8: シーンを開いたとき、編集中の値は消えない", abs(s.pose.curves.get("bs.smile_L", 0) - 0.33) < 1e-9)
    s.end_edit(quiet=True)
    s.close()


def sec_m7_export_job_snapshots_the_data(tmp: Path) -> None:
    """M-7: 出力の開始後にデータを編集しても、書き出す .fcpose は出力開始時の内容（FBX と食い違わない）。
    S-14: 出力の開始に失敗したとき、一時フォルダ・ログが残らない。"""
    import subprocess
    import tempfile as _tf

    from tdrive_facial import export

    ids, s = fresh(tmp)

    class FakeProc:
        def poll(self):
            return 0

        def kill(self):
            pass

        def wait(self):
            pass

    real_popen, real_mayapy = subprocess.Popen, export._mayapy
    subprocess.Popen = lambda *a, **kw: FakeProc()
    export._mayapy = lambda: "mayapy"
    try:
        job = export.UnityExportJob(s.doc, out_dir=tmp / "out")
        s.set_edge_fade(7)  # 出力が始まったあとの編集
        check("M-7: 出力のジョブが持つデータは、開始時の複製（あとの編集が入らない）", job._doc is not s.doc and job._doc.grid.edge_fade != 7.0, str(job._doc.grid.edge_fade))
        job.cancel()
        # 開始に失敗したとき（Popen が例外）: 一時フォルダが残らない
        before = set(Path(_tf.gettempdir()).glob("tdrive_facial_export_*"))

        def boom(*a, **kw):
            raise OSError("cannot start")

        subprocess.Popen = boom
        try:
            export.UnityExportJob(s.doc, out_dir=tmp / "out2")
            raised = False
        except OSError:
            raised = True
        after = set(Path(_tf.gettempdir()).glob("tdrive_facial_export_*"))
        check("S-14: 開始に失敗したら例外にし、一時フォルダ・ログを残さない", raised and after == before, str(after - before))
    finally:
        subprocess.Popen, export._mayapy = real_popen, real_mayapy
    s.close()


def sec_m6_shell_close_cleans_up_before_deleting_the_control(tmp: Path) -> None:
    """M-6: パネルを閉じるとき、後片付け（出力中のジョブの中止・購読解除）を、Qt の枠を消すより先にする。"""
    from tdrive import shell

    order: list[str] = []

    class FakeWindow:
        def detach(self):
            order.append("detach")

    class FakeCmds:
        @staticmethod
        def workspaceControl(name, exists=False, **_kw):
            return exists

        @staticmethod
        def deleteUI(name, **_kw):
            order.append("deleteUI")

    real_cmds, real_window = shell.cmds, shell._window
    shell.cmds, shell._window = FakeCmds, FakeWindow()
    try:
        shell.close()
    finally:
        shell.cmds, shell._window = real_cmds, real_window
    check("M-6: 後片付け（detach）が枠の削除（deleteUI）より先", order and order[0] == "detach" and "deleteUI" in order, str(order))


def sec_m10_export_warns_about_fc_not_in_data(tmp: Path) -> None:
    """M-10: Undo / Redo のあとシーンに残った、データに無い FC_* は、そのまま FBX に入る。出力の前の警告（「それでも出力する」の確認）に出す。"""
    import numpy as np

    from tdrive_facial import export, scene

    ids, s = fresh(tmp)
    s.bake_all()
    check("M-10 前提: 焼いた直後は警告なし", not export._bake_warnings(s.doc), str(export._bake_warnings(s.doc)))
    node = scene.primary_blend_shape(scene.resolve_mesh("mini_face"))
    scene.write_target_delta(node, "FC_mini_Neutral_R2_C0", np.array([[0.0, 0.1, 0.0]]), [0], 0)  # データに無い点（Undo の取り残し）
    w = export._bake_warnings(s.doc)
    check("M-10: データに無い FC_* がシーンにあると、出力の前の警告に出る", any("データに無い" in x and "1" in x for x in w), str(w))
    s.close()


def sec_m11_preview_group_skips_refresh_while_hidden(tmp: Path) -> None:
    """M-11: プレビュー欄が見えていないあいだは、セッションの軽い通知（スライダー 1 目盛りごと）で読み直さない。見えたとき 1 回だけ読み直す。"""
    from tdrive_facial.ui_preview import PreviewGroup

    from PySide6 import QtWidgets

    ids, s = fresh(tmp)
    host = QtWidgets.QWidget()  # 画面（ウィンドウ）は出ていて、プレビュー欄のタブだけが隠れている状態
    QtWidgets.QVBoxLayout(host)
    group = PreviewGroup(s)
    host.layout().addWidget(group)
    host.show()
    group.hide()
    calls = []
    real = group.refresh
    group.refresh = lambda: (calls.append(1), real())
    for _ in range(5):
        group.on_state()
    check("M-11: 隠れているあいだは読み直さない", calls == [], str(calls))
    group.show()
    check("M-11: 見えたとき 1 回だけ読み直す", len(calls) == 1, str(calls))
    group.on_state()
    check("M-11: 見えている間は読み直す", len(calls) == 2, str(calls))
    group.hide()
    group.detach()
    host.close()
    s.close()


def sec_m12_converted_file_is_not_overwritten(tmp: Path) -> None:
    """M-12: 別の座標系（UE 版など）から変換して開いたファイルは、元ファイルが既定の保存先と同じでも上書きできない（別名保存を強制）。"""
    import facial_fixture
    from tdrive import project
    from tdrive_facial import session as S
    from tdrive_facial.core import fcpose_io, space
    from tdrive_facial.core.model import Meta

    project.set_root(tmp)
    facial_fixture.build_mini_head()
    s = S.current()
    s.close()
    doc = facial_fixture.make_doc()
    ue = space.convert_document(doc, space.UE) if hasattr(space, "UE") else None
    if ue is None:
        doc.meta = Meta(unit="cm", up_axis="Z", handedness="left", source="UE")
        ue = doc
    p = S.default_path("mini")  # 元ファイル = 既定の保存先
    p.parent.mkdir(parents=True, exist_ok=True)
    fcpose_io.save(ue, p)
    before = p.read_bytes()
    s.open(p)
    check("M-12 前提: 変換して開いた・保存先は元ファイルと同じ", s.converted_from is not None and s.path == p)
    try:
        s.save(overwrite=True)
        overwrote = True
    except S.FacialSessionError:
        overwrote = False
    check("M-12: 元ファイルへは上書き保存できない（overwrite=True でも）", not overwrote and p.read_bytes() == before)
    other = tmp / "facial" / "mini" / "mini_maya.fcpose.json"
    s.save_as(other)
    check("M-12: 別名なら保存できる", other.exists() and p.read_bytes() == before)
    s.close()


def sec_s7_undo_restores_deleted_target_contents(tmp: Path) -> None:
    """S-7: ベイクの掃除で消した FC_* は、ベイクを Undo すると中身（頂点の差分）ごと戻る。"""
    import numpy as np
    from maya import cmds

    from tdrive_facial import scene

    ids, s = fresh(tmp)
    s.bake_all()
    node = scene.primary_blend_shape(scene.resolve_mesh("mini_face"))
    idx = [0, 5, 9]
    delta = np.array([[0.0, 0.2, 0.0], [0.1, 0.0, 0.0], [0.0, 0.0, 0.3]])
    scene.write_target_delta(node, "FC_mini_Gone_R0_C0", delta, idx, 0)
    cmds.undoInfo(state=True)
    cmds.flushUndo()
    s.bake_all()  # 掃除で FC_mini_Gone_R0_C0 が消える
    check("S-7 前提: 掃除で消えた", "FC_mini_Gone_R0_C0" not in scene.target_indices(node))
    cmds.undo()
    got = scene.read_target_delta(node, "FC_mini_Gone_R0_C0", 0) if "FC_mini_Gone_R0_C0" in scene.target_indices(node) else None
    check("S-7: Undo で消した FC_* が中身ごと戻る", got is not None and list(got[0]) == idx and np.allclose(got[1], delta), str(got))
    s.close()


def sec_s9_preview_build_is_atomic(tmp: Path) -> None:
    """S-9: プレビューの作成が途中で失敗してもノードが残らない・同名の別ノードがあれば止める・Undo 1 回で全部戻る。"""
    from maya import cmds

    from tdrive_facial import preview_rig

    ids, s = fresh(tmp)
    s.bake_all()
    doc = s.doc

    def preview_nodes():
        return sorted(n for n in (cmds.ls() or []) if n.startswith("tdFacialPreview_"))

    check("S-9 前提: プレビューのノードは無い", preview_nodes() == [])
    real = preview_rig.cmds.expression

    def boom(*a, **kw):
        raise RuntimeError("expression failed")

    preview_rig.cmds.expression = boom
    try:
        s.preview_build()
        failed = False
    except Exception:
        failed = True
    finally:
        preview_rig.cmds.expression = real
    check("S-9: 途中で失敗したら例外になり、作りかけのノードが残らない", failed and preview_nodes() == [], str(preview_nodes()))

    cmds.createNode("transform", name=preview_rig.rig_name("mini"))  # 同名の別ノード（rig ではない）
    try:
        s.preview_build()
        collided = False
    except Exception:
        collided = True
    others = preview_nodes()
    check("S-9: 同名の別ノードがあれば止め、何も増やさない（見失わない）", collided and others == [preview_rig.rig_name("mini")], str(others))
    cmds.delete(preview_rig.rig_name("mini"))

    cmds.undoInfo(state=True)
    cmds.flushUndo()
    s.preview_build()
    n = len(preview_nodes())
    cmds.undo()
    check("S-9: 作成は Undo 1 回で全部戻る", n >= 4 and preview_nodes() == [], f"{n} {preview_nodes()}")
    s.close()


def sec_s10_thumbnail_undo_does_not_revive_camera(tmp: Path) -> None:
    """S-10: サムネイルを作ったあとに Ctrl+Z しても、一時カメラが復活しない・Undo の履歴は汚れない。"""
    from maya import cmds

    from tdrive_facial import thumbnails

    ids, s = fresh(tmp)
    cmds.undoInfo(state=True)
    cmds.setAttr(f"{ids['bs']}.weight[0]", 0.2)  # ユーザーの作業
    name_before = cmds.undoInfo(query=True, undoName=True)

    def fake_render(cam, path, size):
        Path(path).write_bytes(b"png")
        return True

    thumbnails.capture(s, render=fake_render)
    check("S-10: サムネイルのあと一時カメラは無い", not cmds.ls("tdFacialThumbCam*"))
    cmds.undo()
    check("S-10: Ctrl+Z で一時カメラが復活しない（戻るのはユーザーの作業）", not cmds.ls("tdFacialThumbCam*") and abs(cmds.getAttr(f"{ids['bs']}.weight[0]")) < 1e-9, str(cmds.ls("tdFacialThumbCam*")))
    s.close()


def sec_s11_s12_s13_low_scene_fixes(tmp: Path) -> None:
    """S-11: write_target_delta が途中で失敗しても名前の無いターゲットが残らない / S-12: 中間形の重みが 0.9995 以上・0.0005 以下は受け付けない /
    S-13: 複数ジオメトリの blendShape で、変形していないメッシュの番号に 0 を返して別のメッシュへ書かない。"""
    import numpy as np
    from maya import cmds

    from tdrive_facial import scene, shapes

    ids, s = fresh(tmp)
    node = ids["bs"]
    # S-11
    before = (dict(scene.target_indices(node)), scene._all_indices(node))
    real = scene.cmds.setAttr

    def flaky(plug, *a, **kw):
        if str(plug).endswith(".inputComponentsTarget"):
            raise RuntimeError("setAttr failed")
        return real(plug, *a, **kw)

    scene.cmds.setAttr = flaky
    try:
        scene.write_target_delta(node, "FC_mini_Tmp_R0_C0", np.array([[0.0, 0.1, 0.0]]), [0], 0)
        raised = False
    except RuntimeError:
        raised = True
    finally:
        scene.cmds.setAttr = real
    after = (dict(scene.target_indices(node)), scene._all_indices(node))
    check("S-11: 書き込みが途中で失敗しても、名前の無いターゲット（weight の穴）が残らない", raised and after == before, f"{before} {after}")
    # S-12
    def code_of(w):
        try:
            shapes.inbetween_item(w)
        except shapes.ShapeError as e:
            return e.code
        return None

    check("S-12: 重み 0.9996 は本体（item 6000）を上書きするので受け付けない", code_of(0.9996) == "bad_weight" and code_of(0.0004) == "bad_weight")
    check("S-12: 0.5 は受け付ける（item 5500）", shapes.inbetween_item(0.5) == 5500 and shapes.inbetween_item(0.999) == 5999)
    # S-13
    tgt = cmds.duplicate(ids["face"], name="multi_target")[0]
    multi = cmds.blendShape(tgt, ids["face"], name="bs_multi")[0]
    cmds.blendShape(multi, edit=True, geometry=ids["brow"])
    other = cmds.polyCube(name="other_mesh")[0]
    check("S-13: 複数ジオメトリの blendShape で、各メッシュの番号が返る", scene.geometry_index(multi, ids["face"]) != scene.geometry_index(multi, ids["brow"]))
    try:
        scene.geometry_index(multi, other)
        wrong = True
    except ValueError:
        wrong = False
    check("S-13: 変形していないメッシュには番号を返さず ValueError（別のメッシュへ書かない）", not wrong)
    s.close()


def sec_v1_autokey_does_not_key_during_edit(tmp: Path) -> None:
    """（実機確認項目の一部を mayapy で）オートキーがオンでも、編集の出入り・ポーズの当て込みでジョイント・シェイプにキーが打たれない。"""
    from maya import cmds

    from tdrive_facial.core.model import BoneOffset

    import facial_fixture

    ids, s = fresh(tmp)
    cmds.autoKeyframe(state=True)
    try:
        s.begin_edit()
        s.select_point(1, 2)
        s.set_curve("bs.smile_L", 0.6)
        s.set_bone("eye_L", BoneOffset(r=facial_fixture._q_axis((0, 1, 0), 15.0)))
        s.end_edit()
        n = sum(len(cmds.keyframe(j, query=True) or []) for j in ids["joints"]) + len(cmds.keyframe(ids["bs"], query=True) or [])
        check("オートキーがオンでも、編集の出入り・当て込みでキーが打たれない", n == 0, str(n))
    finally:
        cmds.autoKeyframe(state=False)
    s.close()


def sec_s3b_other_deformers_warn(tmp: Path) -> None:
    """S-3（他のデフォーマ）: blendShape・スキン以外のデフォーマが効いているメッシュは、基準姿勢に入るとき警告する。"""
    from maya import cmds

    from tdrive_facial import scene

    ids, s = fresh(tmp)
    ref = scene.enter_reference_pose(["mini_face"])
    check("S-3: 他のデフォーマが無ければ警告しない", not any("デフォーマ" in w for w in ref.warnings), str(ref.warnings))
    ref.restore()
    cmds.select(f"{ids['face']}.vtx[0:20]")
    cmds.cluster(name="faceCluster")
    ref = scene.enter_reference_pose(["mini_face"])
    check("S-3: クラスタが効いているメッシュは警告する", any("デフォーマ" in w and "faceCluster" in w for w in ref.warnings), str(ref.warnings))
    ref.restore()
    s.close()


SECTIONS = [(n[4:], f) for n, f in sorted(globals().items()) if n.startswith("sec_") and callable(f)]


def main() -> None:
    wanted = sys.argv[1:]
    for name, fn in SECTIONS:
        if wanted and not any(w in name for w in wanted):
            continue
        tmp = Path(tempfile.mkdtemp(prefix="tdrive_frev_"))
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
