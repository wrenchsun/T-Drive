// FT-2: D-Drive ブリッジ。役名の規則によるバインド解決、検証（IValidator とその純粋な検査関数）。
// CutsceneData はメモリ上で作る（ScriptableObject.CreateInstance。D-Drive のレジストリ・アセットには触れない）。
using System.Collections.Generic;
using System.Linq;
using global::DDrive.Foundation.Validation;
using DDrive.Runtime.Cutscene;
using NUnit.Framework;
using TDrive.Facial.DDrive;
using TDrive.Facial.DDrive.Editor;
using TDrive.Facial.Tests.Timeline;
using TDrive.Facial.Timeline;
using UnityEngine;
using UnityEngine.Timeline;

namespace TDrive.Facial.Tests.DDrive
{
    public class FacialDDriveBridgeTests
    {
        FacialTimelineTestHarness _h;
        CutsceneData _cut;
        FacialPoseAsset _pose;
        System.Func<UnityEngine.Playables.PlayableDirector, TrackAsset, Object> _savedResolver;

        [SetUp]
        public void SetUp()
        {
            _h = new FacialTimelineTestHarness();
            _savedResolver = FacialTimelineBinding.FallbackResolver;
            FacialTimelineBinding.FallbackResolver = null;
            FacialDDriveBinding.Register();
        }

        [TearDown]
        public void TearDown()
        {
            FacialTimelineBinding.FallbackResolver = _savedResolver;
            if (_cut != null) Object.DestroyImmediate(_cut);
            if (_pose != null) Object.DestroyImmediate(_pose);
            _h.Dispose();
        }

        AnimationTrack AddRoleAnimationTrack(string role, bool bind)
        {
            var anim = _h.timeline.CreateTrack<AnimationTrack>(role);
            if (bind) _h.director.SetGenericBinding(anim, _h.animator);
            return anim;
        }

        // ---------------------------------------------------------------- バインド

        [Test]
        public void TryGetRoleStripsTheFacialSuffixes()
        {
            string role;
            Assert.IsTrue(FacialDDriveBinding.TryGetRole("Shizuku_Facial", out role)); Assert.AreEqual("Shizuku", role);
            Assert.IsTrue(FacialDDriveBinding.TryGetRole("Shizuku_Facial(auto)", out role)); Assert.AreEqual("Shizuku", role);
            Assert.IsFalse(FacialDDriveBinding.TryGetRole("Shizuku", out role));
            Assert.IsFalse(FacialDDriveBinding.TryGetRole("_Facial", out role));
            Assert.IsFalse(FacialDDriveBinding.TryGetRole(null, out role));
        }

        [Test]
        public void FacialTrackResolvesTheAnimatorBoundToTheSameRoleAnimationTrack()
        {
            AddRoleAnimationTrack("Shizuku", true);
            FacialCorrectionTrack ft = _h.AddTrack("Shizuku_Facial", false);
            Assert.AreSame(_h.animator, FacialDDriveBinding.ResolveFallback(_h.director, ft));
            Assert.AreSame(_h.rig.runner, FacialTimelineBinding.Resolve(_h.director, ft, null));
        }

        [Test]
        public void AutoSuffixAlsoResolvesAndOtherRolesDoNot()
        {
            AddRoleAnimationTrack("Shizuku", true);
            Assert.AreSame(_h.animator, FacialDDriveBinding.ResolveFallback(_h.director, _h.AddTrack("Shizuku_Facial(auto)", false)));
            Assert.IsNull(FacialDDriveBinding.ResolveFallback(_h.director, _h.AddTrack("Other_Facial", false)));
            Assert.IsNull(FacialDDriveBinding.ResolveFallback(_h.director, _h.AddTrack("NoSuffix", false)));
        }

        [Test]
        public void MixerUsesTheRoleAnimatorWhenTheFacialTrackIsUnbound()
        {
            AddRoleAnimationTrack("Shizuku", true);
            FacialCorrectionTrack ft = _h.AddTrack("Shizuku_Facial", false);
            FacialCorrectionClip c = _h.AddClip(ft, 0, 2);
            c.template.useAlpha = true; c.template.alpha = 0.5f;
            _h.At(1.0);
            Assert.AreEqual(50f, _h.rig.W(FacialTestRig.N("Neutral", 1, 1)), 0.5f);
        }

