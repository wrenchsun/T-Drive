// .fcpose の素のデータ型（UnityEngine 非依存）。Python 版 core/model.py のうち、ランタイムが使う部分の写し。
// 既定値は model.py と同じ（欠けたキーはこの既定値になる）。座標は meta の系のまま（変換は FacialSpace が受け持つ）。
using System.Collections.Generic;

namespace TDrive.Facial.Core
{
    public sealed class FcBone
    {
        public Vec3 T = new Vec3(0, 0, 0);
        public Quat R = new Quat(0, 0, 0, 1); // [x, y, z, w]。正規化しない
        public Vec3 S = new Vec3(1, 1, 1);
    }

    /// <summary>1 点分のポーズ。名前は大文字小文字を区別する。</summary>
    public sealed class FcPose
    {
        public readonly Dictionary<string, double> Curves = new Dictionary<string, double>();
        public readonly Dictionary<string, FcBone> Bones = new Dictionary<string, FcBone>();
    }

    public sealed class FcPoint
    {
        public int Row;
        public int Col;
        public bool IsKey;
        public FcPose Pose = new FcPose();
    }

    public sealed class FcLayer
    {
        public string Name = "Neutral";
        public string EmotionCurve = "";
        public bool Enabled = true;
        /// <summary>作った点だけ（読んだ順）。格子の外の点も保持する。</summary>
        public readonly List<FcPoint> Points = new List<FcPoint>();

        /// <summary>(row, col) の点。無ければ null。</summary>
        public FcPoint FindPoint(int row, int col)
        {
            for (int i = 0; i < Points.Count; i++)
                if (Points[i].Row == row && Points[i].Col == col) return Points[i];
            return null;
        }
    }

    public sealed class FcMeta
    {
        public string Unit = "cm";
        public string UpAxis = "Z";
        public string Handedness = "left";
        public string Source = "";
    }

    public sealed class FcGrid
    {
        public double YawRange = 90.0;
        public double PitchRange = 45.0;
        public int Cols = 5;
        public int Rows = 3;
        public string BaseBone = "head";
        public string ForwardAxis = "+X";
        public Vec3 CenterOffset = new Vec3(0, 0, 0);
        public double EdgeFade = 15.0;
    }

    public sealed class FcPolicy
    {
        public double ExpressionDampen = 0.5;
        public double InterpSpeed = 10.0;
        public double SnapAngle = 45.0;
        public double FadeStart = 0.0; // 距離フェード（meta の長さの単位。開始 = 終了 = 0 で無効）
        public double FadeEnd = 0.0;
        public double GlobalAlpha = 1.0;
    }

    public sealed class FcQuality
    {
        public double Sharpness = 1.0;
        public double StepFps = 0.0;
        public double AngleEpsilon = 0.1;
        public int MaxLod = 0;
        /// <summary>誇張（_Ex シェイプ）の既定の強さ 0〜1（1 = 作った通り）。F5-5。</summary>
        public double Exaggeration = 1.0;
        /// <summary>補間の種類（"bilinear" 既定 / "catmullRom"）。F5-11。</summary>
        public string Interpolation = "bilinear";
    }

    /// <summary>レイヤーの重みの入力元（layerWeights の 1 項目。F5-3）。Source: "direct" | "curve" | "distance"。</summary>
    public sealed class FcLayerWeight
    {
        public string Source = "direct";
        /// <summary>distance のとき: 開始・終了の距離（ドキュメントの単位）と、そのときの重み。</summary>
        public double Start, End;
        public double From = 0.0, To = 1.0;
    }

    /// <summary>パース補正のキー 1 個（R-34）。配列の順番がシェイプの番号（FC_&lt;asset&gt;_Persp_K{n}）。</summary>
    public sealed class FcPerspectiveKey
    {
        public double Value;
        public FcPose Pose = new FcPose();
        /// <summary>curves も bones も無い = 「補正なし」のキー（シェイプを作らない）。</summary>
        public bool IsEmpty { get { return Pose.Curves.Count == 0 && Pose.Bones.Count == 0; } }
    }

