"""tdrive_facial.export から別プロセスで呼ばれる（mayapy）。一時シーンを開き、`fcs_*` と作業用ノードを消して Unity 向け FBX を書き出す。

  mayapy tools/export_facial_fbx_batch.py <args.json>
  args.json: {"scene": 一時シーン, "meshes": [...], "out": FBX パス, "result": 結果 JSON パス, "sculptPrefix": "fcs_"}
"""

import json
import os
import sys
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
os.environ.setdefault("TDRIVE_ROOT", REPO.as_posix())
sys.path.insert(0, str(REPO / "maya" / "scripts"))

import maya.standalone  # noqa: E402

maya.standalone.initialize(name="python")
code = 1  # 最後まで行けたときだけ 0（失敗したのに成功で終わらない。S-14）
try:
    from maya import cmds

    a = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    for plugin in ("dx11Shader",):  # 一時シーンに Toon のプレビューノードが含まれることがある（無くてもよい）
        try:
            cmds.loadPlugin(plugin, quiet=True)
        except RuntimeError:
            pass
    cmds.file(a["scene"], open=True, force=True)
    from tdrive_facial import export

    try:
        res = export.export_in_place(a["meshes"], a["out"], a.get("sculptPrefix", "fcs_"))
    except Exception:
        Path(a["result"]).with_suffix(".error.txt").write_text(traceback.format_exc(), encoding="utf-8")
        raise
    Path(a["result"]).write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
    code = 0
finally:
    maya.standalone.uninitialize()
    os._exit(code)
