"""Maya MCP (GG_MayaMCP) をこのリポジトリ向けの設定で起動するランチャー。

.mcp.json から ``uv run --with maya-mcp==<ver>`` 経由で呼ばれる。
maya-mcp は MAYA_MCP_SCRIPT_DIRS に絶対パスを要求するため、
リポジトリの位置からパスを解決して環境変数を設定してからサーバーを起動する。

stdout は MCP の stdio トランスポートが使うので、ここでは何も print しないこと。
"""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# MCP の script.execute で実行を許可するディレクトリ（承認済みスクリプト置き場）
os.environ.setdefault("MAYA_MCP_SCRIPT_DIRS", str(REPO_ROOT / "maya" / "mcp_scripts"))

from maya_mcp.server import main  # noqa: E402  (環境変数設定後に import する)

if __name__ == "__main__":
    main()