        [Test]
        public void ExplicitBindingWinsOverTheRoleAnimationTrack()
        {
            var other = new FacialTestRig();
            try
            {
                other.root.AddComponent<Animator>();
                AddRoleAnimationTrack("Shizuku", true); // _h の Animator
                FacialCorrectionTrack ft = _h.AddTrack("Shizuku_Facial", false);
                _h.director.SetGenericBinding(ft, other.root.GetComponent<Animator>());
                other.runner.useManualAngles = true;
                FacialCorrectionClip c = _h.AddClip(ft, 0, 2);
                c.template.useAlpha = true; c.template.alpha = 0.5f;
                _h.At(1.0);
                Assert.AreEqual(50f, other.W(FacialTestRig.N("Neutral", 1, 1)), 0.5f);
                Assert.AreEqual(0f, _h.rig.FcSum(), 1e-6f);
            }
            finally { other.Dispose(); }
        }

        // ---------------------------------------------------------------- 検査（純粋な関数）

        static FacialModelLookup Known(FacialCorrectionRunner r) { return new FacialModelLookup { Resolvable = true, Runner = r }; }

        static List<FacialCutsceneIssue> Of(List<FacialCutsceneIssue> l, string code) { return l.Where(i => i.Code == code).ToList(); }

        [Test]
        public void CheckReportsAnUnresolvableBinding()
        {
            _h.AddTrack("Lonely_Facial", false);
            List<FacialCutsceneIssue> r = FacialCutsceneChecks.Check(_h.timeline, new CutsceneBinding[0], b => default(FacialModelLookup));
            Assert.AreEqual(1, Of(r, FacialCutsceneChecks.CodeUnbound).Count);
        }

        [Test]
        public void CheckAcceptsABindingEntryOrABoundRoleAnimationTrack()
        {
            _h.AddTrack("A_Facial", false);                       // 自分の名前の Bindings
            _h.AddTrack("B_Facial", false); AddRoleAnimationTrack("B", false); // 役名の AnimationTrack + その Bindings
            var bindings = new[]
            {
                new CutsceneBinding { TrackName = "A_Facial", Target = CutsceneBindTarget.Self },
                new CutsceneBinding { TrackName = "B", Target = CutsceneBindTarget.Self },
            };
            Assert.AreEqual(0, Of(FacialCutsceneChecks.Check(_h.timeline, bindings, b => default(FacialModelLookup)), FacialCutsceneChecks.CodeUnbound).Count);
        }

        [Test]
        public void CheckReportsARoleAnimationTrackThatItselfHasNoBinding()
        {
            _h.AddTrack("B_Facial", false); AddRoleAnimationTrack("B", false);
            List<FacialCutsceneIssue> r = FacialCutsceneChecks.Check(_h.timeline, new CutsceneBinding[0], b => default(FacialModelLookup));
            Assert.AreEqual(1, Of(r, FacialCutsceneChecks.CodeUnbound).Count);
        }

        [Test]
        public void CheckReportsUnknownEmotionLayersOnlyWhenTheModelIsKnown()
        {
            FacialCorrectionTrack ft = _h.AddTrack("A_Facial", false);
            FacialCorrectionClip c = _h.AddClip(ft, 0, 2);
            c.template.emotions = new[] { new FacialEmotionEntry { layer = "Joy", weight = 1f }, new FacialEmotionEntry { layer = "Rage", weight = 1f } };
            var bindings = new[] { new CutsceneBinding { TrackName = "A_Facial", Target = CutsceneBindTarget.SpawnModel } };

            List<FacialCutsceneIssue> known = FacialCutsceneChecks.Check(_h.timeline, bindings, b => Known(_h.rig.runner));
            List<FacialCutsceneIssue> layers = Of(known, FacialCutsceneChecks.CodeUnknownLayer);
            Assert.AreEqual(1, layers.Count);
            StringAssert.Contains("Rage", layers[0].Message);

            Assert.AreEqual(0, FacialCutsceneChecks.Check(_h.timeline, bindings, b => default(FacialModelLookup)).Count, "静的に分からないときは飛ばす");
        }

