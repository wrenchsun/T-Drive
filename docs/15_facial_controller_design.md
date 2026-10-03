# 15. FacialController 設計

- 作成: 2026-10-03
- 仕様: [14](14_facial_controller_spec.md)。D-Drive 側の変更: [16](16_ddrive_changes_for_facial.md)。チケット: [tasks.md](tasks.md) Phase S / F
- 方針は UE 版の移植設計（`FacialController_UE/Docs/Porting/00・01・03`）を踏襲し、T-Drive の決まり（CLAUDE.md、[01](01_architecture.md)）に合わせる

## 1. 設計の柱

| # | 柱 | 内容 |
|---|---|---|
| 1 | ソースが正 | `.fcpose.json` が唯一の真実。ベイク結果（FC_*）・プレビュー用ノードは作り直せる使い捨て |
| 2 | 計算は 1 か所ずつ | 角度 → 重みの計算は Python（`tdrive_facial.core`）と C#（`TDrive.Facial.Core`）に各 1 つ。同じテストデータで一致を確かめる（[14](14_facial_controller_spec.md) §9）。Toon の「式は ToonCore.hlsl の 1 か所」と同じ考え |
| 3 | Maya 非依存層を分ける | データ・計算・検証・画面の状態（Presenter）は `maya` を import しない。pytest で単体テストする（CLAUDE.md「Maya 非依存層に maya を import しない」を facial にも適用） |
| 4 | 元を壊さない | モデルの元のシェイプ・スキン・割り当てに触らない。作る / 消すのは `FC_*`・`fcs_*`・`td*` で始まるものだけ |
| 5 | プラグインなし | Maya 側は純 Python + 標準ノード。Python のオブジェクトを Maya に登録する所は `lifecycle`（keep_alive / on_reload / guarded）を必ず通す |
| 6 | D-Drive に依存しない | Unity パッケージは D-Drive を参照しない。D-Drive 向けの補助は別 asmdef（D-Drive があるときだけコンパイル） |

## 2. リポジトリ構成

