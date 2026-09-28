# 07. Maya MCP 導入

Claude Code から起動中の Maya 2026 を操作するための MCP 構成。
ツール開発（コードのリロード・動作確認・ビューポートキャプチャによるルック確認）を
Claude から直接回せるようにするのが目的。

## 採用: GG_MayaMCP (`maya-mcp` 0.6.1)

| 候補 | 判断 |
|---|---|
| **GimbalGoats/GG_MayaMCP** (`maya-mcp` on PyPI) | **採用**。MIT / 型付きツール 71 個 / Maya 2024+ / サーバー側で `maya.cmds` を import しない / localhost 限定 / ビューポートキャプチャあり / 承認済みスクリプト実行 (`MAYA_MCP_SCRIPT_DIRS`) |
| abrahamADSK/maya-mcp | RAG・自己学習など機能過多。依存が重い |
| chadrik/maya-mcp-server, zu-akbar/MayaMCP, AYDJI/Autodesk-Maya-MCP | 軽量だがツールが型付けされておらず、生コード実行中心 |

選定理由（このプロジェクト固有）:

- **ビューポートキャプチャ**が A/B 比較・ルック確認の自動化に直結する
- **`script.execute`（承認済みディレクトリ内の .py のみ実行）**で、`maya/mcp_scripts/` に置いた
  定型操作（ツールのリロード、UnityChan セットアップ、A/B キャプチャ）を安全に呼べる
- 生コード実行 (`script.run`) は開発時のみ有効化（下記）

## 構成

```
Claude Code ──stdio──> uv run maya-mcp==0.6.1 (tools/maya_mcp_launcher.py)
                              │ TCP localhost:7001 (commandPort)
                              ▼
                         Maya 2026 (TDriveToon モジュールの userSetup.py がポートを開く)
```

- `.mcp.json` … プロジェクトスコープの MCP 定義（バージョン固定）
- `tools/maya_mcp_launcher.py` … `MAYA_MCP_SCRIPT_DIRS` をリポジトリ位置から絶対パスで解決して起動
- `maya/scripts/tdrive_toon/mcp_bridge.py` … Maya 側 commandPort 管理
- `maya/mcp_scripts/` … MCP から実行を許可するスクリプト置き場

## セットアップ手順

1. 前提: `uv` がインストール済み（`uv --version`）。Python/パッケージは uv が自動取得する。
2. Maya モジュールを登録:

   ```bash
   powershell -ExecutionPolicy Bypass -File tools/install_maya_module.ps1
   ```

3. Maya 2026 を起動 → Script Editor に `[T-Drive] Maya MCP commandPort opened on localhost:7001` が出ることを確認
4. このリポジトリで Claude Code を開き、`.mcp.json` の `maya` サーバーを承認
5. 疎通確認: Claude に「maya の health.check と scene.info を実行して」と頼む

## 環境変数

| 変数 | 既定 | 用途 |
|---|---|---|
| `TDRIVE_MCP_PORT` (Maya 側) | `7001` | commandPort のポート。`0` で自動オープン無効 |
| `MAYA_MCP_ENABLE_RAW_EXECUTION` (.mcp.json) | `true` | `script.run`（任意 Python 実行）を許可。開発用。共有 PC では `false` に |
| `MAYA_MCP_SCRIPT_DIRS` (ランチャーが設定) | `<repo>/maya/mcp_scripts` | `script.execute` の許可ディレクトリ |
| `MAYA_MCP_SCRIPT_TIMEOUT` | `120` | スクリプト実行タイムアウト秒 |

## セキュリティ

- commandPort は `":7001"`（ホスト省略）で開くためループバックのみ待ち受け。
- commandPort は Maya 上で任意 Python を実行できる。信頼できないプロセスが動く環境では
  `TDRIVE_MCP_PORT=0` にして、必要な時だけメニュー「T-Drive Toon > MCP ポートを開く」で開く。

## よく使う MCP スクリプト

| スクリプト | 内容 |
|---|---|
| `reload_tdrive.py`（ドラフト）| `tdrive_toon` パッケージをリロードし UI を開き直す |
| `setup_unitychan.py`（予定: 1-3）| UnityChan 読込 → 自動部位登録 → Toon 表示 |
| `capture_ab.py`（予定: 1-7）| A/B 両バリアントを同条件でキャプチャし `captures/` に保存 |
