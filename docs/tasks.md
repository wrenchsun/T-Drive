# タスク（優先順位順チケット）

優先順位は [04_technique_priority.md](04_technique_priority.md) に従い、**ルック（P0 → P1 → P2）→ アニメーション（A1 → A2）** の順で発行する。
Unity は T-Drive for Unity パッケージ（Phase U）として実装する（2026-09-28 方針）。ルックに関わる Maya 側の作業（Phase 3）を先に仕上げる。
FacialController（カメラ角度に応じた顔の補正。UE 版からの移植）は Phase S / F0〜F5（2026-10-03 追加。仕様 [14](14_facial_controller_spec.md)・設計 [15](15_facial_controller_design.md)）。
アニメーション作業台（MS2026 の命名規則・クリップ・ブレンド / 遷移のプレビュー・Unity 用の分割出力・ショット）は Phase AN（2026-10-22 追加・AN-1〜AN-9 実装済み。仕様 [20](20_animation_workbench_spec.md)）。

- 粒度: 1 チケット = 0.5〜3 人日。依存は `←` の先行チケット
- 状態: ✅ 完了 / 🔶 ドラフトあり（仕様確定後に見直し） / ⬜ 未着手
- 受け入れ条件（AC）を満たしたら ✅。チケット番号はコミットメッセージに入れる（[06](06_release_versioning.md) §6）
- **共通の完了条件**: デザイナーが触る機能は [デザイナーマニュアル](DesignerManual/Readme.html) の該当ページを同じコミットで更新していること
- **共通の完了条件（2026-10-03 追加）**: 人が操作して確かめる項目を、確認項目リスト（ルック = [10](10_manual_verification_2026-09-28.md)、FacialController ほか = [18](18_manual_verification_facial.md)）に同じコミットで足していること（「自動」欄に、テストで確認済みの範囲を書く）

---

## Phase 0: 基盤（M0 — 動く骨格）

| # | チケット | 技術 | 日数 | 依存 | 受け入れ条件 | 状態 |
|---|---|---|---|---|---|---|
| 0-1 | リポジトリ雛形（.gitignore / .gitattributes + Git LFS / VERSION / CHANGELOG / CLAUDE.md / README） | — | 0.5 | — | バイナリ（fbx/tga/png/mb）が LFS 管理。`captures/` `build/` が管理外 | ✅ |
| 0-8 | デザイナーマニュアル（HTML、D-Drive と同形式）: トップ / 導入 / 部位の登録 / パラメータ / Unity との一致 / 困ったとき | — | 1 | 1-4 | 実装済み機能と準備中機能が区別され、実装と食い違う記述が無い | ✅ 以後チケットごとに更新 |
| 0-2 | 仕様書一式（docs/00〜08, tasks.md） | — | 1 | — | 目的・制約・優先順位・Look 仕様・シェーダー仕様・ツール仕様・リリース運用・移植計画が揃う | ✅ |
| 0-3 | **Maya MCP 導入**（GG_MayaMCP 0.6.1 / `.mcp.json` / ランチャー / Maya モジュール / commandPort 自動オープン） | — | 0.5 | 0-1 | Claude Code から `health.check` → `scene.info` が成功（Maya 起動状態で確認） | 🔶 設定済み・疎通未確認 |
| 0-4 | UnityChan テスト素体の取り込み（D-Drive から FBX・テクスチャ・ライセンス） | — | 0.5 | 0-1 | `assets/unitychan/` に配置。Maya で FBX を読み込み、マテリアル一覧が取れる | ✅ CgFX マテリアル消失はフィクスチャ `tools/fixtures/build_unitychan_scene.py` 側で吸収（ツールには持ち込まない） |
| 0-5 | パラメータ契約・ロール・Look 定義（読み書き・検証・バリアント解決・差分・D-Drive 形式出力）＋ pytest | — | 1.5 | 0-2 | [02](02_look_definition_spec.md) §6 の全検証ルールと [03](03_shader_spec.md) §7.2 の契約がテストで担保される | ✅ 契約と docs/03 の一致もテスト化 |
| 0-7 | Unity 環境プロファイル仕様と既定値（`looks/_env/ms2026_ingame.json` を手入力で作成: Linear / トーンマップ / ライト / 縦 FOV 40） | — | 0.5 | 0-2 | [09](09_render_parity.md) §3 の形式で読み込める。トーンマップの本番値（None/Neutral）を MS2026 に確認済み | ✅ InGame=Neutral |
| 0-6 | リリーススクリプト（`check-release.ps1` / `bump-version.ps1` / 契約スナップショット） | — | 1 | 0-5 | DryRun で VERSION・CHANGELOG の更新内容が表示される。契約から削除があるのに MAJOR でない場合に失敗する | ✅ Python 版（pwsh が無いため）。check_release / bump_version、契約差分から上げ幅を機械算出。v0.1.0 のタグ付けは未実施（ユーザー判断） |

**M0 デモ**: Claude から MCP 経由で Maya に UnityChan を読み込み、マテリアル一覧を取得できる。

## Phase 1: ルック P0（M1 — MVP）

| # | チケット | 技術 | 日数 | 依存 | 受け入れ条件 | 状態 |
|---|---|---|---|---|---|---|
| 1-1 | **共通シェーダーコア `shaders/ToonCore.hlsl`** + Maya ラッパー `TDriveToon.fx`（dx11Shader: 2 階調影・影色・影の強さ・キャラクターライト・固定色・トーンマップ・Opaque/Cutout/Transparent） | T-01 T-02 T-04 T-08 | 2 | 0-5 0-7 | [03](03_shader_spec.md) §2 の式どおり。ToonCore が Unity/Maya 両方の制約（[09](09_render_parity.md) D-5）を満たす。VP2(DirectX 11) でコンパイルエラーなし。全パラメータがアトリビュートとして出る | ✅ 実機（DX11）で UnityChan 表示確認。落とし穴は [09](09_render_parity.md) §6 |
| 1-1b | Maya の描画環境合わせ（DX11 / OCIO・Un-tone-mapped 固定 / テクスチャ色空間 / 環境プロファイル読込 / 縦 FOV カメラ） | — | 1 | 1-1 | [09](09_render_parity.md) §2 のパリティ表の Maya 側設定がワンクリックで揃う。ずれている項目を警告表示 | ✅ プレビュータブのパリティ表＋「色の設定を Unity に合わせる」 |
| 1-2 | プレビュー差し替え（元マテリアル ⇔ Toon の切替・値の流し込み・部分更新） | — | 1 | 1-1 | 元マテリアル・割り当てを破壊せず往復できる。Toon のまま保存したシーンも戻せる。切替 1 秒以内 | ✅ mayapy スモークテスト（tests/maya/smoke.py）で往復・保存後復帰を担保 |
| 1-3 | 編集セッション層 + MCP スクリプト（`reload_tdrive` / `setup_unitychan`） | — | 1 | 1-2 | UI 無しで 新規→自動登録→表示→値変更→保存 が API で通る。MCP から `setup_unitychan` 実行で UnityChan が Toon 表示になる | ✅ setup_unitychan / reload_tdrive、スモークテスト 12 項目 |
| 1-4 | **部位タブ**（自動登録・選択から登録・ロール変更・部位を選択） | — | 1.5 | 1-3 | UnityChan を 3 クリック以内で全部位登録・Toon 表示（[05](05_maya_tool_spec.md) §3） | ✅ ヘッダー + 部位タブ（スモーク 18 項目） |
| 1-5 | **ルックタブ**（部位/マテリアル単位の編集・即時反映・混在表示・リセット・エディタ Undo） | — | 2 | 1-4 | スライダー操作がビューポートに連続反映（< 16ms）。部位選択で一括設定 | ✅ 1 回の反映 0.2ms。スモーク 24 項目 |
| 1-6 | アウトライン（背面法線押し出し・画面上一定幅・部位別太さ・色付き線） | T-05 | 1 | 1-1 | 距離を変えても線幅が画面上で一定。線幅 0 の部位は描画されない | ✅ 実測: 線幅 4 → 1080p で 4px（距離 2 倍でも 4px）、540p で 2px |
| 1-7 | **A/B タブ**（バリアント追加・A⇔B 切替 Alt+T・並べてキャプチャ・差分・採用） | — | 2 | 1-5 | 同一カメラ/ライトで A/B 画像が並ぶ。差分一覧が `look.diff` と一致。採用で base に確定 | ✅ 4 方向一括キャプチャ・左右/ワイプ・差分（日本語名）・採用/削除（Undo 可） |
| 1-8 | **プレビュータブ**（カメラプリセット・ライト方向/プリセット・ライト回転再生・環境の保存） | T-02 | 1.5 | 1-3 | 正面/3/4/横/後ろ をワンクリック。preview.json で条件を再現できる | ✅ 環境・カメラ 5 方向・ライト 5 プリセット＋回転・パリティ表・preview.json（スモーク 28 項目） |
| 1-9 | 頂点カラー Toon マスク（`tdToonMask` 初期化・チャンネル別ペイント起動・チャンネル単体表示） | T-03 T-07 T-08 | 2 | 1-1 | 首下を R 黒で塗ると常に影、頬の G 黒で線が消える、が塗った瞬間に見える | ✅ 作業用 Color Set で 1 チャンネルずつペイント、塗り中にプレビュー反映、確定/キャンセル、チャンネル単体表示（スモーク 31 項目） |
| 1-10 | アウトライン用スムーズ法線のベイク（接空間・八面体エンコード → UV `tdSmoothNormal`） | T-06 | 1.5 | 1-6 | 髪・服の角で線が割れない。ポーズを付けても線が追従する | ✅ 往復誤差 ~1e-16、スキン付きは Orig に焼く、ハードエッジ立方体で角の線がつながることを確認 |
| 1-11 | 出力（Unity 用 materialdata.json・FBX（頂点カラー/UV2/接線込み）） | — | 1 | 1-9 1-10 | 出力 JSON が [02](02_look_definition_spec.md) §7 に一致。FBX を再読込して頂点カラー・UV2 が残っている | ✅ 別プロセスで整形・書き出し（シーン不変・Maya は応答継続）。再読込で頂点カラー/UV 順を検証（スモーク 34 項目） |
| 1-13 | パリティ比較ツール（Maya 側キャプチャ + `tools/parity/compare.py`: 差分統計・ヒートマップ） | — | 1 | 1-1b | 同じ画像同士で差 0、1px ずらした画像でアウトライン縁除外が効く | ✅ compare.py（キャラクター画素の平均差・縁を除いた最大差・ヒートマップ、合否を終了コードで）+ session.capture_parity（4 方向・プロファイル解像度） |
| 1-12 | UnityChan ルック v0.1.0 の作成（A/B で決めた値を採用し `look/unitychan/v0.1.0` をリリース） | — | 1 | 1-7 1-11 | 比較画像と採用理由が `looks/unitychan/CHANGELOG.md` に残る | ⬜ |

**M1 デモ**: UnityChan を読み込み → 3 クリックで部位登録・Toon 表示 → 顔の影を弱めた B 案を作って A/B 比較 → 採用 → 出力。

## Phase 2: ルック P1（M2 — 顔・髪のクオリティ）

| # | チケット | 技術 | 日数 | 依存 | 受け入れ条件 | 状態 |
|---|---|---|---|---|---|---|
| 2-1 | 眉・目・アイラインを髪の上に（ビュー空間デプスオフセット `_ToonDepthOffset`） | T-09 | 1 | 1-6 | 前髪越しに眉が見える。横顔で眉が頭の外にはみ出さない範囲の既定値がある | ✅ `_ToonDepthOffset`（視線方向に寄せる。本体・線の両パス）。ロール既定 眉 3cm/アイライン 2.5cm/目 2cm。前髪越しに目が出ることを実機確認 |
| 2-2 | 顔の法線編集ツール（球/楕円体プロキシから法線転写・ウェイトで部分適用） | T-10 | 2.5 | 1-9 | 顔の影がライト回転で「作画的な 2〜3 パターン」に収まる。FBX 出力で法線が保持される | ✅ 楕円体プロキシ・強さ・選択頂点・リセット（カスタム法線も復元）。誤差 1e-6 未満をスモークで担保、実機で影の単純化を確認 |
| 2-3 | 目のハイライト固定（ライト非依存の表示・影/線なしのロール設定） | T-11 | 0.5 | 1-5 | ライトを回してもハイライトが動かない | ✅ `_ToonLightColorInfluence`、目のロール既定 0（影なし・線なし） |
| 2-4 | 鼻を線に・鼻影を消す（B 黒/テクスチャ線の運用手順をツールのプリセットに） | T-14 | 0.5 | 1-9 | UnityChan の鼻で手順どおりに再現できる。docs にガイド追記 | ✅ ガイド（マニュアル「表現のレシピ」: B 黒で鼻影を消す・G 黒で線を消す・テクスチャで線） |
| 2-5 | リムライト（明側のみ・マスク） | T-13 | 0.5 | 1-1 | `_ToonRim*` 追加（MINOR）。既定値で見た目が変わらない | ✅ `_ToonRim*`（明側のみ）。既定値で従来と同じ |
| 2-6 | 髪ハイライト（天使の輪・束に沿ったハイライト・視線依存シフト） | T-12 | 2 | 1-1 | `_ToonHairHighlight*` 追加。カメラの上下でハイライトが自然に移動 | ✅ `_ToonHairHighlight*`（R の帯を視線でシフト、明側のみ） |
| 2-7 | インナーライン（テクスチャ描き込み / UV 直線ライン法のガイドとプレビュー） | T-15 | 1.5 | 1-1 | 服の境界・髪の束の内部線が距離で潰れない描き方の手順がある | ✅ ガイド（テクスチャ描き込み・UV 直線ライン法） |
| 2-8 | Toon マスクテクスチャ（`_ToonMaskMap`、頂点カラーに乗算） | T-19 | 1 | 1-9 | 頂点密度が足りない目周りの影をテクスチャで補える | ✅ 実装済み機能の確認（R 黒のテクスチャで画素単位の影） |
| 2-9 | 線幅の距離・FOV 補正（Maya プレビュー側） | T-18 | 0.5 | 1-6 | `_ToonOutlineDistanceScale` 追加。遠景で線が太すぎない | ✅ `_ToonOutlineDistanceScale` / `_ToonOutlineRefDistance`。実測 6px→4px（計算 3.7px） |

## Phase 3: ルック P2 — Maya 側（M3）

2026-09-28 方針: **ルックに関わるものを Maya 側で先に仕上げる**。Unity でのみ実現できるもの（コンポーネント・Renderer Feature）も、設定値は Maya で編集して Look 定義に持つ（Maya でプレビューできないものは「Unity でのみ」と表示）。
**Phase 3 の完了は Maya 側の完了**。Unity でのみ描画されるもの（T-17 / T-23 / T-24 / T-29）は 3-8 で設定値を持てるようになっただけで、描画は Phase 4（Maya プレビュー）と Phase U（Unity）のチケット。対応は [04](04_technique_priority.md) §3 の「チケット」列を参照。

| # | チケット | 技術 | 日数 | 依存 | 受け入れ条件 | 状態 |
|---|---|---|---|---|---|---|
| 3-1 | 2 影（2nd shade）: 影の中にもう 1 段暗い影 | T-27 | 1 | 1-1 | `_ToonShade2Color` / `_ToonShade2Threshold` 追加（MINOR、既定で従来と同じ）。Maya で 3 階調が出る | ✅ `_ToonShade2*`。Maya で 3 段階の塗り分けを確認 |
| 3-2 | 部位別の色補正（彩度・明るさ） | T-28 | 0.5 | 1-1 | 部位ごとに彩度・明るさを調整できる。既定で従来と同じ | ✅ `_ToonSaturation` / `_ToonBrightness` |
| 3-3 | 線の太さの方向依存（下側・影側を太く） | T-26 | 1 | 1-6 | 影側の線が太くなる。既定で従来と同じ | ✅ `_ToonOutlineShadowSide` / `_ToonOutlineBottom` |
| 3-4 | MatCap（髪・装飾の簡易反射） | T-30 | 0.5 | 1-1 | MatCap テクスチャ・強さで反射が乗る | ✅ `_ToonMatCapMap` / `_ToonMatCapStrength`（ビュー空間法線） |
| 3-5 | SDF 顔影マップ（顔の影をライト角度で作画どおりに） | T-21 | 3 | 2-2 | 閾値マスク群から SDF を生成するツール + シェーダー。ライトを回すと影が作画どおりの形で進む | ✅ sdf.py（numpy のみ・EDT は総当たりと一致）+ MImage 入出力、生成→顔に設定。ライト回転で影が境界どおりに進むことを実機確認 |
| 3-6 | ビュー空間の奥行き圧縮（部位マスク付き） | T-22 | 1.5 | 1-9 | 顔だけ奥行きを圧縮しても線・影が破綻しない | ✅ `_ToonDepthCompressWeight`（マテリアル）× `characterSettings.depthCompression`（キャラクター）。中心は効かせる部位の中心 |
| 3-7 | カメラ角度依存の補正 BlendShape（作成支援・角度 → ウェイトのプレビュー） | T-20 | 3 | 1-8 | Maya でカメラを回すと補正 Shape が自動で混ざる。設定は Look の `characterSettings.viewCorrection` | ✅ 作る / 登録 / カメラに連動（DG + expression）。式は envmath と一致（誤差 0.01 以内）。彫刻用メッシュは出力対象外 |
| 3-8 | キャラクター単位の設定を Maya で編集（Look 定義 `character` セクション: ライト平滑化・ステンシル・インナーライン・接地影・表情パラメータ） | T-17 T-23 T-24 T-25 T-29 | 2 | 0-5 | [08](08_unity_port_plan.md) §4.2 の項目をエディタで編集・保存できる。Maya でプレビューできない項目は「Unity でのみ」と表示 | ✅ 設定値の編集・保存のみ（描画は 4-5〜4-7 / U-5 U-7 U-10 U-11）。`characterSettings`（`character` と衝突するため改名）、キャラクタータブ |
| 3-9 | 表情パラメータのプレビュー（頬の赤み等をスライダーで動かす） | T-25 | 1 | 3-8 | `expressions` の対応表どおりにプレビューの値が動く | ✅ プレビュー（保存しない・Look の値に戻す） |

