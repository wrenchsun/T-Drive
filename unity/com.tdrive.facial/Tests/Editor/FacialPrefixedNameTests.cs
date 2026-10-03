// FBX 取り込み後の名前（"<blendShape ノード名>.<ターゲット名>" = bs.FC_...）への対応のテスト。
// 合成メッシュ（FacialTestRig の fcPrefix = "bs."）で、Runner・検証が接頭辞つきの名前を引けることを確かめる。
using System.Collections.Generic;
using System.Linq;
using NUnit.Framework;
using TDrive.Facial.Editor;
using UnityEngine;

namespace TDrive.Facial.Tests
{
    public class FacialPrefixedNameTests
    {
        const float Dt = 0.02f;
        FacialTestRig _rig;

        [TearDown]
        public void TearDown() { if (_rig != null) _rig.Dispose(); }

        [Test]
        public void MeshHasPrefixedNames()
        {
            _rig = new FacialTestRig(true, "bs.");
            Assert.GreaterOrEqual(_rig.mesh.GetBlendShapeIndex("bs." + FacialTestRig.N("Neutral", 1, 1)), 0);
            Assert.AreEqual(-1, _rig.mesh.GetBlendShapeIndex(FacialTestRig.N("Neutral", 1, 1)), "素の名前では引けない（Unity 側の前提）");
        }

        [Test]
        public void RunnerAutoFindsTargetAndBindsPrefixedShapes()
        {
            _rig = new FacialTestRig(false, "bs.");
            Assert.AreEqual(1, _rig.runner.ResolvedTargets.Count, "FC_<asset>_ を持つメッシュを接頭辞つきでも自動検出する");
            Assert.AreEqual(9, _rig.runner.BoundShapeCount, "Neutral 8（1 つはメッシュに無い）+ Joy 1");
            CollectionAssert.AreEqual(new[] { FacialTestRig.Missing }, _rig.runner.GetMissingShapeNames().ToArray());
        }

        [Test]
        public void ExactPointWritesWeightToPrefixedShape()
        {
            _rig = new FacialTestRig(true, "bs.");
            _rig.runner.EvaluateNow(0f, 0f, Dt);
            Assert.AreEqual(100f, _rig.W(FacialTestRig.N("Neutral", 1, 1)), 0.5f);
            Assert.AreEqual(100f, _rig.FcSum(), 0.5f, "ほかの FC_ は 0");
            Assert.AreEqual(0f, _rig.W("bs.jaw"), 1e-4f);
            _rig.runner.ResetWeights();
            Assert.AreEqual(0f, _rig.FcSum(), 1e-4f);
        }

        [Test]
        public void MidpointWritesHalfAndHalf()
        {
            _rig = new FacialTestRig(true, "bs.");
            _rig.runner.EvaluateNow(45f, 0f, Dt); // 列 1 と 2 の中間
            Assert.AreEqual(50f, _rig.W(FacialTestRig.N("Neutral", 1, 1)), 0.5f);
            Assert.AreEqual(50f, _rig.W(FacialTestRig.N("Neutral", 1, 2)), 0.5f);
        }

        [Test]
        public void EmotionLayerWorksWithPrefixedNames()
        {
            _rig = new FacialTestRig(true, "bs.");
            _rig.runner.SetEmotionWeight(1, 1f);
            _rig.runner.EvaluateNow(0f, 0f, Dt);
            Assert.AreEqual(100f, _rig.W(FacialTestRig.N("Joy", 1, 1)), 0.5f);
        }

        [Test]
        public void IntensityCurveWithNodePrefixedNameMatchesDirectly()
        {
            _rig = new FacialTestRig(true, "bs.");
            // データの強さカーブ "bs.jaw" は、Unity のシェイプ名 "bs.jaw" とそのまま一致する
            int jaw = _rig.mesh.GetBlendShapeIndex("bs.jaw");
            Assert.GreaterOrEqual(jaw, 0);
            _rig.smr.SetBlendShapeWeight(jaw, 100f);
            _rig.runner.EvaluateNow(0f, 0f, Dt);
            Assert.AreEqual(0.5f, _rig.runner.LastScale, 1e-3f, "表情の強さ 1 × expressionDampen 0.5 → 倍率 0.5");
            Assert.AreEqual(50f, _rig.W(FacialTestRig.N("Neutral", 1, 1)), 0.5f);
        }

