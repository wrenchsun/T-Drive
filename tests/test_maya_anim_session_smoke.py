"""mayapy で tests/maya/anim_session_smoke.py を実行する（アニメーション作業台の骨格。ヘッドレス。Maya 2026 がある環境のみ）。"""

import pytest

from anim_smoke_runner import MAYA_AVAILABLE, run_smoke


@pytest.mark.skipif(not MAYA_AVAILABLE, reason="mayapy なし / スキップ指定")
def test_maya_anim_session_smoke():
    run_smoke("anim_session_smoke.py")