## Phase 4: 機能のオン/オフ（Maya）

仕様: [11_features_and_shader_generation.md](11_features_and_shader_generation.md)。

| # | チケット | 技術 | 日数 | 依存 | 受け入れ条件 | 状態 |
|---|---|---|---|---|---|---|
| 4-1 | 機能の定義（features.py: 機能 ID・属するパラメータ / 設定・実現方式）+ Look の `features`（既定値、古い Look は使用中の機能をオン） | — | 1 | 3-* | 全パラメータ・全キャラクター設定がちょうど 1 つの機能に属する（テストで担保） | ✅ test_look で全パラメータ・設定の所属を担保 |
| 4-2 | 解決規則: オフの機能は既定値で解決（保存値は保持）。Unity 出力はオフの機能を出力しない + `features` を出力 | — | 1 | 4-1 | オフにすると見た目が「その機能が無い」状態になり、オンに戻すと元の調整値が戻る | ✅ test_look / スモーク（線幅 0・保存値保持・Undo） |
| 4-3 | 機能タブ（オン/オフ・使用中の表示・使っていない機能をオフ）+ ルック / キャラクタータブでオフの機能を隠す | — | 1.5 | 4-2 | 機能を切るとそのパラメータ欄が消え、プレビューも効果なしになる | ✅ 機能タブ。Maya 上で表示切替を確認 |
| 4-4 | `.fx` とテンプレートの機能区切りの整合テスト（`//#feature <id>` の規則を Python で検証） | — | 0.5 | 4-1 | 機能表・.fx・（Unity 側）テンプレートの機能 ID が一致しないとテストが落ちる | ⬜ |
| 4-5 | ライトの安定化を Maya のライト回転で再現（Unity と同じ平滑化・ヒステリシス） | T-17 | 0.5 | 4-3 | 平滑化を上げると影の追従が遅れ、ヒステリシスで小さな揺れが無視される | ✅ envmath.LightStabilizer（Unity は移植）+ プレビュータブ（安定化・小さく揺らす）。式をテストで担保 |
| 4-6 | 接地影の簡易プレビュー（足元にプレビュー専用の楕円影） | T-29 | 0.5 | 4-3 | 半径・濃さを変えると足元の影が変わる。書き出しには入らない | ✅ `Toon_ContactShadow`（ToonCore、Unity と共有）+ 足ジョイント追従の板。登録・出力の対象外をスモークで担保、実機で表示確認 |
| 4-7 | インナーラインの Maya プレビュー（Render Override で画面上のエッジ検出） | T-23 | 3 | 4-3 | 線を出す部位の境界に線が出る。Unity（U-10）と同じアルゴリズム | ✅ Render Override（MRT で ToonId → `Toon_LineInnerPair` で合成）。判定式は ToonCore（Unity と共有）。実機で部位境界・折れ目・髪の束に線を確認 |
| 4-8 | スクリーンスペース外側輪郭: 設定（`characterSettings.screenOutline`: 有無・線幅・色）・機能 `screenOutline`・Maya プレビュー（4-7 のエッジ検出を共有） | T-42 | 1.5 | 4-7 | オンにするとキャラクターの外形に画面上一定幅の線が出る。背面押し出しの線と併用 / 置き換えを選べる | ✅ `characterSettings.screenOutline`・機能 `screenOutline`・`Toon_LineOuterPair`。置き換えは機能 `outline` をオフ。輪郭線パスも ToonId を書く（穴で体の内側に線が出る不具合を修正） |

### Phase 4 追加（2026-09-28）

| # | チケット | 技術 | 日数 | 依存 | 受け入れ条件 | 状態 |
|---|---|---|---|---|---|---|
| 4-9 | テクスチャの差し替え（ルックタブの「テクスチャ」: ベースマップ / 法線マップ / 発光マップ、↺ で元マテリアルのテクスチャに戻す、見つからないファイルの警告、テクスチャを読み込み直す） | — | 1 | 4-3 | 部位を選んでベースマップを別の画像にすると即座に変わり、保存・Unity 出力に入る | ✅ ルックタブ「テクスチャ（差し替え）」、↺ = 元マテリアル、見つからないファイルは赤、テクスチャを読み込み直す（ogs -reloadTextures） |
| 4-10 | 法線マップ・発光を Maya の表示に反映（ToonCore の式・機能 `normalMap` / `emission`、既定オフ） | — | 1 | 4-9 | 法線マップで影の形が変わり、発光は影でも暗くならない。既定で従来と同じ見た目 | ✅ Toon_NormalFromMap（Unity の UnpackNormalmapRGorAG と同じ）/ Toon_Emission。機能 normalMap / emission（既定オフ）。実機でしわの影・発光を確認 |

| 4-11 | セルフシャドウ（Maya）: ToonCore の式・`_ToonReceiveShadow`（顔ロール 0、古い Look も顔は 0）・機能 `selfShadow`・影を落とすパス・影用ライトとビューポートの設定 | T-43 | 2 | 4-10 | 前髪の影が額以外（顔ロールでない部位）に、腕の影が体に落ちる。顔には落ちない。オフで従来と同じ | ✅ Toon_SelfShadowLit・_ToonReceiveShadow（顔 0、古い Look も）・機能 selfShadow・shadowPass・影用ライト（self_shadow.py）。法線方向のずらしでアクネ対策。実機で首・髪・胴の落ち影を確認 |

| 4-12 | 機能のマテリアル（シェーダー）単位の上書き: `featureOverrides`・解決・出力（マテリアルごとの Features）・機能タブの部位 / マテリアル選択（全体に従う / オン / オフ）・ルックタブの表示 | — | 1.5 | 4-3 | 髪だけリムをオンにすると髪だけに効き、他の部位の欄には出ない。出力の髪の Features にだけ rim が入る | ✅ featureOverrides（material_scope の機能のみ）・resolve / 出力 materialFeatures・機能タブの部位選択（全体に従う / オン / オフ・混在）・ルックタブの表示。実機で確認 |
| 4-13 | 機能タブを一覧表（機能 × 全体・部位）に: マス = 実際に有効か、押すとその部位だけ切り替え・全体と同じなら上書きを消す・上書きの色・行ごとに全体どおりに戻す。セルフシャドウを「落とす」も部位ごとに（`_ToonCastShadow`、0/1 のパラメータはチェックボックス） | T-43 | 1 | 4-12 | どの部位で何が有効か一目で分かる。髪だけ影を落とさない・顔だけ受けない を設定でき、ビューポートでその通りになる | ✅ 機能タブを一覧表に（マス = 実際に有効、押すと部位だけ、全体と同じなら上書きを消す、色は全体と違うマスだけ、↺）。_ToonCastShadow（0/1 はチェックボックス）。実機で髪の影を落とさない を確認 |
| 4-14 | 透かし線（D-2 の別の描き方）: `_ToonSeeThroughOutline`（0/1）・`characterSettings.seeThroughOutline`・機能 `seeThroughOutline`・オンの部位は手前に出さない・Render Override に透かしバッファ・`Toon_LineSeeThroughPair` | T-44 | 2 | 4-7 | 眉・目をオンにすると、前髪に隠れた所だけ外形の線が前髪の上に出る。重なっていない所・前髪以外（顔の皮膚・手など）越しには線が出ない | ✅ |
| 4-15 | 輪郭線をスクリーンスペースで（部位ごと）: `_ToonOutlineScreenSpace`（0/1）・機能 `outlineScreenSpace`・オンのマテリアルは背面押し出しを描かない・合成に部位の表（r・色・ベース色の混ぜ具合）・`Toon_LineSilhouettePart`（外形 + 奥行きの段差） | T-42 | 2 | 4-8 | 部位ごとに切り替えると、同じ線幅・線色のまま画面上一定幅の線になる（外形・腕と胴の段差にも出る）。オフの部位は今までどおり | ✅ |

## Phase E: エディタの操作感（lilToon / Poiyomi を参考に。2026-10-09 追加）

仕様: [05](05_maya_tool_spec.md) §4.2。Look のスキーマ・パラメータ契約は変えない。Unity 側（Phase U）は対象外。
確認項目: [10](10_manual_verification_2026-09-28.md) §T。マニュアル: `look-params.html` / `setup.html` / `Readme.html`。

| # | チケット | 技術 | 日数 | 依存 | 受け入れ条件 | 状態 |
|---|---|---|---|---|---|---|
| E-1 | メニューを **Tools › T-Drive › …** に（「Tools」は他のツールと共用。古い「T-Drive」「T-Drive Toon」のトップメニューは消す。マニュアル・確認項目・docs の表記も直す） | — | 0.5 | — | Maya 起動後、上部メニュー「Tools」の中に「T-Drive」があり、従来の全項目が動く。トップに「T-Drive」が残らない | ✅（2026-10-09。`tdrive/menu.py` が Tools を再利用または作成し、古いトップメニューを削除。`tests/test_menu.py`） |
| E-2 | ルックタブのグループを折りたたみ式に + 見出しに部位ごとの機能のオン/オフ（代表機能のチェック、サブ機能はチェック付き小見出し、○/△ の表示、「?」でマニュアル）。開閉は optionVar、「すべて開く / 閉じる」 | — | 2 | — | 線の見出しのチェックを外すと、その部位だけ輪郭線が消え、機能タブの hair の列も外れる。オフの見出しは残り、チェックで値が戻る。開閉状態が Maya 再起動後も残る | ✅（2026-10-09。`look_layout.py`（Maya / Qt 非依存の表）+ `ui_look.py` の `Section`。マニュアルにアンカー追加。`tests/test_look_layout.py`。サブ機能の行は自分の機能がオフの間だけ隠す（代表機能がオフでも独立）） |
| E-3 | パラメータの検索欄（表示名・Unity 名・説明に部分一致。該当グループだけ開く。Esc でクリア） | — | 0.5 | E-2 | 「線」と打つと線のグループの行だけ出る。空にすると元の表示 | ✅（2026-10-09。`look_layout.matches` / `row_passes`、`LookTab._apply_visibility` が機能のオン/オフ AND 検索で行を決める。部分一致なので「法線」「視線」の行も出る） |
| E-4 | 右クリックのコピー / 貼り付け / ↺ / base に戻す（パラメータ行・グループ見出し・ツリーの部位 / マテリアル。クリップボードは JSON。知らないキー・型違いは捨てて件数を知らせる。1 回の貼り付け = 1 Undo） | — | 1 | E-2 | hair の「線」をコピーして cloth に貼ると線の値だけが揃い、Ctrl+Z で戻る。メモ帳に貼ると JSON が見える | ✅（2026-10-09。`look.flat_to_payload` / `filter_pasteable` / `restrict_keys`、`look_layout.collect_values`（Qt 非依存でテスト）。`session.set_values` / `reset_values` / `clear_overrides` で 1 回の編集 = 1 Undo。UI は項目の右クリック（ラベル・編集欄）・グループ見出し・左ツリー。ツリーはコピー / 貼り付けのみ（マテリアル全体の ↺ は用意しない）。項目 / グループへの貼り付けは、その範囲のキーだけ入れる） |
| E-5 | 名前付きプリセット（プロジェクトの `presets/*.json`。保存時に含めるグループを選ぶ。一覧 / 適用 / 保存… / フォルダを開く。読み込み時に契約で検証） | — | 1.5 | E-4 | 「影と線だけ」のプリセットを保存して別の部位に適用すると、その 2 グループだけ変わる。Look には何も残らない。壊れた JSON は警告だけで落ちない | ✅（2026-10-09。`presets.py`（Maya / Qt 非依存。build / save / parse / load / list_presets / safe_name。契約の検証は `look.filter_pasteable`）+ `tests/test_presets.py`。UI は `ui_look_dialogs.PresetSaveDialog`・上書き確認（既定「いいえ」）。適用は E-4 の `apply_flat` と同じ経路（1 Undo）。壊れたものは一覧に「（読めません）」で無効表示。`.gitignore` には足さない） |
| E-6 | 影色をベース色から作る…（色相のずらし / 彩度 / 明度。元 = ベース色（乗算）かベースマップの平均色。ライブ反映、OK で 1 Undo、キャンセルで戻す。2 影の色も同じ量ずらす） | T-01 | 1 | E-7 | スライダーを動かすと影色が変わりながらビューポートに出る。キャンセルで元の色。既定値でプリセットの影色に近い色になる | ✅（2026-10-09。`look_layout.shade_from_base` / `shade_pair` / `average_rgb`（HSV 経由、Qt 非依存。白ベース + 既定値が影色のプリセットに各成分 ±0.15 以内であることをテスト）。UI は `ui_look_dialogs.ShadeFromBaseDialog`（開いたときに checkpoint、ライブ反映、OK 1 Undo、キャンセルは `cancel_edit`）。平均色は `QImage` を 32px に縮めて平均。開いた直後に既定値の影色を反映する（キャンセルで戻る）） |
| E-7 | 色のライブ反映（ダイアログのドラッグ中に反映、キャンセルで戻す）+ 色ボタンの右クリックに 16 進 / 0〜1 のコピー・貼り付け | — | 0.5 | — | ピッカーをドラッグすると影色がその場で変わる。キャンセルで元に戻る。`#8070a0` を貼ると色になる | ✅（2026-10-09。`QColorDialog` を `open()`（ウィンドウモーダルで非ブロック）し `currentColorChanged` → live。キャンセルは `session.cancel_edit()`（直前の checkpoint を捨てる。混在でも元の各値に戻る）。16 進 / 数値は `look_layout.parse_color` / `format_hex` / `format_floats`） |
| E-8 | スライダーの操作感（ダブルクリックで ↺、Shift ドラッグで 1/10、ホイールはフォーカス時だけ、数値欄の Enter / Esc、ツールチップに範囲と既定値） | — | 0.5 | — | スライダーの上でホイールを回してもスクロールだけが動く。ダブルクリックでプリセット値に戻る | ✅（2026-10-09。`ToonSlider` / `ToonSpin` / `ToonIntSpin`（ホイールはフォーカス時だけ）。Shift ドラッグは `mouseMoveEvent` で移動量を 1/10 に（Shift を押した後はドラッグの終わりまで相対移動）。数値欄の Esc は `_at_focus` に戻し、変わっていなければ Undo を積まない。既定値は `session.default_value`） |
| E-9 | 表示モード「かんたん / 詳細」（よく使う項目の表は `ui_look.py`。optionVar に記憶。検索中は両方から探す） | — | 0.5 | E-2 E-3 | かんたんにすると約 13 項目だけになり、詳細で全部出る。再起動後も選んだモードのまま | ✅（2026-10-09。表は `look_layout.SIMPLE_KEYS`（`ui_look` からも参照できる。Qt 非依存でテストするため look_layout に置いた）。既定は詳細。オフの機能の行はモードに関係なく隠れる） |
| E-10 | テクスチャ欄のサムネイル・ドラッグ&ドロップ・右クリック（フォルダを開く / パスをコピー） | — | 1 | — | Explorer から PNG をドロップするとベースマップが差し替わり、左に小さい絵が出る | ✅（2026-10-09。`look_layout.path_from_uri` / `first_image_from_uri_list`（Qt 非依存でテスト）。UI は `ui_look_dialogs.TextureThumb`（48px。パスごとに更新時刻つきでキャッシュ）/ `TexturePathEdit`（ドロップ）/ `TextureZoomDialog`（512px 上限）。Maya の file ノード名は `cmds.objExists` + `fileTextureName`。右クリック: フォルダを開く / パスをコピー / クリア。赤表示はそのまま） |
| E-11 | ビューポートの選択に追従（トグル、既定オン）+ ツリーの右クリック「ビューポートで選ぶ」+ ツリーにベース色のチップ。scriptJob は lifecycle に登録 | — | 1 | — | 髪のメッシュをクリックすると、ルックタブの選択が hair に移る。トグルを切ると移らない。ツールをリロードしてもエラーが出ない | ✅（2026-10-09。選択 → ターゲットの決定は `look_layout.follow_target`（フェース優先・未登録は無視・部位内が 1 つなら部位）を Qt 非依存でテスト。選択の読み取りは `preview.selection_materials`（フェースは最大 100 面を調べる）/ `faces_of_materials`。scriptJob は `ui_look` のモジュールの関数 1 つ（`guarded` + `keep_alive` + `on_reload(kill_follow_job)`、登録前に kill して二重登録しない。タブは弱参照、`ToonPanel.detach()` でも kill）。変化は `QTimer` 50ms でまとめ、「ビューポートで選ぶ」の間・直後は追従を止める。ベース色チップは 12px の `QPixmap`） |
| E-12 | 複数の部位 / マテリアルの同時編集（Ctrl / Shift で複数選択、違う値は「—」、タイトルに件数。`session.set_value` の target に列を渡せる） | — | 1 | E-2 | hair と cloth を選んで線幅を動かすと両方が変わる。片方だけ違う値の欄は「—」 | ✅（2026-10-09。`LookTab.targets`（`target` は先頭を返すプロパティ。`select_target(str)` は維持）。`look.target_names` / `materials_of_targets`（重複を除く）を `session.materials_of` が使うので `set_value` / `set_values` / `reset_values` / `clear_overrides` / `values` / `material_feature_state` / `set_part_feature` は str でも列でも動く（`tests/test_look.py`）。値が違えば既存の「—」、タイトルは `look_layout.targets_title`。機能チェックは混在で中間 → クリックで全部同じ値。複数のときは `set_part_feature(列)`。コピーは先頭のマテリアルの値。ツリーを右クリックしたとき、選択済みの行なら選択全体へ。追従は単一選択に置き換えるが、選択中の範囲内のメッシュなら複数選択を壊さない） |

