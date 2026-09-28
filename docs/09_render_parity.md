# 09. 描画パリティ（Maya プレビュー ⇔ Unity）

**方針: Maya のプレビューは可能な限り Unity（MS2026）の描画に合わせ、環境移行による見た目のギャップを最小にする。**
「Maya で調整した見た目が、そのまま Unity で出る」ことをツールの品質基準とし、差は測って管理する。

## 1. 基本戦略

| # | 戦略 | 内容 |
|---|---|---|
| S-1 | **シェーダーコードを共有する** | 式の本体を `shaders/ToonCore.hlsl`（純粋な HLSL 関数のみ）に 1 本化し、Unity の `MS2026/Toon.shader` と Maya の `TDriveToon.fx`（dx11Shader）の両方が `#include` する。二重実装による式のズレを構造的に無くす |
| S-2 | **Unity の描画環境を数値で持ち込む** | 色空間・トーンマップ・ライト・カメラ FOV などを「Unity 環境プロファイル」（JSON）として Unity から書き出し、Maya プレビューはそれを読んで再現する |
| S-3 | **差を測る** | 同一カメラ・ライト・解像度で Unity と Maya のキャプチャを撮り、画素差分で一致度を検証する（パリティテスト） |
| S-4 | **再現できない差は明示する** | §4 の既知ギャップ一覧に載せ、プレビュー UI にも「Unity でのみ確認」と表示する |

### D-5: Maya プレビューは dx11Shader（HLSL）+ Viewport 2.0 DirectX 11 とする

当初案の GLSLShader（.ogsfx）は Unity の HLSL と式を二重に書く必要があるため廃止する。

| 観点 | dx11Shader (.fx / HLSL) | GLSLShader (.ogsfx) |
|---|---|---|
| Unity とのコード共有 | **可能**（同じ HLSL を include） | 不可（GLSL へ手移植） |
| 動作環境 | Windows / VP2 = DirectX 11 | 全 OS / VP2 = OpenGL Core |
| マルチパス（アウトライン） | 可 | 可 |

制作環境は Windows のみのため、DirectX 11 前提で問題ない。VP2 のレンダリングエンジンが DirectX 11 でない場合、ツールは起動時に警告する。

`ToonCore.hlsl` の制約（Unity・Maya 両方でコンパイルできるための規則）:

- テクスチャのサンプリング・行列・頂点入力は **含めない**。サンプル済みの値・変換済みのベクトルを引数で受け取る純関数だけを置く
- 型は `float`/`float2..4`/`float3x3` のみ（`half` は使わない。Unity 側でも精度差を出さない）
- Unity のマクロ（`TEXTURE2D` 等）・Maya のセマンティクスは各ラッパー側に書く

```
shaders/ToonCore.hlsl            ← 式の唯一の実装（03_shader_spec.md の式）
   ├─ maya/shaders/TDriveToon.fx          (dx11Shader ラッパー: uniform・サンプリング・パス)
   └─ MS2026/…/Toon/MS2026_Toon.shader    (URP ラッパー: CBUFFER・サンプリング・パス)  ← U-2 でコピー or サブモジュール
```

### MS2026 はプロトタイプ（値は変わる前提）

MS2026 自体が開発中で、色空間以外（トーンマップ・ライト・カメラ・ポスト・解像度）は今後変わりうる。そのため:

- **ツールのコードに MS2026 の値を書かない**。値はすべて環境プロファイル（データ）に置き、プロファイルを差し替えれば追従する
- ツール側は URP の選択肢を網羅的に扱う（トーンマップ None / Neutral / ACES、任意の FOV・解像度）。未対応の値（現状 ACES）は警告を出してプレビュー上「要確認」と表示する
- プロファイルには取得元と日付を残す。U-0 以降は Unity 側の書き出しで更新し、**手入力値は暫定**とする
- 下表の「Unity」列は 2026-09-28 時点のスナップショット。変わったら表とプロファイルを同時に更新する

## 2. 一致させる項目（パリティ表）

