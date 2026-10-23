"""ブレンド / 遷移の合成（docs/20 §4）。配置（`core/sequence.py`）→ Maya のシーンに**合成**を作る。

## 方式（docs/20 §4.1 の決定の変更。理由は docs/20 に追記）
Time Editor（`timeEditorClip`）は、裏の mayapy ではクリップの問い合わせ・編集が効かず（クロスフェード曲線・トリムを検証できない）、
**どのレンダラーでも同じに動くことを機械的に確かめられなかった**。そこで仕様の「フォールバック」にあたる方式を正とした:

1. 合成の対象は**スケルトンのジョイントのうち、入力のつながりがある（キー・コンストレイントで動く）チャンネル**
2. 配置ごとに、クリップの範囲を**1 フレームずつサンプリング**（`getAttr(time=)`）し、タイムラインの位置にずらした**複製のカーブ**を作る
   （ループのクリップは cycle、そうでなければ最後で止まる）。サンプリングは合成を作る前の状態で行うので、アニメーションレイヤーやコンストレイントの結果を含む
3. 配置の列を `pairBlend`（移動 + 回転。**回転は四元数補間 = Unity と同じ**）と `blendTwoAttr`（スケール）の連鎖でつなぎ、重みは**線形**に
   0 → 1（Unity の `CrossFadeInFixedTime` と同じ）。ジョイントの元のつながりは切り離して控えておく
4. 上半身などのレイヤーは、マスクのジョイントにだけ別の列を `pairBlend` で重ねる（重みはノードの属性 `layerWeight<N>`）

**元のカーブ・コンストレイントは壊さない**: 合成を外すと、控えておいた元のつながりへ戻し、作ったノードを全部消し、再生範囲・時刻・ループ設定も戻す。
状態は `tdAnimComp_<名前>`（network ノード）に文字列で持つので、シーンを保存して開き直しても外せる。
**合成の作成・解除は Maya の Undo に積まない**（`undo_off`）: 積むと、Ctrl+Z でノードだけ戻って再生範囲が戻らない・元の範囲を持つノードごと消える・
スライダーのたびの作り直しで履歴が埋まる（docs/21 SF-4 / SF-5）。戻すのは「合成を外す」。
**合成中にシーンを保存すると**、保存の間だけジョイントを元のつながりへ戻して書き、保存したら合成につなぎ直す（`suspend_for_save` / `resume_after_save`。SF-19）。
合成中はオートキーを切る（外すと戻す。ジョイントへ打ったキーが合成の入力に紛れないように。SF-7）。
再生範囲・時刻の「元の状態」は `playstate` で共有する（クリップ再生・ショットと、戻す順に関係なく最後に元へ戻る。SF-8）。

dx11Shader・Render Override は使わない。
"""

from __future__ import annotations

import contextlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from maya import cmds
from maya.api import OpenMaya as om
from maya.api import OpenMayaAnim as oma

from tdrive import lifecycle

from . import clips as MC
from . import playstate
from .core import sequence as Seq

PREFIX = "tdAnimComp_"
TRANSLATE = ("translateX", "translateY", "translateZ")
ROTATE = ("rotateX", "rotateY", "rotateZ")
SCALE = ("scaleX", "scaleY", "scaleZ")
CHANNELS = TRANSLATE + ROTATE + SCALE
CURVE_TYPE = {"translate": "animCurveTL", "rotate": "animCurveTA", "scale": "animCurveTU"}


@dataclass
class Layer:
    """マスクしたジョイントにだけ重ねる列。weight は 0〜1。"""

    placements: list[Seq.Placement]
    joints: list[str]
    weight: float = 1.0
    name: str = "UpperBody"


@dataclass
class Result:
    name: str
    node: str
    frames: float
    joints: int
    plugs: int
    nodes: int
    layers: int = 0
    warnings: list[str] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)  # 段階ごとの所要秒（サンプリング / 切り離し / 連鎖 / 保存。AN-F-26）


@contextlib.contextmanager
def undo_off():
    """中で行う操作を Maya の Undo に積まない（合成は「合成を外す」で戻す。履歴を細切れにしない・再生範囲だけ戻らない半端を作らない）。"""
    was = cmds.undoInfo(query=True, state=True)
    cmds.undoInfo(stateWithoutFlush=False)
    try:
        yield
    finally:
        if was:
            cmds.undoInfo(stateWithoutFlush=True)


