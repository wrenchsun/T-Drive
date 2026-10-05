"""「顔以外を隠す」: グリッドの角度によっては髪などが顔の補正の邪魔になるので、顔のメッシュ以外を一時的に見えなくする（表示だけ。任意）。

しくみ: 隠すメッシュの transform に `overrideEnabled = 1` / `overrideVisibility = 0`（ドローイングオーバーライド）を書き、元の値を覚えておいて戻す。
`visibility` は触らない（キーが付いている・他とつながっている・ロックされているかもしれないため）。
**isolateSelect（ビューごとの「選択物だけ表示」）は使わない**: パネルを作らない mayapy で試せない・パネルごとにしか効かない・
`<パネル名>ViewSelectedSet` というセットがシーンに残る（シーンが変更扱いになり保存される）・あとで作られた物（シーンの格子など）が見えなくなるため。

- 隠す対象: 表示中のメッシュ（中間でない shape を持つ transform）のうち、文書の対象メッシュ（顔 + extraMeshes + LOD）でないもの。
  プレビュー専用（tdPreviewOnly）・もともと非表示のものは対象にしない。ジョイント・ロケータ・カメラ・シーンの格子は元から対象外（メッシュではない）
- 「隠さないもの」（`keep`。名前で覚える）に入れたメッシュは隠さない。髪を見たいときは「隠すもの…」でチェックを外す
- `overrideEnabled` / `overrideVisibility` がロックされている・他のノード（表示レイヤーなど）とつながっているメッシュは、触らずに飛ばし、数と名前を知らせる（`status_text`）
- **シーンには残さない**: 保存・出力の直前に元へ戻し（`suspend`）、直後に隠し直す（`resume`）。ツールのリロード・パネルを閉じる / データを閉じる・
  新しいシーン / シーンを開くで元へ戻る。Maya の Undo に積まず、変更フラグも元へ戻す（`scene_grid._quiet`）
- 設定（出す / 隠さないもの）は文書に保存しない。ツールのリロードをまたいで引き継ぐ（`export_state` / `import_state`）
- 追従: `follow()`（グリッドタブのタイマーから。1.5 秒おき）が、増えた・減ったメッシュに合わせる
"""

from __future__ import annotations

import contextlib
import time
import traceback
from typing import Optional

from maya import cmds
from tdrive import lifecycle

from . import scene as scene_mod
from .scene_grid import _quiet

SUSPEND_TIMEOUT = 15.0  # 保存・出力が失敗して「直後」の通知が来なかったとき、これだけ経ったら隠し直す（秒）
SCAN_INTERVAL = 1.5  # follow が見直す間隔（秒）
ATTRS = ("overrideEnabled", "overrideVisibility")

_active: Optional["HideOthers"] = None


class HideOthersError(RuntimeError):
    """顔以外を隠せない（データ・顔のメッシュが無い等）。"""


def _reload_cleanup() -> None:
    g = _active
    if g is not None:
        g.remove()


lifecycle.on_reload(_reload_cleanup)


