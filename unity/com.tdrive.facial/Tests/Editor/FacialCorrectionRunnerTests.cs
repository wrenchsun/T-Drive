// FacialCorrectionRunner のテスト（Unity EditMode）。メッシュ・階層・データはすべてテスト内で作り、TearDown で破棄する。
// LateUpdate は EditMode では呼ばれないので EvaluateNow を使う。アセットはディスクへ作らない。
// ゲームオブジェクトはプレビューシーン（NewPreviewScene）に作る = ユーザーの開いているシーンを汚さない（docs/19 §6）。
using System;
using System.Collections.Generic;
using System.Reflection;
using NUnit.Framework;
using TDrive.Facial.Core;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using Object = UnityEngine.Object;

namespace TDrive.Facial.Tests
{
    public class FacialCorrectionRunnerTests
    {
        const float Dt = 0.02f;
        const string Asset = "test";
        const string Missing = "FC_test_Neutral_R2_C2"; // データにあるがメッシュに作らない

        GameObject _root, _head, _face, _viewer;
        Mesh _mesh;
        SkinnedMeshRenderer _smr;
        FacialCorrectionData _data;
        FacialCorrectionOverrides _overrides;
        FacialCorrectionRunner _runner;
        readonly List<Object> _extra = new List<Object>();

        static string N(string layer, int r, int c) { return FacialNaming.MorphName(Asset, layer, r, c); }

        [SetUp]
        public void SetUp()
        {
            _scene = EditorSceneManager.NewPreviewScene();
            // メッシュ: 3 頂点 + ブレンドシェイプ（Neutral の 3×3 から 1 個欠け・Joy の中央・通常シェイプ 2 つ）
            _mesh = new Mesh { name = "FacialTestMesh" };
            _mesh.vertices = new[] { Vector3.zero, Vector3.right, Vector3.up };
            _mesh.triangles = new[] { 0, 1, 2 };
            for (int r = 0; r < 3; r++)
                for (int c = 0; c < 3; c++)
                {
                    string n = N("Neutral", r, c);
                    if (n != Missing) AddShape(n);
                }
            AddShape(N("Joy", 1, 1));
            AddShape("bs.jaw");
            AddShape("bs.other");

            _root = NewGo("FacialTestRoot");
            _head = NewGo("head");
            _head.transform.SetParent(_root.transform, false);
            _head.transform.position = new Vector3(0f, 1.5f, 0f); // 顔は +Z を向く（回転なし）
            _face = NewGo("face");
            _face.transform.SetParent(_root.transform, false);
            _smr = _face.AddComponent<SkinnedMeshRenderer>();
            _smr.sharedMesh = _mesh;
            _viewer = NewGo("viewer");

            _data = ScriptableObject.CreateInstance<FacialCorrectionData>();
            _data.assetName = Asset;
            _data.grid = new FacialGridData
            {
                yawRange = 90f, pitchRange = 45f, cols = 3, rows = 3, edgeFade = 15f,
                baseBone = "head", forwardAxis = "+Z", centerOffset = Vector3.zero,
            };
            _data.policy = new FacialPolicyData
            {
                expressionDampen = 0.5f, interpSpeed = 10f, snapAngle = 45f, fadeStart = 0f, fadeEnd = 0f, globalAlpha = 1f,
            };
            _data.quality = new FacialQualityData { angleEpsilon = 0.1f, maxLod = 0, sharpness = 1f, stepFps = 0f };
            var neutral = new string[9];
            for (int r = 0; r < 3; r++) for (int c = 0; c < 3; c++) neutral[r * 3 + c] = N("Neutral", r, c);
            var joy = new string[9];
            for (int i = 0; i < 9; i++) joy[i] = "";
            joy[1 * 3 + 1] = N("Joy", 1, 1);
            _data.layers = new[]
            {
                new FacialLayerData { name = "Neutral", emotionCurve = "", enabled = true, morphNames = neutral },
                new FacialLayerData { name = "Joy", emotionCurve = "emo_joy", enabled = true, morphNames = joy },
            };
            _data.intensityCurves = new[] { "bs.jaw" };

            _runner = _root.AddComponent<FacialCorrectionRunner>();
            _runner.data = _data;
        }

        Scene _scene;

        // 普通のゲームオブジェクトを、ユーザーのシーンではなくプレビューシーンに作る
        GameObject NewGo(string name)
        {
            var go = new GameObject(name);
            SceneManager.MoveGameObjectToScene(go, _scene);
            return go;
        }

