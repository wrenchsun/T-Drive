"""UI コードの既知の落とし穴を静的に検出する（Maya 不要）。"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "maya/scripts"
# 検査の対象: Toon・殻（tdrive）・FacialController（tdrive_facial。Maya 非依存の core は別のテストが見る）
UI_FILES = sorted(
    [*(SCRIPTS / "tdrive_toon").glob("ui*.py"), *(SCRIPTS / "tdrive").glob("*.py"), *(SCRIPTS / "tdrive_facial").glob("*.py")]
)


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


def test_line_radius_is_float():
    """シェーダーの float パラメータへ渡す値は float（int だと kInvalidParameter）。screen_line は maya を import するので式だけ確認する。"""
    src = (ROOT / "maya/scripts/tdrive_toon/screen_line.py").read_text(encoding="utf-8")
    assert "return float(max(1.0, round(" in src
    assert "_EXCLUDE_ALL = 0x7FFFFFFF" in src  # Windows の C long（32 ビット符号付き）に収める


# ---------------------------------------------------------------- Maya に登録するもの・Maya から呼ばれるもの（lifecycle.py）
import ast  # noqa: E402

TOOL_FILES = sorted(
    f
    for pkg in ("tdrive_toon", "tdrive", "tdrive_facial")
    for f in (SCRIPTS / pkg).glob("*.py")
    if not (f.parent.name == "tdrive_toon" and f.read_text(encoding="utf-8").lstrip().startswith('"""転送モジュール'))
)  # 転送モジュールは本体（tdrive/）を検査する
# Maya に Python のオブジェクト・関数を渡して保持させる API（リロードで解放されると落ちる / 古いコードが動き続ける）
MAYA_REGISTRATION = re.compile(
    r"registerOverride\(|\.addCallback\(|registerCommand\(|registerNode\(|MUiMessage|MEventMessage|MSceneMessage|MDGMessage|MNodeMessage"
    r"|scriptJob\([^)]*=\s*[A-Za-z_][\w.]*\s*[,\)]"  # scriptJob に文字列でなく関数を渡している
)


def test_maya_registrations_have_reload_cleanup():
    bad = []
    for f in TOOL_FILES:
        src = f.read_text(encoding="utf-8")
        if f.name != "lifecycle.py" and MAYA_REGISTRATION.search(src) and "lifecycle.on_reload(" not in src:
            bad.append(f.name)
    assert not bad, f"Maya に登録しているのに lifecycle.on_reload で後片付けを登録していない: {bad}"


def test_forwarding_modules_share_the_moved_module():
    """tdrive_toon の lifecycle / project / updater / ui_update / mcp_bridge は tdrive へ移った。状態を 1 つにするため、転送モジュールは
    コピーでなく同じモジュールオブジェクトを返す（sys.modules を差し替える）。"""
    for name in ("lifecycle", "project", "updater", "ui_update", "mcp_bridge"):
        src = (SCRIPTS / "tdrive_toon" / f"{name}.py").read_text(encoding="utf-8")
        assert f"from tdrive import {name} as _module" in src and "sys.modules[__name__] = _module" in src, name
        assert (SCRIPTS / "tdrive" / f"{name}.py").exists(), name


def test_reload_handles_all_packages():
    """リロードは tdrive_toon だけでなく tdrive（殻）・tdrive_facial のモジュールも捨てて読み直す。"""
    src = (ROOT / "maya/mcp_scripts/reload_tdrive.py").read_text(encoding="utf-8")
    assert '"tdrive_toon", "tdrive", "tdrive_facial"' in src


def test_reload_runs_cleanups_before_dropping_modules():
    src = (ROOT / "maya/mcp_scripts/reload_tdrive.py").read_text(encoding="utf-8")
    assert src.index("run_reload_cleanups()") < src.index("del sys.modules[name]")


def _maya_callback_classes(tree: ast.AST):
    """Maya の API クラス（omr.* / om.MPx*）を継承したクラス。"""
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            bases = [ast.unparse(b) for b in node.bases]
            if any(b.startswith(("omr.", "om.MPx", "OpenMayaRender.", "OpenMayaMPx.")) for b in bases):
                yield node


def test_maya_callbacks_are_guarded():
    """Maya から呼ばれるメソッドは lifecycle.guarded で包む（例外が描画のたびに出続けない・C long に収まらない int を返さない）。"""
    bad = []
    for f in TOOL_FILES:
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for cls in _maya_callback_classes(tree):
            for fn in cls.body:
                if isinstance(fn, ast.FunctionDef) and fn.name != "__init__" and fn.name[0] != "_" and fn.name != "release":
                    if not any("lifecycle.guarded" in ast.unparse(d) for d in fn.decorator_list):
                        bad.append(f"{f.name}:{cls.name}.{fn.name}")
    assert not bad, bad


def test_set_parameter_goes_through_typed_helper():
    """シェーダーパラメータは set_param（型を確かめる）経由で設定する（int を float に渡して kInvalidParameter の再発防止）。"""
    bad = []
    for f in TOOL_FILES:
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if isinstance(fn, ast.FunctionDef) and fn.name != "set_param":
                for call in ast.walk(fn):
                    if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and call.func.attr == "setParameter":
                        bad.append(f"{f.name}:{fn.name}:{call.lineno}")
    assert not bad, bad


def test_no_delete_of_possibly_empty_list():
    """cmds.delete に内包表記を直接渡さない（空だと「何も選択されていません」の警告。先に空かどうか確かめる）。"""
    bad = [f"{f.name}:{i}" for f in TOOL_FILES for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1)
           if re.search(r"cmds\.delete\(\s*\[", line)]
    assert not bad, bad


def test_distribution_bat_is_ascii_crlf():
    """配布用バッチは ASCII のみ・CRLF（cmd は UTF-8 のマルチバイト行を読み違えて行が壊れる。2026-09-28 に確認）。"""
    raw = (ROOT / "tools/distribution/Install-TDriveToon.bat").read_bytes()
    assert all(b < 128 for b in raw), "日本語などは install.ps1 側に書く"
    assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b"")


def test_install_ps1_has_bom():
    """Windows PowerShell 5.1 は BOM の無い UTF-8 を ANSI（cp932）として読み、日本語が化けて構文エラーになる。"""
    assert (ROOT / "tools/install.ps1").read_bytes().startswith(b"\xef\xbb\xbf")
