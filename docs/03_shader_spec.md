# 03. シェーダー仕様（MS2026/Toon ・ Maya プレビュー共通）

式の実装は **`shaders/ToonCore.hlsl` の 1 本だけ**。Unity 側 `MS2026/Toon` と Maya プレビュー `maya/shaders/TDriveToon.fx`（dx11Shader）は
これを include する薄いラッパーで、同じパラメータ名を使う（[09_render_parity.md](09_render_parity.md) D-5）。
この文書が式の仕様であり、変更時は この文書 → ToonCore.hlsl → パリティテスト の順に行う。

対象は P0 技術（[04](04_technique_priority.md) T-01〜T-08）と、P1 / P2 のうちシェーダーで行うもの（T-09 / T-11 / T-12 / T-13 / T-18 / T-21 / T-22 / T-26 / T-27 / T-28 / T-30）。それ以外は §7.3 に予約名のみ定義する。

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

// P2 T-27 2 影: 影の中のさらに暗い部分（_ToonShade2Strength = 0 なら従来の 2 階調）
lit2   = lerp(1, smoothstep(_ToonShade2Threshold - _ToonShadeFeather,
                            _ToonShade2Threshold + _ToonShadeFeather, x), _ToonShade2Strength)
shadeC = lerp(base.rgb * _ToonShade2Color.rgb, base.rgb * _ToonShadeColor.rgb, lit2)
col    = lerp(shadeC, base.rgb, lit) * lightC

// P2 T-30 MatCap（ビュー空間の法線で球面テクスチャを引く。未設定 = 黒 = 何も足さない）
col   += _ToonMatCapMap(normalVS.xy * 0.5 + 0.5).rgb * _ToonMatCapStrength * lightC

// P1 T-13 リム（明側のみ）
rim    = pow(1 - saturate(dot(N, V)), _ToonRimPower) * _ToonRimStrength * lit
col   += _ToonRimColor.rgb * rim * lightC

// P1 T-12 髪ハイライト（テクスチャの帯を、カメラの上下の角度で縦にずらす。明側のみ）
huv    = uv + float2(0, _ToonHairHighlightShift * dot(V, up))   // up = ワールド上方向
col   += _ToonHairHighlightColor.rgb * _ToonHairHighlightMap(huv).r * lit * lightC   // マップ未設定 = 黒 = 出ない

tint   = (1 - mask.a) * _ToonTintStrength                 // A 黒 → 固定色（頬・耳・口内）
col    = lerp(col, col * _ToonTintColor.rgb, saturate(tint))

// P2 T-28 部位別の色補正（彩度・明るさ。1 = そのまま）
luma   = dot(col, (0.2126, 0.7152, 0.0722))
col    = lerp(luma, col, _ToonSaturation) * _ToonBrightness

alpha  = base.a                                            // Cutout は Cutoff で discard、Transparent はそのまま

col    = Tonemap(col, 環境プロファイルの tonemapping)     // None / Neutral（Unity URP と同じ曲線）。Unity ではポスト側で掛かるため
                                                           // ラッパーはプレビュー時のみ適用する