# ------------------------------------------------------------------ 状態の出し入れ（network ノード）
def _comp_node(name: str) -> str:
    return PREFIX + name


def _owner(name: str) -> str:
    return "comp:" + name


def _source(plug: str) -> str | None:
    """プラグの入力の元（単位変換ノードは飛ばした本当の元。外すとき単位変換が消えても、つなぎ直せば Maya が入れ直す。SF-3）。"""
    src = cmds.listConnections(plug, source=True, destination=False, plugs=True, skipConversionNodes=True) or []
    return src[0] if src else None


def active() -> list[str]:
    """シーンに残っている合成の名前（`tdAnimComp_` を除いたもの）。"""
    return sorted(n[len(PREFIX):] for n in cmds.ls(PREFIX + "*", type="network") or [])


def _store(node: str, key: str, value: Any) -> None:
    if not cmds.attributeQuery(key, node=node, exists=True):
        cmds.addAttr(node, longName=key, dataType="string")
    cmds.setAttr(f"{node}.{key}", json.dumps(value, ensure_ascii=False), type="string")


def _load(node: str, key: str, default: Any = None) -> Any:
    if not cmds.attributeQuery(key, node=node, exists=True):
        return default
    try:
        return json.loads(cmds.getAttr(f"{node}.{key}") or "null")
    except ValueError:
        return default


def info(name: str) -> dict[str, Any]:
    """合成の情報（フレーム数・ジョイント数など）。無ければ {}。"""
    node = _comp_node(name)
    return _load(node, "tdInfo", {}) if cmds.objExists(node) else {}


# ------------------------------------------------------------------ 集める
FAMILIES = ("translate", "rotate", "scale")


def _is_compound(plug: str) -> bool:
    """`joint.rotate` のような複合プラグか（`joint.rotateX` は子プラグ）。"""
    return plug.rsplit(".", 1)[-1] in FAMILIES


def _compound_source(plug: str) -> str | None:
    """複合プラグ**自身**の入力の元（mGear は `joint.rotate` ← `mgear_matrixConstraint.rotate` のように複合ごとつなぐ。子プラグの入力は含めない。AN-F-6）。"""
    src = cmds.connectionInfo(plug, sourceFromDestination=True)
    if not src:
        return None
    node = src.split(".")[0]
    if cmds.nodeType(node) == "unitConversion":  # 単位変換ノードが挟まれていたら、その手前の本当の元
        up = cmds.listConnections(node + ".input", source=True, destination=False, plugs=True) or []
        return up[0] if up else src
    return src


def _children(plug: str) -> list[str]:
    fam = plug.rsplit(".", 1)[-1]
    return [f"{plug.rsplit('.', 1)[0]}.{fam}{ax}" for ax in "XYZ"]


def _current_source(plug: str) -> str | None:
    """プラグの今の入力の元。複合プラグは、複合自身の入力があればそれ、無ければ子プラグの入力（元でも合成でもないものを優先）。"""
    if not _is_compound(plug):
        return _source(plug)
    own = _compound_source(plug)
    if own:
        return own
    kids = [x for x in (_source(c) for c in _children(plug)) if x]
    foreign = [x for x in kids if not x.split(".")[0].startswith(PREFIX)]
    return (foreign or kids or [None])[0]


def _connect_original(src: str, dst: str) -> None:
    """控えた元のつながりを戻す。複合プラグへ戻すときは、先に子プラグの入力（合成の出力）を切る。"""
    if _is_compound(dst):
        for c in _children(dst):
            cur = _source(c)
            if cur:
                cmds.disconnectAttr(cur, c)
    cmds.connectAttr(src, dst, force=True)


def _connect_output(out: str, dst: str) -> None:
    """合成の出力を子プラグへつなぐ。親の複合プラグに入力が残っていたら先に切る。"""
    parent = dst[:-1]  # rotateX → rotate
    own = _compound_source(parent)
    if own:
        cmds.disconnectAttr(own, parent)
    cmds.connectAttr(out, dst, force=True)


