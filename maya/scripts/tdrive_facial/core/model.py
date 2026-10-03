"""`.fcpose.json` と 1:1 のデータクラス（Maya 非依存）。

- UE 版の構造体・JSON のキーと対応が取れる名前（キーは camelCase、Python は snake_case）
- 既定値は UE 版アセットの既定値に合わせる（欠けたキーはこの既定値になる）:
  meta = cm / Z-up / left（UE 準拠。キーが無い JSON は UE 版が書いたものとみなす）、
  forwardAxis = "+X"、baseBone = "head"、mirror.boneAxis = "Y"、edgeFade = 15、
  expressionDampen = 0.5、interpSpeed = 10、snapAngle = 45、idwPower = 2
  （Maya の新規データは session 側が meta・forwardAxis・boneAxis を明示して作る）
- 各データクラスの `extra` は「知らないキー」。読み込みで受け取り、書き出しで戻す（往復で落とさない）
- T-Drive の追加キー（asset / target / bake / limits / material / quality / perspective /
  layerWeights / sculptShapes）は None = JSON に無い（書き出しでも出さない）。UE 版が書いたファイルを
  読んで書いても追加キーが増えないようにするため
- 点（GridPoint）は「作った点だけ」を `Layer.points` に持つ。消すときは辞書から消す
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

Vec3 = tuple[float, float, float]
Quat = tuple[float, float, float, float]  # [x, y, z, w]

FORMAT_CORRECTION = "FacialCorrection"
FORMAT_POSE = "FacialPose"
SUPPORTED_VERSION = 1
MAX_LAYERS = 16  # UE 版 UFacialCorrectionAsset::MaxLayers（Neutral を含む）

FORWARD_AXES = ("+X", "-X", "+Y", "-Y", "+Z", "-Z")  # ±Z は T-Drive が足した値（Y-up の環境用）
MIRROR_AXES = ("X", "Y", "Z")
FILL_MODES = ("IDW", "NearestKey")


@dataclass
class BoneOffset:
    """親ボーン空間での加算トランスフォーム。適用順 Scale → Rotation → Translation。
    r はクォータニオン [x, y, z, w]（書き出しでは正規化済みを期待。読み込みでは触らない）。"""

    t: Vec3 = (0.0, 0.0, 0.0)
    r: Quat = (0.0, 0.0, 0.0, 1.0)
    s: Vec3 = (1.0, 1.0, 1.0)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class SourcePose:
    """1 点分のポーズ。curves = シェイプ名 → 重み、bones = ボーン名 → BoneOffset。名前は大文字小文字を区別する。"""

    curves: dict[str, float] = field(default_factory=dict)
    bones: dict[str, BoneOffset] = field(default_factory=dict)

    def is_empty(self) -> bool:
        return not self.curves and not self.bones


@dataclass
class GridPoint:
    """格子の 1 点。is_key=True がキー、False は自動生成。位置は (row, col)。"""

    row: int
    col: int
    is_key: bool = False
    pose: SourcePose = field(default_factory=SourcePose)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class Layer:
    """レイヤー。レイヤー 0 は必ず Neutral。points は (row, col) → GridPoint（作った点だけ）。"""

    name: str = "Neutral"
    emotion_curve: str = ""
    enabled: bool = True
    points: dict[tuple[int, int], GridPoint] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class Meta:
    """座標系のメタ。この系で数値（ボーンのずらし・centerOffset など）を保存している。"""

    unit: str = "cm"  # "cm" | "m" | "mm" | ...（space.UNIT_TO_CM のキー）
    up_axis: str = "Z"  # "Y" | "Z"
    handedness: str = "left"  # "left" | "right"
    source: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class Grid:
    """格子の定義。角度は基準ボーン正面 = 0°、格子の中央。点 index = row * cols + col。"""

    yaw_range: float = 90.0
    pitch_range: float = 45.0
    cols: int = 5
    rows: int = 3
    base_bone: str = "head"
    forward_axis: str = "+X"
    center_offset: Vec3 = (0.0, 0.0, 0.0)
    edge_fade: float = 15.0
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class Policy:
    expression_dampen: float = 0.5
    interp_speed: float = 10.0
    snap_angle: float = 45.0
    fade: tuple[float, float] = (0.0, 0.0)  # 距離フェードの開始 / 終了（0, 0 = 無効）
    global_alpha: float = 1.0
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class WorkingSet:
    curves: list[str] = field(default_factory=list)
    bones: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class Mirror:
    enabled: bool = True
    suffix_l: str = "_L"
    suffix_r: str = "_R"
    exclude: list[str] = field(default_factory=list)
    bone_axis: str = "Y"  # 鏡映する軸（meta の系での軸名。Maya では顔の左右 = "X"）
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class Autogen:
    mode: str = "IDW"  # "IDW" | "NearestKey"
    idw_power: float = 2.0
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class Exclude:
    curves: list[str] = field(default_factory=list)
    bones: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)


# --- T-Drive の追加キー（docs/14 §4.2）---


@dataclass
class Target:
    mesh: str = ""
    extra_meshes: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class Bake:
    delta_threshold: float = 0.001  # cm 未満の頂点は捨てる
    differential: bool = True
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class Material:
    mode: str = "none"  # "none" | "propertyBlock"
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class Quality:
    sharpness: float = 1.0
    step_fps: float = 0.0
    angle_epsilon: float = 0.1
    max_lod: int = 0
    exaggeration: float = 1.0  # 誇張（`_Ex` シェイプ）の既定の強さ 0〜1。1 = 作った通り（F5-5）
    extra: dict[str, Any] = field(default_factory=dict)


PERSPECTIVE_AXES = ("distance", "fov")  # 軸: distance = 視点と格子の中心の距離（文書の単位）/ fov = 視点の縦の画角（度）
MAX_PERSPECTIVE_KEYS = 8


@dataclass
class PerspectiveKey:
    """パース補正のキー 1 個。value = 軸の値、curves / bones = 基準姿勢に足すポーズ（空 = 補正なしのキー）。
    配列の順番がシェイプの番号（`FC_<asset>_Persp_K{n}`）。"""

    value: float = 0.0
    curves: dict[str, float] = field(default_factory=dict)
    bones: dict[str, BoneOffset] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def pose(self) -> SourcePose:
        """ポーズとして見る（curves / bones は同じ辞書を共有する。pose_hash・検査用）。"""
        return SourcePose(curves=self.curves, bones=self.bones)

    def is_empty(self) -> bool:
        return not self.curves and not self.bones


@dataclass
class Perspective:
    enabled: bool = False
    axis: str = "distance"  # PERSPECTIVE_AXES のどれか（知らない値も読んで保持し、検証で知らせる）
    strength: float = 1.0  # 0〜1。全体の強さ（Unity の調整値・Timeline で上書きできる）
    keys: list[PerspectiveKey] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class SculptShapes:
    prefix: str = "fcs_"
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class Document:
    """`"format": "FacialCorrection"`（全アセット）。"""

    version: int = SUPPORTED_VERSION
    meta: Meta = field(default_factory=Meta)
    grid: Grid = field(default_factory=Grid)
    policy: Policy = field(default_factory=Policy)
    layers: list[Layer] = field(default_factory=lambda: [Layer()])
    working_set: WorkingSet = field(default_factory=WorkingSet)
    mirror: Mirror = field(default_factory=Mirror)
    autogen: Autogen = field(default_factory=Autogen)
    exclude: Exclude = field(default_factory=Exclude)
    intensity_curves: list[str] = field(default_factory=list)
    profile: str = ""
    # --- T-Drive の追加キー（None = JSON に無い）---
    asset: Optional[str] = None
    target: Optional[Target] = None
    bake: Optional[Bake] = None
    limits: Optional[dict[str, tuple[float, float]]] = None
    material: Optional[Material] = None
    quality: Optional[Quality] = None
    perspective: Optional[Perspective] = None
    layer_weights: Optional[dict[str, dict[str, Any]]] = None
    sculpt_shapes: Optional[SculptShapes] = None
    # 知らないキー（トップレベル）
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def format(self) -> str:
        return FORMAT_CORRECTION


@dataclass
class PoseDocument:
    """`"format": "FacialPose"`（1 点分のポーズ。ポーズの持ち運び・カット補正用）。"""

    version: int = SUPPORTED_VERSION
    meta: Meta = field(default_factory=Meta)
    pose: SourcePose = field(default_factory=SourcePose)
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def format(self) -> str:
        return FORMAT_POSE