    /// <summary>パース補正（perspective）。Axis: "distance"（視点と格子の中心の距離。meta の長さの単位）| "fov"（縦の画角、度）。</summary>
    public sealed class FcPerspective
    {
        public const string AxisDistance = "distance";
        public const string AxisFov = "fov";
        public bool Enabled;
        public string Axis = AxisDistance;
        public double Strength = 1.0;
        public readonly List<FcPerspectiveKey> Keys = new List<FcPerspectiveKey>();
    }

    /// <summary>リップシンクの行 1 つ（音素 × 感情 → シェイプの重み。ボーンは持たない）。Emotion "" = 基本。</summary>
    public sealed class FcLipSyncEntry
    {
        public string Phoneme = "";
        public string Emotion = "";
        public readonly Dictionary<string, double> Curves = new Dictionary<string, double>();
    }

    /// <summary>声量 → 口の大きさの倍率: lerp(From, To, saturate((v - Min) / (Max - Min)))。</summary>
    public sealed class FcLipSyncVolume
    {
        public double Min = 0.0, Max = 1.0, From = 0.5, To = 1.0;
    }

    /// <summary>リップシンクの対応表（lipSync。R-18）。音声の解析は持たない。Follow = 追従の速さ（1/秒。0 以下 = 即時）。</summary>
    public sealed class FcLipSync
    {
        public bool Enabled = true;
        public double Strength = 1.0;
        public readonly List<string> Phonemes = new List<string>();
        public readonly List<FcLipSyncEntry> Entries = new List<FcLipSyncEntry>();
        public FcLipSyncVolume Volume = new FcLipSyncVolume();
        public double Follow = 20.0;

        /// <summary>(音素, 感情) の行。同じ組が複数あれば先のもの。無ければ null。</summary>
        public FcLipSyncEntry FindEntry(string phoneme, string emotion)
        {
            for (int i = 0; i < Entries.Count; i++)
                if (Entries[i].Phoneme == phoneme && Entries[i].Emotion == emotion) return Entries[i];
            return null;
        }
    }

    public sealed class FcLimit
    {
        public double Min;
        public double Max;
    }

    /// <summary>"format": "FacialCorrection"（全アセット）。T-Drive の追加キーは無ければ null / 既定値。</summary>
    public sealed class FcDocument
    {
        public int Version = FcposeReader.SupportedVersion;
        public FcMeta Meta = new FcMeta();
        public FcGrid Grid = new FcGrid();
        public FcPolicy Policy = new FcPolicy();
        public readonly List<FcLayer> Layers = new List<FcLayer>();
        public readonly List<string> WorkingCurves = new List<string>();
        public readonly List<string> WorkingBones = new List<string>();
        public readonly List<string> ExcludeCurves = new List<string>();
        public readonly List<string> ExcludeBones = new List<string>();
        public readonly List<string> IntensityCurves = new List<string>();
        public string Profile = "";
        // --- T-Drive の追加キー ---
        public string Asset;                // null = JSON に無い
        public string TargetMesh = "";
        public readonly List<string> TargetExtraMeshes = new List<string>();
        public readonly Dictionary<string, FcLimit> Limits = new Dictionary<string, FcLimit>();
        public string MaterialMode = "none";
        public FcQuality Quality = new FcQuality();
        /// <summary>パース補正。無ければ null（何もしない）。</summary>
        public FcPerspective Perspective;
        /// <summary>リップシンクの対応表。無ければ null（何もしない）。</summary>
        public FcLipSync LipSync;
        /// <summary>レイヤー名 → 重みの入力元（無ければ入力は呼び出し側 = direct）。F5-3。</summary>
        public readonly Dictionary<string, FcLayerWeight> LayerWeights = new Dictionary<string, FcLayerWeight>();
    }

    /// <summary>"format": "FacialPose"（1 点分のポーズ）。</summary>
    public sealed class FcPoseDocument
    {
        public int Version = FcposeReader.SupportedVersion;
        public FcMeta Meta = new FcMeta();
        public FcPose Pose = new FcPose();
    }

    /// <summary>読んだ結果。Format に応じて Document / Pose のどちらかが入る。</summary>
    public sealed class FcFile
    {
        public string Format;
        public FcDocument Document;
        public FcPoseDocument Pose;
    }
}
