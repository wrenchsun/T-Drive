// F5 の実行時の計算（シャープさ・コマ打ち・距離でレイヤーの重み・誇張）の Runner テスト（Unity EditMode）と、取り込み・書き出し。
// 合成キャラクター（FacialTestRig: 格子 3×3・頭 (0, 1.5, 0)・顔は +Z）を使う。
using System;
using System.Collections.Generic;
using NUnit.Framework;
using TDrive.Facial.Core;
using TDrive.Facial.Editor;
using UnityEditor;
using UnityEngine;
using Object = UnityEngine.Object;

namespace TDrive.Facial.Tests
{
    public class FacialF5Tests
    {
        const float Dt = 0.02f;
        FacialTestRig _rig;
        GameObject _viewer;
        FacialCorrectionOverrides _ov;
        readonly List<Object> _owned = new List<Object>();

        static string N(string layer, int r, int c) { return FacialTestRig.N(layer, r, c); }
        static string Ex(string layer, int r, int c) { return FacialNaming.MorphName(FacialTestRig.Asset, layer, r, c, true); }

        [SetUp]
        public void SetUp()
        {
            _rig = new FacialTestRig();
            _viewer = EditorUtility.CreateGameObjectWithHideFlags("viewer", HideFlags.HideAndDontSave);
        }

        [TearDown]
        public void TearDown()
        {
            _rig.Dispose();
            if (_viewer != null) Object.DestroyImmediate(_viewer);
            if (_ov != null) Object.DestroyImmediate(_ov);
            foreach (Object o in _owned) if (o != null) Object.DestroyImmediate(o);
            _owned.Clear();
        }

        FacialCorrectionRunner R { get { return _rig.runner; } }
        float C11 { get { return _rig.W(N("Neutral", 1, 1)); } }
        float C12 { get { return _rig.W(N("Neutral", 1, 2)); } }

        void UseOverrides()
        {
            _ov = ScriptableObject.CreateInstance<FacialCorrectionOverrides>();
            R.overrides = _ov;
        }

        void AddEx(int r, int c)
        {
            _rig.AddShape(Ex("Neutral", r, c));
        }

        /// <summary>Neutral の全点に _Ex の名前を割り当てる（メッシュに無いものは Runner が飛ばす）。</summary>
        void ListAllExNames()
        {
            var ex = new string[9];
            for (int r = 0; r < 3; r++) for (int c = 0; c < 3; c++) ex[r * 3 + c] = Ex("Neutral", r, c);
            _rig.data.layers[0].exMorphNames = ex;
        }

        // ---------------------------------------------------------------- シャープさ

        [Test]
        public void SharpnessOneKeepsTheLinearSplit()
        {
            R.EvaluateNow(22.5f, 0f, Dt); // 列の位置 1.25 → 75 / 25
            Assert.AreEqual(75f, C11, 1e-3f);
            Assert.AreEqual(25f, C12, 1e-3f);
        }

        [Test]
        public void SharpnessTwoMakesQuarterThreeQuartersTenNinety()
        {
            UseOverrides();
            _ov.overrideSharpness = true; _ov.sharpness = 2f;
            R.EvaluateNow(22.5f, 0f, Dt);
            Assert.AreEqual(90f, C11, 1e-2f);
            Assert.AreEqual(10f, C12, 1e-2f);
            Assert.AreEqual(2f, R.LastSharpness, 1e-6f);
        }

        [Test]
        public void SharpnessFromDataIsUsedWithoutOverrides()
        {
            _rig.data.quality.sharpness = 2f;
            R.EvaluateNow(22.5f, 0f, Dt);
            Assert.AreEqual(90f, C11, 1e-2f);
        }

        [Test]
        public void LargeSharpnessSnapsToTheNearestKey()
        {
            UseOverrides();
            _ov.overrideSharpness = true; _ov.sharpness = 64f;
            R.EvaluateNow(22.5f, 0f, Dt);
            Assert.AreEqual(100f, C11, 1e-2f);
            Assert.AreEqual(0f, C12, 1e-2f);
        }

        [Test]
        public void SharpnessKeepsTheExactMidpoint()
        {
            _rig.data.quality.sharpness = 4f;
            R.EvaluateNow(45f, 0f, Dt);
            Assert.AreEqual(50f, C11, 1e-2f);
            Assert.AreEqual(50f, C12, 1e-2f);
        }

        [Test]
        public void ZeroSharpnessIsTreatedAsUnset()
        {
            _rig.data.quality.sharpness = 0f; // 古いアセット・手作りのデータ
            R.EvaluateNow(22.5f, 0f, Dt);
            Assert.AreEqual(75f, C11, 1e-3f);
        }

