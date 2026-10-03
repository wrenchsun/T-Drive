"""FacialController の補間の種類（Catmull-Rom）とマテリアル連携（Toon の「顔の角度連動」へ角度を渡す）の Maya 側のスモーク（mayapy・画面なし）。

  set QT_QPA_PLATFORM=offscreen
  "C:/Program Files/Autodesk/Maya2026/bin/mayapy.exe" tests/maya/facial_f5_interp_smoke.py

確かめること:
- 補間: 双線形のとき式は従来と同一（バイト一致）/ catmullRom で式が変わり、rig = Python（evaluate_python。セルの境目・格子の点・範囲の外を含む角度の表 × シャープさ）
  / 設定を変えると古い印 / セットアップの「補間」の選択が文書に入る・保存の往復
- マテリアル連携: 受け口（ToonFacialYaw / Pitch / Strength）を持つ代わりのノード（ヘッドレスの mayapy では dx11Shader の受け口がノードの属性にならないため）。
  連携オフ = つながない・式が従来と同一 / オン = 接続・値がカメラに追従（Python の計算と一致）/ 消す = 接続なし・受け口 0 / 作り直し = つなぎ直し /
  受け口が無い = 「つないでいません」/ ほかから接続済みの受け口は触らない / キーに焼くと外れて 0
ファイルはすべて一時フォルダ。
"""

from __future__ import annotations

import math
import os
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
INFO: list[str] = []
W_TOL = 1e-4


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(cond), detail))


def spherical(center, yaw_deg: float, pitch_deg: float, dist: float = 55.0):
    y, p = math.radians(yaw_deg), math.radians(pitch_deg)
    return (
        center[0] + dist * math.sin(y) * math.cos(p),
        center[1] + dist * math.sin(p),
        center[2] + dist * math.cos(y) * math.cos(p),
    )


