// 角度 → 補正シェイプの重みの計算（Python 版 core/evaluate.py の写し。UE 版 FacialCore.cpp が元）。
// 計算は正準空間（UE 準拠: cm / Z-up / 左手 / 前 +X）。UnityEngine 非依存・毎フレームの経路で割り当てなし。
using System;
using System.Collections.Generic;

namespace TDrive.Facial.Core
{
    public static class FacialCore
    {
        public const double KindaSmallNumber = 1e-4; // UE の KINDA_SMALL_NUMBER
        public const double SmallNumber = 1e-8;      // UE の UE_SMALL_NUMBER

        const double RadToDeg = 180.0 / Math.PI;
        const double DegToRad = Math.PI / 180.0;

        struct AxisSample
        {
            public int Index0, Index1;
            public double Frac, Fade;
        }

        public static bool IsNearlyZero(double value, double tolerance = SmallNumber)
        {
            return Math.Abs(value) <= tolerance;
        }

        public static double Clamp(double value, double lo, double hi)
        {
            return value < lo ? lo : (value > hi ? hi : value);
        }

        /// <summary>FRotator::ClampAxis: [0, 360) へ。C# の % は C の fmod と同じ（符号は被除数）。</summary>
        public static double ClampAxis(double angle)
        {
            angle = angle % 360.0;
            if (angle < 0.0) angle += 360.0;
            return angle;
        }

        /// <summary>FRotator::NormalizeAxis: (-180, 180] へ。</summary>
        public static double NormalizeAxis(double angle)
        {
            angle = ClampAxis(angle);
            if (angle > 180.0) angle -= 360.0;
            return angle;
        }

        static AxisSample SampleAxis(double angleDeg, double rangeDeg, int numPoints, double edgeFadeDeg)
        {
            var o = new AxisSample { Index0 = 0, Index1 = 0, Frac = 0.0, Fade = 1.0 };
            if (numPoints <= 1) return o; // 単一点は常にその点を 100%
            double safeRange = Math.Max(rangeDeg, KindaSmallNumber);
            double u = (angleDeg / safeRange + 1.0) * 0.5;
            double pos = Clamp(u, 0.0, 1.0) * (numPoints - 1);
            o.Index0 = (int)Clamp(Math.Floor(pos), 0, numPoints - 1);
            o.Index1 = Math.Min(o.Index0 + 1, numPoints - 1);
            o.Frac = pos - o.Index0;
            double excess = Math.Max(0.0, Math.Abs(angleDeg) - rangeDeg);
            if (excess <= 0.0) o.Fade = 1.0;
            else if (edgeFadeDeg <= 0.0) o.Fade = 0.0;
            else o.Fade = Clamp(1.0 - excess / edgeFadeDeg, 0.0, 1.0);
            return o;
        }