```

- 計算はすべて **リニア空間**（MS2026 は Linear カラースペース）。ベースマップは sRGB → リニア化済みの値を受け取る
- **Look 定義・パラメータ契約の色（Color 型）は sRGB 値**（Unity のインスペクター / `Material.SetColor` と同じ）。Unity は Linear 色空間でこれをリニアへ変換してシェーダーに渡すので、Maya ラッパーも同じ変換（`envmath.srgb_color_to_linear`、RGB のみ・α はそのまま）をしてから渡す。ライト色も同様

- 影色は **乗算色**。暗くするだけでなく色相をずらす（肌 → 赤紫寄り等）ことを前提にした既定値にする
- ライトの強度・環境光・受け影（シャドウマップ）は P0 では扱わない（T-16/T-17 で Unity 側に追加）

## 2.3 セルフシャドウ（P2 T-43、2026-09-29 追加。機能 `selfShadow`、既定オフ）

キャラクター自身が落とす影（前髪 → 額、腕 → 体など）を、ライトの影（シャドウマップ）から取り、**トゥーンの影として**足す。

```
atten = シャドウマップの比較（1 = 光が当たる、0 = 影）。PCF で数点の平均
s     = smoothstep(0.5 - _ToonShadeFeather, 0.5 + _ToonShadeFeather, atten)     // 境界をくっきり（通常の影と同じぼかし幅）
k     = _ToonShadowStrength * _ToonReceiveShadow * mask.b                       // B 黒（常に明）の所には落ちない
lit   = min(lit, lerp(1, s, k))                                                  // 通常の影と合成（影色は _ToonShadeColor）
```

- `_ToonReceiveShadow`（0〜1、既定 1）: その部位が影を受ける量。**顔のロールは 0**（T-16: 髪の影を顔に落とさない。固定影は頂点マスク R で描く）
- 影を落とすのは本体の面だけ（輪郭線の押し出しは落とさない）。**部位ごとに `_ToonCastShadow`（0/1、既定 1）で落とすかを選ぶ**（例: まつ毛・装飾は落とさない）。
  Maya は shadowPass で捨てる、Unity はマテリアルの ShadowCaster パスを止める（`Material.SetShaderPassEnabled`）
- 機能をオフにすると `_ToonReceiveShadow` と `_ToonCastShadow` は 0 で解決（そのマテリアルは影を受けも落としもしない）
- Unity: URP のメインライトのシャドウマップ（`GetMainLight(shadowCoord)` の `shadowAttenuation`）を `atten` に使う。Maya: VP2 のシャドウマップ（プレビューのキャラクターライトと同じ向きの影用ライト）。
  シャドウマップの解像度・バイアス・カスケードは環境で違うので、**Maya の表示は △（簡易）**

## 2.2 法線マップ・発光（Common、2026-09-28 追加。機能 `normalMap` / `emission`、既定オフ）

D-Drive MaterialCommon の `Normal` / `NormalScale` / `Emission` / `EmissionColor` / `EmissionIntensity` を、Unity（URP Lit）と同じ解釈で使う。

```
// 法線マップ（Toon_NormalFromMap）: Unity の UnpackNormalScale と同じ（+Y = OpenGL 形式、テクスチャはリニア）
nTS.xy = float2(tex.r * tex.a, tex.g) * 2 - 1   // Unity の UnpackNormalmapRGorAG と同じ（通常の画像は a = 1 で rg）
nTS.xy *= normalScale
nTS.z  = sqrt(saturate(1 - dot(nTS.xy, nTS.xy)))
N = normalize(T * nTS.x + B * nTS.y + N * nTS.z)   // B = cross(N, T) * tangent.w
// 以降の影・リム・MatCap・顔影はこの N を使う。輪郭線（§3）の押し出しは使わない