        [Test]
        public void ChangingSharpnessInvalidatesTheReuseCache()
        {
            UseOverrides();
            R.EvaluateNow(22.5f, 0f, 0f);
            Assert.AreEqual(75f, C11, 1e-3f);
            _ov.overrideSharpness = true; _ov.sharpness = 2f;
            R.EvaluateNow(22.5f, 0f, 1f); // 同じ角度でも、シャープさが変わったら計算し直す
            Assert.AreEqual(90f, C11, 1e-2f);
        }

        // ---------------------------------------------------------------- コマ打ち

        [Test]
        public void SteppedUpdateHoldsBetweenStepsAndJumpsWithoutSmoothing()
        {
            _rig.data.quality.stepFps = 10f; // 周期 0.1 秒 = Dt 5 回
            R.EvaluateNow(0f, 0f, Dt); // 最初は必ず評価
            Assert.IsFalse(R.StepHolding);
            Assert.AreEqual(100f, C11, 1e-3f);
            for (int i = 0; i < 4; i++)
            {
                R.EvaluateNow(45f, 0f, Dt); // 角度が変わっても、周期が来るまで前の重みのまま
                Assert.IsTrue(R.StepHolding, "frame " + i);
                Assert.AreEqual(100f, C11, 1e-4f);
                Assert.AreEqual(0f, C12, 1e-4f);
            }
            R.EvaluateNow(45f, 0f, Dt); // 周期（0.1 秒）に届いた → 目標へ跳ぶ（追従は使わない）
            Assert.IsFalse(R.StepHolding);
            Assert.AreEqual(50f, C11, 1e-3f);
            Assert.AreEqual(50f, C12, 1e-3f);
            Assert.AreEqual(10f, R.LastStepFps, 1e-4f);
        }

        [Test]
        public void SteppedUpdateResumesEveryPeriod()
        {
            _rig.data.quality.stepFps = 10f;
            int evals = 0;
            for (int i = 0; i < 30; i++)
            {
                R.EvaluateNow(i % 2 == 0 ? 0f : 45f, 0f, Dt);
                if (!R.StepHolding) evals++;
            }
            Assert.AreEqual(6, evals, "0.6 秒 / 0.1 秒 = 6 回（最初の 1 回を含む）");
        }

        [Test]
        public void SnapForcesAnImmediateEvaluationAndRestartsThePeriod()
        {
            _rig.data.quality.stepFps = 10f;
            R.EvaluateNow(0f, 0f, Dt);
            R.EvaluateNow(0f, 0f, Dt);
            Assert.IsTrue(R.StepHolding);
            R.EvaluateNow(90f, 0f, Dt); // 45° を超える変化 = カット
            Assert.IsTrue(R.Snapped);
            Assert.IsFalse(R.StepHolding);
            Assert.AreEqual(100f, _rig.W(N("Neutral", 1, 2)), 1e-3f);
            for (int i = 0; i < 4; i++) { R.EvaluateNow(90f, 0f, Dt); Assert.IsTrue(R.StepHolding, "frame " + i); }
            R.EvaluateNow(90f, 0f, Dt);
            Assert.IsFalse(R.StepHolding);
        }

        [Test]
        public void StepFpsZeroEvaluatesEveryFrameWithSmoothing()
        {
            R.EvaluateNow(0f, 0f, Dt);
            R.EvaluateNow(45f, 0f, Dt);
            Assert.IsFalse(R.StepHolding);
            Assert.Greater(C11, 50f, "追従で少しずつ動く（跳ばない）");
            Assert.Less(C11, 100f);
        }

        [Test]
        public void FrameOverrideStepFpsReplacesTheData()
        {
            R.EvaluateNow(0f, 0f, Dt);
            R.PushOverride(new FacialFrameOverride { hasStepFps = true, stepFps = 10f });
            R.EvaluateNow(45f, 0f, Dt); // 評価（accum が 0 → 0.02 で保持）
            Assert.IsTrue(R.StepHolding);
            Assert.AreEqual(100f, C11, 1e-3f);
            R.PushOverride(new FacialFrameOverride { hasStepFps = true, stepFps = 0f });
            R.EvaluateNow(45f, 0f, Dt); // 0 = 毎フレーム
            Assert.IsFalse(R.StepHolding);
        }

