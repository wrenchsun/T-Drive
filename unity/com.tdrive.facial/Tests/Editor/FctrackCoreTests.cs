// .fctrack の読み取り（Core。UnityEngine 非依存）のテスト。Unity の EditMode と unity/FacialCoreTests（素の .NET）の両方で動く。
// 期待は Python 版（core/fctrack.py、tests/facial/test_fctrack.py）に合わせる。JSON は test_fctrack.py の _track() と同じ内容を文字列で持つ（Python のテストは変えない）。
using System.Collections.Generic;
using NUnit.Framework;
using TDrive.Facial.Core;

namespace TDrive.Facial.Tests
{
    public class FctrackCoreTests
    {
        // Python の dumps(_track()) と同じ形（カーブごとに 1 行）
        const string Good =
            "{\n  \"format\": \"FacialTrack\",\n  \"version\": 1,\n  \"shot\": \"S010\",\n  \"model\": \"shizuku\",\n  \"frameRate\": 30,\n  \"range\": [0, 240],\n  \"curves\": {\n" +
            "  \"alpha\": [[0.0, 1.0], [2.5, 0.0]],\n  \"emotion.Joy\": [[1.0, 0.0], [1.5, 1.0]],\n  \"useManual\": [[0.0, 0.0], [4.0, 1.0]],\n  \"manualYaw\": [[4.0, -30.0], [6.0, 30.0]]\n  }\n}\n";

        static string Mutate(string from, string to)
        {
            Assert.IsTrue(Good.Contains(from), "テストの前提: " + from);
            return Good.Replace(from, to);
        }

        [Test]
        public void ReadsTheGoodFile()
        {
            FcTrack t = FctrackReader.Read(Good);
            Assert.AreEqual("S010", t.Shot);
            Assert.AreEqual("shizuku", t.Model);
            Assert.AreEqual(30.0, t.FrameRate);
            Assert.AreEqual(0.0, t.RangeStart);
            Assert.AreEqual(240.0, t.RangeEnd);
            Assert.AreEqual(8.0, t.DurationSeconds, 1e-9);
            Assert.AreEqual(4, t.Curves.Count);
            List<FcKey> joy = t.Curves["emotion.Joy"];
            Assert.AreEqual(2, joy.Count);
            Assert.AreEqual(1.5, joy[1].Time);
            Assert.AreEqual(1.0, joy[1].Value);
            Assert.AreEqual(-30.0, t.Curves["manualYaw"][0].Value);
        }

        [Test]
        public void MissingCurvesAndRangeUseDefaults()
        {
            FcTrack t = FctrackReader.Read("{\"format\":\"FacialTrack\",\"version\":1,\"shot\":\"a\",\"model\":\"b\"}");
            Assert.AreEqual(0, t.Curves.Count);
            Assert.AreEqual(30.0, t.FrameRate);
            Assert.AreEqual(0.0, t.DurationSeconds);
        }

        [Test]
        public void UnknownTopLevelKeysAreIgnoredAndBomIsAccepted()
        {
            FcTrack t = FctrackReader.Read("﻿" + Mutate("\"version\": 1,", "\"version\": 1, \"futureKey\": {\"a\": 1},"));
            Assert.AreEqual("S010", t.Shot);
        }

        [Test]
        public void EqualTimesAreAllowedForSteps()
        {
            FcTrack t = FctrackReader.Read(Mutate("[[0.0, 1.0], [2.5, 0.0]]", "[[0.0, 1.0], [1.0, 1.0], [1.0, 0.0]]"));
            Assert.AreEqual(3, t.Curves["alpha"].Count);
        }

        [TestCase("\"format\": \"FacialTrack\"", "\"format\": \"FacialPose\"", "format")]
        [TestCase("\"version\": 1", "\"version\": 2", "version")]
        [TestCase("\"version\": 1", "\"version\": \"1\"", "version")]
        [TestCase("\"version\": 1", "\"version\": 1.5", "version")]
        [TestCase("\"frameRate\": 30", "\"frameRate\": 0", "frameRate")]
        [TestCase("\"frameRate\": 30", "\"frameRate\": \"x\"", "frameRate")]
        [TestCase("\"range\": [0, 240]", "\"range\": [10, 0]", "range")]
        [TestCase("\"range\": [0, 240]", "\"range\": [0]", "range")]
        [TestCase("\"range\": [0, 240]", "\"range\": [0, \"a\"]", "range")]
        [TestCase("\"shot\": \"S010\"", "\"shot\": \"\"", "shot")]
        [TestCase("\"model\": \"shizuku\"", "\"model\": 3", "model")]
        [TestCase("\"alpha\":", "\"bogus\":", "カーブ名")]
        [TestCase("\"emotion.Joy\":", "\"emotion.\":", "カーブ名")]
        [TestCase("[[0.0, 1.0], [2.5, 0.0]]", "[[1.0, 1.0], [0.5, 0.0]]", "昇順")]
        [TestCase("[[0.0, 1.0], [2.5, 0.0]]", "[[0.0, \"x\"]]", "数")]
        [TestCase("[[0.0, 1.0], [2.5, 0.0]]", "[[0.0]]", "数")]
        [TestCase("[[0.0, 1.0], [2.5, 0.0]]", "5", "配列")]
        [TestCase("\"curves\": {", "\"curves\": [], \"x\": {", "curves")]
        public void RejectsWithAMessageThatNamesTheProblem(string from, string to, string expectedText)
        {
            FctrackException e = Assert.Throws<FctrackException>(() => FctrackReader.Read(Mutate(from, to)));
            StringAssert.Contains(expectedText, e.Message);
        }

