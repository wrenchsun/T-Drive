"""命名タブの Maya 側（docs/20 §2.5）: シーンの検査・改名の適用・掃除候補・シーン設定の修正・Humanoid 対応表。

- 規則そのものは `core/naming_rules.py`（Maya 非依存）。ここは「シーンから名前を集める」「改名を 1 回の Undo で適用する」だけ
- **元マテリアル・元の割り当ては壊さない**: 改名はノード名を変えるだけ（割り当てはノードのつながりなので保たれる）。
  T-Drive のプレビュー用シェーダー（`<名前>_tdToon`）は名前と `tdSourceMaterial` を追従させる。Look の名前も追従させる（`core/look_follow.py`）
- テクスチャの改名は**提案だけ**（ファイル名も変わるため。実施は人。docs/20 §9 Q-6）
- dx11Shader・Render Override は使わない。DirectX 11 でない環境でも動く
"""

from __future__ import annotations

import contextlib
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from maya import cmds

from tdrive import project

from .core import animset as A
from .core import humanoid_map as H
from .core import look_follow
from .core import naming_rules as N

PREVIEW_SUFFIX = "_tdToon"  # tdrive_toon.preview.SUFFIX と同じ約束（プレビュー用。出力対象外）
PREVIEW_SOURCE_ATTR = "tdSourceMaterial"
DEFAULT_MATERIALS = {"lambert1", "particleCloud1", "standardSurface1"}
DEFAULT_SGS = {"initialShadingGroup", "initialParticleSE"}
TIME_UNITS = {30: "ntsc", 60: "ntscf"}  # Maya の時間単位: ntsc = 30 fps、ntscf = 60 fps、film = 24 fps


# ------------------------------------------------------------------ 結果の型
@dataclass
class SettingIssue:
    key: str  # "time" | "linear" | "up" | "root"
    label: str
    current: str
    wanted: str
    severity: str
    fixable: bool = True


@dataclass
class CleanupItem:
    kind: str  # "material" | "file" | "namespace"
    label: str
    nodes: list[str] = field(default_factory=list)
    namespace: str = ""


@dataclass
class Report:
    findings: list[N.Finding] = field(default_factory=list)
    settings: list[SettingIssue] = field(default_factory=list)
    cleanup: list[CleanupItem] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)  # 規則ファイルが壊れている など（止めない）

    @property
    def errors(self) -> list:
        return [f for f in self.findings if f.is_error] + [s for s in self.settings if s.severity == N.ERROR]

    @property
    def has_errors(self) -> bool:
        return bool(self.errors)


# ------------------------------------------------------------------ 規則
def profile() -> tuple[N.Profile, list[str]]:
    d = project.anim_dir()
    return N.load_profile(d / "_naming.json", d / "_vocabulary.json")


# ------------------------------------------------------------------ 集める
def _is_td_node(name: str) -> bool:
    short = N.strip_namespace(name)
    return short.startswith("td") and (len(short) == 2 or short[2].isupper() or short[2] == "_")


def _is_referenced(node: str) -> bool:
    try:
        return bool(cmds.referenceQuery(node, isNodeReferenced=True))
    except RuntimeError:
        return False


def scene_meshes() -> list[str]:
    """メッシュの transform（完全パス）。中間オブジェクト・T-Drive のプレビュー専用・`td` 接頭辞のノードは除く。"""
    out: list[str] = []
    for shape in cmds.ls(type="mesh", long=True, noIntermediate=True) or []:
        parent = (cmds.listRelatives(shape, parent=True, fullPath=True) or [""])[0]
        if not parent or _is_td_node(parent.split("|")[-1]) or cmds.attributeQuery("tdPreviewOnly", node=parent, exists=True):
            continue
        if parent not in out:
            out.append(parent)
    return out


def _surface_shader(sg: str) -> str | None:
    for plug in ("surfaceShader", "aiSurfaceShader"):
        if cmds.attributeQuery(plug, node=sg, exists=True):
            src = cmds.listConnections(f"{sg}.{plug}", source=True, destination=False) or []
            if src:
                return src[0]
    return None


