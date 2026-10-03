"""T-Drive Toon: Maya 2026 用セルルック キャラクター Look Development ツール。

Maya 非依存モジュール（params / look / parts_rules）と、Maya 依存モジュール
（parts / preview / ab / ui / menu）に分かれる。Maya 非依存側は tests/ から単体テストする。
共通部分（lifecycle / project / updater / ui_update / mcp_bridge）は tdrive に移り、同名の転送モジュールを残している。
"""

from tdrive import REPO_ROOT, __version__  # noqa: F401  既存の import（tdrive_toon.REPO_ROOT）を保つ
