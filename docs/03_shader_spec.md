# 03. シェーダー仕様（MS2026/Toon ・ Maya プレビュー共通）

式の実装は **`shaders/ToonCore.hlsl` の 1 本だけ**。Unity 側 `MS2026/Toon` と Maya プレビュー `maya/shaders/TDriveToon.fx`（dx11Shader）は
これを include する薄いラッパーで、同じパラメータ名を使う（[09_render_parity.md](09_render_parity.md) D-5）。
この文書が式の仕様であり、変更時は この文書 → ToonCore.hlsl → パリティテスト の順に行う。

対象は P0 技術（[04](04_technique_priority.md) T-01〜T-08）と、P1 のうちシェーダーで行うもの（T-09 / T-11 / T-12 / T-13 / T-18）。P2 以降は §7.3 に予約名のみ定義する。

## 1. 入力

| 入力 | 内容 | Maya | Unity |
|---|---|---|---|
| ベース色 | `_BaseMap × _BaseColor` | file テクスチャ | TextureData（D-Drive Common.Albedo） |
| 法線 | メッシュ法線（T-10 で編集済みのもの） | そのまま | FBX 法線をインポート（再計算しない） |
| **頂点カラー** | Toon マスク（§4） | Color Set `tdToonMask` | `mesh.colors` |
| **UV2** | アウトライン用スムーズ法線（§5） | UV Set `tdSmoothNormal` | `TEXCOORD2` |
| キャラクターライト | 方向・色 | ツールのプレビューライト | グローバル `_ToonCharacterLightDir` / `_ToonCharacterLightColor`（§6） |

## 2. 本体（2 階調影）

```
mask   = vertexColor(RGBA) × _ToonMaskMap(RGBA)          // どちらも「白 = 何もしない」
N      = normalize(法線) ; L = キャラクターライト方向 ; V = 視線方向

hl     = dot(N, L) * 0.5 + 0.5                            // ハーフランバート
x      = saturate(hl * mask.r + (1 - mask.b))             // R 黒 → 常に影側 / B 黒 → 常に明側
lit    = smoothstep(_ToonShadeThreshold - _ToonShadeFeather,
                    _ToonShadeThreshold + _ToonShadeFeather, x)
lit    = lerp(1, lit, _ToonShadowStrength)                // 0 = 影を出さない（顔を弱く）

base   = _BaseMap(uv) * _BaseColor
lightC = lerp(1, lightColor, _ToonLightColorInfluence)   // P1 T-11: 0 = ライト色の影響を受けない（目のハイライト等）
col    = lerp(base.rgb * _ToonShadeColor.rgb, base.rgb, lit) * lightC

// P1 T-13 リム（明側のみ）
rim    = pow(1 - saturate(dot(N, V)), _ToonRimPower) * _ToonRimStrength * lit
col   += _ToonRimColor.rgb * rim * lightC

// P1 T-12 髪ハイライト（テクスチャの帯を、カメラの上下の角度で縦にずらす。明側のみ）
huv    = uv + float2(0, _ToonHairHighlightShift * dot(V, up))   // up = ワールド上方向
col   += _ToonHairHighlightColor.rgb * _ToonHairHighlightMap(huv).r * lit * lightC   // マップ未設定 = 黒 = 出ない

tint   = (1 - mask.a) * _ToonTintStrength                 // A 黒 → 固定色（頬・耳・口内）
col    = lerp(col, col * _ToonTintColor.rgb, saturate(tint))

alpha  = base.a                                            // Cutout は Cutoff で discard、Transparent はそのまま

col    = Tonemap(col, 環境プロファイルの tonemapping)     // None / Neutral（Unity URP と同じ曲線）。Unity ではポスト側で掛かるため
                                                           // ラッパーはプレビュー時のみ適用する
```

- 計算はすべて **リニア空間**（MS2026 は Linear カラースペース）。ベースマップは sRGB → リニア化済みの値を受け取る
- **Look 定義・パラメータ契約の色（Color 型）は sRGB 値**（Unity のインスペクター / `Material.SetColor` と同じ）。Unity は Linear 色空間でこれをリニアへ変換してシェーダーに渡すので、Maya ラッパーも同じ変換（`envmath.srgb_color_to_linear`、RGB のみ・α はそのまま）をしてから渡す。ライト色も同様

- 影色は **乗算色**。暗くするだけでなく色相をずらす（肌 → 赤紫寄り等）ことを前提にした既定値にする
- ライトの強度・環境光・受け影（シャドウマップ）は P0 では扱わない（T-16/T-17 で Unity 側に追加）

## 3. アウトライン（背面法線押し出し）

```
n      = _ToonOutlineSmoothNormal > 0.5 ? decodeSmoothNormal(UV2, TBN) : 法線
clip   = WorldViewProjection * pos
nClip  = (ViewProjection * float4(n_world, 0)).xy
dirPx  = normalize(nClip * screenSize / 2)                // 押し出し方向はピクセル空間で求める（横縦比で歪ませない）
px     = _ToonOutlineWidth * mask.g * screenSize.y / 1080 // 「1080p 換算の px」。解像度に比例、距離によらず一定
clip.xy += dirPx * px / (screenSize / 2) * clip.w          // ピクセル → NDC
描画: 背面のみ（Unity・Maya とも Cull Front）
色:   lerp(_ToonOutlineColor.rgb, _ToonOutlineColor.rgb * base.rgb, _ToonOutlineBaseMix)
```

