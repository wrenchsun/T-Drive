// .fcpose（JSON）の読み取り。Python 版 core/fcpose_io.py の読み込み側の写し。UnityEngine 非依存。
// - 知らないキーは無視する。欠けたキー・型が合わない値は model.py と同じ既定値（UE 版と同じ寛容さ）
// - version がこの実装より新しいときは warn コールバックへ警告を出し、読める所だけ読む
// - 格子の外の点も落とさず保持する
// - FacialCorrection で layers が空 / 無いときは Neutral を 1 つ足す
using System;
using System.Collections.Generic;

namespace TDrive.Facial.Core
{
    /// <summary>読めない（JSON が壊れている・format が違う）。</summary>
    public sealed class FcposeException : FormatException
    {
        public FcposeException(string message) : base(message) { }
    }

    public static class FcposeReader
    {
        public const int SupportedVersion = 1;
        public const string FormatCorrection = "FacialCorrection";
        public const string FormatPose = "FacialPose";

        /// <summary>JSON 文字列 → FcFile（format で Document / Pose が決まる）。warn は null 可。</summary>
        public static FcFile Read(string text, Action<string> warn)
        {
            object root;
            try { root = MiniJson.Parse(text); }
            catch (FormatException e) { throw new FcposeException("JSON の解析に失敗しました（書式が壊れています）: " + e.Message); }
            var d = root as Dictionary<string, object>;
            if (d == null) throw new FcposeException("JSON のトップレベルがオブジェクトではありません");
            string fmt = Str(d, "format", null);
            var file = new FcFile { Format = fmt };
            if (fmt == FormatCorrection) file.Document = ReadDocument(d, warn);
            else if (fmt == FormatPose) file.Pose = ReadPoseDocument(d, warn);
            else throw new FcposeException("format が FacialCorrection / FacialPose ではありません: " + (fmt ?? "null"));
            return file;
        }

        /// <summary>FacialCorrection だけを読む（FacialPose は例外）。</summary>
        public static FcDocument ReadDocument(string text, Action<string> warn)
        {
            FcFile f = Read(text, warn);
            if (f.Document == null) throw new FcposeException("FacialCorrection 形式ではありません（FacialPose です）");
            return f.Document;
        }

        // --- 型を確かめる取り出し（Python の _f / _i / _s / _b / _vec / _strs / _obj） ---

        static bool IsNum(object v) { return v is double; }

        /// <summary>double → int（0 方向への切り捨て。範囲外は int の端に収める。未定義の (int) キャストを避ける）。</summary>
        internal static int ToInt(double v)
        {
            if (double.IsNaN(v)) return 0;
            if (v >= int.MaxValue) return int.MaxValue;
            if (v <= int.MinValue) return int.MinValue;
            return (int)v;
        }

        static double F(Dictionary<string, object> d, string key, double def)
        {
            object v;
            return d != null && d.TryGetValue(key, out v) && IsNum(v) ? (double)v : def;
        }

        static int I(Dictionary<string, object> d, string key, int def)
        {
            object v;
            return d != null && d.TryGetValue(key, out v) && IsNum(v) ? ToInt((double)v) : def; // Python の int() と同じ 0 方向への切り捨て
        }

        static string Str(Dictionary<string, object> d, string key, string def)
        {
            object v;
            return d != null && d.TryGetValue(key, out v) && v is string ? (string)v : def;
        }

        static bool B(Dictionary<string, object> d, string key, bool def)
        {
            object v;
            return d != null && d.TryGetValue(key, out v) && v is bool ? (bool)v : def;
        }

        static Dictionary<string, object> Obj(Dictionary<string, object> d, string key)
        {
            object v;
            return d != null && d.TryGetValue(key, out v) ? v as Dictionary<string, object> : null;
        }

        /// <summary>長さ n 以上で先頭 n 個がすべて数の配列。違えば null。</summary>
        static double[] Vec(object v, int n)
        {
            var l = v as List<object>;
            if (l == null || l.Count < n) return null;
            var o = new double[n];
            for (int i = 0; i < n; i++)
            {
                if (!IsNum(l[i])) return null;
                o[i] = (double)l[i];
            }
            return o;
        }

