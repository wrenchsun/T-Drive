"""tdrive_facial.core.presenters（画面の状態 4 種）のテスト。Qt も Maya も使わない。

- 共通のテストデータ conformance/presenter.json（操作列 → 状態。形式は conformance/README_presenter.md）を全部流す
- そのあと、各コマンド・各ガード（未保存の確認、Neutral の保護、上限、失敗したら文書を変えない）の直接のテスト
"""

import copy
import json
import re
from pathlib import Path

import pytest

from tdrive_facial.core import fcpose_io, naming, space
from tdrive_facial.core import model as m
from tdrive_facial.core import presenters as P
from tdrive_facial.core import profile as prof
from tdrive_facial.core import validate as V

CONF = Path(__file__).parent / "conformance"
TOL = 1e-4
DATA = json.loads((CONF / "presenter.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# JSON ⇔ 内部データ（README_presenter.md の形）
# ---------------------------------------------------------------------------


def camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(w[:1].upper() + w[1:] for w in rest)


def to_json(obj):
    """結果の dataclass を camelCase の素の JSON にする（辞書のキー = シェイプ名などはそのまま）。"""
    if hasattr(obj, "__dataclass_fields__"):
        return {camel(k): to_json(getattr(obj, k)) for k in obj.__dataclass_fields__}
    if isinstance(obj, dict):
        return {k: to_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_json(v) for v in obj]
    return obj


def camel_keys(d: dict) -> dict:
    return {camel(k): v for k, v in d.items()}


def bone_from_json(d: dict) -> m.BoneOffset:
    return m.BoneOffset(
        t=tuple(d.get("t", (0, 0, 0))), r=tuple(d.get("r", (0, 0, 0, 1))), s=tuple(d.get("s", (1, 1, 1)))
    )


def bone_to_json(b: m.BoneOffset) -> dict:
    return {"t": list(b.t), "r": list(b.r), "s": list(b.s)}


def match(exp, act, path="$"):
    """exp が act の部分集合か。辞書は exp のキーだけ、リストは長さも一致、数値は 1e-4、それ以外は ==。"""
    if isinstance(exp, bool) or isinstance(act, bool) or exp is None or act is None:
        assert exp is act or exp == act, f"{path}: 期待 {exp!r} / 実際 {act!r}"
    elif isinstance(exp, (int, float)):
        assert isinstance(act, (int, float)), f"{path}: 数値のはずが {act!r}"
        assert abs(exp - act) <= TOL, f"{path}: 期待 {exp} / 実際 {act}"
    elif isinstance(exp, dict):
        assert isinstance(act, dict), f"{path}: 辞書のはずが {act!r}"
        for k, v in exp.items():
            assert k in act, f"{path}.{k}: 無い（実際のキー {sorted(act)}）"
            match(v, act[k], f"{path}.{k}")
    elif isinstance(exp, list):
        assert isinstance(act, list), f"{path}: リストのはずが {act!r}"
        assert len(exp) == len(act), f"{path}: 長さが違う 期待 {exp!r} / 実際 {act!r}"
        for i, (e, a) in enumerate(zip(exp, act)):
            match(e, a, f"{path}[{i}]")
    else:
        assert exp == act, f"{path}: 期待 {exp!r} / 実際 {act!r}"


def match_pose(exp: dict, curves: dict, bones: dict, path: str) -> None:
    """名前の集合は完全一致（ほぼ 0 の値・恒等のボーンは「無い」扱い）、数値は 1e-4。"""
    if "curves" in exp:
        got = {k: v for k, v in curves.items() if abs(v) > P.CURVE_SAVE_EPS}
        assert set(got) == set(exp["curves"]), f"{path}.curves: 名前 期待 {sorted(exp['curves'])} / 実際 {sorted(got)}"
        for k, v in exp["curves"].items():
            assert abs(got[k] - v) <= TOL, f"{path}.curves.{k}: 期待 {v} / 実際 {got[k]}"
    if "bones" in exp:
        got_b = {k: bone_to_json(b) for k, b in bones.items() if not P._is_bone_identity(b)}
        assert set(got_b) == set(exp["bones"]), f"{path}.bones: 名前 期待 {sorted(exp['bones'])} / 実際 {sorted(got_b)}"
        for k, v in exp["bones"].items():
            match(v, got_b[k], f"{path}.bones.{k}")


# ---------------------------------------------------------------------------
# 操作列の実行
# ---------------------------------------------------------------------------


class Runner:
    def __init__(self, setup: dict):
        d = setup["document"]
        d = DATA["documents"][d] if isinstance(d, str) else d
        self.doc = fcpose_io.from_dict(copy.deepcopy(d))
        profile = prof.from_dict(setup["profile"]) if "profile" in setup else None
        scene = None
        if "scene" in setup:
            s = setup["scene"]
            scene = V.SceneInfo(curves=s.get("curves"), bones=s.get("bones"), targets=s.get("targets"))
        bake = None
        if "bakeState" in setup:
            bake = {}
            for morph, h in setup["bakeState"].items():
                bake[morph] = self._hash_of(morph) if h == "@current" else "stale-hash"
        self.ps = P.PresenterSet(self.doc, profile, bake, scene)
        self.last_export = None

    def _hash_of(self, morph: str) -> str:
        p = naming.parse_name(morph, self.doc.asset)
        layer = next(l for l in self.doc.layers if l.name == p.layer)
        return V.pose_hash(layer.points[(p.row, p.col)].pose)

    # --- op ---

    def run(self, op: str, a: dict):
        ps = self.ps
        g, pz, ly, va = ps.grid, ps.pose, ps.layers, ps.validation
        a = a or {}
        table = {
            "noop": lambda: None,
            "select": lambda: g.select(a["row"], a["col"]),
            "confirm_select": lambda: g.confirm_select(a["choice"]),
            "unkey": lambda: g.unkey(a.get("row"), a.get("col")),
            "clear_point": lambda: g.clear_point(a.get("row"), a.get("col")),
            "clear_layer": lambda: g.clear_layer(),
            "generate": lambda: g.generate(a.get("allLayers", False)),
            "copy_pose": lambda: g.copy_pose(a.get("row"), a.get("col")),
            "paste_pose": lambda: g.paste_pose(a.get("row"), a.get("col")),
            "paste_mirrored": lambda: g.paste_mirrored(a.get("row"), a.get("col")),
            "preview_resize": lambda: g.preview_resize(a["cols"], a["rows"], a.get("yawRange"), a.get("pitchRange")),
            "resize": lambda: g.resize(a["cols"], a["rows"], a.get("yawRange"), a.get("pitchRange")),
            "locate": lambda: g.locate(a["yaw"], a["pitch"]),
            "nearest_point": lambda: g.nearest_point(a["yaw"], a["pitch"]),
            "angles_of": lambda: g.angles_of(a["row"], a["col"]),
            "actions": lambda: camel_keys(g.actions(a.get("row"), a.get("col"))),
            "toolbar": lambda: camel_keys(g.toolbar_actions()),
            "pose.set_curve": lambda: pz.set_curve(a["name"], a["value"]),
            "pose.set_bone": lambda: pz.set_bone(a["name"], bone_from_json(a)),
            "pose.reset_bone": lambda: pz.reset_bone(a["name"]),
            "pose.reset_bones": lambda: pz.reset_bones(),
            "pose.zero": lambda: pz.zero(),
            "pose.mirror": lambda: pz.mirror(),
            "pose.save": lambda: pz.save(a.get("keepEmptyKey", False)),
            "pose.reload": lambda: pz.reload(),
            "pose.ingest": lambda: pz.ingest(
                a.get("curves", {}),
                {k: bone_from_json(v) for k, v in a.get("bones", {}).items()},
                replace=a.get("replace", True),
                working_set_only=a.get("workingSetOnly"),
            ),
            "pose.set_filter": lambda: pz.set_filter(a["text"]),
            "pose.set_bone_filter": lambda: pz.set_bone_filter(a["text"]),
            "pose.set_working_set_only": lambda: pz.set_working_set_only(a["value"]),
            "pose.export": self._export,
            "pose.import_last_export": lambda: pz.import_pose(self.last_export),
            "layer.add": lambda: ly.add(a["name"]),
            "layer.add_or_select": lambda: ly.add_or_select(a["name"]),
            "layer.rename": lambda: ly.rename(a["index"], a["name"]),
            "layer.set_enabled": lambda: ly.set_enabled(a["index"], a["enabled"]),
            "layer.set_emotion_curve": lambda: ly.set_emotion_curve(a["index"], a["curve"]),
            "layer.delete": lambda: ly.delete(a["index"]),
            "layer.set_active": lambda: ly.set_active(a["index"]),
            "layer.confirm_set_active": lambda: ly.confirm_set_active(a["choice"]),
            "layer.copy_from": lambda: ly.copy_from(a["source"], a["dest"]),
            "validation.run": lambda: va.run(),
            "validation.set_choice": lambda: va.set_choice(a["kind"], a["old"], a["new"]),
            "validation.apply_renames": lambda: va.apply_renames(),
            "validation.remove_missing": lambda: va.remove_missing(a.get("includeCaseMismatch", False)),
            "bake.mark_point": lambda: self._mark(a),
            "bake.clear": lambda: ps.ctx.set_bake_state({}),
        }
        return table[op]()

    def _export(self):
        doc = self.ps.pose.export_pose_document()
        if doc is None:
            return P.CommandResult(ok=False, code="empty")
        self.last_export = self.ps.pose.export_text()
        return {
            "ok": True,
            "format": doc.format,
            "curves": dict(doc.pose.curves),
            "bones": {k: bone_to_json(b) for k, b in doc.pose.bones.items()},
        }

    def _mark(self, a):
        ctx = self.ps.ctx
        layer = self.doc.layers[a["layer"]]
        morph = naming.morph_name(self.doc.asset, layer.name, a["row"], a["col"])
        if ctx.bake_state is None:
            ctx.bake_state = {}
        ctx.bake_state[morph] = V.pose_hash(layer.points[(a["row"], a["col"])].pose)
        ctx.notify_bake_changed()

    # --- expect ---

    def check(self, exp: dict, result, where: str) -> None:
        ps, doc = self.ps, self.doc
        if "result" in exp:
            match(exp["result"], to_json(result), f"{where}.result")
        if "selection" in exp:
            sel = ps.ctx.selection
            match(exp["selection"], None if sel is None else list(sel), f"{where}.selection")
        if "activeLayer" in exp:
            assert ps.ctx.active_layer == exp["activeLayer"], f"{where}.activeLayer: {ps.ctx.active_layer}"
        if "dirty" in exp:
            assert ps.pose.dirty is exp["dirty"], f"{where}.dirty: {ps.pose.dirty}"
        if "clipboard" in exp:
            assert (ps.ctx.clipboard is not None) is exp["clipboard"], f"{where}.clipboard"
        if "grid" in exp:
            match(exp["grid"], to_json(doc.grid), f"{where}.grid")
        if "points" in exp:
            for e in exp["points"]:
                got = to_json(ps.grid.point_view(e["row"], e["col"]))
                match(e, got, f"{where}.points[R{e['row']}C{e['col']}]")
        if "summary" in exp:
            match(exp["summary"], to_json(ps.grid.summary()), f"{where}.summary")
        if "gridView" in exp:
            match(exp["gridView"], to_json(ps.grid.view()), f"{where}.gridView")
        if "layers" in exp:
            match(exp["layers"], to_json(ps.layers.view().layers), f"{where}.layers")
        if "layerView" in exp:
            match(exp["layerView"], to_json(ps.layers.view()), f"{where}.layerView")
        if "pose" in exp:
            match_pose(exp["pose"], ps.pose.curves, ps.pose.bones, f"{where}.pose")
        if "poseRows" in exp:
            v = ps.pose.view()
            got = {"curves": [r.name for r in v.curves], "bones": [r.name for r in v.bones]}
            match(exp["poseRows"], got, f"{where}.poseRows")
        if "poseView" in exp:
            match(exp["poseView"], to_json(ps.pose.view()), f"{where}.poseView")
        if "docPoints" in exp:
            for e in exp["docPoints"]:
                pt = doc.layers[e["layer"]].points.get((e["row"], e["col"]))
                tag = f"{where}.docPoints[L{e['layer']}R{e['row']}C{e['col']}]"
                if e.get("absent"):
                    assert pt is None, f"{tag}: 点があってはいけない"
                    continue
                assert pt is not None, f"{tag}: 点が無い"
                if "isKey" in e:
                    assert pt.is_key is e["isKey"], f"{tag}.isKey"
                match_pose(e, pt.pose.curves, pt.pose.bones, tag)
        if "issueCodes" in exp:
            assert [i.code for i in ps.validation.issues] == exp["issueCodes"], f"{where}.issueCodes"
        if "validationView" in exp:
            match(exp["validationView"], to_json(ps.validation.view()), f"{where}.validationView")


def _cases():
    return [pytest.param(c, id=c["name"]) for c in DATA["cases"]]


@pytest.mark.parametrize("case", _cases())
def test_conformance_case(case):
    r = Runner(case["setup"])
    for n, st in enumerate(case["steps"]):
        where = f"{case['name']}#{n}:{st['op']}"
        result = r.run(st["op"], st.get("args"))
        r.check(st.get("expect", {}), result, where)


def test_presenter_json_shape():
    assert DATA["kind"] == "presenter"
    names = [c["name"] for c in DATA["cases"]]
    assert len(names) == len(set(names))
    assert all(c["source"] == "python-port" for c in DATA["cases"])
    for c in DATA["cases"]:
        d = c["setup"]["document"]
        assert isinstance(d, dict) or d in DATA["documents"], c["name"]


# ---------------------------------------------------------------------------
# 直接のテスト
# ---------------------------------------------------------------------------


def make_doc(asset="a") -> m.Document:
    d = m.Document()
    d.asset = asset
    d.working_set = m.WorkingSet(curves=["Smile_L", "Smile_R", "JawOpen"], bones=["Eye_L", "Eye_R"])
    d.layers[0].points[(1, 2)] = m.GridPoint(1, 2, True, m.SourcePose({"JawOpen": 0.4}))
    d.layers[0].points[(1, 3)] = m.GridPoint(1, 3, True, m.SourcePose({"Smile_L": 0.8}))
    d.layers[0].points[(2, 3)] = m.GridPoint(2, 3, False, m.SourcePose({"Smile_L": 0.2}))
    return d


def make_set(**kw) -> P.PresenterSet:
    return P.PresenterSet(kw.pop("doc", None) or make_doc(), **kw)


def snap(doc) -> dict:
    return fcpose_io.to_dict(doc)


class Recorder:
    def __init__(self, source):
        self.events = []
        self.off = source.subscribe(self.events.append)


# --- 通知・文脈 ---


def test_observable_subscribe_and_unsubscribe():
    ps = make_set()
    rec = Recorder(ps.grid)
    ps.ctx.set_selection((1, 2))
    assert "grid" in rec.events
    rec.events.clear()
    rec.off()
    rec.off()  # 二重に呼んでも安全
    ps.ctx.set_selection((0, 0))
    assert rec.events == []


def test_every_presenter_emits_on_change():
    ps = make_set()
    rg, rp, rl, rv = Recorder(ps.grid), Recorder(ps.pose), Recorder(ps.layers), Recorder(ps.validation)
    ps.grid.select(1, 2)
    ps.pose.set_curve("JawOpen", 0.5)
    ps.layers.add("Joy")
    ps.validation.run()
    assert rg.events and "pose" in rp.events and "layers" in rl.events and "issues" in rv.events


def test_set_document_resets_selection_and_layer_but_keeps_clipboard():
    ps = make_set()
    ps.grid.select(1, 3)
    ps.grid.copy_pose()
    ps.layers.add("Joy")
    ps.layers.set_active(1)
    ps.ctx.set_document(make_doc())
    assert ps.ctx.selection is None and ps.ctx.active_layer == 0 and ps.ctx.clipboard is not None
    assert ps.pose.curves == {}


def test_notify_document_changed_rereads_selected_pose():
    ps = make_set()
    ps.grid.select(1, 2)
    ps.ctx.doc.layers[0].points[(1, 2)].pose = m.SourcePose({"JawOpen": 0.9})  # Undo の復元など（Presenter を通さない）
    ps.ctx.notify_document_changed()
    assert ps.pose.curves == {"JawOpen": 0.9} and not ps.pose.dirty


def test_failed_commands_do_not_change_the_document():
    ps = make_set()
    before = snap(ps.ctx.doc)
    ps.grid.select(0, 0)
    fails = [
        ps.grid.unkey(),
        ps.grid.clear_point(),
        ps.grid.paste_pose(),
        ps.grid.paste_mirrored(),
        ps.grid.copy_pose(),
        ps.grid.resize(0, 3),
        ps.grid.resize(3, 3, yaw_range=-1),
        ps.layers.delete(0),
        ps.layers.rename(0, "X"),
        ps.layers.add(""),
        ps.layers.copy_from(0, 0),
        ps.validation.remove_missing(),
        ps.validation.apply_renames(),
    ]
    assert all(not r.ok for r in fails)
    assert snap(ps.ctx.doc) == before


def test_each_mutating_command_changes_the_document_once_and_restores_from_snapshot():
    """Undo の約束: コマンドの前にスナップショットを取れば、1 回の呼び出しの前後が分かれる。"""
    ps = make_set()
    ps.grid.select(1, 3)
    for command in (
        lambda: ps.grid.unkey(),
        lambda: ps.grid.generate(),
        lambda: ps.grid.clear_layer(),
    ):
        before = snap(ps.ctx.doc)
        copy_before = copy.deepcopy(ps.ctx.doc)
        assert command().ok
        assert snap(ps.ctx.doc) != before
        assert snap(copy_before) == before  # 前のスナップショットは後の変更の影響を受けない


# --- GridPresenter ---


def test_point_states_colors_and_view_shape():
    ps = make_set()
    v = ps.grid.view()
    assert (v.rows, v.cols, len(v.points)) == (3, 5, 15)
    assert v.display_rows == [2, 1, 0]
    pts = {(p.row, p.col): p for p in v.points}
    assert (pts[(1, 2)].state, pts[(1, 2)].color) == ("key", "green")
    assert (pts[(2, 3)].state, pts[(2, 3)].color) == ("generated", "cyan")
    assert (pts[(0, 0)].state, pts[(0, 0)].color) == ("empty", "grey")
    assert [l[1] for l in v.legend] == ["green", "cyan", "grey"]
    assert v.points[1 * 5 + 2] is not None and v.points[1 * 5 + 2].col == 2  # 行優先
    assert pts[(0, 0)].tooltip == "Yaw -90.0° / Pitch -45.0°  (R0, C0)"


def test_empty_pose_point_is_empty_unless_key():
    ps = make_set()
    ps.ctx.doc.layers[0].points[(0, 0)] = m.GridPoint(0, 0, False, m.SourcePose())
    ps.ctx.doc.layers[0].points[(0, 1)] = m.GridPoint(0, 1, True, m.SourcePose())
    assert ps.grid.point_view(0, 0).state == "empty"
    assert ps.grid.point_view(0, 1).state == "key"  # 補正なしのキー（autofill も実キーとして集める）
    assert ps.grid.point_view(0, 1).bake == "none"


def test_bake_unknown_when_no_bake_state_or_no_asset():
    ps = make_set()
    assert ps.grid.point_view(1, 2).bake == P.BAKE_UNKNOWN
    ps2 = make_set(doc=make_doc(asset=""), bake_state={})
    assert ps2.grid.point_view(1, 2).bake == P.BAKE_UNKNOWN
    assert ps2.grid.point_view(1, 2).morph == ""


def test_summary_counts_only_non_empty_points_as_unbaked_and_text():
    ps = make_set(bake_state={})
    s = ps.grid.summary()
    assert (s.keys, s.generated, s.empty, s.unbaked, s.total) == (2, 1, 12, 3, 15)
    assert s.text == "キー 2 / 生成 1 / 空 12 / 未ベイク 3"
    ps.ctx.bake_state[naming.morph_name("a", "Neutral", 1, 3)] = "x"
    ps.ctx.notify_bake_changed()
    assert ps.grid.summary().text.endswith("未ベイク 2 / 変更あり 1")


def test_bake_status_follows_bake_state_changes_and_notifies():
    ps = make_set(bake_state={})
    rec = Recorder(ps.grid)
    morph = naming.morph_name("a", "Neutral", 1, 2)
    ps.ctx.bake_state[morph] = V.pose_hash(ps.ctx.doc.layers[0].points[(1, 2)].pose)
    ps.ctx.notify_bake_changed()
    assert ps.grid.point_view(1, 2).bake == "baked" and "grid" in rec.events
    ps.ctx.set_bake_state(None)
    assert ps.grid.point_view(1, 2).bake == "unknown"


@pytest.mark.parametrize(
    "yaw,pitch,col_pos,row_pos,clamped,nearest",
    [
        (0, 0, 2.0, 1.0, False, (1, 2)),
        (90, 45, 4.0, 2.0, False, (2, 4)),  # 範囲の端ちょうどはクランプでない
        (-90.5, 0, -0.0111, 1.0, True, (1, 0)),
        (22.5, 0, 2.5, 1.0, False, (1, 3)),  # 同距離は大きい方の点
        (0, 100, 2.0, 3.2222, True, (2, 2)),
    ],
)
def test_locate(yaw, pitch, col_pos, row_pos, clamped, nearest):
    mk = make_set().grid.locate(yaw, pitch)
    assert mk.col_pos == pytest.approx(col_pos, abs=1e-3)
    assert mk.row_pos == pytest.approx(row_pos, abs=1e-3)
    assert mk.clamped is clamped
    assert (mk.nearest_row, mk.nearest_col) == nearest


def test_locate_single_column_and_row():
    ps = make_set()
    ps.ctx.doc.grid.cols = 1
    ps.ctx.doc.grid.rows = 1
    mk = ps.grid.locate(30, 10)
    assert (mk.col_pos, mk.row_pos, mk.nearest_row, mk.nearest_col) == (0.0, 0.0, 0, 0)
    assert ps.grid.angles_of(0, 0) == (0.0, 0.0)


def test_angles_roundtrip_through_nearest_point():
    ps = make_set()
    for r in range(3):
        for c in range(5):
            yaw, pitch = ps.grid.angles_of(r, c)
            assert ps.grid.nearest_point(yaw, pitch) == (r, c)


def test_select_returns_angles_and_notifies_pose_reload():
    ps = make_set()
    res = ps.grid.select(1, 3)
    assert (res.status, res.yaw, res.pitch, res.camera_jump) == ("selected", 45.0, 0.0, True)
    assert ps.pose.curves == {"Smile_L": 0.8}


def test_select_guard_only_when_dirty_and_other_point():
    ps = make_set()
    ps.grid.select(1, 2)
    assert ps.grid.select(1, 3).status == "selected"  # きれいなら確認なし
    ps.pose.set_curve("Smile_L", 0.1)
    assert ps.grid.select(1, 3).status == "same_point"
    res = ps.grid.select(0, 0)
    assert res.status == "needs_confirm" and ps.grid.pending == (0, 0) and ps.ctx.selection == (1, 3)
    assert ps.grid.confirm_select("bogus").status == "invalid"
    assert ps.grid.pending == (0, 0)  # 不正な答えでは保留が残る
    assert ps.grid.confirm_select("save").saved is True
    assert ps.grid.pending is None and ps.ctx.selection == (0, 0)


def test_select_new_pending_replaces_old_and_document_change_clears_it():
    ps = make_set()
    ps.grid.select(1, 2)
    ps.pose.set_curve("JawOpen", 0.9)
    ps.grid.select(0, 0)
    ps.grid.select(0, 1)
    assert ps.grid.pending == (0, 1)
    ps.ctx.set_document(make_doc())
    assert ps.grid.pending is None


def test_unkey_removes_empty_non_key_point_and_keeps_pose_otherwise():
    ps = make_set()
    ps.ctx.doc.layers[0].points[(0, 1)] = m.GridPoint(0, 1, True, m.SourcePose())
    assert ps.grid.unkey(0, 1).ok
    assert (0, 1) not in ps.ctx.doc.layers[0].points
    assert ps.grid.unkey(1, 3).ok
    assert ps.ctx.doc.layers[0].points[(1, 3)].pose.curves == {"Smile_L": 0.8}


def test_clear_point_clears_key_with_empty_pose_and_outside_points_via_clear_layer():
    ps = make_set()
    ps.ctx.doc.layers[0].points[(9, 9)] = m.GridPoint(9, 9, False, m.SourcePose({"X": 1.0}))
    assert ps.grid.clear_layer().ok
    assert ps.ctx.doc.layers[0].points == {}


def test_generate_all_layers_and_stale_reporting():
    d = make_doc()
    d.layers.append(m.Layer("Joy", points={(1, 2): m.GridPoint(1, 2, True, m.SourcePose({"Smile_L": 1.0}))}))
    bake = {naming.morph_name("a", "Neutral", 2, 3): "h"}
    ps = make_set(doc=d, bake_state=bake)
    res = ps.grid.generate(all_layers=True)
    assert res.ok and res.summary.layers == 2
    assert res.stale_morphs == []  # 生成の結果が空にならない点は消えない
    # 生成の点が消える場合: キーを外して全部空に（unkey → 結果の点は生成のまま。generate は skipped になる）
    ps2 = make_set(doc=make_doc(), bake_state={naming.morph_name("a", "Neutral", 2, 3): "h"})
    ps2.grid.unkey(1, 2)
    ps2.grid.unkey(1, 3)
    res2 = ps2.grid.generate()
    assert not res2.ok and res2.code == "no_keys" and res2.summary.skipped_layers == ["Neutral"]


def test_paste_keeps_point_extra_and_makes_key():
    ps = make_set()
    ps.ctx.doc.layers[0].points[(2, 3)].extra["note"] = 1
    ps.grid.copy_pose(1, 2)
    assert ps.grid.paste_pose(2, 3).ok
    p = ps.ctx.doc.layers[0].points[(2, 3)]
    assert p.is_key and p.extra == {"note": 1} and p.pose.curves == {"JawOpen": 0.4}
    p.pose.curves["JawOpen"] = 9  # 貼ったポーズはクリップボードと別物
    assert ps.ctx.clipboard.curves == {"JawOpen": 0.4}


def test_paste_mirrored_mirrors_bones_and_drops_excluded_names():
    d = make_doc()
    d.mirror.exclude = ["Jaw"]
    d.layers[0].points[(1, 3)].pose.bones["Eye_L"] = m.BoneOffset(t=(0.0, 2.0, 1.0))
    d.layers[0].points[(1, 3)].pose.curves["JawOpen"] = 0.5
    ps = make_set(doc=d)
    ps.grid.copy_pose(1, 3)
    assert ps.grid.paste_mirrored(1, 1).ok
    pose = d.layers[0].points[(1, 1)].pose
    assert pose.curves == {"Smile_R": 0.8} and pose.bones["Eye_R"].t == (0.0, -2.0, 1.0)


def test_actions_without_selection_or_outside_grid_are_all_false():
    ps = make_set()
    assert not any(ps.grid.actions().values())
    assert not any(ps.grid.actions(9, 9).values())
    assert set(ps.grid.actions(1, 2)) == set(P.POINT_ACTIONS)


def test_bake_point_action_needs_asset():
    ps = make_set(doc=make_doc(asset=""))
    assert ps.grid.actions(1, 2)[P.ACTION_BAKE_POINT] is False


def test_resize_rolls_back_the_selection_and_returns_dropped_keys():
    ps = make_set()
    pre = ps.grid.preview_resize(3, 3)
    assert pre.ok and pre.dry_run and len(pre.dropped_keys) == 1 and ps.ctx.doc.grid.cols == 5
    ps.grid.select(1, 4)
    res = ps.grid.resize(3, 3)
    assert res.ok and not res.dry_run and res.dropped_keys[0].col == 3
    assert ps.ctx.selection is None and ps.ctx.doc.grid.cols == 3
    ps.grid.select(1, 1)
    assert ps.grid.resize(5, 3).ok and ps.ctx.selection == (1, 1)  # 範囲内なら選択は残る


def test_resize_rejects_non_integers_and_bad_ranges_without_change():
    ps = make_set()
    before = snap(ps.ctx.doc)
    assert ps.grid.resize(2.5, 3).code == "invalid_size"
    assert ps.grid.resize(3, 3, pitch_range=float("nan")).code == "invalid_range"
    assert ps.grid.preview_resize(3, 0).code == "invalid_size"
    assert snap(ps.ctx.doc) == before


def test_clear_layer_on_non_neutral_layer_only():
    ps = make_set()
    ps.layers.add("Joy")
    ps.layers.copy_from(0, 1)
    ps.layers.set_active(1)
    assert ps.grid.clear_layer().ok
    assert ps.ctx.doc.layers[0].points and not ps.ctx.doc.layers[1].points


# --- PosePresenter ---


def test_dirty_tolerances_match_ue():
    ps = make_set()
    ps.grid.select(1, 2)
    ps.pose.set_curve("JawOpen", 0.4009)
    assert not ps.pose.dirty
    ps.pose.set_curve("JawOpen", 0.402)
    assert ps.pose.dirty
    ps.pose.reload()
    ps.pose.set_bone("Eye_L", m.BoneOffset(t=(0.0005, 0, 0)))
    assert not ps.pose.dirty  # 恒等に近いボーンは未保存でない
    ps.pose.set_bone("Eye_L", m.BoneOffset(t=(0.01, 0, 0)))
    assert ps.pose.dirty


def test_dirty_treats_q_and_minus_q_as_same_rotation():
    d = make_doc()
    d.layers[0].points[(1, 2)].pose.bones["Eye_L"] = m.BoneOffset(r=(0.0, 0.0, 0.2588, 0.9659))
    ps = make_set(doc=d)
    ps.grid.select(1, 2)
    ps.pose.set_bone("Eye_L", m.BoneOffset(r=(0.0, 0.0, -0.2588, -0.9659)))
    assert not ps.pose.dirty


def test_set_curve_clamps_with_profile_and_doc_limits_and_nan():
    p = prof.NamingProfile(name="p", limits={"Smile_L": (0.0, 2.0)})
    ps = make_set(profile=p)
    ps.grid.select(0, 0)
    assert ps.pose.set_curve("Smile_L", 5).value == 2.0
    ps.ctx.doc.limits = {"Smile_L": (0.0, 1.5)}  # 文書の値が勝つ
    assert ps.pose.set_curve("Smile_L", 5).value == 1.5
    assert ps.pose.set_curve("Smile_R", float("nan")).value == 0.0
    r = ps.pose.set_curve("Smile_L", 0.5)
    assert r.clamped is False and r.message == ""


def test_pose_commands_need_a_point():
    ps = make_set()
    for res in (
        ps.pose.set_curve("a", 1),
        ps.pose.remove_curve("a"),
        ps.pose.set_bone("b", m.BoneOffset()),
        ps.pose.reset_bone("b"),
        ps.pose.reset_bones(),
        ps.pose.zero(),
        ps.pose.mirror(),
        ps.pose.save(),
        ps.pose.ingest({"a": 1}),
        ps.pose.import_pose(PoseDoc()),
    ):
        assert not res.ok and res.code == "no_point"
    assert not ps.pose.dirty and ps.pose.view().can_edit is False


def PoseDoc(curves=None, bones=None, meta=None):
    return m.PoseDocument(meta=meta or m.Meta(), pose=m.SourcePose(curves or {}, bones or {}))


def test_remove_curve_and_bone_offset_copy():
    ps = make_set()
    ps.grid.select(1, 2)
    ps.pose.remove_curve("JawOpen")
    assert ps.pose.dirty and ps.pose.curves == {}
    off = ps.pose.bone_offset("none")
    off.t = (1.0, 0.0, 0.0)
    assert ps.pose.bones == {}  # 返り値は複製


def test_save_trims_and_does_not_touch_other_points_and_extra():
    ps = make_set()
    ps.ctx.doc.layers[0].points[(1, 3)].extra["x"] = 1
    ps.grid.select(1, 3)
    ps.pose.set_curve("Smile_L", 0.5)
    ps.pose.set_curve("Smile_R", 0.00001)
    assert ps.pose.save().ok
    p = ps.ctx.doc.layers[0].points[(1, 3)]
    assert p.pose.curves == {"Smile_L": 0.5} and p.extra == {"x": 1} and p.is_key
    assert ps.ctx.doc.layers[0].points[(1, 2)].pose.curves == {"JawOpen": 0.4}
    assert ps.pose.curves == {"Smile_L": 0.5}  # 保存後は読み直した状態


def test_pose_to_apply_equals_what_is_saved_and_is_a_copy():
    ps = make_set()
    ps.grid.select(0, 0)
    ps.pose.set_curve("Smile_L", 0.5)
    ps.pose.set_curve("Smile_R", 0.0)
    ps.pose.set_bone("Eye_L", m.BoneOffset())
    out = ps.pose.pose_to_apply()
    assert out.curves == {"Smile_L": 0.5} and out.bones == {}
    out.curves["Smile_L"] = 9
    assert ps.pose.curves["Smile_L"] == 0.5


def test_mirror_swaps_names_mirrors_bone_and_keeps_excluded_untouched():
    d = make_doc()
    d.mirror.exclude = ["Jaw"]
    d.layers[0].points[(1, 3)].pose.curves["JawOpen"] = 0.3
    d.layers[0].points[(1, 3)].pose.bones["Jaw_bone"] = m.BoneOffset(t=(0.0, 1.0, 0.0))
    ps = make_set(doc=d)
    ps.grid.select(1, 3)
    assert ps.pose.mirror().ok
    assert ps.pose.curves == {"Smile_R": 0.8, "JawOpen": 0.3}
    assert ps.pose.bones["Jaw_bone"].t == (0.0, 1.0, 0.0)  # 除外パターンの名前は反転せず残る
    assert ps.pose.view().can_mirror is True


def test_ingest_replace_scope_and_report_fields():
    ps = make_set(scene=V.SceneInfo(curves=["JawOpen", "Smile_L"], bones=["Eye_L"]))
    ps.grid.select(1, 2)
    ps.pose.set_curve("Other", 0.5)  # 作業セットの外の編集（置き換えの範囲外）
    rep = ps.pose.ingest({"Smile_L": 0.3, "Smile_R": 0.2, "Extra": 1.0})
    assert rep.ignored == ["Extra"] and rep.unknown == ["Smile_R"]
    assert ps.pose.curves == {"Other": 0.5, "Smile_L": 0.3, "Smile_R": 0.2}  # JawOpen は範囲内で渡されず消えた
    assert rep.removed == 1 and rep.curves_set == 2 and rep.changed
    # 作業セットの絞り込みを切ると全部が範囲
    rep2 = ps.pose.ingest({"Smile_L": 0.3}, working_set_only=False)
    assert ps.pose.curves == {"Smile_L": 0.3} and rep2.removed == 2


def test_ingest_with_empty_working_set_takes_everything():
    d = make_doc()
    d.working_set = m.WorkingSet()
    ps = make_set(doc=d)
    ps.grid.select(0, 0)
    rep = ps.pose.ingest({"Anything": 0.5})
    assert rep.ignored == [] and ps.pose.curves == {"Anything": 0.5}


def test_ingest_notifies_only_when_changed():
    ps = make_set()
    ps.grid.select(1, 2)
    rec = Recorder(ps.pose)
    ps.pose.ingest({"JawOpen": 0.4})
    assert rec.events == []
    ps.pose.ingest({"JawOpen": 0.6})
    assert rec.events == ["pose"]


def test_working_set_view_flags_and_hidden_counts():
    ps = make_set(scene=V.SceneInfo(curves=["Smile_L", "JawOpen", "Blink"], bones=["Eye_L", "Eye_R", "head"]))
    ps.grid.select(1, 2)
    ps.pose.ingest({"Blink": 0.5}, working_set_only=False, replace=False)
    v = ps.pose.view()
    assert [c.name for c in v.curves] == ["Smile_L", "Smile_R", "JawOpen"]
    assert [c.missing for c in v.curves] == [False, True, False]  # Smile_R はモデルに無い
    assert v.hidden_curves == 1 and v.working_set_active and v.edited_curves == 2
    ps.pose.set_working_set_only(False)
    v = ps.pose.view()
    assert [c.name for c in v.curves] == ["Blink", "JawOpen", "Smile_L", "Smile_R"] and v.hidden_curves == 0


def test_working_set_filter_is_ignored_when_the_set_is_empty():
    d = make_doc()
    d.working_set = m.WorkingSet()
    ps = make_set(doc=d, scene=V.SceneInfo(curves=["B", "A"], bones=["x"]))
    ps.grid.select(1, 2)
    v = ps.pose.view()
    assert [c.name for c in v.curves] == ["A", "B", "JawOpen"] and v.working_set_active is False


def test_text_filter_is_case_insensitive_substring_and_view_status_text():
    ps = make_set()
    ps.grid.select(1, 2)
    ps.pose.set_filter("SMI")
    assert [c.name for c in ps.pose.view().curves] == ["Smile_L", "Smile_R"]
    assert ps.pose.view().status_text == ""
    ps.pose.set_curve("JawOpen", 1.0)
    assert ps.pose.view().status_text == " [未保存]"


def test_export_import_text_roundtrip_and_errors():
    ps = make_set()
    ps.grid.select(1, 3)
    ps.pose.set_bone("Eye_L", m.BoneOffset(t=(0.0, 2.0, 1.0)))
    text = ps.pose.export_text()
    assert '"format": "FacialPose"' in text
    ps.grid.select(0, 0)
    rep = ps.pose.import_pose(text)
    assert rep.ok and rep.curves_set == 1 and rep.bones_set == 1 and ps.pose.dirty
    assert ps.pose.bones["Eye_L"].t == (0.0, 2.0, 1.0)
    assert ps.pose.import_pose("{ not json").code == "invalid"
    full = fcpose_io.dumps(make_doc())
    assert ps.pose.import_pose(full).code == "not_pose"
    assert ps.pose.export_pose_document().meta.unit == "cm"


def test_import_converts_coordinate_system_and_respects_working_set_and_limits():
    ps = make_set()  # 文書は UE 系（cm / Z-up / 左手）
    ps.grid.select(0, 0)
    src_meta = m.Meta(unit="cm", up_axis="Y", handedness="right")
    pd = PoseDoc({"Smile_L": 3.0, "Other": 0.5}, {"Eye_L": m.BoneOffset(t=(1.0, 0.0, 0.0))}, src_meta)
    rep = ps.pose.import_pose(pd, working_set_only=True)
    assert rep.ignored == ["Other"] and rep.clamped == ["Smile_L"]
    assert ps.pose.curves == {"Smile_L": 1.0}
    expected = space.convert_pose_document(pd, ps.ctx.doc.meta).pose.bones["Eye_L"]  # 変換の中身は test_space が確かめる
    assert ps.pose.bones["Eye_L"].t == pytest.approx(expected.t)


def test_import_replaces_the_buffer_first():
    ps = make_set()
    ps.grid.select(1, 2)  # JawOpen 0.4
    ps.pose.import_pose(PoseDoc({"Smile_L": 0.3}))
    assert ps.pose.curves == {"Smile_L": 0.3}


def test_reload_and_zero_notify_and_do_not_touch_document():
    ps = make_set()
    ps.grid.select(1, 2)
    before = snap(ps.ctx.doc)
    ps.pose.zero()
    ps.pose.reload()
    assert snap(ps.ctx.doc) == before and ps.pose.curves == {"JawOpen": 0.4}


# --- LayerPresenter ---


def test_layer_view_rows_and_copy_sources():
    ps = make_set(bake_state={})
    ps.layers.add("Joy")
    v = ps.layers.view()
    assert [r.name for r in v.layers] == ["Neutral", "Joy"]
    n, j = v.layers
    assert (n.is_neutral, n.can_rename, n.can_delete, n.can_set_emotion_curve) == (True, False, False, False)
    assert (j.can_rename, j.can_delete, j.can_set_emotion_curve) == (True, True, True)
    assert (n.points, n.keys, n.baked) == (3, 2, 0)
    assert v.copy_sources == {0: [1], 1: [0]} and v.limit == 16 and v.count == 2


def test_preset_names():
    assert P.EMOTION_PRESETS == ("Anger", "Contempt", "Disgust", "Fear", "Joy", "Sadness", "Surprise")
    ps = make_set()
    for name in P.EMOTION_PRESETS:
        assert ps.layers.add(name).ok
    assert [r.name for r in ps.layers.view().layers] == ["Neutral", *P.EMOTION_PRESETS]
    assert all(already for _name, already in ps.layers.view().presets)


def test_check_name_matches_validate_rules():
    """check_name が通す名前は、レイヤーに足しても validate のレイヤー名の検出（エラー・警告）が出ない。"""
    ps = make_set()
    good = ["Joy", "Anger_2", "L1", "abc123", "A_B"]
    bad = {"": "empty", "  ": "empty", "Persp": "reserved", "a b": "chars", "笑顔": "chars", "a-b": "chars", "Neutral": "duplicate", "neutral": "case_collision"}
    for name in good:
        assert ps.layers.check_name(name).ok, name
    for name, code in bad.items():
        assert ps.layers.check_name(name).code == code, name
    d = make_doc()
    for name in good:
        d.layers.append(m.Layer(name=name))
    codes = {i.code for i in V.validate(d, V.SceneInfo())}
    assert not codes & {"layer_name_empty", "layer_name_chars", "layer_name_reserved", "layer_name_duplicate", "layer_name_case_collision"}


def test_check_name_strips_whitespace_and_ignores_self_when_renaming():
    ps = make_set()
    ps.layers.add("Joy")
    assert ps.layers.check_name("  Joy  ", ignore_index=1).ok
    assert ps.layers.check_name("Joy ").code == "duplicate"
    assert ps.layers.add("  Fear ").ok and ps.ctx.doc.layers[-1].name == "Fear"


def test_add_limit_is_sixteen_including_neutral_and_does_not_change_active():
    ps = make_set()
    for i in range(15):
        assert ps.layers.add(f"L{i}").ok
    assert len(ps.ctx.doc.layers) == P.MAX_LAYER_COUNT == 16
    assert ps.layers.add("More").code == "limit"
    assert ps.layers.view().can_add is False
    assert ps.ctx.active_layer == 0


def test_add_or_select_with_dirty_pose_returns_pending_switch():
    ps = make_set()
    ps.grid.select(1, 2)
    ps.pose.set_curve("JawOpen", 0.9)
    res = ps.layers.add_or_select("Joy")
    assert res.ok and res.index == 1 and res.switch.status == "needs_confirm"
    assert ps.ctx.active_layer == 0 and ps.layers.pending == 1 and len(ps.ctx.doc.layers) == 2
    assert ps.layers.confirm_set_active("discard").status == "selected" and ps.ctx.active_layer == 1
    assert ps.layers.confirm_set_active("discard").status == "invalid"


def test_set_active_guard_and_unknown_answers():
    ps = make_set()
    ps.layers.add("Joy")
    ps.grid.select(1, 2)
    ps.pose.set_curve("JawOpen", 0.9)
    assert ps.layers.set_active(1).status == "needs_confirm"
    assert ps.layers.confirm_set_active("bogus").status == "invalid" and ps.layers.pending == 1
    assert ps.layers.confirm_set_active("cancel").status == "cancelled" and ps.layers.pending is None
    assert ps.pose.dirty and ps.ctx.active_layer == 0


def test_set_active_reloads_pose_of_the_same_point_in_the_new_layer():
    ps = make_set()
    ps.layers.add("Joy")
    ps.grid.select(1, 3)
    assert ps.pose.curves == {"Smile_L": 0.8}
    ps.layers.set_active(1)
    assert ps.pose.curves == {} and ps.ctx.selection == (1, 3)


def test_rename_reports_stale_morphs_and_warning_and_notifies():
    ps = make_set(bake_state={naming.morph_name("a", "Joy", 1, 2): "h"})
    ps.layers.add("Joy")
    ps.layers.copy_from(0, 1)
    ps.ctx.doc.layers[1].points[(1, 2)].pose = m.SourcePose({"Smile_L": 1.0})
    res = ps.layers.rename(1, "Happy")
    assert res.ok and res.stale_morphs == ["FC_a_Joy_R1_C2"] and "EmotionWeights" in res.warnings[0]
    assert ps.ctx.doc.layers[1].name == "Happy"


def test_emotion_curve_warnings():
    ps = make_set()
    ps.layers.add("Joy")
    assert ps.layers.set_emotion_curve(1, "joy_w").warnings == []
    r = ps.layers.set_emotion_curve(1, "")
    assert r.ok and r.warnings and "解除" in r.warnings[0] and ps.ctx.doc.layers[1].emotion_curve == ""
    ps.layers.set_emotion_curve(1, " w ")
    assert ps.ctx.doc.layers[1].emotion_curve == "w"
    r = ps.layers.set_emotion_curve(1, "w2")
    assert "w2" in r.warnings[0]


def test_set_enabled_applies_to_neutral_too_and_range_checks():
    ps = make_set()
    assert ps.layers.set_enabled(0, False).ok and ps.ctx.doc.layers[0].enabled is False
    assert ps.layers.set_enabled(3, True).code == "range"
    assert ps.layers.rename(7, "X").code == "range" and ps.layers.set_emotion_curve(7, "x").code == "range"


def test_delete_active_layer_adjustments_and_discarded_edits():
    ps = make_set()
    for n in ("A", "B", "C"):
        ps.layers.add(n)
    ps.layers.set_active(3)
    r = ps.layers.delete(1)  # 前のレイヤーを消す: 番号を詰める。同じレイヤーなので編集中の値は残る
    assert ps.ctx.active_layer == 2 and ps.ctx.layer.name == "C" and not r.discarded_edits
    ps.grid.select(1, 2)
    ps.pose.set_curve("JawOpen", 0.9)
    r = ps.layers.delete(2)  # アクティブを消す: 編集中の値は捨てられる
    assert r.discarded_edits and ps.ctx.active_layer == 1 and ps.ctx.layer.name == "B"
    assert not ps.pose.dirty
    ps.layers.delete(1)
    assert ps.ctx.active_layer == 0
    ps2 = make_set()
    ps2.layers.add("A")
    ps2.layers.add("B")
    ps2.layers.set_active(1)
    ps2.layers.delete(2)  # 後ろを消す: アクティブはそのまま
    assert ps2.ctx.active_layer == 1


def test_delete_dirty_edit_on_another_layer_is_kept():
    ps = make_set()
    ps.layers.add("A")
    ps.layers.add("B")
    ps.grid.select(1, 2)
    ps.pose.set_curve("JawOpen", 0.9)
    r = ps.layers.delete(2)
    assert not r.discarded_edits and ps.pose.dirty


def test_delete_morph_names_use_computed_names_when_nothing_is_known():
    ps = make_set()  # bake_state も scene も None
    ps.layers.add("Joy")
    ps.layers.copy_from(0, 1)
    r = ps.layers.delete(1)
    assert r.stale_morphs == ["FC_a_Joy_R1_C2", "FC_a_Joy_R1_C3", "FC_a_Joy_R2_C3"]


def test_delete_morph_names_empty_without_asset():
    ps = make_set(doc=make_doc(asset=""), bake_state={})
    ps.layers.add("Joy")
    ps.layers.copy_from(0, 1)
    assert ps.layers.delete(1).stale_morphs == []


def test_copy_from_keeps_destination_points_not_in_source_and_extra():
    ps = make_set()
    ps.layers.add("Joy")
    ps.ctx.doc.layers[1].points[(0, 0)] = m.GridPoint(0, 0, True, m.SourcePose({"Z": 1.0}))
    ps.ctx.doc.layers[0].points[(1, 2)].extra["x"] = 1
    r = ps.layers.copy_from(0, 1)
    assert r.ok and r.count == 3
    assert (0, 0) in ps.ctx.doc.layers[1].points  # 元に無い点は触らない
    assert ps.ctx.doc.layers[1].points[(1, 2)].extra == {"x": 1}
    ps.ctx.doc.layers[1].points[(1, 2)].pose.curves["JawOpen"] = 9
    assert ps.ctx.doc.layers[0].points[(1, 2)].pose.curves["JawOpen"] == 0.4  # 複製
    ps.ctx.doc.layers[0].points.clear()
    assert ps.layers.copy_from(0, 1).code == "empty"


def test_copy_from_rereads_selected_point_when_destination_is_active():
    ps = make_set()
    ps.layers.add("Joy")
    ps.layers.set_active(1)
    ps.grid.select(1, 2)
    assert ps.pose.curves == {}
    ps.layers.copy_from(0, 1)
    assert ps.pose.curves == {"JawOpen": 0.4}


def test_layer_document_event_clamps_active_layer():
    ps = make_set()
    ps.layers.add("Joy")
    ps.layers.set_active(1)
    del ps.ctx.doc.layers[1]  # Undo で消えた
    ps.ctx.notify_document_changed()
    assert ps.ctx.active_layer == 0 and ps.ctx.layer.name == "Neutral"
    assert ps.pose.curves == {}


# --- ValidationPresenter ---


def scene_for(**kw):
    return V.SceneInfo(curves=kw.get("curves"), bones=kw.get("bones", ["head"]))


def test_validation_sorting_is_severity_then_location():
    d = make_doc()
    d.layers.append(m.Layer("Joy"))
    d.layers[0].points[(1, 2)].pose.curves["zzz"] = 5.0  # 可動域の外（警告）
    ps = make_set(doc=d, scene=V.SceneInfo(curves=["Smile_L", "JawOpen"], bones=["x"]), bake_state={})
    ps.validation.run()
    sev = [i.severity for i in ps.validation.issues]
    assert sev == sorted(sev, key=P.SEVERITY_ORDER.index)
    assert sev[0] == "error"
    locs = [(i.layer, i.row, i.col) for i in ps.validation.issues if i.severity == "warning" and i.layer is not None]
    assert locs == sorted(locs)


def test_validation_view_rows_fix_kinds_and_point_links():
    d = make_doc()
    d.layers[0].points[(1, 2)].pose.curves["smile_l"] = 0.1
    d.layers[0].points[(1, 2)].pose.curves["Ghost"] = 0.1
    ps = make_set(doc=d, scene=V.SceneInfo(curves=["Smile_L", "Smile_R", "JawOpen"], bones=["Eye_L", "Eye_R", "head"]))
    ps.validation.run()
    rows = {r.code + ":" + r.name: r for g in ps.validation.view().groups for r in g.issues}
    case = rows["curve_case_mismatch:smile_l"]
    assert (case.kind, case.fix, case.suggestion) == ("curve", "rename", "Smile_L")
    assert case.can_select_point and case.layer_name == "Neutral" and (case.row, case.col) == (1, 2)
    ghost = rows["curve_missing:Ghost"]
    assert ghost.fix == "remove"


def test_proposals_dedupe_count_and_default_to_first_candidate():
    d = make_doc()
    d.layers[0].points[(1, 2)].pose.curves["smile_l"] = 0.1
    d.layers[0].points[(1, 3)].pose.curves["smile_l"] = 0.1
    ps = make_set(doc=d, scene=V.SceneInfo(curves=["Smile_L", "Smile_R", "JawOpen"], bones=["Eye_L", "Eye_R", "head"]))
    ps.validation.run()
    props = ps.validation.suggestions()
    assert [(p.kind, p.old, p.new, p.count) for p in props] == [("curve", "smile_l", "Smile_L", 1)]
    assert props[0].candidates == ("Smile_L",)


def test_unfixable_codes_have_no_fix():
    ps = make_set(scene=V.SceneInfo(curves=["Smile_L"], bones=["x"]))
    ps.ctx.doc.mirror.suffix_l = ""
    ps.validation.run()
    kinds = {i.code: P._fix_of(i) for i in ps.validation.issues}
    assert kinds["mirror_suffix_invalid"] == ("", "none")


def test_apply_renames_both_kinds_at_once_and_reruns():
    d = make_doc()
    d.layers[0].points[(1, 2)].pose.curves["smile_l"] = 0.1
    d.layers[0].points[(1, 2)].pose.bones["eye_l"] = m.BoneOffset(t=(1.0, 0.0, 0.0))
    d.working_set.bones.append("eye_l")
    ps = make_set(doc=d, scene=V.SceneInfo(curves=["Smile_L", "Smile_R", "JawOpen"], bones=["Eye_L", "Eye_R", "head"]))
    ps.validation.run()
    before = len(ps.validation.issues)
    rep = ps.validation.apply_renames()
    assert rep.ok and rep.curve.curve_replacements == 1 and rep.bone.bone_replacements == 1 and rep.bone.working_set_replacements == 1
    assert rep.issues_before == before and rep.issues_after < before
    assert "Smile_L" in d.layers[0].points[(1, 2)].pose.curves and "Eye_L" in d.layers[0].points[(1, 2)].pose.bones


def test_apply_renames_with_choices_argument_and_blank_choice_skips():
    d = make_doc()
    d.layers[0].points[(1, 2)].pose.curves["smile_l"] = 0.1
    ps = make_set(doc=d, scene=V.SceneInfo(curves=["Smile_L", "Smile_R", "JawOpen"], bones=["Eye_L", "Eye_R", "head"]))
    ps.validation.run()
    assert ps.validation.apply_renames({("curve", "smile_l"): ""}).code == "nothing"
    rep = ps.validation.apply_renames({("curve", "smile_l"): "Smile_R"})
    assert rep.ok and "Smile_R" in d.layers[0].points[(1, 2)].pose.curves


def test_choices_survive_rerun_only_for_remaining_proposals():
    d = make_doc()
    d.layers[0].points[(1, 2)].pose.curves["smile_l"] = 0.1
    ps = make_set(doc=d, scene=V.SceneInfo(curves=["Smile_L", "Smile_R", "JawOpen"], bones=["Eye_L", "Eye_R", "head"]))
    ps.validation.run()
    ps.validation.set_choice("curve", "smile_l", "Smile_R")
    ps.validation.run()
    assert ps.validation.choices == {("curve", "smile_l"): "Smile_R"}
    del d.layers[0].points[(1, 2)].pose.curves["smile_l"]
    ps.validation.run()
    assert ps.validation.choices == {}


def test_rename_collision_is_reported_not_applied():
    d = make_doc()
    d.layers[0].points[(1, 2)].pose.curves.update({"smile_l": 0.1, "Smile_L": 0.2})
    ps = make_set(doc=d, scene=V.SceneInfo(curves=["Smile_L", "Smile_R", "JawOpen"], bones=["Eye_L", "Eye_R", "head"]))
    ps.validation.run()
    rep = ps.validation.apply_renames()
    assert rep.total == 0 and rep.curve.collisions == 1 and "見送った" in rep.message
    assert d.layers[0].points[(1, 2)].pose.curves["smile_l"] == 0.1


def test_remove_missing_include_case_and_discards_dirty_edit_on_selected_point():
    d = make_doc()
    d.layers[0].points[(1, 2)].pose.curves["Ghost"] = 0.1
    ps = make_set(doc=d, scene=V.SceneInfo(curves=["JawOpen", "Smile_L", "Smile_R"], bones=["Eye_L", "Eye_R"]))
    ps.grid.select(1, 2)
    ps.pose.set_curve("JawOpen", 0.9)
    rep = ps.validation.remove_missing()
    assert rep.ok and rep.removed == 1 and rep.discarded_edits
    assert "Ghost" not in d.layers[0].points[(1, 2)].pose.curves and not ps.pose.dirty


def test_validation_stale_flag_and_view_before_and_after():
    ps = make_set(scene=V.SceneInfo(curves=["Smile_L"], bones=["x"]))
    v = ps.validation.view()
    assert not v.ran and v.summary == "未実行" and v.can_remove_missing
    ps.validation.run()
    assert ps.validation.stale is False
    ps.layers.add("Joy")
    assert ps.validation.stale is True
    ps.validation.run()
    assert ps.validation.stale is False and ps.validation.view().ran


def test_validation_does_not_modify_document():
    ps = make_set(scene=V.SceneInfo(curves=["Smile_L"], bones=["x"]), bake_state={})
    before = snap(ps.ctx.doc)
    ps.validation.run()
    assert snap(ps.ctx.doc) == before


def test_issue_count_summary_text():
    d = make_doc()
    ps = make_set(doc=d, scene=V.SceneInfo(curves=["Smile_L", "Smile_R", "JawOpen"], bones=["Eye_L", "Eye_R", "head"]))
    ps.validation.run()
    v = ps.validation.view()
    assert v.total == sum(v.counts.values())
    assert v.summary == "問題は見つかりませんでした" or v.summary.startswith(("エラー", "警告", "情報"))


# --- 共通 ---


def test_poses_equal_and_trim_pose():
    a = m.SourcePose({"x": 0.5}, {"b": m.BoneOffset(t=(1.0, 0, 0))})
    assert P.poses_equal(a, copy.deepcopy(a))
    assert not P.poses_equal(a, m.SourcePose({"x": 0.5}))
    assert P.poses_equal(m.SourcePose({"x": 0.0005}), m.SourcePose())
    t = P.trim_pose(m.SourcePose({"x": 0.00001, "y": 1.0}, {"b": m.BoneOffset(), "c": m.BoneOffset(t=(0.0, 0.0, 5.0))}))
    assert t.curves == {"y": 1.0} and list(t.bones) == ["c"]


def test_edit_scope_flags_for_commands_on_other_points():
    ps = make_set()
    ps.grid.select(1, 2)
    ps.pose.set_curve("JawOpen", 0.9)
    r = ps.grid.unkey(1, 3)
    assert r.ok and not r.discarded_edits and ps.pose.dirty
    r = ps.grid.clear_layer()  # 選択中の点のポーズが消える
    assert r.discarded_edits and not ps.pose.dirty and ps.pose.curves == {}


def test_presenter_set_wires_one_context():
    ps = make_set()
    assert ps.pose.ctx is ps.grid.ctx is ps.layers.ctx is ps.validation.ctx is ps.ctx
    assert ps.ctx.pose is ps.pose


def test_module_has_no_maya_or_qt_imports():
    src = (Path(P.__file__)).read_text(encoding="utf-8")
    assert not re.search(r"^\s*(import|from)\s+(maya|PySide|shiboken|PyQt)", src, re.M)
