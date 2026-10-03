// FT-1 で Runner に足した口のテスト: manualAngleBlend（ライブとの補間）とカット補正（ポーズの加算・復元）、デバッグ表示のレベル。
using NUnit.Framework;
using UnityEngine;

namespace TDrive.Facial.Tests
{
    public class FacialRunnerOverrideTests
    {
        const float Dt = 10f;
        FacialTestRig _rig;
        FacialPoseAsset _pose;

        [SetUp]
        public void SetUp()
        {
            _rig = new FacialTestRig();
            _rig.runner.useManualAngles = true; // 視点なしで動かす（ライブの角度 = 手動 0, 0）
        }

        [TearDown]
        public void TearDown()
        {
            if (_pose != null) Object.DestroyImmediate(_pose);
            _rig.Dispose();
        }

        void Push(FacialFrameOverride o) { _rig.runner.PushOverride(o); }
        void Eval() { _rig.runner.EvaluateNow((Transform)null, Dt); }

        FacialPoseAsset MakePose(float otherValue)
        {
            _pose = ScriptableObject.CreateInstance<FacialPoseAsset>();
            _pose.curves = new[] { new FacialPoseCurve { name = "bs.other", value = otherValue } };
            _pose.bones = new[]
            {
                new FacialPoseBone { name = "head", position = new Vector3(0f, 1f, 0f), rotation = Quaternion.Euler(0f, 90f, 0f), scale = new Vector3(2f, 2f, 2f) },
            };
            return _pose;
        }

        [Test]
        public void ManualAngleBlendInterpolatesWithLiveAngles()
        {
            Push(new FacialFrameOverride { hasManualAngles = true, yaw = 45f, pitch = 0f, manualAngleBlend = 0.5f });
            Eval();
            Assert.AreEqual(22.5f, _rig.runner.CurrentYaw, 1e-3f);
            Assert.AreEqual(FacialAngleSource.Blended, _rig.runner.LastAngleSource);
            Assert.AreEqual(75f, _rig.W(FacialTestRig.N("Neutral", 1, 1)), 0.2f);
        }

        [Test]
        public void ManualAngleBlendZeroMeansFullManualForBackwardCompatibility()
        {
            Push(new FacialFrameOverride { hasManualAngles = true, yaw = 45f, pitch = 0f });
            Eval();
            Assert.AreEqual(45f, _rig.runner.CurrentYaw, 1e-3f);
            Assert.AreEqual(FacialAngleSource.OverrideManual, _rig.runner.LastAngleSource);
        }

        [Test]
        public void ManualAngleBlendTakesTheShortWayAroundWhenInterpolating()
        {
            _rig.runner.manualYaw = 170f;
            Push(new FacialFrameOverride { hasManualAngles = true, yaw = -170f, manualAngleBlend = 0.5f });
            Eval();
            Assert.AreEqual(180f, Mathf.Abs(_rig.runner.CurrentYaw), 1e-2f);
        }

        [Test]
        public void ManualOverrideWithoutViewerStillWorks()
        {
            _rig.runner.useManualAngles = false;
            _rig.data.grid.baseBone = "noSuchBone"; // 基準ボーンが無い = ライブの角度を求められない
            _rig.runner.RebuildCaches();
            Push(new FacialFrameOverride { hasManualAngles = true, yaw = 45f, manualAngleBlend = 0.5f }); // → 完全に手動
            Eval();
            Assert.AreEqual(45f, _rig.runner.CurrentYaw, 1e-3f);
        }

        [Test]
        public void PoseCurvesAddToNamedShapeAndNeverAccumulate()
        {
            _rig.smr.SetBlendShapeWeight(_rig.mesh.GetBlendShapeIndex("bs.other"), 10f);
            FacialPoseAsset pose = MakePose(0.5f);
            for (int i = 0; i < 3; i++)
            {
                Push(new FacialFrameOverride { pose = pose, poseWeight = 1f });
                Eval();
                Assert.AreEqual(60f, _rig.W("bs.other"), 1e-3f, "同じ入力を繰り返しても積もらない（" + i + "）");
            }
            Push(new FacialFrameOverride { pose = pose, poseWeight = 0.5f });
            Eval();
            Assert.AreEqual(35f, _rig.W("bs.other"), 1e-3f);

            Eval(); // 上書きなし → 元の値へ
            Assert.AreEqual(10f, _rig.W("bs.other"), 1e-3f);
        }

