"""tdrive_toon パッケージをリロードし、開いている Look があればプレビューを作り直す（MCP script.execute 用）。

.fx / ToonCore.hlsl の変更も反映される（dx11Shader -reload は使わない。docs/09 §6）。
"""

import sys

from maya import cmds

for name in sorted([m for m in sys.modules if m == "tdrive_toon" or m.startswith("tdrive_toon.")], reverse=True):
    del sys.modules[name]

from tdrive_toon import look, preview, session  # noqa: E402

path = preview.remembered_look_path()
if path and preview.preview_shaders():
    s = session.current()
    s.open(path)
    preview.reload_shader_file(look.resolve(s.look, s.shown))
    print(f"tdrive_toon reloaded; preview rebuilt from {path}")
else:
    print("tdrive_toon reloaded")
cmds.refresh(force=True)
