// D-Drive の CutsceneBinding の「FC-1 で増えた部分」（Target = SameAsTrack・SourceTrackName）を、型の名前を直接書かずに読み書きする入口。
// 理由: リリース済みの D-Drive（v1.3.1 まで）には CutsceneBindTarget.SameAsTrack も CutsceneBinding.SourceTrackName も無い。
//       直接書くとブリッジ全体がコンパイルできないので、名前（文字列）で引く。無い版では「SameAsTrack は使われていない」として動く（docs/19 E-1）。
//       FC-1 のある D-Drive（1.4.0 以降）でも同じコードで動く。
using System;
using System.Reflection;
using DDrive.Runtime.Cutscene;

namespace TDrive.Facial.DDrive
{
    public static class CutsceneBindingAccess
    {
        public const string SameAsTrackName = "SameAsTrack";
        public const string SourceTrackFieldName = "SourceTrackName";

        static readonly FieldInfo SourceField = typeof(CutsceneBinding).GetField(SourceTrackFieldName, BindingFlags.Public | BindingFlags.Instance);

        /// <summary>この D-Drive に SameAsTrack（と SourceTrackName）があるか。</summary>
        public static bool SameAsTrackSupported
        {
            get { return SourceField != null && Enum.IsDefined(typeof(CutsceneBindTarget), SameAsTrackName); }
        }

        /// <summary>SameAsTrack の列挙値（int）。無ければ false。</summary>
        public static bool TryGetSameAsTrackValue(out int value)
        {
            value = 0;
            if (!SameAsTrackSupported) return false;
            value = (int)Enum.Parse(typeof(CutsceneBindTarget), SameAsTrackName);
            return true;
        }

        public static bool IsSameAsTrack(CutsceneBinding b)
        {
            return string.Equals(b.Target.ToString(), SameAsTrackName, StringComparison.Ordinal);
        }

        /// <summary>SourceTrackName（無い版・空は null）。</summary>
        public static string GetSourceTrackName(CutsceneBinding b)
        {
            if (SourceField == null) return null;
            object boxed = b; // 構造体をボックス化してから読む
            return SourceField.GetValue(boxed) as string;
        }

        /// <summary>{ TrackName, Target = SameAsTrack, SourceTrackName } の Binding を作る。この D-Drive に SameAsTrack が無ければ false。</summary>
        public static bool TryMakeSameAsTrack(string trackName, string sourceTrackName, out CutsceneBinding binding)
        {
            binding = default(CutsceneBinding);
            int v;
            if (!TryGetSameAsTrackValue(out v)) return false;
            object boxed = new CutsceneBinding { TrackName = trackName, Target = (CutsceneBindTarget)v };
            SourceField.SetValue(boxed, sourceTrackName);
            binding = (CutsceneBinding)boxed;
            return true;
        }
    }
}
