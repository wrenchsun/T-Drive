# Changelog

形式は [Keep a Changelog](https://keepachangelog.com/ja/1.1.0/)、バージョンは [SemVer](https://semver.org/lang/ja/)。
各バージョンに `### 互換性` を必ず書く（[docs/06_release_versioning.md](docs/06_release_versioning.md)）。

## [Unreleased]

### 追加
- プロジェクト雛形（.gitignore / .gitattributes（Git LFS）/ VERSION / CLAUDE.md / README）
- 仕様書一式（docs/00〜09, docs/tasks.md）
- Maya MCP（GG_MayaMCP 0.6.1）導入: `.mcp.json`、`tools/maya_mcp_launcher.py`、Maya モジュール（commandPort 自動オープン）
- テスト素体 UnityChan（D-Drive から FBX・テクスチャ・ライセンスを取り込み）
- ドラフト実装（仕様確定前の試作。tasks.md で 🔶）: パラメータ契約・Look 定義・プレビュー・セッション層

### 互換性
- 初回のため互換性の面は未確定（0.x の間は MINOR で破壊的変更を許容する）
