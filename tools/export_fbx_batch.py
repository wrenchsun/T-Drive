"""export.export_fbx から別プロセスで呼ばれる（mayapy）。一時シーンを開いて Unity 向けに整形し FBX を書き出す。

  mayapy tools/export_fbx_batch.py <args.json>
  args.json: {"scene": 一時シーン, "meshes": [...], "out": FBX パス, "result": 結果 JSON パス}
"""

import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
os.environ.setdefault("TDRIVE_ROOT", REPO.as_posix())
sys.path.insert(0, str(REPO / "maya" / "scripts"))

import maya.standalone  # noqa: E402

maya.standalone.initialize(name="python")
try:
    from maya import cmds

    a = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    cmds.loadPlugin("dx11Shader", quiet=True)  # 一時シーンにプレビューノードが含まれるため
    cmds.file(a["scene"], open=True, force=True)
    from tdrive_toon import export

    res = export.export_in_place(a["meshes"], a["out"])
    Path(a["result"]).write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
finally:
    maya.standalone.uninitialize()
    os._exit(0)
