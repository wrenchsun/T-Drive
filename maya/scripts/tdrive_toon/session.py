"""編集セッション: 現在の Look・編集対象バリアント・A/B 表示状態を持ち、Look とプレビューを同期させる。

UI も MCP スクリプトもこのモジュール経由で操作する（UI 無しでも全機能を呼べる）。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from functools import wraps
from typing import Any, Iterable

from maya import cmds

from . import REPO_ROOT, environment, features, look, preview, roles

LOOKS_DIR = REPO_ROOT / "looks"
CAPTURE_DIR = REPO_ROOT / "captures"
EXPORT_DIR = REPO_ROOT / "build" / "unity"


def undoable(fn):
    """エディタ内 Undo の区切り（checkpoint）を自動で入れる。入れ子の呼び出しでは外側の 1 回だけ。"""

    @wraps(fn)
    def wrapper(self, *args, **kwargs):
        outer = self._undo_depth == 0
        if outer:
            self.checkpoint()
        self._undo_depth += 1
        try:
            return fn(self, *args, **kwargs)
        finally:
            self._undo_depth -= 1

    return wrapper


class Session:
    def __init__(self) -> None:
        self.look: dict[str, Any] | None = None
        self.path: Path | None = None
        self.dirty = False
        self.edit_variant = look.BASE  # スライダーが書き込む先
        self.ab = [look.BASE, look.BASE]  # A / B に割り当てたバリアント
        self.shown = look.BASE  # ビューポートに表示中のバリアント
        self.listeners: list = []  # 変更通知（UI 更新用）
        self._undo: list[dict[str, Any]] = []  # Look のスナップショット（エディタ内 Undo。Maya の Undo とは別）
        self._redo: list[dict[str, Any]] = []
        self._undo_depth = 0

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
            except Exception as exc:  # UI 側の不具合で編集を止めない。ただし見えるように通知する
                import traceback

                from . import lifecycle

                lifecycle.report_error(f"画面の更新でエラー: {exc}", traceback.format_exc())

    # ------------------------------------------------------------ ファイル
    def new(self, character: str, model: str = "") -> None:
        model = model or preview.to_repo_path(cmds.file(query=True, sceneName=True) or "")
        self.look = look.new_look(character, model or "unknown")
        self.path = LOOKS_DIR / character / "look.json"
        self.edit_variant = self.shown = look.BASE
        self._undo.clear()
        self._redo.clear()
        self.ab = [look.BASE, look.BASE]
        self._changed()

    def open(self, path: str | Path) -> None:
        self.look = look.load(path)
        self.path = Path(path)
        self.dirty = False
        self.edit_variant = self.shown = look.BASE
        self._undo.clear()
        self._redo.clear()
        names = look.variant_names(self.look)
        self.ab = [look.BASE, names[1] if len(names) > 1 else look.BASE]
        preview.remember_look_path(str(self.path))
        preview.load_preview_settings(self.preview_settings_path())  # 保存したプレビュー条件（環境・ライト）を復元
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

    def preview_settings_path(self) -> Path:
        base = self.path.parent if self.path else LOOKS_DIR / self.require()["character"]
        return base / "preview.json"

    def save_preview_settings(self) -> Path:
        path = self.preview_settings_path()
        preview.save_preview_settings(path)
        return path

    # ------------------------------------------------------------ 部位登録
    @undoable
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

    @undoable
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
        # プリセットは新しく入るマテリアルにだけ適用し、既存メンバーの調整値は保持する。
        # register_part はマテリアルの dict をその場で書き換えるので、参照ではなく複製を退避する
        import copy

        keep = {m: copy.deepcopy(lk["materials"][m]) for m in mats if m in lk["materials"] and m not in preset_for}
        look.register_part(lk, part, role, mats, apply_preset=True)
        lk["materials"].update(keep)
        for m in preset_for:
            common = lk["materials"][m]["common"]
            if not common.get("albedo"):
                common["albedo"] = preview.base_texture_of(m)
            common["blend"] = preview.source_blend(m)  # Blend は元マテリアルから継承（roles.py 参照）
        if preview.is_active():
            preview.enable({m: look.resolve(lk, self.shown)[m] for m in mats})
            self._sync_character_preview()  # 部位が増えると部位キー（画面上の線）が変わる

    @undoable
    def unregister(self, part: str) -> None:
        lk = self.require()
        lk["parts"].pop(part, None)
        il = lk.get(look.SETTINGS, {}).get("innerLine", {})
        il["parts"] = [p for p in il.get("parts", []) if p != part]  # インナーライン対象からも外す
        self._sync_character_preview()
        self._changed()

    @undoable
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

    @undoable
    def rename_part(self, old: str, new: str) -> None:
        lk = self.require()
        new = new.strip()
        if not new or new == old:
            return
        if new in lk["parts"]:
            raise ValueError(f"部位 '{new}' は既にあります")
        lk["parts"] = {(new if k == old else k): v for k, v in lk["parts"].items()}
        il = lk.get(look.SETTINGS, {}).get("innerLine", {})
        il["parts"] = [new if p == old else p for p in il.get("parts", [])]  # インナーライン対象の部位名も追従
        self._sync_character_preview()
        self._changed()

    @undoable
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
            self._sync_character_preview()  # 接地影の板も隠す
            self._changed(dirty=False)

    def select_part(self, part: str) -> None:
        meshes = preview.scene_materials()
        targets = [m for mat in self.require()["parts"][part]["materials"] for m in meshes.get(mat, [])]
        cmds.select(targets, replace=True)

    # ------------------------------------------------------------ 機能のオン/オフ（docs/11）
    def feature_enabled(self, feature_id: str) -> bool:
        return look.enabled(self.require(), feature_id)

    @undoable
    def set_feature(self, feature_id: str, on: bool) -> None:
        """機能をオン/オフする。保存値は残し、プレビューは効果なしの値で描き直す。"""
        lk = self.require()
        look.set_feature(lk, feature_id, on)
        if preview.is_active():
            self._refresh_feature_preview([feature_id])
        self._changed()

    def _refresh_feature_preview(self, feature_ids: list[str]) -> None:
        """機能を切り替えた後のプレビュー更新。必要な所だけ（クリックごとに全体を作り直すと 0.3 秒かかっていた）。"""
        resolved = look.resolve(self.require(), self.shown)
        if "vertexMask" in feature_ids:
            preview.enable(resolved)  # 頂点マスクはメッシュ側の頂点ストリーム設定も変わる
        else:
            for mat, values in resolved.items():
                if cmds.objExists(preview.preview_shader_of(mat)):
                    preview.apply_values(mat, values)
        if any(features.BY_ID[f].settings for f in feature_ids):
            self._sync_character_preview()  # キャラクター設定を持つ機能（接地影・画面上の線など）

    @undoable
    def disable_unused_features(self) -> list[str]:
        """使っていない（値がすべて効果なしの）機能をオフにする。オフにした ID を返す。"""
        lk = self.require()
        used = look.used_features(lk)
        off = [f.id for f in features.FEATURES
               if not f.required and look.enabled(lk, f.id) and f.id not in used]
        for fid in off:
            look.set_feature(lk, fid, False)
        if off and preview.is_active():
            self._refresh_feature_preview(off)
        self._changed(dirty=bool(off))
        return off

    # ------------------------------------------------------------ キャラクター単位の設定（docs/02 §4.1）
    def setting(self, path: str) -> Any:
        return look.get_setting(self.require(), path)

    @undoable
    def set_setting(self, path: str, value: Any, notify: bool = True) -> None:
        look.set_setting(self.require(), path, value)
        self.dirty = True
        if path == "depthCompression" or path.startswith(("faceShadow", "contactShadow", "innerLine", "screenOutline")):
            self._sync_character_preview()
        if notify:
            self._changed()

    def _sync_character_preview(self) -> None:
        """キャラクター単位の設定のうち Maya でプレビューできるもの（奥行き圧縮・顔影・接地影）をプレビューへ反映する。"""
        from . import contact_shadow, envmath, screen_line

        lk = self.look
        if lk is None:
            return
        cs = look.resolved_settings(lk)  # オフの機能の設定は効果なし
        amount = float(cs["depthCompression"])
        resolved = look.resolve(lk, self.shown)
        weighted = [m for m, v in resolved.items() if v["specific"].get("_ToonDepthCompressWeight", 0) > 0]
        scene = preview.scene_materials()
        meshes = [x for m in weighted for x in scene.get(m, [])]
        pivot = None
        if meshes:
            b = cmds.exactWorldBoundingBox(meshes)
            pivot = ((b[0] + b[3]) / 2, (b[1] + b[4]) / 2, (b[2] + b[5]) / 2)
        preview.set_depth_compression(amount, pivot)
        fs = cs["faceShadow"]
        preview.set_face_axes(fs["forward"], fs["right"])
        preview.set_line_keys(look.line_part_keys(lk))
        il = cs["innerLine"]
        so = cs["screenOutline"]
        inner = {"width": il["width"], "color": envmath.srgb_color_to_linear(il["color"])[:3]} if il["enabled"] else None
        outer = {"width": so["width"], "color": envmath.srgb_color_to_linear(so["color"])[:3]} if so["enabled"] else None
        screen_line.update(environment.model_panel(), inner, outer, preview.environment_state()["tonemap"], visible=preview.is_active())
        if cs["contactShadow"].get("enabled") or contact_shadow.exists():
            contact_shadow.update(self.export_meshes(), cs["contactShadow"], visible=preview.is_active())

    # ------------------------------------------------------------ カメラ角度の補正 BlendShape（T-20）
    def _vc_mesh(self) -> str:
        from . import view_correction

        name = self.setting("viewCorrection.mesh")
        found = cmds.ls(name, long=True) if name else []
        return found[0] if found else view_correction.mesh_for_selection()

    def create_view_correction(self, key: str) -> str:
        """選択中の顔メッシュ（または登録済みのメッシュ）から補正シェイプを複製して作る。"""
        from . import view_correction

        mesh = self._vc_mesh()
        target = view_correction.create_target(mesh, key)
        self.set_setting("viewCorrection.mesh", preview.short_name(mesh))
        return target

    def register_view_correction(self, key: str) -> str:
        """彫った補正シェイプ（<メッシュ>_vc_<角度>）を BlendShape に登録し、Look に記録する。"""
        from . import view_correction

        mesh = self._vc_mesh()
        target = f"{preview.short_name(mesh)}_vc_{key}"
        if not cmds.objExists(target):
            raise RuntimeError(f"{target} がありません（先に「作る」）")
        alias = view_correction.register(mesh, key, target)
        self.set_setting(f"viewCorrection.{key}", alias)
        return alias

    def connect_view_correction(self, camera: str | None = None) -> str:
        from . import view_correction

        targets = {k: self.setting(f"viewCorrection.{k}") for k in view_correction.KEYS}
        return view_correction.connect_driver(self._vc_mesh(), targets, camera)

    def disconnect_view_correction(self) -> None:
        from . import view_correction

        view_correction.disconnect_driver(self._vc_mesh())

    # ------------------------------------------------------------ SDF 顔影マップ（T-21）
    @undoable
    def generate_face_shadow(self, folder: str, size: int = 512, target: str | None = None) -> str:
        """角度別マスクのフォルダから顔影マップを作り、顔マテリアル（既定: ロール face の部位）に設定する。"""
        from . import sdf_maya

        path = sdf_maya.generate(folder, size=size)
        rel = preview.to_repo_path(path)
        lk = self.require()
        targets = [target] if target else [p for p, v in lk["parts"].items() if v["role"] == "face"]
        if not targets:
            raise RuntimeError("顔の部位（ロール face）がありません。部位を指定してください")
        for part in targets:
            self.set_value(part, "_ToonFaceShadowMap", rel, notify=False)
            if self.value(self.materials_of(part)[0], "_ToonFaceShadowWeight") == 0.0:
                self.set_value(part, "_ToonFaceShadowWeight", 1.0, notify=False)
        self._changed()
        return rel

    def preview_expression(self, name: str, t: float) -> list[tuple[str, str, float]]:
        """表情パラメータを t（0–1）にしたときの見た目をプレビューする（Look には保存しない。T-25）。

        対応表の値をプレビューに流すだけなので、表示を切り替える（show）と Look の値に戻る。
        """
        if not look.enabled(self.require(), "expressions"):
            return []  # 機能がオフ
        rows = look.expression_values(self.require(), name, t)
        for material, prop, value in rows:
            preview.apply_value(material, prop, value)
        return rows

    # ------------------------------------------------------------ Toon マスク（頂点カラー）
    def meshes_for(self, target: str | None) -> list[str]:
        """target（部位 / マテリアル）のメッシュ。None なら選択中のメッシュ。"""
        if target is None:
            return cmds.ls(selection=True, long=True, type="transform") or []
        scene = preview.scene_materials()
        return sorted({m for mat in self.materials_of(target) for m in scene.get(mat, [])})

    def init_mask(self, target: str | None = None) -> list[str]:
        from . import mask

        created = mask.init(self.meshes_for(target))
        if self.look is not None and preview.is_active():
            self.show(self.shown)  # 頂点マスクの有効 / 無効を更新
        return created

    def bake_smooth_normals(self, target: str | None = None) -> dict[str, int]:
        from . import smooth_normals

        done = smooth_normals.bake(self.meshes_for(target))
        if self.look is not None and preview.is_active():
            self.show(self.shown)
        return done

    # ------------------------------------------------------------ 顔の法線（Toon Normal、T-10）
    def create_face_proxy(self, target: str | None = None) -> str:
        from . import face_normals

        return face_normals.create_proxy(self.meshes_for(target))

    def transfer_face_normals(self, target: str | None = None, weight: float = 1.0, selected_only: bool = False) -> dict[str, int]:
        from . import face_normals

        verts = face_normals.selected_vertex_ids() if selected_only else None
        if selected_only and not verts:
            raise RuntimeError("頂点を選択してください（「選択した頂点だけ」がオン）")
        meshes = sorted({cmds.listRelatives(s, parent=True, fullPath=True)[0] for s in verts}) if verts else self.meshes_for(target)
        return face_normals.transfer(meshes, weight, verts)

    def reset_face_normals(self, target: str | None = None) -> list[str]:
        from . import face_normals

        return face_normals.reset(self.meshes_for(target))

    def begin_mask_paint(self, channel: str, target: str | None = None) -> None:
        from . import mask

        mask.begin_paint(self.meshes_for(target), channel)
        if self.look is not None:
            self.show(self.shown)
            mask._set_edit_channel(mask.CHANNELS.index(channel) + 1)
        self._changed(dirty=False)

    def end_mask_paint(self, commit: bool = True) -> None:
        from . import mask

        mask.commit() if commit else mask.cancel()
        self._changed(dirty=False)

    # ------------------------------------------------------------ 編集
    def materials_of(self, target: str) -> list[str]:
        """target は部位名かマテリアル名。"""
        lk = self.require()
        if target in lk["parts"]:
            return list(lk["parts"][target]["materials"])
        return [target]

    def value(self, material: str, key: str) -> Any:
        mat = look.merged(self.require(), self.edit_variant)[material]  # 編集中の値（オフの機能でも保存値を見せる）
        if key == "renderQueueOffset":
            return mat["renderQueueOffset"]
        if key.startswith("common."):
            return mat["common"].get(key.split(".", 1)[1])
        return mat["specific"].get(key)

    def set_value(self, target: str, key: str, value: Any, notify: bool = True) -> None:
        self.require()
        for mat in self.materials_of(target):
            self._set_one(mat, key, value)
        if key == "_ToonDepthCompressWeight":
            self._sync_character_preview()  # 効かせる部位が変わると中心も変わる
        if notify:
            self._changed()

    def values(self, target: str, key: str) -> list[Any]:
        """target（部位 or マテリアル）に属する各マテリアルの、編集先バリアントでの値。"""
        return [self.value(m, key) for m in self.materials_of(target)]

    def is_overridden(self, material: str, key: str) -> bool:
        """編集先バリアントで base から上書きされているか（base 編集中は常に False）。"""
        if self.edit_variant == look.BASE:
            return False
        ov = self.require()["variants"][self.edit_variant]["overrides"].get(material, {})
        if key == "renderQueueOffset":
            return "renderQueueOffset" in ov
        if key.startswith("common."):
            return key.split(".", 1)[1] in ov.get("common", {})
        return key in ov.get("specific", {})

    def default_value(self, material: str, key: str) -> Any:
        """リセット先: 部位ロールのプリセット値、無ければパラメータ契約 / MaterialCommon の既定値。"""
        from . import params

        lk = self.require()
        part = look.part_of(lk, material)
        preset = roles.ROLE_PRESETS[lk["parts"][part]["role"]] if part else {}
        if key == "renderQueueOffset":
            return preset.get("renderQueueOffset", 0)
        if key.startswith("common."):
            field = key.split(".", 1)[1]
            return preset.get("common", {}).get(field, params.COMMON_FIELDS[field])
        return preset.get("specific", {}).get(key, params.PARAMS_BY_UNITY[key].default)

    def reset_value(self, target: str, key: str) -> None:
        self.checkpoint()
        for m in self.materials_of(target):
            self._set_one(m, key, self.default_value(m, key))
        self._changed()

    def clear_override(self, target: str, key: str) -> None:
        """バリアントの上書きを外して base の値に戻す。"""
        if self.edit_variant == look.BASE:
            return
        self.checkpoint()
        lk = self.require()
        overrides = lk["variants"][self.edit_variant]["overrides"]
        for m in self.materials_of(target):
            ov = overrides.get(m, {})
            if key == "renderQueueOffset":
                ov.pop("renderQueueOffset", None)
            elif key.startswith("common."):
                ov.get("common", {}).pop(key.split(".", 1)[1], None)
            else:
                ov.get("specific", {}).pop(key, None)
            for section in ("common", "specific"):
                if section in ov and not ov[section]:
                    del ov[section]
            if m in overrides and not overrides[m]:
                del overrides[m]
            if self.shown == self.edit_variant:
                preview.apply_value(m, key, self.value(m, key))
        self._changed()

    def _set_one(self, material: str, key: str, value: Any) -> None:
        look.set_value(self.require(), material, key, value, variant=self.edit_variant)
        if self.shown == self.edit_variant:
            preview.apply_value(material, key, value)
        self.dirty = True

    # ------------------------------------------------------------ Undo（エディタ内）
    def checkpoint(self) -> None:
        """編集操作の直前に呼ぶ（スライダーは押した瞬間に 1 回）。"""
        import copy

        if self.look is None:
            return
        self._undo.append(copy.deepcopy(self.look))
        del self._undo[:-100]
        self._redo.clear()

    def undo(self) -> bool:
        return self._restore(self._undo, self._redo)

    def redo(self) -> bool:
        return self._restore(self._redo, self._undo)

    def _restore(self, src: list, dst: list) -> bool:
        import copy

        if not src or self.look is None:
            return False
        dst.append(copy.deepcopy(self.look))
        self.look = src.pop()
        if self.edit_variant != look.BASE and self.edit_variant not in self.look["variants"]:
            self.edit_variant = look.BASE
        self.show(self.shown if self.shown in look.variant_names(self.look) else look.BASE)
        self.dirty = True
        self._changed()
        return True

    # ------------------------------------------------------------ バリアント / A/B
    @undoable
    def add_variant(self, name: str, label: str = "", copy_from: str | None = None) -> None:
        look.add_variant(self.require(), name, label, copy_from or self.edit_variant)
        self._changed()

    @undoable
    def delete_variant(self, name: str) -> None:
        lk = self.require()
        lk["variants"].pop(name, None)
        self.ab = [v if v != name else look.BASE for v in self.ab]
        if self.edit_variant == name:
            self.edit_variant = look.BASE
        if self.shown == name:
            self.show(look.BASE)
        self._changed()

    @undoable
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
        self._sync_character_preview()
        if failed:
            print(f"[T-Drive] シーンに存在しないマテリアル: {', '.join(failed)}")
        self._changed(dirty=False)

    def set_ab(self, slot: int, variant: str) -> None:
        if variant not in look.variant_names(self.require()):
            raise ValueError(f"バリアント '{variant}' はありません")
        self.ab[slot] = variant
        self._changed(dirty=False)

    def show_ab(self, slot: int) -> None:
        self.show(self.ab[slot])

    def toggle_ab(self) -> str:
        self.show(self.ab[1] if self.shown == self.ab[0] else self.ab[0])
        return self.shown

    def capture_ab(
        self, yaws: list[float] | None = None, target: str = "all", width: int = 1920, height: int = 1080
    ) -> list[dict[str, Any]]:
        """A と B を同じカメラ・ライト・解像度でキャプチャする。表示は元のバリアントに戻す。

        yaws=None なら現在のカメラで 1 組。角度を渡すと環境プロファイルのカメラでその方向から撮る。
        戻り値: [{"yaw": 角度 or None, "A": パス, "B": パス}, ...]
        """
        stamp = time.strftime("%Y%m%d-%H%M%S")
        char = self.require()["character"]
        before = self.shown
        prof_name = preview.environment_state()["profile"] or environment.default_profile()
        prof = environment.load_profile(prof_name) if prof_name else None
        env_tag = prof_name or "noenv"
        shots = []
        with environment.capture_panel():
            for yaw in yaws or [None]:
                if yaw is not None:
                    if prof is None:
                        raise RuntimeError("環境プロファイルがありません（looks/_env）")
                    environment.frame_camera(prof, yaw, target=target)
                shot: dict[str, Any] = {"yaw": yaw}
                for slot, variant in zip("AB", self.ab):
                    self.show(variant)
                    cmds.refresh(force=True)
                    angle = "cur" if yaw is None else f"{int(yaw):+04d}"
                    name = f"{stamp}_{env_tag}_{angle}_{slot}_{variant}.png"
                    shot[slot] = preview.capture(str(CAPTURE_DIR / char / name), width, height)
                shots.append(shot)
        self.show(before)
        return shots

    def capture_parity(self, variant: str | None = None, target: str = "all") -> list[str]:
        """パリティ比較用（docs/09 §5）: 環境プロファイルの解像度・カメラで 正面 / 3/4 / 横 / 後ろ を撮る。

        Unity 側で同じ条件のキャプチャを撮り、tools/parity/compare.py で比べる。
        """
        prof_name = preview.environment_state()["profile"] or environment.default_profile()
        if not prof_name:
            raise RuntimeError("環境プロファイルがありません（looks/_env）")
        prof = environment.load_profile(prof_name)
        w, h = prof.get("captureSize", [1920, 1080])
        before = self.shown
        self.show(variant or self.shown)
        out_dir = CAPTURE_DIR / self.require()["character"] / "parity"
        paths = []
        with environment.capture_panel():
            for name, yaw in list(environment.CAMERA_PRESETS.items())[:4]:
                environment.frame_camera(prof, yaw, target=target)
                cmds.refresh(force=True)
                paths.append(preview.capture(str(out_dir / f"maya_{prof_name}_{int(yaw):+04d}_{target}.png"), w, h))
        self.show(before)
        return paths

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
            "features": look.enabled_features(lk),  # プロジェクト専用シェーダーの生成に使う（docs/11 §3）
            "characterSettings": {  # オンの機能の設定だけ（オフは Unity 側で処理しない）
                k: v for k, v in look.resolved_settings(lk).items()
                if look.enabled(lk, features.FEATURE_OF_SETTING[k])
            },
            "materials": {m: look.to_ddrive_material_data(lk, m, variant) for m in sorted(lk["materials"])},
        }
        path = out / f"{lk['character']}_{variant}.materialdata.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path

    def export_meshes(self) -> list[str]:
        """Look に登録されたマテリアルが付いているメッシュ（FBX に入れるもの）。

        カメラ角度補正の彫刻用メッシュ（BlendShape として顔に入るもの）は除く。
        """
        from . import view_correction

        scene = preview.scene_materials()
        meshes = {m for mat in self.require()["materials"] for m in scene.get(mat, [])}
        return sorted(m for m in meshes if not view_correction.is_target(m))

    def fbx_path(self, out_dir: Path | None = None) -> Path:
        char = self.require()["character"]
        return (out_dir or EXPORT_DIR) / char / f"{char}.fbx"

    def start_unity_export(self, variant: str = look.BASE, out_dir: Path | None = None):
        """materialdata.json を書き、FBX の書き出しをバックグラウンドで始める。(json パス, ExportJob) を返す。"""
        from . import export

        json_path = self.export_unity(variant, out_dir)
        job = export.ExportJob(self.export_meshes(), self.fbx_path(out_dir))
        return json_path, job


_current = Session()


def on_scene_opened() -> None:
    """SceneOpened イベント（userSetup.py で登録）: シーンに記録された Look があれば開く。

    編集中の Look に未保存の変更があるときは上書きしない（作業を消さない）。
    """
    path = preview.remembered_look_path()
    if not path or not Path(path).exists():
        return
    if _current.dirty:
        print(f"[T-Drive] 未保存の Look があるため自動では開きません: {path}")
        return
    try:
        _current.open(path)
        print(f"[T-Drive] Look を開きました: {preview.to_repo_path(path)}")
    except Exception as exc:  # 壊れた Look でシーンを開く操作自体は止めない
        print(f"[T-Drive] Look を開けませんでした: {exc}")


def current() -> Session:
    return _current
