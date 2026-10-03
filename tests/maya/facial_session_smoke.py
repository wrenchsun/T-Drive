"""FacialController のセッション層（tdrive_facial.session）のスモークテスト（mayapy で実行。画面なし）。

  "C:/Program Files/Autodesk/Maya2026/bin/mayapy.exe" tests/maya/facial_session_smoke.py

合成の小さな頭（tests/maya/facial_fixture.py）で確かめる。ファイルはすべて一時フォルダ（プロジェクトのルートもそこへ向ける）。
リポジトリの assets / looks / facial には書かない。
"""

from __future__ import annotations

import copy
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

import maya.standalone  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(cond), detail))


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


def near(a, b, tol=1e-4) -> bool:
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def run() -> None:
    from maya import cmds

    import facial_fixture
    from tdrive import project
    from tdrive_facial import scene
    from tdrive_facial import session as S
    from tdrive_facial.core import fcpose_io, naming, space
    from tdrive_facial.core.presenters import CONFIRM_CANCEL, CONFIRM_DISCARD, CONFIRM_SAVE

    tmp = Path(tempfile.mkdtemp(prefix="tdrive_fsess_"))
    project.set_root(tmp)  # すべてここの下へ書く
    ids = facial_fixture.build_mini_head()
    face, bs = ids["face"], ids["bs"]
    joints = ids["joints"]
    s = S.current()
    events: list[str] = []
    s.listeners.append(lambda: events.append("changed"))

    # ------------------------------------------------------------ new
    cmds.select(ids["brow"])  # blendShape の無いメッシュを選んでいても、blendShape 付きの顔を選ぶ
    doc = s.new("mini")
    check("new: 座標系が Maya（cm / Y-up / right）で明示されている", space.space_of(doc.meta) == space.MAYA and doc.meta.source != "")
    check("new: forwardAxis +Z・mirror.boneAxis X", doc.grid.forward_axis == "+Z" and doc.mirror.bone_axis == "X")
    check("new: asset = キャラクター名・既定の格子 5x3・Neutral レイヤー",
          doc.asset == "mini" and (doc.grid.cols, doc.grid.rows) == (5, 3) and [l.name for l in doc.layers] == ["Neutral"])
    check("new: 対象メッシュが blendShape 付きの顔", doc.target is not None and doc.target.mesh == "mini_face", f"{doc.target}")
    check("new: 基準ボーンが自動検出される（head）", doc.grid.base_bone == "head", doc.grid.base_bone)
    expected = tmp / "facial" / "mini" / "mini.fcpose.json"
    check("new: 既定の保存先が <プロジェクト>/facial/<character>/<character>.fcpose.json", s.path == expected, f"{s.path}")
    check("new: dirty・listeners に通知された", s.dirty and "changed" in events)
    check("new: シーン情報と bake_state（空）が同期している",
          s.scene is not None and "bs.mouth_open" in s.scene.curves and "head" in s.scene.bones and s.bake_state == {})

    # ------------------------------------------------------------ save / fileInfo
    path = s.save()
    check("save: ファイルができ fcpose_io で往復できる",
          path == expected and path.exists() and fcpose_io.to_dict(fcpose_io.load_document(path)) == fcpose_io.to_dict(s.doc))
    check("save: dirty が下りる", not s.dirty)
    info = cmds.fileInfo(S.FILE_INFO_KEY, query=True)
    check("fileInfo: プロジェクト相対パスが記録される", info == ["facial/mini/mini.fcpose.json"], f"{info}")
    try:
        s.new("mini")
        s.save()
        guard = False
    except FileExistsError:
        guard = True
    check("save: 一度も保存していない既定の保存先に既存ファイルがあれば上書きしない（FileExistsError）", guard)
    s.save(overwrite=True)

    # ------------------------------------------------------------ 設定コマンド・プロファイル・作業セット
    n_undo = len(s._undo)
    r = s.set_forward_axis("+Y")
    check("set_forward_axis: 上軸は失敗し Undo に積まない", not r.ok and len(s._undo) == n_undo)
    r = s.set_forward_axis("-Z")
    check("set_forward_axis -Z: 反映・Undo に積む", r.ok and s.doc.grid.forward_axis == "-Z" and len(s._undo) == n_undo + 1)
    s.undo()
    check("undo: forwardAxis が戻る", s.doc.grid.forward_axis == "+Z")
    s.redo()
    s.undo()
    r = s.apply_profile("shizuku")
    check("apply_profile: プロファイルのミラー規則・可動域が入る", r.ok and s.doc.profile == "shizuku" and s.profile is not None and s.doc.limits, r.message)
    r = s.apply_profile("no_such_profile")
    check("apply_profile: 無い名前は失敗", not r.ok)
    s.add_to_working_set(curves=["bs.mouth_open", "bs.smile_L", "bs.smile_R", "bs.brow_up"], bones=["eye_L", "eye_R"])
    s.remove_from_working_set(curves=["bs.brow_up"])
    check("作業セット: 追加・削除", s.doc.working_set.curves == ["bs.mouth_open", "bs.smile_L", "bs.smile_R"] and s.doc.working_set.bones == ["eye_L", "eye_R"])
    s.set_mirror(bone_axis="X", suffix_l="_L", suffix_r="_R")
    s.set_autogen(mode="IDW", idw_power=2.0)
    s.set_bake_options(delta_threshold=0.001)
    s.set_edge_fade(15)
    s.set_center_offset((0, 1, 0))
    s.set_center_offset((0, 0, 0))
    check("セットアップ変更: 値が入る", s.doc.bake is not None and s.doc.bake.delta_threshold == 0.001 and s.doc.grid.center_offset == (0.0, 0.0, 0.0))
    r = s.set_target(mesh="no_such_mesh")
    check("set_target: シーンに無いメッシュは失敗して文書を変えない", not r.ok and s.doc.target.mesh == "mini_face")
    r = s.set_target(extra_meshes=["mini_brow"])
    check("set_target: extraMeshes を設定・シーン情報を取り直す", r.ok and s.doc.target.extra_meshes == ["mini_brow"])
    s.set_target(extra_meshes=[])
    s.save()

    # ------------------------------------------------------------ 保存 → シーンを保存 → 開き直す → on_scene_opened
    scene_file = tmp / "scene.mb"
    cmds.file(rename=str(scene_file))
    cmds.file(save=True, type="mayaBinary")
    saved_dict = fcpose_io.to_dict(s.doc)
    s.close()
    check("close: データが閉じる・シーンの記録が消える", s.presenters is None and not cmds.fileInfo(S.FILE_INFO_KEY, query=True))
    cmds.file(str(scene_file), open=True, force=True)
    check("シーンを開き直すと fileInfo が残っている", cmds.fileInfo(S.FILE_INFO_KEY, query=True) == ["facial/mini/mini.fcpose.json"])
    S.on_scene_opened()
    check("on_scene_opened: 記録されたデータが同じ内容で開く", s.presenters is not None and fcpose_io.to_dict(s.doc) == saved_dict and not s.dirty and s.path == expected)
    check("on_scene_opened: シーン情報が取り込まれる", s.scene is not None and s.scene.curves)

    # ------------------------------------------------------------ 編集状態
    cmds.setAttr("eye_L.rotateY", 12)  # 基準姿勢（バインドポーズ）と違う値から始める
    cmds.setKeyframe(f"{bs}.smile_L", time=1, value=0.2)  # 接続つきの重みも戻ることを確かめる
    j0, w0 = joint_state(joints), weight_state(bs)
    src0 = cmds.listConnections(f"{bs}.smile_L", source=True, destination=False, plugs=True)
    check("前提: smile_L にアニメ接続がある", bool(src0))
    s.begin_edit()
    check("begin_edit: 編集状態になる・基準姿勢（eye_L.rotateY = 0）", s.editing and abs(cmds.getAttr("eye_L.rotateY")) < 1e-9)
    r = s.select_point(1, 3)
    check("select_point: 選択される・適用された点が分かる", r.status == "selected" and s.applied_point == (0, 1, 3), f"{r.status} {s.applied_point}")
    cmds.setAttr(f"{bs}.mouth_open", 0.7)
    cmds.setAttr("eye_L.rotateY", 25)
    after_edit = joint_state(joints)
    rep = s.capture_from_scene(working_set_only=False)
    check("capture_from_scene: シェイプとボーンが編集中の値に入る・未保存になる",
          rep.ok and abs(s.pose.curves.get("bs.mouth_open", 0) - 0.7) < 1e-6 and "eye_L" in s.pose.bones and s.pose.dirty, f"{s.pose.curves} {list(s.pose.bones)}")
    r = s.select_point(1, 4)
    check("select_point: 未保存の編集があると needs_confirm（何も変えない）", r.status == "needs_confirm" and s.ctx.selection == (1, 3))
    r = s.select_point(1, 4, choice=CONFIRM_CANCEL)
    check("select_point: cancel で選択は変わらない", r.status == "cancelled" and s.ctx.selection == (1, 3) and s.pose.dirty)
    res = s.save_point()
    pt = s.doc.layers[0].points.get((1, 3))
    check("save_point: 点がキーとして保存される・dirty", res.ok and pt is not None and pt.is_key and abs(pt.pose.curves["bs.mouth_open"] - 0.7) < 1e-6 and "eye_L" in pt.pose.bones and s.dirty)
    r = s.select_point(1, 4)
    check("別の点へ移ると基準姿勢へ戻る（重み 0・eye_L.rotateY 0）",
          r.status == "selected" and s.applied_point == (0, 1, 4) and abs(cmds.getAttr(f"{bs}.mouth_open")) < 1e-9 and abs(cmds.getAttr("eye_L.rotateY")) < 1e-9)
    r = s.select_point(1, 3)
    again = joint_state(joints)
    check("戻ると保存したポーズが再現される（重み・ジョイント）",
          abs(cmds.getAttr(f"{bs}.mouth_open") - 0.7) < 1e-6 and again["eye_L"] == after_edit["eye_L"], f"{again['eye_L']} vs {after_edit['eye_L']}")
    # discard
    cmds.setAttr(f"{bs}.smile_L", 0.5)
    s.capture_from_scene(working_set_only=False)
    r = s.select_point(2, 2, choice=CONFIRM_DISCARD)
    check("select_point: discard は捨てて移る（保存済みは変わらない）",
          r.status == "selected" and not r.saved and "bs.smile_L" not in s.doc.layers[0].points[(1, 3)].pose.curves and s.ctx.selection == (2, 2))
    # save
    cmds.setAttr(f"{bs}.brow_up", 0.4)
    s.capture_from_scene(working_set_only=False)
    r = s.select_point(0, 0, choice=CONFIRM_SAVE)
    p22 = s.doc.layers[0].points.get((2, 2))
    check("select_point: save は保存して移る（Undo に積まれる）", r.status == "selected" and r.saved and p22 is not None and p22.is_key and abs(p22.pose.curves.get("bs.brow_up", 0) - 0.4) < 1e-6)
    s.pose.set_curve("bs.smile_L", 0.9)
    s.apply_buffer_to_scene()
    check("apply_buffer_to_scene: 編集中の値がシーンへ当たる", abs(cmds.getAttr(f"{bs}.smile_L") - 0.9) < 1e-6)
    s.reload_pose()
    check("reload_pose: 編集中の値を捨ててシーンも戻る", abs(cmds.getAttr(f"{bs}.smile_L")) < 1e-9 and not s.pose.dirty)
    # 保存→ Undo
    check("save_point の Undo: 点が消える", s.undo() and (2, 2) not in s.doc.layers[0].points and s.editing)
    s.redo()
    s.end_edit()
    check("end_edit: ジョイント・重み・接続が編集前へ戻る",
          not s.editing and joint_state(joints) == j0 and weight_state(bs) == w0
          and cmds.listConnections(f"{bs}.smile_L", source=True, destination=False, plugs=True) == src0)

    # ------------------------------------------------------------ Undo / Redo（文書コマンド）
    ev: list[str] = []
    unsub = s.ctx.subscribe(ev.append)
    n_before = fcpose_io.to_dict(s.doc)
    events.clear()
    r = s.add_layer("Anger")
    check("add_layer: レイヤーが増える", r.ok and [l.name for l in s.doc.layers] == ["Neutral", "Anger"])
    ev.clear()
    events.clear()
    check("undo: Document が戻る", s.undo() and fcpose_io.to_dict(s.doc) == n_before)
    check("undo: listeners と Presenter に通知される", "changed" in events and "document" in ev, f"{events} {ev}")
    check("redo: やり直せる", s.redo() and [l.name for l in s.doc.layers] == ["Neutral", "Anger"])
    unsub()
    n_undo = len(s._undo)
    r = s.rename_layer(0, "X")
    check("失敗するコマンドは Undo に積まない（Neutral は改名不可）", not r.ok and len(s._undo) == n_undo)
    check("can_undo / can_redo", s.can_undo and not s.can_redo)

    # ------------------------------------------------------------ 自動生成
    r = s.generate()
    neutral = s.doc.layers[0]
    n_keys = sum(1 for p in neutral.points.values() if p.is_key)
    check("generate: キーから点が埋まる", r.ok and len(neutral.points) > n_keys and n_keys >= 2, f"{len(neutral.points)} / keys {n_keys}")
    check("generate: Undo で元へ戻る", s.undo() and len(s.doc.layers[0].points) == n_keys)
    s.redo()
    r = s.copy_layer(0, 1)
    check("copy_layer: Neutral → Anger にポーズが入る", r.ok and len(s.doc.layers[1].points) == len(s.doc.layers[0].points))

    # ------------------------------------------------------------ ベイク
    j_pre = joint_state(joints)
    s.begin_edit()
    s.select_point(1, 3)
    report = s.bake_all()
    live = [(l.name, rc) for l in s.doc.layers for rc, p in l.points.items() if not p.pose.is_empty()]
    names = {naming.morph_name("mini", ln, *rc) for ln, rc in live}
    fc = {t.alias for t in scene.fc_targets(face)}
    check("bake_all: 編集状態を抜けてから焼く（シーンは元の姿勢）", not s.editing and joint_state(joints) == j_pre)
    check("bake_all: FC_* ターゲットが全点ぶんできる", fc == names and len(report.created) == len(names), f"{len(fc)} vs {len(names)}")
    check("bake_all: bake_state が同期している", set(s.bake_state) == names)
    s.validate()
    codes = {i.code for i in s.validation.issues}
    check("validate: 焼いた直後は未ベイク・変更ありが無い", not (codes & {"point_unbaked", "point_changed_since_bake", "baked_morph_missing"}), f"{codes}")
    check("bake_all 後の集計: 未ベイク 0・変更あり 0", s.grid.summary().unbaked == 0 and s.grid.summary().changed == 0)
    check("bake: Maya の Undo の 1 区切りで戻せる（FC_* が消える）", (cmds.undo() or True) and not scene.fc_targets(face))
    cmds.redo()
    s.refresh_scene()
    check("bake: redo で戻る", {t.alias for t in scene.fc_targets(face)} == names)

    # Neutral だけ焼き直す → 感情レイヤーの警告
    s.begin_edit()
    s.select_point(1, 3)
    s.pose.set_curve("bs.mouth_open", 0.5)
    s.save_point()
    rep2 = s.bake_point(1, 3, layer=0)
    check("bake_point: Neutral だけだと感情レイヤーの焼き直しを促す警告", any("感情レイヤー" in w for w in rep2.warnings), f"{rep2.warnings}")
    check("bake_point: 1 点だけ置き換える", rep2.replaced == [naming.morph_name("mini", "Neutral", 1, 3)] and not rep2.created, f"{rep2.replaced} {rep2.created}")
    s.begin_edit()
    s.select_point(1, 3)
    s.pose.set_curve("bs.mouth_open", 0.6)
    s.save_point()  # Neutral が「変更あり」になる
    rep3 = s.bake_layer(1)
    check("bake_layer: 感情レイヤーだけだと Neutral の未反映を促す警告", any("Neutral" in w for w in rep3.warnings), f"{rep3.warnings}")
    s.bake_all()

    # ------------------------------------------------------------ clear_point（焼いた FC_* を消す）・編集中でも安全
    target = naming.morph_name("mini", "Neutral", 1, 3)
    s.begin_edit()
    s.select_point(1, 4)
    table = scene.target_indices(bs)
    idx = table[target]
    other_before = {a: i for a, i in table.items() if a != target}
    res = s.clear_point(1, 3)
    table2 = scene.target_indices(bs)
    check("clear_point: 焼いた FC_* がシーンから消える", res.ok and target not in table2 and target in res.stale_morphs, f"{res.stale_morphs}")
    check("clear_point: ほかのターゲットの番号・元のシェイプは不変", {a: i for a, i in table2.items()} == other_before and "mouth_open" in table2)
    check("clear_point: bake_state・シーン情報からも外れる", target not in s.bake_state and target not in s.scene.targets)
    s.end_edit()
    check("clear_point: 編集中に消しても、戻すとき消した要素を作り直さない", idx not in (cmds.getAttr(f"{bs}.weight", multiIndices=True) or []), f"{cmds.getAttr(bs + '.weight', multiIndices=True)}")
    s.undo()
    check("clear_point の Undo: 文書の点は戻り、FC_* は未ベイクに見える", (1, 3) in s.doc.layers[0].points and s.grid.summary().unbaked >= 1)
    s.bake_all()

    # ------------------------------------------------------------ レイヤー削除
    anger = [t.alias for t in scene.fc_targets(face) if "_Anger_" in t.alias]
    check("前提: Anger の FC_* が焼かれている", len(anger) > 0)
    res = s.delete_layer(1)
    left = [t.alias for t in scene.fc_targets(face) if "_Anger_" in t.alias]
    check("delete_layer: そのレイヤーの FC_* がシーンから消える", res.ok and not left and set(anger) <= set(res.stale_morphs), f"{left}")
    check("delete_layer: Neutral の FC_* は残る", any("_Neutral_" in t.alias for t in scene.fc_targets(face)))

    # ------------------------------------------------------------ カメラ
    cam = cmds.camera(name="testCam")[0]
    cmds.xform(cam, worldSpace=True, translation=(15, 20, 60))
    yaw0, pitch0 = s.view_angles(cam)
    check("view_angles: 前方（+Z）の斜め上・+X 側は Yaw 正・Pitch 正", yaw0 > 0 and pitch0 > 0, f"{yaw0} {pitch0}")
    worst = 0.0
    pts = [(0, 0), (0, 4), (2, 0), (2, 4), (1, 1), (1, 3), (1, 2), (0, 2), (2, 2)]
    for r_, c_ in pts:
        ey, ep = s.camera_to_point(r_, c_, cam)
        gy, gp = s.view_angles(cam)
        worst = max(worst, abs(gy - ey), abs(gp - ep))
    check("camera_to_point → view_angles が点の角度と 0.5° 以内で一致（負の Yaw・Pitch を含む）", worst < 0.5, f"最大誤差 {worst}")
    d0 = math.dist(cmds.xform(cam, query=True, worldSpace=True, translation=True), cmds.xform("head", query=True, worldSpace=True, translation=True))
    s.camera_to_point(1, 4, cam)
    d1 = math.dist(cmds.xform(cam, query=True, worldSpace=True, translation=True), cmds.xform("head", query=True, worldSpace=True, translation=True))
    cx = cmds.xform(cam, query=True, worldSpace=True, translation=True)[0]
    check("camera_to_point: 距離を保つ・Yaw 正（+90°）はキャラクターの左 = +X 側", abs(d0 - d1) < 1e-3 and cx > 10, f"{d0} {d1} x={cx}")
    s.camera_to_point(1, 2, cam, distance=100)
    d2 = math.dist(cmds.xform(cam, query=True, worldSpace=True, translation=True), cmds.xform("head", query=True, worldSpace=True, translation=True))
    check("camera_to_point: distance 指定", abs(d2 - 100) < 1e-3, f"{d2}")
    # 頭を回す・前方向軸・中心のずらし
    cmds.setAttr("head.rotateY", 30)
    s.camera_to_point(1, 2, cam)  # 正面（0°, 0°）= 頭の前方向（+Z を 30° 回したもの）
    cp = cmds.xform(cam, query=True, worldSpace=True, translation=True)
    hp = cmds.xform("head", query=True, worldSpace=True, translation=True)
    v = (cp[0] - hp[0], cp[2] - hp[2])
    ang = math.degrees(math.atan2(v[0], v[1]))
    check("頭の向きを反映する（頭を Y 回転 30° → 正面のカメラも 30°）", abs(ang - 30) < 0.5, f"{ang}")
    worst = 0.0
    for r_, c_ in pts:
        ey, ep = s.camera_to_point(r_, c_, cam)
        gy, gp = s.view_angles(cam)
        worst = max(worst, abs(gy - ey), abs(gp - ep))
    check("頭を回しても往復が一致", worst < 0.5, f"{worst}")
    s.set_forward_axis("-Z")
    s.set_center_offset((1.5, 2.0, -1.0))
    worst = 0.0
    for r_, c_ in pts:
        ey, ep = s.camera_to_point(r_, c_, cam)
        gy, gp = s.view_angles(cam)
        worst = max(worst, abs(gy - ey), abs(gp - ep))
    s.camera_to_point(1, 2, cam)
    cp = cmds.xform(cam, query=True, worldSpace=True, translation=True)
    v = (cp[0] - hp[0], cp[2] - hp[2])
    ang2 = math.degrees(math.atan2(v[0], v[1]))
    check("forwardAxis -Z・centerOffset を反映しても往復が一致し、-Z なので反対側から見る",
          worst < 0.5 and abs(abs(ang2 - 30) - 180) < 3.0, f"{worst} {ang2}")
    cmds.setAttr("head.rotateY", 0)
    s.set_center_offset((0, 0, 0))
    s.set_forward_axis("+Z")
    # 選択（モデルパネルの無い mayapy）でも persp にフォールバックして動く
    s.camera_to_point(1, 2)
    check("camera: 省略すると persp（パネルが無いとき）", abs(s.view_angles()[0]) < 0.5)

    # ------------------------------------------------------------ 検証・修復
    s.clear_layer()  # 自動生成の点に正しい綴りが入っていると、改名が衝突して見送られるので空にしておく
    s.begin_edit()
    s.select_point(0, 1)
    s.pose.set_curve("bs.mouth_opne", 0.5)  # 綴りの違い
    s.pose.set_curve("bs.zzz_not_in_model", 0.3)
    s.save_point()
    s.end_edit()
    issues = s.validate()
    miss = [i for i in issues if i.code == "curve_missing"]
    check("validate: 無いシェイプを検出（候補つき）", len(miss) >= 2 and any(i.suggestion == "bs.mouth_open" for i in miss), f"{[(i.name, i.suggestion) for i in miss]}")
    s.validation.set_choice("curve", "bs.zzz_not_in_model", "")
    rr = s.apply_renames()
    check("apply_renames: 候補で改名される（候補なしは見送る）", rr.ok and rr.total >= 1 and "bs.mouth_opne" not in s.doc.layers[0].points[(0, 1)].pose.curves
          and "bs.mouth_open" in s.doc.layers[0].points[(0, 1)].pose.curves, f"{rr.message}")
    rm = s.remove_missing()
    check("remove_missing: モデルに無い参照が消える", rm.ok and rm.removed >= 1 and "bs.zzz_not_in_model" not in s.doc.layers[0].points[(0, 1)].pose.curves, rm.message)
    check("apply_renames / remove_missing は Undo できる", s.undo() and s.undo())

    # ------------------------------------------------------------ 例外で編集状態が残らない
    cmds.setAttr("eye_L.rotateY", 7)
    cmds.setAttr(f"{bs}.brow_up", 0.25)
    j1, w1 = joint_state(joints), weight_state(bs)
    try:
        with s.edit_guard():
            s.select_point(1, 3)
            cmds.setAttr(f"{bs}.smile_R", 0.8)
            raise RuntimeError("編集中の失敗")
    except RuntimeError:
        pass
    check("edit_guard: 例外が出ても元の姿勢・重みへ戻る", not s.editing and joint_state(joints) == j1 and weight_state(bs) == w1)
    s.begin_edit()
    try:
        s.select_point(1, 3)
        orig = S.pose_apply.apply_pose
        S.pose_apply.apply_pose = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("壊れた"))
        try:
            s.select_point(1, 2)
        finally:
            S.pose_apply.apply_pose = orig
        raised = False
    except RuntimeError:
        raised = True
    check("シーンを触るメソッドの例外: 編集状態を抜けて元へ戻す", raised and not s.editing and joint_state(joints) == j1 and weight_state(bs) == w1)
    # 編集状態のまま新しいシーン: 新しいシーンへ古い値を書かない
    s.begin_edit()
    cmds.file(new=True, force=True)
    facial_fixture.build_mini_head()
    rot_new = cmds.getAttr("eye_L.rotateY")
    s.on_new_scene()
    check("新しいシーン: 編集状態は捨てられ、新しいシーンのジョイントへ古い値を書かない", not s.editing and cmds.getAttr("eye_L.rotateY") == rot_new == 0.0)
    s.on_before_scene_change()  # 何も入っていないとき安全
    # リロード
    cmds.setAttr("eye_L.rotateY", 9)
    j2 = joint_state(joints)
    s.begin_edit()
    from tdrive import lifecycle

    errs = lifecycle.run_reload_cleanups()
    check("リロードの後片付け: 編集状態を抜けて元の姿勢へ戻す", not errs and not s.editing and joint_state(joints) == j2, f"{errs}")
    lifecycle.on_reload(S._reload_cleanup)

    # ------------------------------------------------------------ on_scene_saved
    s.set_edge_fade(20)
    check("前提: dirty", s.dirty)
    before_mtime = s.path.stat().st_mtime_ns
    S.on_scene_saved()
    reread = fcpose_io.load_document(s.path)
    check("on_scene_saved: dirty なら文書も保存し、dirty が下りる", not s.dirty and reread.grid.edge_fade == 20.0 and s.path.stat().st_mtime_ns >= before_mtime)
    check("on_scene_saved: シーンが未保存に戻らない", not cmds.file(query=True, modified=True))
    n = s.path.stat().st_mtime_ns
    S.on_scene_saved()
    check("on_scene_saved: dirty でなければ何もしない", s.path.stat().st_mtime_ns == n)

    # ------------------------------------------------------------ UE の系のファイルを開く
    src = REPO / "tests" / "facial" / "fixtures" / "ue_full_asset.fcpose.json"
    ue = tmp / "ue_in" / "ue_full_asset.fcpose.json"
    ue.parent.mkdir()
    shutil.copy(src, ue)
    ue_bytes = ue.read_bytes()
    s.open(ue)
    d = s.doc
    check("UE の系のファイルは Maya の系へ変換して開く", space.space_of(d.meta) == space.MAYA and d.grid.forward_axis == "+Z" and d.mirror.bone_axis == "X")
    check("変換: centerOffset (0,0,3.5) → Maya (0,3.5,0)", near(d.grid.center_offset, (0.0, 3.5, 0.0)), f"{d.grid.center_offset}")
    check("変換: 元の meta を doc.extra に残す・converted_from", d.extra.get(S.ORIGINAL_META_KEY, {}).get("upAxis") == "Z" and s.converted_from is not None and s.converted_from["handedness"] == "left")
    check("変換: asset が補われ、対象メッシュが検出される", d.asset == "ue_full_asset" and d.target is not None and d.target.mesh == "mini_face", f"{d.asset} {d.target}")
    check("変換: 保存先は元のファイルでなく既定の場所・dirty", s.path == tmp / "facial" / "ue_full_asset" / "ue_full_asset.fcpose.json" and s.dirty and s.source_path == ue)
    s.save()
    check("変換して開いたものを保存しても元のファイルは変わらない・保存先は Maya の系",
          ue.read_bytes() == ue_bytes and space.space_of(fcpose_io.load_document(s.path).meta) == space.MAYA and not s.dirty)
    s.open(s.path)
    check("保存した Maya の系のものを開き直すと変換なし・dirty なし", s.converted_from is None and not s.dirty)

    # ------------------------------------------------------------ 後片付け
    s.close()
    check("close: 最後に編集状態でない", not s.editing)
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
