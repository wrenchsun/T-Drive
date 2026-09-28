# 01. アーキテクチャ

## 1. 基本方針

**「Maya と Unity が同じ Look 定義と同じシェーダーコードを使い、Maya プレビューを Unity の描画に合わせる」。**

- データ: Look 定義（`look.json`）を両者が読む
- 式: `shaders/ToonCore.hlsl` を両者が include する（二重実装しない）
- 環境: Unity の色空間・トーンマップ・ライト・カメラを環境プロファイルとして Maya に持ち込む
- 検証: 同条件キャプチャの画素差分で一致を確認する

詳細は [09_render_parity.md](09_render_parity.md)。Unity 側は T-Drive の UPM パッケージ（[08](08_unity_port_plan.md)）がルックを一任で受け持ち、D-Drive とは互換ブリッジでつなぐ。Unity の Renderer Feature やポストなど、マテリアル外の描画は再現しない（既知ギャップとして管理）。

```
                    looks/<character>/look.json  （唯一の真実・Git 管理）
                                  │
                ┌─────────────────┴─────────────────┐
                ▼                                   ▼
        Maya 2026 (本リポジトリ)                 Unity（T-Drive for Unity パッケージ → MS2026）
        Look Development                       Runtime Renderer
        ├ 部位登録                              ├ MS2026/Toon シェーダー
        ├ ルック調整 / A/B / プレビュー          ├ インポーター → D-Drive MaterialData
        ├ マスク・法線などアートデータ作成       ├ キャラクターライト / 影の安定化
        └ Unity 向け中間ファイル出力 ──────────▶ └ ゲームカメラ・ポスト・LOD
```

### 役割分担（どちらで持つか）

| 種類 | 例 | 持ち場 | 理由 |
|---|---|---|---|
| **キャラクター固有のアートデータ** | ShadowMask / Toon 法線 / 顔影 / 線マスク / 部位ロール / 描画優先度 / 補正 BlendShape | **Maya → Look 定義** | モデルを見ながら「ここは影にしたい」を直接作る方が速い |
| **キャラクター固有の調整値** | 影色・影の境界・線幅・リム・頬色 | **Look 定義**（Maya/Unity 両方で編集可、最終的に Look に書き戻す） | どちらでも触れる必要がある |
| **ゲーム環境依存** | ライト方向・影の安定化（ヒステリシス）・カメラ距離による線幅・ポスト・LOD | **Unity** | ゲーム中の状況で決まる。Maya ではプレビュー用の仮値だけ持つ |

## 2. レイヤー構成（Maya 側）

```
shaders/ToonCore.hlsl               式の唯一の実装（Unity と共有する純 HLSL）
looks/_env/*.json                   Unity 環境プロファイル
maya/
├─ shaders/TDriveToon.fx            プレビュー用 dx11Shader（../../shaders/ToonCore.hlsl を include）
├─ scripts/tdrive_toon/
│   ├─ params.py      ┐
│   ├─ roles.py       │ Maya 非依存（純 Python、tests/ で単体テスト）
│   ├─ look.py        ┘   パラメータ契約 / ロール / Look 定義の読み書き・検証・差分・出力
│   ├─ preview.py     ┐
│   ├─ session.py     │ Maya 依存
│   ├─ ui.py          │   シーン走査・シェーダー差し替え / 編集セッション / エディタ UI / メニュー
│   ├─ menu.py        │
│   └─ mcp_bridge.py  ┘   Maya MCP 用 commandPort
├─ scripts/userSetup.py             起動時にメニュー追加・commandPort オープン
└─ mcp_scripts/                     MCP から実行を許可する定型スクリプト
```

- **Maya 非依存層に仕様を寄せる**: Look の検証・バリアント解決・差分・D-Drive 形式への変換はすべて純 Python。Unity 側インポーターの仕様確認にもそのまま使える
- **UI はセッション層の薄いラッパー**: 全操作は `session.current()` の API で完結し、MCP スクリプトからも同じ操作ができる（Claude による自動検証の前提）

## 3. データフロー

```
[FBX / Maya シーン]
      │ 部位登録（自動推定 + 手動）
      ▼
[look.json]  ◀────── エディタ UI のスライダー（base またはバリアントへ書き込み）
      │ resolve(variant)            │
      ▼                              ▼
[マテリアルごとの最終値] ──setAttr──▶ [GLSLShader ×マテリアル数]（VP2 で即時反映）
      │
      │ to_ddrive_material_data()
      ▼
[build/unity/<char>/<char>_<variant>.materialdata.json]
      │（のちに）MS2026 インポーター
      ▼
[D-Drive MaterialData .asset]  + テクスチャ（TextureData）
```

## 4. Maya シーンとの関係

- 元マテリアルは **削除・変更しない**。プレビュー時は面の割り当てを `<material>_tdToon` の SG に移し、解除時に戻す
- Look 定義のパスはシーンの `fileInfo "tdriveToonLook"` に記録し、シーンを開き直しても同じ Look を再オープンできる
- シーンファイルは Look 定義の従属物。**Unity はシーンファイル (.ma/.mb) に依存しない**

## 5. 技術選定

| 項目 | 選定 | 理由 / 代替 |
|---|---|---|
| Maya プレビュー | **dx11Shader (.fx / HLSL) on VP2 DirectX 11** | Unity と同じ HLSL（ToonCore）を include でき、式のズレが構造的に起きない。マルチパス可・アトリビュートが自動生成されスライダーと直結。代替: GLSLShader（式の二重実装が必要なので不採用、D-5）、ShaderFX（式管理が困難） |
| UI | PySide6（Maya 2026 同梱）+ MayaQWidgetDockableMixin | ドッキング可能なエディタ |
| データ形式 | JSON（キー順固定・インデント 2） | Git で差分レビューしやすい。JSON Schema で外部検証も可能 |
| テスト | pytest（`uv run --with pytest`）で Maya 非依存層、mayapy / MCP で Maya 依存層 | |
| Maya 操作自動化 | GG_MayaMCP 0.6.1 | [07_maya_mcp_setup.md](07_maya_mcp_setup.md) |
