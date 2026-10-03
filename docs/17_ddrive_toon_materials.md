# 17. D-Drive では扱いにくい Toon マテリアル — 解決法と D-Drive 側の推奨変更

- 作成: 2026-10-03。調査対象: `C:\Users\yamag\wrench\unity\D-Drive`（com.ddrive.core v1.3.1、HEAD `9f40cbb`）。**読み取りのみ**（文書とソースを読んだ結果。コンパイル・実行はしていない。推測は「推測」と明記）
- 前提: キャラクターのルックは T-Drive for Unity（`com.tdrive.toon`）に一任し、D-Drive とは互換ブリッジでつなぐ（2026-09-28 の方針、[08](08_unity_port_plan.md) §6）
- FacialController 側の同じ整理は [16](16_ddrive_changes_for_facial.md)。Unity 側の実装チケットは [tasks.md](tasks.md) Phase U

> **2026-10-03 追記: D-Drive 側で受け入れ済み。** D-Drive の main（`00028ba`、PR #86）で、チケット 7-8 は「T-Drive 版 FacialController（`com.tdrive.facial`）を使う。D-Drive 内に Facial 種別は作らない」に書き換えられ、
> 本書と [16](16_ddrive_changes_for_facial.md) の提案は D-Drive のチケット **FC-0〜FC-20**（設計は D-Drive の `docs/51_tdrive_integration.md`、一覧は `docs/11_tasks.md` の FC 節）として起票された。実装は D-Drive 側で未着手（文書のみ）。
> 番号の対応: **C-1〜C-9 → FC-1〜FC-9**、**doc17 の M-1〜M-9 → FC-11〜FC-19**（D-Drive 既存の M チケットと番号が衝突するため）、FC-10 = 「変更なしで動く」前提（§3 の A-1〜A-9 など）を D-Drive のテストで固定、FC-20 = `FC_` 接頭辞の予約とモデル取り込みが名前・ボーンを保つことの確認。
> Unity 側の確認は、D-Drive のリポジトリのローカルブランチ `tdrive-facial`（push しない）に `com.tdrive.facial` をローカルパッケージとして入れて行う。

## 1. 結論

| 区分 | 内容 |
|---|---|
| **そのまま載るもの** | `_Toon` 接頭辞のパラメータ（数値・色・テクスチャ・0/1）、Blend 帯 + RenderQueueOffset、背面押し出しの輪郭線パス。D-Drive の MaterialData（Common / Specific / RenderQueueOffset）にそのまま入り、実行時に適用される |
| **T-Drive 側で解決するもの**（D-Drive は変更不要） | キャラクター単位の設定、Renderer Feature が要る線、機能の組み合わせごとの専用シェーダー、頂点カラー・UV に入れたデータ、A/B、キャラクターごとの値、マスクのリニア取り込み（§3） |
| **気を付けないと壊れるもの（罠）** | D-Drive の自動処理が Toon のマテリアルを Lit に置き換える・マスクを sRGB にする、など 5 件（§4）。運用の決まりで避けられる |
| **D-Drive 側に実装したほうがいい機能** | 必須ではないが、あると回避策が要らなくなるもの 9 件（§5）。優先が高いのは M-1（パスの有効 / 無効）と M-2（スポーン時の通知） |

## 2. D-Drive のマテリアルの仕組み（調査結果の要約）

