# 16. FacialController のための D-Drive 側の変更（調査）

- 作成: 2026-10-03。調査対象: `C:\Users\yamag\wrench\unity\D-Drive`（v1.3.1、HEAD `9f40cbb`）。**読み取りのみ**（D-Drive のリポジトリは変更していない。コンパイル・実行もしていない = 文書とソースを読んだ結果）
- 仕様: [14](14_facial_controller_spec.md) §7、設計: [15](15_facial_controller_design.md) §5.5
- ここに挙げる変更は **D-Drive 側のチケットとして起こす提案**。T-Drive 側は「変更なしでも動く」範囲で先に作る

## 1. 結論

| 区分 | 件数 | 内容 |
|---|---|---|
| **A. 変更なしで動く** | — | Runner を Prefab に付ける・Timeline のトラック・シーク / 同期・再取り込みでトラックが残る・検証の追加・外部パッケージからの取り込み |
| **B. 必須（決めないと進めにくい）** | 1 | チケット 7-8（D-Drive 内に FacialController を移植する計画）の扱い（§2）。コードの変更ではなく方針 |
| **C. 必須ではないが、実装したほうがいい** | 9 | T-Drive 側の回避策で動くが、D-Drive 側にあると安全・楽になるもの（§4）。Toon のマテリアルが「D-Drive の MaterialData では扱いにくいのでブリッジで変換する」のと同じ位置づけ（マテリアル側の整理は [17](17_ddrive_toon_materials.md)） |
| **D. 任意（D-Drive の一級の種別にするなら）** | 7 | Facial を ID で引けるアセットにする場合の一式（§5） |

## 2. 【B】チケット 7-8 との関係（要確認）

D-Drive の `docs/11_tasks.md` に **7-8「FacialController 移植」**（2026-09-10 決定、未着手、26〜29 日）がある。内容は D-Drive の中に新種別 `Facial`（`FacialData`・ベイカー・`FacialManager`・エディタ・`.fcpose.json` 入出力）を作る計画で、今回の T-Drive 版と**同じものを二重に作る**ことになる。

| 案 | 内容 | 評価 |
|---|---|---|
| **1（推奨）** | T-Drive 版を正とする。D-Drive 7-8 は「T-Drive for Unity（`com.tdrive.facial`）を使う。D-Drive 側は §4 の小さな追加だけ」に書き換える | Toon と同じ方針（2026-09-28: キャラクターのルックは T-Drive に一任、D-Drive とは互換ブリッジ）。Unity でのベイク・フルエディタ（7-8 の大半）が不要になる = Maya が作成・編集、Unity はランタイムという今回の役割分担に合う |
| 2 | D-Drive 7-8 を予定どおり D-Drive 内に実装し、T-Drive は Maya 側だけ | Unity 側の計算・データ型が D-Drive の版管理（追加のみ・MAJOR 禁止）に縛られる。UE 版・Maya と結果を合わせる変更がしにくい |
| 3 | 両方作る | 二重管理。不採用 |

補足: 7-8 の本文の移植元パスは `C:\Users\yamag\wrench\FacialController_UE` だが、実際の場所は `C:\Users\yamag\wrench\ue\FacialController_UE`。書き換えるときに直す。

## 3. 【A】変更なしで動くもの

| # | 内容 | 根拠 |
|---|---|---|
| A-1 | キャラクターの Prefab（`ModelData.Prefab`）に `FacialCorrectionRunner` を付ければ動く | D-Drive はモデルを Prefab からプールで出すだけ。コンポーネントはそのまま生きる |
| A-2 | `FacialCorrectionTrack` が Timeline ウィンドウに出て、`CutsceneManager` の再生で動く | トラックの登録簿は無く、Unity 標準の `[TrackClipType]` で見つかる。`CutsceneManager.Tick` は `time += dt; Evaluate()` |
| A-3 | シーク・スキップ・一時停止・速度変更・ネットワーク同期に追従 | 受信側は Director をシークするので、連続的に値を当てるトラックはそのまま同期される |
| A-4 | カットシーン FBX を再取り込みしてもトラックが消えない | `CutsceneImportService` は自分が作ったトラック（名前と型で照合）だけを更新する |
| A-5 | 知らないトラックでも検証の警告は出ない | `CutsceneDataValidator` が警告するのは標準の Audio / Control / Signal とカメラ上の AnimationTrack だけ |
| A-6 | T-Drive 側の検証を D-Drive の検証（CI）に載せられる | `IValidator` は全アセンブリから自動で見つかる（`DDrive.Tests*` 以外） |
| A-7 | `.fcpose` / `.fctrack` の取り込み | D-Drive に Maya からの JSON 取り込みの前例は無いが、Unity 標準の `ScriptedImporter` / `AssetPostprocessor` は外部アセンブリから使える |
| A-8 | アニメーションのブレンドシェイプ（`AnimData.BlendShapes`）と衝突しない | D-Drive は `Update`（`AnimManager.Tick`）で名前指定のシェイプを書く。Runner は `LateUpdate` で `FC_*` だけを書く |
| A-9 | 起動後にマネージャーへ登録したい場合の口 | `IAssetManager` + `GameLoop.Register` が公開（今回は使わない。Runner は自立したコンポーネント） |

