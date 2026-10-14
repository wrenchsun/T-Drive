# 13. 配布・導入・更新（Maya ツール）

2026-09-28 作成。D-Drive の配布（`D-Drive/docs/42_distribution.md` §3〜4、更新ウィンドウ `Editor/Update/UpdateWindow.cs`）と同じ体験を Maya で提供する。

決定事項（2026-09-28 ユーザー確認）:

- **配布は git のタグで版を固定**（D-Drive の UPM `#vX.Y.Z` と同じ考え方）。各 PC に git と GitHub（非公開リポジトリ `wrenchsun/T-Drive`）の権限が必要
- **ツール本体とデータ（Look）を分ける**。デザイナーは「プロジェクトフォルダ」を選び、Look・出力はそこに置く（D-Drive がパッケージと MS2026 のデータを分けているのと同じ）

## 1. 全体像

```
%LOCALAPPDATA%\TDriveToon\T-Drive\       ← ツール本体（git の checkout、タグ vX.Y.Z に固定。デザイナーは触らない）
    maya\scripts / maya\shaders / shaders\ToonCore.hlsl / looks\_env（既定の環境プロファイル）/ docs\DesignerManual
%USERPROFILE%\Documents\maya\modules\TDriveToon.mod   ← Maya に上の maya\ を登録（インストーラーが生成）

<プロジェクトフォルダ>\                    ← デザイナーのデータ（例: アート用リポジトリ、共有ドライブ）
    looks\<キャラクター>\look.json          ← Look（lookVersion で独自に版管理。06 §4）
    looks\_env\*.json                       ← プロジェクトの環境プロファイル（あればツール同梱の同名より優先）
    build\unity\                            ← Unity 出力
    captures\                               ← A/B・パリティのキャプチャ
    .tdrive\project.json                    ← プロジェクトの設定（最後に更新を適用したツールの版 など）
```

- 開発用（このリポジトリで開発する場合）は、プロジェクトフォルダ = リポジトリ自身。従来どおり動く
- Look 内のテクスチャの相対パスは**プロジェクトフォルダ基準**。シーンのファイルノードには `$TDRIVE_PROJECT/…` で書く（どの PC でも開ける）。
  従来の `$TDRIVE_ROOT/…` はツール本体を指す（UnityChan のテスト素体など）

## 2. 導入（初回だけ）

前提: Windows / Maya 2026 / git（Git LFS は不要）/ GitHub の `wrenchsun/T-Drive` を読める権限。

PowerShell で:

```
1. $env:GIT_LFS_SKIP_SMUDGE = "1"; git clone https://github.com/wrenchsun/T-Drive.git "$env:LOCALAPPDATA\TDriveToon\T-Drive"
2. powershell -ExecutionPolicy Bypass -File "$env:LOCALAPPDATA\TDriveToon\T-Drive\tools\install.ps1"
3. Maya を起動 → Tools › T-Drive › プロジェクトを選ぶ…
```

（1 の `GIT_LFS_SKIP_SMUDGE` はテスト素体など大きなファイル（約 50MB）を取らないため。ツールには不要）

`tools/install.ps1` がすること（D-Drive のセットアップウィザード相当）:

| 手順 | 内容 |
|---|---|
| 前提の確認 | git がある / Maya 2026 がある / リポジトリである |
| 版の固定 | `git fetch --tags` → 最新のリリースタグ `vX.Y.Z`（`-Version` で指定可）を detached で checkout。**Git LFS のファイルは取らない**（`GIT_LFS_SKIP_SMUDGE=1`。マニュアルの画像だけ `git lfs` があれば取る） |
| モジュール登録 | `TDriveToon.mod` を生成（VERSION・`TDRIVE_ROOT`。MCP の commandPort は既定で無効 `TDRIVE_MCP_PORT=0`、`-EnableMcp` で有効） |
| 結果 | 導入した版・場所を表示。既存の `TDriveToon.mod` は上書き前に `.bak` を残す |

- 開発用の登録（`tools/install_maya_module.ps1`、リポジトリを直接指す・MCP 有効）は従来どおり残す
- アンインストール: `TDriveToon.mod` と `%LOCALAPPDATA%\TDriveToon` を消す（プロジェクトフォルダのデータは残る）

### 2.1 配布用インストーラー（推奨。2026-09-28 追加）

デザイナーには **`tools/distribution/Install-TDriveToon.bat` の 1 ファイルだけ**を配る（リリースごとに GitHub Release にも添付する）。