| もの | 中身 |
|---|---|
| `MaterialData` | `Shader`、`Common`（Albedo・Normal・Mask・Emission・Blend・Cutoff・DoubleSided）、`Specific`（`ShaderParam[]` = プロパティ名 + 値）、`RenderQueueOffset`、`RenderingLayerMask`、`Anims`（`MaterialAnim[]`）、`SourceMaterial`（再取り込みの照合キー） |
| 実行時の Material | `MaterialManager` が MaterialData 1 つにつき **共有の Material を 1 つ** `new Material(shader)` で作り、Common → Specific → renderQueue の順に書く。モデルのスポーン時に `ModelData.Slots` の各スロットへ差し替える |
| Specific で使える型 | Float / Int / Bool（0/1 の Float として）/ Color / Vector / テクスチャ（直接参照）。シェーダーに無いプロパティは飛ばす |
| キーワード・描画ステート | Common が決めるものだけ（`_NORMALMAP` `_EMISSION` `_ALPHATEST_ON` など / `_Surface` `_Blend` `_ZWrite` `_Cull`（0 か 2）など）。**Specific からキーワード・パスの有効 / 無効・ステンシル・ZTest は指定できない** |
| インスタンスごとの値 | 無い（MaterialPropertyBlock はモデルでは使っていない）。インスタンス単位でできるのは `Models.SetMaterial(handle, slot, id)` でマテリアルごと差し替えることだけ |
| `MaterialAnim` | 時間で動くだけ（外から 0〜1 の値を渡す口は無い）。共有 Material に書くので、同じマテリアルの全インスタンスが一緒に変わる |
| 予約名 | `MaterialCommonNaming` の共通名・描画ステート名以外はすべて Specific 扱い → **`_Toon*` は衝突しない** |
| Maya からの取り込み | FBX から Unity が作った Material を読んで MaterialData を作る（Maya のアトリビュートや外部の JSON は読まない）。シェーダーは「プロファイルの指定 → 元が `DDrive/` で始まればそれ → `DDrive/Lit`」 |
| テクスチャの取り込み規則 | `Assets/SourceAssets`・`Assets/GameData` の下で、名前の規則（`_N` 法線 / `_M` マスク（リニア）/ `_E` / 接頭辞 `T_` は sRGB …）を**毎回の取り込みで強制** |
| レンダラー・ポストエフェクト | D-Drive は URP の Renderer・Renderer Feature・トーンマップを持たない / 縛らない（URP が有効かだけ確認） |
| モデルのメッシュ設定 | 触らない（頂点カラー・UV・法線 / 接線の取り込み設定に規則なし） |
| バリアント・キャラクター設定 | 無い（予定の `AssetVariantSet`（7-7）は品質 / プラットフォーム別で、ルックの A/B とは別） |

## 3. 扱いにくいものと、T-Drive 側の解決法