        static Vec3 Vec3Of(Dictionary<string, object> d, string key, Vec3 def)
        {
            object v;
            double[] a = d != null && d.TryGetValue(key, out v) ? Vec(v, 3) : null;
            return a == null ? def : new Vec3(a[0], a[1], a[2]);
        }

        static void Strs(Dictionary<string, object> d, string key, List<string> into)
        {
            object v;
            var l = d != null && d.TryGetValue(key, out v) ? v as List<object> : null;
            if (l == null) return;
            for (int i = 0; i < l.Count; i++)
                if (l[i] is string) into.Add((string)l[i]);
        }

        // --- 各部 ---

        static int CheckVersion(Dictionary<string, object> d, Action<string> warn)
        {
            int version = I(d, "version", SupportedVersion);
            if (version > SupportedVersion && warn != null)
                warn("fcpose の version=" + version + " はこの実装（" + SupportedVersion + "）より新しいため、読める範囲だけ読みます");
            return version;
        }

        static FcMeta ReadMeta(Dictionary<string, object> d)
        {
            var m = new FcMeta();
            if (d == null) return m;
            m.Unit = Str(d, "unit", m.Unit);
            m.UpAxis = Str(d, "upAxis", m.UpAxis);
            m.Handedness = Str(d, "handedness", m.Handedness);
            m.Source = Str(d, "source", m.Source);
            return m;
        }

        static FcBone ReadBone(Dictionary<string, object> d)
        {
            var b = new FcBone();
            b.T = Vec3Of(d, "t", b.T);
            object v;
            double[] r = d.TryGetValue("r", out v) ? Vec(v, 4) : null;
            if (r != null) b.R = new Quat(r[0], r[1], r[2], r[3]);
            b.S = Vec3Of(d, "s", b.S);
            return b;
        }

        static FcPose ReadPose(object curves, object bones)
        {
            var pose = new FcPose();
            var c = curves as Dictionary<string, object>;
            if (c != null)
                foreach (KeyValuePair<string, object> kv in c)
                    if (IsNum(kv.Value)) pose.Curves[kv.Key] = (double)kv.Value;
            var b = bones as Dictionary<string, object>;
            if (b != null)
                foreach (KeyValuePair<string, object> kv in b)
                {
                    var bd = kv.Value as Dictionary<string, object>;
                    if (bd != null) pose.Bones[kv.Key] = ReadBone(bd);
                }
            return pose;
        }

        static FcPoint ReadPoint(Dictionary<string, object> d, Action<string> warn)
        {
            object r, c;
            if (!(d.TryGetValue("row", out r) && IsNum(r) && d.TryGetValue("col", out c) && IsNum(c)))
            {
                if (warn != null) warn("row / col が無い点を読み飛ばしました");
                return null;
            }
            object curves, bones;
            d.TryGetValue("curves", out curves);
            d.TryGetValue("bones", out bones);
            return new FcPoint
            {
                Row = ToInt((double)r),
                Col = ToInt((double)c),
                IsKey = B(d, "isKey", false),
                Pose = ReadPose(curves, bones),
            };
        }

        static FcLayer ReadLayer(Dictionary<string, object> d, Action<string> warn)
        {
            var layer = new FcLayer
            {
                Name = Str(d, "name", "Neutral"),
                EmotionCurve = Str(d, "emotionCurve", ""),
                Enabled = B(d, "enabled", true),
            };
            object pv;
            var points = d.TryGetValue("points", out pv) ? pv as List<object> : null;
            if (points != null)
            {
                for (int i = 0; i < points.Count; i++)
                {
                    var pd = points[i] as Dictionary<string, object>;
                    if (pd == null) continue;
                    FcPoint p = ReadPoint(pd, warn);
                    if (p == null) continue;
                    // 同じ (row, col) は後勝ち（Python の辞書と同じ）。最初の位置を保つ
                    int at = -1;
                    for (int k = 0; k < layer.Points.Count; k++)
                        if (layer.Points[k].Row == p.Row && layer.Points[k].Col == p.Col) { at = k; break; }
                    if (at >= 0) layer.Points[at] = p; else layer.Points.Add(p);
                }
            }
            return layer;
        }

