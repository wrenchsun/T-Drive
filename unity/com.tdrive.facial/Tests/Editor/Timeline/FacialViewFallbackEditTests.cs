// FT-6（FC-3）: 編集時の Timeline のプレビューの視点（Mixer の EditViewer）と Fallback の順。Auto = Fallback → メインカメラ → Scene ビュー / 指定（メインカメラ・Scene ビュー）は Fallback より先。
using NUnit.Framework;
using UnityEngine;

namespace TDrive.Facial.Tests.Timeline
{
    public class FacialViewFallbackEditTests
    {
        FacialViewResolver.Provider _saved;

        [SetUp]
        public void SetUp() { _saved = FacialViewResolver.Fallback; }

        [TearDown]
        public void TearDown() { FacialViewResolver.Fallback = _saved; }

        static FacialViewResolver.Provider At(Vector3 pos)
        {
            return (Transform s, out Vector3 p, out Quaternion r, out float f) => { p = pos; r = Quaternion.identity; f = 42f; return true; };
        }

        // ---------------------------------------------------------------- 編集時の Timeline のプレビュー（Mixer の EditViewer）

        [Test]
        public void EditAutoUsesTheFallbackBeforeTheMainCamera()
        {
            using (var h = new FacialTimelineTestHarness())
            {
                h.rig.runner.useManualAngles = false;
                FacialViewResolver.Fallback = At(new Vector3(-2f, 1.5f, 0f));
                h.AddClip(h.AddTrack(), 0, 2).template.useAlpha = true;
                h.rig.runner.editViewer = FacialEditViewer.Auto;
                h.At(1.0);
                Assert.AreEqual(FacialViewerSource.Fallback, h.rig.runner.LastViewerSource);
            }
        }

        [Test]
        public void EditViewerMainCameraAndSceneViewComeBeforeTheFallback()
        {
            using (var h = new FacialTimelineTestHarness())
            {
                h.rig.runner.useManualAngles = false;
                FacialViewResolver.Fallback = At(new Vector3(-2f, 1.5f, 0f));
                h.AddClip(h.AddTrack(), 0, 2).template.useAlpha = true;

                bool checkedAny = false;
                if (Camera.main != null)
                {
                    h.rig.runner.editViewer = FacialEditViewer.MainCamera;
                    h.At(1.0);
                    Assert.AreEqual(FacialViewerSource.Parameter, h.rig.runner.LastViewerSource, "メインカメラ指定が Fallback より先");
                    Assert.AreSame(Camera.main.transform, h.rig.runner.LastViewer);
                    checkedAny = true;
                }
                UnityEditor.SceneView sv = UnityEditor.SceneView.lastActiveSceneView;
                if (sv != null && sv.camera != null)
                {
                    h.rig.runner.editViewer = FacialEditViewer.SceneView;
                    h.At(1.0);
                    Assert.AreEqual(FacialViewerSource.Parameter, h.rig.runner.LastViewerSource, "Scene ビュー指定が Fallback より先");
                    Assert.AreSame(sv.camera.transform, h.rig.runner.LastViewer);
                    checkedAny = true;
                }
                if (!checkedAny) Assert.Ignore("メインカメラも Scene ビューも無いので、先に使われる視点を確かめられない");
            }
        }
    }
}