```
T-Drive/
├─ maya/scripts/
│   ├─ tdrive/                     ★ 新規: 共通の殻（ウィンドウ・メニュー・プロジェクト・更新・ライフサイクル）
│   │   ├─ __init__.py             REPO_ROOT / __version__
│   │   ├─ shell.py                2 段タブのウィンドウ（workspaceControl）。ツールを登録して並べる
│   │   ├─ menu.py                 メインメニュー「T-Drive」
│   │   ├─ tool.py                 ツールの約束（Tool プロトコル: id / label / build_widget / on_scene_opened / on_scene_saved / undo / redo）
│   │   └─ lifecycle.py project.py updater.py ui_update.py mcp_bridge.py   （tdrive_toon から移す。tdrive_toon には同名の薄い転送モジュールを残す）
│   ├─ tdrive_toon/                既存（Toon）。ui.py の EditorWindow の中身を「ToonTool」として殻に載せる
│   ├─ tdrive_facial/              ★ 新規（FacialController）
│   │   ├─ core/                   ── Maya 非依存 ──
│   │   │   ├─ model.py            データクラス（Document / Grid / Policy / Layer / GridPoint / SourcePose / BoneOffset）
│   │   │   ├─ fcpose_io.py        .fcpose.json の読み書き・版の扱い・追加キー・検証（スキーマ）
│   │   │   ├─ space.py            座標系の変換（meta ⇔ 自分の系 ⇔ 正準空間）
│   │   │   ├─ evaluate.py         EvaluateCorrection / ComputeViewAngles / 端のフェード / 表情での弱め / 距離フェード / スムージング
│   │   │   ├─ autofill.py         GenerateFromKeys（ミラー + IDW / 最近傍）、格子サイズ変更時の引き継ぎ
│   │   │   ├─ profile.py          命名規則プロファイル（プリセット・読み書き・ミラー規則・可動域）
│   │   │   ├─ validate.py         検証・似た名前の候補・一括改名
│   │   │   ├─ naming.py           FC_<asset>_<layer>_R{r}_C{c} などの名前の規則（1 か所）
│   │   │   ├─ unity_anim.py       Unity の .anim（YAML）からブレンドシェイプ / ボーンのカーブを読む
│   │   │   └─ presenters.py       画面の状態（GridPresenter / PosePresenter / LayerPresenter / ValidationPresenter）
│   │   ├─ scene.py                ── 以下 Maya 依存 ── シーンの読み取り（メッシュ・blendShape・ジョイント・基準姿勢）
│   │   ├─ pose_apply.py           ポーズをシーンに当てる / シーンからポーズを取り込む（ボーンのずれ）
│   │   ├─ bake.py                 ベイク
│   │   ├─ preview_rig.py          カメラ連動プレビュー（ノード + expression）、キーに焼く
│   │   ├─ shapes.py               シェイプ作成支援（この角度で彫る・左右分割・ミラー・転写・整理）
│   │   ├─ export.py               Unity 向け FBX + JSON、fctrack の出力
│   │   ├─ session.py              開いているデータ・Undo・変更通知（Toon の session と同じ形）
│   │   └─ ui_*.py                 タブ（setup / grid / pose / shapes / layers / validate / export）
│   └─ userSetup.py                tdrive.shell / 各ツールの起動処理を呼ぶ
├─ schema/fcpose.schema.json       .fcpose.json のスキーマ（追加キー込み）
├─ tests/
│   ├─ facial/                     core の単体テスト（Maya なし）
│   ├─ facial/conformance/*.json   ★ 共通のテストデータ（Python と C# が同じものを読む）
│   └─ maya/facial_smoke.py        mayapy のスモーク（合成の小さな頭で: 当てる → ベイク → プレビューの重み）
└─ unity/
    ├─ com.tdrive.toon/            （既存計画。Phase U）
    └─ com.tdrive.facial/          ★ 新規
        ├─ Runtime/Core/           TDrive.Facial.Core.asmdef（UnityEngine 非依存の純 C#。計算のみ）
        ├─ Runtime/                TDrive.Facial.Runtime.asmdef（FacialCorrectionData / Runner / Overrides / MaterialOutput）
        ├─ Runtime/Timeline/       TDrive.Facial.Timeline.asmdef（com.unity.timeline があるときだけ。Track / Clip / Mixer）
        ├─ Editor/                 TDrive.Facial.Editor.asmdef（インポーター・インスペクター・格子ビューア・検証・プレビュー）
        ├─ Bridges/DDrive/         TDrive.Facial.DDrive(.Editor).asmdef（com.ddrive.core があるときだけ）
        └─ Tests/                  共通のテストデータを読む EditMode テスト
```

### 2.1 殻（`tdrive`）への切り出し（F0-1）

今は `tdrive_toon` がウィンドウ・メニュー・プロジェクト・更新まで全部を持っている。2 段タブにするため、共通部分を `tdrive` へ移す。

- `tdrive.shell.ShellWindow`: `QTabWidget`（1 段目）に、登録されたツールのウィジェットを並べる。エラー表示のバー・Ctrl+Z / Ctrl+Y の振り分け（今開いているツールへ）は殻が持つ
- `tdrive_toon.ui.EditorWindow` は「ヘッダー + 6 タブ」の中身を `ToonPanel(QWidget)` に分け、`ToonTool` として登録する。`tdrive_toon.ui.show()` は互換のため残し、殻を開いて Toon を前面にする
- workspaceControl は名前を `TDriveShellWorkspaceControl` に変える。古い `TDriveToonEditorWorkspaceControl` が残っていたら位置を読んで引き継ぎ、消す。`uiScript` は `from tdrive import shell; shell.restore()`
- 移すモジュール（lifecycle / project / updater / ui_update / mcp_bridge）は、`tdrive_toon.<name>` を「`from tdrive.<name> import *`」の転送として残す（テスト・userSetup・古い uiScript が import している）。**Python の公開名は変えない = 互換を壊さない**
- Maya モジュール名（`TDriveToon.mod`）・`TDRIVE_ROOT` / `TDRIVE_PROJECT` はそのまま。メニューの表示名だけ「T-Drive」に変える
- `tests/test_ui_patterns.py` の検査対象に `tdrive/` `tdrive_facial/` を加える（lambda の約束・keep_alive・cmds.delete の空リスト）