        [Test]
        public void SteppedUpdateWritesNothingWhileHolding()
        {
            _rig.data.quality.stepFps = 10f;
            R.EvaluateNow(0f, 0f, Dt);
            int i = _rig.mesh.GetBlendShapeIndex(N("Neutral", 1, 1));
            _rig.smr.SetBlendShapeWeight(i, 33f); // 外から触っても、保持中は Runner が書き直さない
            R.EvaluateNow(0f, 0f, Dt);
            Assert.IsTrue(R.StepHolding);
            Assert.AreEqual(33f, _rig.smr.GetBlendShapeWeight(i), 1e-4f);
        }

        // ---------------------------------------------------------------- 距離でレイヤーの重み

        void SetDistanceLayer(float start, float end, float from, float to)
        {
            _rig.data.layers[1].weight = new FacialLayerWeightData
            {
                source = FacialLayerWeightSource.Distance, start = start, end = end, from = from, to = to,
            };
        }

        void ViewerInFront(float distance)
        {
            _viewer.transform.position = new Vector3(0f, 1.5f, distance); // 頭から正面へ distance
        }

        float Joy { get { return _rig.W(N("Joy", 1, 1)); } }

        [Test]
        public void DistanceLayerWeightFollowsTheViewerDistance()
        {
            SetDistanceLayer(2f, 6f, 0f, 1f);
            ViewerInFront(1f);
            R.EvaluateNow(_viewer.transform, 1f);
            Assert.AreEqual(0f, Joy, 1e-3f);
            ViewerInFront(4f);
            R.EvaluateNow(_viewer.transform, 1f);
            Assert.AreEqual(50f, Joy, 1e-2f);
            ViewerInFront(10f);
            R.EvaluateNow(_viewer.transform, 1f);
            Assert.AreEqual(100f, Joy, 1e-2f);
        }

        [Test]
        public void DistanceSourceReplacesTheRunnerEmotionWeight()
        {
            SetDistanceLayer(2f, 6f, 0f, 1f);
            R.SetEmotionWeight(1, 1f); // 距離で決めるレイヤーでは使われない
            ViewerInFront(1f);
            R.EvaluateNow(_viewer.transform, Dt);
            Assert.AreEqual(0f, Joy, 1e-3f);
        }

        [Test]
        public void ExplicitOverrideEmotionWinsOverTheDistanceWeight()
        {
            SetDistanceLayer(2f, 6f, 0f, 1f);
            ViewerInFront(1f); // 距離の重みは 0
            R.PushOverride(new FacialFrameOverride { emotionWeights = new[] { 0f, 0.25f } });
            R.EvaluateNow(_viewer.transform, Dt);
            Assert.AreEqual(25f, Joy, 1e-2f);
        }

        [Test]
        public void NaNInTheOverrideMeansNotProvidedSoTheDistanceWeightIsUsed()
        {
            SetDistanceLayer(2f, 6f, 0f, 1f);
            ViewerInFront(4f);
            R.PushOverride(new FacialFrameOverride { emotionWeights = new[] { 0f, float.NaN } });
            R.EvaluateNow(_viewer.transform, Dt);
            Assert.AreEqual(50f, Joy, 1e-2f);
        }

        [Test]
        public void DistanceLayerFallsBackToTheRunnerValueWithoutAViewer()
        {
            if (Camera.main != null) Assert.Ignore("シーンにメインカメラがあると視点として使われるので、このテストは視点なしにならない");
            SetDistanceLayer(2f, 6f, 0f, 1f);
            R.SetEmotionWeight(1, 0.5f);
            R.EvaluateNow(0f, 0f, Dt); // 角度を直接渡す・メインカメラも無い = 視点なし
            Assert.AreEqual(50f, Joy, 1e-2f);
        }

        [Test]
        public void ReversedFromToMakesNearViewersStronger()
        {
            SetDistanceLayer(2f, 6f, 1f, 0f);
            ViewerInFront(1f);
            R.EvaluateNow(_viewer.transform, 1f);
            Assert.AreEqual(100f, Joy, 1e-2f);
            ViewerInFront(8f);
            R.EvaluateNow(_viewer.transform, 1f);
            Assert.AreEqual(0f, Joy, 1e-2f);
        }

        [Test]
        public void DirectAndCurveLayersAreUnchanged()
        {
            _rig.data.layers[1].weight = new FacialLayerWeightData { source = FacialLayerWeightSource.Curve };
            R.SetEmotionWeight(1, 0.4f);
            ViewerInFront(1f);
            R.EvaluateNow(_viewer.transform, Dt);
            Assert.AreEqual(40f, Joy, 1e-2f);
        }

        // ---------------------------------------------------------------- 誇張

