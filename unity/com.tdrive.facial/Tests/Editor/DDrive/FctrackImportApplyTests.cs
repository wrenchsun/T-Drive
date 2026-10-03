// FT-6 / docs/19 E-8: 取り込み完了の通知（FC-5）で .fctrack を反映する共通部分（D-Drive 1.4.0 の型を使わない）と、後追いの取り込み（AssetPostprocessor）との分担。
// アセットは一切作らない・保存しない（ScriptableObject.CreateInstance のメモリ上。.fctrack の読み込みは差し替えの関数）。
using System.Linq;
using DDrive.Runtime.Cutscene;
using NUnit.Framework;
using TDrive.Facial.DDrive.Editor;
using TDrive.Facial.Timeline;
using UnityEngine;
using UnityEngine.Timeline;

namespace TDrive.Facial.Tests.DDrive
{
    public class FctrackImportApplyTests
    {
        const string Fbx = "Assets/SourceAssets/Cutscene/Story/S010__Shizuku.fbx";
        const string Fct = "Assets/SourceAssets/Cutscene/Story/S010__Shizuku.fctrack";

        CutsceneData _cut;
        TimelineAsset _tl;
        FacialTrackAsset _asset;
        int _loads;

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
            _loads = 0;
        }

        [TearDown]
        public void TearDown()
        {
            FctrackListenerGate.OverrideForTests = null;
            if (_cut != null) Object.DestroyImmediate(_cut);
            if (_tl != null) Object.DestroyImmediate(_tl);
            if (_asset != null) Object.DestroyImmediate(_asset);
        }

        bool Exists(string p) { return p == Fct; }
        FacialTrackAsset Load(string p) { _loads++; return p == Fct ? _asset : null; }

        FctrackSyncResult Apply(string fbx = Fbx, string role = "Shizuku")
        {
            return FctrackImportApply.ApplyForRole(_cut, _tl, role, fbx, Exists, Load);
        }

        FacialCorrectionTrack AutoTrack() { return _tl.GetOutputTracks().OfType<FacialCorrectionTrack>().SingleOrDefault(); }

        // ---------------------------------------------------------------- 「リスナーがあるか」の判断

        [Test]
        public void GateFollowsTheOverrideAndOtherwiseTheCompiledListenerType()
        {
            FctrackListenerGate.OverrideForTests = true;
            Assert.IsTrue(FctrackListenerGate.Available);
            FctrackListenerGate.OverrideForTests = false;
            Assert.IsFalse(FctrackListenerGate.Available);
            FctrackListenerGate.OverrideForTests = null;
            // 実際: FC のアセンブリ（D-Drive 1.4.0 以降）が入っているときだけ、型が見つかる
            bool compiled = System.Type.GetType(FctrackListenerGate.ListenerTypeName, false) != null;
            Assert.AreEqual(compiled, FctrackListenerGate.Available);
        }

        [Test]
        public void ListenerTypeNameMatchesTheTypeWhenTheFcAssemblyIsCompiled()
        {
            // 型を直接使うテストは FC のテストにある（FctrackImportListenerTests）。ここでは、名前の指す型が「ある」ときは必ず ICutsceneImportListener を実装していることだけ確かめる
            System.Type t = System.Type.GetType(FctrackListenerGate.ListenerTypeName, false);
            if (t == null) { Assert.Pass("この環境では FC のアセンブリがコンパイルされていない（D-Drive 1.4.0 未満）"); return; }
            Assert.IsNotNull(t.GetConstructor(System.Type.EmptyTypes), "引数なしの public コンストラクタ（D-Drive の発見の条件）");
            Assert.IsTrue(t.GetInterfaces().Any(i => i.Name == "ICutsceneImportListener"));
        }

        // ---------------------------------------------------------------- 後追い（.fctrack だけ後から置いた）の扱い

        static FctrackSyncResult NotReady(bool noData) { return new FctrackSyncResult { Status = FctrackSyncStatus.NotReady, NoCutsceneData = noData }; }

        [Test]
        public void WithoutTheListenerTheOldRetryStaysUntilTheLimit()
        {
            const int Max = 6;
            for (int attempts = 0; attempts < Max - 1; attempts++)
                Assert.AreEqual(FctrackRetryDecision.Retry, FctrackImportApply.DecideRetry(NotReady(true), attempts, Max, false), "attempts=" + attempts);
            Assert.AreEqual(FctrackRetryDecision.GiveUpWarn, FctrackImportApply.DecideRetry(NotReady(true), Max - 1, Max, false));
            Assert.AreEqual(FctrackRetryDecision.Retry, FctrackImportApply.DecideRetry(NotReady(false), 0, Max, false));
        }

