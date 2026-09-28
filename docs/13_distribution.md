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

```
1. git clone --filter=blob:none https://github.com/wrenchsun/T-Drive.git "%LOCALAPPDATA%\TDriveToon\T-Drive"
2. powershell -ExecutionPolicy Bypass -File "%LOCALAPPDATA%\TDriveToon\T-Drive\tools\install.ps1"
3. Maya を起動 → T-Drive Toon › プロジェクトを選ぶ…
```

`tools/install.ps1` がすること（D-Drive のセットアップウィザード相当）:

| 手順 | 内容 |
|---|---|
| 前提の確認 | git がある / Maya 2026 がある / リポジトリである |
| 版の固定 | `git fetch --tags` → 最新のリリースタグ `vX.Y.Z`（`-Version` で指定可）を detached で checkout。**Git LFS のファイルは取らない**（`GIT_LFS_SKIP_SMUDGE=1`。マニュアルの画像だけ `git lfs` があれば取る） |
| モジュール登録 | `TDriveToon.mod` を生成（VERSION・`TDRIVE_ROOT`。MCP の commandPort は既定で無効 `TDRIVE_MCP_PORT=0`、`-EnableMcp` で有効） |
| 結果 | 導入した版・場所を表示。既存の `TDriveToon.mod` は上書き前に `.bak` を残す |

- 開発用の登録（`tools/install_maya_module.ps1`、リポジトリを直接指す・MCP 有効）は従来どおり残す
- アンインストール: `TDriveToon.mod` と `%LOCALAPPDATA%\TDriveToon` を消す（プロジェクトフォルダのデータは残る）

## 3. 更新（Maya のメニュー **T-Drive Toon › 更新…**）

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
- **起動時の確認**（D-Drive には無い、追加）: 1 日 1 回、裏で `git ls-remote` し、新しい版があればビューポートに「更新があります（T-Drive Toon › 更新…）」を出す。
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
