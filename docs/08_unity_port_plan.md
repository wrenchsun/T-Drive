# 08. T-Drive for Unity（UPM パッケージ）仕様

> 2026-09-28 方針変更: **キャラクターのルックは T-Drive の Unity パッケージに一任し、D-Drive からは切り離す**。
> ただし D-Drive との**互換性は保つ**（シェーダーのプロパティ構造を D-Drive の命名規約と同じにし、MaterialData へ変換できる等）。
> シェーダー単体でできない表現は、このパッケージの**コンポーネント・Renderer Feature・エディタ拡張**で実装する。

> MS2026 は現在プロトタイプで、描画設定などは変わりうる。値に依存するものは環境プロファイル（[09](09_render_parity.md) §3）経由にし、コード・仕様に直書きしない。

## 1. 全体像

```
T-Drive リポジトリ
├─ maya/                                Maya ツール（Look Development）
├─ looks/<character>/look.json          Look 定義（唯一の真実。Maya・Unity の両方が読み書きする）
└─ unity/com.tdrive.toon/               ★ Unity パッケージ（本書）
    ├─ Shaders/ToonCore.hlsl            式の唯一の実装（Maya の .fx もここを include する）
    ├─ Shaders/TDriveToon.shader        URP シェーダー "TDrive/Toon"
    ├─ Runtime/                         コンポーネント・Renderer Feature・データ
    ├─ Editor/                          インポーター・ルックエディタ・パリティキャプチャ
    └─ Bridges/DDrive/                  D-Drive 互換（com.ddrive.core がある場合だけコンパイル）

MS2026（消費者）
└─ Packages/manifest.json: "com.tdrive.toon": "https://github.com/wrenchsun/T-Drive.git?path=unity/com.tdrive.toon#vX.Y.Z"
```

- 配布は D-Drive と同じく **git URL + タグ**（[06](06_release_versioning.md)）。ツール本体と Unity パッケージは同じバージョン番号で一緒にリリースする
- **D-Drive への依存はゼロ**（asmdef の参照なし）。D-Drive 互換機能は別 asmdef（§6）で、D-Drive が入っているプロジェクトでだけ有効になる

### D-6: ToonCore.hlsl をパッケージ内に移す

UPM の git パッケージは指定フォルダしか取り込まないため、式の唯一の実装 `ToonCore.hlsl` は `unity/com.tdrive.toon/Shaders/` に置く。
Maya の `TDriveToon.fx` はそこを相対パスで include する（1 本のまま。[09](09_render_parity.md) D-5 は維持）。

## 2. 実現方式の分類

シェーダー単体で無理なものは、次の層のどれかで実装する（[04](04_technique_priority.md) の各技術の「実現方式」列）。

| 層 | 何をするか | 例 |
|---|---|---|
| **シェーダー**（`TDrive/Toon`） | 1 マテリアルで完結する式 | 2 階調影・線・リム・髪ハイライト・手前に出す |
| **メッシュデータ**（FBX） | Maya で作って FBX で運ぶ | 頂点カラーのマスク・スムーズ法線・顔の法線・補正 BlendShape |
| **コンポーネント**（`ToonCharacter` ほか） | キャラクター単位の実行時処理 | キャラクターライト・影の安定化・表情パラメータ・ステンシル設定・カメラ角度補正・受け影の制御 |
| **Renderer Feature**（`ToonRendererFeature`） | 画面全体の追加パス | スクリーンスペースのインナーライン・接地影 |
| **エディタ拡張** | データの取り込み・調整・検証 | Look のインポート、Unity 上のルックエディタ、パリティキャプチャ、D-Drive 変換 |

## 3. ランタイム

### 3.1 シェーダー `TDrive/Toon`