| 項目 | Unity（MS2026、2026-09-28 調査） | Maya プレビューでの合わせ方 |
|---|---|---|
| 色空間 | **Linear**（`m_ActiveColorSpace: 1`） | OCIO 有効・レンダリング空間 scene-linear Rec.709-sRGB。シェーダー内の計算はすべてリニア |
| ベースカラーテクスチャ | sRGB テクスチャ（ハードウェアでリニア化） | file ノード colorSpace = sRGB（VP2 がリニア化） |
| マスク・法線系 | リニア（sRGB OFF） | colorSpace = Raw |
| マテリアルの色・ライト色 | インスペクター値（sRGB）をリニアへ変換してシェーダーへ | Look の色は sRGB 値で保存し、Maya ラッパーで同じ変換をしてから setAttr（2026-09-28 のパリティ確認で判明） |
| 頂点カラー | そのまま（変換なし） | そのまま（Color Set の値を変換しない） |
| HDR | ON | 浮動小数のまま計算し、最後にトーンマップ |
| トーンマップ | **InGame = Neutral**（SampleSceneProfile）。Title/Lobby 等 Volume の無いシーンは DefaultVolumeProfile = None | プロファイルの値に従い、シェーダー最終段で `ToonCore` の同じトーンマップ関数を適用。Maya のビュー変換は **Un-tone-mapped (sRGB)** に固定（ACES 等を掛けない） |
| 出力 | sRGB 表示 | 同上（Un-tone-mapped = リニア → sRGB のみ） |
| メインライト | InGame: Directional 強度 2・色温度 5000K・回転 (50, -30, 0) | **キャラクターライトはシーンライトと独立**（D-4）。Maya/Unity とも環境プロファイルの `characterLight` を使う。シーンライトは参考値として `sceneMainLight` に記録 |
| 環境光 | P0 の式では使わない | 同左。式に環境光を足す場合は ToonCore に入れ、プロファイルから SH/単色を渡す |
| カメラ | Cinemachine 縦 FOV **40°**・Near 0.1・Far 5000（Player.prefab。シーンの Camera 60° は Cinemachine が上書き） | カメラプリセットは **縦 FOV 指定**（Unity の Gate Fit=Vertical と同じ）で焦点距離を逆算。Near/Far もプロファイル値 |
| 単位 | 1 unit = 1 m（UnityChan FBX globalScale 0.01） | Maya は cm。**長さを持つパラメータは Unity 単位（m）で定義**し、Maya ラッパーで ×100 する |
| 解像度・線幅 | 画面高さ基準 | 線幅は「画面高さに対する割合」で定義済み（[03](03_shader_spec.md) §3）なので解像度非依存で一致。キャプチャは 1920×1080 に統一 |
| アンチエイリアス | MSAA なし（`m_MSAA: 1`）、ポスト AA は要確認 | パリティキャプチャ時は VP2 のマルチサンプルを OFF |
| 法線・接線 | FBX の法線/接線を Import | FBX 出力時に法線・接線（MikkTSpace）を書き出す。Maya 側プレビューも同じ接線を使う |
| ポスト | InGame は Bloom・Vignette が有効 | 再現しない（既知ギャップ）。パリティキャプチャは Unity 側で Bloom/Vignette を切って撮る |
| 背面カリング | `_Cull` = Back（DoubleSided で Off） | シェイプの Backface Culling をマテリアルの doubleSided に合わせて設定 |

## 3. Unity 環境プロファイル

`looks/_env/<profile>.json`（例: `ms2026_ingame.json`）。Unity 側の小さなエディタ拡張で現在のシーンから書き出す（U チケット）。書き出せるようになるまでは手入力の既定値を使う。

実例: [looks/_env/ms2026_ingame.json](../looks/_env/ms2026_ingame.json)

