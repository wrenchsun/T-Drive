// 調整値（FacialCorrectionOverrides）の合成と、「Maya へ戻す」JSON の変換（取り込みの逆）のテスト。
using System.Collections.Generic;
using NUnit.Framework;
using TDrive.Facial.Core;
using TDrive.Facial.Editor;
using UnityEngine;

namespace TDrive.Facial.Tests
{
    public class FacialOverridesTests
    {
        readonly List<Object> _owned = new List<Object>();

        [TearDown]
        public void TearDown()
        {
            foreach (Object o in _owned) if (o != null) Object.DestroyImmediate(o);
            _owned.Clear();
        }

        static string Doc(string unit, string tail)
        {
            return "{\"format\":\"FacialCorrection\",\"version\":1,"
                + "\"meta\":{\"unit\":\"" + unit + "\",\"upAxis\":\"Y\",\"handedness\":\"right\",\"source\":\"test\"},"
                + "\"grid\":{\"yawRange\":90,\"pitchRange\":45,\"cols\":3,\"rows\":3,\"baseBone\":\"head\",\"forwardAxis\":\"+Z\",\"centerOffset\":[0,0,0],\"edgeFade\":15},"
                + "\"layers\":[{\"name\":\"Neutral\",\"points\":[{\"row\":1,\"col\":1,\"isKey\":true,\"curves\":{}}]}],\"asset\":\"test\","
                + tail + "}";
        }

        const string Tuning = "\"policy\":{\"expressionDampen\":0.4,\"interpSpeed\":8,\"snapAngle\":30,\"fade\":[50,200],\"globalAlpha\":0.9},"
            + "\"quality\":{\"sharpness\":1.5,\"stepFps\":12,\"angleEpsilon\":0.2,\"maxLod\":1}";

        FacialCorrectionData Import(string json)
        {
            FacialCorrectionData d = FcposeConverter.BuildData(json, "x", null);
            _owned.Add(d);
            return d;
        }

        FacialCorrectionOverrides NewOverrides()
        {
            var o = ScriptableObject.CreateInstance<FacialCorrectionOverrides>();
            _owned.Add(o);
            return o;
        }

        [Test]
        public void ResolveUsesOverridesOnlyWhenFlagged()
        {
            FacialCorrectionData d = Import(Doc("cm", Tuning));
            FacialEffectiveParams none = FacialCorrectionOverrides.Resolve(d, null);
            Assert.AreEqual(0.9f, none.globalAlpha, 1e-6f);
            Assert.AreEqual(0.5f, none.fadeStart, 1e-6f);   // 50 cm → 0.5 m
            Assert.AreEqual(2f, none.fadeEnd, 1e-6f);

            FacialCorrectionOverrides o = NewOverrides();
            o.globalAlpha = 0.1f; o.interpSpeed = 99f; // フラグが無いので効かない
            FacialEffectiveParams same = FacialCorrectionOverrides.Resolve(d, o);
            Assert.AreEqual(0.9f, same.globalAlpha, 1e-6f);
            Assert.AreEqual(8f, same.interpSpeed, 1e-6f);

            o.overrideGlobalAlpha = true; o.overrideFadeEnd = true; o.fadeEnd = 3f;
            FacialEffectiveParams p = FacialCorrectionOverrides.Resolve(d, o);
            Assert.AreEqual(0.1f, p.globalAlpha, 1e-6f);
            Assert.AreEqual(3f, p.fadeEnd, 1e-6f);
            Assert.AreEqual(0.5f, p.fadeStart, 1e-6f);
        }

        [Test]
        public void ToSourceReversesTheImportConversion()
        {
            FacialCorrectionData d = Import(Doc("cm", Tuning));
            FacialSourceTuning t = FacialMayaExport.ToSource(d, FacialCorrectionOverrides.Resolve(d, null));
            Assert.AreEqual("cm", t.unit);
            Assert.AreEqual(50.0, t.fadeStart, 1e-4);
            Assert.AreEqual(200.0, t.fadeEnd, 1e-4);
            Assert.AreEqual(0.4, t.expressionDampen, 1e-6);
            Assert.AreEqual(8.0, t.interpSpeed, 1e-6);
            Assert.AreEqual(30.0, t.snapAngle, 1e-6);
            Assert.AreEqual(0.9, t.globalAlpha, 1e-6);
            Assert.AreEqual(1.5, t.sharpness, 1e-6);
            Assert.AreEqual(12.0, t.stepFps, 1e-6);
            Assert.AreEqual(0.2, t.angleEpsilon, 1e-6);
            Assert.AreEqual(1, t.maxLod);
        }

