# T-Drive Facial（com.tdrive.facial）

視点の角度に応じて顔のブレンドシェイプを補正する FacialController の Unity 側。Maya の T-Drive が出力する `.fcpose` を読む。
仕様は `docs/14_facial_controller_spec.md`、設計は `docs/15_facial_controller_design.md`（T-Drive リポジトリ）。

## 構成（docs/15 §5.1）

| asmdef | 中身 |
|---|---|
| `TDrive.Facial.Core` | 計算（UnityEngine 非依存）。`FacialCore` / `FacialSpace` |
| `TDrive.Facial.Runtime` | データ・Runner（FU-2 以降） |
| `TDrive.Facial.Timeline` | Timeline 連携（com.unity.timeline があるときだけ。`TDRIVE_FACIAL_TIMELINE`） |
| `TDrive.Facial.Editor` | インポーター・インスペクター（FU-2 以降） |
| `TDrive.Facial.DDrive` / `.Editor` | D-Drive 連携（com.ddrive.core があるときだけ。`TDRIVE_FACIAL_DDRIVE`） |

## テスト

- 共通のテストデータ（`tests/facial/conformance/*.json`）を Python と C# が同じものとして読む
- Unity なしで回す: `dotnet test unity/FacialCoreTests`
- Unity: Test Runner の EditMode（`Tests/Editor/ConformanceTests.cs`）。データの場所は上へたどって探す。見つからなければ環境変数 `TDRIVE_CONFORMANCE_DIR`

## TODO（初回の Unity 取り込み）

- `.meta` ファイルは Unity が作る。**まだ置いていない**。git URL で配るパッケージは `.meta`（GUID）をコミットしないと参照が壊れるので、最初に開いたあとでコミットすること
- Unity 6000.3 でコンパイル・EditMode テストが通ることを確認する
