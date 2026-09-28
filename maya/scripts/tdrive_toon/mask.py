"""頂点カラーの Toon マスク（docs/03 §4、T-03 / T-07 / T-08）。

- 本番: Color Set `tdToonMask`（RGBA、白 = 何もしない）
- チャンネル単位のペイント: Maya の Paint Vertex Color Tool は RGB 単位でしか塗れないため、
  作業用 Color Set `tdToonMaskEdit` に白黒で塗り、確定で本番の指定チャンネルへ書き込む。
  塗っている間はプレビューシェーダーの COLOR1 → MaskEditChannel でそのチャンネルに反映される。
"""

from __future__ import annotations

from maya import cmds, mel
from maya.api import OpenMaya as om

from . import preview

MASK = preview.MASK_COLOR_SET
EDIT = "tdToonMaskEdit"
CHANNELS = ("R", "G", "B", "A")
CHANNEL_HELP = {
    "R": "黒 = 常に影（首の下・目の周り・前髪の下）",
    "G": "黒 = 線が細く / 消える（頬・鼻・顎の線）",
    "B": "黒 = 常に明るい（顔の中心・鼻の影を消す）",
    "A": "黒 = 固定色が乗る（頬・耳の赤み・口の中）",
}

_editing: dict[str, object] = {"channel": None, "meshes": []}


# ---------------------------------------------------------------- 基本


def _shapes(meshes: list[str]) -> list[str]:
    shapes = []
    for m in meshes:
        if cmds.nodeType(m) == "mesh":
            shapes.append(m)
        else:
            shapes += cmds.listRelatives(m, shapes=True, noIntermediate=True, fullPath=True, type="mesh") or []
    return shapes


def _fn(shape: str) -> om.MFnMesh:
    return om.MFnMesh(om.MSelectionList().add(shape).getDagPath(0))


def has_mask(mesh: str) -> bool:
    return all(MASK in (cmds.polyColorSet(s, query=True, allColorSets=True) or []) for s in _shapes([mesh]))


def init(meshes: list[str]) -> list[str]:
    """Color Set tdToonMask を作って白で埋める。既にあるメッシュは触らない。作ったメッシュを返す。"""
    created = []
    for shape in _shapes(meshes):
        if MASK in (cmds.polyColorSet(shape, query=True, allColorSets=True) or []):
            continue
        _fill(shape, MASK, (1.0, 1.0, 1.0, 1.0))
        created.append(shape)
    return created


def _fill(shape: str, color_set: str, rgba: tuple[float, float, float, float]) -> None:
    if color_set not in (cmds.polyColorSet(shape, query=True, allColorSets=True) or []):
        cmds.polyColorSet(shape, create=True, colorSet=color_set, representation="RGBA", clamped=True)
    # setVertexColors は current の Color Set に書くので、対象を current にしてから呼ぶ
    cmds.polyColorSet(shape, currentColorSet=True, colorSet=color_set)
    fn = _fn(shape)
    n = fn.numVertices
    fn.setVertexColors(om.MColorArray(n, om.MColor(rgba)), list(range(n)))


def read_channel(shape: str, channel: str, color_set: str = MASK) -> list[float]:
    fn = _fn(shape)
    colors = fn.getVertexColors(color_set)
    i = CHANNELS.index(channel)
    return [(c.r, c.g, c.b, c.a)[i] if c.r >= 0 else 1.0 for c in colors]  # 未設定（-1）は白 = 何もしない


def write_channel(shape: str, channel: str, values: list[float]) -> None:
    fn = _fn(shape)
    cur = fn.getVertexColors(MASK)
    i = CHANNELS.index(channel)
    out = om.MColorArray()
    for c, v in zip(cur, values):
        rgba = [c.r, c.g, c.b, c.a] if c.r >= 0 else [1.0, 1.0, 1.0, 1.0]
        rgba[i] = v
        out.append(om.MColor(rgba))
    cmds.polyColorSet(shape, currentColorSet=True, colorSet=MASK)
    fn.setVertexColors(out, list(range(len(values))))


# ---------------------------------------------------------------- チャンネル単位ペイント


def editing() -> str | None:
    return _editing["channel"]  # type: ignore[return-value]


