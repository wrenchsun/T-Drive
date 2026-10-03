// パース補正（F5-4）の Runner・取り込み・調整値・検証のテスト（Unity EditMode）。
// 合成キャラクター（FacialTestRig）の頭は (0, 1.5, 0)・顔は +Z。視点を頭の正面（+Z）に置くと、角度は 0 / 0 で格子の中央（Neutral(1,1) = 100）になる。
using System.Collections.Generic;
using NUnit.Framework;
using TDrive.Facial.Core;
using TDrive.Facial.Editor;
using UnityEditor;
using UnityEngine;
using Object = UnityEngine.Object;

namespace TDrive.Facial.Tests
{
    public class FacialPerspectiveTests
    {
        const float Dt = 0.02f;
        FacialTestRig _rig;
        GameObject _viewer;
        FacialCorrectionOverrides _ov;
        string _k0, _k1;

        FacialCorrectionRunner R { get { return _rig.runner; } }

        [SetUp]
        public void SetUp()
        {
            _rig = new FacialTestRig();
            _viewer = EditorUtility.CreateGameObjectWithHideFlags("viewer", HideFlags.HideAndDontSave);
            _k0 = FacialNaming.PerspectiveName(FacialTestRig.Asset, 0);
            _k1 = FacialNaming.PerspectiveName(FacialTestRig.Asset, 1);
            _rig.AddShape(_k0);
            _rig.AddShape(_k1);
            SetKeys(FacialPerspectiveAxis.Distance, 0.3f, 0.8f);
        }

        [TearDown]
        public void TearDown()
        {
            _rig.Dispose();
            if (_viewer != null) Object.DestroyImmediate(_viewer);
            if (_ov != null) Object.DestroyImmediate(_ov);
        }

        void SetKeys(FacialPerspectiveAxis axis, float v0, float v1, bool emptySecond = false)
        {
            _rig.data.perspective = new FacialPerspectiveData
            {
                enabled = true, axis = axis, strength = 1f,
                keys = new[]
                {
                    new FacialPerspectiveKeyData { value = v0, morphName = _k0 },
                    new FacialPerspectiveKeyData { value = v1, morphName = emptySecond ? "" : _k1 },
                },
            };
        }

        void EvalAt(float distance, float dt = 1f)
        {
            _viewer.transform.position = new Vector3(0f, 1.5f, distance);
            R.EvaluateNow(_viewer.transform, dt);
        }

        float K0 { get { return _rig.W(_k0); } }
        float K1 { get { return _rig.W(_k1); } }

        [Test]
        public void DistanceAxisMixesTheTwoKeysAndAddsToTheAngleCorrection()
        {
            EvalAt(0.55f);
            Assert.AreEqual(50f, K0, 1e-2f);
            Assert.AreEqual(50f, K1, 1e-2f);
            Assert.AreEqual(100f, _rig.W(FacialTestRig.N("Neutral", 1, 1)), 1e-2f, "角度の補正はそのまま（足すだけ）");
            Assert.AreEqual(0.55f, R.LastPerspectiveAxisValue, 1e-4f);
            Assert.AreEqual(2, R.PerspectiveKeyCount);
            EvalAt(0.2f);
            Assert.AreEqual(100f, K0, 1e-2f); Assert.AreEqual(0f, K1, 1e-2f);
            EvalAt(2f);
            Assert.AreEqual(0f, K0, 1e-2f); Assert.AreEqual(100f, K1, 1e-2f);
        }

        [Test]
        public void KeyOrderInTheArrayIsTheShapeNumberNotTheValueOrder()
        {
            SetKeys(FacialPerspectiveAxis.Distance, 0.8f, 0.3f); // K0 が遠い側
            EvalAt(0.2f);
            Assert.AreEqual(0f, K0, 1e-2f); Assert.AreEqual(100f, K1, 1e-2f);
        }

        [Test]
        public void EmptyKeyHasNoShapeButStillMarksTheRange()
        {
            SetKeys(FacialPerspectiveAxis.Distance, 0.3f, 0.8f, true);
            EvalAt(0.55f);
            Assert.AreEqual(50f, K0, 1e-2f);
            Assert.AreEqual(0f, K1, 1e-2f, "空のキーのシェイプは作らない / 書かない");
            EvalAt(5f);
            Assert.AreEqual(0f, K0, 1e-2f, "遠い側は空のキー = 補正なし");
        }

