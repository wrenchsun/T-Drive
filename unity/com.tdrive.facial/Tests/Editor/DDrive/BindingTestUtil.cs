// D-Drive の SameAsTrack（FC-1。1.4.0 以降）を、型の名前を書かずに扱うテスト用の道具。
// ブリッジの基本のアセンブリは D-Drive 1.3.1 でもコンパイルできる形（CutsceneBindingAccess）なので、そのテストも同じ制約で書く。
using DDrive.Runtime.Cutscene;
using NUnit.Framework;
using TDrive.Facial.DDrive;

namespace TDrive.Facial.Tests.DDrive
{
    public static class BindingTestUtil
    {
        public static bool Supported { get { return CutsceneBindingAccess.SameAsTrackSupported; } }

        /// <summary>この D-Drive に SameAsTrack が無ければ、そのテストは Inconclusive（SameAsTrack 専用の振る舞いを検査できない）。</summary>
        public static void RequireSameAsTrack()
        {
            Assume.That(Supported, "この D-Drive には SameAsTrack が無い（1.4.0 より前）");
        }

        public static CutsceneBinding Same(string trackName, string source)
        {
            CutsceneBinding b;
            Assert.IsTrue(CutsceneBindingAccess.TryMakeSameAsTrack(trackName, source, out b), "SameAsTrack を作れない");
            return b;
        }

        public static bool IsSame(CutsceneBinding b) { return CutsceneBindingAccess.IsSameAsTrack(b); }

        public static string Source(CutsceneBinding b) { return CutsceneBindingAccess.GetSourceTrackName(b); }
    }
}
