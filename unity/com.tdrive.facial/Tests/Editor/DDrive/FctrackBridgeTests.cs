// FT-3 / FT-5: .fctrack → D-Drive のカットシーン。パスの読み取り（純粋な関数）、メモリ上の CutsceneData / TimelineAsset への反映、SameAsTrack の検証。
// アセットは一切保存しない（ScriptableObject.CreateInstance）。AssetPostprocessor の実際のコールバック・メニュー・アセットの探し方・保存は、ここでは試せない（手動確認）。
using System.Collections.Generic;
using System.Linq;
using DDrive.Editor.AssetBrowser;
using DDrive.Editor.Cutscene;
using DDrive.Foundation.Identity;
using DDrive.Runtime.Cutscene;
using DDrive.Runtime.Model;
using NUnit.Framework;
using TDrive.Facial.DDrive;
using TDrive.Facial.DDrive.Editor;
using TDrive.Facial.Tests.Timeline;
using TDrive.Facial.Timeline;
using TDrive.Facial.Timeline.Editor;
using UnityEngine;
using UnityEngine.Timeline;

namespace TDrive.Facial.Tests.DDrive
{
    public class FctrackBridgeTests
    {
        const string Root = "Assets/SourceAssets";
        CutsceneData _cut;
        TimelineAsset _tl;
        FacialTrackAsset _asset;

        [SetUp]
        public void SetUp()
        {
            _tl = ScriptableObject.CreateInstance<TimelineAsset>();
            _tl.hideFlags = HideFlags.HideAndDontSave;
            _cut = ScriptableObject.CreateInstance<CutsceneData>();
            _cut.hideFlags = HideFlags.HideAndDontSave;
            _cut.Timeline = _tl;
            _asset = ScriptableObject.CreateInstance<FacialTrackAsset>();
            _asset.hideFlags = HideFlags.HideAndDontSave;
            _asset.name = "S010__Shizuku"; _asset.frameRate = 30f; _asset.rangeEnd = 60f;
        }

        [TearDown]
        public void TearDown()
        {
            if (_cut != null) Object.DestroyImmediate(_cut);
            if (_tl != null) Object.DestroyImmediate(_tl);
            if (_asset != null) Object.DestroyImmediate(_asset);
            if (_h != null) { _h.Dispose(); _h = null; }
        }

        // ---------------------------------------------------------------- パス（純粋）

        [Test]
        public void PathUnderTheCutsceneFolderIsParsed()
        {
            FctrackShotInfo i;
            Assert.IsTrue(FctrackShotPath.TryParse("Assets/SourceAssets/Cutscene/Story/S010__Shizuku.fctrack", Root, out i));
            Assert.AreEqual("Story", i.Category);
            Assert.AreEqual("S010", i.Shot);
            Assert.AreEqual("Shizuku", i.RoleTrackName);
            Assert.AreEqual("Assets/SourceAssets/Cutscene/Story/S010__Shizuku.fctrack", i.AssetPath);

            Assert.IsTrue(FctrackShotPath.TryParse("Assets/SourceAssets/Cutscene/S010__Hero_2.fctrack", Root, out i));
            Assert.AreEqual("", i.Category);
            Assert.AreEqual("Hero_2", i.RoleTrackName, "'_2' の末尾も含む（D-Drive のトラック名と同じ）");

            Assert.IsTrue(FctrackShotPath.TryParse("Assets\\SourceAssets\\Cutscene\\A\\B\\S1__X.FCTRACK", Root + "/", out i));
            Assert.AreEqual("A/B", i.Category);
            Assert.AreEqual("Assets/SourceAssets/Cutscene/A/B/S1__X.FCTRACK", i.AssetPath);
        }

