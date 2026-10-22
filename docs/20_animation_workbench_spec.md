# 20. アニメーション作業台（命名規則・クリップ・ブレンド / 遷移のプレビュー・分割出力・ショット）

2026-10-22 作成。対象モデルは MS2026 の本番キャラクター **MisotenChara**（`MisotenChara_Rig.ma`、mGear リグ）。
Maya の中で **MS2026（D-Drive）と同じ感じでアニメーションを確認**し、**Unity 用にモデルとアニメーションを分けて出力**し、
**ショット（Timeline）を作って出力**するための仕様。チケットは [tasks.md](tasks.md) の Phase AN。

> 依頼文の「繊維のプレビュー」は「**遷移**（クリップからクリップへの切り替わり）のプレビュー」と解釈した。違っていればここを直す。

## 0. 前提（調査結果 2026-10-22）

### 0.1 MS2026 の命名規則（`Docs/Rules/FolderStructure.md` §3。T-Drive はこれに従う）

| 種類 | 規則 | 例 |
|---|---|---|
| モデル | `SK_<名前>`（スキン付き）/ `SM_<名前>`（静的） | `SK_Player` |
| アニメーション | `A_<対象>_<動作>` | `A_Player_Run` |
| AnimatorController | `AC_<対象>` | `AC_Player` |
| マテリアル | `M_<対象>` | `M_PlayerBody` |
| テクスチャ | `T_<対象>_<種類>` | `T_PlayerBody_BaseColor` |
| Prefab | PascalCase | `Player.prefab` |
| カットシーン（D-Drive） | `<Shot>.fbx`（カメラ・小道具）+ `<Shot>__<ModelId>.fbx`（キャラクター 1 体ずつ、アンダースコア 2 つ。2 体目は `_2`） | `Opening01__Hero.fbx` |
| 禁止 | 日本語・半角スペース・全角文字・`# % & +`、捨て名（`test` `aaa` `Cube (1)`）、版の接尾辞（`_v2`）、**`@` 分割（`Model@Anim.fbx`）** | |

### 0.2 D-Drive のアニメーション（`com.ddrive.core` 1.10.0、`docs/05` `docs/26` `docs/51`）

- 土台は Unity の **AnimatorController**（待機 / 移動などの基本動作。MS2026 の `AC_PlayerPreview` は Idle / Run / Jump の 3 状態、遷移は **固定秒 0.08 s のクロスフェード**、Exit Time なし、条件は `Speed`(float) と `ActionState`(int: Idle=0 Move=1 Jump=2 Fall=3 Interacting=4 Stunned=5)）
- 単発動作は **`AnimData`**（`Clip` / `StateName` / `Layer` / `Loop` / `DefaultCrossFade`=0.1 s / `Mask`（情報のみ）/ `BlendShapes[]`（正規化時間 0〜1 → 重み 0〜100）/ イベント（**フレーム番号指定**。Unity の AnimationEvent は使わない））。再生は `Animator.CrossFadeInFixedTime`（**線形・固定秒**）
- フレームレートは **30（または 60）**。再生は秒基準
- モデル FBX とアニメーション FBX は**別ファイル**（`SourceAssets/Model/<cat>/Foo.fbx` → `MODEL_<cat>_Foo`、`SourceAssets/Anim/<cat>/Foo.fbx|.anim` → `ANIM_<cat>_Foo`。FBX は最初の埋め込みクリップを使う）
- カットシーン FBX（`docs/26` §5.1）: cm / Y-up、fps は 30 か 60 で 1 ショット内は同じ、Bake Animation ON（Step 1）、Cameras ON、Lights OFF、Embed Media OFF、Input Connections OFF、キャラクター FBX は **ルートジョイントの階層だけ**（Deformed Models / Skins / Blend Shapes OFF）、複数カメラは Ubercam で 1 本に。再出力は同名で上書き（自動生成トラックだけ差し替わる）。リグの**ネームスペース = ModelId**
- ブレンドシェイプ名 `FC_` `fcs_` は T-Drive の予約。顔は `FacialCorrectionTrack` / `.fctrack`（[14](14_facial_controller_spec.md) §7）
- MS2026 には本番キャラクターのアニメーションがまだ無い（`PlayerPreview` の仮クリップのみ、Humanoid の本番リグも無し）。**MisotenChara の Unity 側取り込み規則は本仕様で決める**

### 0.3 リグ `MisotenChara_Rig.ma`

