# 11. 機能のオン/オフと、プロジェクト専用シェーダーの生成

2026-09-28 追加。

- ルックの機能（技術）を**キャラクターごとにオン/オフ**できるようにする
- オンの機能だけを使った、**プロジェクト（MS2026）のキャラクター専用に最適化したシェーダー**を Unity で生成して使う

## 1. 機能（Feature）

機能 = 技術（[04](04_technique_priority.md) の T-xx）と、それに属するパラメータ・Unity の仕組みを束ねた単位。定義の実体は `maya/scripts/tdrive_toon/features.py`。

| 機能 ID | 名前 | 技術 | 属するもの | 実現 | Maya プレビュー |
|---|---|---|---|---|---|
| `shade` | 2 階調影（必須） | T-01 T-02 T-04 | `_ToonShadeColor` `_ToonShadeThreshold` `_ToonShadeFeather` `_ToonShadowStrength` | Sh | ○ |
| `outline` | 輪郭線 | T-05 | `_ToonOutlineColor` `_ToonOutlineBaseMix` `_ToonOutlineWidth` + Outline パス | Sh | ○ |
| `outlineSmoothNormal` | 線のスムーズ法線 | T-06 | `_ToonOutlineSmoothNormal` + UV2 | Sh Me | ○ |
| `outlineDistance` | 距離で線を細く | T-18 | `_ToonOutlineDistanceScale` `_ToonOutlineRefDistance` | Sh | ○ |
| `outlineDirection` | 線の太さの方向依存 | T-26 | `_ToonOutlineShadowSide` `_ToonOutlineBottom` | Sh | ○ |
| `normalMap` | 法線マップ | — | `common.normal` `common.normalScale` | Sh | ○ |
| `emission` | 発光 | — | `common.emission` `common.emissionColor` `common.emissionIntensity` | Sh | ○ |
| `vertexMask` | 頂点カラーの Toon マスク | T-03 T-07 | 頂点カラー（COLOR） | Sh Me | ○ |
| `maskMap` | Toon マスクテクスチャ | T-19 | `_ToonMaskMap` | Sh | ○ |
| `tint` | 固定色（頬・耳・口内） | T-08 | `_ToonTintColor` `_ToonTintStrength` | Sh | ○ |
| `shade2` | 2 影 | T-27 | `_ToonShade2*` | Sh | ○ |
| `lightColorInfluence` | ライト色の影響 | T-11 | `_ToonLightColorInfluence` | Sh | ○ |
| `rim` | リム | T-13 | `_ToonRim*` | Sh | ○ |
| `hairHighlight` | 髪ハイライト | T-12 | `_ToonHairHighlight*` | Sh | ○ |
| `matCap` | MatCap | T-30 | `_ToonMatCap*` | Sh | ○ |
| `colorCorrect` | 色補正 | T-28 | `_ToonSaturation` `_ToonBrightness` | Sh | ○ |
| `depthOffset` | 手前に出す | T-09 | `_ToonDepthOffset` | Sh | ○ |
| `depthCompression` | 奥行き圧縮 | T-22 | `_ToonDepthCompressWeight` + `characterSettings.depthCompression` | Sh Co | ○ |
| `faceShadowSdf` | SDF 顔影マップ | T-21 | `_ToonFaceShadow*` + `characterSettings.faceShadow` | Sh Co | ○ |
| `lightStabilize` | ライトの安定化 | T-17 | `characterSettings.light` | Co | ○（プレビュータブで同じ式を掛ける。4-5） |
| `stencil` | 髪越し表示（ステンシル） | T-24 | `characterSettings.stencil` + ステンシル状態 | Sh Co | △（手前に出すで代用） |
| `innerLine` | 画面上のインナーライン | T-23 | `characterSettings.innerLine` + ToonId パス | RF | ○（Render Override で同じ判定式。4-7） |
| `screenOutline` | 画面上の外側輪郭 | T-42 | `characterSettings.screenOutline` + ToonId パス | RF | ○（Render Override。4-8） |
| `contactShadow` | 接地影 | T-29 | `characterSettings.contactShadow` | RF | △（足元の板に同じ式で描く。地面は無いので背景に落ちる。4-6） |
| `viewCorrection` | カメラ角度補正 | T-20 | `characterSettings.viewCorrection` + BlendShape | Me Co | ○ |
| `expressions` | 表情パラメータ | T-25 | `characterSettings.expressions` | Co | ○（プレビュー） |

- `shade` は必須（オフにできない）
- 機能を増やすときはこの表と `features.py` に追加する（互換性は MINOR）

## 2. Look 定義での持ち方

```jsonc
"features": { "outline": true, "rim": false, "hairHighlight": true, ... }   // キャラクター単位（base のみ。バリアントの対象外）
```

