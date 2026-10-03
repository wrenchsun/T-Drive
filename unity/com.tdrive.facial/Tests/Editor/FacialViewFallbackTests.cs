// FT-6（FC-3）: 視点の最後の手段 FacialViewResolver.Fallback。順: 上書き → viewerOverride → EvaluateNow の引数 → (手動の角度) → Fallback → Camera.main。
// Fallback はテストごとに元へ戻す（エディタでは D-Drive のブリッジが入れていることがある）。
using System.Reflection;
using NUnit.Framework;
using TDrive.Facial.Core;
using UnityEngine;
using UnityEngine.TestTools;

namespace TDrive.Facial.Tests
{
    public class FacialViewFallbackTests
    {
        const float Dt = 1f / 60f;
        FacialTestRig _rig;
        FacialViewResolver.Provider _saved;
        int _calls;

        [SetUp]
        public void SetUp()
        {
            _saved = FacialViewResolver.Fallback;
            FacialViewResolver.Fallback = null;
            _rig = new FacialTestRig();
            _rig.runner.useManualAngles = false;
            _calls = 0;
        }

        [TearDown]
        public void TearDown()
        {
            FacialViewResolver.Fallback = _saved;
            _rig.Dispose();
        }

        FacialViewResolver.Provider At(Vector3 pos, float fov = 42f, bool ok = true)
        {
            return (Transform s, out Vector3 p, out Quaternion r, out float f) => { _calls++; p = pos; r = Quaternion.identity; f = fov; return ok; };
        }

        void Eval(Transform viewer = null) { _rig.runner.EvaluateNow(viewer, Dt); }

        [Test]
        public void FallbackIsUsedWhenNothingElseGivesAViewer()
        {
            FacialViewResolver.Fallback = At(new Vector3(-2f, 1.5f, 0f)); // キャラクターの左（世界の -X）
            Eval();
            Assert.AreEqual(FacialViewerSource.Fallback, _rig.runner.LastViewerSource);
            Assert.IsNull(_rig.runner.LastViewer, "Fallback の視点は Transform が無い");
            Assert.IsTrue(_rig.runner.HasLastViewerPosition);
            Assert.AreEqual(-2f, _rig.runner.LastViewerPosition.x, 1e-4f);
            Assert.AreEqual(42f, _rig.runner.LastViewerFov, 1e-4f);
            Assert.AreEqual(90f, _rig.runner.CurrentYaw, 1e-2f, "角度は Fallback の位置から（左は +90）");
            Assert.AreEqual(FacialAngleSource.Viewer, _rig.runner.LastAngleSource);
        }

        [Test]
        public void ViewerOverrideAndParameterComeBeforeFallback()
        {
            FacialViewResolver.Fallback = At(new Vector3(-2f, 1.5f, 0f));
            GameObject v = Hidden("V1", new Vector3(0f, 1.5f, 3f));
            try
            {
                Eval(v.transform);
                Assert.AreEqual(FacialViewerSource.Parameter, _rig.runner.LastViewerSource);
                Assert.AreEqual(0f, _rig.runner.CurrentYaw, 1e-2f);
                _rig.runner.viewerOverride = v.transform;
                Eval();
                Assert.AreEqual(FacialViewerSource.Component, _rig.runner.LastViewerSource);
                Assert.AreEqual(0, _calls, "先の段で決まれば Fallback は呼ばない");
            }
            finally { Object.DestroyImmediate(v); }
        }

        [Test]
        public void OverrideViewerComesBeforeFallback()
        {
            FacialViewResolver.Fallback = At(new Vector3(-2f, 1.5f, 0f));
            GameObject v = Hidden("V2", new Vector3(0f, 1.5f, 3f));
            try
            {
                _rig.runner.PushOverride(new FacialFrameOverride { viewer = v.transform });
                Eval();
                Assert.AreEqual(FacialViewerSource.Override, _rig.runner.LastViewerSource);
                Assert.AreEqual(0, _calls);
            }
            finally { Object.DestroyImmediate(v); }
        }

        [Test]
        public void ManualAnglesKeepTheirAnglesButDistanceUsesTheFallbackPosition()
        {
            _rig.runner.useManualAngles = true;
            _rig.data.policy.fadeStart = 1f; _rig.data.policy.fadeEnd = 2f;
            FacialViewResolver.Fallback = At(new Vector3(0f, 1.5f, 10f)); // 遠い
            Eval();
            Assert.AreEqual(0f, _rig.runner.CurrentYaw, 1e-3f, "手動の角度は視点で変わらない");
            Assert.AreEqual(0f, _rig.runner.LastScale, 1e-4f, "距離フェードは Fallback の位置で効く");
            FacialViewResolver.Fallback = At(new Vector3(0f, 1.5f, 0.5f)); // 近い
            Eval();
            Assert.Greater(_rig.runner.LastScale, 0.5f);
        }

