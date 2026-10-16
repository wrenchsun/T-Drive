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
| ベースカラーテクスチャ | sRGB テクスチャ（ハードウェアでリニア化） | file ノード colorSpace = sRGB は付けるが、**dx11Shader はそれを見ずに生の値でサンプルする**（2026-10-16 に実測）ため、Maya ラッパー `TDriveToon.fx` の `SrgbToLinear` でリニア化する（ベースマップ・発光マップ） |
| マスク・法線系 | リニア（sRGB OFF） | colorSpace = Raw |
| マテリアルの色・ライト色 | インスペクター値（sRGB）をリニアへ変換してシェーダーへ | Look の色は sRGB 値で保存し、Maya ラッパーで同じ変換をしてから setAttr（2026-09-28 のパリティ確認で判明） |
| 頂点カラー | そのまま（変換なし） | そのまま（Color Set の値を変換しない） |
| HDR | ON | 浮動小数のまま計算し、最後にトーンマップ |
| トーンマップ | **InGame = Neutral**（SampleSceneProfile）。Title/Lobby 等 Volume の無いシーンは DefaultVolumeProfile = None | プロファイルの値に従い、シェーダー最終段で `ToonCore` の同じトーンマップ関数を適用。Maya のビュー変換は **Un-tone-mapped (sRGB)** に固定（ACES 等を掛けない） |
| 出力 | sRGB 表示 | 同上（Un-tone-mapped = リニア → sRGB のみ） |
| メインライト | InGame: Directional 強度 2・色温度 5000K・回転 (50, -30, 0) | **キャラクターライトはシーンライトと独立**（D-4）。Maya/Unity とも環境プロファイルの `characterLight` を使う。シーンライトは参考値として `sceneMainLight` に記録 |
| 環境光 | P0 の式では使わない | 同左。式に環境光を足す場合は ToonCore に入れ、プロファイルから SH/単色を渡す |
| カメラ | Cinemachine 縦 FOV **40°**・Near 0.1・Far 5000（Player.prefab。シーンの Camera 60° は Cinemachine が上書き） | カメラプリセットは **縦 FOV 指定**（Unity の Gate Fit=Vertical と同じ）で焦点距離を逆算。Near/Far もプロファイル値 |
| 単位 | 1 unit = 1 m（UnityChan FBX globalScale 0.01） | Maya は cm。**長さを持つパラメータは Unity 単位（m）で定義**し、Maya ラッパーでシーン単位へ換算する（`cmds.currentUnit` から。cm 固定にしない） |
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
| テクスチャの発色が薄い（UnityChan が公式より淡い。2026-10-16 デザイナー報告） | dx11Shader は file ノードの colorSpace（sRGB）を無視して生の値でサンプルし、ビュー変換でもう一度 sRGB に符号化していた（sRGB 128 の灰色 → 188、(200,60,60) → (229,133,133) と実測）。Look の色は Python 側でリニア化しているので、テクスチャの部分だけ淡くなる | `TDriveToon.fx` の `SrgbToLinear` でベースマップ・発光マップをリニア化（修正後は 128 → 128、(200,60,60) → (200,60,60)）。Raw の欄（マスク・法線・MatCap・髪ハイライト・顔影）には掛けない。なお環境プロファイル `ms2026_ingame` の Neutral トーンマップは Unity InGame と同じ意図の変更で、白 255 が 208 になる（公式 UnityChan はトーンマップ無し） |
| 画面上の線の太さが Unity と 1 px ずれる（線幅 5 / 9 / 13 px@1080p） | Python の `round()` は偶数丸め（2.5 → 2）、HLSL の `round` は 0.5 を 0 から遠い方へ（2.5 → 3） | `screen_line.line_radius` を `math.floor(x + 0.5)` に。docs/03 §10.2 に丸め規則を明記（C# の `Mathf.Round` も偶数丸めなので使わない）。2026-10-16 のシェーダーレビュー |
| MatCap のハイライトが上下逆（PNG 等の上から並ぶ画像） | 他のテクスチャにはある `<名前>FlipV` が MatCap だけ無く、`_set` が黙って何もしていなかった | `ToonMatCapMapFlipV` を追加し `FlipUV` を通す。同レビュー |
| スクリーンスペース輪郭線で「ベース色を混ぜる」を上げると線が Unity より暗い・くすむ | 持ち主の画素の色はトーンマップ済みなのに、混ぜた結果にもう一度 `Toon_Tonemap` を掛けていた | 線色だけトーンマップしてから混ぜる（docs/03 §10.4 の近似）。本筋はトーンマップを合成パスに移すこと（docs/tasks.md SR-3）。同レビュー |
| Maya がフリーズ | 描画中の dx11Shader に `dx11Shader -reload` | 使わない。`.fx` 更新時はプレビューノードを作り直す（`preview.reload_shader_file`） |
| float4 の色に setAttr できない | dx11Shader は `color1x4` を `<名前>RGB` + `<名前>A` に分ける | 子アトリビュートに個別に設定 |
| 頂点カラー・UV2 が届かない | 既定の取得元が `color:colorSet` / `uv:map3` | `Color0_Source = color:tdToonMask`、`TexCoord2_Source = uv:tdSmoothNormal` を設定 |
| スキン付きメッシュに書いた UV Set が見えない | 変形後に書くとヒストリ再評価で消える。Orig に書いた直後は `polyUVSet -q` が変形後シェイプの古い名前一覧を返す | 書き込みは Orig（中間オブジェクト）へ。存在確認は評価済みデータ（`MFnMesh.getUVSetNames`）で |
| FBX 書き出し後にシーンが戻らない | 並べ替えを Undo で戻そうとしたが `FBXExport` が Undo 履歴を消す | シーンを一時ファイルへ書き出し、別プロセスの mayapy で整形・書き出し（開いているシーンは触らない） |
| ツールのリロード後に画面上の線を触ると Maya が落ちる（python311.dll のアクセス違反） | Maya は登録した Render Override の Python オブジェクトを参照として保持しない。リロードで古いモジュールと一緒に解放され、Maya が解放済みのメモリを触った | リロード前に `screen_line.unregister()`。登録したオブジェクトはリロードの影響を受けない場所（`maya.api.OpenMayaRender` の属性）からも参照して解放させない |
| （再発防止の仕組み） | 上の 3 件はいずれも「Maya / Qt との境界」の約束違反 | `tdrive_toon/lifecycle.py`（keep_alive / on_reload / guarded / エラー表示）と `screen_line.set_param`。約束は CLAUDE.md、違反は `tests/test_ui_patterns.py` とスモークテストが検出 |
| Render Override で毎フレーム OverflowError / kInvalidParameter | `MFrameContext.kExcludeAll`（0xFFFF…FFFF）が Python の戻り値として符号付き long に収まらない。`round()` の int を float のパラメータに渡した | 戻り値は Windows の C long（32 ビット符号付き）に収める。除外は `0x7FFFFFFF` + `objectSetOverride` に空の選択（消去だけの操作）。シェーダーパラメータは型を合わせて渡す（float / int / bool） |
| Render Override の描画先が None になる / テクスチャが読めない | 解放し忘れた同名の描画先があると acquire が None。Python では `MRenderTargetAssignment` が効かず、サンプラー未設定だと結び付かない | 描画先の名前を登録ごとに固有に。`setParameter` に MRenderTarget を直接、サンプラーを明示 |
| Unity で頂点カラー・スムーズ法線が別のものになる | Unity は UV・頂点カラーを名前でなく順番で読む（頂点カラーは先頭のみ） | 書き出し時に頂点カラーを tdToonMask だけ、UV を map1 / 予備 / tdSmoothNormal の順にする |
| 画面上の線を使うと、HUD を表示しているパネルでキャラクターの上に巨大な半透明の板と「前」などの文字が重なり、白っぽく抜けて見える（2026-10-15 報告。実データ misoten_chara の front ビューで再現） | 板の正体は **ViewCube**。Override の UI パス（`kRenderNonShadedItems`）が ViewCube（マニピュレータ扱い）を描くとき、シーンの奥行きが入った自前の depth を描画先に渡していると、隅の小さい ViewCube に加えてビュー全体に巨大な板として描かれる（depth を渡さなければ隅だけ）。プレビューのパネルは HUD を消すので今まで見えなかった | UI パスは `objectTypeExclusions = kExcludeManipulators`、マニピュレータ（ギズモ・ViewCube）は `_ManipOp` が `out` だけ（depth なし）に描く。切り分けは `maya/mcp_scripts/diag_screen_line.py`（環境の出力 + Override あり / なし / HUD なしの 3 枚）。同じ調査で不透明パス・合成パスの出力アルファを 1 に揃えた（ベースマップの α を表示先に残さない） |