| 項目 | 現状 | 本仕様での扱い |
|---|---|---|
| リグ | mGear Shifter。コントローラ `*_ctl`（174 本）、ジョイント `<部位>_<側><番号>_<n>_jnt`（149 本）、ルート `rig/jnt_org/spine_C0_0_jnt` | ジョイント名は**変えない**（mGear の再ビルドで戻るため）。Humanoid への対応表（§2.4）をツールが持つ |
| メッシュ | `rig/miso_geo/MisotenChara_Rig5_IdolGirl_{Body_Chest,Skirt,Ribbonback}`（読み込み時の接頭辞が付いた名前） | `SK_<ModelId>_<部位>` に改名（§2.2。ツールの改名計画で、Look の参照も同時に追従） |
| 顔 | **ボーン駆動**（jaw / lip / teeth / tongue / eye）。blendShape なし | FacialController のベイク対象は今後。本仕様はボーンのアニメーションとして扱う |
| マテリアル | `Hair` `Face` `IdolGirl_Other2` … と `MisotenChara_Rig5_<名>_tdToon`（T-Drive の Toon プレビュー済み）、多数の namespace 残骸（`MisotenChara_Rig3_*`、`hair:*`、`ffffff:*`） | `M_<ModelId>_<部位>` に改名計画。未使用の残骸は「掃除」候補として一覧に出すだけ（削除は人が選ぶ） |
| 時間単位 | **film（24 fps）**、再生範囲 0〜500 | **30 fps（ntsc）に直す**（シーン検査で警告 + ワンクリック修正。既存キーは Maya の単位変換に従う） |
| テクスチャ | `$TDRIVE_PROJECT/sourceimages/<uuid>.png` と `D:/misoten/misoten_chara//sourceimages/...` の混在 | 命名チェックで `T_<ModelId>_<部位>_<種類>` 以外を警告（改名はファイル名も変えるので**提案のみ**。実施は人） |

### 0.4 T-Drive の現状

- アニメーションのプレビュー・クリップ・タイムラインの機能は**無い**（FacialController の `.fctrack` 出力とフレーム読み込みだけ）
- Toon プレビューは dx11Shader + Render Override で **DirectX 11 必須**（OpenGL では描画されず、警告のみ）
- 殻（`tdrive.shell`）は `_BUILTIN_TOOLS` に `(id, ラベル, モジュール)` を足すだけで 1 段目のタブが増える（[15](15_facial_controller_design.md) §2.2）

## 1. 目的とスコープ

| 目的 | 内容 |
|---|---|
| 命名を揃える | MS2026 の規則（§0.1）をツールが**検査・生成・維持**する（D-Drive の思想「命名規則は人が守るものではなくツールが生成・維持する」） |
| Maya で MS2026 と同じ感じに見る | クリップの一覧・ループ再生、**ブレンド**（2 クリップの固定秒クロスフェード）、**遷移**（Idle → Run → Jump のような状態の切り替わり。MS2026 の Controller と同じ秒数・同じ条件）、**レイヤー**（上半身だけ別クリップ）を、**DirectX 11 でない環境でも**ビューポートで確認できる |
| Unity 用に分けて出す | モデル `SK_<ModelId>.fbx`（メッシュ + スキン + スケルトン、アニメ無し）と、クリップごとの `A_<ModelId>_<動作>.fbx`（ボーンのみ・ベイク済み）+ 付帯 JSON |
| ショット（Timeline） | Maya でショットを作り（カメラ・キャラクター・クリップ列・イベント・顔の演出）、D-Drive のカットシーン規則どおりに出力する |
| デザイナーが迷わない | 1 段目タブ「Animation」の中に **命名 / クリップ / ブレンド / ショット / 出力** の 5 タブ。Phase E の操作感（検索・右クリック・1 操作 = 1 Undo・ツールチップ）を踏襲。マニュアルと確認項目を同じコミットで更新 |

**スコープ外**: リギング・スキニングの支援、Unity 側の AnimatorController / AnimData の自動生成（Phase U の U-A1 として別チケット。本仕様の JSON がその入力）、アニメの“嘘”（T-31〜T-39、Phase A。本作業台の上に載せる）、OpenGL 用のセルルック表示（AN-10 保留）。

### 1.1 DirectX 11 でない環境での動作（必須要件）

- Animation タブは **dx11Shader・Render Override・`.fx` を一切使わない**。Maya 標準の時間・アニメーション機能（Time Editor / animLayer / Camera Sequencer / playblast）だけで成り立つ
- Toon 表示が OFF（元のマテリアル）でもすべての機能が使える。Toon 表示が ON で DX11 でないときは、Toon の警告（既存）が出るだけで Animation タブは止まらない
- `environment.parity_status()` の DX11 判定は Animation タブでは**参照しない**
- mayapy のスモークテストは `-renderer` 指定なし（ヘッドレス）で全機能が通ること

## 2. 命名規則（命名タブ、AN-2）

### 2.1 ModelId

- 英数字 PascalCase（`^[A-Z][A-Za-z0-9]*$`）。D-Drive の Model 識別子・カットシーンの `__<ModelId>`・Maya のネームスペースに使う
- MisotenChara の ModelId は **`MisotenChara`**（ファイル名から。確定は人。`IdolGirl` は部位側の語として残す）
- 保存先: `anim/<ModelId>/animset.json`（§3.3）の `modelId`。Maya シーンには fileInfo `tdAnimModelId`

### 2.2 Maya シーンのノード名

