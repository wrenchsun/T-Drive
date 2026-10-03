"""T-Drive（殻 tdrive・Toon tdrive_toon）の Maya 起動時処理（画面ありの Maya だけ）。

起動の最初（シーンを開く前）に行うもの — 遅らせると、起動と同時に開いたシーンでテクスチャ（$TDRIVE_PROJECT/…）が
読めずに真っ黒になり、Look も自動で開かない（2026-09-29 の不具合）:
- プロジェクトフォルダを決めて $TDRIVE_PROJECT を設定（docs/13 §1）
- シーンを開いたら記録された Look を開く（SceneOpened）/ シーンを保存したら Look も保存する（SceneSaved）

画面の準備ができてから行うもの（executeDeferred）:
- MCP 用の commandPort を localhost:7001 で開く（環境変数 TDRIVE_MCP_PORT=0 で無効化）
- メインメニュー「T-Drive」、更新の確認
- 起動と同時にシーンが開いていたら、Look を開いてテクスチャを読み込み直す

mayapy / バッチ（Unity 出力の別プロセス・スモークテスト）では何もしない。
そこでポートを開くと、画面ありの Maya が無いときに MCP の接続先が一時的な裏の Maya になってしまうため。
"""

import maya.utils
from maya import cmds


def _tdrive_early():
    from tdrive_toon import session

    session.load_project_preference()  # $TDRIVE_PROJECT（シーンのテクスチャのパスが参照する）
    cmds.scriptJob(event=["SceneOpened", "from tdrive_toon import session; session.on_scene_opened()"], protected=True)
    cmds.scriptJob(event=["SceneSaved", "from tdrive_toon import session; session.on_scene_saved()"], protected=True)


def _tdrive_startup():
    from tdrive import mcp_bridge, menu
    from tdrive_toon import session

    mcp_bridge.open_from_env()
    menu.install()
    if cmds.file(query=True, sceneName=True) and session.current().look is None:
        session.on_scene_opened()  # scriptJob より先にシーンが開いていた場合
    try:
        from tdrive import ui_update

        ui_update.startup_check()  # 更新後の確認待ちの案内・1 日 1 回の更新の確認（docs/13 §3）
    except Exception as exc:  # noqa: BLE001  起動を止めない
        print(f"[T-Drive] 更新の確認に失敗: {exc}")


if not cmds.about(batch=True):
    try:
        _tdrive_early()
    except Exception as exc:  # noqa: BLE001  Maya の起動を止めない
        print(f"[T-Drive] 起動時の準備に失敗: {exc}")
    maya.utils.executeDeferred(_tdrive_startup)
