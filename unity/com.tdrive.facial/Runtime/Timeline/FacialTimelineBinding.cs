// Timeline トラックのバインド先 → FacialCorrectionRunner の解決。
// バインドは Animator が基本（D-Drive の CutsceneBinding は Animator か Transform を渡す）。防御的に Transform / GameObject / Runner も受ける。
// バインドが無いとき用の口（FallbackResolver）だけを持つ。D-Drive は Bridges/DDrive 側がここへ登録する（この assembly は D-Drive を参照しない）。
using System;
using UnityEngine;
using UnityEngine.Playables;
using UnityEngine.Timeline;

namespace TDrive.Facial.Timeline
{
    public static class FacialTimelineBinding
    {
        /// <summary>
        /// トラックにバインドが無い（または null）とき、バインド先の代わりを返す口。ブリッジが設定する（例: 同じ役名の AnimationTrack のバインド先）。
        /// 戻り値は Animator / Transform / GameObject / FacialCorrectionRunner のいずれか。null = 見つからない。
        /// </summary>
        public static Func<PlayableDirector, TrackAsset, UnityEngine.Object> FallbackResolver;

        /// <summary>バインド先（またはフォールバック）を返す。バインドが無ければ FallbackResolver を呼ぶ。</summary>
        public static UnityEngine.Object ResolveBindingObject(PlayableDirector director, TrackAsset track, UnityEngine.Object bound)
        {
            if (bound != null) return bound;
            Func<PlayableDirector, TrackAsset, UnityEngine.Object> f = FallbackResolver;
            if (f == null || director == null || track == null) return null;
            try { return f(director, track); }
            catch (Exception e) { Debug.LogException(e); return null; } // フェイルソフト
        }

        /// <summary>オブジェクト（Animator / Transform / GameObject / Runner）から Runner を探す。子 → 親の順。無ければ null。</summary>
        public static FacialCorrectionRunner RunnerFrom(UnityEngine.Object obj)
        {
            if (obj == null) return null;
            var runner = obj as FacialCorrectionRunner;
            if (runner != null) return runner;
            GameObject go = obj as GameObject;
            if (go == null)
            {
                var c = obj as Component;
                if (c != null) go = c.gameObject;
            }
            if (go == null) return null;
            runner = go.GetComponentInChildren<FacialCorrectionRunner>(true);
            if (runner == null) runner = go.GetComponentInParent<FacialCorrectionRunner>(true);
            return runner;
        }

        /// <summary>トラックのバインド（無ければフォールバック）から Runner を求める。見つからなければ null（何もしない）。</summary>
        public static FacialCorrectionRunner Resolve(PlayableDirector director, TrackAsset track, UnityEngine.Object bound)
        {
            return RunnerFrom(ResolveBindingObject(director, track, bound));
        }
    }
}