        /// <summary>
        /// 角度（度）から補正シェイプの重み一式を output に詰める（output は先にクリアする）。
        /// layers[0] は Neutral（常に全量）。それ以外は emotionWeight を掛けて足す。
        /// 無効レイヤー・重み 0・焼いていない点は飛ばす。並びは最初に現れた順。
        /// </summary>
        /// <remarks>
        /// sharpness（既定 1 = 何もしない）: 4 隅の双線形の重みを w^s / Σ w^s に直す（フェード・レイヤーの重みの前。F5-1）。
        /// exaggeration（0〜1、既定 1）: layer.ExMorphNames の _Ex シェイプの重み = 対応する通常シェイプの重み × exaggeration（F5-5）。
        /// </remarks>
        public static void EvaluateCorrection(GridShape grid, IReadOnlyList<LayerEvalInput> layers,
            double yawDeg, double pitchDeg, List<MorphWeight> output, double sharpness = 1.0, double exaggeration = 1.0)
        {
            output.Clear();
            if (layers.Count == 0 || grid.NumCols <= 0 || grid.NumRows <= 0) return;
            AxisSample col = SampleAxis(yawDeg, grid.YawRangeDeg, grid.NumCols, grid.EdgeFadeDeg);
            AxisSample row = SampleAxis(pitchDeg, grid.PitchRangeDeg, grid.NumRows, grid.EdgeFadeDeg);
            double fadeScale = col.Fade * row.Fade;
            if (fadeScale <= KindaSmallNumber) return; // 範囲の外（フェード幅も超えた）

            double b0 = (1.0 - col.Frac) * (1.0 - row.Frac);
            double b1 = col.Frac * (1.0 - row.Frac);
            double b2 = (1.0 - col.Frac) * row.Frac;
            double b3 = col.Frac * row.Frac;
            if (sharpness != 1.0) Sharpen4(ref b0, ref b1, ref b2, ref b3, sharpness);
            double exag = Clamp(exaggeration, 0.0, 1.0);

            for (int li = 0; li < layers.Count; li++)
            {
                LayerEvalInput layer = layers[li];
                if (!layer.Enabled) continue;
                double layerScale = li == 0 ? 1.0 : layer.EmotionWeight;
                if (IsNearlyZero(layerScale)) continue;
                IReadOnlyList<string> names = layer.CornerMorphNames;
                int count = names == null ? 0 : names.Count;
                IReadOnlyList<string> exNames = layer.ExMorphNames;
                int exCount = exNames == null ? 0 : exNames.Count;
                for (int k = 0; k < 4; k++)
                {
                    int cRow, cCol;
                    double bilinear;
                    switch (k)
                    {
                        case 0: cRow = row.Index0; cCol = col.Index0; bilinear = b0; break;
                        case 1: cRow = row.Index0; cCol = col.Index1; bilinear = b1; break;
                        case 2: cRow = row.Index1; cCol = col.Index0; bilinear = b2; break;
                        default: cRow = row.Index1; cCol = col.Index1; bilinear = b3; break;
                    }
                    double weight = bilinear * layerScale * fadeScale;
                    if (IsNearlyZero(weight)) continue;
                    int pointIndex = cRow * grid.NumCols + cCol;
                    if (pointIndex < 0 || pointIndex >= count) continue; // フェイルソフト
                    string name = names[pointIndex];
                    if (string.IsNullOrEmpty(name)) continue; // 焼いていない点
                    AddWeight(output, name, weight);
                    if (exCount > 0 && exag > 0.0 && pointIndex < exCount)
                    {
                        string exName = exNames[pointIndex];
                        if (!string.IsNullOrEmpty(exName)) AddWeight(output, exName, weight * exag);
                    }
                }
            }
            // 打ち消し合って 0 近傍になった分は除く（順序を保って詰める）
            int w = 0;
            for (int i = 0; i < output.Count; i++)
            {
                if (IsNearlyZero(output[i].Weight)) continue;
                if (w != i) output[w] = output[i];
                w++;
            }
            if (w < output.Count) output.RemoveRange(w, output.Count - w);
        }

        static void AddWeight(List<MorphWeight> output, string name, double weight)
        {
            int at = IndexOfName(output, name);
            if (at < 0)
            {
                output.Add(new MorphWeight(name, weight));
            }
            else
            {
                MorphWeight m = output[at];
                m.Weight += weight;
                output[at] = m;
            }
        }

        public const double SharpnessMin = 0.01;
        public const double SharpnessMax = 64.0;

        public static double ClampSharpness(double sharpness)
        {
            return Clamp(sharpness, SharpnessMin, SharpnessMax);
        }

        static double PowW(double w, double s) { return w > 0.0 ? Math.Pow(w, s) : 0.0; }