        [Test]
        public void DistanceLayerWeightUsesTheFallbackPosition()
        {
            _rig.runner.useManualAngles = true;
            _rig.data.layers[1].weight = new FacialLayerWeightData { source = FacialLayerWeightSource.Distance, start = 1f, end = 3f, from = 0f, to = 1f };
            FacialViewResolver.Fallback = At(new Vector3(0f, 1.5f, 5f)); // end の外
            Eval();
            float far = _rig.W(FacialTestRig.N("Joy", 1, 1));
            FacialViewResolver.Fallback = At(new Vector3(0f, 1.5f, 0.5f)); // start の内
            Eval();
            float near = _rig.W(FacialTestRig.N("Joy", 1, 1));
            Assert.Greater(Mathf.Abs(far - near), 0.5f, "距離で決まる感情レイヤーが Fallback の距離に従う");
        }

        [Test]
        public void FallbackReturningFalseGoesOnToTheMainCamera()
        {
            FacialViewResolver.Fallback = At(new Vector3(-2f, 1.5f, 0f), 42f, false);
            Eval();
            Assert.AreEqual(1, _calls, "呼ばれる");
            Assert.AreNotEqual(FacialViewerSource.Fallback, _rig.runner.LastViewerSource);
            Camera main = Camera.main;
            if (main != null)
            {
                Assert.AreEqual(FacialViewerSource.MainCamera, _rig.runner.LastViewerSource);
                Assert.AreSame(main.transform, _rig.runner.LastViewer);
            }
            else Assert.AreEqual(FacialViewerSource.None, _rig.runner.LastViewerSource);
        }

        [Test]
        public void NullFallbackKeepsTheOldBehaviour()
        {
            Eval();
            Camera main = Camera.main;
            Assert.AreEqual(main != null ? FacialViewerSource.MainCamera : FacialViewerSource.None, _rig.runner.LastViewerSource);
        }

        [Test]
        public void AProviderThatThrowsIsLoggedAndTreatedAsNoView()
        {
            FacialViewResolver.Fallback = (Transform s, out Vector3 p, out Quaternion r, out float f) => { throw new System.InvalidOperationException("boom"); };
            LogAssert.Expect(LogType.Exception, new System.Text.RegularExpressions.Regex("boom"));
            Eval();
            Assert.AreNotEqual(FacialViewerSource.Fallback, _rig.runner.LastViewerSource);
        }

        [Test]
        public void ResolvingThroughTheFallbackAllocatesNothing()
        {
            FacialViewResolver.Fallback = At(new Vector3(-2f, 1.5f, 0f)); // デリゲートは 1 度だけ作る（毎フレーム作らない）
            Eval(); Eval();
            AllocProbe.AssertNoAlloc(() =>
            {
                for (int i = 0; i < AllocProbe.CallsPerAction; i++) Eval();
            }, "Fallback で視点を解決する経路は毎フレームの割り当てなし");
        }

        [Test]
        public void TheFallbackIsClearedOnSubsystemRegistration()
        {
            FacialViewResolver.Fallback = At(Vector3.zero);
            MethodInfo m = typeof(FacialViewResolver).GetMethod("ResetStatics", BindingFlags.Static | BindingFlags.NonPublic);
            Assert.IsNotNull(m);
            m.Invoke(null, null);
            Assert.IsNull(FacialViewResolver.Fallback, "ドメインリロード無しの再生でも残らない");
        }

        // ---------------------------------------------------------------- デバッグ表示（左右反転の親）

        [Test]
        public void MirrorSafeDirectionsAgreeWithTheRunnerYaw()
        {
            // 親を X 反転。顔の向きがローカル +X のとき、世界の前方は -X。Runner は -X の視点を正面（Yaw 0）とみなす。デバッグ表示も同じ式で同じ向きを出す
            _rig.root.transform.localScale = new Vector3(-1f, 1f, 1f);
            _rig.data.grid.forwardAxis = "+X";
            _rig.runner.RebuildCaches();
            GameObject v = Hidden("V3", new Vector3(-3f, 1.5f, 0f));
            try
            {
                Eval(v.transform);
                Assert.AreEqual(0f, _rig.runner.CurrentYaw, 1e-2f);
                Vec3 axis;
                Assert.IsTrue(FacialSpace.TryAxisVector("+X", out axis));
                Vector3 wf, wo;
                FacialCorrectionRunner.MirrorSafeDirections(_rig.head.transform, axis, Vector3.zero, out wf, out wo);
                Vector3 toViewer = (v.transform.position - _rig.head.transform.position).normalized;
                Assert.AreEqual(1f, Vector3.Dot(wf.normalized, toViewer), 1e-3f, "表示の前方向が視点を向く（回転だけで求めると逆向きになる）");
            }
            finally { Object.DestroyImmediate(v); }
        }

        static GameObject Hidden(string name, Vector3 pos)
        {
            GameObject go = UnityEditor.EditorUtility.CreateGameObjectWithHideFlags(name, HideFlags.HideAndDontSave);
            go.transform.position = pos;
            return go;
        }
    }
}