        [Test]
        public void DisabledOrNoKeysDoesNothing()
        {
            _rig.data.perspective.enabled = false;
            EvalAt(0.55f);
            Assert.AreEqual(0f, K0, 1e-4f);
            Assert.IsTrue(float.IsNaN(R.LastPerspectiveAxisValue));
            Assert.IsFalse(R.PerspectiveActive);
            _rig.data.perspective = new FacialPerspectiveData { enabled = true, strength = 1f, keys = new FacialPerspectiveKeyData[0] };
            EvalAt(0.55f);
            Assert.AreEqual(0f, K0, 1e-4f);
        }

        [Test]
        public void FovAxisUsesTheCameraOnTheViewer()
        {
            SetKeys(FacialPerspectiveAxis.Fov, 30f, 50f);
            EvalAt(1f);
            Assert.AreEqual(0f, K0, 1e-4f, "Camera が無い = 画角が分からない = 補正 0");
            Assert.IsTrue(float.IsNaN(R.LastPerspectiveAxisValue));
            Camera cam = _viewer.AddComponent<Camera>();
            cam.fieldOfView = 40f;
            EvalAt(1f);
            Assert.AreEqual(40f, R.LastPerspectiveAxisValue, 1e-3f);
            Assert.AreEqual(50f, K0, 1e-2f);
            Assert.AreEqual(50f, K1, 1e-2f);
            cam.orthographic = true;
            EvalAt(1f);
            Assert.AreEqual(0f, K0, 1e-4f, "正射影は画角なし");
        }

        [Test]
        public void StrengthFromDataOverridesAndFrameOverrideMultiply()
        {
            _rig.data.perspective.strength = 0.5f;
            EvalAt(0.2f);
            Assert.AreEqual(50f, K0, 1e-2f);
            _ov = ScriptableObject.CreateInstance<FacialCorrectionOverrides>();
            _ov.overridePerspectiveStrength = true; _ov.perspectiveStrength = 0.8f;
            R.overrides = _ov;
            EvalAt(0.2f);
            Assert.AreEqual(80f, K0, 1e-2f, "調整値がデータの強さを置き換える");
            R.PushOverride(new FacialFrameOverride { hasPerspective = true, perspective = 0.5f });
            EvalAt(0.2f);
            Assert.AreEqual(40f, K0, 1e-2f, "Timeline の値は掛かる");
            Assert.AreEqual(0.4f, R.LastPerspectiveStrength, 1e-4f);
            EvalAt(0.2f);
            Assert.AreEqual(80f, K0, 1e-2f, "PushOverride は 1 回きり");
        }

        [Test]
        public void GlobalAlphaAndOverrideAlphaScaleThePerspectiveToo()
        {
            R.alpha = 0.5f;
            EvalAt(0.2f);
            Assert.AreEqual(50f, K0, 1e-2f);
            R.alpha = 1f;
            R.PushOverride(new FacialFrameOverride { hasAlpha = true, alpha = 0.25f });
            EvalAt(0.2f);
            Assert.AreEqual(25f, K0, 1e-2f);
        }

        [Test]
        public void ExpressionDampenAppliesToThePerspective()
        {
            _rig.smr.SetBlendShapeWeight(new FacialShapeIndex(_rig.mesh).Find("bs.jaw"), 100f); // 表情の強さ 1 → 1 - 0.5
            EvalAt(0.2f);
            Assert.AreEqual(50f, K0, 1e-2f);
        }

        [Test]
        public void SlotsMergeLastOneWins()
        {
            var a = new object(); var b = new object();
            R.SetOverride(a, new FacialFrameOverride { hasPerspective = true, perspective = 0.2f });
            R.SetOverride(b, new FacialFrameOverride { hasPerspective = true, perspective = 0.6f });
            EvalAt(0.2f);
            Assert.AreEqual(60f, K0, 1e-2f);
            R.ClearOverride(b);
            EvalAt(0.2f);
            Assert.AreEqual(20f, K0, 1e-2f);
        }

        [Test]
        public void StepHoldingKeepsThePerspectiveToo()
        {
            _ov = ScriptableObject.CreateInstance<FacialCorrectionOverrides>();
            _ov.overrideStepFps = true; _ov.stepFps = 10f;
            R.overrides = _ov;
            EvalAt(0.2f, 0.02f);
            Assert.AreEqual(100f, K0, 1e-2f);
            EvalAt(2f, 0.02f); // 次の周期まで保持
            Assert.IsTrue(R.StepHolding);
            Assert.AreEqual(100f, K0, 1e-2f);
        }

        [Test]
        public void ResetWeightsAndDisableZeroThePerspectiveShapes()
        {
            EvalAt(0.55f);
            Assert.Greater(_rig.FcSum(), 0f);
            R.ResetWeights();
            Assert.AreEqual(0f, _rig.FcSum(), 1e-5f);
            EvalAt(0.55f);
            // EditMode では OnDisable が自動で呼ばれないので、非公開のメッセージ関数を直接呼ぶ
            System.Reflection.MethodInfo m = typeof(FacialCorrectionRunner).GetMethod("OnDisable", System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.NonPublic);
            Assert.NotNull(m);
            m.Invoke(R, null);
            Assert.AreEqual(0f, _rig.FcSum(), 1e-5f);
        }

