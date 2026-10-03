# Changelog

## [Unreleased]
- FU-0: パッケージ雛形（asmdef・README）
- FU-1: `TDrive.Facial.Core`（FacialCore / FacialSpace）と共通テストデータの EditMode テスト
- docs/19 の Unity 側の修正:
  - E-1: D-Drive ブリッジを基本（1.3.1 でコンパイル可）と FC（`TDrive.Facial.DDrive.FC`、`TDRIVE_FACIAL_DDRIVE_FC || TDRIVE_DDRIVE_FC_FORCE`）に分割
  - E-2 / E-3 / U-3: 持ち主ごとの持続する上書き `SetOverride` / `ClearOverride` / `ClearAllOverrides`（`PushOverride` は互換で残る）。Timeline の Mixer は持続させ、クリップの無い区間・グラフ終了・無効化で消す
  - E-4: `FacialSaveGuard`（保存・リロード・再生前・Undo の直後に、読み込まれているすべての Runner の FC_ を 0 に）。編集時の `ResetWeights` は結んだ FC_ をすべて 0 に
  - U-1 / U-2 / U-4 / U-5 / U-6 / U-7 / U-8 / U-9、E-5〜E-7 / E-9〜E-14（詳細は docs/19）
  - `FacialCorrectionData.sourceJson`（エディタだけのシリアライズ欄）を廃止（元の JSON は `FacialSourceJson`。エディタ）。左右反転の親の下の向きの修正