| 対象 | 規則 | MisotenChara での例 |
|---|---|---|
| ネームスペース | `<ModelId>:`（参照して使うとき。1 体作業のシーンでは無しでも可。カットシーン出力時は必須） | `MisotenChara:` |
| メッシュ（transform） | `SK_<ModelId>_<部位>`。shape は Maya 既定の `…Shape` | `SK_MisotenChara_Body` `SK_MisotenChara_Skirt` `SK_MisotenChara_RibbonBack` |
| ジョイント | **mGear の名前のまま**（`<部位>_<側><番号>_<n>_jnt`）。ルートは `spine_C0_0_jnt` | 変更なし |
| マテリアル | `M_<ModelId>_<部位>`。T-Drive のプレビュー用 `…_tdToon` は対象外（ツールが作り直す） | `M_MisotenChara_Hair` `M_MisotenChara_Face` |
| テクスチャ（file ノード / ファイル名） | `T_<ModelId>_<部位>_<種類>`、種類は `BaseColor` `Normal` `Mask` `Emission` `MatCap` `Sdf` | `T_MisotenChara_Face_BaseColor.png` |
| クリップ（§3） | `A_<ModelId>_<動作>`。動作は PascalCase の語彙（§2.3）+ 任意の方向 `_F/_B/_L/_R` + 任意の連番 `01` | `A_MisotenChara_Idle` `A_MisotenChara_Run_F` `A_MisotenChara_Jump` |
| ショット（§5） | `<Shot>`: `^[A-Z][A-Za-z0-9]*$`（例 `Opening01`）。ファイルは `<Shot>.fbx` と `<Shot>__<ModelId>.fbx` | |
| カメラ（ショット内） | 1 台なら自由、複数は `CAM_<名前>`。出力は Ubercam 1 本 | `CAM_Main` |
| 小道具 | `PRP_<ModelId>` | |
| イベントロケータ | `EVT_<キー>` | `EVT_Hit` |
| T-Drive のプレビュー用ノード | `td` 接頭辞（`tdPreviewCam` `tdAnimComp_*` `tdShot_*`）。**出力の対象外** | |

禁止（§0.1）に加えて: 先頭の数字、連続アンダースコア（`__` はカットシーンのファイル名だけ）、末尾の `Shape` 以外の Maya 自動接尾辞（`pCube1` 系の捨て名、`polySurface###`）。

### 2.3 動作の語彙（MS2026 の `PlayerActionState` に合わせる）

| 動作 | 用途 | MS2026 の状態 |
|---|---|---|
| `Idle` | 待機（ループ） | `ActionState=0` |
| `Walk` / `Run` | 移動（ループ。方向 `_F/_B/_L/_R`） | `Move=1`、`Speed` で切替 |
| `Jump` / `Fall` / `Land` | 跳ぶ / 落ちる / 着地 | `Jump=2` `Fall=3` |
| `Interact` | 操作中 | `Interacting=4` |
| `Stunned` | ひるみ | `Stunned=5` |
| `Win` `Lose` `Damaged` `Wave` `Talk` `Pose01`… | 単発（AnimData） | — |

語彙に無い語も許す（警告: 「語彙にない動作名」）。語彙はプロジェクトファイル `anim/_vocabulary.json` で増やせる。

### 2.4 Humanoid 対応表（`anim/<ModelId>/humanoid.json`）

Unity の Humanoid ボーン名 → Maya のジョイント名。mGear の名前は Unity の自動マッピングで外れやすいので、ツールが対応表を持ち、モデル出力時に書き出す（Unity 側の取り込み U-A1 が Avatar 作成に使う）。

```jsonc
{ "format": "TDriveHumanoidMap", "version": 1, "modelId": "MisotenChara",
  "root": "spine_C0_0_jnt",
  "bones": { "Hips": "spine_C0_0_jnt", "Spine": "spine_C0_1_jnt", "Chest": "spine_C0_2_jnt", "UpperChest": "spine_C0_3_jnt",
             "Neck": "neck_C0_0_jnt", "Head": "neck_C0_head_jnt",
             "LeftShoulder": "shoulder_L0_shoulder_jnt", "LeftUpperArm": "arm_L0_0_jnt", "LeftLowerArm": "arm_L0_4_jnt", "LeftHand": "arm_L0_end_jnt",
             "LeftUpperLeg": "leg_L0_0_jnt", "LeftLowerLeg": "leg_L0_4_jnt", "LeftFoot": "leg_L0_end_jnt", "LeftToes": "foot_L0_0_jnt",
             "Jaw": "mouth_C0_jaw_jnt", "LeftEye": "eye_L0_eye_jnt", "...": "..." },
  "extra": ["ahoge_C0_0_jnt", "Skirt_L0_0_jnt", "Tailr_R0_0_jnt", "..."] }
```