## Phase R: Maya ツールの配布・導入・更新

仕様: [13_distribution.md](13_distribution.md)。D-Drive と同じ「git タグで固定 + 更新ウィンドウ」。

| # | チケット | 技術 | 日数 | 依存 | 受け入れ条件 | 状態 |
|---|---|---|---|---|---|---|
| R-1 | プロジェクトフォルダ（Look・出力・キャプチャ・環境プロファイルの置き場所をツール本体から分離）+ メニュー「プロジェクトを選ぶ…」 | — | 1 | — | 開発用はリポジトリがそのままプロジェクト。別フォルダを選ぶと Look・出力がそこに保存され、テクスチャのパスはプロジェクト基準 | ✅ project.py。開発用はリポジトリ = プロジェクト。テクスチャは $TDRIVE_PROJECT 基準 |
| R-2 | 版の比較・タグ解析・CHANGELOG の範囲・git 呼び出し（`updater.py`、Maya 非依存）+ 単体テスト | — | 1 | — | PATCH / MINOR / MAJOR の判定、範囲の CHANGELOG の抽出がテストで担保される | ✅ updater.py。本物の git（bare の GitHub 役）で更新・元に戻すをテスト |
| R-3 | インストーラー `tools/install.ps1`（前提確認・最新タグに固定・LFS を取らない・.mod 生成・MCP 既定無効） | — | 1 | R-2 | 別フォルダへの clone から 1 コマンドで導入でき、Maya 起動でメニューが出る | ✅ install.ps1 + 配布用 Install-TDriveToon.bat（置いたフォルダ / Maya の最後のプロジェクトを自動で登録）。作業用フォルダで導入・更新を確認 |
| R-4 | 更新ウィンドウ（確認・変更点・更新・前の版に戻す・更新後の確認）+ 起動時の確認（1 日 1 回・更新後の確認待ち） | — | 2 | R-2 R-3 | 古いタグに固定した導入から、ウィンドウ操作だけで新しいタグに更新・元に戻せる | ✅ ui_update.py（更新ウィンドウ・起動時の確認）。mayapy で更新・元に戻す・更新後の確認。画面での確認は N-3 |
| R-5 | マニュアル（導入・更新）と、デザイナー向けの導入手順書 | — | 0.5 | R-3 R-4 | 手順書どおりに導入・更新できる | ✅ マニュアル「はじめに」を書き換え、「更新のしかた」を追加。docs/13 §2.1〜2.2 |
| R-6 | v0.1.0 のリリース（CHANGELOG の互換性・check_release・タグ） | — | 0.5 | R-1〜R-5 | タグ v0.1.0 があり、更新ウィンドウで確認できる（GitHub への push は指示を受けてから） | ✅ v0.1.0・v0.2.0 を GitHub に push（2026-09-29） |

### Phase R 追加（2026-10-14）: 外部ツールパッケージ（URL で追加）

仕様: [13](13_distribution.md) §6。MS2026 のグレーボックス → Maya → Unity 背景パイプライン（`MS2026/Tools/Maya/`）を最初の外部ツールにする。
T-Drive は殻 + 配布・更新の基盤を担い、ゲームと寿命の違うツールは別リポジトリ・別タグで、同じインストーラーと更新ウィンドウから導入・更新する（2026-10-14 決定。MS2026 本体に Maya ツールは置かない）。

| # | チケット | 技術 | 日数 | 依存 | 受け入れ条件 | 状態 |
|---|---|---|---|---|---|---|
| R-7 | `updater.py` を複数リポジトリ対応に（`REPO_ROOT` 固定の `update_to` / `rollback` / `installed_version` / `read_state` を `Git(repo)` で受ける。本体の呼び出しは挙動不変）+ 既存テストが通る | — | 0.5 | R-2 | 本物の git（bare）で、本体と別パスの checkout を同じ関数で更新・元に戻せる。既存の updater テストが変更なしで通る | ✅ `latest_tag` / `clone` を追加。本物の git で別パスの checkout を更新・元に戻すテスト |
| R-8 | `tdrive/external.py`: `tools.json` の読み書き・`tool.json` の検証（必須キー・`id` の文字・`entry`）・`requires.tdrive` の判定・開発用（絶対パスの `dir`）の判定 + 単体テスト | — | 1 | — | 壊れた `tools.json` / `tool.json` は警告だけで他のツールを止めない。`requires.tdrive` を満たさないツールは「読み込まない理由」を返す | ✅ `resolve_tools` が読み込めるツール / 読み込まないツール（理由）/ 警告を返す。12 テスト |
| R-9 | 起動時の読み込み: `userSetup.py` → `shell` が `tools.json` の各ツールの `scripts` を `sys.path` に足し、`entry.create_tool()` を `register_tool`。失敗は `_UnavailableTool`（理由付き）。「ツールをリロード」で外部ツールも再読み込み | — | 1 | R-8 F0-1 | ダミーの外部ツール（tests/fixtures）を登録して Maya を起動すると 1 段目のタブに並ぶ。`entry` が例外を投げても Toon / FacialController は動く | ✅ `tdrive/external_load.py`。フィクスチャ tests/fixtures/external_tool（_broken）。mayapy スモーク。確認項目 docs/18 R9-1〜R9-5 |
| R-10 | 更新ウィンドウの「外部ツール」区画: URL を入力して追加（clone → `tool.json` → 最新タグ → `tools.json` → その場で読み込み）・一覧（名前・版・固定タグ・最新・上げ幅）・更新・元に戻す・変更点・無効化・削除。起動時の確認を各ツールにも | — | 2 | R-7 R-8 R-9 R-4 | mayapy で、bare の GitHub 役からダミーツールを URL 追加 → 新しいタグに更新 → 元に戻す → 削除 が通る。画面の確認項目を [18](18_manual_verification_facial.md) に追加 | ✅ `tdrive/external_ops.py`（Maya 非依存。bare の git で 7 テスト）+ 更新ウィンドウの「外部ツール」区画（mayapy スモーク 22 項目）。起動時は本体とツールを 1 つのメッセージに。確認項目 docs/18 R10-1〜R10-11。マニュアルは R-13 |
| R-11 | `install.ps1 -Tools "<url>[#tag],..."`（`tools.json` へ追記して clone。既にあれば何もしない）。`Install-TDriveToon.bat` は変えない | — | 0.5 | R-10 | 作業用フォルダで `-Tools` 付き導入 → Maya 起動でツールが並ぶ。bat の ASCII / CRLF 検査が通る | ✅ `tools/install_tools.ps1`（`Add-ExternalTools`。install.ps1 から dot-source）。bat は `TD_EXTRA` で渡す。PowerShell + bare git の 8 テスト。実機の導入は未確認（docs/18 R11） |
| R-12 | **MS2026 グレーボックスを外部ツールとして切り出す**: 新リポジトリ `wrenchsun/MS2026-Maya`（`ms2026_maya_*` を `maya/scripts/` へ、エントリーポイント `maya/scripts/ms2026_greybox/tool.py`、`tool.json` / `VERSION` / `CHANGELOG.md` / `create_tool`）。作業フォルダの既定を `$TDRIVE_PROJECT/MayaWork/` に。MS2026 側は `Tools/Maya/` を削除し、`Docs/Guides/MayaGreyboxPipeline.md` に必要な版と URL の表、Unity の取り込みに JSON `version` の警告を追加（MS2026 側はブランチ + PR。別リポジトリの作業なので push は指示を受けてから） | — | 1.5 | R-10 | デザイナーの PC で URL 追加だけで `Tools › T-Drive` にグレーボックスのタブ（または案内）が出て、Export Block → Unity の Import Background が従来どおり通る。MS2026 のハイジーン検査と docs 検査が通る | ✅ 切り出し済み（MS2026-Maya v1.0.0 = 2026-10-14、`tool.json` の `requires.tdrive` は 0.6.0。MS2026 側はブランチ feature/maya-simple-collision に Tools/Maya 削除・版の表・JSON version の警告。push は未）。T-Drive 側で `resolve_tools` が 0.6.0 なら読み込める／0.5.0 なら理由付きで読み込まないことを確認。画面ありの Maya で URL 追加 → タブ・メニュー・ウィンドウまで確認（2026-10-14。docs/18 R12-1） |
| R-13 | マニュアル（「外部ツールを追加する」「更新のしかた」の外部ツール分）と、配る側の手順（docs/13 §6 の運用）。本体の MINOR リリース（`tool.json` の `requires.tdrive` の基準になる版） | — | 0.5 | R-10 R-12 | 手順書どおりにグレーボックスを導入・更新できる。CHANGELOG の `### 互換性` に外部ツールの約束（§6.2）が MINOR で足されたと書いてある | ✅ マニュアル update.html「外部ツール」節・setup.html「配布担当向け」・Readme.html の表（画像 2 枚を 2026-10-14 に実機で撮影）。v0.6.0（ローカルタグ。push は指示を受けてから）。手順どおりの導入は未確認（docs/18 R13） |

### シェーダーのコードレビュー（2026-10-16）の残件

発色が薄い不具合（dx11Shader が sRGB テクスチャをリニア化しない。docs/09 §6）の調査に続けて、ToonCore / Maya ラッパーをレビューした。
その場で直したもの: 画面上の線の半径の丸め（Python の偶数丸め → HLSL と同じ）、MatCap の V 反転、スクリーンスペース輪郭線のベース色混ぜのトーンマップ二重掛け。残りは次のチケット。

| # | チケット | 技術 | 日数 | 依存 | 受け入れ条件 | 状態 |
|---|---|---|---|---|---|---|
| SR-1 | セルフシャドウの参照位置: 本体パスは `_ToonDepthOffset` / 奥行き圧縮で手前に寄せた `positionWS` でシャドウマップを引くが、影を落とすパス（`VS_Shadow`）は寄せない。寄せた分がライト方向に射影され、目・眉・顔で影の境界がずれる。`VSOut` に未変位の位置を持ち `ShadowAtten` にはそれを渡す。Unity も同じ約束（URP の ShadowCaster は未変位）を docs/03 §2.3 に書く | T-10 | 0.5 | — | 目に `_ToonDepthOffset` 0.05 を入れても前髪の影の境界が目の上で動かない | ⬜ |
| SR-2 | Raw 扱いテクスチャの Unity 側の色空間を決めて書く: `_ToonMatCapMap` / `_ToonHairHighlightMap` / `_ToonFaceShadowMap` / `_ToonMaskMap` / `_NormalMap` は sRGB OFF（値を角度・強さとして使う）。MatCap を「絵」として sRGB にする運用なら Maya 側を `srgb=True` + `SrgbToLinear` に揃える。docs/03 §7・docs/08 Q-2 と AssetPostprocessor の対象に明記 | — | 0.25 | — | docs に表があり、Unity の取り込み設定と Maya の `_set_texture(srgb=…)` が 1 対 1 で対応している | ⬜ |
| SR-3 | 透明パスのトーンマップの順序: Maya は tm(src)·a + tm(dst)·(1−a)、Unity は tm(src·a + dst·(1−a))。本筋は Render Override 有効時に本体の `PreviewTonemap` を None にし、合成パス（`TDriveScreenLine.fx`）の最後に 1 回だけ掛ける（スクリーンスペース輪郭線の近似も不要になる）。まず docs/09 §4 に差として載せる | 4-7 | 1 | — | Override あり / なしで同じ見た目。半透明の目のハイライトが Unity のキャプチャと平均 2/255 以内 | ⬜ |
| SR-4 | ToonCore の規則の整理: `uint occluderMask` の型（D-5「float 系のみ」との食い違い）、画面上の線の走査（`EdgeSilhouette` の持ち主探索・4 方向 max）が Maya ラッパーにしか無い件。docs/03 §10.4 の疑似コードを「正」と明記するか、走査 1 段分の純関数を ToonCore に切り出す。ToonId の法線は法線マップ適用前の幾何法線であることを docs/03 §10.1 に書く | — | 0.5 | — | Unity の `ToonRendererFeature` を書く人が Maya ラッパーを読まずに済む | ⬜ |

## Phase U: T-Drive for Unity パッケージ（Unity で再現・一任）

仕様: [08_unity_port_plan.md](08_unity_port_plan.md)。キャラクターのルックは T-Drive パッケージに一任し、D-Drive からは切り離す（互換ブリッジで変換可能）。シェーダー単体で無理なものはコンポーネント・Renderer Feature で実装する。