| キー | 内容 | 使う側 |
|---|---|---|
| `colorSpace` | `Linear` のみ対応 | 検証のみ |
| `tonemapping` | `None` / `Neutral` | Maya ラッパーが最終段で ToonCore の同関数を適用 |
| `characterLight` | 色・強度・回転（Unity のオイラー角、度） | Maya プレビューライト / Unity のキャラクターライト既定値（D-4） |
| `sceneMainLight` / `ambientSkyColor` | シーンのライト（参考値） | 現状未使用。環境光を式に入れる場合に使う |
| `camera` | 縦 FOV・Near・Far（m） | Maya カメラプリセット |
| `post` | 有効なポスト | 既知ギャップの表示用 |
| `captureSize` | パリティキャプチャ解像度 | 両方 |

- プレビュータブで「環境」を選ぶと Maya のライト・カメラ・トーンマップ・ビュー変換がこの値に揃う
- キャプチャのファイル名に環境名を含め、A/B・パリティ比較の条件を必ず揃える

## 4. 既知のギャップ（再現しないもの）

| 項目 | 理由 | 扱い |
|---|---|---|
| 描画順（RenderQueueOffset）・ステンシル | VP2 の描画順を制御できない | T-09 はデプスオフセット方式で再現（[04](04_technique_priority.md) D-2）。ステンシル方式（T-24）は Unity のみ |
| 受け影（シャドウマップ）・SSAO | Unity のシャドウ実装を再現しない | P0 の式は受け影を使わない。T-16 以降は「Unity でのみ確認」 |
| ポストプロセス（Bloom 等） | 画面全体の処理 | 対象外（T-41） |
| 透明のソート | VP2 任せ | 目・頬など重なりが少ない部位に限定 |
| キャラクターライトの平滑化（T-17） | ランタイムの時間処理 | Maya は瞬時値。ライト回転再生で境界の動きだけ確認 |

## 5. パリティテスト

| 項目 | 仕様 |
|---|---|
| 入力 | 同一 Look・同一環境プロファイル・同一カメラ（正面/3/4/横）・同一ポーズ（バインドポーズ） |
| キャプチャ | Unity: エディタ拡張でゲームビューを 1920×1080 PNG / Maya: `session.capture_parity()` |
| 比較 | `tools/parity/compare.py`: キャラクター画素（背景マスク外）の平均絶対差・最大差・差分ヒートマップ画像を出力 |
| 合格基準（初期値） | 平均絶対差 ≤ 2/255、アウトライン縁 ±1px を除いた最大差 ≤ 8/255 |
| 実施タイミング | シェーダー式・ToonCore を変更したとき、リリース前（[06](06_release_versioning.md) §5 に追加） |

## 6. 実装メモ（Maya dx11Shader の落とし穴、2026-09-28 の実機確認で判明）

| 症状 | 原因 | 対策 |
|---|---|---|
| dx11Shader にテクニック・アトリビュートが出ない | VP2 が OpenGL のまま | レンダリングエンジンを DirectX 11 にして Maya 再起動 |
| Toon 表示にならず既定のスムースシェーディングになる | ビューポートのテクスチャ表示 OFF | プレビュー有効化時にモデルパネルのテクスチャ表示を ON にする |
| 部位ごとにテクスチャの 1 色でくすんで見える | `overridesDrawState = true` では DirectX 既定（時計回り = 表面）になり、カリングが逆転してアウトラインが表面を覆っていた | ラスタライザステートに `FrontCounterClockwise = true` |
| 線色・影色が想定より明るい | Unity は Color を sRGB → リニア変換してから渡すが、Maya ではリニアのまま渡していた | Look の色は sRGB 値。Maya ラッパーで `srgb_color_to_linear` してから setAttr |
| Maya がフリーズ | 描画中の dx11Shader に `dx11Shader -reload` | 使わない。`.fx` 更新時はプレビューノードを作り直す（`preview.reload_shader_file`） |
| float4 の色に setAttr できない | dx11Shader は `color1x4` を `<名前>RGB` + `<名前>A` に分ける | 子アトリビュートに個別に設定 |
| 頂点カラー・UV2 が届かない | 既定の取得元が `color:colorSet` / `uv:map3` | `Color0_Source = color:tdToonMask`、`TexCoord2_Source = uv:tdSmoothNormal` を設定 |
