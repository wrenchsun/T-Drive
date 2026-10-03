// 検証（FacialValidation）のテスト。メッシュ・階層・データはすべてテスト内で作る（アセットは作らない）。
using System.Collections.Generic;
using System.Linq;
using NUnit.Framework;
using TDrive.Facial.Editor;
using UnityEditor;
using UnityEngine;

namespace TDrive.Facial.Tests
{
    public class FacialValidationTests
    {
        FacialTestRig _rig;

        [TearDown]
        public void TearDown() { if (_rig != null) _rig.Dispose(); }

        static List<FacialIssue> Of(List<FacialIssue> all, FacialIssueKind kind)
        {
            return all.Where(i => i.Kind == kind).ToList();
        }

        [Test]
        public void CleanSetupHasNoIssues()
        {
            _rig = new FacialTestRig(true);
            List<FacialIssue> issues = FacialValidation.Run(_rig.runner);
            Assert.AreEqual(0, issues.Count, string.Join("\n", issues.Select(i => i.ToString())));
        }

        [Test]
        public void MissingShapeIsReported()
        {
            _rig = new FacialTestRig(false);
            List<FacialIssue> m = Of(FacialValidation.Run(_rig.runner), FacialIssueKind.MissingShape);
            Assert.AreEqual(1, m.Count);
            Assert.AreEqual(FacialTestRig.Missing, m[0].ShapeName);
            Assert.AreEqual(FacialIssueSeverity.Warning, m[0].Severity);
            StringAssert.Contains(FacialTestRig.Missing, m[0].Message);
        }

        [Test]
        public void OrphanShapeIsReportedButPerspectiveAndExtremeAreNot()
        {
            _rig = new FacialTestRig(true);
            _rig.AddShape("FC_test_Neutral_R9_C9");       // データが知らない
            _rig.AddShape("FC_other_Neutral_R0_C0");      // 別アセットの残り
            _rig.AddShape("FC_test_Persp_K0");            // F5 で使う
            _rig.AddShape("FC_test_Neutral_R0_C0_Ex");    // F5 で使う
            _rig.runner.RebuildCaches();
            List<FacialIssue> o = Of(FacialValidation.Run(_rig.runner), FacialIssueKind.OrphanShape);
            CollectionAssert.AreEquivalent(new[] { "FC_test_Neutral_R9_C9", "FC_other_Neutral_R0_C0" }, o.Select(i => i.ShapeName));
        }

        [Test]
        public void BaseBoneMissingIsAnError()
        {
            _rig = new FacialTestRig(true);
            _rig.head.name = "not_head";
            _rig.runner.RebuildCaches();
            List<FacialIssue> b = Of(FacialValidation.Run(_rig.runner), FacialIssueKind.BaseBoneMissing);
            Assert.AreEqual(1, b.Count);
            Assert.AreEqual(FacialIssueSeverity.Error, b[0].Severity);
            Assert.AreEqual("head", b[0].BoneName);
        }

        [Test]
        public void NoTargetIsAnError()
        {
            _rig = new FacialTestRig(true);
            List<FacialIssue> issues = FacialValidation.Run(_rig.data, new SkinnedMeshRenderer[0], _rig.head.transform);
            Assert.AreEqual(1, Of(issues, FacialIssueKind.NoTarget).Count);
            Assert.AreEqual(FacialIssueSeverity.Error, Of(issues, FacialIssueKind.NoTarget)[0].Severity);
            Assert.AreEqual(0, Of(issues, FacialIssueKind.MissingShape).Count, "対象が無いときは不足を並べない");
        }

        [Test]
        public void NoDataIsAnError()
        {
            List<FacialIssue> issues = FacialValidation.Run(null, new SkinnedMeshRenderer[0], null);
            Assert.AreEqual(1, issues.Count);
            Assert.AreEqual(FacialIssueKind.NoData, issues[0].Kind);
        }