        [TearDown]
        public void TearDown()
        {
            if (_runner != null) _runner.ResetWeights();
            if (_root != null) Object.DestroyImmediate(_root);
            if (_viewer != null) Object.DestroyImmediate(_viewer);
            if (_mesh != null) Object.DestroyImmediate(_mesh);
            if (_data != null) Object.DestroyImmediate(_data);
            if (_overrides != null) Object.DestroyImmediate(_overrides);
            foreach (Object o in _extra) if (o != null) Object.DestroyImmediate(o);
            _extra.Clear();
            if (_scene.IsValid()) EditorSceneManager.ClosePreviewScene(_scene);
        }

        void AddShape(string name)
        {
            _mesh.AddBlendShapeFrame(name, 100f, new[] { Vector3.up * 0.01f, Vector3.zero, Vector3.zero },
                new Vector3[3], new Vector3[3]);
        }

        float W(string name)
        {
            int i = _mesh.GetBlendShapeIndex(name);
            Assert.GreaterOrEqual(i, 0, "メッシュにシェイプが無い: " + name);
            return _smr.GetBlendShapeWeight(i);
        }

        /// <summary>FC_ シェイプのうち、期待に入っていないものは 0 であること。</summary>
        void AssertFcWeights(Dictionary<string, float> expected, float tol = 1e-3f)
        {
            for (int i = 0; i < _mesh.blendShapeCount; i++)
            {
                string n = _mesh.GetBlendShapeName(i);
                if (!n.StartsWith("FC_", StringComparison.Ordinal)) continue;
                float e;
                if (!expected.TryGetValue(n, out e)) e = 0f;
                Assert.AreEqual(e, _smr.GetBlendShapeWeight(i), tol, n);
            }
        }

        void ViewerAt(Vector3 p) { _viewer.transform.position = p; }

        // ---------------------------------------------------------------- 格子の重み

        [Test]
        public void ExactCornerWritesWeight100ToOneShape()
        {
            ViewerAt(new Vector3(-2f, 1.5f, 0f)); // キャラクターの左（-X）= Yaw +90
            _runner.EvaluateNow(_viewer.transform, Dt);
            Assert.AreEqual(90f, _runner.CurrentYaw, 1e-3f);
            Assert.AreEqual(0f, _runner.CurrentPitch, 1e-3f);
            Assert.IsTrue(_runner.Snapped, "最初の評価はスナップ");
            AssertFcWeights(new Dictionary<string, float> { { N("Neutral", 1, 2), 100f } });
        }

        [Test]
        public void FrontViewIsCenterShape()
        {
            ViewerAt(new Vector3(0f, 1.5f, 3f));
            _runner.EvaluateNow(_viewer.transform, Dt);
            AssertFcWeights(new Dictionary<string, float> { { N("Neutral", 1, 1), 100f } });
        }

        [Test]
        public void MidpointSplitsFiftyFifty()
        {
            _runner.EvaluateNow(45f, 0f, Dt);
            AssertFcWeights(new Dictionary<string, float> { { N("Neutral", 1, 1), 50f }, { N("Neutral", 1, 2), 50f } });
        }

        [Test]
        public void EmotionLayerAddsLayerWeight()
        {
            _runner.SetEmotionWeight(1, 0.5f);
            _runner.EvaluateNow(0f, 0f, Dt);
            AssertFcWeights(new Dictionary<string, float> { { N("Neutral", 1, 1), 100f }, { N("Joy", 1, 1), 50f } });
        }

        [Test]
        public void EmotionWeightIsClampedAtZeroAndLayerZeroIsIgnored()
        {
            _runner.emotionWeights = new[] { 0.7f, -1f };
            _runner.EvaluateNow(0f, 0f, Dt);
            AssertFcWeights(new Dictionary<string, float> { { N("Neutral", 1, 1), 100f } });
        }

        [Test]
        public void MutedLayerIsRemoved()
        {
            _runner.SetEmotionWeight(1, 1f);
            _runner.SetLayerMuted(1, true);
            _runner.EvaluateNow(0f, 0f, Dt);
            AssertFcWeights(new Dictionary<string, float> { { N("Neutral", 1, 1), 100f } });
            Assert.AreEqual(0f, W(N("Joy", 1, 1)));
        }

        [Test]
        public void DisabledLayerIsSkipped()
        {
            _data.layers[1].enabled = false;
            _runner.SetEmotionWeight(1, 1f);
            _runner.EvaluateNow(0f, 0f, Dt);
            Assert.AreEqual(0f, W(N("Joy", 1, 1)));
        }

        // ---------------------------------------------------------------- 全体に掛ける倍率