        [Test]
        public void CheckReportsAPrefabWithoutRunnerOrData()
        {
            _h.AddTrack("A_Facial", false);
            var bindings = new[] { new CutsceneBinding { TrackName = "A_Facial", Target = CutsceneBindTarget.SpawnModel } };
            Assert.AreEqual(1, Of(FacialCutsceneChecks.Check(_h.timeline, bindings, b => Known(null)), FacialCutsceneChecks.CodeNoRunner).Count);
            _h.rig.runner.data = null;
            Assert.AreEqual(1, Of(FacialCutsceneChecks.Check(_h.timeline, bindings, b => Known(_h.rig.runner)), FacialCutsceneChecks.CodeNoData).Count);
        }

        // ---------------------------------------------------------------- IValidator（CutsceneData をメモリ上で作る）

        [Test]
        public void ValidatorFindsABrokenCutsceneAndIsDiscoverable()
        {
            _cut = ScriptableObject.CreateInstance<CutsceneData>();
            _cut.hideFlags = HideFlags.HideAndDontSave;
            _cut.Timeline = _h.timeline;
            _cut.Bindings = new CutsceneBinding[0];
            _h.AddTrack("Lonely_Facial", false);

            var validator = new FacialCutsceneValidator();
            Assert.AreEqual(global::DDrive.Foundation.Identity.AssetType.Cutscene, validator.Target);
            List<ValidationResult> results = validator.Validate(_cut, new ValidationContext(new global::DDrive.Foundation.Data.AssetDataBase[] { _cut })).ToList();
            Assert.AreEqual(1, results.Count);
            Assert.AreEqual(FacialCutsceneChecks.CodeUnbound, results[0].Code);
            Assert.AreEqual(ValidationSeverity.Warning, results[0].Severity);

            // D-Drive の CI と同じ発見規則（引数なしコンストラクタを持つ IValidator）で見つかる
            Assert.IsTrue(global::DDrive.Editor.CI.DiscoverValidators().Any(v => v is FacialCutsceneValidator));
        }

        [Test]
        public void ValidatorStaysQuietForAHealthyCutsceneAndForOnesWithoutFacialTracks()
        {
            _cut = ScriptableObject.CreateInstance<CutsceneData>();
            _cut.hideFlags = HideFlags.HideAndDontSave;
            _cut.Timeline = _h.timeline;
            AddRoleAnimationTrack("Shizuku", false);
            _h.AddTrack("Shizuku_Facial", false);
            _cut.Bindings = new[] { new CutsceneBinding { TrackName = "Shizuku", Target = CutsceneBindTarget.Self } };
            var ctx = new ValidationContext(new global::DDrive.Foundation.Data.AssetDataBase[] { _cut });
            Assert.AreEqual(0, new FacialCutsceneValidator().Validate(_cut, ctx).Count());

            _cut.Timeline = null;
            Assert.AreEqual(0, new FacialCutsceneValidator().Validate(_cut, ctx).Count());
        }

        [Test]
        public void EmptyOrPresentPoseIsNotReportedAsMissing()
        {
            FacialCorrectionClip c = _h.AddClip(_h.AddTrack("A_Facial", false), 0, 2);
            Assert.AreEqual(0, FacialCutsceneValidator.FindMissingPoseReferences(_h.timeline).Count, "ポーズを使わないクリップは問題なし");
            _pose = ScriptableObject.CreateInstance<FacialPoseAsset>();
            c.template.pose = _pose;
            Assert.AreEqual(0, FacialCutsceneValidator.FindMissingPoseReferences(_h.timeline).Count);
        }

        // ---------------------------------------------------------------- プール返却

        [Test]
        public void DeactivatingTheModelResetsTheFacialShapes()
        {
            // D-Drive の PoolService.ForceReturn は SetActive(false) する（読んで確認）。ここでは Runner が無効化で FC_ を戻すことを確かめる。
            // 編集時は OnDisable が呼ばれないので、同じ後片付け（ResetWeights）を直接呼ぶ。
            _h.rig.runner.EvaluateNow(0f, 0f, 1f);
            Assert.Greater(_h.rig.FcSum(), 50f);
            _h.rig.runner.ResetWeights();
            Assert.AreEqual(0f, _h.rig.FcSum(), 1e-6f);
        }
    }
}
