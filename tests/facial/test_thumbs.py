"""格子のサムネイルのキャッシュの鍵・置き場所（core/thumbs.py。Maya なし）。"""

from pathlib import Path

from tdrive_facial.core import thumbs as T
from tdrive_facial.core import validate as V
from tdrive_facial.core.model import SourcePose


def test_cache_dir_is_under_temp_and_per_document(tmp_path):
    a = T.cache_dir("C:/proj/facial/x/x.fcpose.json", base=tmp_path)
    b = T.cache_dir(r"C:\PROJ\facial\x\x.fcpose.json", base=tmp_path)  # 区切り・大文字小文字の違いは同じデータ
    c = T.cache_dir("C:/proj/facial/y/y.fcpose.json", base=tmp_path)
    assert a == b and a != c
    assert a.parent == tmp_path / T.ROOT_NAME and len(a.name) == 12
    assert T.cache_dir(None, base=tmp_path) != a  # 未保存のデータ用の置き場
    assert str(T.cache_root()).startswith(str(Path(__import__("tempfile").gettempdir())))  # 既定は一時フォルダ


def test_point_key_changes_with_pose_angle_and_size():
    h1 = V.pose_hash(SourcePose({"bs.a": 0.5}))
    h2 = V.pose_hash(SourcePose({"bs.a": 0.6}))
    k = T.point_key(h1, 22.5, -10.0, 128)
    assert k == T.point_key(h1, 22.5, -10.0, 128) and len(k) == 16
    assert k != T.point_key(h2, 22.5, -10.0, 128)  # ポーズが変わった
    assert k != T.point_key(h1, 22.6, -10.0, 128)  # 角度が変わった
    assert k != T.point_key(h1, 22.5, -10.0, 64)  # 大きさが変わった


def test_stale_thumbnail_is_not_found_and_can_be_pruned(tmp_path):
    folder = tmp_path / "d"
    folder.mkdir()
    old = T.point_key(V.pose_hash(SourcePose({"bs.a": 0.5})), 0.0, 0.0)
    new = T.point_key(V.pose_hash(SourcePose({"bs.a": 0.7})), 0.0, 0.0)
    T.thumb_path(folder, 0, 1, 2, old).write_bytes(b"png")
    assert T.find_current(folder, 0, 1, 2, old) is not None
    assert T.find_current(folder, 0, 1, 2, new) is None  # ポーズが変わった → 古い画像は出さない
    assert T.find_current(folder, 0, 1, 3, old) is None  # 別の点
    assert [p.name for p in T.stale_files(folder, 0, 1, 2, new)] == [T.file_name(0, 1, 2, old)]
    assert T.stale_files(folder, 0, 1, 2, old) == []
    (folder / "keep_me.txt").write_text("x")
    other = T.thumb_path(folder, 0, 0, 0, old)
    other.write_bytes(b"png")
    assert T.prune(folder, [other.name]) == 1  # 規則の名前だけが対象。他のファイルは消さない
    assert (folder / "keep_me.txt").exists() and other.exists()


def test_empty_file_counts_as_missing(tmp_path):
    folder = tmp_path / "d"
    folder.mkdir()
    k = "0" * 16
    T.thumb_path(folder, 0, 0, 0, k).write_bytes(b"")
    assert T.find_current(folder, 0, 0, 0, k) is None