        [Test]
        public void AlphaScalesWeights()
        {
            _runner.alpha = 0.5f;
            _runner.EvaluateNow(0f, 0f, Dt);
            Assert.AreEqual(50f, W(N("Neutral", 1, 1)), 1e-3f);
        }

        [Test]
        public void ExpressionDampenScalesByIntensityShapes()
        {
            // bs.jaw = 100（= 1.0）、expressionDampen 0.5 → 1 - 0.5 × 1 = 0.5
            _smr.SetBlendShapeWeight(_mesh.GetBlendShapeIndex("bs.jaw"), 100f);
            _runner.EvaluateNow(0f, 0f, Dt);
            Assert.AreEqual(50f, W(N("Neutral", 1, 1)), 1e-3f);
            Assert.AreEqual(100f, W("bs.jaw"), "通常のシェイプには触らない");

            // bs.jaw = 50 → s = 0.5 → 1 - 0.5 × 0.5 = 0.75（interpSpeed 0 で即時）
            _data.policy.interpSpeed = 0f;
            _smr.SetBlendShapeWeight(_mesh.GetBlendShapeIndex("bs.jaw"), 50f);
            _runner.EvaluateNow(0f, 0f, Dt);
            Assert.AreEqual(75f, W(N("Neutral", 1, 1)), 1e-3f);
            Assert.AreEqual(0.75f, _runner.LastScale, 1e-6f);
        }

        [Test]
        public void IntensityIsSumOfAbsoluteValuesClamped()
        {
            _data.intensityCurves = new[] { "bs.jaw", "bs.other" };
            _runner.RebuildCaches();
            _smr.SetBlendShapeWeight(_mesh.GetBlendShapeIndex("bs.jaw"), 80f);
            _smr.SetBlendShapeWeight(_mesh.GetBlendShapeIndex("bs.other"), 80f);
            _runner.EvaluateNow(0f, 0f, Dt);
            Assert.AreEqual(50f, W(N("Neutral", 1, 1)), 1e-3f, "合計 1.6 は 1 に丸める");
        }

        [Test]
        public void DistanceFadeScalesByViewerDistance()
        {
            _data.policy.fadeStart = 2f;
            _data.policy.fadeEnd = 4f;
            ViewerAt(new Vector3(0f, 1.5f, 3f)); // 基準ボーンから 3 m → フェード 0.5
            _runner.EvaluateNow(_viewer.transform, Dt);
            Assert.AreEqual(50f, W(N("Neutral", 1, 1)), 1e-3f);

            ViewerAt(new Vector3(0f, 1.5f, 5f)); // 遠い → 0（スムージングで 0 へ向かう。スナップ判定は角度が同じなので無し）
            _runner.EvaluateNow(_viewer.transform, 100f);
            Assert.AreEqual(0f, W(N("Neutral", 1, 1)), 1e-3f);
        }

        [Test]
        public void OutsideTheGridFadesOutAcrossEdgeFade()
        {
            _runner.EvaluateNow(105f, 0f, Dt); // 範囲 90 + 端のフェード 15 のちょうど外 → 0
            AssertFcWeights(new Dictionary<string, float>());
            _data.policy.interpSpeed = 0f;
            _runner.EvaluateNow(97.5f, 0f, Dt); // フェードの半分
            Assert.AreEqual(50f, W(N("Neutral", 1, 2)), 1e-3f);
        }

        // ---------------------------------------------------------------- 可動域・スナップ・スムージング

        [Test]
        public void LimitsClampOutput()
        {
            _data.limits = new[] { new FacialLimitEntry { name = N("Neutral", 1, 1), min = 0f, max = 0.4f } };
            _runner.RebuildCaches();
            _runner.EvaluateNow(0f, 0f, Dt);
            Assert.AreEqual(40f, W(N("Neutral", 1, 1)), 1e-3f);
        }

