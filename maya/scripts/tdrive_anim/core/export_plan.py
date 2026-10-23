"""Unity 用の分割出力の計画（docs/20 §6。Maya 非依存）: ファイル名・出力先のプリセット・「変更のあるクリップだけ」の記録。

- モデル `SK_<ModelId>.fbx`（メッシュ + スキン + スケルトン、アニメ無し）と、クリップごとの `A_<ModelId>_<動作>.fbx`（ジョイントだけ・ベイク済み・1 テイク）
- **`@` は使わない**（D-Drive の規則）。ファイル名はクリップ名そのもの
- 出力先: 既定 `<プロジェクト>/build/unity/<ModelId>/`（モデル）と `…/anim/`（アニメ）。もう 1 つは**共有フォルダ**（任意のフォルダ。Google Drive など）で、
  その下に `Model/<Category>/`・`Anim/<Category>/`・`Cutscene/<Category>/` の構成で作る（MS2026 の `SourceAssets` と同じ下位フォルダ名。AN-F-18）。
  共有フォルダのパスはプロジェクト設定（`.tdrive/project.json` の `animSharedRoot`。ユーザーごと・Git 管理外）に持つ。未設定なら使えない
- 「変更のあるクリップだけ」: クリップごとの指紋（範囲内のカーブ + 属性のハッシュ）を `anim/<ModelId>/.export-state.json`（Git 管理外）に記録し、
  指紋が同じで出力ファイルがあれば飛ばす
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .fileio import write_text_atomic

PRESETS = ("default", "shared")
PRESET_LABEL = {"default": "既定（build/unity/<ModelId>）", "shared": "共有フォルダ…"}
STATE_NAME = ".export-state.json"
SHARED_KEY = "animSharedRoot"


class ExportPlanError(ValueError):
    pass


def model_file(model_id: str) -> str:
    return f"SK_{model_id}.fbx"


def clip_file(clip_name: str) -> str:
    """クリップのファイル名。`@` を含む名前は使えない（D-Drive の規則）。"""
    if "@" in clip_name:
        raise ExportPlanError(f"クリップ名に「@」は使えません: {clip_name}")
    return f"{clip_name}.fbx"


def _shared_root(config: dict[str, Any]) -> Path:
    root = config.get(SHARED_KEY)
    if not root:
        raise ExportPlanError(f"共有フォルダが設定されていません（出力タブの「共有フォルダ…」で選んでください。プロジェクト設定 {SHARED_KEY}）")
    return Path(root)


def resolve_dirs(animset: dict[str, Any], preset: str, config: dict[str, Any], project_root: str | Path) -> tuple[Path, Path]:
    """(モデルの出力先, アニメの出力先)。プリセットが使えないとき（共有フォルダ未設定）は ExportPlanError。"""
    mid = animset["modelId"]
    cat = (animset.get("export") or {}).get("category") or "Player"
    if preset == "default":
        base = Path(project_root) / "build" / "unity" / mid
        return base, base / "anim"
    if preset == "shared":
        root = _shared_root(config)
        return root / "Model" / cat, root / "Anim" / cat
    raise ExportPlanError(f"出力先のプリセットが不明です: {preset}")


def resolve_shot_dir(shot: dict[str, Any], preset: str, config: dict[str, Any], project_root: str | Path) -> Path:
    """ショットの出力先（docs/20 §5.3）。既定 `build/cutscene/<Category>/`、共有フォルダは `<共有>/Cutscene/<Category>/`。"""
    cat = (shot.get("export") or {}).get("category") or "Cutscene"
    if preset == "default":
        return Path(project_root) / "build" / "cutscene" / cat
    if preset == "shared":
        return _shared_root(config) / "Cutscene" / cat
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