class HideOthers:
    """`FacialSession.hide_others`。"""

    def __init__(self, session) -> None:
        self.session = session
        self.enabled = False
        self.keep: set[str] = set()  # 隠さないメッシュの名前（短い名前）
        self.skipped: list[str] = []  # 触れなくて隠せなかったメッシュ（短い名前）
        self.last_error = ""
        self._applied: dict[str, tuple[str, int, bool]] = {}  # uuid → (長い名前, 元の overrideEnabled, 元の overrideVisibility)
        self._suspended = False
        self._suspended_at = 0.0
        self._next_scan = 0.0

    # ------------------------------------------------------------------ 状態
    def hidden_names(self) -> list[str]:
        """いま隠している（このツールが overrideVisibility を書いている）メッシュの短い名前。"""
        return sorted(scene_mod.short_name(long) for long, _e, _v in self._applied.values())

    def export_state(self) -> dict:
        return {"on": self.enabled, "keep": sorted(self.keep)}

    def import_state(self, state: Optional[dict]) -> None:
        if not state:
            return
        self.keep = {str(n) for n in state.get("keep") or []}
        if state.get("on"):
            try:
                self.set_enabled(True)
            except Exception:  # noqa: BLE001  隠せなくても引き継ぎは成功（設定は残る。隠せるようになったら追従で隠す）
                self.enabled = True

    def status_text(self) -> str:
        """グリッドタブに出す一行（隠している数・触れなかったもの）。出していなければ空。"""
        if not self.enabled:
            return ""
        n = len(self._applied)
        parts = [f"顔以外のメッシュ {n} 個を隠しています" if n else "隠すメッシュはありません"]
        if self.skipped:
            parts.append(
                f"{len(self.skipped)} 個は隠せませんでした（表示レイヤーなどにつながっているか、ロックされています）: " + "、".join(self.skipped[:5]) + ("…" if len(self.skipped) > 5 else "")
            )
        if self.last_error:
            parts.append(self.last_error)
        return "。".join(parts)

    # ------------------------------------------------------------------ 操作
    def set_enabled(self, on: bool) -> None:
        """隠す / 戻す。隠せなければ HideOthersError（設定は「隠さない」へ戻る）。"""
        if on:
            self.enabled = True
            try:
                self._targets()  # 顔のメッシュが分かるか確かめる
                self.apply()
            except Exception as exc:
                self.enabled = False
                self.restore()
                raise HideOthersError(str(exc)) from exc
        else:
            self.enabled = False
            self.restore()
            self.skipped = []

    def set_keep(self, names) -> None:
        """隠さないメッシュを決める（短い名前）。出していれば反映する。"""
        self.keep = {str(n) for n in names}
        if self.enabled and not self._suspended:
            self.apply()

    def hide_all_but_face(self) -> None:
        self.set_keep(())

    def show_all(self) -> None:
        """全部見せる（今ある候補を全部「隠さないもの」にする。チェックは入ったまま）。"""
        self.set_keep(short for short, _long in self.candidates())

    def remove(self) -> None:
        """元へ戻す（設定の「隠す」は変えない）。データを閉じる・シーンの入れ替え・リロードで呼ぶ。"""
        self._suspended = False
        self._next_scan = 0.0
        self.restore()

    def forget(self) -> None:
        """新しいシーン / シーンを開いた直後: 古いシーンの記録を捨てる。"""
        self.remove()
        self._applied = {}
        scene_mod.TOOL_HIDDEN.clear()

    def suspend(self) -> None:
        """保存・出力の直前: 元へ戻す。`resume` で隠し直す。"""
        if self._applied or self.enabled:
            self._suspended = True
            self._suspended_at = time.monotonic()
            self.restore()

    def resume(self) -> None:
        if not self._suspended:
            return
        self._suspended = False
        if self.enabled:
            try:
                self.apply()
            except Exception:  # noqa: BLE001  保存は成功させる。追従が隠し直す
                self.last_error = traceback.format_exc().splitlines()[-1]

    # ------------------------------------------------------------------ 候補
    def _targets(self) -> set[str]:
        """対象メッシュ（長い名前）。分からなければ例外（顔が分からないまま全部隠さない）。"""
        s = self.session
        doc = s.require()
        return set(s._resolve_meshes(doc))

    def candidates(self) -> list[tuple[str, str]]:
        """隠せる候補 [(短い名前, 長い名前)]: 表示中のメッシュの transform（対象メッシュ・プレビュー専用・対象を子に持つものは除く）。"""
        try:
            targets = self._targets()
        except Exception:  # noqa: BLE001
            return []
        out: list[tuple[str, str]] = []
        seen: set[str] = set()
        for shape in cmds.ls(type="mesh", long=True, noIntermediate=True) or []:
            parents = cmds.listRelatives(shape, parent=True, fullPath=True) or []
            xf = parents[0] if parents else None
            if not xf or xf in seen:
                continue
            seen.add(xf)
            if xf in targets or self._preview_only(xf) or scene_mod._is_hidden(xf):
                continue
            below = set(cmds.listRelatives(xf, allDescendents=True, fullPath=True, type="transform") or [])
            if below & targets:  # 顔を子に持つなら、隠すと顔も消える
                continue
            out.append((scene_mod.short_name(xf), xf))
        return out

    @staticmethod
    def _preview_only(xf: str) -> bool:
        n: Optional[str] = xf
        while n:
            if cmds.attributeQuery(scene_mod.PREVIEW_ONLY_ATTR, node=n, exists=True):
                return True
            p = cmds.listRelatives(n, parent=True, fullPath=True)
            n = p[0] if p else None
        return False

    # ------------------------------------------------------------------ 隠す・戻す
    @staticmethod
    def _writable(xf: str) -> bool:
        for a in ATTRS:
            plug = f"{xf}.{a}"
            if cmds.getAttr(plug, lock=True) or cmds.connectionInfo(plug, isDestination=True):
                return False
        return True

    def apply(self) -> None:
        """今の設定に合わせて隠す・戻す（増えた分を隠し、隠さなくなった分・無くなった分を戻す）。"""
        global _active
        if not self.enabled or self._suspended:
            return
        self._next_scan = time.monotonic() + SCAN_INTERVAL
        want = [(s, long) for s, long in self.candidates() if s not in self.keep]
        want_long = {long for _s, long in want}
        skipped: list[str] = []
        _active = self
        with _quiet():
            for uid, (long, _e, _v) in list(self._applied.items()):
                cur = (cmds.ls(uid, long=True) or [None])[0]
                if cur is None or cur not in want_long:
                    self._restore_one(uid)
            done = {rec[0] for rec in self._applied.values()}
            for short, long in want:
                if long in done:
                    continue
                if not self._writable(long):
                    skipped.append(short)
                    continue
                uid = (cmds.ls(long, uuid=True) or [""])[0]
                prev_e = int(cmds.getAttr(long + ".overrideEnabled"))
                prev_v = bool(cmds.getAttr(long + ".overrideVisibility"))
                cmds.setAttr(long + ".overrideEnabled", 1)
                cmds.setAttr(long + ".overrideVisibility", 0)
                self._applied[uid] = (long, prev_e, prev_v)
                scene_mod.TOOL_HIDDEN.add(long)
        self.skipped = skipped
        self.last_error = ""

    def _restore_one(self, uid: str) -> None:
        long, prev_e, prev_v = self._applied.pop(uid)
        scene_mod.TOOL_HIDDEN.discard(long)
        cur = (cmds.ls(uid, long=True) or [None])[0]
        if cur is None:
            return
        scene_mod.TOOL_HIDDEN.discard(cur)
        with contextlib.suppress(RuntimeError):
            cmds.setAttr(cur + ".overrideVisibility", prev_v)
            cmds.setAttr(cur + ".overrideEnabled", prev_e)

    def restore(self) -> None:
        """隠したものを全部、元の値へ戻す。"""
        if not self._applied:
            scene_mod.TOOL_HIDDEN.clear()
            return
        with _quiet():
            for uid in list(self._applied):
                self._restore_one(uid)
        scene_mod.TOOL_HIDDEN.clear()

    def follow(self) -> None:
        """グリッドタブのタイマーから: 保存の「直後」が来なかったら隠し直す・一定の間隔でメッシュの増減に合わせる。"""
        if self._suspended and time.monotonic() - self._suspended_at > SUSPEND_TIMEOUT:
            self.resume()
        if not self.enabled or self._suspended or self.session.presenters is None:
            return
        if time.monotonic() < self._next_scan:
            return
        try:
            self.apply()
        except Exception as exc:  # noqa: BLE001  表示の見直しの失敗で編集を止めない
            self.last_error = str(exc)
            self._next_scan = time.monotonic() + SCAN_INTERVAL