| # | チケット | 技術 | 日数 | 依存 | 受け入れ条件 | 状態 |
|---|---|---|---|---|---|---|
| U-1 | パッケージ雛形 `unity/com.tdrive.toon`（asmdef: Runtime / Editor / Bridges.DDrive）+ 検証用プロジェクト `unity/TDriveSandbox`（6000.3.13f1 / URP 17.3）、ToonCore.hlsl をパッケージへ移設（D-6） | — | 1 | — | MS2026 に git URL で入れてコンパイルが通る。D-Drive 非依存。Maya の .fx も移設先を include して従来どおり動く | ⬜ |
| U-2 | シェーダー `TDrive/Toon`（ForwardLit / Outline / ShadowCaster / Depth 系、D-Drive と同じ Properties 区分） | P0〜P2 のシェーダー技術 | 3 | U-1 | Maya と同じ値で同等の見た目。SRP Batcher 互換 | ⬜ |
| U-3 | Look インポーター（look.json → `CharacterLook` + マテリアル、差分マージ）+ FBX 取込設定（AssetPostprocessor） | — | 2.5 | U-2 | UnityChan の look.json と FBX から、Maya と同じ部位・値の見た目になる。再インポートで Unity 側の手調整が消えない | ⬜ |
| U-4 | パリティ最小環境（環境プロファイル書き出し・同条件キャプチャ）+ Maya との比較 | — | 1.5 | U-3 1-13 | 正面 / 3/4 / 横 で [09](09_render_parity.md) §5 の合格基準を満たす | ⬜ |
| U-5 | `ToonCharacter` + `ToonLightRig`: キャラクターライト・影の安定化（平滑化・ヒステリシス） | T-02 T-17 | 2 | U-3 | ライトが少し動いても影の境界がパカパカしない | ⬜ |
| U-6 | 受け影の制御（顔は自己影を受けない） | T-16 | 1 | U-5 | 前髪の影が顔に落ちない | ⬜ |
| U-7 | ステンシルによる髪越し表示（ロールから Ref/Comp 自動） | T-24 | 2 | U-5 | 眉・目が前髪の上に、顔の輪郭からはみ出さずに出る | ⬜ |
| U-8 | 表情パラメータ API（`SetExpression`、Timeline/Animator から駆動） | T-25 | 2 | U-5 3-8 | 頬の赤み等を実行時に変えられる | ⬜ |
| U-9 | カメラ角度補正 BlendShape の実行時駆動 / 奥行き圧縮 | T-20 T-22 | 2 | U-5 3-7 | カメラを回すと補正 Shape が混ざる | ⬜ |
| U-10 | `ToonRendererFeature`: ToonId パス + スクリーンスペース インナーライン | T-23 | 4 | U-2 | 部位ごとの有無・太さ・色どおりに内側の線が出る | ⬜ |
| U-20 | `ToonRendererFeature` + シェーダー: 輪郭線のスクリーンスペース（マテリアルから部位の表を作る・スクリーンスペースのマテリアルは Outline パスを描かない） | T-42 | 1.5 | U-17 4-15 | Maya（4-15）と同じ部位に同じ太さ・色で線が出る | ⬜ |
| U-19 | `ToonRendererFeature`: 透かし線（透かしの部位のレンダラーだけ ToonId で別 RT に描き、`Toon_LineSeeThroughPair` で合成） | T-44 | 1.5 | U-10 4-14 | Maya（4-14）と同じ所に線が出る | ⬜ |
| U-18 | シェーダー: セルフシャドウ（メインライトの影を Toon_SelfShadowLit へ、生成シェーダーでは機能オンのときだけ） | T-43 | 1 | U-2 4-11 | Maya（4-11）と同じ部位に影が落ちる | ⬜ |
| U-17 | `ToonRendererFeature`: スクリーンスペース外側輪郭（U-10 のエッジ検出を共有） | T-42 | 1.5 | U-10 4-8 | Maya（4-8）と同じ設定で外形に線が出る | ⬜ |
| U-11 | `ToonRendererFeature`: 接地影 | T-29 | 2 | U-10 | 足元に影が残り、キャラクターが浮いて見えない | ⬜ |
| U-12 | Unity 上のルックエディタ（部位 / ルック / A/B / プレビュー）+ look.json への書き戻し | — | 4 | U-3 | Unity で調整した値を Maya で開ける（往復） | ⬜ |
| U-13 | D-Drive 互換ブリッジ（MaterialData 変換・ShaderConversionTable・命名規約検証。D-Drive があるときだけ有効） | — | 2 | U-3 | D-Drive 入りのプロジェクトで CharacterLook → MaterialData に変換できる。D-Drive 無しでもコンパイルが通る | ⬜ |
| U-15 | プロジェクト専用シェーダーの生成（マテリアルごとの機能の組み合わせ単位で、使わない機能のコード・プロパティ・パスを除いた `TDrive/Toon_<プロジェクト>/<組み合わせ>`、キーワードなし。docs/11 §3）+ 再生成の必要性の検証 | — | 3 | U-2 4-4 | MS2026 のキャラクターに必要な機能だけのシェーダーが生成され、D-Drive 互換の共通名は残る | ⬜ |
| U-16 | 生成シェーダーと汎用シェーダーのパリティテスト | — | 1 | U-15 U-4 | 同じ Look で画素差が基準以内 | ⬜ |
| U-14 | MS2026 への導入手順（git URL・PC_Renderer に Feature 追加・Prefab に ToonCharacter） | — | 0.5 | U-3 U-10 | MS2026 チームが手順どおりに導入できる（手順書 + マニュアル） | ⬜ |
| U-21 | D-Drive 側への提案の文面（[17](17_ddrive_toon_materials.md) §5 の M-1〜M-9。D-Drive のリポジトリは読み取りのみなので、チケットの形にして渡す） | — | 0.5 | — | M-1〜M-9 が D-Drive のチケットの書式になっている | ✅ D-Drive 側で起票済み（FC-11〜FC-19。[17](17_ddrive_toon_materials.md) 冒頭） |
| U-22 | D-Drive ブリッジの書き出しの決まり: 生成シェーダーに無い `_Toon*` を Specific から外す・色だけのマテリアルに白テクスチャ・変換表を `Assets/` に生成・取り込み設定（接線 = 取り込む / 頂点カラー・UV を保持 / ライトマップ UV なし）・マスクのリニア規則の案内（[17](17_ddrive_toon_materials.md) §3・§4） | — | 2 | U-1 | 書き出した MaterialData で D-Drive の検証に警告が出ない。マスクがリニアで取り込まれる | ⬜ |
| U-23 | 検証（D-Drive の `IValidator`）: Renderer Feature の有無・スロットが `DDrive/Lit` に置き換わっていないか・マスクが sRGB になっていないか・T-Drive のキャラクターが自動取り込みの対象に入っていないか（[17](17_ddrive_toon_materials.md) §4 の罠） | — | 1.5 | U-22 | 罠 1〜3 の状態を作ると検証に出る | ⬜ |

## Phase S: サンプルモデルの移行（UnityChan → shizuku）

仕様: [14](14_facial_controller_spec.md) §11。モデルはリポジトリに入れない（規約の確認が取れるまで。`assets/shizuku/` は .gitignore）。

| # | チケット | 技術 | 日数 | 依存 | 受け入れ条件 | 状態 |
|---|---|---|---|---|---|---|
| S-1 | shizuku の取り込み手順: `tools/setup_sample_shizuku.py`（`shizuku.fbx` とテクスチャをプロジェクトフォルダの `assets/shizuku/` へ写し、Maya シーンを作る。元のマテリアル・シェーダーは使わず lambert + テクスチャだけ）+ .gitignore | — | 1 | — | 手順 1 回でシーンが開き、メッシュ 7・ジョイント 219・`mdl_face02` のシェイプ 77 個がある。リポジトリにモデルのファイルが入らない | ✅ `tools/setup_sample_shizuku.py`（実機で実行済み: メッシュ 7・ジョイント 219・顔 77 / 体 14 シェイプ。2 回目は写し 0 件）。テクスチャは `$TDRIVE_PROJECT/assets/shizuku/textures/…` |
| S-2 | shizuku の Look（`looks/shizuku/look.json`）: 部位登録（face / hair / eye / skin / cloth …）・ロール・テクスチャ（`_d` 色 / `_n` 法線。`_m` の扱いを決める）。UnityChan と同じ Toon シェーダー | — | 1.5 | S-1 | Toon 表示で破綻なく表示でき、Unity 出力が検証を通る | ✅ `tools/setup_sample_shizuku_look.py` → `looks/shizuku/look.json`（7 部位: skin / faceOption / hair / wear01〜03 / watchLcd。色 + 法線マップ）。Toon 表示・解除・Unity 出力を裏の Maya で確認（見た目は未確認）。顔の肌は体と同じマテリアル `mat_body01` のため「顔」ロールにできないが、フェイシャル用のサンプルなのでそのままにする。`_m` マスク・腕時計の液晶は未割り当て |
| S-3 | テスト・文書の切り替え: スモークは合成モデルを主に、shizuku は「あるときだけ」。CLAUDE.md の「UnityChan はテスト素体」を更新。マニュアルの画像は規約確認後に差し替え（Q2） | — | 2 | S-2 | UnityChan が無くてもテストが通る。CLAUDE.md・マニュアルの記述が実態と一致 | ⬜ |
| S-4 | 体のアニメーション（`Animations/Base/*.fbx`）を Maya に読み込む手順（参照 / 取り込み）と確認用のシーン | — | 0.5 | S-1 | 待機・歩きを再生しながら Toon と FacialController のプレビューを確認できる | ✅ `tools/load_sample_shizuku_anim.py`（体のアニメーション FBX を既存の骨格へ読み込み、`assets/shizuku/shizuku_anim_<名前>.mb` として保存。23 本すべて読み込める） |
| S-5 | 顔の肌を別の部位にする: 顔メッシュ（`mdl_face02`）の肌の面に、体とは別のマテリアルを割り当てる手順（取り込みスクリプトで `mat_face01` を作って割り当て直す。元の FBX は変えない）→ Look で「顔」ロールに。あわせて自動登録のロール推定を直す（`body` を服と判定する・`wear` を判定できない） | — | 1 | S-2 | 顔だけ影を弱くする・顔の法線・顔影マップが使える。自動登録で skin / cloth が正しく付く | — 見送り（2026-10-03: このモデルはフェイシャル用のサンプルなので、マテリアルの重複は無視してよい） |

## Phase F0: FacialController 基盤（殻 + Maya 非依存のコア）

仕様: [14](14_facial_controller_spec.md)、設計: [15](15_facial_controller_design.md)。移植元: `C:\Users\yamag\wrench\ue\FacialController_UE`（読み取りのみ）。リギングツールは対象外。

| # | チケット | 要件 | 日数 | 依存 | 受け入れ条件 | 状態 |
|---|---|---|---|---|---|---|
| F0-1 | 殻 `tdrive`（2 段タブ「Toon / FacialController」・メニュー「T-Drive」・共通モジュールの移動と転送モジュール・ドッキング位置の引き継ぎ）。Toon の挙動は変えない | — | 2.5 | — | 既存のテストが全部通る。エディタを開くと 1 段目に Toon と FacialController（空のパネル）が並ぶ。古いドッキング位置から開ける。マニュアル（画面構成）更新 | ✅ |
| F0-2 | `tdrive_facial.core`: データクラス・計算（evaluate / view angles / 端のフェード / 表情での弱め / 距離フェード / スムージング）+ 共通のテストデータ（UE の 11 件を写す） | R-12 | 2 | — | pytest で全ケースが許容誤差 1e-4 で一致。maya を import していない | ✅ `tdrive_facial/core/{model,evaluate,naming}.py`。共通のテストデータ `tests/facial/conformance/`（UE の 11 件すべて + Python 版で起こした追加ケース。書式は README） |
| F0-3 | `.fcpose.json` の読み書き・スキーマ（`schema/fcpose.schema.json`）・追加キー・未知キーの保持・座標系の変換（`space.py`）・`±Z` の前方軸 | JSON | 2 | F0-2 | UE 版の実ファイルを読んで書くと意味が同じ。Maya ⇔ UE ⇔ Unity の座標の往復テストが通る | ✅ `core/{fcpose_io,space}.py`・`schema/fcpose.schema.json`。UE 版の実ファイルと全体形式の往復が一致。欠けたキーは UE 版の既定値で読む |
| F0-4 | 自動生成（ミラー + IDW / 最近傍）・格子サイズ変更時の引き継ぎ・データ間のコピー（キーの再サンプル） | R-02 R-09 | 1.5 | F0-2 | テストデータ（autofill）が一致。ミラーで接尾辞の入れ替えとボーンの鏡映が正しい | ✅ `core/autofill.py`（generate_from_keys / interpolate_pose_at_angles / resize_grid / copy_from / mirror_pose）。テストデータ `conformance/autofill.json`（書式は README_autofill.md）。格子サイズ変更は UE 版と違い角度で引き継ぐ |
| F0-5 | 命名規則プロファイル（プリセット 4 種・読み書き・ミラー規則と可動域の自動適用）+ 検証・似た名前の候補・一括改名 + 名前の規則（`naming.py`） | R-15 R-16 R-31 | 2 | F0-3 | プロファイルを選ぶと mirror / limits が入る。存在しない名前・大小違いを検出し、改名を一括で適用できる | ✅ `core/{profile,validate}.py`・プリセット `tdrive_facial/profiles/*.fcprofile.json`（arkit52 / vrchat_viseme / metahuman / shizuku）・`schema/fcprofile.schema.json`。検証コード 32 種（エラー / 警告 / 情報） |
| F0-6 | 画面の状態（Presenter 4 種）と遷移のテストデータ | R-01 R-04 R-11 | 1.5 | F0-4 F0-5 | 操作列 → 状態のテストが通る（Qt なし） | ✅ `core/presenters.py`（PresenterSet / Grid / Pose / Layer / Validation。Qt・maya 非依存）。テストデータ `conformance/presenter.json`（書式は README_presenter.md） |

## Phase F1: FacialController Maya の最小の一周

| # | チケット | 要件 | 日数 | 依存 | 受け入れ条件 | 状態 |
|---|---|---|---|---|---|---|
| F1-1 | セッション（開く / 保存 / Undo / シーンへの記録）+ セットアップタブ（対象メッシュ・基準ボーンの自動検出・前方向・格子・プロファイル・作業セットと強調表示） | R-04 R-07 | 2.5 | F0-1 F0-6 | shizuku で新規作成 → 保存 → シーンを開き直すと同じデータが開く。`bone_head` が自動で選ばれる | ✅ `session.py` + `ui.py`（パネル・ヘッダー・編集の入り切り）+ `ui_setup.py`。スモーク（セッション 168 件・画面 a 102 件）。実機の Maya での見た目・操作感は人の確認待ち（[18](18_manual_verification_facial.md) C 節） |
| F1-2 | グリッドタブ（格子の図・今のカメラ角度・点へカメラ移動・自動生成 / キー解除 / クリア・右クリック・集計） | R-01 R-30 | 3 | F1-1 | 点をクリックするとその角度から顔が見える。カメラを回すと赤い点が動く | ✅ `ui_grid.py`（格子の図・今のカメラ角度・クリックで選択とカメラ移動・自動生成 / キー解除 / クリア・右クリック・集計）。スモーク b 86 件 |
| F1-3 | ポーズタブ（シェイプのスライダー・ボーンは Maya のマニピュレータで動かして取り込み・保存 / 読み直し / ゼロ / 左右反転・ポーズの書き出し / 読み込み） | R-05 R-08 | 3 | F1-2 | シェイプと目のボーンを動かして保存 → 別の点へ移って戻ると再現される。編集を抜けるとシーンが基準へ戻る | ✅ `pose_apply.py` + `ui_pose.py`（スライダー・シーンから取り込む・ボーン・保存 / 読み直す / ゼロ / 左右反転・1 点の書き出し / 読み込み） |
| F1-4 | ベイク（基準姿勢・差分ベイク・しきい値・FC_ だけ作り直す・複数メッシュ・要約・Undo 1 回・ベイクの状態） | R-03 | 3 | F1-3 | 合成モデルのスモーク: 焼いた FC_* を 1 にするとポーズと同じ形（誤差内）。既存ターゲットの番号と差分が不変。Undo で元に戻る | ✅ `bake.py` + グリッドタブのベイク（変更のある点 / 全部 / この点 / このレイヤー）。Neutral の点を焼くと同じ点の感情レイヤーも焼き直す |
| F1-5 | カメラ連動プレビュー（ノード + 生成した expression・手動角度・感情の重み・強さ・補正あり / なし・A/B・ターンテーブル） | R-10 R-12 R-29 | 2.5 | F1-4 | カメラを回すと補正が追従。テストデータの入力で expression の結果 = Python の計算 | ✅ `preview_rig.py` + `ui_preview.py`（グリッドタブ内。作る / 作り直す / 消す・補正あり / なし・カメラ・強さ・感情・手動角度・キーに焼く）。ベイクや格子の変更後は自動で作り直す。ターンテーブルは未実装 |
| F1-6 | レイヤータブ（感情レイヤーの追加・改名・有効 / 無効・削除・コピー・表情での弱めの設定） | R-11 | 1.5 | F1-4 | Joy レイヤーを作って焼き、重み 0 → 1 で上乗せされる（Neutral は変わらない） | ✅ `ui_layers.py`（プリセット 7 種・改名・削除・コピー・補正を弱める強さ・強さを測るシェイプ） |
| F1-7 | 検証タブ（検出一覧・一括改名・再ベイク）+ 他のデータからコピー | R-09 R-31 | 2 | F1-4 | シェイプ名を変えたモデルで不一致が一覧に出て、候補から一括で直せる | ✅ `ui_validate.py`（一覧・候補から一括改名・無い参照の削除・変更のある点だけベイク・点へ移動）。他のデータからのコピーはグリッドタブ「他のデータからコピー…」（`anim_import.copy_from_file`） |
| F1-8 | 出力タブ: Unity 向け FBX（FC_* 入り・`fcs_*` と作業用ノードを除く）+ `.fcpose` の写し、UE 版向け JSON | JSON | 2 | F1-4 | 出力した FBX に FC_* がそろい fcs_* が無い。UE 版で JSON を読める（手動確認） | ✅ `export.py` + `ui_export.py`（Unity 向け FBX + `.fcpose` を別プロセスで・UE 版向け・Timeline 用 `.fctrack`）。shizuku で Maya → Unity の通しを確認（`tools/setup_sample_shizuku_facial.py`: FC_ 30 個・ベイク 0.97 秒・出力 7.3 秒・FBX 6.2 MB） |
| F1-9 | T-20（カメラ角度補正 BlendShape）のデータを格子へ取り込む | — | 1 | F1-3 | UnityChan / shizuku の T-20 の 3 キーが Yaw 0 / 45 / 90° のキーとして入る。T-20 側は消さない | ✅ `session.import_t20` + セットアップタブのボタン（正面 / 3/4 / 横 → Yaw 0 / 45 / 90° のキー。T-20 と Look は変えない）。開いている Look からの取り込みは実機で未確認 |
| F1-10 | デザイナーマニュアル: FacialController の章（はじめに・セットアップ・グリッド・ポーズ・ベイク・プレビュー・出力）と「今できること」 | — | 1.5 | F1-8 | 各チケットで該当ページを同じコミットで更新したうえで、通しで読めること | ✅ `docs/DesignerManual/facial*.html`（6 ページ: とは・はじめに・グリッド・ポーズ・レイヤーと検証・出力と Unity）+ 画像 7 枚（テスト用の小さなモデルで撮影）。Readme・character・troubleshooting から参照 |

