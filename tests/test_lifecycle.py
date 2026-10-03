"""lifecycle（Maya への登録の寿命・コールバックの保護・エラー通知）の単体テスト。"""

import sys

import pytest

from tdrive_toon import lifecycle


@pytest.fixture(autouse=True)
def _clean():
    lifecycle._reported.clear()
    lifecycle._cleanups.clear()
    yield
    lifecycle._reported.clear()
    lifecycle._cleanups.clear()


def _collect():
    got = []
    lifecycle.add_error_listener(lambda s, d: got.append(s))
    return got


def test_guarded_returns_default_and_reports_once():
    got = _collect()

    @lifecycle.guarded(default=7)
    def boom():
        raise RuntimeError("x")

    assert boom() == 7 and boom() == 7
    assert len(got) == 1  # 描画のたびに出続けない
    lifecycle._error_listeners.clear()


def test_guarded_rejects_int_outside_c_long():
    got = _collect()

    @lifecycle.guarded(default=0)
    def exclusions():
        return 0xFFFFFFFFFFFFFFFF  # kExcludeAll（2026-09-28 の OverflowError）

    assert exclusions() == 0 and got
    lifecycle._error_listeners.clear()


def test_guarded_fallback_uses_args():
    class Op:
        safe = "S"

        @lifecycle.guarded(fallback=lambda self: self.safe)
        def clear(self):
            raise ValueError

    assert Op().clear() == "S"


def test_reload_cleanups_run_all_even_if_one_fails():
    ran = []

    def bad():
        raise RuntimeError("fail")

    lifecycle.on_reload(bad)
    lifecycle.on_reload(lambda: ran.append(1))
    errors = lifecycle.run_reload_cleanups()
    assert ran == [1] and len(errors) == 1 and not lifecycle._cleanups


def test_keep_alive_survives_module_state():
    obj = object()
    lifecycle.keep_alive(obj)
    assert obj in getattr(sys, lifecycle._KEEPALIVE_ATTR)


def test_error_hook_reports_tool_exceptions_and_chains():
    got = _collect()
    chained = []
    saved_hook = sys.excepthook
    saved_attr = getattr(sys, lifecycle._ORIGINAL_HOOK_ATTR, None)
    try:
        if hasattr(sys, lifecycle._ORIGINAL_HOOK_ATTR):
            delattr(sys, lifecycle._ORIGINAL_HOOK_ATTR)
        sys.excepthook = lambda *a: chained.append(a[0])
        lifecycle.install_error_hook()
        lifecycle.install_error_hook()  # 2 回呼んでも重ならない（元のフックへは 1 回だけ渡る）
        try:
            raise KeyError("in test")  # テストファイルの例外 = ツールのものではない → 通知しない
        except KeyError:
            sys.excepthook(*sys.exc_info())
        assert got == [] and chained == [KeyError]
        try:  # ツールのパッケージ内のファイルで起きた例外として作る
            exec(compile("raise ValueError('tool')", str(lifecycle.PACKAGE_DIR / "fake_module.py"), "exec"))
        except ValueError:
            sys.excepthook(*sys.exc_info())
        assert got and chained == [KeyError, ValueError]
    finally:
        sys.excepthook = saved_hook
        if saved_attr is None and hasattr(sys, lifecycle._ORIGINAL_HOOK_ATTR):
            delattr(sys, lifecycle._ORIGINAL_HOOK_ATTR)
        lifecycle._error_listeners.clear()


# ---------------------------------------------------------------- docs/19 M-14 / M-15
def test_cleanups_survive_importlib_reload():
    """M-14: lifecycle 自身が importlib.reload されても、登録済みの後片付けが消えない（Maya に登録したものが外せなくなる）。"""
    import importlib

    ran = []
    lifecycle.on_reload(lambda: ran.append(1))
    mod = importlib.reload(lifecycle)
    try:
        assert mod.run_reload_cleanups() == [] and ran == [1]
    finally:
        mod._cleanups.clear()


def test_repeated_error_still_reaches_script_editor(capsys, monkeypatch):
    """M-15: 同じエラーの 2 回目以降も Script Editor（stderr）には 1 行出る。エラーの表示バー（listener）だけ 1 回。"""
    got = _collect()
    now = [1000.0]
    monkeypatch.setattr(lifecycle, "_now", lambda: now[0])
    lifecycle.report_error("同じエラー", "Traceback...\nValueError: x")
    now[0] += 10.0  # 描画のたびに出続けない程度の間隔を空ける
    lifecycle.report_error("同じエラー", "Traceback...\nValueError: x")
    err = capsys.readouterr().err
    assert len(got) == 1  # 表示のバーは 1 回
    assert err.count("同じエラー") == 2  # Script Editor には 2 回目も出る
    now[0] += 0.001  # すぐ後（毎フレーム）の繰り返しは出さない
    lifecycle.report_error("同じエラー", "Traceback...\nValueError: x")
    assert capsys.readouterr().err.count("同じエラー") == 0
    lifecycle._error_listeners.clear()
