// 座標系の変換（Maya / Unity / UE ⇔ 正準空間）。Python 版 core/space.py の写し。ここだけが座標系を知っている。
// 正準空間 = UE 準拠（cm / Z-up / 左手 / 前 +X・右 +Y・上 +Z）。canonical = M · v（M は署名付きの軸の入れ替え）。
using System;

namespace TDrive.Facial.Core
{
    /// <summary>座標系の記述 {unit, upAxis, handedness}。.fcpose.json の meta と同じ。</summary>
    public sealed class SpaceSpec
    {
        public readonly string Unit;       // "mm" "cm" "m" "km" "in" "ft"
        public readonly string UpAxis;     // "Y" | "Z"
        public readonly string Handedness; // "left" | "right"

        public SpaceSpec(string unit, string upAxis, string handedness)
        {
            if (FacialSpace.UnitToCm(unit) <= 0.0) throw new ArgumentException("未対応の単位: '" + unit + "'");
            if (upAxis != "Y" && upAxis != "Z") throw new ArgumentException("未対応の upAxis: '" + upAxis + "'");
            if (handedness != "left" && handedness != "right") throw new ArgumentException("未対応の handedness: '" + handedness + "'");
            Unit = unit;
            UpAxis = upAxis;
            Handedness = handedness;
        }
    }

    /// <summary>src → dst の変換（軸の入れ替え行列・長さの倍率・向きの反転 det）。</summary>
    public sealed class SpaceConverter
    {
        readonly int[] _m; // 行優先 3x3
        readonly double _scale;
        readonly int _det;
        readonly string _srcUpAxis;

        internal SpaceConverter(int[] m, double scale, int det, string srcUpAxis)
        {
            _m = m;
            _scale = scale;
            _det = det;
            _srcUpAxis = srcUpAxis;
        }

        public double Scale { get { return _scale; } }
        public int Det { get { return _det; } }

        /// <summary>行列の (r, c) 成分。</summary>
        public int Matrix(int r, int c) { return _m[r * 3 + c]; }

        Vec3 Apply(double x, double y, double z)
        {
            return new Vec3(
                _m[0] * x + _m[1] * y + _m[2] * z,
                _m[3] * x + _m[4] * y + _m[5] * z,
                _m[6] * x + _m[7] * y + _m[8] * z);
        }

        Vec3 ApplyAbs(double x, double y, double z)
        {
            return new Vec3(
                Math.Abs(_m[0]) * x + Math.Abs(_m[1]) * y + Math.Abs(_m[2]) * z,
                Math.Abs(_m[3]) * x + Math.Abs(_m[4]) * y + Math.Abs(_m[5]) * z,
                Math.Abs(_m[6]) * x + Math.Abs(_m[7]) * y + Math.Abs(_m[8]) * z);
        }

        public Vec3 Position(Vec3 p)
        {
            Vec3 v = Apply(p.X, p.Y, p.Z);
            return new Vec3(v.X * _scale, v.Y * _scale, v.Z * _scale);
        }

        public Vec3 Direction(Vec3 d) { return Apply(d.X, d.Y, d.Z); }

        public Quat Quaternion(Quat q)
        {
            Vec3 v = Apply(q.X, q.Y, q.Z);
            return new Quat(_det * v.X, _det * v.Y, _det * v.Z, q.W);
        }

        /// <summary>スケールは成分の入れ替えのみ（符号は付けない）。</summary>
        public Vec3 ScaleVector(Vec3 s) { return ApplyAbs(s.X, s.Y, s.Z); }

        public BoneOffset ConvertBoneOffset(BoneOffset b)
        {
            return new BoneOffset(Position(b.T), Quaternion(b.R), ScaleVector(b.S));
        }

        /// <summary>forwardAxis（"+X" … "-Z"）を変換後の軸名へ。元の系の上軸は前方向にできない。</summary>
        public string ForwardAxis(string axis)
        {
            Vec3 vec;
            if (!FacialSpace.TryAxisVector(axis, out vec)) throw new ArgumentException("未知の forwardAxis: '" + axis + "'");
            if (axis[1].ToString() == _srcUpAxis)
                throw new ArgumentException("上軸 '" + axis + "' は forwardAxis にできません（上軸 = " + _srcUpAxis + "）");
            Vec3 o = Direction(vec);
            string name = FacialSpace.AxisNameOf(o);
            if (name == null) throw new InvalidOperationException("signed permutation の結果が軸にならない");
            return name;
        }

        /// <summary>mirror.boneAxis（"X" / "Y" / "Z"）を |M| で入れ替えた軸名（符号なし）。</summary>
        public string MirrorAxis(string axis)
        {
            int idx = axis == null ? -1 : "XYZ".IndexOf(axis, StringComparison.Ordinal);
            if (axis == null || axis.Length != 1 || idx < 0) throw new ArgumentException("未知の mirror.boneAxis: '" + axis + "'");
            Vec3 o = ApplyAbs(idx == 0 ? 1.0 : 0.0, idx == 1 ? 1.0 : 0.0, idx == 2 ? 1.0 : 0.0);
            int best = 0;
            double bv = o.X;
            if (o.Y > bv) { best = 1; bv = o.Y; }
            if (o.Z > bv) { best = 2; }
            return "XYZ".Substring(best, 1);
        }
    }

