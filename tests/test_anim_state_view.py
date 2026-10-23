"""状態図の記録の表示（Maya 不要。AN-F-27）: 遷移列の一行表示・記録の表（最大 50 行）・同じ時刻は上書き。"""

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
    rows = M.timeline_rows(tl, r, {"Speed": 0.0, "ActionState": 0.0})
    assert rows[0][0] == "1.00s" and "Speed=0.5" in rows[0][1] and rows[0][2] == "Run"
    assert rows[1][1] == "ActionState=2" and rows[1][2] == "Jump"


def test_same_time_overwrites_and_rows_are_capped():
    tl = []
    for v in range(10):  # ドラッグ中の連続記録（同じ時刻）は 1 件にまとまる
        tl = M.with_event(tl, 1.0, {"Speed": v / 10})
    assert len(tl) == 1 and tl[0][1]["Speed"] == 0.9
    many = [(i * 0.1, {"Speed": (i % 2) * 0.5}) for i in range(120)]
    r = M.evaluate(_graph(), many, 12.0, 30)
    rows = M.timeline_rows(many, r)
    assert len(rows) == M.MAX_TIMELINE_ROWS and rows[0][0] == "…" and "古い記録" in rows[0][1]