        [Test]
        public void MissingPerspectiveShapeIsReportedLikeOtherFcShapes()
        {
            string missing = FacialNaming.PerspectiveName(FacialTestRig.Asset, 7);
            _rig.data.perspective.keys[1].morphName = missing;
            R.RebuildCaches();
            CollectionAssert.Contains(new List<string>(R.GetMissingShapeNames()), missing);
            List<FacialIssue> issues = FacialValidation.Run(R);
            Assert.IsTrue(issues.Exists(i => i.Kind == FacialIssueKind.MissingShape && i.ShapeName == missing));
            EvalAt(0.55f);
            Assert.AreEqual(50f, K0, 1e-2f, "あるシェイプは動く（無いものだけ飛ばす）");
        }

        [Test]
        public void FovFromTheFallbackProviderIsUsedAndZeroMeansUnknown()
        {
            SetKeys(FacialPerspectiveAxis.Fov, 30f, 50f);
            FacialViewResolver.Fallback = (Transform s, out Vector3 p, out Quaternion r, out float fov) =>
            { p = new Vector3(0f, 1.5f, 1f); r = Quaternion.identity; fov = 0f; return true; };
            try
            {
                R.EvaluateNow((Transform)null, 1f);
                Assert.AreEqual(0f, K0, 1e-4f);
                FacialViewResolver.Fallback = (Transform s, out Vector3 p, out Quaternion r, out float fov) =>
                { p = new Vector3(0f, 1.5f, 1f); r = Quaternion.identity; fov = 30f; return true; };
                R.EvaluateNow((Transform)null, 1f);
                Assert.AreEqual(100f, K0, 1e-2f);
            }
            finally { FacialViewResolver.Fallback = null; }
        }

        [Test]
        public void SteadyStateDoesNotAllocate()
        {
            _ov = ScriptableObject.CreateInstance<FacialCorrectionOverrides>();
            _ov.overridePerspectiveStrength = true; _ov.perspectiveStrength = 0.9f;
            R.overrides = _ov;
            for (int i = 0; i < 40; i++)
            {
                _viewer.transform.position = new Vector3(0f, 1.5f, 0.2f + i * 0.02f);
                R.PushOverride(new FacialFrameOverride { hasPerspective = true, perspective = 0.9f });
                R.EvaluateNow(_viewer.transform, Dt);
            }
            AllocProbe.AssertNoAlloc(() =>
            {
                for (int i = 0; i < 300; i++)
                {
                    _viewer.transform.position = new Vector3(0f, 1.5f, 0.2f + Mathf.Abs(Mathf.Sin(i * 0.05f)));
                    R.PushOverride(new FacialFrameOverride { hasPerspective = true, perspective = 0.9f });
                    R.EvaluateNow(_viewer.transform, Dt);
                }
            }, "パース補正を使った 2 回目以降の評価でマネージドの割り当てがある");
        }

        // ---------------------------------------------------------------- 取り込み

        static string Json(string axis, string keys)
        {
            return "{\"format\":\"FacialCorrection\",\"version\":1,\"meta\":{\"unit\":\"cm\",\"upAxis\":\"Y\",\"handedness\":\"right\"},"
                + "\"grid\":{\"yawRange\":90,\"pitchRange\":45,\"cols\":3,\"rows\":3,\"baseBone\":\"head\",\"forwardAxis\":\"+Z\",\"edgeFade\":15},"
                + "\"layers\":[{\"name\":\"Neutral\",\"points\":[{\"row\":1,\"col\":1,\"isKey\":true,\"curves\":{}}]}],\"asset\":\"test\","
                + "\"perspective\":{\"enabled\":true,\"axis\":\"" + axis + "\",\"strength\":0.7,\"keys\":" + keys + "}}";
        }

