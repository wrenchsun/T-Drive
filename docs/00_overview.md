# 00. 概要・要件

## 1. 目的

Maya 2026 上で **セルルック（アニメ調）キャラクターのルックを作り込むためのツール（Look Development 環境）** を作る。
作ったルックデータと、検証で確定した技術を元に、最適化した形で Unity（MS2026）へ移植する。

- ツールの本質は「Toon シェーダー」ではなく、**3D モデルを 2D 作画に寄せるための“嘘”をキャラクターごと・部位ごとにデータとして持ち、調整・比較できる仕組み**
- Maya は **ルックを作る場所**、Unity は **ゲーム環境で最終的に描く場所**。ただし **Maya のプレビューは可能な限り Unity の描画に合わせ、移行時の見た目のギャップを最小にする**（シェーダーコード共有・Unity 環境の数値持ち込み・差分計測。[09_render_parity.md](09_render_parity.md)）

## 2. スコープ

| 対象 | 内容 |
|---|---|
| **やる** | Maya ツール（部位登録 / 部位別ルック調整 / A/B 比較 / プレビュー / Unity 向け出力）、Look データ仕様、シェーダー仕様、Unity 移植計画、リリース運用 |
| **のちにやる** | Unity 側シェーダー・インポーター実装（MS2026）→ [08_unity_port_plan.md](08_unity_port_plan.md)<br>アニメーションの“嘘”（ルック完了後。優先順位は **ルック → アニメーション**）→ [04](04_technique_priority.md) A1/A2 |
| **やらない** | PBR ベースの質感表現、Maya でのレンダリング（Arnold）、UnityChan 元シェーダー（UTS）の再現 |

## 3. 制約

| # | 制約 | 出典 / 理由 |
|---|---|---|
| C-1 | ツールは **Maya 2026**（Python 3.11 / PySide6 / Viewport 2.0）で動くこと | 指定 |
| C-2 | 移植先は `C:\Users\yamag\wrench\unity\MS2026`（Unity 6000.3.13f1 / URP 17.3 / Forward+） | 指定・調査 |
| C-3 | ~~D-Drive の MaterialData に適合すること~~ → **2026-09-28 改定**: キャラクターのルックは **T-Drive の Unity パッケージ（`com.tdrive.toon`）に一任**し D-Drive から切り離す。ただし**互換性は保つ**（シェーダーのプロパティ構造を D-Drive の命名規約と同じにし、MaterialData へ変換できる）。シェーダー単体で無理な表現はコンポーネント・Renderer Feature で実装 | 指定 → [08](08_unity_port_plan.md) |
| C-4 | テスト素体は **UnityChan**（D-Drive リポジトリから取得）。形状・UV・テクスチャのみ使用し、元マテリアル（URP Lit / UTS 残骸）は一切引き継がない | 指定 |
| C-5 | セルルック優先。PBR 系技術の優先度は大きく下げる | 指定 |
| C-7 | **Maya プレビューと Unity 描画のギャップを最小にする**（同じシェーダーコード・同じ色空間/トーンマップ・同じカメラ条件）| 指定 → [09_render_parity.md](09_render_parity.md) |
| C-6 | **D-Drive のリリース運用（SemVer + 互換性区分 + タグ + CHANGELOG）に倣った** バージョン管理ができること | 指定 → [06_release_versioning.md](06_release_versioning.md) |

### C-3 の具体的な意味（D-Drive 調査結果。2026-09-28 以降は「互換性として保つ条件」）

D-Drive (`com.ddrive.core` 1.3.0) のマテリアルは Unity の .mat を直接持たず、`MaterialData` アセットで表現される。

```
MaterialData
├─ Shader                       … 任意のシェーダー（D-Drive にトゥーンは存在しない → 新規作成）
├─ Common (MaterialCommon)      … どのシェーダーでも意味が共通のチャンネル
│    Albedo / AlbedoTint / Normal / NormalScale / Mask(R=Metal G=AO B=Detail A=Smooth)
│    Emission / EmissionColor / EmissionIntensity / Blend(Opaque|Cutout|Transparent) / Cutoff / DoubleSided
├─ Specific: ShaderParam[]      … シェーダー固有 (Property名 + ParamValue: Float/Int/Bool/Color/Vector/…/Object(Texture))
├─ RenderQueueOffset            … Blend 帯（Opaque 2000 / Cutout 2450 / Transparent 3000）からのオフセット
├─ RenderingLayerMask
└─ Anims: MaterialAnim[]        … Float / UV オフセットを ValueDef で常時駆動
```

