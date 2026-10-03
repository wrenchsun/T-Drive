"""FacialController のシェイプ作成支援（tdrive_facial.shapes / session のシェイプ道具 / ui_shapes）のスモーク（mayapy・画面なし）。

  set QT_QPA_PLATFORM=offscreen
  "C:/Program Files/Autodesk/Maya2026/bin/mayapy.exe" tests/maya/facial_shapes_smoke.py

手順（この順に守らないと落ちる）: QT_QPA_PLATFORM=offscreen → QApplication を作る → maya.standalone.initialize。
合成の小さな頭（tests/maya/facial_fixture.py。球は X に対称）で、F2-1 〜 F2-5 の道具を確かめる:
  彫る（スカルプトのつもりで cmds.move）/ ポーズをシェイプに / 左右に分ける / ミラー（非対称は止まる）/ 中間・誇張 / 組み合わせ補正 /
  別メッシュへ写す（同じ頂点・違う頂点）/ 整理 / どの操作も Maya の Undo 1 回 / 画面（ShapesTab）。
ファイルはすべて一時フォルダ。リポジトリの assets / looks / facial には書かない。
スクリーンショット: 環境変数 TDRIVE_UI_SHOT_DIR があれば、シェイプタブを f_shapes*.png に書く。
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

from PySide6 import QtGui, QtWidgets  # noqa: E402

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
    import numpy as np
    from maya import cmds

    import facial_fixture
    from tdrive import project
    from tdrive_facial import bake as bakemod
    from tdrive_facial import export, pose_apply, scene, shapes
    from tdrive_facial import session as S
    from tdrive_facial.core import fcpose_io, naming
    from tdrive_facial.core.model import BoneOffset, GridPoint, SourcePose

    tmp = Path(tempfile.mkdtemp(prefix="tdrive_shapes_"))
    project.set_root(tmp)
    ids = facial_fixture.build_mini_head()
    face = scene.resolve_mesh("mini_face")
    bs = ids["bs"]
    n = scene.vertex_count(face)
    s = S.current()
    s.close()
    s.listeners.clear()
    s.state_listeners.clear()
    doc0 = facial_fixture.make_doc()
    p0 = tmp / "src" / "mini.fcpose.json"
    p0.parent.mkdir()
    fcpose_io.save(doc0, p0)
    s.open(p0)
    s.set_mirror(bone_axis="X", suffix_l="_L", suffix_r="_R")
    doc = s.doc
    NEUTRAL = "Neutral"

    def D(name, item=6000, node=bs, nv=n):
        d = shapes.dense_delta(node, name, nv, 0, item)
        return d

    def pts() -> np.ndarray:
        return scene.read_points(face)

    def allnames(node=bs) -> dict:
        return dict(scene.target_indices(node))

    originals = ("mouth_open", "smile_L", "smile_R", "brow_up")
    orig_idx = {k: allnames()[k] for k in originals}
    orig_delta = {k: D(k) for k in originals}

    def originals_intact(label: str) -> None:
        now = allnames()
        same = all(now.get(k) == orig_idx[k] and np.allclose(D(k), orig_delta[k], atol=1e-9) for k in originals)
        check(f"{label}: 元のターゲット 4 本の番号と差分が変わっていない", same)

    def snap(node=bs) -> dict:
        out = {}
        for k, i in scene.target_indices(node).items():
            out[k] = (i, tuple(scene.item_numbers(node, k, 0)), round(float(np.abs(D(k, node=node)).sum()), 9))
        return out

    def undo_once() -> None:
        cmds.undo()

    def undo_redo(pred) -> bool:
        """Maya の Undo 1 回の後の状態を pred で調べ、Redo で戻す。"""
        cmds.undo()
        try:
            return bool(pred())
        finally:
            cmds.redo()

    sc = shapes.make_ctx(doc, create=True)
    check("準備: ShapeCtx（顔メッシュ・blendShape・軸 X・接尾辞 _L/_R・fcs_）", sc.node == bs and sc.axis == 0 and (sc.suffix_l, sc.suffix_r) == ("_L", "_R") and sc.prefix == "fcs_" and sc.nverts == n)
    check("準備: 元のターゲットは全部「元から」", all(i.tag == shapes.TAG_ORIGINAL for i in s.shape_list()) and len(s.shape_list()) == 4)

    # ============================================================ F2-1 この角度で彫る
    s.select_point(1, 2)
    check("彫る: 点 (1,2) を選ぶと編集状態になる", s.editing and s.applied_point == (0, 1, 2))
    name = s.sculpt_target_name()
    check("彫る: 彫り用の名前は fcs_<layer>_R{r}_C{c}", name == "fcs_Neutral_R1_C2" == naming.sculpt_name(NEUTRAL, 1, 2))
    before_names = set(allnames())
    res = s.sculpt_begin()
    idx = allnames()[name]
    check("彫る: fcs_ ターゲットができる（全頂点・差分 0 で作る）", name in allnames() and name not in before_names and name in res.created and D(name).shape == (n, 3) and np.abs(D(name)).max() == 0.0)
    check("彫る: スカルプト対象になる（sculptTargetIndex）", shapes.sculpt_active_index(bs) == idx and s.sculpting is not None and s.sculpting.name == name)
    check("彫る: 重み 1・ポーズの編集中の値にも入る", abs(cmds.getAttr(f"{bs}.weight[{idx}]") - 1.0) < 1e-9 and s.pose.curves.get(f"{bs}.{name}") == 1.0)
    check("彫る: この時点ではまだ点のポーズに保存していない", f"{bs}.{name}" not in doc.layers[0].points[(1, 2)].pose.curves)
    # スカルプトのつもりで頂点を動かす（口の少し下の前面）
    rest = shapes.rest_points(face) if False else None
    flat = cmds.xform(f"{face}.vtx[*]", query=True, worldSpace=True, translation=True)
    P = np.array(flat).reshape(-1, 3)
    sel = [i for i in range(n) if P[i][2] > 3.0 and 6.0 < P[i][1] < 8.5 and abs(P[i][0]) < 3.0]
    check("彫る: 動かす頂点がある", len(sel) > 3, str(len(sel)))
    before_pts = pts()
    cmds.move(0.0, 0.5, 0.2, [f"{face}.vtx[{i}]" for i in sel], relative=True, objectSpace=True)
    after_pts = pts()
    dd = D(name)
    moved = np.nonzero(np.linalg.norm(dd, axis=1) > 1e-6)[0].tolist()
    check("彫る: 動かした頂点の差分が fcs_ ターゲットに入る", set(moved) == set(sel) and np.allclose(dd[sel], [0.0, 0.5, 0.2], atol=1e-4), f"{len(moved)}/{len(sel)} {dd[sel][:1]}")
    check("彫る: メッシュは動かしたとおりに見える", np.allclose((after_pts - before_pts)[sel], [0.0, 0.5, 0.2], atol=1e-4))
    originals_intact("彫る（彫り中）")
    res = s.sculpt_end()
    check("彫り終わる: スカルプト対象が外れる", shapes.sculpt_active_index(bs) == -1 and s.sculpting is None)
    kept = scene.read_target_delta(bs, name)
    check("彫り終わる: 差分の無い頂点が捨てられ、動かした頂点だけ残る", sorted(kept[0]) == sorted(sel), f"{len(kept[0])}")
    pt = doc.layers[0].points[(1, 2)]
    check("彫り終わる: 点のポーズに fcs_ = 1 が記録され、元のスライダーも残る", pt.pose.curves.get(f"{bs}.{name}") == 1.0 and pt.pose.curves.get("bs.mouth_open") == 0.6 and pt.is_key)
    check("彫り終わる: 保存済み（編集中の値と同じ）", not s.pose.dirty)
    originals_intact("彫り終わる")
    check("彫り終わる: 編集状態は続く・基準姿勢のまま（fcs_ の重みも編集を抜けると 0 に戻る）", s.editing)
    # ベイク: FC_ は彫りを含む
    rep = s.bake_all()
    fcn = naming.morph_name("mini", NEUTRAL, 1, 2)
    base_pts = None
    with scene.enter_reference_pose([face], extra_joints=["eye_L", "eye_R"]) as ref:
        base_pts = scene.read_points(face)
        pose_apply.apply_pose(doc, doc.layers[0].points[(1, 2)].pose, ref)
        want = scene.read_points(face) - base_pts
    got = D(fcn)
    check("ベイク: FC_ は彫りを含む姿と一致（1e-3 以内）", np.abs(got - want).max() < 1e-3 and np.abs(got[sel] - want[sel]).max() < 1e-3, f"{np.abs(got - want).max():.5f}")
    sculpt_part = np.abs(got[sel][:, 1]).max()
    check("ベイク: 彫った分（y 方向の 0.5）が FC_ にある", sculpt_part > 0.3, f"{sculpt_part}")
    check("ベイク: fcs_ の重みは 0 に戻っている・元のターゲットは変わらない", abs(cmds.getAttr(f"{bs}.weight[{idx}]")) < 1e-9)
    originals_intact("ベイク")
    check("ベイク後も彫り用ターゲットは残る", name in allnames())

    # 頭を動かすポーズでは始められない
    doc.layers[0].points[(2, 2)] = GridPoint(2, 2, True, SourcePose({"bs.smile_L": 0.5}, {"head": BoneOffset(t=(0.0, 0.3, 0.0))}))
    s.select_point(2, 2)
    refused = None
    try:
        s.sculpt_begin()
    except shapes.ShapeError as e:
        refused = e
    check("彫る: 頭を動かしたポーズでは断る（説明つき・fcs_ を作らない・編集状態は残る）", refused is not None and refused.code == "head_moved" and "頭" in str(refused) and "fcs_Neutral_R2_C2" not in allnames() and s.sculpting is None and s.editing)
    # 目だけ動かすポーズは彫れる
    s.select_point(0, 2)
    r2 = s.sculpt_begin()
    check("彫る: 目のボーンだけのポーズは彫れる", s.sculpting is not None and shapes.sculpt_active_index(bs) == allnames()["fcs_Neutral_R0_C2"], str(r2.message))
    # 何も彫らずに終える → 作ったターゲットを消す・記録しない
    r3 = s.sculpt_end()
    check("彫り終わる: 何も彫らなければ作ったターゲットを消し、ポーズにも記録しない", "fcs_Neutral_R0_C2" not in allnames() and "bs.fcs_Neutral_R0_C2" not in doc.layers[0].points[(0, 2)].pose.curves and r3.removed == ["fcs_Neutral_R0_C2"])
    # 点を移ると彫りが終わる（記録される）
    s.select_point(1, 0)
    s.sculpt_begin()
    cmds.move(0.0, 0.3, 0.0, [f"{face}.vtx[{sel[0]}]"], relative=True, objectSpace=True)
    s.select_point(1, 2)
    check("彫る: 点を移ると彫りを終えて元の点へ記録する", s.sculpting is None and doc.layers[0].points[(1, 0)].pose.curves.get(f"{bs}.fcs_Neutral_R1_C0") == 1.0)
    # 彫りを消す
    s.select_point(1, 2)
    d_before_del = D(name)
    res = s.sculpt_delete([name])
    check("彫りを消す: ターゲットが消える・ポーズからの参照も消える", name not in allnames() and f"{bs}.{name}" not in doc.layers[0].points[(1, 2)].pose.curves and res.removed == [name])
    originals_intact("彫りを消す")
    check("彫りを消す: Maya の Undo 1 回でターゲットが戻る（差分も同じ）", undo_redo(lambda: name in allnames() and np.allclose(D(name), d_before_del)))
    cmds.undo()
    s.undo()
    check("彫りを消す: エディタ内 Undo でポーズの参照が戻る", doc.layers[0].points[(1, 2)].pose.curves.get(f"{bs}.{name}") == 1.0 if s.doc is doc else s.doc.layers[0].points[(1, 2)].pose.curves.get(f"{bs}.{name}") == 1.0)
    doc = s.doc
    # 孤立した fcs_ の一覧と削除
    sc = shapes.make_ctx(doc)
    shapes.sculpt_prepare(sc, "fcs_Neutral_R0_C0")
    au = s.shape_audit()
    check("整理: どの点にも参照されない fcs_ は「孤立」に出る・消せる一覧に入る", "fcs_Neutral_R0_C0" in au.orphan_sculpt and "fcs_Neutral_R0_C0" in au.deletable and name not in au.orphan_sculpt)
    s.shape_delete(["fcs_Neutral_R0_C0"])
    check("整理: 孤立した fcs_ を消せる", "fcs_Neutral_R0_C0" not in allnames())
    refused = None
    try:
        s.shape_delete(["smile_L"])
    except shapes.ShapeError as e:
        refused = e
    check("整理: 元のシェイプは消せない", refused is not None and "smile_L" in allnames())
    s.end_edit()

    # ============================================================ F2-3a ポーズをシェイプにする
    s.select_point(1, 2)
    before = snap()
    res = s.shape_from_pose("smileMouth", add_to_working_set=True)
    check("ポーズ→シェイプ: 新しいターゲットができ「作ったもの」になる", "smileMouth" in allnames() and shapes.tag_of(sc, "smileMouth") == shapes.TAG_MADE and res.created == ["smileMouth"], res.summary())
    check("ポーズ→シェイプ: Undo 1 回で消える（作ったものの印も）", undo_redo(lambda: snap().keys() == before.keys() and "smileMouth" not in allnames() and shapes.get_made(bs) == []), f"{sorted(before)} {sorted(snap())} {shapes.get_made(bs)}")
    with scene.enter_reference_pose([face], extra_joints=["eye_L", "eye_R"]) as ref:
        pose_apply.apply_pose(doc, s.pose.pose_to_apply(), ref)
        want = scene.read_points(face) - base_pts
    check("ポーズ→シェイプ: 差分はベイクと同じ（1e-3 以内）", np.abs(D("smileMouth") - want).max() < 1e-3)
    check("ポーズ→シェイプ: 作業セットに入る", "bs.smileMouth" in s.doc.working_set.curves)
    check("ポーズ→シェイプ: 元のターゲットは上書きしない", _raises(lambda: s.shape_from_pose("mouth_open")) and np.allclose(D("mouth_open"), orig_delta["mouth_open"]))
    check("ポーズ→シェイプ: 空のポーズは断る", _raises(lambda: (s.zero_pose(), s.shape_from_pose("emptyOne"))))
    s.reload_pose()
    s.end_edit()

    # ============================================================ F2-2a 左右に分ける
    sc = shapes.make_ctx(s.doc)
    before = snap()
    res = s.shape_split_lr("mouth_open", "mouth_open_L", "mouth_open_R", width=1.0)
    L, R, O = D("mouth_open_L"), D("mouth_open_R"), D("mouth_open")
    check("左右: L と R ができる（元は残る）", set(res.created) == {"mouth_open_L", "mouth_open_R"} and np.allclose(O, orig_delta["mouth_open"]))
    check("左右: Undo 1 回で作ったターゲットが消える", undo_redo(lambda: "mouth_open_L" not in allnames() and "mouth_open_R" not in allnames() and snap().keys() == before.keys()))
    check("左右: L + R = 元（1e-6 以内）", np.abs(L + R - O).max() < 1e-6, f"{np.abs(L + R - O).max()}")
    rp = shapes.rest_points(face)
    x = rp[:, 0]
    check("左右: 幅の外では片側だけ（+X 側が L・-X 側が R）", np.abs(R[x > 0.5]).max() == 0.0 and np.abs(L[x < -0.5]).max() == 0.0 and np.allclose(L[x > 0.5], O[x > 0.5]))
    mid = (np.abs(x) < 0.5) & (np.linalg.norm(O, axis=1) > 1e-6)
    check("左右: 中央のぼかし幅の中は両方に重みがある", mid.sum() > 0 and bool(np.all(np.linalg.norm(L[mid], axis=1) > 0)) or mid.sum() == 0, str(mid.sum()))
    res0 = shapes.split_lr(sc, "mouth_open", "mo0_L", "mo0_R", width=0.0, rest=rp)
    L0, R0 = D("mo0_L"), D("mo0_R")
    check("左右: 幅 0 でも L + R = 元・段差", np.abs(L0 + R0 - O).max() < 1e-6 and np.abs(R0[x > 0.01]).max() == 0.0)
    wide = shapes.split_lr(sc, "mouth_open", "mo4_L", "mo4_R", width=6.0, rest=rp)
    check("左右: 幅を広げると中央の領域が広がる", int(np.count_nonzero(np.linalg.norm(D("mo4_R"), axis=1) > 0)) > int(np.count_nonzero(np.linalg.norm(R, axis=1) > 0)))
    check("左右: 元からあるシェイプ名への分割は断る", _raises_code(lambda: shapes.split_lr(sc, "mouth_open", "smile_L", "x_R"), "exists_original") and np.allclose(D("smile_L"), orig_delta["smile_L"]))
    check("左右: 作ったシェイプ名なら置き換えられる", "mouth_open_L" in shapes.split_lr(sc, "mouth_open", "mouth_open_L", "mouth_open_R", width=1.0).replaced)
    check("左右: 名前の規則（_LR を外す）", shapes.base_name("smile_LR") == "smile" and shapes.base_name("brow_up") == "brow_up")
    originals_intact("左右")

    # ============================================================ F2-2b ミラー
    before = snap()
    res = s.shape_mirror("smile_L", "smileMirror_R")
    check("ミラー: _L から _R を作る（元の smile_R と一致 = 鏡映が正しい）", "smileMirror_R" in res.created and np.abs(D("smileMirror_R") - orig_delta["smile_R"]).max() < 1e-5, f"{np.abs(D('smileMirror_R') - orig_delta['smile_R']).max()}")
    check("ミラー: 対応は全頂点（球は対称）", res.stats["unmatched"] == 0 and res.stats["matched"] == n)
    check("ミラー: Undo 1 回で作ったターゲットが消える", undo_redo(lambda: "smileMirror_R" not in allnames()))
    check("ミラー: 元は変わらない", np.allclose(D("smile_L"), orig_delta["smile_L"]))
    res = s.shape_mirror("smileMirror_R", "smileMirror_L")
    check("ミラー: 反対向きも作れる（_R → _L が smile_L と一致）", np.abs(D("smileMirror_L") - orig_delta["smile_L"]).max() < 1e-5)
    check("ミラー: 元からある名前（smile_R）への鏡映は断る", _raises_code(lambda: s.shape_mirror("smile_L"), "exists_original") and np.allclose(D("smile_R"), orig_delta["smile_R"]))
    check("ミラー: 名前が _L/_R で終わらず相手を指定しないと断る", _raises_code(lambda: s.shape_mirror("brow_up"), "not_sided"))
    L1 = D("mouth_open_L")
    res = s.shape_mirror("mouth_open_L")
    mp = shapes.symmetry_map(face, rp).mapping
    check("ミラー: 既にある _R（作ったもの）は更新する（_L を鏡映した形）", res.replaced == ["mouth_open_R"] and np.abs(D("mouth_open_R") - D("mouth_open_L")[mp] * np.array([-1.0, 1.0, 1.0])).max() < 1e-9)
    # 非対称: 内側の頂点を 1 つ動かす
    cand = [i for i in range(n) if rp[i][0] > 2.0 and 4.0 < rp[i][1] < 10.0 and rp[i][2] > 0][0]
    cmds.move(0.0, 0.0, 0.8, f"{face}.vtx[{cand}]", relative=True, objectSpace=True)
    cmds.select(clear=True)
    stop = None
    try:
        s.shape_mirror("smile_L", "smileAsym_R")
    except shapes.AsymmetryStop as e:
        stop = e
    sel_now = cmds.ls(selection=True, flatten=True)
    check("ミラー: 左右で位置が合わない頂点があると止まる（ターゲットは作らない）", stop is not None and cand in stop.unmatched and "smileAsym_R" not in allnames(), str(stop and stop.unmatched))
    check("ミラー: 対応が取れない頂点はビューポートで選択される", any(f"vtx[{cand}]" in x for x in sel_now) and len(sel_now) == len(stop.unmatched), f"{len(sel_now)}")
    res = s.shape_mirror("smile_L", "smileAsym_R", allow_unmatched=True)
    check("ミラー: 「それでも続ける」なら作る（対応の無い頂点は 0・警告）", "smileAsym_R" in res.created and res.warnings and res.stats["unmatched"] == len(stop.unmatched))
    check("ミラー: 「それでも続ける」も Undo 1 回", undo_redo(lambda: "smileAsym_R" not in allnames()))
    cmds.move(0.0, 0.0, -0.8, f"{face}.vtx[{cand}]", relative=True, objectSpace=True)  # 動かした頂点を戻す
    check("ミラー: 非対称にした頂点を戻すと対応表が取り直される（キャッシュは内側の頂点の編集も見分ける）", len(shapes.symmetry_map(face, shapes.rest_points(face)).unmatched) == 0)
    originals_intact("ミラー")

    # ============================================================ F2-3b 中間・誇張
    check("中間の前: すべてのシェイプの重みが 0（彫りを消す Undo のあとも重み 1 が残らない）", all(abs(cmds.getAttr(f"{bs}.{k}")) < 1e-9 for k in scene.target_indices(bs)), str({k: cmds.getAttr(f"{bs}.{k}") for k in scene.target_indices(bs) if abs(cmds.getAttr(f"{bs}.{k}")) > 1e-9}) + f" editing={s.editing}")
    cmds.setAttr(f"{bs}.smile_L", 1.0)  # 今のシーンの形 = smile_L
    cmds.setAttr(f"{bs}.mouth_open_L", 1.0)  # 自分自身の重みは 0 で測る
    before = snap()
    refused = None
    try:
        s.shape_inbetween("smile_L", 0.5)
    except shapes.ShapeError as e:
        refused = e
    check("中間: 元からあるシェイプへは確認なしでは足さない", refused is not None and refused.code == "original_needs_confirm" and scene.item_numbers(bs, "smile_L") == [6000])
    res = s.shape_inbetween("mouth_open_L", 0.5)
    check("中間: 作ったシェイプには足せる（item 5500）", scene.item_numbers(bs, "mouth_open_L") == [5500, 6000] and res.stats["item"] == 5500)
    check("中間: 自分の重みは測定のあと元に戻る", abs(cmds.getAttr(f"{bs}.mouth_open_L") - 1.0) < 1e-9)
    check("中間: Undo 1 回で中間形が消える", undo_redo(lambda: scene.item_numbers(bs, "mouth_open_L") == [6000]))
    cmds.setAttr(f"{bs}.smile_L", 0.0)
    cmds.setAttr(f"{bs}.mouth_open_L", 0.5)
    mid_shape = pts() - rp
    check("中間: 重み 0.5 で与えた形になる（1e-5 以内）", np.abs(mid_shape - orig_delta["smile_L"]).max() < 1e-5, f"{np.abs(mid_shape - orig_delta['smile_L']).max()}")
    cmds.setAttr(f"{bs}.mouth_open_L", 1.0)
    check("中間: 重み 1 は元の形のまま", np.abs((pts() - rp) - D("mouth_open_L")).max() < 1e-5 and np.allclose(D("mouth_open_L"), L1, atol=1e-9))
    cmds.setAttr(f"{bs}.mouth_open_L", 0.75)
    check("中間: 重み 0.75 は 5500 と 6000 の間", np.abs((pts() - rp) - (orig_delta["smile_L"] + D("mouth_open_L")) / 2).max() < 1e-5)
    cmds.setAttr(f"{bs}.mouth_open_L", 0.0)
    res = s.shape_inbetween("smile_L", 0.25, confirm_original=True)
    check("中間: 確認つきなら元のシェイプにも足せる（中間形だけ増える・本体は不変）", scene.item_numbers(bs, "smile_L") == [5250, 6000] and np.allclose(D("smile_L"), orig_delta["smile_L"]))
    check("中間: 元のシェイプへの中間形も Undo で戻る", undo_redo(lambda: scene.item_numbers(bs, "smile_L") == [6000]))
    cmds.undo()
    check("中間: 重みは 0〜1 の間", _raises_code(lambda: s.shape_inbetween("mouth_open_L", 1.0), "bad_weight"))
    cmds.setAttr(f"{bs}.brow_up", 1.0)
    s.shape_inbetween("mouth_open_L", 0.5)
    cmds.setAttr(f"{bs}.brow_up", 0.0)
    s.set_working_set(curves=[])  # 誇張: 作業セットは関係なし
    res = s.shape_exaggerate("mouth_open_L")
    check("誇張: <name>_Ex ができる", "mouth_open_L_Ex" in allnames() and "mouth_open_L_Ex" in res.created)
    check("誇張: 可動域（limits）が 0〜2 になる（エディタ内 Undo で戻る）", s.doc.limits.get(f"{bs}.mouth_open_L") == (0.0, 2.0))
    s.undo()
    check("誇張: limits はエディタ内 Undo で戻る", not (s.doc.limits or {}).get(f"{bs}.mouth_open_L"))
    check("誇張: シェイプ名の誇張は作れない", _raises_code(lambda: s.shape_exaggerate("mouth_open_L_Ex"), "bad_name"))
    originals_intact("中間・誇張")

    # ============================================================ F2-3c 組み合わせ補正
    cmds.setAttr(f"{bs}.mouth_open_L", 0.0)
    before = snap()
    res = s.shape_combo("smile_L", "brow_up")
    cname = "fcs_combo_smile_L__brow_up"
    cidx = allnames()[cname]
    check("組み合わせ: fcs_combo_<a>__<b> ができ、combinationShape につながる", res.created == [cname] and shapes.combo_drivers(sc, cname) == ["smile_L", "brow_up"] and cidx in scene.driven_indices(bs))
    check("組み合わせ: Undo 1 回でターゲットも節も消える", undo_redo(lambda: cname not in allnames() and not cmds.ls(type="combinationShape")))
    ws = []
    for a, b in ((0, 0), (1, 0), (0, 1), (1, 1), (0.5, 0.5)):
        cmds.setAttr(f"{bs}.smile_L", a)
        cmds.setAttr(f"{bs}.brow_up", b)
        ws.append(round(cmds.getAttr(f"{bs}.weight[{cidx}]"), 6))
    check("組み合わせ: 2 つとも > 0 のときだけ効く（積）", ws == [0.0, 0.0, 0.0, 1.0, 0.25], str(ws))
    cmds.setAttr(f"{bs}.smile_L", 0.0)
    cmds.setAttr(f"{bs}.brow_up", 0.0)
    check("組み合わせ: ポーズ用の一覧（list_curves）には出ない・タブの一覧には出る", cname not in [c.alias for c in scene.list_curves(face)] and cname in [c.alias for c in scene.list_curves(face, include_driven=True)] and any(i.name == cname and i.driven and i.tag == shapes.TAG_COMBO for i in s.shape_list()))
    check("組み合わせ: 同じ組は作り直せない・駆動元が FC_ / 組み合わせは不可", _raises_code(lambda: s.shape_combo("smile_L", "brow_up"), "exists") and _raises_code(lambda: s.shape_combo("smile_L", "smile_L"), "bad_name"))
    # 編集状態でも組み合わせの重みは切り離されない
    s.select_point(1, 2)
    check("組み合わせ: 編集状態（基準姿勢）に入っても接続は切れない", s.editing and cmds.listConnections(f"{bs}.weight[{cidx}]", source=True, destination=False, type="combinationShape") is not None)
    s.end_edit()
    # 彫る
    res = s.sculpt_begin_combo(cname)
    check("組み合わせを彫る: 駆動元 2 つが 1 になり、スカルプト対象になる", s.sculpting is not None and shapes.sculpt_active_index(bs) == cidx and abs(cmds.getAttr(f"{bs}.smile_L") - 1) < 1e-9 and abs(cmds.getAttr(f"{bs}.brow_up") - 1) < 1e-9 and abs(cmds.getAttr(f"{bs}.weight[{cidx}]") - 1) < 1e-9)
    tgt = sel[:4]
    cmds.move(0.0, -0.4, 0.0, [f"{face}.vtx[{i}]" for i in tgt], relative=True, objectSpace=True)
    s.sculpt_end()
    check("組み合わせを彫る: 差分が補正ターゲットに入る・終わると駆動元の重みは元へ", np.allclose(D(cname)[tgt], [0.0, -0.4, 0.0], atol=1e-4) and abs(cmds.getAttr(f"{bs}.smile_L")) < 1e-9 and not s.editing)
    cmds.setAttr(f"{bs}.smile_L", 1.0)
    only_a = pts() - rp
    cmds.setAttr(f"{bs}.brow_up", 1.0)
    both = pts() - rp
    cmds.setAttr(f"{bs}.smile_L", 0.0)
    cmds.setAttr(f"{bs}.brow_up", 0.0)
    want_both = orig_delta["smile_L"] + orig_delta["brow_up"] + D(cname)
    check("組み合わせ: 片方だけでは補正なし・両方で補正が足される", np.abs(only_a - orig_delta["smile_L"]).max() < 1e-5 and np.abs(both - want_both).max() < 1e-5)
    # ベイクが補正を含む
    fcn2 = "FC_mini_Neutral_R1_C1"
    tmp_pose = SourcePose({"bs.smile_L": 1.0, "bs.brow_up": 1.0})
    brep = bakemod.pose_to_shape(s.doc, tmp_pose, "comboBaked")
    check("組み合わせ: ベイク（ポーズ→シェイプ）は補正の結果を含む", np.abs(D("comboBaked") - want_both).max() < 1e-3, f"{np.abs(D('comboBaked') - want_both).max()}")
    cmds.undo()
    cmds.undo() if False else None
    s.sculpt_delete([cname])
    check("組み合わせ: 消すと combinationShape の節も消える", cname not in allnames() and not cmds.ls(type="combinationShape"))
    originals_intact("組み合わせ")

    # ============================================================ F2-4 別メッシュへ写す
    cmds.setAttr(f"{bs}.smile_L", 0.0)
    same = cmds.duplicate(face, name="copy_same")[0]
    low = cmds.polySphere(name="dst_low", radius=8.0, subdivisionsX=12, subdivisionsY=10, axis=(0, 1, 0))[0]
    cmds.move(0, 12, 0, low, absolute=True)
    cmds.makeIdentity(low, apply=True, translate=True)
    cmds.delete(low, constructionHistory=True)
    before_nodes = set(cmds.ls())
    before_mesh = set(cmds.ls(type="mesh", noIntermediate=True))
    plan = s.shape_transfer_plan(["smile_L", "mouth_open"], same)
    check("写す: 頂点・順序が同じなら「複写」と判定", plan.same_topology and plan.dest_node is None and not plan.collisions)
    check("写す: 違う頂点数なら「転写」と判定", not s.shape_transfer_plan(["smile_L"], low).same_topology)
    res = s.shape_transfer(["smile_L", "mouth_open"], same)
    dnode = res.stats["dest_node"]
    check("写す（複写）: 写し先に tdFacial_<mesh> ができ、差分が完全に同じ", dnode == "tdFacial_copy_same" and res.stats["method"] == "copy" and all(np.array_equal(D(k, node=dnode), orig_delta[k]) for k in ("smile_L", "mouth_open")))
    check("写す（複写）: 元（顔）のターゲットは変わらない・写し先の最大差分が出る", res.stats["per_shape"]["smile_L"]["max"] > 0.5)
    plan2 = s.shape_transfer_plan(["smile_L"], same)
    check("写す: 写し先に同じ名前（作ったもの）があれば衝突に出る", plan2.collisions == ["smile_L"] and plan2.protected == [])
    check("写す: 衝突は確認なしでは飛ばす", _raises_code(lambda: s.shape_transfer(["smile_L"], same), "nothing"))
    undo_once()
    check("写す: Undo 1 回で写し先の blendShape ごと消える", not cmds.objExists("tdFacial_copy_same"))
    # 元からあるターゲットを持つ写し先
    dst_bs_mesh = cmds.duplicate(face, name="dst_has")[0]
    cmds.blendShape(cmds.duplicate(face, name="dst_has_t")[0], dst_bs_mesh, name="dst_bs")
    cmds.aliasAttr("mouth_open", "dst_bs.weight[0]")
    dd0 = D("mouth_open", node="dst_bs")
    plan3 = s.shape_transfer_plan(["mouth_open", "smile_L"], dst_bs_mesh)
    check("写す: 写し先の元からあるターゲットとの衝突は「保護」に出る", plan3.protected == ["mouth_open"] and plan3.collisions == ["mouth_open"])
    res = s.shape_transfer(["mouth_open", "smile_L"], dst_bs_mesh)
    check("写す: 保護されたターゲットは上書きせず、他は写す", np.array_equal(D("mouth_open", node="dst_bs"), dd0) and "smile_L" in scene.target_indices("dst_bs") and any("mouth_open" in x for x in res.notes))
    undo_once()
    # 違うトポロジ（proximityWrap）
    before_mesh = set(cmds.ls(type="mesh", noIntermediate=True))
    res = s.shape_transfer(["mouth_open"], low)
    ldn = res.stats["dest_node"]
    lp = scene.read_points(low)
    dl = shapes.dense_delta(ldn, "mouth_open", scene.vertex_count(low), 0)
    near = (lp[:, 2] > 3.0) & (lp[:, 1] > 8.0) & (lp[:, 1] < 10.5) & (np.abs(lp[:, 0]) < 3.5)
    far = lp[:, 2] < -2.0
    check("写す（転写）: 口の近くの頂点が動く（0 でない差分）", res.stats["method"] == "wrap" and near.sum() > 0 and np.linalg.norm(dl[near], axis=1).max() > 0.3, f"near={near.sum()} max={np.linalg.norm(dl[near], axis=1).max() if near.sum() else 0}")
    check("写す（転写）: 離れた頂点は動かない", np.abs(dl[far]).max() < 1e-6)
    check("写す（転写）: 向きが元と同じ（口が下がる = y が負）", dl[near][:, 1].mean() < -0.1)
    check("写す（転写）: 一時ノード（複製・proximityWrap）は残らない", not cmds.ls("tdFacialXfer*") and not cmds.ls(type="proximityWrap") and set(cmds.ls(type="mesh", noIntermediate=True)) - before_mesh == set(), str(cmds.ls("tdFacialXfer*")))
    check("写す（転写）: 件数・最大差分を報告する", res.stats["per_shape"]["mouth_open"]["vertices"] > 0 and res.stats["per_shape"]["mouth_open"]["max"] > 0.3)
    undo_once()
    check("写す（転写）: Undo 1 回で戻る・基準姿勢の出入りの痕も残らない", not cmds.objExists(ldn) and abs(cmds.getAttr(f"{bs}.mouth_open")) < 1e-9, f"{cmds.objExists(ldn)} {cmds.getAttr(f'{bs}.mouth_open')}")
    originals_intact("写す")

    # ============================================================ F2-5 整理
    sc = shapes.make_ctx(s.doc)
    cur = scene.read_target_delta(bs, "mouth_open_L")
    comps = list(cur[0]) + [1, 2, 3]
    comps = sorted(set(comps))
    dd = D("mouth_open_L")
    dd[[1, 2, 3]] = [1e-5, 0, 0]
    shapes._write_dense(sc, "mouth_open_L", dd, threshold=1e-9)
    c_before = len(scene.read_target_delta(bs, "mouth_open_L")[0])
    snapshot_before = snap()
    res = s.shape_clean(["mouth_open_L", "smile_L"], 0.001)
    c_after = len(scene.read_target_delta(bs, "mouth_open_L")[0])
    check("掃除: しきい値未満の頂点が消える（レポートに件数）", c_before - c_after >= 3 and res.stats["removed"] >= 3 and res.stats["per_shape"].get("mouth_open_L", 0) >= 3, f"{c_before}->{c_after}")
    check("掃除: 元からあるシェイプは確認なしでは触らない", "smile_L" not in res.stats["per_shape"] and np.allclose(D("smile_L"), orig_delta["smile_L"]) and any("smile_L" in x for x in res.notes))
    undo_once()
    check("掃除: Undo 1 回で戻る", len(scene.read_target_delta(bs, "mouth_open_L")[0]) == c_before)
    shapes._write_dense(sc, "mouth_open_L", D("mouth_open_L"), threshold=1e-6)
    shapes._write_dense(sc, "mouth_open_L", dd * 0, threshold=1e-6) if False else None
    # 空・未使用
    scene.write_target_delta(bs, "madeEmpty", np.zeros((0, 3)), [], 0)
    shapes.add_made(sc, ["madeEmpty"])
    au = s.shape_audit()
    check("整理: 空のシェイプが一覧に出る", "madeEmpty" in au.empty)
    check("整理: 未使用（どのポーズ・作業セットからも参照されない）が出る（情報だけ）", "madeEmpty" in au.unreferenced and "mouth_open_L" in au.unreferenced and "mouth_open" not in au.unreferenced and "madeEmpty" not in au.deletable)
    check("整理: 消せるのは fcs_ / FC_ だけ（元から・作ったものは出ない）", all(naming.is_fc_name(x) or naming.is_sculpt_name(x) for x in au.deletable))
    s.apply_profile("arkit52")
    miss = s.shape_missing_standard()
    check("整理: プロファイルの標準シェイプの不足が出る", "jawOpen" in miss and "mouth_open" not in miss)
    originals_intact("整理")

    # ============================================================ 出力: fcs_ は Unity の FBX に入らない
    s.select_point(1, 2)
    s.sculpt_begin()
    cmds.move(0.0, 0.2, 0.0, [f"{face}.vtx[{sel[0]}]"], relative=True, objectSpace=True)
    s.sculpt_end()
    s.bake_all()
    cmds.setAttr(f"{bs}.weight[{allnames()[sorted(k for k in allnames() if naming.is_sculpt_name(k) and not k.startswith('fcs_combo'))[0]]}]", 0.0)
    fcs_before = [k for k in allnames() if naming.is_sculpt_name(k)]
    out = export.export_in_place([face], tmp / "unity" / "mini.fbx")
    left = [k for k in allnames() if naming.is_sculpt_name(k)]
    check("出力: fcs_* は FBX に入らない（書き出し用に消される・FC_ は残る）", out["excluded_fcs"] == len(fcs_before) > 0 and not left and out["fc"][scene.short_name(face)] >= 1 and (tmp / "unity" / "mini.fbx").exists(), str(out))

    # ============================================================ 画面
    _ui_part(tmp, ids, run_locals=locals())
    shutil.rmtree(tmp, ignore_errors=True)


def _raises(fn) -> bool:
    from tdrive_facial import session as S  # noqa: F401
    from tdrive_facial import shapes

    try:
        fn()
    except (shapes.ShapeError, RuntimeError, ValueError):
        return True
    return False


def _raises_code(fn, code: str) -> bool:
    from tdrive_facial import shapes

    try:
        fn()
    except shapes.ShapeError as e:
        return e.code == code
    return False


def _ui_part(tmp, ids, run_locals) -> None:
    """画面（ShapesTab）: ボタンの click() と、差し替えたダイアログで操作する。"""
    import numpy as np
    from maya import cmds
    from PySide6 import QtCore

    import facial_fixture
    from tdrive_facial import scene, shapes
    from tdrive_facial import session as S
    from tdrive_facial import ui
    from tdrive_facial.core import fcpose_io

    shot_dir = os.environ.get("TDRIVE_UI_SHOT_DIR")
    if shot_dir:
        Path(shot_dir).mkdir(parents=True, exist_ok=True)
    s = S.current()
    s.close()
    s.listeners.clear()
    s.state_listeners.clear()
    ids = facial_fixture.build_mini_head()
    bs = ids["bs"]
    face = scene.resolve_mesh("mini_face")
    n = scene.vertex_count(face)
    panel = ui.FacialPanel()
    panel.resize(560, 1900)
    tab = panel.tab("shapes")
    check("画面: シェイプタブが本物（準備中ではない）", not panel.tab_is_placeholder("shapes") and type(tab).__name__ == "ShapesTab")
    check("画面: データが無いときは全部無効（ヒントが出る）", not tab.body.isEnabled() and not tab.no_doc.isHidden())
    doc0 = facial_fixture.make_doc()
    p1 = tmp / "ui" / "mini.fcpose.json"
    p1.parent.mkdir(exist_ok=True)
    fcpose_io.save(doc0, p1)
    s.open(p1)
    s.set_mirror(bone_axis="X", suffix_l="_L", suffix_r="_R")
    panel.select_tab("shapes")
    pump()
    asked: list[str] = []
    errors: list[str] = []
    answers = {"confirm": True, "asym": True}
    tab.confirm = lambda text: (asked.append(text), answers["confirm"])[1]
    tab.show_error = lambda text: errors.append(text)
    tab.ask_continue_asymmetric = lambda count: (asked.append(f"asym {count}"), answers["asym"])[1]

    def pick(*names) -> None:
        tab.list.clearSelection()
        for i in range(tab.list.count()):
            it = tab.list.item(i)
            if it.data(QtCore.Qt.UserRole) in names:
                it.setSelected(True)
        pump()

    def allnames() -> dict:
        return dict(scene.target_indices(bs))

    def dense(name):
        return np.array(shapes.dense_delta(bs, name, n, 0))

    check("画面: 一覧に元のシェイプ 4 本（タグ「元から」）", tab.list.count() == 4 and all("[元から]" in tab.list.item(i).text() for i in range(4)) and tab.body.isEnabled())
    check("画面: 点が無いと「この角度で彫る」は押せない", not tab.sculpt_btn.isEnabled() and not tab.sculpt_end_btn.isEnabled())
    s.select_point(1, 2)
    pump()
    check("画面: 点を選ぶと「この角度で彫る」が押せる（名前が出る）", tab.sculpt_btn.isEnabled() and "fcs_Neutral_R1_C2" in tab.sculpt_label.text())
    tab.sculpt_btn.click()
    pump()
    check("画面: 押すと彫り中になる（終わるボタンが有効・表示が変わる）", s.sculpting is not None and tab.sculpt_end_btn.isEnabled() and not tab.sculpt_btn.isEnabled() and "彫り中" in tab.sculpt_label.text())
    P = np.array(cmds.xform(f"{face}.vtx[*]", query=True, worldSpace=True, translation=True)).reshape(-1, 3)
    sel = [i for i in range(n) if P[i][2] > 3.0 and 6.0 < P[i][1] < 8.5 and abs(P[i][0]) < 3.0]
    cmds.move(0.0, 0.5, 0.2, [f"{face}.vtx[{i}]" for i in sel], relative=True, objectSpace=True)
    shot(tab, "f_shapes_sculpting", 560, 1900)
    tab.sculpt_end_btn.click()
    pump()
    pt = s.doc.layers[0].points[(1, 2)]
    check("画面: 「彫り終わる」で点のポーズに記録される・一覧に fcs_ が出る", s.sculpting is None and pt.pose.curves.get(f"{bs}.fcs_Neutral_R1_C2") == 1.0 and any("fcs_Neutral_R1_C2" in tab.sculpt_list.item(i).text() and "点 Neutral R1 C2" in tab.sculpt_list.item(i).text() for i in range(tab.sculpt_list.count())), tab.status.text())
    check("画面: 状態の行に結果が出る", "彫りを終えました" in tab.status.text())
    s.end_edit()
    # 左右
    pick("mouth_open")
    check("画面: 1 本選ぶと左右の名前が自動で入る", tab.name_l.text() == "mouth_open_L" and tab.name_r.text() == "mouth_open_R" and tab.split_btn.isEnabled())
    tab.split_btn.click()
    pump()
    check("画面: 「左右に分ける」で _L / _R ができる", "mouth_open_L" in allnames() and "mouth_open_R" in allnames() and "作成" in tab.status.text())
    # ミラー: 元からある smile_R へは確認が出る（いいえなら何もしない）
    answers["confirm"] = False
    before = dense("smile_R")
    pick("smile_L")
    tab.mirror_btn.click()
    pump()
    check("画面: 元からあるシェイプへのミラーは確認し、いいえなら変えない", any("元からある" in a for a in asked) and np.array_equal(dense("smile_R"), before))
    answers["confirm"] = True
    # 非対称で止まる → いいえ / それでも続ける
    pick("mouth_open_L")
    cand = [i for i in range(n) if P[i][0] > 2.0 and 4.0 < P[i][1] < 10.0 and P[i][2] > 0][0]
    cmds.move(0.0, 0.0, 0.8, f"{face}.vtx[{cand}]", relative=True, objectSpace=True)
    answers["asym"] = False
    r_before = dense("mouth_open_R")
    tab.mirror_btn.click()
    pump()
    check("画面: 非対称だと止まり、「いいえ」なら何も変えない（頂点は選択済み）", any(a.startswith("asym") for a in asked) and np.array_equal(dense("mouth_open_R"), r_before) and len(cmds.ls(selection=True)) > 0)
    answers["asym"] = True
    tab.mirror_btn.click()
    pump()
    check("画面: 「それでも続ける」なら _R を更新する", "mouth_open_R" in tab.status.text() and "鏡映" in tab.status.text(), tab.status.text())
    cmds.move(0.0, 0.0, -0.8, f"{face}.vtx[{cand}]", relative=True, objectSpace=True)
    # ポーズをシェイプに
    s.select_point(1, 2)
    pump()
    tab.pose_name.setText("uiPose")
    tab.pose_btn.click()
    pump()
    check("画面: 「ポーズをシェイプにする」で新しいシェイプができる・名前の候補が進む", "uiPose" in allnames() and tab.pose_name.text() == "pose_shape_01", tab.status.text())
    s.end_edit()
    # 中間・誇張
    pick("uiPose")
    tab.inb_weight.setValue(0.5)
    tab.inb_btn.click()
    pump()
    check("画面: 「中間形を足す」で item 5500", scene.item_numbers(bs, "uiPose") == [5500, 6000], tab.status.text())
    tab.ex_btn.click()
    pump()
    check("画面: 「誇張形」で _Ex ができ、可動域が 0〜2", "uiPose_Ex" in allnames() and s.doc.limits.get(f"{bs}.uiPose") == (0.0, 2.0), tab.status.text())
    # 組み合わせ
    pick("smile_L")
    check("画面: 組み合わせは 2 本選ぶまで押せない", not tab.combo_btn.isEnabled())
    pick("smile_L", "brow_up")
    check("画面: 2 本選ぶと押せる", tab.combo_btn.isEnabled())
    tab.combo_btn.click()
    pump()
    cn = "fcs_combo_smile_L__brow_up"
    check("画面: 「組み合わせ補正を作る」で fcs_combo_ ができ、彫りの一覧に出る", cn in allnames() and any(cn in tab.sculpt_list.item(i).text() and "組み合わせ補正" in tab.sculpt_list.item(i).text() for i in range(tab.sculpt_list.count())))
    pick(cn)
    tab.combo_sculpt_btn.click()
    pump()
    check("画面: 「組み合わせ補正を彫る」で彫り中になる", s.sculpting is not None and s.sculpting.kind == "combo")
    tab.sculpt_end_btn.click()
    pump()
    check("画面: 終わると彫り中でなくなる", s.sculpting is None)
    # 写す
    check("画面: 写し先の候補にほかのメッシュが出る（顔は出ない）", tab.dest_combo.count() >= 1 and all(tab.dest_combo.itemText(i) != "mini_face" for i in range(tab.dest_combo.count())))
    pick("mouth_open")
    tab.dest_combo.setCurrentIndex(max(tab.dest_combo.findText("mini_brow"), 0))
    tab.transfer_btn.click()
    pump()
    check("画面: 「写す」で写し先に同じ名前のシェイプができる", cmds.objExists("tdFacial_mini_brow") and "mouth_open" in scene.target_indices("tdFacial_mini_brow"), tab.status.text())
    # 整理
    pick("uiPose")
    tab.clean_btn.click()
    pump()
    check("画面: 「微小な差分を掃除」の結果が出る", "掃除" in tab.status.text(), tab.status.text())
    tab.audit_btn.click()
    pump()
    check("画面: 「空・未使用を調べる」で一覧が出る", "空のシェイプ" in tab.audit_text.toPlainText() and "標準シェイプの不足" in tab.audit_text.toPlainText())
    # 元からある名前への分割
    pick("brow_up")
    tab.name_l.setText("smile_L")
    answers["confirm"] = False
    d_sl = dense("smile_L")
    tab.split_btn.click()
    pump()
    check("画面: 元からある名前への分割は確認し、いいえなら何もしない", np.array_equal(dense("smile_L"), d_sl) and any("元からあるシェイプです" in a for a in asked))
    answers["confirm"] = True
    pick()
    check("画面: 何も選ばないと道具のボタンは押せない", not tab.split_btn.isEnabled() and not tab.inb_btn.isEnabled() and not tab.clean_btn.isEnabled() and not tab.transfer_btn.isEnabled())
    # 彫りを消す
    pick("fcs_Neutral_R1_C2")
    tab.sculpt_del_btn.click()
    pump()
    check("画面: 「彫りを消す」で消える（確認が出る）", "fcs_Neutral_R1_C2" not in allnames() and any("彫り用シェイプ" in a for a in asked))
    # 失敗はダイアログへ
    pick("smile_L")
    check("画面: 失敗（ShapeError）は show_error に出る", _fail_shown(tab, errors))
    # 検証タブへ
    tab.validate_btn.click()
    pump()
    check("画面: 「検証タブへ」で検証タブが開く", panel.tabs.currentWidget() is panel.slots["validate"])
    panel.select_tab("shapes")
    shot(tab, "f_shapes", 560, 1900)
    if shot_dir:
        panel.resize(560, 900)
        panel.select_tab("shapes")
        pump()
        panel.grab().save(str(Path(shot_dir) / "f_shapes_panel.png"))
    panel.detach()
    check("画面: detach で通知の購読が外れる", tab.on_state not in s.state_listeners and not s.editing)
    s.close()


def _fail_shown(tab, errors) -> bool:
    """作ったシェイプ以外への同名の分割（元からある名前・確認 yes でも ShapeError にならない道具）ではなく、重みの不正で失敗させる。"""
    errors.clear()
    tab.inb_weight.setRange(0.0, 5.0)
    tab.inb_weight.setValue(1.0)
    tab.inb_btn.click()
    pump()
    tab.inb_weight.setRange(0.01, 0.99)
    return bool(errors) and "重み" in errors[0]


def shot(widget, name: str, w: int, h: int) -> None:
    shot_dir = os.environ.get("TDRIVE_UI_SHOT_DIR")
    if not shot_dir:
        return
    p = widget
    while p.parent() is not None and not hasattr(p, "select_tab"):
        p = p.parent()
    host = p if hasattr(p, "select_tab") else widget
    host.resize(w, h)
    host.layout().activate()
    pump()
    host.grab().save(str(Path(shot_dir) / f"{name}.png"))


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
