"""tdrive_toon パッケージをリロードしてエディタを開き直す（MCP script.execute 用）。"""

import sys

for name in sorted([m for m in sys.modules if m == "tdrive_toon" or m.startswith("tdrive_toon.")], reverse=True):
    del sys.modules[name]

from tdrive_toon import menu, ui  # noqa: E402

menu.install()
ui.show()
print("tdrive_toon reloaded")
