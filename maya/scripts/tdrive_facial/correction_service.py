"""顔の補正（プレビューの rig の enable）を、他のツール（Toon のヘッダー）へ出すサービス（tdrive.services）。

状態の持ち主は rig の `enable` アトリビュートだけ（ここに別のフラグは持たない）。FacialController タブの
「補正あり / 補正なし」と同じセッション API（`preview_set_enabled`）を呼ぶので、どちらから切り替えても同じ状態になる。
変更の通知はセッションの軽い通知（state_listeners）を受けて、サービスの購読者へ流す。
"""

from __future__ import annotations

from maya import cmds

from tdrive import lifecycle, services

from . import session as session_mod

NO_PREVIEW = "FacialController のプレビューが無いので、顔の補正は掛かっていません（FacialController › グリッド ›「プレビューを作る」で作れます）"
KEYS_ONLY = "プレビューがキーに焼いた状態（式が外れている）なので、ここでは切り替えられません（FacialController › グリッド ›「作り直す」で式が戻ります）"
EDITING = "FacialController の「編集」中は、シーンが基準姿勢のため顔の補正は止まっています。編集を終えると再開します"
ON_TIP = "オフにすると、カメラの角度に合わせた顔の補正を止めます（プレビューの仕掛けは残ります。FacialController タブの「補正あり / 補正なし」と同じ）"


def _enable_block(rig: str) -> str:
    """enable を書き換えられない理由（無ければ空）。キーが打ってある・他から駆動・ロック。"""
    plug = f"{rig}.enable"
    if (cmds.keyframe(plug, query=True, keyframeCount=True) or 0) > 0:
        return "補正の切り替え（enable）にはキーが打ってあるため変えられません（Maya のチャンネルボックスで変えてください）"
    if cmds.listConnections(plug, source=True, destination=False):
        return "補正の切り替え（enable）は他のノードから駆動されているため変えられません"
    if cmds.getAttr(plug, lock=True):
        return "補正の切り替え（enable）はロックされているため変えられません"
    return ""


class _Provider:
    def state(self) -> dict:
        s = session_mod.current()
        st = s.preview_state()  # データが無ければ "none"
        if st == "none":
            return {"available": False, "enabled": False, "changeable": False, "reason": NO_PREVIEW}
        if st == "keys":
            return {"available": False, "enabled": False, "changeable": False, "reason": KEYS_ONLY}
        enabled = s.preview_is_enabled()
        if s.editing:
            return {"available": True, "enabled": enabled, "changeable": False, "reason": EDITING}
        return {"available": True, "enabled": enabled, "changeable": True, "reason": ON_TIP}

    def set_enabled(self, on: bool) -> dict:
        s = session_mod.current()
        rig = s.preview_rig_node()
        if rig is None:
            return {"ok": False, "message": NO_PREVIEW}
        block = _enable_block(rig)
        if block:
            return {"ok": False, "message": block}
        s.preview_set_enabled(bool(on))  # 通知（state_listeners → _forward）はここから出る
        return {"ok": True, "message": "補正あり" if on else "補正なし（元の形）"}


_provider = _Provider()
_attached_to = None  # 通知を受けているセッション（リロード前の古いセッションから外すために覚える）


def _forward() -> None:
    services.notify(services.FACIAL_CORRECTION)


def register() -> None:
    """サービスに登録し、セッションの軽い通知を購読する（何度呼んでもよい）。"""
    global _attached_to
    s = session_mod.current()
    if _forward not in s.state_listeners:
        s.state_listeners.append(_forward)
    _attached_to = s
    services.register(services.FACIAL_CORRECTION, _provider)
    lifecycle.on_reload(unregister)
    services.notify(services.FACIAL_CORRECTION)  # 先に出来ていた Toon のヘッダーへ「提供元が出来た」と知らせる


def unregister() -> None:
    """リロード前の後片付け: 登録を外し、セッションの購読を外す。"""
    global _attached_to
    services.unregister(services.FACIAL_CORRECTION, _provider)
    if _attached_to is not None and _forward in _attached_to.state_listeners:
        _attached_to.state_listeners.remove(_forward)
    _attached_to = None
