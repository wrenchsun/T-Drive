// パース補正のクリップ（「パース補正を使う」+ 強さ）と .fctrack の perspective カーブ（Unity EditMode）。
// 視点は viewerOverride に置いた Transform（頭の正面 1 m）。キーは 1 個なので重みは常に 1 → シェイプの重み = 100 × 強さ。
using NUnit.Framework;
using TDrive.Facial.Core;
using TDrive.Facial.Timeline;
using UnityEngine;

namespace TDrive.Facial.Tests.Timeline
{
    public class FacialPerspectiveTimelineTests
    {
        FacialTimelineTestHarness _h;
        FacialTrackAsset _asset;
        GameObject _viewer;
        string _k0;

        [SetUp]
        public void SetUp()
        {
            _h = new FacialTimelineTestHarness();
            _asset = ScriptableObject.CreateInstance<FacialTrackAsset>();
            _asset.frameRate = 30f; _asset.rangeStart = 0f; _asset.rangeEnd = 120f;
            _k0 = FacialNaming.PerspectiveName(FacialTestRig.Asset, 0);
            _h.rig.AddShape(_k0);
            _h.rig.data.perspective = new FacialPerspectiveData
            {
                enabled = true, axis = FacialPerspectiveAxis.Distance, strength = 1f,
                keys = new[] { new FacialPerspectiveKeyData { value = 0.3f, morphName = _k0 } },
            };
            _viewer = new GameObject("persp_viewer") { hideFlags = HideFlags.HideAndDontSave };
            _viewer.transform.position = new Vector3(0f, 1.5f, 1f);
            _h.rig.runner.viewerOverride = _viewer.transform;
        }

        [TearDown]
        public void TearDown()
        {
            _h.Dispose();
            if (_asset != null) Object.DestroyImmediate(_asset);
            if (_viewer != null) Object.DestroyImmediate(_viewer);
        }

        static FacialKey K(float t, float v) { return new FacialKey(t, v); }
        float P { get { return _h.rig.W(_k0); } }

        FacialCorrectionClip Clip(bool withTrack)
        {
            FacialCorrectionClip c = _h.AddClip(_h.AddTrack(), 0, 4);
            if (withTrack) c.track = _asset;
            return c;
        }

        [Test]
        public void ClipValueScalesThePerspective()
        {
            FacialCorrectionClip c = Clip(false);
            c.template.usePerspective = true; c.template.perspective = 0.5f;
            _h.At(1.0); Assert.AreEqual(50f, P, 0.5f);
        }

        [Test]
        public void WithoutTheClipFlagOrCurveThePerspectiveIsUnchanged()
        {
            Clip(false);
            _h.At(1.0); Assert.AreEqual(100f, P, 0.5f);
        }

        [Test]
        public void FctrackPerspectiveCurveDrivesItAndTheManualToggleWins()
        {
            _asset.perspective = new[] { K(0f, 1f), K(2f, 0f) };
            Clip(true);
            _h.At(0.0); Assert.AreEqual(100f, P, 0.5f);
            _h.At(1.0); Assert.AreEqual(50f, P, 0.5f);
            _h.At(2.0); Assert.AreEqual(0f, P, 0.5f);
        }

        [Test]
        public void ClipValueWinsOverTheCurve()
        {
            _asset.perspective = new[] { K(0f, 0f) }; // 曲線は 0
            FacialCorrectionClip c = Clip(true);
            c.template.usePerspective = true; c.template.perspective = 0.25f; // 先に設定（グラフは最初の評価で作られる）
            _h.At(1.1); Assert.AreEqual(25f, P, 0.5f, "「パース補正を使う」の手の値が曲線に優先する");
        }
    }
}
