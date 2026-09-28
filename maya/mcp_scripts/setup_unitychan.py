"""テスト素体（UnityChan）をセットアップして Toon 表示にする（MCP script.execute 用）。

1. assets/unitychan/unitychan_test.ma を開く（無ければ tools/fixtures で生成）
2. 部位を自動登録 → 環境プロファイル ms2026_ingame → Toon 表示

未保存の変更があるシーンでは何もしない（ユーザーの作業を消さない）。
"""

import sys
from pathlib import Path

from maya import cmds

from tdrive_toon import REPO_ROOT, environment, preview, session

FIXTURE = REPO_ROOT / "assets" / "unitychan" / "unitychan_test.ma"

if cmds.file(query=True, modified=True):
    raise RuntimeError("未保存の変更があります。保存してから実行してください（作業を消さないため中止）")

if not FIXTURE.exists():
    sys.path.insert(0, str(REPO_ROOT / "tools" / "fixtures"))
    import build_unitychan_scene

    build_unitychan_scene.build()

cmds.file(str(FIXTURE), open=True, force=True)
s = session.current()
s.new("unitychan", "assets/unitychan/unitychan_test.ma")
registered = s.auto_register()
warnings = preview.use_profile("ms2026_ingame")
s.show("base")
environment.prepare_panel()
environment.frame_camera(environment.load_profile("ms2026_ingame"), 0.0, target="all")
print(f"registered: {registered}")
for w in warnings + environment.parity_problems():
    print(f"WARNING: {w}")
