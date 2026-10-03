// Timeline のミキサーと持続する上書き（docs/19 E-2 / E-3 / E-10 / E-14）。
// 「一時停止中のカットシーン」= ミキサーが動かない（Evaluate が来ない）フレーム。Runner の評価だけが続く状況を EvaluateNow の連続で再現する。
using NUnit.Framework;
using TDrive.Facial.Timeline;
using UnityEngine;
using UnityEngine.Playables;
using UnityEngine.Timeline;

namespace TDrive.Facial.Tests.Timeline
{
    public class FacialPersistentTimelineTests
    {
        const float Dt = 10f;
        FacialTimelineTestHarness _h;
        System.Func<PlayableDirector, TrackAsset, Object> _savedResolver;

        [SetUp]
        public void SetUp()
        {
            _h = new FacialTimelineTestHarness();
            _savedResolver = FacialTimelineBinding.FallbackResolver;
        }

        [TearDown]
        public void TearDown()
        {
            FacialTimelineBinding.FallbackResolver = _savedResolver;
            _h.Dispose();
        }

        FacialCorrectionRunner R { get { return _h.rig.runner; } }

        [Test]
        public void E2_CorrectionStaysWhileTheDirectorIsPausedAndGoesBackWhenTheClipEnds()
        {
            FacialCorrectionTrack t = _h.AddTrack();
            FacialCorrectionClip c = _h.AddClip(t, 0, 2);
            c.template.fixAngles = true; c.template.yaw = 45f; c.template.pitch = 0f;
            _h.At(1.0);
            Assert.AreEqual(45f, R.CurrentYaw, 1e-3f, "前提: クリップの中");
            for (int i = 0; i < 5; i++)
            {
                R.EvaluateNow((Transform)null, Dt); // 一時停止中: ミキサーは来ない。Runner の LateUpdate だけが回る
                Assert.AreEqual(45f, R.CurrentYaw, 1e-3f, "一時停止 " + i);
            }
            _h.At(5.0); // クリップの外（重みの合計 0）
            Assert.AreEqual(0f, R.CurrentYaw, 1e-3f, "クリップが無い区間では通常へ戻る");
            Assert.AreEqual(0, R.OverrideCount);
        }

        [Test]
        public void E2_StoppingTheGraphClearsTheOverride()
        {
            FacialCorrectionTrack t = _h.AddTrack();
            FacialCorrectionClip c = _h.AddClip(t, 0, 2);
            c.template.fixAngles = true; c.template.yaw = 45f;
            _h.At(1.0);
            Assert.AreEqual(1, R.OverrideCount);
            _h.director.RebuildGraph(); // 古いグラフを壊す（OnPlayableDestroy）。Timeline を差し替えた・シーンを抜けたときと同じ
            Assert.AreEqual(0, R.OverrideCount, "グラフが壊れたら消える");
        }

        [Test]
        public void E3_TwoFacialTracksOnOneRunnerBothTakeEffect()
        {
            // 手で作ったトラック: 強さ 0.5 + 感情 Joy。自動トラック: 角度の固定
            FacialCorrectionTrack manual = _h.AddTrack("Role_Facial");
            FacialCorrectionClip m = _h.AddClip(manual, 0, 2);
            m.template.useAlpha = true; m.template.alpha = 0.5f;
            m.template.emotions = new[] { new FacialEmotionEntry { layer = "Joy", weight = 1f } };
            FacialCorrectionTrack auto = _h.AddTrack("Role_Facial(auto)");
            FacialCorrectionClip a = _h.AddClip(auto, 0, 2);
            a.template.fixAngles = true; a.template.yaw = 40f;

            _h.At(1.0);
            Assert.AreEqual(2, R.OverrideCount, "持ち主ごとに別々に持つ");
            Assert.AreEqual(40f, R.CurrentYaw, 1e-3f, "自動トラックの角度が効く");
            Assert.AreEqual(0.5f, R.LastScale, 0.05f, "手のトラックの強さも効く（後のトラックに消されない）");
            Assert.Greater(_h.rig.W(FacialTestRig.N("Joy", 1, 1)), 10f, "手のトラックの感情も効く（自動トラックの「指定なし」に消されない）");
        }

        [Test]
        public void E3_ManualTrackBeatsTheAutoTrackOnTheSameEmotionLayer()
        {
            FacialCorrectionTrack manual = _h.AddTrack("Role_Facial");
            _h.AddClip(manual, 0, 2).template.emotions = new[] { new FacialEmotionEntry { layer = "Joy", weight = 1f } };
            FacialCorrectionTrack auto = _h.AddTrack("Role_Facial(auto)");
            _h.AddClip(auto, 0, 2).template.emotions = new[] { new FacialEmotionEntry { layer = "Joy", weight = 0f } };
            _h.At(1.0);
            Assert.AreEqual(100f, _h.rig.W(FacialTestRig.N("Joy", 1, 1)), 0.5f, "(auto) は優先度が低いので、手のトラックが勝つ");
        }

        [Test]
        public void E14_AnUnresolvableTrackDoesNotSearchEveryFrame()
        {
            int calls = 0;
            FacialTimelineBinding.FallbackResolver = (d, tr) => { calls++; return null; };
            FacialCorrectionTrack t = _h.AddTrack("Nobody_Facial", false);
            _h.AddClip(t, 0, 5).template.useAlpha = true;
            for (int i = 0; i < 20; i++) _h.At(0.1 * (i + 1));
            Assert.LessOrEqual(calls, 2, "見つからなかった結果を使い回す（毎フレームの探し直しをしない）");
        }

        [Test]
        public void E10_EditViewerOptionChoosesTheCameraForTheEditTimePreview()
        {
            Camera main = Camera.main;
            Assume.That(main != null, "シーンにメインカメラが無い");
            R.useManualAngles = false;
            FacialCorrectionTrack t = _h.AddTrack();
            _h.AddClip(t, 0, 2).template.useAlpha = true;

            R.editViewer = FacialEditViewer.MainCamera;
            _h.At(1.0);
            Assert.AreSame(main.transform, R.LastViewer, "メインカメラ指定");

            R.editViewer = FacialEditViewer.Auto;
            _h.At(1.0);
            Assert.AreSame(main.transform, R.LastViewer, "自動: メインカメラがあればそれ");

            UnityEditor.SceneView sv = UnityEditor.SceneView.lastActiveSceneView;
            if (sv != null && sv.camera != null && sv.camera.transform != main.transform)
            {
                R.editViewer = FacialEditViewer.SceneView;
                _h.At(1.0);
                Assert.AreSame(sv.camera.transform, R.LastViewer, "Scene ビュー指定");
            }
        }
    }
}