// 発光（Toon_Emission）: 影・ライト色・色補正の影響を受けない。トーンマップの前に足す
emission = emissionMap.rgb(sRGB → リニア) * linear(EmissionColor.rgb) * EmissionIntensity   // マップ無しは白
color += emission
```

- 既定（法線マップ無し・発光の強さ 0）で従来と同じ見た目（MINOR）
- 機能をオフにすると Common の値は既定値で解決する（出力の Common には既定値が入る。[11](11_features_and_shader_generation.md) §2）

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

- **方向依存の太さ（P2 T-26）**: 影側・下側の線を太くして重さを出す（どちらも 1 = 補正なし）
  ```
  litV = saturate(dot(n_world, L) * 0.5 + 0.5)            // 頂点での明るさ（ハーフランバート）
  px  *= lerp(_ToonOutlineShadowSide, 1, litV)             // 影側ほど _ToonOutlineShadowSide 倍
  px  *= lerp(1, _ToonOutlineBottom, saturate(-n_world.y)) // 下向きの面ほど _ToonOutlineBottom 倍
  ```

## 2.1 SDF 顔影マップ（P2 T-21）

顔の明暗を法線ではなく「ライトの水平角度」と「作画どおりの影の進み方を焼いたテクスチャ」で決める。

```
F, R   = 顔の正面・右（ワールド、水平）。キャラクター単位（characterSettings.faceShadow）/ Unity では頭のボーン
Lh     = normalize(L.xz)
angle  = acos(dot(Fh, Lh)) / π                    // 0 = 正面から照らす / 1 = 真後ろから
uvF    = dot(Rh, Lh) >= 0 ? uv : float2(1 - uv.x, uv.y)   // マップは「右から照らす」向きで作る。左からは左右反転
value  = _ToonFaceShadowMap(uvF).r                // その画素が明るいままでいられる最大の角度（0〜1）
litF   = smoothstep(angle - _ToonShadeFeather, angle + _ToonShadeFeather, value)
lit    = lerp(lit, lerp(1, litF, _ToonShadowStrength), _ToonFaceShadowWeight)   // 0 = 使わない（従来の法線の影）
```

- マップは 1 チャンネル（R）・リニア・なるべく 16bit。作り方は docs/05 §3.3（角度ごとの白黒マスク → SDF 合成）
- 顔の UV が左右対称である前提（非対称なら左右 2 枚が必要。今回は対象外）

## 3.2 奥行き圧縮（P2 T-22: 顔を「ぺったんこ」にして 2D 作画に寄せる）

```
posVS    = View * posWS ; pivotVS = View * pivotWS                // ビュー空間
k        = depthCompression * _ToonDepthCompressWeight           // キャラクター単位の量 × マテリアル単位の効かせ具合
posVS.z  = lerp(posVS.z, pivotVS.z, k)                           // 中心の奥行きへ寄せる（xy はそのまま → 遠近感が弱まる）
posWS    = ViewInverse * posVS
```

- `depthCompression`（0–1）と中心 `pivotWS` はキャラクター単位（Look の `characterSettings.depthCompression`）。Unity では `ToonCharacter` が頭のボーン位置を中心にしてグローバル / キャラクター定数で渡す。Maya プレビューでは効かせるマテリアル（重み > 0）のメッシュのバウンディングボックス中心
- `_ToonDepthCompressWeight`（マテリアル単位、既定 0 = 効かない）で「顔だけ」などを選ぶ
- 本体・アウトラインの両パスに適用（デプスオフセットの前）

## 3.1 デプスオフセット（P1 T-09: 眉・目を髪の上に）

```
posWS += normalize(cameraPosWS - posWS) * _ToonDepthOffset    // _ToonDepthOffset は m（Unity 単位）。Maya ラッパーはシーン単位へ換算（cm なら ×100、`environment.units_per_meter`）
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

### 7.2 Specific（P0 / P1 / P2）

