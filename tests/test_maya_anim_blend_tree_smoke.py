"""mayapy で tests/maya/anim_blend_tree_smoke.py を実行する（ブレンドツリー（AN-11-2 / AN-11-3）。ヘッドレス。Maya 2026 がある環境のみ）。"""

import pytest

from anim_smoke_runner import MAYA_AVAILABLE, run_smoke


@pytest.mark.skipif(not MAYA_AVAILABLE, reason="mayapy なし / スキップ指定")
def test_maya_anim_blend_tree_smoke():
    run_smoke("anim_blend_tree_smoke.py")
