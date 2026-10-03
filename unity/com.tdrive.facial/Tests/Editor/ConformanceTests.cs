// 共通のテストデータ（tests/facial/conformance/*.json）を読んで TDrive.Facial.Core と比べる。
// Unity の EditMode テストと、unity/FacialCoreTests（素の .NET）の両方が、このファイルをそのまま使う。
// 形式は tests/facial/conformance/README.md。Python 版は tests/facial/test_conformance.py。
using System;
using System.Collections.Generic;
using System.IO;
using NUnit.Framework;
using TDrive.Facial.Core;

namespace TDrive.Facial.Tests
{
    using Obj = Dictionary<string, object>;

    /// <summary>テストデータの場所の探索と読み込み（Unity の型は使わない）。</summary>
    public static class ConformanceData
    {
        public const string EnvVar = "TDRIVE_CONFORMANCE_DIR";
        static readonly string[] KnownKinds = { "evaluate", "view_angles", "scalar", "smooth", "convert" };

        public sealed class Case
        {
            public string File;
            public string Kind;
            public Obj Data;
            public string Name { get { return (string)Data["name"]; } }
            public override string ToString() { return File + ":" + Name; }
        }

        static List<Case> _cases;
        static string _dir;
        static string _error;
        static readonly Dictionary<string, int> _skipped = new Dictionary<string, int>();

        public static string Directory { get { Load(); return _dir; } }
        public static string Error { get { Load(); return _error; } }
        public static Dictionary<string, int> SkippedKinds { get { Load(); return _skipped; } }

        static string FindDir()
        {
            string env = Environment.GetEnvironmentVariable(EnvVar);
            if (!string.IsNullOrEmpty(env))
            {
                if (System.IO.Directory.Exists(env)) return env;
                return null;
            }
            var starts = new List<string>();
            try { starts.Add(TestContext.CurrentContext.TestDirectory); } catch (Exception) { }
            starts.Add(AppContext.BaseDirectory);
            starts.Add(System.IO.Directory.GetCurrentDirectory());
            try { starts.Add(Path.GetDirectoryName(typeof(ConformanceData).Assembly.Location)); } catch (Exception) { }
            foreach (string start in starts)
            {
                if (string.IsNullOrEmpty(start)) continue;
                var d = new DirectoryInfo(start);
                while (d != null)
                {
                    string cand = Path.Combine(d.FullName, "tests", "facial", "conformance");
                    if (System.IO.Directory.Exists(cand)) return cand;
                    d = d.Parent;
                }
            }
            return null;
        }

        static void Load()
        {
            if (_cases != null) return;
            _cases = new List<Case>();
            _dir = FindDir();
            if (_dir == null)
            {
                _error = "tests/facial/conformance が見つからない（環境変数 " + EnvVar + " で指定できる）";
                return;
            }
            string[] files = System.IO.Directory.GetFiles(_dir, "*.json");
            Array.Sort(files, StringComparer.Ordinal);
            foreach (string f in files)
            {
                var root = (Obj)MiniJson.Parse(File.ReadAllText(f));
                string kind = (string)root["kind"];
                var cases = (List<object>)root["cases"];
                if (Array.IndexOf(KnownKinds, kind) < 0)
                {
                    int n;
                    _skipped.TryGetValue(kind, out n);
                    _skipped[kind] = n + cases.Count;
                    continue;
                }
                foreach (object c in cases)
                    _cases.Add(new Case { File = Path.GetFileName(f), Kind = kind, Data = (Obj)c });
            }
        }

        public static IEnumerable<TestCaseData> Of(string kind)
        {
            Load();
            if (_error != null)
            {
                yield return new TestCaseData(null).SetName("conformance_dir_not_found");
                yield break;
            }
            foreach (Case c in _cases)
                if (c.Kind == kind) yield return new TestCaseData(c).SetName(c.File + ":" + c.Name);
        }

        public static int Count(string kind)
        {
            Load();
            int n = 0;
            foreach (Case c in _cases) if (c.Kind == kind) n++;
            return n;
        }
    }

