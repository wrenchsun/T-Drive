"""mayapy で tests/maya/facial_preview_smoke.py を実行する（Maya 2026 がある環境のみ。FacialController のカメラ連動プレビューと出力）。

スキップ: 環境変数 TDRIVE_SKIP_MAYA=1。shizuku の結合部分だけ飛ばす: TDRIVE_FACIAL_SHIZUKU=0
"""

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MAYAPY = Path(r"C:\Program Files\Autodesk\Maya2026\bin\mayapy.exe")


@pytest.mark.skipif(not MAYAPY.exists() or os.environ.get("TDRIVE_SKIP_MAYA") == "1", reason="mayapy なし / スキップ指定")
def test_maya_facial_preview_smoke():
    # MAYA_DISABLE_CER: 裏の mayapy が落ちてもクラッシュ報告の画面をデザイナーの画面に出さない（2026-09-28）
    env = dict(os.environ, PYTHONIOENCODING="utf-8", TDRIVE_ROOT=ROOT.as_posix(), MAYA_DISABLE_CER="1")
    r = subprocess.run([str(MAYAPY), str(ROOT / "tests/maya/facial_preview_smoke.py")], capture_output=True, env=env, timeout=1200)
    out = r.stdout.decode("utf-8", errors="replace")
    lines = [l for l in out.splitlines() if l.startswith("SMOKE") or l.startswith("    ")]
    assert r.returncode == 0, "\n".join(l for l in lines if "PASS" not in l) or out[-3000:]