### 2.2 ツールの約束（`tdrive.tool.Tool`）

```python
class Tool(Protocol):
    id: str            # "toon" | "facial"
    label: str         # 1 段目のタブ名
    def build_widget(self) -> QtWidgets.QWidget: ...
    def on_scene_opened(self) -> None: ...     # シーンに記録されたデータを開く
    def on_scene_saved(self) -> None: ...      # データも保存する
    def undo(self) -> bool: ...
    def redo(self) -> bool: ...
    def refresh(self) -> None: ...
```

殻は Tool を知っているだけで、Toon・Facial の中身を知らない。3 つ目のツールを足すときも同じ形で足せる。

## 3. データとコア（Maya 非依存）

### 3.1 データクラス（`core/model.py`）

`.fcpose.json` と 1:1 のデータクラス。UE 版の構造体・JSON のキーと対応が取れる名前にする。

```python
@dataclass
class BoneOffset:  t: Vec3 = (0,0,0); r: Quat = (0,0,0,1); s: Vec3 = (1,1,1)     # 親ボーン空間の加算。S → R → T
@dataclass
class SourcePose:  curves: dict[str, float]; bones: dict[str, BoneOffset]
@dataclass
class GridPoint:   row: int; col: int; is_key: bool; pose: SourcePose             # 自動生成 = is_key False
@dataclass
class Layer:       name: str; emotion_curve: str; enabled: bool; points: dict[tuple[int,int], GridPoint]
@dataclass
class Grid:        yaw_range: float = 90; pitch_range: float = 45; cols: int = 5; rows: int = 3
                   base_bone: str = ""; forward_axis: str = "+Z"; center_offset: Vec3 = (0,0,0); edge_fade: float = 15
@dataclass
class Policy:      expression_dampen: float = 0.5; interp_speed: float = 10; snap_angle: float = 45
                   fade: tuple[float,float] = (0,0); global_alpha: float = 1
@dataclass
class Document:    meta; grid; policy; layers: list[Layer]; working_set; mirror; autogen; exclude
                   intensity_curves; profile; asset; target; bake; limits; material; quality; perspective; layer_weights
                   extra: dict   # 知らないキーはそのまま持って書き戻す（往復で落とさない）
```

- 読み込み: 知らないキーは `extra` に保持。`version` が新しければ警告して読める所だけ読む。欠けたキーは既定値
- 書き出し: UE 版と同じキー順・同じ表記（クォータニオン `[x,y,z,w]`、作った点だけ）。**UE 版が書いたファイルを読んで何も変えずに書くと、意味が同じ JSON になる**（往復テスト）
- ベイクの状態（焼いた時のポーズのハッシュ）は JSON に入れず、Maya シーン側（§4.1）に持つ

### 3.2 計算（`core/evaluate.py`）

UE 版 `FacialCore`（`FacialCore.h/.cpp`）の写し。入出力は素のデータ（Document を直接は受けない）。

```python
def compute_view_angles(head_pos, head_forward_yaw_deg, viewer_pos) -> tuple[float, float]      # 正準空間。Yaw / Pitch（度）
def evaluate_correction(grid: GridShape, layers: list[LayerEvalInput], yaw, pitch,
                        sharpness=1.0) -> list[MorphWeight]                                      # (シェイプ名, 重み)
def expression_scale(dampen, intensity_sum) -> float
def distance_fade(distance, start, end) -> float
def smooth_weights(prev, target, speed, dt, snap) -> dict[str, float]
```

- `GridShape` = 範囲・列行・端のフェード。`LayerEvalInput` = 重み・点ごとの「焼いたシェイプ名（無ければ None）」
- 追加機能（シャープニング・コマ打ち・パース・距離の重み・誇張）は引数の既定値で「何もしない」になる形で足す（既存のテストデータの結果が変わらないこと）
- `core/space.py`: Maya（Y-up 右手 cm）・Unity（Y-up 左手 m）・UE（Z-up 左手 cm）と正準空間の相互変換。位置・回転・`forwardAxis`・`boneAxis`。**ここだけが座標系を知っている**