        [Test]
        public void SnapAndSmoothingMatchCoreSmoothWeightsSeries()
        {
            var grid = new GridShape(90, 45, 3, 3, 15);
            var layers = new List<LayerEvalInput>();
            foreach (FacialLayerData l in _data.layers) layers.Add(new LayerEvalInput(l.morphNames, 0.0, true));
            var steps = new[]
            {
                new Vector2(0, 0),   // 最初 = スナップ
                new Vector2(30, 0),  // 小さな変化 = スムージング
                new Vector2(30, 0),  // 同じ角度（前回の格子の結果を使い回す）
                new Vector2(30, 10),
                new Vector2(80, 0),  // 50° 飛ぶ = スナップ（即時）
                new Vector2(80, 0),
                new Vector2(-20, -30),
            };
            var prev = new List<MorphWeight>();
            var target = new List<MorphWeight>();
            var next = new List<MorphWeight>();
            bool hasPrev = false;
            double py = 0, pp = 0;
            for (int s = 0; s < steps.Length; s++)
            {
                double y = steps[s].x, p = steps[s].y;
                FacialCore.EvaluateCorrection(grid, layers, y, p, target);
                bool snap = FacialCore.ShouldSnap(hasPrev, py, pp, y, p, 45.0);
                FacialCore.SmoothWeights(prev, target, Dt, 10.0, snap, next);
                hasPrev = true; py = y; pp = p;
                var t = prev; prev = next; next = t;

                _runner.EvaluateNow((float)y, (float)p, Dt);
                Assert.AreEqual(snap, _runner.Snapped, "step " + s);
                for (int i = 0; i < _mesh.blendShapeCount; i++)
                {
                    string n = _mesh.GetBlendShapeName(i);
                    if (!n.StartsWith("FC_", StringComparison.Ordinal)) continue;
                    double e = 0;
                    foreach (MorphWeight m in prev) if (m.MorphName == n) { e = m.Weight; break; }
                    Assert.AreEqual(e * 100.0, _smr.GetBlendShapeWeight(i), 2e-3, "step " + s + " " + n);
                }
            }
        }

        [Test]
        public void SnapIsInstantAndInterpSpeedZeroIsInstant()
        {
            _runner.EvaluateNow(0f, 0f, Dt);
            _runner.EvaluateNow(30f, 0f, Dt); // スムージング: まだ 100 の一部が残る
            Assert.IsFalse(_runner.Snapped);
            Assert.Greater(W(N("Neutral", 1, 1)), 70f);
            Assert.Less(W(N("Neutral", 1, 2)), 30f);

            _runner.EvaluateNow(85f, 0f, Dt); // 55° の飛び = スナップ = 即時
            Assert.IsTrue(_runner.Snapped);
            float c2 = W(N("Neutral", 1, 2));
            Assert.AreEqual(((85f / 90f + 1f) / 2f * 2f - 1f) * 100f, c2, 1e-2f);

            _data.policy.interpSpeed = 0f;
            _runner.EvaluateNow(0f, 0f, Dt);
            Assert.AreEqual(100f, W(N("Neutral", 1, 1)), 1e-3f);
            Assert.AreEqual(0f, W(N("Neutral", 1, 2)), 1e-3f);
        }

        // ---------------------------------------------------------------- フェイルソフト・リセット

        [Test]
        public void MissingShapeNamesAreSkippedAndReported()
        {
            CollectionAssert.AreEqual(new[] { Missing }, new List<string>(_runner.GetMissingShapeNames()));
            _runner.EvaluateNow(90f, 45f, Dt); // 角（2, 2）= 欠けたシェイプ
            AssertFcWeights(new Dictionary<string, float>());
            Assert.AreEqual(0, _runner.ActiveWeightCount);
            // 欠けていない隣は普通に動く
            _data.policy.interpSpeed = 0f;
            _runner.EvaluateNow(90f, 0f, Dt);
            Assert.AreEqual(100f, W(N("Neutral", 1, 2)), 1e-3f);
        }

        [Test]
        public void ResetWeightsZeroesOnlyFcShapes()
        {
            _smr.SetBlendShapeWeight(_mesh.GetBlendShapeIndex("bs.other"), 33f);
            _smr.SetBlendShapeWeight(_mesh.GetBlendShapeIndex("bs.jaw"), 12f);
            _runner.SetEmotionWeight(1, 1f);
            _runner.EvaluateNow(30f, 0f, Dt);
            Assert.Greater(W(N("Neutral", 1, 1)), 0f);
            Assert.Greater(_runner.ActiveWeightCount, 0);

            _runner.ResetWeights();
            AssertFcWeights(new Dictionary<string, float>());
            Assert.AreEqual(33f, W("bs.other"));
            Assert.AreEqual(12f, W("bs.jaw"));
            Assert.AreEqual(0, _runner.ActiveWeightCount);
        }

        [Test]
        public void OnDisableResetsWrittenShapes()
        {
            _smr.SetBlendShapeWeight(_mesh.GetBlendShapeIndex("bs.other"), 33f);
            _runner.EvaluateNow(0f, 0f, Dt);
            Assert.AreEqual(100f, W(N("Neutral", 1, 1)), 1e-3f);
            // EditMode では OnDisable が自動で呼ばれないので、非公開のメッセージ関数を直接呼ぶ
            MethodInfo m = typeof(FacialCorrectionRunner).GetMethod("OnDisable", BindingFlags.Instance | BindingFlags.NonPublic);
            Assert.NotNull(m);
            m.Invoke(_runner, null);
            AssertFcWeights(new Dictionary<string, float>());
            Assert.AreEqual(33f, W("bs.other"));
        }