    public class ConformanceTests
    {
        const double WeightTol = 1e-4;
        const double ScalarTolDefault = 1e-6; // Python 版（test_scalar）と同じ

        // --- JSON の取り出し補助 ---
        static double Num(object o) { return Convert.ToDouble(o, System.Globalization.CultureInfo.InvariantCulture); }
        static Obj O(object o) { return (Obj)o; }
        static List<object> A(object o) { return (List<object>)o; }
        static double NumOr(Obj d, string key, double def) { object v; return d.TryGetValue(key, out v) && v != null ? Num(v) : def; }
        static bool BoolOr(Obj d, string key, bool def) { object v; return d.TryGetValue(key, out v) && v != null ? (bool)v : def; }
        static Vec3 V3(object o) { List<object> a = A(o); return new Vec3(Num(a[0]), Num(a[1]), Num(a[2])); }
        static Quat Q4(object o) { List<object> a = A(o); return new Quat(Num(a[0]), Num(a[1]), Num(a[2]), Num(a[3])); }
        static SpaceSpec Space(object o)
        {
            Obj d = O(o);
            return new SpaceSpec((string)d["unit"], (string)d["upAxis"], (string)d["handedness"]);
        }

        static void RequireDir(ConformanceData.Case c)
        {
            if (c == null) Assert.Fail(ConformanceData.Error);
        }

        static Dictionary<string, double> ByName(List<MorphWeight> list)
        {
            var d = new Dictionary<string, double>();
            for (int i = 0; i < list.Count; i++)
            {
                Assert.That(d.ContainsKey(list[i].MorphName), Is.False, "出力に同名が重複: " + list[i].MorphName);
                d[list[i].MorphName] = list[i].Weight;
            }
            return d;
        }

        static void AssertWeights(Dictionary<string, double> got, Dictionary<string, double> expect, double tol, string where)
        {
            var gn = new List<string>(got.Keys); gn.Sort(StringComparer.Ordinal);
            var en = new List<string>(expect.Keys); en.Sort(StringComparer.Ordinal);
            Assert.That(gn, Is.EqualTo(en), where + " 名前の集合");
            foreach (KeyValuePair<string, double> kv in expect)
                Assert.That(got[kv.Key], Is.EqualTo(kv.Value).Within(tol), where + " " + kv.Key);
        }

        // --- evaluate ---

        [TestCaseSource(typeof(ConformanceData), "Of", new object[] { "evaluate" })]
        public void Evaluate(ConformanceData.Case c)
        {
            RequireDir(c);
            Obj d = c.Data;
            Obj g = O(d["grid"]);
            var grid = new GridShape(Num(g["yawRange"]), Num(g["pitchRange"]), (int)Num(g["cols"]), (int)Num(g["rows"]), Num(g["edgeFade"]));
            var layers = new List<LayerEvalInput>();
            foreach (object lo in A(d["layers"]))
            {
                Obj l = O(lo);
                var morphs = new List<string>();
                foreach (object m in A(l["morphs"])) morphs.Add((string)m);
                layers.Add(new LayerEvalInput(morphs, NumOr(l, "emotionWeight", 0.0), BoolOr(l, "enabled", true)));
            }
            var output = new List<MorphWeight>();
            FacialCore.EvaluateCorrection(grid, layers, Num(d["yaw"]), Num(d["pitch"]), output);

            double tol = NumOr(d, "tolerance", WeightTol);
            var expect = new Dictionary<string, double>();
            foreach (KeyValuePair<string, object> kv in O(O(d["expect"])["weights"])) expect[kv.Key] = Num(kv.Value);
            AssertWeights(ByName(output), expect, tol, c.ToString());
        }

        // --- view_angles ---

