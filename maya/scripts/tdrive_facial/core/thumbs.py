"""格子のサムネイルのキャッシュ（Maya 非依存。docs/14 §5.3 の「点ごとのサムネイル」、チケット F2-6）。

サムネイル = その点のポーズを当てて、その点の角度から見た顔の小さな PNG。**リポジトリ・プロジェクトの中には置かず**、
ユーザーの一時フォルダの下に、データ（`.fcpose.json`）のパスごとのフォルダを作って置く:

    <temp>/tdrive_facial_thumbs/<データのパスのハッシュ 12 桁>/L{レイヤー番号}_R{row}_C{col}_{鍵}.png

鍵 = ポーズのハッシュ + 点の角度 + 画像の大きさ から作る。**ポーズを変えると鍵が変わり、古い画像は「古い」ので出さない**
（ファイル名が違うので見つからないだけ。作り直すときに同じ点の古いファイルを消す）。
"""

from __future__ import annotations

import hashlib
import re
import tempfile
from pathlib import Path
from typing import Iterable, Optional, Union

ROOT_NAME = "tdrive_facial_thumbs"
DEFAULT_SIZE = 128
_NAME = re.compile(r"^L(\d+)_R(\d+)_C(\d+)_([0-9a-f]{16})\.png$")


def cache_root(base: Optional[Union[str, Path]] = None) -> Path:
    """キャッシュの親フォルダ（既定はユーザーの一時フォルダの下）。作りはしない。"""
    return Path(base if base is not None else tempfile.gettempdir()) / ROOT_NAME


def doc_key(doc_path: Optional[Union[str, Path]], fallback: str = "unsaved") -> str:
    """データのパスのハッシュ（12 桁）。大文字小文字・区切りの違いは同じ扱い。パスが無い（未保存）なら fallback の名前。"""
    text = str(doc_path).replace("\\", "/").lower() if doc_path else f"<{fallback}>"
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def cache_dir(doc_path: Optional[Union[str, Path]], base: Optional[Union[str, Path]] = None, fallback: str = "unsaved") -> Path:
    """このデータのサムネイルを置くフォルダ（作りはしない）。"""
    return cache_root(base) / doc_key(doc_path, fallback)


def point_key(pose_hash: str, yaw: float, pitch: float, size: int = DEFAULT_SIZE) -> str:
    """サムネイルの鍵（16 桁）。ポーズ・角度（0.01° まで）・画像の大きさのどれかが変わると変わる。"""
    text = f"{pose_hash}|{yaw:.2f}|{pitch:.2f}|{int(size)}"
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def file_name(layer_index: int, row: int, col: int, key: str) -> str:
    return f"L{int(layer_index)}_R{int(row)}_C{int(col)}_{key}.png"


def thumb_path(folder: Union[str, Path], layer_index: int, row: int, col: int, key: str) -> Path:
    return Path(folder) / file_name(layer_index, row, col, key)


def find_current(folder: Union[str, Path], layer_index: int, row: int, col: int, key: str) -> Optional[Path]:
    """鍵が今のポーズと同じサムネイルがあればそのパス。無い・古い（鍵が違う）なら None。"""
    p = thumb_path(folder, layer_index, row, col, key)
    try:
        return p if p.is_file() and p.stat().st_size > 0 else None
    except OSError:
        return None


def stale_files(folder: Union[str, Path], layer_index: int, row: int, col: int, keep_key: str) -> list[Path]:
    """同じ点の、鍵が違う（古い）サムネイルのファイル。"""
    out: list[Path] = []
    try:
        entries = list(Path(folder).iterdir())
    except OSError:
        return out
    for p in entries:
        m = _NAME.match(p.name)
        if m and (int(m.group(1)), int(m.group(2)), int(m.group(3))) == (layer_index, row, col) and m.group(4) != keep_key:
            out.append(p)
    return out


def prune(folder: Union[str, Path], keep: Iterable[str]) -> int:
    """`keep`（残すファイル名）以外のサムネイル（この規則の名前のもの）を消す。消した数を返す。他のファイルには触らない。"""
    keep_set = set(keep)
    n = 0
    try:
        entries = list(Path(folder).iterdir())
    except OSError:
        return 0
    for p in entries:
        if _NAME.match(p.name) and p.name not in keep_set:
            try:
                p.unlink()
                n += 1
            except OSError:
                pass
    return n
