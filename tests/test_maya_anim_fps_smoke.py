"""mayapy で tests/maya/anim_fps_smoke.py を実行する（fps を直したときのクリップ範囲の追従。AN-F-13。Maya 2026 がある環境のみ）。"""

import pytest

from anim_smoke_runner import MAYA_AVAILABLE, run_smoke


@pytest.mark.skipif(not MAYA_AVAILABLE, reason="mayapy なし / スキップ指定")
def test_maya_anim_fps_smoke():
    run_smoke("anim_fps_smoke.py")