- 初期値は mGear の部位名から**自動推定**（`spine_C0_*` → Hips/Spine/Chest、`arm_L0_{0,4,end}` → UpperArm/LowerArm/Hand、`leg_L0_*`、`neck_C0_*`、`finger_L{0..3}_*` `thumb_L0_*` → 指）。推定できないものは空欄で警告
- 命名タブに対応表の編集（Humanoid 名の行 × ジョイント名の欄、ビューポートで選んで「ここに入れる」）
- 検証: 必須 15 ボーン（Hips〜Head、両腕・両脚）が埋まっていること、同じジョイントを 2 回使っていないこと、親子の順序が Humanoid の順序と矛盾しないこと（Hips が Spine の先祖、など）

### 2.5 命名タブの機能

| 機能 | 仕様 |
|---|---|
| 検査 | シーンの対象（メッシュ・マテリアル・file・クリップ・ショット・カメラ・ジョイントのルート・時間単位・fps・単位 cm/Y-up）を規則で検査し、**一覧**（種類 / 今の名前 / 提案 / 理由）に出す。重大度: ❌ 出力が止まる（禁止文字・`@`・24 fps）/ ⚠ 警告（語彙外・未使用マテリアル・テクスチャのパス不統一） |
| 改名計画 | 提案名をその場で編集 → 「適用」で **1 回の Undo** にまとめて改名。マテリアル改名は `looks/<character>/look.json` のマテリアル名（と `_tdToon` プレビュー）を同じ操作で追従させる（Look の読み書きは `tdrive_toon.look` 経由。**割り当ては変えない**） |
| 掃除候補 | 未割り当てのマテリアル / SG / file、空のネームスペース を一覧に出す（削除はチェックして「削除」。既定はすべて未チェック） |
| シーン設定 | 時間単位（30 fps）・単位（cm）・Y-up のワンクリック修正。fps 変更は Maya の `currentUnit -t ntsc`（キーの時間は Maya の既定どおり保持） |
| プロファイル（実装メモ） | 動作の語彙は `anim/_naming.json` の `vocabulary` と `anim/_vocabulary.json` の両方から足せる（2026-10-22）。テクスチャの名前の問題は FBX に入らないので ⚠ のみ（❌ にしない）。Toon 表示が ON のまま保存されたシーンでは、割り当てが `_tdToon` 側にあるため、`tdSourceMaterial` から元マテリアルを辿って「未使用」と誤認しない |
| プロファイル | 規則は `maya/scripts/tdrive_anim/core/naming_rules.py` に **Maya 非依存**で持ち、`anim/_naming.json` で接頭辞・語彙・禁止を上書きできる（MS2026 以外のプロジェクト用） |

## 3. クリップ（クリップタブ、AN-3）

### 3.1 クリップとは

リグのシーン上の **フレーム範囲 + 属性**。Maya の Game Exporter の「Animation Clips」に相当し、Unity の `.anim` / D-Drive の `AnimData` 1 つに対応する。

| 属性 | 型 | 既定 | 意味 / Unity 側 |
|---|---|---|---|
| `name` | `A_<ModelId>_<動作>` | — | ファイル名・`StateName`・クリップ名 |
| `start` `end` | int（フレーム） | — | 出力範囲（end を含む）。ショットの `SourceFrameRange` と同じ流儀 |
| `loop` | bool | 動作が Idle/Walk/Run なら true | `loopTime` / `AnimData.Loop` |
| `rootMotion` | `none` / `bake` | `none` | `none`: Hips の移動を出力時にその場に固定（Unity の "Original" に相当）。`bake`: そのまま出す |
| `layer` | `Base` / `UpperBody` | `Base` | `AnimData.Layer`。`UpperBody` は上半身マスク（§4.3） |
| `crossFade` | float 秒 | 0.1（MS2026 の Controller 流儀なら 0.08） | `DefaultCrossFade`。ブレンド / 遷移プレビューの既定 |
| `events[]` | `{frame, key, args}` | [] | `AnimData.Events`（Frame）。`OnFootPlant` は `args: 0/1` |
| `stateGroup` | `locomotion` / `oneshot` | 動作語彙から | §4.2 の状態図に載せるか |

### 3.2 クリップタブの機能

- 一覧（名前・範囲・ループ・レイヤー・イベント数）。検索欄。右クリック: 再生 / 範囲をタイムスライダーに / 複製 / 削除 / 現在の再生範囲から作る
- 「現在の範囲から追加」: タイムスライダーの範囲とキーの有無から候補名を出す（キーのあるコントローラから動作を推定はしない。名前は人が選ぶ。語彙はコンボ）
- 再生: 選んだクリップの範囲でループ再生（`playbackOptions` + `play`）。**元の再生範囲は戻す**
- イベント: タイムスライダー上の現在フレームに `EVT_` を追加 / 一覧で編集。足音は `OnFootPlant` + 左右
- クリップごとのサムネイル（`playblast` の 1 フレーム、FacialController の `thumbnails.py` を流用）
- 範囲の重なりは許す（同じキーから速度違いのクリップなど）。名前の重複はエラー

### 3.3 保存形式 `anim/<ModelId>/animset.json`（唯一の真実。Git 管理）

