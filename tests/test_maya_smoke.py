"""mayapy で tests/maya/smoke.py を実行する（Maya 2026 がある環境のみ。約 1 分）。

スキップ: 環境変数 TDRIVE_SKIP_MAYA=1
"""

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MAYAPY = Path(r"C:\Program Files\Autodesk\Maya2026\bin\mayapy.exe")


@pytest.mark.skipif(not MAYAPY.exists() or os.environ.get("TDRIVE_SKIP_MAYA") == "1", reason="mayapy なし / スキップ指定")
def test_maya_smoke():
    env = dict(os.environ, PYTHONIOENCODING="utf-8", TDRIVE_ROOT=ROOT.as_posix())
    r = subprocess.run([str(MAYAPY), str(ROOT / "tests/maya/smoke.py")], capture_output=True, env=env, timeout=600)
    out = r.stdout.decode("utf-8", errors="replace")
    lines = [l for l in out.splitlines() if l.startswith("SMOKE") or l.startswith("    ")]
    assert r.returncode == 0, "\n".join(lines) or out[-3000:]
