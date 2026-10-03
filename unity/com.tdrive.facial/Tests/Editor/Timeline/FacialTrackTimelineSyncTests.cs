// FT-3: FacialTrackTimelineSync（.fctrack → Timeline の自動トラック）。TimelineAsset はメモリ上（HideAndDontSave）で作る。何も保存しない。
using System.Linq;
using NUnit.Framework;
using TDrive.Facial.Timeline;
using TDrive.Facial.Timeline.Editor;
using UnityEngine;
using UnityEngine.Timeline;

namespace TDrive.Facial.Tests.Timeline
{
    public class FacialTrackTimelineSyncTests
    {
        TimelineAsset _tl;
        FacialTrackAsset _a;

        [SetUp]
        public void SetUp()
        {
            _tl = ScriptableObject.CreateInstance<TimelineAsset>();
            _tl.hideFlags = HideFlags.HideAndDontSave;
            _a = NewAsset("S010__Shizuku", 30f, 0f, 90f);
        }

        [TearDown]
        public void TearDown()
        {
            if (_tl != null) Object.DestroyImmediate(_tl);
            if (_a != null) Object.DestroyImmediate(_a);
        }

        static FacialTrackAsset NewAsset(string name, float fps, float start, float end)
        {
            var a = ScriptableObject.CreateInstance<FacialTrackAsset>();
            a.hideFlags = HideFlags.HideAndDontSave;
            a.name = name; a.frameRate = fps; a.rangeStart = start; a.rangeEnd = end;
            return a;
        }

        AnimationTrack AddRole(string role) { return _tl.CreateTrack<AnimationTrack>(null, role); }

        FacialCorrectionTrack[] FacialTracks() { return _tl.GetOutputTracks().OfType<FacialCorrectionTrack>().ToArray(); }

        [Test]
        public void AddsTheAutoTrackWithOneClipSpanningTheRange()
        {
            AddRole("Shizuku");
            FacialTrackSyncReport r = FacialTrackTimelineSync.Apply(_tl, _a, "Shizuku");
            Assert.IsTrue(r.Success, r.ToString());
            Assert.IsTrue(r.Changed); Assert.IsTrue(r.CreatedTrack); Assert.IsTrue(r.CreatedClip);
            Assert.AreEqual("Shizuku_Facial(auto)", r.TrackName);

            FacialCorrectionTrack[] tracks = FacialTracks();
            Assert.AreEqual(1, tracks.Length);
            Assert.AreEqual("Shizuku_Facial(auto)", tracks[0].name);
            TimelineClip[] clips = tracks[0].GetClips().ToArray();
            Assert.AreEqual(1, clips.Length);
            Assert.AreEqual(0d, clips[0].start, 1e-9);
            Assert.AreEqual(3d, clips[0].duration, 1e-6, "90 フレーム / 30fps");
            var fc = (FacialCorrectionClip)clips[0].asset;
            Assert.AreSame(_a, fc.track);
            Assert.AreEqual("S010__Shizuku", clips[0].displayName);
        }

        [Test]
        public void ReapplyingUpdatesInPlaceAndReportsNoChange()
        {
            AddRole("Shizuku");
            FacialTrackSyncReport first = FacialTrackTimelineSync.Apply(_tl, _a, "Shizuku");
            var clipBefore = first.Clip;
            var assetBefore = clipBefore.asset;

            FacialTrackSyncReport again = FacialTrackTimelineSync.Apply(_tl, _a, "Shizuku");
            Assert.IsTrue(again.Success);
            Assert.IsFalse(again.Changed, again.ToString());
            Assert.IsFalse(again.CreatedTrack); Assert.IsFalse(again.CreatedClip);
            Assert.AreEqual(1, FacialTracks().Length);
            Assert.AreEqual(1, FacialTracks()[0].GetClips().Count());
            Assert.AreSame(assetBefore, FacialTracks()[0].GetClips().First().asset, "クリップを作り直さない");
        }

        [Test]
        public void ChangedRangeOrFrameRateUpdatesTheClip()
        {
            AddRole("Shizuku");
            FacialTrackTimelineSync.Apply(_tl, _a, "Shizuku");
            _a.rangeEnd = 240f; _a.frameRate = 24f; // 10 秒
            FacialTrackSyncReport r = FacialTrackTimelineSync.Apply(_tl, _a, "Shizuku");
            Assert.IsTrue(r.Success); Assert.IsTrue(r.Changed); Assert.IsFalse(r.CreatedTrack); Assert.IsFalse(r.CreatedClip);
            Assert.AreEqual(10d, FacialTracks()[0].GetClips().First().duration, 1e-6);
        }

        [Test]
        public void ChangingTheReferencedAssetRepointsTheSameClip()
        {
            AddRole("Shizuku");
            FacialTrackTimelineSync.Apply(_tl, _a, "Shizuku");
            FacialTrackAsset b = NewAsset("S010__Shizuku", 30f, 0f, 30f);
            try
            {
                FacialTrackSyncReport r = FacialTrackTimelineSync.Apply(_tl, b, "Shizuku");
                Assert.IsTrue(r.Changed);
                Assert.AreSame(b, ((FacialCorrectionClip)FacialTracks()[0].GetClips().First().asset).track);
                Assert.AreEqual(1d, FacialTracks()[0].GetClips().First().duration, 1e-6);
            }
            finally { Object.DestroyImmediate(b); }
        }