def animated_channels(joints: list[str]) -> dict[str, list[str]]:
    """ジョイント → つながりのあるチャンネル（translateX …）。ロックされたもの・合成が作ったつながりは除く。
    複合プラグ（`joint.rotate` ← 他ノード）でつながるジョイントは、その 3 チャンネルすべてを対象にする（AN-F-6）。"""
    out: dict[str, list[str]] = {}
    for j in joints:
        got = []
        for fam in FAMILIES:
            chs = [f"{fam}{ax}" for ax in "XYZ"]
            comp = _compound_source(f"{j}.{fam}")
            if comp:
                if not comp.split(".")[0].startswith(PREFIX) and not any(cmds.getAttr(f"{j}.{ch}", lock=True) for ch in chs):  # ロックがあると付け替えられない
                    got.extend(chs)
                continue
            for ch in chs:
                src = _source(f"{j}.{ch}")
                if src and not src.split(".")[0].startswith(PREFIX) and not cmds.getAttr(f"{j}.{ch}", lock=True):  # 別の合成がつないだチャンネルは除く
                    got.append(ch)
        if got:
            out[j] = got
    return out


def _family(ch: str) -> str:
    return "translate" if ch.startswith("translate") else "rotate" if ch.startswith("rotate") else "scale"


class ComposeCancelled(Exception):
    """進捗の「中止」で合成を作らずに終えた（元のまま）。"""


Progress = Callable[[int, int], bool]  # (済んだ数, 全体) → 続けるか（False で中止）

# サンプリング結果の使い回し（AN-F-26）。スライダーのドラッグ後の作り直しなど、アニメーションが同じなら 1 回だけ読む。
# 「同じ」の判定は、チャンネルの組・フレーム列・すべてのアニメーションカーブのキーの指紋。キーの無いコントローラの値を手で変えたときは検知できないので、
# ユーザーが押す「合成を作る」では使わない（`use_cache=False`）。
_sample_cache: dict[str, Any] = {"key": None, "table": None}


def _anim_fingerprint() -> int:
    curves = cmds.ls(type=["animCurveTL", "animCurveTA", "animCurveTU", "animCurveTT"]) or []
    vals = cmds.keyframe(curves, query=True, valueChange=True) if curves else []
    times = cmds.keyframe(curves, query=True, timeChange=True) if curves else []
    return hash((len(curves), tuple(vals or ()), tuple(times or ())))


def _sample_table(chans: dict[str, list[str]], frames: list[int], progress: Progress | None) -> dict[int, dict[tuple[str, str], tuple[float, ...]]]:
    """frames の各フレームについて、全ジョイントの translate / rotate / scale をまとめて読む（AN-F-21）。
    フレームごとに `currentTime` を 1 回動かして全部読む（評価の回数 ≈ フレーム数）。元のつながりのままの評価で、終わったら時刻を戻す。"""
    fams = {j: sorted({_family(c) for c in chs}) for j, chs in chans.items()}
    table: dict[int, dict[tuple[str, str], tuple[float, ...]]] = {}
    original = cmds.currentTime(query=True)
    try:
        for i, f in enumerate(frames):
            if progress is not None and not progress(i, len(frames)):
                raise ComposeCancelled()
            cmds.currentTime(f, edit=True)
            table[f] = {(j, fam): tuple(cmds.getAttr(f"{j}.{fam}")[0]) for j, fs in fams.items() for fam in fs}
    finally:
        cmds.currentTime(original, edit=True)
    return table


def _sample(joint: str, channels: list[str], frames: list[int]) -> dict[str, list[float]]:
    """frames の各フレームのチャンネル値（元のつながりのままの評価）。"""
    out = {ch: [] for ch in channels}
    fams = sorted({_family(c) for c in channels})
    for f in frames:
        for fam in fams:
            vals = cmds.getAttr(f"{joint}.{fam}", time=f)[0]
            for i, ax in enumerate("XYZ"):
                ch = f"{fam}{ax}"
                if ch in out:
                    out[ch].append(float(vals[i]))
    return out


