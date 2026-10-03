// FT-3: FacialTrackAsset（曲線の読み取り）と .fctrack → アセットの変換。FT-5: Runner の戻し（ResetWeights / OnDisable）と、D-Drive の FC-2（重みの復元）が二重になっても壊れないこと。
// FC-2 の復元は D-Drive の ModelInstancePoolable.OnReturn の動き（変化のある重みだけ、控えた値へ戻す）の写し。実際のプール経路でのテストは DDrive テスト側。
using System.Reflection;
using NUnit.Framework;
using TDrive.Facial.Core;
using TDrive.Facial.Editor;
using UnityEngine;

namespace TDrive.Facial.Tests
{
    public class FacialTrackAssetTests
    {
        static FacialKey K(float t, float v) { return new FacialKey(t, v); }

        // ---------------------------------------------------------------- 曲線の読み取り

        [Test]
        public void SampleIsLinearAndClampedAtBothEnds()
        {
            FacialKey[] k = { K(1f, 0f), K(3f, 1f) };
            Assert.AreEqual(0f, FacialTrackAsset.Sample(k, 0f), 1e-6f, "最初のキーより前は最初の値");
            Assert.AreEqual(0f, FacialTrackAsset.Sample(k, 1f), 1e-6f);
            Assert.AreEqual(0.25f, FacialTrackAsset.Sample(k, 1.5f), 1e-6f);
            Assert.AreEqual(0.5f, FacialTrackAsset.Sample(k, 2f), 1e-6f);
            Assert.AreEqual(1f, FacialTrackAsset.Sample(k, 3f), 1e-6f);
            Assert.AreEqual(1f, FacialTrackAsset.Sample(k, 9f), 1e-6f, "最後のキーより後は最後の値");
        }

        [Test]
        public void SampleHandlesEmptySingleAndStepKeys()
        {
            Assert.AreEqual(7f, FacialTrackAsset.Sample(null, 1f, 7f));
            Assert.AreEqual(7f, FacialTrackAsset.Sample(new FacialKey[0], 1f, 7f));
            Assert.AreEqual(2f, FacialTrackAsset.Sample(new[] { K(5f, 2f) }, 0f));
            Assert.AreEqual(2f, FacialTrackAsset.Sample(new[] { K(5f, 2f) }, 9f));
            // 同じ時刻の 2 キー = 段差（その時刻ちょうどは後の値）
            FacialKey[] step = { K(0f, 1f), K(1f, 1f), K(1f, 0f), K(2f, 0f) };
            Assert.AreEqual(1f, FacialTrackAsset.Sample(step, 0.99f), 1e-6f);
            Assert.AreEqual(0f, FacialTrackAsset.Sample(step, 1f), 1e-6f);
            Assert.AreEqual(0f, FacialTrackAsset.Sample(step, 1.5f), 1e-6f);
            // 全部同じ時刻
            FacialKey[] same = { K(1f, 0f), K(1f, 5f) };
            Assert.AreEqual(0f, FacialTrackAsset.Sample(same, 0.5f));
            Assert.AreEqual(5f, FacialTrackAsset.Sample(same, 1f));
            Assert.AreEqual(5f, FacialTrackAsset.Sample(same, 2f));
        }

        // ---------------------------------------------------------------- 変換

        const string Json =
            "{\"format\":\"FacialTrack\",\"version\":1,\"shot\":\"S010\",\"model\":\"shizuku\",\"frameRate\":24,\"range\":[10,58]," +
            "\"curves\":{\"alpha\":[[0.0,1.0],[1.0,0.0]],\"emotion.Joy\":[[0.5,0.0],[1.0,1.0]],\"emotion.Anger\":[[0.0,0.2]],\"useManual\":[[0.0,1.0]],\"manualYaw\":[[0.0,-30.0]]}}";

        [Test]
        public void ConverterFillsTheAssetFromAFctrack()
        {
            FacialTrackAsset a = FctrackConverter.Build(FctrackReader.Read(Json), "S010__shizuku");
            try
            {
                Assert.AreEqual("S010__shizuku", a.name);
                Assert.AreEqual("S010", a.shot);
                Assert.AreEqual("shizuku", a.model);
                Assert.AreEqual(24f, a.frameRate);
                Assert.AreEqual(10f, a.rangeStart);
                Assert.AreEqual(58f, a.rangeEnd);
                Assert.AreEqual(2f, a.DurationSeconds, 1e-6f);
                Assert.AreEqual(2, a.alpha.Length);
                Assert.IsTrue(a.HasAlpha); Assert.IsTrue(a.HasUseManual); Assert.IsTrue(a.HasEmotions);
                Assert.AreEqual(0, a.manualPitch.Length, "打っていないカーブは空");
                Assert.AreEqual(-30f, a.manualYaw[0].value);
                Assert.AreEqual(2, a.emotions.Length);
                Assert.AreEqual("Anger", a.emotions[0].layer, "感情は名前順（取り込みのたびに同じ並び）");
                Assert.AreEqual("Joy", a.emotions[1].layer);
                Assert.AreEqual(1f, a.emotions[1].keys[1].value);
            }
            finally { Object.DestroyImmediate(a); }
        }

