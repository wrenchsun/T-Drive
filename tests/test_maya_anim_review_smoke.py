"""mayapy で tests/maya/anim_review_*_smoke.py を実行する（docs/21 のコードレビュー対応の回帰。Maya 2026 がある環境のみ）。"""

from pathlib import Path

import pytest

from anim_smoke_runner import MAYA_AVAILABLE, ROOT, run_smoke

SMOKES = sorted(p.name for p in (ROOT / "tests/maya").glob("anim_review_*_smoke.py"))


@pytest.mark.skipif(not MAYA_AVAILABLE, reason="mayapy なし / スキップ指定")
@pytest.mark.parametrize("name", SMOKES)
def test_maya_anim_review_smoke(name):
    run_smoke(name, timeout=1200)