```jsonc
{ "format": "TDriveAnimSet", "version": 1, "modelId": "MisotenChara",
  "frameRate": 30, "units": "cm", "upAxis": "Y",
  "skeletonRoot": "spine_C0_0_jnt", "meshes": ["SK_MisotenChara_Body", "SK_MisotenChara_Skirt", "SK_MisotenChara_RibbonBack"],
  "clips": [
    { "name": "A_MisotenChara_Idle", "start": 0, "end": 60, "loop": true, "rootMotion": "none", "layer": "Base", "crossFade": 0.1,
      "events": [], "stateGroup": "locomotion" },
    { "name": "A_MisotenChara_Run_F", "start": 100, "end": 118, "loop": true, "rootMotion": "none", "layer": "Base", "crossFade": 0.08,
      "events": [{ "frame": 4, "key": "OnFootPlant", "args": 0 }, { "frame": 13, "key": "OnFootPlant", "args": 1 }], "stateGroup": "locomotion" }
  ],
  "layers": [ { "name": "Base" }, { "name": "UpperBody", "maskRoot": "spine_C0_3_jnt" } ],
  "stateGraph": { "...": "§4.2" },
  "export": { "modelDir": "", "animDir": "", "category": "Player" } }
```

- シーンには fileInfo `tdAnimSetPath`（相対パス）だけを置く。シーンを開いたとき自動で読む（Look と同じ流儀）
- スキーマは `tdrive_anim/core/animset_schema.json`。読み書き・検証は Maya 非依存（`core/animset.py`）で pytest

## 4. ブレンドと遷移のプレビュー（ブレンドタブ、AN-4 / AN-5）

> **実装での変更（2026-10-22, AN-4）**: Time Editor は使わず、下の「フォールバック」にあたる方式（ジョイントのサンプリング → 時間をずらした複製カーブ → `pairBlend`（回転は四元数）/ `blendTwoAttr` の連鎖。重みは線形のカーブ）を**正**とした。
> 理由: 裏の mayapy では `timeEditorClip` の問い合わせ・編集（開始・長さ・クロスフェード）が効かず、クロスフェードの曲線とトリムを機械的に検証できなかった。pairBlend 方式は値を元のカーブと突き合わせて確かめられ、どのレンダラーでも同じに動く。
> 合成名は `tdAnimComp_<名前>`（network ノード。元のつながり・作ったノード・再生範囲を文字列で持ち、保存して開き直しても外せる）。遷移列の `switchAt` は D-Drive と同じ「前のクリップの切替位置」、`holdSec` > 0 が優先。表は保存しない（状態図は animset に保存）。
> Time Editor 版は、実機で確かめられるようになった時の改善候補（AN-4 の後）。

### 4.1 実現方式（D-6: Maya 標準の Time Editor を裏で使う）← 上の注記のとおり変更

Unity の `CrossFadeInFixedTime` は「固定秒・線形」の合成。Maya では **Time Editor**（`timeEditorComposition` / `timeEditorClip`）が同じこと（クリップの配置・重なり部分のクロスフェード・トラックごとのウェイト・マスク）をプラグインなしで行い、どのレンダラーでも再生できる。
ツールは **「遷移列」モデル（Maya 非依存）→ Time Editor の合成を生成** する。

- 合成名 `tdAnimComp_<用途>`（`td` 接頭辞、出力対象外）。生成し直すときは同名を消して作る。ブレンドタブを閉じる / 「元に戻す」で合成を外し、タイムスライダーの範囲と元のアニメーション（Time Editor は元のカーブを壊さない）に戻す
- クロスフェード: 重なり区間 = `crossFade` 秒 × fps、カーブは線形（Unity と同じ）。Time Editor の既定カーブが線形でない場合は線形に設定する
- 速度: トラックの `speed`（D-Drive の `SetSpeed` 相当）
- Time Editor が使えない環境（無い Maya は無いが、プラグインのロード失敗など）は、**フォールバック**として「2 クリップの時刻で `getAttr(t=)` して `pairBlend` で合成」の簡易方式を用意する（AN-4 の AC に含める。線形のみ・レイヤーなし）

### 4.2 遷移列と状態図

```jsonc
"stateGraph": { "format": "TDriveStateGraph", "version": 1,
  "parameters": [ { "name": "Speed", "type": "float", "default": 0 }, { "name": "ActionState", "type": "int", "default": 0 } ],
  "states": [ { "name": "Idle", "clip": "A_MisotenChara_Idle" }, { "name": "Run", "clip": "A_MisotenChara_Run_F" }, { "name": "Jump", "clip": "A_MisotenChara_Jump" } ],
  "transitions": [
    { "from": "Idle", "to": "Run", "duration": 0.08, "conditions": [ { "param": "Speed", "op": ">", "value": 0.1 } ] },
    { "from": "Run", "to": "Idle", "duration": 0.08, "conditions": [ { "param": "Speed", "op": "<", "value": 0.1 } ] },
    { "from": "*",   "to": "Jump", "duration": 0.08, "conditions": [ { "param": "ActionState", "op": "==", "value": 2 } ] },
    { "from": "Jump", "to": "Idle", "duration": 0.08, "conditions": [ { "param": "ActionState", "op": "!=", "value": 2 } ] } ] }
```