        [Test]
        public void ConverterFillsTheExaggerationCurveOnlyWhenPresent()
        {
            const string ex = "{\"format\":\"FacialTrack\",\"version\":1,\"shot\":\"a\",\"model\":\"b\",\"range\":[0,30],\"curves\":{\"exaggeration\":[[0.0,1.0],[0.5,0.0]]}}";
            FacialTrackAsset a = FctrackConverter.Build(FctrackReader.Read(ex), "x");
            FacialTrackAsset old = FctrackConverter.Build(FctrackReader.Read(Json), "y");
            try
            {
                Assert.IsTrue(a.HasExaggeration);
                Assert.AreEqual(2, a.exaggeration.Length);
                Assert.AreEqual(0f, a.exaggeration[1].value);
                Assert.IsFalse(old.HasExaggeration, "古い .fctrack = 誇張は変えない");
            }
            finally { Object.DestroyImmediate(a); Object.DestroyImmediate(old); }
        }

        [Test]
        public void EmptyCurvesGiveAnAssetWithoutAnyCurve()
        {
            FacialTrackAsset a = FctrackConverter.Build(FctrackReader.Read("{\"format\":\"FacialTrack\",\"version\":1,\"shot\":\"a\",\"model\":\"b\",\"range\":[0,30]}"), "x");
            try
            {
                Assert.IsFalse(a.HasAlpha); Assert.IsFalse(a.HasUseManual); Assert.IsFalse(a.HasEmotions);
                Assert.AreEqual(1f, a.DurationSeconds, 1e-6f);
            }
            finally { Object.DestroyImmediate(a); }
        }

        // ---------------------------------------------------------------- FC-2 との二重の戻し

        static void OnDisable(FacialCorrectionRunner r)
        {
            // 編集時は OnDisable が呼ばれない。無効化と同じ後片付けを、実際のメソッドで呼ぶ
            MethodInfo m = typeof(FacialCorrectionRunner).GetMethod("OnDisable", BindingFlags.Instance | BindingFlags.NonPublic);
            Assert.IsNotNull(m, "Runner.OnDisable が見つからない");
            m.Invoke(r, null);
        }

        // D-Drive の FC-2（ModelInstancePoolable.ResetBlendShapes）の写し: 変化のある重みだけ、控えた値へ戻す
        static void RestoreCaptured(SkinnedMeshRenderer smr, float[] captured)
        {
            for (int j = 0; j < captured.Length; j++)
                if (smr.GetBlendShapeWeight(j) != captured[j]) smr.SetBlendShapeWeight(j, captured[j]);
        }

        static float[] Capture(SkinnedMeshRenderer smr)
        {
            var w = new float[smr.sharedMesh.blendShapeCount];
            for (int j = 0; j < w.Length; j++) w[j] = smr.GetBlendShapeWeight(j);
            return w;
        }

        // 「控えた値」が 0 でない FC_ シェイプ（Prefab がそう作ってあった場合）と、アニメーションが持つ普通のシェイプを用意する
        static float[] ArrangeCaptured(FacialTestRig rig)
        {
            int iJoy = new FacialShapeIndex(rig.mesh).Find(FacialTestRig.N("Joy", 1, 1));
            int iOther = new FacialShapeIndex(rig.mesh).Find("bs.other");
            rig.smr.SetBlendShapeWeight(iJoy, 7f);
            rig.smr.SetBlendShapeWeight(iOther, 30f);
            rig.runner.SetEmotionWeight(1, 1f); // Joy を書かせる
            return Capture(rig.smr);
        }

