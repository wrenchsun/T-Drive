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
from dataclasses import dataclass, field
from typing import Any

from maya import cmds

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
def animated_channels(joints: list[str]) -> dict[str, list[str]]:
    """ジョイント → つながりのあるチャンネル（translateX …）。ロックされたもの・合成が作ったつながりは除く。"""
    out: dict[str, list[str]] = {}
    for j in joints:
        got = []
        for ch in CHANNELS:
            plug = f"{j}.{ch}"
            src = _source(plug)
            if src and not src.split(".")[0].startswith(PREFIX) and not cmds.getAttr(plug, lock=True):  # 別の合成がつないだチャンネルは除く
                got.append(ch)
        if got:
            out[j] = got
    return out


def _family(ch: str) -> str:
    return "translate" if ch.startswith("translate") else "rotate" if ch.startswith("rotate") else "scale"


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

    def curve(self, family: str, label: str, times: list[float], values: list[float], loop: bool) -> str:
        c = self.make(CURVE_TYPE[family], f"{PREFIX}{self.name}_{label}")
        for t, v in zip(times, values):
            cmds.setKeyframe(c, time=t, value=v)
        cmds.keyTangent(c, inTangentType="linear", outTangentType="linear")
        cmds.setAttr(f"{c}.preInfinity", 0)  # constant
        cmds.setAttr(f"{c}.postInfinity", 3 if loop and len(times) > 1 else 0)  # 3 = cycle（2 は欠番）、0 = constant
        return c

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


def compose(name: str, placements: list[Seq.Placement], joints: list[str], fps: int, layers: list[Layer] | None = None, playback: bool = True) -> Result:
    """合成を作る。同じ名前の合成は先に外す（別の名前の合成は残る。ジョイントが重ならなければ同時に使える。ショットのキャラクターごとの合成）。
    配置が空・ジョイントにつながりが無いときは ValueError。`playback=False` は再生範囲・時刻に触れない（ショットが自分で決める）。"""
    if not placements:
        raise ValueError("遷移列が空です")
    with undo_off():
        return _compose(name, placements, joints, fps, layers, playback)


def _compose(name: str, placements: list[Seq.Placement], joints: list[str], fps: int, layers: list[Layer] | None, playback: bool) -> Result:
    MC.stop()  # クリップ再生中なら先に止めて、再生範囲を元へ戻してから合成の範囲にする（控えが混ざらないように。SF-8）
    remove(name)
    chans = animated_channels(joints)
    if not chans:
        raise ValueError("合成できるジョイントがありません（キーやコンストレイントでつながったチャンネルが無い）")
    b = _Builder(name, fps)
    warnings: list[str] = []
    layer_list = layers or []
    outputs: list[list[str]] = []  # [[合成の出力, ジョイントのプラグ], ...]（保存の間だけ元へ戻すため）
    try:
        node = cmds.createNode("network", name=b.node)
        b.created.append(node)
        # ---- 1. サンプリング（つながりを切る前）
        samples: dict[tuple[str, str, int, int], dict[str, list[float]]] = {}
        all_layers = [(None, placements)] + [(L, L.placements) for L in layer_list]
        for L, pls in all_layers:
            for p in pls:
                for j, chs in chans.items():
                    if L is not None and j not in L.joints:
                        continue
                    key = (j, p.clip, p.src_start, p.src_end)
                    if key not in samples:
                        samples[key] = _sample(j, chs, list(range(p.src_start, p.src_end + 1)))
        # ---- 2. 元のつながりを切り離して控える
        for j, chs in chans.items():
            for ch in chs:
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
        # ---- 4. ジョイントごとに連鎖
        n_plugs = 0
        for j, chs in chans.items():
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
                cmds.connectAttr(plug, f"{j}.{ch}", force=True)
                outputs.append([plug, f"{j}.{ch}"])
                n_plugs += 1
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
    return Result(name, node, total, len(chans), n_plugs, len(b.created), len(layer_list), warnings)


def _rollback(b: _Builder) -> None:
    """作りかけの合成を元へ戻す（途中で失敗したとき）。"""
    for src, dst in b.original:
        with contextlib.suppress(RuntimeError):
            cmds.connectAttr(src, dst, force=True)
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
            cur = _source(dst)
            if cur == src:
                continue
            if cur is not None and cur.split(".")[0] not in created and not cur.split(".")[0].startswith(PREFIX):
                last_warnings.append(f"{dst} は合成中に別の接続（{cur}）が足されていたため、元のつながりへ戻しませんでした")
                continue
            with contextlib.suppress(RuntimeError):
                cmds.connectAttr(src, dst, force=True)
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
                        cmds.connectAttr(src, dst, force=True)
            _suspended.extend(_load(node, "tdOutputs", []))
    return len(_suspended)


def resume_after_save() -> int:
    """`suspend_for_save` のあと、ジョイントを合成につなぎ直す。つなぎ直したチャンネル数。"""
    n = 0
    with undo_off():
        for out, dst in _suspended:
            if cmds.objExists(out.split(".")[0]) and cmds.objExists(dst.split(".")[0]):
                with contextlib.suppress(RuntimeError):
                    cmds.connectAttr(out, dst, force=True)
                    n += 1
    _suspended.clear()
    return n


def set_layer_weight(name: str, index: int, weight: float) -> None:
    """レイヤーの重み（0〜1）。再生中でも効く。"""
    node = _comp_node(name)
    attr = f"{node}.layerWeight{index}"
    if cmds.objExists(node) and cmds.attributeQuery(f"layerWeight{index}", node=node, exists=True):
        cmds.setAttr(attr, max(0.0, min(1.0, float(weight))))