# ------------------------------------------------------------------ 作る
class _Builder:
    def __init__(self, name: str, fps: int) -> None:
        self.name, self.fps = name, fps
        self.node = _comp_node(name)
        self.created: list[str] = []
        self.original: list[list[str]] = []  # [[元のソース, ジョイントのプラグ], ...]

    def make(self, node_type: str, name: str, **attrs: Any) -> str:
        n = cmds.createNode(node_type, name=name)
        self.created.append(n)
        for k, v in attrs.items():
            cmds.setAttr(f"{n}.{k}", v)
        return n

    _CURVE_KIND = {"translate": oma.MFnAnimCurve.kAnimCurveTL, "rotate": oma.MFnAnimCurve.kAnimCurveTA, "scale": oma.MFnAnimCurve.kAnimCurveTU}
    _TO_INTERNAL = {"translate": om.MDistance.uiToInternal, "rotate": om.MAngle.uiToInternal, "scale": float}

    def curve(self, family: str, label: str, times: list[float], values: list[float], loop: bool) -> str:
        """複製のカーブ。`cmds.setKeyframe` を 1 キーずつ呼ぶと MisotenChara（131 ジョイント × 113 フレーム）で 5 秒以上かかるので、API でまとめて打つ（AN-F-26）。"""
        fn = oma.MFnAnimCurve()
        obj = fn.create(self._CURVE_KIND[family])
        name = om.MFnDependencyNode(obj).setName(f"{PREFIX}{self.name}_{label}")
        self.created.append(name)
        units = om.MTime.uiUnit()
        conv = self._TO_INTERNAL[family]
        fn.addKeys(om.MTimeArray([om.MTime(t, units) for t in times]), om.MDoubleArray([conv(v) for v in values]),
                   oma.MFnAnimCurve.kTangentLinear, oma.MFnAnimCurve.kTangentLinear)
        fn.setPreInfinityType(oma.MFnAnimCurve.kConstant)
        fn.setPostInfinityType(oma.MFnAnimCurve.kCycle if loop and len(times) > 1 else oma.MFnAnimCurve.kConstant)
        return name

    def weight_curve(self, label: str, start: float, fade: float) -> str:
        """前の結果 → この配置へ、fade フレームで線形に 0 → 1（fade 0 は即切り替え）。"""
        c = self.make("animCurveTU", f"{PREFIX}{self.name}_{label}")
        a, b = (start, start + fade) if fade > 0 else (start - 0.01, start)
        cmds.setKeyframe(c, time=a, value=0.0)
        cmds.setKeyframe(c, time=b, value=1.0)
        cmds.keyTangent(c, inTangentType="linear", outTangentType="linear")
        cmds.setAttr(f"{c}.preInfinity", 0)
        cmds.setAttr(f"{c}.postInfinity", 0)
        return c

    def blend(self, joint: str, tag: str, a: dict[str, str], b: dict[str, str], weight_plug: str, statics: dict[str, float]) -> dict[str, str]:
        """a, b: チャンネル → プラグ。結果: チャンネル → 出力プラグ。移動 + 回転は pairBlend（四元数）、スケールは blendTwoAttr。"""
        out: dict[str, str] = {}
        tr = [c for c in TRANSLATE + ROTATE if c in a]
        if tr:
            pb = self.make("pairBlend", f"{PREFIX}{self.name}_{joint}_{tag}_pb", rotInterpolation=1)
            cmds.connectAttr(weight_plug, f"{pb}.weight")
            cmds.connectAttr(f"{joint}.rotateOrder", f"{pb}.rotateOrder")  # 回転順がジョイントと違うと姿勢がずれる（xyz 以外のリグ。SP-10）
            for ch in TRANSLATE + ROTATE:
                fam = "Translate" if ch.startswith("translate") else "Rotate"
                ax = ch[-1]
                for idx, src in ((1, a), (2, b)):
                    plug = f"{pb}.in{fam}{ax}{idx}"
                    if ch in src:
                        cmds.connectAttr(src[ch], plug)
                    else:
                        cmds.setAttr(plug, statics.get(ch, 0.0))  # 動かないチャンネルは今の値で固定
                if ch in a:
                    out[ch] = f"{pb}.out{fam}{ax}"
        for ch in SCALE:
            if ch in a:
                bt = self.make("blendTwoAttr", f"{PREFIX}{self.name}_{joint}_{ch}_{tag}_bt")
                cmds.connectAttr(a[ch], f"{bt}.input[0]")
                cmds.connectAttr(b[ch], f"{bt}.input[1]")
                cmds.connectAttr(weight_plug, f"{bt}.attributesBlender")
                out[ch] = f"{bt}.output"
        return out


