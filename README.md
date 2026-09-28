# T-Drive — Maya 2026 セルルック キャラクター Look Development ツール

Maya 2026 でセルルック（アニメ調）キャラクターのルックを部位ごとに作り込み、A/B 比較しながら調整し、
D-Drive のマテリアルシステム（MaterialData）に適合する形で Unity（MS2026）へ移植するためのツール。

- 仕様: [docs/README.md](docs/README.md)（まず [00_overview](docs/00_overview.md) → [04_technique_priority](docs/04_technique_priority.md) → [tasks](docs/tasks.md)）
- 状態: **仕様策定段階**。実装は docs/tasks.md の優先順に進める

## 構成

```
docs/                 仕様書・チケット
shaders/              Unity と共有するシェーダーコア（ToonCore.hlsl、予定）
maya/                 Maya モジュール（scripts/tdrive_toon, shaders, mcp_scripts）
looks/                キャラクターごとの Look 定義（唯一の真実）と Unity 環境プロファイル
assets/unitychan/     テスト素体（UnityChan、UCL2.02）
schema/               JSON Schema（予定）
tools/                MCP ランチャー・インストーラー・リリーススクリプト
tests/                Maya 非依存層の pytest
```

## セットアップ

```bash
git lfs install
powershell -ExecutionPolicy Bypass -File tools/install_maya_module.ps1
```

Maya 2026 を起動すると「T-Drive Toon」メニューと MCP 用 commandPort（localhost:7001）が有効になる。
Claude Code からの Maya 操作は [docs/07_maya_mcp_setup.md](docs/07_maya_mcp_setup.md)。

## テスト

```bash
uv run --no-project --with pytest python -m pytest tests
```

## ライセンス表記

`assets/unitychan/` のデータは © Unity Technologies Japan/UCL。ユニティちゃんライセンス条項（UCL2.02）に従う（`assets/unitychan/license/`）。