    public static class FacialSpace
    {
        public static readonly SpaceSpec Maya = new SpaceSpec("cm", "Y", "right");
        public static readonly SpaceSpec Unity = new SpaceSpec("m", "Y", "left");
        public static readonly SpaceSpec UE = new SpaceSpec("cm", "Z", "left");
        public static readonly SpaceSpec Canonical = UE;

        /// <summary>長さの単位 → cm。未知の単位は 0。</summary>
        public static double UnitToCm(string unit)
        {
            switch (unit)
            {
                case "mm": return 0.1;
                case "cm": return 1.0;
                case "m": return 100.0;
                case "km": return 100000.0;
                case "in": return 2.54;
                case "ft": return 30.48;
                default: return 0.0;
            }
        }

        /// <summary>系 → 正準の軸の入れ替え行列 M（行優先 9 要素。長さの換算は含まない）。</summary>
        public static int[] ToCanonicalMatrix(SpaceSpec s)
        {
            if (s == null) throw new ArgumentNullException("s");
            if (s.UpAxis == "Y")
            {
                int h = s.Handedness == "left" ? 1 : -1;
                return new[] { 0, 0, 1, h, 0, 0, 0, 1, 0 };
            }
            if (s.Handedness == "left") return new[] { 1, 0, 0, 0, 1, 0, 0, 0, 1 };
            return new[] { 0, 1, 0, 1, 0, 0, 0, 0, 1 };
        }

        static int Det(int[] m)
        {
            return m[0] * (m[4] * m[8] - m[5] * m[7])
                 - m[1] * (m[3] * m[8] - m[5] * m[6])
                 + m[2] * (m[3] * m[7] - m[4] * m[6]);
        }

        /// <summary>M = M_dst^T · M_src。</summary>
        public static SpaceConverter Converter(SpaceSpec src, SpaceSpec dst)
        {
            if (src == null) throw new ArgumentNullException("src");
            if (dst == null) throw new ArgumentNullException("dst");
            int[] ms = ToCanonicalMatrix(src);
            int[] md = ToCanonicalMatrix(dst);
            var m = new int[9];
            for (int i = 0; i < 3; i++)
                for (int j = 0; j < 3; j++)
                {
                    int sum = 0;
                    for (int k = 0; k < 3; k++) sum += md[k * 3 + i] * ms[k * 3 + j]; // (md^T)[i][k] = md[k][i]
                    m[i * 3 + j] = sum;
                }
            return new SpaceConverter(m, UnitToCm(src.Unit) / UnitToCm(dst.Unit), Det(m), src.UpAxis);
        }

        /// <summary>軸名（"+X" … "-Z"）→ 単位ベクトル。</summary>
        public static bool TryAxisVector(string axis, out Vec3 v)
        {
            v = default(Vec3);
            if (axis == null || axis.Length != 2) return false;
            double sign;
            if (axis[0] == '+') sign = 1.0;
            else if (axis[0] == '-') sign = -1.0;
            else return false;
            switch (axis[1])
            {
                case 'X': v = new Vec3(sign, 0, 0); return true;
                case 'Y': v = new Vec3(0, sign, 0); return true;
                case 'Z': v = new Vec3(0, 0, sign); return true;
                default: return false;
            }
        }

        /// <summary>単位ベクトルに（誤差 1e-9 で）一致する軸名。無ければ null。</summary>
        public static string AxisNameOf(Vec3 v)
        {
            string[] names = { "+X", "-X", "+Y", "-Y", "+Z", "-Z" };
            for (int i = 0; i < names.Length; i++)
            {
                Vec3 a;
                TryAxisVector(names[i], out a);
                if (Math.Abs(v.X - a.X) < 1e-9 && Math.Abs(v.Y - a.Y) < 1e-9 && Math.Abs(v.Z - a.Z) < 1e-9) return names[i];
            }
            return null;
        }

        /// <summary>forwardAxis を正準空間（±X / ±Y）の軸名へ。</summary>
        public static string ForwardAxisToCanonical(string axis, SpaceSpec src)
        {
            return Converter(src, Canonical).ForwardAxis(axis);
        }

        // --- クォータニオン・視点の補助 ---

        public static Quat QuatNormalize(Quat q)
        {
            double n = Math.Sqrt(q.X * q.X + q.Y * q.Y + q.Z * q.Z + q.W * q.W);
            if (n == 0.0) return Quat.Identity;
            return new Quat(q.X / n, q.Y / n, q.Z / n, q.W / n);
        }

