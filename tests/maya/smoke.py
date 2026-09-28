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

    from tdrive_toon import environment, look, preview, session

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
    shape = mask._shapes(face_meshes)[0]
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

    # ---- Unity 向け FBX（別プロセスの mayapy で整形・書き出し。開いているシーンは変わらない）
    from maya.api import OpenMaya as om

    from tdrive_toon import export

    probe = s.meshes_for("hair")[0]
    probe_shape = mask._shapes([probe])[0]
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
