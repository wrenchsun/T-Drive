// D-Drive ブリッジ: カットシーン 1 つ分の検査（純粋な関数。D-Drive のレジストリ・アセットに触れない）。
// IValidator（Editor 側の FacialCutsceneValidator）がこれを呼ぶ。モデルの解決はデリゲートで外から渡す。
//   D-Drive 1.3.1 でもコンパイルできるよう、SameAsTrack / SourceTrackName は CutsceneBindingAccess（名前で引く）経由で読む（docs/19 E-1）
//   バインドの解決（001）は 3 通り: ① Bindings にこのトラック名があり Target=SameAsTrack（推奨。参照先をたどる）
//   ② Bindings にこのトラック名がある（その Target をそのまま使う）③ Bindings に無く、トラック名が '<役名>_Facial' の規則に合う（同じ役名のアニメーショントラックのバインドを使う）
//   TD-FACIAL-001 バインドが解決できない / 002 クリップの感情レイヤー名がデータに無い / 004 モデルに Runner が無い / 005 Runner にデータが無い
//   006 同じ役に Facial のトラックが 2 本以上ある（合成される）/ 007 Facial のトラックに SpawnModel・MainCamera・AnchorPoint を直接結んでいる
//   （003 = ポーズ・.fctrack 参照の欠損は Editor 側。SerializedObject が要るため）
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
        public const string CodeSameRole = "TD-FACIAL-006";
        public const string CodeOwnBinding = "TD-FACIAL-007";

        const string Prefix = "[T-Drive Facial] ";

        /// <summary>
        /// timeline の FacialCorrectionTrack を検査する。bindings は CutsceneData.Bindings。
        /// resolveModel は「そのバインドが指すモデルの Runner」を静的に引く関数（引けなければ Resolvable = false → レイヤー名の検査は飛ばす）。
        /// </summary>
        public static List<FacialCutsceneIssue> Check(TimelineAsset timeline, IReadOnlyList<CutsceneBinding> bindings, Func<CutsceneBinding, FacialModelLookup> resolveModel)
        {
            var issues = new List<FacialCutsceneIssue>();
            if (timeline == null) return issues;
            var byRole = new Dictionary<string, List<string>>(StringComparer.Ordinal); // 役（解決した Binding の名前）→ Facial のトラック名
            foreach (TrackAsset t in timeline.GetOutputTracks())
            {
                var track = t as FacialCorrectionTrack;
                if (track == null) continue;
                string role = CheckTrack(issues, timeline, track, bindings, resolveModel);
                if (role == null) continue;
                List<string> names;
                if (!byRole.TryGetValue(role, out names)) { names = new List<string>(); byRole[role] = names; }
                names.Add(track.name);
            }
            foreach (KeyValuePair<string, List<string>> kv in byRole)
            {
                if (kv.Value.Count < 2) continue;
                Add(issues, FacialCutsceneSeverity.Info, CodeSameRole, kv.Value[0],
                    "同じ役 '" + kv.Key + "' に Facial のトラックが " + kv.Value.Count + " 本あります（" + string.Join(", ", kv.Value)
                    + "）。補正は合成されます（強さは掛け算・感情はレイヤーごとに後のトラック・角度の固定は割合の大きいほう。\"(auto)\" のトラックは手で作ったトラックより弱い）。意図したものか確認してください");
            }
            return issues;
        }

        // 戻り値: このトラックが解決した役の名前（解決できなければ null）
        static string CheckTrack(List<FacialCutsceneIssue> issues, TimelineAsset timeline, FacialCorrectionTrack track,
            IReadOnlyList<CutsceneBinding> bindings, Func<CutsceneBinding, FacialModelLookup> resolveModel)
        {
            // --- バインドが解決できるか ---
            // ① 自分の名前の Bindings がある（SameAsTrack なら参照先まで）② 無ければ役名の規則（'<役名>_Facial' → 同じ役名のアニメーショントラックの Bindings）
            bool hasOwn;
            CutsceneBinding own = Find(bindings, track.name, out hasOwn);
            CutsceneBinding effective = own;
            bool haveEffective = false;

            if (hasOwn)
            {
                // 自分の名前の Binding に SpawnModel などを直接結ぶと、Facial のトラックのためにモデルがもう 1 体出る（docs/16 C-1）
                if (own.Target == CutsceneBindTarget.SpawnModel || own.Target == CutsceneBindTarget.MainCamera || own.Target == CutsceneBindTarget.AnchorPoint)
                {
                    Add(issues, FacialCutsceneSeverity.Warning, CodeOwnBinding, track.name,
                        "トラック '" + track.name + "' の Binding が " + own.Target + " です。" + (own.Target == CutsceneBindTarget.SpawnModel ? "モデルがもう 1 体出てしまいます。" : "モデル（Runner）に結ばれません。")
                        + "Target を SameAsTrack・SourceTrackName を同じキャラクターのアニメーショントラック名にしてください");
                }
                if (CutsceneBindingAccess.IsSameAsTrack(own))
                {
                    string problem = FollowSameAsTrack(timeline, bindings, own, out effective);
                    if (problem != null)
                    {
                        Add(issues, FacialCutsceneSeverity.Warning, CodeUnbound, track.name,
                            "トラック '" + track.name + "' は Target=SameAsTrack ですが" + problem + "（このトラックは何もしません）");
                        return null;
                    }
                }
                haveEffective = true;
            }
            else
            {
                string role;
                bool hasRole = FacialDDriveBinding.TryGetRole(track.name, out role);
                AnimationTrack roleTrack = hasRole ? FacialDDriveBinding.FindRoleAnimationTrack(timeline, role) : null;
                if (roleTrack == null)
                {
                    Add(issues, FacialCutsceneSeverity.Warning, CodeUnbound, track.name,
                        "トラック '" + track.name + "' のバインドを解決できません。Bindings にこのトラック名を足して Target を SameAsTrack・SourceTrackName を同じキャラクターのアニメーショントラック名にするのがおすすめです（または、トラック名を '<役名>_Facial' にして同じ役名のアニメーショントラックを置いてください）。このトラックは何もしません");
                }
                else
                {
                    bool hasRoleBinding;
                    CutsceneBinding roleBinding = Find(bindings, role, out hasRoleBinding);
                    if (!hasRoleBinding)
                    {
                        Add(issues, FacialCutsceneSeverity.Warning, CodeUnbound, track.name,
                            "トラック '" + track.name + "' は同じ役名のアニメーショントラック '" + role + "' のバインド先を使いますが、Bindings に '" + role + "' がありません（このトラックは何もしません）");
                    }
                    else
                    {
                        effective = roleBinding;
                        haveEffective = true;
                    }
                }
            }

            // --- モデルが静的に分かるとき: Runner とレイヤー名 ---
            if (!haveEffective) return null;
            string roleKey = effective.TrackName ?? track.name;
            if (resolveModel == null) return roleKey;
            FacialModelLookup lookup = resolveModel(effective);
            if (!lookup.Resolvable) return roleKey;
            if (lookup.Runner == null)
            {
                Add(issues, FacialCutsceneSeverity.Warning, CodeNoRunner, track.name,
                    "トラック '" + track.name + "' のバインド先のモデル Prefab に FacialCorrectionRunner がありません（補正は掛かりません）");
                return roleKey;
            }
            FacialCorrectionData data = lookup.Runner.data;
            if (data == null || data.layers == null)
            {
                Add(issues, FacialCutsceneSeverity.Warning, CodeNoData, track.name,
                    "トラック '" + track.name + "' のバインド先のモデルの Runner に FacialCorrectionData が設定されていません（補正は掛かりません）");
                return roleKey;
            }
            foreach (TimelineClip clip in track.GetClips())
            {
                var asset = clip.asset as FacialCorrectionClip;
                if (asset == null) continue;
                if (asset.template != null && asset.template.emotions != null)
                {
                    FacialEmotionEntry[] emo = asset.template.emotions;
                    for (int i = 0; i < emo.Length; i++)
                    {
                        string layer = emo[i].layer;
                        if (string.IsNullOrEmpty(layer) || HasLayer(data, layer)) continue;
                        Add(issues, FacialCutsceneSeverity.Warning, CodeUnknownLayer, track.name,
                            "トラック '" + track.name + "' のクリップ '" + clip.displayName + "' の感情レイヤー '" + layer + "' が、モデルの補正データ（" + data.name + "）にありません（その重みは無視されます）");
                    }
                }
                // Maya の演出カーブ（.fctrack）の感情レイヤー
                if (asset.track != null && asset.track.emotions != null)
                {
                    FacialEmotionCurve[] curves = asset.track.emotions;
                    for (int i = 0; i < curves.Length; i++)
                    {
                        string layer = curves[i] != null ? curves[i].layer : null;
                        if (string.IsNullOrEmpty(layer) || HasLayer(data, layer)) continue;
                        Add(issues, FacialCutsceneSeverity.Warning, CodeUnknownLayer, track.name,
                            "トラック '" + track.name + "' のクリップ '" + clip.displayName + "' の演出カーブ（" + asset.track.name + "）の感情レイヤー '" + layer + "' が、モデルの補正データ（" + data.name + "）にありません（その重みは無視されます）");
                    }
                }
            }
            return roleKey;
        }

        /// <summary>
        /// SameAsTrack の参照をたどって、最後（SameAsTrack でない）の Binding を返す。問題があれば説明文（先頭に空白なし）、無ければ null。
        /// D-Drive の CutsceneManager.ResolveSameAsTrack と同じ規則（自己参照・循環・参照先なし）に、参照先のトラックが Timeline にあるかの確認を足す。
        /// </summary>
        public static string FollowSameAsTrack(TimelineAsset timeline, IReadOnlyList<CutsceneBinding> bindings, CutsceneBinding start, out CutsceneBinding terminal)
        {
            terminal = start;
            CutsceneBinding cur = start;
            int count = bindings != null ? bindings.Count : 0;
            for (int depth = 0; depth <= count; depth++)
            {
                string src = CutsceneBindingAccess.GetSourceTrackName(cur);
                if (string.IsNullOrEmpty(src)) return "、SourceTrackName が空です";
                bool found;
                CutsceneBinding next = Find(bindings, src, out found);
                if (!found) return "、SourceTrackName '" + src + "' に一致する Binding がありません";
                if (string.Equals(next.TrackName, cur.TrackName, StringComparison.Ordinal)) return "、SourceTrackName '" + src + "' が自分自身を指しています";
                if (!CutsceneBindingAccess.IsSameAsTrack(next))
                {
                    if (timeline != null && !HasTrack(timeline, src)) return "、参照先 '" + src + "' に対応するトラックが Timeline にありません";
                    terminal = next;
                    return null;
                }
                cur = next;
            }
            return "、SourceTrackName の参照が循環しています";
        }

        static bool HasTrack(TimelineAsset timeline, string name)
        {
            foreach (TrackAsset t in timeline.GetOutputTracks())
                if (t != null && string.Equals(t.name, name, StringComparison.Ordinal)) return true;
            return false;
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