        [TestCase("Assets/SourceAssets/Cutscene/Story/S010.fctrack", Description = "ショット名だけ（モデルが無い）")]
        [TestCase("Assets/SourceAssets/Cutscene/Story/S010__.fctrack")]
        [TestCase("Assets/SourceAssets/Cutscene/Story/__Shizuku.fctrack")]
        [TestCase("Assets/SourceAssets/Model/S010__Shizuku.fctrack", Description = "Cutscene フォルダの外")]
        [TestCase("Assets/Other/Cutscene/S010__Shizuku.fctrack")]
        [TestCase("Assets/sourceassets/Cutscene/S010__Shizuku.fctrack", Description = "先頭一致は大文字小文字を区別（D-Drive と同じ）")]
        [TestCase("Assets/SourceAssets/Cutscene/S010__Shizuku.fbx")]
        [TestCase("Assets/SourceAssets/Cutscene/S010__Shizuku.fctrack.meta")]
        [TestCase("")]
        [TestCase(null)]
        public void OtherPathsAreNotCutsceneFctracks(string path)
        {
            FctrackShotInfo i;
            Assert.IsFalse(FctrackShotPath.TryParse(path, Root, out i));
        }

        [Test]
        public void CutsceneDataPathFollowsD_DriveNaming()
        {
            FctrackShotInfo i;
            Assert.IsTrue(FctrackShotPath.TryParse("Assets/SourceAssets/Cutscene/Story/S010__Shizuku.fctrack", Root, out i));
            string p = FctrackShotPath.CutsceneDataPath(i, "Assets/GameData", "Cutscene");
            Assert.AreEqual(CutsceneImportService.ComputeCutsceneDataPath("Assets/GameData", "Story", AssetNamingService.ToIdentifier("S010", "Cutscene")), p);
            StringAssert.StartsWith("Assets/GameData/", p);
            StringAssert.EndsWith(".asset", p);
            StringAssert.Contains("CUT_", p);

            Assert.IsTrue(FctrackShotPath.TryParse("Assets/SourceAssets/Cutscene/S010__Shizuku.fctrack", Root, out i));
            StringAssert.EndsWith("/CUT_S010.asset", FctrackShotPath.CutsceneDataPath(i, "Assets/GameData", "Cutscene"));
        }

        [Test]
        public void SelectTargetsKeepsOnlyCutsceneFctracksWithoutDuplicates()
        {
            var paths = new[]
            {
                "Assets/SourceAssets/Cutscene/S010__Shizuku.fctrack",
                "Assets/SourceAssets/Cutscene/S010__Shizuku.fbx",
                "Assets/SourceAssets/Cutscene/S010.fbx",
                "Assets/SourceAssets/Cutscene/S010__Shizuku.fctrack",
                "Assets/Other/X__Y.fctrack",
                "Assets/SourceAssets/Cutscene/S020__Hero.fctrack",
            };
            List<string> t = FctrackTimelineHook.SelectTargets(paths, Root);
            CollectionAssert.AreEqual(new[] { "Assets/SourceAssets/Cutscene/S010__Shizuku.fctrack", "Assets/SourceAssets/Cutscene/S020__Hero.fctrack" }, t);
            Assert.AreEqual(0, FctrackTimelineHook.SelectTargets(null, Root).Count);
        }

        // ---------------------------------------------------------------- 反映（メモリ上）

        AnimationTrack AddRole(string role) { return _tl.CreateTrack<AnimationTrack>(null, role); }