## Phase F2: Maya ならではのシェイプ作成支援

| # | チケット | 要件 | 日数 | 依存 | 受け入れ条件 | 状態 |
|---|---|---|---|---|---|---|
| F2-1 | この角度で彫る（`fcs_*` を作ってスカルプトへ → ポーズに重み 1 を記録 → ベイクに含まれる） | R-24 | 2.5 | F1-4 | 横顔で口元を彫る → ベイク → プレビューで横顔のときだけ出る。fcs_* は Unity 出力に入らない | ✅ `shapes.py` + `ui_shapes.py`「この角度で彫る / 彫り終わる / 彫りを消す」。裏の Maya で頂点を動かして確認（実際のスカルプトツールでの操作は人の確認待ち） |
| F2-2 | 左右に分ける・ミラー（対称の対応表・対応が取れない頂点の表示） | R-24 | 2 | F1-1 | 一体のシェイプから `_L` / `_R` ができ、足すと元の形。`_L` を直してミラーで `_R` が更新される | ✅ 左右に分ける（足すと元の形）・ミラー（対応が取れない頂点を選択して止まる）。左右の軸はメッシュの X（+X が L）を前提 |
| F2-3 | ポーズをシェイプにする・中間形・誇張形（`_Ex`）・組み合わせ補正 | R-24 R-37 | 2 | F1-4 | 今の状態を 1 個のシェイプにできる。2 つのシェイプの同時使用の補正が作れる | ✅ ポーズをシェイプにする・中間形・誇張形（`_Ex` を作り可動域を 0〜2 に。評価は F5）・組み合わせ補正（`fcs_combo_…`。Maya の中だけの評価でベイクに含まれる） |
| F2-4 | 別メッシュへ写す（同じ頂点 = 複写 / 違う = 近接で転写） | R-24 | 2 | F2-2 | 顔のシェイプをまつ毛・別の頭へ写せる | ✅ 同じ頂点なら複写、違えば近接（proximityWrap）で転写。写し先の元からのシェイプは確認なしで上書きしない |
| F2-5 | 整理（微小な差分の掃除・空 / 未使用の一覧・プロファイルに沿った改名・不足一覧）・ゴースト表示 | R-15 | 1.5 | F0-5 | 不足している標準シェイプと未使用のシェイプが一覧に出る | ✅ 微小な差分の掃除・空 / 未使用 / 孤立の一覧と削除（fcs_ / FC_ だけ）・標準シェイプの不足。ゴースト表示は未実装 |
| F2-6 | 格子のサムネイル・ビューポートの格子の球（点をクリック） | R-30 | 2 | F1-2 | 各点の小さな画像が格子に出る。球の点をクリックで選べる | ✅ `thumbnails.py` + グリッドタブ「サムネイルを表示 / 作り直す」（一時カメラで撮影・一時フォルダに保存・ポーズが変わった画像は出さない）。**実際の撮影（playblast）は裏の Maya では試せないため未確認**。ビューポートの格子の球は未実装 |
| F2-7 | アニメから読み込む（今のフレーム / Unity の `.anim` / ARKit 名のカーブ）+ 土台の表情としてのプレビュー | R-26 | 2 | F1-3 | shizuku の `face_happy01.anim` を読んでポーズ・土台の表情にできる | ✅ `core/unity_anim.py`（Unity の .anim の読み取り。PyYAML なし）・`anim_import.py`・ポーズタブ「アニメから読み込む…」と「土台の表情」。shizuku の表情アニメ 49 個がすべて読める（`face_happy01.anim` は 10 シェイプすべて一致）。ARKit 名などの名前の対応付けも可 |
| F2-8 | キーに焼く（時間範囲をサンプルして FC_* の重みにキー → プレビューの仕組みを外す） | R-19 | 1 | F1-5 | 焼いたあとプレビュー用ノードを消しても同じ見た目で再生される | ✅ `preview_rig.bake_to_keys` + プレビューの「キーに焼く…」（F1-5 と一緒に実装） |

## Phase FU: FacialController Unity（ランタイム・プレビュー・調整）

パッケージ `unity/com.tdrive.facial`（[15](15_facial_controller_design.md) §5）。D-Drive に依存しない。

| # | チケット | 要件 | 日数 | 依存 | 受け入れ条件 | 状態 |
|---|---|---|---|---|---|---|
| FU-0 | パッケージ雛形（asmdef 5 つ・package.json・検証用プロジェクト `unity/TDriveSandbox` を U-1 と共用） | — | 1 | — | Sandbox と MS2026 でコンパイルが通る。D-Drive なしでも通る | ✅ `unity/com.tdrive.facial`（package.json・asmdef 7 つ・.meta）。D-Drive の Unity プロジェクト（6000.3.13f1、ブランチ `tdrive-facial`、ローカルパッケージ）でコンパイルと EditMode テスト 168 件の通過を確認。検証用プロジェクト `unity/TDriveSandbox` は作らず D-Drive のプロジェクトで確認する。MS2026 での確認は導入時 |
| FU-1 | `TDrive.Facial.Core`（C# の計算）+ 共通のテストデータの EditMode テスト | R-12 | 2 | F0-2 FU-0 | Python と同じテストデータが全部通る | ✅ `Runtime/Core/{FacialCore,FacialSpace,FacialTypes}.cs`。Unity を使わない `unity/FacialCoreTests`（dotnet test）で共通のテストデータ 167 件が Python と一致。Unity の EditMode でも同じ 167 件が一致 |
| FU-2 | 取り込み（`.fcpose` → `FacialCorrectionData`）・座標変換・FBX の取り込み設定の確認 | JSON R-13 | 2 | FU-1 F0-3 | Maya の出力を置くとデータができる。取り込み直しても Overrides が残る | ✅ `Runtime/Core/{FcposeReader,FcposeModel,FacialNaming,MiniJson}.cs`・`Runtime/Data/{FacialCorrectionData,FacialCorrectionOverrides,FacialPoseAsset}.cs`・`Editor/Import/{FcposeImporter,FcposeConverter}.cs`。shizuku の出力を Unity に置いての取り込みも確認済み（`SampleShizukuTests`。サンプルが無い環境では飛ばす） |
| FU-3 | `FacialCorrectionRunner`（視点の解決・重み・表情での弱め・距離フェード・スムージング / スナップ・LOD / 非表示の打ち切り・シェイプ番号の事前解決・OnDisable で戻す） | R-10 R-11 R-12 R-28 R-29 | 3.5 | FU-2 | shizuku でカメラを回すと Maya のプレビューと同じ重み（数値一致）。プールへ返すと FC_* が 0 | ✅ `Runtime/FacialCorrectionRunner.cs`（LateUpdate・視点の解決・スナップ / スムージング・表情での弱め・距離フェード・OnDisable で戻す・1 フレーム限りの上書き `PushOverride`・毎フレームの割り当て 0）。Unity の EditMode テスト 252 件通過（合成メッシュ）。LOD の打ち切り（`maxLod`）は値を持つだけで未使用。shizuku でも確認（FBX のシェイプ名は `bs.FC_…` になるため、名前の解決を「完全一致 → `.` + 名前」の順に修正）。D-Drive 1.4.0 の実行順の検査からの除外を宣言（`FacialCameraExecutionOrderExemption`）。再生しての見た目は人の確認待ち |
| FU-4 | マテリアル出力（MaterialPropertyBlock: `_FC_Angles` / `_FC_Emotion*`） | R-14 | 1 | FU-3 | 対象の Renderer にだけ値が入る（2 体で衝突しない） | ✅ `Runtime/FacialMaterialOutput.cs`（`_FC_Angles` / `_FC_Emotion0…` を MaterialPropertyBlock へ。グローバルには書かない。Runner の `materialOutput` で指定も可） |
| FU-5 | エディタ: プレビュー（Scene ビューのカメラ・A/B・ターンテーブル）・格子ビューア・検証 | R-01 R-30 R-31 | 3 | FU-3 | 再生せずに補正を確認でき、抜けると元に戻る。FC_* の不足が検証に出る | ✅ プレビュー（`Editor/Preview/FacialPreviewDriver.cs`: Scene ビューのカメラ / ターンテーブル / 角度指定・A/B。保存・再生・再コンパイル・終了の前に重みを戻す。シーンを汚さない）・格子ビューア（`Editor/Grid/`、メニュー Tools/T-Drive/Facial/グリッド）・検証（`Editor/Validation/FacialValidation.cs`）。計算はテスト済み、ウィンドウの見た目とカメラの動きは人の確認待ち |
| FU-6 | 簡単なパラメータ調整: `FacialCorrectionOverrides`・Runner のインスペクター・JSON への書き出し（Maya へ戻す） | — | 1.5 | FU-3 | 強さ・追従などを変えて保存 → 取り込み直しても残る → 書き出した値を Maya で読める | ✅ Runner / Data / Overrides のインスペクター（状態・調整・感情の重み・視点・検証）、調整用アセットの作成、Maya へ戻す JSON の書き出し（`policy` / `quality`。端のフェード（`grid.edgeFade`）も上書きしているときは書き出す） |
| FU-7 | デバッグ表示（HUD・3D 表示） | R-27 | 1 | FU-3 | 角度・重み・スナップが実行中に見える | ✅ `Runtime/FacialDebugOverlay.cs`（レベル 0 切 / 1 HUD / 2 HUD + 3D 表示）。見た目は人の確認待ち |
| FU-8 | マニュアル（Unity での使い方・調整できる値・取り込み設定） | — | 1 | FU-6 | — | ✅ `facial-export.html` に Unity での使い方・調整できる値・準備中の一覧（Unity の画面の画像は無し） |

## Phase FT: TimeLine / D-Drive 連携

仕様: [14](14_facial_controller_spec.md) §7、D-Drive 側の変更: [16](16_ddrive_changes_for_facial.md)。

| # | チケット | 要件 | 日数 | 依存 | 受け入れ条件 | 状態 |
|---|---|---|---|---|---|---|
| FT-1 | `FacialCorrectionTrack` / `Clip` / `Mixer`（強さ・感情の重み・角度の固定・視点・カット補正・コマ打ち。クリップのブレンド。プレビュー後に元へ戻す） | R-36 | 3 | FU-3 | Timeline のスクラブと再生で補正が変わる。プレビューを抜けると元の値 | ✅ `Runtime/Timeline/`（FacialCorrectionTrack / Clip / Mixer。強さ・感情の重み・角度の固定（部分的な重なりはカメラ角度と補間）・視点・カット補正（ポーズを加算、終わると元に戻る）。再生していないスクラブでも補正が見える）。コマ打ちは値を渡すだけ（F5）。Timeline のテスト 16 件。Timeline ウィンドウでの操作感は人の確認待ち |
| FT-2 | D-Drive ブリッジ: バインドの補助（同じ役名の AnimationTrack）・検証（IValidator）・プール返却の確認テスト・D-Drive のカットシーンでのサンプル | — | 2.5 | FT-1 | MS2026 で `Cutscene.Play` → 補正が動く・シークとスキップに追従。D-Drive を変更していない | ✅ `Bridges/DDrive/`（`<役名>_Facial` → 同じ役名の AnimationTrack のバインド先・D-Drive の検証 `TD-FACIAL-001〜005`）。プール返却はインスタンスが無効化されるので Runner が FC_* を戻す（追加の処理は不要）。D-Drive は変更していない。`Cutscene.Play` での再生は人の確認待ち |
| FT-3 | Maya → Timeline: `.fctrack` の出力（プレビュー用ノードのキー）+ Unity 側で `.playable` にトラックを足す / 更新する | — | 3 | FT-2 F1-5 | Maya で打ったキーが Timeline のクリップ / カーブになる。再出力でデザイナーが足したトラックは残る | ✅ Maya: 出力タブの `.fctrack`。Unity: `FctrackImporter` → `FacialTrackAsset`、クリップがカーブで動く、`FacialTrackTimelineSync`（`<役名>_Facial(auto)` を 1 本だけ管理）、D-Drive では `Assets/SourceAssets/Cutscene/<分類>/<ショット>__<モデル>.fctrack` を置くと自動で反映して SameAsTrack で結ぶ（順番が前後したら再試行 + メニューでやり直し）。**実際のカットシーンでの取り込みは未確認** |
| FT-4 | D-Drive 側への提案の起票（チケット 7-8 の書き換え案・C-1〜C-9）※ D-Drive のリポジトリは読み取りのみなので、文面を用意して渡す | — | 0.5 | — | [16](16_ddrive_changes_for_facial.md) の内容が D-Drive のチケットの形になっている | ✅ D-Drive 側で起票済み（D-Drive `docs/51_tdrive_integration.md`・`docs/11_tasks.md` FC-0〜FC-10、PR #86）。対応表は [16](16_ddrive_changes_for_facial.md) 冒頭 |
| FT-5 | D-Drive の新しい拡張点に合わせる（D-Drive main `8af02ea` の FC-1 / FC-2 / FC-12）: ブリッジで Runner を `IModelInstanceListener` に対応（スポーン後にキャッシュを作り直す・返却前に戻す）、FC-2 の重みの復元と Runner の戻しが二重になっても壊れないテスト、`.fctrack` の取り込み（FT-3）では `SameAsTrack` の Binding を足す | — | 1.5 | FT-2 | D-Drive のプールから出し直したキャラクターで補正が正しく掛かる。SameAsTrack で結んだ Facial トラックが動く | ✅ `FacialModelInstanceBridge`（モデルの Prefab に付ける。スポーン後に作り直し・返却前に戻す）、D-Drive の重みの復元と二重になっても壊れないテスト、検証が SameAsTrack をたどる。**D-Drive 1.4.0 のタグが出たら版の条件を付ける**（今はブリッジが FC-1 / FC-12 入りの D-Drive を前提にする） |
| FT-6 | D-Drive の新しい拡張点に合わせる 2（origin/main `51e1827` の FC-3 / FC-5）: `.fctrack` の反映を `ICutsceneImportListener` へ（後追いの再試行は古い D-Drive 用に残す）、視点の最後の手段に `ViewCamera.TryGetCurrent`（ブリッジが設定。Runner の実行順 10000 は変えない）。版の条件付きアセンブリに置く | — | 1.5 | FT-5 | FBX と `.fctrack` をどちらの順で置いても 1 回の取り込みで Timeline に入る。分割画面などで視点を差し替えた D-Drive でも補正がその視点に従う | ✅ `FctrackImportListener`（条件付きの `TDrive.Facial.DDrive.FC.Editor`）で、FBX の取り込みと同じ回に `.fctrack` を反映（保存・Undo なし、何度呼ばれても重複しない）。通知がある D-Drive では後追いの再試行をしない。視点は `FacialViewResolver.Fallback` に `ViewCamera.TryGetCurrent` を設定（視点の指定 → これ → メインカメラ）。**条件付きアセンブリは D-Drive 1.4.0 のタグ、または `TDRIVE_DDRIVE_FC_FORCE` が要る。実際の取り込みは未確認** |

## Phase F5: UE 版の未実装項目

UE 版で未実装（文書だけ）の機能。計算に関わるものは Python と C# の両方 + 共通のテストデータを足す。既定値では今までと同じ結果にする。

