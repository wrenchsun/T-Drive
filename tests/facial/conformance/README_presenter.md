# 共通のテストデータ: presenter.json（画面の状態）

`README.md`（共通の形）の続き。`presenter.json` は Python（`core/presenters.py`）と、のちに C# の Presenter（Unity 側の編集 UI を作るとき）が
同じファイルを読んで状態を比べる。UI（Qt / Maya / Unity のウィジェット）には触れず、**操作列 → 観測できる状態**だけを決める。

## 期待値の出どころ

UE 版のパネル（`SFacialGridPanel` / `SFacialPosePanel` / `SFacialLayerPanel` / `SFacialWorkingSetPanel`）の動きを Python に写した実装の出力で、
全ケース `"source": "python-port"`。UE 版との違い（T-Drive が意図して変えた所）は末尾。数値の許容誤差は **1e-4**。
期待値を変える変更は、Presenter の約束（ビューが読む値）を変えるので MINOR 以上（docs/15 §7）。

## ファイルの形

```jsonc
{ "kind": "presenter", "description": "…",
  "documents": { "smile": { /* format = FacialCorrection の .fcpose.json そのもの */ }, … },
  "cases": [ { "name": "…", "source": "python-port", "note": "…", "setup": { … }, "steps": [ { "op": "select", "args": { … }, "expect": { … } }, … ] } ] }
```

`kind` は `"presenter"`（`test_conformance.py` の許可する種類の一覧にこの値を足す必要がある）。

### setup

| キー | 意味 |
|---|---|
| `document` | 文書。`documents` のキー（文字列）か、`.fcpose.json` の dict をそのまま。読み込みは `fcpose_io.from_dict` と同じ（欠けたキーは既定値） |
| `profile` | 命名規則プロファイル（`.fcprofile.json` の dict）。省略 = なし |
| `bakeState` | ベイク状態 `{ "FC_a_Neutral_R1_C2": "@current" \| "@stale" }`。`@current` = 今の文書のその点のポーズの `pose_hash`（ベイク済み）、`@stale` = 一致しないハッシュ（変更あり）。**省略 = 不明（null）**、`{}` = 何も焼いていない |
| `scene` | モデルの事情 `{ "curves": […], "bones": […], "targets": […] }`。省略した欄 = 不明。`scene` ごと省略 = なし |

ハッシュの値そのものは Python の実装（`validate.pose_hash`）の持ち物なので、共通のデータには書かない（実装ごとに `@current` を自分のハッシュで解決する）。
初期状態: アクティブレイヤー 0、選択なし、クリップボードなし、編集中の値は空、作業セットの絞り込み = オン。

### step の op

`args` は下の表のとおり。op の結果（戻り値）は `expect.result` で比べる（結果の dataclass を camelCase の JSON にしたもの）。