| # | Toon で必要なもの | D-Drive で扱いにくい理由 | T-Drive 側の解決法（D-Drive は変更不要） |
|---|---|---|---|
| 1 | `_Toon*` のパラメータ（数値・色・テクスチャ・0/1） | 扱える（Specific にそのまま入る）。ただしスライダーの範囲・チェックボックスの情報は D-Drive に残らない | 範囲・表示名は T-Drive のパラメータ契約（`params.py` → Unity の `CharacterLook` / ルックエディタ）が持つ。D-Drive 側は値の入れ物としてだけ使う |
| 2 | **部位ごとの機能のオン / オフ → 組み合わせごとの専用シェーダー**（[11](11_features_and_shader_generation.md)） | D-Drive に「機能」の考えが無い。Specific からキーワードも切り替えられない | T-Drive が組み合わせごとにシェーダーを生成し、ブリッジが `MaterialData.Shader` にそのシェーダーを指定する。生成シェーダーに無い `_Toon*` は Specific から外して書き出す（残すと D-Drive の検証が警告を出す） |
| 3 | **キャラクター単位の設定**（影の安定化・ステンシル・インナーライン・外側輪郭・透かし線・接地影・奥行き圧縮の中心・顔影の軸・カメラ角度補正・表情パラメータ） | マテリアル単位のデータしか無く、置き場所が無い | `CharacterLook`（ScriptableObject）に持ち、キャラクターの Prefab に付けた `ToonCharacter` が実行時に配る（[08](08_unity_port_plan.md) §3.2・§4.1）。D-Drive の `ModelData.Prefab` にこのコンポーネントが付いていれば動く |
| 4 | **Renderer Feature が要る表現**（ToonId の描画先・画面上の線・透かし線・スクリーンスペースの輪郭線・接地影） | D-Drive は Renderer に関与しない（邪魔もしないが、必要な Feature が入っているかも見ない） | プロジェクトの Renderer に `ToonRendererFeature` を 1 回追加（MS2026 側の設定）。入っていないときは T-Drive の検証（D-Drive の検証の仕組み `IValidator` に載せられる）が知らせる |
| 5 | **頂点カラーのマスク・UV に入れたスムーズ法線** | D-Drive に規則が無い = Unity の既定のまま（接線は再計算、ライトマップ UV を作ると UV が上書きされ得る: 推測） | T-Drive の取り込み処理（AssetPostprocessor）が、Look のあるモデルの取り込み設定（接線 = 取り込む、頂点カラー・UV を保持、ライトマップ UV を作らない）を決める |
| 6 | **A/B バリアント** | バリアントの考えが無い | 採用したバリアントを base に確定して書き出す（今の方式）。Unity 上で切り替えて比べたい場合は T-Drive 側（`CharacterLook` のバリアント → `ToonCharacter` で切り替え） |
| 7 | **キャラクターごと・インスタンスごとの値**（表情パラメータ = 名前付きの 0〜1 → `_Toon*` の値、キャラクターライト、顔の軸） | インスタンスごとの上書きが無い。`MaterialAnim` は時間駆動だけで、共有 Material を書くので全員が一緒に変わる | T-Drive 側でキャラクターごとの値を渡す（[08](08_unity_port_plan.md) Q-7: キャラクター ID ごとのバッファ、または MaterialPropertyBlock）。D-Drive の `MaterialAnim` は使わない |
| 8 | **マスクテクスチャをリニアで取り込む** | D-Drive の規則が毎回の取り込みで sRGB を決める（§4 の罠 2） | T-Drive のマスクは接尾辞 `_ToonMask` などにし、プロジェクトの `TextureImportProfile` に「`_ToonMask` → sRGB オフ」の規則を `T_` より前に足す（プロジェクト側の設定。D-Drive 本体は変えない） |
| 9 | **ステンシル・ZTest**（髪越し表示） | 予約名（`_ZTest` など）は Specific に入れられず、Common も書かない | `_ToonStencilRef` / `_ToonStencilComp` のように `_Toon` 接頭辞のプロパティとしてシェーダー側で受ける |
| 10 | **セルフシャドウを落とさない**（`_ToonCastShadow` = 0 → ShadowCaster パスを止める） | D-Drive が作る Material ではパスの有効 / 無効を指定できない（`new Material` で状態が消える） | 落とさないマテリアルには ShadowCaster パスを持たない生成シェーダーを割り当てる（#2 の仕組みに含める）。§5 M-1 があれば不要になる |
| 11 | **輪郭線**（背面押し出しのパス・部位ごとの線幅と色・スクリーンスペースへの切り替え） | パスは自動で描かれる（扱える）。線幅 0 でもパスの負荷は残る | 輪郭線を使わない部位は、輪郭線パスの無い生成シェーダー（#2）。スクリーンスペースは Renderer Feature（#4） |
| 12 | **D-Drive の検証の警告**（Albedo 未設定 / シェーダーに無い Specific） | テクスチャの無い Toon マテリアル・生成シェーダーで削った `_Toon*` に警告が出る | ブリッジが書き出すときに、無いプロパティを外し（#2）、色だけのマテリアルには白の既定テクスチャを入れる |

## 4. 気を付けないと壊れるもの（罠）と避け方

