"""Animation（Phase AN）の mayapy スモークを実行する共通部分（tests/test_maya_anim_*_smoke.py が使う）。

docs/21 TS-17: 終了コードだけでなく、出力に `SMOKE RESULT n/n passed` の行があり、確認の件数が 0 でなく、全部通ったことも見る
（スクリプトが途中で早く return しても、通ったことにならない）。
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAYAPY = Path(r"C:\Program Files\Autodesk\Maya2026\bin\mayapy.exe")
MAYA_AVAILABLE = MAYAPY.exists() and os.environ.get("TDRIVE_SKIP_MAYA") != "1"


def run_smoke(script: str, timeout: int = 900, args: tuple[str, ...] = ()) -> str:
    """`tests/maya/<script>` を mayapy で実行して、失敗なら AssertionError。標準出力を返す。"""
    env = dict(os.environ, PYTHONIOENCODING="utf-8", TDRIVE_ROOT=ROOT.as_posix(), MAYA_DISABLE_CER="1", QT_QPA_PLATFORM="offscreen")
    r = subprocess.run([str(MAYAPY), str(ROOT / "tests/maya" / script), *args], capture_output=True, env=env, timeout=timeout)
    out = r.stdout.decode("utf-8", errors="replace")
    lines = [l for l in out.splitlines() if l.startswith("SMOKE") or l.startswith("    ")]
    assert r.returncode == 0, "\n".join(lines) or out[-3000:] + r.stderr.decode("utf-8", errors="replace")[-2000:]
    m = re.search(r"SMOKE RESULT (\d+)/(\d+) passed", out)
    assert m and int(m.group(2)) > 0 and m.group(1) == m.group(2), "SMOKE RESULT の行が無い／確認の件数が 0／全部は通っていない:\n" + out[-1500:]
    return out