        [Test]
        public void ResetThenRestoreThenOnDisableLeavesEveryShapeAtItsCapturedValue()
        {
            using (var rig = new FacialTestRig(true))
            {
                rig.runner.ZeroAllBoundOverride = false; // 再生中と同じ（書いた分だけ戻す）。編集時は結んだ FC_ をすべて 0 にする（E-4）
                rig.runner.useManualAngles = true;
                float[] captured = ArrangeCaptured(rig);
                rig.runner.EvaluateNow(0f, 0f, 1f);
                Assert.AreEqual(100f, rig.W(FacialTestRig.N("Joy", 1, 1)), 0.5f, "前提: Runner が書いている");

                // D-Drive の順序: 通知（ブリッジが ResetWeights）→ 重みの復元 → 無効化（OnDisable）
                rig.runner.ResetWeights();
                RestoreCaptured(rig.smr, captured);
                OnDisable(rig.runner);

                for (int j = 0; j < captured.Length; j++)
                    Assert.AreEqual(captured[j], rig.smr.GetBlendShapeWeight(j), 1e-4f, rig.mesh.GetBlendShapeName(j));
            }
        }

        [Test]
        public void WithoutTheBridgeOnDisableAfterTheRestoreWritesZeroOverACapturedFcValue()
        {
            // 既知の挙動（ブリッジが要る理由）: 復元のあとに OnDisable が「前回書いた記録」で 0 を書く。控えた値が 0 の FC_ では結果は同じ
            using (var rig = new FacialTestRig(true))
            {
                rig.runner.useManualAngles = true;
                float[] captured = ArrangeCaptured(rig);
                rig.runner.EvaluateNow(0f, 0f, 1f);
                RestoreCaptured(rig.smr, captured);
                OnDisable(rig.runner);
                Assert.AreEqual(0f, rig.W(FacialTestRig.N("Joy", 1, 1)), 1e-4f, "控えた値 7 ではなく 0 になる");
                Assert.AreEqual(30f, rig.W("bs.other"), 1e-4f, "FC_ でないシェイプには触れない");
                Assert.AreEqual(0f, rig.W(FacialTestRig.N("Neutral", 1, 1)), 1e-4f);
            }
        }

        [Test]
        public void ResetEmptiesTheBookkeepingSoALaterOnDisableWritesNothing()
        {
            using (var rig = new FacialTestRig(true))
            {
                rig.runner.ZeroAllBoundOverride = false; // 再生中と同じ（書いた分だけ戻す）。編集時は結んだ FC_ をすべて 0 にする（E-4）
                rig.runner.useManualAngles = true;
                rig.runner.EvaluateNow(0f, 0f, 1f);
                Assert.Greater(rig.runner.ActiveWeightCount, 0);
                rig.runner.ResetWeights();
                Assert.AreEqual(0, rig.runner.ActiveWeightCount);
                Assert.IsFalse(rig.runner.Snapped);
                Assert.IsFalse(rig.runner.HasValidAngles);

                // 復元後に外から値が入っても、OnDisable（2 回目の戻し）は触らない
                int iN11 = new FacialShapeIndex(rig.mesh).Find(FacialTestRig.N("Neutral", 1, 1));
                rig.smr.SetBlendShapeWeight(iN11, 42f);
                OnDisable(rig.runner);
                OnDisable(rig.runner);
                Assert.AreEqual(42f, rig.smr.GetBlendShapeWeight(iN11), 1e-4f);
            }
        }

        [Test]
        public void FirstEvaluationAfterReenableWritesCorrectWeightsAndSnaps()
        {
            using (var rig = new FacialTestRig(true))
            {
                rig.runner.useManualAngles = true;
                float[] captured = ArrangeCaptured(rig);
                rig.runner.EvaluateNow(0f, 0f, 1f);
                rig.runner.ResetWeights(); RestoreCaptured(rig.smr, captured); OnDisable(rig.runner);

                // 再び有効 → スポーン後のキャッシュの作り直し（ブリッジの OnModelSpawned）→ 最初の評価（deltaTime が小さくても目標へスナップ）
                rig.runner.RebuildCaches();
                rig.runner.EvaluateNow(0f, 0f, 0.001f);
                Assert.AreEqual(100f, rig.W(FacialTestRig.N("Neutral", 1, 1)), 0.5f, "古い記録に引きずられず、最初から正しい重み");
                Assert.AreEqual(100f, rig.W(FacialTestRig.N("Joy", 1, 1)), 0.5f);
                Assert.IsTrue(rig.runner.Snapped);

                // もう一度往復しても同じ
                rig.runner.ResetWeights(); RestoreCaptured(rig.smr, captured); OnDisable(rig.runner);
                rig.runner.RebuildCaches();
                rig.runner.EvaluateNow(0f, 0f, 0.001f);
                Assert.AreEqual(100f, rig.W(FacialTestRig.N("Neutral", 1, 1)), 0.5f);
            }
        }
    }
}
