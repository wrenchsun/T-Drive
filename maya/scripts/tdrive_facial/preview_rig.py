"""カメラ連動プレビュー（Maya のランタイム相当。Maya 依存。docs/14 §5.8、docs/15 §4.4）。

プラグインを使わない（標準ノード + 生成した expression 1 個）。シーンを他の人が開いても壊れない。

```
camera.worldMatrix[0] → decomposeMatrix（<rig>_camDM） ─┐
基準ボーン.worldMatrix[0] → decomposeMatrix（<rig>_headDM）─┼→ expression（<rig>_expr）
tdFacialPreview_<asset>.（enable / alpha / useManual / manualYaw / manualPitch / emotion_<Layer>）─┘
        → blendShape.weight[FC_*]（顔メッシュと target.extraMeshes の同名ターゲット）と outYaw / outPitch
```

- expression の文字列は `core/evaluate.py` と `core/space.py` の式を**データから生成**する（格子・レイヤー・存在する FC_* が変わったら
  作り直す = `is_stale`）。重みの計算は「軸ごとに (index0, index1, frac, fade)」→「列の山形の重み wc[c]」×「行の山形の重み wr[r]」
  × 端のフェード（両軸の積。1e-4 以下は 0）× レイヤーの重み（Neutral = 1、他 = `emotion_<Layer>`）× alpha × enable × 表情での弱め。
  焼いていない点はそもそも配線しない（フェイルソフト）。スムージング・スナップ・距離フェードは掛けない（Unity の品質機能）
- 角度の符号は session.view_angles と同じ: Yaw 正 = カメラがキャラクターの左側 / Pitch 正 = カメラが基準点より上（ふかん）。
  Maya の系 → 正準空間（core.space）の対応: 前方 (fx, fy, fz) → (fz, -fx, fy)。
  Yaw = normalize(atan2(-fx, fz) − atan2(-dx, dz))、Pitch = atan2(dy, √(dx² + dz²))（d = カメラ − 格子の中心、f = 基準ボーンの回転で回した forwardAxis）
- **カメラを動かすと追従する理由**: expression は decomposeMatrix の出力（= カメラ・ボーンの worldMatrix）と rig のアトリビュート、
  blendShape の重み（表情での弱め）を**属性として直接読む**ので、それらが変われば DG の依存で dirty になり、blendShape が評価される
  （ビューの描画・再生・レンダリング・バッチのどれでも）ときに一緒に評価される。getAttr・Python 呼び出しは使わない
  （評価マネージャの並列評価を妨げない）。時間を直接読まないので `alwaysEvaluate` は切る。`unitConversion` は none
  （cm のまま・度のまま読む。作業単位が m でも式がずれない）
- Python のコールバック・プラグインのノードは使わない。リロードで落ちない
- 基準姿勢（session.begin_edit / scene.enter_reference_pose）との共存: expression の出力接続は enter で一時的に切られ、restore で
  `connectAttr(元のソース, weight)` に戻る（scene.py は変更不要。smoke で確認）
- 作る・消すのは `tdFacialPreview_<asset>` と、その `tdFacialCreated`（文字列アトリビュート）に記録したノードだけ
- 出力（FBX）には含めない: rig の transform に `tdPreviewOnly` を付ける（export.py は別プロセスの一時シーンで消す）

追加の品質機能（F5。既定のときは式が変わらない = 従来と同じ）:
- **シャープニング**（`quality.sharpness` ≠ 1 のときだけ式に入る）: 4 隅の双線形の重み w を w^s / Σ w^s にしてから、端のフェード・レイヤーの重みを掛ける
  （`evaluate.sharpen_weights` と同じ。s は [0.01, 64] に丸める）
- **誇張**: シーンに `FC_…_Ex` があって、基になる `FC_…` が配線されるとき、`_Ex` の weight = 基の weight × rig の `exaggeration`（0〜1、キーが打てる。
  初期値は `quality.exaggeration`）。`_Ex` が 1 本も無いときは式に `exaggeration` を書かない
- **距離で重みを決めるレイヤー**（`layerWeights[<レイヤー>].source == "distance"`）: レイヤーの重み = lerp(from, to, saturate((d − start) / (end − start)))。
  d = カメラと格子の中心（基準ボーン + 中心のずらし）の距離（cm）。**`emotion_<Layer>` は使わない（無視する）**: アトリビュートは残る（キー・値は壊さない）が効かない。
  d は `outDistance`（距離のレイヤーがあるときだけ作る）に出る。手動の角度（useManual）でも距離はカメラから測る。スムージングなどは掛けない

評価コスト: 式の長さは「配線するターゲットの数 + 軸の数」にほぼ比例し、毎評価で O(ターゲット数)。数百ターゲットでも数 ms（smoke の SMOKE INFO）。
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

from maya import cmds
from maya.api import OpenMaya as om

from . import pose_apply
from . import scene as scene_mod
from .core import evaluate, naming, space
from .core.model import Document
from .core.presenters import LayerPresenter

RIG_PREFIX = "tdFacialPreview_"
PREVIEW_ONLY_ATTR = scene_mod.PREVIEW_ONLY_ATTR  # tdPreviewOnly（Toon のプレビュー専用メッシュと同じ目印）
ASSET_ATTR = "tdFacialAsset"
CREATED_ATTR = "tdFacialCreated"  # この rig が作ったノード名の JSON 配列
TARGETS_ATTR = "tdFacialTargets"  # {ターゲット名: [weight の plug, ...]} の JSON
SIGNATURE_ATTR = "tdFacialSignature"
EMOTION_PREFIX = "emotion_"
KEYABLE_FIXED = ("alpha", "useManual", "manualYaw", "manualPitch")
FADE_EPSILON = evaluate.KINDA_SMALL_NUMBER  # evaluate_correction の「範囲の外」の判定
EXAGGERATION_ATTR = "exaggeration"  # 誇張（_Ex）の強さ 0〜1（キーが打てる。KEYABLE_FIXED には入れない: .fctrack に出さない）
OUT_DISTANCE_ATTR = "outDistance"  # カメラと格子の中心の距離（cm。距離で重みを決めるレイヤーがあるときだけ作る）


class PreviewRigError(RuntimeError):
    """プレビューを組めない（基準ボーン・カメラが無い等）。メッセージはそのまま画面に出せる日本語。"""


# ---------------------------------------------------------------------------
# 名前
# ---------------------------------------------------------------------------


def _safe(text: str) -> str:
    return re.sub(r"[^0-9A-Za-z_]", "_", text)


def rig_name(asset: str) -> str:
    return f"{RIG_PREFIX}{_safe(asset)}"


def emotion_attrs(doc: Document) -> dict[int, str]:
    """レイヤー番号（Neutral を除く）→ rig の感情アトリビュート名 `emotion_<Layer>`（記号は `_` へ。衝突したら番号を付ける）。"""
    out: dict[int, str] = {}
    used: set[str] = set()
    for i, layer in enumerate(doc.layers):
        if i == 0:
            continue
        name = EMOTION_PREFIX + _safe(layer.name)
        if name in used:
            name = f"{name}_{i}"
        used.add(name)
        out[i] = name
    return out


def find_rig(asset: str) -> Optional[str]:
    """asset の rig の transform（無ければ None）。"""
    name = rig_name(asset)
    if cmds.objExists(name) and cmds.attributeQuery(ASSET_ATTR, node=name, exists=True):
        return cmds.ls(name, long=False)[0]
    return None


def exists(asset: str) -> bool:
    return find_rig(asset) is not None


def list_rigs() -> list[str]:
    """シーンにある FacialController のプレビュー rig（transform）すべて。"""
    out = []
    for n in cmds.ls(type="transform") or []:
        if cmds.attributeQuery(ASSET_ATTR, node=n, exists=True) and cmds.attributeQuery(PREVIEW_ONLY_ATTR, node=n, exists=True):
            out.append(n)
    return out


def _require(asset: str) -> str:
    rig = find_rig(asset)
    if rig is None:
        raise PreviewRigError(f"プレビュー用ノード {rig_name(asset)} がありません（build してください）")
    return rig


def _read_json(rig: str, attr: str, default):
    if not cmds.attributeQuery(attr, node=rig, exists=True):
        return default
    try:
        return json.loads(cmds.getAttr(f"{rig}.{attr}") or "")
    except ValueError:
        return default


def _write_json(rig: str, attr: str, value) -> None:
    if not cmds.attributeQuery(attr, node=rig, exists=True):
        cmds.addAttr(rig, longName=attr, dataType="string")
    cmds.setAttr(f"{rig}.{attr}", json.dumps(value, ensure_ascii=False, sort_keys=True), type="string")


# ---------------------------------------------------------------------------
# カメラ・基準ボーン・角度（Python 側。session.view_angles と同じ式）
# ---------------------------------------------------------------------------


def camera_transform(camera: Optional[str] = None) -> str:
    """カメラの transform（長い名前）。省くと今のビューのカメラ（session と同じ決め方）。"""
    from .session import FacialSession  # 遅延: session は重いので必要なときだけ

    try:
        return FacialSession.camera_transform(camera)
    except RuntimeError as e:  # FacialSessionError
        raise PreviewRigError(str(e)) from e


def _meshes(doc: Document) -> list[str]:
    if doc.target is None or not doc.target.mesh:
        raise PreviewRigError("対象メッシュ（target.mesh）が設定されていません")
    out: list[str] = []
    for name in [doc.target.mesh, *doc.target.extra_meshes]:
        try:
            m = scene_mod.resolve_mesh(name)
        except ValueError as e:
            if name == doc.target.mesh:
                raise PreviewRigError(f"対象メッシュが見つかりません: {e}") from e
            continue  # extraMeshes が無いのは飛ばす
        if m not in out:
            out.append(m)
    return out


def base_joint(doc: Document) -> str:
    meshes = _meshes(doc)
    joint = scene_mod.find_joint(doc.grid.base_bone, scene_mod.mesh_joints(meshes)) if doc.grid.base_bone else None
    if joint is None:
        raise PreviewRigError(f"基準ボーン「{doc.grid.base_bone}」がシーンにありません")
    return joint


def _axis_vector(axis: str) -> tuple[float, float, float]:
    v = [0.0, 0.0, 0.0]
    v["XYZ".index(axis[1])] = 1.0 if axis[0] == "+" else -1.0
    return (v[0], v[1], v[2])


def _dag(node: str) -> om.MDagPath:
    sel = om.MSelectionList()
    sel.add(node)
    return sel.getDagPath(0)


def view_angles(doc: Document, camera: Optional[str] = None) -> tuple[float, float]:
    """カメラの (Yaw, Pitch)[度]。session.view_angles と同じ計算（セッションなしで呼べる）。"""
    pose_apply.assert_maya_space(doc)
    joint = base_joint(doc)
    tm = om.MTransformationMatrix(_dag(joint).inclusiveMatrix())
    t = tm.translation(om.MSpace.kWorld)
    q = tm.rotation(asQuaternion=True)
    cam = camera_transform(camera)
    c = om.MTransformationMatrix(_dag(cam).inclusiveMatrix()).translation(om.MSpace.kWorld)
    return space.compute_view_angles_in_space(
        space.MAYA, (t.x, t.y, t.z), space.quat_normalize((q.x, q.y, q.z, q.w)), doc.grid.forward_axis, (c.x, c.y, c.z), doc.grid.center_offset
    )


def view_distance(doc: Document, camera: Optional[str] = None) -> float:
    """カメラと格子の中心（基準ボーン + 中心のずらし）の距離（cm。距離で重みを決めるレイヤーの d）。"""
    pose_apply.assert_maya_space(doc)
    joint = base_joint(doc)
    tm = om.MTransformationMatrix(_dag(joint).inclusiveMatrix())
    t = tm.translation(om.MSpace.kWorld)
    q = tm.rotation(asQuaternion=True)
    off = space.rotate_vector(space.quat_normalize((q.x, q.y, q.z, q.w)), tuple(doc.grid.center_offset))
    c = om.MTransformationMatrix(_dag(camera_transform(camera)).inclusiveMatrix()).translation(om.MSpace.kWorld)
    return math.sqrt((c.x - t.x - off[0]) ** 2 + (c.y - t.y - off[1]) ** 2 + (c.z - t.z - off[2]) ** 2)


def distance_spec(doc: Document, layer_index: int) -> Optional[dict]:
    """レイヤーの重みをカメラの距離で決めるときの {"start", "end", "from", "to"}（数値）。そうでなければ None。Neutral は常に None。"""
    if layer_index <= 0 or layer_index >= len(doc.layers) or not doc.layer_weights:
        return None
    spec = doc.layer_weights.get(doc.layers[layer_index].name)
    if not spec or spec.get("source") != "distance":
        return None
    d = LayerPresenter.DISTANCE_DEFAULTS
    out = {}
    for k in ("start", "end", "from", "to"):
        v = spec.get(k, d[k])
        if not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v):
            return None
        out[k] = float(v)
    return out


def distance_layers(doc: Document) -> list[int]:
    """重みをカメラの距離で決めるレイヤーの番号（Neutral を除く。有効・無効は問わない）。"""
    return [i for i in range(1, len(doc.layers)) if distance_spec(doc, i) is not None]


def has_extreme_targets(asset: str) -> bool:
    """rig が配線している `_Ex` があるか。"""
    rig = find_rig(asset)
    return rig is not None and any(a.endswith(naming.EXTREME_SUFFIX) for a in _read_json(rig, TARGETS_ATTR, {}))


# ---------------------------------------------------------------------------
# 配線の計画（データ → expression の文字列）
# ---------------------------------------------------------------------------


@dataclass
class _Target:
    alias: str
    layer: int
    row: int
    col: int
    plugs: list[str] = field(default_factory=list)
    ex: bool = False  # 誇張用（`_Ex`）。基になる FC_…（同じレイヤー・行・列）が配線されているときだけ使う


@dataclass
class _Plan:
    asset: str
    joint: str
    targets: list[_Target]
    ex_targets: list[_Target]
    sharpness: float
    distance: dict[int, dict]  # レイヤー番号 → 距離の指定（配線に使うレイヤーのうち、距離で重みを決めるもの）
    layer_attrs: dict[int, str]  # 配線に使うレイヤー（有効で、ターゲットがあるもの）→ emotion アトリビュート名（Neutral は ""）
    intensity_plugs: list[str]
    dampen: float
    template: str  # @RIG@ / @CAM@ / @HEAD@ を含む expression
    signature: str
    skipped: list[str] = field(default_factory=list)  # 他から接続されていて配線しなかった weight

    def plug_map(self) -> dict[str, list[str]]:
        return {t.alias: list(t.plugs) for t in (*self.targets, *self.ex_targets)}


def _f(v: float) -> str:
    """MEL の数値リテラル（指数表記を避ける）。"""
    s = f"{float(v):.12f}".rstrip("0")
    return s + "0" if s.endswith(".") else s


def _rot_lines(prefix: str, v: Sequence[float]) -> list[str]:
    """v をクォータニオン ($qx $qy $qz $qw) で回した結果を $<prefix>x / y / z に入れる式（space.rotate_vector と同じ）。"""
    vx, vy, vz = (_f(c) for c in v)
    return [
        f"float ${prefix}tx = 2.0 * ($qy * {vz} - $qz * {vy});",
        f"float ${prefix}ty = 2.0 * ($qz * {vx} - $qx * {vz});",
        f"float ${prefix}tz = 2.0 * ($qx * {vy} - $qy * {vx});",
        f"float ${prefix}x = {vx} + $qw * ${prefix}tx + ($qy * ${prefix}tz - $qz * ${prefix}ty);",
        f"float ${prefix}y = {vy} + $qw * ${prefix}ty + ($qz * ${prefix}tx - $qx * ${prefix}tz);",
        f"float ${prefix}z = {vz} + $qw * ${prefix}tz + ($qx * ${prefix}ty - $qy * ${prefix}tx);",
    ]


def _axis_lines(tag: str, angle: str, range_deg: float, n: int, edge_fade: float) -> list[str]:
    """1 軸分: index0 / index1 / frac / fade（evaluate._sample_axis と同じ）。変数は $<tag>0 $<tag>1 $f<tag> $fade<tag>。"""
    if n <= 1:
        return [f"int ${tag}0 = 0;", f"int ${tag}1 = 0;", f"float $f{tag} = 0.0;", f"float $fade{tag} = 1.0;"]
    safe_range = max(range_deg, evaluate.KINDA_SMALL_NUMBER)
    lines = [
        f"float $u{tag} = ({angle} / {_f(safe_range)} + 1.0) * 0.5;",
        f"float $p{tag} = clamp(0.0, 1.0, $u{tag}) * {n - 1};",
        f"int ${tag}0 = clamp(0, {n - 1}, floor($p{tag}));",
        f"int ${tag}1 = min(${tag}0 + 1, {n - 1});",
        f"float $f{tag} = $p{tag} - ${tag}0;",
        f"float $ex{tag} = max(0.0, abs({angle}) - {_f(range_deg)});",
        f"float $fade{tag} = 1.0;",
    ]
    if edge_fade <= 0.0:
        lines.append(f"if ($ex{tag} > 0.0) $fade{tag} = 0.0;")
    else:
        lines.append(f"if ($ex{tag} > 0.0) $fade{tag} = clamp(0.0, 1.0, 1.0 - $ex{tag} / {_f(edge_fade)});")
    return lines


def _collect_targets(doc: Document, meshes: Sequence[str]) -> tuple[list[_Target], dict[int, str], list[_Target]]:
    """シーンにある FC_<asset>_<layer>_R{r}_C{c}（格子の中・有効なレイヤー）と、誇張用の `…_Ex` を集める。"""
    asset = doc.asset or ""
    prefix = doc.sculpt_shapes.prefix if doc.sculpt_shapes is not None else naming.DEFAULT_SCULPT_PREFIX
    layer_index = {layer.name: i for i, layer in enumerate(doc.layers)}
    attrs = emotion_attrs(doc)
    found: dict[str, _Target] = {}
    found_ex: dict[str, _Target] = {}
    for mesh in meshes:
        for ref in scene_mod.fc_targets(mesh, prefix):
            parsed = naming.parse_name(ref.alias, asset)
            if parsed is None or parsed.kind not in (naming.KIND_POINT, naming.KIND_POINT_EX):
                continue
            li = layer_index.get(parsed.layer or "")
            if li is None or not doc.layers[li].enabled:
                continue
            if not (0 <= parsed.row < doc.grid.rows and 0 <= parsed.col < doc.grid.cols):
                continue
            ex = parsed.kind == naming.KIND_POINT_EX
            t = (found_ex if ex else found).setdefault(ref.alias, _Target(ref.alias, li, parsed.row, parsed.col, ex=ex))
            if ref.plug not in t.plugs:
                t.plugs.append(ref.plug)
    targets = sorted(found.values(), key=lambda t: (t.layer, t.row, t.col))
    ex_targets = sorted(found_ex.values(), key=lambda t: (t.layer, t.row, t.col))
    return targets, {li: attrs.get(li, "") for li in sorted({t.layer for t in targets})}, ex_targets


def _intensity_plugs(doc: Document, meshes: Sequence[str]) -> list[str]:
    names = list(doc.intensity_curves) or list(doc.working_set.curves)
    plugs: list[str] = []
    for name in names:
        for m in meshes:
            ref = scene_mod.resolve_curve(name, m)
            if ref is not None:
                if ref.plug not in plugs:
                    plugs.append(ref.plug)
                break
    return plugs


def _plan(doc: Document, own_nodes: Iterable[str] = ()) -> _Plan:
    pose_apply.assert_maya_space(doc)
    if not doc.asset:
        raise PreviewRigError("アセット名（asset）が設定されていません")
    meshes = _meshes(doc)
    joint = base_joint(doc)
    own = set(own_nodes)
    targets, layer_attrs, ex_targets = _collect_targets(doc, meshes)
    skipped: list[str] = []
    for t in (*targets, *ex_targets):  # 他の接続（アニメ・別の expression 等）が既にある weight は戦わないので配線しない
        free = []
        for p in t.plugs:
            srcs = cmds.listConnections(p, source=True, destination=False) or []
            if any(s not in own for s in srcs):
                skipped.append(p)
            else:
                free.append(p)
        t.plugs = free
    targets = [t for t in targets if t.plugs]
    layer_attrs = {li: layer_attrs[li] for li in sorted({t.layer for t in targets})}
    # 誇張: 基になる FC_…（同じレイヤー・行・列）が配線されるものだけ（evaluate_correction と同じ。基が無い点の Ex は鳴らさない）
    base_keys = {(t.layer, t.row, t.col) for t in targets}
    ex_targets = [t for t in ex_targets if t.plugs and (t.layer, t.row, t.col) in base_keys]
    sharp = evaluate.clamp_sharpness(doc.quality.sharpness) if doc.quality is not None else 1.0
    use_sharp = sharp != 1.0
    distance = {li: sp for li in layer_attrs if (sp := distance_spec(doc, li)) is not None}

    g = doc.grid
    dampen = evaluate.clamp(doc.policy.expression_dampen, 0.0, 1.0)
    inten = _intensity_plugs(doc, meshes) if dampen > 0.0 else []
    L: list[str] = ["// tdFacial プレビュー（tdrive_facial.preview_rig が生成。手で編集しない。作り直すと上書きされる）"]
    L += [
        "float $qx = @HEAD@.outputQuatX;",
        "float $qy = @HEAD@.outputQuatY;",
        "float $qz = @HEAD@.outputQuatZ;",
        "float $qw = @HEAD@.outputQuatW;",
    ]
    L += _rot_lines("f", _axis_vector(g.forward_axis))
    off = tuple(g.center_offset)
    if any(abs(c) > 0.0 for c in off):
        L += _rot_lines("o", off)
        L += [f"float $cx = @HEAD@.outputTranslateX + $ox;", "float $cy = @HEAD@.outputTranslateY + $oy;", "float $cz = @HEAD@.outputTranslateZ + $oz;"]
    else:
        L += ["float $cx = @HEAD@.outputTranslateX;", "float $cy = @HEAD@.outputTranslateY;", "float $cz = @HEAD@.outputTranslateZ;"]
    L += [
        "float $dx = @CAM@.outputTranslateX - $cx;",
        "float $dy = @CAM@.outputTranslateY - $cy;",
        "float $dz = @CAM@.outputTranslateZ - $cz;",
        "float $yaw = rad_to_deg(atan2(-$fx, $fz)) - rad_to_deg(atan2(-$dx, $dz));",
        "$yaw = fmod($yaw, 360.0);",
        "if ($yaw < 0.0) $yaw += 360.0;",
        "if ($yaw > 180.0) $yaw -= 360.0;",
        "float $pitch = rad_to_deg(atan2($dy, sqrt($dx * $dx + $dz * $dz)));",
        "if (@RIG@.useManual) { $yaw = @RIG@.manualYaw; $pitch = @RIG@.manualPitch; }",
        "@RIG@.outYaw = $yaw;",
        "@RIG@.outPitch = $pitch;",
    ]
    L += _axis_lines("c", "$yaw", g.yaw_range, g.cols, g.edge_fade)
    L += _axis_lines("r", "$pitch", g.pitch_range, g.rows, g.edge_fade)
    L += [
        "float $fade = $fadec * $fader;",
        f"if ($fade <= {_f(FADE_EPSILON)}) $fade = 0.0;",
        "float $damp = 1.0;",
    ]
    if inten:
        L.append(f"$damp = 1.0 - {_f(dampen)} * clamp(0.0, 1.0, {' + '.join(inten)});")
    L.append("float $g = @RIG@.alpha * $fade * $damp;")
    L.append("if (@RIG@.enable == 0) $g = 0.0;")
    wired = [*targets, *ex_targets]
    if use_sharp:  # シャープニング: 4 隅の双線形の重みを w^s / Σ w^s に（端のフェード・レイヤーの重みを掛ける前）
        L += [
            "float $b00 = (1.0 - $fc) * (1.0 - $fr);",
            "float $b01 = $fc * (1.0 - $fr);",
            "float $b10 = (1.0 - $fc) * $fr;",
            "float $b11 = $fc * $fr;",
        ]
        for k in ("00", "01", "10", "11"):
            L.append(f"float $q{k} = ($b{k} > 0.0) ? pow($b{k}, {_f(sharp)}) : 0.0;")
        L.append("float $qs = $q00 + $q01 + $q10 + $q11;")
        L.append("if ($qs > 0.0) { $b00 = $q00 / $qs; $b01 = $q01 / $qs; $b10 = $q10 / $qs; $b11 = $q11 / $qs; }")
        for c in sorted({t.col for t in wired}):
            L.append(f"float $ca{c} = ($c0 == {c}); float $cb{c} = ($c1 == {c});")
        for r in sorted({t.row for t in wired}):
            L.append(f"float $ra{r} = ($r0 == {r}); float $rb{r} = ($r1 == {r});")
    else:
        for c in sorted({t.col for t in wired}):
            L.append(f"float $wc{c} = (($c0 == {c}) ? (1.0 - $fc) : 0.0) + (($c1 == {c}) ? $fc : 0.0);")
        for r in sorted({t.row for t in wired}):
            L.append(f"float $wr{r} = (($r0 == {r}) ? (1.0 - $fr) : 0.0) + (($r1 == {r}) ? $fr : 0.0);")
    if distance:
        L.append("float $dist = sqrt($dx * $dx + $dy * $dy + $dz * $dz);")
        L.append(f"@RIG@.{OUT_DISTANCE_ATTR} = $dist;")
    for li, attr in layer_attrs.items():
        if li == 0:
            L.append("float $gL0 = $g;")
        elif li in distance:  # 距離で決める: emotion_<Layer> は使わない
            sp = distance[li]
            if sp["end"] == sp["start"]:
                L.append(f"float $lw{li} = ($dist >= {_f(sp['start'])}) ? {_f(sp['to'])} : {_f(sp['from'])};")
            else:
                L.append(
                    f"float $lw{li} = {_f(sp['from'])} + ({_f(sp['to'])} - {_f(sp['from'])}) * clamp(0.0, 1.0, ($dist - {_f(sp['start'])}) / ({_f(sp['end'])} - {_f(sp['start'])}));"
                )
            L.append(f"float $gL{li} = $g * $lw{li};")
        else:
            L.append(f"float $gL{li} = $g * @RIG@.{attr};")

    def corner_weight(t: _Target) -> str:
        if not use_sharp:
            return f"$wc{t.col} * $wr{t.row}"
        r, c = t.row, t.col
        return f"($ra{r} * $ca{c} * $b00 + $ra{r} * $cb{c} * $b01 + $rb{r} * $ca{c} * $b10 + $rb{r} * $cb{c} * $b11)"

    for t in targets:
        for p in t.plugs:
            L.append(f"{p} = {corner_weight(t)} * $gL{t.layer};")
    for t in ex_targets:
        for p in t.plugs:
            L.append(f"{p} = {corner_weight(t)} * $gL{t.layer} * @RIG@.{EXAGGERATION_ATTR};")
    template = "\n".join(L) + "\n"
    sig = hashlib.sha1(
        (template + "|" + joint + "|" + "|".join(f"{k}={','.join(v)}" for k, v in sorted({t.alias: t.plugs for t in wired}.items()))).encode("utf-8")
    ).hexdigest()[:16]
    return _Plan(doc.asset, joint, targets, ex_targets, sharp, distance, layer_attrs, inten, dampen, template, sig, skipped)


# ---------------------------------------------------------------------------
# 作る・消す
# ---------------------------------------------------------------------------


@dataclass
class BuildReport:
    rig: str
    expression: str
    signature: str
    targets: int
    layers: list[str]
    expression_chars: int
    warnings: list[str] = field(default_factory=list)
    extreme_targets: int = 0  # 配線した誇張用（_Ex）の数（targets には含めない）

    def summary(self) -> str:
        ex = f"、誇張 {self.extreme_targets}" if self.extreme_targets else ""
        return f"プレビュー: {self.rig}（ターゲット {self.targets}{ex}、式 {self.expression_chars} 文字）"


def _add_attr(rig: str, name: str, **kw) -> bool:
    if cmds.attributeQuery(name, node=rig, exists=True):
        return False
    cmds.addAttr(rig, longName=name, **kw)
    return True


def _create_attrs(rig: str, doc: Document) -> None:
    if _add_attr(rig, PREVIEW_ONLY_ATTR, attributeType="bool", defaultValue=True):
        cmds.setAttr(f"{rig}.{PREVIEW_ONLY_ATTR}", True)
    for a in (ASSET_ATTR, CREATED_ATTR, TARGETS_ATTR, SIGNATURE_ATTR):
        _add_attr(rig, a, dataType="string")
    _add_attr(rig, "camera", attributeType="message")
    if _add_attr(rig, "enable", attributeType="bool", defaultValue=True, keyable=True):
        pass
    if _add_attr(rig, "alpha", attributeType="double", minValue=0.0, maxValue=1.0, defaultValue=1.0, keyable=True):
        cmds.setAttr(f"{rig}.alpha", evaluate.clamp(doc.policy.global_alpha, 0.0, 1.0))
    if _add_attr(rig, EXAGGERATION_ATTR, attributeType="double", minValue=0.0, maxValue=1.0, defaultValue=1.0, keyable=True):
        q = doc.quality
        cmds.setAttr(f"{rig}.{EXAGGERATION_ATTR}", evaluate.clamp(q.exaggeration if q is not None else 1.0, 0.0, 1.0))
    _add_attr(rig, "useManual", attributeType="bool", defaultValue=False, keyable=True)
    _add_attr(rig, "manualYaw", attributeType="double", defaultValue=0.0, keyable=True)
    _add_attr(rig, "manualPitch", attributeType="double", defaultValue=0.0, keyable=True)
    for _, attr in emotion_attrs(doc).items():
        _add_attr(rig, attr, attributeType="double", minValue=0.0, maxValue=1.0, defaultValue=0.0, keyable=True)
    for a in ("outYaw", "outPitch", *((OUT_DISTANCE_ATTR,) if distance_layers(doc) else ())):
        if _add_attr(rig, a, attributeType="double", defaultValue=0.0):
            cmds.setAttr(f"{rig}.{a}", edit=True, channelBox=True)
    for a in ("tx", "ty", "tz", "rx", "ry", "rz", "sx", "sy", "sz", "v"):
        cmds.setAttr(f"{rig}.{a}", edit=True, lock=True, keyable=False, channelBox=False)


def _helper_nodes(rig: str) -> list[str]:
    return [n for n in _read_json(rig, CREATED_ATTR, []) if n != rig and cmds.objExists(n)]


def _zero_plugs(plugs: Iterable[str]) -> None:
    """配線をやめた weight を 0 に戻す（他から接続されている・ロックされているものは触らない）。"""
    for p in plugs:
        try:
            if not cmds.objExists(p) or cmds.listConnections(p, source=True, destination=False):
                continue
            if cmds.getAttr(p) != 0.0:
                cmds.setAttr(p, 0.0)
        except RuntimeError:
            pass


def _all_plugs(rig: str) -> list[str]:
    out: list[str] = []
    for plugs in _read_json(rig, TARGETS_ATTR, {}).values():
        out.extend(p for p in plugs if p not in out)
    return out


def _delete_nodes(nodes: Sequence[str]) -> None:
    live = [n for n in nodes if cmds.objExists(n)]
    if live:  # 空のリストを cmds.delete に渡さない
        cmds.delete(live)


def delete(asset: str) -> bool:
    """asset の rig を消す。作ったノード（transform・decomposeMatrix・expression）だけを消し、駆動していた weight は 0 に戻す。
    無ければ False。"""
    rig = find_rig(asset)
    if rig is None:
        return False
    plugs = _all_plugs(rig)
    helpers = _helper_nodes(rig)
    _delete_nodes([n for n in helpers if cmds.nodeType(n) == "expression"])  # 先に式（出力接続）を消す
    _delete_nodes(helpers)
    _zero_plugs(plugs)
    _delete_nodes([rig])
    return True


def build_ex(doc: Document, camera: Optional[str] = None) -> BuildReport:
    """プレビューを（作り直して）組む。rig の transform とそのキー・アトリビュートの値は作り直しでも残す（式と補助ノードだけ作り直す）。

    全体が Maya の Undo の 1 区切り。途中で失敗したら、そこまでに作ったノードを消して例外を投げる（半端なノードを残さない。S-9）。"""
    created: list[str] = []
    cmds.undoInfo(openChunk=True, chunkName="tdFacialPreviewBuild")
    try:
        return _build_ex(doc, camera, created)
    except BaseException:
        for n in reversed(created):  # 作りかけを片付ける（作り直しのとき残っていた rig・キーは作ったものではないので消さない）
            try:
                if cmds.objExists(n):
                    cmds.delete(n)
            except RuntimeError:
                pass
        raise
    finally:
        cmds.undoInfo(closeChunk=True)


def _build_ex(doc: Document, camera: Optional[str], created: list[str]) -> BuildReport:
    asset = doc.asset or ""
    cam = camera_transform(camera)
    rig = find_rig(asset)
    own = [rig] if rig else []
    own += _helper_nodes(rig) if rig else []
    plan = _plan(doc, own)
    old_plugs = _all_plugs(rig) if rig else []
    if rig:
        _delete_nodes([n for n in _helper_nodes(rig) if cmds.nodeType(n) == "expression"])
        _delete_nodes(_helper_nodes(rig))
        _zero_plugs([p for p in old_plugs])
    else:
        rig = cmds.createNode("transform", name=rig_name(asset))
        created.append(rig)
        if rig != rig_name(asset):  # 同じ名前の別のノードがある（つけた名前と違う名前になった）: あとで見つけられないので止める
            raise PreviewRigError(f"{rig_name(asset)} という名前の別のノードがあるため、プレビューを作れません（その名前を変えてください）")
    _create_attrs(rig, doc)
    cam_dm = cmds.createNode("decomposeMatrix", name=f"{rig}_camDM")
    created.append(cam_dm)
    head_dm = cmds.createNode("decomposeMatrix", name=f"{rig}_headDM")
    created.append(head_dm)
    cmds.connectAttr(f"{cam}.worldMatrix[0]", f"{cam_dm}.inputMatrix", force=True)
    cmds.connectAttr(f"{plan.joint}.worldMatrix[0]", f"{head_dm}.inputMatrix", force=True)
    for src in cmds.listConnections(f"{rig}.camera", source=True, destination=False, plugs=True) or []:
        cmds.disconnectAttr(src, f"{rig}.camera")
    cmds.connectAttr(f"{cam}.message", f"{rig}.camera", force=True)
    text = plan.template.replace("@RIG@", rig).replace("@CAM@", cam_dm).replace("@HEAD@", head_dm)
    expr = cmds.expression(string=text, name=f"{rig}_expr", alwaysEvaluate=False, unitConversion="none")
    created.append(expr)
    _write_json(rig, CREATED_ATTR, [rig, cam_dm, head_dm, expr])
    _write_json(rig, TARGETS_ATTR, plan.plug_map())
    cmds.setAttr(f"{rig}.{SIGNATURE_ATTR}", plan.signature, type="string")
    cmds.setAttr(f"{rig}.{ASSET_ATTR}", asset, type="string")
    warnings = [f"{p} は他から接続されているため配線しませんでした" for p in plan.skipped]
    return BuildReport(
        rig, expr, plan.signature, len(plan.targets), [doc.layers[i].name for i in plan.layer_attrs], len(text), warnings, len(plan.ex_targets)
    )


def build(doc: Document, camera: Optional[str] = None) -> str:
    """プレビューを組んで rig の transform 名を返す（詳細は `build_ex`）。camera を省くと今のビューのカメラ。"""
    return build_ex(doc, camera).rig


def has_expression(asset: str) -> bool:
    """rig があり、プレビューの式が生きている（キーに焼いたあとは False）か。"""
    rig = find_rig(asset)
    return rig is not None and any(cmds.nodeType(n) == "expression" for n in _helper_nodes(rig))


def is_stale(doc: Document) -> bool:
    """作ったあとで格子・レイヤー・存在する FC_*（`_Ex` を含む）のターゲット・基準ボーン・表情での弱め・シャープさ・距離で決めるレイヤーの設定が変わった
    （= 作り直しが要る）か。
    rig が無ければ False。rig はあるが式が無い（キーに焼いたあと）なら True。"""
    rig = find_rig(doc.asset or "")
    if rig is None:
        return False
    if not any(cmds.nodeType(n) == "expression" for n in _helper_nodes(rig)):
        return True
    own = [rig, *_helper_nodes(rig)]
    try:
        plan = _plan(doc, own)
    except PreviewRigError:
        return True
    have = cmds.getAttr(f"{rig}.{SIGNATURE_ATTR}") if cmds.attributeQuery(SIGNATURE_ATTR, node=rig, exists=True) else ""
    return plan.signature != have


def _camera_dm(rig: str) -> Optional[str]:
    for n in _helper_nodes(rig):
        if n.endswith("_camDM"):
            return n
    return None


def get_camera(asset: str) -> Optional[str]:
    """今つながっているカメラの transform。"""
    rig = _require(asset)
    src = cmds.listConnections(f"{rig}.camera", source=True, destination=False) or []
    return cmds.ls(src[0], long=True)[0] if src else None


def set_camera(asset: str, camera: Optional[str] = None) -> str:
    """プレビューのカメラを入れ替える（式は作り直さない）。つないだカメラの transform を返す。"""
    rig = _require(asset)
    dm = _camera_dm(rig)
    if dm is None:
        raise PreviewRigError("プレビューの式がありません（作り直してください）")
    cam = camera_transform(camera)
    for src in cmds.listConnections(f"{dm}.inputMatrix", source=True, destination=False, plugs=True) or []:
        cmds.disconnectAttr(src, f"{dm}.inputMatrix")
    cmds.connectAttr(f"{cam}.worldMatrix[0]", f"{dm}.inputMatrix", force=True)
    for src in cmds.listConnections(f"{rig}.camera", source=True, destination=False, plugs=True) or []:
        cmds.disconnectAttr(src, f"{rig}.camera")
    cmds.connectAttr(f"{cam}.message", f"{rig}.camera", force=True)
    return cam


def set_enabled(asset: str, enabled: bool) -> None:
    """補正あり / なし（enable。A/B 比較）。キーが打ってあれば、そのキーが優先される。"""
    cmds.setAttr(f"{_require(asset)}.enable", bool(enabled))


def is_enabled(asset: str) -> bool:
    return bool(cmds.getAttr(f"{_require(asset)}.enable"))


def current_weights(asset: str) -> dict[str, float]:
    """今の（rig が駆動している）FC_* の重み。ターゲット名 → 値（同名のターゲットが複数のメッシュにあるときは最初のもの）。"""
    rig = _require(asset)
    out: dict[str, float] = {}
    for alias, plugs in _read_json(rig, TARGETS_ATTR, {}).items():
        if plugs and cmds.objExists(plugs[0]):
            out[alias] = float(cmds.getAttr(plugs[0]))
    return out


def current_angles(asset: str) -> tuple[float, float]:
    """rig の outYaw / outPitch（実際に使っている角度。useManual のときは手動の値）。"""
    rig = _require(asset)
    return float(cmds.getAttr(f"{rig}.outYaw")), float(cmds.getAttr(f"{rig}.outPitch"))


# ---------------------------------------------------------------------------
# 同じ計算を Python（core.evaluate）で（比較・テスト用）
# ---------------------------------------------------------------------------


def evaluate_python(
    doc: Document,
    camera: Optional[str] = None,
    *,
    emotions: Optional[dict[str, float]] = None,
    alpha: Optional[float] = None,
    enable: Optional[bool] = None,
    manual: Optional[tuple[float, float]] = None,
    use_manual: Optional[bool] = None,
    exaggeration: Optional[float] = None,
    distance: Optional[float] = None,
) -> dict:
    """rig と同じ計算を core.evaluate で行う。戻り: {"yaw", "pitch", "distance", "weights": {ターゲット名: 重み}}（配線される全ターゲット。`_Ex` と 0 も含む）。

    emotions（レイヤー名 → 0〜1）・alpha・enable・manual（(Yaw, Pitch)）・use_manual・exaggeration は、省くと rig があればその値、無ければ既定
    （感情 0・alpha = policy.globalAlpha・enable・カメラから・exaggeration = quality.exaggeration）。表情での弱めはシーンの今の blendShape の重みを読む。
    距離で重みを決めるレイヤーは emotions を無視し、距離 d（省くとカメラと格子の中心の距離）から `layer_weight_from_distance` で決める。
    sharpness は doc.quality から。
    """
    pose_apply.assert_maya_space(doc)
    asset = doc.asset or ""
    rig = find_rig(asset)

    def rig_attr(name, default):
        return cmds.getAttr(f"{rig}.{name}") if rig and cmds.attributeQuery(name, node=rig, exists=True) else default

    own = [rig, *_helper_nodes(rig)] if rig else []
    plan = _plan(doc, own)
    if alpha is None:
        alpha = float(rig_attr("alpha", doc.policy.global_alpha))
    if enable is None:
        enable = bool(rig_attr("enable", True))
    if use_manual is None:
        use_manual = bool(rig_attr("useManual", False)) if manual is None else True
    if use_manual:
        yaw, pitch = manual if manual is not None else (float(rig_attr("manualYaw", 0.0)), float(rig_attr("manualPitch", 0.0)))
    else:
        yaw, pitch = view_angles(doc, camera)
    if exaggeration is None:
        exaggeration = float(rig_attr(EXAGGERATION_ATTR, doc.quality.exaggeration if doc.quality is not None else 1.0))
    if plan.distance and distance is None:
        distance = view_distance(doc, camera)
    attrs = emotion_attrs(doc)
    g = doc.grid
    shape = evaluate.GridShape(g.yaw_range, g.pitch_range, g.cols, g.rows, g.edge_fade)
    names: dict[tuple[int, int, int], str] = {(t.layer, t.row, t.col): t.alias for t in plan.targets}
    ex_names: dict[tuple[int, int, int], str] = {(t.layer, t.row, t.col): t.alias for t in plan.ex_targets}
    layers: list[evaluate.LayerEvalInput] = []
    for li, layer in enumerate(doc.layers):
        if li in plan.distance:
            sp = plan.distance[li]
            w = evaluate.layer_weight_from_distance(float(distance), sp["start"], sp["end"], sp["from"], sp["to"])
        elif emotions is not None and layer.name in emotions:
            w = float(emotions[layer.name])
        elif li > 0 and rig and attrs.get(li):
            w = float(rig_attr(attrs[li], 0.0))
        else:
            w = 0.0
        if li > 0 and li not in plan.distance:
            w = evaluate.clamp(w, 0.0, 1.0)  # rig のアトリビュートは 0〜1 に収まる
        corner = [names.get((li, r, c)) for r in range(g.rows) for c in range(g.cols)]
        ex_corner = [ex_names.get((li, r, c)) for r in range(g.rows) for c in range(g.cols)] if ex_names else None
        layers.append(evaluate.LayerEvalInput(corner, w, layer.enabled, ex_corner))
    out = {t.alias: 0.0 for t in (*plan.targets, *plan.ex_targets)}
    scale = float(alpha) if enable else 0.0
    if plan.intensity_plugs:
        s = sum(float(cmds.getAttr(p)) for p in plan.intensity_plugs)
        scale *= evaluate.expression_scale(plan.dampen, s)
    sharp = doc.quality.sharpness if doc.quality is not None else 1.0
    for mw in evaluate.evaluate_correction(shape, layers, yaw, pitch, sharp, evaluate.clamp(float(exaggeration), 0.0, 1.0)):
        out[mw.morph_name] = out.get(mw.morph_name, 0.0) + mw.weight * scale
    return {"yaw": yaw, "pitch": pitch, "distance": distance, "weights": out}


# ---------------------------------------------------------------------------
# キーに焼く（R-19 / F2-8）
# ---------------------------------------------------------------------------


def bake_to_keys(doc: Document, start: float, end: float, step: float = 1, remove_rig: bool = False) -> dict:
    """時間範囲を step フレームごとに評価して FC_* の weight にキーを打ち、プレビューの式を外す（キーだけで同じ重みになる）。

    - どのフレームでも 0 のターゲットにはキーを打たない（weight は 0 のまま）。キーは線形の接線
    - 式・補助ノードを消して rig の transform は残す（強さ・感情のキーを残すため。`remove_rig=True` で rig ごと消す）
    - 時刻は元に戻す。1 つの Undo にまとめる
    戻り: {"frames": [...], "keyed": [ターゲット名...], "start", "end", "step"}
    """
    asset = doc.asset or ""
    rig = _require(asset)
    if step <= 0:
        raise PreviewRigError("step は 0 より大きくしてください")
    if end < start:
        raise PreviewRigError("終了フレームが開始フレームより前です")
    if not any(cmds.nodeType(n) == "expression" for n in _helper_nodes(rig)):
        raise PreviewRigError("プレビューの式がありません（先に作ってください）")
    targets: dict[str, list[str]] = _read_json(rig, TARGETS_ATTR, {})
    frames: list[float] = []
    f = float(start)
    while f <= end + 1e-9:
        frames.append(f)
        f += step
    if frames[-1] < end - 1e-9:
        frames.append(float(end))
    t0 = cmds.currentTime(query=True)
    samples: dict[str, list[float]] = {a: [] for a in targets}
    try:
        for fr in frames:
            cmds.currentTime(fr, edit=True)
            for alias, plugs in targets.items():
                samples[alias].append(float(cmds.getAttr(plugs[0])))
    finally:
        cmds.currentTime(t0, edit=True)
    keyed = [a for a, vals in samples.items() if any(abs(v) > 0.0 for v in vals)]
    cmds.undoInfo(openChunk=True, chunkName="tdFacialBakeToKeys")
    try:
        plugs_all = _all_plugs(rig)
        _delete_nodes([n for n in _helper_nodes(rig) if cmds.nodeType(n) == "expression"])
        _delete_nodes([n for n in _helper_nodes(rig) if cmds.nodeType(n) != "transform"])
        _zero_plugs(plugs_all)
        for alias in keyed:
            for plug in targets[alias]:
                for fr, v in zip(frames, samples[alias]):
                    cmds.setKeyframe(plug, time=fr, value=v, inTangentType="linear", outTangentType="linear")
        cmds.setAttr(f"{rig}.{SIGNATURE_ATTR}", "", type="string")
        _write_json(rig, CREATED_ATTR, [rig])
        if remove_rig:
            delete(asset)
    finally:
        cmds.undoInfo(closeChunk=True)
    return {"frames": frames, "keyed": keyed, "start": float(start), "end": float(end), "step": float(step)}
