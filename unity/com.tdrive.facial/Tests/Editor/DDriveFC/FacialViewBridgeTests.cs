// FT-6 / FC-3（D-Drive 1.4.0 以降）: 視点の最後の手段に ViewCamera.TryGetCurrent を差し込む FacialViewBridge。
using DDrive.Runtime.Viewing;
using NUnit.Framework;
using TDrive.Facial.DDrive;
using UnityEngine;

namespace TDrive.Facial.Tests.DDrive
{
    public class FacialViewBridgeTests
    {
        sealed class FixedView : IViewProvider
        {
            public Vector3 Position;
            public bool TryGetView(Transform subject, out ViewPose pose)
            {
                pose = new ViewPose(Position, Quaternion.identity, 37f, ViewSource.Override, null);
                return true;
            }
        }

        FacialViewResolver.Provider _saved;

        [SetUp]
        public void SetUp() { _saved = FacialViewResolver.Fallback; }

        [TearDown]
        public void TearDown() { FacialViewResolver.Fallback = _saved; }

        [Test]
        public void InstallAndUninstallOnlyTouchTheirOwnProvider()
        {
            FacialViewResolver.Fallback = null;
            FacialViewBridge.Install();
            Assert.IsTrue(FacialViewBridge.IsInstalled);
            FacialViewResolver.Provider first = FacialViewResolver.Fallback;
            FacialViewBridge.Install();
            Assert.AreSame(first, FacialViewResolver.Fallback, "デリゲートは 1 つを使い回す（毎フレームの割り当てなし）");
            FacialViewBridge.Uninstall();
            Assert.IsNull(FacialViewResolver.Fallback);

            FacialViewResolver.Provider other = (Transform s, out Vector3 p, out Quaternion r, out float f) => { p = default(Vector3); r = Quaternion.identity; f = 0f; return false; };
            FacialViewResolver.Fallback = other;
            FacialViewBridge.Uninstall();
            Assert.AreSame(other, FacialViewResolver.Fallback, "他が差し替えたものは外さない");
        }

        [Test]
        public void TheRunnerFollowsAViewProviderRegisteredInDDrive()
        {
            var view = new FixedView { Position = new Vector3(-2f, 1.5f, 0f) }; // キャラクターの左
            using (var rig = new FacialTestRig())
            {
                rig.runner.useManualAngles = false;
                FacialViewBridge.Install();
                ViewCamera.Register(view, 1000);
                try
                {
                    rig.runner.EvaluateNow((Transform)null, 1f / 60f);
                    Assert.AreEqual(FacialViewerSource.Fallback, rig.runner.LastViewerSource);
                    Assert.AreEqual(90f, rig.runner.CurrentYaw, 1e-2f, "D-Drive が差し替えた視点に補正が従う（分割画面など）");
                    Assert.AreEqual(37f, rig.runner.LastViewerFov, 1e-4f);
                }
                finally { ViewCamera.Unregister(view); }
            }
        }

        [Test]
        public void TheDDriveViewIsNotUsedWhenTheComponentGivesItsOwnViewer()
        {
            var view = new FixedView { Position = new Vector3(-2f, 1.5f, 0f) };
            using (var rig = new FacialTestRig())
            {
                rig.runner.useManualAngles = false;
                GameObject v = UnityEditor.EditorUtility.CreateGameObjectWithHideFlags("BridgeViewer", HideFlags.HideAndDontSave);
                FacialViewBridge.Install();
                ViewCamera.Register(view, 1000);
                try
                {
                    v.transform.position = new Vector3(0f, 1.5f, 3f);
                    rig.runner.viewerOverride = v.transform;
                    rig.runner.EvaluateNow((Transform)null, 1f / 60f);
                    Assert.AreEqual(FacialViewerSource.Component, rig.runner.LastViewerSource);
                    Assert.AreEqual(0f, rig.runner.CurrentYaw, 1e-2f);
                }
                finally { ViewCamera.Unregister(view); Object.DestroyImmediate(v); }
            }
        }

        [Test]
        public void TheRunnerExecutionOrderIsAfterTheCutsceneCameraApplier()
        {
            // docs/26 §4.6.5: LateUpdate で DDriveCutsceneCameraApplier.ExecutionOrder より後（Runner は 10000。変えない）
            var attr = (DefaultExecutionOrder)System.Attribute.GetCustomAttribute(typeof(FacialCorrectionRunner), typeof(DefaultExecutionOrder));
            Assert.AreEqual(10000, attr.order);
            Assert.Greater(attr.order, global::DDrive.Runtime.Cutscene.DDriveCutsceneCameraApplier.ExecutionOrder);
        }
    }
}
