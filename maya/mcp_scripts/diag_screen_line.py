"""画面上の線（Render Override）の表示が崩れる環境の切り分け（2026-10-15 の不具合: 絵が白っぽく半透明に抜け、巨大な「前」が重なる）。

Maya のスクリプトエディタ（Python）にこのファイルの中身を貼って実行するか、MCP の script.execute で実行する。
やること:
1. 環境（Maya の版・GPU・VP2 のレンダリングエンジン・AA・浮動小数点描画先・カラーマネジメント・DPI・HUD の状態）を出力
2. 今のモデルパネルで、Override を外した状態 / 付けた状態 / HUD を消した状態 の 3 枚をキャプチャ（%TEMP%/tdrive_diag_*.png）
出力のテキストと 3 枚の画像を送ってもらう。
"""

import os
import tempfile

from maya import cmds

try:
    import maya.api.OpenMayaRender as omr
except Exception:  # noqa: BLE001
    omr = None

OUT_DIR = tempfile.gettempdir()
NAME = "tdToonScreenLine"


def _q(fn, *a, **k):
    try:
        return fn(*a, **k)
    except Exception as exc:  # noqa: BLE001
        return f"<err {exc}>"


def _panel():
    p = cmds.getPanel(withFocus=True)
    if cmds.getPanel(typeOf=p) != "modelPanel":
        for p in cmds.getPanel(type="modelPanel") or []:
            if cmds.modelEditor(p, query=True, rendererOverrideName=True) == NAME:
                return p
        p = (cmds.getPanel(visiblePanels=True) or ["modelPanel4"])[0]
    return p


def environment():
    info = {
        "maya": _q(cmds.about, installedVersion=True),
        "api": _q(cmds.about, apiVersion=True),
        "os": _q(cmds.about, operatingSystemVersion=True),
        "vp2_engine": _q(cmds.optionVar, q="vp2RenderingEngine"),
        "gpu": _q(cmds.ogs, deviceInformation=True),
        "hwRenderer": {
            k: _q(cmds.getAttr, f"hardwareRenderingGlobals.{k}")
            for k in ("multiSampleEnable", "multiSampleCount", "ssaoEnable", "motionBlurEnable", "floatingPointRTEnable",
                      "floatingPointRTFormat", "transparencyAlgorithm", "lineAAEnable", "renderDepthOfField", "gammaCorrectionEnable")
        },
        "colorManagement": {
            "enabled": _q(cmds.colorManagementPrefs, q=True, cmEnabled=True),
            "viewTransform": _q(cmds.colorManagementPrefs, q=True, viewTransformName=True),
            "outputTransform": _q(cmds.colorManagementPrefs, q=True, outputTransformEnabled=True),
        },
        "displayPref_gradient": _q(cmds.displayPref, q=True, displayGradient=True),
        "hud": {
            "cameraNames": _q(cmds.headsUpDisplay, "HUDCameraNames", q=True, visible=True) if cmds.headsUpDisplay("HUDCameraNames", exists=True) else None,
            "viewCube": _q(cmds.viewManip, q=True, visible=True),
            "inViewMessage": _q(cmds.optionVar, q="inViewMessageEnable"),
        },
    }
    try:
        from PySide6 import QtWidgets

        app = QtWidgets.QApplication.instance()
        info["qt"] = {
            "screens": [(s.name(), s.size().width(), s.size().height(), s.devicePixelRatio(), s.logicalDotsPerInch()) for s in app.screens()],
            "env_QT_SCALE": {k: v for k, v in os.environ.items() if "QT_" in k and ("SCALE" in k or "DPI" in k)},
            "MAYA_INTERFACE_SCALING": os.environ.get("MAYA_UI_SCALE") or os.environ.get("MAYA_INTERFACE_SCALING"),
        }
    except Exception as exc:  # noqa: BLE001
        info["qt"] = f"<err {exc}>"
    if omr is not None:
        info["renderer"] = {
            "drawAPI": _q(omr.MRenderer.drawAPI),
            "outputTargetSize": _q(lambda: tuple(omr.MRenderer.outputTargetSize())),
            "override_registered": _q(lambda: omr.MRenderer.findRenderOverride(NAME) is not None),
        }
    p = _panel()
    info["panel"] = {
        "name": p,
        "control_size": _q(lambda: (cmds.control(p, q=True, width=True), cmds.control(p, q=True, height=True))),
        "rendererOverrideName": _q(cmds.modelEditor, p, q=True, rendererOverrideName=True),
        "rendererName": _q(cmds.modelEditor, p, q=True, rendererName=True),
        "camera": _q(cmds.modelEditor, p, q=True, camera=True),
        "hud": _q(cmds.modelEditor, p, q=True, headsUpDisplay=True),
        "displayTextures": _q(cmds.modelEditor, p, q=True, displayTextures=True),
    }
    return info


def capture(tag: str, panel: str, w: int = 1280, h: int = 720) -> str:
    path = os.path.join(OUT_DIR, f"tdrive_diag_{tag}.png").replace("\\", "/")
    cmds.setFocus(panel)
    cmds.refresh(currentView=True)
    cmds.playblast(
        completeFilename=path, frame=cmds.currentTime(q=True), format="image", compression="png", viewer=False,
        showOrnaments=True, percent=100, quality=100, width=w, height=h, forceOverwrite=True, editorPanelName=panel,
    )
    return path


def run():
    info = environment()
    print("==== T-Drive 画面上の線 診断 ====")
    for k, v in info.items():
        print(f"{k}: {v}")
    p = info["panel"]["name"]
    had = cmds.modelEditor(p, q=True, rendererOverrideName=True) == NAME
    hud = cmds.modelEditor(p, q=True, headsUpDisplay=True)
    shots = []
    try:
        cmds.modelEditor(p, e=True, rendererOverrideName="")
        shots.append(capture("1_no_override", p))
        if omr is not None and omr.MRenderer.findRenderOverride(NAME) is not None:
            cmds.modelEditor(p, e=True, rendererOverrideName=NAME)
            shots.append(capture("2_override", p))
            cmds.modelEditor(p, e=True, headsUpDisplay=False)
            shots.append(capture("3_override_no_hud", p))
        else:
            print("Override が登録されていない（線の機能がオフ）ので、2 と 3 は撮っていない。ルックで線を有効にしてから実行")
    finally:
        cmds.modelEditor(p, e=True, headsUpDisplay=hud)
        cmds.modelEditor(p, e=True, rendererOverrideName=NAME if had else "")
    print("captures:", shots)
    print("==== ここまでのテキストと画像を送ってください ====")


run()