- 既定の状態図は MS2026 の `AC_PlayerPreview` と同じ（Idle / Run / Jump、0.08 s、Exit Time なし）。クリップの `stateGroup=locomotion` のものを動作名で状態に割り当てる
- **遷移列**（D-Drive の AnimEditor「Blend」の Steps と同じ）: `[{state|clip, holdSec, crossFadeSec, switchAt(0..1)}]`。例: Idle 1.0 s → Run 1.5 s → Jump → Idle
- プレビューの 2 つの入口:
  1. **遷移列を手で並べる**（表に行を足す。各行: クリップ / 保持秒 / クロスフェード秒 / 切替位置）
  2. **パラメータで動かす**（`Speed` `ActionState` のスライダー / コンボを時間軸上で変えると、状態図の条件を Maya 非依存の `core/state_machine.py` が評価して遷移列を作る）。「MS2026 と同じ動き」の確認はこちら
- 遷移列 → 合成の生成 → ループ再生。評価は Maya 非依存（pytest で「Idle 1 s → Speed 0.5 → Run に 0.08 s でクロスフェード」が列として出ること）

### 4.3 レイヤー（同時再生）

- `UpperBody` レイヤー: `maskRoot`（既定 `spine_C0_3_jnt` 以下 = 胸から上と腕）のジョイントだけに別クリップを乗せる。Time Editor のトラックのマスク（アトリビュートの除外）で実現。ウェイトのスライダー（`SetLayerWeight` 相当）
- 顔: FacialController のプレビュー（カメラ連動）はそのまま動く。顔のボーンはクリップのキーに従う

### 4.4 ブレンドタブの UI

- 上: 遷移列の表（追加 / 削除 / 上下 / 右クリックでコピー・貼り付け）。「MS2026 の状態図から」ボタンでパラメータ駆動モードに切替
- 中: パラメータ（`Speed` スライダー 0〜1、`ActionState` コンボ）。変更は**再生中に効く**（現在時刻で遷移が起きる）。ここは D-Drive の AnimEditor と同じ気持ちで触れること
- 下: 再生 / 停止 / ループ / 速度、「合成を外す」。右端に「この列をショットへ」（§5）
- 再生中はタイムスライダーに遷移の区間を色で示す（`timeEditor` の表示をそのまま使う。独自描画はしない）

## 5. ショット（ショットタブ、AN-7 / AN-8）

### 5.1 ショットとは

D-Drive の **CutsceneData 1 つ**に相当する Maya 側の単位。`anim/<ModelId>/../shots/<Shot>.shot.json`（キャラクターをまたぐので `shots/` はプロジェクト直下）。

```jsonc
{ "format": "TDriveShot", "version": 1, "shot": "Opening01", "frameRate": 30, "range": [0, 240],
  "cameras": [ { "name": "CAM_Main", "node": "CAM_Main", "start": 0, "end": 120 }, { "name": "CAM_Close", "node": "CAM_Close", "start": 121, "end": 240 } ],
  "characters": [ { "modelId": "MisotenChara", "namespace": "MisotenChara", "sequence": [ { "clip": "A_MisotenChara_Idle", "holdSec": 2.0, "crossFadeSec": 0.1 }, { "clip": "A_MisotenChara_Wave" } ], "facialTrack": true } ],
  "props": [ { "modelId": "Mic", "node": "PRP_Mic" } ],
  "events": [ { "frame": 60, "key": "Hit" } ],
  "export": { "dir": "", "category": "Opening" } }
```

### 5.2 作成

- 「新規ショット」: 名前（`^[A-Z][A-Za-z0-9]*$`）・fps（30 / 60）・範囲。シーンにグループ `tdShot_<Shot>`（出力対象外）を作り、ロケータでカメラ区間と `EVT_` を持つ
- カメラ: シーンのカメラを区間付きで並べる。**Camera Sequencer**（`sequencer` / `shot` ノード）に同期して、Maya のシーケンサー再生でカメラ切替を確認する。出力時は Ubercam（`doCreateUbercam`）を作って 1 本にし、終わったら消す
- キャラクター: ネームスペース（= ModelId）ごとに **クリップ列**（§4.2 の遷移列と同じ型）。配置は Time Editor の合成（`tdAnimComp_<Shot>_<ModelId>`）で、ブレンドタブの「この列をショットへ」から持ち込める。手で打ったキー（口パク・視線など）は合成の上の animLayer に打てる（出力時はベイクされる）
- 顔: FacialController のプレビューノードにキーがあれば `.fctrack` も一緒に出す（[14](14_facial_controller_spec.md) §7.3。既存の `tdrive_facial.export.export_fctrack` を呼ぶ）
- イベント: 現在フレームに `EVT_<キー>` を追加（ロケータ `EVT_<キー>` を `tdShot_<Shot>` の下に。D-Drive の Signal マーカー `cutscene/<キー>` になる）。
  **時刻の持ち方（実装での決め 2026-10-22）**: ロケータの `visibility` を「イベントの 1 フレーム前は 0、イベントのフレームから 1」のステップキーにする（ベイクしても残る。出力時は 0 → 1 に変わる最初のフレームがイベントの時刻）。属性 `tdEventFrame` / `tdEventKey` も持つ。同じキーを 2 回使うと Maya が名前に数字を付ける
