# T-Drive ドキュメント

| # | 文書 | 内容 |
|---|---|---|
| 00 | [00_overview.md](00_overview.md) | 目的・スコープ・制約（D-Drive 適合）・必須要件・用語 |
| 01 | [01_architecture.md](01_architecture.md) | Maya ⇔ Look 定義 ⇔ Unity の構成、レイヤー、技術選定 |
| 02 | [02_look_definition_spec.md](02_look_definition_spec.md) | Look 定義 JSON 仕様（部位・マテリアル・バリアント・検証・Unity 向け出力） |
| 03 | [03_shader_spec.md](03_shader_spec.md) | シェーダーの式・頂点カラー割当・パラメータ契約 |
| 04 | [04_technique_priority.md](04_technique_priority.md) | 技術の優先順位（ルック → アニメーション）と設計判断 D-1〜D-5 |
| 05 | [05_maya_tool_spec.md](05_maya_tool_spec.md) | Maya エディタ仕様（部位 / ルック / A/B / プレビュー） |
| 06 | [06_release_versioning.md](06_release_versioning.md) | バージョン管理・互換性区分・リリース手順（D-Drive 準拠） |
| 07 | [07_maya_mcp_setup.md](07_maya_mcp_setup.md) | Maya MCP 導入手順 |
| 08 | [08_unity_port_plan.md](08_unity_port_plan.md) | MS2026 への移植計画・D-Drive 適合チェック・要合意事項 |
| 09 | [09_render_parity.md](09_render_parity.md) | Maya プレビューと Unity 描画の一致（パリティ）方針 |
| 11 | [11_features_and_shader_generation.md](11_features_and_shader_generation.md) | 機能のオン/オフと、プロジェクト専用シェーダーの生成 |
| 13 | [13_distribution.md](13_distribution.md) | 配布・導入・更新（Maya ツール。D-Drive と同じ git タグ固定 + 更新ウィンドウ） |
| 12 | [12_substance_painter.md](12_substance_painter.md) | Substance Painter の扱い（検討メモ・確認待ち） |
| 10 | [10_manual_verification_2026-09-28.md](10_manual_verification_2026-09-28.md) | 人による確認項目（Phase 1〜4） |
| — | [tasks.md](tasks.md) | 優先順位順のチケット一覧 |
| — | [DesignerManual/Readme.html](DesignerManual/Readme.html) | **デザイナーマニュアル**（HTML。デザイナー向けの使い方） |

読む順番: 00 → 04 → tasks → 必要に応じて 01〜03, 05〜09。
