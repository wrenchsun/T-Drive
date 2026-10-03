// 計算に使う素のデータ型（UnityEngine 非依存）。Python 版 core/evaluate.py・core/model.py と対応。
using System.Collections.Generic;

namespace TDrive.Facial.Core
{
    /// <summary>格子の形（角度 → セル位置に必要な最小情報）。</summary>
    public sealed class GridShape
    {
        public double YawRangeDeg = 90.0;
        public double PitchRangeDeg = 45.0;
        public int NumCols = 5;
        public int NumRows = 3;
        /// <summary>範囲の外側でこの幅をかけて 0 へ減衰（0 以下 = 範囲外は即 0）。</summary>
        public double EdgeFadeDeg = 15.0;

        public GridShape() { }

        public GridShape(double yawRangeDeg, double pitchRangeDeg, int numCols, int numRows, double edgeFadeDeg)
        {
            YawRangeDeg = yawRangeDeg;
            PitchRangeDeg = pitchRangeDeg;
            NumCols = numCols;
            NumRows = numRows;
            EdgeFadeDeg = edgeFadeDeg;
        }
    }

    /// <summary>
    /// 1 レイヤー分の評価入力。CornerMorphNames は行優先（index = row * NumCols + col）。
    /// 焼いていない点は null / ""（重み 0 として飛ばす）。EmotionWeight は Layers[0]（Neutral）では無視。
    /// </summary>
    public sealed class LayerEvalInput
    {
        public IReadOnlyList<string> CornerMorphNames;
        public double EmotionWeight;
        public bool Enabled = true;

        public LayerEvalInput() { }

        public LayerEvalInput(IReadOnlyList<string> cornerMorphNames, double emotionWeight, bool enabled)
        {
            CornerMorphNames = cornerMorphNames;
            EmotionWeight = emotionWeight;
            Enabled = enabled;
        }
    }

    /// <summary>シェイプ名と重み。</summary>
    public struct MorphWeight
    {
        public string MorphName;
        public double Weight;

        public MorphWeight(string morphName, double weight)
        {
            MorphName = morphName;
            Weight = weight;
        }
    }

    /// <summary>今の角度が属するセル（デバッグ表示用）。</summary>
    public struct GridCellInfo
    {
        public int Row0, Col0, Row1, Col1;
        public double RowFrac, ColFrac;
        public double FadeScale;
    }

    public readonly struct Vec3
    {
        public readonly double X, Y, Z;
        public Vec3(double x, double y, double z) { X = x; Y = y; Z = z; }
    }

    /// <summary>クォータニオン [x, y, z, w]。</summary>
    public readonly struct Quat
    {
        public readonly double X, Y, Z, W;
        public Quat(double x, double y, double z, double w) { X = x; Y = y; Z = z; W = w; }
        public static Quat Identity { get { return new Quat(0, 0, 0, 1); } }
    }

    /// <summary>親ボーン空間での加算トランスフォーム（S → R → T）。</summary>
    public readonly struct BoneOffset
    {
        public readonly Vec3 T;
        public readonly Quat R;
        public readonly Vec3 S;
        public BoneOffset(Vec3 t, Quat r, Vec3 s) { T = t; R = r; S = s; }
    }
}