| # | チケット | 要件 | 日数 | 依存 | 受け入れ条件 | 状態 |
|---|---|---|---|---|---|---|
| F5-1 | キー角度のシャープニング（`w' = w^s / Σ w^s`） | R-32 | 1 | F0-2 FU-1 | s = 1 で従来と同じ。s を上げるとキー角度付近でキーのポーズそのものになる | ✅ 計算（Python / C#・共通の適合ベクトル）、Unity の Runner、Maya のプレビュー（s ≠ 1 のときだけ式に入る）、セットアップ › 品質 |
| F5-2 | コマ打ち（更新の間引き。スムージングと排他） | R-33 | 1 | FU-3 | 12 fps 指定で補正が 2 コマ打ちになる | ✅ `step_gate`、Unity の Runner / 調整値 / クリップ。Maya のプレビューには掛けない（欄だけ） |
| F5-3 | 距離でレイヤーの重み（重みの入力元: カーブ / 直接 / 距離） | R-35 | 1.5 | FU-3 | 遠景でデフォルメ用レイヤーが効く | ✅ `layerWeights`（direct / distance）、レイヤータブ ›「重みの出どころ」、Maya のプレビュー・Unity で同じ式。curve は名前だけ（direct と同じ扱い） |
| F5-4 | パース補正（画角 / 距離の 1 次元のキー・`FC_<asset>_Persp_K{n}`・編集 UI） | R-34 | 3.5 | F1-4 FU-3 | 広角・近接のときだけ奥行きを圧縮する補正が掛かる | ✅ 計算（Python / C#・共通のテストデータ 30 件）、`.fcpose` の `perspective`、ベイク `FC_<asset>_Persp_K{n}`、Maya の「パース補正」の箱（キーが編集の対象になる）・プレビュー・`.fctrack` の `perspective`、Unity の Runner・調整値・クリップ・検証。**実際の見え方は未確認** |
| F5-5 | 誇張（重み 1〜2・`_Ex` の焼き分け・ランタイムの受け渡し・可動域の上限 2） | R-37 | 2.5 | F1-4 FU-3 | 重み 1.5 で通常と誇張の中間。0〜1 は従来と同じ | ✅ ベイクで 1 超の分を `FC_…_Ex` に焼き分け（感情レイヤーは Neutral の Ex を引く）、検証（未ベイク・不要な Ex）、プレビューの「誇張」、Unity の評価・調整値・クリップ。`.fctrack` には入れない。**シーンの見え方（元シェイプの `<名前>_Ex` へのクロスフェード）は未対応 = 直線の延長** |
| F5-6 | 補正からの除外・レイヤーのミュート・部位別の強さ（目まわりだけ補正 0 など） | R-17 | 1.5 | F1-4 FU-3 | 除外したシェイプは焼かれない。部位別の強さが効く | ✅ 除外パターン、レイヤーのミュート、**部位別の強さ（ベイクのときに掛ける。`bake.partStrength`、セットアップタブ、変えたら自動で変更あり）**。ゲーム中の部位別の変更は対象外（2026-10-04 決定） |
| F5-7 | LOD1 以降のメッシュへのベイク | — | 1.5 | F1-4 | LOD メッシュにも同じ名前の FC_* ができる | ✅ `target.lodMeshes`、メッシュごとの差分を同じ名前の FC_ として焼く、プレビュー・FBX 出力、検証（メッシュ無し・ターゲット無し・補正が足りない）、Unity の「書き込む LOD の上限」（0 = 制限なし）。**実際の LOD 付きキャラクターでは未確認** |
| F5-8 | リップシンクの対応表（音素 × 感情 × 声量 → シェイプ）: データ形式・Maya の編集 UI・Unity で口形を出す口。音声解析は外部 | R-18 | 4 | FU-3 | 音素と声量を渡すと口形が出る（shizuku の `lipSync_a/i/u/e/o`） | ✅ 対応表 `lipSync`（音素 × 感情 → シェイプ、声量、強さ、追従）、計算（Python / C#・共通のテストデータ 39 件）、プロファイルの `lipSync`（shizuku / VRChat ビセム / ARKit）、Maya の「リップシンク」タブ（マスが編集の対象・「試す」）、Unity の `Runner.SetLipSync`・調整値・プレビュー・検証、uLipSync とのつなぎ（uLipSync v3.1.5 と一緒にコンパイルできることを確認）。**実際の音声での動作は未確認** |
| F5-9 | Toon シェーダー側の受け口（角度に応じた線幅・顔影の補正）。`_Toon` 接頭辞・機能として追加 | R-14 | 2 | FU-4 | 横顔のときだけ線幅を変えられる。機能オフで従来と同じ | ✅ Toon シェーダー側（機能「顔の角度連動」）、Maya のプレビューからのつなぎ込み（「Toon マテリアルに顔の角度を渡す」）、Unity の `_ToonFacialAngles`。実際の Maya で、角度の入力が出ること・陰の境目と線幅が変わることを確認（2026-10-04。手で値を入れて確認。**プレビューからつないだ状態の見え方は未確認**） |
| F5-10 | 輪郭を大きく崩すアニメとの整合: ガイドライン（崩し用レイヤー・弱め設定）をマニュアルに。破綻例を集めて個別ルールを検討 | R-23 | 1 | F1-6 | マニュアルに手順がある | ✅ マニュアルに手引き（弱める → ショットごとに切る → 崩し専用のレイヤー → 決め構図は手で直す）。破綻例の収集と個別ルールは、例が集まってから |
| F5-11 | 補間の種類（Catmull-Rom）※ 既定は双線形のまま | — | 1.5 | F0-2 FU-1 | 切り替えで中間角度が滑らかになる。既定では結果が変わらない | ✅ `quality.interpolation`（bilinear / catmullRom）。Python / C# の計算と共通のテストデータ 27 件、Maya のプレビュー、Unity の Runner・調整値。既定では結果が変わらない |
| F5-12 | 誇張の強さを `.fctrack` に入れる（Maya の出力・検証、Unity の取り込み・クリップのカーブ）。無いファイルは従来どおり | R-37 | 1 | F5-5 FT-3 | Maya で打った誇張のキーどおりに Unity の Timeline で誇張が動く。古い `.fctrack` も読める | ✅ Maya: キーのある「誇張」を固定カーブ `exaggeration` として書き出す。Unity: 読み取り → クリップが従う（クリップの「誇張を使う」が優先）。**誇張のキー入りの `.fctrack` は、古い Unity パッケージでは取り込みに失敗する**（知らないカーブ名はエラー） |
| F5-13 | 除外パターンを変えたら自動で「変更あり」（ベイク時の除外パターンを記録して比べる） | R-17 | 0.5 | F5-6 | パターンを変えると全点が焼き直しの対象になり、焼くと消える。変えていなければ既存データは変わらない | ✅ シェイプごとに「焼いたときの除外パターンの印」をシーンに記録（`tdFacialBakeExclude`）。記録の無い古いベイクは変更なし扱い。一部だけ焼いても、焼いていない点の印は残る |

保留（作らない）: テクスチャ / VAT へのベイク、MetaHuman、推奨構成の自動割り当て、リギングツール（R-20 含む）、Unity でのポーズ編集・ベイク。

## Phase AN: アニメーション作業台（命名規則・クリップ・ブレンド / 遷移のプレビュー・分割出力・ショット。2026-10-22 追加）

仕様 [20](20_animation_workbench_spec.md)。**AN-1〜AN-9 実装済み（2026-10-22。AN-10 は保留）**。対象は MS2026 の本番キャラクター MisotenChara（mGear リグ）。**DirectX 11 でない環境でも動く**（dx11Shader・Render Override を使わない。[20](20_animation_workbench_spec.md) §1.1）。
Phase A（アニメの“嘘”）より先に進める（作業台が A 系の土台になる）。1 段目タブ「Animation」の中に 命名 / クリップ / ブレンド / ショット / 出力 の 5 タブ。
共通の完了条件（マニュアル・確認項目）に加えて: **Maya 非依存の core は pytest、Maya 依存層は mayapy のヘッドレスのスモーク**で担保する。確認項目は [18](18_manual_verification_facial.md) に「AN」の節を足す。マニュアルは `docs/DesignerManual/anim*.html`。

| # | チケット | 技術 | 日数 | 依存 | 受け入れ条件 | 状態 |
|---|---|---|---|---|---|---|
| AN-0 | 仕様 [20](20_animation_workbench_spec.md)（MS2026 / D-Drive / リグの調査、命名規則、クリップ・ブレンド・ショット・出力の仕様、設計） | — | 1 | — | 決めること §9 が列挙され、チケットが切れている | ✅ 2026-10-22 |
| AN-1 | **殻に Animation タブ + `tdrive_anim` の骨格**: `_BUILTIN_TOOLS` に追加、メニュー `Tools › T-Drive › Animation…`、`session.py`（Undo の束ね・変更通知）、5 つのサブタブの空の枠、core の `animset.py`（§3.3 読み書き・検証・スキーマ）、fixture のミニリグ（`tests/fixtures/anim/`） | — | 1.5 | — | タブが出て Ctrl+Z が殻から届く。`animset.json` の読み書き・検証が pytest で担保。mayapy のスモークがヘッドレスで通る。マニュアル `anim.html`（作業の流れ・用語・今できること） | ✅ 2026-10-22 |
| AN-2 | **命名タブ**: 規則（§2。`core/naming_rules.py` + `anim/_naming.json` で上書き）、シーンの検査一覧（種類 / 今の名前 / 提案 / 理由 / 重大度）、改名計画の適用（1 Undo。Look の参照と `_tdToon` の追従。割り当ては変えない）、掃除候補、シーン設定（30 fps / cm / Y-up のワンクリック修正）、Humanoid 対応表（§2.4 推定・編集・検証・`humanoid.json`） | — | 3 | AN-1 | MisotenChara_Rig.ma を検査すると 24 fps・メッシュ名・マテリアル名・残骸 namespace が一覧に出て、適用で `SK_` / `M_` 名になり Look が壊れない。Humanoid 必須 15 ボーンが自動推定で埋まる。規則の判定は pytest | ✅ 2026-10-22 |
| AN-3 | **クリップタブ**: クリップ（§3.1 の属性）の一覧・検索・右クリック、「現在の範囲から追加」、ループ再生（元の再生範囲を戻す）、イベント（`OnFootPlant` 左右ほか）、サムネイル（FacialController の `thumbnails.py` を流用）、`animset.json` へ保存・シーンを開いたとき自動で読む | — | 2 | AN-1 | クリップを 3 つ作って再生・保存・開き直しで戻る。重複名はエラー。mayapy スモーク | ✅ 2026-10-22 |
| AN-4 | **ブレンドのプレビュー**: 遷移列（§4.2 の型）→ Time Editor の合成 `tdAnimComp_*` の生成 / 解除（線形・固定秒のクロスフェード = Unity の `CrossFadeInFixedTime`）、速度、`core/sequence.py`（配置の計算）、Time Editor が使えないときの `pairBlend` フォールバック、ブレンドタブの表（追加 / 削除 / 上下 / 右クリック）と再生 | — | 2.5 | AN-3 | Idle → Run を 0.08 s でクロスフェードした合成が OpenGL のビューポートで再生できる。配置の計算は pytest。合成を外すと元のカーブ・再生範囲に戻る | ✅ 2026-10-22（Time Editor ではなく pairBlend 方式。docs/20 §4.1） |
| AN-5 | **遷移のプレビュー（MS2026 と同じ状態図）**: `core/state_machine.py`（パラメータの時系列 → 遷移列。既定は `AC_PlayerPreview` と同じ Idle / Run / Jump・0.08 s・Exit Time なし）、パラメータ UI（`Speed` スライダー・`ActionState` コンボ。再生中に効く）、`UpperBody` レイヤー（マスク + ウェイト）、状態図の `animset.json` への保存 | — | 2.5 | AN-4 | pytest: Idle 1 s → Speed 0.5 で Run に 0.08 s で遷移、ActionState=2/3 で Idle・Run から Jump（`AC_PlayerPreview.controller` と同じ遷移・並び順）。Maya: スライダーを動かすと再生中に遷移する。上半身だけ別クリップが乗る | ✅ 2026-10-22 |
| AN-6 | **Unity 用の分割出力**: `SK_<ModelId>.fbx`（Toon の `export_in_place` に相乗り + `humanoid.json` / `animset.json` の写し）、クリップごとの `A_<ModelId>_<動作>.fbx`（ジョイントのみ・ベイク・1 テイク・`rootMotion=none` は Hips の XZ 固定・`@` 不使用）、変更のあるクリップだけ、出力先プリセット（既定 / D-Drive / MS2026 の `SourceAssets`）、出力タブ（3 区画・進捗・ログ・❌ で無効）、`ExportJob` の共通化（`tdrive.export_job`、転送モジュールで公開名維持） | — | 2.5 | AN-2 AN-3 | mayapy で fixture のリグから `SK_*.fbx` 1 本と `A_*.fbx` 2 本が出て、FBX にメッシュが入っていない / 入っていることをテストで確認。命名の ❌ があると出力が止まる | ✅ 2026-10-22（テイク名は Take 001 のまま。docs/20 §6.2） |
| AN-7 | **ショットの作成**: `shots/<Shot>.shot.json`（§5.1）、`tdShot_<Shot>` グループ、カメラ区間と Camera Sequencer の同期、キャラクターのクリップ列（ブレンドタブから「この列をショットへ」）、手付けキー用の animLayer、`EVT_` イベント、シーケンサー再生と `playblast`（`captures/shots/`） | — | 2.5 | AN-4 | ショットを作ってカメラ 2 台の切替とクリップ列が Maya で再生できる。`shot.json` の読み書き・検証は pytest | ✅ 2026-10-22 |
| AN-8 | **ショットの出力**: `<Shot>.fbx`（Ubercam + `PRP_` + `EVT_`）、`<Shot>__<ModelId>.fbx`（ジョイント階層だけ・ベイク）、`.fctrack`（既存を呼ぶ）、D-Drive のオプション（§5.3）、同名上書き、`tools/export_fbx_batch.py --mode shot` | — | 2 | AN-6 AN-7 | fixture のショットから 2 本の FBX が出て、キャラクター FBX にメッシュが無い・カメラ FBX にカメラが 1 台だけあることをテストで確認。D-Drive（ブランチ `tdrive-facial`）に置いて CutsceneData が自動で作られる（人が確認） | ✅ 2026-10-22（D-Drive での取り込みは人の確認待ち） |
| AN-9 | **マニュアルと確認項目の総点検**: `anim.html` `anim-naming.html` `anim-clips.html` `anim-blend.html` `anim-shot.html` `anim-export.html`、Readme の「今できること」、スクリーンショット（実機）、[18](18_manual_verification_facial.md) AN 節、[10](10_manual_verification_2026-09-28.md) に「OpenGL で Animation タブ」の項目、CHANGELOG | — | 1 | AN-1〜AN-8 | 準備中の表記と実装が食い違わない。MisotenChara で一周（命名 → クリップ → ブレンド → 出力 → ショット → 出力）を人が確認できる手順になっている | ✅ 2026-10-22（スクリーンショットは実機が要るので「準備中」のまま。裏の Maya で MisotenChara_Rig の一周は通る） |
| AN-11-1 | **ブレンドツリーの重み計算**（Maya 非依存 `core/blend_tree.py`）: 1D / 2DSimpleDirectional / 2DFreeformDirectional / 2DFreeformCartesian を Unity と同じ式で。状態図 JSON の `blendTree`（§4.5）の読み書き・検証（モーションのクリップが存在・しきい値の重複・2D の位置の重複） | — | 1.5 | AN-5 | pytest: モーション上で 1、1D の中点で 0.5/0.5、Simple Directional の原点で中央のみ、Freeform の代表点が Unity の値（手計算の固定値）と一致。スキーマの検証 | ⬜ |
| AN-11-2 | **N 本の合成とブレンドタブの「Blend Tree パラメータ」節**: 正規化時間で同期した N 本の重み付き合成（位置は重み付き和・回転は正規化重みの slerp）、パラメータの変更で作り直し（止まって 0.15 秒後）、2D パッド（160 px、0〜1 / -1〜1 切替、ドラッグ、重みの %）、float スライダー、合成に各モーションの重みを残す（AN-F-5 の表示に使う） | — | 2.5 | AN-11-1 AN-F-6 | mayapy: 4 方向 + 中央の 2D ツリーでパッドの四隅・中点・原点の合成値が重みどおり。1D の中点で 2 クリップの半々。MisotenChara 規模で作り直し 1 秒以内。見た目は人（docs/18 AN-11 節） | ⬜ |
| AN-11-3 | **雛形とマニュアル・出力**: `_F/_B/_L/_R` が揃ったら「2D の移動ツリーを作る」/ Idle・Walk・Run で「1D の Locomotion を作る」、animset への保存、Unity 用の `animset.json` にそのまま出る（U-A1 の入力）、マニュアル anim-blend.html の節、D-Drive の AnimEditor との対応表（遷移シーケンス / レイヤー / Blend Tree = 3 つとも揃ったことを明記）、確認項目 | — | 1 | AN-11-2 | 雛形で作った状態図が保存・再読込で戻る。マニュアルに D-Drive との対応表 | ⬜ |
| AN-10 | （保留）OpenGL 用の簡易セル表示（ramp / surfaceShader の 2 階調。Unity との一致は保証しない） | — | 2 | AN-1 | DX11 が無い PC でも影の段が見える | ⬜ 保留 |

Unity 側（U-A1 / U-A2。[20](20_animation_workbench_spec.md) §8）は Phase U に積む。

### 実機確認（2026-10-23、MisotenChara_Rig.ma + mGear）で見つかった修正チケット AN-F

確認項目は [18](18_manual_verification_facial.md) AN 節の結果メモ。直し方の決まりは [21](21_code_review_2026-10-22.md) 冒頭と同じ（再現テストを先に書く → 直す → docs/18 の該当行の「自動」欄と結果メモを更新）。