| op | args | 備考 |
|---|---|---|
| `noop` | – | 何もしない（状態だけ見る） |
| `select` / `confirm_select` | `row, col` / `choice`（`save` `discard` `cancel`） | 点の選択と、未保存の確認への答え |
| `unkey` `clear_point` `copy_pose` `paste_pose` `paste_mirrored` | `row, col`（省略 = 選択中の点） | |
| `clear_layer` | – | アクティブレイヤーの全部 |
| `generate` | `allLayers`（省略 = false） | |
| `preview_resize` / `resize` | `cols, rows, yawRange?, pitchRange?` | 予行は文書を変えない |
| `locate` | `yaw, pitch` | カメラの赤い点（結果 = CameraMarker） |
| `nearest_point` / `angles_of` | `yaw, pitch` / `row, col` | 結果は `[row, col]` / `[yaw, pitch]` |
| `actions` / `toolbar` | `row, col`（省略 = 選択中の点）/ – | 操作の可否。結果は `{ unkey, clear, bakePoint, copy, paste, pasteMirrored, cameraToPoint }` / `{ generate, unkey, clear, clearLayer }` |
| `pose.set_curve` | `name, value` | 可動域で丸める（`result.value` `result.clamped`） |
| `pose.set_bone` | `name, t?, r?, s?`（既定 `[0,0,0]` `[0,0,0,1]` `[1,1,1]`） | |
| `pose.reset_bone` `pose.reset_bones` `pose.zero` `pose.mirror` `pose.reload` | `name`（reset_bone のみ） | |
| `pose.save` | `keepEmptyKey?` | |
| `pose.ingest` | `curves, bones?, replace?（既定 true）, workingSetOnly?` | `bones` は `{ 名前: {t,r,s} }` |
| `pose.set_filter` / `pose.set_bone_filter` / `pose.set_working_set_only` | `text` / `text` / `value` | |
| `pose.export` / `pose.import_last_export` | – | 単独ポーズ（FacialPose）の書き出し → 最後に書き出したものを読み込む。`pose.export` の結果は `{ ok, format, curves, bones }`（空なら `{ ok: false, code: "empty" }`） |
| `layer.add` `layer.add_or_select` | `name` | |
| `layer.rename` `layer.set_enabled` `layer.set_emotion_curve` | `index, name` / `index, enabled` / `index, curve` | |
| `layer.delete` | `index` | |
| `layer.set_active` / `layer.confirm_set_active` | `index` / `choice` | |
| `layer.copy_from` | `source, dest` | |
| `validation.run` | – | |
| `validation.set_choice` | `kind, old, new` | 結果は bool |
| `validation.apply_renames` | – | 選び済みの新名で一括改名 |
| `validation.remove_missing` | `includeCaseMismatch?` | |
| `bake.mark_point` / `bake.clear` | `layer, row, col` / – | 「Maya 層がそのポーズを焼いた」/「何も焼いていない（`{}`）」を真似る |

`row` / `col` は 0 始まり（行 0 が -Pitch、列 0 が -Yaw）。レイヤーは番号（0 = Neutral）。

### expect（step ごとに、書いたものだけ比べる）

数値は 1e-4、辞書は「expect のキーが実際にある」、リストは長さも一致。

| キー | 比べるもの |
|---|---|
| `result` | op の戻り値の部分集合（`ok` `code` `message` `staleMorphs` `discardedEdits` と、op ごとの欄: `status` `row` `col` `yaw` `pitch` `cameraJump` `saved`、`summary` `droppedKeys` `dryRun`、`value` `clamped` `becomesKey` `removed`、`changed` `curvesSet` `bonesSet` `ignored` `unknown` `clamped`、`index` `count` `warnings` `switch`、`curve` `bone` `total` など。名前は Python の dataclass の欄の camelCase） |
| `selection` | `[row, col]` または `null` |
| `activeLayer` | アクティブレイヤーの番号 |
| `dirty` | 編集中のポーズに未保存の変更があるか |
| `clipboard` | クリップボードにポーズがあるか |
| `grid` | 文書の格子（`cols` `rows` `yawRange` `pitchRange`） |
| `points` | アクティブレイヤーの点の表示。`[{ row, col, state, color, displayColor, selected, hasPose, bake, frame, morph, yaw, pitch, tooltip }]`（書いた欄だけ） |
| `summary` | `{ keys, generated, empty, unbaked, changed, baked, total }` |
| `gridView` | 格子の表示全体（`rows` `cols` `displayRows` `layer` など） |
| `layers` | レイヤーの一覧 `[{ index, name, enabled, emotionCurve, active, isNeutral, canRename, canDelete, … }]`（長さは一致） |
| `layerView` | `{ count, limit, canAdd, presets: [[名前, あるか]], active, … }` |
| `pose` | 編集中の値 `{ curves: {名前: 値}, bones: {名前: {t,r,s}} }`。**名前の集合は完全一致**（ほぼ 0 のシェイプ・恒等のボーンは無いものとして扱う） |
| `poseRows` | ポーズタブの一覧に出る名前 `{ curves: […], bones: […] }`（順序も一致） |
| `poseView` | ポーズタブの表示（`hiddenCurves` `workingSetActive` `canMirror` `curves: [{name, lo, hi, explicitLimit, …}]` など） |
| `docPoints` | 文書の点 `[{ layer, row, col, isKey, curves, bones, absent }]`。`absent: true` = その点が無い。`curves` / `bones` を書いたら名前の集合は完全一致 |
| `issueCodes` | 検証の結果のコードの並び（重さ順） |
| `validationView` | `{ ran, ok, counts, total, summary, groups: [{severity, label}], proposals: [{kind, old, candidates, new, count}], canRemoveMissing }` |