        [Test]
        public void ApplyAddsTheTrackAndABindingOnce()
        {
            AddRole("Shizuku");
            _cut.Bindings = new[] { new CutsceneBinding { TrackName = "Shizuku", Target = CutsceneBindTarget.SpawnModel, Model = new AssetId<ModelMarker>(123, AssetType.Model) } };

            FctrackSyncResult r = FctrackCutsceneSync.ApplyToCutscene(_cut, _asset, "Shizuku");
            Assert.AreEqual(FctrackSyncStatus.Done, r.Status, r.Message);
            Assert.IsTrue(r.Changed);
            Assert.AreEqual(FctrackBindingResult.Added, r.Binding);
            Assert.AreEqual(1, _tl.GetOutputTracks().OfType<FacialCorrectionTrack>().Count());

            Assert.AreEqual(2, _cut.Bindings.Length);
            CutsceneBinding b = _cut.Bindings[1];
            Assert.AreEqual("Shizuku_Facial(auto)", b.TrackName);
            Assert.AreEqual(CutsceneBindTarget.SameAsTrack, b.Target);
            Assert.AreEqual("Shizuku", b.SourceTrackName);
            Assert.IsFalse(b.Model.IsValid, "直前の Binding の Model を引きずらない");
            Assert.IsTrue(string.IsNullOrEmpty(b.SceneObjectName));
            // 元の Binding はそのまま
            Assert.AreEqual("Shizuku", _cut.Bindings[0].TrackName);
            Assert.AreEqual(CutsceneBindTarget.SpawnModel, _cut.Bindings[0].Target);
            Assert.AreEqual(123UL, _cut.Bindings[0].Model.Value);

            // 2 回目: 変化なし・Binding は増えない
            FctrackSyncResult again = FctrackCutsceneSync.ApplyToCutscene(_cut, _asset, "Shizuku");
            Assert.AreEqual(FctrackSyncStatus.Done, again.Status);
            Assert.IsFalse(again.Changed, again.Message);
            Assert.AreEqual(FctrackBindingResult.AlreadyOk, again.Binding);
            Assert.AreEqual(2, _cut.Bindings.Length);
        }

        [Test]
        public void ApplyWorksWhenThereAreNoBindingsYet()
        {
            AddRole("Shizuku");
            _cut.Bindings = new CutsceneBinding[0];
            Assert.AreEqual(FctrackSyncStatus.Done, FctrackCutsceneSync.ApplyToCutscene(_cut, _asset, "Shizuku").Status);
            Assert.AreEqual(1, _cut.Bindings.Length);
            Assert.AreEqual(CutsceneBindTarget.SameAsTrack, _cut.Bindings[0].Target);
        }

        [Test]
        public void ADesignerBindingWithTheSameNameIsNeverChanged()
        {
            AddRole("Shizuku");
            _cut.Bindings = new[] { new CutsceneBinding { TrackName = "Shizuku_Facial(auto)", Target = CutsceneBindTarget.Self } };
            FctrackSyncResult r = FctrackCutsceneSync.ApplyToCutscene(_cut, _asset, "Shizuku");
            Assert.AreEqual(FctrackSyncStatus.Done, r.Status);
            Assert.AreEqual(FctrackBindingResult.ConflictLeftAlone, r.Binding);
            Assert.AreEqual(1, _cut.Bindings.Length);
            Assert.AreEqual(CutsceneBindTarget.Self, _cut.Bindings[0].Target);
            StringAssert.Contains("注意", r.Message);
        }

        [Test]
        public void ADesignerMadeSameAsTrackBindingToADifferentSourceIsLeftAlone()
        {
            AddRole("Shizuku");
            _cut.Bindings = new[] { new CutsceneBinding { TrackName = "Shizuku_Facial(auto)", Target = CutsceneBindTarget.SameAsTrack, SourceTrackName = "Other" } };
            Assert.AreEqual(FctrackBindingResult.ConflictLeftAlone, FctrackCutsceneSync.ApplyToCutscene(_cut, _asset, "Shizuku").Binding);
            Assert.AreEqual("Other", _cut.Bindings[0].SourceTrackName);
        }

        [Test]
        public void MissingRoleTrackIsNotReadyAndChangesNothing()
        {
            _cut.Bindings = new CutsceneBinding[0];
            FctrackSyncResult r = FctrackCutsceneSync.ApplyToCutscene(_cut, _asset, "Shizuku");
            Assert.AreEqual(FctrackSyncStatus.NotReady, r.Status);
            StringAssert.Contains("Shizuku", r.Message);
            Assert.AreEqual(0, _cut.Bindings.Length);
            Assert.AreEqual(0, _tl.GetOutputTracks().Count());
        }

