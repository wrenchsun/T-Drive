"""tdrive/services.py（ツール同士の橋渡しの登録表）。Maya 不要。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "maya" / "scripts"))

import pytest  # noqa: E402

from tdrive import services  # noqa: E402

NAME = services.FACIAL_CORRECTION


class Fake:
    def __init__(self, enabled=True):
        self.enabled = enabled

    def state(self):
        return {"available": True, "enabled": self.enabled, "reason": "ok"}

    def set_enabled(self, on):
        self.enabled = on
        return {"ok": True, "message": "done"}


@pytest.fixture(autouse=True)
def clean():
    services._clear()
    yield
    services._clear()


def test_absent_provider_is_unavailable():
    st = services.state(NAME)
    assert st["available"] is False and st["enabled"] is False and st["changeable"] is False and st["reason"]
    res = services.set_enabled(NAME, True)
    assert res["ok"] is False and res["message"]


def test_register_state_and_set():
    p = Fake()
    services.register(NAME, p)
    st = services.state(NAME)
    assert st["available"] and st["enabled"] and st["changeable"] and st["reason"] == "ok"
    assert services.set_enabled(NAME, False) == {"ok": True, "message": "done"}
    assert p.enabled is False


def test_unregister_and_replace():
    a, b = Fake(True), Fake(False)
    services.register(NAME, a)
    services.register(NAME, b)  # 置き換え
    assert services.state(NAME)["enabled"] is False
    services.unregister(NAME, a)  # 古いものを外そうとしても、今のもの（b）は残る
    assert services.has_provider(NAME)
    services.unregister(NAME, b)
    assert not services.has_provider(NAME) and services.state(NAME)["available"] is False


def test_provider_exceptions_are_reported_as_unavailable():
    class Bad:
        def state(self):
            raise RuntimeError("boom")

        def set_enabled(self, on):
            raise RuntimeError("nope")

    services.register(NAME, Bad())
    st = services.state(NAME)
    assert st["available"] is False and "boom" in st["reason"]
    res = services.set_enabled(NAME, True)
    assert res["ok"] is False and "nope" in res["message"]


def test_subscribe_notify_and_isolation():
    calls = []

    def good():
        calls.append("good")

    def bad():
        raise ValueError("x")

    def gone():
        raise RuntimeError("deleted")  # Qt の枠が破棄済み: 購読が外れる

    for fn in (bad, gone, good):
        services.subscribe(NAME, fn)
    services.subscribe(NAME, good)  # 二重登録しない
    services.notify(NAME)
    services.notify(NAME)
    assert calls == ["good", "good"]
    assert gone not in services._subscribers[NAME]
    services.unsubscribe(NAME, good)
    services.notify(NAME)
    assert calls == ["good", "good"]