        [TestCase("cm")]
        [TestCase("m")]
        [TestCase("in")]
        [TestCase("mm")]
        public void FadeRoundTripsInEverySourceUnit(string unit)
        {
            FacialCorrectionData d = Import(Doc(unit, "\"policy\":{\"fade\":[10,20]}"));
            FacialSourceTuning t = FacialMayaExport.ToSource(d, FacialCorrectionOverrides.Resolve(d, null));
            Assert.AreEqual(unit, t.unit);
            Assert.AreEqual(10.0, t.fadeStart, 1e-3);
            Assert.AreEqual(20.0, t.fadeEnd, 1e-3);
        }

        [Test]
        public void ExportedJsonHasOnlyPolicyAndQualityWithFcposeKeys()
        {
            FacialCorrectionData d = Import(Doc("cm", Tuning));
            var root = (Dictionary<string, object>)MiniJson.Parse(FacialMayaExport.BuildJson(d, null));
            CollectionAssert.AreEquivalent(new[] { "policy", "quality" }, root.Keys);
            var policy = (Dictionary<string, object>)root["policy"];
            CollectionAssert.AreEquivalent(new[] { "expressionDampen", "interpSpeed", "snapAngle", "fade", "globalAlpha" }, policy.Keys);
            var quality = (Dictionary<string, object>)root["quality"];
            CollectionAssert.AreEquivalent(new[] { "sharpness", "stepFps", "angleEpsilon", "maxLod" }, quality.Keys);
            var fade = (List<object>)policy["fade"];
            Assert.AreEqual(50.0, (double)fade[0], 1e-4);
            Assert.AreEqual(200.0, (double)fade[1], 1e-4);
        }

        [Test]
        public void OverridesRoundTripThroughExportAndReimport()
        {
            FacialCorrectionData d = Import(Doc("cm", Tuning));
            FacialCorrectionOverrides o = NewOverrides();
            o.overrideFadeStart = true; o.fadeStart = 0.25f;          // 25 cm
            o.overrideGlobalAlpha = true; o.globalAlpha = 0.5f;
            o.overrideSharpness = true; o.sharpness = 2f;
            o.overrideSnapAngle = true; o.snapAngle = 60f;
            FacialEffectiveParams eff = FacialCorrectionOverrides.Resolve(d, o);

            string exported = FacialMayaExport.BuildJson(d, o);
            // 書き出した policy / quality を Maya の出力に差し込んだものと見なして取り込み直す
            string body = exported.Trim();
            body = body.Substring(1, body.Length - 2);
            FacialCorrectionData d2 = Import(Doc("cm", body));
            FacialEffectiveParams re = FacialCorrectionOverrides.Resolve(d2, null);

            Assert.AreEqual(eff.fadeStart, re.fadeStart, 1e-5f);
            Assert.AreEqual(eff.fadeEnd, re.fadeEnd, 1e-5f);
            Assert.AreEqual(eff.globalAlpha, re.globalAlpha, 1e-6f);
            Assert.AreEqual(eff.sharpness, re.sharpness, 1e-6f);
            Assert.AreEqual(eff.snapAngle, re.snapAngle, 1e-6f);
            Assert.AreEqual(eff.interpSpeed, re.interpSpeed, 1e-6f);
            Assert.AreEqual(eff.expressionDampen, re.expressionDampen, 1e-6f);
            Assert.AreEqual(eff.stepFps, re.stepFps, 1e-6f);
            Assert.AreEqual(d.quality.angleEpsilon, d2.quality.angleEpsilon, 1e-6f);
            Assert.AreEqual(d.quality.maxLod, d2.quality.maxLod);
            Assert.AreEqual(0.25f, d2.policy.fadeStart, 1e-5f);
        }

        [Test]
        public void MissingSourceUnitFallsBackToCentimeters()
        {
            var d = ScriptableObject.CreateInstance<FacialCorrectionData>();
            _owned.Add(d);
            d.policy.fadeStart = 0.3f; d.policy.fadeEnd = 1f;
            FacialSourceTuning t = FacialMayaExport.ToSource(d, FacialCorrectionOverrides.Resolve(d, null));
            Assert.AreEqual("cm", t.unit);
            Assert.AreEqual(30.0, t.fadeStart, 1e-3);
            Assert.AreEqual(100.0, t.fadeEnd, 1e-3);
        }
    }
}