        [Test]
        public void MissingCutsceneOrTimelineIsNotReadyAndBadInputFails()
        {
            Assert.AreEqual(FctrackSyncStatus.NotReady, FctrackCutsceneSync.ApplyToCutscene(null, _asset, "Shizuku").Status);
            _cut.Timeline = null;
            Assert.AreEqual(FctrackSyncStatus.NotReady, FctrackCutsceneSync.ApplyToCutscene(_cut, _asset, "Shizuku").Status);
            Assert.AreEqual(FctrackSyncStatus.Failed, FctrackCutsceneSync.ApplyToCutscene(_cut, null, "Shizuku").Status);
            Assert.AreEqual(FctrackSyncStatus.Failed, FctrackCutsceneSync.ApplyToCutscene(_cut, _asset, "").Status);
        }

        [Test]
        public void ReapplyAfterTheRangeChangedUpdatesTheClipButNotTheBindings()
        {
            AddRole("Shizuku");
            FctrackCutsceneSync.ApplyToCutscene(_cut, _asset, "Shizuku");
            _asset.rangeEnd = 300f;
            FctrackSyncResult r = FctrackCutsceneSync.ApplyToCutscene(_cut, _asset, "Shizuku");
            Assert.IsTrue(r.Changed);
            Assert.AreEqual(FctrackBindingResult.AlreadyOk, r.Binding);
            Assert.AreEqual(10d, _tl.GetOutputTracks().OfType<FacialCorrectionTrack>().Single().GetClips().Single().duration, 1e-6);
            Assert.AreEqual(1, _cut.Bindings.Length);
        }

        // ---------------------------------------------------------------- 検証（SameAsTrack）

        FacialTimelineTestHarness _h;

        FacialTimelineTestHarness Harness()
        {
            if (_h == null) _h = new FacialTimelineTestHarness();
            return _h;
        }

        static FacialModelLookup Known(FacialCorrectionRunner r) { return new FacialModelLookup { Resolvable = true, Runner = r }; }
        static List<FacialCutsceneIssue> Of(List<FacialCutsceneIssue> l, string code) { return l.Where(i => i.Code == code).ToList(); }

        [Test]
        public void SameAsTrackBindingResolvesAndPassesTheReferencedBindingToTheModelLookup()
        {
            FacialTimelineTestHarness h = Harness();
            h.timeline.CreateTrack<AnimationTrack>(null, "Shizuku");
            h.AddTrack("Face", false); // '<役名>_Facial' の規則に合わない名前でも、SameAsTrack なら解決できる
            var bindings = new[]
            {
                new CutsceneBinding { TrackName = "Shizuku", Target = CutsceneBindTarget.SpawnModel },
                new CutsceneBinding { TrackName = "Face", Target = CutsceneBindTarget.SameAsTrack, SourceTrackName = "Shizuku" },
            };
            CutsceneBinding seen = default(CutsceneBinding);
            List<FacialCutsceneIssue> r = FacialCutsceneChecks.Check(h.timeline, bindings, b => { seen = b; return Known(h.rig.runner); });
            Assert.AreEqual(0, r.Count, string.Join("\n", r));
            Assert.AreEqual("Shizuku", seen.TrackName, "参照先の Binding でモデルを引く");
            Assert.AreEqual(CutsceneBindTarget.SpawnModel, seen.Target);
        }

        [Test]
        public void SameAsTrackChainIsFollowed()
        {
            FacialTimelineTestHarness h = Harness();
            h.timeline.CreateTrack<AnimationTrack>(null, "Shizuku");
            h.AddTrack("Face", false);
            var bindings = new[]
            {
                new CutsceneBinding { TrackName = "Face", Target = CutsceneBindTarget.SameAsTrack, SourceTrackName = "Mid" },
                new CutsceneBinding { TrackName = "Mid", Target = CutsceneBindTarget.SameAsTrack, SourceTrackName = "Shizuku" },
                new CutsceneBinding { TrackName = "Shizuku", Target = CutsceneBindTarget.SpawnModel },
            };
            CutsceneBinding seen = default(CutsceneBinding);
            Assert.AreEqual(0, FacialCutsceneChecks.Check(h.timeline, bindings, b => { seen = b; return Known(h.rig.runner); }).Count);
            Assert.AreEqual("Shizuku", seen.TrackName);
        }