したがって本ツールの出力は以下を満たす必要がある:

1. シェーダーのプロパティ名が D-Drive の命名規約 `MaterialCommonNaming` に沿う
   - 共通チャンネルは規約名（`_BaseMap` `_BaseColor` `_BumpMap` …）をそのまま使う
   - 固有パラメータは **予約名（共通チャンネル名と `_ZTest` `_Cull` `_ZWrite` `_QueueOffset` 等の描画ステート名）と衝突しない** 名前にする（衝突すると D-Drive が Common の値を Specific で上書きしたとして Conflict 扱いになる） → 本プロジェクトは **`_Toon` 接頭辞**で統一
2. ルックの値は **Common + Specific + RenderQueueOffset** に落とし込める形で持つ
3. 描画順の制御（眉を髪の上に等）は RenderQueueOffset と、Specific に置いたステンシル値で表現する
4. 表情等によるランタイム変化は `MaterialAnim` / `Mats.SetGlobalParam` / MaterialPropertyBlock で表現できる範囲に収める

## 4. 必須要件

| ID | 要件 | 対応仕様 |
|---|---|---|
| R-1 | **モデルセットアップが簡単**: 頭・髪・目などの部位をマテリアル単位で登録できる。自動推定 + 選択からの登録 | [05_maya_tool_spec.md](05_maya_tool_spec.md) §3 |
| R-2 | **部位に合わせたシェーダー/ルック調整**: 部位ロールごとのプリセットを初期値にし、部位単位・マテリアル単位で調整 | [05](05_maya_tool_spec.md) §4, [02](02_look_definition_spec.md) §4 |
| R-3 | **A/B 比較**: 2 案を即時切替・同一条件キャプチャで並べて比較・差分表示・採用 | [05](05_maya_tool_spec.md) §5 |
| R-4 | **プレビューが簡単**: ワンクリックで Toon 表示 ⇔ 元表示、カメラ（正面/3/4/横/後ろ）・ライト方向プリセット | [05](05_maya_tool_spec.md) §6 |
| R-5 | **エディターですぐ調整**: スライダー操作がビューポートに即時反映（setAttr のみ、シェーダー再コンパイル無し） | [05](05_maya_tool_spec.md) §4 |
| R-6 | **リリース運用**: ツール本体は SemVer + タグ + CHANGELOG(互換性)、ルックデータも版管理 | [06_release_versioning.md](06_release_versioning.md) |
| R-8 | **描画パリティ**: 同条件の Maya/Unity キャプチャの差が基準以内 | [09](09_render_parity.md) §5 |
| R-7 | **Maya MCP**: Claude Code から Maya を操作・検証できる | [07_maya_mcp_setup.md](07_maya_mcp_setup.md) |

## 5. 用語

| 用語 | 意味 |
|---|---|
| Look（ルック）定義 | 1 キャラクター分のルックデータ（`looks/<character>/look.json`）。唯一の真実 |
| 部位 (Part) | 名前付きのマテリアル集合（例: `face` = [face, mouth]）。ロールを 1 つ持つ |
| ロール (Role) | 部位の種類（face / hair / eye / brow …）。既定値プリセットと描画順の既定を決める |
| バリアント (Variant) | base に対する差分上書きの名前付きセット。A/B 比較の単位 |
| base | バリアント上書きの無い基本値 |
| パラメータ契約 | `_Toon*` パラメータの名前・型・範囲の定義。Maya プレビュー / Look / Unity の共通語彙 |
| プレビューシェーダー | Maya VP2 (DirectX 11) 用 dx11Shader (.fx)。Unity シェーダーと同じ `ToonCore.hlsl` を include する |
| 環境プロファイル | Unity の描画環境（色空間・トーンマップ・ライト・カメラ）を数値化した JSON。Maya プレビューが読み込む |
| アニメ的な嘘 | 物理的に正しくないが絵として伝わる処理（眉を髪の上に、顔だけ影を弱く 等） |
