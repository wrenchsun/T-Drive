"""T-Drive Toon モジュールの Maya 起動時処理。

- MCP 用の commandPort を localhost:7001 で開く（環境変数 TDRIVE_MCP_PORT=0 で無効化）
- メインメニュー「T-Drive Toon」を追加（エディタ実装後。docs/tasks.md 1-4）
"""

import maya.utils


def _tdrive_startup():
    from tdrive_toon import mcp_bridge

    mcp_bridge.open_from_env()
    try:
        from tdrive_toon import menu
    except ImportError:
        return  # エディタ未実装の段階ではメニューを出さない
    menu.install()


maya.utils.executeDeferred(_tdrive_startup)
