// D-Drive ブリッジ: バインドの補助。FacialCorrectionTrack にバインドが無いとき、同じ役名の AnimationTrack のバインド先を使う。
//   名前の規則: Facial のトラック "<役名>_Facial"（または "<役名>_Facial(auto)"）⇔ アニメーショントラック "<役名>"。
// 理由: D-Drive の SpawnModel のバインドを 2 つ書くとモデルが 2 体出るので、Facial 用には同じ相手を引き直す（docs/16 C-1 の回避策）。
// D-Drive の型は使わない（TimelineAsset と PlayableDirector だけ）。Timeline 側の口（FacialTimelineBinding.FallbackResolver）へ登録する。
using System.Runtime.CompilerServices;
using TDrive.Facial.Timeline;
using UnityEngine;
using UnityEngine.Playables;
using UnityEngine.Timeline;

namespace TDrive.Facial.DDrive
{
    public static class FacialDDriveBinding
    {
        public const string FacialSuffix = "_Facial";
        public const string AutoSuffix = "_Facial(auto)";

        // トラック → 同じ役名の AnimationTrack（見つかったものだけ覚える。トラックが消えれば一緒に消える）
        static readonly ConditionalWeakTable<TrackAsset, TrackAsset> RoleCache = new ConditionalWeakTable<TrackAsset, TrackAsset>();

        /// <summary>Facial のトラック名から役名を取り出す。規則に合わなければ false。</summary>
        public static bool TryGetRole(string facialTrackName, out string role)
        {
            role = null;
            if (string.IsNullOrEmpty(facialTrackName)) return false;
            string n = facialTrackName;
            if (n.EndsWith(AutoSuffix, System.StringComparison.Ordinal)) n = n.Substring(0, n.Length - AutoSuffix.Length);
            else if (n.EndsWith(FacialSuffix, System.StringComparison.Ordinal)) n = n.Substring(0, n.Length - FacialSuffix.Length);
            else return false;
            if (n.Length == 0) return false;
            role = n;
            return true;
        }

        /// <summary>同じ役名の AnimationTrack（名前の完全一致）を探す。無ければ null。</summary>
        public static AnimationTrack FindRoleAnimationTrack(TimelineAsset timeline, string role)
        {
            if (timeline == null || string.IsNullOrEmpty(role)) return null;
            foreach (TrackAsset t in timeline.GetOutputTracks())
            {
                var a = t as AnimationTrack;
                if (a != null && string.Equals(a.name, role, System.StringComparison.Ordinal)) return a;
            }
            return null;
        }

        /// <summary>Facial のトラックに対応する AnimationTrack（役名の規則）。無ければ null。</summary>
        public static AnimationTrack FindRoleAnimationTrack(TrackAsset facialTrack)
        {
            if (facialTrack == null) return null;
            string role;
            if (!TryGetRole(facialTrack.name, out role)) return null;
            TrackAsset cached;
            // 名前を変えたあとは古い結果を使わない
            if (RoleCache.TryGetValue(facialTrack, out cached) && cached != null && string.Equals(cached.name, role, System.StringComparison.Ordinal))
                return cached as AnimationTrack;
            AnimationTrack found = FindRoleAnimationTrack(facialTrack.timelineAsset, role);
            if (found != null)
            {
                RoleCache.Remove(facialTrack);
                RoleCache.Add(facialTrack, found);
            }
            return found;
        }

        /// <summary>
        /// バインドの代わり: 役名が同じ AnimationTrack のバインド先（Animator か、D-Drive が Animator 無しのとき渡す Transform）。
        /// 見つからなければ null。Timeline 側の FallbackResolver に登録される。
        /// </summary>
        public static UnityEngine.Object ResolveFallback(PlayableDirector director, TrackAsset facialTrack)
        {
            if (director == null) return null;
            AnimationTrack anim = FindRoleAnimationTrack(facialTrack);
            return anim != null ? director.GetGenericBinding(anim) : null;
        }

        /// <summary>Timeline 側の口へ登録する（既に別のものが登録されていたら上書きしない）。</summary>
        public static void Register()
        {
            if (FacialTimelineBinding.FallbackResolver == null) FacialTimelineBinding.FallbackResolver = ResolveFallback;
        }

        [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.SubsystemRegistration)]
        static void RegisterAtRuntime()
        {
            Register();
        }

#if UNITY_EDITOR
        // エディタの起動・再コンパイル後（Timeline ウィンドウでのプレビュー、Edit モードのテスト）
        [UnityEditor.InitializeOnLoadMethod]
        static void RegisterInEditor()
        {
            Register();
        }
#endif
    }
}
