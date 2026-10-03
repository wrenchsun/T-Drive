// Core（UnityEngine 非依存）の堅さのテスト（docs/19 U-9 とテスト基盤の穴）。Unity の EditMode と unity/FacialCoreTests（素の .NET）の両方で動く。
using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using NUnit.Framework;
using TDrive.Facial.Core;

namespace TDrive.Facial.Tests
{
    public class FacialCoreRobustnessTests
    {
        static string Nested(int depth, string open, string close, string inner)
        {
            var sb = new StringBuilder();
            for (int i = 0; i < depth; i++) sb.Append(open);
            sb.Append(inner);
            for (int i = 0; i < depth; i++) sb.Append(close);
            return sb.ToString();
        }

        [Test]
        public void MiniJsonAcceptsNestingUpToTheLimit()
        {
            Assert.DoesNotThrow(() => Core.MiniJson.Parse(Nested(Core.MiniJson.MaxDepth, "[", "]", "1")));
            Assert.DoesNotThrow(() => Core.MiniJson.Parse(Nested(Core.MiniJson.MaxDepth, "{\"a\":", "}", "1")));
        }

        [Test]
        public void MiniJsonRejectsDeeperNestingWithFormatExceptionInsteadOfOverflowingTheStack()
        {
            Assert.Throws<FormatException>(() => Core.MiniJson.Parse(Nested(Core.MiniJson.MaxDepth + 1, "[", "]", "1")));
            Assert.Throws<FormatException>(() => Core.MiniJson.Parse(Nested(Core.MiniJson.MaxDepth + 1, "{\"a\":", "}", "1")));
            Assert.Throws<FormatException>(() => Core.MiniJson.Parse(new string('[', 200000))); // 閉じていない巨大な入れ子でも落ちない
        }

        [Test]
        public void MiniJsonSiblingsDoNotAccumulateDepth()
        {
            // 深さは「今の入れ子」であって、読んだ配列の総数ではない
            var sb = new StringBuilder("[");
            for (int i = 0; i < 1000; i++) sb.Append(i == 0 ? "[1]" : ",[1]");
            sb.Append("]");
            Assert.DoesNotThrow(() => Core.MiniJson.Parse(sb.ToString()));
        }

        [Test]
        public void MiniJsonNullInputIsAFormatException()
        {
            Assert.Throws<FormatException>(() => Core.MiniJson.Parse(null));
        }

        [TestCase("NaN")]
        [TestCase("Infinity")]
        [TestCase("-Infinity")]
        [TestCase("[1, NaN]")]
        [TestCase("1e999")]
        [TestCase("-1e999")]
        [TestCase("{\"a\": 1e400}")]
        public void MiniJsonRejectsNonFiniteNumbers(string text)
        {
            Assert.Throws<FormatException>(() => Core.MiniJson.Parse(text), text);
        }

        [Test]
        public void MiniJsonStillReadsOrdinaryNumbers()
        {
            Assert.AreEqual(1.5e300, (double)Core.MiniJson.Parse("1.5e300"), 1e290);
            Assert.AreEqual(-0.25, (double)Core.MiniJson.Parse("-0.25"), 1e-12);
        }

        // ---------------------------------------------------------------- 整数の範囲

        [Test]
        public void FcposeIntegersOutOfRangeAreClampedNotUndefined()
        {
            const string json = "{\"format\":\"FacialCorrection\",\"version\":3e9,\"layers\":[{\"name\":\"Neutral\",\"points\":[{\"row\":3e9,\"col\":-3e9}]}]}";
            var warnings = new List<string>();
            FcDocument d = FcposeReader.ReadDocument(json, w => warnings.Add(w));
            Assert.AreEqual(int.MaxValue, d.Version);
            Assert.AreEqual(int.MaxValue, d.Layers[0].Points[0].Row);
            Assert.AreEqual(int.MinValue, d.Layers[0].Points[0].Col);
            Assert.IsTrue(warnings.Exists(w => w.Contains("version")), "新しすぎる version の警告");
        }

        [Test]
        public void FctrackHugeVersionIsRejectedAsUnsupportedNotCrashed()
        {
            const string json = "{\"format\":\"FacialTrack\",\"version\":1e300,\"shot\":\"s\",\"model\":\"m\",\"frameRate\":30,\"range\":[0,10],\"curves\":{}}";
            Assert.Throws<FctrackException>(() => FctrackReader.Read(json));
        }

