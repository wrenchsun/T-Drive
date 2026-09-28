"""起動中の Maya（commandPort :7001）へ Python を送って結果を表示する開発用ヘルパー。

MCP が使えない場面（別セッション・スクリプトからの検証）用。
使い方: python tools/maya_exec.py "_result = cmds.about(version=True)"
        python tools/maya_exec.py -f some_script.py
コード内では `cmds` が使える。`_result` に代入した値（str はそのまま、それ以外は repr）を返す。
"""

from __future__ import annotations

import argparse
import socket

PORT = 7001
MARK_PREFIX = "<<TDRIVE_RESULT"

_TEMPLATE = """import maya.cmds as cmds
_ns = {{'cmds': cmds, '__name__': '__maya_exec__'}}
try:
    exec(compile({code!r}, '<maya_exec>', 'exec'), _ns)
    _r = _ns.get('_result')
except Exception:
    import traceback
    _r = 'ERROR\\n' + traceback.format_exc()
print({mark!r} + (_r if isinstance(_r, str) else repr(_r)) + {mark!r})
"""


def run(code: str, port: int = PORT, timeout: float = 300.0) -> str:
    import uuid

    # 呼び出しごとに一意のマーカー（エコーに残る過去の結果と区別する）
    MARK = f"{MARK_PREFIX}:{uuid.uuid4().hex}>>"
    wrapped = _TEMPLATE.format(code=code, mark=MARK)
    # commandPort は 1 行ずつ評価するため全体を 1 行の exec にする。
    # echoOutput には Script Editor の過去ログも混ざるので、マーカーで結果だけ取り出す
    line = f"exec({wrapped!r})"
    buf = b""
    with socket.create_connection(("127.0.0.1", port), timeout=timeout) as s:
        s.sendall(line.encode("utf-8") + b"\n")
        while buf.count(MARK.encode()) < 2:
            data = s.recv(65536)
            if not data:
                break
            buf += data
    text = buf.decode("utf-8", errors="replace")
    parts = text.split(MARK)
    return parts[1] if len(parts) >= 3 else text


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("code", nargs="?")
    ap.add_argument("-f", "--file")
    a = ap.parse_args()
    src = open(a.file, encoding="utf-8").read() if a.file else a.code
    print(run(src))