**バインドの注意**（変更なしで使うときの決まり）: `CutsceneBinding` は Animator か Transform を渡すので、トラックのバインド型は `Animator` にする。`Target` は Self / Target / SceneObjectByName を使う（SpawnModel は §4 C-1）。

## 4. 【C】必須ではないが、実装したほうがいい機能

T-Drive 側に回避策があるので無くても動く。優先度は「効果 ÷ D-Drive 側の手間」。互換の区分は D-Drive の規則（追加 = MINOR）での見立て。

| # | 機能 | 今の問題と T-Drive 側の回避策 | D-Drive 側の変更（場所） | 区分 | 優先 |
|---|---|---|---|---|---|
| **C-1** | **同じモデルへのバインド**（`CutsceneBindTarget` に「トラック X と同じ相手」） | カットシーンで出した（SpawnModel）キャラクターに Facial のトラックを結べない。SpawnModel のバインドを 2 つ書くとモデルが 2 体出る。回避策 = ブリッジが同じ役名の AnimationTrack のバインド先を引く（名前の規則に頼る） | enum に値を追加 + `CutsceneManager.ApplyBindings / ResolveBindingObject` と、編集時の `CutsceneEditModeDirectorSetup` に分岐を追加 | MINOR | 高 |
| **C-2** | **プールへ返すときにブレンドシェイプの重みを戻す** | `ModelInstancePoolable` は重みを戻さないので、前の表情・補正が次の利用者に残る。Facial は Runner の `OnDisable` で FC_* を戻すが、`AnimData.BlendShapes` が書いた通常のシェイプは残る（Facial に限らない既存の穴） | `ModelInstancePoolable` の返却処理で全 SkinnedMeshRenderer の重みを 0（または取得時の値）に戻す | 挙動の追加（MINOR） | 高 |
| **C-3** | **「今の視点カメラ」を取れる API** | 補正は「どのカメラから見ているか」で決まる。カットシーン中はカットのカメラ（`CutsceneCameraTrack` / MainCamera バインド）、ゲーム中はゲームのカメラで、分割画面・カメラブレンド中は `Camera.main` では決まらない。回避策 = Runner の視点を手で指定 / Timeline のクリップで指定 | D-Drive の公開 API に「現在の描画視点（Transform・画角）」を返す口（カットシーン・カメラ切り替えを反映）。パース補正（R-34）でも画角が要る | MINOR | 中 |
| **C-4** | **マーカーの汎用の受け口** | `CutsceneManager.CollectMarkers` が D-Drive の 4 種類のマーカーを決め打ちで集めるので、外部パッケージのマーカーは実行時に黙って無視される。回避策 = マーカーを使わずクリップだけにする（今回の設計はそうしている） | インターフェース（例 `ICutsceneMarker`）を実装したマーカーを汎用に集めて通知。編集時プレビュー側（`CutsceneEditModePreviewProvider`）も同様 | MINOR | 中 |
| **C-5** | **カットシーン取り込み後の通知**（外部が追随できる口） | `.fctrack` から Facial のトラックを足す処理は、D-Drive の取り込み（`CutsceneFbxPostprocessor` → `CutsceneImportService`）の**後**に走る必要がある。回避策 = AssetPostprocessor の順番（postprocessOrder）に頼る + 同名ショットを自分で探す | `CutsceneImportService` が取り込み完了時に呼ぶ公開イベント（ショット名・モデル識別子・`.playable`・役名 → トラックの対応を渡す） | MINOR | 中 |
| **C-6** | **取り込みルールの外部拡張 / `SourceAssets/Facial/` の扱い** | `IImportRuleHandler` は公開だがハンドラは internal の静的配列。知らない種別フォルダを `SourceAssets/` に置くと「不明な種別フォルダ」の案内が出る。回避策 = Facial のデータは `SourceAssets/Cutscene/` か GameData 外（T-Drive のフォルダ）に置く | ハンドラの外部登録（TypeCache で発見）か、対象外フォルダ一覧（`KnownNonTargetTypeFolders`）への追加 | MINOR（1〜数行） | 低 |
| **C-7** | **依存関係の追跡が Timeline のクリップ内まで届くか**（未確認） | `DependencyGraphCollector` が Data アセット本体しか歩いていないなら、`.playable` 内のクリップが参照するアセット（Facial のデータ・ポーズ）が「使用箇所」「安全な削除」に出ない。Addressables の依存としては `.playable` に付いて運ばれるので再生は問題ない | Timeline のサブアセット（クリップ）の参照も歩く。まず実際の挙動を確認 | 挙動の追加 | 低 |
| **C-8** | **カットシーンのキャラクター FBX で表情（ブレンドシェイプ）を通す道** | 手順書が Blend Shapes OFF を指示し、取り込みは Humanoid 固定で AnimationTrack だけを作る。そのため表情の重み（= 補正を弱める入力・感情の出どころ）がショットの FBX からは来ない。回避策 = 表情は別のアニメーション（AnimData / Animator）で付け、感情の重みは Facial のクリップ / `.fctrack` で渡す | 取り込みの選択肢に「ブレンドシェイプのカーブを残す」を追加 + 手順書（`cutscene-maya-export.html`）の更新 | MINOR + 文書 | 低（表情アニメの運用が決まってから） |
| **C-9** | **デバッグ・調整機能との接続** | D-Drive の予定機能（7-3 デバッグオーバーレイ、7-4 Live Tuning）から Facial の値（強さ・追従・感情）を触れると、実機での調整が D-Drive の流儀に乗る。回避策 = T-Drive 独自の HUD とインスペクター | 7-3 / 7-4 の実装時に、外部コンポーネントが値を公開できる口を用意 | 新規機能側の設計 | 低（7-3 / 7-4 着手時） |

