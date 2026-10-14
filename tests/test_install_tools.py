"""tools/install_tools.ps1（install.ps1 の -Tools）。bare リポジトリを origin にして、PowerShell の関数を直接呼んで確かめる。"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tdrive import external

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures" / "external_tool"
SCRIPT = ROOT / "tools" / "install_tools.ps1"
PS = shutil.which("powershell.exe") or shutil.which("powershell")

pytestmark = pytest.mark.skipif(PS is None or shutil.which("git") is None, reason="powershell.exe / git が無い")


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


def _make_remote(tmp_path, name="remote.git", with_manifest=True, tags=("0.1.0", "0.2.0")):
    work, bare = tmp_path / f"work_{name}", tmp_path / name
    shutil.copytree(FIX, work)
    if not with_manifest:
        (work / "tool.json").unlink()
    _git(work, "init", "-q", "-b", "main")
    _git(work, "config", "user.email", "t@example.com")
    _git(work, "config", "user.name", "t")
    for ver in tags:
        (work / "VERSION").write_text(ver + "\n", encoding="utf-8")
        _git(work, "add", "-A")
        _git(work, "commit", "-q", "-m", ver)
        _git(work, "tag", "-a", f"v{ver}", "-m", ver)
    subprocess.run(["git", "clone", "-q", "--bare", str(work), str(bare)], check=True)
    return bare.as_posix()


def _run(spec, base):
    cmd = f"[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; . '{SCRIPT}'; Add-ExternalTools '{spec}' '{base}'"
    return subprocess.run(
        [PS, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", cmd],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )


@pytest.fixture
def base(tmp_path):
    return tmp_path / "TDriveToon"


def test_script_has_bom():
    assert SCRIPT.read_bytes().startswith(b"\xef\xbb\xbf")


def test_adds_latest_tag_and_loads(base, tmp_path):
    url = _make_remote(tmp_path)
    res = _run(url, base)
    assert res.returncode == 0, res.stderr
    entries, warnings = external.load_tools(external.tools_json_path(base))
    assert warnings == []
    assert [(e.id, e.dir, e.tag, e.url, e.enabled) for e in entries] == [("tdrive_dummy", "tdrive_dummy", "v0.2.0", url, True)]
    assert (base / "tools" / "tdrive_dummy" / "tool.json").is_file()
    assert not list((base / "tools").glob("_adding_*"))
    raw = external.tools_json_path(base).read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf") and json.loads(raw)["version"] == 1
    loadable, skipped, warns = external.resolve_tools(external.tools_json_path(base), "0.5.0")
    assert [t.entry.id for t in loadable] == ["tdrive_dummy"] and not skipped and not warns


def test_tag_suffix(base, tmp_path):
    url = _make_remote(tmp_path)
    assert _run(f"{url}#v0.1.0", base).returncode == 0
    entries, _ = external.load_tools(external.tools_json_path(base))
    assert entries[0].tag == "v0.1.0"
    assert (base / "tools" / "tdrive_dummy" / "VERSION").read_text().strip() == "0.1.0"


def test_existing_is_left_alone(base, tmp_path):
    url = _make_remote(tmp_path)
    assert _run(f"{url}#v0.1.0", base).returncode == 0
    marker = base / "tools" / "tdrive_dummy" / "marker.txt"
    marker.write_text("x")
    before = external.tools_json_path(base).read_bytes()
    res = _run(url, base)
    assert res.returncode == 0 and "既にあります" in res.stdout
    assert marker.exists() and external.tools_json_path(base).read_bytes() == before
    assert not list((base / "tools").glob("_adding_*"))


def test_folder_without_entry_is_not_overwritten(base, tmp_path):
    url = _make_remote(tmp_path)
    (base / "tools" / "tdrive_dummy").mkdir(parents=True)
    res = _run(url, base)
    assert "既にあります" in res.stdout
    assert not external.tools_json_path(base).exists()


def test_keeps_other_entries(base, tmp_path):
    other = {"version": 1, "tools": [{"id": "other_tool", "url": "u", "dir": "other_tool", "tag": "v1.0.0", "enabled": False}]}
    base.mkdir(parents=True)
    external.tools_json_path(base).write_text(json.dumps(other), encoding="utf-8")
    assert _run(_make_remote(tmp_path), base).returncode == 0
    entries, warnings = external.load_tools(external.tools_json_path(base))
    assert warnings == [] and [e.id for e in entries] == ["other_tool", "tdrive_dummy"]
    assert entries[0].enabled is False


def test_no_manifest_is_not_added(base, tmp_path):
    url = _make_remote(tmp_path, with_manifest=False)
    res = _run(url, base)
    assert res.returncode == 0 and "tool.json" in res.stdout
    assert not external.tools_json_path(base).exists()
    assert not [p for p in (base / "tools").iterdir()]


def test_bad_url_continues_with_next(base, tmp_path):
    url = _make_remote(tmp_path)
    res = _run(f"{(tmp_path / 'nothing.git').as_posix()},{url}", base)
    assert res.returncode == 0
    entries, _ = external.load_tools(external.tools_json_path(base))
    assert [e.id for e in entries] == ["tdrive_dummy"]
