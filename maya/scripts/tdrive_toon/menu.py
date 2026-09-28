"""Maya メインメニュー「T-Drive Toon」。"""

from __future__ import annotations

from maya import cmds, mel

MENU_NAME = "TDriveToonMenu"


def install() -> None:
    if cmds.menu(MENU_NAME, exists=True):
        cmds.deleteUI(MENU_NAME)
    main = mel.eval("$tmp = $gMainWindow")
    cmds.menu(MENU_NAME, label="T-Drive Toon", parent=main, tearOff=True)
    cmds.menuItem(label="エディタ", command=lambda *_: _ui().show())
    cmds.menuItem(divider=True)
    cmds.menuItem(label="Toon 表示 ON/OFF", command=lambda *_: _toggle_preview())
    cmds.menuItem(label="ツールをリロード", command=lambda *_: _reload())
    cmds.menuItem(divider=True)
    cmds.menuItem(label="MCP ポートを開く", command=lambda *_: _bridge().open_port())
    cmds.menuItem(label="MCP ポートを閉じる", command=lambda *_: _bridge().close_port())


def _ui():
    from tdrive_toon import ui

    return ui


def _bridge():
    from tdrive_toon import mcp_bridge

    return mcp_bridge


def _toggle_preview() -> None:
    from tdrive_toon import preview, session

    s = session.current()
    if s.look is None:
        cmds.warning("T-Drive Toon: Look が開かれていません（エディタで 新規 / 開く）")
        return
    s.set_preview(not preview.is_active())


def _reload() -> None:
    import runpy

    from tdrive_toon import REPO_ROOT

    runpy.run_path(str(REPO_ROOT / "maya" / "mcp_scripts" / "reload_tdrive.py"))