| # | 罠 | 何が起きるか | 避け方（運用） |
|---|---|---|---|
| 1 | **モデルエディタの「元ファイル再読み込み」** | スロットを作り直すとき、知らないシェーダーのマテリアルを `DDrive/Lit` の MaterialData に変換して結び直す → スポーン時に Toon のマテリアルが Lit に置き換わる | T-Drive のキャラクターは、スロットを T-Drive のブリッジが書き出した MaterialData（シェーダー = `TDrive/Toon` または生成シェーダー）で埋めておく。または `ModelData.Slots` を空にして Prefab のマテリアルをそのまま使う（空・無効な ID のスロットは触られない） |
| 2 | **テクスチャ名の規則** | `Assets/SourceAssets`・`Assets/GameData` の下で `T_` で始まるテクスチャは毎回 sRGB オンにされる。`T_xxx_ToonMask` のようなマスクが壊れる | §3 #8 の規則を足す。または T-Drive のテクスチャをその 2 フォルダの外に置く |
| 3 | **FBX の自動取り込み** | T-Drive のキャラクターの FBX を `Assets/SourceAssets` に置くと、Look を通らずに `DDrive/Lit` の MaterialData が自動で作られる | T-Drive のキャラクターは T-Drive の取り込み経路（Look のインポート）で入れる。`MayaImportProfile` の対象パスから外す |
| 4 | **変換表の置き場所** | D-Drive のマテリアル変換ウィンドウは、変換表（`ShaderConversionTable`）を `Assets/` と D-Drive 自身のパッケージからしか探さない → T-Drive のパッケージ内に置いた表は見つからない | ブリッジが変換表を `Assets/` の下に生成する（生成シェーダーごとの表も同じ） |
| 5 | **カットシーン用のキャラクター FBX**（推測） | D-Drive はショットごとのキャラクター FBX を別の FBX として取り込む。メッシュを含めると T-Drive の取り込み設定（#5）が当たらない | カットシーンの FBX はアニメーションだけにする（D-Drive の手順書どおり: メッシュ・スキン・ブレンドシェイプをオフ） |

そのほか: `MaterialData.RenderingLayerMask` は欄があるだけで実行時には使われていない（効くのは `ModelData.LightLayerMask`）。Toon 側でライトレイヤーを使うときはモデル側の値を見る。

## 5. D-Drive 側に実装したほうがいい機能（必須ではない）

T-Drive 側の解決法（§3）で動くので無くてもよいが、あると回避策・運用の決まりが要らなくなる。区分は D-Drive の規則（欄・API・enum 末尾の追加 = MINOR）での見立て。

