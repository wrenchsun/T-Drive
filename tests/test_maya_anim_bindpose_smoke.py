"""mayapy で tests/maya/anim_bindpose_smoke.py を実行する（モデル出力の骨がバインドポーズになる。AN-F-14。Maya 2026 がある環境のみ）。"""

import pytest

from anim_smoke_runner import MAYA_AVAILABLE, ROOT, run_smoke


@pytest.mark.skipif(not MAYA_AVAILABLE, reason="mayapy なし / スキップ指定")
def test_maya_anim_bindpose_smoke():
    run_smoke("anim_bindpose_smoke.py", args=("mini",))


@pytest.mark.skipif(not MAYA_AVAILABLE or not (ROOT / "assets/misoten/MisotenChara_Rig.ma").is_file(), reason="mayapy または本番リグ（Git 管理外）なし")
def test_maya_anim_bindpose_smoke_misoten():
    """本番リグ（読み込むだけ）でも、動かしたコントローラを戻して骨が bindPreMatrix と一致する。"""
    run_smoke("anim_bindpose_smoke.py", args=("misoten",))
