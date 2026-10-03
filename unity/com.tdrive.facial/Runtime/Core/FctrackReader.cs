// .fctrack（Timeline 用の演出カーブ）の読み取り。Python 版 core/fctrack.py（from_dict / validate）の写し。UnityEngine 非依存。
// 形式: { "format": "FacialTrack", "version": 1, "shot", "model", "frameRate", "range": [開始, 終了], "curves": { 名前: [[秒, 値], ...] } }
// 正しくなければ FctrackException（メッセージはそのまま画面に出せる日本語）。知らないキーは無視する。
using System;
using System.Collections.Generic;

namespace TDrive.Facial.Core
{
    /// <summary>読めない / 正しくない .fctrack。</summary>
    public sealed class FctrackException : FormatException
    {
        public FctrackException(string message) : base(message) { }
    }

    public struct FcKey
    {
        public double Time;  // 秒（range の開始が 0）
        public double Value;
        public FcKey(double time, double value) { Time = time; Value = value; }
    }

    public sealed class FcTrack
    {
        public int Version = FctrackReader.SupportedVersion;
        public string Shot = "";
        public string Model = "";
        public double FrameRate = 30.0;
        public double RangeStart, RangeEnd; // フレーム
        public readonly Dictionary<string, List<FcKey>> Curves = new Dictionary<string, List<FcKey>>(StringComparer.Ordinal);

        public double DurationSeconds { get { return FrameRate > 0.0 ? (RangeEnd - RangeStart) / FrameRate : 0.0; } }
    }

    public static class FctrackReader
    {
        public const string Format = "FacialTrack";
        public const int SupportedVersion = 1;
        public const string EmotionPrefix = "emotion.";
        public const string FileSuffix = ".fctrack";
        public static readonly string[] FixedCurves = { "alpha", "useManual", "manualYaw", "manualPitch" };

        public static bool IsValidCurveName(string name)
        {
            if (name == null) return false;
            for (int i = 0; i < FixedCurves.Length; i++) if (FixedCurves[i] == name) return true;
            return name.StartsWith(EmotionPrefix, StringComparison.Ordinal) && name.Length > EmotionPrefix.Length;
        }

        /// <summary>"<ショット>__<モデル>" 形式のファイル名（拡張子なし）を分ける。最初の "__" で区切る（D-Drive の CutsceneShotParser と同じ）。</summary>
        public static bool TryParseFileStem(string stem, out string shot, out string model)
        {
            shot = null; model = null;
            if (string.IsNullOrEmpty(stem)) return false;
            int sep = stem.IndexOf("__", StringComparison.Ordinal);
            if (sep <= 0 || sep + 2 >= stem.Length) return false;
            shot = stem.Substring(0, sep);
            model = stem.Substring(sep + 2);
            return true;
        }

