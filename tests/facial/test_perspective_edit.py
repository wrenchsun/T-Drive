"""パース補正のキーを「編集の対象」にする（点と同じ編集バッファ・保存・確認）の core。"""

from tdrive_facial.core import model as m
from tdrive_facial.core import presenters as P
from tdrive_facial.core import validate as V


def make_set() -> P.PresenterSet:
    doc = m.Document()
    doc.asset = "a"
    doc.grid.rows = 3
    doc.grid.cols = 3
    doc.layers.append(m.Layer(name="Joy", emotion_curve="Joy"))
    doc.perspective = m.Perspective(
        enabled=True,
        keys=[
            m.PerspectiveKey(30.0, {"bs.smile_L": 0.5}),
            m.PerspectiveKey(50.0, {"bs.jaw_open": 0.4}),
            m.PerspectiveKey(80.0),
        ],
    )
    return P.PresenterSet(doc)


def test_select_key_clears_point_and_loads_key_pose():
    ps = make_set()
    ps.grid.select(1, 1)
    r = ps.perspective.select_key(1)
    assert r.status == P.SELECT_SELECTED and r.kind == "key" and r.index == 1
    assert ps.ctx.selection is None and ps.ctx.selected_key() == 1
    assert ps.pose.curves == {"bs.jaw_open": 0.4} and not ps.pose.dirty and ps.pose.has_point
    view = ps.pose.view()
    assert view.key_index == 1 and view.selection is None and view.can_save
    assert ps.perspective.view().selected == 1
    ps.grid.select(0, 0)  # 点を選ぶとキーの対象は外れる
    assert ps.ctx.key_target is None and ps.ctx.selection == (0, 0)


def test_select_key_out_of_range_and_same():
    ps = make_set()
    assert ps.perspective.select_key(9).status == P.SELECT_INVALID
    ps.perspective.select_key(0)
    ps.pose.set_curve("bs.x", 0.3)
    r = ps.perspective.select_key(0)  # 同じキー: 確認なし・編集中の値を保つ
    assert r.status == P.SELECT_SAME and ps.pose.curves["bs.x"] == 0.3


def test_unsaved_edit_asks_before_switching_and_save_writes_to_key():
    ps = make_set()
    ps.perspective.select_key(0)
    ps.pose.set_curve("bs.brow", 0.7)
    r = ps.perspective.select_key(1)
    assert r.status == P.SELECT_NEEDS_CONFIRM and ps.ctx.selected_key() == 0 and ps.pose.curves["bs.brow"] == 0.7
    assert ps.perspective.confirm_select_key(P.CONFIRM_CANCEL).status == P.SELECT_CANCELLED and ps.ctx.selected_key() == 0
    ps.perspective.select_key(1)
    r = ps.perspective.confirm_select_key(P.CONFIRM_SAVE)
    assert r.status == P.SELECT_SELECTED and r.saved and ps.ctx.selected_key() == 1
    assert ps.ctx.doc.perspective.keys[0].curves == {"bs.smile_L": 0.5, "bs.brow": 0.7}
    # 破棄
    ps.pose.set_curve("bs.brow", 0.9)
    ps.perspective.select_key(2)
    r = ps.perspective.confirm_select_key(P.CONFIRM_DISCARD)
    assert r.status == P.SELECT_SELECTED and "bs.brow" not in ps.ctx.doc.perspective.keys[1].curves


def test_point_selection_asks_when_key_edit_is_unsaved_and_saves_to_key():
    ps = make_set()
    ps.perspective.select_key(0)
    ps.pose.set_curve("bs.brow", 0.7)
    r = ps.grid.select(1, 1)
    assert r.status == P.SELECT_NEEDS_CONFIRM
    r = ps.grid.confirm_select(P.CONFIRM_SAVE)
    assert r.status == P.SELECT_SELECTED and r.saved
    assert ps.ctx.doc.perspective.keys[0].curves["bs.brow"] == 0.7 and not ps.ctx.doc.layers[0].points
    assert ps.ctx.selection == (1, 1) and ps.ctx.key_target is None


def test_save_empty_pose_keeps_key_as_empty_key():
    ps = make_set()
    ps.perspective.select_key(0)
    ps.pose.zero()
    res = ps.pose.save()
    assert res.ok
    k = ps.ctx.doc.perspective.keys[0]
    assert k.is_empty() and len(ps.ctx.doc.perspective.keys) == 3


def test_layer_switch_does_not_touch_key_edit():
    ps = make_set()
    ps.perspective.select_key(0)
    ps.pose.set_curve("bs.brow", 0.7)
    r = ps.layers.set_active(1)  # キーの編集はレイヤーに関係しない: 確認なし
    assert r.status == P.SELECT_SELECTED and ps.ctx.active_layer == 1
    assert ps.ctx.selected_key() == 0 and ps.pose.curves["bs.brow"] == 0.7


def test_remove_key_keeps_target_aligned():
    ps = make_set()
    ps.perspective.select_key(2)
    ps.pose.set_curve("bs.brow", 0.7)
    ps.perspective.remove_key(0)  # 後ろのキーの番号が詰まる: 同じキーのまま（2 → 1）・編集中の値は残る
    assert ps.ctx.selected_key() == 1 and ps.ctx.doc.perspective.keys[1].value == 80.0
    assert ps.pose.curves.get("bs.brow") == 0.7
    ps.perspective.remove_key(1)  # 選んでいたキーを消すと対象なし
    assert ps.ctx.selected_key() is None and ps.ctx.key_target is None and not ps.pose.has_point


def test_external_change_of_key_pose_reloads_buffer():
    ps = make_set()
    ps.perspective.select_key(0)
    ps.perspective.set_key_pose(0, m.SourcePose({"bs.z": 0.2}, {}))
    assert ps.pose.curves == {"bs.z": 0.2}


def test_issue_rows_expose_key_for_navigation():
    ps = make_set()
    ps.ctx.scene = V.SceneInfo(curves={"bs.smile_L", "bs.jaw_open"}, bones=set(), targets=set())
    ps.ctx.bake_state = {}
    ps.validation.run()
    rows = [r for g in ps.validation.view().groups for r in g.issues if r.code == "perspective_key_unbaked"]
    assert rows and all(r.key is not None and r.can_select_key and not r.can_select_point for r in rows)
    assert {r.key for r in rows} == {0, 1}