        static FcPerspective ReadPerspective(Dictionary<string, object> d, Action<string> warn)
        {
            var p = new FcPerspective
            {
                Enabled = B(d, "enabled", false),
                Axis = Str(d, "axis", FcPerspective.AxisDistance),
                Strength = F(d, "strength", 1.0),
            };
            object kv;
            var keys = d.TryGetValue("keys", out kv) ? kv as List<object> : null;
            if (keys == null) return p;
            for (int i = 0; i < keys.Count; i++)
            {
                var kd = keys[i] as Dictionary<string, object>;
                if (kd == null)
                {
                    if (warn != null) warn("オブジェクトでないパース補正のキーを読み飛ばしました");
                    continue;
                }
                object vv;
                if (!(kd.TryGetValue("value", out vv) && IsNum(vv)))
                {
                    if (warn != null) warn("value が数でないパース補正のキーを読み飛ばしました");
                    continue;
                }
                object curves, bones;
                kd.TryGetValue("curves", out curves);
                kd.TryGetValue("bones", out bones);
                p.Keys.Add(new FcPerspectiveKey { Value = (double)vv, Pose = ReadPose(curves, bones) });
            }
            return p;
        }

        /// <summary>lipSync のオブジェクト（パース済み）→ FcLipSync。共通のテストデータの読み込みにも使う。</summary>
        public static FcLipSync ReadLipSync(Dictionary<string, object> d, Action<string> warn)
        {
            var def = new FcLipSync();
            var l = new FcLipSync
            {
                Enabled = B(d, "enabled", true),
                Strength = F(d, "strength", def.Strength),
                Follow = F(d, "follow", def.Follow),
            };
            Strs(d, "phonemes", l.Phonemes);
            Dictionary<string, object> vd = Obj(d, "volume");
            if (vd != null)
            {
                var dv = new FcLipSyncVolume();
                l.Volume = new FcLipSyncVolume
                {
                    Min = F(vd, "min", dv.Min),
                    Max = F(vd, "max", dv.Max),
                    From = F(vd, "from", dv.From),
                    To = F(vd, "to", dv.To),
                };
            }
            object ev;
            var entries = d.TryGetValue("entries", out ev) ? ev as List<object> : null;
            if (entries == null) return l;
            for (int i = 0; i < entries.Count; i++)
            {
                var ed = entries[i] as Dictionary<string, object>;
                if (ed == null)
                {
                    if (warn != null) warn("オブジェクトでないリップシンクの行を読み飛ばしました");
                    continue;
                }
                string ph = Str(ed, "phoneme", null);
                if (ph == null)
                {
                    if (warn != null) warn("phoneme が文字列でないリップシンクの行を読み飛ばしました");
                    continue;
                }
                var entry = new FcLipSyncEntry { Phoneme = ph, Emotion = Str(ed, "emotion", "") };
                object cv;
                ed.TryGetValue("curves", out cv);
                FcPose pose = ReadPose(cv, null);
                foreach (KeyValuePair<string, double> kv in pose.Curves) entry.Curves[kv.Key] = kv.Value;
                l.Entries.Add(entry);
            }
            return l;
        }

