"""サンプルモデル shizuku の Look（looks/shizuku/look.json）を作る（チケット S-2）。

デザイナーがエディタでやる操作（新規 Look → 部位登録 → ロールの修正 → 保存）を、画面なしの mayapy から
セッション API（tdrive_toon.session）で行う。JSON は手書きしない。アバター元のシェーダー・マテリアル設定は使わず、
UnityChan と同じ T-Drive Toon シェーダー + テクスチャ（色 `_d` と法線 `_n`）だけを使う。

  mayapy tools/setup_sample_shizuku_look.py [--rebuild] [--no-verify] [--project <プロジェクトフォルダ>]

  前提: tools/setup_sample_shizuku.py（S-1）で assets/shizuku/shizuku.mb ができていること。
  既定は「既にある looks/shizuku/look.json を開いて足りない所だけ足す」（エディタでの調整を壊さない）。
  --rebuild で Look を作り直す。何度実行しても同じ結果になる。
  Blend・Cutoff・両面は元の Unity マテリアル（--source-materials、既定は下の SOURCE_MATERIALS_DIR）から決める。
  無ければ BLEND_FALLBACK の表を使う。Transparent / Cutout にするのは色テクスチャに半透明の画素があるときだけ。
  新規作成・--rebuild では自動で入れる。既にある Look には --fix-blend を付けたときだけ入れる（Look を誰が作ったか分からないため。
  Blend が既定の Opaque・Cutoff 0.5・片面のままのマテリアルだけを対象にし、エディタでの調整は上書きしない）。
  --fix-blend は部位 faceOption のロールが blush でないときの登録し直しも行う。
  mayapy は環境変数 MAYA_DISABLE_CER=1 と PYTHONIOENCODING=utf-8 を付けて起動すること。

部位・ロールの割り当て（マテリアル名とそれが覆う範囲から決めた。自動推定 guess_role では
mat_body01 が cloth、mat_wear* が other になり実態と合わないので、手で登録する）:

  部位名      ロール     マテリアル        覆う範囲
  skin        skin       mat_body01        体の肌 + 顔メッシュ（mdl_face02）の肌の面（目・肌・頬など。下記「顔の肌」）
  faceOption  blush      mat_faceOption1   顔メッシュの涙・頬の赤み・汗などのオーバーレイのカード（アトラス。下記「faceOption」）
  hair        hair       mat_hair01        前髪 mdl_hair01_F・後ろ髪 mdl_hair01_B
  wear01      cloth      mat_wear01        服 1（mdl_wear01）
  wear02      cloth      mat_wear02        服 2（mdl_wear02）
  wear03      cloth      mat_wear03        服 3（mdl_wear03）
  watchLcd    accessory  lambert2          腕時計の液晶 4 面（mdl_wear01 の一部の面。テクスチャ無し）

  - faceOption は涙・頬の赤み・汗などを描いた半透明のカード（元の Unity マテリアルは `_ZWrite: 0` のオーバーレイ）。
    輪郭線のあるロール（mouth など）だとカードの周りに線が出て目を覆うので、オーバーレイ用の blush（影なし・輪郭線の幅 0）にする。
    部位が混ざるので眉・目を個別に調整したくなったら、モデル側でマテリアルを分けてから登録する。
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
import re
import struct
import traceback
import zlib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CHARACTER = "shizuku"
SCENE_REL = Path("assets") / "shizuku" / "shizuku.mb"

# (部位名, ロール, マテリアル)
PARTS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("skin", "skin", ("mat_body01",)),
    ("faceOption", "blush", ("mat_faceOption1",)),
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

# 元の Unity マテリアル（読み取り専用）。無い環境では BLEND_FALLBACK を使う
SOURCE_MATERIALS_DIR = Path(r"C:\Users\yamag\Downloads\kuromaru9\shizuku\Materials")
SOURCE_MAT_FILE = {  # Look のマテリアル → 元の .mat
    "mat_body01": "body01.mat",
    "mat_faceOption1": "faceOption.mat",
    "mat_hair01": "hair01.mat",
    "mat_wear01": "wear01.mat",
    "mat_wear02": "wear02.mat",
    "mat_wear03": "wear03.mat",
}
# 元マテリアルが無いとき用: マテリアル → (blend, cutoff, doubleSided)。wear02 は元が `_Transparent: 1` だが色テクスチャに alpha が無い
BLEND_FALLBACK = {
    "mat_body01": ("Opaque", 0.5, False),
    "mat_faceOption1": ("Transparent", 0.5, False),
    "mat_hair01": ("Cutout", 0.5, False),
    "mat_wear01": ("Transparent", 0.5, False),
    "mat_wear02": ("Opaque", 0.5, False),
    "mat_wear03": ("Opaque", 0.5, False),
}


def parse_unity_mat(text: str) -> dict[str, float]:
    """Unity の .mat（YAML）から `- _Name: 数値` の行だけを読む（最小限。テクスチャや色は見ない）。"""
    out: dict[str, float] = {}
    for m in re.finditer(r"^\s*-\s+(_\w+):\s*(-?[0-9.eE+-]+)\s*$", text, re.M):
        try:
            out[m.group(1)] = float(m.group(2))
        except ValueError:
            pass
    return out


def blend_from_source(props: dict[str, float]) -> tuple[str, float, bool]:
    """元マテリアルの数値から (blend, cutoff, doubleSided)。テクスチャの alpha はここでは見ない（apply_alpha_rule）。

    `_Transparent: 1` で `_Cutoff` がある → Cutout / `_Transparent: 1` または `_ZWrite: 0`（オーバーレイ）→ Transparent。
    両面は `_cullMode: 0`（Off）。
    """
    cutoff = props.get("_Cutoff", props.get("_MaskClipValue", 0.5))
    if props.get("_Transparent", 0.0) >= 1.0 and "_Cutoff" in props:
        blend = "Cutout"
    elif props.get("_Transparent", 0.0) >= 1.0 or props.get("_ZWrite", 1.0) == 0.0:
        blend = "Transparent"
    else:
        blend = "Opaque"
    return blend, float(cutoff), props.get("_cullMode", 2.0) == 0.0


def apply_alpha_rule(blend: str, alpha_state: str) -> str:
    """Transparent / Cutout は色テクスチャに半透明の画素があるときだけ（無ければ Opaque）。alpha_state: none / opaque / partial。"""
    return blend if blend != "Opaque" and alpha_state == "partial" else "Opaque"


def png_alpha_state(path: Path) -> str:
    """PNG の alpha の状態。none = alpha チャンネル無し / opaque = 全画素が不透明 / partial = 半透明・透明の画素あり。

    8 ビット・インターレース無しの RGBA / グレー+alpha は alpha だけをフィルター復元して調べる（Pillow・numpy 不要。
    フィルターはチャンネルごとに独立）。それ以外の形式（16 ビット等）は、alpha チャンネルがあれば partial 扱い（安全側）。
    """
    data = Path(path).read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"PNG ではない: {path}")
    pos, idat, ihdr, trns = 8, [], None, None
    while pos + 8 <= len(data):
        n, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + n]
        if kind == b"IHDR":
            ihdr = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat.append(body)
        elif kind == b"tRNS":
            trns = body
        pos += 12 + n
    if ihdr is None:
        raise ValueError(f"IHDR が無い: {path}")
    width, height, depth, ctype, _, _, interlace = ihdr
    if ctype in (0, 2, 3):  # alpha チャンネル無し（tRNS は透明色・パレットの透明度）
        if not trns:
            return "none"
        return "partial" if ctype != 3 or any(v < 255 for v in trns) else "none"
    channels = 4 if ctype == 6 else 2
    if depth != 8 or interlace:
        return "partial"
    stride = width * channels
    raw = zlib.decompress(b"".join(idat))
    prev = bytearray(width)
    for y in range(height):
        base = y * (stride + 1)
        ftype = raw[base]
        # alpha だけを取り出す（フィルターはチャンネルごとに独立なので bpp = 1 で復元できる）
        cur = bytearray(raw[base + channels:base + 1 + stride:channels])
        if ftype == 1:
            for x in range(1, width):
                cur[x] = (cur[x] + cur[x - 1]) & 255
        elif ftype == 2:
            cur = bytearray((a + b) & 255 for a, b in zip(cur, prev))
        elif ftype == 3:
            left = 0
            for x in range(width):
                left = cur[x] = (cur[x] + ((left + prev[x]) >> 1)) & 255
        elif ftype == 4:
            left = up_left = 0
            for x in range(width):
                up = prev[x]
                pa, pb, pc = abs(up - up_left), abs(left - up_left), abs(left + up - 2 * up_left)
                pred = left if pa <= pb and pa <= pc else (up if pb <= pc else up_left)
                left = cur[x] = (cur[x] + pred) & 255
                up_left = up
        if min(cur) < 255:
            return "partial"
        prev = cur
    return "opaque"


def decide_blends(project_root: Path, source_dir: Path | None, lk: dict) -> dict[str, tuple[str, float, bool]]:
    """マテリアル → (blend, cutoff, doubleSided)。元マテリアルがあればそこから、無ければ表から。alpha の規則を掛ける。"""
    result = {}
    for mat, (fb_blend, fb_cutoff, fb_double) in BLEND_FALLBACK.items():
        src = source_dir / SOURCE_MAT_FILE[mat] if source_dir else None
        if src is not None and src.is_file():
            blend, cutoff, double = blend_from_source(parse_unity_mat(src.read_text(encoding="utf-8", errors="replace")))
        else:
            blend, cutoff, double = fb_blend, fb_cutoff, fb_double
        albedo = lk["materials"].get(mat, {}).get("common", {}).get("albedo")
        tex = project_root / albedo if albedo else None
        state = png_alpha_state(tex) if tex and tex.suffix.lower() == ".png" and tex.is_file() else "none"
        result[mat] = (apply_alpha_rule(blend, state), cutoff, double)
    return result


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="shizuku の Look を作る（S-2）")
    ap.add_argument("--rebuild", action="store_true", help="既存の look.json を無視して作り直す")
    ap.add_argument("--fix-blend", action="store_true", help="既存の Look にも Blend / Cutoff / 両面（と faceOption のロール）を入れる。既定のまま触られていないマテリアルだけが対象")
    ap.add_argument("--source-materials", type=Path, default=SOURCE_MATERIALS_DIR, help="元の Unity マテリアル（.mat）のフォルダ。無ければ表を使う")
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
    fresh = args.rebuild or not look_path.exists()
    if not fresh:
        s.look = look.load(look_path)  # open() は show() まで走らせる。ここは中身だけ読む
        s.path = look_path
        s.dirty = False
    else:
        s.new(CHARACTER, preview.to_repo_path(str(scene)))

    for part, role, part_mats in PARTS:
        registered = look.part_of(s.look, part_mats[0]) == part
        if registered and (s.look["parts"][part]["role"] == role or not (fresh or args.fix_blend)):
            continue  # 登録済み（エディタでの調整値を保つ）。ロールの付け替えは新規 / --fix-blend のときだけ
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

    # Blend / Cutoff / 両面（元の Unity マテリアル + 色テクスチャの alpha）。既存の Look は --fix-blend のときだけ
    if fresh or args.fix_blend:
        source = args.source_materials if args.source_materials and Path(args.source_materials).is_dir() else None
        print(f"[S-2] Blend の決定元: {source or '表（BLEND_FALLBACK）'}")
        for mat, (blend, cutoff, double) in decide_blends(project.root(), source, s.look).items():
            common = s.look["materials"][mat]["common"]
            if not fresh and (common.get("blend") != "Opaque" or common.get("cutoff") != 0.5 or common.get("doubleSided")):
                print(f"[S-2] {mat}: 調整済みのため Blend を変えない（{common.get('blend')}）")
                continue
            for key, value in (("blend", blend), ("cutoff", cutoff), ("doubleSided", double)):
                if common.get(key) != value:
                    s.set_value(mat, f"common.{key}", value, notify=False)
            print(f"[S-2] {mat}: blend={blend} cutoff={cutoff} doubleSided={double}")

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