        [Test]
        public void WithTheListenerThereIsNoRetry()
        {
            const int Max = 6;
            // CutsceneData がまだ無い = あとの FBX の取り込みでリスナーが反映する → 静かにやめる
            Assert.AreEqual(FctrackRetryDecision.GiveUpSilent, FctrackImportApply.DecideRetry(NotReady(true), 0, Max, true));
            // CutsceneData はあるのに反映できない（役のトラックが無いなど）→ 再試行せず警告
            Assert.AreEqual(FctrackRetryDecision.GiveUpWarn, FctrackImportApply.DecideRetry(NotReady(false), 0, Max, true));
            Assert.AreEqual(FctrackRetryDecision.GiveUpSilent, FctrackImportApply.DecideRetry(NotReady(true), 3, Max, true));
        }

        [Test]
        public void MissingCutsceneDataIsFlaggedSoTheHookCanTellItApart()
        {
            _cut.Timeline = _tl;
            AddRole("Shizuku");
            FctrackSyncResult none = FctrackCutsceneSyncApplyNull();
            Assert.AreEqual(FctrackSyncStatus.NotReady, none.Status);
            Assert.IsTrue(none.NoCutsceneData);
            FctrackSyncResult noRole = FctrackCutsceneSync.ApplyToCutscene(_cut, _asset, "Nobody");
            Assert.AreEqual(FctrackSyncStatus.NotReady, noRole.Status);
            Assert.IsFalse(noRole.NoCutsceneData, "役のトラックが無い = データはある");
        }

        FctrackSyncResult FctrackCutsceneSyncApplyNull() { return FctrackCutsceneSync.ApplyToCutscene(null, _asset, "Shizuku"); }

        AnimationTrack AddRole(string role) { return _tl.CreateTrack<AnimationTrack>(null, role); }

        // ---------------------------------------------------------------- 役 1 つ分の反映（リスナーの中身）

        [Test]
        public void FctrackPathIsTheFbxPathWithTheFctrackExtension()
        {
            Assert.AreEqual(Fct, FctrackImportApply.FctrackPathFor(Fbx));
            Assert.AreEqual(Fct, FctrackImportApply.FctrackPathFor(Fbx.Replace('/', '\\')));
            Assert.AreEqual("Assets/A.B/S__M_2.fctrack", FctrackImportApply.FctrackPathFor("Assets/A.B/S__M_2.fbx"), "'_2' も含む。フォルダ名の '.' に惑わされない");
            Assert.AreEqual("noext.fctrack", FctrackImportApply.FctrackPathFor("noext"));
            Assert.IsNull(FctrackImportApply.FctrackPathFor(""));
            Assert.IsNull(FctrackImportApply.FctrackPathFor(null));
        }

        [Test]
        public void E8_FbxImportWithAFctrackAppliesInTheSamePassWithoutAnyRetry()
        {
            // D-Drive が CutsceneData / Timeline / 役のトラックを作ったところ（FBX の取り込み）に、リスナーが呼ばれた状況の再現
            AddRole("Shizuku");
            FctrackSyncResult r = Apply();
            Assert.IsNotNull(r);
            Assert.AreEqual(FctrackSyncStatus.Done, r.Status, r.Message);
            Assert.IsTrue(r.Changed);
            Assert.IsNotNull(AutoTrack(), "同じ呼び出しの中で Timeline に入る（delayCall の再試行を待たない）");
            Assert.AreEqual("Shizuku_Facial(auto)", AutoTrack().name);
            Assert.AreSame(_asset, ((FacialCorrectionClip)AutoTrack().GetClips().Single().asset).track);
            if (BindingTestUtil.Supported)
            {
                Assert.AreEqual(1, _cut.Bindings.Length);
                Assert.IsTrue(BindingTestUtil.IsSame(_cut.Bindings[0]));
                Assert.AreEqual("Shizuku", BindingTestUtil.Source(_cut.Bindings[0]));
            }
            Assert.AreEqual(1, _loads);
        }

