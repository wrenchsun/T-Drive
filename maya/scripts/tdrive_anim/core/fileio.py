"""JSON などの書き込み（Maya 非依存）。同じフォルダの一時ファイルに書いてから置き換える（SF-16）。

書いている最中に落ちても、以前のファイルは壊れない（半分だけ書かれた animset.json / shot.json を残さない）。
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path


def write_text_atomic(path: str | Path, text: str) -> Path:
    """UTF-8・LF で書く。親フォルダが無ければ作る。書き込みに失敗したら一時ファイルを消して例外を再送する。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=p.name + ".", suffix=".tmp", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, p)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    return p
