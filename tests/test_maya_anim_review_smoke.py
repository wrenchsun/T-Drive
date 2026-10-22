"""mayapy で tests/maya/anim_review_*_smoke.py を実行する（docs/21 のコードレビュー対応の回帰。Maya 2026 がある環境のみ）。

TS-17: 終了コードだけでなく、`SMOKE RESULT` の行があり、確認の件数が 0 でないことも見る（途中で早く return しても通らない）。
"""

import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MAYAPY = Path(r"C:\Program Files\Autodesk\Maya2026\bin\mayapy.exe")
SMOKES = sorted(p.name for p in (ROOT / "tests/maya").glob("anim_review_*_smoke.py"))


@pytest.mark.skipif(not MAYAPY.exists() or os.environ.get("TDRIVE_SKIP_MAYA") == "1", reason="mayapy なし / スキップ指定")
@pytest.mark.parametrize("name", SMOKES)
def test_maya_anim_review_smoke(name):
    env = dict(os.environ, PYTHONIOENCODING="utf-8", TDRIVE_ROOT=ROOT.as_posix(), MAYA_DISABLE_CER="1", QT_QPA_PLATFORM="offscreen")
    r = subprocess.run([str(MAYAPY), str(ROOT / "tests/maya" / name)], capture_output=True, env=env, timeout=1200)
    out = r.stdout.decode("utf-8", errors="replace")
    lines = [l for l in out.splitlines() if l.startswith("SMOKE") or l.startswith("    ")]
    assert r.returncode == 0, "\n".join(lines) or out[-3000:] + r.stderr.decode("utf-8", errors="replace")[-2000:]
    m = re.search(r"SMOKE RESULT (\d+)/(\d+) passed", out)
    assert m and int(m.group(2)) > 0 and m.group(1) == m.group(2), out[-1500:]