def preview_sources() -> dict[str, str]:
    """T-Drive のプレビュー用シェーダー（`*_tdToon`）の元マテリアル（シーンのノード名）→ プレビュー用シェーダー。

    Toon 表示が ON のまま保存されたシーンでは、割り当ては元ではなくプレビュー用 SG にあるので、元マテリアルを「未使用」と
    誤解しないために使う。名前と属性（tdSourceMaterial / tdSourceNamespace）だけを見る（dx11Shader には触れない）。
    """
    out: dict[str, str] = {}
    for node in cmds.ls(f"*{PREVIEW_SUFFIX}") or []:
        if not cmds.attributeQuery(PREVIEW_SOURCE_ATTR, node=node, exists=True):
            continue
        doc = cmds.getAttr(f"{node}.{PREVIEW_SOURCE_ATTR}") or ""
        ns = cmds.getAttr(f"{node}.tdSourceNamespace") if cmds.attributeQuery("tdSourceNamespace", node=node, exists=True) else ""
        src = f"{ns}:{doc}" if ns else doc
        if doc and cmds.objExists(src):
            out[src] = node
    return out


def scene_materials() -> dict[str, str]:
    """メッシュに割り当てられている元マテリアル名 → その shadingEngine。プレビュー用・既定のマテリアルは除く。"""
    out: dict[str, str] = {}
    sources = preview_sources()
    for sg in cmds.ls(type="shadingEngine") or []:
        if sg in DEFAULT_SGS or not cmds.sets(sg, query=True):
            continue
        shader = _surface_shader(sg)
        if shader and shader.endswith(PREVIEW_SUFFIX):  # Toon 表示 ON: 元マテリアルを数える
            shader = next((src for src, prev in sources.items() if prev == shader), None)
        if not shader or shader in DEFAULT_MATERIALS or shader.endswith(PREVIEW_SUFFIX) or _is_td_node(shader):
            continue
        out.setdefault(shader, sg)
    return out


def scene_textures() -> list[tuple[str, str]]:
    """(file ノード, ファイル名) 。つながっている file ノードだけ。"""
    out, seen = [], set()
    for f in cmds.ls(type="file") or []:
        if not cmds.listConnections(f, source=False, destination=True):
            continue
        path = cmds.getAttr(f"{f}.fileTextureName") or ""
        name = path.replace("\\", "/").rsplit("/", 1)[-1]
        if name and name not in seen:  # 同じファイルを指す file ノードは 1 行にまとめる
            seen.add(name)
            out.append((f, name))
    return out


def scene_settings(frame_rate: int, skeleton_root: str = "") -> list[SettingIssue]:
    out: list[SettingIssue] = []
    wanted_time = TIME_UNITS.get(frame_rate, "ntsc")
    cur = cmds.currentUnit(query=True, time=True)
    if cur != wanted_time:
        out.append(SettingIssue("time", "時間単位（フレームレート）", f"{cur}（{_fps_label(cur)}）", f"{wanted_time}（{frame_rate} fps）", N.ERROR))
    if cmds.currentUnit(query=True, linear=True) != "cm":
        out.append(SettingIssue("linear", "長さの単位", cmds.currentUnit(query=True, linear=True), "cm", N.ERROR))
    if cmds.upAxis(query=True, axis=True) != "y":
        out.append(SettingIssue("up", "上方向", cmds.upAxis(query=True, axis=True), "y", N.ERROR))
    if skeleton_root and not cmds.objExists(skeleton_root):
        out.append(SettingIssue("root", "スケルトンのルートジョイント", f"{skeleton_root} がシーンにありません", skeleton_root, N.ERROR, fixable=False))
    return out


def clips_without_keys(animset: dict) -> list[str]:
    """範囲にキーが 1 つも無いクリップの名前（fps を変えたあとにクリップ範囲がキーとずれたのを見つける。AN-F-13）。
    mGear のようにキーがジョイントでなくコントローラにあるリグもあるので、シーンのすべてのアニメーションカーブを見る。"""
    curves = cmds.ls(type=["animCurveTL", "animCurveTA", "animCurveTU", "animCurveTT"]) or []
    if not curves:
        return [c["name"] for c in animset.get("clips", [])]
    out = []
    for c in animset.get("clips", []):
        n = cmds.keyframe(curves, query=True, time=(c["start"], c["end"]), keyframeCount=True) or 0
        if not n:
            out.append(c["name"])
    return out


def scene_fps() -> int | None:
    """シーンの時間単位が 30 / 60 fps のときそのフレームレート、それ以外（film の 24 fps など）は None。"""
    return {"ntsc": 30, "ntscf": 60}.get(cmds.currentUnit(query=True, time=True))


TIME_UNIT_FPS = {"film": 24, "ntsc": 30, "ntscf": 60, "pal": 25, "game": 15, "show": 48}


def scene_fps_exact() -> int | None:
    """シーンの時間単位のフレームレート（film の 24 fps なども）。知らない単位は None。"""
    return TIME_UNIT_FPS.get(cmds.currentUnit(query=True, time=True))


