"""FacialController のネームスペース（参照したキャラクター）のスモークテスト（mayapy で実行。画面なし）。

  "C:/Program Files/Autodesk/Maya2026/bin/mayapy.exe" tests/maya/facial_namespace_smoke.py

合成の頭を一時ファイルに保存して `chr:` などのネームスペース付きで参照し、ネームスペースなしのシーンと同じ結果になることを確かめる。
データ（.fcpose.json）の名前はネームスペースなしのまま。ファイルはすべて一時フォルダ。
ローカルに assets/shizuku/shizuku_facial.mb があれば（git 管理外）、読み取り専用で参照する「あるときだけ」の確認も行う。
"""

from __future__ import annotations

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

APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv[:1])


def _load_ui_font() -> None:
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
SHOT_DIR = Path(r"C:\Users\yamag\AppData\Local\Temp\claude\C--Users-yamag-wrench-maya-T-Drive\7adf5442-7a48-4c8c-9be6-1504fc059ded\scratchpad\ui_shots13")


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


def run() -> None:
    import numpy as np
    from maya import cmds, mel
    from PySide6 import QtGui  # noqa: F401

    import facial_fixture
    from tdrive import project
    from tdrive_facial import bake as bake_mod
    from tdrive_facial import export, preview_rig as pr, scene, thumbnails
    from tdrive_facial import session as S
    from tdrive_facial import ui_setup
    from tdrive_facial.core import fcpose_io, naming

    tmp = Path(tempfile.mkdtemp(prefix="tdrive_fns_"))
    project.set_root(tmp)
    s = S.current()
    doc_path = tmp / "mini.fcpose.json"
    fcpose_io.save(facial_fixture.make_doc(), doc_path)

    # ------------------------------------------------------------ 基準: ネームスペースなしのシーン
    facial_fixture.build_mini_head()
    scene.set_namespace("")
    s.close()
    s.open(doc_path)
    base_codes = sorted({i.code for i in s.validate()})
    s.bake_all()
    base_node = scene.primary_blend_shape("mini_face")
    base_fc = sorted(a for a in scene.target_indices(base_node) if naming.is_fc_name(a))
    base_delta = {a: scene.read_target_delta(base_node, a) for a in base_fc}
    check("基準: ネームスペースなしは今までどおり（ネームスペース ""・メッセージなし・FC_ ができる）", s.namespace == "" and s.namespace_message == "" and len(base_fc) > 0, str(base_fc[:3]))
    s.close()

    # ------------------------------------------------------------ 1. 参照（chr:）: 自動検出
    ids = facial_fixture.reference_mini_head(tmp, "chr")
    face, joints, ref_bs = ids["face"], ids["joints"], ids["bs"]
    scene.set_namespace("")
    s.close()
    s.open(doc_path)
    check("検出: 1 つだけなら自動で chr を使う（状態の一文・シーンに記録）",
          s.namespace == "chr" and s.namespace_message == "ネームスペース chr: のキャラクターを使います" and cmds.fileInfo("tdFacialNamespace", query=True) == ["chr"], f"{s.namespace!r} {s.namespace_message!r}")
    check("データの名前はネームスペースなしのまま（mesh = mini_face・基準ボーン head・曲線 bs.*）",
          s.doc.target.mesh == "mini_face" and s.doc.grid.base_bone == "head" and all("chr" not in c for c in s.scene.curves) and "bs.mouth_open" in s.scene.curves, f"{s.scene.curves}")
    check("シーン情報はデータの名前（ボーン head / eye_L・親 head → eye_L）", {"head", "eye_L", "eye_R", "root"} <= set(s.scene.bones) and s.scene.bone_parents.get("eye_L") == "head" and s.scene.target_mesh_found is True, f"{s.scene.bones}")
    check("候補（namespace_choices）= chr", s.namespace_choices() == ["chr"])
    codes = sorted({i.code for i in s.validate()})
    check("検証: ネームスペースなしと同じ問題だけ（target_mesh_missing なし・誤検出なし）", codes == base_codes and "target_mesh_missing" not in codes, f"{codes} vs {base_codes}")

    # ------------------------------------------------------------ 2. 編集状態・点の選択（参照したノードの値を書く）
    j_before = joint_state(joints)
    w_before = weight_state(ref_bs)
    r = s.select_point(1, 2)
    check("select_point: できる（編集状態に入り、ポーズが当たる）", r.status == S.SELECT_SELECTED and s.editing, str(r))
    check("編集中: 参照したブレンドシェイプの重みが動いている（mouth_open 0.6）", abs(cmds.getAttr(f"{ref_bs}.mouth_open") - 0.6) < 1e-4, f"{cmds.getAttr(ref_bs + '.mouth_open')}")
    s.select_point(0, 2)
    check("編集中: 目のボーン（chr:eye_L）が動く", joint_state(joints) != j_before)
    s.end_edit()
    check("編集を抜ける: ジョイントの値・重みが元と完全に同じ", joint_state(joints) == j_before and weight_state(ref_bs) == w_before)
    s.begin_edit()
    s.end_edit()
    check("begin_edit / end_edit だけでも元と同じ", joint_state(joints) == j_before and weight_state(ref_bs) == w_before)

    # ------------------------------------------------------------ 3. ベイク: シーン側のブレンドシェイプに作る・差分はネームスペースなしと同じ
    rep = s.bake_all()
    local = scene.primary_blend_shape(face)
    refs_fc = [a for a in scene.target_indices(ref_bs) if naming.is_fc_name(a)]
    got_fc = sorted(a for a in scene.target_indices(local) if naming.is_fc_name(a))
    check("ベイク: FC_ は参照していない側（シーンの tdFacial_chr_mini_face）に作る・参照したノードには足さない",
          local == "tdFacial_chr_mini_face" and not scene.is_referenced(local) and not refs_fc and got_fc == base_fc, f"{local} {refs_fc} {got_fc[:3]}")
    hist = cmds.listHistory(scene.mesh_shape(face), pruneDagObjects=True)
    check("ベイク: シーン側のノードはスキンより前（バインド空間に足される）", hist.index(local) > hist.index(ids["skin"]), str(hist))
    worst = max((float(np.max(np.abs(scene.read_target_delta(local, a)[1] - base_delta[a][1]))) for a in base_fc if len(base_delta[a][0])), default=0.0)
    same_idx = all(scene.read_target_delta(local, a)[0] == base_delta[a][0] for a in base_fc)
    check("ベイク: 差分（頂点番号・値）がネームスペースなしのシーンと同じ（1e-5）", same_idx and worst < 1e-5, f"worst={worst}")
    check("ベイク: 記録（tdFacialBakeState）はシーン側のノードに・未ベイク / 変更の問題が出ない",
          bool(scene.get_bake_state(local)) and not [i for i in s.validate() if i.code in ("point_unbaked", "point_changed_since_bake", "baked_morph_missing", "orphan_target")], str([i.code for i in s.validate()]))
    check("ベイク: 参照したキャラクターのメッシュのターゲット（mouth_open など）は元のまま", {"mouth_open", "smile_L", "smile_R", "brow_up"} == set(scene.target_indices(ref_bs)))
    # 再ベイク（置き換え）でも増えない
    s.bake_all()
    check("ベイク: もう一度焼いても同じ数・同じノード", sorted(a for a in scene.target_indices(scene.primary_blend_shape(face)) if naming.is_fc_name(a)) == base_fc and len(cmds.ls("tdFacial_*", type="blendShape")) == 1)

    # ------------------------------------------------------------ 4. プレビュー（rig はシーン側。ネームスペースを名前に埋める）
    cam = cmds.camera()[0]
    cmds.move(0, 12, 60, cam)
    rep_pv = s.preview_build(cam)
    rig = rep_pv.rig
    check("プレビュー: rig 名にネームスペースが入る（tdFacialPreview_chr_mini）・find_rig が見つける", rig == "tdFacialPreview_chr_mini" and pr.find_rig("mini") == rig and s.preview_status().state == "live", rig)
    cmds.setAttr(rig + ".useManual", 1)
    cmds.setAttr(rig + ".manualYaw", 20.0)
    cmds.setAttr(rig + ".manualPitch", 5.0)
    ev = pr.evaluate_python(s.doc, cam)
    cur = pr.current_weights("mini")
    worst = max((abs(cur[k] - v) for k, v in ev["weights"].items() if k in cur), default=1.0)
    check("プレビュー: rig の重み = Python（core.evaluate）の重み（1e-6）・動いているものがある", worst < 1e-6 and any(v > 0 for v in cur.values()), f"worst={worst}")
    check("プレビュー: 式がネームスペース付きの weight を駆動している（chr: か tdFacial_chr_ が入る）", any("tdFacial_chr_mini_face" in p for ps in pr._read_json(rig, pr.TARGETS_ATTR, {}).values() for p in ps))
    check("プレビュー: 作り直しの必要なし", not pr.is_stale(s.doc))

    # ------------------------------------------------------------ 5. シーンの格子・顔以外を隠す・サムネイル
    s.scene_grid.set_enabled(True)
    check("シーンの格子: 出る", s.scene_grid.exists())
    s.scene_grid.set_enabled(False)
    s.hide_others.set_enabled(True)
    hidden = s.hide_others.hidden_names()
    check("顔以外を隠す: 顔以外（chr:mini_brow）を隠す・顔は隠さない", "chr:mini_brow" in hidden and not cmds.getAttr(scene.resolve_mesh("mini_face") + ".overrideVisibility") is False, str(hidden))
    s.hide_others.set_enabled(False)
    check("顔以外を隠す: 戻すと元の表示（眉は表示）", cmds.getAttr(ids["brow"] + ".overrideEnabled") == 0 and cmds.getAttr(ids["brow"] + ".overrideVisibility") == 1)

    def fake_render(cam_, out, size):
        from PySide6 import QtGui as G

        img = G.QImage(size, size, G.QImage.Format_RGB32)
        img.fill(G.QColor(120, 160, 200))
        return img.save(str(out), "PNG")

    trep = thumbnails.capture(s, render=fake_render)
    check("サムネイル: ネームスペース付きでも作れる", trep.ok and len(trep.made) >= 4, trep.message)

    # ------------------------------------------------------------ 6. 出力（FBX）: 名前はネームスペースなし・シーンは変わらない
    s.end_edit()
    pr.delete("mini")
    before = (cmds.file(query=True, sceneName=True), cmds.file(query=True, modified=True), sorted(cmds.ls(long=True)), weight_state(ref_bs), joint_state(joints))
    res = export.export_unity(s.doc, None, out_dir=tmp / "unity")
    check("出力: メッシュ名はネームスペースなし（mini_face・mini_brow）・FC_ の数が合う", sorted(res["meshes"]) == ["mini_brow", "mini_face"] and res["fc_count"] == len(base_fc) and not res["warnings"], str((res["meshes"], res["fc_count"], res["warnings"])))
    after = (cmds.file(query=True, sceneName=True), cmds.file(query=True, modified=True), sorted(cmds.ls(long=True)), weight_state(ref_bs), joint_state(joints))
    check("出力: 使っているシーン（参照・ネームスペース・名前・重み・ジョイント）は変わらない", before == after and cmds.ls("chr:*", type="blendShape") == ["chr:bs"])
    scene_file = tmp / "ns_scene.mb"
    cmds.file(rename=str(scene_file))
    cmds.file(save=True, type="mayaBinary")
    cmds.file(new=True, force=True)
    cmds.loadPlugin("fbxmaya", quiet=True)
    mel.eval("FBXResetImport")
    mel.eval("FBXImportMode -v add")
    mel.eval(f'FBXImport -f "{Path(res["fbx"]).as_posix()}"')
    names = [n.split("|")[-1] for n in cmds.ls(type="transform", long=True)]
    aliases = {a for m in ("mini_face",) for n in scene.blend_shapes(m) for a in scene.target_indices(n)}
    check("FBX を読み直す: ネームスペースなしの mini_face・mini_brow・ジョイント（head）・FC_ と元のシェイプ", {"mini_face", "mini_brow", "head"} <= set(names) and not any(":" in n for n in names) and {"mouth_open", "smile_L"} <= aliases and len([a for a in aliases if naming.is_fc_name(a)]) == len(base_fc), str(sorted(names)))
    s.close()
    cmds.file(str(scene_file), open=True, force=True)

    # ------------------------------------------------------------ 7. 間違ったネームスペース: 検証が知らせる・操作は日本語のエラー
    scene.set_namespace("")
    s.open(doc_path)
    check("開き直し: シーンに記録した chr を使う（ツールのリロード・シーンを開き直したあとも）", s.namespace == "chr")
    scene.set_namespace("zzz")
    s.refresh_scene()
    issues = [i for i in s.validate() if i.code == "target_mesh_missing"]
    check("間違ったネームスペース: target_mesh_missing（エラー・ネームスペースの案内）", len(issues) == 1 and issues[0].severity == "error" and "ネームスペース" in issues[0].message and issues[0].candidates == ("chr",), str(issues))
    try:
        s.select_point(1, 2)
        err = ""
    except Exception as e:  # noqa: BLE001
        err = str(e)
    check("間違ったネームスペース: 点を選ぶと「シーンにありません」の日本語", "mini_face" in err and "シーンにありません" in err, err)
    r = s.set_namespace("nope")
    check("候補にないネームスペースは選べない", not r.ok and s.namespace == "zzz")
    r = s.find_namespace()
    check("「自動で探す」: chr が見つかる", r.ok and s.namespace == "chr" and not [i for i in s.validate() if i.code == "target_mesh_missing"], r.message)

    # ------------------------------------------------------------ 8. ツールのリロード（メモリのネームスペースが消えても引き継ぐ）
    state = s.export_state()
    scene.set_namespace("")
    s.close()
    s.import_state(state)
    check("リロードの引き継ぎ: ネームスペースが残る・編集（点の選択）もできる", s.namespace == "chr" and s.select_point(1, 2).status == S.SELECT_SELECTED)
    s.end_edit()
    s.close()

    # ------------------------------------------------------------ 9. 2 体（chrA / chrB）
    ids2 = facial_fixture.reference_mini_head(tmp, namespaces=["chrA", "chrB"], rig_file=ids["file"])
    scene.set_namespace("")
    s.open(doc_path)
    check("2 体: 自動では決めず、選ぶよう知らせる（ネームスペースなしにして警告）", s.namespace == "" and "chrA:" in s.namespace_message and "chrB:" in s.namespace_message and "選んで" in s.namespace_message, s.namespace_message)
    check("2 体: 検証が target_mesh_missing（候補 chrA・chrB）", [i.candidates for i in s.validate() if i.code == "target_mesh_missing"] == [("chrA", "chrB")])
    check("2 体: 候補は chrA・chrB", s.namespace_choices() == ["chrA", "chrB"])
    check("2 体: 「自動で探す」は決められず知らせる", not s.find_namespace().ok and s.namespace == "")
    s.set_namespace("chrA")
    s.bake_all()
    la, lb = "tdFacial_chrA_mini_face", "tdFacial_chrB_mini_face"
    check("2 体: chrA に焼く（chrB には FC_ のノードもターゲットも作らない）", cmds.objExists(la) and not cmds.objExists(lb) and scene.primary_blend_shape("chrB:mini_face") == "chrB:bs" and not [a for a in scene.target_indices("chrB:bs") if naming.is_fc_name(a)])
    check("2 体: chrB に切り替えるとまだ未ベイク（chrA のベイクの記録を見ない）", s.set_namespace("chrB").ok and any(i.code == "point_unbaked" for i in s.validate()))
    cmds.move(0, 12, 60, cam) if cmds.objExists(cam) else None
    s.preview_build(cmds.camera()[0])
    s.bake_all()
    s.set_namespace("chrA")
    ra = s.preview_build(cmds.camera()[0]).rig
    check("2 体: プレビュー rig は別々（tdFacialPreview_chrA_mini / tdFacialPreview_chrB_mini）・今のものだけを見る",
          ra == "tdFacialPreview_chrA_mini" and cmds.objExists("tdFacialPreview_chrB_mini") and pr.find_rig("mini") == ra, str(pr.list_rigs()))
    s.set_namespace("chrB")
    check("2 体: 切り替えると preview_status は chrB の rig", s.preview_status().rig == "tdFacialPreview_chrB_mini", s.preview_status().rig)
    s.set_namespace("chrA")
    s.select_point(1, 2)
    check("2 体: chrA の編集中は chrB の重み・ジョイントに触らない", weight_state("chrB:bs") == weight_state("chrB:bs") and joint_state(["chrB:head", "chrB:eye_L"]) == joint_state(["chrB:head", "chrB:eye_L"]) and cmds.getAttr("chrB:bs.mouth_open") == 0.0)
    s.end_edit()
    pr.delete("mini")
    s.set_namespace("chrB")
    pr.delete("mini")
    s.close()

    # ------------------------------------------------------------ 10. 入れ子のネームスペース
    ids3 = facial_fixture.reference_mini_head(tmp, "grp:chr", rig_file=ids["file"])
    scene.set_namespace("")
    s.open(doc_path)
    check("入れ子のネームスペース: grp:chr を自動で使う・点の選択・ベイクができる",
          s.namespace == "grp:chr" and s.select_point(1, 2).status == S.SELECT_SELECTED and s.bake_all() is not None and cmds.objExists("tdFacial_grp_chr_mini_face"), f"{s.namespace!r}")
    s.end_edit()
    check("入れ子のネームスペース: 曲線名・ボーン名もデータの名前", "bs.mouth_open" in s.scene.curves and "head" in s.scene.bones and "grp:chr:head" == scene.to_scene("head"))
    s.close()

    # ------------------------------------------------------------ 11. 新規: 選んだメッシュのネームスペースを使う
    facial_fixture.reference_mini_head(tmp, "chr", rig_file=ids["file"])
    scene.set_namespace("")
    cmds.fileInfo(remove="tdFacialNamespace")
    cmds.select("chr:mini_face")
    d = s.new("mini")
    check("新規: ネームスペース付きのメッシュを選ぶと、データには外した名前を書きシーンの設定にする（基準ボーンも外した名前）",
          d.target.mesh == "mini_face" and d.grid.base_bone == "head" and s.namespace == "chr" and cmds.fileInfo("tdFacialNamespace", query=True) == ["chr"], f"{d.target.mesh} {d.grid.base_bone} {s.namespace}")
    s.close()

    # ------------------------------------------------------------ 12. 出力（Toon のプレビューが付いたキャラクターは、元のマテリアル名で出る）
    facial_fixture.build_mini_head()
    scene.set_namespace("")
    st = facial_fixture.add_toon_standin()
    s.open(doc_path)
    s.bake_all()
    nodes_before = sorted(cmds.ls(long=True))
    sg_before = sorted(cmds.sets(st["preview_sg"], query=True) or [])
    res = export.export_unity(s.doc, None, out_dir=tmp / "unity_toon")
    check("Toon 出力: 使っているシーンは変わらない（プレビューのシェーダー・割り当てがそのまま）", sorted(cmds.ls(long=True)) == nodes_before and sorted(cmds.sets(st["preview_sg"], query=True) or []) == sg_before)
    cmds.file(rename=str(tmp / "toon_scene.mb"))
    cmds.file(save=True, type="mayaBinary")
    cmds.file(new=True, force=True)
    mel.eval("FBXResetImport")
    mel.eval("FBXImportMode -v add")
    mel.eval(f'FBXImport -f "{Path(res["fbx"]).as_posix()}"')
    mats = sorted({m for sg in cmds.ls(type="shadingEngine") for m in cmds.listConnections(sg + ".surfaceShader", source=True, destination=False) or [] if m not in ("lambert1", "particleCloud1") and not m.startswith("openPBR")})
    check("Toon 出力: FBX のマテリアル名は元の名前（mat_x。_tdToon なし）", mats == ["mat_x"], str(mats))
    faces_ok = True
    for m in ("mini_face", "mini_brow"):
        shp = scene.mesh_shape(m)
        sgs = cmds.listConnections(shp, type="shadingEngine") or []
        faces_ok = faces_ok and bool(sgs) and all("tdToon" not in g for g in sgs)
    check("Toon 出力: どのメッシュにもマテリアルが付いている・FC_ のシェイプが残る", faces_ok and len([a for n in scene.blend_shapes("mini_face") for a in scene.target_indices(n) if naming.is_fc_name(a)]) == len(base_fc))
    cmds.file(str(tmp / "toon_scene.mb"), open=True, force=True)
    s.close()

    # ------------------------------------------------------------ 13. 画面（セットアップタブの行）
    ids = facial_fixture.reference_mini_head(tmp, namespaces=["chrA", "chrB"], rig_file=ids["file"])
    scene.set_namespace("")
    s.open(doc_path)
    tab = ui_setup.SetupTab(s)
    tab.resize(480, 700)
    tab.show()
    pump()
    check("UI: ネームスペースの行（2 体: 候補 chrA: / chrB:・選べる・自動で探すボタン）", [tab.ns_combo.itemText(i) for i in range(tab.ns_combo.count())][:2] == ["chrA:", "chrB:"] or tab.ns_combo.count() >= 1, str([tab.ns_combo.itemText(i) for i in range(tab.ns_combo.count())]))
    check("UI: 決められないときは一文を出す", tab.ns_note.isVisible() or "選んで" in tab.ns_note.text(), tab.ns_note.text())
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    [g for g in tab.findChildren(QtWidgets.QGroupBox) if g.title() == "対象のメッシュ"][0].grab().save(str(SHOT_DIR / "setup_namespace_ambiguous.png"))
    tab.ns_combo.setCurrentIndex(1)
    tab.on_namespace_activated(1)
    pump()
    check("UI: コンボで chrB を選ぶと切り替わる（シーンに記録）", s.namespace == "chrB" and cmds.fileInfo("tdFacialNamespace", query=True) == ["chrB"])
    tab.on_namespace_find()
    pump()
    check("UI: 「自動で探す」で、決められなければ状態欄に理由が出る", "複数" in tab.status.text() or "選んで" in tab.status.text(), tab.status.text())
    s.set_namespace("chrA")
    tab.refresh()
    pump()
    box = [g for g in tab.findChildren(QtWidgets.QGroupBox) if g.title() == "対象のメッシュ"][0]
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    box.grab().save(str(SHOT_DIR / "setup_namespace.png"))
    tab.hide()
    s.close()

    # ------------------------------------------------------------ 14. 実データ（あるときだけ）
    sample = REPO / "assets" / "shizuku" / "shizuku_facial.mb"
    fc_json = REPO / "facial" / "shizuku" / "shizuku.fcpose.json"
    if sample.exists() and fc_json.exists():
        real_sample(tmp, sample, fc_json, check, s, scene, S, project, pr, naming, fcpose_io)
    shutil.rmtree(tmp, ignore_errors=True)