        /// <summary>4 隅の重みを w^s / Σ w^s に直す（0 は 0 のまま。合計 0 ならそのまま）。割り当てなし。</summary>
        static void Sharpen4(ref double w0, ref double w1, ref double w2, ref double w3, double sharpness)
        {
            double s = ClampSharpness(sharpness);
            if (s == 1.0) return;
            double p0 = PowW(w0, s), p1 = PowW(w1, s), p2 = PowW(w2, s), p3 = PowW(w3, s);
            double total = p0 + p1 + p2 + p3;
            if (total <= 0.0) return;
            w0 = p0 / total; w1 = p1 / total; w2 = p2 / total; w3 = p3 / total;
        }

        /// <summary>キー角度の強調（R-32）の補助（テスト・UI 用）。weights を w^s / Σ w^s に直す。</summary>
        public static void SharpenWeights(double[] weights, double sharpness)
        {
            double s = ClampSharpness(sharpness);
            if (s == 1.0 || weights == null) return;
            double total = 0.0;
            for (int i = 0; i < weights.Length; i++) total += PowW(weights[i], s);
            if (total <= 0.0) return;
            for (int i = 0; i < weights.Length; i++) weights[i] = PowW(weights[i], s) / total;
        }

        /// <summary>距離でレイヤーの重みを決める（R-35）。lerp(from, to, saturate((d - start) / (end - start)))。end == start は d &gt;= start で to、他は from。</summary>
        public static double LayerWeightFromDistance(double distance, double start, double end, double from, double to)
        {
            double t;
            if (end == start) t = distance >= start ? 1.0 : 0.0;
            else t = Clamp((distance - start) / (end - start), 0.0, 1.0);
            if (t <= 0.0) return from;
            if (t >= 1.0) return to;
            return from + (to - from) * t;
        }

        /// <summary>
        /// パース補正のキーの重み（R-34。Python 版 evaluate.perspective_weights と同じ）。values = キーの value（並んでいなくてよい）、
        /// x = 軸の値、output = 長さ count 以上の配列（先頭 count 個に values と同じ順で書く）。割り当てなし。
        /// 昇順に並べたとき x を挟む 2 つを直線で混ぜる。範囲の外は端のキーが 1。キー 0 個は何も書かない。1 個は 1。x が NaN は全部 0。
        /// 同じ value のキーは添字が小さいほうだけが重みを受け取る。value が有限でないキーは常に 0。
        /// </summary>
        public static void PerspectiveWeights(IReadOnlyList<double> values, int count, double x, double[] output)
        {
            for (int i = 0; i < count; i++) output[i] = 0.0;
            if (count <= 0 || double.IsNaN(x)) return;
            int lo = -1, hi = -1, first = -1;
            for (int i = 0; i < count; i++)
            {
                double v = values[i];
                if (double.IsNaN(v) || double.IsInfinity(v)) continue;
                bool dup = false;
                for (int j = 0; j < i; j++) if (values[j] == v) { dup = true; break; }
                if (dup) continue; // 同じ value は若い添字だけ
                if (first < 0 || v < values[first]) first = i;
                if (v <= x) { if (lo < 0 || v > values[lo]) lo = i; }
                else { if (hi < 0 || v < values[hi]) hi = i; }
            }
            if (first < 0) return;
            if (lo < 0) { output[first] = 1.0; return; } // いちばん小さいキーより下
            if (hi < 0) { output[lo] = 1.0; return; }    // いちばん大きいキー以上
            double t = (x - values[lo]) / (values[hi] - values[lo]);
            output[lo] = 1.0 - t;
            output[hi] = t;
        }

        /// <summary>
        /// コマ打ちの判定（R-33）。accum は前回の評価からの経過時間。accum += dt。stepFps &lt;= 0 は (true, 0)。
        /// force（最初のフレーム・カット）か accum が 1/stepFps に届いたら評価する。
        /// </summary>
        public static bool StepGate(double accum, double dt, double stepFps, bool force, out double newAccum)
        {
            if (stepFps <= 0.0) { newAccum = 0.0; return true; }
            accum += dt;
            double period = 1.0 / stepFps;
            if (force || accum + 1e-9 >= period)
            {
                double rem = accum % period;
                if (rem + 1e-9 >= period) rem = 0.0; // 周期ぴったりの誤差で残りが周期ぎりぎりにならないように
                newAccum = rem;
                return true;
            }
            newAccum = accum;
            return false;
        }

