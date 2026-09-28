"""tdrive_toon パッケージをリロードし、開いている Look があればプレビューを作り直す（MCP script.execute / メニュー用）。

.fx / ToonCore.hlsl の変更も反映される（dx11Shader -reload は使わない。docs/09 §6）。
**編集中の Look（未保存の変更・エディタ内 Undo を含む）とプレビュー環境（プロファイル・ライト）は引き継ぐ**。
以前はファイルから開き直していたため、未保存の変更が消えていた（2026-09-28 修正）。
"""

import sys

from maya import cmds

_SESSION_FIELDS = ("look", "path", "dirty", "edit_variant", "ab", "shown", "_undo", "_redo")

old_session = sys.modules.get("tdrive_toon.session")
old_preview = sys.modules.get("tdrive_toon.preview")
old_ui = sys.modules.get("tdrive_toon.ui")

carry = None
if old_session is not None:
    s_old = old_session.current()
    carry = {k: getattr(s_old, k) for k in _SESSION_FIELDS if hasattr(s_old, k)}
env = dict(getattr(old_preview, "_env", {})) if old_preview is not None else {}
editor_open = False
if old_ui is not None:
    editor_open = getattr(old_ui, "_window", None) is not None and cmds.workspaceControl(old_ui.CONTROL_NAME, exists=True)
    if getattr(old_ui, "_window", None) is not None:
        old_ui._window.detach()  # 古いエディタの変更通知・タイマーを止める

# Maya に登録した Python オブジェクト（Render Override）は、モジュールを捨てる前に登録を外す。
# 外さないと古いモジュールと一緒にオブジェクトが解放され、Maya が解放済みのメモリを触って落ちる（2026-09-28 のクラッシュ）
old_screen_line = sys.modules.get("tdrive_toon.screen_line")
if old_screen_line is not None:
    try:
        old_screen_line.unregister()
    except Exception as exc:  # noqa: BLE001  リロード自体は続ける
        print(f"[T-Drive] 画面上の線の Override の登録解除に失敗: {exc}")

for name in sorted([m for m in sys.modules if m == "tdrive_toon" or m.startswith("tdrive_toon.")], reverse=True):
    del sys.modules[name]

from tdrive_toon import look, preview, session, ui  # noqa: E402

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
    ui.restore(ui.CONTROL_NAME)  # 同じドッキング位置に新しいコードのエディタを作り直す
cmds.refresh(force=True)
