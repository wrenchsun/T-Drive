// リップシンク（F5-8）の Runner・取り込み・調整値・検証のテスト（Unity EditMode）。計算そのものは共通のテストデータ（lipsync.json）で確かめる。
// 合成キャラクター（FacialTestRig）に口のシェイプ bs.lip_a / bs.lip_i / bs.smile を足す。EvaluateNow(0, 0, dt) で 1 フレーム進める。
using System.Collections.Generic;
using NUnit.Framework;
using TDrive.Facial.Core;
using TDrive.Facial.Editor;
using UnityEngine;
using Object = UnityEngine.Object;

namespace TDrive.Facial.Tests
{
    public class FacialLipSyncTests
    {
        const float Dt = 0.02f;
        const string A = "bs.lip_a", I = "bs.lip_i", Smile = "bs.smile";
        FacialTestRig _rig;
        FacialCorrectionOverrides _ov;
        int _ia;

        FacialCorrectionRunner R { get { return _rig.runner; } }

        [SetUp]
        public void SetUp()
        {
            _rig = new FacialTestRig();
            _rig.AddShape(A); _rig.AddShape(I); _rig.AddShape(Smile);
            SetTable(follow: 0f);
            _ia = new FacialShapeIndex(_rig.mesh).Find(A);
        }

        [TearDown]
        public void TearDown()
        {
            _rig.Dispose();
            if (_ov != null) Object.DestroyImmediate(_ov);
        }

        static FacialLipSyncEntryData Entry(string ph, string emo, params object[] nameWeight)
        {
            var shapes = new FacialLipSyncShapeData[nameWeight.Length / 2];
            for (int i = 0; i < shapes.Length; i++)
                shapes[i] = new FacialLipSyncShapeData { name = (string)nameWeight[i * 2], weight = (float)nameWeight[i * 2 + 1] };
            return new FacialLipSyncEntryData { phoneme = ph, emotion = emo, shapes = shapes };
        }

        void SetTable(float follow, bool enabled = true)
        {
            _rig.data.lipSync = new FacialLipSyncData
            {
                enabled = enabled, strength = 1f, follow = follow,
                phonemes = new[] { "A", "I" },
                volume = new FacialLipSyncVolumeData { min = 0f, max = 1f, from = 1f, to = 1f },
                entries = new[]
                {
                    Entry("A", "", A, 1f), Entry("I", "", I, 1f),
                    Entry("A", "Joy", A, 0.8f, Smile, 0.3f),
                },
            };
        }

        void Eval(float dt = Dt) { R.EvaluateNow(0f, 0f, dt); }
        float Lip { get { return _rig.smr.GetBlendShapeWeight(_ia); } }
        void SetLip(float v) { _rig.smr.SetBlendShapeWeight(_ia, v); }

        // ---------------------------------------------------------------- 計算

        [Test]
        public void IndexInputWritesTheTableOutput()
        {
            Assert.IsTrue(R.LipSyncActive);
            Assert.AreEqual(2, R.LipSyncPhonemeCount);
            Assert.AreEqual("I", R.GetLipSyncPhonemeName(1));
            R.SetLipSyncByIndex(new[] { 1f, 0f }, 2, float.NaN);
            Eval();
            Assert.AreEqual(100f, Lip, 1e-2f);
            Assert.AreEqual(0f, _rig.W(I), 1e-3f);
            R.SetLipSyncByIndex(new[] { 0.25f, 0.5f }, 2, float.NaN);
            Eval();
            Assert.AreEqual(25f, Lip, 1e-2f);
            Assert.AreEqual(50f, _rig.W(I), 1e-2f);
        }

        [Test]
        public void NameInputIgnoresUnknownPhonemesAndCopiesTheValues()
        {
            var names = new List<string> { "A", "X" };
            var weights = new List<float> { 0.5f, 1f };
            R.SetLipSync(names, weights, 2);
            weights[0] = 1f; // 渡したあとの変更は影響しない
            Eval();
            Assert.AreEqual(50f, Lip, 1e-2f);
            Assert.AreEqual(0.5f, R.GetLipSyncPhonemeWeight(0), 1e-4f);
        }

