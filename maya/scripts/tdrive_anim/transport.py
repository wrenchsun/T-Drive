"""再生の「全部止める」と「今の状態」（AN-F-35）。

画面では止まって見えるのに内部で時刻が動き続ける、という事態を防ぐため、止める操作はどのタブの ■ でも・ウィンドウを閉じる・リロード・シーンを開く でも、
ここの `stop_all()` を通す。止めるもの:
- Maya の再生（`cmds.play`）… ブレンド / クリップの ▶
- クリップ再生の控え（再生範囲・時刻・ループを元へ。`clips.stop`）
- ショットの QTimer トランスポートとパネルのカメラ（`shot_scene.stop`）
- ブレンドの作り直しの待ちタイマー（タブが `register` したもの）
`status()` は今動いているものの名前（最上部の「再生中（…）/ 停止」の表示用）。
"""

from __future__ import annotations

import contextlib
import weakref
from collections.abc import Callable

from maya import cmds

from tdrive import lifecycle

from . import clips as MC
from . import shot_scene as SS

_stoppers: list[Callable[[], None] | weakref.WeakMethod] = []  # タブが持つタイマーを止める関数（弱い参照。タブが消えれば自然に外れる）
_checkers: list[Callable[[], str] | weakref.WeakMethod] = []  # 動いているものの名前を返す関数（動いていなければ ""）


def _ref(fn):
    return weakref.WeakMethod(fn) if hasattr(fn, "__self__") else fn


def register(stopper: Callable[[], None], checker: Callable[[], str] | None = None) -> None:
    """タブが持つタイマーの止め方と、動いているかの確かめ方を登録する。"""
    _stoppers.append(_ref(stopper))
    if checker is not None:
        _checkers.append(_ref(checker))


def _live(items):
    out = []
    for r in items:
        fn = r() if isinstance(r, weakref.WeakMethod) else r
        if fn is not None:
            out.append(fn)
    return out


def stop_all() -> None:
    """動いている再生・トランスポート・待ちタイマーを全部止める。どれかが失敗しても残りを止める。"""
    for fn in [lambda: cmds.play(state=False), SS.stop, MC.stop, *_live(_stoppers)]:
        with contextlib.suppress(Exception):
            fn()


def status() -> list[str]:
    """今動いているものの名前（空なら停止中）。"""
    names: list[str] = []
    with contextlib.suppress(Exception):
        if cmds.play(query=True, state=True):
            names.append("Maya の再生")
    if SS.is_playing():
        names.append("ショット")
    if MC.is_playing():
        names.append("クリップ")
    for fn in _live(_checkers):
        with contextlib.suppress(Exception):
            name = fn()
            if name:
                names.append(name)
    return names


def status_text() -> str:
    names = status()
    return f"▶ 再生中（{' / '.join(names)}）" if names else "■ 停止"


lifecycle.on_reload(stop_all)