        [Test]
        public void SameAsTrackProblemsAreReportedAsUnbound()
        {
            FacialTimelineTestHarness h = Harness();
            h.timeline.CreateTrack<AnimationTrack>(null, "Shizuku");
            h.AddTrack("Face", false);
            CutsceneBinding Same(string name, string src) { return new CutsceneBinding { TrackName = name, Target = CutsceneBindTarget.SameAsTrack, SourceTrackName = src }; }

            // 参照先が空 / Binding が無い / 自己参照 / 循環 / 参照先のトラックが Timeline に無い
            var cases = new[]
            {
                (new[] { Same("Face", "") }, "SourceTrackName が空"),
                (new[] { Same("Face", "Nowhere") }, "一致する Binding がありません"),
                (new[] { Same("Face", "Face") }, "自分自身"),
                (new[] { Same("Face", "B"), Same("B", "Face") }, "循環"),
                (new[] { Same("Face", "Ghost"), new CutsceneBinding { TrackName = "Ghost", Target = CutsceneBindTarget.Self } }, "Timeline にありません"),
            };
            foreach (var (bindings, text) in cases)
            {
                List<FacialCutsceneIssue> r = FacialCutsceneChecks.Check(h.timeline, bindings, b => Known(h.rig.runner));
                Assert.AreEqual(1, Of(r, FacialCutsceneChecks.CodeUnbound).Count, text + ": " + string.Join("\n", r));
                StringAssert.Contains(text, r[0].Message);
                StringAssert.Contains("SameAsTrack", r[0].Message);
            }
        }

        [Test]
        public void WithoutABindingEntryTheRoleNamingRuleStillApplies()
        {
            FacialTimelineTestHarness h = Harness();
            h.timeline.CreateTrack<AnimationTrack>(null, "Shizuku");
            h.AddTrack("Shizuku_Facial(auto)", false);
            var bindings = new[] { new CutsceneBinding { TrackName = "Shizuku", Target = CutsceneBindTarget.SpawnModel } };
            Assert.AreEqual(0, FacialCutsceneChecks.Check(h.timeline, bindings, b => Known(h.rig.runner)).Count);
            // 役名のアニメーショントラックの Binding が無い → 001
            List<FacialCutsceneIssue> r = FacialCutsceneChecks.Check(h.timeline, new CutsceneBinding[0], b => Known(h.rig.runner));
            Assert.AreEqual(1, Of(r, FacialCutsceneChecks.CodeUnbound).Count);
            StringAssert.Contains("Bindings に 'Shizuku'", r[0].Message);
            // 役名の規則にも合わない → 001。メッセージは SameAsTrack を案内する
            h.AddTrack("Lonely", false);
            r = FacialCutsceneChecks.Check(h.timeline, new CutsceneBinding[0], b => Known(h.rig.runner));
            Assert.IsTrue(r.Any(i => i.TrackName == "Lonely" && i.Message.Contains("SameAsTrack")));
        }

        [Test]
        public void EmotionLayersInTheFctrackCurvesAreChecked()
        {
            FacialTimelineTestHarness h = Harness();
            h.timeline.CreateTrack<AnimationTrack>(null, "Shizuku");
            FacialCorrectionTrack ft = h.AddTrack("Shizuku_Facial(auto)", false);
            FacialCorrectionClip c = h.AddClip(ft, 0, 2);
            c.track = _asset;
            _asset.emotions = new[]
            {
                new FacialEmotionCurve { layer = "Joy", keys = new[] { new FacialKey(0f, 1f) } },
                new FacialEmotionCurve { layer = "Rage", keys = new[] { new FacialKey(0f, 1f) } },
            };
            var bindings = new[] { new CutsceneBinding { TrackName = "Shizuku", Target = CutsceneBindTarget.SpawnModel } };
            List<FacialCutsceneIssue> r = Of(FacialCutsceneChecks.Check(h.timeline, bindings, b => Known(h.rig.runner)), FacialCutsceneChecks.CodeUnknownLayer);
            Assert.AreEqual(1, r.Count);
            StringAssert.Contains("Rage", r[0].Message);
            StringAssert.Contains(_asset.name, r[0].Message);
        }
    }
}
