"""mayapy で tests/maya/anim_perf_smoke.py を実行する（合成の作成の速さと中止。AN-F-21。Maya 2026 がある環境のみ）。"""

import pytest

from anim_smoke_runner import MAYA_AVAILABLE, ROOT, run_smoke


@pytest.mark.skipif(not MAYA_AVAILABLE, reason="mayapy なし / スキップ指定")
def test_maya_anim_perf_smoke():
    run_smoke("anim_perf_smoke.py", args=("mini",))


@pytest.mark.skipif(not MAYA_AVAILABLE or not (ROOT / "assets/misoten/MisotenChara_Rig.ma").is_file(), reason="mayapy または本番リグ（Git 管理外）なし")
def test_maya_anim_perf_smoke_misoten():
    """本番リグ（読み込むだけ）で 113 フレームの合成が数秒以内。"""
    run_smoke("anim_perf_smoke.py", args=("misoten",))