- 式は [03](03_shader_spec.md)（`ToonCore.hlsl` を include）
- Properties は **D-Drive `DDrive_Lit.shader` と同じ区分・同じ共通名**で並べる: `[Header(Common)]`（`_BaseMap` `_BaseColor` `_BumpMap` …）/ `[Header(Render State)]`（`_Surface` `_Blend` `_Cull` `_ZWrite` `_QueueOffset` …）/ `[Header(Specific)]`（`_Toon*`）。これが D-Drive 互換（§6）の前提
- パス: ForwardLit（`UniversalForward`）/ Outline（`SRPDefaultUnlit`、Cull Front）/ ShadowCaster / DepthOnly / DepthNormals / **ToonId**（インナーライン用に部位 ID を書く。§3.4）
- ステンシル（T-24）: `_ToonStencilRef` `_ToonStencilComp` を Specific として持つ（値は `ToonCharacter` がロールから自動設定）
- SRP Batcher 互換を保つ。キャラクター単位の値の渡し方は Q-7

### 3.2 `ToonCharacter`（キャラクターのルート GameObject に付ける）

| 機能 | 技術 | 内容 |
|---|---|---|
| Look の適用 | — | `CharacterLook` アセット（§4）を参照し、元マテリアル名でレンダラーのスロットを探してマテリアルを割り当てる |
| キャラクターライト | T-02 / T-17 | シーンのメインライトとは別の方向・色。**平滑化・ヒステリシス**で影の境界のパカパカを防ぐ。`ToonLightRig`（シーンに 1 つ）の既定値をキャラクターごとに上書きできる |
| 受け影の制御 | T-16 | 顔ロールのマテリアルは自己影を受けない。固定影は頂点マスク R で表現 |
| ステンシル | T-24 | ロールから Ref / Comp を自動設定（髪: 書く、眉・目: 髪の上だけ描く） |
| 表情パラメータ | T-25 | `SetExpression("blush", 0.8)` 等の API。名前付きパラメータ → シェーダー値の対応表（`CharacterLook` の `expressions`）で駆動。Timeline / Animator からも動かせる |
| カメラ角度補正 | T-20 | カメラとの角度から補正 BlendShape（正面 / 3/4 / 横）のウェイトを計算して適用 |
| 奥行き圧縮 | T-22 | ビュー空間の奥行き圧縮率をキャラクター単位で渡す |
| バリアント切替 | — | 実行中に A/B を切り替え（調整用） |

### 3.3 `ToonLightRig`（シーンに 1 つ）

- 既定のキャラクターライト（方向・色・強度）と、影の安定化（平滑化時間・ヒステリシス角度）の既定値

### 3.4 `ToonRendererFeature`（URP の Renderer に追加）

| パス | 技術 | 内容 |
|---|---|---|
| ToonId | T-23 の前提 | キャラクターのマテリアルが部位 ID・線の種類を専用バッファに書く |
| Screen-space Inner Line | T-23 | Depth / Normal / 部位 ID の差からエッジ検出。部位ごとの線の有無・太さ・色は `CharacterLook` から |
| Contact Shadow | T-29 | 足元の接地影（足のボーン位置からの楕円影、またはスクリーンスペース） |

- 設定は Renderer Feature 本体（全体の既定）と `CharacterLook`（キャラクター単位）の 2 段

### 3.5 アニメーション補助（Phase A）

T-31〜T-39 はコンポーネント（例: `ToonFollowThrough` = 階層遅延、`ToonSquashStretch`、`ToonSmear`）として同パッケージに追加する。詳細仕様は Phase A 着手時に起こす。

## 4. データ

### 4.1 `CharacterLook`（ScriptableObject）

`looks/<character>/look.json`（[02](02_look_definition_spec.md)）と**同じ構造**を Unity のアセットにしたもの。

- `parts` / `materials`（common / specific / renderQueueOffset）/ `variants` は look.json と 1 対 1
- キャラクター単位の設定（§4.2）
- 生成したマテリアル（`TDrive/Toon`）への参照

### 4.2 look.json の拡張（schemaVersion 1 のまま追加 = MINOR）

項目・既定値・検証は [02](02_look_definition_spec.md) §4.1（`characterSettings` セクション）が正。

