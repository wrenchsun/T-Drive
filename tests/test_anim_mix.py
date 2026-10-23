"""今のブレンドの割合の表示（Maya 不要。AN-F-5）。"""

from tdrive_anim.core import sequence as Seq


def _pls():
    clips = {"A_M_Idle": {"name": "A_M_Idle", "start": 0, "end": 30, "loop": True}, "A_M_Run_F": {"name": "A_M_Run_F", "start": 100, "end": 118, "loop": True}}
    return Seq.from_rows([{"clip": "A_M_Idle", "holdSec": 1.0}, {"clip": "A_M_Run_F", "holdSec": 1.0, "crossFadeSec": 1.0}], clips, 30)


def test_mix_text_changes_with_time():
    p = _pls()
    assert Seq.mix_text(p, 0) == "Idle 100%（f=0）"
    assert Seq.mix_text(p, 45) == "Idle 50% / Run_F 50%（f=45）"
    assert Seq.mix_text(p, 75) == "Run_F 100%（f=75）"
    assert Seq.mix_text(p, 39) == "Idle 70% / Run_F 30%（f=39）"
