// FT-3: .fctrack の曲線でクリップを動かす。director を編集時に Evaluate して、Runner（合成メッシュ）のブレンドシェイプの重みで確かめる。
// 重みの目安（FacialTimelineTests と同じ）: 強さ 1 で中央の Neutral(1,1) = 100、感情 Joy の重み w で Joy(1,1) = 100 * w。
using NUnit.Framework;
using TDrive.Facial.Timeline;
using UnityEngine;

namespace TDrive.Facial.Tests.Timeline
{
    public class FacialTrackPlaybackTests
    {
        FacialTimelineTestHarness _h;
        FacialTrackAsset _asset;

        [SetUp]
        public void SetUp()
        {
            _h = new FacialTimelineTestHarness();
            _asset = ScriptableObject.CreateInstance<FacialTrackAsset>();
            _asset.name = "S010__test";
            _asset.frameRate = 30f;
            _asset.rangeStart = 0f;
            _asset.rangeEnd = 120f; // 4 秒
        }

        [TearDown]
        public void TearDown()
        {
            _h.Dispose();
            if (_asset != null) Object.DestroyImmediate(_asset);
        }

        static FacialKey K(float t, float v) { return new FacialKey(t, v); }
        float N11 { get { return _h.rig.W(FacialTestRig.N("Neutral", 1, 1)); } }
        float N12 { get { return _h.rig.W(FacialTestRig.N("Neutral", 1, 2)); } }
        float Joy { get { return _h.rig.W(FacialTestRig.N("Joy", 1, 1)); } }

        FacialCorrectionClip ClipFor(double start, double duration)
        {
            FacialCorrectionClip c = _h.AddClip(_h.AddTrack(), start, duration);
            c.track = _asset;
            return c;
        }

        [Test]
        public void AlphaCurveIsSampledAtEachTime()
        {
            _asset.alpha = new[] { K(0f, 1f), K(2f, 0f) };
            ClipFor(0, 4);
            _h.At(0.0); Assert.AreEqual(100f, N11, 0.5f);
            _h.At(0.5); Assert.AreEqual(75f, N11, 0.5f);
            _h.At(1.0); Assert.AreEqual(50f, N11, 0.5f);
            _h.At(2.0); Assert.AreEqual(0f, N11, 0.5f);
            _h.At(3.0); Assert.AreEqual(0f, N11, 0.5f, "最後のキーより後は最後の値");
        }

        [Test]
        public void EmotionCurveDrivesTheLayerByName()
        {
            _asset.emotions = new[]
            {
                new FacialEmotionCurve { layer = "Joy", keys = new[] { K(0f, 0f), K(2f, 1f) } },
                new FacialEmotionCurve { layer = "NoSuchLayer", keys = new[] { K(0f, 1f) } }, // 未知の名前は無視
            };
            ClipFor(0, 4);
            _h.At(0.0); Assert.AreEqual(0f, Joy, 0.5f);
            _h.At(1.0); Assert.AreEqual(50f, Joy, 0.5f);
            _h.At(1.5); Assert.AreEqual(75f, Joy, 0.5f);
            _h.At(2.0); Assert.AreEqual(100f, Joy, 0.5f);
        }

        [Test]
        public void StepKeysJumpWithoutInterpolation()
        {
            _asset.alpha = new[] { K(0f, 1f), K(1f, 1f), K(1f, 0f), K(3f, 0f) };
            ClipFor(0, 4);
            _h.At(0.9); Assert.AreEqual(100f, N11, 0.5f);
            _h.At(1.1); Assert.AreEqual(0f, N11, 0.5f);
        }

        [Test]
        public void ManualAnglesFollowUseManualAboveHalf()
        {
            _h.rig.runner.useManualAngles = false;
            _h.rig.runner.viewerOverride = _h.NewViewer(new Vector3(0f, 1.5f, 2f)); // 正面
            _asset.useManual = new[] { K(0f, 0f), K(1f, 0f), K(1f, 1f) };
            _asset.manualYaw = new[] { K(0f, 0f), K(4f, 40f) };
            _asset.manualPitch = new[] { K(0f, 10f) };
            ClipFor(0, 4);

            _h.At(0.5);
            Assert.AreEqual(0f, _h.rig.runner.CurrentYaw, 1e-2f, "useManual が 0 の間は視点の角度");
            _h.At(2.0);
            Assert.AreEqual(20f, _h.rig.runner.CurrentYaw, 1e-2f, "useManual が 1 の間は曲線の角度に固定");
            Assert.AreEqual(10f, _h.rig.runner.CurrentPitch, 1e-2f);
            Assert.AreEqual(FacialAngleSource.OverrideManual, _h.rig.runner.LastAngleSource);
        }

