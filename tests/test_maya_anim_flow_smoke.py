"""mayapy で tests/maya/anim_flow_smoke.py を実行する（一周（AN-9。MisotenChara_Rig.ma があればそれ、無ければミニリグ）。ヘッドレス。Maya 2026 がある環境のみ）。"""

import pytest

from anim_smoke_runner import MAYA_AVAILABLE, run_smoke


@pytest.mark.skipif(not MAYA_AVAILABLE, reason="mayapy なし / スキップ指定")
def test_maya_anim_flow_smoke():
    run_smoke("anim_flow_smoke.py")
