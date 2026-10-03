// D-Drive ブリッジ: カットシーン 1 つ分の検査（純粋な関数。D-Drive のレジストリ・アセットに触れない）。
// IValidator（Editor 側の FacialCutsceneValidator）がこれを呼ぶ。モデルの解決はデリゲートで外から渡す。
//   TD-FACIAL-001 バインドが解決できない / 002 クリップの感情レイヤー名がデータに無い / 004 モデルに Runner が無い / 005 Runner にデータが無い
//   （003 = ポーズ参照の欠損は Editor 側。SerializedObject が要るため）
using System;
using System.Collections.Generic;
using DDrive.Runtime.Cutscene;
using TDrive.Facial.Timeline;
using UnityEngine.Timeline;

namespace TDrive.Facial.DDrive
{
    public enum FacialCutsceneSeverity { Info, Warning, Error }

    public struct FacialCutsceneIssue
    {
        public FacialCutsceneSeverity Severity;
        public string Code;
        public string Message;
        public string TrackName;
        public override string ToString() { return "[" + Severity + "] " + Code + " " + Message; }
    }

    /// <summary>静的にモデルが分かったか（Resolvable）と、その Prefab の Runner（無ければ null）。</summary>
    public struct FacialModelLookup
    {
        public bool Resolvable;
        public FacialCorrectionRunner Runner;
    }

    public static class FacialCutsceneChecks
    {
        public const string CodeUnbound = "TD-FACIAL-001";
        public const string CodeUnknownLayer = "TD-FACIAL-002";
        public const string CodeMissingPose = "TD-FACIAL-003";
        public const string CodeNoRunner = "TD-FACIAL-004";
        public const string CodeNoData = "TD-FACIAL-005";

        const string Prefix = "[T-Drive Facial] ";

        /// <summary>
        /// timeline の FacialCorrectionTrack を検査する。bindings は CutsceneData.Bindings。
        /// resolveModel は「そのバインドが指すモデルの Runner」を静的に引く関数（引けなければ Resolvable = false → レイヤー名の検査は飛ばす）。
        /// </summary>
        public static List<FacialCutsceneIssue> Check(TimelineAsset timeline, IReadOnlyList<CutsceneBinding> bindings, Func<CutsceneBinding, FacialModelLookup> resolveModel)
        {
            var issues = new List<FacialCutsceneIssue>();
            if (timeline == null) return issues;
            foreach (TrackAsset t in timeline.GetOutputTracks())
            {
                var track = t as FacialCorrectionTrack;
                if (track == null) continue;
                CheckTrack(issues, timeline, track, bindings, resolveModel);
            }
            return issues;
        }

        static void CheckTrack(List<FacialCutsceneIssue> issues, TimelineAsset timeline, FacialCorrectionTrack track,
            IReadOnlyList<CutsceneBinding> bindings, Func<CutsceneBinding, FacialModelLookup> resolveModel)
        {
            string role;
            bool hasRole = FacialDDriveBinding.TryGetRole(track.name, out role);
            bool hasOwn, hasRoleBinding = false;
            CutsceneBinding own = Find(bindings, track.name, out hasOwn);
            CutsceneBinding roleBinding = default(CutsceneBinding);
            AnimationTrack roleTrack = hasRole ? FacialDDriveBinding.FindRoleAnimationTrack(timeline, role) : null;
            if (hasRole) roleBinding = Find(bindings, role, out hasRoleBinding);

            // --- バインドが解決できるか ---
            if (!hasOwn && roleTrack == null)
            {
                Add(issues, FacialCutsceneSeverity.Warning, CodeUnbound, track.name,
                    "トラック '" + track.name + "' のバインドを解決できません。Bindings にこのトラック名を足すか、トラック名を '<役名>_Facial' にして同じ役名のアニメーショントラックを置いてください（このトラックは何もしません）");
            }
            else if (!hasOwn && !hasRoleBinding)
            {
                Add(issues, FacialCutsceneSeverity.Warning, CodeUnbound, track.name,
                    "トラック '" + track.name + "' は同じ役名のアニメーショントラック '" + role + "' のバインド先を使いますが、Bindings に '" + role + "' がありません（このトラックは何もしません）");
            }

            // --- モデルが静的に分かるとき: Runner とレイヤー名 ---
            if (resolveModel == null || (!hasOwn && !hasRoleBinding)) return;
            FacialModelLookup lookup = resolveModel(hasOwn ? own : roleBinding);
            if (!lookup.Resolvable) return;
            if (lookup.Runner == null)
            {
                Add(issues, FacialCutsceneSeverity.Warning, CodeNoRunner, track.name,
                    "トラック '" + track.name + "' のバインド先のモデル Prefab に FacialCorrectionRunner がありません（補正は掛かりません）");
                return;
            }
            FacialCorrectionData data = lookup.Runner.data;
            if (data == null || data.layers == null)
            {
                Add(issues, FacialCutsceneSeverity.Warning, CodeNoData, track.name,
                    "トラック '" + track.name + "' のバインド先のモデルの Runner に FacialCorrectionData が設定されていません（補正は掛かりません）");
                return;
            }
            foreach (TimelineClip clip in track.GetClips())
            {
                var asset = clip.asset as FacialCorrectionClip;
                if (asset == null || asset.template == null || asset.template.emotions == null) continue;
                FacialEmotionEntry[] emo = asset.template.emotions;
                for (int i = 0; i < emo.Length; i++)
                {
                    string layer = emo[i].layer;
                    if (string.IsNullOrEmpty(layer) || HasLayer(data, layer)) continue;
                    Add(issues, FacialCutsceneSeverity.Warning, CodeUnknownLayer, track.name,
                        "トラック '" + track.name + "' のクリップ '" + clip.displayName + "' の感情レイヤー '" + layer + "' が、モデルの補正データ（" + data.name + "）にありません（その重みは無視されます）");
                }
            }
        }

        static CutsceneBinding Find(IReadOnlyList<CutsceneBinding> bindings, string trackName, out bool found)
        {
            found = false;
            if (bindings == null || string.IsNullOrEmpty(trackName)) return default(CutsceneBinding);
            for (int i = 0; i < bindings.Count; i++)
                if (string.Equals(bindings[i].TrackName, trackName, StringComparison.Ordinal)) { found = true; return bindings[i]; }
            return default(CutsceneBinding);
        }

        static bool HasLayer(FacialCorrectionData data, string layer)
        {
            for (int i = 0; i < data.layers.Length; i++)
                if (string.Equals(data.layers[i].name, layer, StringComparison.Ordinal)) return true;
            return false;
        }

        static void Add(List<FacialCutsceneIssue> list, FacialCutsceneSeverity sev, string code, string track, string message)
        {
            list.Add(new FacialCutsceneIssue { Severity = sev, Code = code, TrackName = track, Message = Prefix + message });
        }
    }
}
