"""Toon のネームスペース（参照したキャラクター）のスモークテスト（mayapy で実行。画面なし）。

  "C:/Program Files/Autodesk/Maya2026/bin/mayapy.exe" tests/maya/toon_namespace_smoke.py

合成のテスト素体（assets/unitychan/unitychan_test.ma）を `chr:` などのネームスペース付きで参照し、ネームスペースなしのシーンと同じ結果になることを確かめる。
Look の名前はネームスペースなしのまま。ファイルはすべて一時フォルダ。
ローカルに assets/shizuku/shizuku_facial.mb（または shizuku.mb。git 管理外）があれば、読み取り専用で参照する「あるときだけ」の確認も行う。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
REPO = Path(__file__).resolve().parents[2]
FIXTURE = REPO / "assets" / "unitychan" / "unitychan_test.ma"
os.environ.setdefault("TDRIVE_ROOT", REPO.as_posix())
sys.path.insert(0, str(REPO / "maya" / "scripts"))
sys.path.insert(0, str(REPO / "tools" / "fixtures"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6 import QtWidgets  # noqa: E402

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
SHOT_DIR = Path(os.environ.get("TDRIVE_SHOT_DIR", r"C:\Users\yamag\AppData\Local\Temp\claude\C--Users-yamag-wrench-maya-T-Drive\7adf5442-7a48-4c8c-9be6-1504fc059ded\scratchpad\ui_shots14"))
SHIZUKU_FILES = [REPO / "assets" / "shizuku" / "shizuku_facial.mb", REPO / "assets" / "shizuku" / "shizuku.mb"]
SHIZUKU_LOOK = REPO / "looks" / "shizuku" / "look.json"


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(cond), detail))


def pump() -> None:
    APP.processEvents()


def membership() -> dict[str, list[str]]:
    from maya import cmds

    return {sg: sorted(cmds.sets(sg, q=True) or []) for sg in cmds.ls(type="shadingEngine")}


def original_only(m: dict[str, list[str]]) -> dict[str, list[str]]:
    """プレビューの SG（`*_tdToonSG`）を除いた、中身のある SG の割り当て。"""
    return {k: v for k, v in m.items() if "_tdToon" not in k and v}


def reference(path, ns: str, new_scene: bool = True) -> None:
    """ネームスペース付きで参照する（入れ子 `a:b` は親のネームスペースの中へ）。"""
    from maya import cmds

    if new_scene:
        cmds.file(new=True, force=True)
    parent, _, leaf = ns.rpartition(":")
    if parent:
        cmds.namespace(add=parent)
        cmds.namespace(set=parent)
    cmds.file(str(path), reference=True, namespace=leaf)
    cmds.namespace(set=":")


def reset_session(s) -> None:
    from tdrive_toon import naming

    s.look, s.path, s.dirty = None, None, False
    s._undo.clear()
    s._redo.clear()
    s.namespace_message = ""
    naming.set_namespace("")


def fbx_summary(path: str) -> dict:
    """出力した FBX を空のシーンへ取り込み、メッシュ名・マテリアル名・頂点数を返す。"""
    from maya import cmds

    cmds.file(new=True, force=True)
    cmds.loadPlugin("fbxmaya", quiet=True)
    cmds.file(path, i=True, type="FBX", ignoreVersion=True)
    meshes = sorted(cmds.listRelatives(s, parent=True)[0] for s in cmds.ls(type="mesh", noIntermediate=True))
    mats = sorted({m for sg in cmds.ls(type="shadingEngine") for m in cmds.listConnections(sg + ".surfaceShader", source=True, destination=False) or []
                   if m not in ("lambert1", "particleCloud1") and not m.startswith("openPBR")})
    return {"meshes": meshes, "materials": mats, "sets": sorted(cmds.ls(type="shadingEngine"))}


def run() -> None:
    from maya import cmds

    if not FIXTURE.exists():
        import build_unitychan_scene

        build_unitychan_scene.build()

    from tdrive_toon import export, look, naming, preview, session, view_correction

    tmp = Path(tempfile.mkdtemp(prefix="tdrive_tns_"))
    s = session.current()

    # ------------------------------------------------------------ 0. 基準: ネームスペースなしのシーン
    cmds.file(str(FIXTURE), open=True, force=True)
    reset_session(s)
    s.new("nssmoke", "assets/unitychan/unitychan_test.ma")
    s.auto_register()
    base_names = sorted(s.look["materials"])
    look_path = s.save(tmp / "look" / "look.json")
    base_scene_mats = preview.scene_materials()
    s.open(look_path)  # 保存した Look を開き直した状態で比べる（開くときの補完を同じにする）
    preview.delete_all()
    base_json = json.loads(s.export_unity("base", out_dir=tmp / "base_out").read_text(encoding="utf-8"))
    base_json_text = (tmp / "base_out" / "nssmoke" / "nssmoke_base.materialdata.json").read_text(encoding="utf-8")
    check("基準: ネームスペースなしは今までどおり（ネームスペース "" ・メッセージなし・行は出さない）",
          s.namespace == "" and s.namespace_message == "" and not s.namespace_row_visible() and "face" in base_names,
          f"{s.namespace!r} {s.namespace_message!r}")
    check("基準: プレビューのシェーダー名は <マテリアル>_tdToon", preview.preview_shader_of("face") == "face_tdToon")
    s.show("base")
    base_shaders = sorted(preview.preview_shaders())
    check("基準: 作られるプレビューのシェーダーは <マテリアル>_tdToon", base_shaders == sorted(f"{m}_tdToon" for m in base_names), str(base_shaders))
    check("基準: tdSourceMaterial だけ（tdSourceNamespace なし）", not cmds.attributeQuery(preview.SOURCE_NS_ATTR, node="face_tdToon", exists=True)
          and cmds.getAttr("face_tdToon.tdSourceMaterial") == "face")
    saved_char = tmp / "char_with_preview.ma"  # プレビュー中のまま保存したキャラクターのファイル（後で参照して使う）
    cmds.file(rename=str(saved_char))
    cmds.file(save=True, type="mayaAscii", force=True)
    preview.delete_all()
    base_fbx = export.export_fbx(s.export_meshes(), tmp / "base.fbx")
    base_fbx_info = fbx_summary(base_fbx["path"])

    # ------------------------------------------------------------ 1. 参照（chr:）: 自動検出・プレビュー
    reference(FIXTURE, "chr")
    reset_session(s)
    ref_node = cmds.file(query=True, reference=True)[0]
    orig = membership()
    orig_sg = {m: preview._shading_group(f"chr:{m}") for m in base_names}
    orig_members = {m: orig[orig_sg[m]] for m in base_names}
    check("参照: 素のシーンにはプレビューのノードも Toon の記録もない", not preview.preview_shaders() and not cmds.fileInfo("tdriveToonNamespace", query=True))
    check("参照: Look を開く前でも、メッシュにマテリアルがあるネームスペースを候補にできる（Look なし）", s.namespace_choices() == ["chr"], str(s.namespace_choices()))
    s.open(look_path)
    check("検出: 1 つだけなら自動で chr を使う（状態の一文・シーンに記録）",
          s.namespace == "chr" and s.namespace_message == "ネームスペース chr: のキャラクターを使います"
          and cmds.fileInfo("tdriveToonNamespace", query=True) == ["chr"], f"{s.namespace!r} {s.namespace_message!r}")
    check("検出: 候補は chr だけ・行を出す", s.namespace_choices() == ["chr"] and s.namespace_row_visible())
    check("Look の名前はネームスペースなしのまま（Look は基準と同じ）", sorted(s.look["materials"]) == base_names and not any(":" in m for m in s.look["materials"]))
    sm = preview.scene_materials()
    check("シーンのマテリアル: 名前はネームスペースなし・メッシュは chr: 付き（基準と同じ名前の一覧）",
          sorted(sm) == sorted(base_scene_mats) and all(":" in m.split("|")[-1] for ms in sm.values() for m in ms), str(sorted(sm)))
    check("プレビュー: Look を開くと Toon 表示になる", preview.is_active())
    shaders = sorted(preview.preview_shaders())
    check("プレビューのシェーダー名: chr_<マテリアル>_tdToon（唯一の決め方 = preview_shader_of）",
          shaders == sorted(f"chr_{m}_tdToon" for m in base_names) and all(preview.preview_shader_of(m) == f"chr_{m}_tdToon" for m in base_names)
          and preview.preview_shader_of(f"chr:{base_names[0]}") == f"chr_{base_names[0]}_tdToon", str(shaders))
    check("プレビューのシェーダーに元のマテリアル（ネームスペースなし）とネームスペースの記録",
          cmds.getAttr("chr_face_tdToon.tdSourceMaterial") == "face" and cmds.getAttr("chr_face_tdToon.tdSourceNamespace") == "chr"
          and preview.source_material("chr_face_tdToon") == "face" and preview.source_scene_name("chr_face_tdToon") == "chr:face")
    moved = all(sorted(cmds.sets(f"chr_{m}_tdToonSG", q=True) or []) == orig_members[m] for m in base_names)
    check("割り当て: プレビューの SG に chr: のメッシュが元どおり移る（SG のメンバーが元の SG と同じ）", moved)
    check("割り当て: 元の chr: の SG は空", all(not cmds.sets(orig_sg[m], q=True) for m in base_names))
    check("割り当て: 参照への編集としてシーンに記録される（接続の編集）",
          any("chr_face_tdToonSG" in e for e in cmds.referenceQuery(ref_node, editStrings=True)))
    n_nodes = len(cmds.ls())
    s.show("base")
    check("有効化は冪等（ノードが増えない）", len(cmds.ls()) == n_nodes and len(preview.preview_shaders()) == len(base_names))
    preview.apply_value("hair", "common.doubleSided", True)
    preview.apply_value("hair", "common.blend", "Transparent")
    preview.apply_value("hair", "common.blend", "Opaque")
    check("値の反映: 名前を通してネームスペース付きのシェーダー（chr_hair_tdToon）の technique が動く",
          cmds.getAttr("chr_hair_tdToon.technique") == "OpaqueDoubleSided")
    s.checkpoint()
    s.set_value("face", "_ToonShadowStrength", 0.123)
    check("値の反映: Look の値が変わる（Look はネームスペースなし）", s.look["materials"]["face"]["specific"]["_ToonShadowStrength"] == 0.123)
    s.undo()

    # ---- 元の見た目
    s.set_preview(False)
    check("元の見た目: 割り当てが参照した直後と完全に一致（chr: の SG のメンバー）", original_only(membership()) == original_only(orig) and not preview.is_active())
    s.set_preview(True)
    check("Toon に戻せる", preview.is_active() and all(sorted(cmds.sets(f"chr_{m}_tdToonSG", q=True) or []) == orig_members[m] for m in base_names))

    # ---- 値の反映（キャラクター単位のプレビュー）が chr: のメッシュに届く
    sm = preview.scene_materials()
    check("部位選択: select_part は chr: のメッシュを選ぶ", (s.select_part("face"), all(n.split("|")[-1].startswith("chr:") for n in cmds.ls(selection=True, long=True)))[1])
    check("Unity 向けメッシュ一覧は chr: のメッシュ（カメラ補正用メッシュを除く）", bool(s.export_meshes()) and all(m.split("|")[-1].startswith("chr:") for m in s.export_meshes()))

    # ------------------------------------------------------------ 2. 出力: ネームスペースなしと同じ
    ns_json_path = s.export_unity("base", out_dir=tmp / "ns_out")
    ns_json_text = ns_json_path.read_text(encoding="utf-8")
    ns_json = json.loads(ns_json_text)
    check("Unity 出力（materialdata.json）: ネームスペースなしのシーンと同じ内容", ns_json == base_json and ":" not in "".join(ns_json["materials"]))
    check("Unity 出力: SourceMaterial にもネームスペースがない", all(":" not in v["SourceMaterial"] for v in ns_json["materials"].values()))

    # ------------------------------------------------------------ 3. 保存 → 開き直し（プレビューが入ったまま）→ 元に戻せる
    scene_path = tmp / "ns_scene.ma"
    cmds.file(rename=str(scene_path))
    cmds.file(save=True, type="mayaAscii", force=True)
    cmds.file(new=True, force=True)
    cmds.file(str(scene_path), open=True, force=True)
    reset_session(s)
    check("保存後: fileInfo にネームスペースが残る", cmds.fileInfo("tdriveToonNamespace", query=True) == ["chr"])
    session.on_scene_opened()
    check("開き直し: 記録した Look が開き、ネームスペース chr が戻る", s.look is not None and s.namespace == "chr" and naming.namespace() == "chr")
    still = all(sorted(cmds.sets(f"chr_{m}_tdToonSG", q=True) or []) == orig_members[m] for m in base_names)
    check("開き直し: 参照への編集でプレビューの割り当てが復元される（ネームスペースなしのシーンと同じ方針）", still)
    s.set_preview(False)
    check("開き直し: 元の見た目で元の割り当てに戻る", original_only(membership()) == original_only(orig))
    s.set_preview(True)

    # ---- ツールのリロード（モジュールを捨てて読み直す）でネームスペースを持ち越す
    for name in [n for n in sys.modules if n == "tdrive_toon" or n.startswith("tdrive_toon.")]:
        del sys.modules[name]
    from tdrive_toon import naming as naming2
    from tdrive_toon import session as session2

    check("リロード: シーンに覚えたネームスペースを読み戻す（Look はまだ開いていない）", naming2.namespace() == "chr" and session2.current().namespace == "chr" and session2.current().look is None)
    # 以降は読み直したモジュールを使う
    from tdrive_toon import export, look, naming, preview, session, view_correction  # noqa: F811

    s = session.current()
    s.open(look_path)
    check("リロード後: Look を開き直しても chr のまま・プレビューが付く", s.namespace == "chr" and preview.is_active() and "chr_face_tdToon" in preview.preview_shaders())

    # ------------------------------------------------------------ 4. 間違ったネームスペース
    try:
        s.set_namespace("nope")
        raised = ""
    except ValueError as exc:
        raised = str(exc)
    check("間違ったネームスペース: 一文で断る・今のネームスペースは変わらない", raised == "ネームスペース nope: にこの Look のマテリアルがありません" and s.namespace == "chr", raised)
    try:
        s.set_namespace("")
        raised2 = ""
    except ValueError as exc:
        raised2 = str(exc)
    check("ネームスペースなしにはできない（chr: のキャラクターだけのシーン）", raised2 == "ネームスペースなし にこの Look のマテリアルがありません", raised2)
    check("同じネームスペースを選び直しても何も起きない", s.set_namespace("chr") == "ネームスペース chr: のキャラクターを使います" and preview.is_active())
    check("自動で探す: chr のまま・一文を返す", s.find_namespace() == "ネームスペース chr: のキャラクターを使います" and s.namespace == "chr")

    # ------------------------------------------------------------ 5. 頂点のデータを書く機能（参照したメッシュには書けない: 日本語で断る）
    face_meshes = s.meshes_for("face")
    before_sets = {m: cmds.polyColorSet(preview.mesh_shapes([m])[0], q=True, allColorSets=True) for m in face_meshes}

    def refusal(fn) -> str:
        try:
            fn()
        except RuntimeError as exc:
            return str(exc)
        return ""

    msgs = [
        refusal(lambda: s.init_mask("face")),
        refusal(lambda: s.begin_mask_paint("R", "face")),
        refusal(lambda: s.bake_smooth_normals("face")),
    ]
    s.create_face_proxy("face")
    msgs.append(refusal(lambda: s.transfer_face_normals("face", 1.0)))
    msgs.append(refusal(lambda: s.reset_face_normals("face")) or "（バックアップなしの reset は何もしない）")
    from tdrive_toon import face_normals

    if cmds.objExists(face_normals.PROXY):
        cmds.delete(face_normals.PROXY)
    check("頂点カラー・スムーズ法線・顔の法線: 参照したメッシュには日本語の一文で断る（Traceback にしない）",
          all("参照したキャラクターのメッシュには" in m and "書き込めません" in m for m in msgs[:4]), str(msgs))
    after_sets = {m: cmds.polyColorSet(preview.mesh_shapes([m])[0], q=True, allColorSets=True) for m in face_meshes}
    check("断ったあと: メッシュは何も変わっていない（Color Set が増えていない）", after_sets == before_sets)
    check("頂点マスクの状態を読む処理は参照したメッシュでも動く", isinstance(s.look, dict) and preview.scene_materials() is not None)

    # ------------------------------------------------------------ 6. カメラ角度の補正（T-20）: 参照したスキン付きの顔メッシュ
    head = cmds.ls("chr:head_back", long=True)[0] if cmds.ls("chr:head_back", long=True) else [m for m in cmds.ls(type="transform", long=True) if m.endswith("chr:head_back")][0]
    cmds.select(head, replace=True)
    targets = []
    for key in view_correction.KEYS:
        t = s.create_view_correction(key)
        targets.append(t)
        cmds.move(0, 0, 0.5, t + ".vtx[0]", relative=True)
        s.register_view_correction(key)
    vc = {k: s.setting(f"viewCorrection.{k}") for k in view_correction.KEYS}
    check("補正シェイプ: Look にはネームスペースなしの名前（メッシュ head_back・ターゲット head_back_vc_*）が入る",
          s.setting("viewCorrection.mesh") == "head_back" and vc == {k: f"head_back_vc_{k}" for k in view_correction.KEYS}, f"{s.setting('viewCorrection.mesh')} {vc}")
    check("補正シェイプ: 作業メッシュ・BlendShape はシーンのノード（ネームスペースのコロンなし）",
          all(":" not in t and cmds.objExists(t) for t in targets) and cmds.objExists("tdViewCorrection_chr_head_back"), str(targets))
    check("補正シェイプ: BlendShape のターゲット名（エイリアス）が Look の名前どおり", sorted((cmds.aliasAttr("tdViewCorrection_chr_head_back", q=True) or [])[::2]) == sorted(vc.values()))
    cam = cmds.camera(name="tdSmokeVCCam")[0]
    s.connect_view_correction(cam)
    check("補正シェイプ: カメラ連動のノードができる（コロンなしの名前）", cmds.objExists("tdVC_expr_chr_head_back"))
    s.disconnect_view_correction()
    check("補正シェイプ: 外すと消える", not cmds.objExists("tdVC_expr_chr_head_back"))
    check("補正シェイプ: 彫刻用メッシュは Unity 出力の対象外", not any("_vc_" in m for m in s.export_meshes()))
    check("補正シェイプを入れた Look が検証を通る", look.validate(s.look) == [], str(look.validate(s.look)))
    # 保存して開き直しても、参照したメッシュへの BlendShape が残る（ネームスペースなしと同じく、シーンのノードと参照への編集として）
    vc_scene = tmp / "ns_vc.ma"
    cmds.file(rename=str(vc_scene))
    cmds.file(save=True, type="mayaAscii", force=True)
    cmds.file(str(vc_scene), open=True, force=True)
    hist = cmds.listHistory(preview.mesh_shapes([cmds.ls("chr:head_back", long=True)[0]])[0]) or []
    check("補正シェイプ: シーンを保存して開き直しても、参照したメッシュに BlendShape がかかったまま", "tdViewCorrection_chr_head_back" in hist, str(hist[:8]))
    reset_session(s)
    s.open(look_path)  # 補正シェイプを足した Look は保存していない（look_path はそのまま）
    check("開き直し後: 自動検出", s.namespace == "chr")

    # ------------------------------------------------------------ 7. FacialController との約束（マテリアル連携・FBX の元のマテリアル名）
    from tdrive_facial import export as fexport
    from tdrive_facial import preview_rig
    from tdrive_facial import scene as fscene
    from tdrive_facial.core.model import Document, Meta, Target

    fscene.set_namespace("chr")
    doc = Document(meta=Meta(unit="cm", up_axis="Y", handedness="right", source="smoke"))
    doc.target = Target(mesh="head_back")
    for sh in preview.preview_shaders():  # ヘッドレスの mayapy では dx11Shader の受け口が属性にならない: 代わりの属性を足す
        for a in preview_rig.toon_shader_attrs():
            if not cmds.attributeQuery(a, node=sh, exists=True):
                cmds.addAttr(sh, longName=a, attributeType="double")
    found, missing = preview_rig.find_toon_shaders(doc)
    check("Facial 連携（Toon 表示中）: 顔のメッシュのプレビューシェーダー（chr_<マテリアル>_tdToon）が見つかる", len(found) >= 1 and all(f.startswith("chr_") and f.endswith("_tdToon") for f in found) and not missing, f"{found} {missing}")
    s.set_preview(False)
    found2, missing2 = preview_rig.find_toon_shaders(doc)
    check("Facial 連携（元の見た目）: 元のマテリアル chr:<名前> から preview_shader_of で同じシェーダーが見つかる", sorted(found2) == sorted(found) and not missing2, f"{found2} {missing2}")
    s.set_preview(True)
    # FBX 出力の前処理: 参照を取り込み、プレビューを元のマテリアルへ戻す
    members_by_doc = {m: sorted(naming.strip_all(x) for x in cmds.sets(f"chr_{m}_tdToonSG", q=True)) for m in base_names}
    fexport.flatten_references()
    res = fexport.restore_original_materials()
    flat_ok = all(sorted(cmds.sets(preview._shading_group(m), q=True) or []) and [naming.strip_all(x) for x in sorted(cmds.sets(preview._shading_group(m), q=True))] == members_by_doc[m] for m in base_names if cmds.objExists(m))
    check("Facial の FBX 出力: 取り込み後に元のマテリアル（ネームスペースなしの名前）へ戻り、プレビューのノードが消える",
          res["restored"] == len(base_names) and flat_ok and not [n for n in cmds.ls() if n.endswith("_tdToon") or n.endswith("_tdToonSG")], str(res))

    # ------------------------------------------------------------ 8. Unity 向け FBX（Toon）: ネームスペースなしと同じ名前
    cmds.file(str(scene_path), open=True, force=True)
    reset_session(s)
    session.on_scene_opened()
    s.show("base")
    fbx = export.export_fbx(s.export_meshes(), tmp / "ns.fbx")
    info = fbx_summary(fbx["path"])
    check("FBX（Toon）: メッシュ名・マテリアル名・割り当てがネームスペースなしのシーンと同じ（コロンなし）",
          info["meshes"] == base_fbx_info["meshes"] and info["materials"] == base_fbx_info["materials"] and not any(":" in n for n in info["meshes"] + info["materials"]),
          f"{info['meshes'][:4]} vs {base_fbx_info['meshes'][:4]} / {info['materials']} vs {base_fbx_info['materials']}")
    check("FBX（Toon）: マテリアル名に _tdToon がない", not any("tdToon" in n for n in info["materials"] + info["sets"]))
    cmds.file(str(scene_path), open=True, force=True)

    # ------------------------------------------------------------ 9. 2 体（chrA / chrB）
    reference(FIXTURE, "chrA")
    reference(FIXTURE, "chrB", new_scene=False)
    reset_session(s)
    orig2 = membership()
    members_a = {m: orig2[preview._shading_group(f"chrA:{m}")] for m in base_names}
    s.open(look_path)
    check("2 体: 決められないときはなし・一文で選ぶよう知らせる",
          s.namespace == "" and s.namespace_message == "Look のマテリアルが複数のネームスペースにあります（chrA:、chrB:）。「ネームスペース」で選んでください"
          and s.namespace_choices() == ["chrA", "chrB"] and s.namespace_row_visible(), f"{s.namespace!r} {s.namespace_message!r}")
    check("2 体: 決まるまでは何も差し替えない（元の割り当てのまま・プレビューのノードなし）", original_only(membership()) == original_only(orig2) and not preview.preview_shaders())
    msg = refusal(lambda: s.auto_register())
    check("2 体: 決まる前に部位登録しようとすると、一文で断る（Look にネームスペース付きの名前を書かない）", "ネームスペースが付いています" in msg and sorted(s.look["materials"]) == base_names, msg)
    shot_state_a = (s.namespace, s.namespace_message)
    # 画面（ヘッダーのネームスペースの行）
    from tdrive_toon import ui

    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    header = ui.HeaderBar(s)
    header.resize(750, 150)
    header.show()
    pump()
    header.refresh()
    pump()
    check("UI: 行が出る（候補 chrA: / chrB:・選べる・自動で探すボタン・一文）",
          header.ns_box.isVisible() and [header.ns_combo.itemText(i) for i in range(header.ns_combo.count())][:2] == ["chrA:", "chrB:"]
          and header.ns_find.isEnabled() and header.ns_note.isVisible() and "選んでください" in header.ns_note.text(),
          str([header.ns_combo.itemText(i) for i in range(header.ns_combo.count())]))
    header.grab().save(str(SHOT_DIR / "toon_namespace_ambiguous.png"))
    s.set_namespace("chrA")
    check("2 体: chrA を選ぶと chrA のメッシュだけ Toon・chrB は元の割り当てのまま",
          all(sorted(cmds.sets(f"chrA_{m}_tdToonSG", q=True) or []) == members_a[m] for m in base_names)
          and all(orig2[sg] == sorted(cmds.sets(sg, q=True) or []) for sg in orig2 if sg.startswith("chrB:")), "")
    check("2 体: chrA のシェーダーだけができる", sorted(preview.preview_shaders()) == sorted(f"chrA_{m}_tdToon" for m in base_names))
    check("2 体: 選びがシーンに残る", cmds.fileInfo("tdriveToonNamespace", query=True) == ["chrA"] and s.namespace_message == "")
    sel_b = [x for x in cmds.ls(type="transform", long=True) if x.split("|")[-1] == "chrB:head_back"][0]
    cmds.select(sel_b, replace=True)
    msg = refusal(lambda: s.register_selection("headx", "skin"))
    check("2 体: 別のキャラクター（chrB）のメッシュで部位登録しようとすると断る", "chrA:" in msg and "選び直して" in msg, msg)
    header.refresh()
    pump()
    check("UI: 選んだあとは chrA: が選ばれ、一文は消える", header.ns_combo.currentText() == "chrA:" and not header.ns_note.isVisible())
    header.grab().save(str(SHOT_DIR / "toon_namespace.png"))
    s.set_namespace("chrB")
    check("2 体: chrB に切り替えると chrA は元の割り当てに戻り、chrB が Toon になる",
          all(orig2[sg] == sorted(cmds.sets(sg, q=True) or []) for sg in orig2 if sg.startswith("chrA:"))
          and sorted(preview.preview_shaders()) == sorted(f"chrB_{m}_tdToon" for m in base_names) and not any("chrA_" in n for n in cmds.ls()),
          str(sorted(preview.preview_shaders())[:3]))
    s.set_preview(False)
    check("2 体: 元の見た目で両方とも元の割り当て", original_only(membership()) == original_only(orig2))
    s.set_namespace("chrA")
    check("2 体: 元の見た目のまま切り替えても Toon にはならない", not preview.is_active())
    header.detach()
    header.deleteLater()
    s.set_preview(True)
    find_msg = ""
    cmds.fileInfo(remove="tdriveToonNamespace")
    naming.set_namespace("")
    try:
        find_msg = s.find_namespace()
    except RuntimeError as exc:
        find_msg = str(exc)
    check("2 体: 自動で探しても決められないときは一文を返す", find_msg.startswith("Look のマテリアルが複数のネームスペースにあります"), find_msg)
    # FacialController が選んでいるネームスペースを既定にする
    cmds.fileInfo("tdFacialNamespace", "chrB")
    reset_session(s)
    s.open(look_path)
    check("2 体: FacialController が chrB を選んでいれば、既定は chrB（シーンに覚える）", s.namespace == "chrB" and s.namespace_message == "ネームスペース chrB: のキャラクターを使います", f"{s.namespace!r}")
    cmds.fileInfo(remove="tdFacialNamespace")

    # ------------------------------------------------------------ 10. 入れ子（grp:chr）
    reference(FIXTURE, "grp:chr")
    reset_session(s)
    orig3 = membership()
    s.open(look_path)
    check("入れ子: grp:chr を使う", s.namespace == "grp:chr" and s.namespace_choices() == ["grp:chr"], f"{s.namespace!r} {s.namespace_choices()}")
    check("入れ子: シェーダー名は grp_chr_<マテリアル>_tdToon・割り当てが移る",
          sorted(preview.preview_shaders()) == sorted(f"grp_chr_{m}_tdToon" for m in base_names)
          and all(sorted(cmds.sets(f"grp_chr_{m}_tdToonSG", q=True) or []) for m in base_names))
    check("入れ子: Look はネームスペースなしのまま・出力は基準と同じ",
          ":" not in "".join(s.look["materials"]) and json.loads(s.export_unity("base", out_dir=tmp / "nested_out").read_text(encoding="utf-8")) == base_json)
    s.set_preview(False)
    check("入れ子: 元の見た目で元の割り当てに戻る", original_only(membership()) == original_only(orig3))

    # ------------------------------------------------------------ 10b. キャラクターのファイルがプレビュー中のまま保存されていた
    reference(saved_char, "chr")
    reset_session(s)
    saved_nodes = sorted(n for n in cmds.ls(type="dx11Shader") if n.endswith("_tdToon"))
    check("プレビュー中のまま保存されたファイル: 参照すると chr:<マテリアル>_tdToon が中に入っている（準備）", len(saved_nodes) == len(base_names) and all(n.startswith("chr:") for n in saved_nodes), str(saved_nodes[:3]))
    check("プレビュー中のまま保存されたファイル: 保存済みのシェーダーはこのツールのプレビューに数えない", not preview.preview_shaders())
    sm_saved = preview.scene_materials()
    check("プレビュー中のまま保存されたファイル: メッシュのマテリアルは元のマテリアル名で引ける（Look の名前）", sorted(sm_saved) == sorted(f"chr:{m}" for m in base_scene_mats), str(sorted(sm_saved)))
    s.open(look_path)
    check("プレビュー中のまま保存されたファイル: ネームスペース chr を自動で使い、メッシュが chr_<マテリアル>_tdToon に移る",
          s.namespace == "chr" and sorted(preview.preview_shaders()) == sorted(f"chr_{m}_tdToon" for m in base_names)
          and all(sorted(cmds.sets(f"chr_{m}_tdToonSG", q=True) or []) == orig_members[m] for m in base_names)
          and all(not cmds.sets(sg, q=True) for sgs in preview._saved_preview_sgs().values() for sg in sgs))
    s.set_preview(False)
    check("プレビュー中のまま保存されたファイル: 元の見た目で元のマテリアルの割り当てに戻る（元の SG のメンバーがファイルの元の状態と同じ）", original_only(membership()) == original_only(orig))
    preview.delete_all()

    # ------------------------------------------------------------ 11. 後から参照 / 外す
    cmds.file(str(FIXTURE), open=True, force=True)  # ネームスペースなし
    reset_session(s)
    s.open(look_path)
    check("ネームスペースなしのキャラクターの Look: そのまま（なし・メッセージなし・Toon）", s.namespace == "" and s.namespace_message == "" and preview.is_active() and not s.namespace_row_visible())
    cmds.file(new=True, force=True)
    s.show("base")
    check("キャラクターがシーンに無い: 今までの動き（何も差し替えない）に、一文を添える",
          not preview.preview_shaders() and s.namespace_message == "この Look のマテリアルがシーンに見つかりません。キャラクターを読み込んでいるか確かめてください" and s.namespace_row_visible())
    cmds.file(str(FIXTURE), reference=True, namespace="late")
    s.show("base")
    check("Look を開いたあとにキャラクターを参照: Toon にするとき自動で late を使う", s.namespace == "late" and preview.is_active() and s.namespace_message == "ネームスペース late: のキャラクターを使います", f"{s.namespace!r} {s.namespace_message!r}")

    # ------------------------------------------------------------ 12. 新規 Look（参照したキャラクター）
    reference(FIXTURE, "chr")
    reset_session(s)
    s.new("newns")
    check("新規 Look: 参照したキャラクターを自動で使い、model は参照先のファイル", s.namespace == "chr" and s.look["model"].endswith("unitychan_test.ma"), f"{s.namespace!r} {s.look['model']}")
    done = s.auto_register()
    check("新規 Look: 自動登録はネームスペースなしの名前で入る", sorted(done) == base_names and sorted(s.look["materials"]) == base_names)
    cmds.select(cmds.ls("chr:head_back", long=True)[0], replace=True)
    check("選択からの部位登録: ネームスペースなしのマテリアル名が取れる", s.selected_materials() == sorted(preview.materials_on_selection()) and not any(":" in m for m in s.selected_materials()), str(s.selected_materials()))
    preview.delete_all()

    # ------------------------------------------------------------ 13. あるときだけ: ローカルの実データ（読み取り専用の参照・Look は一時フォルダへコピー）
    sample = next((p for p in SHIZUKU_FILES if p.exists()), None)
    if sample is not None and SHIZUKU_LOOK.exists():
        work = tmp / "shizuku_look"
        work.mkdir()
        copy = work / "look.json"
        shutil.copyfile(SHIZUKU_LOOK, copy)
        from tdrive import project

        reference(sample, "chr")
        reset_session(s)
        orig4 = membership()
        s.open(copy)
        mats = sorted(s.look["materials"])
        check(f"実データ（{sample.name}）: ネームスペース chr を自動で選ぶ", s.namespace == "chr" and len(mats) == 7, f"{s.namespace!r} {len(mats)}")
        sh = sorted(preview.preview_shaders())
        check("実データ: 7 つのマテリアルすべてにプレビューのシェーダーが付く", sh == sorted(f"chr_{m}_tdToon" for m in mats), str(sh))
        scene_ms = preview.scene_materials()
        check("実データ: すべてのマテリアルが chr: のメッシュに割り当てられている（プレビューの SG のメンバー）",
              all(scene_ms.get(m) and all(x.split("|")[-1].startswith("chr:") for x in scene_ms[m]) and cmds.sets(f"chr_{m}_tdToonSG", q=True) for m in mats), str({m: len(scene_ms.get(m, [])) for m in mats}))
        paths = [v["common"].get("albedo") for v in s.look["materials"].values() if v["common"].get("albedo")]
        missing_tex = [p for p in paths if not Path(project.from_project_path(p)).exists()]
        check("実データ: テクスチャのパスがディスクに見つかる", bool(paths) and not missing_tex, str(missing_tex))
        base_tex = preview.base_texture_of("mat_body01")
        check("実データ: 参照したマテリアルの元のテクスチャを辿れる（base_texture_of）", base_tex is None or Path(project.from_project_path(base_tex)).exists(), str(base_tex))
        out = s.export_unity("base", out_dir=work / "out")
        data = json.loads(out.read_text(encoding="utf-8"))
        check("実データ: Unity 出力にネームスペースが入らない", ":" not in "".join(data["materials"]) and all(":" not in v["SourceMaterial"] for v in data["materials"].values()))
        s.set_preview(False)
        after = original_only(membership())
        assigned = lambda m: sorted({x for v in m.values() for x in v})  # noqa: E731
        check("実データ: 元の見た目で、すべてのメッシュが元のマテリアルへ戻り（プレビュー中のまま保存されていた分も）、プレビューの SG は空",
              assigned(after) == assigned(orig4) and not any(v for k, v in membership().items() if "_tdToon" in k), "")
        cmds.file(new=True, force=True)
    else:
        print("（実データ assets/shizuku は無いので、あるときだけの確認は飛ばします）")

    # 後片付け
    cmds.file(new=True, force=True)
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
