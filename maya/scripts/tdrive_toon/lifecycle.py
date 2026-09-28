"""Maya に登録するものの寿命と、ツール内のエラーの通知（2026-09-28 の不具合の再発防止）。

Maya 非依存（maya を import しない）。守ること:

1. **Maya に Python のオブジェクト・関数を登録したら、`on_reload(後片付け)` も登録する**
   Maya は登録された Python オブジェクトを参照として保持しないことがある。ツールのリロードで古いモジュールと
   一緒に解放されると、Maya が解放済みのメモリを触って落ちる（Render Override で発生）。
   リロード（maya/mcp_scripts/reload_tdrive.py）は、モジュールを捨てる前に `run_reload_cleanups()` を呼ぶ。
   さらに `keep_alive(obj)` で、リロードの影響を受けない場所からも参照しておく。
   → tests/test_ui_patterns.py が、登録しているのに後片付けを登録していないモジュールを検出する
2. **Maya（VP2 など）から呼ばれるコールバックは `guarded(既定値)` で包む**
   例外は描画のたびに出続け、スクリプトエディタを見ないと気付けない。戻り値の int が Windows の C long
   （32 ビット符号付き）に収まらないと、Python の外で OverflowError になる（kExcludeAll で発生）。
   `guarded` は例外と範囲外の int を捕まえて 1 回だけ通知し、既定値を返す
3. **ツール内のエラーはエディタに表示する**（`report_error` / `install_error_hook`）
   Qt のスロットで起きた例外もスクリプトエディタに出るだけだった（機能タブのチェックが効かなかった）
"""

from __future__ import annotations

import sys
import traceback
from functools import wraps
from pathlib import Path
from typing import Any, Callable

PACKAGE_DIR = Path(__file__).resolve().parent
INT32_MIN, INT32_MAX = -(2**31), 2**31 - 1

_KEEPALIVE_ATTR = "_tdrive_toon_keepalive"  # sys の属性（リロードで消えない）
_ORIGINAL_HOOK_ATTR = "_tdrive_toon_original_excepthook"

_cleanups: list[Callable[[], None]] = []
_error_listeners: list[Callable[[str, str], None]] = []
_reported: set[str] = set()


# ---------------------------------------------------------------- 寿命
def on_reload(cleanup: Callable[[], None]) -> None:
    """ツールのリロードの前に呼ぶ後片付けを登録する（同じ関数は 1 回だけ）。"""
    if all(c is not cleanup for c in _cleanups):
        _cleanups.append(cleanup)


def run_reload_cleanups() -> list[str]:
    """登録された後片付けをすべて呼ぶ。失敗しても続け、失敗の内容を返す。"""
    errors = []
    for cleanup in list(_cleanups):
        try:
            cleanup()
        except Exception as exc:  # noqa: BLE001  リロード自体は止めない
            errors.append(f"{getattr(cleanup, '__qualname__', cleanup)}: {exc}")
    _cleanups.clear()
    return errors


def keep_alive(obj: Any) -> None:
    """Maya に登録したオブジェクトを、モジュールが捨てられても解放されない場所から参照する。"""
    held = getattr(sys, _KEEPALIVE_ATTR, None)
    if held is None:
        held = []
        setattr(sys, _KEEPALIVE_ATTR, held)
    held.append(obj)


# ---------------------------------------------------------------- コールバックの保護
def guarded(default: Any = None, name: str | None = None, fallback: Callable[..., Any] | None = None) -> Callable:
    """Maya から呼ばれるコールバックを包む。例外・32 ビットに収まらない int は 1 回だけ通知して default を返す。

    fallback を渡すと、失敗時は fallback(*args)（self から安全な値を作るとき。例: self.mClearOperation）を返す。
    """

    def deco(fn: Callable) -> Callable:
        label = name or fn.__qualname__

        @wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                result = fn(*args, **kwargs)
            except Exception:  # noqa: BLE001  描画を止めない
                report_error(f"{label} で例外", traceback.format_exc())
                return fallback(*args) if fallback else default
            if isinstance(result, int) and not isinstance(result, bool) and not INT32_MIN <= result <= INT32_MAX:
                report_error(f"{label} の戻り値が 32 ビットに収まらない", f"{result:#x}（Windows の C long に変換できない）")
                return fallback(*args) if fallback else default
            return result

        return wrapper

    return deco


# ---------------------------------------------------------------- エラーの通知
def add_error_listener(fn: Callable[[str, str], None]) -> None:
    if fn not in _error_listeners:
        _error_listeners.append(fn)


def remove_error_listener(fn: Callable[[str, str], None]) -> None:
    if fn in _error_listeners:
        _error_listeners.remove(fn)


def report_error(summary: str, detail: str = "", once: bool = True) -> None:
    """ツール内のエラーを通知する（スクリプトエディタに出し、エディタに表示）。once=True なら同じ内容は 1 回だけ。"""
    last_line = detail.strip().splitlines()[-1] if detail.strip() else ""
    key = f"{summary}\n{last_line}"
    if once and key in _reported:
        return
    _reported.add(key)
    sys.stderr.write(f"[T-Drive] エラー: {summary}\n{detail}\n")
    for fn in list(_error_listeners):
        try:
            fn(summary, detail)
        except Exception:  # noqa: BLE001  通知の失敗で連鎖させない
            pass


def is_tool_traceback(tb) -> bool:
    """トレースバックに tdrive_toon のコードが含まれるか。"""
    for frame, _lineno in traceback.walk_tb(tb):
        try:
            if Path(frame.f_code.co_filename).resolve().is_relative_to(PACKAGE_DIR):
                return True
        except (OSError, ValueError):
            continue
    return False


def install_error_hook() -> None:
    """Qt のスロットなどで起きた未処理の例外のうち、ツールのものをエディタに通知する（元の処理にも渡す）。

    リロードのたびに呼んでも重ならないよう、最初の元のフックを sys に覚えておく。
    """
    original = getattr(sys, _ORIGINAL_HOOK_ATTR, None)
    if original is None:
        original = sys.excepthook
        setattr(sys, _ORIGINAL_HOOK_ATTR, original)

    def hook(exc_type, exc, tb):
        try:
            if is_tool_traceback(tb):
                detail = "".join(traceback.format_exception(exc_type, exc, tb))
                report_error(f"{exc_type.__name__}: {exc}", detail, once=False)
        finally:
            original(exc_type, exc, tb)

    sys.excepthook = hook
