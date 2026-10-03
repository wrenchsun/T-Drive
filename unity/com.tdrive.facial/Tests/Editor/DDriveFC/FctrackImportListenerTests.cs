// FT-6 / docs/19 E-8（D-Drive 1.4.0 以降）: FC-5 の ICutsceneImportListener として実装した FctrackImportListener。
// CutsceneImportResult をメモリ上で作って呼ぶ（実際の取り込みはしない・アセットを作らない）。TDRIVE_FACIAL_DDRIVE_FC か TDRIVE_DDRIVE_FC_FORCE のときだけコンパイルされる。
using System.Collections.Generic;
using System.Linq;
using DDrive.Editor.Cutscene;
using DDrive.Runtime.Cutscene;
using NUnit.Framework;
using TDrive.Facial.DDrive.Editor;
using TDrive.Facial.Timeline;
using UnityEngine;
using UnityEngine.Timeline;

namespace TDrive.Facial.Tests.DDrive
{
    public class FctrackImportListenerTests
    {
        const string Fbx = "Assets/SourceAssets/Cutscene/Story/S010__Shizuku.fbx";
        const string Fct = "Assets/SourceAssets/Cutscene/Story/S010__Shizuku.fctrack";

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
            _cut.Bindings = new CutsceneBinding[0];
            _asset = ScriptableObject.CreateInstance<FacialTrackAsset>();
            _asset.hideFlags = HideFlags.HideAndDontSave;
            _asset.frameRate = 30f; _asset.rangeEnd = 60f;
        }

        [TearDown]
        public void TearDown()
        {
            if (_cut != null) Object.DestroyImmediate(_cut);
            if (_tl != null) Object.DestroyImmediate(_tl);
            if (_asset != null) Object.DestroyImmediate(_asset);
        }

        bool Exists(string p) { return p == Fct; }
        FacialTrackAsset Load(string p) { return p == Fct ? _asset : null; }

        CutsceneImportResult Result(params CutsceneImportRole[] roles)
        {
            return new CutsceneImportResult
            {
                ShotName = "S010", Category = "Story", Data = _cut, Timeline = _tl, TimelinePath = "Assets/GameData/Cutscene/CUT_Story_S010.playable",
                IsNew = true, Roles = new List<CutsceneImportRole>(roles),
            };
        }

        CutsceneImportRole Role(string name, CutsceneImportRoleKind kind, string fbx)
        {
            TrackAsset track = _tl.GetOutputTracks().FirstOrDefault(t => t.name == name) ?? _tl.CreateTrack<AnimationTrack>(null, name);
            return new CutsceneImportRole { RoleName = name, ModelIdentifier = name, Kind = kind, Track = track, SourcePath = fbx };
        }

        FacialCorrectionTrack AutoTrack() { return _tl.GetOutputTracks().OfType<FacialCorrectionTrack>().SingleOrDefault(); }

        [Test]
        public void ItIsDiscoveredByTheDDriveListenerLookupAndTheGateSeesIt()
        {
            Assert.IsTrue(CutsceneImportListeners.Discover().Any(l => l is FctrackImportListener), "TypeCache で発見される（public・引数なしコンストラクタ）");
            Assert.IsTrue(FctrackListenerGate.Available, "型名で「リスナーがある」と分かる = 後追いは再試行しない");
            Assert.AreEqual(typeof(FctrackImportListener), System.Type.GetType(FctrackListenerGate.ListenerTypeName, true));
            Assert.AreEqual(1000, new FctrackImportListener().Order);
        }

        [Test]
        public void E8_ImportedShotWithAFctrackIsAppliedInTheSameCallWithoutARetry()
        {
            CutsceneImportResult r = Result(Role("Shizuku", CutsceneImportRoleKind.Character, Fbx));
            Assert.IsNull(AutoTrack());
            int done = FctrackImportListener.Apply(r, Exists, Load); // D-Drive が OnCutsceneShotImported を呼んだ時点
            Assert.AreEqual(1, done);
            Assert.IsNotNull(AutoTrack(), "FBX の取り込みと同じパスで Timeline に入る（delayCall の再試行なし）");
            Assert.AreEqual("Shizuku_Facial(auto)", AutoTrack().name);
            Assert.AreEqual(1, _cut.Bindings.Length);
            Assert.IsTrue(TDrive.Facial.DDrive.CutsceneBindingAccess.IsSameAsTrack(_cut.Bindings[0]), "SameAsTrack の Binding を足す（FC-1）");
        }

        [Test]
        public void ReimportCallsAgainWithoutDuplicates()
        {
            CutsceneImportResult r = Result(Role("Shizuku", CutsceneImportRoleKind.Character, Fbx));
            FctrackImportListener.Apply(r, Exists, Load);
            int bindings = _cut.Bindings.Length;
            r.IsNew = false;
            Assert.AreEqual(1, FctrackImportListener.Apply(r, Exists, Load));
            Assert.AreEqual(1, _tl.GetOutputTracks().OfType<FacialCorrectionTrack>().Count());
            Assert.AreEqual(1, AutoTrack().GetClips().Count());
            Assert.AreEqual(bindings, _cut.Bindings.Length);
        }

        [Test]
        public void OnlyCharacterRolesWithAFctrackAreTouched()
        {
            CutsceneImportResult r = Result(
                Role("Camera01", CutsceneImportRoleKind.Camera, "Assets/SourceAssets/Cutscene/Story/S010.fbx"),
                Role("Chair", CutsceneImportRoleKind.Prop, "Assets/SourceAssets/Cutscene/Story/S010__PRP_Chair.fbx"),
                Role("Hero", CutsceneImportRoleKind.Character, "Assets/SourceAssets/Cutscene/Story/S010__Hero.fbx"), // .fctrack なし
                Role("Shizuku", CutsceneImportRoleKind.Character, Fbx));
            Assert.AreEqual(1, FctrackImportListener.Apply(r, Exists, Load));
            Assert.AreEqual(1, _tl.GetOutputTracks().OfType<FacialCorrectionTrack>().Count());
            Assert.AreEqual("Shizuku_Facial(auto)", AutoTrack().name);
        }

        [Test]
        public void ADesignerMovedClipStaysWhereItIsOnReimport()
        {
            CutsceneImportResult r = Result(Role("Shizuku", CutsceneImportRoleKind.Character, Fbx));
            FctrackImportListener.Apply(r, Exists, Load);
            TimelineClip c = AutoTrack().GetClips().Single();
            c.start = 1.5; c.duration = 0.5;
            _asset.rangeEnd = 300f;
            FctrackImportListener.Apply(r, Exists, Load);
            Assert.AreEqual(1.5, c.start, 1e-6);
            Assert.AreEqual(0.5, c.duration, 1e-6);
        }

        [Test]
        public void NullAndEmptyResultsAreIgnoredAndTheRealLookupFindsNoFile()
        {
            var l = new FctrackImportListener();
            l.OnCutsceneShotImported(null);
            l.OnCutsceneShotImported(new CutsceneImportResult());
            l.OnCutsceneShotImported(new CutsceneImportResult { Data = _cut, Timeline = _tl });
            // 実際の AssetDatabase で探す（そんな .fctrack は無い）→ 何もしない
            l.OnCutsceneShotImported(Result(Role("Shizuku", CutsceneImportRoleKind.Character, "Assets/__NoSuchFolder__/S010__Shizuku.fbx")));
            Assert.IsNull(AutoTrack());
        }
    }
}
