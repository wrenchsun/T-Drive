"""サンプルモデル shizuku の Look（looks/shizuku/look.json）を作る（チケット S-2）。

デザイナーがエディタでやる操作（新規 Look → 部位登録 → ロールの修正 → 保存）を、画面なしの mayapy から
セッション API（tdrive_toon.session）で行う。JSON は手書きしない。アバター元のシェーダー・マテリアル設定は使わず、
UnityChan と同じ T-Drive Toon シェーダー + テクスチャ（色 `_d` と法線 `_n`）だけを使う。

  mayapy tools/setup_sample_shizuku_look.py [--rebuild] [--no-verify] [--project <プロジェクトフォルダ>]

  前提: tools/setup_sample_shizuku.py（S-1）で assets/shizuku/shizuku.mb ができていること。
  既定は「既にある looks/shizuku/look.json を開いて足りない所だけ足す」（エディタでの調整を壊さない）。
  --rebuild で Look を作り直す。何度実行しても同じ結果になる。
  mayapy は環境変数 MAYA_DISABLE_CER=1 と PYTHONIOENCODING=utf-8 を付けて起動すること。

部位・ロールの割り当て（マテリアル名とそれが覆う範囲から決めた。自動推定 guess_role では
mat_body01 が cloth、mat_wear* が other になり実態と合わないので、手で登録する）:

  部位名      ロール     マテリアル        覆う範囲
  skin        skin       mat_body01        体の肌 + 顔メッシュ（mdl_face02）の肌の面（目・肌・頬など。下記「顔の肌」）
  faceOption  mouth      mat_faceOption1   顔メッシュの目・眉・口の中などのオプションパーツの面（アトラス）
  hair        hair       mat_hair01        前髪 mdl_hair01_F・後ろ髪 mdl_hair01_B
  wear01      cloth      mat_wear01        服 1（mdl_wear01）
  wear02      cloth      mat_wear02        服 2（mdl_wear02）
  wear03      cloth      mat_wear03        服 3（mdl_wear03）
  watchLcd    accessory  lambert2          腕時計の液晶 4 面（mdl_wear01 の一部の面。テクスチャ無し）

  - faceOption は色テクスチャ（faceOption_d）が口の中・舌・眉・頬の赤みなどを 1 枚に詰めたもの。
    1 つのマテリアルに複数の用途が混ざるので、いちばん広い面積を占める「口」を選んだ（影を弱く・輪郭線を細く）。
    眉・頬を個別に調整したくなったら、モデル側でマテリアルを分けてから brow / blush に登録する。
  - 顔の肌: Look の部位は「マテリアル単位」（look.register_part の materials）で、面単位の登録は無い。
    mat_body01 は体と顔メッシュの肌の両方に付いているので、skin に登録すると顔の肌も skin ロールになる。
    顔の肌に "face" ロール（セルフシャドウを受けない・影のしきい値が顔向け）を付けるには、モデル側で顔の肌の面を
    別マテリアルに分ける必要がある。モデルは変更しない（この Look は skin のまま。docs/02 への提案としてレポート）。
  - テクスチャ: 色 = lambert に繋がる `_d`、法線 = 同じ名前の `_n`（body01 / wear01〜03。あるものだけ）。
    `_m`（マスク）は各チャンネルの意味が不明なので割り当てない。法線マップの機能（features.normalMap）はオンにする。
    パスはプロジェクトフォルダ基準の相対（assets/shizuku/textures/…）。絶対パスは Look に入れない。
  - 後始末: アバターの非表示ブレンドシェイプのターゲットメッシュは preview.scene_materials() に出てこない
    （マテリアルの割り当てが無い）ので、可視メッシュだけが対象になる。念のため EXPECTED_MESHES と突き合わせる。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CHARACTER = "shizuku"
SCENE_REL = Path("assets") / "shizuku" / "shizuku.mb"

# (部位名, ロール, マテリアル)
PARTS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("skin", "skin", ("mat_body01",)),
    ("faceOption", "mouth", ("mat_faceOption1",)),
    ("hair", "hair", ("mat_hair01",)),
    ("wear01", "cloth", ("mat_wear01",)),
    ("wear02", "cloth", ("mat_wear02",)),
    ("wear03", "cloth", ("mat_wear03",)),
    ("watchLcd", "accessory", ("lambert2",)),
)
EXPECTED_MATERIALS = {m for _, _, mats in PARTS for m in mats}
EXPECTED_MESHES = {"mdl_face02", "mdl_body02", "mdl_hair01_F", "mdl_hair01_B", "mdl_wear01", "mdl_wear02", "mdl_wear03"}
NORMAL_MATERIALS = ("mat_body01", "mat_wear01", "mat_wear02", "mat_wear03")  # `_n` があるもの（無ければ飛ばす）
MASK_SUFFIX = "_m"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="shizuku の Look を作る（S-2）")
    ap.add_argument("--rebuild", action="store_true", help="既存の look.json を無視して作り直す")
    ap.add_argument("--no-verify", action="store_true", help="Toon プレビュー・Unity 出力の確認を省く")
    ap.add_argument("--project", type=Path, default=None, help="プロジェクトフォルダ（既定: $TDRIVE_PROJECT、無ければリポジトリ直下）")
    return ap.parse_args(argv)


def short(path: str) -> str:
    return path.rsplit("|", 1)[-1]


def visible_scene_materials(preview, cmds) -> dict[str, list[str]]:
    """マテリアル → 可視メッシュの短い名前。非表示メッシュ（ブレンドシェイプのターゲット）は除く。"""
    result: dict[str, list[str]] = {}
    for mat, meshes in preview.scene_materials().items():
        vis = [short(m) for m in meshes if cmds.getAttr(f"{m}.visibility")]
        if vis:
            result[mat] = vis
    return result


def build(args: argparse.Namespace) -> dict:
    """Look を作って保存し、シーンに Look のパスを記録して保存する。後続の確認に使う情報を返す。"""
    from maya import cmds

    from tdrive_toon import look, preview, project, session

    scene = project.root() / SCENE_REL
    if not scene.exists():
        raise FileNotFoundError(f"{scene} がありません。先に tools/setup_sample_shizuku.py を実行してください（S-1）")
    cmds.file(str(scene), open=True, force=True)

    mats = visible_scene_materials(preview, cmds)
    missing = EXPECTED_MATERIALS - set(mats)
    extra = set(mats) - EXPECTED_MATERIALS
    if missing or extra:
        raise RuntimeError(f"マテリアルが想定と違う: 不足 {sorted(missing)} / 余分 {sorted(extra)}")
    meshes = {m for v in mats.values() for m in v}
    if meshes != EXPECTED_MESHES:
        raise RuntimeError(f"可視メッシュが想定と違う: {sorted(meshes ^ EXPECTED_MESHES)}")
    # scene_materials は非表示メッシュを含んでいないか（含むならツール側の不具合として報告する）
    hidden_in_tool = sorted({short(m) for v in preview.scene_materials().values() for m in v} - EXPECTED_MESHES)
    if hidden_in_tool:
        print(f"[FINDING] preview.scene_materials() が想定外のメッシュを返した（非表示のターゲットか）: {hidden_in_tool[:5]}…")

    s = session.current()
    look_path = project.looks_dir() / CHARACTER / "look.json"
    if look_path.exists() and not args.rebuild:
        s.look = look.load(look_path)  # open() は show() まで走らせる。ここは中身だけ読む
        s.path = look_path
        s.dirty = False
    else:
        s.new(CHARACTER, preview.to_repo_path(str(scene)))

    for part, role, part_mats in PARTS:
        if look.part_of(s.look, part_mats[0]) == part and s.look["parts"][part]["role"] == role:
            continue  # 登録済み（エディタでの調整値を保つ）
        s.register(part, role, part_mats)

    # テクスチャ: 色 `_d` は register が元マテリアルから拾う。足りなければここで補う
    assigned: dict[str, dict[str, str | None]] = {}
    for mat in sorted(EXPECTED_MATERIALS):
        albedo = s.look["materials"][mat]["common"].get("albedo") or preview.base_texture_of(mat)
        if albedo and not s.look["materials"][mat]["common"].get("albedo"):
            s.set_value(mat, "common.albedo", albedo, notify=False)
        normal = None
        if mat in NORMAL_MATERIALS and albedo:
            cand = albedo.replace("_d.", "_n.")
            if cand != albedo and (project.root() / cand).exists():
                normal = cand
                if s.look["materials"][mat]["common"].get("normal") != normal:
                    s.set_value(mat, "common.normal", normal, notify=False)
        assigned[mat] = {"albedo": albedo, "normal": normal}
    if not s.feature_enabled("normalMap"):
        s.set_feature("normalMap", True)

    errors = look.validate(s.look)
    if errors:
        raise RuntimeError("Look の検証エラー:\n  " + "\n  ".join(errors))
    path = s.save()
    # s.save() が cmds.fileInfo("tdriveToonLook", <相対パス>) も書く。シーンを保存して Look への参照を残す
    cmds.file(save=True, type="mayaBinary", force=True)
    unassigned = sorted(
        p.name for p in (project.root() / "assets" / "shizuku" / "textures").glob(f"*{MASK_SUFFIX}.png")
    )
    print(f"[S-2] Look を保存: {path}")
    return {"session": s, "path": path, "assigned": assigned, "masks": unassigned}


def print_table(s, cmds, preview, look) -> None:
    mats = visible_scene_materials(preview, cmds)
    print("[S-2] 部位 → ロール → マテリアル → メッシュ → テクスチャ")
    for part, info in s.look["parts"].items():
        for m in info["materials"]:
            c = s.look["materials"][m]["common"]
            print(f"  {part:<11} {info['role']:<9} {m:<16} {', '.join(sorted(mats.get(m, []))) or '-':<34} "
                  f"albedo={c.get('albedo')}  normal={c.get('normal')}  blend={c.get('blend')}")


def membership(cmds) -> dict[str, list[str]]:
    return {sg: sorted(cmds.sets(sg, q=True) or []) for sg in cmds.ls(type="shadingEngine")}


def verify(info: dict) -> None:
    """Toon プレビューの有効化・無効化の往復、Unity 出力の検証。"""
    from maya import cmds

    from tdrive_toon import look, preview

    s = info["session"]
    problems: list[str] = []
    print_table(s, cmds, preview, look)

    before = membership(cmds)
    s.show("base")
    if not preview.is_active():
        problems.append("show 後に preview.is_active() が False")
    shaders = preview.preview_shaders()
    on_preview: set[str] = set()
    for sh in shaders:
        for sg in cmds.listConnections(sh, type="shadingEngine") or []:
            for member in cmds.sets(sg, q=True) or []:
                node = cmds.ls(member, long=True, objectsOnly=True)[0]
                if cmds.nodeType(node) == "mesh":
                    node = cmds.listRelatives(node, parent=True, fullPath=True)[0]
                on_preview.add(short(node))
    print(f"[S-2] プレビューシェーダー {len(shaders)} 個 / 割り当て済みメッシュ: {sorted(on_preview)}")
    if on_preview != EXPECTED_MESHES:
        problems.append(f"プレビューに割り当たったメッシュが想定と違う: {sorted(on_preview ^ EXPECTED_MESHES)}")
    if set(shaders) != {preview.preview_shader_of(m) for m in EXPECTED_MATERIALS}:
        problems.append(f"プレビューシェーダーの集合が想定と違う: {shaders}")

    s.set_preview(False)
    if preview.is_active():
        problems.append("無効化後も preview.is_active() が True")
    after = membership(cmds)
    # 元からあった SG は面単位まで同じ。プレビュー用に増えた SG は空に戻っている
    changed = [sg for sg in before if after.get(sg) != before[sg]]
    leftover = [sg for sg in after if sg not in before and after[sg]]
    if changed or leftover:
        problems.append(f"無効化後のマテリアル割り当てが元と一致しない: 変化 {changed} / プレビュー SG に残り {leftover}")
    else:
        print("[S-2] 無効化後の割り当ては元と完全に一致（面単位の SG メンバーシップ。プレビュー用 SG は空）")

    out_dir = Path(tempfile.mkdtemp(prefix="tdrive_shizuku_export_"))
    json_path = s.export_unity(out_dir=out_dir)
    data = json.loads(json_path.read_text(encoding="utf-8"))
    print(f"[S-2] Unity 出力: {json_path}（materials {len(data['materials'])}・parts {list(data['parts'])}）")
    if set(data["materials"]) != EXPECTED_MATERIALS:
        problems.append(f"出力のマテリアルが想定と違う: {sorted(data['materials'])}")
    errors = look.validate(s.look)
    if errors:
        problems += errors
    exported = set(short(m) for m in s.export_meshes())
    print(f"[S-2] FBX に入るメッシュ: {sorted(exported)}")
    if exported != EXPECTED_MESHES:
        problems.append(f"export_meshes が想定と違う: {sorted(exported ^ EXPECTED_MESHES)}")
    if problems:
        raise RuntimeError("確認でエラー:\n  " + "\n  ".join(problems))
    print("[S-2] 確認 OK")


def report_model(cmds) -> None:
    """Toon の機能（スムーズ法線・マスク）に関わるモデルの様子を出力する（見るだけ）。"""
    from tdrive_toon import preview

    print("[S-2] 可視メッシュのカラーセット・UV セット")
    seen = set()
    for meshes in preview.scene_materials().values():
        for mesh in meshes:
            if mesh in seen or not cmds.getAttr(f"{mesh}.visibility"):
                continue
            seen.add(mesh)
            shape = cmds.listRelatives(mesh, shapes=True, noIntermediate=True, fullPath=True)[0]
            print(f"  {short(mesh):<14} colorSets={cmds.polyColorSet(shape, q=True, allColorSets=True)} "
                  f"uvSets={cmds.polyUVSet(shape, q=True, allUVSets=True)}")


def main() -> int:
    args = parse_args()
    if args.project:
        os.environ["TDRIVE_PROJECT"] = str(args.project)
    os.environ.setdefault("TDRIVE_ROOT", REPO.as_posix())
    sys.path.insert(0, str(REPO / "maya" / "scripts"))
    code = 0
    import maya.standalone

    maya.standalone.initialize(name="python")
    try:
        from maya import cmds

        info = build(args)
        print(f"[S-2] 割り当てたテクスチャ: {json.dumps(info['assigned'], ensure_ascii=False)}")
        print(f"[S-2] 未割り当てのマスク（チャンネルの意味が不明）: {info['masks']}")
        report_model(cmds)
        if not args.no_verify:
            verify(info)
    except Exception:
        traceback.print_exc()
        code = 1
    sys.stdout.flush()
    sys.stderr.flush()
    try:
        maya.standalone.uninitialize()
    finally:
        os._exit(code)


if __name__ == "__main__":
    main()