        [Test]
        public void EmotionWeightComesFromTheRunnersEffectiveWeights()
        {
            R.emotionWeights = new[] { 0f, 1f };
            R.SetLipSyncByIndex(new[] { 1f, 0f }, 2, float.NaN);
            Eval();
            Assert.AreEqual(80f, Lip, 1e-2f);
            Assert.AreEqual(30f, _rig.W(Smile), 1e-2f);
            R.PushOverride(new FacialFrameOverride { emotionWeights = new[] { float.NaN, 0f } }); // Timeline の上書きが優先
            Eval();
            Assert.AreEqual(100f, Lip, 1e-2f);
            R.SetLayerMuted(1, true);
            R.emotionWeights = new[] { 0f, 1f };
            Eval();
            Assert.AreEqual(100f, Lip, 1e-2f, "ミュートしたレイヤーは 0");
        }

        [Test]
        public void VolumeScalesTheMouth()
        {
            var d = _rig.data.lipSync;
            d.volume = new FacialLipSyncVolumeData { min = 0.2f, max = 0.8f, from = 0.5f, to = 1f };
            _rig.data.lipSync = d;
            R.SetLipSyncByIndex(new[] { 1f, 0f }, 2, 0.5f);
            Eval();
            Assert.AreEqual(75f, Lip, 1e-2f);
            R.SetLipSyncByIndex(new[] { 1f, 0f }, 2, float.NaN);
            Eval();
            Assert.AreEqual(100f, Lip, 1e-2f);
        }

        [Test]
        public void OverrideAssetChangesStrengthAndFollow()
        {
            _ov = ScriptableObject.CreateInstance<FacialCorrectionOverrides>();
            _ov.overrideLipSyncStrength = true; _ov.lipSyncStrength = 0.5f;
            R.overrides = _ov;
            R.SetLipSyncByIndex(new[] { 1f, 0f }, 2, float.NaN);
            Eval();
            Assert.AreEqual(50f, Lip, 1e-2f);
            Assert.AreEqual(0.5f, R.EffectiveParams.lipSyncStrength, 1e-6f);
            _ov.overrideLipSyncStrength = false;
            _ov.overrideLipSyncFollow = true; _ov.lipSyncFollow = 5f;
            Assert.AreEqual(5f, R.EffectiveParams.lipSyncFollow, 1e-6f);
        }

        [Test]
        public void DisabledTableOrNoDataDoesNothing()
        {
            _rig.mesh.name = "RigMesh";
            SetLip(40f);
            SetTable(0f, enabled: false);
            Assert.IsFalse(R.LipSyncActive);
            R.SetLipSyncByIndex(new[] { 1f, 0f }, 2, float.NaN);
            Eval();
            Assert.AreEqual(40f, Lip, 1e-3f);
        }

        // ---------------------------------------------------------------- 追従

        [Test]
        public void FollowSmoothsTheWeightsAndReturnsToZeroWhenCleared()
        {
            SetTable(follow: 10f);
            SetLip(40f);
            R.SetLipSyncByIndex(new[] { 1f, 0f }, 2, float.NaN);
            Eval(0.05f);
            float w1 = R.GetLipSyncPhonemeWeight(0);
            Assert.That(w1, Is.GreaterThan(0.2f).And.LessThan(0.8f), "追従: 一度では届かない");
            for (int i = 0; i < 100; i++) Eval(0.05f);
            Assert.AreEqual(1f, R.GetLipSyncPhonemeWeight(0), 1e-3f);
            Assert.AreEqual(100f, Lip, 0.5f);
            R.ClearLipSync();
            Eval(0.05f);
            Assert.Greater(R.GetLipSyncPhonemeWeight(0), 0f, "入力が止まっても追従の速さで戻る");
            for (int i = 0; i < 200; i++) Eval(0.05f);
            Assert.AreEqual(0f, R.GetLipSyncPhonemeWeight(0));
            Assert.AreEqual(40f, Lip, 1e-3f, "戻りきったら書く前の値");
        }