- Maya ツールでも同じ項目を編集できるようにする。プレビューできるものは Maya でも再現し、できないものは「Unity でのみ」と表示する（[09](09_render_parity.md) §4）

## 5. エディタ拡張

| 機能 | 内容 |
|---|---|
| Look インポーター | `look.json` を読み込んで `CharacterLook` と `TDrive/Toon` マテリアルを生成・更新（再インポートで Unity 側の手調整を上書きしない差分マージ） |
| FBX 取込設定 | T-Drive の FBX に対し、法線・接線 Import、頂点カラー・UV2 保持、`*_ToonMask` 等のリニア設定を自動で行う（AssetPostprocessor） |
| ルックエディタ（Unity） | Maya と同じ概念（部位 / ルック / A/B / プレビュー）で `CharacterLook` を調整。**look.json への書き戻し**で Maya と往復できる |
| パリティキャプチャ | 環境プロファイルと同じ条件でゲームビューを撮り、`tools/parity/compare.py` で Maya と比較 |
| 環境プロファイル書き出し | 現在のシーンの色空間・トーンマップ・ライト・カメラを `looks/_env/*.json` 形式で書き出す |
| 検証 | 必須プロパティ、ロールとステンシルの整合、Renderer Feature の有無、頂点カラー・UV2 の有無 |

## 6. D-Drive 互換（切り離したうえで引き継ぐもの）

`Bridges/DDrive/`（asmdef `TDrive.Toon.DDriveBridge`。`versionDefines` で `com.ddrive.core` があるときだけ有効）

| 機能 | 内容 |
|---|---|
| MaterialData への変換 | `CharacterLook` のマテリアルを D-Drive `MaterialData`（Common / Specific / RenderQueueOffset）として書き出す（従来の materialdata.json の役割） |
| シェーダー変換表 | D-Drive の `ShaderConversionTable` に `TDrive/Toon ⇄ DDrive/Lit` の対応を提供（共通チャンネルはそのまま、`_Toon*` は破棄または対応付け） |
| 命名規約の検証 | `TDrive/Toon` のプロパティが D-Drive `MaterialCommonNaming` の共通名・描画ステート名と衝突しないことを検証 |

- 互換の前提（[03](03_shader_spec.md) §7）: 共通チャンネルは D-Drive の規約名、固有パラメータは `_Toon` 接頭辞、Blend 帯（2000 / 2450 / 3000）+ RenderQueueOffset の考え方も同じ
- ランタイムは D-Drive を使わない。D-Drive の `ModelData` から T-Drive のキャラクター Prefab を参照するのは自由（Prefab に `ToonCharacter` が付いていれば動く）

## 7. 事前に決めること

| # | 事項 | 状態 |
|---|---|---|
| Q-1 | シェーダーの置き場所・名前 | **解決（2026-09-28）**: T-Drive パッケージの `TDrive/Toon` |
| Q-2 | マスク等のテクスチャ規約 | T-Drive の AssetPostprocessor で `*_ToonMask` 等をリニアに設定 |
| Q-3 | Renderer Feature の追加 | **方針（2026-09-28）**: T-Drive の `ToonRendererFeature` を MS2026 の PC_Renderer に追加（MS2026 側で 1 回設定） |
| Q-4 | UnityChan の扱い | テスト素体。ゲームには入れない |
| Q-5 | 本番シーンのトーンマップ | **解決（2026-09-28）**: InGame は Neutral |
| Q-6 | Unity パッケージの開発・検証場所 | 案: 本リポジトリの `unity/com.tdrive.toon/` + 検証用 Unity プロジェクト `unity/TDriveSandbox/`（MS2026 と同じ 6000.3.13f1 / URP 17.3）。MS2026 へは git URL で入れる |
| Q-7 | キャラクター単位の値の渡し方 | 案: キャラクター ID ごとの StructuredBuffer（SRP Batcher を崩さない）。実装時に MaterialPropertyBlock 方式と性能比較 |