- 無い機能は**既定値**: P0 の機能（shade / outline / outlineSmoothNormal / vertexMask / tint）はオン、それ以外はオフ
- 古い Look を読み込んだときは、**値が既定値と違う（= 使われている）機能をオン**にして補う（`look.upgrade`）。見た目は変わらない

### オフの機能の解決規則（重要）

`resolve()`（プレビュー・Unity 出力・生成シェーダーの共通の入口）で、**オフの機能に属するパラメータは「効果なしの値」として解決する**。
効果なしの値は原則パラメータ契約の既定値で、既定値が効果ありのもの（輪郭線の線幅 1 など）だけ機能側で「オフのときの値」を定める（`outline`: `_ToonOutlineWidth = 0`、`outlineSmoothNormal`: `_ToonOutlineSmoothNormal = 0`）。

- 保存されている値は消さない（オンに戻すと元の調整値が復活する）
- 既定値は「その機能が無いのと同じ見た目」になる値（[06](06_release_versioning.md) の MINOR 規則で保証済み）。したがって
  **オフの機能 = 既定値で解決 = 生成シェーダーでコードを取り除いたもの** が画素単位で一致する
- Unity の機能（Co / RF）はオフならコンポーネント・Renderer Feature の処理をしない

## 3. プロジェクト専用シェーダーの生成（Unity）

```
looks/*/look.json（プロジェクトで使うキャラクター）
        │ 機能の和集合
        ▼
unity/com.tdrive.toon/ShaderTemplates/TDriveToon.shader.template  （全機能入りのテンプレート。機能ごとに //#feature <id> … //#end で囲む）
        │ 使わない機能のブロックを取り除く
        ▼
MS2026/Assets/_Project/Art/Shaders/Generated/TDriveToon_MS2026.shader   "TDrive/Toon_MS2026"
```

| 取り除くもの | 効果 |
|---|---|
| 使わない機能のコード（シェーダーの計算） | 命令数・テクスチャ読み込みが減る |
| 使わない機能のプロパティと CBUFFER の変数 | 定数バッファが小さくなる、インスペクターが簡潔になる |
| 使わない機能のパス（輪郭線オフなら Outline パス、インナーライン オフなら ToonId パス） | 描画回数が減る |

- **キーワード（shader_feature）は使わない**: プロジェクトで使う機能は 1 通りなので、分岐やバリアントを作らない 1 本のシェーダーにする（ビルド時間・メモリも減る）
- **D-Drive 互換は維持**: 共通チャンネル（`_BaseMap` 等）・描画ステートの名前はテンプレートどおり残す。生成シェーダーにも D-Drive 互換ブリッジ（[08](08_unity_port_plan.md) §6）の変換表を作る
- **検証**: キャラクターの Look がオンにしている機能を、生成シェーダーが持っているか（持っていなければ「再生成が必要」と警告）
- **パリティ**: 全機能入り（汎用 `TDrive/Toon`）と生成シェーダーで、同じ Look の見た目が一致することをテストする
- 生成器は Unity パッケージのエディタ拡張（C#）。テンプレートの区切り（`//#feature`）の規則は Python 側でも検証できるようにし、Maya の `.fx` と機能の区切りが一致しているかをテストする

## 4. Maya 側

| 機能 | 内容 |
|---|---|
| 機能タブ | 機能ごとのオン/オフ。「使用中（値が既定と違う）」の表示、「使っていない機能をオフにする」ボタン |
| ルックタブ | オフの機能のパラメータのグループを隠す（または「オフ」表示で編集不可） |
| キャラクタータブ | オフの機能の設定を隠す |
| プレビュー | `resolve()` 経由なので自動でオフ = 効果なしになる |
| Unity 出力 | オフの機能のパラメータは出力しない（Unity 側は既定値 = 効果なし）。`features` は出力に含める |

## 5. Unity でのみの機能の Maya プレビュー（2026-09-28 の質問への対応）

| 機能 | Maya での再現 | チケット |
|---|---|---|
| lightStabilize | プレビュータブの「ライト回転」に Unity と同じ平滑化・ヒステリシスを掛ける | 4-5 |
| contactShadow | 足元にプレビュー専用の板を置き、足のジョイントに追従する影を `Toon_ContactShadow` で描く | 4-6 ✅ |
| innerLine | Maya の Render Override（`screen_line.py`）で ToonId を MRT に書き、`Toon_LineInnerPair`（ToonCore、Unity と共有）でエッジ検出 | 4-7 ✅ |
| screenOutline（T-42） | 4-7 の Render Override のエッジ検出を共有して外形に線を出す（`Toon_LineOuterPair`） | 4-8 ✅ |
| stencil | 本物は再現しない（Maya の描画順を制御できない）。「手前に出す」で代用し、Unity で確認 | — |
