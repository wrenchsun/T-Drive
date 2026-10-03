// D-Drive の検証（CI の Run All）に載る IValidator。D-Drive は全アセンブリから IValidator を自動で見つける（引数なしのコンストラクタ）。
// カットシーンの TimelineAsset にある FacialCorrectionTrack について、バインド・感情レイヤー名・ポーズ参照を確認する。何も書き換えない。
using System.Collections.Generic;
using DDrive.Foundation.Data;
using DDrive.Foundation.Identity;
using DDrive.Foundation.Validation;
using DDrive.Runtime.Cutscene;
using DDrive.Runtime.Model;
using TDrive.Facial.Timeline;
using UnityEditor;
using UnityEngine;
using UnityEngine.Timeline;

namespace TDrive.Facial.DDrive.Editor
{
    public sealed class FacialCutsceneValidator : IValidator
    {
        public AssetType Target { get { return AssetType.Cutscene; } }

        public IEnumerable<ValidationResult> Validate(AssetDataBase data, ValidationContext ctx)
        {
            var cutscene = data as CutsceneData;
            if (cutscene == null || cutscene.Timeline == null) yield break;

            List<FacialCutsceneIssue> issues = FacialCutsceneChecks.Check(cutscene.Timeline, cutscene.Bindings, b => LookupModel(b, ctx));
            for (int i = 0; i < issues.Count; i++)
                yield return ToResult(issues[i]);

            foreach (string text in FindMissingReferences(cutscene.Timeline))
                yield return ValidationResult.Error(text, null, FacialCutsceneChecks.CodeMissingPose);
        }

        static ValidationResult ToResult(FacialCutsceneIssue i)
        {
            switch (i.Severity)
            {
                case FacialCutsceneSeverity.Error: return ValidationResult.Error(i.Message, null, i.Code);
                case FacialCutsceneSeverity.Info: return ValidationResult.Info(i.Message, null, i.Code);
                default: return ValidationResult.Warning(i.Message, null, i.Code);
            }
        }

        /// <summary>バインドが SpawnModel で、ModelData が見つかり、その Prefab があるときだけ静的に分かる（それ以外は検査を飛ばす）。</summary>
        static FacialModelLookup LookupModel(CutsceneBinding binding, ValidationContext ctx)
        {
            var result = new FacialModelLookup();
            if (binding.Target != CutsceneBindTarget.SpawnModel || !binding.Model.IsValid || ctx == null || ctx.AllAssets == null) return result;
            for (int i = 0; i < ctx.AllAssets.Count; i++)
            {
                var model = ctx.AllAssets[i] as ModelData;
                if (model == null || model.Id != binding.Model.Value) continue;
                if (model.Prefab == null) return result; // Prefab の欠損は D-Drive の ModelDataValidator が報告する
                result.Resolvable = true;
                result.Runner = model.Prefab.GetComponentInChildren<FacialCorrectionRunner>(true);
                return result;
            }
            return result;
        }

        /// <summary>ポーズの参照が切れているクリップの説明を返す（互換のための名前。FindMissingReferences の一部）。</summary>
        public static List<string> FindMissingPoseReferences(TimelineAsset timeline)
        {
            return Find(timeline, true, false);
        }

        /// <summary>ポーズ（FacialPoseAsset）・演出カーブ（.fctrack = FacialTrackAsset）の参照が切れている（アセットを消した）クリップの説明を返す（003）。</summary>
        public static List<string> FindMissingReferences(TimelineAsset timeline)
        {
            return Find(timeline, true, true);
        }

        static List<string> Find(TimelineAsset timeline, bool pose, bool fctrack)
        {
            var list = new List<string>();
            if (timeline == null) return list;
            foreach (TrackAsset t in timeline.GetOutputTracks())
            {
                var track = t as FacialCorrectionTrack;
                if (track == null) continue;
                foreach (TimelineClip clip in track.GetClips())
                {
                    var asset = clip.asset as FacialCorrectionClip;
                    if (asset == null) continue;
                    var so = new SerializedObject(asset);
                    SerializedProperty p = so.FindProperty("template.pose");
                    if (pose && p != null && p.objectReferenceValue == null && p.objectReferenceInstanceIDValue != 0)
                        list.Add("[T-Drive Facial] トラック '" + track.name + "' のクリップ '" + clip.displayName + "' のカット補正のポーズ（FacialPoseAsset）の参照が切れています");
                    SerializedProperty trackProp = so.FindProperty("track");
                    if (fctrack && trackProp != null && trackProp.objectReferenceValue == null && trackProp.objectReferenceInstanceIDValue != 0)
                        list.Add("[T-Drive Facial] トラック '" + track.name + "' のクリップ '" + clip.displayName + "' の演出カーブ（.fctrack）の参照が切れています。.fctrack を取り込み直すか、クリップの「Track」を入れ直してください");
                }
            }
            return list;
        }
    }
}