### 3.3 共通のテストデータ（`tests/facial/conformance/`）

```jsonc
// evaluate_*.json
{ "case": "four_corner_blend", "grid": {...}, "layers": [...], "yaw": 22.5, "pitch": 0, "emotions": {...},
  "expect": { "FC_a_Neutral_R1_C2": 0.5, "FC_a_Neutral_R1_C3": 0.5 }, "tolerance": 1e-4 }
```

- 種類: `evaluate`（UE の 11 件 + 追加機能）/ `view_angles`（各環境の座標での入力 → 期待する Yaw・Pitch）/ `autofill`（キー → 生成された点）/ `bone_offset`（S → R → T と合成順）/ `smooth`（時系列）/ `presenter`（操作列 → 状態）
- Python 側は pytest、C# 側は EditMode テストが**同じファイル**を読む（Unity のテストは `../../tests/facial/conformance` を相対で読む。パッケージ配布物には含めない）

### 3.4 画面の状態（`core/presenters.py`）

Qt に依存しない状態機械。ウィジェットは「描く + 入力を渡す」だけにする（Toon の「UI はセッション層の薄いラッパー」と同じ）。

| Presenter | 持つもの |
|---|---|
| `GridPresenter` | 点の状態（空 / キー / 自動生成 / 未ベイク / 変更あり）と色、選択、点 ⇔ 角度、集計、右クリックの操作 |
| `PosePresenter` | 作業セットでの絞り込み、編集中の値、ミラー、可動域での丸め、未保存の判定 |
| `LayerPresenter` | レイヤーの追加・改名・削除・上限（16）・プリセット |
| `ValidationPresenter` | 検出結果の一覧、候補、改名の適用 |

## 4. Maya 側

### 4.1 シーンとの関係

| もの | 持ち方 |
|---|---|
| 開いているデータのパス | `fileInfo("tdFacialData", "<プロジェクト相対パス>")`（Look と同じ流儀）。SceneOpened で開き、SceneSaved で保存 |
| ベイクの状態 | 顔メッシュの blendShape ノードの文字列アトリビュート `tdFacialBakeState`（JSON: シェイプ名 → 焼いた時のポーズのハッシュ）。ソースとハッシュが違えば「変更あり」 |
| プレビュー用ノード | `tdFacialPreview_<asset>`（transform。子にノード群）。出力（FBX）から除く |
| 基準姿勢 | ベイク・ボーンのずれの基準はバインドポーズ（`dagPose -bindPose`）。編集開始時にジョイントのローカル値を控える |

- シェイプの名前とノード: ソースの名前 `bs.eye_close_L` = blendShape ノード `bs` のターゲット `eye_close_L`。`scene.py` が「名前 ⇔（ノード, ターゲット番号）」の対応を引く。ノード名が付かない名前（`.` なし）は対象メッシュの最初の blendShape のターゲットとして探す
- FC_* を足す先: 顔メッシュに**元からある blendShape ノード**（shizuku なら FBX 由来のノード）にターゲットを追加する。新しいノードを作らない理由: FBX 出力・Unity 取り込みで 1 メッシュ 1 セットに確実にまとまるため。追加するのは `FC_*` / `fcs_*` だけで、既存ターゲットの番号・値は変えない。blendShape が無いメッシュには `tdFacial_<mesh>` を（スキンより前に）作る

### 4.2 ポーズを当てる / 取り込む（`pose_apply.py`）

- 当てる: シェイプ = blendShape の重みを設定。ボーン = 基準のローカル行列に `offset × base`（S → R → T）で加算。除外・作業セットの外は触らない
- 取り込む: シェイプ = 重みが 0 でないもの。ボーン = 現在のローカル行列と基準の差を BoneOffset に分解（しきい値未満は捨てる）
- 編集中は「どの点のポーズがシーンに当たっているか」を session が持つ。点を切り替える / 編集を抜けると基準へ戻す（シーンを汚さない）。Maya の Undo とは別に、エディタ内 Undo（Toon と同じ方式）で値を戻す

### 4.3 ベイク（`bake.py`）