- プレビュー: ショットの範囲でシーケンサー再生（カメラ切替 + 合成 + 顔）。`playblast` で動画（`captures/shots/<Shot>.mov`。Git 管理外）

### 5.3 出力（D-Drive のカットシーン規則そのまま）

| ファイル | 中身 | オプション |
|---|---|---|
| `<Shot>.fbx` | Ubercam（+ `PRP_*` 小道具）。`EVT_*` ロケータ | Bake Animation ON（Step 1）、Cameras ON、Lights OFF、Embed Media OFF、Input Connections OFF |
| `<Shot>__<ModelId>.fbx` | そのキャラクターの**ルートジョイント階層だけ**（メッシュ・スキン・ブレンドシェイプ無し）、ショット範囲でベイク | 同上 + Deformed Models / Skins / Blend Shapes OFF |
| `<Shot>__<ModelId>.fctrack` | 顔の演出（任意） | 既存 |
| `<Shot>.shot.json` | 上の内容（Unity 側は読まない。T-Drive の再現用） | |

- 出力先の既定: `build/cutscene/<Category>/`。「D-Drive の SourceAssets へ」プリセット: `<D-Drive または MS2026 のパス>/Assets/_Project/DDrive/SourceAssets/Cutscene/<Category>/`（MS2026）/ `Assets/SourceAssets/Cutscene/<Category>/`（D-Drive）。パスはプロジェクト設定（`project.py`）に持つ
- 同名で上書き（D-Drive の再取り込みの流儀）。出力前に命名検査（§2.5）の ❌ があれば止める
- 出力は Toon / Facial と同じ **別プロセスの mayapy** 方式（開いているシーンを壊さない。`tools/export_fbx_batch.py` に `--mode shot` を足す）

## 6. Unity 用の分割出力（出力タブ、AN-6）

### 6.1 モデル `SK_<ModelId>.fbx`

- 対象: `animset.meshes` のメッシュ + `skeletonRoot` 以下のジョイント（バインドポーズ、アニメ無し）。Toon の出力（`tdrive_toon.export.export_in_place`: 頂点カラー `tdToonMask`・UV `tdSmoothNormal`・参照のフラット化・`_tdToon` を元マテリアルに戻す）を**そのまま使う**（相乗り。二重実装しない）
- 追加で `humanoid.json`（§2.4）と `animset.json` の写しを同じフォルダに出す
- 出力先: `build/unity/<ModelId>/SK_<ModelId>.fbx`。「D-Drive の SourceAssets へ」プリセット: `SourceAssets/Model/<Category>/SK_<ModelId>.fbx`

### 6.2 アニメーション `A_<ModelId>_<動作>.fbx`（クリップごと 1 ファイル）

- 対象: `skeletonRoot` 以下のジョイントだけ。クリップ範囲で **ベイク**（`bakeResults`、Step 1、コントローラやコンストレイントは出さない）。`rootMotion=none` のときは Hips の平行移動の XZ（と任意で Y）を開始フレームの値に固定
- FBX: Animation ON、Bake Animation ON、Deformed Models / Skins / Blend Shapes OFF、Embed Media OFF、Input Connections OFF、1 ファイル 1 テイク（**実装での変更 2026-10-22**: テイク名は Maya の既定 `Take 001`。名前付きのテイクに分けると 2 テイクになり、D-Drive が先頭を取り込む仕組み（ファイル名で取り込む）と合わないため。範囲の先頭は 0 フレームに寄せる）
- **`@` は使わない**（D-Drive の規則）。ファイル名は `A_<ModelId>_<動作>.fbx`、D-Drive は最初の埋め込みクリップを `ANIM_<Category>_A_<ModelId>_<動作>` として取り込む
- 出力先: `build/unity/<ModelId>/anim/`。プリセット: `SourceAssets/Anim/<Category>/`
- 「変更のあるクリップだけ」: クリップの範囲内のカーブのハッシュを `animset.json` 横の `.export-state.json`（Git 管理外）に記録し、変わっていないものは飛ばす（任意、既定 ON）

### 6.3 付帯 JSON（Unity 側の取り込み U-A1 の入力）

`animset.json`（クリップ属性・イベント・状態図）と `humanoid.json`。U-A1 ではこれから Avatar / `AC_<ModelId>.controller` / `AnimData` を生成する（本仕様では**生成しない**。MS2026 側の取り込み規則が決まるまで JSON を正とする）。

### 6.4 出力タブの UI

