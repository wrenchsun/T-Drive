"""mayapy で tests/maya/facial_ui_smoke_c.py を実行する（Maya 2026 がある環境のみ。FacialController の統合: プレビューのグループ・出力タブ・T-20 の取り込み・変更のある点だけベイク）。

画面なし（QT_QPA_PLATFORM=offscreen）で Qt のウィジェットを作って操作する。Unity 向けの出力は裏の mayapy が走るので少し時間がかかる。
スキップ: 環境変数 TDRIVE_SKIP_MAYA=1。スクリーンショットを見たいときは環境変数 TDRIVE_UI_SHOT_DIR に出力先のフォルダを指定する。
"""

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MAYAPY = Path(r"C:\Program Files\Autodesk\Maya2026\bin\mayapy.exe")


@pytest.mark.skipif(not MAYAPY.exists() or os.environ.get("TDRIVE_SKIP_MAYA") == "1", reason="mayapy なし / スキップ指定")
def test_maya_facial_ui_smoke_c():
    # MAYA_DISABLE_CER: 裏の mayapy が落ちてもクラッシュ報告の画面をデザイナーの画面に出さない（2026-09-28）
    env = dict(os.environ, PYTHONIOENCODING="utf-8", TDRIVE_ROOT=ROOT.as_posix(), MAYA_DISABLE_CER="1", QT_QPA_PLATFORM="offscreen")
    r = subprocess.run([str(MAYAPY), str(ROOT / "tests/maya/facial_ui_smoke_c.py")], capture_output=True, env=env, timeout=1200)
    out = r.stdout.decode("utf-8", errors="replace")
    lines = [l for l in out.splitlines() if l.startswith("SMOKE") or l.startswith("    ")]
    assert r.returncode == 0, "\n".join(lines) or out[-3000:]