def run() -> None:
    from maya import cmds

    import facial_fixture
    from tdrive import project
    from tdrive_facial import preview_rig as pr
    from tdrive_facial import scene, ui
    from tdrive_facial import session as S
    from tdrive_facial.core import autofill, fcpose_io, naming
    from tdrive_facial.core import validate as V

    tmp = Path(tempfile.mkdtemp(prefix="tdrive_f5i_"))
    project.set_root(tmp)
    facial_fixture.build_mini_head()
    face = scene.resolve_mesh("mini_face")
    asset = "mini"
    s = S.current()
    s.close()
    s.listeners.clear()
    s.state_listeners.clear()
    S.FacialSession.model_cameras = staticmethod(lambda: ["persp", "pvcam"])
    cam = cmds.ls(cmds.camera(name="pvcam")[0], long=True)[0]
    head_c = tuple(cmds.xform("head", query=True, worldSpace=True, translation=True))
    cmds.setAttr(cam + ".translate", 0, 10, 60)

    doc0 = facial_fixture.make_doc()
    doc0.bake.delta_threshold = 1e-5
    autofill.generate_from_keys(doc0)
    path = tmp / "src" / "mini.fcpose.json"
    path.parent.mkdir()
    fcpose_io.save(doc0, path)
    ws_curves = ["bs.mouth_open", "bs.smile_L", "bs.smile_R", "bs.brow_up"]
    s.open(path)
    s.add_to_working_set(curves=ws_curves, bones=["eye_L", "eye_R"])
    s.set_mirror(bone_axis="X", suffix_l="_L", suffix_r="_R")
    s.bake_all()
    doc = s.doc

    def expr_text(rig: str) -> str:
        e = [n for n in pr._helper_nodes(rig) if cmds.nodeType(n) == "expression"]
        return cmds.expression(e[0], query=True, string=True) if e else ""

    # ============================================================ 1. 補間の種類
    check("補間: 既定は双線形・quality は無いまま（既存の文書は変わらない）", doc.quality is None or doc.quality.interpolation == "bilinear")
    rep_b = pr.build_ex(doc, cam)
    rig = rep_b.rig
    text_bilinear = expr_text(rig)
    sig_bilinear = cmds.getAttr(f"{rig}.tdFacialSignature")
    check("補間: 双線形の式に Catmull-Rom の記述が無い", "$kcs" not in text_bilinear and "$n0_0" not in text_bilinear)

    r = s.set_quality(interpolation="catmullRom")
    ok1 = r.ok and s.doc.quality.interpolation == "catmullRom"
    ok2 = s.undo() and (s.doc.quality is None or s.doc.quality.interpolation == "bilinear")
    ok3 = s.redo() and s.doc.quality.interpolation == "catmullRom"
    doc = s.doc  # 元に戻す / やり直しで Document は入れ替わる
    check("set_quality: 補間を catmullRom にできる・元に戻す / やり直し", ok1 and ok2 and ok3, f"{ok1} {ok2} {ok3}")
    check("set_quality: 同じ値は変更なし・知らない値は失敗して文書は変わらない", s.set_quality(interpolation="catmullRom").code == "unchanged" and not s.set_quality(interpolation="spline").ok and doc.quality.interpolation == "catmullRom")
    check("セッション: 補間を変えるとプレビューが自動で作り直される（最新・式が変わる）", not pr.is_stale(doc) and "$kcs" in expr_text(rig) and s.preview_state() == "live", s.preview_state())
    doc.quality.interpolation = "bilinear"  # セッションを通さずに変えた
    check("is_stale: 補間を変えると古い印（式が変わる）", pr.is_stale(doc))
    doc.quality.interpolation = "catmullRom"
    check("is_stale: 戻すと最新", not pr.is_stale(doc))
    rep_c = pr.build_ex(doc, cam)
    text_cr = expr_text(rep_c.rig)
    check("補間: catmullRom の式に Catmull-Rom の記述がある・署名が変わる", "$kcs" in text_cr and cmds.getAttr(f"{rig}.tdFacialSignature") != sig_bilinear and not pr.is_stale(doc))

    cmds.setAttr(rig + ".emotion_Joy", 0.6)
    angles = [(0, 0), (45, 0), (90, 45), (-90, -45), (-90, 0), (22.5, 11.25), (-67.5, 33.75), (78.75, -33.75), (10, 5), (92, 0), (-100, 50), (45, 22.5), (60, 40), (150, 0)]
    worst = 0.0
    for sharp in (1.0, 2.5):
        s.set_quality(sharpness=sharp)
        rig = pr.build_ex(doc, cam).rig
        cmds.setAttr(rig + ".emotion_Joy", 0.6)
        for yaw, pitch in angles:
            cmds.setAttr(cam + ".translate", *spherical(head_c, yaw, pitch))
            w = pr.current_weights(asset)
            ev = pr.evaluate_python(doc, cam)
            dw = max((abs(w[k] - ev["weights"][k]) for k in w), default=0.0)
            miss = set(ev["weights"]) ^ set(w)
            worst = max(worst, dw)
            check(f"catmullRom（シャープさ {sharp:g}）Yaw {yaw} / Pitch {pitch}: rig = Python（1e-4）", dw < W_TOL and not miss, f"dw={dw:.2e} miss={miss}")
    INFO.append(f"catmullRom × シャープさ 2 × 角度 {len(angles)}: rig = Python の最大誤差 {worst:.2e}")
    s.set_quality(sharpness=1.0)
    rig = pr.build_ex(doc, cam).rig
    cmds.setAttr(cam + ".translate", *spherical(head_c, 90, 0))  # 格子の点の上 = 双線形と同じ
    w = pr.current_weights(asset)
    n12 = naming.morph_name(asset, "Neutral", 1, 2)
    check("catmullRom: 格子の点の上ではその点が 1", abs(w[n12] - 1.0) < 1e-6 and abs(sum(v for k, v in w.items() if k.startswith("FC_mini_Neutral")) - 1.0) < 1e-6)

    s.set_quality(interpolation="bilinear")
    rep_b2 = pr.build_ex(doc, cam)
    check("補間: 双線形へ戻すと式がバイト一致（従来と同一）・署名も同じ", expr_text(rep_b2.rig) == text_bilinear and cmds.getAttr(f"{rep_b2.rig}.tdFacialSignature") == sig_bilinear)
    check("補間: 双線形のとき quality.interpolation は保存に出ない・catmullRom は出て往復する", "interpolation" not in fcpose_io.to_dict(doc).get("quality", {}))
    doc.quality.interpolation = "catmullRom"
    d_rt = fcpose_io.from_dict(fcpose_io.to_dict(doc))
    check("補間: 保存の往復で catmullRom が残る", d_rt.quality.interpolation == "catmullRom" and fcpose_io.to_dict(doc)["quality"]["interpolation"] == "catmullRom")
    doc.quality.interpolation = "spline"
    check("検証: 知らない補間はエラー", any(i.code == "quality_interpolation_invalid" and i.severity == V.SEVERITY_ERROR for i in s.validate()))
    doc.quality.interpolation = "bilinear"

    # ============================================================ 2. セットアップの「補間」の選択
    fpanel = ui.FacialPanel()
    panel = fpanel.tab("setup")
    if panel is not None:
        APP.processEvents()
        panel.refresh()
        check("セットアップ: 補間の選択肢は「双線形（標準）」「なめらか（Catmull-Rom）」", [panel.quality_interp.itemText(i) for i in range(panel.quality_interp.count())] == ["双線形（標準）", "なめらか（Catmull-Rom）"])
        panel.quality_interp.setCurrentIndex(1)
        panel.on_quality_changed()
        check("セットアップ: 選ぶと文書に入る", doc.quality.interpolation == "catmullRom")
        s.set_quality(interpolation="bilinear")
        panel.refresh()
        check("セットアップ: 文書の値が選択に出る", panel.quality_interp.currentData() == "bilinear")
        panel.material_link.setChecked(True)
        check("セットアップ: 「Toon マテリアルに顔の角度を渡す」をオンにすると文書に入る", doc.material is not None and doc.material.mode == "propertyBlock")
        s.set_material_link(False)
        panel.refresh()
        check("セットアップ: 文書の値がチェックに出る", not panel.material_link.isChecked())
    s.set_quality(interpolation="bilinear")

    # ============================================================ 3. マテリアル連携
    attrs = pr.toon_shader_attrs()
    check("連携: 受け口の名前は Toon の契約（ToonFacialYaw / Pitch / Strength）", attrs == ("ToonFacialYaw", "ToonFacialPitch", "ToonFacialStrength"), str(attrs))

    def make_standin(name: str, assign: bool) -> str:
        n = cmds.shadingNode("lambert", asShader=True, name=name)
        for a in attrs:
            cmds.addAttr(n, longName=a, attributeType="float", defaultValue=0.0)
        if assign:
            sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name=name + "SG")
            cmds.connectAttr(n + ".outColor", sg + ".surfaceShader", force=True)
            cmds.sets(face, edit=True, forceElement=sg)
        return n

    def links_of(node: str) -> list:
        return [cmds.listConnections(f"{node}.{a}", source=True, destination=False, plugs=True) or [] for a in attrs]

    def values_of(node: str) -> list:
        return [cmds.getAttr(f"{node}.{a}") for a in attrs]

    stand = make_standin("toonStandIn", True)
    s.set_quality(interpolation="bilinear")
    cmds.setAttr(cam + ".translate", *spherical(head_c, 45, -22.5))
    rep_off = pr.build_ex(doc, cam)
    rig = rep_off.rig
    check("連携オフ: つながない・式が従来と同一（バイト一致）・状態の一行は空", all(not x for x in links_of(stand)) and expr_text(rig) == text_bilinear and s.preview_status().material_link == "" and (not cmds.attributeQuery("toonYaw", node=rig, exists=True) or cmds.getAttr(rig + ".toonYaw") == 0.0), f"{links_of(stand)} {s.preview_status().material_link!r} {expr_text(rig) == text_bilinear}")

    r = s.set_material_link(True)
    m1 = r.ok and s.doc.material is not None and s.doc.material.mode == "propertyBlock" and s.set_material_link(True).code == "unchanged"
    m2 = s.undo() and (s.doc.material is None or s.doc.material.mode == "none")
    m3 = s.redo() and s.doc.material.mode == "propertyBlock"
    doc = s.doc
    check("連携: set_material_link(True) で material.mode が入る・同じ値は変更なし・元に戻す / やり直し", m1 and m2 and m3, f"{m1} {m2} {m3}")
    check("連携: オンにするとプレビューが自動で作り直される（最新）", not pr.is_stale(doc) and s.preview_state() == "live")
    doc.material.mode = "none"  # セッションを通さずに変えた
    check("is_stale: 連携を変えると古い印（式に出力が増える・減る）", pr.is_stale(doc))
    doc.material.mode = "propertyBlock"
    rep_on = s.preview_build(cam)
    rig = rep_on.rig
    text_on = expr_text(rig)
    check("連携オン: 式に toonYaw / toonPitch / toonStrength が増える", all(f"{rig}.{a}" in text_on for a in pr.RIG_TOON_ATTRS))
    lk = links_of(stand)
    check("連携オン: 3 つの受け口に rig の出力がつながる", [x[0] if x else None for x in lk] == [f"{rig}.{a}" for a in pr.RIG_TOON_ATTRS], str(lk))
    check("連携オン: 状態の一行「マテリアル連携: 1 個の Toon マテリアルにつなぎました」", s.preview_status().material_link == "マテリアル連携: 1 個の Toon マテリアルにつなぎました", s.preview_status().material_link)

    def follows(tag: str, **kw) -> None:
        ev = pr.evaluate_python(doc, cam, **kw)
        want = ev["toon"]
        got = values_of(stand)
        check(f"連携: {tag}: 受け口 = Python の計算（{want[0]:+.3f}, {want[1]:+.3f}, {want[2]:.3f}）", want is not None and all(abs(a - b) < 1e-5 for a, b in zip(got, want)), f"{got} vs {want}")

    follows("Yaw 45 / Pitch -22.5 = (0.5, -0.5, 1)")
    check("連携: 正規化は Yaw / 可動域・Pitch / 可動域（Unity の _FC_Angles.xy と同じ）", abs(values_of(stand)[0] - 0.5) < 1e-4 and abs(values_of(stand)[1] + 0.5) < 1e-4 and abs(values_of(stand)[2] - 1.0) < 1e-6, str(values_of(stand)))
    for yaw, pitch in ((-30, 10), (90, 45), (150, 80), (-120, -70)):
        cmds.setAttr(cam + ".translate", *spherical(head_c, yaw, pitch))
        follows(f"カメラ Yaw {yaw} / Pitch {pitch}")
    cmds.setAttr(cam + ".translate", *spherical(head_c, 150, 80))
    check("連携: 範囲の外は −1〜1 に丸める", values_of(stand)[0] == 1.0 and values_of(stand)[1] == 1.0, str(values_of(stand)))
    cmds.setAttr(cam + ".translate", *spherical(head_c, 45, -22.5))
    cmds.setAttr(rig + ".alpha", 0.5)
    follows("全体の強さ 0.5")
    s.preview_set_enabled(False)
    check("連携: 補正なしにすると強さ 0（角度はそのまま）", abs(values_of(stand)[2]) < 1e-9 and abs(values_of(stand)[0] - 0.5) < 1e-4, str(values_of(stand)))
    s.preview_set_enabled(True)
    cmds.setAttr(rig + ".alpha", 1.0)
    cmds.setAttr(rig + ".useManual", 1)
    cmds.setAttr(rig + ".manualYaw", -45.0)
    cmds.setAttr(rig + ".manualPitch", 45.0)
    follows("手動の角度 (−45, 45) = (−0.5, 1)")
    cmds.setAttr(rig + ".useManual", 0)

    # 消す → 切れて 0
    check("消す前: 受け口は 0 でない", any(abs(v) > 1e-6 for v in values_of(stand)))
    s.preview_delete()
    check("消す: 接続なし・受け口は 0 のまま（属性は残る）・rig は無い", all(not x for x in links_of(stand)) and values_of(stand) == [0.0, 0.0, 0.0] and not pr.exists(asset))
    # 作り直し → つなぎ直す
    rep2 = s.preview_build(cam)
    cmds.setAttr(cam + ".translate", *spherical(head_c, 45, -22.5))
    check("作り直し: つなぎ直される・値が追従", all(len(x) == 1 for x in links_of(stand)) and abs(values_of(stand)[0] - 0.5) < 1e-4, str(links_of(stand)))
    rig = rep2.rig
    rep2b = s.preview_build(cam)
    check("もう一度作り直しても接続は 1 本ずつ", all(len(x) == 1 for x in links_of(stand)))

    # ほかから接続されている受け口は触らない
    loc = cmds.spaceLocator(name="otherSrc")[0]
    s.preview_delete()
    cmds.connectAttr(f"{loc}.tx", f"{stand}.{attrs[1]}", force=True)
    rep3 = s.preview_build(cam)
    check("ほかから接続済みの受け口: つながない・その接続は残る・一行に出る", links_of(stand)[1] == [f"{loc}.translateX"] and len(links_of(stand)[0]) == 1 and "1 個" in s.preview_status().material_link and "ほかから" in s.preview_status().material_link, s.preview_status().material_link)
    s.preview_delete()
    check("ほかから接続済みの受け口: 消してもその接続は残る", links_of(stand)[1] == [f"{loc}.translateX"])
    cmds.disconnectAttr(f"{loc}.tx", f"{stand}.{attrs[1]}")
    cmds.delete(loc)

    # 元のマテリアル + <マテリアル>_tdToon（Toon のプレビュー用のシェーダー）
    cmds.delete(stand)
    cmds.sets(face, edit=True, forceElement="initialShadingGroup")
    rep4 = s.preview_build(cam)
    check("受け口を持つ Toon のシェーダーが無い: 「Toon のプレビューが無いため、つないでいません」", s.preview_status().material_link == "マテリアル連携: Toon のプレビューが無いため、つないでいません", s.preview_status().material_link)
    mat = (cmds.listConnections("initialShadingGroup.surfaceShader", source=True, destination=False) or [""])[0]
    sh = make_standin(mat + "_tdToon", False)
    rep5 = s.preview_build(cam)
    check("元のマテリアルの Toon プレビュー（<マテリアル>_tdToon）につなぐ", all(len(x) == 1 for x in links_of(sh)) and s.preview_status().material_link == "マテリアル連携: 1 個の Toon マテリアルにつなぎました", s.preview_status().material_link)

    # キーに焼く → 外れて 0
    cmds.setAttr(cam + ".translate", *spherical(head_c, 45, -22.5))
    out = s.preview_bake_to_keys(1, 3, 1)
    check("キーに焼く: 式が外れるので受け口の接続も外れて 0", all(not x for x in links_of(sh)) and values_of(sh) == [0.0, 0.0, 0.0], f"{links_of(sh)} {values_of(sh)}")
    s.preview_delete()

    # 連携をオフに戻す
    s.preview_clear_keys()  # 焼いたキーを消す（キーがあると FC_* に配線できず、式が基準と変わる）
    s.set_material_link(False)
    rep6 = s.preview_build(cam)
    check("連携オフに戻して作り直す: つながない・式が従来と同一（バイト一致）・一行は空", all(not x for x in links_of(sh)) and expr_text(rep6.rig) == text_bilinear and s.preview_status().material_link == "", f"{links_of(sh)} {s.preview_status().material_link!r} {expr_text(rep6.rig) == text_bilinear}")
    s.preview_delete()

    # Look のデータには何も書かない
    check("Look のデータ（looks/）には何も書かれない", not (tmp / "looks").exists())

    s.close()


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