## 観測の約束（実装が揃えるもの）

- **点の状態**: 空 / キー / 自動生成（`state`）。色の区分は `green`（キー）`cyan`（自動生成）`grey`（空）。選択中は表示色 `orange`（`displayColor`）。RGB は持たない。ポーズが空で
  キーでもない点は「空」、キーなら（補正なしの）キーのまま
- **ベイクの区分**（`bake`）: `none`（空の点）/ `unknown`（bakeState が null か asset が空）/ `unbaked` / `changed` / `baked`。`scene.targets` が分かるとき、
  それに無い FC_* は焼いていない扱い。枠（`frame`）は `unbaked` と `changed` だけ
- **集計**: `unbaked` / `changed` / `baked` は空でない点だけ。bakeState が不明なら 0
- **未保存**（`dirty`）: 編集中の値と保存済みのポーズを比べる（シェイプ 1e-3、ボーンの平行移動 1e-3、回転 1e-4（q と -q は同じ）、スケール 1e-4。無い側は 0 / 恒等）。値を戻せば false に戻る
- **選択の確認**: 別の点（またはレイヤー）へ移るとき、未保存があれば `needs_confirm`（選択は変えない）。同じ点の押し直しは `same_point`（確認なし）。`confirm_*` の答えは `save`（保存してから切り替え）`discard` `cancel`。確認待ちが無いときは `invalid`
- **選択中の点を別の操作が書き換えた**（クリア・自動生成・貼り付け・改名など）: 編集中の値は読み直す。未保存の編集を捨てたら結果の `discardedEdits = true`。別の点なら編集中の値は残る
- **stale_morphs**: シーンから消すべき FC_*。点がベイクの対象でなくなった（クリア・格子の縮小・保存で空・自動生成の結果が空・レイヤーの削除 / 改名）とき、`bakeState` または `scene.targets` に名前があるものだけ。
  レイヤーの削除・改名は、どちらも不明なら点のポーズから作った名前（`_Ex` は除く）
- **失敗**: `ok = false` のとき文書は変わらない。`code` は理由（`no_selection` `outside_grid` `not_key` `empty` `no_clipboard` `mirror_disabled` `empty_result` `no_keys` `invalid_size` `invalid_range`
  `no_point` `neutral` `range` `same` `limit` `duplicate` `case_collision` `reserved` `chars` `nothing` `no_scene` など）
- **検証の並び**: エラー → 警告 → 情報。同じ重さの中は レイヤー → 行 → 列 → コード → 名前

## UE 版のパネルとの違い（T-Drive が決めたこと）

1. 貼り付けた点は**キー**になる（UE は点のキーの印を変えず、自動生成の点を貼り替えても次の自動生成で上書きされる）
2. 空のポーズを保存すると点を消す（UE と同じ）。ただし `keepEmptyKey` で「補正なし」のキーとして残せる（自動生成の入力にできる）
3. 集計の「未ベイク」は空でない点だけ（UE は空の点も数える）。「変更あり」を別に数える
4. 保存・貼り付け・クリアでベイク済みのシェイプを即座には消さない（UE は消す）: 「変更あり」にして再ベイクで上書きする。点が空になったときだけ `staleMorphs` で消す
5. レイヤー名は英数字と `_` だけ、大文字小文字だけの違いも重複として拒否する（UE は同名のみ拒否。追加ボタンは連番を付ける）。パース補正の予約語（`Persp`）も拒否
6. 作業セットの絞り込みは、シェイプとボーンで別々に「空でなければ効く」（UE と同じ）。文字列の絞り込みは大小無視の部分一致（UE と同じ）
7. 削除したレイヤーの番号が詰まるとき、アクティブレイヤーは同じレイヤーを指し続ける（UE は番号を丸めるだけ）
8. 格子サイズの変更は角度で引き継ぐ（`autofill.resize_grid`）。UE は番号で引き継ぐ