        [Test]
        public void ExaggerationScalesOnlyTheExShape()
        {
            AddEx(1, 1);
            ListAllExNames();
            R.EvaluateNow(0f, 0f, Dt);
            Assert.AreEqual(100f, C11, 1e-3f);
            Assert.AreEqual(100f, _rig.W(Ex("Neutral", 1, 1)), 1e-3f, "既定 1 = 作った通り");
            R.exaggeration = 0.5f;
            R.EvaluateNow(0f, 0f, 1f);
            Assert.AreEqual(100f, C11, 1e-3f);
            Assert.AreEqual(50f, _rig.W(Ex("Neutral", 1, 1)), 1e-3f);
            R.exaggeration = 0f;
            R.EvaluateNow(0f, 0f, 1f);
            Assert.AreEqual(0f, _rig.W(Ex("Neutral", 1, 1)), 1e-3f);
            Assert.AreEqual(100f, C11, 1e-3f);
        }

        [Test]
        public void ExShapeFollowsTheMatchingShapeWeight()
        {
            AddEx(1, 1);
            AddEx(1, 2);
            ListAllExNames();
            R.alpha = 0.5f;
            R.EvaluateNow(22.5f, 0f, Dt);
            Assert.AreEqual(37.5f, C11, 1e-2f);
            Assert.AreEqual(37.5f, _rig.W(Ex("Neutral", 1, 1)), 1e-2f);
            Assert.AreEqual(12.5f, _rig.W(Ex("Neutral", 1, 2)), 1e-2f);
        }

        [Test]
        public void MissingExShapesAreIgnoredAndNotReportedMissing()
        {
            ListAllExNames(); // メッシュには _Ex が 1 つも無い
            R.EvaluateNow(45f, 0f, Dt);
            Assert.AreEqual(50f, C11, 1e-3f);
            foreach (string m in R.GetMissingShapeNames()) StringAssert.DoesNotContain("_Ex", m);
            for (int i = 0; i < _rig.mesh.blendShapeCount; i++)
                Assert.IsFalse(_rig.mesh.GetBlendShapeName(i).EndsWith("_Ex", StringComparison.Ordinal));
        }

        [Test]
        public void ExaggerationFromOverridesAndFrameOverrideMultiply()
        {
            AddEx(1, 1);
            ListAllExNames();
            UseOverrides();
            _ov.overrideExaggeration = true; _ov.exaggeration = 0.5f;
            R.PushOverride(new FacialFrameOverride { hasExaggeration = true, exaggeration = 0.5f });
            R.EvaluateNow(0f, 0f, Dt);
            Assert.AreEqual(25f, _rig.W(Ex("Neutral", 1, 1)), 1e-3f);
            Assert.AreEqual(0.25f, R.LastExaggeration, 1e-6f);
        }

        [Test]
        public void ExaggerationFromDataIsUsedOnlyWhenFlagged()
        {
            AddEx(1, 1);
            ListAllExNames();
            _rig.data.quality.exaggeration = 0.2f; // フラグが無いときは使わない（古いアセットは 1 のまま）
            R.EvaluateNow(0f, 0f, Dt);
            Assert.AreEqual(100f, _rig.W(Ex("Neutral", 1, 1)), 1e-3f);
            _rig.data.quality.hasExaggeration = true;
            R.EvaluateNow(0f, 0f, 1f);
            Assert.AreEqual(20f, _rig.W(Ex("Neutral", 1, 1)), 1e-3f);
        }

        [Test]
        public void ResetWeightsClearsExShapes()
        {
            AddEx(1, 1);
            ListAllExNames();
            R.EvaluateNow(0f, 0f, Dt);
            R.ResetWeights();
            Assert.AreEqual(0f, _rig.FcSum(), 1e-6f);
        }

        // ---------------------------------------------------------------- 割り当て

