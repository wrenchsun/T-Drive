# 共通のテストデータ（conformance）

Python（`maya/scripts/tdrive_facial/core`）と C#（`TDrive.Facial.Core`、FU-1）が**同じファイル**を読み、結果を比べる。
言語に依存しない素の JSON だけで書いてある。仕様は [docs/14 §9](../../../docs/14_facial_controller_spec.md)、設計は [docs/15 §3.3](../../../docs/15_facial_controller_design.md)。

- 許容誤差: 重み 1e-4（ケースに `tolerance` があればそれ）。角度は `toleranceDeg`、座標・四元数は `tolerance`（1e-9）
- 計算は**正準空間**（UE 準拠: cm / Z-up / 左手 / キャラクターの前 +X・右 +Y・上 +Z）で定義する
- UE 版が正。UE 版の文書とコードが食い違う所はコードを正とする（感情レイヤー: Neutral は常に全量、感情は差分を上乗せ）
- 期待値を変える変更は MAJOR（UE 版との一致を壊すため。docs/15 §7）

## ファイルの共通形

```jsonc
{ "kind": "evaluate", "description": "…", "cases": [ { "name": "…", "source": "…", … } ] }
```

- `kind`: ファイルの種類（下の表）。`cases` は配列
- `name`: ファイル内で一意
- `source`: ケースの出どころ
  - `"UE FacialCoreTests.cpp:<TestName>"` … UE 版の自動テストを写したもの（入力・判定の意味は UE のテストと同じ。UE が範囲や合計で判定している所は、厳密な期待値を入れて `note` に書いてある）
  - `"python-port"` … Python 実装で起こしたケース（期待値は UE 版のコードの手計算と Python 実装の出力が一致することを確認して固定）
- `note`: 省略可。意図の説明

| ファイル | kind | 内容 |
|---|---|---|
| `evaluate_ue.json` | `evaluate` | UE の `ExactGridPoint` `BilinearCenter` `EmotionBlend` `ZeroWeightSkip` `EdgeFade`（3 ケース）`UnbakedPointFailSoft` |
| `evaluate_python.json` | `evaluate` | 範囲外のクランプ・端のフェード・感情 2 枚・ミュート・非正方格子（5x3 / 2x2 / 7x5 / 1 列 / 1x1）など |
| `view_angles.json` | `view_angles` | UE の `ViewAnglesFront` `ViewAnglesRoundTrip` + 各 forwardAxis・各座標系 |
| `scalars.json` | `scalar` | UE の `ExpressionScale` `DistanceFade` + 追加（`finterpTo` `normalizeAxis`） |
| `smooth.json` | `smooth` | UE の `SmoothWeights`（4 ケース）+ 時系列 |
| `convert.json` | `convert` | 座標系の変換（位置・方向・四元数・BoneOffset・forwardAxis・mirror.boneAxis） |

UE の自動テストは 11 件: `ExactGridPoint` `BilinearCenter` `EmotionBlend` `ZeroWeightSkip` `EdgeFade` `UnbakedPointFailSoft`
（→ `evaluate_ue.json`）、`ViewAnglesFront` `ViewAnglesRoundTrip`（→ `view_angles.json`）、`ExpressionScale` `DistanceFade`
（→ `scalars.json`）、`SmoothWeights`（→ `smooth.json`）。

## kind: evaluate（`EvaluateCorrection`）

```jsonc
{ "name": "emotion_blend", "source": "…",
  "grid":   { "yawRange": 90, "pitchRange": 45, "cols": 3, "rows": 3, "edgeFade": 15 },
  "layers": [ { "name": "Neutral", "enabled": true, "emotionWeight": 0, "morphs": ["FC_R0_C0", "…", null] },
              { "name": "Happy",   "enabled": true, "emotionWeight": 0.5, "morphs": ["FC_Happy_R0_C0", "…"] } ],
  "yaw": -90, "pitch": -45,
  "expect": { "weights": { "FC_R0_C0": 1.0, "FC_Happy_R0_C0": 0.5 } } }
```

- `layers[0]` が Neutral。`morphs` は行優先（`index = row * cols + col`）で、焼いていない点は `null`（または空文字）
- 入力: 格子 + レイヤー（`morphs`・`emotionWeight`・`enabled`）+ 角度（度）
- 期待: `expect.weights` = シェイプ名 → 重み。**名前の集合も一致させる**（ここに無い名前が出力に出てはいけない。重みが `IsNearlyZero`（1e-8）のものは出力しない）。出力の並びは問わない
- 入力と出力の関係は `evaluate.py` の docstring を参照（双線形、軸ごとのクランプ、端のフェード、Neutral は常に全量、感情は重みを掛ける）