| # | 内容 | 重さ | 日数 | AC | 状態 |
|---|---|---|---|---|---|
| AN-F-6 | **mGear リグで合成が作られない**。`composition.animated_channels` が子プラグ（`rotateX`…）しか見ないため、複合プラグ（`joint.rotate` ← `mgear_matrixConstraint.rotate`）でつながるジョイントが「つながりなし」になる。複合プラグの入力も見て 3 チャンネルを対象にし、外すときは複合プラグへつなぎ直す。`getAttr(time=)` のサンプリングは複合のまま可 | 高 | 1 | fixture のミニリグに「複合プラグで decomposeMatrix からつなぐ」ジョイントを足したスモークで、合成 → 値の一致 → 外して元の接続に戻る。MisotenChara_Rig.ma があればそれでも | ✅（1f2d59c） |
| AN-F-1 | クリップ / ブレンドタブの案内「先に animset を作ってください」が、animset を作ったあとも消えない（`refresh()` が has=True のとき message を消さない） | 中 | 0.2 | animset 作成後に案内が消える（AnimPanel のテスト） | ✅（a41af4e） |
| AN-F-3 | ブレンドタブの既定の遷移列は最初に開いたときの 1 回しか作られず、あとで足したクリップ（Jump）が載らない。未編集（既定のまま）ならクリップの増減で作り直す。「既定の列に戻す」ボタンも足す | 中 | 0.5 | Idle/Run だけで開いたあと Jump を足すと 3 行になる（pytest + パネル） | ✅（ae5af07） |
| AN-F-4 | Animation タブのスピンボックス・コンボ・スライダーが、マウスが重なっただけのホイールで値が変わる。Phase E-8 と同じ「フォーカスしているときだけ」に（`tdrive_toon.ui_look` の作法を `tdrive` の共通ウィジェットに切り出し、Toon / Facial / Animation で共用） | 中 | 0.5 | `test_ui_patterns.py` に「ホイールはフォーカス時だけ」の検査を足し、3 ツールで通る | ✅（f176aa7） |
| AN-F-5 | 再生中に今のブレンドの割合を表示する（ブレンドタブのバナーに「Idle 70% / Run 30%（f=35）」。timeChanged の scriptJob。合成の重みカーブから計算） | 低 | 0.5 | 合成中にタイムスライダーを動かすと割合が変わる（mayapy で値、見た目は人） | ✅（258a086） |
| AN-F-2 | クリップ一覧・遷移列の表に「ダブルクリックで編集」の一言（表の下のグレーの注記）。「現在の範囲から追加」の動作コンボに「入力もできます」のプレースホルダー | 低 | 0.2 | — | ✅（dea1abf） |
| AN-F-7 | 「ツールをリロード」で古い `AnimSession`（`export_state` の無い版）から引き継ぐとき `AttributeError` で警告が出る。`getattr(..., None)` で守り、無ければ「引き継ぎなし」と出す | 低 | 0.2 | 古い形のセッションを渡しても例外にならない（pytest） | ✅（c7f80e8） |
| AN-F-8 | クリップタブの「▶ 再生」のあとの一時停止・コマ送りは Maya 標準（`Alt+V`、`Alt+.`／`Alt+,`）で行う旨をマニュアル anim-clips.html に書く | 低 | 0.1 | — | ✅（dea1abf） |
| AN-F-9 | 命名タブのテクスチャ検査が、どの面にも割り当てられていないマテリアルにつながる file ノードまで拾う（マテリアルは割り当て済みだけなのと食い違う）。割り当て済みマテリアルに届く file だけにし、部位名はマテリアル名から推定（`HairM` → `Hair`。`T_<ModelId>_Part_BaseColor2…` の連番を減らす） | 中 | 0.5 | 未割り当てマテリアルの file が一覧に出ない。割り当て済みの file は部位名がマテリアル由来になる（mayapy） | ✅（6065b9b） |
| AN-F-10 | 命名タブの表示（検査の一覧・シーン設定・掃除候補）が、「適用」「削除」「直す」のあとや Maya 側の Undo / Redo に追従しない（検査を押すまで古いまま）。適用・削除・直すの直後は自動で再検査し、`Undo` / `Redo` / `timeUnitChanged` / `NameChanged` の scriptJob（`lifecycle.keep_alive` + `on_reload`。連続する変化は 0.3 秒まとめる）で再検査する。重いシーンでは一覧だけ更新 | 中 | 0.5 | 改名を Undo すると一覧の「今の名前」が戻る。単位を変えると表示が変わる（mayapy で scriptJob の登録と更新） | ✅（7a2586b） |
| AN-F-11 | 掃除候補の「未使用のマテリアル」が shadingEngine ごとに並び、同じマテリアル（`Hair` × 5 SG）が重複して見える。マテリアル 1 行にまとめて、消すときはその SG を全部消す（割り当てのある SG が 1 つでもあれば候補に出さない） | 低 | 0.3 | Hair が 1 行になり、削除で 5 つの SG が消える（mayapy） | ✅（17948da） |
| AN-F-12 | 命名タブの「適用」「直す」「削除」はシーンを変える Maya の操作なのに、エディタにフォーカスした Ctrl+Z はツールの Undo（animset だけ）に行き、戻らない。ツールの Undo 履歴に「Maya のチャンク名」を積み、ツールの undo がそれを取り出したら `cmds.undo()` を呼ぶ（順序が食い違うときは「ビューポートで Ctrl+Z してください」と出す）。マニュアル anim-naming.html にも明記 | 中 | 1 | エディタで Ctrl+Z → 改名・削除・fps が戻る（mayapy で undo の順序）。Facial の A-7 の挙動は変えない | ✅（ab0a3f8） |
| AN-F-13 | **fps を直すとクリップ範囲がキーとずれる**。「直す」（24 → 30 fps）は Maya の既定どおりキーの時刻を保つのでフレーム番号が ×1.25 になるが、animset のクリップ範囲はそのまま → 範囲にキーが無い / 別の動きを出力する（実機: Run 100〜118 がキー 125〜147.5 とずれ、A_ の中身が Idle→Run の間の補間になった）。直し方: 「直す」のダイアログで「キーの時刻を保つ（フレーム番号が変わる。クリップ範囲も同じ比率で直す・既定）/ フレーム番号を保つ（`updateAnimation=False`。再生速度が変わる）」を選ばせ、前者はクリップ・ショットの範囲とイベントのフレームを比率で直す（1 Undo。animset 側はツールの Undo）。クリップタブは「範囲にルート以下のキーが無い」クリップに ⚠ を出す。docs/20 §9 Q-5 の答えとして書く | 高 | 1 | 24 fps で 0〜60 のクリップを 30 fps に直すと 0〜75 になり、出力の中身が同じ動き（mayapy で値を比較）。範囲にキーの無いクリップに ⚠ | ✅（8788300） |
| AN-F-14 | **モデル出力の骨がバインドポーズにならない**（コンストレイント駆動のリグ）。`export_model_in_place` は dagPose の restore に頼るが、mGear のようにジョイントが `mgear_matrixConstraint` / `decomposeMatrix` に駆動されていると効かず、出力時の現在フレームのポーズで書かれる。直し方（別プロセスの出力用シーンの中で）: ① animCurve を全部切る ② キーのあったコントローラ（と `rig_controllers_grp` セットの中身）の keyable な transform をデフォルト値に戻す（mGear の「ゼロに戻す」相当） ③ 各ジョイントのワールド行列を skinCluster の `bindPreMatrix` の逆行列と比べ、差が閾値を超えたら「バインドポーズに戻せない」の ⚠ をログに出す（出力は続ける）。dagPose があって一致するならそれを優先 | 高 | 1 | ミニリグ（decomposeMatrix 駆動 + キー付き）で、任意のフレームで出力しても骨が bindPreMatrix と一致（mayapy）。MisotenChara でも一致 | ✅（e45fd24） |
| AN-F-15 | **シーケンサー再生でカメラが切り替わらない**。`shot_scene.play` が通常の `cmds.play` なので、Camera Sequencer の shot があってもビューポートのカメラは変わらない（playblast は `sequenceTime=True` で切り替わる）。シーケンス時間で再生する（Maya の「シーケンス時間で再生」= `cmds.sequenceManager` / タイムスライダーの sequence time モードを調べて使う。無理なら timeChanged の scriptJob で `sequenceManager(currentTime)` と shot の `currentCamera` に合わせて `lookThru` を切り替える）。止めたら元のカメラに戻す | 高 | 1 | CAM_Main 0〜120 / CAM_Close 121〜240 のショットを再生すると 121 フレームからパネルのカメラが CAM_Close になる（mayapy で modelPanel のカメラ、見た目は人） | ✅（ee6f9f5） |
| AN-F-16 | 「ショットを反映」がパネルのカメラを CAM_* に切り替えたまま戻さない（タンブルすると撮影カメラが動く）。反映前のカメラを覚え、「合成を外す」/ ショットを閉じる / ウィンドウを閉じるで戻す。マニュアルに「元に戻すには Panels › Perspective › persp」も書く | 中 | 0.3 | 反映 → 外す でパネルのカメラが persp に戻る（mayapy） | ✅（2bc13fa） |
| AN-F-17 | **警告・結果のメッセージ欄が画面の外**。各タブの `message` ラベルがスクロール領域の最下部にあり（ショットタブで y=878、表示域 674）、不正な名前の ⚠ や「保存しました」が見えない。Toon / Facial と同じく、タブの最上部（スクロール領域の外）に固定のメッセージ行を置く。⚠ は 1 回点滅かオレンジ背景で気づけるように | 高 | 0.5 | 全タブで message がスクロール領域の外・最上部にある（AnimPanel のテスト）。見た目は人 | ✅（a7fd1c5） |
| AN-F-18 | **出力先プリセット「D-Drive」「MS2026」を外す**（ユーザー判断 2026-10-23: デザイナーの PC に D-Drive は無く、MS2026 も人による。共有は Google Drive が主）。ブランチ判定（CR-6）・`SourceAssets` へのパス組み立て・「続けますか？」も削除。代わりに「既定（build/…）」と「**共有フォルダ…**（任意のフォルダを選ぶ。モデル / アニメ / ショットの下位フォルダ名は MS2026 の `SourceAssets` と同じ構成で作る）」の 2 択にし、選んだフォルダは `project.py` の設定（ユーザーごと・Git 管理外）に覚える。docs/20 §5.3・§6.1・§6.4・§9 Q-4 とマニュアル anim-export.html を直す | 中 | 0.5 | プリセットのコンボが 2 択。共有フォルダに出すと `Model/<Category>/SK_…` `Anim/<Category>/A_…` `Cutscene/<Category>/<Shot>…` の構成でできる（mayapy）。D-Drive / MS2026 の文言が UI とマニュアルに残らない | ✅（9bcd7a7） |
| AN-F-21 | **合成の作成で Maya が長く固まる**（MisotenChara: 131 ジョイント × 113 フレームで十数秒〜。クロスフェードを変えるたびに同じ待ち）。原因は `_sample` がジョイント × 系統（translate / rotate / scale）× フレームごとに `getAttr(time=)` でリグ全体を評価していること。直し方: フレームごとに 1 回 `currentTime` を動かし（`undo_off` の中、終わったら戻す）、全ジョイントの値をまとめて読む（評価回数 ≈ フレーム数）。同じクリップを 2 回サンプリングしない（キャッシュ）。進捗バー（QProgressDialog。フレームごとに `processEvents`）と「中止」（中止したら合成を作らず元のまま）。目標: MisotenChara の 113 フレームで 3 秒以内 | 高 | 1 | mayapy で fixture のリグの合成の値が従来と同じ（回帰）。評価回数のカウントが フレーム数 + 定数 以下。進捗の中止で何も残らない | ✅（ffc19de） |
| AN-F-20 | ブレンドタブの遷移列（表）が、既定の列の作り直し（AN-F-3）や animset の変更の直後に更新されない（ウィンドウを開き直すと反映。AN-F-10 と同種）。セッションの変更通知で `_fill_rows` を呼ぶ | 中 | 0.3 | クリップを足した直後に行が増える（AnimPanel のテスト） | ✅（12c078d） |
| AN-F-22 | 「今のブレンド: Idle 70% / Run 30%」（AN-F-5）が完全に停止しているときしか更新されない（再生中・タイムスライダーのドラッグ中は Qt の再描画が走らない）。ビューポートの HUD（`headsUpDisplay`。合成中だけ出し、外すと消す）にも同じ文字を出す。Qt 側は `timeChanged` のあと `QWidget.repaint()` を試す | 低 | 0.5 | 再生中に HUD の割合が変わる（人）。HUD が合成を外すと消える（mayapy） | ✅（246a93d） |
| AN-F-23 | 出力タブに「合成を外す」ボタン（合成中で出力が止まっているとき、ブレンド / ショットタブへ戻らずにその場で外せる。理由の行の右に置く。ユーザー要望 2026-10-23） | 低 | 0.2 | 合成中に出力タブのボタンで外れ、出力ボタンが有効になる（mayapy） | ✅（70dcf0d） |
| AN-F-24 | **クリップ出力（`A_*.fbx`）が複合プラグ駆動のリグで壊れる**（mGear）。`export_clip_in_place` の `bakeResults` は `mgear_matrixConstraint.rotate` → `joint.rotate` の複合接続を外さず（Maya の constraint 型ではない）、子プラグに animCurve がつながった状態で複合接続も残る。さらに `keyframe(relative, timeChange=-s)` の移動が効かず、キーが 125〜148 のまま・`BakeComplexStart/End` だけ 0〜23 になる（実機の `A_MisotenChara_Run_F.fbx` で確認。Unity では 0〜23 の範囲にキーが無く動かない）。直し方: ベイクの前に、`composition.animated_channels` と同じ判定で複合プラグの入力を外す（値は `getAttr(time=)` で全フレームをサンプリングして自前でキーを打つ = 合成と同じ経路にし、`bakeResults` に頼らない）→ キーの移動は `cmds.keyframe(curves, edit=True, relative=True, timeChange=-s)` を**カーブ名で**行い、移動後に先頭が 0 であることを検査してから `FBXExport`（違えば例外で止める）。ショット出力（`<Shot>__<ModelId>.fbx`、同じ関数系）も同じ修正 | 高 | 1 | fixture の複合プラグ駆動のミニリグで、出力した FBX を読み戻すとキーが 0〜長さ・値が元の評価と一致・コンストレイント系ノードが入っていない（mayapy）。`assets/misoten/MisotenChara_Rig.ma` があればそれでも | ✅（4406a3c） |
| AN-F-25 | **ショットの再生はシーケンス時間で駆動する**（AN-F-15 の方式を置き換え）。実機で確認: `cmds.play` 中は `timeChanged` の scriptJob による `lookThru` が効かず、しかも `lookThru` は `sequenceManager -modelPanel` の結び付けを外す。一方 `cmds.sequenceManager(currentTime=f)` は即座にそのフレームのカメラへ切り替え、シーンの時刻も動かす（Maya の Camera Sequencer の仕組み）。直し方: ショットタブの ▶ は Maya の `play` を使わず、**QTimer のトランスポート**（シーンの fps、`lifecycle.keep_alive` / `on_reload` で止める）で毎ティック `sequenceManager(currentTime=f)` を進める（範囲の最後で止まる / ループ任意、■ で止める、Esc は Maya の再生ではないので効かない旨を表示）。`sequenceManager(modelPanel=パネル)` は反映時に結び、`lookThru` は使わない。止めたら `lookThru(panel, 元のカメラ)` で戻す（AN-F-16 と一体）。クリップ / ブレンドタブの ▶ は従来どおり `play` | 高 | 1 | mayapy: トランスポートを 1 ティックずつ進めると shot の区間どおりに `modelPanel` のカメラが変わり、止めると元のカメラに戻る。実機: 151 フレームで CAM_Close に切り替わる（人） | ✅（e6536a4） |
| AN-F-26 | **合成の作成がまだ約 10 秒**（MisotenChara 131 ジョイント × 113 フレーム。進捗バーが 99% になったあと約 7 秒止まる = サンプリングの後の「カーブ・pairBlend・blendWeighted の作成と接続」が進捗に含まれず、そこが重い）。直し方: ① 進捗をノード作成・接続の段階まで延ばし、各段階の所要時間をログに出す ② ノード作成・接続を `OpenMaya.MDGModifier`（まとめて `doIt`）で行う、または 1 ジョイントあたりのノード数を減らす（translate / rotate / scale を 1 つの pairBlend で、チャンネルごとの blendWeighted をやめる）③ 既に同じクリップのサンプリング結果があれば再利用（クロスフェードや切替位置だけ変えたときはサンプリングを飛ばす）。目標: MisotenChara の 113 フレームで**作成全体が 3 秒以内**、パラメータ変更での作り直しは 1 秒以内 | 高 | 1.5 | mayapy で fixture と MisotenChara の所要時間を段階ごとに計測して記録（スモークのログ）。値の回帰なし。進捗が最後まで動く | ✅（6afe3c0） |
| AN-F-27 | **状態図のパラメータ操作の UX**: スライダーのドラッグ中の `valueChanged` ごとに時刻付きで記録され、「記録したパラメータ」が数百件の生データになり読めない。さらに作り直しの固まり（AN-F-26）で遷移が起きたか分からない。直し方: ① 記録はドラッグを離したとき（`sliderReleased` / コンボの `activated`）だけ。同じ時刻（同じフレーム）の記録は上書き ② 表示は AN-F-29 のタイムラインと表に置き換える（生データの羅列はやめる） ③ 作り直しの前後で再生位置を保つのは今のまま。作り直し中は「作り直し中…」の表示 ④ 「長さ」の既定は記録の最後の時刻 + 2 秒（8 秒固定をやめる） ⑤ 合成が「この列で合成を作る」（rows）で作られているときにパラメータを動かしたら、「状態図で合成を作る」を押すよう最上部のメッセージで案内する（実機: rows の合成のままスライダーを動かして「反映されない」と見えた） ⑥ Speed のスライダーに「0.1 を境に Idle ⇄ Run（この状態図では速さの違いは出ない。速さのブレンドは AN-11）」のツールチップ | 中 | 1 | AnimPanel のテストでドラッグ中の記録が 1 件にまとまる。評価結果の遷移列が表に出る（mayapy）。操作感は人 | ✅（12b8aab） |
| AN-F-28 | ブレンドタブの「MS2026 の状態図から」ボタンを**廃止**（押しても見た目の反応が無く、合成を作るボタンと誤解される。MS2026 を名指しする UI も docs/20 §9.1 の方針に反する）。「状態図で合成を作る」が状態図未設定なら既定（Idle / Run / Jump。`AC_PlayerPreview` と同じ規則）を自動で使う（今もそう）。節の見出しを「状態図（Idle / Run / Jump）をパラメータで動かす」にし、既定に戻す操作は「パラメータを最初から」の隣の小さな「状態図を既定に戻す」に。マニュアル anim-blend.html も「MS2026」の名指しを「既定の状態図（プレイヤーと同じ規則）」に直す | 低 | 0.3 | ボタンが無く、未設定でも「状態図で合成を作る」が通る（AnimPanel のテスト）。UI とマニュアルに「MS2026 から」の文言が残らない | ✅（12b8aab） |
| AN-F-29 | **パラメータのタイムライン**（ユーザー要望 2026-10-23。AN-F-27 の②の表示をこれに置き換える）。ブレンドタブの状態図の節に、Maya のタイムスライダーと同期した横長のタイムライン（合成の「長さ」の範囲。現在時刻の赤い線は `timeChanged` の scriptJob で追従、再生中は HUD と同じ事情で止まるので止めたとき追従）を置き、記録したパラメータを**マーカー**で示す（ActionState は上段に色付きの大きなマーカー + 「Jump(2)」の文字、Speed は下段に小さなマーカー + 値。評価してできた状態の区間を帯で下に表示: Idle / Run / Jump の色）。操作: マーカーをクリックで選択（右の小さな表と同期。表は 時刻 / パラメータ / 値 / 結果の状態）、ドラッグで時刻を移動（フレームにスナップ）、Delete キー・右クリックで削除、ダブルクリック（または「現在フレームに追加」ボタン）で今のスライダー / コンボの値をそのフレームに追加。**まとめて作成**: 「変更のたびに作り直す」トグル（既定オフ）。オフのときは記録だけして、「作り直す」ボタンで 1 回だけ合成を作る（未反映の変更があれば最上部に「作り直していません」の表示）。記録は animset には保存しない（プレビュー用。シーンの fileInfo に JSON で持ち、開き直しで戻る） | 中 | 1.5 | AnimPanel のテスト: マーカーの追加・移動・削除が timeline に反映、トグルオフで作り直しが起きず「作り直す」で 1 回だけ起きる。帯の区間が `state_machine.evaluate` の結果と一致。見た目・操作感は人（docs/18 に項目を足す） | ✅（b53d85c） |
| AN-F-30 | 「変更のあるクリップだけ」の指紋（`.export-state.json`）がキーのハッシュだけなので、出力プログラムを直しても（AN-F-24）「変更なし」で飛ばされる。指紋に出力形式の版（`EXPORT_FORMAT_VERSION`。出力の中身を変える修正で上げる）と animset のクリップ属性（範囲・rootMotion・loop・イベント）を含める | 低 | 0.3 | 版を上げると全クリップが再出力される。属性だけ変えても再出力される（mayapy） | ✅（a525388） |
| AN-F-31 | **ショットの再生が 2 回目からカメラを切り替えない**（実機: 1 回目は 151 で CAM_Close に切り替わる。2 回目の ▶ は最初から CAM_Close のまま。「合成を外す」→「反映」のあとは切り替わらない）。疑い: ① `play()` が「▶ 直前のパネルのカメラ」を restore として覚えるため、1 回目の終了（`stop()` の `lookThru`）後に 2 回目の restore が CAM_Close になる／② `lookThru` のあとの `sequenceManager(modelPanel=)` の結び直しが効かず（Maya がパネルの明示カメラを優先）、以後 `sequenceManager(currentTime=)` でカメラが変わらない。直し方: 「元のカメラ」は**反映の直前**に 1 回だけ覚える（`_reflected_camera` と統合。CAM_* を覗いている状態は元として覚えない）。結び直しは `lookThru` → `sequenceManager(modelPanel=)` の順で、効くかを mayapy で検証し、効かなければ結び直しの前にパネルのカメラを一度 `persp` に戻す／パネルを作り直さない範囲で効く手順を探す。mayapy のスモークに「play → 終了 → play → 終了 → 外す → 反映 → play」の連続でカメラが毎回 151 で切り替わる検査を足す | 高 | 0.5 | 上の連続シナリオが mayapy で通る。実機で 2 回目以降も切り替わる（人） | ✅（b600c07） |
| AN-F-32 | パラメータのタイムライン（AN-F-29）をクリック / ドラッグすると Maya の現在時刻もそのフレームに動く（スクラブ。マーカーの上以外をクリックしたとき。ドラッグ中は 0.03 秒まとめ）。ユーザー要望 2026-10-23 | 低 | 0.3 | クリックで `currentTime` が変わる（AnimPanel のテスト） | ✅（6f49338） |
| AN-F-33 | Speed スライダーの右に数値入力欄（0.00〜1.00、`editingFinished` で記録）。ユーザー要望 2026-10-23 | 低 | 0.2 | 入力で記録が 1 件増える（AnimPanel のテスト） | ✅（6f49338） |
| AN-F-34 | パラメータのタイムラインに**フレーム番号**を出す（横軸の目盛りに 0 / 30 / 60 … のフレーム、マーカーのラベルと表に「f=90（3.00s）」の形で秒とフレームの両方、現在時刻の線の横に今のフレーム）。ユーザー要望 2026-10-23 | 低 | 0.3 | 目盛りとラベルにフレームが出る（AnimPanel のテスト） | ✅（6f49338） |
| AN-F-35 | **停止ボタンの改修**: 画面では止まって見えるのに内部で時刻が動き続ける状態がある（実機: Maya のログに `currentTime` が流れ続け、裏からの問い合わせが通らない。ショットの QTimer トランスポート、合成の作り直し、再生中の scriptJob のどれかが残っている疑い）。直し方: ① ブレンド / ショット / クリップの「■ 停止」はどのタブからでも**全部**止める（Maya の `play`、ショットのトランスポート、作り直しの待ちタイマー、追従の scriptJob）② 最上部のメッセージ行に「再生中（ショット / クリップ / ブレンド）」「停止」の表示を常に出す ③ ウィンドウを閉じる・リロード・シーンを開くでも全部止める（`detach` / `on_reload` / `SceneOpened`）④ 原因の切り分け: mayapy で「▶ → ■」「▶ → ウィンドウを閉じる」「▶ → リロード」の 3 通りのあとに `QTimer` と scriptJob が残らない検査。実機データ: 停止後に QTimer は 0 件だが、古い追従の `conditionChange=[playingBack, _cb_playing_changed]` が 3 つ（リロードごとに増えた）と `timeChanged` の `_cb_time_changed` が 1 つ残っていた（旧モジュールの関数のまま生存）。また Maya の playbackSpeed が 0.25 のまま戻っていなかった（SF-17 の控えが効いていない場面がある） | 高 | 0.5 | 3 通りのあとにアクティブな QTimer・追従 scriptJob・`play` 状態が無い（mayapy）。実機で ■ のあと Script Editor に `currentTime` が流れない（人） | ✅（17fe65b） |
| AN-F-36 | 合成の作成で Maya が「インタンジェントをステップに設定できません」の警告を毎回出す（複製カーブの接線を step にしようとして弾かれる。step は outTangent だけに設定し、inTangent は linear / auto のまま） | 低 | 0.2 | 合成の作成で警告が出ない（mayapy のログ検査） | ✅（17fe65b） |
| AN-F-37 | **合成中にリグのコントローラとメッシュの動きが一致しない**（合成はジョイントを直接動かすので、mGear の `*_ctl` は元のキーのポーズのまま。見ていて混乱する）。直し方: 合成の作成時にコントローラを非表示にするオプション（既定オン。`rig_controllers_grp` セットの中身、無ければ `*_ctl` の transform / nurbsCurve の表示を切る。表示状態を控えて「外す」で戻す。ショット・ブレンド共通）。バナーに「合成中はコントローラは動きません（非表示にしています）」。ユーザー指摘 2026-10-23 | 中 | 0.5 | fixture のミニリグ（`*_ctl` 付き）で合成中に ctl が非表示になり、外すと戻る（mayapy）。MisotenChara は人 | ✅（a1c626d） |
| AN-F-38 | ショットタブのキャラクター表で**クリップ列の中身が見えない**。「クリップ列」のセルに要約（例: `Idle 1.0s → Run_F 1.5s → Jump`。長ければ「…」+ ツールチップに全行）を出し、ダブルクリックで読み取り専用の一覧（クリップ / 保持 / クロスフェード / 切替位置 / 速度）を開く。ユーザー要望 2026-10-23 | 低 | 0.3 | 列を入れるとセルに要約が出る（AnimPanel のテスト） | ✅（7a6295e） |
| AN-F-39 | **T-Drive のウィンドウを閉じても合成が外れない**（実機: 閉じたあとも `tdAnimComp_blend`・pairBlend 270・ジョイントの接続・再生範囲 0〜113・`timeChanged` の scriptJob・パネルの CAM_Main が残る）。原因: workspaceControl の「閉じる」は**非表示**にするだけで widget は destroy されず、殻の `detach` が走らない。直し方: 殻（`tdrive/shell.py`）が workspaceControl の `visibleChangeCommand`（または `closeCommand`）で非表示を検知して各ツールの `detach()` を呼ぶ（表示に戻ったら `build` し直すか、`refresh` で復帰）。Animation の `detach` は 合成を全部外す・トランスポートと再生を止める・scriptJob を kill・パネルのカメラを戻す・再生範囲と速度を戻す（AN-F-35 と同じ経路）。Toon / Facial の `detach` も同じ契機で呼ばれるようになるので、既存の挙動が変わらないことを確認する | 高 | 0.5 | mayapy: workspaceControl を非表示にすると `detach` が呼ばれ、合成・scriptJob・QTimer が残らない。再表示でタブが使える。実機: 閉じる → 合成が外れてキャラクターが元の動きに戻る（人） | ✅（0b4ecc2） |
| AN-F-40 | 遷移列の表の 1 列目（クリップ名の埋め込みコンボ）の上で右クリックしても行メニューが出ない（コンボが受け取る）。コンボの `contextMenuPolicy` を `CustomContextMenu` にして行メニューへ転送する（表の他の列と同じ位置計算）。ショットタブのキャラクター表・クリップタブの詳細のコンボも同じ扱いに | 低 | 0.2 | コンボ上の右クリックで同じメニューが出る（AnimPanel のテスト） | ✅（e94407d） |
| AN-F-41 | 出力の作業フォルダ `%TEMP%/tdrive_anim_export_*` が消えずに溜まる（実機で約 60 個。成功・中止・失敗のどれでも残る）。出力ジョブの終了（成功 / 中止 / 例外）で必ず `shutil.rmtree`、消せなかったものは起動時に 1 日より古いものを掃除。Toon / Facial の出力ジョブ（共通化した `tdrive.export_job`）も同じ扱い | 低 | 0.3 | mayapy で出力後・中止後に作業フォルダが残らない。古いフォルダが起動時に消える | ✅（e94407d） |
| AN-F-42 | **リロード / 停止で戻す再生範囲・速度・ループが「直前の値」でない**（実機: 範囲 10〜200・速度 1×・ループ continuous にしてからクリップ ▶ → ■ を押さずにリロード → 0〜300・速度 0（毎フレーム再生）・once に戻った）。控え（`clips.py` の再生範囲 / `_saved_speed` / `playstate`）が ▶ のたびではなく最初の 1 回だけ取られている、またはリロードの引き継ぎで古い控えが残っている疑い。直し方: 控えは「▶ の直前」に毎回取り、■ / リロード / 閉じるで戻したら捨てる。控えが無いときは何も変えない。mayapy で「範囲 A で ▶ → ■ → 範囲 B で ▶ → リロード」のあと範囲が B・速度とループも B のときの値、を検査 | 中 | 0.5 | 上の検査が通る。実機で ▶ 前の値に戻る（人） | ⬜ |
| AN-F-43 | リロード直後、ブレンドタブの遷移列の表が空に見える（別のタブへ移って戻ると復活。引き継いだ rows が最初の `refresh` に反映されていない）。殻がタブを作り直したあとの初回表示で `refresh()`（`_fill_rows`）を確実に呼ぶ | 低 | 0.2 | リロード直後に rows が表に出る（AnimPanel のテスト） | ⬜ |
| AN-F-44 | 「新規シーン」で animset は閉じるが、ブレンドタブの状態図の節（状態図・パラメータの記録・タイムライン）に前のシーンの内容が残る。animset を閉じるときに状態図・記録・タイムライン表示も初期化する（SF-10 の範囲に含める） | 低 | 0.2 | 新規シーン後に状態図の節が初期表示（AnimPanel のテスト） | ⬜ |