- 上: 出力先（既定 / D-Drive / MS2026 のプリセット、フォルダを開く）
- 中: 「モデル」「アニメーション（クリップの一覧にチェック、変更のあるものだけ）」「ショット」の 3 つの区画。各区画に「出力」。進捗はウィンドウ内（別プロセスなので Maya は止まらない。既存の `ExportJob` の流儀）
- 下: 直近の出力ログ（ファイル名・サイズ・警告）。命名検査の ❌ があれば出力ボタンを無効にして理由を表示

## 7. 設計（置き場所・Maya 非依存層・テスト）

```
maya/scripts/tdrive_anim/
  __init__.py
  core/                      # Maya 非依存（pytest）
    naming_rules.py          # §2 の規則・検査・提案（プロファイルで上書き）
    humanoid_map.py          # §2.4 推定・検証・読み書き
    animset.py               # §3.3 読み書き・検証・差分
    state_machine.py         # §4.2 状態図の評価（パラメータの時系列 → 遷移列）
    sequence.py              # 遷移列 → 配置（開始秒・重なり・速度）の計算。Time Editor にもフォールバックにも同じ入力
    shot.py                  # §5.1 読み書き・検証
    animset_schema.json / shot_schema.json
  scene_naming.py            # 検査の対象収集・改名の適用（1 Undo）・Look の追従
  clips.py                   # クリップの再生・範囲・サムネイル
  composition.py             # Time Editor の合成の生成 / 解除、フォールバック
  shot_scene.py              # tdShot_* グループ・Camera Sequencer・Ubercam
  export.py                  # 分割出力・ショット出力（mayapy 別プロセスは tools/export_fbx_batch.py に --mode を追加）
  session.py                 # 現在の animset / shot・Undo の束ね・変更通知（Toon / Facial と同じ形）
  ui.py                      # AnimTool（殻の Tool）。make_tool()
  ui_naming.py ui_clips.py ui_blend.py ui_shot.py ui_export.py
tests/
  test_anim_naming_rules.py test_anim_humanoid_map.py test_anim_animset.py test_anim_state_machine.py test_anim_sequence.py test_anim_shot.py
  test_maya_anim_*_smoke.py   # mayapy（ヘッドレス。dx11 を使わない）
tests/fixtures/anim/          # 小さなリグ（ジョイント 6 本 + 立方体スキン）を mayapy で作る fixture。MisotenChara 本体はリポジトリに入れない（LFS でも入れない。`assets/misoten/` は Git 管理外）
```

- 殻への登録: `tdrive/shell.py` の `_BUILTIN_TOOLS` に `("anim", "Animation", "tdrive_anim.ui")` を追加。メニュー `Tools › T-Drive › Animation…`
- `lifecycle` の約束（CLAUDE.md）: scriptJob（時間変化・選択追従）は `keep_alive` + `on_reload`。Qt の `clicked` は `lambda *_, x=...:`
- 出力は Toon の `ExportJob` を共通化して使う（`tdrive_toon.export` → `tdrive.export_job` に移すのは AN-6 の中で。公開名は転送モジュールで維持 = MINOR）
- 互換性区分（[06](06_release_versioning.md)）: `animset.json` / `shot.json` / `humanoid.json` のキー削除・改名は MAJOR、追加は MINOR。Look・パラメータ契約には触れない

## 8. Unity 側（Phase U に積む。本仕様では作らない）

| # | 内容 |
|---|---|
| U-A1 | `animset.json` + `humanoid.json` から Avatar（Humanoid マッピング）・`AC_<ModelId>.controller`（状態図・固定秒クロスフェード）・D-Drive `AnimData`（単発クリップ・イベント・ループ）を生成する ScriptedImporter / メニュー。MS2026-Maya と同じく外部パッケージか `com.tdrive.toon` に同梱かは [08](08_unity_port_plan.md) で決める |
| U-A2 | `OnFootPlant` などのイベントを D-Drive の `AssetEvent` に写す。MS2026 の `FootPlantEvents.md` の流儀（単独 `.anim` 必須）に合わせるため、FBX のクリップを `.anim` に複製するかは要確認 |

## 9. 決めること（要確認）

| # | 事項 | 仮の決め |
|---|---|---|
| Q-1 | ModelId | `MisotenChara` |
| Q-2 | クロスフェードの既定 | クリップ既定 0.1 s（D-Drive）、状態図の遷移 0.08 s（MS2026 の Controller）。Unity 側で一本化するなら Controller 側の値に合わせる |
| Q-3 | 顔のアニメーション（ボーン駆動）を Unity でどう扱うか | 本仕様ではボーンのキーとして A_ / ショット FBX に含める（Humanoid の Jaw / Eye + extra ボーン）。FacialController のベイク（FC_ シェイプ）は顔メッシュに blendShape を足してから |
| Q-4 | 出力先のパス（MS2026 の `Assets/_Project/DDrive/SourceAssets/`） | プロジェクト設定で指定。既定は `build/` |
| Q-5 | 24 → 30 fps 化で既存キーの時間をどう扱うか | Maya 既定（時間を保持 = フレーム番号が変わる）。リグ作業者に確認 |
| Q-6 | テクスチャの改名（ファイル名も変わる） | 提案のみ。実施は人 |
