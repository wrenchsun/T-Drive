"""mayapy で tests/maya/facial_anim_smoke.py を実行する（Maya 2026 がある環境のみ。FacialController のアニメ読み込み・土台の表情・他のデータからコピー）。

画面なし（QT_QPA_PLATFORM=offscreen）で Qt のウィジェットを作って操作する。スキップ: 環境変数 TDRIVE_SKIP_MAYA=1。
assets/shizuku/facial_anims があれば、実データの .anim を全部読むところまで確かめる（無ければその部分は飛ばす）。
"""

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MAYAPY = Path(r"C:\Program Files\Autodesk\Maya2026\bin\mayapy.exe")


@pytest.mark.skipif(not MAYAPY.exists() or os.environ.get("TDRIVE_SKIP_MAYA") == "1", reason="mayapy なし / スキップ指定")
def test_maya_facial_anim_smoke():
    env = dict(os.environ, PYTHONIOENCODING="utf-8", TDRIVE_ROOT=ROOT.as_posix(), MAYA_DISABLE_CER="1", QT_QPA_PLATFORM="offscreen")
    r = subprocess.run([str(MAYAPY), str(ROOT / "tests/maya/facial_anim_smoke.py")], capture_output=True, env=env, timeout=900)
    out = r.stdout.decode("utf-8", errors="replace")
    lines = [l for l in out.splitlines() if l.startswith("SMOKE") or l.startswith("    ")]
    assert r.returncode == 0, "\n".join(lines) or out[-3000:] + r.stderr.decode("utf-8", errors="replace")[-2000:]
