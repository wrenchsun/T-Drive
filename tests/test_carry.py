"""リロードをまたぐ引き継ぎ（tdrive.carry。AN-F-7）: 古い形のセッションを渡しても例外にならない。"""

from tdrive import carry


class Old:
    """export_state の無い古い版のセッション。"""


class Boom:
    def export_state(self):
        raise RuntimeError("壊れている")


class Good:
    def export_state(self):
        return {"data": 1}

    def import_state(self, state):
        return state is not None


def test_old_session_without_export_state_is_not_an_error():
    state, note = carry.export_state(Old())
    assert state is None and "引き継ぎなし" in note


def test_failing_export_is_reported_not_raised():
    state, note = carry.export_state(Boom())
    assert state is None and "壊れている" in note


def test_good_session_round_trips():
    assert carry.export_state(Good()) == ({"data": 1}, "")
    assert carry.import_state(Good(), {"data": 1}) == (True, "")


def test_import_into_old_session_is_reported():
    ok, note = carry.import_state(Old(), {"data": 1})
    assert not ok and note