1. デザイナーが Maya のプロジェクトフォルダ（`workspace.mel` がある所）に置いてダブルクリック
2. 初回は `%LOCALAPPDATA%\TDriveToon\T-Drive` に clone（LFS なし）、2 回目以降はそのまま
3. `tools/install.ps1 -Release -ProjectHint <置いたフォルダ>` を実行 → 最新のリリースに固定・プロジェクトを決定・`.mod` を登録

プロジェクトの決め方: `-Project` 指定 > 置いたフォルダ（`workspace.mel` か `looks\` がある）> Maya で最後に開いたプロジェクト
（`maya/2026/<言語>/prefs/userPrefs.mel` の `lastLocalWS`。日本語版はシステムの文字コードで保存される。Maya の既定プロジェクト `default` は使わない）> フォルダ選択の画面。
`.mod` に `TDRIVE_PROJECT` と `TDRIVE_INSTALL_PROJECT` を書き、Maya は起動時に `TDRIVE_INSTALL_PROJECT` が新しければ 1 回だけ採用する（その後 Maya で選び直したものは上書きしない）。

- バッチは **ASCII のみ・CRLF**（cmd は UTF-8 のマルチバイト行を読み違えて行が壊れる。日本語の案内は `install.ps1` 側）。`install.ps1` は **BOM 付き UTF-8**（Windows PowerShell 5.1 は BOM が無いと cp932 として読む）。`tests/test_ui_patterns.py` が検査
- テスト用に `TD_DIR`（導入先）/ `TD_URL`（取得元）/ `TD_EXTRA`（install.ps1 への追加の引数、例 `-ModulesDir … -NoPause`）で差し替えられる

### 2.2 配る人の手順

1. リリースする（§4）。GitHub にタグを push するまで、デザイナーの更新ウィンドウには出ない
2. デザイナーの GitHub アカウントに `wrenchsun/T-Drive` の読み取り権限を付ける
3. `Install-TDriveToon.bat` を渡し、[マニュアル「はじめに」](DesignerManual/setup.html) を案内する

## 3. 更新（Maya のメニュー **Tools › T-Drive › 更新…**）

D-Drive の更新ウィンドウと同じ構成。

| 区画 | 内容 |
|---|---|
| 版 | 今の版（VERSION）・固定しているタグ・導入の種類（リリース / 開発用） |
| 更新の確認 | **最新の版を確認**: `git ls-remote --tags origin` の `vX.Y.Z` を新しい順に。今の版との差を **PATCH / MINOR / MAJOR** で表示。MAJOR は赤で警告し、移行ガイド（`docs/migrations/vN.md`）へ誘導 |
| 変更点 | 選んだ版までの CHANGELOG（今の版より新しい節）を表示。`### 互換性` を強調 |
| 更新 | **この版に更新**: 確認ダイアログ → `git fetch --tags` → `git checkout vX.Y.Z` → 前の版を記録（1 段の「元に戻す」用）→ `.mod` の版を書き換え → ツールをリロード（未保存の Look は残る）。`userSetup.py` か `.mod` の中身が変わる更新は **Maya の再起動** を案内 |
| 元に戻す | **前の版に戻す**: 記録した前のタグへ checkout（もう一度押すと戻す前に戻る = D-Drive と同じ 1 段の入れ替え） |
| 更新後の確認 | プロジェクトの全 Look を読み込み・検証（`look.upgrade` による自動移行を含む）。問題が無ければプロジェクトの「最後に適用した版」を更新 |

- 導入が開発用（ブランチ上・ローカルの変更あり）のときは更新ボタンを無効にし、「git で更新してください」と表示する
- ツール本体の checkout にローカルの変更があるときは更新しない（デザイナーが触らない前提。変更の一覧を表示）
- **起動時の確認**（D-Drive には無い、追加）: 1 日 1 回、裏で `git ls-remote` し、新しい版があればビューポートに「更新があります（Tools › T-Drive › 更新…）」を出す。
  認証の入力画面は出さない（`GIT_TERMINAL_PROMPT=0`・`GCM_INTERACTIVE=never`、失敗は黙って次回）。更新ウィンドウで無効にできる
- 起動時、プロジェクトの「最後に適用した版」が今の版より古ければ「更新後の確認がまだです」を出す（D-Drive の `DD-SETUP-UPDATE-PENDING` 相当）

## 4. リリース（開発者）