        static FcDocument ReadDocument(Dictionary<string, object> d, Action<string> warn)
        {
            var doc = new FcDocument();
            doc.Version = CheckVersion(d, warn);
            doc.Meta = ReadMeta(Obj(d, "meta"));

            Dictionary<string, object> g = Obj(d, "grid");
            if (g != null)
            {
                var def = new FcGrid();
                doc.Grid = new FcGrid
                {
                    YawRange = F(g, "yawRange", def.YawRange),
                    PitchRange = F(g, "pitchRange", def.PitchRange),
                    Cols = I(g, "cols", def.Cols),
                    Rows = I(g, "rows", def.Rows),
                    BaseBone = Str(g, "baseBone", def.BaseBone),
                    ForwardAxis = Str(g, "forwardAxis", def.ForwardAxis),
                    CenterOffset = Vec3Of(g, "centerOffset", def.CenterOffset),
                    EdgeFade = F(g, "edgeFade", def.EdgeFade),
                };
            }

            Dictionary<string, object> p = Obj(d, "policy");
            if (p != null)
            {
                var def = new FcPolicy();
                doc.Policy.ExpressionDampen = F(p, "expressionDampen", def.ExpressionDampen);
                doc.Policy.InterpSpeed = F(p, "interpSpeed", def.InterpSpeed);
                doc.Policy.SnapAngle = F(p, "snapAngle", def.SnapAngle);
                doc.Policy.GlobalAlpha = F(p, "globalAlpha", def.GlobalAlpha);
                object fv;
                double[] fade = p.TryGetValue("fade", out fv) ? Vec(fv, 2) : null;
                if (fade != null) { doc.Policy.FadeStart = fade[0]; doc.Policy.FadeEnd = fade[1]; }
            }

            object lv;
            var layers = d.TryGetValue("layers", out lv) ? lv as List<object> : null;
            if (layers != null)
                for (int i = 0; i < layers.Count; i++)
                {
                    var ld = layers[i] as Dictionary<string, object>;
                    if (ld != null) doc.Layers.Add(ReadLayer(ld, warn));
                }
            if (doc.Layers.Count == 0) doc.Layers.Add(new FcLayer()); // 最低でも Neutral は存在させる

            Dictionary<string, object> ws = Obj(d, "workingSet");
            Strs(ws, "curves", doc.WorkingCurves);
            Strs(ws, "bones", doc.WorkingBones);
            Dictionary<string, object> ex = Obj(d, "exclude");
            Strs(ex, "curves", doc.ExcludeCurves);
            Strs(ex, "bones", doc.ExcludeBones);
            Strs(d, "intensityCurves", doc.IntensityCurves);
            doc.Profile = Str(d, "profile", "");

            // T-Drive の追加キー
            doc.Asset = Str(d, "asset", null);
            Dictionary<string, object> t = Obj(d, "target");
            if (t != null)
            {
                doc.TargetMesh = Str(t, "mesh", "");
                Strs(t, "extraMeshes", doc.TargetExtraMeshes);
            }
            Dictionary<string, object> lim = Obj(d, "limits");
            if (lim != null)
                foreach (KeyValuePair<string, object> kv in lim)
                {
                    double[] r = Vec(kv.Value, 2);
                    if (r != null) doc.Limits[kv.Key] = new FcLimit { Min = r[0], Max = r[1] };
                }
            Dictionary<string, object> mat = Obj(d, "material");
            if (mat != null) doc.MaterialMode = Str(mat, "mode", "none");
            Dictionary<string, object> q = Obj(d, "quality");
            if (q != null)
            {
                var def = new FcQuality();
                doc.Quality = new FcQuality
                {
                    Sharpness = F(q, "sharpness", def.Sharpness),
                    StepFps = F(q, "stepFps", def.StepFps),
                    AngleEpsilon = F(q, "angleEpsilon", def.AngleEpsilon),
                    MaxLod = I(q, "maxLod", def.MaxLod),
                    Exaggeration = F(q, "exaggeration", def.Exaggeration),
                    Interpolation = Str(q, "interpolation", def.Interpolation),
                };
            }
            Dictionary<string, object> ps = Obj(d, "perspective");
            if (ps != null) doc.Perspective = ReadPerspective(ps, warn);
            Dictionary<string, object> ls = Obj(d, "lipSync");
            if (ls != null) doc.LipSync = ReadLipSync(ls, warn);
            Dictionary<string, object> lw = Obj(d, "layerWeights");
            if (lw != null)
                foreach (KeyValuePair<string, object> kv in lw)
                {
                    var o = kv.Value as Dictionary<string, object>;
                    if (o == null) continue;
                    var w = new FcLayerWeight();
                    w.Source = Str(o, "source", "direct");
                    w.Start = F(o, "start", 0.0);
                    w.End = F(o, "end", 0.0);
                    w.From = F(o, "from", 0.0);
                    w.To = F(o, "to", 1.0);
                    doc.LayerWeights[kv.Key] = w;
                }
            return doc;
        }

        static FcPoseDocument ReadPoseDocument(Dictionary<string, object> d, Action<string> warn)
        {
            object curves, bones;
            d.TryGetValue("curves", out curves);
            d.TryGetValue("bones", out bones);
            return new FcPoseDocument
            {
                Version = CheckVersion(d, warn),
                Meta = ReadMeta(Obj(d, "meta")),
                Pose = ReadPose(curves, bones),
            };
        }
    }
}