        [Test]
        public void ImportConvertsDistanceFromCentimetersToMetersAndNamesShapesByIndex()
        {
            FacialCorrectionData d = FcposeConverter.BuildData(
                Json("distance", "[{\"value\":30,\"curves\":{\"bs.a\":0.5}},{\"value\":80},{\"value\":120,\"curves\":{\"bs.b\":1}}]"), "x", null);
            try
            {
                Assert.IsTrue(d.perspective.enabled);
                Assert.AreEqual(FacialPerspectiveAxis.Distance, d.perspective.axis);
                Assert.AreEqual(0.7f, d.perspective.strength, 1e-6f);
                Assert.AreEqual(3, d.perspective.keys.Length);
                Assert.AreEqual(0.3f, d.perspective.keys[0].value, 1e-6f);
                Assert.AreEqual(0.8f, d.perspective.keys[1].value, 1e-6f);
                Assert.AreEqual(1.2f, d.perspective.keys[2].value, 1e-6f);
                Assert.AreEqual("FC_test_Persp_K0", d.perspective.keys[0].morphName);
                Assert.AreEqual("", d.perspective.keys[1].morphName, "ポーズが空のキーは名前なし");
                Assert.AreEqual("FC_test_Persp_K2", d.perspective.keys[2].morphName, "番号は配列の位置");
            }
            finally { Object.DestroyImmediate(d); }
        }

        [Test]
        public void ImportKeepsFovAsDegreesAndFallsBackOnUnknownAxis()
        {
            FacialCorrectionData d = FcposeConverter.BuildData(Json("fov", "[{\"value\":40,\"curves\":{\"bs.a\":1}}]"), "x", null);
            var warnings = new List<string>();
            FacialCorrectionData u = FcposeConverter.BuildData(Json("zzz", "[{\"value\":40,\"curves\":{\"bs.a\":1}}]"), "x", s => warnings.Add(s));
            try
            {
                Assert.AreEqual(FacialPerspectiveAxis.Fov, d.perspective.axis);
                Assert.AreEqual(40f, d.perspective.keys[0].value, 1e-6f);
                Assert.AreEqual(FacialPerspectiveAxis.Distance, u.perspective.axis);
                Assert.AreEqual(1, warnings.Count);
            }
            finally { Object.DestroyImmediate(d); Object.DestroyImmediate(u); }
        }

        [Test]
        public void ImportWithoutPerspectiveIsANoOp()
        {
            FacialCorrectionData d = FcposeConverter.BuildData("{\"format\":\"FacialCorrection\",\"version\":1,\"asset\":\"test\"}", "x", null);
            try
            {
                Assert.IsFalse(d.perspective.enabled);
                Assert.AreEqual(0, d.perspective.keys.Length);
                Assert.AreEqual(1f, d.perspective.strength);
            }
            finally { Object.DestroyImmediate(d); }
        }

        [Test]
        public void ImportedDataRunsEndToEndWithMeters()
        {
            FacialCorrectionData d = FcposeConverter.BuildData(
                Json("distance", "[{\"value\":30,\"curves\":{\"bs.a\":0.5}},{\"value\":80,\"curves\":{\"bs.b\":1}}]"), "x", null);
            try
            {
                _rig.data.perspective = d.perspective;
                _rig.data.perspective.strength = 1f;
                EvalAt(0.55f); // 55 cm = 30 cm と 80 cm の真ん中
                Assert.AreEqual(50f, K0, 1e-2f);
                Assert.AreEqual(50f, K1, 1e-2f);
            }
            finally { Object.DestroyImmediate(d); }
        }

        [Test]
        public void ResolveUsesDataThenOverride()
        {
            _rig.data.perspective.strength = 0.4f;
            Assert.AreEqual(0.4f, FacialCorrectionOverrides.Resolve(_rig.data, null).perspectiveStrength, 1e-6f);
            _ov = ScriptableObject.CreateInstance<FacialCorrectionOverrides>();
            _ov.overridePerspectiveStrength = true; _ov.perspectiveStrength = 2f;
            Assert.AreEqual(1f, FacialCorrectionOverrides.Resolve(_rig.data, _ov).perspectiveStrength, 1e-6f, "0〜1 に丸める");
            Assert.AreEqual(1f, FacialCorrectionOverrides.Resolve(null, null).perspectiveStrength);
        }

        [Test]
        public void FctrackConverterFillsThePerspectiveCurveOnlyWhenPresent()
        {
            const string json = "{\"format\":\"FacialTrack\",\"version\":1,\"shot\":\"a\",\"model\":\"b\",\"range\":[0,30],\"curves\":{\"perspective\":[[0.0,1.0],[0.5,0.0]]}}";
            FacialTrackAsset a = FctrackConverter.Build(FctrackReader.Read(json), "x");
            try
            {
                Assert.IsTrue(a.HasPerspective);
                Assert.AreEqual(2, a.perspective.Length);
                Assert.IsFalse(FctrackConverter.Build(FctrackReader.Read("{\"format\":\"FacialTrack\",\"version\":1,\"shot\":\"a\",\"model\":\"b\",\"range\":[0,30],\"curves\":{}}"), "y").HasPerspective);
            }
            finally { Object.DestroyImmediate(a); }
        }
    }
}
