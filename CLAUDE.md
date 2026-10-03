# CLAUDE.md — T-Drive

Maya 2026 用セルルック Look Development ツール。仕様は docs/（索引: docs/README.md）。

## 進め方
- **仕様 → チケット → 実装** の順。実装前に該当する docs を更新し、docs/tasks.md のチケット単位で進める
- 優先順位は **ルック（P0→P1→P2）→ アニメーション（A1→A2）**（docs/04）
- Unity 移植は「のちに」。ただし Maya プレビューは Unity の描画に合わせる（docs/09）

## 守ること
- **デザイナーマニュアル（docs/DesignerManual/*.html）をこまめに更新する**: デザイナーが触る機能を追加・変更したら、同じコミットで該当ページ・「今できること」の表・最終更新日・スクリーンショット（images/）を直す。準備中の機能は `<span class="soon">準備中</span>` で明示し、実装と食い違う記述を残さない
- **人による確認項目リストを一緒に作る**: 機能を追加・変更したら、人が操作して確かめる項目を同じコミットで確認項目リスト（docs/10 = ルック、docs/18 = FacialController・Unity ほか）に足す。「自動」欄にはテストで確認済みの範囲を書き、見た目・操作感など人の判断が要るものは未確認のまま残す
- **D-Drive MaterialData に適合**: Look 定義は Common / Specific / RenderQueueOffset に落ちる形。固有パラメータは `_Toon` 接頭辞（D-Drive の予約名と衝突させない）
- シェーダーの式は `shaders/ToonCore.hlsl` の 1 か所だけに書く（Maya .fx と Unity .shader は include するラッパー）
- パラメータ契約（maya/scripts/tdrive_toon/params.py）・頂点カラー割当・Look スキーマの削除/改名は MAJOR。追加は MINOR（docs/06）
- UnityChan はテスト素体。元のマテリアル（URP Lit / UTS 残骸）は参照しない
- 元マテリアル・元の割り当てを破壊する処理を書かない
- Maya 非依存層（params / roles / look）に maya を import しない
- **Maya / Qt との境界の約束**（2026-09-28 のクラッシュ・エラーの再発防止。`tdrive_toon/lifecycle.py`、`tests/test_ui_patterns.py` が検出）
  - Maya に Python のオブジェクト・関数を登録したら `lifecycle.keep_alive` と `lifecycle.on_reload(後片付け)`。リロードは後片付けを先に呼ぶ
  - Maya から呼ばれるコールバック（VP2 の Render Override など）は `@lifecycle.guarded(...)` で包む。戻り値の int は 32 ビット符号付きに収める
  - シェーダーパラメータは `screen_line.set_param`（型を確かめる）経由
  - Qt の clicked / toggled に「必須引数 + 既定値付き引数」の lambda をつながない（`lambda *_, x=...:` にする）
  - `cmds.delete` に空になり得るリストを渡さない
- 参照リポジトリ: MS2026（C:\Users\yamag\wrench\unity\MS2026）は読み取りのみ。D-Drive（C:\Users\yamag\wrench\unity\D-Drive）は **ブランチを切ってローカルで作業**（main に直接コミットしない・push しない。Unity の確認用ブランチは `tdrive-facial`。2026-10-03）。FacialController_UE（C:\Users\yamag\wrench\ue\FacialController_UE）は読み取りのみ

## コマンド
- テスト: `uv run --no-project --with pytest python -m pytest tests`
- Maya MCP: `.mcp.json`（maya-mcp 0.6.1）。Maya 側は tools/install_maya_module.ps1 でモジュール登録
- push・タグ push は明示的に指示されたときだけ