def _statics(joint: str, channels: list[str]) -> dict[str, float]:
    return {ch: float(cmds.getAttr(f"{joint}.{ch}")) for ch in TRANSLATE + ROTATE if ch not in channels}


def compose(name: str, placements: list[Seq.Placement], joints: list[str], fps: int, layers: list[Layer] | None = None, playback: bool = True,
            progress: Progress | None = None, use_cache: bool = False) -> Result:
    """合成を作る。同じ名前の合成は先に外す（別の名前の合成は残る。ジョイントが重ならなければ同時に使える。ショットのキャラクターごとの合成）。
    配置が空・ジョイントにつながりが無いときは ValueError。`progress(済, 全体)` が False を返すと `ComposeCancelled`（何も作らず元のまま）。`playback=False` は再生範囲・時刻に触れない（ショットが自分で決める）。"""
    if not placements:
        raise ValueError("遷移列が空です")
    with undo_off():
        return _compose(name, placements, joints, fps, layers, playback, progress, use_cache)


def _compose(name: str, placements: list[Seq.Placement], joints: list[str], fps: int, layers: list[Layer] | None, playback: bool,
             progress: Progress | None = None, use_cache: bool = False) -> Result:
    MC.stop()  # クリップ再生中なら先に止めて、再生範囲を元へ戻してから合成の範囲にする（控えが混ざらないように。SF-8）
    remove(name)
    chans = animated_channels(joints)
    if not chans:
        raise ValueError("合成できるジョイントがありません（キーやコンストレイントでつながったチャンネルが無い）")
    b = _Builder(name, fps)
    warnings: list[str] = []
    timings: dict[str, float] = {}
    t_all = time.perf_counter()
    layer_list = layers or []
    outputs: list[list[str]] = []  # [[合成の出力, ジョイントのプラグ], ...]（保存の間だけ元へ戻すため）
    try:
        node = cmds.createNode("network", name=b.node)
        b.created.append(node)
        # ---- 1. サンプリング（つながりを切る前）
        all_layers = [(None, placements)] + [(L, L.placements) for L in layer_list]
        needed: dict[tuple[str, str, int, int], tuple[str, list[str]]] = {}  # key → (ジョイント, チャンネル)
        for L, pls in all_layers:
            for p in pls:
                for j, chs in chans.items():
                    if L is not None and j not in L.joints:
                        continue
                    needed[(j, p.clip, p.src_start, p.src_end)] = (j, chs)
        frame_set = sorted({f for (_, _, a, b) in needed for f in range(a, b + 1)})
        n_frames = len(frame_set)
        n_steps = n_frames + len(chans)  # 進捗はサンプリング（フレーム数）+ ノード作成（ジョイント数）
        t_sample = time.perf_counter()
        cache_key = (tuple((j, tuple(chs)) for j, chs in chans.items()), tuple(frame_set), _anim_fingerprint()) if use_cache else None
        if cache_key is not None and _sample_cache["key"] == cache_key:
            table = _sample_cache["table"]
        else:
            table = _sample_table(chans, frame_set, (lambda d, _t: progress(d, n_steps)) if progress else None)
            _sample_cache.update(key=cache_key, table=table if cache_key is not None else None)
        timings["sample"] = time.perf_counter() - t_sample
        samples: dict[tuple[str, str, int, int], dict[str, list[float]]] = {}
        for key, (j, chs) in needed.items():
            out: dict[str, list[float]] = {ch: [] for ch in chs}
            for f in range(key[2], key[3] + 1):
                for ch in chs:
                    out[ch].append(float(table[f][(j, _family(ch))]["XYZ".index(ch[-1])]))
            samples[key] = out
        t_detach = time.perf_counter()
        # ---- 2. 元のつながりを切り離して控える
        for j, chs in chans.items():
            for fam in FAMILIES:
                fch = [c for c in chs if _family(c) == fam]
                if not fch:
                    continue
                comp = _compound_source(f"{j}.{fam}")
                if comp:  # 複合プラグごとの接続（mGear）は、複合のまま控えて切る。AN-F-6
                    b.original.append([comp, f"{j}.{fam}"])
                    cmds.disconnectAttr(cmds.connectionInfo(f"{j}.{fam}", sourceFromDestination=True), f"{j}.{fam}")
                    continue
                for ch in fch:
                    plug = f"{j}.{ch}"
                    src = _source(plug)
                    b.original.append([src, plug])
                    cmds.disconnectAttr(src, plug)
        # ---- 3. 重み（配置ごとに 1 本。ジョイント全員で共有）
        def weights(pls: list[Seq.Placement], tag: str) -> list[str]:
            return [b.weight_curve(f"{tag}_w{k}", p.start, p.fade_in) + ".output" for k, p in enumerate(pls) if k > 0]

        base_w = weights(placements, "base")
        for i, L in enumerate(layer_list):
            cmds.addAttr(node, longName=f"layerWeight{i}", attributeType="double", minValue=0.0, maxValue=1.0, defaultValue=float(L.weight), keyable=True)
        layer_w = [weights(L.placements, f"L{i}") for i, L in enumerate(layer_list)]
        timings["detach"] = time.perf_counter() - t_detach
        t_chain = time.perf_counter()
        # ---- 4. ジョイントごとに連鎖
        n_plugs = 0
        for ji, (j, chs) in enumerate(chans.items()):
            if progress is not None and not progress(n_frames + ji, n_steps):
                raise ComposeCancelled()
            statics = _statics(j, chs)

            def chain(pls: list[Seq.Placement], ws: list[str], tag: str) -> dict[str, str]:
                curves = []
                for k, p in enumerate(pls):
                    vals = samples[(j, p.clip, p.src_start, p.src_end)]
                    times = [p.start + (f - p.src_start) / p.speed for f in range(p.src_start, p.src_end + 1)]
                    curves.append({ch: b.curve(_family(ch), f"{j}_{ch}_{tag}{k}", times, vals[ch], p.loop) + ".output" for ch in chs})
                cur = curves[0]
                for k in range(1, len(pls)):
                    cur = b.blend(j, f"{tag}{k}", cur, curves[k], ws[k - 1], statics)
                return cur

            result = chain(placements, base_w, "b")
            for i, L in enumerate(layer_list):
                if j in L.joints:
                    top = chain(L.placements, layer_w[i], f"L{i}_")
                    result = b.blend(j, f"layer{i}", result, top, f"{node}.layerWeight{i}", statics)
            for ch, plug in result.items():
                cmds.connectAttr(plug, f"{j}.{ch}", force=True)  # 複合プラグは上で切ってある
                outputs.append([plug, f"{j}.{ch}"])
                n_plugs += 1
        timings["chain"] = time.perf_counter() - t_chain
        if progress is not None:
            progress(n_steps, n_steps)
        # ---- 5. 状態の保存・再生範囲
        total = Seq.total_frames(placements + [p for L in layer_list for p in L.placements])
        _store(node, "tdOriginal", b.original)
        _store(node, "tdOutputs", outputs)
        _store(node, "tdCreated", b.created)
        _store(node, "tdInfo", {"frames": total, "joints": len(chans), "plugs": n_plugs, "layers": [L.name for L in layer_list],
                                "clips": [p.clip for p in placements], "fps": fps})
        auto = bool(cmds.autoKeyframe(query=True, state=True))
        _store(node, "tdAutoKey", auto)
        if auto:  # 合成中にオートキーが働くと、合成の入力に紛れたキーが「外す」で消える（SF-7）
            cmds.autoKeyframe(state=False)
        if playback:
            _store(node, "tdPlayback", playstate.acquire(_owner(name)))
        lifecycle.on_reload(remove)  # リロードのとき、元のつながりへ戻す
    except Exception:
        _rollback(b)
        raise
    if playback:
        cmds.playbackOptions(minTime=0, maxTime=total, animationStartTime=0, animationEndTime=total)
        cmds.currentTime(0, edit=True)
    timings["total"] = time.perf_counter() - t_all
    return Result(name, node, total, len(chans), n_plugs, len(b.created), len(layer_list), warnings, timings)


