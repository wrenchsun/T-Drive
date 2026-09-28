"""画面上の線（T-23 インナーライン / T-42 外側輪郭）の Maya プレビュー（4-7 / 4-8）。

VP2 の Render Override で、Unity の ToonRendererFeature と同じ手順を再現する（docs/03 §10）:

1. 描画先を 2 枚（色 / ToonId）にして場面を描く。Toon の本体パスが ToonId（法線・部位キー・奥行き）を 2 枚目に書く
2. 全画面の合成（TDriveScreenLine.fx）で ToonId からエッジを判定し、線を上書きする
3. UI（グリッド・ワイヤー・選択表示）と HUD を上に描いて表示する

線を使うときだけ、プレビューのモデルパネルにこの Override を割り当てる（使わないときは通常の VP2 のまま）。
実装上の注意（2026-09-28 の検証で判明）:
- 描画先の名前は重複できない（解放し忘れた同名の描画先があると acquire が None を返す）→ 登録ごとに固有の名前
- テクスチャは MRenderTarget を直接 setParameter に渡す（MRenderTargetAssignment は Python では効かない）
- サンプラーを明示的に設定しないとテクスチャが結び付かない
- 消去は結び付いた描画先すべてに掛かる → 色と ToonId を別の「消去だけ」の操作で消す
"""

from __future__ import annotations

import uuid
from typing import Any

import maya.api.OpenMaya as om
import maya.api.OpenMayaRender as omr
from maya import cmds

from . import REPO_ROOT, lifecycle

NAME = "tdToonScreenLine"
SHADER_FILE = (REPO_ROOT / "maya" / "shaders" / "TDriveScreenLine.fx").as_posix()

# 合成シェーダーに渡す値（session が更新する）。半径・色は docs/03 §10.2 / §10.3 の定義どおり
_params: dict[str, Any] = {
    "tonemap": 1,
    "inner": None,  # {"width": px@1080p, "color": リニア RGB} or None
    "outer": None,
}


def maya_useNewAPI() -> None:  # noqa: N802 (Maya の規約)
    """Python API 2.0 を使う印。"""


# 全種類を除外したいが、Python API は戻り値を C の long（Windows では 32 ビット符号付き）に変換するため
# kExcludeAll（0xFFFF…FFFF）も 62 ビットの値も OverflowError になる。31 ビットまで（メッシュ等を含む）を立て、
# それ以上の種類（プラグインのシェイプ等）は objectSetOverride の空の選択で描かないようにする（docs/09 §6）
_EXCLUDE_ALL = 0x7FFFFFFF


class _ClearOp(omr.MSceneRender):
    """何も描かずに描画先を消去するだけの操作。"""

    def __init__(self, name: str, ov: "_Override", which: str) -> None:
        super().__init__(name)
        self.ov, self.which = ov, which

    @lifecycle.guarded(None)
    def targetOverrideList(self):  # noqa: N802
        t = self.ov.targets
        return [t["color"], t["depth"]] if self.which == "color" else [t["id"]]

    @lifecycle.guarded(_EXCLUDE_ALL)
    def objectTypeExclusions(self):  # noqa: N802
        return _EXCLUDE_ALL

    @lifecycle.guarded(fallback=lambda self: self.ov.empty_selection)
    def objectSetOverride(self):  # noqa: N802
        return self.ov.empty_selection  # 何も描かない（消去だけ）

    @lifecycle.guarded(fallback=lambda self: self.mClearOperation)
    def clearOperation(self):  # noqa: N802
        c = self.mClearOperation
        c.setMask(omr.MClearOperation.kClearAll)
        if self.which == "color":
            top, bottom, gradient = self.ov.background
            c.setClearColor(top + (1.0,))
            c.setClearColor2(bottom + (1.0,))
            c.setClearGradient(gradient)
        else:
            c.setClearColor((0.0, 0.0, 0.0, 0.0))
            c.setClearGradient(False)
        return c


class _SceneOp(omr.MSceneRender):
    """場面の描画。shaded=True は色 + ToonId へ、False は UI（線の上に描く）。"""

    def __init__(self, name: str, ov: "_Override", shaded: bool) -> None:
        super().__init__(name)
        self.ov, self.shaded = ov, shaded

    @lifecycle.guarded(None)
    def targetOverrideList(self):  # noqa: N802
        t = self.ov.targets
        return [t["color"], t["id"], t["depth"]] if self.shaded else [t["out"], t["depth"]]

    @lifecycle.guarded(fallback=lambda self: omr.MSceneRender.kRenderShadedItems)
    def renderFilterOverride(self):  # noqa: N802
        return omr.MSceneRender.kRenderShadedItems if self.shaded else omr.MSceneRender.kRenderNonShadedItems

    @lifecycle.guarded(fallback=lambda self: self.mClearOperation)
    def clearOperation(self):  # noqa: N802
        c = self.mClearOperation
        c.setMask(omr.MClearOperation.kClearNone)
        return c


