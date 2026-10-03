// D-Drive ブリッジ（Editor）: カットシーンの取り込み完了の通知（FC-5）で .fctrack を反映するための、D-Drive 1.4.0 の型を使わない部分（FT-6）。
//  - FC-5 のリスナー本体（TDrive.Facial.DDrive.FC.Editor。1.4.0 以降だけコンパイルされる）は、役ごとにここを呼ぶだけ。テストはこちらを直接呼べる（メモリ上のアセット）
//  - FctrackListenerGate: 「リスナーが使えるか」の実行時の判断。使えるとき、AssetPostprocessor の後追い（FctrackTimelineHook）は再試行しない（二重に反映しない）
//  - 保存しない・Undo に積まない（D-Drive が全リスナーのあとに 1 回保存する）。何度呼んでも重複しない（FctrackCutsceneSync.Apply が確かめる）
using System;
using DDrive.Runtime.Cutscene;
using TDrive.Facial.Core;
using UnityEditor;
using UnityEngine;
using UnityEngine.Timeline;

namespace TDrive.Facial.DDrive.Editor
{
    /// <summary>FC-5（ICutsceneImportListener）の .fctrack のリスナーが、この環境にあるか。型名で調べる（FC のアセンブリを参照しない）。</summary>
    public static class FctrackListenerGate
    {
        public const string ListenerTypeName = "TDrive.Facial.DDrive.Editor.FctrackImportListener, TDrive.Facial.DDrive.FC.Editor";

        /// <summary>テスト用: true / false で固定する。null = 実際に調べる。</summary>
        public static bool? OverrideForTests;

        static bool? _cached;

        public static bool Available
        {
            get
            {
                if (OverrideForTests.HasValue) return OverrideForTests.Value;
                if (!_cached.HasValue) _cached = Type.GetType(ListenerTypeName, false) != null;
                return _cached.Value;
            }
        }
    }

    /// <summary>NotReady のときの後追いの扱い。</summary>
    public enum FctrackRetryDecision
    {
        /// <summary>もう一度やる（D-Drive に FC-5 が無い・FBX の取り込みを待つ）。</summary>
        Retry,
        /// <summary>やめる。警告を出す。</summary>
        GiveUpWarn,
        /// <summary>やめる。何も出さない（FBX の取り込み時にリスナーが反映する）。</summary>
        GiveUpSilent,
    }

    public static class FctrackImportApply
    {
        /// <summary>
        /// 後追い（.fctrack の取り込み側）で NotReady だったときの扱い（純粋な関数）。
        /// リスナーがあるとき: CutsceneData がまだ無いだけなら、あとの FBX の取り込みでリスナーが反映するので静かにやめる。それ以外は再試行せず警告。
        /// リスナーが無いとき: 上限まで再試行し、超えたら警告（従来どおり）。
        /// </summary>
        public static FctrackRetryDecision DecideRetry(FctrackSyncResult r, int attemptsSoFar, int maxAttempts, bool listenerAvailable)
        {
            if (listenerAvailable)
                return r != null && r.NoCutsceneData ? FctrackRetryDecision.GiveUpSilent : FctrackRetryDecision.GiveUpWarn;
            return attemptsSoFar + 1 < maxAttempts ? FctrackRetryDecision.Retry : FctrackRetryDecision.GiveUpWarn;
        }

        /// <summary>
        /// 役 1 つ分: FBX の隣の同名の .fctrack（FBX が &lt;ショット&gt;__&lt;モデル&gt;.fbx なら &lt;ショット&gt;__&lt;モデル&gt;.fctrack）を探して、(data, timeline) に反映する。
        /// .fctrack が無ければ null（何もしない）。保存しない。exists / load は差し替え用（null = AssetDatabase / ファイル）。
        /// </summary>
        public static FctrackSyncResult ApplyForRole(CutsceneData data, TimelineAsset timeline, string roleName, string sourceFbxPath,
            Func<string, bool> exists = null, Func<string, FacialTrackAsset> load = null)
        {
            string path = FctrackPathFor(sourceFbxPath);
            if (path == null) return null;
            if (exists == null) exists = DefaultExists;
            if (load == null) load = AssetDatabase.LoadAssetAtPath<FacialTrackAsset>;
            if (!exists(path)) return null;

            FacialTrackAsset asset = load(path);
            if (asset == null)
                return new FctrackSyncResult { Status = FctrackSyncStatus.Failed, Message = ".fctrack を読み込めません（取り込みに失敗しています。Console のエラーを見てください）: " + path };

            FctrackSyncResult r = FctrackCutsceneSync.Apply(data, timeline, asset, roleName);
            if (r.Status == FctrackSyncStatus.Done && r.Changed)
            {
                EditorUtility.SetDirty(data); // 保存は D-Drive が 1 回行う
                if (timeline != null) EditorUtility.SetDirty(timeline);
            }
            return r;
        }

        /// <summary>FBX のパス → 隣の同名の .fctrack のパス。FBX のパスが空なら null。</summary>
        public static string FctrackPathFor(string sourceFbxPath)
        {
            if (string.IsNullOrEmpty(sourceFbxPath)) return null;
            string p = sourceFbxPath.Replace('\\', '/');
            int dot = p.LastIndexOf('.');
            int slash = p.LastIndexOf('/');
            string stem = dot > slash ? p.Substring(0, dot) : p;
            return stem + FctrackReader.FileSuffix;
        }

        static bool DefaultExists(string assetPath)
        {
            return !string.IsNullOrEmpty(AssetDatabase.AssetPathToGUID(assetPath)) || System.IO.File.Exists(assetPath);
        }
    }
}
