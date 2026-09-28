"""Maya MCP (GG_MayaMCP) との接続口となる commandPort を管理する。

ポート名は ":<port>" 形式（ホスト省略）にしてループバックのみで待ち受ける。
"""

from __future__ import annotations

import os

from maya import cmds

DEFAULT_PORT = 7001


def _port_name(port: int) -> str:
    return f":{port}"


def is_open(port: int = DEFAULT_PORT) -> bool:
    return _port_name(port) in (cmds.commandPort(query=True, listPorts=True) or [])


def open_port(port: int = DEFAULT_PORT) -> None:
    if is_open(port):
        return
    cmds.commandPort(
        name=_port_name(port),
        sourceType="python",
        echoOutput=True,
        noreturn=False,
        bufferSize=16384,
    )
    print(f"[T-Drive] Maya MCP commandPort opened on localhost:{port}")


def close_port(port: int = DEFAULT_PORT) -> None:
    if is_open(port):
        cmds.commandPort(name=_port_name(port), close=True)
        print(f"[T-Drive] Maya MCP commandPort closed ({port})")


def open_from_env() -> None:
    """TDRIVE_MCP_PORT（既定 7001、0 で無効）に従ってポートを開く。"""
    port = int(os.environ.get("TDRIVE_MCP_PORT", DEFAULT_PORT))
    if port:
        open_port(port)