def _fps_label(unit: str) -> str:
    return {"film": "24 fps", "ntsc": "30 fps", "ntscf": "60 fps", "pal": "25 fps", "game": "15 fps", "show": "48 fps"}.get(unit, unit)


def cleanup_candidates() -> list[CleanupItem]:
    """未使用のマテリアル / file、空のネームスペース。削除は人が選ぶ（ここでは何も消さない）。"""
    out: list[CleanupItem] = []
    sources = preview_sources()
    for sg in cmds.ls(type="shadingEngine") or []:
        if sg in DEFAULT_SGS or cmds.sets(sg, query=True) or _is_referenced(sg):
            continue
        shader = _surface_shader(sg)
        if not shader or shader in DEFAULT_MATERIALS or shader.endswith(PREVIEW_SUFFIX) or _is_td_node(shader):
            continue  # プレビュー用は T-Drive が作り直す
        if shader in sources:
            continue  # Toon 表示 ON で割り当てがプレビュー側にある元マテリアル（未使用ではない）
        out.append(CleanupItem("material", f"未使用のマテリアル: {shader}", [shader, sg]))
    for f in cmds.ls(type="file") or []:
        if not cmds.listConnections(f, source=False, destination=True) and not _is_referenced(f):
            out.append(CleanupItem("file", f"未使用のテクスチャノード: {f}", [f]))
    for ns in sorted(cmds.namespaceInfo(listOnlyNamespaces=True, recurse=True) or [], key=lambda n: -n.count(":")):
        if ns in ("UI", "shared"):
            continue
        if not cmds.namespaceInfo(ns, listNamespace=True) and not cmds.namespaceInfo(ns, listOnlyNamespaces=True):
            out.append(CleanupItem("namespace", f"空のネームスペース: {ns}", namespace=ns))
    return out


# ------------------------------------------------------------------ 検査
def inspect_scene(model_id: str, animset: dict[str, Any] | None = None) -> Report:
    """シーンと animset を規則で検査する。何も変更しない。"""
    prof, warnings = profile()
    rep = Report(warnings=warnings)
    for mesh in scene_meshes():
        f = N.inspect(N.MESH, mesh.split("|")[-1], model_id, prof, node=mesh)
        if f:
            rep.findings.append(f)
    for shader in scene_materials():
        f = N.inspect(N.MATERIAL, shader, model_id, prof, node=shader)
        if f:
            rep.findings.append(f)
    for node, fname in scene_textures():
        f = N.inspect(N.TEXTURE, fname, model_id, prof, node=node)
        if f:
            rep.findings.append(f)
    for clip in (animset or {}).get("clips", []):
        f = N.inspect(N.CLIP, clip["name"], model_id, prof, node=clip["name"])
        if f:
            rep.findings.append(f)
    taken = {N.strip_namespace(n) for n in cmds.ls(long=False) or []} | {c["name"] for c in (animset or {}).get("clips", [])}
    N.unique_suggestions(rep.findings, taken)
    rep.settings = scene_settings((animset or {}).get("frameRate", prof.frame_rate), A.root_name(animset) if animset else "")
    rep.cleanup = cleanup_candidates()
    return rep


# ------------------------------------------------------------------ 改名の適用（1 回の Undo）
@contextlib.contextmanager
def undo_chunk(name: str):
    cmds.undoInfo(openChunk=True, chunkName=name)
    try:
        yield
    finally:
        cmds.undoInfo(closeChunk=True)


def _ns_of(node: str) -> str:
    short = node.split("|")[-1]
    return short.rsplit(":", 1)[0] if ":" in short else ""


def apply_renames(findings: list[N.Finding]) -> dict[str, Any]:
    """メッシュ・マテリアルの提案名を適用する。**1 回の Undo**。テクスチャ・クリップは対象外（呼び出し側が扱う）。

    戻り値: {"renamed": [(旧, 新)], "skipped": [(名前, 理由)], "materials": {旧: 新}, "meshes": {旧: 新}}
    """
    todo = [f for f in findings if f.suggestion and not f.proposal_only and f.kind in (N.MESH, N.MATERIAL) and cmds.objExists(f.node)]
    result: dict[str, Any] = {"renamed": [], "skipped": [], "materials": {}, "meshes": {}}
    uuids = [(f, cmds.ls(f.node, uuid=True)[0]) for f in todo]
    with undo_chunk("tdAnimRename"):
        for f, uid in uuids:
            nodes = cmds.ls(uid, long=True)
            if not nodes:
                result["skipped"].append((f.name, "ノードが見つかりません"))
                continue
            node = nodes[0]
            if _is_referenced(node):
                result["skipped"].append((f.name, "参照したファイルのノードは改名できません"))
                continue
            ns = _ns_of(node)
            target = f"{ns}:{f.suggestion}" if ns else f.suggestion
            if cmds.objExists(target) and cmds.ls(target, uuid=True)[0] != uid:
                result["skipped"].append((f.name, f"{f.suggestion} は既にあります"))
                continue
            old_short = node.split("|")[-1]
            try:
                new_node = cmds.rename(node, target)
            except RuntimeError as exc:
                result["skipped"].append((f.name, str(exc)))
                continue
            result["renamed"].append((f.name, N.strip_namespace(new_node)))
            if f.kind == N.MATERIAL:
                result["materials"][N.strip_namespace(old_short)] = f.suggestion
                _follow_preview(old_short, f.suggestion, ns)
            else:
                result["meshes"][N.strip_namespace(old_short)] = f.suggestion
    return result