def _rollback(b: _Builder) -> None:
    """作りかけの合成を元へ戻す（途中で失敗したとき）。"""
    for src, dst in b.original:
        with contextlib.suppress(RuntimeError):
            _connect_original(src, dst)
    existing = [n for n in b.created if cmds.objExists(n)]
    if existing:
        cmds.delete(existing)
    if b.created and playstate.holds(_owner(b.name)):
        playstate.release_and_restore(_owner(b.name))


# ------------------------------------------------------------------ 外す
last_warnings: list[str] = []  # 直近の `remove` の警告（合成中にユーザーが足した接続を残した、など）


def remove(name: str | None = None) -> int:
    """合成を外す（name 無しは全部）。元のつながりへ戻し、作ったノードを消し、再生範囲・時刻・ループ設定・オートキーを戻す。外した数を返す。
    合成中にユーザーが足した接続（元でも合成でもない）は上書きせずに残し、`last_warnings` に書く（SF-20）。"""
    with undo_off():
        return _remove(name)


def _remove(name: str | None) -> int:
    names = [name] if name else active()
    last_warnings.clear()
    n = 0
    for nm in names:
        node = _comp_node(nm)
        if not cmds.objExists(node):
            continue
        original = _load(node, "tdOriginal", [])
        created = set(_load(node, "tdCreated", []))
        playback = _load(node, "tdPlayback", None)
        auto = _load(node, "tdAutoKey", None)
        for src, dst in original:
            if not (cmds.objExists(src.split(".")[0]) and cmds.objExists(dst.split(".")[0])):
                continue
            cur = _current_source(dst)
            if cur == src:
                continue
            if cur is not None and cur.split(".")[0] not in created and not cur.split(".")[0].startswith(PREFIX):
                last_warnings.append(f"{dst} は合成中に別の接続（{cur}）が足されていたため、元のつながりへ戻しませんでした")
                continue
            with contextlib.suppress(RuntimeError):
                _connect_original(src, dst)
        existing = [x for x in created if cmds.objExists(x)]
        if existing:  # 空のリストを cmds.delete に渡さない
            cmds.delete(existing)
        if cmds.objExists(node):
            cmds.delete(node)
        if auto:
            cmds.autoKeyframe(state=True)
        if playback is not None or playstate.holds(_owner(nm)):
            playstate.release_and_restore(_owner(nm), playback)
        n += 1
    return n