        static int IndexOfName(IReadOnlyList<MorphWeight> list, string name)
        {
            for (int i = 0; i < list.Count; i++)
                if (string.Equals(list[i].MorphName, name, StringComparison.Ordinal)) return i;
            return -1;
        }

        /// <summary>今の角度が属するセル（4 隅の番号・補間係数・端のフェード）。</summary>
        public static GridCellInfo ComputeGridCell(GridShape grid, double yawDeg, double pitchDeg)
        {
            var info = new GridCellInfo { FadeScale = 1.0 };
            if (grid.NumCols <= 0 || grid.NumRows <= 0) return info;
            AxisSample col = SampleAxis(yawDeg, grid.YawRangeDeg, grid.NumCols, grid.EdgeFadeDeg);
            AxisSample row = SampleAxis(pitchDeg, grid.PitchRangeDeg, grid.NumRows, grid.EdgeFadeDeg);
            info.Row0 = row.Index0; info.Col0 = col.Index0;
            info.Row1 = row.Index1; info.Col1 = col.Index1;
            info.RowFrac = row.Frac; info.ColFrac = col.Frac;
            info.FadeScale = col.Fade * row.Fade;
            return info;
        }

        /// <summary>格子の点 (row, col) の (Yaw, Pitch)。1 点しか無い軸は 0°。</summary>
        public static void PointAngles(double yawRangeDeg, double pitchRangeDeg, int numCols, int numRows,
            int row, int col, out double yawDeg, out double pitchDeg)
        {
            double u = numCols > 1 ? (double)col / (numCols - 1) : 0.5;
            double v = numRows > 1 ? (double)row / (numRows - 1) : 0.5;
            yawDeg = (u * 2.0 - 1.0) * yawRangeDeg;
            pitchDeg = (v * 2.0 - 1.0) * pitchRangeDeg;
        }

        /// <summary>forwardAxis → 基準ボーンのワールド Yaw に足す角度（正準空間の ±X / ±Y のみ）。</summary>
        public static double ForwardAxisYawOffsetDeg(string axis)
        {
            switch (axis)
            {
                case "+X": return 0.0;
                case "-X": return 180.0;
                case "+Y": return 90.0;
                case "-Y": return -90.0;
                default:
                    throw new ArgumentException("正準空間で扱えない forwardAxis: '" + axis + "'（±X / ±Y のみ）");
            }
        }

        /// <summary>基準位置から見た視点の (Yaw, Pitch)（度）。Pitch 正 = ふかん。</summary>
        public static void ComputeViewAngles(Vec3 headPos, double headForwardYawDeg, Vec3 viewerPos,
            out double yawDeg, out double pitchDeg)
        {
            double dx = viewerPos.X - headPos.X;
            double dy = viewerPos.Y - headPos.Y;
            double dz = viewerPos.Z - headPos.Z;
            double horizontal = Math.Sqrt(dx * dx + dy * dy);
            double camAngle = Math.Atan2(dy, dx) * RadToDeg;
            yawDeg = NormalizeAxis(headForwardYawDeg - camAngle);
            pitchDeg = Math.Atan2(dz, horizontal) * RadToDeg;
        }

        /// <summary>ComputeViewAngles の逆写像。基準位置から視点へ向かう単位ベクトル（正準空間）。</summary>
        public static Vec3 ComputeViewDirection(double headForwardYawDeg, double yawDeg, double pitchDeg)
        {
            double cam = (headForwardYawDeg - yawDeg) * DegToRad;
            double pitch = pitchDeg * DegToRad;
            return new Vec3(Math.Cos(cam) * Math.Cos(pitch), Math.Sin(cam) * Math.Cos(pitch), Math.Sin(pitch));
        }