[06](06_release_versioning.md) §5 の手順。リリースタグ `vX.Y.Z` を GitHub に push した時点で、各 PC の更新ウィンドウに出る。

- MAJOR を出すときは `docs/migrations/vN.md`（移行ガイド）を必ず書く（`check_release.py` が検査）
- 起動時の確認・更新ウィンドウは **タグだけを見る**（main の途中のコミットは配布されない）

## 5. 実装の置き場所

| もの | 場所 | Maya 依存 |
|---|---|---|
| 版の比較・タグの解析・CHANGELOG の範囲・git の呼び出し | `tdrive_toon/updater.py` | なし（単体テスト） |
| プロジェクトフォルダの解決・設定の読み書き | `tdrive_toon/project.py` | なし（Maya の optionVar だけは呼び出し側） |
| 更新ウィンドウ・起動時の確認 | `tdrive_toon/ui_update.py`、`userSetup.py` | あり |
| インストーラー | `tools/install.ps1`（Windows PowerShell 5.1 で動く） | — |

## 6. 外部ツールパッケージ（URL で追加。2026-10-14 決定）

D-Drive の更新ウィンドウ「URL を入力して追加」と同じ体験を Maya で提供する。T-Drive は **殻 + 配布・更新の基盤**を担い、
ゲームと寿命の違うツール（最初の例: MS2026 のグレーボックス → Maya → Unity 背景パイプライン、`MS2026/Tools/Maya/`）は
**別リポジトリ**にして、デザイナーは同じインストーラーと同じ更新ウィンドウから導入・更新する。

決定事項（2026-10-14 ユーザー確認）:

- **ツール本体（Toon / FacialController）と外部ツールは別リポジトリ・別タグ。** 外部ツールの相手は MS2026 の Unity 側（`MapExchange` の C#・JSON の版）で、T-Drive のリリースとは独立に変わるため
- **MS2026 リポジトリには Maya ツールを置かない。** Unity 側の取り込みコードと「必要なツールの版 + URL」だけを残す（デザイナーに Unity リポジトリを clone させない）
- 「まず T-Drive 本体に `tdrive_greybox` として入れて後で切り出す」案は採らない。最初から外部ツールとして扱う

### 6.1 全体像

```
%LOCALAPPDATA%\TDriveToon\T-Drive\            ← ツール本体（§1 と同じ。タグ vX.Y.Z に固定）
%LOCALAPPDATA%\TDriveToon\tools\<ToolId>\      ← 外部ツール（1 つにつき 1 つの git checkout。タグ vX.Y.Z に固定）
%LOCALAPPDATA%\TDriveToon\tools.json           ← 外部ツールの一覧（下記）
```

`tools.json`（本体の checkout には入れない。デザイナーごとの設定）:

```json
{ "version": 1,
  "tools": [ { "id": "ms2026_greybox", "url": "https://github.com/wrenchsun/MS2026-Maya.git",
               "dir": "ms2026_greybox", "tag": "v1.2.0", "previous": "v1.1.0", "enabled": true } ] }
```

- `id` は外部ツールの `tool.json`（§6.2）の `id`。`dir` は `tools\` 配下のフォルダ名（既定は `id`）。`previous` は 1 段の「元に戻す」用
- 本体の `.mod` は変えない。外部ツールの `maya/scripts` は **起動時に `userSetup.py` が `sys.path` に足す**（`.mod` を増やさない。導入・削除で Maya の再起動を要らなくする）

### 6.2 外部ツールが満たす約束（D-Drive の `ddriveUpdate` 相当）

| もの | 内容 |
|---|---|
| タグ | `vX.Y.Z`（SemVer）。更新ウィンドウはタグだけを見る（main の途中は配布されない） |
| `VERSION` | リポジトリ直下。タグと一致（本体と同じ） |
| `CHANGELOG.md` | リポジトリ直下。版ごとに `## vX.Y.Z` 節、壊す変更は `### 互換性` に「破壊あり」（本体・D-Drive と同じ語） |
| `tool.json` | リポジトリ直下。`{ "id": "ms2026_greybox", "name": "MS2026 グレーボックス", "entry": "ms2026_greybox.tool", "scripts": "maya/scripts", "mayaVersion": "2026", "requires": { "tdrive": "0.6.0" }, "compatibleWith": { "ms2026": "1.3.0" } }`。`requires.tdrive` は必要な本体の最低版（満たさなければ読み込まず警告）、`compatibleWith` は相手側の最低版の宣言（表示だけ） |
| エントリーポイント | `entry` のモジュールに `def create_tool() -> tdrive.tool.Tool` を置く。殻が `register_tool` する（2 段タブの 1 段目に並ぶ。タブを持たないメニューだけのツールは `Tool.build_widget` が案内文を返す） |
| 置き場所 | エントリーポイントのパッケージは `<scripts>/<id>/`。他のモジュールは `ms2026_*` のように衝突しない接頭辞を付ければ `<scripts>/` 直下でもよい。`userSetup.py` は持たない（起動処理は殻が呼ぶ `create_tool` の中で行う） |
| 作業フォルダ | ツールの入出力（例: グレーボックスの `MayaWork/`）は **T-Drive のプロジェクトフォルダ（`$TDRIVE_PROJECT`）配下**を既定にする。ツール独自の optionVar は残してよい |
| Git LFS | ツールには使わない（インストーラーが LFS を取らないため） |