        [Test]
        public void UnassigningDataResetsWrittenShapes()
        {
            _runner.EvaluateNow(0f, 0f, Dt);
            Assert.AreEqual(100f, W(N("Neutral", 1, 1)), 1e-3f);
            _runner.data = null;
            _runner.EvaluateNow(0f, 0f, Dt);
            AssertFcWeights(new Dictionary<string, float>());
        }

        [Test]
        public void ShapesWrittenLastFrameButAbsentNowAreZeroed()
        {
            _data.policy.interpSpeed = 0f;
            _runner.EvaluateNow(0f, 0f, Dt);
            Assert.AreEqual(100f, W(N("Neutral", 1, 1)), 1e-3f);
            _runner.EvaluateNow(0f, 45f, Dt); // 行 2 へ。前のフレームで書いた中央（行 1）は今回の対象から消え、0 に戻る
            Assert.AreEqual(0f, W(N("Neutral", 1, 1)), 1e-3f);
            Assert.AreEqual(100f, W(N("Neutral", 2, 1)), 1e-3f);
        }

        [Test]
        public void NoViewerAndNoCameraDoesNothing()
        {
            if (Camera.main != null) Assert.Ignore("シーンにメインカメラがあるのでこのテストはできない");
            _runner.EvaluateNow(0f, 0f, Dt);
            Assert.AreEqual(100f, W(N("Neutral", 1, 1)), 1e-3f);
            _runner.EvaluateNow((Transform)null, Dt); // 視点が解決できない → 前回のまま
            Assert.AreEqual(100f, W(N("Neutral", 1, 1)), 1e-3f);
        }

        [Test]
        public void MissingBaseBoneDoesNothingFromViewerButManualAnglesWork()
        {
            _runner.baseBone = null;
            _head.name = "not_head";
            _runner.RebuildCaches();
            Assert.IsNull(_runner.ResolvedBaseBone);
            ViewerAt(new Vector3(-2f, 1.5f, 0f));
            UnityEngine.TestTools.LogAssert.Expect(LogType.Warning, new System.Text.RegularExpressions.Regex("基準ボーン"));
            _runner.EvaluateNow(_viewer.transform, Dt);
            AssertFcWeights(new Dictionary<string, float>());
            _runner.EvaluateNow(0f, 0f, Dt);
            Assert.AreEqual(100f, W(N("Neutral", 1, 1)), 1e-3f);
        }

        // ---------------------------------------------------------------- 対象・基準ボーンの解決

        [Test]
        public void AutoFindsTargetsAndBaseBoneFromChildren()
        {
            Assert.AreEqual(1, _runner.ResolvedTargets.Count);
            Assert.AreSame(_smr, _runner.ResolvedTargets[0]);
            Assert.AreSame(_head.transform, _runner.ResolvedBaseBone);
            Assert.AreEqual(9, _runner.BoundShapeCount, "Neutral 8 + Joy 1（欠けた 1 は含めない）");
        }

        [Test]
        public void MeshWithoutAssetShapesIsNotAutoTarget()
        {
            _data.assetName = "someone_else";
            _runner.RebuildCaches();
            Assert.AreEqual(0, _runner.ResolvedTargets.Count);
            _runner.EvaluateNow(0f, 0f, Dt); // 何もしない
            AssertFcWeights(new Dictionary<string, float>());
        }

        [Test]
        public void ExplicitTargetsAndBaseBoneAreUsed()
        {
            var otherGo = NewGo("other");
            _extra.Add(otherGo);
            otherGo.transform.SetParent(_root.transform, false);
            var other = otherGo.AddComponent<SkinnedMeshRenderer>();
            other.sharedMesh = _mesh;
            _runner.targets = new[] { other };
            _runner.baseBone = otherGo.transform;
            _runner.EvaluateNow(0f, 0f, Dt);
            Assert.AreSame(other, _runner.ResolvedTargets[0]);
            Assert.AreEqual(100f, other.GetBlendShapeWeight(_mesh.GetBlendShapeIndex(N("Neutral", 1, 1))), 1e-3f);
            Assert.AreEqual(0f, W(N("Neutral", 1, 1)), "指定していないメッシュには書かない");
        }