| # | 機能 | 無くなる回避策・効果 | D-Drive 側の変更（場所） | 区分 | 優先 |
|---|---|---|---|---|---|
| **M-1** | **MaterialData にパスの有効 / 無効（とキーワード）の欄**（例 `DisabledPasses: string[]`、`Keywords: string[]`） | セルフシャドウを落とさない（§3 #10）・輪郭線なし（#11）のためだけに生成シェーダーを増やさずに済む。Toon 以外（自作シェーダー全般）にも効く | `MaterialData` に欄を追加し、`MaterialManager.GetOrBuild` で `SetShaderPassEnabled` / `EnableKeyword` | MINOR | 高 |
| **M-2** | **モデルのスポーン / 返却の通知**（イベント、または Model への `IAssetBehaviour`） | スロット適用の**後**に外部のコンポーネント（`ToonCharacter`・FacialController の Runner）が確実に初期化・後片付けできる。今は Prefab 上のコンポーネントの OnEnable に頼っている（スロット差し替えとの前後が保証されない） | `ModelsManager.SpawnData` / 返却処理 | MINOR | 高 |
| **M-3** | **インスタンスごとのマテリアル値**（例 `Models.SetMaterialParam(handle, slot, property, value)`。MaterialPropertyBlock） | 表情パラメータ・キャラクターごとの値（§3 #7）を D-Drive の流儀で渡せる。同じマテリアルを使う別キャラクターが一緒に変わる問題が無くなる | `ModelsManager` に API 追加 | MINOR | 中 |
| **M-4** | **変換表・取り込み規則を他のパッケージから登録できるようにする** | 罠 4（変換表を `Assets/` に生成）・罠 2（プロジェクトごとに規則を足す）が不要になる。文書（`docs/42`）が拡張点としている `IImportRuleHandler` が実際には外から足せない点も同じ修正で直る | `AssetSearch` の検索範囲、`TextureImportProfile` の既定規則 or コードから登録する口、`ImportRuleService` のハンドラ配列 | MINOR / PATCH | 中 |
| **M-5** | **「元ファイル再読み込み」で知らないシェーダーを Lit に変換しない**（確認を出す / そのシェーダーの MaterialData を作る） | 罠 1 が無くなる。Toon 以外の自作シェーダーも守られる | `ModelSlotBinder.Rebuild` → `UnityMaterialMigrator.Migrate` のフォールバック | 挙動の変更（要検討） | 中 |
| **M-6** | **モデルに名前付きのスロットセット**（ルックの A/B・衣装違い） | Unity 上でのバリアント切り替え（§3 #6）を D-Drive のデータとして持てる。予定の 7-7（品質別）とは別 | `ModelData` に欄を追加 + `Models` の切り替え API | MINOR | 低 |
| **M-7** | **モデルに外部データへの参照欄**（キャラクター設定など） | `CharacterLook`・FacialController のデータを ModelData から辿れる（依存関係の追跡・安全な削除に乗る）。今は Prefab のコンポーネントが直接参照 | `ModelData` に汎用の参照欄 | MINOR | 低 |
| **M-8** | **プロジェクト設定の検証の拡張点**（必要な Renderer Feature が入っているか等） | §3 #4 の確認を D-Drive のセットアップ検証（`ProjectSetupValidator`）と同じ場所に出せる。今でも `IValidator` で代用できる | 検証の登録口 | MINOR | 低 |
| **M-9** | **検証の警告の調整**（Albedo 未設定を色だけのマテリアルで出さない・`RenderingLayerMask` が未使用であることの明記 or 実装） | §3 #12 の回避（白テクスチャを入れる）が不要 | `MaterialDataValidator`、`MaterialManager` | PATCH | 低 |

### 5.1 おすすめの進め方

1. **M-1・M-2 を先に**: どちらも数十行の追加で、Toon にも FacialController（[16](16_ddrive_changes_for_facial.md) C-2 のプール返却と同じ場所）にも、Toon 以外の自作シェーダーにも効く
2. M-3 は表情パラメータ（T-25）を MS2026 で使い始めるときに、T-Drive 側の方式（[08](08_unity_port_plan.md) Q-7）と比べて決める
3. M-4・M-5 は罠を運用で避けている間は後回しでよいが、デザイナーが誤って踏みやすいのは罠 1（M-5）
4. 入れるときは D-Drive の決まり（追加のみ・スナップショットテスト・CHANGELOG の互換性・マニュアル同時更新）に従う。D-Drive のリポジトリは T-Drive からは読み取りのみなので、提案の文面を用意して渡す（Phase U のチケット U-21）

## 6. T-Drive 側でやること（チケットへの反映）

§3・§4 の解決法のうち、Phase U の既存チケットに含まれていないものを追加する。

| # | 内容 | チケット |
|---|---|---|
| 1 | ブリッジの書き出し: 生成シェーダーに無い `_Toon*` を外す・色だけのマテリアルに白テクスチャ・変換表を `Assets/` に生成 | U-22 |
| 2 | 取り込み設定（接線・頂点カラー・UV・ライトマップ UV）とマスクのリニア規則の案内（プロジェクトの `TextureImportProfile` に足す規則を検証で知らせる） | U-22 |
| 3 | 検証（D-Drive の `IValidator`）: Renderer Feature の有無、スロットが Lit に置き換わっていないか（罠 1）、マスクが sRGB になっていないか（罠 2） | U-23 |
| 4 | D-Drive 側への提案（M-1〜M-9）の文面 | U-21 |
