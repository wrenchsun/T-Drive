# F5 の共通のテストデータ（シャープ化・コマ打ち・距離の重み・誇張）

[README.md](README.md) の続き。UE 版には無い機能なので、すべて `"source": "python-port (F5; UE 未実装)"`。
Python（`core/evaluate.py`）と C#（`FacialCore`）が同じファイルを読む。既定値のとき従来の結果と一致すること（既存のケースは変えない）。

| ファイル | kind | 内容 |
|---|---|---|
| `evaluate_sharpness.json` | `evaluate`（`sharpness` 付き） | キー角度のシャープ化（R-32） |
| `evaluate_exaggeration.json` | `evaluate`（`exaggeration`・`exMorphs` 付き） | 誇張シェイプ `_Ex`（R-37） |
| `layer_distance.json` | `scalar`（`fn: layerWeightFromDistance`） | 距離でレイヤーの重みを決める（R-35） |
| `step.json` | `step`（新） | コマ打ちの判定（R-33） |
| `perspective.json` | `perspective`（新） | パース補正のキーの重み（R-34） |
| `lipsync.json` | `lipsync`（新） | リップシンクの対応表（R-18。F5-8） |

## evaluate への追加キー

- ケース直下 `sharpness`（省略 = 1）: 4 隅の双線形の重み w を `w^s / Σ w^s` に直す（重み 0 の隅は 0 のまま。合計 0 ならそのまま）。
  `s` は [0.01, 64] に丸める。端のフェード・レイヤーの重みを掛ける**前**に直す。焼いていない隅があっても残りは正規化し直さない
- ケース直下 `exaggeration`（省略 = 1、0〜1 に丸める）
- レイヤー `exMorphs`: `morphs` と同じ並びの `_Ex` シェイプ名（無し / null / 空文字 = その点は Ex なし。`morphs` より短くても落ちない）。
  Ex の重み = 同じ点の通常シェイプの重み × `exaggeration`（通常シェイプを焼いていない点の Ex は出さない。0 のときは出力に出ない）

## kind: scalar の追加 fn

| fn | args |
|---|---|
| `layerWeightFromDistance` | `[distance, start, end, from, to]`。`lerp(from, to, saturate((d - start) / (end - start)))`。`end == start` は `d >= start` なら `to`、それ以外は `from` |

## kind: step（`step_gate`）

```jsonc
{ "name": "…", "source": "…", "stepFps": 12, "initialAccum": 0,
  "steps": [ { "dt": 0.0166, "force": true, "expect": { "evaluate": true, "accum": 0.0166 } } ] }
```

- `initialAccum` から始め、`steps` を順に適用する。各ステップの出力の `accum` が次の入力（期待値の丸めの影響を避けるため、実装は自分の `accum` を引き継ぐ）
- `step_gate(accum, dt, step_fps, force)`: `accum += dt`。`step_fps <= 0` は `(true, 0)`。`force`（最初のフレーム・カット）か
  `accum + 1e-9 >= 1/step_fps` なら評価する。評価したときの新しい `accum` は `accum % period`（周期ぴったりの誤差で `period - 1e-9` 以内に残ったら 0）。評価しないときは `accum` のまま
- 比べるもの: `evaluate`（完全一致）、`accum`（許容 1e-8）

## kind: perspective（`perspective_weights`）

```jsonc
{ "name": "…", "source": "…", "values": [80, 30, 50], "x": 65, "strength": 1, "alpha": 1,
  "expect": { "weights": [0.5, 0, 0.5] } }
```

- `values` = キーの value（並んでいなくてよい）。`x` = 軸の値（`null` = NaN）。`strength`（省略 = 1、0〜1 に丸める）、`alpha`（省略 = 1）
- 期待値 = `perspective_weights(values, x)` × `clamp(strength)` × `alpha`。戻りは `values` と同じ順
- `perspective_weights`: 重複を除いた value を昇順に並べ、x を挟む 2 つを直線で混ぜる。端の外は端のキーが 1。キー 0 個 → `[]`、
  1 個 → `[1]`、x が NaN → 全部 0。同じ value のキーは添字が小さいほうだけが重みを受け取る（残りは 0）。有限でない value のキーは常に 0

## kind: lipsync（`lipsync_output` / `lipsync_activity` / `lipsync_apply`）

```jsonc
{ "name": "…", "source": "…",
  "lipSync": { /* .fcpose の lipSync と同じ形（from / to のキー） */ },
  "weights": { "A": 0.5 }, "volume": 0.5, "emotions": { "Joy": 0.5 },
  "current": { "a": 0.4 }, "limits": { "a": [0, 2] },
  "expect": { "output": { "a": 0.5 }, "activity": 0.5, "final": { "a": 0.6 } } }
```

- `volume` 省略 / null = 声量なし（倍率 = to）。`emotions` = 感情レイヤー名 → 重み（各値は 0〜1 に丸め、合計が 1 を超えたら合計で割る）。`current` 省略 = 空。`limits` = シェイプ名 → [下限, 上限]（上限だけ使う。無い名前は 1）
- `output` = 対応表に出てくる**全シェイプ**（音素の一覧にある音素の行だけ。寄与が無ければ 0）。キーの集合も比べる。無効・行なしのときは `{}`
- `activity` = saturate(Σ 各音素の強さ)（音素の一覧にある音素だけ数える）。`final` = `current × (1 − activity) + output` を 0〜上限に丸めた値（`output` と同じキー集合）
- 音素の一覧の重複・空の名前は 1 度だけ・数えない。同じ音素 × 感情の行は先のものだけが効く。比べる許容は 1e-4