- 線幅 0 の部位（目・眉等）は描画しない
- `screenSize` は描画先のピクセルサイズ（Unity: `_ScreenParams.xy`、Maya: `ViewportPixelSize`）。横縦比を無視すると横向きの輪郭で線が W/H 倍に太る（2026-09-28 の計測で判明）
- **距離補正（P1 T-18）**: 遠いほど細くする。`dist` はカメラまでの距離（m）
  ```
  k   = saturate(_ToonOutlineRefDistance / dist)          // 基準距離より近ければ 1（細くしない）
  px *= lerp(1, k, _ToonOutlineDistanceScale)             // 0 = 補正なし（画面上で常に一定）
  ```

## 3.1 デプスオフセット（P1 T-09: 眉・目を髪の上に）

```
posWS += normalize(cameraPosWS - posWS) * _ToonDepthOffset    // _ToonDepthOffset は m（Unity 単位）。Maya ラッパーは ×100
```

- 頂点を**視線方向に沿って**カメラへ寄せる。画面上の位置（投影）は変わらず、深度だけが手前になる → 前髪に隠れていた眉・目が見える
- 本体・アウトラインの両パスに適用する（線だけ置いて行かれないように）
- 寄せすぎると、横顔で眉が頭の外に出る／髪の外側まで手前に出る。部位ロール「眉」「目」の既定値は小さく（数 cm）に留め、キャラクターごとに A/B で決める
- Maya/Unity 同式で再現できる（ステンシル方式 T-24 は Unity のみ。docs/04 D-2）

## 4. 頂点カラー（Toon マスク）のチャンネル割当

**原則: 白 (1,1,1,1) = 何もしない。効かせたい所を黒く塗る。**
（頂点カラー未設定のメッシュ・未ペイント部分がそのまま中立になり、塗り忘れで破綻しない）

| ch | 名前 | 黒く塗ると | 主な用途 | 技術 |
|---|---|---|---|---|
| R | 影寄せ | 常に影になる | 首の下・目の周り・鼻の下・前髪の下・髪の内側 | T-03 |
| G | 線幅 | 線が細くなる / 消える | 頬・鼻・顎の線を消す、毛先を細く | T-07 |
| B | 明寄せ | 常に明るくなる | 顔の中心を常に明るく、鼻影を消す | T-03, T-14 |
| A | 固定色 | `_ToonTintColor` が乗る | 頬・耳の赤み、口内を暗く | T-08 |

- Maya の Color Set 名は `tdToonMask`（ツールが作成・初期化する）。FBX 出力時にこの Color Set を唯一の頂点カラーとして出す
- `_ToonMaskMap`（テクスチャ）は同じ割当で頂点カラーに **乗算**。未設定時は白テクスチャ

## 5. スムーズ法線（UV2）

- 位置が同じ頂点の法線を平均した「スムーズ法線」を、**頂点の接空間**で表し、八面体エンコードした 2 値を UV Set `tdSmoothNormal`（Unity では TEXCOORD2）に格納する
- 接空間で持つのはスキニング後も正しく追従させるため（UV はスキニングされない）
- Unity 側は FBX の接線をインポート（`Tangents: Import`）して Maya と同じ TBN を使う。接線の一致確認は Unity 移植チケットで行う

## 6. キャラクターライト

| 名前 | 型 | 設定者 | 説明 |
|---|---|---|---|
| `_ToonCharacterLightDir` | Vector (xyz, world, 表面→光源) | Unity ランタイム（`Mats.SetGlobalParam`）/ Maya はプレビューライト | シーンのメインライトとは独立 |
| `_ToonCharacterLightColor` | Color | 同上 | |

- **マテリアルのプロパティではない**（Look 定義にも MaterialData にも入らない）。D-4 参照
- MVP はグローバル 1 灯。キャラクター個別化・平滑化は T-17

## 7. パラメータ契約

Look 定義の `specific` のキー = Unity のプロパティ名 = MaterialData.Specific[].Property。
Maya の uniform 名は先頭の `_` を除いた名前。定義の実体は `maya/scripts/tdrive_toon/params.py`。

### 7.1 Common（D-Drive 規約名をそのまま使う）

`_BaseMap` `_BaseColor` `_BumpMap` `_BumpScale` `_EmissionMap` `_EmissionColor` と描画ステート
（`_Surface` `_Blend` `_SrcBlend` `_DstBlend` `_ZWrite` `_AlphaClip` `_Cutoff` `_Cull` `_QueueOffset` …）。
値は D-Drive の `MaterialCommonBinding` が Common から流し込むので、Look 定義では `common` に書く。

### 7.2 Specific（P0 / P1）

