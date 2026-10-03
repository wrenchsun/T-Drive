"""tdrive（殻）・tdrive_toon・tdrive_facial パッケージをリロードし、開いている Look があればプレビューを作り直す（MCP script.execute / メニュー用）。

.fx / ToonCore.hlsl の変更も反映される（dx11Shader -reload は使わない。docs/09 §6）。
**編集中の Look（未保存の変更・エディタ内 Undo を含む）とプレビュー環境（プロファイル・ライト）は引き継ぐ**。
以前はファイルから開き直していたため、未保存の変更が消えていた（2026-09-28 修正）。
"""

import sys

from maya import cmds

_SESSION_FIELDS = ("look", "path", "dirty", "edit_variant", "ab", "shown", "_undo", "_redo")

old_session = sys.modules.get("tdrive_toon.session")
old_preview = sys.modules.get("tdrive_toon.preview")
old_shell = sys.modules.get("tdrive.shell")
old_ui = sys.modules.get("tdrive_toon.ui")  # 殻を切り出す前（F0-1 より前）のツールからのリロード用
PACKAGES = ("tdrive_toon", "tdrive", "tdrive_facial")  # 殻・Toon・FacialController

carry = None
if old_session is not None:
    s_old = old_session.current()
    carry = {k: getattr(s_old, k) for k in _SESSION_FIELDS if hasattr(s_old, k)}
env = dict(getattr(old_preview, "_env", {})) if old_preview is not None else {}
editor_open = False
editor_control = None
editor_tool = None
if old_shell is not None and getattr(old_shell, "_window", None) is not None:
    editor_control = old_shell.active_control()
    editor_open = editor_control is not None
    if editor_open:
        editor_tool = old_shell._window.current_tool_id()  # 前面にしていたツール（Toon / FacialController）
    old_shell._window.detach()  # 古いエディタの変更通知・タイマーを止める
elif old_shell is None and old_ui is not None and getattr(old_ui, "_window", None) is not None:
    # 殻の無い古いエディタ。同じ枠（TDriveToonEditorWorkspaceControl）に新しい殻を作る（restore は旧名の枠でも動く）
    if cmds.workspaceControl(old_ui.CONTROL_NAME, exists=True):
        editor_open, editor_control, editor_tool = True, old_ui.CONTROL_NAME, "toon"
    old_ui._window.detach()

# Maya に登録したもの（Render Override など）は、モジュールを捨てる前に後片付けする（tdrive/lifecycle.py）。
# 外さないと古いモジュールと一緒にオブジェクトが解放され、Maya が解放済みのメモリを触って落ちる（2026-09-28 のクラッシュ）
old_lifecycle = sys.modules.get("tdrive.lifecycle") or sys.modules.get("tdrive_toon.lifecycle")
if old_lifecycle is not None:
    for err in old_lifecycle.run_reload_cleanups():
        print(f"[T-Drive] リロード前の後片付けに失敗: {err}")
old_screen_line = sys.modules.get("tdrive_toon.screen_line")
if old_screen_line is not None and old_lifecycle is None:  # lifecycle 導入前のツールからのリロード
    try:
        old_screen_line.unregister()
    except Exception as exc:  # noqa: BLE001
        print(f"[T-Drive] 画面上の線の Override の登録解除に失敗: {exc}")

for name in sorted([m for m in sys.modules if any(m == p or m.startswith(p + ".") for p in PACKAGES)], reverse=True):
    del sys.modules[name]  # tdrive_toon.lifecycle などの転送モジュールも同じ名前で消える

from tdrive import shell  # noqa: E402
from tdrive_toon import look, preview, session  # noqa: E402

s = session.current()
if carry and carry.get("look") is not None:
    for k, v in carry.items():
        setattr(s, k, v)
    look.upgrade(s.look)  # 新しいコードで増えた項目（例: features）を補う。見た目は変わらない
preview._env.update({k: v for k, v in env.items() if k in preview._env})

if s.look is not None and preview.preview_shaders():
    preview.reload_shader_file(look.resolve(s.look, s.shown))
    s._sync_character_preview()
    preview.apply_environment()
    print(f"tdrive_toon reloaded; preview rebuilt ({s.look['character']}, 未保存の変更 {'あり' if s.dirty else 'なし'})")
elif (path := preview.remembered_look_path()) and preview.preview_shaders():
    s.open(path)  # 編集中の Look が無いときだけファイルから開く
    preview.reload_shader_file(look.resolve(s.look, s.shown))
    s._sync_character_preview()
    print(f"tdrive_toon reloaded; preview rebuilt from {path}")
else:
    print("tdrive_toon reloaded")

if editor_open:
    shell.restore(editor_control)  # 同じドッキング位置に新しいコードのエディタを作り直す
    if editor_tool:
        shell.select_tool(editor_tool)
cmds.refresh(force=True)
