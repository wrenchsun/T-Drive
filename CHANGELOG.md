# Changelog

形式は [Keep a Changelog](https://keepachangelog.com/ja/1.1.0/)、バージョンは [SemVer](https://semver.org/lang/ja/)。
各バージョンに `### 互換性` を必ず書く（[docs/06_release_versioning.md](docs/06_release_versioning.md)）。

## [Unreleased]

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

### 修正
- ツールをリロードすると編集中の Look の未保存の変更・プレビューのライト設定が消えていた
- 画面上の線を使っている間、描画のたびにスクリプトエディタにエラー（OverflowError / kInvalidParameter）が出ていた
- ツールのリロード後に画面上の線（インナーライン・外側輪郭）を操作すると Maya が落ちていた
- 機能タブのチェックを押しても反映されなかった（PySide6 の lambda の引数の扱い）。切り替えを軽くし（約 0.4 秒 → 0.12 秒）、表示していないタブは開いたときに更新するようにした

### 互換性
- 初回リリース。以後の比較の基準となる互換性の面: パラメータ契約（`_Toon*` 33 個）・Look 定義の characterSettings・頂点カラー割当（R 影寄せ / G 線幅 / B 明寄せ / A 固定色）・UV2 = tdSmoothNormal（八面体・接空間）・Look 定義 schemaVersion 1・materialdata.json の形式
- 0.x の間は破壊的変更を MINOR で出せる（MS2026 への移植開始後は追加変更のみ）
