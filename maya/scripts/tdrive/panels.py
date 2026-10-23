"""モデルパネル（3D ビューポート）の選び方の共通部分（Toon のプレビュー・Animation のショットのカメラ切替で共用。AN-F-45）。

「見えているパネル」を優先する。フォーカスがあるパネルが無いとき（T-Drive のウィンドウを操作している間はフォーカスがそちらにある）に、
一覧の先頭（非表示の top など）へ落ちると、カメラの切り替えが見えないパネルに結ばれてしまう（実機で起きた）。

順序: ① フォーカスのある（見えている）モデルパネル → ② 最後にアクティブだったモデルパネル（`playblast -activeEditor`）→ ③ 見えているモデルパネルの先頭
→ ④ 見えているものが無ければ、モデルパネルの先頭（見えていない印つき）。
"""

from __future__ import annotations

from maya import cmds


def pick_model_panel(models: list[str], visible: list[str], focus: str = "", active: str = "") -> tuple[str | None, bool]:
    """(選んだパネル | None, 見えているか)。Maya に触らない純粋な選び方（テスト用に引数で受ける）。"""
    vis = [p for p in models if p in set(visible)]
    if focus and focus in vis:
        return focus, True
    if active and active in vis:
        return active, True
    if vis:
        return vis[0], True
    return (models[0], False) if models else (None, False)


def _active_panel(visible_models: list[str]) -> str:
    """最後にアクティブだったモデルエディタのパネル（`playblast -activeEditor`）。取れなければ ""。"""
    try:
        editor = cmds.playblast(activeEditor=True)
    except RuntimeError:
        return ""
    for p in visible_models:
        try:
            if cmds.modelPanel(p, query=True, modelEditor=True) == editor:
                return p
        except RuntimeError:
            continue
    return ""


def model_panel_info() -> tuple[str | None, bool]:
    """今のシーンで使うモデルパネルと、それが見えているか。パネルが無い環境（mayapy）は (None, False)。"""
    models = list(cmds.getPanel(type="modelPanel") or [])
    visible = list(cmds.getPanel(visiblePanels=True) or [])
    focus = cmds.getPanel(withFocus=True) or ""
    if focus and cmds.getPanel(typeOf=focus) != "modelPanel":
        focus = ""
    return pick_model_panel(models, visible, focus, _active_panel([p for p in models if p in visible]))


def model_panel() -> str | None:
    return model_panel_info()[0]
