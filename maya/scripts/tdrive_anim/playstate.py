"""再生範囲・時刻・ループ設定の「元の状態」の控え（クリップ再生・ブレンドの合成・ショットの反映で共有。docs/21 SF-8）。

以前は 3 か所（クリップ再生の `_saved`・合成の `tdPlayback`・ショットの `tdPlayback`）がそれぞれ「その時点の状態」を控えていたため、
戻す順によっては、合成した範囲やクリップの範囲が残ってしまった（クリップ再生 → 合成 → 閉じる → 最後はクリップの範囲）。
ここでは**最初に取った人の控えが元の状態**で、**最後に手を放した人が戻す**。順番は関係ない。
"""

from __future__ import annotations

from typing import Any

from maya import cmds

_owners: set[str] = set()
_base: dict[str, Any] | None = None


def capture() -> dict[str, Any]:
    """今の再生範囲・アニメーション範囲・ループ・現在の時刻。"""
    return {
        "min": cmds.playbackOptions(query=True, minTime=True), "max": cmds.playbackOptions(query=True, maxTime=True),
        "start": cmds.playbackOptions(query=True, animationStartTime=True), "end": cmds.playbackOptions(query=True, animationEndTime=True),
        "loop": cmds.playbackOptions(query=True, loop=True), "time": cmds.currentTime(query=True),
        "speed": cmds.playbackOptions(query=True, playbackSpeed=True), "maxSpeed": cmds.playbackOptions(query=True, maxPlaybackSpeed=True),  # 再生の速さも控える（AN-F-42）
    }


def apply(state: dict[str, Any]) -> None:
    cmds.playbackOptions(minTime=state["min"], maxTime=state["max"], animationStartTime=state["start"], animationEndTime=state["end"], loop=state["loop"])
    if "speed" in state:  # 古い控え（速さの無いもの）でも動く
        cmds.playbackOptions(playbackSpeed=state["speed"], maxPlaybackSpeed=state["maxSpeed"])
    cmds.currentTime(state["time"], edit=True)


def acquire(owner: str) -> dict[str, Any]:
    """`owner` が再生範囲などを変える前に呼ぶ。元の状態を返す（誰かが既に取っていればその人が取った状態）。シーンのノードに控える用。"""
    global _base
    if not _owners:
        _base = capture()
    _owners.add(owner)
    return dict(_base)  # type: ignore[arg-type]


def rebase(owner: str) -> bool:
    """`owner` だけが持っているとき、控えを「今の状態」に取り直す（▶ を押す直前に、前回の ▶ のあとで範囲などを変えられていた場合。AN-F-42）。
    ほかにも持っている人がいれば、その人の控えが元なので触らない。戻り: 取り直したか。"""
    global _base
    if _owners == {owner}:
        _base = capture()
        return True
    return False


def release(owner: str, fallback: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """`owner` が手を放す。最後の 1 人なら、戻すべき元の状態を返す（呼び出し側が `apply` する。`apply_if_last` が両方やる）。
    ほかに持っている人がいれば None（まだ戻さない）。この実行中に誰も取っていなかった（保存して開き直したシーンの控え）ときは fallback。"""
    global _base
    known = owner in _owners
    _owners.discard(owner)
    if _owners:
        return None
    state, _base = (_base if known and _base is not None else fallback), None
    return state


def release_and_restore(owner: str, fallback: dict[str, Any] | None = None) -> bool:
    state = release(owner, fallback)
    if state is not None:
        apply(state)
    return state is not None


def holds(owner: str) -> bool:
    return owner in _owners


def forget() -> None:
    """別のシーンを開いた・新しいシーンにしたとき。前のシーンの控えは捨てる。"""
    global _base
    _owners.clear()
    _base = None
