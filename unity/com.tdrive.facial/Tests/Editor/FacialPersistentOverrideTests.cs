// 持続する上書き（SetOverride / ClearOverride / ClearAllOverrides）のテスト（docs/19 E-2 / E-3 / U-3）。
// Timeline の一時停止中は評価が来ない = EvaluateNow だけを呼び続けても、上書きの値が保たれること。
using System.Reflection;
using NUnit.Framework;
using UnityEngine;

namespace TDrive.Facial.Tests
{
    public class FacialPersistentOverrideTests
    {
        const float Dt = 10f;
        FacialTestRig _rig;
        GameObject _ownerGo;
        readonly object _a = new object();
        readonly object _b = new object();

        FacialCorrectionRunner R { get { return _rig.runner; } }

        [SetUp]
        public void SetUp()
        {
            _rig = new FacialTestRig();
            _rig.runner.useManualAngles = true;
        }

        [TearDown]
        public void TearDown()
        {
            if (_ownerGo != null) Object.DestroyImmediate(_ownerGo);
            _rig.Dispose();
        }

        void Eval() { R.EvaluateNow((Transform)null, Dt); }

        static void CallOnDisable(FacialCorrectionRunner r)
        {
            typeof(FacialCorrectionRunner).GetMethod("OnDisable", BindingFlags.NonPublic | BindingFlags.Instance).Invoke(r, null);
        }

        [Test]
        public void ValuesSurviveFramesWithoutAnyEvaluationFromTheOwner()
        {
            R.SetOverride(_a, new FacialFrameOverride { hasManualAngles = true, yaw = 45f, pitch = 0f });
            for (int i = 0; i < 5; i++)
            {
                Eval(); // 一時停止中のカットシーン: 持ち主は何も呼ばない
                Assert.AreEqual(45f, R.CurrentYaw, 1e-3f, "フレーム " + i);
            }
        }

        [Test]
        public void PushOverrideStaysOneFrameForCompatibility()
        {
            R.PushOverride(new FacialFrameOverride { hasManualAngles = true, yaw = 45f });
            Eval();
            Assert.AreEqual(45f, R.CurrentYaw, 1e-3f);
            Eval();
            Assert.AreEqual(0f, R.CurrentYaw, 1e-3f, "PushOverride は 1 回で消える");
        }

        [Test]
        public void ClearingTheOwnerReturnsToNormal()
        {
            R.SetOverride(_a, new FacialFrameOverride { hasManualAngles = true, yaw = 45f });
            Eval();
            Assert.IsTrue(R.ClearOverride(_a));
            Assert.IsFalse(R.ClearOverride(_a), "2 回目は無い");
            Eval();
            Assert.AreEqual(0f, R.CurrentYaw, 1e-3f);
            Assert.AreEqual(0, R.OverrideCount);
        }

        [Test]
        public void ClearAllDropsEveryOwnerAndThePendingPush()
        {
            R.SetOverride(_a, new FacialFrameOverride { hasManualAngles = true, yaw = 45f });
            R.SetOverride(_b, new FacialFrameOverride { hasAlpha = true, alpha = 0.5f });
            R.PushOverride(new FacialFrameOverride { hasManualAngles = true, yaw = 10f });
            R.ClearAllOverrides();
            Eval();
            Assert.AreEqual(0f, R.CurrentYaw, 1e-3f);
            Assert.AreEqual(0, R.OverrideCount);
        }

        [Test]
        public void ValuesAreCopiedSoLaterChangesToTheCallersArrayDoNothing()
        {
            var emo = new[] { 0f, 1f };
            R.SetOverride(_a, new FacialFrameOverride { emotionWeights = emo });
            emo[1] = 0f; // 呼び出し側が配列を使い回して書き換えても
            Eval();
            Assert.AreEqual(100f, _rig.W(FacialTestRig.N("Joy", 1, 1)), 0.5f, "コピーした値（1）が使われる");
        }

        // ---------------------------------------------------------------- 合成（E-3）

        [Test]
        public void TwoOwnersBothTakeEffect_AlphaMultipliesAndAnglesAndEmotionsMerge()
        {
            // 手のトラック: 角度を固定 + 感情 Joy。自動トラック: 強さ 0.5
            R.SetOverride(_a, new FacialFrameOverride { hasManualAngles = true, yaw = 45f, emotionWeights = new[] { float.NaN, 1f } });
            R.SetOverride(_b, new FacialFrameOverride { hasAlpha = true, alpha = 0.5f, emotionWeights = new[] { float.NaN, float.NaN } });
            Eval();
            Assert.AreEqual(45f, R.CurrentYaw, 1e-3f, "A の角度が効く");
            float joy = _rig.W(FacialTestRig.N("Joy", 1, 1));
            Assert.Greater(joy, 0f, "A の感情が B の「指定なし」に消されない");

            R.ClearOverride(_a);
            R.SetOverride(_a, new FacialFrameOverride { hasManualAngles = true, yaw = 45f, emotionWeights = new[] { float.NaN, 1f }, hasAlpha = true, alpha = 0.5f });
            Eval();
            float lastScaleBoth = R.LastScale; // 0.5 × 0.5 = 0.25（掛け算）
            R.ClearAllOverrides();
            R.SetOverride(_a, new FacialFrameOverride { hasManualAngles = true, yaw = 45f });
            Eval();
            Assert.AreEqual(0.25f, lastScaleBoth / R.LastScale, 1e-3f, "alpha は持ち主ごとに掛け合わさる");
        }