def _follow_preview(old_short: str, new_name: str, ns: str) -> None:
    """プレビュー用シェーダー（`[ns_]<旧名>_tdToon`）を新しい名前に追従させる。無ければ何もしない。"""
    old_doc = N.strip_namespace(old_short)
    pre = ns.replace(":", "_") + "_" if ns else ""
    shader = f"{pre}{old_doc}{PREVIEW_SUFFIX}"
    if not cmds.objExists(shader):
        return
    if cmds.attributeQuery(PREVIEW_SOURCE_ATTR, node=shader, exists=True):
        cmds.setAttr(f"{shader}.{PREVIEW_SOURCE_ATTR}", new_name, type="string")
    target = f"{pre}{new_name}{PREVIEW_SUFFIX}"
    if not cmds.objExists(target):
        cmds.rename(shader, target)


# ------------------------------------------------------------------ Look の追従
def plan_look_follow(materials: dict[str, str], meshes: dict[str, str] | None = None) -> dict[str, Any]:
    """`follow_look` が何を書き換えるか（書き換えない）: {"open": Toon で Look が開いているか, "files": [(look.json のパス, 書き換える箇所数)]}。
    Look が開いていなければ `looks/*/look.json` のファイルを直接書き換え、Maya の Ctrl+Z では戻らない（SF-11）ので、適用の前に見せて確かめるために使う。"""
    meshes = meshes or {}
    out: dict[str, Any] = {"open": False, "files": []}
    if not materials and not meshes:
        return out
    try:
        from tdrive_toon import look as look_mod
        from tdrive_toon import session as toon
    except Exception:  # noqa: BLE001  Toon が読めなければ書き換えもできない
        return out
    if toon.current().look is not None:
        out["open"] = True
        return out
    for path in sorted(project.looks_dir().glob("*/look.json")):
        try:
            lk = look_mod.load(path)
        except Exception:  # noqa: BLE001  壊れた Look は書き換えない
            continue
        n = look_follow.rename_materials(lk, materials) + look_follow.rename_meshes(lk, meshes)
        if n:
            out["files"].append((path, n))
    return out


def follow_look(materials: dict[str, str], meshes: dict[str, str] | None = None, write_files: bool = True) -> list[str]:
    """Look の名前を追従させる。開いている Look（Toon のセッション）があればそれ（未保存の変更として残る）、
    無ければプロジェクトの `looks/*/look.json` のうち旧名を持つもの（`write_files=True` のときだけファイルを直す。Ctrl+Z では戻らない）。報告の文字列を返す。"""
    msgs: list[str] = []
    meshes = meshes or {}
    if not materials and not meshes:
        return msgs
    try:
        from tdrive_toon import look as look_mod
        from tdrive_toon import session as toon
    except Exception:  # noqa: BLE001  Toon が読めなくても命名は進める
        return [f"Look を更新できませんでした（Toon を読み込めません）: {traceback.format_exc().splitlines()[-1]}"]
    ts = toon.current()
    if ts.look is not None:
        ts.checkpoint()  # Toon のエディタ内 Undo に 1 つ積む
        n = look_follow.rename_materials(ts.look, materials) + look_follow.rename_meshes(ts.look, meshes)
        if n:
            ts._changed()  # noqa: SLF001  画面更新と未保存の印
            msgs.append(f"開いている Look の名前を {n} か所更新しました（Toon タブで保存してください）")
        else:
            ts._undo.pop()  # noqa: SLF001  変わらなかったのでチェックポイントを戻す
        return msgs
    for path in sorted(project.looks_dir().glob("*/look.json")):
        try:
            lk = look_mod.load(path)
        except Exception:  # noqa: BLE001  壊れた Look は警告だけ
            msgs.append(f"{project.to_project_path(path)} を読めないので更新しませんでした")
            continue
        n = look_follow.rename_materials(lk, materials) + look_follow.rename_meshes(lk, meshes)
        if n and write_files:
            look_mod.save(lk, path)
            msgs.append(f"{project.to_project_path(path)} の名前を {n} か所更新しました（ファイルを直接書き換えたので Ctrl+Z では戻りません）")
        elif n:
            msgs.append(f"{project.to_project_path(path)} は旧い名前のままです（{n} か所）。Toon タブでこの Look を開いてから、もう一度改名を適用するか、手で直してください")
    return msgs


