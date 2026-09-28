"""編集セッション: 現在の Look・編集対象バリアント・A/B 表示状態を持ち、Look とプレビューを同期させる。

UI も MCP スクリプトもこのモジュール経由で操作する（UI 無しでも全機能を呼べる）。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Iterable

from maya import cmds

from . import REPO_ROOT, environment, look, preview, roles

LOOKS_DIR = REPO_ROOT / "looks"
CAPTURE_DIR = REPO_ROOT / "captures"
EXPORT_DIR = REPO_ROOT / "build" / "unity"


class Session:
    def __init__(self) -> None:
        self.look: dict[str, Any] | None = None
        self.path: Path | None = None
        self.dirty = False
        self.edit_variant = look.BASE  # スライダーが書き込む先
        self.ab = [look.BASE, look.BASE]  # A / B に割り当てたバリアント
        self.shown = look.BASE  # ビューポートに表示中のバリアント
        self.listeners: list = []  # 変更通知（UI 更新用）

    # ------------------------------------------------------------ 状態
    def require(self) -> dict[str, Any]:
        if self.look is None:
            raise RuntimeError("Look が開かれていません（新規作成 or 開く）")
        return self.look

    def _changed(self, dirty: bool = True) -> None:
        self.dirty = self.dirty or dirty
        for fn in list(self.listeners):
            try:
                fn()
            except Exception as exc:  # UI 側の不具合で編集を止めない
                print(f"[T-Drive] listener error: {exc}")

    # ------------------------------------------------------------ ファイル
    def new(self, character: str, model: str = "") -> None:
        model = model or preview.to_repo_path(cmds.file(query=True, sceneName=True) or "")
        self.look = look.new_look(character, model or "unknown")
        self.path = LOOKS_DIR / character / "look.json"
        self.edit_variant = self.shown = look.BASE
        self.ab = [look.BASE, look.BASE]
        self._changed()

    def open(self, path: str | Path) -> None:
        self.look = look.load(path)
        self.path = Path(path)
        self.dirty = False
        self.edit_variant = self.shown = look.BASE
        names = look.variant_names(self.look)
        self.ab = [look.BASE, names[1] if len(names) > 1 else look.BASE]
        preview.remember_look_path(str(self.path))
        self.show(look.BASE)
        self._changed(dirty=False)

    def save(self, path: str | Path | None = None) -> Path:
        lk = self.require()
        if path:
            self.path = Path(path)
        if self.path is None:
            self.path = LOOKS_DIR / lk["character"] / "look.json"
        look.save(lk, self.path)
        preview.remember_look_path(str(self.path))
        self.dirty = False
        self._changed(dirty=False)
        return self.path

    # ------------------------------------------------------------ 部位登録
    def auto_register(self, overwrite: bool = False) -> dict[str, str]:
        """シーン内のマテリアルをロール推定で部位登録する。登録した {material: part} を返す。"""
        lk = self.require()
        done = {}
        for mat in preview.scene_materials():
            if not overwrite and look.part_of(lk, mat):
                continue
            role = roles.guess_role(mat)
            part = role if role != "other" else mat
            existing = lk["parts"].get(part, {}).get("materials", [])
            self._register(part, role, sorted(set(existing) | {mat}), preset_for=[mat])
            done[mat] = part
        self._changed()
        return done

    def register(self, part: str, role: str, materials: Iterable[str]) -> None:
        mats = list(materials)
        if not mats:
            raise RuntimeError("登録するマテリアルがありません（メッシュか面を選択してください）")
        self._register(part, role, mats, preset_for=mats)
        self._changed()

    def register_selection(self, part: str, role: str) -> list[str]:
        mats = preview.materials_on_selection()
        self.register(part, role, mats)
        return mats

    def _register(self, part: str, role: str, mats: list[str], preset_for: list[str]) -> None:
        lk = self.require()
        # プリセットは新しく入るマテリアルにだけ適用し、既存メンバーの調整値は保持する
        keep = {m: lk["materials"][m] for m in mats if m in lk["materials"] and m not in preset_for}
        look.register_part(lk, part, role, mats, apply_preset=True)
        lk["materials"].update(keep)
        for m in preset_for:
            common = lk["materials"][m]["common"]
            if not common.get("albedo"):
                common["albedo"] = preview.base_texture_of(m)
            common["blend"] = preview.source_blend(m)  # Blend は元マテリアルから継承（roles.py 参照）
        if preview.is_active():
            preview.enable({m: look.resolve(lk, self.shown)[m] for m in mats})

    def unregister(self, part: str) -> None:
        lk = self.require()
        lk["parts"].pop(part, None)
        self._changed()

    def set_role(self, part: str, role: str, apply_preset: bool) -> None:
        """部位のロールを変える。apply_preset=True なら所属マテリアルにロールのプリセットを再適用する。"""
        lk = self.require()
        mats = list(lk["parts"][part]["materials"])
        if apply_preset:
            self._register(part, role, mats, preset_for=mats)
        else:
            if role not in roles.ROLES:
                raise ValueError(f"不明なロール: {role}")
            lk["parts"][part]["role"] = role
        self._changed()

    def rename_part(self, old: str, new: str) -> None:
        lk = self.require()
        new = new.strip()
        if not new or new == old:
            return
        if new in lk["parts"]:
            raise ValueError(f"部位 '{new}' は既にあります")
        lk["parts"] = {(new if k == old else k): v for k, v in lk["parts"].items()}
        self._changed()

    def move_material(self, material: str, part: str, role: str | None = None) -> None:
        """マテリアルを別の部位へ移す（部位が無ければ作る）。移動先のプリセットはそのマテリアルにだけ適用する。"""
        lk = self.require()
        existing = lk["parts"].get(part)
        role = role or (existing["role"] if existing else roles.guess_role(material))
        mats = sorted(set(existing["materials"] if existing else []) | {material})
        self._register(part, role, mats, preset_for=[material])
        self._changed()

    def set_preview(self, on: bool) -> None:
        """Toon 表示 ⇔ 元の見た目。"""
        if on:
            self.show(self.shown)
        else:
            preview.disable()
            self._changed(dirty=False)

    def select_part(self, part: str) -> None:
        meshes = preview.scene_materials()
        targets = [m for mat in self.require()["parts"][part]["materials"] for m in meshes.get(mat, [])]
        cmds.select(targets, replace=True)

    # ------------------------------------------------------------ 編集
    def materials_of(self, target: str) -> list[str]:
        """target は部位名かマテリアル名。"""
        lk = self.require()
        if target in lk["parts"]:
            return list(lk["parts"][target]["materials"])
        return [target]

    def value(self, material: str, key: str) -> Any:
        mat = look.resolve(self.require(), self.edit_variant)[material]
        if key == "renderQueueOffset":
            return mat["renderQueueOffset"]
        if key.startswith("common."):
            return mat["common"].get(key.split(".", 1)[1])
        return mat["specific"].get(key)

    def set_value(self, target: str, key: str, value: Any, notify: bool = True) -> None:
        lk = self.require()
        for mat in self.materials_of(target):
            look.set_value(lk, mat, key, value, variant=self.edit_variant)
            if self.shown == self.edit_variant:
                preview.apply_value(mat, key, value)
        self.dirty = True
        if notify:
            self._changed()

    # ------------------------------------------------------------ バリアント / A/B
    def add_variant(self, name: str, label: str = "", copy_from: str | None = None) -> None:
        look.add_variant(self.require(), name, label, copy_from or self.edit_variant)
        self._changed()

    def delete_variant(self, name: str) -> None:
        lk = self.require()
        lk["variants"].pop(name, None)
        self.ab = [v if v != name else look.BASE for v in self.ab]
        if self.edit_variant == name:
            self.edit_variant = look.BASE
        if self.shown == name:
            self.show(look.BASE)
        self._changed()

    def promote(self, name: str) -> None:
        look.promote(self.require(), name)
        self.delete_variant(name)
        self.show(look.BASE)

    def set_edit_variant(self, name: str) -> None:
        self.edit_variant = name
        self.show(name)

    def show(self, variant: str) -> None:
        lk = self.require()
        self.shown = variant
        environment.prepare_panel()
        if preview.environment_state()["profile"] is None and (name := environment.default_profile()):
            # リロード直後などで環境未設定なら既定のプロファイルを当てる（色管理・トーンマップ・ライト）
            preview.use_profile(name)
        failed = preview.enable(look.resolve(lk, variant))
        if failed:
            print(f"[T-Drive] シーンに存在しないマテリアル: {', '.join(failed)}")
        self._changed(dirty=False)

    def show_ab(self, slot: int) -> None:
        self.show(self.ab[slot])

    def toggle_ab(self) -> str:
        self.show(self.ab[1] if self.shown == self.ab[0] else self.ab[0])
        return self.shown

    def capture_ab(self, width: int = 1920, height: int = 1080) -> tuple[str, str]:
        """A と B を同じカメラ・ライトでキャプチャする。表示は元のバリアントに戻す。"""
        stamp = time.strftime("%Y%m%d-%H%M%S")
        char = self.require()["character"]
        before = self.shown
        paths = []
        for slot, variant in zip("AB", self.ab):
            self.show(variant)
            cmds.refresh(force=True)
            paths.append(preview.capture(str(CAPTURE_DIR / char / f"{stamp}_{slot}_{variant}.png"), width, height))
        self.show(before)
        return paths[0], paths[1]

    def diff_ab(self) -> list[tuple[str, str, Any, Any]]:
        return look.diff(self.require(), *self.ab)

    # ------------------------------------------------------------ Unity 向け出力
    def export_unity(self, variant: str = look.BASE, out_dir: Path | None = None) -> Path:
        """MS2026 の Unity インポーターが読む中間ファイル（MaterialData 相当）を書き出す。"""
        lk = self.require()
        out = (out_dir or EXPORT_DIR) / lk["character"]
        out.mkdir(parents=True, exist_ok=True)
        payload = {
            "schemaVersion": look.SCHEMA_VERSION,
            "character": lk["character"],
            "lookVersion": lk["lookVersion"],
            "variant": variant,
            "parts": lk["parts"],
            "materials": {m: look.to_ddrive_material_data(lk, m, variant) for m in sorted(lk["materials"])},
        }
        path = out / f"{lk['character']}_{variant}.materialdata.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path


_current = Session()


def current() -> Session:
    return _current
