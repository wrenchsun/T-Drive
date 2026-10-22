"""mayapy で tests/maya/anim_compound_smoke.py を実行する（複合プラグでつながるジョイント = mGear 型の合成。AN-F-6。Maya 2026 がある環境のみ）。"""

import pytest

from anim_smoke_runner import MAYA_AVAILABLE, ROOT, run_smoke


@pytest.mark.skipif(not MAYA_AVAILABLE, reason="mayapy なし / スキップ指定")
def test_maya_anim_compound_smoke():
    run_smoke("anim_compound_smoke.py", args=("mini",))


@pytest.mark.skipif(not MAYA_AVAILABLE or not (ROOT / "assets/misoten/MisotenChara_Rig.ma").is_file(), reason="mayapy または本番リグ（Git 管理外）なし")
def test_maya_anim_compound_smoke_misoten():
    """本番リグ（読み込むだけ）でも合成が作られ、外すと複合プラグの接続に戻る。"""
    run_smoke("anim_compound_smoke.py", args=("misoten",))