        [Test]
        public void GridLayerSizeMismatchIsAnError()
        {
            _rig = new FacialTestRig(true);
            _rig.data.layers[1].morphNames = new[] { "", "", "", "" };
            List<FacialIssue> g = Of(FacialValidation.Run(_rig.runner), FacialIssueKind.GridLayerMismatch);
            Assert.AreEqual(1, g.Count);
            Assert.AreEqual(FacialIssueSeverity.Error, g[0].Severity);
            Assert.AreEqual(1, Of(FacialValidation.RunStructure(_rig.data), FacialIssueKind.GridLayerMismatch).Count);
        }

        [Test]
        public void InvalidGridLayerZeroAndDuplicateNames()
        {
            _rig = new FacialTestRig(true);
            _rig.data.grid.cols = 0;
            _rig.data.layers[0].name = "Joy";
            List<FacialIssue> issues = FacialValidation.RunStructure(_rig.data);
            Assert.AreEqual(1, Of(issues, FacialIssueKind.InvalidGrid).Count);
            Assert.AreEqual(1, Of(issues, FacialIssueKind.LayerZeroNotNeutral).Count);
            Assert.AreEqual(1, Of(issues, FacialIssueKind.DuplicateLayerName).Count);
        }

        [Test]
        public void IntensityShapeMissingIsReported()
        {
            _rig = new FacialTestRig(true);
            _rig.data.intensityCurves = new[] { "bs.jaw", "bs.nothing" };
            List<FacialIssue> i = Of(FacialValidation.Run(_rig.runner), FacialIssueKind.IntensityShapeMissing);
            Assert.AreEqual(1, i.Count);
            Assert.AreEqual("bs.nothing", i[0].ShapeName);
        }

        [Test]
        public void LimitsReferencingUnknownOrNonFcShapes()
        {
            _rig = new FacialTestRig(true);
            _rig.data.limits = new[]
            {
                new FacialLimitEntry { name = "bs.unknown", min = 0, max = 1 },
                new FacialLimitEntry { name = "bs.other", min = 0, max = 1 },
                new FacialLimitEntry { name = FacialTestRig.N("Neutral", 1, 1), min = 0, max = 0.5f },
            };
            List<FacialIssue> issues = FacialValidation.Run(_rig.runner);
            List<FacialIssue> unknown = Of(issues, FacialIssueKind.LimitUnknownShape);
            Assert.AreEqual(1, unknown.Count);
            Assert.AreEqual("bs.unknown", unknown[0].ShapeName);
            List<FacialIssue> ignored = Of(issues, FacialIssueKind.LimitIgnored);
            Assert.AreEqual(1, ignored.Count);
            Assert.AreEqual("bs.other", ignored[0].ShapeName);
            Assert.AreEqual(FacialIssueSeverity.Info, ignored[0].Severity);
        }

        [Test]
        public void BlendShapeNormalsIsInfoOnlyWhenDifferentFromRecommended()
        {
            Assert.IsNull(FacialValidation.CheckBlendShapeNormals(FacialValidation.RecommendedBlendShapeNormals, "Assets/a.fbx"));
            foreach (ModelImporterNormals n in new[] { ModelImporterNormals.Calculate, ModelImporterNormals.None })
            {
                FacialIssue i = FacialValidation.CheckBlendShapeNormals(n, "Assets/a.fbx");
                Assert.IsNotNull(i);
                Assert.AreEqual(FacialIssueSeverity.Info, i.Severity);
                Assert.AreEqual(FacialIssueKind.BlendShapeNormals, i.Kind);
                StringAssert.Contains(n.ToString(), i.Message);
                StringAssert.Contains(FacialValidation.RecommendedBlendShapeNormals.ToString(), i.Message);
                StringAssert.Contains("Assets/a.fbx", i.Message);
            }
        }

        [Test]
        public void ProceduralMeshesHaveNoImporterIssue()
        {
            _rig = new FacialTestRig(true);
            Assert.AreEqual(0, Of(FacialValidation.Run(_rig.runner), FacialIssueKind.BlendShapeNormals).Count);
        }

        [Test]
        public void CountBySeverity()
        {
            _rig = new FacialTestRig(false);
            List<FacialIssue> issues = FacialValidation.Run(_rig.runner);
            Assert.AreEqual(1, FacialValidation.Count(issues, FacialIssueSeverity.Warning));
            Assert.AreEqual(0, FacialValidation.Count(issues, FacialIssueSeverity.Error));
        }
    }
}
