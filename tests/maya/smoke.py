"""Maya 依存層のスモークテスト（mayapy で実行。画面なし）。

  "C:/Program Files/Autodesk/Maya2026/bin/mayapy.exe" tests/maya/smoke.py

画面なしでは dx11Shader がコンパイルされない（アトリビュートが無い）ため、見た目ではなく
「元マテリアル ⇔ プレビューの割り当て往復」「保存後の復帰」「Look の保存・出力」のロジックを確認する。
見た目は画面ありの Maya + キャプチャで確認する（docs/09 §5）。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
FIXTURE = REPO / "assets" / "unitychan" / "unitychan_test.ma"
os.environ.setdefault("TDRIVE_ROOT", REPO.as_posix())
sys.path.insert(0, str(REPO / "maya" / "scripts"))
sys.path.insert(0, str(REPO / "tools" / "fixtures"))

import maya.standalone  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(cond), detail))


def membership() -> dict[str, list[str]]:
    from maya import cmds

    return {sg: sorted(cmds.sets(sg, q=True) or []) for sg in cmds.ls(type="shadingEngine")}


def run() -> None:
    from maya import cmds

    if not FIXTURE.exists():
        import build_unitychan_scene

        build_unitychan_scene.build()
    cmds.file(str(FIXTURE), open=True, force=True)

    from maya.api import OpenMaya as om

    from tdrive_toon import environment, look, preview, roles, session

    tmp = Path(tempfile.mkdtemp(prefix="tdrive_smoke_"))
    s = session.current()
    s.new("smoke", "assets/unitychan/unitychan_test.ma")

    original = membership()
    registered = s.auto_register()
    scene_mats = preview.scene_materials()
    check("自動登録: シーンの全マテリアルが登録される", set(registered) == set(scene_mats), f"{sorted(registered)}")
    check("自動登録: Look が検証を通る", look.validate(s.look) == [], str(look.validate(s.look)))
    check("Blend は元マテリアルから継承", s.look["materials"]["face"]["common"]["blend"] == "Opaque"
          and s.look["materials"]["eyeline"]["common"]["blend"] == "Transparent")

    # ---- 有効化
    s.show("base")
    after = membership()
    moved = all(not after[f"{m}SG"] for m in scene_mats if f"{m}SG" in after)
    check("有効化: 元 SG が空になり全メッシュがプレビュー SG へ移る", moved and preview.is_active())
    n_nodes = len(preview.preview_shaders())
    s.show("base")
    check("有効化は冪等（ノードが増えない）", len(preview.preview_shaders()) == n_nodes == len(scene_mats))

    # ---- 解除 → 元どおり
    preview.disable()
    check("解除: 割り当てが有効化前と完全に一致", membership_without_preview(membership()) == membership_without_preview(original))
    check("解除: is_active が False", not preview.is_active())

    # ---- 保存 → 開き直し → 解除で元に戻る
    s.show("base")
    look_path = s.save(tmp / "look.json")
    scene_path = tmp / "saved_with_preview.ma"
    cmds.file(rename=str(scene_path))
    cmds.file(save=True, type="mayaAscii", force=True)
    cmds.file(new=True, force=True)
    cmds.file(str(scene_path), open=True, force=True)
    check("保存後: fileInfo から Look のパスが復元できる", Path(preview.remembered_look_path()).resolve() == look_path.resolve())
    s.look, s.path, s.dirty = None, None, False
    session.on_scene_opened()  # userSetup.py が SceneOpened で呼ぶもの
    check("シーンを開くと記録された Look が自動で開く", s.look is not None and Path(s.path).resolve() == look_path.resolve())
    preview.disable()
    check("保存後: 開き直しても元の割り当てに戻せる",
          membership_without_preview(membership()) == membership_without_preview(original))

    # ---- Look の読み書き・バリアント・出力
    s.open(look_path)
    s.add_variant("B", "顔の影を弱く", copy_from="base")
    s.set_edit_variant("B")
    s.set_value("face", "_ToonShadowStrength", 0.1)
    check("バリアント: 差分が 1 件", look.diff(s.look, "base", "B") == [("face", "_ToonShadowStrength", 0.35, 0.1)],
          str(look.diff(s.look, "base", "B")))
    out = s.export_unity("B", out_dir=tmp)
    data = json.loads(out.read_text(encoding="utf-8"))
    face = {x["Property"]: x["Value"] for x in data["materials"]["face"]["Specific"]}
    check("Unity 出力: B の値が入る", face["_ToonShadowStrength"]["FloatValue"] == 0.1)
    check("Unity 出力: features（オンの機能）が入る",
          "shade" in data["features"] and "outline" in data["features"] and "rim" not in data["features"],
          str(data["features"]))

    # ---- 機能のオン/オフ（docs/11）。mayapy では .fx の属性が読めないので解決値で確かめる
    s.show("base")
    width_on = s.look["materials"]["face"]["specific"]["_ToonOutlineWidth"]
    s.set_feature("outline", False)
    s.set_feature("vertexMask", False)
    r = look.resolve(s.look)["face"]
    check("機能オフ: 線幅 0 で解決され、保存値は残る",
          r["specific"]["_ToonOutlineWidth"] == 0.0 and width_on > 0
          and s.look["materials"]["face"]["specific"]["_ToonOutlineWidth"] == width_on)
    check("機能オフ: 解決結果の features から外れる（頂点マスク無効の根拠）", "vertexMask" not in r["features"])
    s.undo()
    s.undo()
    check("機能オン/オフは Undo で戻る", s.feature_enabled("outline") and s.feature_enabled("vertexMask"))
    turned_off = s.disable_unused_features()
    check("使っていない機能をオフ: 使用中の機能は残る",
          s.feature_enabled("outline") and "outline" not in turned_off and "shade" not in turned_off, str(turned_off))
    s.undo()

    # ---- 部位タブの操作（UI から呼ぶセッション API）
    s.rename_part("hair", "kami")
    check("部位名変更", "kami" in s.look["parts"] and "hair" not in s.look["parts"])
    before_face = dict(s.look["materials"]["face"]["specific"])
    s.set_role("face", "skin", apply_preset=False)
    check("ロール変更（プリセット再適用なし）で値が変わらない",
          s.look["parts"]["face"]["role"] == "skin" and s.look["materials"]["face"]["specific"] == before_face)
    s.set_role("face", "face", apply_preset=True)
    check("ロール変更（プリセット再適用あり）", s.look["materials"]["face"]["specific"]["_ToonShadowStrength"] == 0.35)
    s.move_material("mat_cheek", "face")
    check("マテリアルを別部位へ移動（元の部位は空なら消える）",
          "mat_cheek" in s.look["parts"]["face"]["materials"] and "blush" not in s.look["parts"])
    s.set_preview(False)
    off = not preview.is_active()
    s.set_preview(True)
    check("表示切替 Toon ⇔ 元の見た目", off and preview.is_active())
    check("操作後も Look が検証を通る", look.validate(s.look) == [], str(look.validate(s.look)))

    # ---- ルックタブの操作（値・リセット・上書き解除・Undo）
    s.set_edit_variant("base")
    s.checkpoint()
    s.set_value("face", "_ToonShadowStrength", 0.9)
    s.reset_value("face", "_ToonShadowStrength")
    check("リセット: ロールのプリセット値に戻る", s.value("face", "_ToonShadowStrength") == 0.35)
    s.checkpoint()
    s.set_value("hair", "_ToonOutlineWidth", 3.0)
    s.undo()
    check("Undo: 直前の値に戻る", s.value("hair", "_ToonOutlineWidth") == 1.3)
    s.redo()
    check("Redo: やり直せる", s.value("hair", "_ToonOutlineWidth") == 3.0)
    s.set_edit_variant("B")
    s.set_value("hair", "_ToonOutlineWidth", 2.0)
    check("バリアントで上書き中を判定できる", s.is_overridden("hair", "_ToonOutlineWidth"))
    s.clear_override("hair", "_ToonOutlineWidth")
    check("上書き解除で base の値に戻る",
          not s.is_overridden("hair", "_ToonOutlineWidth") and s.value("hair", "_ToonOutlineWidth") == 3.0)
    s.set_edit_variant("base")
    check("部位単位の値一覧（混在判定用）", len(s.values("eye", "_ToonShadowStrength")) == 3)

    # ---- A/B
    s.set_ab(0, "base")
    s.set_ab(1, "B")
    s.show_ab(1)
    first = s.shown
    s.toggle_ab()
    check("A/B: B 表示 → 切替で A 表示", first == "B" and s.shown == "base")
    s.checkpoint()
    s.promote("B")
    check("採用: B が base に確定し B は消える", "B" not in s.look["variants"] and s.ab == ["base", "base"])
    s.undo()
    check("採用の取り消し（Undo）", "B" in s.look["variants"])

    # ---- プレビュー条件（環境・ライト）の保存と復元
    preview.use_profile(environment.list_profiles()[0])
    preview.set_light_euler(10.0, 123.0)
    saved = s.save_preview_settings()
    preview.set_light_euler(60.0, 0.0)
    s.open(look_path)
    check("preview.json からライトの向きを復元", saved.exists() and preview.environment_state()["lightEuler"] == (10.0, 123.0),
          str(preview.environment_state()["lightEuler"]))

    # ---- Toon マスク（頂点カラー）
    from tdrive_toon import mask

    face_meshes = s.meshes_for("face")
    created = s.init_mask("face")
    shape = preview.mesh_shapes(face_meshes)[0]
    check("マスク初期化: tdToonMask が白で作られる",
          created and all(v == 1.0 for ch in mask.CHANNELS for v in mask.read_channel(shape, ch)))
    check("マスク初期化は冪等", s.init_mask("face") == [])
    n = len(mask.read_channel(shape, "R"))
    mask.write_channel(shape, "G", [0.0] * n)
    check("チャンネル書き込み: G だけ変わり R/B/A は白のまま",
          all(v == 0.0 for v in mask.read_channel(shape, "G"))
          and all(v == 1.0 for ch in "RBA" for v in mask.read_channel(shape, ch)))

    # ---- スムーズ法線
    from tdrive_toon import smooth_normals

    baked = s.bake_smooth_normals("hair")
    check("スムーズ法線: スキン付きメッシュは Orig シェイプに焼かれ、出力シェイプに UV Set が出る",
          all(k.endswith("Orig") for k in baked) and all(smooth_normals.has_smooth_normals(m) for m in s.meshes_for("hair")),
          str(list(baked)))

    # ---- Phase 2: 顔の法線（Toon Normal）
    from tdrive_toon import face_normals

    face_meshes = s.meshes_for("face")
    probe = [x for x in preview.mesh_shapes(face_meshes)][0]

    def fv_normals(shape):
        it = om.MItMeshFaceVertex(om.MSelectionList().add(shape).getDagPath(0))
        out = []
        while not it.isDone():
            out.append((it.vertexId(), it.getNormal(om.MSpace.kWorld)))
            it.next()
        return out

    before_n = fv_normals(probe)
    s.create_face_proxy("face")
    s.transfer_face_normals("face", 1.0)
    pm = om.MMatrix(cmds.xform(face_normals.PROXY, query=True, worldSpace=True, matrix=True))
    pts = om.MFnMesh(om.MSelectionList().add(probe).getDagPath(0)).getPoints(om.MSpace.kWorld)
    err = max(1 - n * face_normals.ellipsoid_normal(pts[vid], pm) for vid, n in fv_normals(probe))
    check("顔の法線: 転写（強さ 1）で楕円体の法線になる", err < 1e-6, str(err))
    s.reset_face_normals("face")
    err_reset = max(1 - a[1] * b[1] for a, b in zip(before_n, fv_normals(probe)))
    check("顔の法線: リセットで転写前に戻る", err_reset < 1e-6, str(err_reset))
    cmds.delete(face_normals.PROXY)
    check("目のロール: ライト色の影響 0・手前に出す", roles.ROLE_PRESETS["eye"]["specific"]["_ToonLightColorInfluence"] == 0.0
          and roles.ROLE_PRESETS["eye"]["specific"]["_ToonDepthOffset"] > 0)

    # ---- コードレビュー（2026-09-28）の修正の回帰テスト
    # #1 部位に別のマテリアルを追加しても、既存メンバーの調整値は残る
    s.set_value("hair", "_ToonOutlineWidth", 2.0)
    s.move_material("mat_cheek", "hair")
    check("#1 部位への追加で既存メンバーの調整値が残る", s.value("hair", "_ToonOutlineWidth") == 2.0,
          str(s.value("hair", "_ToonOutlineWidth")))
    s.undo()

    # #2 両面 → Transparent → Opaque で両面が保たれる
    preview.apply_value("hair", "common.doubleSided", True)
    preview.apply_value("hair", "common.blend", "Transparent")
    preview.apply_value("hair", "common.blend", "Opaque")
    check("#2 ブレンドを往復しても両面表示が保たれる",
          cmds.getAttr(preview.preview_shader_of("hair") + ".technique") == "OpaqueDoubleSided")
    s.show(s.shown)

    # #3 顔の法線: 強さは重ならない / 強さ 0 は元のまま（ハードエッジ含む）
    before_fv = fv_normals(probe)
    s.create_face_proxy("face")
    s.transfer_face_normals("face", 0.0)
    err0 = max(1 - a[1] * b[1] for a, b in zip(before_fv, fv_normals(probe)))
    s.transfer_face_normals("face", 0.5)
    once = fv_normals(probe)
    s.transfer_face_normals("face", 0.5)
    err_twice = max(1 - a[1] * b[1] for a, b in zip(once, fv_normals(probe)))
    check("#3 強さ 0 の転写で法線が変わらない", err0 < 1e-6, str(err0))
    check("#3 同じ強さで 2 回転写しても重ならない", err_twice < 1e-6, str(err_twice))
    s.reset_face_normals("face")
    cmds.delete(face_normals.PROXY)

    # #5 画面なしの Maya では MCP ポートを開かない
    try:
        ports = cmds.commandPort(query=True, listPorts=True) or []
    except RuntimeError:
        ports = []  # バッチでは commandPort の問い合わせ自体が使えないことがある（= 開いていない）
    check("#5 バッチでは commandPort :7001 を開かない", ":7001" not in ports, str(ports))

    # #7 形状のヒストリがあるメッシュにはスムーズ法線を焼かない（あとで消えるため）
    cube = cmds.polyCube(name="tdHistProbe")[0]
    try:
        smooth_normals.bake([cube])
        blocked = False
    except RuntimeError:
        blocked = True
    check("#7 形状のヒストリがあるメッシュへのベイクはエラーにする", blocked)
    cmds.delete(cube)

    # #8 シーン単位（m）でも換算が合う
    unit = cmds.currentUnit(query=True, linear=True)
    cmds.currentUnit(linear="m")
    check("#8 m 単位のシーンでは換算係数 1", environment.units_per_meter() == 1.0)
    cmds.currentUnit(linear=unit)

    # ---- Phase 3: キャラクター単位の設定（characterSettings）
    check("characterSettings の既定値がある", s.setting("contactShadow.radius") == 0.25)
    s.set_setting("innerLine.parts", ["hair"])
    s.rename_part("hair", "kami2")
    check("部位名の変更にインナーライン対象が追従", s.setting("innerLine.parts") == ["kami2"])
    s.undo()
    s.set_feature("expressions", True)
    s.set_setting("expressions", {"blush": [{"material": "face", "property": "_ToonTintStrength", "min": 0.0, "max": 0.8}]})
    rows = s.preview_expression("blush", 0.5)
    check("表情プレビューは対応表どおりの値で、Look には保存されない",
          rows == [("face", "_ToonTintStrength", 0.4)] and s.look["materials"]["face"]["specific"]["_ToonTintStrength"] == 0.0)
    s.checkpoint()
    s.set_value("face", "_ToonDepthCompressWeight", 1.0)
    s.set_feature("depthCompression", True)
    s.set_setting("depthCompression", 0.5)
    st = preview.environment_state()
    check("奥行き圧縮: 量と中心（顔のメッシュ）がプレビューに渡る", st["depthCompression"] == 0.5 and st["depthPivot"] != (0.0, 0.0, 0.0))
    check("characterSettings を含めて Look が検証を通る", look.validate(s.look) == [], str(look.validate(s.look)))

    # ---- 接地影のプレビュー（4-6）
    from tdrive_toon import contact_shadow

    s.set_feature("contactShadow", True)
    s.set_setting("contactShadow.enabled", True)
    feet = contact_shadow.find_feet(s.export_meshes())
    check("接地影: 板ができて表示され、左右の足ジョイントが見つかる",
          contact_shadow.exists() and cmds.getAttr(f"{contact_shadow.NODE}.visibility") and set(feet) == {"L", "R"}, str(feet))
    check("接地影: 板は部位登録・出力の対象外",
          all(contact_shadow.NODE not in m for ms in preview.scene_materials().values() for m in ms)
          and all(contact_shadow.NODE not in m for m in s.export_meshes()))
    s.set_preview(False)
    check("接地影: 元の見た目にすると隠れる", not cmds.getAttr(f"{contact_shadow.NODE}.visibility"))
    s.set_preview(True)
    s.set_feature("contactShadow", False)
    check("接地影: 機能をオフにすると隠れる", not cmds.getAttr(f"{contact_shadow.NODE}.visibility"))
    s.set_feature("contactShadow", True)
    # ---- セルフシャドウ（4-11）: 影用ライトの作成・向き・隠す・消す（画面の無い mayapy なので影の描画は確かめない）
    from tdrive_toon import self_shadow

    s.show(s.shown)
    s.set_feature("selfShadow", True)
    to_light = preview.environment_state()["lightDir"]
    fwd = om.MVector(0, 0, 1).rotateBy(om.MEulerRotation(*[om.MAngle(a, om.MAngle.kDegrees).asRadians()
                                                              for a in cmds.getAttr(f"{self_shadow.LIGHT}.rotate")[0]]))
    check("セルフシャドウ: 影用ライトがキャラクターライトの向きで置かれる",
          self_shadow.exists() and cmds.getAttr(f"{self_shadow.LIGHT}.visibility")
          and (fwd - om.MVector(*to_light).normal()).length() < 1e-3, str(fwd))
    check("セルフシャドウ: 影用ライトは部位登録・出力の対象外",
          all(self_shadow.LIGHT not in m for m in s.export_meshes()))
    s.set_feature("selfShadow", False)
    check("セルフシャドウ: 機能オフで影用ライトが隠れる", not cmds.getAttr(f"{self_shadow.LIGHT}.visibility"))
    self_shadow.delete()
    check("セルフシャドウ: 消せる", not self_shadow.exists())

    # ---- シーンの保存（Ctrl+S）で Look も保存（2026-09-29 の不具合: シーンだけ保存して閉じると Look の変更が消えた）
    saved_look = s.save(tmp / "ctrl_s" / "look.json")
    cmds.file(rename=str(tmp / "ctrl_s.ma"))
    cmds.file(save=True, type="mayaAscii", force=True)
    s.checkpoint()
    s.set_value("face", "_ToonShadowStrength", 0.123)
    check("Look を変えるとシーンに未保存の印が付く", s.dirty and cmds.file(query=True, modified=True))
    cmds.file(save=True, type="mayaAscii", force=True)
    session.on_scene_saved()  # 画面ありの Maya では userSetup の SceneSaved が呼ぶ
    on_disk = json.loads(saved_look.read_text(encoding="utf-8"))
    check("シーンの保存で Look も保存され、どちらも未保存でなくなる",
          on_disk["materials"]["face"]["specific"]["_ToonShadowStrength"] == 0.123 and not s.dirty
          and not cmds.file(query=True, modified=True))
    s.undo()

    # ---- テクスチャの差し替え（4-9 / 4-10）。mayapy では .fx の属性が無く file ノードは作られないので、値と解決結果で確かめる
    body_mat = next(m for m in s.look["materials"] if m.startswith("body"))
    original_tex = s.value(body_mat, "common.albedo")
    s.checkpoint()
    s.set_value(body_mat, "common.albedo", "assets/unitychan/textures/body_01_SPEC.tga")
    check("テクスチャの差し替え: ベースマップが Look と出力に入る",
          look.to_ddrive_material_data(s.look, body_mat)["Common"]["Albedo"] == "assets/unitychan/textures/body_01_SPEC.tga")
    s.reset_value(body_mat, "common.albedo")
    check("テクスチャの差し替え: ↺ で元マテリアルのテクスチャに戻る",
          bool(original_tex) and s.value(body_mat, "common.albedo") == original_tex, f"{original_tex} / {s.value(body_mat, 'common.albedo')}")
    check("テクスチャを読み込み直す（画面の無い mayapy でも落ちない）", preview.reload_textures() >= 0)
    s.set_value(body_mat, "common.normal", "assets/unitychan/textures/body_01_NRM.tga")
    off = look.resolve(s.look)[body_mat]["common"]["normal"]
    s.set_feature("normalMap", True)
    check("法線マップ: 機能オフでは効かず、オンで効く",
          off is None and look.resolve(s.look)[body_mat]["common"]["normal"].endswith("body_01_NRM.tga"))
    for _ in range(4):
        s.undo()
    check("テクスチャの差し替えは Undo で戻る", s.value(body_mat, "common.albedo") == original_tex and not s.feature_enabled("normalMap"))

    # ---- 機能のマテリアル（シェーダー）単位の上書き（4-12）
    s.checkpoint()
    s.set_material_feature(body_mat, "rim", True)  # 全体はオフのまま、体だけオン
    res = look.resolve(s.look)
    out_path = s.export_unity(out_dir=tmp / "mf")
    mf = json.loads(out_path.read_text(encoding="utf-8"))["materialFeatures"]
    check("部位ごとの上書き: 体だけリムが有効で、出力のマテリアルごとの機能に入る",
          "rim" in res[body_mat]["features"] and "rim" not in res["face"]["features"]
          and "rim" in mf[body_mat] and "rim" not in mf["face"], str(mf.get(body_mat)))
    s.undo()
    check("部位ごとの上書きは Undo で戻る", look.material_override(s.look, body_mat, "rim") is None)
    body_part = look.part_of(s.look, body_mat)
    s.set_part_feature(body_part, "outline", True)  # 全体もオン → 上書きは作らない
    no_ov = look.material_override(s.look, body_mat, "outline") is None
    s.set_part_feature(body_part, "outline", False)  # 全体と違う → この部位だけオフ
    ov_off = look.material_override(s.look, body_mat, "outline") is False
    s.clear_feature_overrides("outline")
    check("一覧表のマス: 全体と同じなら上書きなし・違えば上書き・↺ で全体どおり",
          no_ov and ov_off and look.material_override(s.look, body_mat, "outline") is None)

    # ---- Render Override のコールバックの戻り値（Windows の C long = 32 ビット符号付きに収まること。2026-09-28 の OverflowError）
    from tdrive_toon import screen_line

    ov = screen_line._Override()
    raw = []
    for op in ov.ops:
        for name in ("objectTypeExclusions", "renderFilterOverride"):
            fn = getattr(type(op), name, None)
            if fn is not None and hasattr(fn, "__wrapped__"):
                raw.append((f"{type(op).__name__}.{name}", fn.__wrapped__(op)))
    raw.append(("supportedDrawAPIs", type(ov).supportedDrawAPIs.__wrapped__(ov)))
    out_of_range = [(n, v) for n, v in raw if isinstance(v, int) and not -(2**31) <= v < 2**31]
    check("Render Override のコールバックの int が 32 ビットに収まる", bool(raw) and not out_of_range, str(out_of_range or raw))

    # ---- 画面上の線（4-7）: mayapy では VP2 が無いので設定の流れと部位キーだけ確かめる
    s.set_feature("innerLine", True)
    s.set_setting("innerLine.enabled", True)
    s.set_setting("innerLine.parts", ["face"])
    keys = look.line_part_keys(s.look)
    check("画面上の線: 線を出す部位だけ線フラグが立つ",
          all((k % 2 == 1) == (m in s.look["parts"]["face"]["materials"]) for m, k in keys.items()) and keys, str(keys))
    s.set_feature("innerLine", False)
    # 透かし線（4-14）: オンの部位のメッシュだけが透かしバッファの対象になり、手前の部位は既定で髪
    s.set_feature("seeThroughOutline", True)
    s.set_value("eye", "_ToonSeeThroughOutline", 1.0)
    st = screen_line._params["seeThrough"]
    eye_meshes = {x for m in s.look["parts"]["eye"]["materials"] for x in preview.scene_materials().get(m, [])}
    check("透かし線: オンの部位のメッシュと手前の部位（髪）が渡る",
          st is not None and set(st["meshes"]) == eye_meshes and st["occluders"] == look.see_through_occluder_mask(s.look) > 0,
          str(st and {k: v for k, v in st.items() if k != "meshes"}))
    s.set_value("eye", "_ToonSeeThroughOutline", 0.0)
    check("透かし線: オフに戻すと対象なし", screen_line._params["seeThrough"] is None)
    s.set_feature("seeThroughOutline", False)
    # 輪郭線のスクリーンスペース（4-15）: 部位の表が渡り、オフに戻すと消える
    s.set_feature("outlineScreenSpace", True)
    s.set_value("eye", "_ToonOutlineScreenSpace", 1.0)
    s.set_value("eye", "_ToonOutlineWidth", 2.0)
    sil = screen_line._params["silhouette"]
    eye_no = sorted(s.look["parts"]).index("eye") + 1
    check("輪郭線のスクリーンスペース: 部位の表に目の線幅が入る", bool(sil) and sil.get(eye_no, {}).get("width") == 2.0, str(sil))
    s.set_value("eye", "_ToonOutlineScreenSpace", 0.0)
    check("輪郭線のスクリーンスペース: オフに戻すと表が空", screen_line._params["silhouette"] is None)
    s.set_feature("outlineScreenSpace", False)
    preview.delete_all()
    check("接地影: delete_all で消える", not contact_shadow.exists())
    s.show(s.shown)

    # ---- Phase 3: カメラ角度の補正 BlendShape（T-20）
    import math

    from tdrive_toon import envmath, view_correction

    head = cmds.ls("head_back", long=True)[0]
    cmds.select(head, replace=True)
    for key in view_correction.KEYS:
        target = s.create_view_correction(key)
        cmds.move(0, 0, 0.5, target + ".vtx[0]", relative=True)
        s.register_view_correction(key)
    cam = cmds.camera(name="tdSmokeVCCam")[0]
    s.connect_view_correction(cam)
    b = cmds.exactWorldBoundingBox(head)
    c = ((b[0] + b[3]) / 2, (b[1] + b[4]) / 2, (b[2] + b[5]) / 2)
    worst = 0.0
    for ang in (0, 30, 60, 90):
        r = math.radians(ang)
        cmds.xform(cam, worldSpace=True, translation=(c[0] + math.sin(r) * 200, c[1], c[2] + math.cos(r) * 200))
        w = view_correction.weights(head)
        exp = envmath.view_correction_weights(ang)
        worst = max(worst, *(abs(w[s.setting(f"viewCorrection.{k}")] - exp[k]) for k in view_correction.KEYS))
    check("カメラ角度補正: カメラ連動のウェイトが式どおり（誤差 0.01 以内）", worst < 0.01, str(worst))
    s.disconnect_view_correction()
    check("カメラ角度補正: 彫刻用メッシュは Unity 出力の対象外", not any("_vc_" in m for m in s.export_meshes()))
    check("カメラ角度補正: Look にメッシュ名とターゲット名が入り検証を通る",
          s.setting("viewCorrection.mesh") == "head_back" and look.validate(s.look) == [], str(look.validate(s.look)))

    # ---- Phase 3: SDF 顔影マップ（T-21）
    import numpy as np

    from tdrive_toon import sdf, sdf_maya

    mask_dir = tmp / "sdf_masks"
    mask_dir.mkdir()
    xs = np.arange(64)[None, :].repeat(64, 0)
    for ang, edge in ((0, 60), (90, 32), (180, 4)):
        sdf_maya.write_map((xs < edge).astype(float), mask_dir / f"face_shadow_{ang:03d}.png")
    s.checkpoint()
    rel = s.generate_face_shadow(str(mask_dir), size=64)
    ref = sdf.combine([xs < e for e in (60, 32, 4)], [0, 90, 180])
    img = om.MImage()
    img.readFromFile(str(mask_dir / "face_shadow_sdf.png"))
    got = sdf_maya.read_gray(img)[::-1, :]
    check("顔影マップ: 生成結果が計算どおり（8bit の誤差以内）", float(np.abs(got - ref).max()) < 2 / 255)
    check("顔影マップ: 顔の部位に設定され効かせ具合が 1 になる",
          s.value("face", "_ToonFaceShadowMap") == rel and s.value("face", "_ToonFaceShadowWeight") == 1.0)
    s.undo()

    # ---- 同名メッシュ（キャラクターの複製）があっても動く（2026-09-28 不具合: Toon に戻らない）
    dup_group = cmds.group(empty=True, name="tdDupTest")
    dup = cmds.duplicate(s.meshes_for("cloth")[0])[0]  # 同じ短い名前（Leg 等）のメッシュが 2 つになる
    cmds.parent(dup, dup_group)
    s.set_preview(False)
    s.set_preview(True)
    check("同名メッシュがあっても Toon ⇔ 元の見た目 が往復できる", preview.is_active())
    cmds.delete(dup_group)

    # ---- エディタ内 Undo は部位操作も戻せる / Maya の Undo はプレビューを戻さない
    s.rename_part("hair", "hair2")
    s.undo()
    check("部位名変更を Undo で戻せる", "hair" in s.look["parts"] and "hair2" not in s.look["parts"])
    s.add_variant("C", "テスト", "base")
    s.undo()
    check("バリアント追加を Undo で戻せる", "C" not in s.look["variants"])
    cmds.undoInfo(state=True)
    cmds.undoInfo(openChunk=True)
    cmds.polyCube(name="tdUndoProbe")  # Maya の Undo で戻る操作
    cmds.undoInfo(closeChunk=True)
    s.set_preview(False)
    s.set_preview(True)
    cmds.undo()  # 直前の Maya の操作（キューブ作成）だけが戻り、プレビューの割り当ては戻らないこと
    check("Maya の Undo でプレビューの割り当てが戻らない", preview.is_active() and not cmds.objExists("tdUndoProbe"))

    # ---- Unity 向け FBX（別プロセスの mayapy で整形・書き出し。開いているシーンは変わらない）
    from maya.api import OpenMaya as om

    from tdrive_toon import export

    probe = s.meshes_for("hair")[0]
    probe_shape = preview.mesh_shapes([probe])[0]
    before_sets = cmds.polyColorSet(probe_shape, query=True, allColorSets=True)
    fbx = export.export_fbx(s.export_meshes(), tmp / "out.fbx")
    check("FBX 書き出し後も開いているシーンは変わらない",
          cmds.polyColorSet(probe_shape, query=True, allColorSets=True) == before_sets)
    scene_now = cmds.file(query=True, sceneName=True)
    cmds.file(new=True, force=True)
    cmds.loadPlugin("fbxmaya", quiet=True)
    cmds.file(fbx["path"], i=True, type="FBX", ignoreVersion=True)
    bad = []
    for shp in cmds.ls(type="mesh", long=True, noIntermediate=True):
        uvs = list(om.MFnMesh(om.MSelectionList().add(shp).getDagPath(0)).getUVSetNames())
        if (cmds.polyColorSet(shp, query=True, allColorSets=True) or []) != [mask.MASK] or uvs[0] != "map1" or (
            len(uvs) > 2 and uvs[2] != preview.SMOOTH_NORMAL_UV
        ):
            bad.append(shp)
    check("FBX: 頂点カラーは tdToonMask のみ、UV は map1 / 予備 / tdSmoothNormal の順", not bad and fbx["meshes"] > 0, str(bad[:3]))
    cmds.file(scene_now, open=True, force=True)

    # 後片付け
    preview.delete_all()
    check("delete_all: プレビューノードが残らない", not preview.preview_shaders())


def membership_without_preview(m: dict[str, list[str]]) -> dict[str, list[str]]:
    return {k: v for k, v in m.items() if not k.endswith("_tdToonSG") and v}


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
        maya.standalone.uninitialize()
        os._exit(1 if failed else 0)