        [TestCaseSource(typeof(ConformanceData), "Of", new object[] { "view_angles" })]
        public void ViewAngles(ConformanceData.Case c)
        {
            RequireDir(c);
            Obj d = c.Data;
            string mode = (string)d["mode"];
            double yaw, pitch;
            if (mode == "direct")
            {
                FacialCore.ComputeViewAngles(V3(d["headPos"]), Num(d["headForwardYawDeg"]), V3(d["viewerPos"]), out yaw, out pitch);
            }
            else if (mode == "roundtrip")
            {
                double fy = Num(d["headForwardYawDeg"]);
                Vec3 dir = FacialCore.ComputeViewDirection(fy, Num(d["yawDeg"]), Num(d["pitchDeg"]));
                Vec3 head = V3(d["headPos"]);
                double dist = Num(d["distance"]);
                var viewer = new Vec3(head.X + dir.X * dist, head.Y + dir.Y * dist, head.Z + dir.Z * dist);
                FacialCore.ComputeViewAngles(head, fy, viewer, out yaw, out pitch);
            }
            else if (mode == "bone")
            {
                Quat rot = d["headRotation"] == null ? Quat.Identity : Q4(d["headRotation"]);
                Vec3 center = d.ContainsKey("centerOffset") && d["centerOffset"] != null ? V3(d["centerOffset"]) : new Vec3(0, 0, 0);
                FacialSpace.ComputeViewAnglesInSpace(Space(d["space"]), V3(d["headPos"]), rot, (string)d["forwardAxis"],
                    V3(d["viewerPos"]), center, out yaw, out pitch);
            }
            else
            {
                Assert.Fail("未知の mode: " + mode);
                return;
            }
            double tol = Num(d["toleranceDeg"]);
            Obj e = O(d["expect"]);
            double ey = Num(e["yawDeg"]);
            bool yawOk = Math.Abs(FacialCore.NormalizeAxis(yaw - ey)) <= tol
                // 真後ろ（±180）は符号違いも同じ向き
                || (Math.Abs(Math.Abs(ey) - 180) < 1e-6 && Math.Abs(Math.Abs(yaw) - 180) <= tol);
            Assert.That(yawOk, Is.True, "yaw " + yaw + " (期待 " + ey + ")");
            Assert.That(pitch, Is.EqualTo(Num(e["pitchDeg"])).Within(tol), "pitch");
        }

        // --- scalar ---

        [TestCaseSource(typeof(ConformanceData), "Of", new object[] { "scalar" })]
        public void Scalar(ConformanceData.Case c)
        {
            RequireDir(c);
            Obj d = c.Data;
            List<object> a = A(d["args"]);
            double got;
            switch ((string)d["fn"])
            {
                case "expressionScale": got = FacialCore.ExpressionScale(Num(a[0]), Num(a[1])); break;
                case "distanceFade": got = FacialCore.DistanceFade(Num(a[0]), Num(a[1]), Num(a[2])); break;
                case "finterpTo": got = FacialCore.FInterpTo(Num(a[0]), Num(a[1]), Num(a[2]), Num(a[3])); break;
                case "normalizeAxis": got = FacialCore.NormalizeAxis(Num(a[0])); break;
                default: Assert.Fail("未知の fn: " + d["fn"]); return;
            }
            Assert.That(got, Is.EqualTo(Num(d["expect"])).Within(NumOr(d, "tolerance", ScalarTolDefault)));
        }

        // --- smooth ---

        static List<MorphWeight> Weights(object list)
        {
            var l = new List<MorphWeight>();
            foreach (object o in A(list)) { Obj w = O(o); l.Add(new MorphWeight((string)w["name"], Num(w["weight"]))); }
            return l;
        }

        [TestCaseSource(typeof(ConformanceData), "Of", new object[] { "smooth" })]
        public void Smooth(ConformanceData.Case c)
        {
            RequireDir(c);
            Obj d = c.Data;
            double speed = Num(d["speed"]);
            var prev = Weights(d["initial"]);
            var next = new List<MorphWeight>();
            int i = 0;
            foreach (object so in A(d["steps"]))
            {
                Obj step = O(so);
                FacialCore.SmoothWeights(prev, Weights(step["target"]), Num(step["dt"]), speed, (bool)step["snap"], next);
                var expect = new Dictionary<string, double>();
                foreach (MorphWeight w in Weights(step["expect"])) expect[w.MorphName] = w.Weight;
                AssertWeights(ByName(next), expect, WeightTol, c + " step " + i);
                var t = prev; prev = next; next = t; // 出力が次の前回値（バッファは入れ替えて再利用）
                i++;
            }
        }