        [Test]
        public void ExtraMeshesGetTheSameWeights()
        {
            var otherGo = NewGo("eyelash");
            _extra.Add(otherGo);
            otherGo.transform.SetParent(_root.transform, false);
            var other = otherGo.AddComponent<SkinnedMeshRenderer>();
            other.sharedMesh = _mesh;
            _runner.targets = new[] { _smr, other };
            _runner.EvaluateNow(45f, 0f, Dt);
            int i = _mesh.GetBlendShapeIndex(N("Neutral", 1, 2));
            Assert.AreEqual(50f, _smr.GetBlendShapeWeight(i), 1e-3f);
            Assert.AreEqual(50f, other.GetBlendShapeWeight(i), 1e-3f);
        }

        // ---------------------------------------------------------------- 上書き（Overrides / PushOverride）

        [Test]
        public void OverridesAssetChangesEffectiveValues()
        {
            FacialEffectiveParams p0 = FacialCorrectionOverrides.Resolve(_data, null);
            Assert.AreEqual(1f, p0.globalAlpha);
            Assert.AreEqual(10f, p0.interpSpeed);
            Assert.AreEqual(45f, p0.snapAngle);
            Assert.AreEqual(0.5f, p0.expressionDampen);
            Assert.AreEqual(15f, p0.edgeFade);

            _overrides = ScriptableObject.CreateInstance<FacialCorrectionOverrides>();
            // フラグが false の間は値を入れてもデータのまま
            _overrides.globalAlpha = 0.25f;
            _overrides.interpSpeed = 99f;
            Assert.AreEqual(1f, FacialCorrectionOverrides.Resolve(_data, _overrides).globalAlpha);
            Assert.AreEqual(10f, FacialCorrectionOverrides.Resolve(_data, _overrides).interpSpeed);

            _overrides.overrideGlobalAlpha = true;
            _overrides.overrideInterpSpeed = true;
            _overrides.overrideSnapAngle = true; _overrides.snapAngle = 10f;
            _overrides.overrideFadeStart = true; _overrides.fadeStart = 1f;
            _overrides.overrideFadeEnd = true; _overrides.fadeEnd = 2f;
            _overrides.overrideExpressionDampen = true; _overrides.expressionDampen = 0f;
            _overrides.overrideEdgeFade = true; _overrides.edgeFade = 0f;
            _overrides.overrideSharpness = true; _overrides.sharpness = 2f;
            _overrides.overrideStepFps = true; _overrides.stepFps = 12f;
            FacialEffectiveParams p = FacialCorrectionOverrides.Resolve(_data, _overrides);
            Assert.AreEqual(0.25f, p.globalAlpha);
            Assert.AreEqual(99f, p.interpSpeed);
            Assert.AreEqual(10f, p.snapAngle);
            Assert.AreEqual(1f, p.fadeStart);
            Assert.AreEqual(2f, p.fadeEnd);
            Assert.AreEqual(0f, p.expressionDampen);
            Assert.AreEqual(0f, p.edgeFade);
            Assert.AreEqual(2f, p.sharpness);
            Assert.AreEqual(12f, p.stepFps);

            // データそのものは書き換わらない
            Assert.AreEqual(1f, _data.policy.globalAlpha);
            Assert.AreEqual(10f, _data.policy.interpSpeed);
        }

        [Test]
        public void OverridesAreAppliedByRunnerAndDataIsUntouched()
        {
            _overrides = ScriptableObject.CreateInstance<FacialCorrectionOverrides>();
            _overrides.overrideGlobalAlpha = true;
            _overrides.globalAlpha = 0.25f;
            _runner.overrides = _overrides;
            _runner.EvaluateNow(0f, 0f, Dt);
            Assert.AreEqual(25f, W(N("Neutral", 1, 1)), 1e-3f);
            Assert.AreEqual(1f, _data.policy.globalAlpha);

            _overrides.overrideGlobalAlpha = false;
            _data.policy.interpSpeed = 0f;
            _runner.EvaluateNow(0f, 0f, Dt);
            Assert.AreEqual(100f, W(N("Neutral", 1, 1)), 1e-3f);
        }

        [Test]
        public void OverridesEdgeFadeChangesTheFadeWidth()
        {
            _data.policy.interpSpeed = 0f;
            _overrides = ScriptableObject.CreateInstance<FacialCorrectionOverrides>();
            _runner.overrides = _overrides;
            _runner.EvaluateNow(95f, 0f, Dt);
            float withDefault = W(N("Neutral", 1, 2)); // 15 度で減衰: 1 - 5/15
            Assert.AreEqual(66.666f, withDefault, 0.01f);
            _overrides.overrideEdgeFade = true;
            _overrides.edgeFade = 0f;
            _runner.EvaluateNow(95f, 0f, Dt);
            Assert.AreEqual(0f, W(N("Neutral", 1, 2)), 1e-3f, "端のフェード 0 = 範囲外は即 0（前回の格子の結果を使い回さない）");
        }