```
基準姿勢にする（バインドポーズ・FC_* と土台の表情の重み 0・プレビュー用ノードを一時切断）
N = 何も当てない状態の頂点（MFnMesh.getPoints、変形後、オブジェクト空間）
for layer, point in 焼く対象:
    ポーズを当てる → P = 変形後の頂点 → D = P − N
    if layer が感情: D −= D_neutral(同じ点)            # 差分ベイク
    D のうち長さ < deltaThreshold を 0 に
    ターゲット FC_* を作る / 置き換える（頂点差分を直接書き込む: inputTargetItem の points / components）
元に戻す → tdFacialBakeState を更新 → 要約を返す
```

- 差分は**スキンの後**の形で取るが、書き込み先（blendShape）はスキンの前。バインドポーズではスキンの行列が単位行列になるので一致する（基準姿勢でしか焼かない理由）。基準姿勢にできないシーン（バインドポーズが無い等）は検証で止める
- ターゲットの形は別メッシュを作らず差分を直接書く（シーンに複製メッシュを残さない。数百個でも速い）
- 全体を 1 つの Undo にまとめる（`undoInfo -openChunk`）。途中で失敗しても必ず元へ戻す（try / finally）
- 複数メッシュ（`extraMeshes`）は同じ手順をメッシュごとに行い、同じ名前のターゲットを作る

### 4.4 カメラ連動プレビュー（`preview_rig.py`）

T-20 のプレビュー（`view_correction.py`）と同じ「標準ノード + expression」方式を、格子に一般化する。

```
camera.worldMatrix ─┐
head.worldMatrix   ─┼→ expression（1 個）: Yaw / Pitch → 4 点の重み × 端のフェード × 感情 × 強さ → 出力アトリビュート out[i]
tdFacialPreview.（manualYaw / manualPitch / useManual / alpha / emotion_<layer> …キー可）
out[i] → blendShape.weight[FC_*]
```

- expression の中身は `core/evaluate.py` の式から**文字列として生成**する（格子・レイヤーが変わったら作り直す）。手で式を 2 回書かない。生成した expression の結果が Python の計算と一致することを `tests/maya/facial_smoke.py` で確かめる
- スムージング・スナップ・距離フェード・コマ打ちは入れない（Maya はその時刻の状態だけで決まる方がレンダリングで安全）
- プラグインのノード（MPxNode）にしない理由: シーンがプラグインに依存する・リロードで落ちる危険（2026-09-28 の教訓）を避けるため。点の数 × レイヤー数が増えて expression が重い場合に備え、評価時間を計測して検証に出す
- キーに焼く: 時間範囲を 1 フレームずつ評価して `FC_*` の重みにキーを打ち、接続を外す

### 4.5 シェイプ作成支援（`shapes.py`）

| 機能 | 実装の要点 |
|---|---|
| この角度で彫る | 点のポーズを当てた状態で、空のターゲット `fcs_<layer>_R{r}_C{c}` を作り、`sculptTarget` で編集対象にしてスカルプトツールへ。終了時にそのターゲットの重み 1 をポーズに記録。彫るのはスキンの前の空間なので、頭ボーンが基準姿勢のときだけ開始できる（ポーズ側のボーンのずらしは可） |
| ポーズをシェイプにする | 変形後の頂点 − 基準 を新しいターゲットに書く（ベイクと同じ関数） |
| 左右に分ける | 頂点の X（顔の左右）で重みを付けて 2 つのターゲットに分ける。ぼかし幅は cm 指定。重みは smoothstep |
| ミラー | 対称の対応表（位置で最近傍、許容誤差つき）を作ってキャッシュ → 差分を鏡映。対応が取れない頂点を選択して見せる |
| 中間・誇張 | in-between ターゲット（重み 0.5 など）/ 誇張は別ターゲット `<name>_Ex` として作り、プロファイルの可動域を 2 まで開く |
| 組み合わせ補正 | `combinationShape` ノードで 2 入力の積 → 補正ターゲット。ベイクには結果が含まれるので Unity へは持ち出さない |
| 別メッシュへ写す | 頂点数・順序が同じなら差分を複写。違えば近接（proximityWrap）で変形させて差分を取る |
| 整理 | しきい値未満の差分を消す、空・未参照のターゲット一覧、プロファイルに沿った改名（検証と同じ候補）、不足一覧 |

