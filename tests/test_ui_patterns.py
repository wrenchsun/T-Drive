"""UI コードの既知の落とし穴を静的に検出する（Maya 不要）。"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UI_FILES = sorted((ROOT / "maya/scripts/tdrive_toon").glob("ui*.py"))


def test_clicked_lambda_without_required_arg_with_defaults():
    """PySide6 は clicked / toggled / triggered に「必須引数 + 既定値付き引数」の lambda をつなぐと引数なしで呼び、
    TypeError で何も起きない（2026-09-28 機能タブのチェックが反映されなかった）。
    既定値付きにするなら `lambda *_, x=...:` か `lambda _c=False, x=...:` の形にする。
    """
    pattern = re.compile(r"\.(clicked|toggled|triggered)\.connect\(lambda\s+[A-Za-z]\w*\s*,[^:]*=")
    bad = [f"{f.name}:{i}" for f in UI_FILES for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1) if pattern.search(line)]
    assert not bad, bad


def test_reload_unregisters_render_override_before_dropping_modules():
    """Maya に登録した Python オブジェクトは、モジュールを捨てる前に登録を外す（外さないと解放済みメモリで Maya が落ちる）。"""
    src = (ROOT / "maya/mcp_scripts/reload_tdrive.py").read_text(encoding="utf-8")
    assert "screen_line" in src and ".unregister()" in src
    assert src.index(".unregister()") < src.index("del sys.modules[name]")
