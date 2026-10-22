"""Unity 用の分割出力の計画（docs/20 §6。Maya 非依存）: ファイル名・出力先のプリセット・「変更のあるクリップだけ」の記録。

- モデル `SK_<ModelId>.fbx`（メッシュ + スキン + スケルトン、アニメ無し）と、クリップごとの `A_<ModelId>_<動作>.fbx`（ジョイントだけ・ベイク済み・1 テイク）
- **`@` は使わない**（D-Drive の規則）。ファイル名はクリップ名そのもの
- 出力先: 既定 `<プロジェクト>/build/unity/<ModelId>/`（モデル）と `…/anim/`（アニメ）。プリセット:
  - `ms2026`: `<MS2026>/Assets/_Project/DDrive/SourceAssets/Model|Anim/<Category>/`
  - `ddrive`: `<D-Drive>/Assets/SourceAssets/Model|Anim/<Category>/`
  - `custom`: animset の `export.modelDir` / `export.animDir`
  MS2026・D-Drive のパスはプロジェクト設定（`.tdrive/project.json` の `animMs2026Root` / `animDdriveRoot`）に持つ。未設定のプリセットは使えない
- 「変更のあるクリップだけ」: クリップごとの指紋（範囲内のカーブ + 属性のハッシュ）を `anim/<ModelId>/.export-state.json`（Git 管理外）に記録し、
  指紋が同じで出力ファイルがあれば飛ばす
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .fileio import write_text_atomic

PRESETS = ("default", "ms2026", "ddrive", "custom")
PRESET_LABEL = {"default": "既定（build/unity/<ModelId>）", "ms2026": "MS2026 の SourceAssets", "ddrive": "D-Drive の SourceAssets", "custom": "animset の指定先"}
STATE_NAME = ".export-state.json"
MS2026_SUB = Path("Assets") / "_Project" / "DDrive" / "SourceAssets"
DDRIVE_SUB = Path("Assets") / "SourceAssets"
CONFIG_KEYS = {"ms2026": "animMs2026Root", "ddrive": "animDdriveRoot"}


class ExportPlanError(ValueError):
    pass


def model_file(model_id: str) -> str:
    return f"SK_{model_id}.fbx"


def clip_file(clip_name: str) -> str:
    """クリップのファイル名。`@` を含む名前は使えない（D-Drive の規則）。"""
    if "@" in clip_name:
        raise ExportPlanError(f"クリップ名に「@」は使えません: {clip_name}")
    return f"{clip_name}.fbx"


def resolve_dirs(animset: dict[str, Any], preset: str, config: dict[str, Any], project_root: str | Path) -> tuple[Path, Path]:
    """(モデルの出力先, アニメの出力先)。プリセットが使えないとき（パス未設定・指定先が空）は ExportPlanError。"""
    mid = animset["modelId"]
    cat = (animset.get("export") or {}).get("category") or "Player"
    if preset == "default":
        base = Path(project_root) / "build" / "unity" / mid
        return base, base / "anim"
    if preset in CONFIG_KEYS:
        root = config.get(CONFIG_KEYS[preset])
        if not root:
            raise ExportPlanError(f"{PRESET_LABEL[preset]} のパスが設定されていません（プロジェクト設定 {CONFIG_KEYS[preset]}）")
        sub = MS2026_SUB if preset == "ms2026" else DDRIVE_SUB
        return Path(root) / sub / "Model" / cat, Path(root) / sub / "Anim" / cat
    if preset == "custom":
        exp = animset.get("export") or {}
        if not exp.get("modelDir") or not exp.get("animDir"):
            raise ExportPlanError("animset の export.modelDir / animDir が空です")
        return Path(exp["modelDir"]), Path(exp["animDir"])
    raise ExportPlanError(f"出力先のプリセットが不明です: {preset}")


def resolve_shot_dir(shot: dict[str, Any], preset: str, config: dict[str, Any], project_root: str | Path) -> Path:
    """ショットの出力先（docs/20 §5.3）。既定 `build/cutscene/<Category>/`、MS2026 / D-Drive は `…/SourceAssets/Cutscene/<Category>/`、custom は shot の export.dir。"""
    cat = (shot.get("export") or {}).get("category") or "Cutscene"
    if preset == "default":
        return Path(project_root) / "build" / "cutscene" / cat
    if preset in CONFIG_KEYS:
        root = config.get(CONFIG_KEYS[preset])
        if not root:
            raise ExportPlanError(f"{PRESET_LABEL[preset]} のパスが設定されていません（プロジェクト設定 {CONFIG_KEYS[preset]}）")
        return Path(root) / (MS2026_SUB if preset == "ms2026" else DDRIVE_SUB) / "Cutscene" / cat
    if preset == "custom":
        d = (shot.get("export") or {}).get("dir")
        if not d:
            raise ExportPlanError("shot.json の export.dir が空です")
        return Path(d)
    raise ExportPlanError(f"出力先のプリセットが不明です: {preset}")


def shot_file(shot_name: str) -> str:
    """カメラ・小道具・イベントの FBX 名（`<Shot>.fbx`）。キャラクターは `shot.character_files` の `<Shot>__<ModelId>`。"""
    if "@" in shot_name:
        raise ExportPlanError(f"ショット名に「@」は使えません: {shot_name}")
    return f"{shot_name}.fbx"


def available_presets(animset: dict[str, Any], config: dict[str, Any]) -> dict[str, str]:
    """プリセット → 使えないときの理由（使えるなら ""）。"""
    out = {}
    for p in PRESETS:
        try:
            resolve_dirs(animset, p, config, ".")
            out[p] = ""
        except ExportPlanError as exc:
            out[p] = str(exc)
    return out


# ------------------------------------------------------------------ 変更のあるものだけ
def state_path(animset_path: str | Path) -> Path:
    return Path(animset_path).with_name(STATE_NAME)


def load_state(path: str | Path) -> dict[str, Any]:
    """出力の記録。無い・壊れているときは空（全部が「変更あり」になるだけ。止めない）。"""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) and isinstance(raw.get("clips"), dict) else {"clips": {}}
    except (OSError, ValueError):
        return {"clips": {}}


def save_state(path: str | Path, state: dict[str, Any]) -> None:
    write_text_atomic(path, json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def changed_clips(clips: list[str], fingerprints: dict[str, str], state: dict[str, Any], anim_dir: str | Path) -> list[str]:
    """出力が要るクリップ: 記録が無い・指紋が違う・出力ファイルが無い。"""
    out = []
    for name in clips:
        rec = state.get("clips", {}).get(name)
        file = Path(anim_dir) / clip_file(name)
        if rec is None or rec.get("fingerprint") != fingerprints.get(name) or not file.is_file() or rec.get("dir") != str(Path(anim_dir)):
            out.append(name)
    return out


def record(state: dict[str, Any], name: str, fingerprint: str, anim_dir: str | Path) -> None:
    state.setdefault("clips", {})[name] = {"fingerprint": fingerprint, "dir": str(Path(anim_dir))}