どれも「ターゲットの差分を読む / 書く」共通関数（`scene.read_target_delta` / `write_target_delta`）の上に作る。

### 4.6 Unity の `.anim` を読む（`core/unity_anim.py`）

- YAML のうち `m_FloatCurves`（`attribute: blendShape.<name>`、`path`）と Transform のカーブ（目のボーン）だけを読む。Unity の YAML は独自のタグ（`%TAG` / `--- !u!74`）があるので、行ベースの小さなパーサにする（外部ライブラリを増やさない）
- 値は 0〜100 → 0〜1 に直す。指定時刻の値を取り出して SourcePose にする（1 フレームだけのクリップが多い）
- 用途: ポーズへの読み込み、土台の表情としてのプレビュー（「この表情のとき補正がどう見えるか」）、感情レイヤーの出発点

### 4.7 出力（`export.py`）

- FBX: Toon の Unity 出力（別プロセスの mayapy で出力用のシーンを作る方式）に相乗りする。除くもの = `fcs_*` ターゲット・`tdFacialPreview_*`・プレビュー用ライトなど。ブレンドシェイプ・スキン・スムージングの設定は固定のプリセット
- fctrack: `tdFacialPreview_<asset>` のキー可アトリビュートのカーブを、秒単位のキー列として出す

```jsonc
{ "format": "FacialTrack", "version": 1, "shot": "S010", "model": "shizuku", "frameRate": 30, "range": [0, 240],
  "curves": { "alpha": [[0.0, 1.0], [2.5, 0.0]], "emotion.Joy": [[1.0, 0.0], [1.5, 1.0]],
              "manualYaw": [], "manualPitch": [], "useManual": [] } }
```

## 5. Unity 側（`com.tdrive.facial`）

### 5.1 アセンブリ

| asmdef | 参照 | 中身 |
|---|---|---|
| `TDrive.Facial.Core` | なし（`noEngineReferences`） | `FacialCore`（evaluate / view angles / fade / smooth）、データの素の型 |
| `TDrive.Facial.Runtime` | Core | `FacialCorrectionData`（SO）、`FacialCorrectionOverrides`（SO）、`FacialCorrectionRunner`、`FacialMaterialOutput`、`FacialDebugOverlay` |
| `TDrive.Facial.Timeline` | Runtime, Unity.Timeline（versionDefines で有無を判定） | `FacialCorrectionTrack` / `Clip` / `MixerBehaviour` |
| `TDrive.Facial.Editor` | Runtime, (Timeline) | `.fcpose.json` のインポーター、Runner のインスペクター、格子ビューア、検証、プレビュー |
| `TDrive.Facial.DDrive` / `.Editor` | Runtime, Timeline, DDrive.Runtime（com.ddrive.core があるときだけ） | バインドの補助、プール返却時のリセット、D-Drive の検証（IValidator）、fctrack の取り込み |

### 5.2 取り込み

- 拡張子が `.json` のままだと ScriptedImporter を当てられないので、**出力時の拡張子を `.fcpose`** にする（Maya の出力タブが `export/unity/` へ書くときに付け替える。中身は同じ JSON）。fctrack も同様に `.fctrack`
- `FcposeImporter : ScriptedImporter` → `FacialCorrectionData`（主アセット）。ソースの JSON 文字列はエディタ専用の欄に保持
- `FacialCorrectionOverrides` は別アセット（手で作る / Runner のインスペクターから作る）。取り込み直しても消えない。値は「上書きする / しない」のフラグ付き

### 5.3 Runner

