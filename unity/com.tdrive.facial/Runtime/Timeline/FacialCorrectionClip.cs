// Timeline のクリップ。値はテンプレート（FacialCorrectionBehaviour）に持つ。クリップ同士の重なり = ブレンド。
using System;
using UnityEngine;
using UnityEngine.Playables;
using UnityEngine.Timeline;

namespace TDrive.Facial.Timeline
{
    [Serializable]
    public sealed class FacialCorrectionClip : PlayableAsset, ITimelineClipAsset
    {
        public FacialCorrectionBehaviour template = new FacialCorrectionBehaviour();

        [Tooltip("Maya から持ち出した演出カーブ（.fctrack を取り込んだもの）。入れると、クリップの時間に合わせて強さ・感情の重み・角度の固定が動く。手で入れた値は曲線より優先。空なら使わない")]
        public FacialTrackAsset track;

        // ブレンドと「開始位置」（clipIn。.fctrack の曲線の途中から使う）。範囲の外へは延ばさない（クリップが終わったら補正は通常に戻る）
        public ClipCaps clipCaps { get { return ClipCaps.Blending | ClipCaps.ClipIn; } }

        public override Playable CreatePlayable(PlayableGraph graph, GameObject owner)
        {
            ScriptPlayable<FacialCorrectionBehaviour> playable = ScriptPlayable<FacialCorrectionBehaviour>.Create(graph, template);
            FacialCorrectionBehaviour b = playable.GetBehaviour();
            b.resolvedViewer = template.viewer.Resolve(graph.GetResolver()); // 視点（シーン参照）
            b.track = track; // 曲線（.fctrack）。空なら定数のみ
            return playable;
        }
    }
}