        [Test]
        public void ClipValuesTheDesignerSetOnTheAutoClipSurviveAReapply()
        {
            AddRole("Shizuku");
            FacialTrackSyncReport r = FacialTrackTimelineSync.Apply(_tl, _a, "Shizuku");
            var fc = (FacialCorrectionClip)r.Clip.asset;
            fc.template.useAlpha = true; fc.template.alpha = 0.4f;
            _a.rangeEnd = 150f;
            FacialTrackTimelineSync.Apply(_tl, _a, "Shizuku");
            Assert.IsTrue(fc.template.useAlpha);
            Assert.AreEqual(0.4f, fc.template.alpha, 1e-6f);
        }

        [Test]
        public void DesignerTracksAndClipsAreNeverTouched()
        {
            AddRole("Shizuku");
            FacialCorrectionTrack mine = _tl.CreateTrack<FacialCorrectionTrack>(null, "Shizuku_Facial");           // 手で作った Facial のトラック（(auto) なし）
            TimelineClip myClip = mine.CreateClip<FacialCorrectionClip>(); myClip.start = 1; myClip.duration = 2;
            ((FacialCorrectionClip)myClip.asset).template.useAlpha = true;
            FacialCorrectionTrack other = _tl.CreateTrack<FacialCorrectionTrack>(null, "Other_Facial(auto)");     // 別の役の自動トラック
            TimelineClip otherClip = other.CreateClip<FacialCorrectionClip>(); otherClip.start = 0; otherClip.duration = 7;

            FacialTrackTimelineSync.Apply(_tl, _a, "Shizuku");
            FacialTrackTimelineSync.Apply(_tl, _a, "Shizuku");

            Assert.AreEqual(3, FacialTracks().Length);
            Assert.AreEqual(1d, myClip.start); Assert.AreEqual(2d, myClip.duration);
            Assert.IsNull(((FacialCorrectionClip)myClip.asset).track);
            Assert.IsTrue(((FacialCorrectionClip)myClip.asset).template.useAlpha);
            Assert.AreEqual(1, mine.GetClips().Count());
            Assert.AreEqual(7d, otherClip.duration);
            Assert.IsNull(((FacialCorrectionClip)otherClip.asset).track);
        }

        [Test]
        public void ExtraClipsOnTheAutoTrackAreLeftAlone()
        {
            AddRole("Shizuku");
            FacialTrackSyncReport r = FacialTrackTimelineSync.Apply(_tl, _a, "Shizuku");
            TimelineClip extra = r.Track.CreateClip<FacialCorrectionClip>(); extra.start = 10; extra.duration = 1.5;
            FacialTrackTimelineSync.Apply(_tl, _a, "Shizuku");
            Assert.AreEqual(2, r.Track.GetClips().Count());
            Assert.AreEqual(1.5d, extra.duration, 1e-9);
            Assert.IsNull(((FacialCorrectionClip)extra.asset).track);
        }

        [Test]
        public void MissingRoleTrackReportsAndChangesNothing()
        {
            AddRole("Someone");
            int before = _tl.GetOutputTracks().Count();
            FacialTrackSyncReport r = FacialTrackTimelineSync.Apply(_tl, _a, "Shizuku");
            Assert.IsFalse(r.Success); Assert.IsFalse(r.Changed);
            StringAssert.Contains("Shizuku", r.Messages[0]);
            StringAssert.Contains("アニメーショントラック", r.Messages[0]);
            Assert.AreEqual(before, _tl.GetOutputTracks().Count());
            Assert.AreEqual(0, FacialTracks().Length);
        }

        [Test]
        public void RoleTrackMustBeAnAnimationTrack()
        {
            _tl.CreateTrack<FacialCorrectionTrack>(null, "Shizuku"); // 同じ名前でも AnimationTrack でなければ役のトラックではない
            Assert.IsFalse(FacialTrackTimelineSync.Apply(_tl, _a, "Shizuku").Success);
        }

        [Test]
        public void OptionCanSkipTheRoleTrackRequirement()
        {
            var r = FacialTrackTimelineSync.Apply(_tl, _a, "Shizuku", new FacialTrackSyncOptions { RequireRoleTrack = false });
            Assert.IsTrue(r.Success);
            Assert.AreEqual(1, FacialTracks().Length);
        }

        [Test]
        public void BadArgumentsAreReportedNotThrown()
        {
            Assert.IsFalse(FacialTrackTimelineSync.Apply(null, _a, "x").Success);
            Assert.IsFalse(FacialTrackTimelineSync.Apply(_tl, null, "x").Success);
            Assert.IsFalse(FacialTrackTimelineSync.Apply(_tl, _a, "").Success);
            Assert.IsFalse(FacialTrackTimelineSync.Apply(_tl, _a, null).Success);
        }

        [Test]
        public void EmptyRangeStillGetsAPlayableClip()
        {
            AddRole("Shizuku");
            FacialTrackAsset empty = NewAsset("e", 30f, 5f, 5f);
            try
            {
                var r = FacialTrackTimelineSync.Apply(_tl, empty, "Shizuku");
                Assert.IsTrue(r.Success);
                Assert.Greater(r.Clip.duration, 0d);
            }
            finally { Object.DestroyImmediate(empty); }
        }

        [Test]
        public void AutoTrackNameFollowsTheBridgeRule()
        {
            Assert.AreEqual("Hero_2_Facial(auto)", FacialTrackTimelineSync.AutoTrackName("Hero_2"));
        }
    }
}