        // --- convert ---

        static void AssertTree(object got, object expect, double tol, string path)
        {
            if (expect is Obj)
            {
                Obj eo = (Obj)expect, go = (Obj)got;
                Assert.That(new List<string>(go.Keys).ToArray(), Is.EquivalentTo(new List<string>(eo.Keys).ToArray()), path);
                foreach (KeyValuePair<string, object> kv in eo) AssertTree(go[kv.Key], kv.Value, tol, path + "." + kv.Key);
            }
            else if (expect is List<object>)
            {
                List<object> el = (List<object>)expect, gl = (List<object>)got;
                Assert.That(gl.Count, Is.EqualTo(el.Count), path);
                for (int i = 0; i < el.Count; i++) AssertTree(gl[i], el[i], tol, path + "[" + i + "]");
            }
            else if (expect is string)
            {
                Assert.That(got, Is.EqualTo(expect), path);
            }
            else
            {
                Assert.That((double)got, Is.EqualTo(Num(expect)).Within(tol), path);
            }
        }

        static List<object> L(Vec3 v) { return new List<object> { v.X, v.Y, v.Z }; }
        static List<object> L(Quat q) { return new List<object> { q.X, q.Y, q.Z, q.W }; }

        [TestCaseSource(typeof(ConformanceData), "Of", new object[] { "convert" })]
        public void Convert_(ConformanceData.Case c)
        {
            RequireDir(c);
            Obj d = c.Data;
            SpaceConverter cv = FacialSpace.Converter(Space(d["from"]), Space(d["to"]));
            string op = (string)d["op"];
            object input = d["input"];
            object got;
            switch (op)
            {
                case "position": got = L(cv.Position(V3(input))); break;
                case "direction": got = L(cv.Direction(V3(input))); break;
                case "quaternion": got = L(cv.Quaternion(Q4(input))); break;
                case "boneOffset":
                    {
                        Obj b = O(input);
                        BoneOffset r = cv.ConvertBoneOffset(new BoneOffset(V3(b["t"]), Q4(b["r"]), V3(b["s"])));
                        got = new Obj { { "t", L(r.T) }, { "r", L(r.R) }, { "s", L(r.S) } };
                        break;
                    }
                case "forwardAxis": got = cv.ForwardAxis((string)input); break;
                case "mirrorAxis": got = cv.MirrorAxis((string)input); break;
                default: Assert.Fail("未知の op: " + op); return;
            }
            // 期待値は小数 12 桁に丸めて保存してあるので、許容誤差はそれより少し緩める（Python 版と同じ）
            AssertTree(got, d["expect"], Math.Max(NumOr(d, "tolerance", 1e-9), 1e-9), c.ToString());
        }

        // --- 集計 ---

        [Test]
        public void ConformanceDirectoryIsFoundAndNonEmpty()
        {
            Assert.That(ConformanceData.Error, Is.Null);
            TestContext.Out.WriteLine("conformance dir: " + ConformanceData.Directory);
            foreach (string k in new[] { "evaluate", "view_angles", "scalar", "smooth", "convert" })
            {
                TestContext.Out.WriteLine("kind " + k + ": " + ConformanceData.Count(k) + " cases");
                Assert.That(ConformanceData.Count(k), Is.GreaterThan(0), k);
            }
        }

        [Test]
        public void UnknownKindsAreIgnoredExplicitly()
        {
            int total = 0;
            var parts = new List<string>();
            foreach (KeyValuePair<string, int> kv in ConformanceData.SkippedKinds) { total += kv.Value; parts.Add(kv.Key + "=" + kv.Value); }
            string msg = "ignored kinds: " + (parts.Count == 0 ? "(none)" : string.Join(", ", parts)) + " total=" + total;
            TestContext.Out.WriteLine(msg);
            if (total > 0) Assert.Ignore(msg);
        }
    }
}
