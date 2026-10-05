"""サムネイルの playblast 書き出し（tdrive_facial/thumbnails.py）。Maya なし（cmds を偽物に差し替える）。

2026-10-05 の不具合: Maya 2026 の playblast は completeFilename を指定の名前のまま書く（拡張子を足さない）。
拡張子なしで渡したため `<名前>*.png` の探索が空振りし、「この環境ではサムネイルを作れません」と誤って報告した。
"""

import sys
import types
from pathlib import Path

_added = []
try:
    import maya.cmds  # noqa: F401
except ImportError:
    fake = types.ModuleType("maya")
    fake.cmds = types.ModuleType("maya.cmds")
    sys.modules["maya"], sys.modules["maya.cmds"] = fake, fake.cmds
    _added = ["maya", "maya.cmds"]
try:
    from tdrive_facial import thumbnails as T
finally:
    for _n in _added:
        sys.modules.pop(_n, None)

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32


class FakeCmds:
    """mode: exact（指定の名前で書く＝Maya 2026）/ frame（<base>.0001.png）/ noext（<base>）/ none（何も書かない）/ ret_other（別の場所へ書いて戻り値で返す）"""

    def __init__(self, mode, tmp=None):
        self.mode, self.tmp, self.calls = mode, tmp, []

    def modelPanel(self, panel, query=False, edit=False, camera=None):
        return "persp" if query else None

    def currentTime(self, query=False):
        return 1.0

    def playblast(self, **kw):
        self.calls.append(kw)
        name = kw["completeFilename"]
        if self.mode == "exact":
            out = name
        elif self.mode == "frame":
            out = Path(name).with_suffix("").as_posix() + ".0001.png"
        elif self.mode == "noext":
            out = Path(name).with_suffix("").as_posix()
        elif self.mode == "ret_other":
            out = (self.tmp / "elsewhere.png").as_posix()
        else:
            return None
        Path(out).write_bytes(PNG)
        return out


def _run(monkeypatch, tmp_path, mode, stale=False):
    fake = FakeCmds(mode, tmp_path)
    monkeypatch.setattr(T, "cmds", fake)
    monkeypatch.setattr(T, "_model_panel", lambda: "modelPanel4")
    path = tmp_path / "a_R0_C0.png"
    if stale:
        (tmp_path / "a_R0_C0").write_bytes(b"old")
        (tmp_path / "a_R0_C0.0002.png").write_bytes(b"old")
    return T.render_with_playblast("cam1", path, 128), path, fake


def test_exact_name_with_extension(monkeypatch, tmp_path):
    ok, path, fake = _run(monkeypatch, tmp_path, "exact")
    assert ok and path.stat().st_size > 0
    assert fake.calls[0]["completeFilename"].endswith("a_R0_C0.png")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a_R0_C0.png"]


def test_legacy_frame_numbered(monkeypatch, tmp_path):
    ok, path, _ = _run(monkeypatch, tmp_path, "frame")
    assert ok and path.is_file()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a_R0_C0.png"]


def test_legacy_extensionless(monkeypatch, tmp_path):
    ok, path, _ = _run(monkeypatch, tmp_path, "noext")
    assert ok and path.is_file()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a_R0_C0.png"]


def test_returned_path_elsewhere(monkeypatch, tmp_path):
    ok, path, _ = _run(monkeypatch, tmp_path, "ret_other")
    assert ok and path.is_file() and not (tmp_path / "elsewhere.png").exists()


def test_stale_candidates_removed_first(monkeypatch, tmp_path):
    ok, path, _ = _run(monkeypatch, tmp_path, "exact", stale=True)
    assert ok and path.read_bytes() == PNG
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a_R0_C0.png"]


def test_nothing_written_is_false(monkeypatch, tmp_path):
    ok, path, _ = _run(monkeypatch, tmp_path, "none", stale=True)
    assert not ok and not path.exists()


def test_no_panel_is_false(monkeypatch, tmp_path):
    monkeypatch.setattr(T, "_model_panel", lambda: None)
    assert T.render_with_playblast("cam1", tmp_path / "x.png", 128) is False


def test_failed_message_is_not_environment():
    assert T.UNAVAILABLE.startswith("この環境では")
    assert T.FAILED == "サムネイルを作れませんでした" and not T.FAILED.startswith("この環境")