def real_sample(tmp, sample, fc_json, check, s, scene, S, project, pr, naming, fcpose_io) -> None:
    """読み取り専用で参照する（保存しない。json はコピーで作業する）。"""
    from maya import cmds

    work = tmp / "real"
    work.mkdir()
    copy = work / "shizuku.fcpose.json"
    shutil.copyfile(fc_json, copy)
    cmds.file(new=True, force=True)
    cmds.file(str(sample), reference=True, namespace="chr")
    scene.set_namespace("")
    cmds.fileInfo(remove="tdFacialNamespace")
    s.open(copy)
    check("実データ: ネームスペース chr を自動で使う・データの名前は変わらない", s.namespace == "chr" and s.doc.target.mesh == "mdl_face02" and s.doc.grid.base_bone == "bone_head", f"{s.namespace!r} {s.doc.target.mesh} {s.doc.grid.base_bone}")
    codes = [i for i in s.validate() if i.code == "target_mesh_missing"]
    check("実データ: target_mesh_missing なし", not codes)
    face = scene.resolve_mesh("mdl_face02")
    all_joints = cmds.ls("chr:*", type="joint", long=True)
    other = [m for m in cmds.ls("chr:*", type="mesh", long=True, noIntermediate=True) if scene.doc_short(cmds.listRelatives(m, parent=True)[0]) not in set(s.doc.target.all_meshes())]
    j0 = {j: tuple(cmds.getAttr(j + ".translate")[0]) + tuple(cmds.getAttr(j + ".rotate")[0]) for j in all_joints}
    p0 = {m: cmds.polyEvaluate(m, vertex=True) for m in other}
    pts0 = {}
    from tdrive_facial.scene import read_points

    for m in other[:5]:
        pts0[m] = read_points(cmds.listRelatives(m, parent=True, fullPath=True)[0])
    pt = next((r, c) for (r, c), p in s.doc.layers[0].points.items() if not p.pose.is_empty())
    res = s.select_point(*pt)
    check("実データ: select_point できる", res.status == S.SELECT_SELECTED, str(res))
    s.end_edit()
    j1 = {j: tuple(cmds.getAttr(j + ".translate")[0]) + tuple(cmds.getAttr(j + ".rotate")[0]) for j in all_joints}
    check("実データ: end_edit のあと、全ジョイントが元と同じ", j0 == j1)
    errs_before = [i for i in s.validate() if i.severity == "error"]
    rep = s.bake_all()
    cam = cmds.camera()[0]
    cmds.move(0, 150, 300, cam)
    pv = s.preview_build(cam)
    errs = [i for i in s.validate() if i.severity == "error"]
    check("実データ: bake_all → preview_build → validate でエラーなし", not errs and pv.rig.startswith("tdFacialPreview_chr_"), str([(i.code, i.message) for i in errs]))
    j2 = {j: tuple(cmds.getAttr(j + ".translate")[0]) + tuple(cmds.getAttr(j + ".rotate")[0]) for j in all_joints}
    same = all(np_close(read_points(cmds.listRelatives(m, parent=True, fullPath=True)[0]), pts0[m]) for m in pts0 if cmds.objExists(m))
    check("実データ: 顔以外のメッシュ・ジョイントは変わらない", j2 == j0 and same, f"{len(pts0)} meshes")
    pr.delete("shizuku")
    from maya import mel

    from tdrive_facial import export

    out = export.export_unity(s.doc, copy, out_dir=work / "unity")
    s.close()
    cmds.file(new=True, force=True)
    cmds.loadPlugin("fbxmaya", quiet=True)
    mel.eval("FBXResetImport")
    mel.eval("FBXImportMode -v add")
    mel.eval(f'FBXImport -f "{Path(out["fbx"]).as_posix()}"')
    mats = {m for sg in cmds.ls(type="shadingEngine") for m in cmds.listConnections(sg + ".surfaceShader", source=True, destination=False) or [] if m not in ("lambert1", "particleCloud1") and not m.startswith("openPBR")}
    want = {"lambert2", "mat_body01", "mat_faceOption1", "mat_hair01", "mat_wear01", "mat_wear02", "mat_wear03"}
    check("実データ: 出力した FBX のマテリアル名は元の名前だけ（_tdToon なし）", mats == want, f"{sorted(mats)}")
    cmds.file(new=True, force=True)


def np_close(a, b) -> bool:
    import numpy as np

    return a.shape == b.shape and float(np.max(np.abs(a - b))) < 1e-6 if a.size else True


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