```csharp
[DefaultExecutionOrder(10000)]            // Animator・D-Drive の AnimManager（Update）より後
public sealed class FacialCorrectionRunner : MonoBehaviour {
    public FacialCorrectionData data; public FacialCorrectionOverrides overrides;
    public SkinnedMeshRenderer[] targets;  // 空なら子から FC_* を持つものを探す
    public Transform baseBone;             // 空なら data.grid.baseBone を名前で探す
    public Transform viewerOverride; public bool useManualAngles; public float manualYaw, manualPitch;
    [Range(0,1)] public float alpha = 1f; public float[] emotionWeights; public bool[] mutedLayers;
    // Timeline など外からの一時的な上書き（毎フレーム消える）
    public void PushOverride(in FacialFrameOverride o);
    void LateUpdate();                     // [14] §6.2 の 1〜8
    void OnDisable();                      // 書いた FC_* を 0 に戻す
}
```

- 計算は `FacialCore`（静的関数・割り当てなし）。Runner は Unity の入出力だけ（座標変換 → Core → `SetBlendShapeWeight(index, w * 100)`）
- 編集時プレビュー: `[ExecuteAlways]` にはせず、エディタ側のプレビュー用ドライバが `Evaluate(sceneViewCamera)` を呼ぶ（保存データを汚さない。プレビューを切ると 0 に戻す）
- 表情の強さ = `intensityCurves`（無ければ作業セット）のシェイプの現在値の合計（LateUpdate 時点 = アニメーション適用後）

### 5.4 Timeline

- `FacialCorrectionTrack : TrackAsset`、`[TrackBindingType(typeof(Animator))]`、`[TrackClipType(typeof(FacialCorrectionClip))]`
- `FacialCorrectionMixerBehaviour.ProcessFrame`: クリップの重みで値をブレンドし、`playerData`（Animator）の子の Runner に `PushOverride` する。Runner は LateUpdate でそれを使って評価する（Timeline は値を渡すだけ。ブレンドシェイプを直接書かない = 二重の書き込みを作らない）
- `GatherProperties` で Runner の対象シェイプを登録し、プレビューを抜けたら元に戻るようにする
- D-Drive の `CutsceneManager` は `PlayableDirector` を手動で進めて `Evaluate()` するだけなので、そのまま動く。発火型（1 回だけ鳴らす）ではないので `CutsceneDirectorContext.FireEnabled` を見る必要はない

### 5.5 D-Drive ブリッジ（`Bridges/DDrive`）

D-Drive を変更しない前提でできることだけを入れる（変更が要るものは [16](16_ddrive_changes_for_facial.md)）。

| 補助 | 内容 |
|---|---|
| バインドの補助 | `FacialCorrectionTrack` がバインドされていないとき、同じ役名（トラック名の規則 `<役名>_Facial` ⇔ `<役名>`）のアニメーショントラックのバインド先 Animator を使う |
| プール返却 | D-Drive のモデルはプールに戻っても blendShape の重みを戻さない。Runner の `OnDisable` で戻す（§5.3）ので追加の処理は不要。確認のテストだけ持つ |
| 検証 | `IValidator` 実装: CutsceneData の `.playable` にある FacialCorrectionTrack のバインド先に Runner があるか、Data の FC_* がモデルにそろっているか |
| fctrack の取り込み | `AssetPostprocessor`（D-Drive の Cutscene 取り込みより後の順番）で、`SourceAssets/Cutscene/<Category>/<Shot>__<Model>.fctrack` から、対応する `.playable` に `FacialCorrectionTrack` を足す / 更新する。自動で作ったトラックは名前 `<Model>_Facial(auto)` で見分け、デザイナーのトラックは触らない |

## 6. テスト

| 層 | 内容 | 実行 |
|---|---|---|
| core 単体 | fcpose の往復（UE 版の実ファイルを含む）、座標変換、evaluate / view angles / autofill / smooth（共通のテストデータ）、プロファイル、検証・改名、Presenter の遷移、.anim パーサ | pytest（Maya なし） |
| Maya スモーク | 合成の小さな頭（球 + 頭・目のジョイント + シェイプ 4 個をテスト内で作る）で: ポーズを当てる / 取り込む → ベイク（差分・しきい値・FC_ だけ触る・Undo）→ プレビューの重み = Python の計算 → 出力から fcs_ が除かれる | mayapy（`MAYA_DISABLE_CER=1`） |
| shizuku 結合 | モデルがあるときだけ: 取り込み → 作業セット → 数点のキー → 自動生成 → 全点ベイク → カメラ一周の重み。所要時間を記録 | mayapy（任意） |
| UI の約束 | `tests/test_ui_patterns.py` を tdrive / tdrive_facial にも適用 | pytest |
| Unity | 共通のテストデータ（EditMode）、Runner（合成メッシュで LateUpdate の結果・OnDisable のリセット）、Timeline（Evaluate で値が渡る）、インポーターの往復 | Unity Test Runner |
| 見た目 | Maya のプレビューと Unity のランタイムで、同じ角度・同じ感情の重みのとき FC_* の重みが一致（数値）。画像の比較は Toon のパリティ（[09](09_render_parity.md)）に相乗り | 手動 + 数値 |

