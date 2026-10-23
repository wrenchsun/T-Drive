"""状態図の記録の表示と編集（Maya 不要。AN-F-27 / AN-F-29）: 遷移列の一行表示・マーカーの追加 / 移動 / 削除・状態の帯・記録の表（最大 50 行）。"""

from tdrive_anim.core import animset as A
from tdrive_anim.core import clips as C
from tdrive_anim.core import state_machine as M


def _graph():
    d = A.new_animset("MisotenChara")
    for n, a, b, dr in (("Idle", 0, 30, ""), ("Run", 100, 118, "F"), ("Jump", 200, 230, "")):
        C.from_range(d, n, a, b, direction=dr)
    return M.default_graph(d["clips"], "MisotenChara")


def test_describe_and_rows():
    g = _graph()
    tl = [(1.0, {"Speed": 0.5}), (2.0, {"ActionState": 2})]
    r = M.evaluate(g, tl, 3.0, 30)
    text = M.describe_switches(r)
    assert text.startswith("0.00s Idle → ") and "Run（0.08s）" in text and "Jump（0.08s）" in text, text
    rows = M.marker_rows(tl, r)
    assert rows[0] == ("1.00s", "Speed", "0.5", "Run") and rows[1] == ("2.00s", "ActionState", "2", "Jump")


def test_same_time_overwrites_and_rows_are_capped():
    tl = []
    for v in range(10):  # ドラッグ中の連続記録（同じ時刻）は 1 件にまとまる
        tl = M.with_event(tl, 1.0, {"Speed": v / 10})
    assert len(tl) == 1 and tl[0][1]["Speed"] == 0.9
    many = [(i * 0.1, {"Speed": (i % 2) * 0.5}) for i in range(120)]
    r = M.evaluate(_graph(), many, 12.0, 30)
    rows = M.marker_rows(many, r)
    assert len(rows) == M.MAX_TIMELINE_ROWS and rows[0][0] == "…" and "古い記録" in rows[0][1]


def test_marker_editing():
    tl = [(1.0, {"Speed": 0.5, "ActionState": 0}), (2.0, {"ActionState": 2})]
    assert M.markers(tl) == [(1.0, "ActionState", 0.0), (1.0, "Speed", 0.5), (2.0, "ActionState", 2.0)]
    moved = M.move_marker(tl, "ActionState", 2.0, 1.5)  # 動かす（元の時刻は空になるので消える）
    assert [(t, p) for t, p, _ in M.markers(moved)] == [(1.0, "ActionState"), (1.0, "Speed"), (1.5, "ActionState")]
    assert M.move_marker(tl, "Speed", 1.0, 2.0)[-1][1] == {"ActionState": 2, "Speed": 0.5}  # 行き先の時刻に合流する
    over = M.move_marker(tl, "ActionState", 2.0, 1.0)  # 行き先に同じパラメータがあれば上書き
    assert dict(over)[1.0]["ActionState"] == 2 and len(over) == 1
    assert M.delete_marker(tl, "Speed", 1.0)[0] == (1.0, {"ActionState": 0})
    assert len(M.delete_marker(tl, "ActionState", 2.0)) == 1  # 空になったイベントは消える
    assert M.move_marker(tl, "Speed", 5.0, 6.0) == tl  # 無いものは動かない


def test_state_bands_match_evaluation():
    g = _graph()
    tl = [(1.0, {"Speed": 0.5}), (2.0, {"ActionState": 2})]
    r = M.evaluate(g, tl, 3.0, 30)
    bands = M.state_bands(r, 3.0)
    assert bands[0][:2] == (0.0, r.switches[0]["t"]) and bands[0][2] == "Idle"
    assert [b[2] for b in bands] == ["Idle", "Run", "Jump"] and bands[-1][1] == 3.0
    assert all(abs(bands[i][1] - bands[i + 1][0]) < 1e-9 for i in range(len(bands) - 1))