| プロパティ | 型 | 既定 | 範囲 | 説明 | 技術 |
|---|---|---|---|---|---|
| `_ToonLightColorInfluence` | Float | 1 | 0–1 | ライト色の影響（0 = 受けない。P1、既定で従来と同じ） | T-11 |
| `_ToonDepthOffset` | Float | 0 | 0–0.2 | カメラ方向への寄せ量（**m**。Maya ラッパーでシーン単位へ換算）（P1） | T-09 |
| `_ToonRimColor` | Color | (1, 1, 1, 1) | | リム色（P1） | T-13 |
| `_ToonRimPower` | Float | 4 | 0.5–16 | リムの鋭さ（P1） | T-13 |
| `_ToonRimStrength` | Float | 0 | 0–1 | リムの強さ（0 = なし。P1） | T-13 |
| `_ToonHairHighlightMap` | Texture | black | | 髪ハイライトの帯（R を使う。未設定 = 出ない）（P1） | T-12 |
| `_ToonHairHighlightColor` | Color | (1, 1, 0.95, 1) | | 髪ハイライトの色（P1） | T-12 |
| `_ToonHairHighlightShift` | Float | 0 | -0.5–0.5 | カメラの上下に応じて帯を縦にずらす量（UV）（P1） | T-12 |
| `_ToonOutlineDistanceScale` | Float | 0 | 0–1 | 遠いほど線を細くする度合い（0 = 補正なし）（P1） | T-18 |
| `_ToonOutlineRefDistance` | Float | 2 | 0.1–20 | 線を細くし始める距離（m）（P1） | T-18 |
| `_ToonShade2Color` | Color | (0.6, 0.52, 0.72, 1) | | 2 影の乗算色（P2） | T-27 |
| `_ToonShade2Threshold` | Float | 0.25 | 0–1 | 2 影の境界（P2） | T-27 |
| `_ToonShade2Strength` | Float | 0 | 0–1 | 2 影の強さ（0 = 2 影なし）（P2） | T-27 |
| `_ToonSaturation` | Float | 1 | 0–2 | 彩度（1 = そのまま）（P2） | T-28 |
| `_ToonBrightness` | Float | 1 | 0–2 | 明るさ（1 = そのまま）（P2） | T-28 |
| `_ToonOutlineShadowSide` | Float | 1 | 0–3 | 影側の線の太さ倍率（P2） | T-26 |
| `_ToonOutlineBottom` | Float | 1 | 0–3 | 下向きの面の線の太さ倍率（P2） | T-26 |
| `_ToonMatCapMap` | Texture | black | | MatCap（球面）テクスチャ（未設定 = 足さない）（P2） | T-30 |
| `_ToonMatCapStrength` | Float | 0 | 0–1 | MatCap の強さ（P2） | T-30 |
| `_ToonDepthCompressWeight` | Float | 0 | 0–1 | 奥行き圧縮の効かせ具合（量はキャラクター単位 `depthCompression`）（P2） | T-22 |
| `_ToonFaceShadowMap` | Texture | black | | SDF 顔影マップ（R、リニア）（P2） | T-21 |
| `_ToonFaceShadowWeight` | Float | 0 | 0–1 | 顔影マップの効かせ具合（0 = 使わず法線の影）（P2） | T-21 |
| `_ToonShadeColor` | Color | (0.78, 0.72, 0.86, 1) | | 影の乗算色 | T-01 |
| `_ToonShadeThreshold` | Float | 0.5 | 0–1 | 影の境界 | T-01 |
| `_ToonShadeFeather` | Float | 0.02 | 0.001–0.5 | 境界のぼかし幅 | T-01 |
| `_ToonShadowStrength` | Float | 1.0 | 0–1 | 影の強さ（0 = 影なし） | T-04 |
| `_ToonReceiveShadow` | Float | 1.0 | 0–1 | セルフシャドウを受ける量（顔ロールは 0）（P2） | T-43 |
| `_ToonCastShadow` | Float | 1.0 | 0/1 | セルフシャドウを落とす（P2） | T-43 |
| `_ToonSeeThroughOutline` | Float | 0 | 0/1 | 髪と重なる所は輪郭線だけ（透かし線。オンなら手前に出さない）（P2） | T-44 |
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
| （なし。顔の向き F / R はマテリアルでなくキャラクター単位 `characterSettings.faceShadow`） | T-21 |

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

## 10. 画面上の線（P2 T-23 インナーライン / T-42 外側輪郭）

背面押し出し（§3）とは別に、**画面上のエッジ検出**で線を足す。式は ToonCore の `Toon_Line*` にだけ書き、
Maya（Render Override、4-7 / 4-8）と Unity（`ToonRendererFeature`、U-10 / U-17）が同じ関数を使う。

### 10.1 ToonId バッファ（RGBA16F、画面と同じ解像度）

キャラクターの**不透明・カットアウト**のマテリアルが本体パスと同時に書く（Maya = MRT の 2 枚目、Unity = ToonId パス）。

| ch | 内容 | 背景・Toon 以外 |
|---|---|---|
| R G | ビュー空間の法線（八面体エンコード `Toon_OctahedralEncode`、-1〜1） | 0 |
| B | **部位キー** = 部位番号 × 2 + 線フラグ（部位番号は 1 から。線フラグ = `innerLine.parts` に含まれる部位なら 1） | 0 |
| A | ビュー空間の奥行き（m）。**背面押し出しの輪郭線の画素は −奥行き** | 0 |