class _QuadOp(omr.MQuadRender):
    def __init__(self, name: str, ov: "_Override") -> None:
        super().__init__(name)
        self.ov = ov
        self._shader = None
        self._sampler = None

    @lifecycle.guarded(None)
    def shader(self):
        if self._shader is None:
            self._shader = omr.MRenderer.getShaderManager().getEffectsFileShader(SHADER_FILE, "Main", [], False)
            desc = omr.MSamplerStateDesc()
            desc.filter = omr.MSamplerState.kMinMagMipPoint
            desc.addressU = desc.addressV = omr.MSamplerState.kTexClamp
            self._sampler = omr.MStateManager.acquireSamplerState(desc)
        sh = self._shader
        if sh is None:
            return None
        t = self.ov.targets
        w, h = self.ov.size
        set_param(sh, "gColorTex", t["color"])
        set_param(sh, "gIdTex", t["id"])
        set_param(sh, "gPointSampler", self._sampler)
        set_param(sh, "gScreenSize", [w, h])
        set_param(sh, "gTonemap", _params["tonemap"])
        for key in ("inner", "outer"):
            p = _params[key]
            cap = key.capitalize()
            set_param(sh, f"g{cap}Enabled", p is not None)
            if p is not None:
                set_param(sh, f"g{cap}Radius", line_radius(p["width"], h))
                set_param(sh, f"g{cap}Color", p["color"][:3])
        return sh

    @lifecycle.guarded(None)
    def targetOverrideList(self):  # noqa: N802
        return [self.ov.targets["out"], self.ov.targets["depth"]]

    @lifecycle.guarded(fallback=lambda self: self.mClearOperation)
    def clearOperation(self):  # noqa: N802
        c = self.mClearOperation
        c.setMask(omr.MClearOperation.kClearNone)
        return c


class _HudOp(omr.MHUDRender):
    def __init__(self, ov: "_Override") -> None:
        super().__init__()
        self.ov = ov

    @lifecycle.guarded(None)
    def targetOverrideList(self):  # noqa: N802
        return [self.ov.targets["out"], self.ov.targets["depth"]]


class _PresentOp(omr.MPresentTarget):
    def __init__(self, name: str, ov: "_Override") -> None:
        super().__init__(name)
        self.ov = ov

    @lifecycle.guarded(None)
    def targetOverrideList(self):  # noqa: N802
        return [self.ov.targets["out"], self.ov.targets["depth"]]


class _Override(omr.MRenderOverride):
    def __init__(self) -> None:
        super().__init__(NAME)
        self.uid = uuid.uuid4().hex[:8]  # 描画先の名前を登録ごとに固有にする
        self.ops = [
            _ClearOp(f"{NAME}_clearColor", self, "color"),
            _ClearOp(f"{NAME}_clearId", self, "id"),
            _SceneOp(f"{NAME}_scene", self, shaded=True),
            _QuadOp(f"{NAME}_lines", self),
            _SceneOp(f"{NAME}_ui", self, shaded=False),
            _HudOp(self),
            _PresentOp(f"{NAME}_present", self),
        ]
        self.index = 0
        self.empty_selection = om.MSelectionList()
        self.targets: dict[str, Any] = {}
        self.size = (0, 0)
        self.background = ((0.36, 0.36, 0.36), (0.36, 0.36, 0.36), False)

    @lifecycle.guarded(fallback=lambda self: omr.MRenderer.kDirectX11)
    def supportedDrawAPIs(self):  # noqa: N802
        return omr.MRenderer.kDirectX11

    @lifecycle.guarded("T-Drive Toon")
    def uiName(self):  # noqa: N802
        return "T-Drive Toon（画面上の線）"

    @lifecycle.guarded(None)
    def setup(self, destination) -> None:
        if cmds.displayPref(query=True, displayGradient=True):
            self.background = (
                tuple(cmds.displayRGBColor("backgroundTop", query=True)),
                tuple(cmds.displayRGBColor("backgroundBottom", query=True)),
                True,
            )
        else:
            bg = tuple(cmds.displayRGBColor("background", query=True))
            self.background = (bg, bg, False)
        size = tuple(omr.MRenderer.outputTargetSize())
        if size != self.size or not self.targets:
            self.release()
            mgr = omr.MRenderer.getRenderTargetManager()
            w, h = size
            formats = {
                "color": omr.MRenderer.kR16G16B16A16_FLOAT,
                "id": omr.MRenderer.kR16G16B16A16_FLOAT,
                "depth": omr.MRenderer.kD24S8,
                "out": omr.MRenderer.kR8G8B8A8_UNORM,
            }
            for key, fmt in formats.items():
                desc = omr.MRenderTargetDescription(f"{NAME}_{key}_{self.uid}", w, h, 1, fmt, 1, False)
                self.targets[key] = mgr.acquireRenderTarget(desc)
            self.size = size

    def release(self) -> None:
        mgr = omr.MRenderer.getRenderTargetManager()
        for t in self.targets.values():
            if t is not None:
                mgr.releaseRenderTarget(t)
        self.targets = {}

    @lifecycle.guarded(None)
    def cleanup(self) -> None:
        self.index = 0

    @lifecycle.guarded(False)
    def startOperationIterator(self) -> bool:  # noqa: N802
        self.index = 0
        return True

    @lifecycle.guarded(None)
    def renderOperation(self):  # noqa: N802
        return self.ops[self.index] if self.index < len(self.ops) else None

    @lifecycle.guarded(False)
    def nextRenderOperation(self) -> bool:  # noqa: N802
        self.index += 1
        return self.index < len(self.ops)