        // ---------------------------------------------------------------- 「今の値」と書く前の値

        [Test]
        public void NothingElseWritesDoesNotCompoundOurOwnOutput()
        {
            SetLip(40f);
            R.SetLipSyncByIndex(new[] { 0.5f, 0f }, 2, float.NaN);
            for (int i = 0; i < 10; i++)
            {
                Eval();
                Assert.AreEqual(70f, Lip, 1e-2f, "40 × (1 − 0.5) + 50 のまま（前回の出力を読み戻さない）: フレーム " + i);
            }
        }

        [Test]
        public void AnimatedEveryFrameUsesTheNewExternalValue()
        {
            R.SetLipSyncByIndex(new[] { 0.5f, 0f }, 2, float.NaN);
            for (int i = 0; i < 10; i++)
            {
                float ext = 20f + i * 6f;
                SetLip(ext); // Animator が毎フレーム書く
                Eval();
                Assert.AreEqual(ext * 0.5f + 50f, Lip, 1e-2f, "フレーム " + i);
            }
        }

        [Test]
        public void ResetWeightsRestoresTheValueBeforeWeWroteNotZero()
        {
            SetLip(40f);
            R.SetLipSyncByIndex(new[] { 1f, 1f }, 2, float.NaN);
            Eval(); Eval();
            Assert.AreEqual(100f, Lip, 1e-2f);
            R.ResetWeights(); // 編集時（再生中でない）は FC_ をすべて 0 にするが、口は書く前の値へ
            Assert.AreEqual(40f, Lip, 1e-3f);
            Assert.AreEqual(0f, _rig.W(I), 1e-3f);
            Assert.AreEqual(0f, R.GetLipSyncPhonemeWeight(0), "状態も捨てる");
            Eval(); // 入力も捨てたので、何も書かない
            Assert.AreEqual(40f, Lip, 1e-3f);
        }

        [Test]
        public void RestoreLeavesAValueSomeoneElseRewrote()
        {
            SetLip(40f);
            R.SetLipSyncByIndex(new[] { 1f, 0f }, 2, float.NaN);
            Eval();
            SetLip(63f); // 誰かが書き直した
            R.ResetWeights();
            Assert.AreEqual(63f, Lip, 1e-3f);
        }

        [Test]
        public void OnDisableRestoresAndClearsTheInput()
        {
            SetLip(40f);
            R.SetLipSyncByIndex(new[] { 1f, 0f }, 2, float.NaN);
            Eval();
            // EditMode では OnDisable が自動で呼ばれないので、非公開のメッセージ関数を直接呼ぶ
            typeof(FacialCorrectionRunner).GetMethod("OnDisable", System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.NonPublic).Invoke(R, null);
            Assert.AreEqual(40f, Lip, 1e-3f);
            Eval();
            Assert.AreEqual(40f, Lip, 1e-3f, "再び有効にしても入力は残らない");
        }

        // ---------------------------------------------------------------- 表情での弱め・欠けたシェイプ・取り込み・検証

        [Test]
        public void ExpressionDampeningSeesTheLipShapeWrittenThisFrame()
        {
            _rig.data.intensityCurves = new[] { A };
            R.SetLipSyncByIndex(new[] { 1f, 0f }, 2, float.NaN);
            Eval();
            // 口が 100（強さ 1）→ 補正 × (1 − 0.5 × 1) = 0.5。格子の中央 Neutral(1,1) は 100 → 50
            Assert.AreEqual(50f, _rig.W(FacialTestRig.N("Neutral", 1, 1)), 0.5f);
        }