# ------------------------------------------------------------------ 保存の間だけ元へ戻す（SF-19）
_suspended: list[list[str]] = []


def suspend_for_save() -> int:
    """合成中のシーンを保存する直前: ジョイントを元のつながりへ戻す（合成のノードは残す）。保存したあと `resume_after_save`。戻したチャンネル数。"""
    _suspended.clear()
    with undo_off():
        for nm in active():
            node = _comp_node(nm)
            for src, dst in _load(node, "tdOriginal", []):
                if cmds.objExists(src.split(".")[0]) and cmds.objExists(dst.split(".")[0]):
                    with contextlib.suppress(RuntimeError):
                        _connect_original(src, dst)
            _suspended.extend(_load(node, "tdOutputs", []))
    return len(_suspended)


def resume_after_save() -> int:
    """`suspend_for_save` のあと、ジョイントを合成につなぎ直す。つなぎ直したチャンネル数。"""
    n = 0
    with undo_off():
        for out, dst in _suspended:
            if cmds.objExists(out.split(".")[0]) and cmds.objExists(dst.split(".")[0]):
                with contextlib.suppress(RuntimeError):
                    _connect_output(out, dst)
                    n += 1
    _suspended.clear()
    return n


def set_layer_weight(name: str, index: int, weight: float) -> None:
    """レイヤーの重み（0〜1）。再生中でも効く。"""
    node = _comp_node(name)
    attr = f"{node}.layerWeight{index}"
    if cmds.objExists(node) and cmds.attributeQuery(f"layerWeight{index}", node=node, exists=True):
        cmds.setAttr(attr, max(0.0, min(1.0, float(weight))))