### 4.1 おすすめの進め方

1. **C-1・C-2 を先に**（どちらも数十行で、Facial 以外にも効く）。C-2 は既存の不具合に近い。C-2 はマテリアル側の M-2（スポーン / 返却の通知、[17](17_ddrive_toon_materials.md) §5）と同じ場所の変更なので一緒に入れる
2. C-3 は Facial のランタイム（FU-3）を MS2026 で使い始めるときに決める（分割画面・カメラブレンドの有無による）
3. C-4〜C-6 は T-Drive 側の回避策で足りている間は後回し
4. 入れるときは D-Drive の決まり（追加のみ・スナップショットテスト・CHANGELOG の互換性・マニュアル同時更新）に従う

## 5. 【D】任意: Facial を D-Drive の一級の種別にする場合

案 1（§2）では**やらない**。D-Drive の ID・カタログ・AssetBrowser・Placeholder に Facial を載せたくなったときの一式（D-Drive 側の手順書 `docs/ProgrammerManual/extending.html` の 14 手順に相当）。

| # | 変更 | 場所 | 注意 |
|---|---|---|---|
| D-1 | `AssetType` に `Facial` を追加 | `Foundation/Identity/AssetType.cs`（末尾に追加） | 直列化される enum（追加のみ） |
| D-2 | 命名・カタログ名の switch | `Editor/AssetBrowser/AssetNamingService.cs`、`AssetCreationService.GetCatalogName` | |
| D-3 | ID 定数の接頭辞 | `AssetIdGenerator` の `KnownPrefixes` | **方針と衝突**: P-13 以降は追加禁止（`docs/42` §5.3）。入れないと定数名が `FACEID.FACESmile` のようになる。要判断 |
| D-4 | `FacialManager` をマネージャーとして配線 + `Facial.*` の窓口 | `DDriveRuntimeBootstrap`、`CutsceneDirectorManagerRefs`、`CutsceneEditModeManagers` | Runner を自立コンポーネントにしておけば不要 |
| D-5 | イベントから Facial を再生（AssetEvent の PlayAsset） | `AssetEventDispatcher` の switch | |
| D-6 | PresentationData のトラック種別に Facial | `PresentationTrack.TrackKind`（閉じた enum）+ `PresentationManager` の switch + PresentationEditor の UI | 規模が大きい。Presentation から Cutscene を入れ子にできるので、当面は Cutscene 経由で足りる |
| D-7 | D-Drive 内に Data を置く場合の手続き | `[DataEditor]` か除外登録（`DataEditorRegistryTests`）、互換スナップショット、マニュアル | |

`ModelData` に Facial のデータを指す欄を足す案もあるが、Runner を Prefab に付ける方式（A-1）で足りるので不要。

## 6. T-Drive 側が従う D-Drive の決まり

| 決まり | T-Drive 側の対応 |
|---|---|
| 消費者はパッケージを改変しない・`DDrive.*` 名前空間を使わない | 名前空間は `TDrive.Facial.*`。ブリッジは別 asmdef |
| 例外で止めない（警告して何もしない） | Runner・トラックは無い名前・未バインドを警告 + 無視（フェイルソフト。UE 版と同じ） |
| データは実行時に読み取り専用 | `FacialCorrectionData` / `Overrides` を実行時に書き換えない。一時的な上書きは Runner のフィールドと `PushOverride` |
| `Instantiate` / `Resources.Load` を直接使わない | Runner は何も生成しない。データは Prefab / クリップからの直接参照（Addressables の依存として運ばれる） |
| カットシーンのファイル名 `<Shot>__<ModelIdentifier>`（アンダースコア 2 つ） | `.fctrack` も同じ規則。置き場所は `Assets/SourceAssets/Cutscene/<Category>/` |
| 単位・軸: cm / Y-up / FBX は自動単位、フレームレート 30 or 60 | Maya の出力プリセットを合わせる。`.fctrack` は秒単位 + frameRate を併記 |
| ブレンドシェイプは名前で引く・重みは 0〜100 | Runner が ×100。名前は完全一致 |
| 再取り込みでデザイナーの編集を消さない | 自動で作ったトラックだけ更新。調整値は Overrides（別アセット） |
| 配布は git URL + タグ、版は SemVer | T-Drive の VERSION・タグと同じ。`com.tdrive.facial` の package.json の版を合わせる |