### 6.3 導入・更新ウィンドウ

| 操作 | 内容 |
|---|---|
| URL を入力して追加 | 更新ウィンドウの「外部ツール」区画。URL（と任意でタグ）を入れる → `git clone`（`GIT_LFS_SKIP_SMUDGE=1`・`GIT_TERMINAL_PROMPT=0`）→ `tool.json` を読んで `id` を決める → 最新のタグ（指定があればそのタグ）に checkout → `tools.json` に追記 → その場で読み込み（`sys.path` + `register_tool`）。clone できない・`tool.json` が無い・`requires.tdrive` を満たさないときは追加しない |
| 一覧 | 外部ツールごとに 名前・今の版・固定タグ・最新の版・PATCH / MINOR / MAJOR（本体と同じ色分け） |
| 更新 / 元に戻す / 変更点 | 本体と同じ処理を、`Git(repo=<ツールの checkout>)` で行う。`updater.py` の `update_to` / `rollback` / `changelog_between` をツールのパスで呼ぶ（本体固定の `REPO_ROOT` 前提を外す） |
| 無効化 / 削除 | `enabled: false` で読み込まない（checkout は残す）。削除は `tools.json` から外し、checkout フォルダを消す |
| 起動時の確認 | 本体と同じ 1 日 1 回の `git ls-remote` を各ツールにも行い、まとめて「更新があります」を出す |
| インストーラー | `Install-TDriveToon.bat` は変えない。配る側が最初から入れておきたいツールは `install.ps1 -Tools "<url>[#tag],..."` で指定できるようにする（`tools.json` に書き足して clone） |
| 開発用 | `tools.json` の `dir` に絶対パス（自分の clone）を書くと、ブランチ上の checkout をそのまま読み込む。更新ボタンは無効（本体の「開発用」と同じ） |

### 6.4 最初の外部ツール: MS2026 グレーボックス

- リポジトリ: `wrenchsun/MS2026-Maya`（2026-10-14 作成。ローカルは `C:/Users/yamag/wrench/maya/MS2026-Maya`）。移すもの: `MS2026/Tools/Maya/*.py`（`ms2026_maya_*`）と `README.md`。仕様の正は MS2026 の `Docs/Guides/MayaGreyboxPipeline.md` のまま（Unity 側と対で読むため）。ツール側の CHANGELOG に JSON（`.props.json` / `.colliders.json`）の版を書く
- Unity 側（MS2026 `MapExchange`）は残す。取り込み時に JSON の `version` が対応より新しければ警告（版ずれの検出）
- MS2026 の文書に「必要なツールの版と URL」の表を置き、ツールの MAJOR を上げたら同じ PR で更新する

### 6.5 実装の置き場所

| もの | 場所 | Maya 依存 |
|---|---|---|
| `tools.json` の読み書き・`tool.json` の検証・`requires` の判定 | `tdrive/external.py` | なし（単体テスト） |
| 複数リポジトリ対応（`Git(repo)` を受ける `update_to` / `rollback` / `installed_version`） | `tdrive/updater.py` | なし |
| 起動時の読み込み（`sys.path` + `register_tool`）・失敗時の `_UnavailableTool` | `tdrive/shell.py`、`userSetup.py` | あり |
| 更新ウィンドウの「外部ツール」区画・URL 入力 | `tdrive/ui_update.py` | あり |
| `install.ps1 -Tools` | `tools/install.ps1` | — |
