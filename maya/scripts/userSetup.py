"""T-Drive Toon モジュールの Maya 起動時処理（画面ありの Maya だけ）。

- MCP 用の commandPort を localhost:7001 で開く（環境変数 TDRIVE_MCP_PORT=0 で無効化）
- シーンを開いたら記録された Look を自動で開く（SceneOpened）
- メインメニュー「T-Drive Toon」を追加

mayapy / バッチ（Unity 出力の別プロセス・スモークテスト）では何もしない。
そこでポートを開くと、画面ありの Maya が無いときに MCP の接続先が一時的な裏の Maya になってしまうため。
"""

import maya.utils
from maya import cmds


def _tdrive_startup():
    from tdrive_toon import mcp_bridge, menu, session

    session.load_project_preference()  # 選んだプロジェクトフォルダ（docs/13 §1）
    mcp_bridge.open_from_env()
    cmds.scriptJob(event=["SceneOpened", "from tdrive_toon import session; session.on_scene_opened()"], protected=True)
    menu.install()
    try:
        from tdrive_toon import ui_update

        ui_update.startup_check()  # 更新後の確認待ちの案内・1 日 1 回の更新の確認（docs/13 §3）
    except Exception as exc:  # noqa: BLE001  起動を止めない
        print(f"[T-Drive] 更新の確認に失敗: {exc}")


if not cmds.about(batch=True):
    maya.utils.executeDeferred(_tdrive_startup)