        [Test]
        public void PushOverrideIsConsumedOnce()
        {
            _data.policy.interpSpeed = 0f;
            ViewerAt(new Vector3(0f, 1.5f, 3f)); // 正面
            _runner.PushOverride(new FacialFrameOverride
            {
                hasAlpha = true, alpha = 0.5f,
                hasManualAngles = true, yaw = 90f, pitch = 0f,
                emotionWeights = new[] { 0f, 1f },
            });
            _runner.EvaluateNow(_viewer.transform, Dt);
            Assert.AreEqual(90f, _runner.CurrentYaw, 1e-4f);
            Assert.AreEqual(50f, W(N("Neutral", 1, 2)), 1e-3f, "alpha 0.5 が掛かる");
            Assert.AreEqual(0f, W(N("Joy", 1, 1)), 1e-3f, "Joy の点は (1,1)。Yaw 90 では当たらない");

            // 2 回目は上書きが消えていて、視点（正面）で計算される
            _runner.EvaluateNow(_viewer.transform, Dt);
            Assert.AreEqual(0f, _runner.CurrentYaw, 1e-3f);
            Assert.AreEqual(100f, W(N("Neutral", 1, 1)), 1e-3f);
            Assert.AreEqual(0f, W(N("Joy", 1, 1)), 1e-3f, "感情の重みの上書きも消えた（コンポーネントの値は 0）");
            Assert.AreEqual(0f, W(N("Neutral", 1, 2)), 1e-3f);
        }

        [Test]
        public void PushOverrideEmotionAndViewer()
        {
            _data.policy.interpSpeed = 0f;
            var other = NewGo("otherViewer");
            _extra.Add(other);
            other.transform.position = new Vector3(-2f, 1.5f, 0f);
            ViewerAt(new Vector3(0f, 1.5f, 3f));
            _runner.PushOverride(new FacialFrameOverride { emotionWeights = new[] { 0f, 0.5f }, viewer = other.transform });
            _runner.EvaluateNow(_viewer.transform, Dt);
            Assert.AreEqual(90f, _runner.CurrentYaw, 1e-3f, "押し込んだ視点が優先");
            Assert.AreEqual(100f, W(N("Neutral", 1, 2)), 1e-3f);
        }

        [Test]
        public void ManualAnglesBeatViewer()
        {
            ViewerAt(new Vector3(0f, 1.5f, 3f));
            _runner.useManualAngles = true;
            _runner.manualYaw = 90f;
            _runner.manualPitch = 0f;
            _runner.EvaluateNow(_viewer.transform, Dt);
            Assert.AreEqual(100f, W(N("Neutral", 1, 2)), 1e-3f);
        }

        [Test]
        public void ViewerOverrideBeatsEvaluateNowViewer()
        {
            var side = NewGo("side");
            _extra.Add(side);
            side.transform.position = new Vector3(-2f, 1.5f, 0f);
            ViewerAt(new Vector3(0f, 1.5f, 3f));
            _runner.viewerOverride = side.transform;
            _runner.EvaluateNow(_viewer.transform, Dt);
            Assert.AreEqual(90f, _runner.CurrentYaw, 1e-3f);
        }

        // ---------------------------------------------------------------- 角度の計算（共通のテストデータの bone ケース）

        public static IEnumerable<TestCaseData> BoneViewCases()
        {
            foreach (TestCaseData tc in ConformanceData.Of("view_angles"))
            {
                var c = tc.Arguments[0] as ConformanceData.Case;
                if (c == null || (string)c.Data["mode"] == "bone") yield return tc;
            }
        }

        static double Num(object o) { return Convert.ToDouble(o, System.Globalization.CultureInfo.InvariantCulture); }
        static Vec3 V3(object o) { var a = (List<object>)o; return new Vec3(Num(a[0]), Num(a[1]), Num(a[2])); }
        static Vector3 U(Vec3 v) { return new Vector3((float)v.X, (float)v.Y, (float)v.Z); }

