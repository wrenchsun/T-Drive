"""fps をキーの時刻を保って変えたときの、クリップ・ショットのフレーム番号の比率換算（Maya 不要。AN-F-13）。"""

from tdrive_anim.core import animset as A
from tdrive_anim.core import clips as C
from tdrive_anim.core import shot as SH


def test_clip_range_and_events_scale():
    d = A.new_animset("MisotenChara")
    C.from_range(d, "Idle", 0, 60)
    C.from_range(d, "Run", 100, 118, direction="F")
    C.add_event(d["clips"][0], 30, "Hit")
    n = A.rescale_frames(d, 1.25)
    assert n == 2
    assert (d["clips"][0]["start"], d["clips"][0]["end"]) == (0, 75)
    assert (d["clips"][1]["start"], d["clips"][1]["end"]) == (125, 148)  # 147.5 は切り上げ（キーがこぼれない）
    assert d["clips"][0]["events"][0]["frame"] == 38


def test_shot_scale():
    sh = SH.new_shot("Opening01", 30, 0, 120)
    SH.add_event(sh, 60, "Beat")
    SH.rescale_frames(sh, 1.25)
    assert sh["range"] == [0, 150] and sh["events"][0]["frame"] == 75