- 部位番号 = Look の部位名の昇順での番号（1 始まり）。同じ部位のマテリアルは同じ番号
- **透明（Transparent）のマテリアルは書かない**（線は出ない。後ろの部位の線はそのまま見える）
- 背面押し出しのアウトラインパスも**同じ部位キーで書く**（A = −奥行き）。書かないと輪郭線が他の部位を覆った所が「背景」に見え、外側輪郭が体の内側に出てしまう（2026-09-28 に確認）。A < 0 の画素は外形（§10.4）には含め、内側の線（§10.2）の判定には使わない。Unity は Outline パスで ToonId も書く

### 10.2 エッジの判定（`Toon_LineInner`）

画素 p と、上下左右に r 画素離れた 4 点 q を比べる。r = max(1, round(線幅[px@1080p] × 画面の高さ / 1080 / 2))（線は境界の両側に r ずつ出るので、見た目の太さ ≒ 線幅）。
次のどれかを満たす q が 1 つでもあれば、p に線を描く:

1. **部位の境界**: p・q とも Toon で、部位番号が違い、どちらかの線フラグが 1
2. **折れ目**: p・q が同じ部位（線フラグ 1）で、法線の角度が `TOON_LINE_CREASE_DEG`（60°）を超える
3. **奥行きの段差**: p・q が同じ部位（線フラグ 1）で、奥行きの差が近い方の奥行き × `TOON_LINE_DEPTH_REL`（0.03）を超える（髪の束・指の重なり）

### 10.3 合成

線の画素の色 = `innerLine.color`（sRGB → リニア）。本体と同じトーンマップを掛けて上書きする（アンチエイリアスなし。Unity も同じ）。
Maya ではトーンマップをシェーダー内で掛けているので線にも同じ `Toon_Tonemap` を掛ける。Unity は線を描いた後にポストでトーンマップが掛かる（結果は同じ）。

### 10.5 透かし線（T-44、`Toon_LineSeeThroughPair`、2026-09-30 追加）

部位ごとの `_ToonSeeThroughOutline`（0/1）がオンの部位（眉・目など）は、

1. 本体は**手前に出さない**（`_ToonDepthOffset` は使わない）。髪に隠れる所は隠れる
2. その部位だけを別の描画先（**透かしバッファ**。ToonId と同じ形式）に描く。他の物に遮られないので、隠れている所も含めた部位の形と奥行きが分かる
3. 合成で、透かしバッファの**部位の外形**（p が部位の内側、q が外側または別の部位）のうち、
   p で通常の ToonId が**別の面で、しかも手前にある**（奥行きの差が `characterSettings.seeThroughOutline.maxDistance` 以内。手など遠い物越しには出さない）画素に線を描く

```
Toon_LineSeeThroughPair(op, oq, mainP, maxDist):
  op.b > 1.5（p が透かしの部位）かつ |op.b - oq.b| > 0.5（外形）
  かつ mainP.b と op.b が違う（p で見えているのは別の面）
  かつ 0 < op.a - |mainP.a| <= maxDist（その面が手前、距離以内）
```

- 線幅（px@1080p）・色は `characterSettings.seeThroughOutline`（width / color / maxDistance）。部位の輪郭線（§3）とは別（輪郭線の太さ 0 の部位にも使える）
- 重なっていない所は通常の描画のまま（線は足さない）
- Maya: Render Override に「透かしの部位だけを描く」操作を足す（`objectSetOverride`）。Unity: `ToonRendererFeature` で透かしの部位のレンダラーだけを ToonId パスで別の RT に描く

### 10.4 外側輪郭（T-42、`Toon_LineOuterPair`）

p・q の片方だけが Toon（部位キー > 0）のとき線を描く（キャラクターの外形の内外両側に r ずつ）。設定は `characterSettings.screenOutline`（有無・線幅 px@1080p・色）。
内側の線と重なる画素は内側の線の色を優先する。背面押し出しの輪郭線（§3）と併用でき、置き換えるときは機能 `outline` をオフにする。

