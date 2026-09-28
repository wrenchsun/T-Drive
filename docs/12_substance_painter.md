# 12. Substance Painter の扱い（検討メモ）

2026-09-28 作成。状態: **提案（チーム内で Painter を使うか確認待ち）**。

## 1. 結論

- **フル対応（Painter 上でセルルックを表示する）はしない**
- チームのテクスチャ作業で Painter を使う場合に限り、**書き出し設定（Export Preset）とチャンネル規約**だけを用意する（チケット P-1、0.5 日）

## 2. フル対応をしない理由

| 理由 | 内容 |
|---|---|
| 式が 2 重になる | Painter のシェーダーは GLSL。`shaders/ToonCore.hlsl` を書き直すことになり、「式は 1 か所だけ」（CLAUDE.md・[09](09_render_parity.md) D-5）が崩れる。Maya・Unity・Painter の 3 か所でパリティを保つ保守コストが増える |
| 最終判断に使えない | 影の境界・輪郭線・ライト角度・カメラ依存の補正など、セルルックの肝は Maya プレビューと Unity で確認する設計（[01](01_architecture.md)）。Painter で見えても最終判断は Maya / Unity になる |
| マスクの主役は頂点カラー | 影・線・固定色のマスクは頂点カラーが主（[04](04_technique_priority.md) D-1）。Painter は頂点カラーの書き出しに向かないので、Maya で塗る方が合っている |

## 3. Painter が役立つ所

| 対象 | パラメータ | 形式 |
|---|---|---|
| ベース色 | Common `albedo` | sRGB |
| Toon マスクテクスチャ（頂点密度が足りない細部） | `_ToonMaskMap` | **リニア** RGBA。R = 常に影 / G = 線幅 / B = 常に明 / A = 固定色。**白 = 何もしない**（黒ほど効く。[03](03_shader_spec.md) §4） |
| 髪ハイライトの帯 | `_ToonHairHighlightMap` | R（リニア） |

- 対象外: `_ToonFaceShadowMap`（SDF。T-Drive の「マスクから生成」で作る。[05](05_maya_tool_spec.md) §3.3）、`_ToonMatCapMap`（UV に依存しない汎用テクスチャ）

## 4. チケット案

| # | チケット | 日数 | 受け入れ条件 | 状態 |
|---|---|---|---|---|
| P-1 | Painter 書き出し設定（`.spexp`）+ チャンネル・色空間の規約をマニュアルに追加。Toon マスクの 4 チャンネルを塗る用のチャンネル（既定値 = 白）を用意 | 0.5 | 書き出したテクスチャを Look に設定すると、塗った所だけ効く（白の所は変化なし）。色空間の設定手順がマニュアルにある | ⬜ 確認待ち |
| P-2 | （見送り）Painter 用セルルックシェーダー（GLSL） | 3〜 | 採用する場合は、ToonCore と画素比較する自動テストとセット | — |

- 時期: ルックの残り（Phase 4）の後、Unity（Phase U）の前
- 確認事項: キャラクターのテクスチャ制作に Painter を使うか（手描き中心なら Photoshop / CLIP STUDIO 向けの規約だけで足りる）
