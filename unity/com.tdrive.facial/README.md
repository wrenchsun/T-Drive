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
| `TDrive.Facial.DDrive` / `.Editor` | D-Drive 連携の基本（com.ddrive.core 1.3.1 以降。`TDRIVE_FACIAL_DDRIVE`）。トラック名の規則でのバインド・検証・.fctrack の反映。D-Drive 1.3.1 でもコンパイルできる（FC-1 の `SameAsTrack` は名前で引く） |
| `TDrive.Facial.DDrive.FC` | D-Drive の FC-1 / FC-12 の型を直接使う部品（`FacialModelInstanceBridge`）。D-Drive 1.4.0 以降で自動（`TDRIVE_FACIAL_DDRIVE_FC`）、それより前の D-Drive では Project Settings › Player › Scripting Define Symbols に **`TDRIVE_DDRIVE_FC_FORCE`** を足したときだけコンパイルされる（FC-1 / FC-12 を含む D-Drive の開発版を使うとき） |

## 導入 / 更新

配布は git URL。タグ `vX.Y.Z` がこのパッケージの `package.json` の `version` と一致する（T-Drive 本体と同じ版を同時にリリースする）。
**このパッケージが入る最初のリリースは v0.4.0 の次**（v0.4.0 には Unity パッケージが含まれない）。

### D-Drive から（推奨）

1. Unity で `Tools > D-Drive > Update > 更新ウィンドウ` を開く
2. 「URL を入力して追加」に `https://github.com/wrenchsun/T-Drive.git?path=unity/com.tdrive.facial` を入れて追加する（最新の `vX.Y.Z` タグが導入される）
3. 以後の更新・元に戻す・CHANGELOG の確認は同じウィンドウから行う。D-Drive が 1.4.0 より古いと、依存の確認が警告を出す（`package.json` の `ddriveUpdate.compatibleWith`）

### D-Drive なしで

Package Manager の `Add package from git URL` に `https://github.com/wrenchsun/T-Drive.git?path=unity/com.tdrive.facial#vX.Y.Z` を入れる（`vX.Y.Z` は入れたい版）。

### D-Drive 1.4.0 との関係

`TDrive.Facial.DDrive.FC`（`FacialModelInstanceBridge`）は D-Drive 1.4.0 以降の型を使う。D-Drive が 1.4.0 以降なら自動で有効（`TDRIVE_FACIAL_DDRIVE_FC`）。それより古い D-Drive では基本のブリッジ（`TDrive.Facial.DDrive`）だけが動く。FC-1 / FC-12 を含む D-Drive の開発版を使うときは、Scripting Define Symbols に `TDRIVE_DDRIVE_FC_FORCE` を足すと有効にできる。D-Drive が無くてもコアと Runner は動く。

## テスト

- 共通のテストデータ（`tests/facial/conformance/*.json`）を Python と C# が同じものとして読む
- Unity なしで回す: `dotnet test unity/FacialCoreTests`
- Unity: Test Runner の EditMode（`Tests/Editor/ConformanceTests.cs`）。データの場所は上へたどって探す。見つからなければ環境変数 `TDRIVE_CONFORMANCE_DIR`

## TODO（初回の Unity 取り込み）

- `.meta` は置いてある（git URL で配るので GUID をコミットしておく必要がある）。新しいファイルを足したら、Unity で開いたあとに `.meta` もコミットする
- Unity 6000.3 でコンパイル・EditMode テストが通ることを確認する

## リップシンク（uLipSync とのつなぎ）

T-Drive は音声を解析しません。解析結果（音素の強さと声量）を `FacialCorrectionRunner.SetLipSync(...)` に渡すと、`.fcpose` の対応表（`lipSync`）に従って口のシェイプを動かします。

[uLipSync](https://github.com/hecomi/uLipSync)（MIT）を使う場合:

1. プロジェクトに uLipSync（`com.hecomi.ulipsync` 3.0.0 以降）を入れる。入っていると、つなぎのアセンブリ `TDrive.Facial.ULipSync` が自動でコンパイルされる（入っていなければ外れる）
2. `uLipSync` コンポーネントと同じオブジェクト（または Runner の子）に「T-Drive › Facial uLipSync Bridge」を付ける。`source` に uLipSync を指定するか、uLipSync の「On Lip Sync Update」に `FacialULipSyncBridge.OnLipSyncUpdate` を登録する
3. uLipSync のプロファイルの音素名と、T-Drive の対応表の音素名が違うときは、`phonemeMap` に対応を書く（空なら同じ名前として扱う）

確認状況（2026-10-04）: uLipSync v3.1.5 と一緒に Unity 6000.3.13f1 でコンパイルできることを確認した。実際の音声を流しての動作は未確認。