        [Test]
        public void EmotionLayerTakenFromTheLaterOwnerAndPriorityOverridesRegistrationOrder()
        {
            R.SetOverride(_a, new FacialFrameOverride { emotionWeights = new[] { float.NaN, 1f } });
            R.SetOverride(_b, new FacialFrameOverride { emotionWeights = new[] { float.NaN, 0f } });
            Eval();
            Assert.AreEqual(0f, _rig.W(FacialTestRig.N("Joy", 1, 1)), 0.5f, "後に登録した B が勝つ");

            R.SetOverride(_a, new FacialFrameOverride { emotionWeights = new[] { float.NaN, 1f } }, 5); // A の優先度を上げる
            Eval();
            Assert.AreEqual(100f, _rig.W(FacialTestRig.N("Joy", 1, 1)), 0.5f, "priority が大きい A が勝つ");
        }

        [Test]
        public void ManualAngleWithTheLargestBlendWinsAndViewerIsTheLatestNonNull()
        {
            R.SetOverride(_a, new FacialFrameOverride { hasManualAngles = true, yaw = 40f, manualAngleBlend = 1f });
            R.SetOverride(_b, new FacialFrameOverride { hasManualAngles = true, yaw = 10f, manualAngleBlend = 0.5f });
            Eval();
            Assert.AreEqual(40f, R.CurrentYaw, 1e-3f, "blend が大きいほう");
        }

        [Test]
        public void PosesFromAllOwnersAreApplied()
        {
            FacialPoseAsset p1 = ScriptableObject.CreateInstance<FacialPoseAsset>();
            FacialPoseAsset p2 = ScriptableObject.CreateInstance<FacialPoseAsset>();
            try
            {
                p1.curves = new[] { new FacialPoseCurve { name = "bs.other", value = 0.1f } };
                p2.curves = new[] { new FacialPoseCurve { name = "bs.other", value = 0.2f } };
                p1.bones = new FacialPoseBone[0]; p2.bones = new FacialPoseBone[0];
                R.SetOverride(_a, new FacialFrameOverride { pose = p1, poseWeight = 1f });
                R.SetOverride(_b, new FacialFrameOverride { pose = p2, poseWeight = 1f });
                Eval();
                Assert.AreEqual(30f, _rig.W("bs.other"), 1e-3f, "加算（10 + 20）");
                Eval();
                Assert.AreEqual(30f, _rig.W("bs.other"), 1e-3f, "積もらない");
                R.ClearAllOverrides();
                Eval();
                Assert.AreEqual(0f, _rig.W("bs.other"), 1e-3f, "消せば戻る");
            }
            finally { Object.DestroyImmediate(p1); Object.DestroyImmediate(p2); }
        }

        // ---------------------------------------------------------------- 寿命（U-3 / E-2）

        [Test]
        public void DisableAndEnableDoesNotReplayAnOldCut()
        {
            R.SetOverride(_a, new FacialFrameOverride { hasManualAngles = true, yaw = 45f });
            Eval();
            CallOnDisable(R); // プールへ返す
            Assert.AreEqual(0, R.OverrideCount);
            Eval(); // 出し直した最初のフレーム
            Assert.AreEqual(0f, R.CurrentYaw, 1e-3f, "古いカットの上書きが当たらない");
        }

        [Test]
        public void AnOwnerWhoseObjectWasDestroyedIsDropped()
        {
            _ownerGo = UnityEditor.EditorUtility.CreateGameObjectWithHideFlags("OwnerGo", HideFlags.HideAndDontSave);
            R.SetOverride(new object(), new FacialFrameOverride { hasManualAngles = true, yaw = 45f }, 0, _ownerGo);
            Eval();
            Assert.AreEqual(45f, R.CurrentYaw, 1e-3f);
            Object.DestroyImmediate(_ownerGo);
            Eval();
            Assert.AreEqual(0, R.OverrideCount, "持ち主が壊れたら捨てる");
            Assert.AreEqual(0f, R.CurrentYaw, 1e-3f);
        }

        [Test]
        public void NullOwnerIsRejected()
        {
            Assert.Throws<System.ArgumentNullException>(() => R.SetOverride(null, new FacialFrameOverride()));
        }

        // ---------------------------------------------------------------- 割り当て

        [Test]
        public void SteadyStateWithTwoOwnersDoesNotAllocate()
        {
            var emoA = new[] { float.NaN, 0.8f };
            var emoB = new[] { float.NaN, float.NaN };
            for (int i = 0; i < 30; i++)
            {
                R.SetOverride(_a, new FacialFrameOverride { hasManualAngles = true, yaw = i, emotionWeights = emoA });
                R.SetOverride(_b, new FacialFrameOverride { hasAlpha = true, alpha = 0.9f, emotionWeights = emoB });
                Eval();
            }
            AllocProbe.AssertNoAlloc(() =>
            {
                for (int i = 0; i < 300; i++)
                {
                    R.SetOverride(_a, new FacialFrameOverride { hasManualAngles = true, yaw = i * 0.1f, emotionWeights = emoA });
                    R.SetOverride(_b, new FacialFrameOverride { hasAlpha = true, alpha = 0.9f, emotionWeights = emoB });
                    R.EvaluateNow((Transform)null, Dt);
                }
            }, "2 つの持ち主の上書きで、2 回目以降の評価に割り当てがある");
        }
    }
}
