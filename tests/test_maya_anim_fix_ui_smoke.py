"""mayapy で tests/maya/anim_fix_ui_smoke.py を実行する（実機確認の修正チケット AN-F の画面まわり。Maya 2026 がある環境のみ）。"""

import pytest

from anim_smoke_runner import MAYA_AVAILABLE, run_smoke


@pytest.mark.skipif(not MAYA_AVAILABLE, reason="mayapy なし / スキップ指定")
def test_maya_anim_fix_ui_smoke():
    run_smoke("anim_fix_ui_smoke.py")
