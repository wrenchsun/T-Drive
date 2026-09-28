# 08. Unity（MS2026）移植計画

**のちに行う**。本書は Maya 側の仕様を Unity で受けられることを先に確認するための計画であり、実装は [tasks.md](tasks.md) の U チケット。

## 1. 移植先の前提（2026-09-28 調査）

> MS2026 は現在プロトタイプで、以下は変わりうる。値に依存するものは環境プロファイル（[09](09_render_parity.md) §3）経由にし、コード・仕様に直書きしない。

| 項目 | MS2026 | D-Drive |
|---|---|---|
| Unity | 6000.3.13f1 | 6000.3.13f1（アップグレード禁止） |
| パイプライン | URP 17.3.0、PC_Renderer = Forward+ + SSAO | 同 |
| D-Drive | `com.ddrive.core` v1.3.0（git URL で参照） | 本体 |
| トゥーンシェーダー | **なし** | **なし**（DDrive/Lit・Unlit・AiStandardSurface のみ） |
| アウトライン / Renderer Feature | なし | なし |
| キャラクター資産 | `Assets/_Project/Art/Models` は空 | UnityChan（URP Lit、UTS の残骸プロパティあり） |
| 規約 | 全アセットは D-Drive カタログ経由（CLAUDE.md 規則 9）。命名 `M_` `T_<対象>_<種別>` `SK_` | MaterialData / TextureData / ModelData |

## 2. 作るもの

| # | 成果物 | 置き場所（案） | 内容 |
|---|---|---|---|
| 1 | `MS2026/Toon` シェーダー | MS2026 `Assets/_Project/Art/Shaders/Toon/` | **本リポジトリの `shaders/ToonCore.hlsl` を同梱して include する薄いラッパー**（式を書き直さない）。Properties は D-Drive 規約の区分で並べる |
| 2 | Look インポーター（Editor） | MS2026 `Assets/_Project/Scripts/Editor/Toon/` | `*.materialdata.json` → D-Drive `MaterialData`（Common / Specific / RenderQueueOffset）を生成・更新。テクスチャは TextureData に解決 |
| 3 | キャラクターライト（Runtime） | MS2026 `Assets/_Project/Scripts/Toon/` | `_ToonCharacterLightDir/Color` を `Mats.SetGlobalParam` で配布、平滑化（T-17） |
| 4 | FBX 取込設定 | MS2026 | 法線 Import・接線 Import・頂点カラー保持・UV2 保持 |
| 5 | 環境プロファイル書き出し + パリティキャプチャ（Editor） | MS2026 `Assets/_Project/Scripts/Editor/Toon/` | シーンの色空間・トーンマップ・メインライト・カメラを `looks/_env/*.json` 形式で書き出し、同条件でゲームビューを撮る（[09](09_render_parity.md)） |

### D-Drive に手を入れない方針

シェーダー・インポーターは **MS2026 側（消費者側）** に置き、D-Drive 本体は変更しない。
D-Drive の拡張点（MaterialData の Specific、ShaderConversionTable、MayaImportProfile）だけで成立させる。
汎用化できたら D-Drive へ MINOR（追加のみ）として還元することを検討する。

## 3. D-Drive 適合チェックリスト

| 項目 | 確認方法 |
|---|---|
| シェーダーの Common 名が `MaterialCommonNaming` の規約名 | `MaterialCommonBinding.IsSupported(shader, channel)` が Albedo / AlbedoTint / Blend / Cutoff / DoubleSided で true |
| Specific に予約名が無い | `MaterialSpecificResolver.Merge` の `MergeReport.Conflict` が空 |
| Blend と RenderQueue 帯が一致 | `MaterialDataValidator` が警告を出さない（Transparent の目・頬は 3000 帯） |
| マテリアル共有 | `MaterialManager.Get(id)` の共有 Material で描画できる（キャラクター個別値は MPB / グローバル） |
| SRP Batcher | Frame Debugger で SRP Batch になっている |
| Material Editor プレビュー | D-Drive の Material Editor の球プレビューで Toon 表示される（キャラクターライト未設定時の既定方向で） |

## 4. 事前に決めること（要合意）

| # | 事項 | 案 |
|---|---|---|
| Q-1 | シェーダーの置き場所・名前 | MS2026 に `MS2026/Toon`（上記）。D-Drive に入れるなら `DDrive/Toon` |
| Q-2 | `_ToonMaskMap` のテクスチャ規約 | 命名 `T_<対象>_ToonMask`、リニア・圧縮 BC7。D-Drive `TextureImportProfile` に接尾辞ルールを追加するか、MS2026 側のインポート設定で扱うか |
| Q-3 | Renderer Feature の追加可否 | T-23（スクリーンスペース線）/T-29（接地影）/T-24（ステンシル）で必要。MS2026 PC_Renderer への追加を MS2026 チームと合意 |
| Q-5 | ~~本番シーンのトーンマップ~~ | **解決（2026-09-28）**: InGame は SampleSceneProfile = Neutral |
| Q-6 | U-0（パリティ最小環境）の前倒し | Unity 実装は「のちに」だが、ギャップ最小化のため最小シェーダーだけ Phase 1 中に作るか |
| Q-4 | UnityChan の扱い | テスト素体。MS2026 本番キャラクターの受け入れ前の検証にのみ使い、ゲームには入れない |