| プロパティ | 型 | 既定 | 範囲 | 説明 | 技術 |
|---|---|---|---|---|---|
| `_ToonLightColorInfluence` | Float | 1 | 0–1 | ライト色の影響（0 = 受けない。P1、既定で従来と同じ） | T-11 |
| `_ToonDepthOffset` | Float | 0 | 0–0.2 | カメラ方向への寄せ量（**m**。Maya ラッパーで ×100）（P1） | T-09 |
| `_ToonRimColor` | Color | (1, 1, 1, 1) | | リム色（P1） | T-13 |
| `_ToonRimPower` | Float | 4 | 0.5–16 | リムの鋭さ（P1） | T-13 |
| `_ToonRimStrength` | Float | 0 | 0–1 | リムの強さ（0 = なし。P1） | T-13 |
| `_ToonHairHighlightMap` | Texture | black | | 髪ハイライトの帯（R を使う。未設定 = 出ない）（P1） | T-12 |
| `_ToonHairHighlightColor` | Color | (1, 1, 0.95, 1) | | 髪ハイライトの色（P1） | T-12 |
| `_ToonHairHighlightShift` | Float | 0 | -0.5–0.5 | カメラの上下に応じて帯を縦にずらす量（UV）（P1） | T-12 |
| `_ToonOutlineDistanceScale` | Float | 0 | 0–1 | 遠いほど線を細くする度合い（0 = 補正なし）（P1） | T-18 |
| `_ToonOutlineRefDistance` | Float | 2 | 0.1–20 | 線を細くし始める距離（m）（P1） | T-18 |
| `_ToonShadeColor` | Color | (0.78, 0.72, 0.86, 1) | | 影の乗算色 | T-01 |
| `_ToonShadeThreshold` | Float | 0.5 | 0–1 | 影の境界 | T-01 |
| `_ToonShadeFeather` | Float | 0.02 | 0.001–0.5 | 境界のぼかし幅 | T-01 |
| `_ToonShadowStrength` | Float | 1.0 | 0–1 | 影の強さ（0 = 影なし） | T-04 |
| `_ToonMaskMap` | Texture | white | | 頂点カラーに乗算する Toon マスク | T-19 |
| `_ToonTintColor` | Color | (1, 0.6, 0.6, 1) | | 固定色（乗算） | T-08 |
| `_ToonTintStrength` | Float | 0 | 0–1 | 固定色の強さ | T-08 |
| `_ToonOutlineColor` | Color | (0.28, 0.2, 0.2, 1) | | 線色 | T-05 |
| `_ToonOutlineBaseMix` | Float | 0.5 | 0–1 | 線色にベース色を掛ける割合 | T-05 |
| `_ToonOutlineWidth` | Float | 1.0 | 0–10 | 線幅（1080p 換算 px） | T-05 |
| `_ToonOutlineSmoothNormal` | Float | 1 | 0/1 | UV2 のスムーズ法線を使う | T-06 |

### 7.3 予約名（P2 以降。名前だけ先に確定し、追加時は MINOR）

| プロパティ | 技術 |
|---|---|
| `_ToonFaceShadowMap` `_ToonFaceForward` `_ToonFaceRight` | T-21 |
| `_ToonShade2Color` `_ToonShade2Threshold` | T-27 |

### 7.4 命名規則

- 固有パラメータは必ず `_Toon` 接頭辞（D-Drive の共通チャンネル名・描画ステート名との衝突回避）
- 真偽値は Float(0/1)（D-Drive `ParamValue` と Unity の `[Toggle]` に合わせる）
- 名前の変更・削除は MAJOR（[06](06_release_versioning.md)）

## 8. Unity シェーダー構成（のちに実装）

| パス | LightMode | 内容 |
|---|---|---|
| ForwardLit | `UniversalForward` | §2 本体 |
| Outline | `SRPDefaultUnlit` | §3（Cull Front）。URP はマテリアル 1 つで追加パスを描く |
| ShadowCaster | `ShadowCaster` | 影を落とす（受け側の扱いは T-16） |
| DepthOnly / DepthNormals | 同名 | SSAO（MS2026 PC_Renderer に有効）・Forward+ 用 |

- Properties ブロックは `DDrive_Lit.shader` と同じ `[Header(Common)] / [Header(Render State)] / [Header(Specific)]` の区分で並べる
- SRP Batcher 互換（`UnityPerMaterial` CBUFFER に全マテリアルプロパティを入れる）

## 9. Maya プレビューの制限

一致させる項目・既知ギャップの一覧は [09_render_parity.md](09_render_parity.md) §2・§4 が正。要点:

| 項目 | Maya プレビュー | 備考 |
|---|---|---|
| 描画順（RenderQueueOffset） | 再現しない | T-09 のデプスオフセットは再現する |
| 受け影・SSAO・ポスト | なし | Unity でのみ確認 |
| 透明ソート | VP2 任せ | Transparent テクニックを使用 |
| 動作条件 | VP2 レンダリングエンジンが **DirectX 11** | dx11Shader は DirectX 11 でのみ描画される |