        [Test]
        public void IntensityCurveBareNameMatchesPrefixedShape()
        {
            _rig = new FacialTestRig(true, "bs.");
            _rig.data.intensityCurves = new[] { "jaw" }; // 接頭辞なしで書かれていても末尾一致で引ける
            _rig.runner.RebuildCaches();
            _rig.smr.SetBlendShapeWeight(_rig.mesh.GetBlendShapeIndex("bs.jaw"), 100f);
            _rig.runner.EvaluateNow(0f, 0f, Dt);
            Assert.AreEqual(0.5f, _rig.runner.LastScale, 1e-3f);
        }

        [Test]
        public void LimitsApplyToPrefixedShape()
        {
            _rig = new FacialTestRig(true, "bs.");
            _rig.data.limits = new[] { new FacialLimitEntry { name = FacialTestRig.N("Neutral", 1, 1), min = 0f, max = 0.4f } };
            _rig.runner.RebuildCaches();
            _rig.runner.EvaluateNow(0f, 0f, Dt);
            Assert.AreEqual(40f, _rig.W(FacialTestRig.N("Neutral", 1, 1)), 0.5f);
        }

        [Test]
        public void ExactNameWinsOverPrefixedName()
        {
            _rig = new FacialTestRig(true, "bs.");
            string n = FacialTestRig.N("Neutral", 1, 1);
            _rig.mesh.AddBlendShapeFrame(n, 100f, new Vector3[3], new Vector3[3], new Vector3[3]); // 素の名前も追加
            _rig.runner.RebuildCaches();
            _rig.runner.EvaluateNow(0f, 0f, Dt);
            Assert.AreEqual(100f, _rig.smr.GetBlendShapeWeight(_rig.mesh.GetBlendShapeIndex(n)), 0.5f, "完全一致を優先");
            Assert.AreEqual(0f, _rig.smr.GetBlendShapeWeight(_rig.mesh.GetBlendShapeIndex("bs." + n)), 1e-4f);
        }

        [Test]
        public void ValidationIsCleanWithPrefixedNames()
        {
            _rig = new FacialTestRig(true, "bs.");
            List<FacialIssue> issues = FacialValidation.Run(_rig.runner);
            Assert.AreEqual(0, issues.Count, string.Join("\n", issues.Select(i => i.ToString())));
        }

        [Test]
        public void ValidationReportsMissingAndOrphanByBareName()
        {
            _rig = new FacialTestRig(false, "bs.");
            _rig.AddShape("FC_test_Neutral_R9_C9"); // 接頭辞つきで入る = 孤立
            _rig.AddShape("FC_test_Persp_K0");      // 孤立に数えない
            _rig.runner.RebuildCaches();
            List<FacialIssue> all = FacialValidation.Run(_rig.runner);
            List<FacialIssue> missing = all.Where(i => i.Kind == FacialIssueKind.MissingShape).ToList();
            Assert.AreEqual(1, missing.Count);
            Assert.AreEqual(FacialTestRig.Missing, missing[0].ShapeName);
            List<FacialIssue> orphan = all.Where(i => i.Kind == FacialIssueKind.OrphanShape).ToList();
            Assert.AreEqual(1, orphan.Count);
            Assert.AreEqual("FC_test_Neutral_R9_C9", orphan[0].ShapeName, "孤立は接頭辞を除いた名前で報告する");
        }

        [Test]
        public void ValidationIntensityAndLimitLookupsUsePrefixedNames()
        {
            _rig = new FacialTestRig(true, "bs.");
            _rig.data.intensityCurves = new[] { "bs.jaw", "bs.nothing" };
            _rig.data.limits = new[] { new FacialLimitEntry { name = "bs.other", min = 0, max = 1 } };
            List<FacialIssue> all = FacialValidation.Run(_rig.runner);
            Assert.AreEqual(1, all.Count(i => i.Kind == FacialIssueKind.IntensityShapeMissing));
            Assert.AreEqual("bs.nothing", all.First(i => i.Kind == FacialIssueKind.IntensityShapeMissing).ShapeName);
            Assert.AreEqual(1, all.Count(i => i.Kind == FacialIssueKind.LimitIgnored));
            Assert.AreEqual(0, all.Count(i => i.Kind == FacialIssueKind.LimitUnknownShape));
        }
    }
}
