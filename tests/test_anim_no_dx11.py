"""Animation タブは DirectX 11 に依存しない（docs/20 §1）の静的検査。docs/21 TS-7:
以前は 3 つの文字列の部分一致だけで、`from tdrive_toon import preview` などを見逃した。ast で import と呼び出しを拾う。
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ANIM = sorted((ROOT / "maya/scripts/tdrive_anim").rglob("*.py"))
# tdrive_anim が使ってよい tdrive_toon のモジュール（Look の読み書きと、モデル出力の相乗り。どれも dx11Shader・Render Override を読み込まない呼び方）
ALLOWED_TOON = {"look", "session", "export"}
FORBIDDEN_CALLS = {"registerOverride", "setParameter", "shadingNode_dx11"}


def _imports(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            yield node.module, [a.name for a in node.names]
        elif isinstance(node, ast.Import):
            for a in node.names:
                yield a.name, []


def test_tdrive_anim_only_uses_allowed_toon_modules():
    bad = []
    for f in ANIM:
        for module, names in _imports(ast.parse(f.read_text(encoding="utf-8"))):
            parts = module.split(".")
            if parts[0] != "tdrive_toon":
                continue
            used = {parts[1]} if len(parts) > 1 else set(names)
            if not used <= ALLOWED_TOON:
                bad.append(f"{f.name}: {module} {names}")
    assert not bad, bad


def test_tdrive_anim_never_loads_dx11shader_or_registers_overrides():
    bad = []
    for f in ANIM:
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id if isinstance(node.func, ast.Name) else ""
            consts = [a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
            if name == "loadPlugin" and any("dx11" in c.lower() for c in consts):
                bad.append(f"{f.name}:{node.lineno} loadPlugin dx11Shader")
            if name in FORBIDDEN_CALLS:
                bad.append(f"{f.name}:{node.lineno} {name}")
            if any(c in ("dx11Shader", "dx11Shader.mll") for c in consts) and name in ("shadingNode", "createNode", "pluginInfo", "unloadPlugin"):
                bad.append(f"{f.name}:{node.lineno} dx11Shader を扱う {name}")
    assert not bad, bad


def test_only_the_background_process_loads_dx11shader_and_tolerates_failure():
    """dx11Shader を読むのは裏のプロセス（tools/export_fbx_batch.py）だけで、読めなくても出力を続ける。"""
    src = (ROOT / "tools/export_fbx_batch.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    loads = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "loadPlugin"]
    assert loads, "裏のプロセスが dx11Shader を読む（Toon のモデル出力のため）"
    # mode == "anim" のブロックは try/except RuntimeError で包んでいる
    anim_branch = src[src.index('elif mode == "anim"'): src.index("else:", src.index('elif mode == "anim"'))]
    assert "try:" in anim_branch and "except RuntimeError" in anim_branch