        [Test]
        public void MissingLipShapesAreReportedAndSkipped()
        {
            var d = _rig.data.lipSync;
            d.entries = new[] { Entry("A", "", A, 1f, "bs.not_there", 1f) };
            _rig.data.lipSync = d;
            Assert.IsTrue(R.LipSyncActive);
            CollectionAssert.Contains(new List<string>(R.GetMissingLipSyncShapeNames()), "bs.not_there");
            R.SetLipSyncByIndex(new[] { 1f, 0f }, 2, float.NaN);
            Eval();
            Assert.AreEqual(100f, Lip, 1e-2f);
            List<FacialIssue> issues = FacialValidation.Run(R);
            Assert.IsTrue(issues.Exists(x => x.Kind == FacialIssueKind.MissingLipSyncShape && x.ShapeName == "bs.not_there"));
            Assert.IsFalse(issues.Exists(x => x.Kind == FacialIssueKind.MissingLipSyncShape && x.ShapeName == A));
        }

        [Test]
        public void ImportCopiesTheTable()
        {
            string json = "{\"format\":\"FacialCorrection\",\"version\":1,\"asset\":\"test\",\"lipSync\":{\"enabled\":true,\"strength\":0.6,\"phonemes\":[\"A\",\"I\"],"
                + "\"entries\":[{\"phoneme\":\"A\",\"emotion\":\"\",\"curves\":{\"bs.lip_a\":1}},{\"phoneme\":\"A\",\"emotion\":\"Joy\",\"curves\":{\"bs.lip_a\":0.8,\"bs.smile\":0.3}}],"
                + "\"volume\":{\"min\":0.1,\"max\":0.9,\"from\":0.2,\"to\":1.5},\"follow\":12}}";
            FacialCorrectionData d = FcposeConverter.BuildData(json, "x", null);
            try
            {
                FacialLipSyncData l = d.lipSync;
                Assert.IsTrue(l.enabled);
                Assert.AreEqual(0.6f, l.strength, 1e-6f);
                Assert.AreEqual(new[] { "A", "I" }, l.phonemes);
                Assert.AreEqual(2, l.entries.Length);
                Assert.AreEqual("Joy", l.entries[1].emotion);
                Assert.AreEqual(2, l.entries[1].shapes.Length);
                Assert.AreEqual(0.8f, l.entries[1].shapes[0].weight, 1e-6f);
                Assert.AreEqual(1.5f, l.volume.to, 1e-6f);
                Assert.AreEqual(12f, l.follow);
            }
            finally { Object.DestroyImmediate(d); }
            FacialCorrectionData none = FcposeConverter.BuildData("{\"format\":\"FacialCorrection\",\"version\":1,\"asset\":\"test\"}", "x", null);
            try
            {
                Assert.IsFalse(none.lipSync.enabled);
                Assert.AreEqual(0, none.lipSync.entries.Length);
                Assert.AreEqual(1f, none.lipSync.strength);
            }
            finally { Object.DestroyImmediate(none); }
        }

        // ---------------------------------------------------------------- 割り当て

        [Test]
        public void SteadyStateDoesNotAllocate()
        {
            var names = new List<string> { "A", "I", "X" };
            var weights = new List<float> { 0.5f, 0.2f, 0.9f };
            R.emotionWeights = new[] { 0f, 0.5f };
            for (int i = 0; i < 40; i++) { R.SetLipSync(names, weights, 3, 0.5f); Eval(); }
            AllocProbe.AssertNoAlloc(() =>
            {
                for (int i = 0; i < 300; i++)
                {
                    weights[0] = 0.5f + 0.4f * Mathf.Sin(i * 0.1f);
                    R.SetLipSync(names, weights, 3, 0.5f);
                    R.SetLipSyncByIndex(weights, 2, 0.4f);
                    Eval();
                    if (i % 100 == 99) R.ClearLipSync();
                }
            }, "リップシンクを使った 2 回目以降の評価でマネージドの割り当てがある");
        }
    }
}
