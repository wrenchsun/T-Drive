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
    # 変更（意図した挙動変更）: 以前は「感情レイヤーも焼き直してください」の警告だけだった。今は感情レイヤーの同じ位置の点も自動で焼き直し、notes に書く
    check("bake_point: Neutral の点を焼くと感情レイヤーの同じ位置の点も自動で焼き直す（notes に書く）", any("感情レイヤー" in w for w in rep2.notes), f"{rep2.notes} {rep2.warnings}")
    check("bake_point: Neutral の 1 点 + 感情レイヤーの同じ位置の 1 点を置き換える（新規なし）",
          rep2.replaced == [naming.morph_name("mini", "Neutral", 1, 3), naming.morph_name("mini", "Anger", 1, 3)] and not rep2.created, f"{rep2.replaced} {rep2.created}")
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


def run_integration() -> None:
    """統合の追加分: 通知・強さを測るシェイプ・reset_bone・変更のある点だけベイク・cancel 1 回・プレビュー・T-20 の取り込み。"""
    import json

    from maya import cmds

    import facial_fixture
    from tdrive import project
    from tdrive_facial import bake as bakemod
    from tdrive_facial import preview_rig as pr
    from tdrive_facial import scene
    from tdrive_facial import session as S
    from tdrive_facial.core import autofill, fcpose_io, naming
    from tdrive_facial.core.presenters import CONFIRM_CANCEL

    tmp = Path(tempfile.mkdtemp(prefix="tdrive_fint_"))
    project.set_root(tmp)
    ids = facial_fixture.build_mini_head()
    face = scene.resolve_mesh("mini_face")
    s = S.current()
    doc0 = facial_fixture.make_doc()
    doc_path = tmp / "mini.fcpose.json"
    fcpose_io.save(doc0, doc_path)
    s.open(doc_path)
    s.add_to_working_set(curves=["bs.mouth_open", "bs.smile_L", "bs.smile_R", "bs.brow_up"], bones=["eye_L", "eye_R"])
    s.set_mirror(bone_axis="X", suffix_l="_L", suffix_r="_R")
    main_calls: list[int] = []
    state_calls: list[int] = []
    s.listeners.append(lambda: main_calls.append(1))
    s.state_listeners.append(lambda: state_calls.append(1))

    def counts() -> tuple[int, int]:
        return len(main_calls), len(state_calls)

    # ------------------------------------------------------------ 状態の通知（state_listeners）
    c0 = counts()
    s.begin_edit()
    check("通知: begin_edit で state_listeners が呼ばれる（listeners は呼ばれない）", counts()[1] > c0[1] and counts()[0] == c0[0], f"{c0} {counts()}")
    c0 = counts()
    s.select_point(1, 2)
    check("通知: select_point で state_listeners が呼ばれる", counts()[1] > c0[1])
    c0 = counts()
    for i in range(20):  # スライダーのドラッグ相当
        s.set_curve("bs.mouth_open", 0.1 + i * 0.01)
    c1 = counts()
    check("通知: set_curve（ドラッグ）は state_listeners だけ・listeners（重い更新）は呼ばない", c1[1] - c0[1] >= 20 and c1[0] == c0[0], f"{c0} {c1}")
    c0 = counts()
    from tdrive_facial.core.model import BoneOffset

    t_base = cmds.getAttr("eye_L.translate")[0]
    s.set_bone("eye_L", BoneOffset(t=(0.0, 0.3, 0.0)))
    check("通知: set_bone", counts()[1] > c0[1])
    check("set_bone: eye_L がポーズに入る", "eye_L" in s.pose.bones)
    c0 = counts()
    r = s.reset_bone("eye_L")
    check("reset_bone: 1 本だけ戻る（項目が取り除かれる。恒等のずれは残らない）・state 通知", r.ok and "eye_L" not in s.pose.bones and counts()[1] > c0[1], f"{list(s.pose.bones)}")
    check("reset_bone: シーンのジョイントも基準の位置へ戻る", all(abs(a - b) < 1e-6 for a, b in zip(cmds.getAttr("eye_L.translate")[0], t_base)), f"{cmds.getAttr('eye_L.translate')[0]} {t_base}")
    s.set_bone("eye_L", BoneOffset(t=(0.0, 0.3, 0.0)))
    s.set_bone("eye_R", BoneOffset(t=(0.0, 0.2, 0.0)))
    s.reset_bone("eye_L")
    check("reset_bone: 他のボーンは残る", "eye_R" in s.pose.bones and "eye_L" not in s.pose.bones)
    c0 = counts()
    s.zero_pose()
    s.mirror_pose()
    s.reload_pose()
    check("通知: zero / mirror / reload", counts()[1] - c0[1] >= 3)
    c0 = counts()
    s.add_layer("Anger")
    r = s.set_active_layer(1)
    check("通知: set_active_layer", counts()[1] > c0[1] and r.status == "selected")
    s.set_active_layer(0)
    s.delete_layer(2)  # Anger
    c0 = counts()
    s.validate()
    check("通知: validate", counts()[1] > c0[1])
    c0 = counts()
    s.end_edit()
    check("通知: end_edit", counts()[1] > c0[1] and not s.editing)

    # ------------------------------------------------------------ 強さを測るシェイプ
    n_undo = len(s._undo)
    r = s.set_intensity_curves(["bs.mouth_open", "bs.mouth_open", "bs.smile_L"])
    check("set_intensity_curves: 重複を除いて保存・Undo に 1 回積む・dirty", r.ok and s.doc.intensity_curves == ["bs.mouth_open", "bs.smile_L"] and len(s._undo) == n_undo + 1 and s.dirty)
    s.undo()
    check("set_intensity_curves: Undo で戻る", s.doc.intensity_curves == [])
    s.redo()
    n_undo = len(s._undo)
    s.set_intensity_curves(["bs.mouth_open", "bs.smile_L"])
    check("set_intensity_curves: 同じ値は Undo に積まない", len(s._undo) == n_undo)
    s.set_intensity_curves([])

    # ------------------------------------------------------------ select_point の cancel は 1 回の呼び出しで足りる
    s.begin_edit()
    s.select_point(1, 2)
    s.set_curve("bs.brow_up", 0.35)
    r = s.select_point(1, 0, choice=CONFIRM_CANCEL)
    check("cancel 1 回: 先に確認を呼ばなくても cancelled・選択も編集中の値も変わらない", r.status == "cancelled" and s.ctx.selection == (1, 2) and abs(s.pose.curves.get("bs.brow_up", 0) - 0.35) < 1e-9, f"{r.status} {s.ctx.selection}")
    s.add_layer("Joy2")
    r = s.set_active_layer(2, choice=CONFIRM_CANCEL)
    check("cancel 1 回: set_active_layer も", r.status == "cancelled" and s.ctx.active_layer == 0)
    s.delete_layer(2)
    s.reload_pose()
    r = s.select_point(1, 0, choice=CONFIRM_CANCEL)
    check("cancel: 未保存の編集が無ければ普通に選ぶ", r.status == "selected" and s.ctx.selection == (1, 0))
    s.end_edit()

    # ------------------------------------------------------------ 変更のある点だけベイク
    check("bake_stale: 何も焼いていないときは全点が対象（Neutral 4 + Joy 2）", len(s.stale_points()) == 6, f"{s.stale_points()}")
    rep = s.bake_stale()
    check("bake_stale: 全点を焼く", len(rep.created) == 6 and not s.stale_points(), f"{rep.summary()}")
    rep = s.bake_stale()
    check("bake_stale: 対象が無ければ何も焼かず、notes で伝える", not rep.created and not rep.replaced and any("ありません" in n for n in rep.notes), f"{rep.notes}")
    s.begin_edit()
    s.select_point(1, 2)
    s.set_curve("bs.mouth_open", 0.9)
    s.save_point()
    s.end_edit()
    check("bake_stale: Neutral を変えると stale は Neutral の 1 点（感情レイヤーは検出されない = 既知の制約）", s.stale_points() == [(0, 1, 2)], f"{s.stale_points()}")
    rep = s.bake_stale()
    nn, jn = naming.morph_name("mini", "Neutral", 1, 2), naming.morph_name("mini", "Joy", 1, 2)
    check("bake_stale: Neutral の点 + 感情レイヤーの同じ位置の点だけ焼き直す（ほかの点は触らない）", rep.replaced == [nn, jn] and not rep.created, f"{rep.replaced}")
    check("bake_stale: 感情の焼き直しを notes に書く", any("感情レイヤー" in n and "Joy" in n for n in rep.notes), f"{rep.notes}")
    s.begin_edit()
    s.select_point(2, 2)
    s.set_curve("bs.smile_R", 0.5)
    s.save_point()
    s.end_edit()
    check("bake_stale: 未ベイクの新しい点が対象", s.stale_points() == [(0, 2, 2)])
    rep = s.bake_stale()
    check("bake_stale: 新しい点だけ作る・終わると stale が 0", rep.created == [naming.morph_name("mini", "Neutral", 2, 2)] and not rep.replaced and s.stale_points() == [], f"{rep.created} {rep.replaced}")
    # bake_point / bake_layer の自動焼き直し
    rep = s.bake_point(1, 2, layer=0)
    check("bake_point(Neutral): 感情レイヤーの同じ位置も自動で焼き直す", rep.replaced == [nn, jn] and any("感情レイヤー" in n for n in rep.notes), f"{rep.replaced} {rep.notes}")
    rep = s.bake_layer(0)
    check("bake_layer(Neutral): Neutral の全点 + 感情レイヤーの同じ位置の点", len(rep.replaced) == 5 + 2 and jn in rep.replaced, f"{rep.replaced}")
    rep = s.bake_layer(1)
    check("bake_layer(感情): 感情レイヤーだけ（Neutral は足さない）", all("_Joy_" in n for n in rep.replaced) and not rep.notes, f"{rep.replaced} {rep.notes}")

    # ------------------------------------------------------------ プレビュー
    cam = cmds.camera(name="pvc")[0]
    s.camera_to_point(1, 1, cam)
    check("preview: 無いときは state none・exists False・rig None", s.preview_state() == "none" and not s.preview_exists() and s.preview_rig_node() is None and not s.preview_is_stale())
    rep = s.preview_build(cam)
    asset = "mini"
    rig = s.preview_rig_node()
    check("preview_build: rig ができる・state live・ターゲット 7 本・警告なし", rig == "tdFacialPreview_mini" and s.preview_state() == "live" and rep.targets == 7 and not rep.warnings, f"{rig} {s.preview_state()} {rep}")
    check("preview_camera: 今のカメラ", s.preview_camera() == cmds.ls(cam, long=True)[0])
    cams = s.model_cameras()
    check("model_cameras: パネルが無ければ persp", cams == ["persp"], f"{cams}")
    s.camera_to_point(1, 2, cam)
    w = s.preview_weights()
    check("preview: カメラを点 (1,2) へ動かすとその点の Neutral = 1", abs(w[nn] - 1.0) < 1e-4 and sum(v for k, v in w.items() if k != nn and "_Joy_" not in k) < 1e-4, f"{w}")
    s.camera_to_point(1, 0, cam)
    w = s.preview_weights()
    check("preview: カメラを動かすと重みが変わる（(1,0) = 1・(1,2) = 0）", abs(w[naming.morph_name('mini', 'Neutral', 1, 0)] - 1.0) < 1e-4 and abs(w[nn]) < 1e-4)
    s.camera_to_point(1, 2, cam)
    s.preview_set_enabled(False)
    w = s.preview_weights()
    check("preview: 補正なし（A/B）で重みがすべて 0", not s.preview_is_enabled() and max(abs(v) for v in w.values()) < 1e-9)
    s.preview_set_enabled(True)
    check("preview: 補正あり（A/B）で戻る", abs(s.preview_weights()[nn] - 1.0) < 1e-4)
    s.preview_set_attr("alpha", 0.5)
    check("preview: 強さ（alpha）0.5 で半分", abs(s.preview_weights()[nn] - 0.5) < 1e-4)
    s.preview_set_attr("alpha", 1.0)
    s.preview_set_attr("emotion_Joy", 1.0)
    w = s.preview_weights()
    check("preview: 感情（Joy = 1）で Joy の点の重みが出る", abs(w[jn] - 1.0) < 1e-4 and abs(w[nn] - 1.0) < 1e-4, f"{w[jn]} {w[nn]}")
    s.preview_set_attr("emotion_Joy", 0.0)
    s.preview_set_attr("useManual", True)
    s.preview_set_attr("manualYaw", 0.0)
    s.preview_set_attr("manualPitch", 0.0)
    st = s.preview_status()
    check("preview_status: 手動角度・出力角度・感情の一覧", st.use_manual and abs(st.out_yaw) < 1e-3 and [e[0] for e in st.emotions] == ["Joy"] and st.state == "live" and st.camera == cam, f"{st}")
    s.preview_set_attr("useManual", False)
    try:
        s.preview_set_attr("nonsense", 1)
        bad = False
    except S.FacialSessionError:
        bad = True
    check("preview_set_attr: 知らないアトリビュートは失敗", bad)
    # 編集状態との共存
    s.camera_to_point(1, 2, cam)
    s.begin_edit()
    s.select_point(1, 2)
    wz = s.preview_weights()
    check("共存: 編集中はプレビューの重みが止まる（基準姿勢のポーズが当たる。FC_* は 0）", max(abs(v) for v in wz.values()) < 1e-9 and s.editing, f"{max(wz.values())}")
    check("共存: 編集中の preview_status.editing", s.preview_status().editing)
    s.end_edit()
    check("共存: 編集を終えるとプレビューが再開する", abs(s.preview_weights()[nn] - 1.0) < 1e-4)
    # 編集中に作り直し → 保留 → 編集を終えると作り直す
    s.begin_edit()
    s.select_point(1, 2)
    r = s.resize(5, 3)
    check("共存: 編集中に格子を変えると作り直しは保留（stale）", r.ok and s.preview_is_stale() and s._preview_pending and s.editing)
    s.end_edit()
    check("共存: 編集を終えると自動で作り直す（stale でなくなる）", not s.preview_is_stale() and s.preview_state() == "live" and not s._preview_pending)
    # rig のキーは作り直しで残る
    cmds.setKeyframe(f"{rig}.emotion_Joy", time=1, value=0.2)
    cmds.setKeyframe(f"{rig}.emotion_Joy", time=10, value=1.0)
    s.set_edge_fade(10)
    rep = s.bake_all()
    check("自動の作り直し: ベイクのあとに rig が作り直される（notes）・キーが残る", any("プレビューを作り直しました" in n for n in rep.notes) or not s.preview_is_stale(), f"{rep.notes}")
    check("自動の作り直し: rig の感情のキーが残る・live", (cmds.keyframe(f"{rig}.emotion_Joy", query=True, keyframeCount=True) or 0) == 2 and s.preview_state() == "live")
    st = s.preview_status()
    check("preview_status: キーが打ってある感情は keyed", st.emotions and st.emotions[0][3])
    try:
        s.preview_set_attr("emotion_Joy", 0.5)
        bad = False
    except S.FacialSessionError:
        bad = True
    check("preview_set_attr: キーが打ってあるアトリビュートは変えられない", bad)
    cmds.cutKey(rig, attribute="emotion_Joy", clear=True)
    # 外でベイクすると stale → 作り直し
    s.begin_edit()
    s.end_edit()
    s.set_target(extra_meshes=[])  # 何も変わらない（Undo に積まれない）
    bakemod.bake(s.doc)  # session を通さない（自動の作り直しが走らない）
    s.refresh_scene()
    s.set_grid(cols=3)  # 格子を戻す → 自動の作り直し
    check("格子を変えて・ベイクし直すと自動で作り直される", s.preview_state() == "live")
    s.bake_all()
    # 強制的に stale: セッションを通さず FC_ を 1 つ消す
    scene.delete_targets(scene.blend_shapes(face)[0], [nn])
    check("stale: セッションを通さずシェイプが消えると is_stale", s.preview_is_stale() and s.preview_state() == "stale")
    rep = s.preview_build()
    check("preview_build（作り直し）: 同じカメラのまま・live に戻る", s.preview_state() == "live" and s.preview_camera() == cmds.ls(cam, long=True)[0] and not rep.warnings)
    s.bake_all()
    # キーに焼く
    s.camera_to_point(1, 2, cam)
    res = s.preview_bake_to_keys(1, 5, 1)
    check("preview_bake_to_keys: state keys・FC_* にキー", s.preview_state() == "keys" and len(res["frames"]) == 5 and nn in res["keyed"], f"{s.preview_state()} {res['keyed']}")
    s.set_edge_fade(20)
    check("キーに焼いたあと: 自動の作り直しは式を戻さない", s.preview_state() == "keys")
    rep = s.preview_build()
    check("キーが残っていると作り直しは警告（他から接続されていて配線しない）", rep.warnings and s.preview_warnings, f"{rep.warnings}")
    n = s.preview_clear_keys()
    fc_anim = [c for c in cmds.ls(type="animCurve") if cmds.listConnections(c + ".output", plugs=True) and any(".weight[" in p for p in cmds.listConnections(c + ".output", plugs=True))]
    check("preview_clear_keys: FC_* のキーが消える・weight は 0", n > 0 and not fc_anim, f"{n} {fc_anim}")
    rep = s.preview_build()
    check("キーを消して作り直すと警告なし・live", not rep.warnings and s.preview_state() == "live" and not s.preview_warnings, f"{rep.warnings}")
    check("preview_delete: rig が消える・FC_* の重みは 0", s.preview_delete() and not s.preview_exists() and all(abs(cmds.getAttr(c.plug)) < 1e-9 for c in scene.list_curves(face, include_fc=True) if naming.is_fc_name(c.alias)))
    check("preview_delete: 無ければ False", s.preview_delete() is False)

    # ------------------------------------------------------------ T-20
    node = "tdViewCorrection_mini_face"
    targets = []
    for key in ("front", "threeQuarter", "side"):
        d = cmds.duplicate(face, name=f"mini_face_vc_{key}")[0]
        cmds.delete(d, constructionHistory=True)
        if cmds.listRelatives(d, parent=True):
            d = cmds.parent(d, world=True)[0]
        cmds.move(0, 0.4 * (len(targets) + 1), 0, f"{d}.vtx[0:10]", relative=True)
        cmds.setAttr(f"{d}.visibility", 0)
        targets.append(d)
    cmds.blendShape(*targets, face, name=node, frontOfChain=True)
    bs_before = {a: i for a, i in scene.target_indices(node).items()}
    look = {"characterSettings": {"viewCorrection": {"mesh": "mini_face", "front": "mini_face_vc_front", "threeQuarter": "mini_face_vc_threeQuarter", "side": "mini_face_vc_side"}}}
    look_copy = json.loads(json.dumps(look))
    s.refresh_scene()
    try:
        s.import_t20(None)
        nolook = False
    except S.NoLookError:
        nolook = True
    check("import_t20: 開いている Toon の Look が無ければ NoLookError（look.json を選ぶ）", nolook)
    s.clear_layer()
    s.begin_edit()
    s.select_point(1, 2)
    s.set_curve("bs.mouth_open", 0.8)
    s.save_point()
    s.end_edit()  # (1,2) にキーがある（3 列の格子では Yaw 90° の列）
    n_undo = len(s._undo)
    r = s.import_t20(look)
    pts = s.doc.layers[0].points
    check("import_t20: Yaw 0 の列 (R1 C1) に正面のキー（ポーズ = {curve: 1.0}）", (1, 1) in pts and pts[(1, 1)].is_key and pts[(1, 1)].pose.curves == {f"{node}.mini_face_vc_front": 1.0}, f"{r.message}")
    check("import_t20: 3 列の格子では 45° の列が無いので 3/4 は飛ばす（理由と、Yaw 範囲 90°・列数 5 の案内）", any("3/4" in x and "その角度の列がありません" in x for x in r.skipped) and "列数 5" in r.message, r.message)
    check("import_t20: 既にキーがある点（横 → R1 C2）は上書きしない", any("R1 C2" in x for x in r.existing) and "bs.mouth_open" in pts[(1, 2)].pose.curves, f"{r.existing}")
    check("import_t20: 使ったシェイプが作業セットに入る・Undo は 1 回", f"{node}.mini_face_vc_front" in s.doc.working_set.curves and len(s._undo) == n_undo + 1)
    check("import_t20: 結果の文に 自動生成 / T-20 のプレビューをオフ の案内", "自動生成" in r.message and "viewCorrection" in r.message)
    s.undo()
    check("import_t20: Undo で戻る", (1, 1) not in s.doc.layers[0].points and (1, 2) in s.doc.layers[0].points)
    s.set_grid(cols=5)
    s.clear_layer()
    r = s.import_t20(look)
    pts = s.doc.layers[0].points
    got = {(d["key"], d["row"], d["col"], d["yaw"]) for d in r.imported}
    check("import_t20: 5 列の格子で Yaw 0 / 45 / 90° の 3 点（R1 C2 / C3 / C4）", r.ok and got == {("front", 1, 2, 0.0), ("threeQuarter", 1, 3, 45.0), ("side", 1, 4, 90.0)} and not r.skipped, f"{got} {r.message}")
    check("import_t20: T-20 のデータ（blendShape のターゲット）と Look は変わらない", {a: i for a, i in scene.target_indices(node).items()} == bs_before and look == look_copy)
    lp = tmp / "look.json"
    lp.write_text(json.dumps(look), encoding="utf-8")
    s.clear_layer()
    r = s.import_t20(lp)
    check("import_t20: look.json のパスからも取り込める", r.ok and len(r.imported) == 3)
    look2 = {"characterSettings": {"viewCorrection": {"mesh": "mini_face", "front": "no_such_target", "threeQuarter": "", "side": "mini_face_vc_side"}}}
    s.clear_layer()
    r = s.import_t20(look2)
    check("import_t20: シーンに無いシェイプ・未登録は飛ばして理由を出す", len(r.imported) == 1 and any("no_such_target" in x for x in r.skipped) and any("登録されていません" in x for x in r.skipped), r.message)
    r = s.import_t20({"characterSettings": {"viewCorrection": {"mesh": "", "front": "", "threeQuarter": "", "side": ""}}})
    n_undo = len(s._undo)
    check("import_t20: viewCorrection が空なら ok=False・文書は変えない", not r.ok and r.code == "no_t20" and len(s._undo) == n_undo)
    s.clear_layer()
    s.import_t20(look)
    r = s.generate()
    check("T-20 のキーから自動生成できる（左側が埋まる）", r.ok and len(s.doc.layers[0].points) > 3)
    # 後片付け
    s.close()
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    maya.standalone.initialize(name="python")
    try:
        run()
        run_integration()
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