        [Test]
        public void HandWrittenAlphaWinsOverTheAlphaCurve()
        {
            _asset.alpha = new[] { K(0f, 0f) };
            FacialCorrectionClip c = ClipFor(0, 4);
            c.template.useAlpha = true; c.template.alpha = 0.5f; // 手の値（曲線は 0）
            _h.At(1.0);
            Assert.AreEqual(50f, N11, 0.5f);
        }

        [Test]
        public void HandWrittenEmotionWithTheSameNameWinsOverTheCurve()
        {
            _asset.emotions = new[] { new FacialEmotionCurve { layer = "Joy", keys = new[] { K(0f, 1f) } } };
            FacialCorrectionClip c = ClipFor(0, 4);
            c.template.emotions = new[] { new FacialEmotionEntry { layer = "Joy", weight = 0.25f } };
            _h.At(1.0);
            Assert.AreEqual(25f, Joy, 0.5f);
        }

        [Test]
        public void HandWrittenEntriesForOtherLayersDoNotDisturbTheCurve()
        {
            _asset.emotions = new[] { new FacialEmotionCurve { layer = "Joy", keys = new[] { K(0f, 0f), K(2f, 1f) } } };
            FacialCorrectionClip c = ClipFor(0, 4);
            c.template.emotions = new[] { new FacialEmotionEntry { layer = "Neutral", weight = 1f }, new FacialEmotionEntry { layer = "Rage", weight = 1f } };
            _h.At(1.0);
            Assert.AreEqual(50f, Joy, 0.5f, "曲線のレイヤーは曲線のまま");
        }

        [Test]
        public void ClipInOffsetsTheCurveTime()
        {
            // クリップの「開始位置（clipIn）」が 1 秒 = クリップの先頭で曲線の 1 秒目から始まる
            _asset.alpha = new[] { K(0f, 1f), K(2f, 0f) };
            var track = _h.AddTrack();
            var tc = track.CreateClip<FacialCorrectionClip>();
            tc.start = 10; tc.duration = 1; tc.clipIn = 1.0;
            ((FacialCorrectionClip)tc.asset).track = _asset;
            _h.At(10.25); Assert.AreEqual(37.5f, N11, 0.5f);
            _h.At(10.5); Assert.AreEqual(25f, N11, 0.5f);
        }

        [Test]
        public void ClipStartOffsetOnTheTimelineDoesNotShiftTheCurve()
        {
            // クリップがタイムラインの 5 秒から始まるなら、曲線の 0 秒はそこ（クリップの中の時刻で読む）
            _asset.alpha = new[] { K(0f, 1f), K(2f, 0f) };
            ClipFor(5, 4);
            _h.At(5.5); Assert.AreEqual(75f, N11, 0.5f);
            _h.At(6.0); Assert.AreEqual(50f, N11, 0.5f);
            _h.At(4.0); Assert.AreEqual(100f, N11, 0.5f, "クリップの外は通常の補正");
        }

        [Test]
        public void ClipWithoutATrackBehavesAsBefore()
        {
            FacialCorrectionClip c = _h.AddClip(_h.AddTrack(), 0, 2);
            Assert.IsNull(c.track);
            _h.At(1.0);
            Assert.AreEqual(100f, N11, 0.5f);
            Assert.AreEqual(0f, Joy, 0.5f);
        }

        [Test]
        public void CurvesBlendAcrossOverlappingClips()
        {
            // 2 つのクリップの重なりでは、それぞれの曲線の値を重みでブレンドする
            _asset.alpha = new[] { K(0f, 0f) };
            var other = ScriptableObject.CreateInstance<FacialTrackAsset>();
            try
            {
                other.alpha = new[] { K(0f, 1f) };
                var track = _h.AddTrack();
                var a = _h.AddClip(track, 0, 2); a.track = _asset;
                var b = _h.AddClip(track, 1, 2); b.track = other;
                _h.At(1.5); // 重なりの真ん中: 0 と 1 の半々
                Assert.AreEqual(50f, N11, 2f);
            }
            finally { Object.DestroyImmediate(other); }
        }

        [Test]
        public void MixerNeverModifiesTheAsset()
        {
            _asset.alpha = new[] { K(0f, 1f), K(2f, 0f) };
            _asset.emotions = new[] { new FacialEmotionCurve { layer = "Joy", keys = new[] { K(0f, 0f), K(2f, 1f) } } };
            FacialCorrectionClip c = ClipFor(0, 4);
            c.template.emotions = new[] { new FacialEmotionEntry { layer = "Joy", weight = 0.3f } };
            _h.At(1.0); _h.At(2.0);
            Assert.AreEqual(2, _asset.alpha.Length);
            Assert.AreEqual(1f, _asset.emotions[0].keys[1].value);
            Assert.AreEqual(0.3f, c.template.emotions[0].weight, 1e-6f, "クリップの手の値も書き換わらない");
        }
    }
}