        [TestCaseSource(nameof(BoneViewCases))]
        public void RunnerAnglesMatchConformanceCasesInUnitySpace(ConformanceData.Case c)
        {
            if (c == null) Assert.Fail(ConformanceData.Error);
            var d = c.Data;
            var sp = (Dictionary<string, object>)d["space"];
            var src = new SpaceSpec((string)sp["unit"], (string)sp["upAxis"], (string)sp["handedness"]);
            // ケースの値（各環境の座標）を Unity の系へ変換して Runner に与える
            SpaceConverter cv = FacialSpace.Converter(src, FacialSpace.Unity);
            Vec3 headPos = cv.Position(V3(d["headPos"]));
            Vec3 viewerPos = cv.Position(V3(d["viewerPos"]));
            Vec3 offset = d.ContainsKey("centerOffset") && d["centerOffset"] != null ? cv.Position(V3(d["centerOffset"])) : new Vec3(0, 0, 0);
            string fwd = cv.ForwardAxis((string)d["forwardAxis"]);
            Quaternion rot = Quaternion.identity;
            if (d["headRotation"] != null)
            {
                var q = (List<object>)d["headRotation"];
                Quat uq = FacialSpace.QuatNormalize(cv.Quaternion(new Quat(Num(q[0]), Num(q[1]), Num(q[2]), Num(q[3]))));
                rot = new Quaternion((float)uq.X, (float)uq.Y, (float)uq.Z, (float)uq.W);
            }

            _head.transform.SetPositionAndRotation(U(headPos), rot);
            ViewerAt(U(viewerPos));
            _data.grid.forwardAxis = fwd;
            _data.grid.centerOffset = U(offset);
            _runner.RebuildCaches();
            _runner.EvaluateNow(_viewer.transform, Dt);

            double tol = Math.Max(Num(d["toleranceDeg"]), 0.01);
            var e = (Dictionary<string, object>)d["expect"];
            double ey = Num(e["yawDeg"]);
            double yaw = _runner.CurrentYaw;
            bool yawOk = Math.Abs(FacialCore.NormalizeAxis(yaw - ey)) <= tol
                || (Math.Abs(Math.Abs(ey) - 180) < 1e-6 && Math.Abs(Math.Abs(yaw) - 180) <= tol);
            Assert.That(yawOk, Is.True, "yaw " + yaw + " (期待 " + ey + ")");
            Assert.That((double)_runner.CurrentPitch, Is.EqualTo(Num(e["pitchDeg"])).Within(tol), "pitch");
        }

        [Test]
        public void ForwardAxisAndCenterOffsetAreHonoured()
        {
            // 顔を +Z から +X へ 90° 回す（Unity は Y 軸まわり。+Y 回転で +Z → +X）。前方軸 +Z のまま、視点が +X にいれば正面
            _head.transform.rotation = Quaternion.Euler(0f, 90f, 0f);
            ViewerAt(new Vector3(3f, 1.5f, 0f));
            _runner.EvaluateNow(_viewer.transform, Dt);
            Assert.AreEqual(0f, _runner.CurrentYaw, 1e-3f);

            // 前方軸を -X にすると（ボーンの局所 -X は回転後にワールド +Z を向く）、視点が +Z にいれば正面
            _data.grid.forwardAxis = "-X";
            _runner.RebuildCaches();
            ViewerAt(new Vector3(0f, 1.5f, 3f));
            _runner.EvaluateNow(_viewer.transform, Dt);
            Assert.AreEqual(0f, _runner.CurrentYaw, 1e-3f);
        }

        // ---------------------------------------------------------------- 割り当て

        [Test]
        public void SteadyStateEvaluationDoesNotAllocate()
        {
            _runner.SetEmotionWeight(1, 0.5f);
            _overrides = ScriptableObject.CreateInstance<FacialCorrectionOverrides>();
            _runner.overrides = _overrides;
            var emo = new[] { 0f, 0.3f };
            // ウォームアップ（キャッシュ・リストの確保）
            for (int i = 0; i < 20; i++)
            {
                ViewerAt(new Vector3(Mathf.Sin(i * 0.3f) * 3f, 1.5f + i * 0.01f, Mathf.Cos(i * 0.3f) * 3f));
                _runner.PushOverride(new FacialFrameOverride { hasAlpha = true, alpha = 0.9f, emotionWeights = emo });
                _runner.EvaluateNow(_viewer.transform, Dt);
            }
            AllocProbe.AssertNoAlloc(() =>
            {
                for (int i = 0; i < 300; i++)
                {
                    _viewer.transform.position = new Vector3(Mathf.Sin(i * 0.1f) * 3f, 1.5f + Mathf.Sin(i * 0.05f), Mathf.Cos(i * 0.1f) * 3f);
                    _runner.PushOverride(new FacialFrameOverride { hasAlpha = true, alpha = 0.9f, emotionWeights = emo });
                    _runner.EvaluateNow(_viewer.transform, Dt);
                }
            }, "2 回目以降の評価でマネージドの割り当てがある");
        }
    }
}
