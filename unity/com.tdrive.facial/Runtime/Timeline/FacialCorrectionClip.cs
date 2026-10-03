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

        // ブレンドのみ。範囲の外へは延ばさない（クリップが終わったら補正は通常に戻る）
        public ClipCaps clipCaps { get { return ClipCaps.Blending; } }

        public override Playable CreatePlayable(PlayableGraph graph, GameObject owner)
        {
            ScriptPlayable<FacialCorrectionBehaviour> playable = ScriptPlayable<FacialCorrectionBehaviour>.Create(graph, template);
            FacialCorrectionBehaviour b = playable.GetBehaviour();
            b.resolvedViewer = template.viewer.Resolve(graph.GetResolver()); // 視点（シーン参照）
            return playable;
        }
    }
}
