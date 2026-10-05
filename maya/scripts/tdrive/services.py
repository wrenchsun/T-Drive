"""ツール同士の小さな橋渡し（サービスの登録表）。Maya 非依存（maya を import しない）。

Toon から FacialController を直接 import しないで済むよう、提供する側のツールが名前付きで「プロバイダー」を登録し、
使う側は名前で問い合わせる。提供するツールが無い・壊れているときも、使う側は止まらない（available=False が返る）。

プロバイダーの約束（`facial_correction` の例）:
  state() -> dict   available（今、補正が掛かる仕掛けがあるか）/ enabled（オンか）/ changeable（ここから切り替えられるか）/ reason（利用者に見せる説明）
  set_enabled(on) -> dict   {"ok": bool, "message": str}

変更の通知: 提供側が状態の変化を `notify(name)` で知らせ、使う側は `subscribe(name, fn)` で受ける（タイマーで見張らない）。
購読は提供側の登録・解除とは独立（提供側が後から現れても届く）。
"""

from __future__ import annotations

import traceback
from typing import Any, Callable

from . import lifecycle

FACIAL_CORRECTION = "facial_correction"  # FacialController のカメラ連動の補正（プレビューの rig の enable）

NO_PROVIDER_REASON = "FacialController を読み込めていないので、顔の補正は掛かっていません"

_providers: dict[str, Any] = {}
_subscribers: dict[str, list[Callable[[], None]]] = {}


def register(name: str, provider: Any) -> None:
    """プロバイダーを登録する。同じ名前があれば置き換える。"""
    _providers[name] = provider


def unregister(name: str, provider: Any = None) -> None:
    """登録を外す。provider を渡すと、今登録されているものがそれと同じときだけ外す（置き換え後の新しいものを消さない）。"""
    if name in _providers and (provider is None or _providers[name] is provider):
        del _providers[name]


def has_provider(name: str) -> bool:
    return name in _providers


def state(name: str) -> dict:
    """プロバイダーの状態。無い・例外のときは available=False（理由つき）。"""
    provider = _providers.get(name)
    if provider is None:
        return {"available": False, "enabled": False, "changeable": False, "reason": NO_PROVIDER_REASON}
    try:
        got = dict(provider.state())
    except Exception as exc:  # noqa: BLE001  提供側の不具合で使う側を止めない
        return {"available": False, "enabled": False, "changeable": False, "reason": f"顔の補正の状態を読めませんでした: {exc}"}
    got.setdefault("available", False)
    got.setdefault("enabled", False)
    got.setdefault("changeable", bool(got["available"]))
    got.setdefault("reason", "")
    return got


def set_enabled(name: str, on: bool) -> dict:
    """オン / オフを切り替える。戻り: {"ok": bool, "message": str}。失敗しても例外は投げない。"""
    provider = _providers.get(name)
    if provider is None:
        return {"ok": False, "message": NO_PROVIDER_REASON}
    try:
        got = provider.set_enabled(bool(on))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": f"切り替えられませんでした: {exc}"}
    if not isinstance(got, dict):
        return {"ok": True, "message": ""}
    return {"ok": bool(got.get("ok", True)), "message": str(got.get("message", ""))}


def subscribe(name: str, fn: Callable[[], None]) -> None:
    subs = _subscribers.setdefault(name, [])
    if fn not in subs:
        subs.append(fn)


def unsubscribe(name: str, fn: Callable[[], None]) -> None:
    subs = _subscribers.get(name, [])
    if fn in subs:
        subs.remove(fn)


def notify(name: str) -> None:
    """購読している画面へ「変わった」と知らせる。購読側の不具合は伝えない（Qt の枠が破棄済みなら購読を外す）。"""
    for fn in list(_subscribers.get(name, [])):
        try:
            fn()
        except RuntimeError:
            unsubscribe(name, fn)
        except Exception:  # noqa: BLE001
            lifecycle.report_error(f"{name} の通知先でエラー", traceback.format_exc())


def _clear() -> None:
    _providers.clear()
    _subscribers.clear()


lifecycle.on_reload(_clear)
