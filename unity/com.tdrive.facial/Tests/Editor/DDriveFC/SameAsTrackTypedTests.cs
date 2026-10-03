// FC-1（D-Drive 1.4.0 以降）の型を直接使うテスト。TDRIVE_FACIAL_DDRIVE_FC（1.4.0 以降で自動）か TDRIVE_DDRIVE_FC_FORCE（プロジェクトの Scripting Define で手動）のときだけ
// コンパイル・実行される（アセンブリ TDrive.Facial.DDrive.FC.Tests の defineConstraints）。基本のアセンブリのリフレクション経由の読み書きが、型を直接使った結果と一致することも確かめる。
using System.Collections.Generic;
using System.Linq;
using DDrive.Runtime.Cutscene;
using NUnit.Framework;
using TDrive.Facial.DDrive;
using TDrive.Facial.DDrive.Editor;
using TDrive.Facial.Tests.Timeline;
using UnityEngine;
using UnityEngine.Timeline;

namespace TDrive.Facial.Tests.DDrive
{
    public class SameAsTrackTypedTests
    {
        FacialTimelineTestHarness _h;
        CutsceneData _cut;

        [SetUp]
        public void SetUp() { _h = new FacialTimelineTestHarness(); }

        [TearDown]
        public void TearDown()
        {
            if (_cut != null) Object.DestroyImmediate(_cut);
            _h.Dispose();
        }

        static CutsceneBinding Same(string name, string src)
        {
            return new CutsceneBinding { TrackName = name, Target = CutsceneBindTarget.SameAsTrack, SourceTrackName = src };
        }

        static FacialModelLookup Known(FacialCorrectionRunner r) { return new FacialModelLookup { Resolvable = true, Runner = r }; }

        [Test]
        public void ReflectionAccessAgreesWithTheTypedMembers()
        {
            Assert.IsTrue(CutsceneBindingAccess.SameAsTrackSupported);
            CutsceneBinding typed = Same("Face", "Shizuku");
            Assert.IsTrue(CutsceneBindingAccess.IsSameAsTrack(typed));
            Assert.AreEqual("Shizuku", CutsceneBindingAccess.GetSourceTrackName(typed));
            CutsceneBinding made;
            Assert.IsTrue(CutsceneBindingAccess.TryMakeSameAsTrack("Face", "Shizuku", out made));
            Assert.AreEqual(CutsceneBindTarget.SameAsTrack, made.Target);
            Assert.AreEqual("Shizuku", made.SourceTrackName);
            Assert.IsFalse(CutsceneBindingAccess.IsSameAsTrack(new CutsceneBinding { Target = CutsceneBindTarget.Self }));
        }

        [Test]
        public void EnsureBindingWritesTheTypedFields()
        {
            _cut = ScriptableObject.CreateInstance<CutsceneData>();
            _cut.hideFlags = HideFlags.HideAndDontSave;
            _cut.Bindings = new[] { new CutsceneBinding { TrackName = "Shizuku", Target = CutsceneBindTarget.SpawnModel } };
            Assert.AreEqual(FctrackBindingResult.Added, FctrackCutsceneSync.EnsureSameAsTrackBinding(_cut, "Shizuku_Facial(auto)", "Shizuku"));
            Assert.AreEqual(2, _cut.Bindings.Length);
            Assert.AreEqual(CutsceneBindTarget.SameAsTrack, _cut.Bindings[1].Target);
            Assert.AreEqual("Shizuku", _cut.Bindings[1].SourceTrackName);
            Assert.AreEqual(FctrackBindingResult.AlreadyOk, FctrackCutsceneSync.EnsureSameAsTrackBinding(_cut, "Shizuku_Facial(auto)", "Shizuku"));
        }

        [Test]
        public void ChainIsFollowedAndTheReferencedBindingReachesTheModelLookup()
        {
            _h.timeline.CreateTrack<AnimationTrack>(null, "Shizuku");
            _h.AddTrack("Face", false);
            var bindings = new[]
            {
                Same("Face", "Mid"), Same("Mid", "Shizuku"),
                new CutsceneBinding { TrackName = "Shizuku", Target = CutsceneBindTarget.SpawnModel },
            };
            CutsceneBinding seen = default(CutsceneBinding);
            List<FacialCutsceneIssue> r = FacialCutsceneChecks.Check(_h.timeline, bindings, b => { seen = b; return Known(_h.rig.runner); });
            Assert.AreEqual(0, r.Count, string.Join("\n", r));
            Assert.AreEqual("Shizuku", seen.TrackName);
        }

        [Test]
        public void SelfReferenceAndCycleAreReported()
        {
            _h.timeline.CreateTrack<AnimationTrack>(null, "Shizuku");
            _h.AddTrack("Face", false);
            var self = FacialCutsceneChecks.Check(_h.timeline, new[] { Same("Face", "Face") }, b => Known(_h.rig.runner));
            Assert.AreEqual(1, self.Count(i => i.Code == FacialCutsceneChecks.CodeUnbound));
            var cycle = FacialCutsceneChecks.Check(_h.timeline, new[] { Same("Face", "B"), Same("B", "Face") }, b => Known(_h.rig.runner));
            Assert.AreEqual(1, cycle.Count(i => i.Code == FacialCutsceneChecks.CodeUnbound));
        }
    }
}