# ------------------------------------------------------------------ シーン設定の修正
def time_change_ratio(frame_rate: int = 30) -> float:
    """シーンの時間単位を animset の fps にするとき、キーの時刻を保つとフレーム番号が何倍になるか（24 → 30 fps で 1.25）。"""
    cur = TIME_UNIT_FPS.get(cmds.currentUnit(query=True, time=True))
    return frame_rate / cur if cur else 1.0


def fix_settings(keys: list[str], frame_rate: int = 30, keep_frames: bool = False) -> list[str]:
    """時間単位（フレームレート）・長さの単位・上方向を直す。1 回の Undo。直したキーを返す。
    時間単位は、既定では Maya の既定どおりキーの時刻（実時間）を保つ（フレーム番号が変わる。呼ぶ側がクリップの範囲を比率で直す）。
    `keep_frames=True` はフレーム番号を保つ（`updateAnimation=False`。再生速度が変わる）。"""
    fixed: list[str] = []
    with undo_chunk("tdAnimSceneSettings"):
        for key in keys:
            if key == "time":
                cmds.currentUnit(time=TIME_UNITS.get(frame_rate, "ntsc"), updateAnimation=not keep_frames)
            elif key == "linear":
                cmds.currentUnit(linear="cm")
            elif key == "up":
                cmds.upAxis(axis="y", rotateView=False)
            else:
                continue
            fixed.append(key)
    return fixed


# ------------------------------------------------------------------ 掃除
def apply_cleanup(items: list[CleanupItem]) -> int:
    """選ばれた掃除候補を削除する（既定ではどれも選ばれていない）。1 回の Undo。消した数を返す。"""
    n = 0
    with undo_chunk("tdAnimCleanup"):
        for it in items:
            if it.kind == "namespace":
                if it.namespace and cmds.namespace(exists=it.namespace):
                    with contextlib.suppress(RuntimeError):
                        cmds.namespace(removeNamespace=it.namespace)
                        n += 1
                continue
            existing = [x for x in it.nodes if cmds.objExists(x)]
            if existing:  # 空のリストを cmds.delete に渡さない
                cmds.delete(existing)
                n += 1
    return n


# ------------------------------------------------------------------ Humanoid 対応表
def humanoid_path(model_id: str) -> Path:
    return project.anim_dir() / model_id / "humanoid.json"


def scene_joints(root: str) -> list[str]:
    """ルートとその子孫のジョイント名（短い名前）。ルートが無ければ空。"""
    if not root or not cmds.objExists(root):
        return []
    found = cmds.listRelatives(root, allDescendents=True, type="joint", fullPath=False) or []
    return [N.strip_namespace(root)] + [N.strip_namespace(j) for j in reversed(found)]


def scene_parents(joints: list[str], ns: str = "") -> dict[str, str | None]:
    """ジョイント名（ネームスペースなし）→ 親のジョイント名。`ns` はシーンでのネームスペース。"""
    out: dict[str, str | None] = {}
    have = set(joints)
    pre = f"{ns}:" if ns else ""
    for j in joints:
        if not cmds.objExists(pre + j):
            continue
        p = (cmds.listRelatives(pre + j, parent=True, type="joint") or [None])[0]
        p = N.strip_namespace(p) if p else None
        out[j] = p if p in have else None
    return out


def humanoid_for(model_id: str, root: str, ns: str = "") -> dict[str, Any]:
    """保存済みの対応表があればそれ、無ければシーンのジョイントから推定する（保存はしない）。壊れたファイルは推定にする。"""
    path = humanoid_path(model_id)
    if path.exists():
        try:
            return H.load(path)
        except H.HumanoidMapError:
            pass
    return H.new_map(model_id, scene_joints(f"{ns}:{root}" if ns else root), root)


def humanoid_problems(data: dict[str, Any], root: str, ns: str = "") -> list[str]:
    joints = scene_joints(f"{ns}:{root}" if ns else root)
    return H.validate(data, joints=joints, parents=scene_parents(joints, ns))