        [Test]
        public void NotJsonAndNotAnObjectAreRejected()
        {
            Assert.Throws<FctrackException>(() => FctrackReader.Read("{not json"));
            Assert.Throws<FctrackException>(() => FctrackReader.Read("[1,2]"));
            Assert.Throws<FctrackException>(() => FctrackReader.Read(""));
        }

        // F5-12: 誇張の固定カーブ（任意。名前は exaggeration）。テスト内の JSON（共通のテストデータは変えない）
        const string WithExaggeration =
            "{\"format\":\"FacialTrack\",\"version\":1,\"shot\":\"S010\",\"model\":\"shizuku\",\"frameRate\":30,\"range\":[0,60]," +
            "\"curves\":{\"exaggeration\":[[0.0,1.0],[1.0,0.25]]}}";

        [Test]
        public void PerspectiveIsAnOptionalFixedCurve()
        {
            const string json = "{\"format\":\"FacialTrack\",\"version\":1,\"shot\":\"S010\",\"model\":\"shizuku\",\"frameRate\":30,\"range\":[0,60]," +
                "\"curves\":{\"perspective\":[[0.0,0.0],[1.0,1.0]]}}";
            FcTrack t = FctrackReader.Read(json);
            Assert.AreEqual(2, t.Curves[FctrackReader.PerspectiveCurve].Count);
            Assert.AreEqual("perspective", FctrackReader.PerspectiveCurve);
            Assert.Contains("perspective", FctrackReader.FixedCurves);
            Assert.IsFalse(FctrackReader.Read(Good).Curves.ContainsKey("perspective"));
        }

        [Test]
        public void ExaggerationIsAnOptionalFixedCurve()
        {
            FcTrack t = FctrackReader.Read(WithExaggeration);
            Assert.AreEqual(2, t.Curves[FctrackReader.ExaggerationCurve].Count);
            Assert.AreEqual(0.25, t.Curves["exaggeration"][1].Value);
            Assert.AreEqual("exaggeration", FctrackReader.ExaggerationCurve);
            Assert.Contains("exaggeration", FctrackReader.FixedCurves);
            // 古い .fctrack（カーブなし）も読める
            FcTrack old = FctrackReader.Read(Good);
            Assert.IsFalse(old.Curves.ContainsKey("exaggeration"));
        }

        [Test]
        public void ExaggerationNeedsTheKeyArrayForm()
        {
            var e = Assert.Throws<FctrackException>(() => FctrackReader.Read(WithExaggeration.Replace("[[0.0,1.0],[1.0,0.25]]", "0.5")));
            StringAssert.Contains("exaggeration", e.Message);
        }

        [Test]
        public void CurveNamesFollowThePythonRule()
        {
            foreach (string ok in new[] { "alpha", "useManual", "manualYaw", "manualPitch", "exaggeration", "emotion.Joy", "emotion.a.b" })
                Assert.IsTrue(FctrackReader.IsValidCurveName(ok), ok);
            foreach (string ng in new[] { "", "emotion.", "Alpha", "bogus", "emotionJoy" })
                Assert.IsFalse(FctrackReader.IsValidCurveName(ng), ng);
            Assert.IsFalse(FctrackReader.IsValidCurveName(null));
        }

        [Test]
        public void FileStemSplitsAtTheFirstDoubleUnderscore()
        {
            string shot, model;
            Assert.IsTrue(FctrackReader.TryParseFileStem("S010__shizuku", out shot, out model));
            Assert.AreEqual("S010", shot); Assert.AreEqual("shizuku", model);
            Assert.IsTrue(FctrackReader.TryParseFileStem("S010__Hero_2", out shot, out model));
            Assert.AreEqual("Hero_2", model);
            Assert.IsTrue(FctrackReader.TryParseFileStem("A__b__c", out shot, out model));
            Assert.AreEqual("A", shot); Assert.AreEqual("b__c", model);
            Assert.IsFalse(FctrackReader.TryParseFileStem("S010", out shot, out model));
            Assert.IsFalse(FctrackReader.TryParseFileStem("__x", out shot, out model));
            Assert.IsFalse(FctrackReader.TryParseFileStem("S010__", out shot, out model));
            Assert.IsFalse(FctrackReader.TryParseFileStem(null, out shot, out model));
        }
    }
}