        [Test]
        public void SteadyStateWithAllF5FeaturesDoesNotAllocate()
        {
            AddEx(1, 1);
            AddEx(1, 2);
            ListAllExNames();
            SetDistanceLayer(2f, 6f, 0f, 1f);
            UseOverrides();
            _ov.overrideSharpness = true; _ov.sharpness = 2f;
            _ov.overrideStepFps = true; _ov.stepFps = 30f;
            R.exaggeration = 0.7f;
            var emo = new[] { 0f, float.NaN };
            for (int i = 0; i < 40; i++)
            {
                _viewer.transform.position = new Vector3(Mathf.Sin(i * 0.3f) * 3f, 1.5f, Mathf.Cos(i * 0.3f) * 3f);
                R.PushOverride(new FacialFrameOverride { emotionWeights = emo, hasExaggeration = true, exaggeration = 0.9f });
                R.EvaluateNow(_viewer.transform, Dt);
            }
            AllocProbe.AssertNoAlloc(() =>
            {
                for (int i = 0; i < 300; i++)
                {
                    _viewer.transform.position = new Vector3(Mathf.Sin(i * 0.1f) * 3f, 1.5f + Mathf.Sin(i * 0.05f), Mathf.Cos(i * 0.1f) * 3f);
                    R.PushOverride(new FacialFrameOverride { emotionWeights = emo, hasExaggeration = true, exaggeration = 0.9f });
                    R.EvaluateNow(_viewer.transform, Dt);
                }
            }, "F5 の機能を使った 2 回目以降の評価でマネージドの割り当てがある");
        }

        // ---------------------------------------------------------------- 取り込み・書き出し

        const string ImportJson = "{\"format\":\"FacialCorrection\",\"version\":1,"
            + "\"meta\":{\"unit\":\"cm\",\"upAxis\":\"Y\",\"handedness\":\"right\"},"
            + "\"grid\":{\"yawRange\":90,\"pitchRange\":45,\"cols\":3,\"rows\":3,\"baseBone\":\"head\",\"forwardAxis\":\"+Z\",\"centerOffset\":[0,0,0],\"edgeFade\":15},"
            + "\"layers\":[{\"name\":\"Neutral\",\"points\":[{\"row\":1,\"col\":1,\"isKey\":true,\"curves\":{}}]},"
            + "{\"name\":\"Joy\",\"points\":[{\"row\":1,\"col\":1,\"isKey\":true,\"curves\":{}}]}],\"asset\":\"test\","
            + "\"quality\":{\"sharpness\":2,\"stepFps\":12,\"exaggeration\":0.5},"
            + "\"layerWeights\":{\"Joy\":{\"source\":\"distance\",\"start\":100,\"end\":300,\"from\":0.2,\"to\":1}}}";

        [Test]
        public void ImporterFillsExNamesQualityAndDistanceLayerInMetres()
        {
            FacialCorrectionData d = FcposeConverter.BuildData(ImportJson, "x", null);
            _owned.Add(d);
            Assert.AreEqual(2f, d.quality.sharpness);
            Assert.AreEqual(12f, d.quality.stepFps);
            Assert.IsTrue(d.quality.hasExaggeration);
            Assert.AreEqual(0.5f, d.quality.exaggeration);
            Assert.AreEqual(9, d.layers[0].exMorphNames.Length);
            Assert.AreEqual(Ex("Neutral", 1, 1), d.layers[0].exMorphNames[4]);
            Assert.AreEqual("", d.layers[0].exMorphNames[0], "作っていない点は空");
            Assert.AreEqual(FacialLayerWeightSource.Direct, d.layers[0].weight.source);
            FacialLayerWeightData w = d.layers[1].weight;
            Assert.AreEqual(FacialLayerWeightSource.Distance, w.source);
            Assert.AreEqual(1f, w.start, 1e-5f, "cm → m");
            Assert.AreEqual(3f, w.end, 1e-5f);
            Assert.AreEqual(0.2f, w.from, 1e-6f);
            Assert.AreEqual(1f, w.to, 1e-6f);
            Assert.AreEqual(0.5f, FacialCorrectionOverrides.Resolve(d, null).exaggeration, 1e-6f);
        }

        [Test]
        public void MayaExportWritesExaggerationOnlyWhenOverridden()
        {
            FacialCorrectionData d = FcposeConverter.BuildData(ImportJson, "x", null);
            _owned.Add(d);
            var root = (Dictionary<string, object>)MiniJson.Parse(FacialMayaExport.BuildJson(d, null));
            CollectionAssert.AreEquivalent(new[] { "sharpness", "stepFps", "angleEpsilon", "maxLod" }, ((Dictionary<string, object>)root["quality"]).Keys);
            UseOverrides();
            _ov.overrideExaggeration = true; _ov.exaggeration = 0.3f;
            _ov.overrideStepFps = true; _ov.stepFps = 24f;
            root = (Dictionary<string, object>)MiniJson.Parse(FacialMayaExport.BuildJson(d, _ov));
            var q = (Dictionary<string, object>)root["quality"];
            Assert.AreEqual(0.3, Convert.ToDouble(q["exaggeration"]), 1e-6);
            Assert.AreEqual(24.0, Convert.ToDouble(q["stepFps"]), 1e-6);
        }
    }
}
