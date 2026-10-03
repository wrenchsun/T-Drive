// Timeline のトラック。バインド先はキャラクターの Animator（その子から Runner を探す）。
// バインドが無いときは FacialTimelineBinding.FallbackResolver（D-Drive ブリッジ）が同じ役名の AnimationTrack から引く。
using System.Collections.Generic;
using System.ComponentModel;
using TDrive.Facial.Core;
using UnityEngine;
using UnityEngine.Playables;
using UnityEngine.Timeline;

namespace TDrive.Facial.Timeline
{
    [TrackColor(0.95f, 0.62f, 0.76f)]
    [TrackClipType(typeof(FacialCorrectionClip))]
    [TrackBindingType(typeof(Animator))]
    [DisplayName("Facial Correction")]
    public sealed class FacialCorrectionTrack : TrackAsset
    {
        static readonly List<SkinnedMeshRenderer> TmpRenderers = new List<SkinnedMeshRenderer>(4);
        static readonly List<int> TmpIndices = new List<int>(16);
        static readonly List<Transform> TmpBones = new List<Transform>(16);

        protected override void OnCreateClip(TimelineClip clip)
        {
            clip.displayName = "補正";
            base.OnCreateClip(clip);
        }

        public override Playable CreateTrackMixer(PlayableGraph graph, GameObject go, int inputCount)
        {
            ScriptPlayable<FacialCorrectionMixerBehaviour> playable = ScriptPlayable<FacialCorrectionMixerBehaviour>.Create(graph, inputCount);
            FacialCorrectionMixerBehaviour m = playable.GetBehaviour();
            m.director = go != null ? go.GetComponent<PlayableDirector>() : null;
            m.track = this;
            return playable;
        }

        /// <summary>
        /// Timeline ウィンドウのプレビュー用。Runner が書くブレンドシェイプ（FC_*、カット補正のシェイプ）とカット補正のボーンを登録し、
        /// プレビューを抜けたら Timeline が元の値へ戻せるようにする。
        /// </summary>
        public override void GatherProperties(PlayableDirector director, IPropertyCollector driver)
        {
            base.GatherProperties(director, driver);
            if (director == null) return;
            FacialCorrectionRunner runner = FacialTimelineBinding.Resolve(director, this, director.GetGenericBinding(this));
            if (runner == null || runner.data == null) return;

            // FC_* のシェイプ
            IReadOnlyList<SkinnedMeshRenderer> targets = runner.ResolvedTargets;
            for (int t = 0; t < targets.Count; t++)
            {
                SkinnedMeshRenderer r = targets[t];
                if (r == null || r.sharedMesh == null) continue;
                Mesh m = r.sharedMesh;
                for (int k = 0; k < m.blendShapeCount; k++)
                {
                    string n = m.GetBlendShapeName(k);
                    if (FacialNaming.HasFcPrefix(n, FacialNaming.FcPrefix)) driver.AddFromName<SkinnedMeshRenderer>(r.gameObject, "blendShape." + n);
                }
            }

            // クリップのカット補正（ポーズ）が触るシェイプとボーン
            foreach (TimelineClip clip in GetClips())
            {
                var asset = clip.asset as FacialCorrectionClip;
                if (asset == null || asset.template == null || asset.template.pose == null) continue;
                FacialPoseAsset pose = asset.template.pose;
                runner.GetPoseShapeTargets(pose, TmpRenderers, TmpIndices);
                for (int i = 0; i < TmpRenderers.Count; i++)
                {
                    SkinnedMeshRenderer r = TmpRenderers[i];
                    if (r == null || r.sharedMesh == null) continue;
                    driver.AddFromName<SkinnedMeshRenderer>(r.gameObject, "blendShape." + r.sharedMesh.GetBlendShapeName(TmpIndices[i]));
                }
                runner.GetPoseBoneTransforms(pose, TmpBones);
                for (int i = 0; i < TmpBones.Count; i++) driver.AddFromComponent(TmpBones[i].gameObject, TmpBones[i]);
            }
            TmpRenderers.Clear();
            TmpIndices.Clear();
            TmpBones.Clear();
        }
    }
}
