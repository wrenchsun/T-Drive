"""命名タブのスモーク（AN-2。mayapy・ヘッドレス。dx11 / Render Override は使わない）。

  "C:/Program Files/Autodesk/Maya2026/bin/mayapy.exe" tests/maya/anim_naming_smoke.py

確かめること: 24 fps / メッシュ名 / マテリアル名 / 空のネームスペースが一覧に出る → 適用で SK_ / M_ になり、割り当て・Look・プレビュー用シェーダーの
追従が保たれる・1 回の Undo で戻る・テクスチャは提案のみ・シーン設定の修正・掃除・Humanoid 対応表・UI（一覧・検索・提案の編集）。
assets/misoten/MisotenChara_Rig.ma（Git 管理外）があれば、そちらでも必須 15 ボーンと 24 fps を確かめる。
"""

from __future__ import annotations

import copy
import json
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
sys.path.insert(0, str(REPO / "tests" / "fixtures" / "anim"))

from PySide6 import QtCore, QtWidgets  # noqa: E402

APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

import maya.standalone  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []
INFO: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(cond), detail))


def run() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="tdrive_anim_naming_"))
    try:
        _run(tmp)
        _misoten(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _run(tmp: Path) -> None:
    from maya import cmds

    import make_mini_rig
    from tdrive import project
    from tdrive_anim import scene_naming as SN
    from tdrive_anim import session as S
    from tdrive_anim import ui_naming
    from tdrive_anim.core import naming_rules as N
    from tdrive_toon import look as look_mod
    from tdrive_toon import session as toon

    project.set_root(tmp)
    rig = make_mini_rig.build()
    make_mini_rig.add_materials()
    s = S.current()
    s.new("MisotenChara", meshes=[rig["mesh"]])

    # ---- 検査
    rep = SN.inspect_scene("MisotenChara", s.data)
    kinds = {(f.kind, f.name): f for f in rep.findings}
    mesh_f = kinds.get(("mesh", rig["mesh"]))
    check("メッシュ名が一覧に出て SK_ の提案が付く", mesh_f is not None and mesh_f.suggestion == "SK_MisotenChara_Body", str(mesh_f))
    check("マテリアル Hair → M_MisotenChara_Hair", kinds.get(("material", "Hair")) and kinds[("material", "Hair")].suggestion == "M_MisotenChara_Hair")
    check("IdolGirl_Other2 → M_MisotenChara_Other2", kinds.get(("material", "IdolGirl_Other2")) and kinds[("material", "IdolGirl_Other2")].suggestion == "M_MisotenChara_Other2")
    check("プレビュー用 _tdToon は対象外", not any(f.name.endswith("_tdToon") for f in rep.findings))
    keys = {i.key for i in rep.settings}
    check("24 fps（film）が❌で出る", "time" in keys and any(i.key == "time" and i.severity == "error" for i in rep.settings), str(rep.settings))
    check("掃除候補: 未使用マテリアルと空ネームスペース", {c.kind for c in rep.cleanup} >= {"material", "namespace"}, str([c.label for c in rep.cleanup]))
    check("掃除候補にプレビュー用は出ない", not any("tdToon" in c.label for c in rep.cleanup))
    check("検査はシーンを変えない", cmds.objExists(rig["mesh"]) and cmds.objExists("Hair"))
    check("出力を止める❌がある", rep.has_errors)

    # ---- Look を用意（Toon のセッションに開く）
    lk = look_mod.new_look("miso", "MisotenChara")
    look_mod.register_part(lk, "hair", "hair", ["Hair"])
    look_mod.register_part(lk, "cloth", "cloth", ["IdolGirl_Other2"])
    ts = toon.current()
    ts.look, ts.path = copy.deepcopy(lk), tmp / "looks" / "miso" / "look.json"

    # ---- 適用: 1 回の Undo
    assign_before = {sg: sorted(cmds.sets(sg, query=True)) for sg in ("HairSG", "IdolGirl_Other2SG")}
    res = SN.apply_renames([f for f in rep.findings if f.kind in ("mesh", "material")])
    check("メッシュが SK_ になる", cmds.objExists("SK_MisotenChara_Body") and not cmds.objExists(rig["mesh"]), str(res))
    check("マテリアルが M_ になる", cmds.objExists("M_MisotenChara_Hair") and cmds.objExists("M_MisotenChara_Other2"))
    check("割り当てが保たれる（SG のメンバーが同じ）",
          {sg: sorted(m.replace("SK_MisotenChara_Body", rig["mesh"]) for m in cmds.sets(sg, query=True)) for sg in ("HairSG", "IdolGirl_Other2SG")} == assign_before,
          str(assign_before))
    check("スキンが壊れない", bool(cmds.ls(type="skinCluster")))
    check("プレビュー用シェーダーが追従（名前と tdSourceMaterial）",
          cmds.objExists("M_MisotenChara_Hair_tdToon") and cmds.getAttr("M_MisotenChara_Hair_tdToon.tdSourceMaterial") == "M_MisotenChara_Hair")
    msgs = SN.follow_look(res["materials"], res["meshes"])
    check("開いている Look の名前が追従", "M_MisotenChara_Hair" in ts.look["materials"] and ts.look["parts"]["hair"]["materials"] == ["M_MisotenChara_Hair"] and ts.dirty, str(msgs))
    cmds.undo()
    check("1 回の Undo で全部戻る", cmds.objExists(rig["mesh"]) and cmds.objExists("Hair") and cmds.objExists("Hair_tdToon") and cmds.objExists("IdolGirl_Other2")
          and not cmds.objExists("M_MisotenChara_Hair"))
    check("Undo 後の割り当て", {sg: sorted(cmds.sets(sg, query=True)) for sg in ("HairSG", "IdolGirl_Other2SG")} == assign_before)

    # ---- Look がセッションに無いとき: ファイルを直す
    ts.look = None
    path = tmp / "looks" / "miso" / "look.json"
    look_mod.save(lk, path)
    SN.apply_renames([f for f in SN.inspect_scene("MisotenChara", s.data).findings if f.kind == "material"])
    msgs = SN.follow_look({"Hair": "M_MisotenChara_Hair"})
    saved = json.loads(path.read_text(encoding="utf-8"))
    check("Look のファイルも追従", "M_MisotenChara_Hair" in saved["materials"] and msgs, str(msgs))

    # ---- 既にある名前へは上書きしない・参照できない名前
    cmds.polyCube(name="SK_MisotenChara_Hat")
    cmds.polyCube(name="MisotenChara_Rig5_IdolGirl_Hat")
    f = N.inspect("mesh", "MisotenChara_Rig5_IdolGirl_Hat", "MisotenChara", node="MisotenChara_Rig5_IdolGirl_Hat")
    r2 = SN.apply_renames([f])
    check("同名があるときはスキップ", r2["skipped"] and cmds.objExists("MisotenChara_Rig5_IdolGirl_Hat"), str(r2))

    # ---- テクスチャは提案のみ
    tex = cmds.shadingNode("file", asTexture=True, name="file_face")
    cmds.setAttr(tex + ".fileTextureName", "D:/x/sourceimages/face_normal.png", type="string")
    lam = cmds.ls("M_MisotenChara_Hair", type="lambert")[0]
    cmds.connectAttr(tex + ".outColor", lam + ".color", force=True)
    ft = [f for f in SN.inspect_scene("MisotenChara", s.data).findings if f.kind == "texture"]
    check("テクスチャは提案のみ", ft and ft[0].proposal_only and ft[0].suggestion == "T_MisotenChara_Face_Normal.png", str(ft))
    r3 = SN.apply_renames(ft)
    check("提案のみは適用されない", r3["renamed"] == [] and cmds.getAttr(tex + ".fileTextureName").endswith("face_normal.png"))

    # ---- シーン設定
    SN.fix_settings(["time"], 30)
    check("30 fps に直る", cmds.currentUnit(query=True, time=True) == "ntsc")
    cmds.undo()
    check("時間単位の修正も 1 Undo", cmds.currentUnit(query=True, time=True) == "film")
    SN.fix_settings(["time", "linear", "up"], 30)
    check("設定の❌が消える", not [i for i in SN.scene_settings(30, "spine_C0_0_jnt") if i.key != "root"])

    # ---- 掃除（チェックしたものだけ）
    cands = SN.cleanup_candidates()
    ns_item = next(c for c in cands if c.kind == "namespace")
    check("名前空間 ffffff は空として出る", ns_item.namespace == "ffffff")
    n = SN.apply_cleanup([ns_item])
    check("空ネームスペースを削除", n == 1 and not cmds.namespace(exists="ffffff"))
    check("空のリストでも落ちない", SN.apply_cleanup([]) == 0 and SN.apply_cleanup([SN.CleanupItem("file", "x", [])]) == 0)
    mat_item = next(c for c in SN.cleanup_candidates() if c.kind == "material")
    SN.apply_cleanup([mat_item])
    check("未使用マテリアルを削除（割り当て済みは残る）", not cmds.objExists("Unused") and cmds.objExists("M_MisotenChara_Hair"))

    # ---- Humanoid（fixture のミニリグは必須 15 に足りない → 警告。推定できる分は入る）
    h = SN.humanoid_for("MisotenChara", "spine_C0_0_jnt")
    check("ミニリグの Hips / Spine / Chest が推定される", h["bones"].get("Hips") == "spine_C0_0_jnt" and h["bones"].get("Chest") == "spine_C0_2_jnt", str(h["bones"]))
    probs = SN.humanoid_problems(h, "spine_C0_0_jnt")
    check("足りない必須ボーンは警告", any("Head" in p for p in probs))

    # ---- UI
    cmds.currentUnit(time="film")
    ui = ui_naming.NamingTab(s)
    ui.inspect()
    APP.processEvents()
    n_rows = ui.table.rowCount()
    check("UI: 一覧に行が出る", n_rows >= 1 and "❌" in ui.summary.text(), ui.summary.text())
    ui.search.setText("zzzz")
    check("UI: 検索で全行が隠れる", all(ui.table.isRowHidden(r) for r in range(n_rows)))
    ui.search.setText("")
    check("UI: 検索を消すと戻る", not any(ui.table.isRowHidden(r) for r in range(n_rows)))
    ui.only_errors.setChecked(True)
    check("UI: ❌ だけ", all(ui.table.isRowHidden(r) == (not ui.report.findings[r].is_error) for r in range(n_rows)))
    ui.only_errors.setChecked(False)
    if n_rows:
        r = next((i for i, f in enumerate(ui.report.findings) if f.kind == "mesh" and f.suggestion), 0)
        ui.table.item(r, ui_naming.COL_SUG).setText("bad name")
        check("UI: 規則に合わない提案は赤い", ui.table.item(r, ui_naming.COL_SUG).toolTip() != "")
        ui.apply()
        check("UI: 規則に合わない提案は適用しない", "規則に合わない" in ui.result.text(), ui.result.text())
        ui.table.item(r, ui_naming.COL_SUG).setText("SK_MisotenChara_Torso")
        ui.apply()
        check("UI: 編集した提案で改名できる", cmds.objExists("SK_MisotenChara_Torso") or cmds.objExists("SK_MisotenChara_Body"), ui.result.text())
    ui.humanoid_infer()
    check("UI: Humanoid の表が出る", ui.h_table.rowCount() > 10)
    ui.h_search.setText("Hips")
    check("UI: Humanoid の検索", sum(not ui.h_table.isRowHidden(r) for r in range(ui.h_table.rowCount())) >= 1)
    ui.humanoid_save()
    check("UI: humanoid.json が保存される", (tmp / "anim" / "MisotenChara" / "humanoid.json").exists())
    ui.detach()

    # ---- クリップ名の改名（animset）
    from tdrive_anim.core import animset as A

    s.add_clip(A.default_clip("Run_F", 0, 10, "MisotenChara"))
    rep2 = SN.inspect_scene("MisotenChara", s.data)
    cf = next(f for f in rep2.findings if f.kind == "clip")
    check("クリップ名の❌と提案", cf.is_error and cf.suggestion == "A_MisotenChara_Run_F", str(cf))
    s.rename_clip("Run_F", cf.suggestion)
    check("クリップ名の改名（Undo 可）", A.find_clip(s.data, "A_MisotenChara_Run_F") is not None and s.undo() and A.find_clip(s.data, "Run_F") is not None)

    # ---- 壊れた規則ファイルは警告だけ
    (tmp / "anim").mkdir(exist_ok=True)
    (tmp / "anim" / "_naming.json").write_text("{bad", encoding="utf-8")
    rep3 = SN.inspect_scene("MisotenChara", s.data)
    check("壊れた _naming.json は警告だけで検査は続く", rep3.warnings and rep3.findings is not None)


def _misoten(tmp: Path) -> None:
    ma = REPO / "assets" / "misoten" / "MisotenChara_Rig.ma"
    if not ma.exists():
        INFO.append("assets/misoten/MisotenChara_Rig.ma が無いので本物のリグでの確認は飛ばしました")
        return
    from maya import cmds

    from tdrive import project
    from tdrive_anim import scene_naming as SN
    from tdrive_anim import session as S
    from tdrive_anim.core import humanoid_map as H

    project.set_root(tmp / "misoten")  # ミニリグの humanoid.json を拾わない
    cmds.file(new=True, force=True)
    cmds.file(str(ma), open=True, force=True, ignoreVersion=True, prompt=False)
    s = S.current()
    s.new("MisotenChara")
    rep = SN.inspect_scene("MisotenChara", s.data)
    check("[MisotenChara] 24 fps が❌で出る", any(i.key == "time" for i in rep.settings), str(rep.settings))
    meshes = [f for f in rep.findings if f.kind == "mesh"]
    check("[MisotenChara] メッシュ 3 枚に SK_ の提案", {f.suggestion for f in meshes} >= {"SK_MisotenChara_Body", "SK_MisotenChara_Skirt", "SK_MisotenChara_RibbonBack"}, str([(f.name, f.suggestion) for f in meshes]))
    mats = [f for f in rep.findings if f.kind == "material"]
    check("[MisotenChara] マテリアルに M_ の提案", mats and all(f.suggestion.startswith("M_MisotenChara_") for f in mats), str([(f.name, f.suggestion) for f in mats][:6]))
    check("[MisotenChara] 掃除候補（残骸）が出る", len(rep.cleanup) > 0, str(len(rep.cleanup)))
    h = SN.humanoid_for("MisotenChara", "spine_C0_0_jnt")
    check("[MisotenChara] 必須 15 ボーンが自動推定で埋まる", H.missing_required(h) == [], str(H.missing_required(h)))
    probs = SN.humanoid_problems(h, "spine_C0_0_jnt")
    check("[MisotenChara] Humanoid の検証に問題なし", probs == [], str(probs[:5]))
    INFO.append(f"[MisotenChara] 検査 {len(rep.findings)} 件（メッシュ {len(meshes)} / マテリアル {len(mats)}）、掃除候補 {len(rep.cleanup)} 件、extra {len(h['extra'])} 本")


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
        for line in INFO:
            print(f"SMOKE INFO {line}")
        print(f"SMOKE RESULT {len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
        sys.stdout.flush()
        maya.standalone.uninitialize()
        os._exit(1 if failed else 0)