        [Test]
        public void AppliedTwiceItDoesNotDuplicateTheTrackClipOrBinding()
        {
            AddRole("Shizuku");
            Apply();
            int bindings = _cut.Bindings.Length;
            FctrackSyncResult again = Apply(); // 再取り込み（毎回呼ばれる）
            Assert.AreEqual(FctrackSyncStatus.Done, again.Status);
            Assert.IsFalse(again.Changed, again.Message);
            Assert.AreEqual(1, _tl.GetOutputTracks().OfType<FacialCorrectionTrack>().Count());
            Assert.AreEqual(1, AutoTrack().GetClips().Count());
            Assert.AreEqual(bindings, _cut.Bindings.Length);
        }

        [Test]
        public void ADesignerMovedClipIsNotMovedByAReimport()
        {
            AddRole("Shizuku");
            Apply();
            TimelineClip c = AutoTrack().GetClips().Single();
            c.start = 1.5; c.duration = 0.5; c.clipIn = 0.25;
            _asset.rangeEnd = 300f;
            Apply();
            Assert.AreEqual(1.5, c.start, 1e-6);
            Assert.AreEqual(0.5, c.duration, 1e-6);
            Assert.AreEqual(0.25, c.clipIn, 1e-6);
        }

        [Test]
        public void NoFctrackNextToTheFbxDoesNothing()
        {
            AddRole("Shizuku");
            Assert.IsNull(FctrackImportApply.ApplyForRole(_cut, _tl, "Shizuku", "Assets/SourceAssets/Cutscene/S020__Hero.fbx", Exists, Load));
            Assert.IsNull(FctrackImportApply.ApplyForRole(_cut, _tl, "Shizuku", "", Exists, Load));
            Assert.IsNull(AutoTrack());
            Assert.AreEqual(0, _loads, "無ければ読み込みもしない");
        }

        [Test]
        public void AFctrackThatCannotBeLoadedIsReportedAsFailedNotThrown()
        {
            AddRole("Shizuku");
            FctrackSyncResult r = FctrackImportApply.ApplyForRole(_cut, _tl, "Shizuku", Fbx, Exists, p => null);
            Assert.AreEqual(FctrackSyncStatus.Failed, r.Status);
            StringAssert.Contains(Fct, r.Message);
            Assert.IsNull(AutoTrack());
        }

        [Test]
        public void ApplyUsesTheGivenTimelineAndDoesNotSearchForTheCutscene()
        {
            // リスナーは result.Timeline を渡す（CutsceneData の検索はしない）。Data.Timeline が空でも、渡された Timeline に入る
            _cut.Timeline = null;
            AddRole("Shizuku");
            FctrackSyncResult r = FctrackCutsceneSync.Apply(_cut, _tl, _asset, "Shizuku");
            Assert.AreEqual(FctrackSyncStatus.Done, r.Status, r.Message);
            Assert.IsNotNull(AutoTrack());
        }

        [Test]
        public void ApplyDoesNotRecordUndoOrSaveAnything()
        {
            AddRole("Shizuku");
            UnityEditor.Undo.IncrementCurrentGroup();
            string before = UnityEditor.Undo.GetCurrentGroupName();
            Apply();
            Assert.AreEqual(before, UnityEditor.Undo.GetCurrentGroupName(), "Undo に積まない");
            // メモリ上のアセット（保存先なし）なので、保存が呼ばれてもディスクには出ない。保存の呼び出しが無いことはコードで確かめる（ApplyForRole / Apply に SaveAssets 系が無い）
            string src = System.IO.File.ReadAllText(PackageFile("Bridges/DDrive/Editor/FctrackImportApply.cs")) + System.IO.File.ReadAllText(PackageFile("Bridges/DDrive/FC/Editor/FctrackImportListener.cs"));
            StringAssert.DoesNotContain("SaveAssets(", Strip(src));
            StringAssert.DoesNotContain("SaveAssetIfDirty(", Strip(src));
        }

        // コメントを除いたコードだけを見る
        static string Strip(string src)
        {
            var sb = new System.Text.StringBuilder();
            foreach (string line in src.Split('\n'))
            {
                int c = line.IndexOf("//", System.StringComparison.Ordinal);
                sb.Append(c >= 0 ? line.Substring(0, c) : line).Append('\n');
            }
            return sb.ToString();
        }

        static string PackageFile(string relative)
        {
            var info = UnityEditor.PackageManager.PackageInfo.FindForAssembly(typeof(FctrackImportApply).Assembly);
            if (info == null) Assert.Inconclusive("パッケージの場所が分からない");
            return System.IO.Path.Combine(info.resolvedPath, relative);
        }
    }
}
