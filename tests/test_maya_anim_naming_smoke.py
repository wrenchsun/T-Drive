"""mayapy で tests/maya/anim_naming_smoke.py を実行する（命名タブ（AN-2）。ヘッドレス。Maya 2026 がある環境のみ）。"""

import pytest

from anim_smoke_runner import MAYA_AVAILABLE, run_smoke


@pytest.mark.skipif(not MAYA_AVAILABLE, reason="mayapy なし / スキップ指定")
def test_maya_anim_naming_smoke():
    run_smoke("anim_naming_smoke.py")
