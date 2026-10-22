"""別プロセスの mayapy で FBX を書き出すジョブの共通部分（docs/20 §7。Toon の `ExportJob` から切り出し）。

開いているシーンを壊さないために、**現在のシーンを一時ファイルに書き出し → 別プロセスの mayapy（`tools/<script>`）で開いて整形・書き出し**する。
Maya の画面は止まらない。`start()` は要らず、生成すると開始する。`poll()` が True になったら `result()`。

公開名の互換: `tdrive_toon.export.ExportJob` は、このクラスを継いだまま従来の引数（meshes, path）で使える。
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from maya import cmds

from . import REPO_ROOT


def mayapy() -> Path:
    exe = Path(sys.executable).with_name("mayapy.exe")
    if exe.exists():
        return exe
    return Path(os.environ.get("MAYA_LOCATION", r"C:\Program Files\Autodesk\Maya2026")) / "bin" / "mayapy.exe"


class BatchJob:
    """`tools/<script>` を別プロセスの mayapy で実行する。payload は args.json として渡る（scene・result は自動で足す）。"""

    def __init__(self, payload: dict[str, Any], script: str = "export_fbx_batch.py", prefix: str = "tdrive_export_") -> None:
        self.work = Path(tempfile.mkdtemp(prefix=prefix))
        self.result_path = self.work / "result.json"
        scene = self.work / "scene.mb"
        # シーン名・未保存フラグを変えずに内容だけ書き出す（メインスレッドで行う）
        try:
            cmds.file(str(scene), exportAll=True, type="mayaBinary", force=True, preserveReferences=True)
        except RuntimeError:
            # 不明なノード（読み込めていないプラグインのノード。mGear など）を含む .ma は、バイナリに変換できない。.ma のまま書く
            scene = self.work / "scene.ma"
            cmds.file(str(scene), exportAll=True, type="mayaAscii", force=True, preserveReferences=True)
        args = self.work / "args.json"
        args.write_text(json.dumps({**payload, "scene": str(scene), "result": str(self.result_path)}, ensure_ascii=False), encoding="utf-8")
        env = dict(os.environ, TDRIVE_ROOT=REPO_ROOT.as_posix(), PYTHONIOENCODING="utf-8", MAYA_DISABLE_CER="1")  # 裏の mayapy のクラッシュ画面を出さない
        self.log = open(self.work / "log.txt", "wb")
        self.proc = subprocess.Popen(
            [str(mayapy()), str(REPO_ROOT / "tools" / script), str(args)],
            stdout=self.log, stderr=subprocess.STDOUT, env=env,
            # mayapy はコンソールアプリなので、指定しないと黒いウィンドウが書き出し中ずっと出る（閉じると失敗する）
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    def poll(self) -> bool:
        return self.proc.poll() is not None

    def kill(self) -> None:
        """裏の mayapy を止める（キャンセル・タイムアウト・ウィンドウを閉じる）。ログを閉じ、作業フォルダを消す（SF-15）。"""
        if self.proc.poll() is None:
            self.proc.kill()
            with contextlib.suppress(Exception):
                self.proc.wait(timeout=10)
        self.dispose()

    def dispose(self) -> None:
        """ログを閉じ、作業フォルダ（シーンの丸ごとのコピー）を消す。何度呼んでもよい。成功のあとと kill のあとに呼ぶ。
        失敗したときは呼ばない（ログを読めるように残す）。"""
        with contextlib.suppress(Exception):
            self.log.close()
        shutil.rmtree(self.work, ignore_errors=True)

    def log_tail(self, n: int = 3000) -> str:
        try:
            return (self.work / "log.txt").read_bytes().decode("utf-8", "replace")[-n:]
        except OSError:
            return ""

    def result(self) -> dict[str, Any]:
        self.log.close()
        if not self.result_path.exists():
            raise RuntimeError("FBX 書き出しに失敗しました:\n" + self.log_tail())
        return json.loads(self.result_path.read_text(encoding="utf-8"))