def begin_paint(meshes: list[str], channel: str) -> None:
    """指定チャンネルの現在値を作業用 Color Set に白黒で写し、Paint Vertex Color Tool を起動する。"""
    if channel not in CHANNELS:
        raise ValueError(channel)
    if editing():
        commit()
    shapes = _shapes(meshes)
    if not shapes:
        raise RuntimeError("メッシュを選択してください")
    init(shapes)
    for shape in shapes:
        values = read_channel(shape, channel)
        if EDIT not in (cmds.polyColorSet(shape, query=True, allColorSets=True) or []):
            cmds.polyColorSet(shape, create=True, colorSet=EDIT, representation="RGBA", clamped=True)
        cmds.polyColorSet(shape, currentColorSet=True, colorSet=EDIT)
        fn = _fn(shape)
        fn.setVertexColors(om.MColorArray([om.MColor((v, v, v, 1.0)) for v in values]), list(range(len(values))))
    _editing.update(channel=channel, meshes=shapes)
    _set_edit_channel(CHANNELS.index(channel) + 1)
    cmds.select([cmds.listRelatives(s, parent=True, fullPath=True)[0] for s in shapes], replace=True)
    mel.eval("PaintVertexColorTool")
    ctx = cmds.currentCtx()
    if cmds.artAttrPaintVertexCtx(ctx, exists=True):
        # 黒で塗る（白 = 何もしない、が既定）。RGB を同じ値で塗り、プレビューは R を使う
        cmds.artAttrPaintVertexCtx(ctx, edit=True, colorRGBValue=(0.0, 0.0, 0.0), paintRGBA=False, selectedattroper="absolute")


def commit() -> None:
    """作業用 Color Set の値を本番 tdToonMask の対象チャンネルへ書き込み、作業用を消す。"""
    channel = editing()
    if not channel:
        return
    for shape in _editing["meshes"]:  # type: ignore[union-attr]
        if not cmds.objExists(shape):
            continue
        values = read_channel(shape, "R", color_set=EDIT)
        write_channel(shape, channel, values)
    cancel()


def cancel() -> None:
    """作業用 Color Set を消して編集を終える（本番の値は変えない）。"""
    for shape in _editing["meshes"]:  # type: ignore[union-attr]
        if cmds.objExists(shape) and EDIT in (cmds.polyColorSet(shape, query=True, allColorSets=True) or []):
            cmds.polyColorSet(shape, delete=True, colorSet=EDIT)
            cmds.polyColorSet(shape, currentColorSet=True, colorSet=MASK)
    _editing.update(channel=None, meshes=[])
    _set_edit_channel(0)
    if cmds.currentCtx() != "selectSuperContext":
        cmds.setToolTo("selectSuperContext")


def _shaders_of(shapes: list[str]) -> set[str]:
    out = set()
    for sg in cmds.listConnections(shapes, type="shadingEngine") or [] if shapes else []:
        for sh in cmds.listConnections(f"{sg}.surfaceShader") or []:
            if cmds.nodeType(sh) == preview.NODE_TYPE:
                out.add(sh)
    return out


def _set_edit_channel(index: int) -> None:
    """編集中メッシュのプレビューシェーダーだけ作業用チャンネル表示にする。

    作業用 Color Set を持たないメッシュで有効にすると COLOR1 が 0（黒）で読まれて壊れて見えるため。
    """
    targets = _shaders_of(list(_editing["meshes"])) if index else set()  # type: ignore[arg-type]
    for shader in preview.preview_shaders():
        if cmds.attributeQuery("MaskEditChannel", node=shader, exists=True):
            cmds.setAttr(f"{shader}.MaskEditChannel", index if shader in targets else 0)
        if shader in targets and cmds.attributeQuery("Color1_Source", node=shader, exists=True):
            cmds.setAttr(f"{shader}.Color1_Source", f"color:{EDIT}", type="string")


def show_channel(channel: str | None) -> None:
    """チャンネル単体表示（白黒）。None で通常表示に戻す。"""
    mode = 0 if channel is None else 4 + CHANNELS.index(channel)  # TDriveToon.fx の PreviewDebug
    for shader in preview.preview_shaders():
        if cmds.attributeQuery("PreviewDebug", node=shader, exists=True):
            cmds.setAttr(f"{shader}.PreviewDebug", mode)