        public static Quat QuatFromAxisAngle(Vec3 axis, double angleDeg)
        {
            double n = Math.Sqrt(axis.X * axis.X + axis.Y * axis.Y + axis.Z * axis.Z);
            double h = angleDeg * (Math.PI / 180.0) * 0.5;
            double s = Math.Sin(h) / n;
            return new Quat(axis.X * s, axis.Y * s, axis.Z * s, Math.Cos(h));
        }

        /// <summary>a × b（b を先に適用してから a）。</summary>
        public static Quat QuatMultiply(Quat a, Quat b)
        {
            return new Quat(
                a.W * b.X + a.X * b.W + a.Y * b.Z - a.Z * b.Y,
                a.W * b.Y - a.X * b.Z + a.Y * b.W + a.Z * b.X,
                a.W * b.Z + a.X * b.Y - a.Y * b.X + a.Z * b.W,
                a.W * b.W - a.X * b.X - a.Y * b.Y - a.Z * b.Z);
        }

        /// <summary>v' = q v q*（正規化済みの q。標準の式。右手系・左手系のどちらでも同じ式）。</summary>
        public static Vec3 RotateVector(Quat q, Vec3 v)
        {
            double tx = 2.0 * (q.Y * v.Z - q.Z * v.Y);
            double ty = 2.0 * (q.Z * v.X - q.X * v.Z);
            double tz = 2.0 * (q.X * v.Y - q.Y * v.X);
            return new Vec3(
                v.X + q.W * tx + (q.Y * tz - q.Z * ty),
                v.Y + q.W * ty + (q.Z * tx - q.X * tz),
                v.Z + q.W * tz + (q.X * ty - q.Y * tx));
        }

        /// <summary>
        /// 各環境の座標のまま (Yaw, Pitch) を求める入口。
        /// 格子の中心 = headPos + headRotation で回した centerOffset、前方 = headRotation で回した forwardAxis。
        /// それらを正準空間へ変換 → 前方の水平成分から Yaw を取り FacialCore.ComputeViewAngles へ。
        /// </summary>
        public static void ComputeViewAnglesInSpace(SpaceConverter toCanonical, Vec3 headPos, Quat headRotation,
            string forwardAxis, Vec3 viewerPos, Vec3 centerOffset, out double yawDeg, out double pitchDeg)
        {
            if (toCanonical == null) throw new ArgumentNullException("toCanonical");
            Vec3 o = RotateVector(headRotation, centerOffset);
            var center = new Vec3(headPos.X + o.X, headPos.Y + o.Y, headPos.Z + o.Z);
            Vec3 axisVec;
            if (!TryAxisVector(forwardAxis, out axisVec)) throw new ArgumentException("未知の forwardAxis: '" + forwardAxis + "'");
            Vec3 fwd = toCanonical.Direction(RotateVector(headRotation, axisVec));
            // 頭の Yaw / Pitch の規則は T-Drive（Unity 準拠）を正とする。UE 版とは、前方向が ±Y で頭にロールとピッチが両方あるとき最大 7° ほど違う
            double forwardYaw = Math.Atan2(fwd.Y, fwd.X) * (180.0 / Math.PI);
            FacialCore.ComputeViewAngles(toCanonical.Position(center), forwardYaw, toCanonical.Position(viewerPos),
                out yawDeg, out pitchDeg);
        }

        /// <summary>
        /// ComputeViewAnglesInSpace の、回転ではなく「世界での向き」を直接渡す版。左右反転（スケール -1）のボーンでは、回転だけでは軸の向きを表せないので、
        /// 呼び出し側が TransformDirection で求めた worldForward（forwardAxis の世界での向き）と worldCenterOffset（centerOffset の世界での向き・回転だけ）を渡す。
        /// 回転が正しい（反転なし）ときは ComputeViewAnglesInSpace と同じ結果になる。
        /// </summary>
        public static void ComputeViewAnglesFromWorldVectors(SpaceConverter toCanonical, Vec3 headPos, Vec3 worldCenterOffset, Vec3 worldForward,
            Vec3 viewerPos, out double yawDeg, out double pitchDeg)
        {
            if (toCanonical == null) throw new ArgumentNullException("toCanonical");
            var center = new Vec3(headPos.X + worldCenterOffset.X, headPos.Y + worldCenterOffset.Y, headPos.Z + worldCenterOffset.Z);
            Vec3 fwd = toCanonical.Direction(worldForward);
            double forwardYaw = Math.Atan2(fwd.Y, fwd.X) * (180.0 / Math.PI);
            FacialCore.ComputeViewAngles(toCanonical.Position(center), forwardYaw, toCanonical.Position(viewerPos), out yawDeg, out pitchDeg);
        }

        public static void ComputeViewAnglesInSpace(SpaceSpec space, Vec3 headPos, Quat headRotation,
            string forwardAxis, Vec3 viewerPos, Vec3 centerOffset, out double yawDeg, out double pitchDeg)
        {
            ComputeViewAnglesInSpace(Converter(space, Canonical), headPos, headRotation, forwardAxis, viewerPos,
                centerOffset, out yawDeg, out pitchDeg);
        }
    }
}
