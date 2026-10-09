"""Maya メインメニュー Tools › T-Drive（E-1。Unity 側の Tools › T-Drive と揃える）。

「Tools」は他のツールと共用するので、あれば再利用し、無ければ作る（消さない）。
共通の項目（エディタ・プロジェクト・リロード・マニュアル・更新・MCP ポート）はその直下に、
Toon だけの項目はサブメニュー「Toon」に置く（docs/15 §2.1）。
"""

from __future__ import annotations

from maya import cmds, mel

MENU_NAME = "TDriveMenu"  # Tools の中のサブメニュー（menuItem）。以前はトップメニューの名前だった
OLD_MENU_NAME = "TDriveToonMenu"  # 「T-Drive Toon」だった頃のトップメニュー
TOOLS_MENU_NAME = "TDriveToolsMenu"  # 「Tools」が無かったときに作るトップメニュー
TOOLS_LABEL = "Tools"


def _find_tools_menu(main: str) -> str | None:
    for m in cmds.window(main, query=True, menuArray=True) or []:
        if cmds.menu(m, query=True, label=True) == TOOLS_LABEL:
            return m
    return None


def install() -> None:
    # 古いトップメニュー「T-Drive」「T-Drive Toon」と、前回のサブメニューを消す
    if cmds.menu(MENU_NAME, exists=True):
        cmds.deleteUI(MENU_NAME)
    if cmds.menuItem(MENU_NAME, exists=True):
        cmds.deleteUI(MENU_NAME)
    if cmds.menu(OLD_MENU_NAME, exists=True):
        cmds.deleteUI(OLD_MENU_NAME)
    main = mel.eval("$tmp = $gMainWindow")
    tools = _find_tools_menu(main)
    if tools is None:
        if cmds.menu(TOOLS_MENU_NAME, exists=True):
            tools = TOOLS_MENU_NAME
        else:
            tools = cmds.menu(TOOLS_MENU_NAME, label=TOOLS_LABEL, parent=main, tearOff=True)
    cmds.setParent(tools, menu=True)
    cmds.menuItem(MENU_NAME, label="T-Drive", subMenu=True, tearOff=True)
    cmds.menuItem(label="エディタ", command=lambda *_: _shell().show())
    cmds.menuItem(label="プロジェクトを選ぶ…", command=lambda *_: _choose_project())
    cmds.menuItem(divider=True)
    cmds.menuItem(label="Toon", subMenu=True, tearOff=True)
    cmds.menuItem(label="Toon 表示 ON/OFF", command=lambda *_: _toon_menu().toggle_preview())
    cmds.setParent(MENU_NAME, menu=True)
    cmds.menuItem(label="ツールをリロード", command=lambda *_: _reload())
    cmds.menuItem(divider=True)
    cmds.menuItem(label="マニュアルを開く", command=lambda *_: _open_manual())
    cmds.menuItem(label="更新…", command=lambda *_: _update_window())
    cmds.menuItem(divider=True)
    cmds.menuItem(label="MCP ポートを開く", command=lambda *_: _bridge().open_port())
    cmds.menuItem(label="MCP ポートを閉じる", command=lambda *_: _bridge().close_port())
    cmds.setParent("..", menu=True)


def _choose_project() -> None:
    from tdrive import project
    from tdrive_toon import session  # プロジェクトを選ぶとテクスチャのパスを組み直す（今は Toon のセッションが持つ）

    picked = cmds.fileDialog2(
        fileMode=3, caption="プロジェクトフォルダ（Look・出力を置く場所）", startingDirectory=project.root().as_posix()
    )
    if picked:
        root = session.choose_project(picked[0])
        cmds.inViewMessage(amg=f"T-Drive: プロジェクト <hl>{root.as_posix()}</hl>", pos="topCenter", fade=True)


def _open_manual() -> None:
    import webbrowser

    from tdrive import REPO_ROOT

    webbrowser.open((REPO_ROOT / "docs" / "DesignerManual" / "Readme.html").as_uri())


def _update_window() -> None:
    from tdrive import ui_update

    ui_update.show()


def _shell():
    from tdrive import shell

    return shell


def _toon_menu():
    from tdrive_toon import menu

    return menu


def _bridge():
    from tdrive import mcp_bridge

    return mcp_bridge


def _reload() -> None:
    import runpy

    from tdrive import REPO_ROOT

    runpy.run_path(str(REPO_ROOT / "maya" / "mcp_scripts" / "reload_tdrive.py"))
