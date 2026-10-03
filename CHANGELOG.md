# Changelog

形式は [Keep a Changelog](https://keepachangelog.com/ja/1.1.0/)、バージョンは [SemVer](https://semver.org/lang/ja/)。
各バージョンに `### 互換性` を必ず書く（[docs/06_release_versioning.md](docs/06_release_versioning.md)）。

## [Unreleased]

### 互換性
- MINOR: Python の公開モジュール名は転送モジュールで維持（`tdrive_toon.lifecycle` / `project` / `updater` / `ui_update` / `mcp_bridge` は `tdrive` に移したが、同じモジュールを返すので既存の import はそのまま動く）。Look・パラメータ契約に変更なし

### 変更
- エディタを 2 段タブに（F0-1）: ウィンドウとドッキングの見出しは「T-Drive <版>」、1 段目のタブは Toon（これまでのエディタ）と FacialController（準備中）。メインメニューは「T-Drive Toon」から「T-Drive」に改名し、Toon 表示 ON/OFF はサブメニュー「Toon」へ。Toon の動作は変わらない

## [0.4.0] - 2026-09-30

### 互換性
- MINOR: パラメータ `_ToonSeeThroughOutline`・`_ToonOutlineScreenSpace` と `characterSettings.seeThroughOutline` を追加（既存の Look は既定値で読み込まれ、見た目は変わらない）

### 追加
- 透かし線（T-44 / 4-14）: 前髪に隠れた所だけ、眉・目などの部位の外形の線を前髪の上に描く（重なっていない所は通常の描画）。部位ごとにオン/オフ（機能「透かし線」）、線幅・色・距離の上限・透かす手前の部位（既定は髪ロール）はキャラクタータブ。Maya は Render Override で透かしバッファを描いてプレビュー（Unity は U-19 で対応予定）
- 輪郭線をスクリーンスペースで描く（4-15）: 部位ごとに、背面押し出しの輪郭線を画面上の線に切り替え（ルックタブ › 線 ›「スクリーンスペースで描く」、機能「輪郭線をスクリーンスペースで」）。線幅・線色はそのまま、外形と手前・奥の段差に一定の太さの線（Unity は U-20 で対応予定）

## [0.3.0] - 2026-09-29

### 変更
- 機能タブを一覧表（機能 × 全体・部位）に: 各マスが「その部位で実際に使うか」。押すとその部位だけ切り替え、全体と同じにすると部位ごとの設定は消える。全体と違うマスは色付き、行末の ↺ で全体どおりに戻す（「全体に従う / オン / オフ」の選択式は分かりにくかったため廃止）
- 0/1 のパラメータ（スムーズ法線を使う など）はルックタブでチェックボックス

### 追加
- セルフシャドウを落とすかを部位ごとに（`_ToonCastShadow`。Unity はそのマテリアルの ShadowCaster パスを止める）

### 互換性
- MINOR: パラメータ契約に `_ToonCastShadow` を追加（既定 1 = 従来どおり落とす。機能 selfShadow がオフのときは効果なし）

## [0.2.1] - 2026-09-29

### 修正
- エディタをドッキングしたときのタブの見出しの版が、更新しても古いまま（例: v0.2.0 なのに 0.1.0）だった

### 互換性
- PATCH: 互換性の面に触れていない

## [0.2.0] - 2026-09-29

### 追加
- テクスチャの差し替え: ルックタブ「テクスチャ（差し替え）」（ベースマップ・法線マップ・発光マップ）、↺ で元マテリアルのテクスチャ、見つからないファイルの警告、テクスチャを読み込み直す
- 機能の部位（シェーダー）単位のオン/オフ: 機能タブで部位・マテリアルを選び「全体に従う / オン / オフ」。出力に materialFeatures（Unity では組み合わせごとにシェーダーを生成）
- セルフシャドウ（T-43）: キャラクター自身が落とす影をトゥーンの影として（機能 selfShadow、既定オフ）。パラメータ契約に `_ToonReceiveShadow` を追加（顔ロールは 0）
- 法線マップ・発光を Maya の表示に反映（ToonCore に Toon_NormalFromMap / Toon_Emission、機能 normalMap / emission は既定オフ）

### 修正
- 起動と同時にシーンが開くと、テクスチャが読めずに真っ黒になり、Look も自動で開かなかった（$TDRIVE_PROJECT の設定とシーンを開いたときの処理を起動の最初に）
- シーンの保存（Ctrl+S）で Look が保存されず、閉じると Look の変更が失われた（シーンの保存で Look も保存。Look を変えるとシーンに未保存の印）

### 互換性
- MINOR（すべて追加。既存の Look は同じ値で同じ見た目）
  - パラメータ契約: `_ToonReceiveShadow` を追加（機能 selfShadow がオフのときは効果なし。古い Look は顔ロール 0 で補う）
  - Look スキーマ: マテリアルに `featureOverrides`（任意）を追加。機能に normalMap / emission / selfShadow を追加（既定オフ）
  - 出力（materialdata.json）: `materialFeatures` を追加。既存のフィールドは変更なし
  - シェーダーの式: セルフシャドウ・法線マップ・発光を追加（既定＝機能オフ・法線マップ無し・発光の強さ 0 で従来と同じ見た目）

## [0.1.0] - 2026-09-29

### 追加
- プロジェクト雛形（.gitignore / .gitattributes（Git LFS）/ VERSION / CLAUDE.md / README）
- 仕様書一式（docs/00〜09, docs/tasks.md）
- Maya MCP（GG_MayaMCP 0.6.1）導入: `.mcp.json`、`tools/maya_mcp_launcher.py`、Maya モジュール（commandPort 自動オープン）
- テスト素体 UnityChan（D-Drive から FBX・テクスチャ・ライセンスを取り込み）
- 共通シェーダーコア `shaders/ToonCore.hlsl`（Unity と共有）と Maya dx11Shader ラッパー（2 階調影・固定色・背面法線アウトライン・Neutral トーンマップ）
- 環境プロファイル（`looks/_env/ms2026_ingame.json`）と Maya 側の描画環境合わせ（色管理・縦 FOV カメラ・キャラクターライト）
- エディタ: 部位タブ（自動登録・選択から登録・ロール）/ ルックタブ（部位単位の即時調整・Undo）/ A/B タブ（切替・同条件キャプチャ・差分・採用）/ プレビュータブ（カメラ・ライト・パリティ表）
- 頂点カラーの Toon マスク（チャンネル単位ペイント）、アウトライン用スムーズ法線のベイク
- Unity 出力（materialdata.json + FBX。開いているシーンは変えない）
- 描画パリティ比較ツール（`tools/parity/compare.py`）、リリーススクリプト（`tools/release/`）
- デザイナーマニュアル（`docs/DesignerManual/`）
- ルック P1（Phase 2）: 眉・目を前髪の上に（`_ToonDepthOffset`）/ ライト色の影響 / リム / 髪ハイライト / 線幅の距離補正（パラメータ契約に 10 項目追加）
- 顔の法線編集（楕円体プロキシへの転写・リセット）、表現のレシピ（マニュアル）
- ルック P2（Phase 3）: 2 影 / 部位別の色補正 / 線の太さの方向依存 / MatCap / 奥行き圧縮 / SDF 顔影マップ（パラメータ契約に 12 項目追加）、カメラ角度の補正 BlendShape
- Look 定義に `characterSettings`（キャラクター単位の設定: ライト安定化・ステンシル・インナーライン・接地影・カメラ角度補正・奥行き圧縮・表情パラメータ）、キャラクタータブ

- 機能のオン/オフ（機能タブ）。オフの機能は効果なしで表示・出力
- Unity でのみの機能の Maya プレビュー: 影の安定化（プレビュータブ）/ 接地影（足元の板）/ 画面上のインナーライン・外側輪郭（Render Override）
- characterSettings に `screenOutline`（画面上の外側輪郭、T-42）

- 配布・導入・更新: 配布用インストーラー（Install-TDriveToon.bat。置いた Maya のプロジェクトに自動で登録）、更新ウィンドウ（T-Drive Toon › 更新…。確認・変更点・更新・前の版に戻す・更新後の確認）、起動時の更新の確認（1 日 1 回）
- プロジェクトフォルダ: Look・出力・キャプチャ・環境プロファイルの置き場所をツール本体から分離（T-Drive Toon › プロジェクトを選ぶ…）
- マニュアル「更新のしかた」、メニュー「マニュアルを開く」

### 変更
- ツール内のエラーをエディタ上部に赤く表示する（これまではスクリプトエディタにだけ出ていた）

### 修正
- ツールをリロードすると編集中の Look の未保存の変更・プレビューのライト設定が消えていた
- 画面上の線を使っている間、描画のたびにスクリプトエディタにエラー（OverflowError / kInvalidParameter）が出ていた
- ツールのリロード後に画面上の線（インナーライン・外側輪郭）を操作すると Maya が落ちていた
- 機能タブのチェックを押しても反映されなかった（PySide6 の lambda の引数の扱い）。切り替えを軽くし（約 0.4 秒 → 0.12 秒）、表示していないタブは開いたときに更新するようにした

### 互換性
- 初回リリース。以後の比較の基準となる互換性の面: パラメータ契約（`_Toon*` 33 個）・Look 定義の characterSettings・頂点カラー割当（R 影寄せ / G 線幅 / B 明寄せ / A 固定色）・UV2 = tdSmoothNormal（八面体・接空間）・Look 定義 schemaVersion 1・materialdata.json の形式
- 0.x の間は破壊的変更を MINOR で出せる（MS2026 への移植開始後は追加変更のみ）