        /// <summary>JSON 文字列 → FcTrack。正しくなければ FctrackException。</summary>
        public static FcTrack Read(string text)
        {
            object root;
            try { root = MiniJson.Parse(text); }
            catch (FormatException e) { throw new FctrackException("JSON として読めません: " + e.Message); }
            var d = root as Dictionary<string, object>;
            if (d == null) throw new FctrackException("fctrack の最上位が JSON オブジェクトではありません");

            object fmt;
            d.TryGetValue("format", out fmt);
            if (!(fmt is string) || (string)fmt != Format)
                throw new FctrackException("\"format\" が \"" + Format + "\" ではありません: " + Show(fmt));

            object ver;
            d.TryGetValue("version", out ver);
            if (!(ver is double) || Math.Floor((double)ver) != (double)ver)
                throw new FctrackException("version が整数ではありません: " + Show(ver));

            object rng;
            if (!d.TryGetValue("range", out rng)) rng = new List<object> { 0.0, 0.0 };
            object curvesRaw;
            if (!d.TryGetValue("curves", out curvesRaw)) curvesRaw = new Dictionary<string, object>();
            var curvesDict = curvesRaw as Dictionary<string, object>;
            if (curvesDict == null) throw new FctrackException("curves がオブジェクトではありません");
            var rangeList = rng as List<object>;
            if (rangeList == null || rangeList.Count != 2) throw new FctrackException("range は [開始, 終了] にしてください: " + Show(rng));

            // キーの配列の形（Python の from_dict と同じ段階）
            foreach (KeyValuePair<string, object> kv in curvesDict)
            {
                var keys = kv.Value as List<object>;
                if (keys == null) throw new FctrackException(kv.Key + " はキーの配列 [[秒, 値], ...] にしてください");
                for (int i = 0; i < keys.Count; i++)
                    if (!(keys[i] is List<object>)) throw new FctrackException(kv.Key + " はキーの配列 [[秒, 値], ...] にしてください");
            }

            var t = new FcTrack();
            t.Version = FcposeReader.ToInt((double)ver);
            object v;
            t.Shot = d.TryGetValue("shot", out v) && v is string ? (string)v : "";
            t.Model = d.TryGetValue("model", out v) && v is string ? (string)v : "";

            // validate の順序: version → shot → model → frameRate → range → curves
            if (t.Version != SupportedVersion)
                throw new FctrackException("version " + t.Version + " は未対応です（対応: " + SupportedVersion + "）");
            if (t.Shot.Length == 0) throw new FctrackException("shot が空です");
            if (t.Model.Length == 0) throw new FctrackException("model が空です");
            object fr = d.TryGetValue("frameRate", out v) ? v : (object)30.0;
            if (!(fr is double) || !(((double)fr) > 0.0))
                throw new FctrackException("frameRate は正の数にしてください: " + Show(fr));
            t.FrameRate = (double)fr;
            if (!(rangeList[0] is double) || !(rangeList[1] is double))
                throw new FctrackException("range は [開始, 終了] の数 2 つにしてください: " + Show(rng));
            t.RangeStart = (double)rangeList[0];
            t.RangeEnd = (double)rangeList[1];
            if (t.RangeStart > t.RangeEnd)
                throw new FctrackException("range の開始が終了より後です: [" + t.RangeStart + ", " + t.RangeEnd + "]");

            foreach (KeyValuePair<string, object> kv in curvesDict)
            {
                if (!IsValidCurveName(kv.Key))
                    throw new FctrackException("カーブ名が正しくありません: '" + kv.Key + "'（alpha / useManual / manualYaw / manualPitch / emotion.<レイヤー名>）");
                var keys = (List<object>)kv.Value;
                var list = new List<FcKey>(keys.Count);
                double last = double.NegativeInfinity;
                for (int i = 0; i < keys.Count; i++)
                {
                    var k = (List<object>)keys[i];
                    if (k.Count != 2 || !(k[0] is double) || !(k[1] is double))
                        throw new FctrackException(kv.Key + "[" + i + "] は [秒, 値] の数 2 つにしてください: " + Show(k));
                    double time = (double)k[0];
                    if (time < last)
                        throw new FctrackException(kv.Key + "[" + i + "] の時刻が前のキーより前です（昇順にしてください）");
                    last = time;
                    list.Add(new FcKey(time, (double)k[1]));
                }
                t.Curves[kv.Key] = list;
            }
            return t;
        }

        static string Show(object v)
        {
            if (v == null) return "null";
            if (v is string) return "'" + v + "'";
            if (v is bool) return (bool)v ? "true" : "false";
            if (v is double) return ((double)v).ToString("R", System.Globalization.CultureInfo.InvariantCulture);
            var l = v as List<object>;
            if (l != null)
            {
                var parts = new string[l.Count];
                for (int i = 0; i < l.Count; i++) parts[i] = Show(l[i]);
                return "[" + string.Join(", ", parts) + "]";
            }
            return v.GetType().Name;
        }
    }
}
