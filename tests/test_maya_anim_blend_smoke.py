"""mayapy で tests/maya/anim_blend_smoke.py を実行する（ブレンド・遷移（AN-4 / AN-5）。ヘッドレス。Maya 2026 がある環境のみ）。"""

import pytest

from anim_smoke_runner import MAYA_AVAILABLE, run_smoke


@pytest.mark.skipif(not MAYA_AVAILABLE, reason="mayapy なし / スキップ指定")
def test_maya_anim_blend_smoke():
    run_smoke("anim_blend_smoke.py")
