# Changelog

形式は [Keep a Changelog](https://keepachangelog.com/ja/1.1.0/)、バージョンは [SemVer](https://semver.org/lang/ja/)。
各バージョンに `### 互換性` を必ず書く（[docs/06_release_versioning.md](../../docs/06_release_versioning.md)）。
タグ `vX.Y.Z` はこのパッケージの `package.json` の `version` と一致する（T-Drive 本体と同時にリリース。D-Drive の更新ウィンドウがこのファイルを読む）。

## [Unreleased]

### 互換性
- MINOR: 追加のみ。公開済みの API・シリアライズ形式の削除や改名はない（このパッケージはまだリリースされていない）。挙動の変更点:
  - 持続する上書き（`SetOverride`）は持ち主ごとに保持し、複数の持ち主の上書きは合成規則でまとめて適用する（`PushOverride` は従来どおり残る）
  - 複数のトラックが誇張を指定したとき、掛け合わせではなく、優先度の高いトラック（手で置いたトラック > `(auto)`）の値を使う
  - 上書きの感情の重みが NaN のときは「指定なし」= Runner の値（距離で決まるレイヤーは距離の値）を使う（以前は 0 扱い）
  - 再生していないときの Timeline のプレビューは、メインカメラがあればそれを視点にする（以前はシーンビュー。Runner の「編集時の視点」で変えられる）
  - `FacialModelInstanceBridge` は D-Drive 連携の FC 用 asmdef（`TDrive.Facial.DDrive.FC`）に移した。D-Drive 1.4.0 以降が必要（それより前の D-Drive では `TDRIVE_DDRIVE_FC_FORCE` を定義したときだけコンパイルされる）
  - `exaggeration` カーブを含む `.fctrack` は、古い版のパッケージでは読めない

### 追加
- F5-8: リップシンクの対応表（`.fcpose` の `lipSync`。音素 × 感情 → 口のシェイプ）。`Runner.SetLipSync` / `SetLipSyncByIndex` / `ClearLipSync`（音素の強さ・声量を渡す。追従 `follow` つき）、調整値「リップシンクの強さ」「追従」、インスペクターの状態とプレビュー、検証「口のシェイプがメッシュに無い」。口のシェイプは書く前の値へ戻す（無効化・プール返却・保存の前）。解析とのつなぎ `FacialULipSyncBridge`（uLipSync があるときだけコンパイルされる別アセンブリ `TDrive.Facial.ULipSync`）。`.fcpose` の取り込みの版は上げていない（`lipSync` が無ければ何もしない）
- F5-7: 調整値「書き込む LOD の上限」（`maxLod`。0 = すべての LOD に書く = これまでどおり、N ≥ 1 = LOD N まで）。LOD は LODGroup から調べる。`Runner.EffectiveMaxLod` / `GetRendererLod`
- F5-4: パース補正（距離 / 画角の軸のキー。`FC_<asset>_Persp_K{n}` を角度の補正に足す）。調整値「パース補正の強さ」、Timeline のクリップ「パース補正を使う」、`.fctrack` の固定カーブ `perspective`。`.fcpose` / `.fctrack` の取り込みの版を上げた（再取り込みされる）
- FU-0: パッケージ雛形（asmdef・README）
- FU-1: `TDrive.Facial.Core`（FacialCore / FacialSpace）と共通テストデータの EditMode テスト
- E-2 / E-3 / U-3: 持ち主ごとの持続する上書き `SetOverride` / `ClearOverride` / `ClearAllOverrides`（`PushOverride` は互換で残る）。Timeline の Mixer は持続させ、クリップの無い区間・グラフ終了・無効化で消す
- E-4: `FacialSaveGuard`（保存・リロード・再生前・Undo の直後に、読み込まれているすべての Runner の FC_ を 0 に）
- U-1 / U-2 / U-4 / U-5 / U-6 / U-7 / U-8 / U-9、E-5〜E-7 / E-9〜E-14（詳細は docs/19）

### 変更
- E-1: D-Drive ブリッジを基本（`TDrive.Facial.DDrive`。1.3.1 でコンパイル可）と FC（`TDrive.Facial.DDrive.FC`。`TDRIVE_FACIAL_DDRIVE_FC || TDRIVE_DDRIVE_FC_FORCE`）に分割
- E-4: 編集時の `ResetWeights` は結んだ FC_ をすべて 0 にする
- `FacialCorrectionData.sourceJson`（エディタだけのシリアライズ欄）を廃止（元の JSON は `FacialSourceJson`。エディタ）

### 修正
- 左右反転の親の下で向きがずれる問題
