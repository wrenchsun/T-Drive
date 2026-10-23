"""「ツールをリロード」をまたぐ状態の引き継ぎの共通部分（Maya 非依存。`maya/mcp_scripts/reload_tdrive.py` が使う）。

リロード前のセッションが**古い形**（`export_state` / `import_state` が無い版）でも例外にせず、「引き継ぎなし」として理由を返す（AN-F-7）。
"""

from __future__ import annotations

from typing import Any


def export_state(session: Any) -> tuple[Any, str]:
    """`session.export_state()` の結果と、引き継げなかったときの理由（引き継げたら空）。

    戻り: (状態 | None, 理由)。状態が None で理由が空なら、開いているデータが無かっただけ。
    """
    fn = getattr(session, "export_state", None)
    if not callable(fn):
        return None, "リロード前の版は引き継ぎに対応していないため、引き継ぎなし（開き直してください）"
    try:
        return fn(), ""
    except Exception as exc:  # noqa: BLE001
        return None, f"引き継ぎなし: {exc}"


def import_state(session: Any, state: Any) -> tuple[bool, str]:
    """`session.import_state(state)` を呼ぶ。戻り: (復元したか, 理由)。"""
    fn = getattr(session, "import_state", None)
    if not callable(fn):
        return False, "新しい版が引き継ぎに対応していません"
    try:
        return bool(fn(state)), ""
    except Exception as exc:  # noqa: BLE001
        return False, f"引き継げませんでした: {exc}"
