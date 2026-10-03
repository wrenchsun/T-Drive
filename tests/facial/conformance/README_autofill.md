# 共通のテストデータ: autofill.json（自動生成・補間）

`README.md`（共通の形）の続き。`autofill.json` は Python（`core/autofill.py`）と C#（FU-1）が同じファイルを読んで結果を比べる。

## 期待値の出どころ

UE 版には自動生成の自動テストが無い。**期待値は UE 版のコード（`FacialCorrectionAsset.cpp` の `GenerateFromKeys` / `InterpolatePoseAtAngles` / `MirrorBoneOffset`）を
Python に写した実装の出力**で、全ケース `"source": "python-port"`。一部（`idw2_*` の中間値、完全一致、しきい値）は `tests/facial/test_autofill.py` で手計算と突き合わせてある。
許容誤差は **1e-4**（重み・平行移動・四元数・スケール。ケースに `tolerance` があればそれ）。UE は float32、Python は float64。
期待値を変える変更は MAJOR（UE 版との一致が崩れるため）。

## 共通の形

```jsonc
{ "kind": "autofill", "description": "…", "cases": [ { "name": "…", "source": "python-port", "note": "…", "op": "generate", … } ] }
```

座標系は書かない（正準空間 = cm / Z-up / 左手として扱う。`mirror.boneAxis` はその系の軸名）。レイヤーは 1 枚（レイヤー 0）だけ。

入力（共通）:

```jsonc
"grid":    { "yawRange": 90, "pitchRange": 45, "cols": 5, "rows": 3 },
"mirror":  { "enabled": true, "suffixL": "_L", "suffixR": "_R", "exclude": ["Brow"], "boneAxis": "Y" },
"autogen": { "mode": "IDW" /* または "NearestKey" */, "idwPower": 2 },
"keys":    [ { "row": 1, "col": 3, "curves": { "Smile_L": 1.0 },
               "bones": { "Eye_L": { "t": [0, 2, 1], "r": [0, 0, 0.2588, 0.9659], "s": [1, 1, 1] } } } ]
```

- `keys` は格子のキー（`isKey = true` の点）。点の角度は `core/evaluate.py` の `point_angles`（行 0 が -Pitch、中央が 0°、1 点の軸は 0°）
- `bones.*` の `r` は `[x, y, z, w]`、`s` は省略可（既定 `[1, 1, 1]`）
- `exclude` は部分一致（大文字小文字を区別）。空文字のパターンは無視

## op: generate（`generate_from_keys`）

```jsonc
"expect": { "summary": { "keys": 2, "mirrorKeys": 1, "generated": 13 },
            "points":  [ { "row": 0, "col": 0, "curves": { … }, "bones": { "Name": { "t": [..], "r": [..], "s": [..] } } }, … ] }
```

- `summary`: 実キーの数 / 足したミラーの仮想キーの数 / 書き込んだ自動生成の点の数（結果が空の点は数えない）
- `points`: **キー以外の点で、結果が空でないものの全部**。ここに無い点が出力にあってはいけない。カーブ・ボーンの**名前の集合も一致**させる（`curves` `bones` は空でも必ず書く）
- キーは変わらないこと（実装側のテストで確かめる）

## op: interpolate（`interpolate_pose_at_angles`）

`yaw` `pitch`（度）、`useMirror`（省略 = false。true のとき `mirror.enabled` なら仮想キーも入力にする）。`expect.pose` は `{curves, bones}`。

## 規則の要点（実装は `core/autofill.py` の docstring）

- ミラーの仮想キー: `|yaw| > 0.1°` の実キーを `-yaw` へ。反転位置から**距離 < 1°**（度のユークリッド距離）に、実キー・すでに作った仮想キー・自分自身のどれかがあれば作らない。
  複製はカーブ名・ボーン名の末尾の接尾辞を入れ替え、除外パターンに部分一致する名前は複製しない。ボーンは `boneAxis` の平行移動成分を反転、回転は軸まわり成分を残して他の 2 成分を反転して正規化、スケールは 1
- IDW: 距離 = `hypot(Δyaw / max(yawRange, 1), Δpitch / max(pitchRange, 1))`、重み `1 / d^power`、`d < 1e-4` は完全一致でそのキーのポーズをそのままコピー（スケールも）
- NearestKey: 度のままの二乗距離が最小のキーをコピー（`<` で比べるので同距離は先のキー。順序は row 優先・col の昇順の実キー → 仮想キー）
- 補間結果: カーブは重み付き和で `|v| <= 1e-3` は捨てる。ボーンは平行移動が重み付き和、回転は最初のキーの向きにそろえた重み付き和を正規化、**スケールは補間しない（1）**。
  `|t| <= 1e-3`（全成分）かつ回転が単位（各成分 1e-4 以内）なら捨てる。そのボーンを持たないキーは、平行移動は 0 として、回転は和に入れずに計算する（UE と同じ）
