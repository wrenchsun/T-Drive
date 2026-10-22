"""MS2026 の命名規則の検査・提案（docs/20 §2。Maya 非依存）。

規則の出典は MS2026 `Docs/Rules/FolderStructure.md` §3 と D-Drive のカットシーン規則（docs/20 §0.1）。
D-Drive の思想「命名規則は人が守るものではなくツールが生成・維持する」に沿って、検査（`inspect`）と提案（`suggest_*`）を同じ場所に持つ。

- 重大度: `error` = 出力が止まる（禁止文字・`@`・捨て名・版の接尾辞・クリップ名の形）、`warning` = 直すのがおすすめ（接頭辞・語彙外の動作）
- プロファイル（`Profile`）は既定が MS2026。プロジェクトの `anim/_naming.json`（接頭辞・語彙・禁止語・部位の言い換え）と
  `anim/_vocabulary.json`（動作の語彙の追加）で上書きできる。壊れたファイルは既定のまま警告を返す（止めない）
- ネームスペース（`MisotenChara:`）は検査の前に呼び出し側が外す（`strip_namespace`）
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ERROR, WARNING = "error", "warning"

# 種類（Finding.kind）
MESH, MATERIAL, TEXTURE, CLIP, SHOT, CAMERA, PROP, EVENT = "mesh", "material", "texture", "clip", "shot", "camera", "prop", "event"

DEFAULT_VOCABULARY = [
    "Idle", "Walk", "Run", "Jump", "Fall", "Land", "Interact", "Stunned",
    "Win", "Lose", "Damaged", "Wave", "Talk", "Pose",
]
TEXTURE_KINDS = ["BaseColor", "Normal", "Mask", "Emission", "MatCap", "Sdf"]
DIRECTIONS = ("F", "B", "L", "R")

# Maya が付ける捨て名（pCube1 / polySurface12 / lambert2 …）
_AUTO_NAME = re.compile(
    r"^(p(Cube|Sphere|Cylinder|Cone|Plane|Torus|Pipe|Disc|Prism|Pyramid|Helix|Solid)|polySurface|nurbs[A-Za-z]*|group|null|joint|locator|"
    r"transform|camera|lambert|blinn|phong|phongE|standardSurface|file|place2dTexture|aiStandardSurface)\d+$"
)
_DISCARD = {"test", "aaa", "temp", "tmp", "dummy", "new", "untitled"}
_BAD_CHARS = re.compile(r"[#%&+@\s]")
_VERSION_SUFFIX = re.compile(r"_?v\d+$", re.IGNORECASE)
_PASCAL = re.compile(r"^[A-Z][A-Za-z0-9]*$")


@dataclass
class Profile:
    """規則の値。既定は MS2026。"""

    mesh_prefix: str = "SK"
    static_mesh_prefix: str = "SM"
    material_prefix: str = "M"
    texture_prefix: str = "T"
    clip_prefix: str = "A"
    camera_prefix: str = "CAM"
    prop_prefix: str = "PRP"
    event_prefix: str = "EVT"
    vocabulary: list[str] = field(default_factory=lambda: list(DEFAULT_VOCABULARY))
    texture_kinds: list[str] = field(default_factory=lambda: list(TEXTURE_KINDS))
    forbidden_words: list[str] = field(default_factory=lambda: sorted(_DISCARD))
    # 小文字の語 → 部位名（提案に使う）。例 ribbonback → RibbonBack
    part_words: dict[str, str] = field(default_factory=lambda: {"ribbonback": "RibbonBack", "ribbonfront": "RibbonFront"})
    # 提案の前に取り除く読み込み時の接頭辞（正規表現）。MisotenChara の「MisotenChara_Rig5_IdolGirl_」など
    noise_prefixes: list[str] = field(default_factory=lambda: [r"^[A-Za-z0-9]*_Rig\d*_", r"^IdolGirl_"])
    frame_rate: int = 30


def default_profile() -> Profile:
    return Profile()


def load_profile(naming_json: str | Path | None = None, vocabulary_json: str | Path | None = None) -> tuple[Profile, list[str]]:
    """プロファイルを読む。ファイルが無ければ既定。壊れていれば既定のまま警告を返す（止めない）。"""
    p, warnings = Profile(), []
    keymap = {
        "meshPrefix": "mesh_prefix", "staticMeshPrefix": "static_mesh_prefix", "materialPrefix": "material_prefix",
        "texturePrefix": "texture_prefix", "clipPrefix": "clip_prefix", "cameraPrefix": "camera_prefix",
        "propPrefix": "prop_prefix", "eventPrefix": "event_prefix",
    }
    if naming_json and Path(naming_json).exists():
        try:
            raw = json.loads(Path(naming_json).read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("オブジェクトではありません")
            for k, attr in keymap.items():
                if isinstance(raw.get(k), str) and raw[k]:
                    setattr(p, attr, raw[k])
            for k, attr in (("vocabulary", "vocabulary"), ("textureKinds", "texture_kinds"), ("forbiddenWords", "forbidden_words"), ("noisePrefixes", "noise_prefixes")):
                if isinstance(raw.get(k), list):
                    setattr(p, attr, [str(x) for x in raw[k]])
            if isinstance(raw.get("partWords"), dict):
                p.part_words = {str(a).lower(): str(b) for a, b in raw["partWords"].items()}
            if isinstance(raw.get("frameRate"), int):
                p.frame_rate = raw["frameRate"]
        except (OSError, ValueError) as exc:
            warnings.append(f"_naming.json を読めないので既定の規則を使います: {exc}")
    if vocabulary_json and Path(vocabulary_json).exists():
        try:
            raw = json.loads(Path(vocabulary_json).read_text(encoding="utf-8"))
            words = raw.get("actions") if isinstance(raw, dict) else raw
            if not isinstance(words, list):
                raise ValueError("配列ではありません")
            p.vocabulary = list(dict.fromkeys(p.vocabulary + [str(w) for w in words]))
        except (OSError, ValueError) as exc:
            warnings.append(f"_vocabulary.json を読めないので既定の語彙を使います: {exc}")
    return p, warnings


@dataclass
class Finding:
    """検査の 1 件（命名タブの一覧の 1 行）。"""

    kind: str
    name: str  # 今の名前
    suggestion: str  # 提案（無ければ ""）
    reason: str
    severity: str
    node: str = ""  # シーンのノード（完全パスなど。改名の対象）
    proposal_only: bool = False  # 提案だけで適用しない（テクスチャ。ファイル名も変わるため）

    @property
    def is_error(self) -> bool:
        return self.severity == ERROR


# ------------------------------------------------------------------ 名前の部品
def strip_namespace(name: str) -> str:
    return name.rsplit(":", 1)[-1].rsplit("|", 1)[-1]


def has_japanese_or_wide(name: str) -> bool:
    return any(ord(c) > 127 for c in name)


def pascal(token: str, profile: Profile | None = None) -> str:
    """単語を PascalCase に（先頭だけ大文字、残りはそのまま。部位の言い換えがあれば優先）。"""
    profile = profile or default_profile()
    alias = profile.part_words.get(token.lower())
    if alias:
        return alias
    return token[:1].upper() + token[1:]


def common_problems(name: str, profile: Profile | None = None) -> list[tuple[str, str]]:
    """どの種類の名前にも共通の禁止事項。[(重大度, 理由)]。"""
    profile = profile or default_profile()
    out: list[tuple[str, str]] = []
    if not name:
        return [(ERROR, "名前が空です")]
    if has_japanese_or_wide(name):
        out.append((ERROR, "日本語・全角文字は使えません"))
    if "@" in name:
        out.append((ERROR, "「@」は使えません（Model@Anim.fbx の分割は禁止。D-Drive の規則）"))
    if re.search(r"\s", name):
        out.append((ERROR, "半角スペースは使えません"))
    bad = sorted(set(re.findall(r"[#%&+]", name)))
    if bad:
        out.append((ERROR, f"使えない文字: {' '.join(bad)}"))
    if name[0].isdigit():
        out.append((ERROR, "先頭に数字は使えません"))
    if name.lower() in {w.lower() for w in profile.forbidden_words}:
        out.append((ERROR, "捨て名（test / aaa など）は使えません"))
    if _AUTO_NAME.match(name):
        out.append((ERROR, "Maya の自動名（pCube1 / polySurface3 / lambert2 など）のままです"))
    if re.search(r"_v\d+$", name, re.IGNORECASE):
        out.append((ERROR, "版の接尾辞（_v2 など）は使えません。版は Git で管理します"))
    return out


def sanitize(name: str) -> str:
    """共通の禁止事項を取り除いた名前（提案の下ごしらえ）。"""
    s = strip_namespace(name)
    s = re.sub(r"_?v\d+$", "", s, flags=re.IGNORECASE)
    s = s.replace("@", "_")
    s = "".join(c if ord(c) < 128 else "_" for c in s)
    s = re.sub(r"[#%&+\s]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s


def part_token(name: str, kind: str, model_id: str, profile: Profile | None = None) -> str:
    """元の名前から「部位の語」を取り出す（提案用）。

    読み込み時の接頭辞・既に付いている規則の接頭辞・`_tdToon`・Maya の末尾数字（`Hair1`）を落とす。
    メッシュは最初の語（`Body_Chest` → `Body`）、マテリアルは全部の語をつなぐ（`IdolGirl_Other2` → `Other2`）。
    """
    profile = profile or default_profile()
    s = sanitize(name)
    s = re.sub(r"_tdToon$", "", s)
    for pre in (f"{profile.mesh_prefix}_{model_id}_", f"{profile.static_mesh_prefix}_{model_id}_", f"{profile.material_prefix}_{model_id}_",
                f"{profile.texture_prefix}_{model_id}_"):
        if s.lower().startswith(pre.lower()):  # 大文字小文字の間違い（sk_misotenchara_ など）でも接頭辞を落とす（docs/21 TS-15）
            s = s[len(pre):]
            break
    for rx in profile.noise_prefixes:
        s2 = re.sub(rx, "", s, count=1)
        if s2 and s2 != s:
            s = s2
    tokens = [t for t in s.split("_") if t]
    if not tokens:
        return "Part"
    if kind == MESH:
        tokens = tokens[:1]
    word = "".join(pascal(t, profile) for t in tokens)
    word = re.sub(r"[^A-Za-z0-9]", "", word)
    return word if word and word[0].isalpha() else "Part" + word


# ------------------------------------------------------------------ 種類ごとの名前の形
def mesh_pattern(model_id: str, profile: Profile) -> re.Pattern:
    return re.compile(rf"^({re.escape(profile.mesh_prefix)}|{re.escape(profile.static_mesh_prefix)})_{re.escape(model_id)}_[A-Z][A-Za-z0-9]*$")


def material_pattern(model_id: str, profile: Profile) -> re.Pattern:
    return re.compile(rf"^{re.escape(profile.material_prefix)}_{re.escape(model_id)}_[A-Z][A-Za-z0-9]*$")


def texture_pattern(model_id: str, profile: Profile) -> re.Pattern:
    kinds = "|".join(re.escape(k) for k in profile.texture_kinds)
    return re.compile(rf"^{re.escape(profile.texture_prefix)}_{re.escape(model_id)}_[A-Z][A-Za-z0-9]*_({kinds})$")


def clip_pattern(model_id: str, profile: Profile) -> re.Pattern:
    return re.compile(rf"^{re.escape(profile.clip_prefix)}_{re.escape(model_id)}_(?P<action>[A-Z][A-Za-z]*?)(?:_(?P<dir>[FBLR]))?(?P<num>\d{{2}})?$")


def suggest_mesh(name: str, model_id: str, profile: Profile | None = None) -> str:
    profile = profile or default_profile()
    return f"{profile.mesh_prefix}_{model_id}_{part_token(name, MESH, model_id, profile)}"


def suggest_material(name: str, model_id: str, profile: Profile | None = None) -> str:
    profile = profile or default_profile()
    return f"{profile.material_prefix}_{model_id}_{part_token(name, MATERIAL, model_id, profile)}"


def suggest_texture(stem: str, model_id: str, profile: Profile | None = None) -> str:
    """テクスチャのファイル名（拡張子なし）の提案。種類は名前から推定（BaseColor を既定）。"""
    profile = profile or default_profile()
    low = stem.lower()
    kind = next((k for k in profile.texture_kinds if k.lower() in low), "")
    if not kind:
        kind = "Normal" if "norm" in low or "nrm" in low else "Mask" if "mask" in low else "Emission" if "emi" in low else "BaseColor"
    base = re.sub(re.escape(kind), "", stem, flags=re.IGNORECASE) if kind.lower() in low else stem
    if re.fullmatch(r"[0-9a-fA-F-]{16,}", sanitize(base)):
        base = "Part"  # uuid のファイル名は部位が分からない
    return f"{profile.texture_prefix}_{model_id}_{part_token(base, TEXTURE, model_id, profile)}_{kind}"


def suggest_clip(name: str, model_id: str, profile: Profile | None = None) -> str:
    profile = profile or default_profile()
    s = strip_namespace(name)
    if "@" in s:  # Model@Anim の分割: 左がクリップ名ならそれ、そうでなければ右（動作）を使う
        left, right = s.split("@", 1)
        s = left if left.startswith(profile.clip_prefix + "_") else right
    s = sanitize(s)
    s = re.sub(rf"^{re.escape(profile.clip_prefix)}_({re.escape(model_id)}_)?", "", s, flags=re.IGNORECASE)
    m = re.match(r"^([A-Za-z]+)(?:_?([FBLR]))?(\d{1,2})?$", s)
    if m:
        action, d, num = m.group(1), m.group(2), m.group(3)
        out = f"{profile.clip_prefix}_{model_id}_{pascal(action.lower() if action.isupper() else action, profile)}"
        if d:
            out += f"_{d}"
        if num:
            out += num.zfill(2)
        return out
    return f"{profile.clip_prefix}_{model_id}_{pascal(re.sub(r'[^A-Za-z0-9]', '', s) or 'Action', profile)}"


def action_word(clip_name: str, model_id: str, profile: Profile | None = None) -> str:
    """クリップ名から動作の語（語彙の照合に使う先頭の語。Run_F → Run、Pose01 → Pose）。"""
    profile = profile or default_profile()
    m = clip_pattern(model_id, profile).match(clip_name)
    if not m:
        return ""
    first = re.match(r"^[A-Z][a-z]*", m.group("action"))
    return first.group(0) if first else m.group("action")


# ------------------------------------------------------------------ 検査
def inspect(kind: str, name: str, model_id: str, profile: Profile | None = None, node: str = "") -> Finding | None:
    """1 つの名前を検査する。問題が無ければ None。問題が複数なら最も重いものを理由の先頭に、残りを「／」でつなぐ。"""
    profile = profile or default_profile()
    shown = strip_namespace(name)
    problems = common_problems(shown, profile)
    suggestion = ""
    if kind == MESH:
        if not mesh_pattern(model_id, profile).match(shown):
            problems.append((WARNING, f"メッシュは {profile.mesh_prefix}_{model_id}_<部位> の形にします"))
            suggestion = suggest_mesh(shown, model_id, profile)
    elif kind == MATERIAL:
        if shown.endswith("_tdToon"):
            return None  # T-Drive のプレビュー用（ツールが作り直す。対象外）
        if not material_pattern(model_id, profile).match(shown):
            problems.append((WARNING, f"マテリアルは {profile.material_prefix}_{model_id}_<部位> の形にします"))
            suggestion = suggest_material(shown, model_id, profile)
    elif kind == TEXTURE:
        stem = shown.rsplit(".", 1)[0]
        problems = common_problems(stem, profile) if "." in shown else problems
        if not texture_pattern(model_id, profile).match(stem):
            problems.append((WARNING, f"テクスチャは {profile.texture_prefix}_{model_id}_<部位>_<種類> の形にします（種類: {' / '.join(profile.texture_kinds)}）"))
            suggestion = suggest_texture(stem, model_id, profile) + (("." + shown.rsplit(".", 1)[1]) if "." in shown else "")
    elif kind == CLIP:
        m = clip_pattern(model_id, profile).match(shown)
        if not m:
            problems.append((ERROR, f"クリップは {profile.clip_prefix}_{model_id}_<動作> の形にします（例: {profile.clip_prefix}_{model_id}_Run_F）"))
            suggestion = suggest_clip(shown, model_id, profile)
        elif action_word(shown, model_id, profile) not in profile.vocabulary:
            problems.append((WARNING, f"語彙にない動作名です（{action_word(shown, model_id, profile)}）。_vocabulary.json に足せます"))
    elif kind == SHOT:
        if not _PASCAL.match(shown):
            problems.append((ERROR, "ショット名は英数字の PascalCase（例: Opening01）"))
            suggestion = pascal(re.sub(r"[^A-Za-z0-9]", "", sanitize(shown)) or "Shot")
        elif "__" in shown:
            problems.append((ERROR, "ショット名に「__」は使えません（<Shot>__<ModelId>.fbx の区切り）"))
    elif kind == CAMERA:
        pass  # 1 台なら自由。複数のときは inspect_cameras が CAM_ を求める
    elif kind == PROP:
        if not shown.startswith(profile.prop_prefix + "_"):
            problems.append((WARNING, f"小道具は {profile.prop_prefix}_<名前> の形にします"))
            suggestion = f"{profile.prop_prefix}_{pascal(part_token(shown, PROP, model_id, profile))}"
    elif kind == EVENT:
        if not shown.startswith(profile.event_prefix + "_"):
            problems.append((WARNING, f"イベントロケータは {profile.event_prefix}_<キー> の形にします"))
            suggestion = f"{profile.event_prefix}_{pascal(part_token(shown, EVENT, model_id, profile))}"
    if kind in (MESH, MATERIAL, PROP, EVENT, CAMERA) and "__" in shown:
        problems.append((WARNING, "「__」はカットシーンのファイル名だけに使います"))
    if not problems:
        return None
    if kind == TEXTURE:  # テクスチャは FBX に入らない（Embed Media OFF）ので、出力は止めない。提案だけ
        problems = [(WARNING, r) for _, r in problems]
    problems.sort(key=lambda p: p[0] != ERROR)  # error を先に
    severity = ERROR if problems[0][0] == ERROR else WARNING
    if not suggestion and severity == ERROR and kind in (MESH, MATERIAL):
        suggestion = suggest_mesh(shown, model_id, profile) if kind == MESH else suggest_material(shown, model_id, profile)
    if suggestion == shown:
        suggestion = ""
    return Finding(kind, shown, suggestion, "／".join(dict.fromkeys(r for _, r in problems)), severity, node=node or name,
                   proposal_only=(kind == TEXTURE))


def inspect_cameras(names: list[str], profile: Profile | None = None) -> list[Finding]:
    """ショット内のカメラ: 1 台なら自由、複数は CAM_<名前>。"""
    profile = profile or default_profile()
    if len(names) <= 1:
        return []
    out = []
    for n in names:
        shown = strip_namespace(n)
        if not shown.startswith(profile.camera_prefix + "_"):
            out.append(Finding(CAMERA, shown, f"{profile.camera_prefix}_{pascal(sanitize(shown))}", f"カメラが複数あるときは {profile.camera_prefix}_<名前> にします", WARNING, node=n))
    return out


def unique_suggestions(findings: list[Finding], taken: set[str] | None = None) -> None:
    """提案名が重複しないように、後ろの提案に 2, 3 … を付ける（既にシーンにある名前・同じ一覧の中の提案を避ける）。"""
    used = set(taken or ())
    for f in findings:
        if not f.suggestion:
            continue
        stem, dot, ext = f.suggestion.partition(".")
        cand, i = f.suggestion, 1
        while cand in used:
            i += 1
            cand = f"{stem}{i}{dot}{ext}"
        f.suggestion = cand
        used.add(cand)
