# 06. リリース・バージョン管理

D-Drive の運用（`D-Drive/docs/42_distribution.md` §4〜5、`docs/12_review.md` §7）に倣う。
**上げ幅は判断ではなく互換性区分で機械的に決める** のが原則。

## 1. 版を持つもの

| 対象 | 版の置き場所 | 形式 | タグ |
|---|---|---|---|
| ツール本体（Maya モジュール + シェーダー + 仕様） | `VERSION`（唯一の正）、`TDriveToon.mod` はインストール時に VERSION から生成 | SemVer `X.Y.Z` | `vX.Y.Z` |
| キャラクターのルック | `looks/<character>/look.json` の `lookVersion` | SemVer | `look/<character>/vX.Y.Z` |
| Look 定義スキーマ | `look.json` の `schemaVersion` | 整数 | （ツール MAJOR に同期） |

## 2. ツール本体の互換性区分

「互換性の面（compat surface）」= Unity/MS2026 やルックデータから見える約束事:

1. パラメータ契約（`_Toon*` の名前・型・意味、[03](03_shader_spec.md) §7）
2. 頂点カラー / UV のチャンネル割当（[03](03_shader_spec.md) §4〜5）
3. Look 定義スキーマと解決規則（[02](02_look_definition_spec.md)）
4. Unity 向け中間ファイルの形式（[02](02_look_definition_spec.md) §7）
5. シェーダーの式（同じ値で見た目が変わるか）= `shaders/ToonCore.hlsl`

| 区分 | 条件 | 例 |
|---|---|---|
| **MAJOR** | 上記の面の削除・改名・意味変更。既存ルックの見た目が同じ値で変わる | パラメータ改名、頂点カラー割当の変更、影の式の変更 |
| **MINOR** | 追加のみ（既定値で従来と同じ見た目）、自動マイグレーション付きの変更、`deprecated` 化、検証警告の追加 | 予約名パラメータの追加（既定値で無効）、新ロール、新 UI 機能 |
| **PATCH** | 面に触れない修正 | UI の不具合修正、性能改善、ドキュメント |

- MAJOR はユーザー承認が必要。**MS2026 への移植開始後は MAJOR を原則出さない**（D-Drive と同じく追加変更で対応する）
- MAJOR を出す場合は `look.py` にマイグレーション関数（schemaVersion n → n+1）を必ず同梱する

## 3. CHANGELOG

- ルートの `CHANGELOG.md`。[Keep a Changelog](https://keepachangelog.com/ja/1.1.0/) 形式
- 各バージョンに **必ず `### 互換性` セクション** を書く（MAJOR/MINOR/PATCH の根拠 = どの面に触れたか / 触れていないか）
- 作業中の変更は `## [Unreleased]` に積む

## 4. ルックの版（lookVersion）

ルックは Unity へ渡る「アートデータのリリース」。ツール本体とは独立に版を上げる。

| 区分 | 条件 |
|---|---|
| MAJOR | 部位構成の変更（マテリアルの追加・削除・部位移動）、頂点カラー・法線の大幅な塗り直し（メッシュの再出力が必要） |
| MINOR | 値の調整（見た目が変わる）、バリアントの採用 |
| PATCH | 見た目が変わらない修正（ラベル・未使用バリアントの削除） |

- `looks/<character>/CHANGELOG.md` に履歴を書く（採用した A/B と、その理由・比較画像のパスを残す）
- ルックのリリース時に Unity 向け中間ファイルと FBX を生成し、GitHub Release（タグ `look/<character>/vX.Y.Z`）に添付する

## 5. リリース手順（ツール本体）

D-Drive の Tools/Release と同じ手順・引数体系。この PC には PowerShell 7 が無いため Python（uv で実行）で実装している。

```
1. CHANGELOG.md の [Unreleased] → ### 互換性 を埋める
2. uv run --no-project python tools/release/check_release.py --version X.Y.Z   # 検査のみ（下記）
3. uv run --no-project --with pytest --with numpy --with pillow python -m pytest tests
      # 単体テスト + mayapy スモークテスト（tests/maya/smoke.py、Maya 2026 がある環境）
4. python tools/parity/compare.py …                                            # ToonCore / シェーダーを変更した場合のみ（09 §5）
5. uv run --no-project python tools/release/bump_version.py --version X.Y.Z --dry-run
6. uv run --no-project python tools/release/bump_version.py --version X.Y.Z --tag
      → VERSION・CHANGELOG・契約スナップショットを更新、"Release vX.Y.Z" でコミット、注釈付きタグ vX.Y.Z
7. git push --follow-tags   ※明示的に指示されたときだけ
```

`check_release.py` の検査項目:

- 作業ツリーがクリーン（`--allow-dirty` で試し打ち可）
- `[Unreleased]` に `### 互換性` があり、記入済み
- パラメータ契約のスナップショット（`tests/snapshots/params.json` = 直近リリース時点の契約）との差分から**必要な上げ幅を機械的に算出**し、指定した上げ幅が足りなければ失敗（削除・型変更 → MAJOR、追加 → MINOR。0.x の間は破壊的変更も MINOR）
- 全 `looks/*/look.json` が検証を通る
- 初回リリース（現在の VERSION のタグが無い）は上げ幅の検査を省略し、その時点の契約を基準にする

`bump_version.py` は `--part major|minor|patch`（現在から自動計算）/ `--no-commit` / `--allow-dirty`（リリースに関係しない作業中のファイルがあっても実行。コミットするのは VERSION・CHANGELOG・スナップショットだけ）にも対応。

## 6. ブランチ運用

- `main` + 作業ブランチ（`feat/<ticket>` / `fix/<ticket>`）→ PR でマージ。リリースブランチは作らない
- コミットメッセージにチケット番号を入れる（例: `feat(T0-3): 部位タブの自動登録`）