## 7. 互換性の区分（[06](06_release_versioning.md) に追加する項目）

| 変更 | 区分 |
|---|---|
| `.fcpose.json` のキーの削除・改名・意味の変更、FC_* の命名規則の変更、計算結果が変わる変更（既定値のまま） | MAJOR |
| キー・追加機能の追加（既定で今までと同じ結果）、Unity コンポーネントの公開フィールドの追加 | MINOR |
| 共通のテストデータの期待値を変える変更 | MAJOR（UE 版との一致を壊すため。UE 版起点の仕様変更に追従するときだけ） |

パラメータ契約のスナップショット（Toon）と同じ考えで、`schema/fcpose.schema.json` と FC_* の命名のスナップショットテストを持つ。

## 8. 進める順番

```
Phase S  サンプルモデル移行（shizuku を Maya に取り込み、Toon の Look を作る）
Phase F0 基盤: 殻（2 段タブ）+ core（データ・計算・自動生成・プロファイル・検証）      ← Maya なしで作れてテストできる所から
Phase F1 Maya の最小の一周: セットアップ → グリッド → ポーズ → ベイク → プレビュー → 出力
Phase F2 Maya ならではの作成支援（シェイプタブ）
Phase FU Unity ランタイム・プレビュー・調整
Phase FT Timeline / D-Drive 連携
Phase F5 UE 版の未実装項目（R-32〜R-37 ほか）
```

- F0 の core と FU-1（C# の計算）は同じテストデータを使うので並行できる
- Unity 側（FU / FT）は Phase U-1（パッケージ雛形・検証用プロジェクト `unity/TDriveSandbox`）と同じ土台を使う。U-1 が未着手なので、FU-0 で雛形だけ先に作る
- 実装はチケット単位で進め、デザイナーが触る機能はマニュアル（`docs/DesignerManual/facial-*.html`）を同じコミットで更新する

## 9. リスクと対策

| リスク | 対策 |
|---|---|
| 殻への切り出しで Toon が壊れる | F0-1 は挙動を変えない移動だけにする。既存のテスト（スモーク 85 件以上）が全部通ることを完了条件に。転送モジュールで import 名を保つ |
| expression が重い（点 × レイヤーが多い） | 評価時間を計測。重ければ「アクティブなレイヤーだけ」「まわりの点だけ動的に接続」に切り替える余地を残す |
| バインドポーズ以外で焼いてずれる | 基準姿勢にできないと焼かない。検証で事前に出す |
| blendShape のターゲット番号がずれて既存アニメが壊れる | 追加だけ（番号は末尾に足す）。既存ターゲットの削除・並べ替えをしない。スモークで既存ターゲットの番号と差分が不変であることを確かめる |
| ミラー・左右分割が非対称メッシュで破綻 | 対応が取れない頂点を見せて止める（黙って進めない） |
| FBX で FC_* が数百個になり取り込みが重い | しきい値で頂点数を減らす。格子・レイヤーの数の目安をマニュアルに書く。ベイクの要約に合計頂点数を出す |
| UE 版と結果がずれる | 共通のテストデータ。UE 版の実ファイル（`NewFacialCorrectionAsset_R2_C4.fcpose.json`）での往復 |
| shizuku の規約 | リポジトリに入れない。テストは合成データを主に |
| D-Drive のチケット 7-8 と二重実装 | [16](16_ddrive_changes_for_facial.md) §2 の提案で一本化（要確認） |
