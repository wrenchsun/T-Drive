# CLAUDE.md — T-Drive

Maya 2026 用セルルック Look Development ツール。仕様は docs/（索引: docs/README.md）。

## 進め方
- **仕様 → チケット → 実装** の順。実装前に該当する docs を更新し、docs/tasks.md のチケット単位で進める
- 優先順位は **ルック（P0→P1→P2）→ アニメーション（A1→A2）**（docs/04）
- Unity 移植は「のちに」。ただし Maya プレビューは Unity の描画に合わせる（docs/09）

## 守ること
- **デザイナーマニュアル（docs/DesignerManual/*.html）をこまめに更新する**: デザイナーが触る機能を追加・変更したら、同じコミットで該当ページ・「今できること」の表・最終更新日・スクリーンショット（images/）を直す。準備中の機能は `<span class="soon">準備中</span>` で明示し、実装と食い違う記述を残さない
- **D-Drive MaterialData に適合**: Look 定義は Common / Specific / RenderQueueOffset に落ちる形。固有パラメータは `_Toon` 接頭辞（D-Drive の予約名と衝突させない）
- シェーダーの式は `shaders/ToonCore.hlsl` の 1 か所だけに書く（Maya .fx と Unity .shader は include するラッパー）
- パラメータ契約（maya/scripts/tdrive_toon/params.py）・頂点カラー割当・Look スキーマの削除/改名は MAJOR。追加は MINOR（docs/06）
- UnityChan はテスト素体。元のマテリアル（URP Lit / UTS 残骸）は参照しない
- 元マテリアル・元の割り当てを破壊する処理を書かない
- Maya 非依存層（params / roles / look）に maya を import しない
- 参照リポジトリ（D-Drive: C:\Users\yamag\wrench\unity\D-Drive、MS2026: C:\Users\yamag\wrench\unity\MS2026）は読み取りのみ

## コマンド
- テスト: `uv run --no-project --with pytest python -m pytest tests`
- Maya MCP: `.mcp.json`（maya-mcp 0.6.1）。Maya 側は tools/install_maya_module.ps1 でモジュール登録
- push・タグ push は明示的に指示されたときだけ
