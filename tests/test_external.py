"""外部ツールパッケージ（docs/13 §6）: tools.json の読み書き・tool.json の検証・requires の判定。"""

import json

from tdrive import external as ex


def write_tool(root, name, **over):
    d = root / name
    d.mkdir(parents=True)
    manifest = {"id": name, "name": f"{name} tool", "entry": f"{name}.tool"}
    manifest.update(over)
    (d / "tool.json").write_text(json.dumps(manifest), encoding="utf-8")
    return d


def write_tools_json(base, tools):
    p = base / "tools.json"
    p.write_text(json.dumps({"version": 1, "tools": tools}), encoding="utf-8")
    return p


def test_default_path_uses_localappdata(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert ex.tools_json_path() == tmp_path / "TDriveToon" / "tools.json"


def test_roundtrip_and_format(tmp_path):
    p = tmp_path / "tools.json"
    ex.upsert_tool(ex.ToolEntry(id="a", url="u", tag="v1.0.0"), p)
    ex.upsert_tool(ex.ToolEntry(id="b", url="u2", previous="v0.9.0", enabled=False), p)
    ex.upsert_tool(ex.ToolEntry(id="a", url="u", tag="v1.1.0", previous="v1.0.0"), p)  # 更新
    text = p.read_text(encoding="utf-8")
    assert text.endswith("\n") and '\n  "version": 1' in text
    entries, warns = ex.load_tools(p)
    assert warns == []
    assert [(e.id, e.tag, e.dir, e.enabled) for e in entries] == [("a", "v1.1.0", "a", True), ("b", "", "b", False)]
    assert entries[0].previous == "v1.0.0"
    assert ex.remove_tool("a", p) and not ex.remove_tool("a", p)
    assert [e.id for e in ex.load_tools(p)[0]] == ["b"]


def test_missing_file_is_empty(tmp_path):
    assert ex.load_tools(tmp_path / "none.json") == ([], [])


def test_broken_json_and_version(tmp_path):
    p = tmp_path / "tools.json"
    p.write_text("{oops", encoding="utf-8")
    entries, warns = ex.load_tools(p)
    assert entries == [] and len(warns) == 1
    p.write_text(json.dumps({"version": 2, "tools": [{"id": "a"}]}), encoding="utf-8")
    entries, warns = ex.load_tools(p)
    assert entries == [] and "version" in warns[0]


def test_broken_entry_dropped_others_live(tmp_path):
    p = write_tools_json(
        tmp_path,
        [{"id": "ok"}, {"url": "x"}, "str", {"id": "Bad-ID"}, {"id": "b", "enabled": "yes"}, {"id": "ok2", "dir": "d"}],
    )
    entries, warns = ex.load_tools(p)
    assert [e.id for e in entries] == ["ok", "ok2"]
    assert entries[1].dir == "d"
    assert len(warns) == 4


def test_manifest_validation():
    ok, why = ex.parse_manifest(
        {"id": "ms_grey", "name": "N", "entry": "ms_grey.tool", "requires": {"tdrive": "0.6.0"}, "compatibleWith": {"ms2026": "1.3.0"}}
    )
    assert why == "" and ok.scripts == "maya/scripts" and ok.requires_tdrive == "0.6.0"
    assert ok.compatible_with == {"ms2026": "1.3.0"}
    for bad in (
        {"name": "N", "entry": "a.b"},
        {"id": "1a", "name": "N", "entry": "a.b"},
        {"id": "a", "entry": "a.b"},
        {"id": "a", "name": "N", "entry": "a-b"},
        {"id": "a", "name": "N", "entry": "a.b", "requires": {"tdrive": "x"}},
        [],
    ):
        m, why = ex.parse_manifest(bad)
        assert m is None and why


def test_check_requires():
    m = ex.ToolManifest(id="a", name="A", entry="a.t", requires_tdrive="0.6.0")
    assert ex.check_requires(m, "0.6.0") == "" and ex.check_requires(m, "1.0.0") == ""
    assert "0.6.0" in ex.check_requires(m, "0.5.9")
    assert ex.check_requires(ex.ToolManifest(id="a", name="A", entry="a.t"), "0.0.1") == ""


def test_resolve_normal(tmp_path):
    write_tool(tmp_path / "tools", "alpha", requires={"tdrive": "0.5.0"})
    p = write_tools_json(tmp_path, [{"id": "alpha", "url": "u", "tag": "v1.0.0"}])
    loadable, skipped, warns = ex.resolve_tools(p, "0.5.0")
    assert warns == [] and skipped == []
    t = loadable[0]
    assert t.manifest.entry == "alpha.tool" and not t.dev
    assert t.scripts_path == tmp_path / "tools" / "alpha" / "maya" / "scripts"


def test_resolve_broken_manifest_others_live(tmp_path):
    write_tool(tmp_path / "tools", "good")
    bad = tmp_path / "tools" / "bad"
    bad.mkdir(parents=True)
    (bad / "tool.json").write_text("{x", encoding="utf-8")
    (tmp_path / "tools" / "nojson").mkdir()
    write_tool(tmp_path / "tools", "other", id="mismatch")
    p = write_tools_json(tmp_path, [{"id": "bad"}, {"id": "good"}, {"id": "nojson"}, {"id": "gone"}, {"id": "other"}])
    loadable, skipped, _ = ex.resolve_tools(p, "1.0.0")
    assert [t.entry.id for t in loadable] == ["good"]
    assert {s.id for s in skipped} == {"bad", "nojson", "gone", "other"} and all(s.reason for s in skipped)


def test_resolve_requires_too_new(tmp_path):
    write_tool(tmp_path / "tools", "alpha", requires={"tdrive": "9.0.0"})
    p = write_tools_json(tmp_path, [{"id": "alpha"}])
    loadable, skipped, _ = ex.resolve_tools(p, "0.5.0")
    assert loadable == [] and "9.0.0" in skipped[0].reason


def test_resolve_disabled(tmp_path):
    write_tool(tmp_path / "tools", "alpha")
    p = write_tools_json(tmp_path, [{"id": "alpha", "enabled": False}])
    loadable, skipped, _ = ex.resolve_tools(p, "1.0.0")
    assert loadable == [] and skipped == [ex.SkippedTool("alpha", "無効")]


def test_resolve_dev_absolute_dir(tmp_path):
    clone = write_tool(tmp_path / "mine", "devtool", scripts="src")
    p = write_tools_json(tmp_path, [{"id": "devtool", "dir": str(clone)}])
    assert ex.is_dev_dir(str(clone)) and not ex.is_dev_dir("devtool")
    loadable, skipped, _ = ex.resolve_tools(p, "1.0.0")
    assert skipped == [] and loadable[0].dev
    assert loadable[0].checkout == clone and loadable[0].scripts_path == clone / "src"