        /// <summary>カット切り替え判定。前回が無い、または Yaw（正規化した差）か Pitch の変化が snapAngleDeg を超えたら true。</summary>
        public static bool ShouldSnap(bool hasPrev, double prevYawDeg, double prevPitchDeg,
            double yawDeg, double pitchDeg, double snapAngleDeg)
        {
            if (!hasPrev) return true;
            return Math.Abs(NormalizeAxis(yawDeg - prevYawDeg)) > snapAngleDeg
                || Math.Abs(pitchDeg - prevPitchDeg) > snapAngleDeg;
        }

        /// <summary>表情が強いときに補正を弱める: 1 - dampen × s（どちらも 0〜1 に丸める）。</summary>
        public static double ExpressionScale(double expressionDampen, double intensityS)
        {
            return 1.0 - Clamp(expressionDampen, 0.0, 1.0) * Clamp(intensityS, 0.0, 1.0);
        }

        public static float ExpressionScale(float expressionDampen, float intensityS)
        {
            return (float)ExpressionScale((double)expressionDampen, (double)intensityS);
        }

        /// <summary>視点までの距離フェード。end &lt;= start は無効（常に 1）。</summary>
        public static double DistanceFade(double distance, double fadeStart, double fadeEnd)
        {
            if (fadeEnd <= fadeStart) return 1.0;
            if (distance <= fadeStart) return 1.0;
            if (distance >= fadeEnd) return 0.0;
            return 1.0 - (distance - fadeStart) / (fadeEnd - fadeStart);
        }

        public static float DistanceFade(float distance, float fadeStart, float fadeEnd)
        {
            return (float)DistanceFade((double)distance, (double)fadeStart, (double)fadeEnd);
        }

        /// <summary>UE の FMath::FInterpTo と同じ。</summary>
        public static double FInterpTo(double current, double target, double deltaTime, double interpSpeed)
        {
            if (interpSpeed <= 0.0) return target;
            double dist = target - current;
            if (dist * dist < SmallNumber) return target;
            double deltaMove = dist * Clamp(deltaTime * interpSpeed, 0.0, 1.0);
            return current + deltaMove;
        }

        /// <summary>
        /// 重みの指数補間。interpSpeed &lt;= 0 または snap なら target をそのまま返す。
        /// 前回にだけある名前は 0 へ補間し、ほぼ 0 になるまで残す。
        /// 並びは「前回の順（残るもの）→ 今回新しく出てきたもの」。output は prev / target と別のリスト。
        /// </summary>
        public static void SmoothWeights(IReadOnlyList<MorphWeight> prev, IReadOnlyList<MorphWeight> target,
            double deltaTime, double interpSpeed, bool snap, List<MorphWeight> output)
        {
            output.Clear();
            bool instant = snap || interpSpeed <= 0.0;
            for (int i = 0; i < prev.Count; i++)
            {
                MorphWeight p = prev[i];
                int ti = IndexOfName(target, p.MorphName); // 同名は先勝ち
                bool hasTarget = ti >= 0;
                double tv = hasTarget ? target[ti].Weight : 0.0;
                double nv = instant ? tv : FInterpTo(p.Weight, tv, deltaTime, interpSpeed);
                if (hasTarget || !IsNearlyZero(nv)) output.Add(new MorphWeight(p.MorphName, nv));
            }
            for (int i = 0; i < target.Count; i++)
            {
                MorphWeight t = target[i];
                if (IndexOfName(prev, t.MorphName) >= 0) continue;
                double nv = instant ? t.Weight : FInterpTo(0.0, t.Weight, deltaTime, interpSpeed);
                output.Add(new MorphWeight(t.MorphName, nv));
            }
        }
    }
}
