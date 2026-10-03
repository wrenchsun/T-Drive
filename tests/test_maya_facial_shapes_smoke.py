"""mayapy で tests/maya/facial_shapes_smoke.py を実行する（Maya 2026 がある環境のみ。FacialController のシェイプ作成支援（F2。彫る・左右・ミラー・中間・組み合わせ・写す・整理 + シェイプタブ））。

画面なし（QT_QPA_PLATFORM=offscreen）でシェイプタブも操作する。スキップ: 環境変数 TDRIVE_SKIP_MAYA=1。スクリーンショット: TDRIVE_UI_SHOT_DIR。
"""

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MAYAPY = Path(r"C:\Program Files\Autodesk\Maya2026\bin\mayapy.exe")


@pytest.mark.skipif(not MAYAPY.exists() or os.environ.get("TDRIVE_SKIP_MAYA") == "1", reason="mayapy なし / スキップ指定")
def test_maya_facial_shapes_smoke():
    # MAYA_DISABLE_CER: 裏の mayapy が落ちてもクラッシュ報告の画面をデザイナーの画面に出さない（2026-09-28）
    env = dict(os.environ, PYTHONIOENCODING="utf-8", TDRIVE_ROOT=ROOT.as_posix(), MAYA_DISABLE_CER="1", QT_QPA_PLATFORM="offscreen")
    r = subprocess.run([str(MAYAPY), str(ROOT / "tests/maya/facial_shapes_smoke.py")], capture_output=True, env=env, timeout=900)
    out = r.stdout.decode("utf-8", errors="replace")
    lines = [l for l in out.splitlines() if l.startswith("SMOKE") or l.startswith("    ")]
    assert r.returncode == 0, "\n".join(lines) or out[-3000:]