_override: _Override | None = None

def set_param(shader, name: str, value: Any) -> None:
    """シェーダーパラメータを**型を確かめて**設定する（int を float に渡して kInvalidParameter になった再発防止）。"""
    si = omr.MShaderInstance
    kind = shader.parameterType(name)
    vectors = {si.kFloat2: 2, si.kFloat3: 3, si.kFloat4: 4}
    if kind == si.kFloat:
        shader.setParameter(name, float(value))
    elif kind == si.kInteger:
        shader.setParameter(name, int(value))
    elif kind == si.kBoolean:
        shader.setParameter(name, bool(value))
    elif kind in vectors:
        v = [float(x) for x in value]
        if len(v) != vectors[kind]:
            raise ValueError(f"{name}: {vectors[kind]} 要素のところに {len(v)} 要素")
        shader.setParameter(name, v)
    elif kind in (si.kTexture2, si.kSampler):
        shader.setParameter(name, value)
    else:
        raise ValueError(f"シェーダーに {name} が無い、または未対応の型（{kind}）")


def line_radius(width_px: float, screen_height: float) -> float:
    """docs/03 §10.2（Toon_LineRadius と同じ）: 境界の両側に r 画素ずつ。

    必ず float で返す（round() は int を返し、float のシェーダーパラメータへ渡すと kInvalidParameter になっていた）。
    """
    return float(max(1.0, round(float(width_px) * float(screen_height) / 1080.0 * 0.5)))


def register() -> None:
    """Override を登録する（ツールのリロードで古い登録が残っていれば置き換える）。"""
    global _override
    old = omr.MRenderer.findRenderOverride(NAME)
    if old is not None:
        _release_registered(old)
        omr.MRenderer.deregisterOverride(old)
    _override = _Override()
    # Maya は登録した Python オブジェクトを保持しない → リロードで解放されると Maya が落ちる（lifecycle.py）
    lifecycle.keep_alive(_override)
    lifecycle.on_reload(unregister)
    omr.MRenderer.registerOverride(_override)


def unregister() -> None:
    global _override
    for panel in cmds.getPanel(type="modelPanel") or []:
        if cmds.modelEditor(panel, query=True, rendererOverrideName=True) == NAME:
            cmds.modelEditor(panel, edit=True, rendererOverrideName="")
    old = omr.MRenderer.findRenderOverride(NAME)
    if old is not None:
        _release_registered(old)
        omr.MRenderer.deregisterOverride(old)
    _override = None


def _release_registered(ov) -> None:
    release = getattr(ov, "release", None)
    if callable(release):
        release()


def is_applied(panel: str) -> bool:
    return bool(panel) and cmds.modelEditor(panel, query=True, rendererOverrideName=True) == NAME


def update(panel: str | None, inner: dict | None, outer: dict | None, tonemap: int, visible: bool) -> None:
    """線の設定を渡し、必要ならパネルに Override を割り当てる / 外す。

    inner / outer: {"width": px@1080p, "color": リニア RGB} または None（その線を使わない）。
    """
    _params.update(inner=inner, outer=outer, tonemap=int(tonemap))
    want = visible and (inner is not None or outer is not None)
    if want and panel:
        if omr.MRenderer.findRenderOverride(NAME) is None or _override is None:
            register()
        if not is_applied(panel):
            cmds.modelEditor(panel, edit=True, rendererOverrideName=NAME)
        cmds.refresh(currentView=True)
    elif not want:
        for p in cmds.getPanel(type="modelPanel") or []:  # 以前に割り当てた別のパネルからも外す
            if is_applied(p):
                cmds.modelEditor(p, edit=True, rendererOverrideName="")
