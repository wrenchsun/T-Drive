"""メインメニュー「T-Drive」の Toon 用の項目（Toon 表示 ON/OFF）。メニュー本体は tdrive.menu（F0-1 で移した）。"""

from __future__ import annotations

from maya import cmds

from tdrive.menu import MENU_NAME, install  # noqa: F401  既存の呼び出し（tdrive_toon.menu.install）を保つ


def toggle_preview() -> None:
    from tdrive_toon import preview, session

    s = session.current()
    if s.look is None:
        cmds.warning("T-Drive Toon: Look が開かれていません（エディタで 新規 / 開く）")
        return
    s.set_preview(not preview.is_active())