        [Test]
        public void PoseDoesNotClobberAValueTheAnimationRewrote()
        {
            int idx = _rig.mesh.GetBlendShapeIndex("bs.other");
            _rig.smr.SetBlendShapeWeight(idx, 10f);
            Push(new FacialFrameOverride { pose = MakePose(0.5f), poseWeight = 1f });
            Eval();
            _rig.smr.SetBlendShapeWeight(idx, 20f); // アニメーションが書き直した
            Eval();                                  // 上書きなし
            Assert.AreEqual(20f, _rig.W("bs.other"), 1e-3f);
        }

        [Test]
        public void PoseBonesApplyAdditiveOffsetAndRestore()
        {
            Transform head = _rig.head.transform;
            Vector3 p0 = head.localPosition;
            Push(new FacialFrameOverride { pose = MakePose(0f), poseWeight = 0.5f });
            Eval();
            Assert.AreEqual(p0 + new Vector3(0f, 0.5f, 0f), head.localPosition);
            Assert.AreEqual(45f, Quaternion.Angle(Quaternion.identity, head.localRotation), 1e-2f);
            Assert.AreEqual(1.5f, head.localScale.x, 1e-4f);

            Push(new FacialFrameOverride { pose = _pose, poseWeight = 0.5f });
            Eval();
            Assert.AreEqual(p0 + new Vector3(0f, 0.5f, 0f), head.localPosition, "繰り返しても積もらない");
            Assert.AreEqual(1.5f, head.localScale.x, 1e-4f);

            Eval();
            Assert.AreEqual(p0, head.localPosition);
            Assert.AreEqual(1f, head.localScale.x, 1e-4f);
            Assert.AreEqual(0f, Quaternion.Angle(Quaternion.identity, head.localRotation), 1e-3f);
        }

        [Test]
        public void ResetWeightsRestoresPoseEverywhere()
        {
            int idx = _rig.mesh.GetBlendShapeIndex("bs.other");
            _rig.smr.SetBlendShapeWeight(idx, 10f);
            Vector3 p0 = _rig.head.transform.localPosition;
            Push(new FacialFrameOverride { pose = MakePose(0.5f), poseWeight = 1f });
            Eval();
            Assert.AreEqual(60f, _rig.W("bs.other"), 1e-3f);
            _rig.runner.ResetWeights();
            Assert.AreEqual(10f, _rig.W("bs.other"), 1e-3f);
            Assert.AreEqual(p0, _rig.head.transform.localPosition);
        }

        [Test]
        public void DebugOverlayUsesTheLargerOfComponentAndGlobalLevel()
        {
            var go = EditorUtilityHidden();
            try
            {
                var ov = go.AddComponent<FacialDebugOverlay>();
                int saved = FacialDebugOverlay.GlobalLevel;
                Assert.AreEqual(0, ov.EffectiveLevel);
                ov.debugLevel = 1;
                Assert.AreEqual(1, ov.EffectiveLevel);
                FacialDebugOverlay.GlobalLevel = 2;
                Assert.AreEqual(2, ov.EffectiveLevel);
                FacialDebugOverlay.GlobalLevel = 99;
                Assert.AreEqual(2, FacialDebugOverlay.GlobalLevel, "0〜2 に収める");
                FacialDebugOverlay.GlobalLevel = saved;
            }
            finally { Object.DestroyImmediate(go); }
        }

        static GameObject EditorUtilityHidden()
        {
            return UnityEditor.EditorUtility.CreateGameObjectWithHideFlags("DebugOverlayTest", HideFlags.HideAndDontSave);
        }
    }
}