        // ---------------------------------------------------------------- SpaceConverter の null

        [Test]
        public void SpaceConverterEntryPointsRejectNullWithArgumentNullException()
        {
            Assert.Throws<ArgumentNullException>(() => FacialSpace.Converter(null, FacialSpace.Unity));
            Assert.Throws<ArgumentNullException>(() => FacialSpace.Converter(FacialSpace.Unity, null));
            Assert.Throws<ArgumentNullException>(() => FacialSpace.ToCanonicalMatrix(null));
            SpaceConverter cv = FacialSpace.Converter(FacialSpace.Maya, FacialSpace.Unity);
            Assert.Throws<ArgumentException>(() => cv.MirrorAxis(null)); // ArgumentNullException も ArgumentException
            Assert.Throws<ArgumentException>(() => cv.MirrorAxis(""));
            Assert.Throws<ArgumentException>(() => cv.ForwardAxis(null));
            Assert.Throws<ArgumentNullException>(() => FacialSpace.ComputeViewAnglesInSpace((SpaceConverter)null, new Vec3(0, 0, 0), Quat.Identity, "+Z",
                new Vec3(0, 0, 1), new Vec3(0, 0, 0), out double _, out double _));
            Assert.Throws<ArgumentException>(() => new SpaceSpec(null, "Y", "left"));
        }

        // ---------------------------------------------------------------- 共通のテストデータの読み込み（テスト基盤）

        string _tmp;

        [TearDown]
        public void TearDown()
        {
            if (_tmp != null && Directory.Exists(_tmp)) { try { Directory.Delete(_tmp, true); } catch (IOException) { } }
            _tmp = null;
        }

        string TempDir(params KeyValuePair<string, string>[] files)
        {
            _tmp = Path.Combine(Path.GetTempPath(), "tdrive_conf_" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(_tmp);
            foreach (var kv in files) File.WriteAllText(Path.Combine(_tmp, kv.Key), kv.Value);
            return _tmp;
        }

        static KeyValuePair<string, string> F(string name, string text) { return new KeyValuePair<string, string>(name, text); }

        [Test]
        public void ConformanceLoaderFailsWholeRunWhenAJsonFileCannotBeParsed()
        {
            string dir = TempDir(F("a.json", "{\"kind\":\"step\",\"cases\":[{\"name\":\"x\"}]}"), F("b.json", "{ this is broken"));
            var cases = new List<ConformanceData.Case>();
            var skipped = new Dictionary<string, int>();
            string error = ConformanceData.LoadFrom(dir, cases, skipped);
            Assert.IsNotNull(error);
            StringAssert.Contains("b.json", error);
            Assert.AreEqual(0, cases.Count, "一部だけ読んだ状態を残さない");
        }

        [Test]
        public void ConformanceLoaderFailsOnAKindThatIsNeitherImplementedNorPythonOnly()
        {
            string dir = TempDir(F("new.json", "{\"kind\":\"brand_new\",\"cases\":[{\"name\":\"x\"}]}"));
            string error = ConformanceData.LoadFrom(dir, new List<ConformanceData.Case>(), new Dictionary<string, int>());
            Assert.IsNotNull(error);
            StringAssert.Contains("brand_new", error);
        }

        [Test]
        public void ConformanceLoaderAllowsTheExplicitPythonOnlyKinds()
        {
            string dir = TempDir(F("a.json", "{\"kind\":\"autofill\",\"cases\":[{\"name\":\"x\"},{\"name\":\"y\"}]}"),
                F("p.json", "{\"kind\":\"presenter\",\"cases\":[{\"name\":\"z\"}]}"),
                F("s.json", "{\"kind\":\"step\",\"cases\":[{\"name\":\"w\"}]}"));
            var cases = new List<ConformanceData.Case>();
            var skipped = new Dictionary<string, int>();
            Assert.IsNull(ConformanceData.LoadFrom(dir, cases, skipped));
            Assert.AreEqual(1, cases.Count);
            Assert.AreEqual(2, skipped["autofill"]);
            Assert.AreEqual(1, skipped["presenter"]);
        }
    }
}