## Phase A: アニメーション（ルック完了後）

| # | チケット | 技術 | 日数 | 依存 | 受け入れ条件 | 状態 |
|---|---|---|---|---|---|---|
| A-1 | 階層遅延・フォロースルー（体→頭→髪）のプレビューと調整 | T-31 | 3 | Phase 3 | 髪が体より遅れて大きく動く設定を Maya で作り Unity へ渡せる | ⬜ |
| A-2 | 瞬きの誇張・左右ずれ | T-32 | 1 | — | — | ⬜ |
| A-3 | 呼吸・セリフ中の微動 | T-33 | 1 | — | — | ⬜ |
| A-4 | 表情の瞬間強調 | T-34 | 2 | — | — | ⬜ |
| A-5 | リップシンク誇張 | T-35 | 2 | — | — | ⬜ |
| A-6 | Squash & Stretch / オーバーシュート | T-36 | 3 | A-1 | — | ⬜ |
| A-7 | Smear | T-37 | 4 | A-6 | — | ⬜ |
| A-8 | カメラ依存のポーズ補正 | T-38 | 3 | 3-7 | — | ⬜ |
| A-9 | ポーズ/カメラ依存の形状誇張 | T-39 | 3 | 3-7 | — | ⬜ |

A 系の詳細仕様は Phase 3 完了時に起こす。A 系は Phase AN の作業台（クリップ・ブレンド・ショット）の上に載せる。

## サマリ

| Phase | 内容 | 日数（概算） |
|---|---|---|
| 0 | 基盤 | 5.5 |
| 1 | ルック P0（MVP） | 19.5 |
| 2 | ルック P1 | 10 |
| 3 | ルック P2（Maya で仕上げる） | 13.5 |
| 4 | 機能のオン/オフ・Unity でのみの機能の Maya プレビュー | 9.5 |
| E | エディタの操作感（lilToon / Poiyomi を参考に） | 11.5 |
| R | Maya ツールの配布・導入・更新（R-7〜R-13 外部ツールパッケージ 7 日を含む） | 13 |
| U | T-Drive for Unity パッケージ | 39 |
| AN | アニメーション作業台（命名・クリップ・ブレンド / 遷移・分割出力・ショット。AN-11 ブレンドツリー 5 日を含む） | 26.5 |
| A | アニメーション | 22 |
| S | サンプルモデルの移行（shizuku） | 5 |
| F0 | FacialController 基盤（殻 + コア） | 11.5 |
| F1 | FacialController Maya の最小の一周 | 22 |
| F2 | Maya ならではのシェイプ作成支援 | 15 |
| FU | FacialController Unity | 16 |
| FT | TimeLine / D-Drive 連携 | 10.5 |
| F5 | UE 版の未実装項目 | 21 |