## kind: view_angles（`ComputeViewAngles`）

共通: `expect.yawDeg` / `expect.pitchDeg`（Yaw は (-180, 180]）、`toleranceDeg`。

- `mode: "direct"`: 正準空間の `headPos`（格子の中心）・`headForwardYawDeg`（キャラクターの前方のワールド Yaw）・`viewerPos` → `compute_view_angles`
- `mode: "roundtrip"`: `headPos`・`headForwardYawDeg`・`yawDeg`・`pitchDeg`・`distance`。視点を
  `headPos + compute_view_direction(headForwardYawDeg, yawDeg, pitchDeg) × distance` で作り、`compute_view_angles` で元の角度が出ること
- `mode: "bone"`: 各環境の座標のまま。`space`（`{unit, upAxis, handedness}`）、`headPos`（基準ボーンのワールド位置）、
  `headRotation`（ワールド回転 `[x, y, z, w]`、`null` = 無回転）、`forwardAxis`（その系の軸名 `+X` … `-Z`）、
  `centerOffset`（基準ボーンのローカル）、`viewerPos`。手順:
  1. 格子の中心 = `headPos + rotate(headRotation, centerOffset)`
  2. 前方 = `rotate(headRotation, forwardAxis の単位ベクトル)`
  3. 中心・視点・前方を `space` → 正準空間へ変換（`convert.json` と同じ変換）
  4. `headForwardYawDeg = atan2(前方.y, 前方.x)`（度）→ `compute_view_angles`

## kind: scalar

`{ "fn": "expressionScale", "args": [0.5, 2.0], "expect": 0.5, "tolerance": 1e-6 }`

| fn | args |
|---|---|
| `expressionScale` | `[expressionDampen, s]`（どちらも 0〜1 に丸めて `1 - dampen × s`） |
| `distanceFade` | `[distance, fadeStart, fadeEnd]`（`end <= start` は無効で 1） |
| `finterpTo` | `[current, target, deltaTime, speed]`（UE の `FMath::FInterpTo`。`speed <= 0` は target、距離の 2 乗が 1e-8 未満は target、それ以外は `current + dist × clamp(dt × speed, 0, 1)`） |
| `normalizeAxis` | `[angleDeg]`（`FRotator::NormalizeAxis`: (-180, 180] へ） |

## kind: smooth（`SmoothWeights`）

```jsonc
{ "name": "…", "source": "…", "speed": 10,
  "initial": [ { "name": "A", "weight": 0.2 } ],
  "steps": [ { "dt": 0.05, "snap": false, "target": [ { "name": "A", "weight": 1 } ],
               "expect": [ { "name": "A", "weight": 0.28 } ] } ] }
```

- `initial` が最初の前回値。`steps` を順に適用し、**各ステップの出力が次のステップの前回値**になる
- `speed <= 0` または `snap` なら目標をそのまま返す。前回にだけある名前は 0 へ補間し、
  「目標に無く、補間後がほぼ 0（`IsNearlyZero`）」になったら出力から消える。目標に 0 として載っている名前は出力に残る
- 出力の並びは問わない（名前 → 重みで比べる）。許容誤差 1e-4

## kind: convert（`space.py`）

```jsonc
{ "name": "…", "op": "position", "from": {"unit": "cm", "upAxis": "Y", "handedness": "right"},
  "to": {"unit": "cm", "upAxis": "Z", "handedness": "left"}, "input": [0, 0, 100], "expect": [100, 0, 0], "tolerance": 1e-9 }
```

| op | input | expect |
|---|---|---|
| `position` | `[x, y, z]`（`from` の単位） | 軸の入れ替え + 長さの換算 |
| `direction` | `[x, y, z]` | 軸の入れ替えのみ |
| `quaternion` | `[x, y, z, w]` | `[det·M·xyz, w]`（鏡映のとき回転の向きが反転） |
| `boneOffset` | `{t, r, s}` | `t` は位置、`r` は四元数、`s` は成分の入れ替えのみ（符号なし） |
| `forwardAxis` | `"+Z"` など | 変換後の軸名 |
| `mirrorAxis` | `"X"` など | 変換後の軸名（符号なし） |

軸の対応（系 → 正準。`canonical = M · v`、長さは cm へ換算）:

- upAxis Y・左手（Unity）: `(x, y, z) → (z, x, y)`
- upAxis Y・右手（Maya）: `(x, y, z) → (z, -x, y)`
- upAxis Z・左手（UE）: 恒等
- upAxis Z・右手（Blender 型）: `(x, y, z) → (y, x, z)`

`det(M) = -1`（右手 ⇔ 左手）のとき四元数は `(−M·xyz, w)`。系から系への変換は「src → 正準 → dst」の合成。
