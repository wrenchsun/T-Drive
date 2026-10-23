"""mayapy で tests/maya/anim_clipbake_smoke.py を実行する（複合プラグ駆動のリグのクリップ出力。AN-F-24。Maya 2026 がある環境のみ）。"""

import os
from pathlib import Path

import pytest

from anim_smoke_runner import MAYA_AVAILABLE, run_smoke


@pytest.mark.skipif(not MAYA_AVAILABLE, reason="mayapy なし / スキップ指定")
def test_maya_anim_clipbake_smoke():
    run_smoke("anim_clipbake_smoke.py", args=("mini",))


@pytest.mark.skipif(not MAYA_AVAILABLE or not Path(os.environ.get("TDRIVE_LIVE_SCENE", "")).is_file(), reason="mayapy または TDRIVE_LIVE_SCENE（実機のキー入りシーン）なし")
def test_maya_anim_clipbake_smoke_live():
    """実機のキー入りシーン（読むだけ）で、クリップ範囲どおりに 0〜長さへ出る。"""
    run_smoke("anim_clipbake_smoke.py", args=("live",))
