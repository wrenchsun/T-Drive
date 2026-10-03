// D-Drive ブリッジ（Editor）: .fctrack → D-Drive のカットシーン（CutsceneData + TimelineAsset）への反映（FT-3 / FT-5）。
//  - パスの読み取り（FctrackShotPath）と、メモリ上の CutsceneData への反映（FctrackCutsceneSync.ApplyToCutscene）は AssetDatabase に触れない = テストできる
//  - アセットの探し方・保存は SyncFromPath（FctrackTimelineHook から呼ぶ）
// 命名は D-Drive の CutsceneImportService と同じ:
//   .fctrack  = Assets/SourceAssets/Cutscene/<カテゴリ>/<ショット>__<モデル>.fctrack（FBX の <ショット>__<モデル>.fbx と同じ名前）
//   CutsceneData = <GameData>/<AssetNamingService の Cutscene フォルダ>/CUT_<カテゴリ>_<ショット識別子>.asset
//   アニメーショントラック名 = FBX のファイル名の "__" の後ろ（<モデル>。"_2" 等の末尾も含む）→ 自動トラックは "<モデル>_Facial(auto)"
using System;
using System.Collections.Generic;
using DDrive.Editor.AssetBrowser;
using DDrive.Editor.Cutscene;
using DDrive.Editor.Import;
using DDrive.Editor.Versioning;
using DDrive.Runtime.Cutscene;
using TDrive.Facial.Core;
using TDrive.Facial.Timeline.Editor;
using UnityEditor;
using UnityEngine.Timeline;

namespace TDrive.Facial.DDrive.Editor
{
    /// <summary>.fctrack のパスから分かること。</summary>
    public struct FctrackShotInfo
    {
        public string AssetPath;
        /// <summary>Cutscene フォルダの下のサブフォルダ（空 = 直下）。</summary>
        public string Category;
        /// <summary>ショット名（"__" の前）。</summary>
        public string Shot;
        /// <summary>モデル識別子（"__" の後ろ）= D-Drive が作るアニメーショントラックの名前。</summary>
        public string RoleTrackName;
    }

    public static class FctrackShotPath
    {
        /// <summary>
        /// "&lt;sourceRoot&gt;/Cutscene/&lt;カテゴリ&gt;/&lt;ショット&gt;__&lt;モデル&gt;.fctrack" か。違えば false。
        /// D-Drive の CutsceneImportService.ProcessPaths と同じ読み方（"\" は "/" にそろえる、先頭一致は大文字小文字を区別する）。
        /// </summary>
        public static bool TryParse(string assetPath, string sourceRoot, out FctrackShotInfo info)
        {
            info = default(FctrackShotInfo);
            if (string.IsNullOrEmpty(assetPath) || string.IsNullOrEmpty(sourceRoot)) return false;
            string path = assetPath.Replace('\\', '/');
            if (!path.EndsWith(FctrackReader.FileSuffix, StringComparison.OrdinalIgnoreCase)) return false;
            string root = sourceRoot.Replace('\\', '/').TrimEnd('/') + "/" + CutsceneImportService.TypeFolder + "/";
            if (!path.StartsWith(root, StringComparison.Ordinal)) return false;

            string rest = path.Substring(root.Length);
            int slash = rest.LastIndexOf('/');
            string fileName = slash < 0 ? rest : rest.Substring(slash + 1);
            string category = slash < 0 ? string.Empty : rest.Substring(0, slash);
            string stem = fileName.Substring(0, fileName.Length - FctrackReader.FileSuffix.Length);

            string shot, model;
            if (!FctrackReader.TryParseFileStem(stem, out shot, out model)) return false; // "<ショット>__<モデル>" でない = カットシーン用ではない
            info = new FctrackShotInfo { AssetPath = path, Category = category, Shot = shot, RoleTrackName = model };
            return true;
        }

        /// <summary>D-Drive が作る CutsceneData のパス（CutsceneImportService.ProcessShot と同じ規則）。</summary>
        public static string CutsceneDataPath(FctrackShotInfo info, string gameDataRoot, string identifierFallback)
        {
            string identifier = AssetNamingService.ToIdentifier(info.Shot, identifierFallback);
            return CutsceneImportService.ComputeCutsceneDataPath(gameDataRoot, info.Category, identifier);
        }
    }

    public enum FctrackBindingResult
    {
        /// <summary>Bindings に足した。</summary>
        Added,
        /// <summary>同じ内容が既にある（何もしない）。</summary>
        AlreadyOk,
        /// <summary>同じトラック名の Binding が別の内容で既にある（デザイナーが作ったもの。触らない）。</summary>
        ConflictLeftAlone,
        /// <summary>この D-Drive には Target = SameAsTrack が無い（1.4.0 より前）ので足していない。トラック名の規則（'&lt;役名&gt;_Facial(auto)'）で結ばれる。</summary>
        NotSupported,
    }

    public enum FctrackSyncStatus
    {
        /// <summary>反映できた（変更なしを含む）。</summary>
        Done,
        /// <summary>まだ反映できない（CutsceneData / Timeline / 役名のアニメーショントラックが無い）。あとで再試行する。</summary>
        NotReady,
        /// <summary>反映できない（.fctrack が読めない、など）。再試行しても変わらない。</summary>
        Failed,
    }

    public sealed class FctrackSyncResult
    {
        public FctrackSyncStatus Status;
        public string Message = "";
        public FacialTrackSyncReport Timeline;
        public FctrackBindingResult? Binding;
        /// <summary>NotReady の理由が「このショットの CutsceneData がまだ無い」こと（FBX の取り込みで作られる。FC-5 のリスナーが取り込み時に反映する）。</summary>
        public bool NoCutsceneData;
        public bool Changed;
        public override string ToString() { return Status + ": " + Message; }
    }

    public static class FctrackCutsceneSync
    {
        /// <summary>
        /// CutsceneData の TimelineAsset に自動トラックを反映し、Bindings に SameAsTrack の Binding を 1 つ足す（無ければ）。保存はしない。
        /// デザイナーが作った Binding（同じトラック名で内容が違うもの）には触らず、警告だけ返す。
        /// </summary>
        public static FctrackSyncResult ApplyToCutscene(CutsceneData data, FacialTrackAsset asset, string roleTrackName)
        {
            return Apply(data, data != null ? data.Timeline : null, asset, roleTrackName);
        }

        /// <summary>
        /// 反映の共通の入口（探さない・保存しない）。後追いの取り込み（SyncFromPath）と D-Drive の取り込み完了の通知（FC-5 のリスナー）が同じ処理を通る。
        /// 何度呼んでも重複しない（"(auto)" のトラックと Binding を先に確かめる）。timeline = null なら data.Timeline。
        /// </summary>
        public static FctrackSyncResult Apply(CutsceneData data, TimelineAsset timeline, FacialTrackAsset asset, string roleTrackName)
        {
            var r = new FctrackSyncResult();
            if (data != null && timeline == null) timeline = data.Timeline;
            if (asset == null) { r.Status = FctrackSyncStatus.Failed; r.Message = ".fctrack（FacialTrackAsset）がありません"; return r; }
            if (string.IsNullOrEmpty(roleTrackName)) { r.Status = FctrackSyncStatus.Failed; r.Message = "役名（モデル識別子）が分かりません"; return r; }
            if (data == null) { r.Status = FctrackSyncStatus.NotReady; r.NoCutsceneData = true; r.Message = "このショットの CutsceneData がまだありません"; return r; }
            if (timeline == null) { r.Status = FctrackSyncStatus.NotReady; r.Message = "CutsceneData '" + data.name + "' に TimelineAsset がありません"; return r; }

            // D-Drive のフレーム範囲の切り出し（SourceFrameRange）に、クリップの開始位置と長さを合わせる（docs/19 E-5）
            var options = new FacialTrackSyncOptions { SourceStartFrame = data.SourceFrameRange.Start, SourceEndFrame = data.SourceFrameRange.End };
            FacialTrackSyncReport tl = FacialTrackTimelineSync.Apply(timeline, asset, roleTrackName, options);
            r.Timeline = tl;
            if (!tl.Success) { r.Status = FctrackSyncStatus.NotReady; r.Message = string.Join(" / ", tl.Messages); return r; }

            FctrackBindingResult b = EnsureSameAsTrackBinding(data, tl.TrackName, roleTrackName);
            ForgetUndo(data, tl);
            r.Binding = b;
            r.Changed = tl.Changed || b == FctrackBindingResult.Added;
            r.Status = FctrackSyncStatus.Done;
            r.Message = "'" + data.name + "' の " + string.Join(" / ", tl.Messages);
            if (b == FctrackBindingResult.Added) r.Message += " / Bindings に '" + tl.TrackName + "'（SameAsTrack → '" + roleTrackName + "'）を足しました";
            else if (b == FctrackBindingResult.NotSupported)
                r.Message += " / この D-Drive には SameAsTrack が無いので Binding は足していません（トラック名の規則 '" + tl.TrackName + "' で '" + roleTrackName + "' のモデルに結ばれます）";
            else if (b == FctrackBindingResult.ConflictLeftAlone)
                r.Message += " / 注意: Bindings に同じ名前 '" + tl.TrackName + "' の別の設定が既にあるので変えていません（SameAsTrack → '" + roleTrackName + "' にするとモデルに結ばれます）";
            return r;
        }

        // 自動の反映は Undo に積まない（Timeline の CreateTrack / CreateClip が積む記録を消す。ユーザーの Ctrl+Z で消える対象にしない。docs/19 E-12）
        static void ForgetUndo(CutsceneData data, FacialTrackSyncReport tl)
        {
            UnityEditor.Undo.ClearUndo(data);
            if (data.Timeline != null) UnityEditor.Undo.ClearUndo(data.Timeline);
            if (tl.Track != null) UnityEditor.Undo.ClearUndo(tl.Track);
            if (tl.Clip != null && tl.Clip.asset != null) UnityEditor.Undo.ClearUndo(tl.Clip.asset);
        }

        /// <summary>
        /// Bindings に { TrackName, Target = SameAsTrack, SourceTrackName } を 1 つ足す。同名があれば足さない（内容が同じなら AlreadyOk、違えば触らない）。
        /// SerializedObject 経由・名前で読み書きする（SameAsTrack の無い D-Drive でもコンパイルできる。無ければ NotSupported）。
        /// Undo には積まない（自動の反映。docs/19 E-12）。
        /// </summary>
        public static FctrackBindingResult EnsureSameAsTrackBinding(CutsceneData data, string trackName, string sourceTrackName)
        {
            int sameAs;
            if (!CutsceneBindingAccess.TryGetSameAsTrackValue(out sameAs)) return FctrackBindingResult.NotSupported;
            var so = new SerializedObject(data);
            SerializedProperty arr = so.FindProperty("Bindings");
            for (int i = 0; i < arr.arraySize; i++)
            {
                SerializedProperty el = arr.GetArrayElementAtIndex(i);
                if (el.FindPropertyRelative("TrackName").stringValue != trackName) continue;
                SerializedProperty src = el.FindPropertyRelative(CutsceneBindingAccess.SourceTrackFieldName);
                bool same = el.FindPropertyRelative("Target").intValue == sameAs && src != null && src.stringValue == sourceTrackName;
                return same ? FctrackBindingResult.AlreadyOk : FctrackBindingResult.ConflictLeftAlone;
            }
            int index = arr.arraySize;
            arr.InsertArrayElementAtIndex(index); // 末尾の複製になるので、全項目を書き直す
            CutsceneBinding fresh;
            CutsceneBindingAccess.TryMakeSameAsTrack(trackName, sourceTrackName, out fresh);
            arr.GetArrayElementAtIndex(index).boxedValue = fresh;
            so.ApplyModifiedPropertiesWithoutUndo();
            EditorUtility.SetDirty(data);
            return FctrackBindingResult.Added;
        }

        /// <summary>
        /// 触ったアセットだけを保存する。D-Drive の版数は進めない（VersionStampSuppression の中）。
        /// プロジェクト全体の SaveAssets は呼ばない（開いている別のアセットを巻き込まない。docs/19 E-7）。
        /// </summary>
        public static void SaveTouched(IEnumerable<UnityEngine.Object> assets)
        {
            if (assets == null) return;
            using (VersionStampSuppression.Scope())
            {
                var done = new HashSet<UnityEngine.Object>();
                foreach (UnityEngine.Object o in assets)
                    if (o != null && done.Add(o)) AssetDatabase.SaveAssetIfDirty(o);
            }
        }

        /// <summary>.fctrack のアセットパスから CutsceneData を探して反映し、変更があれば保存する（版数は進めない。D-Drive の自動取り込みと同じ扱い）。</summary>
        public static FctrackSyncResult SyncFromPath(string fctrackPath)
        {
            var touched = new List<UnityEngine.Object>();
            FctrackSyncResult r = SyncFromPath(fctrackPath, touched);
            SaveTouched(touched);
            return r;
        }

        /// <summary>
        /// 反映する。変えたアセット（CutsceneData・TimelineAsset）は touched に足すだけで、保存しない（呼び出し側が SaveTouched で 1 回にまとめる）。
        /// </summary>
        public static FctrackSyncResult SyncFromPath(string fctrackPath, List<UnityEngine.Object> touched)
        {
            string sourceRoot = ImportRuleService.ResolveSourceRoot(ImportRuleService.DefaultSourceRoot);
            FctrackShotInfo info;
            if (!FctrackShotPath.TryParse(fctrackPath, sourceRoot, out info))
                return new FctrackSyncResult
                {
                    Status = FctrackSyncStatus.Failed,
                    Message = "'" + fctrackPath + "' は " + sourceRoot + "/" + CutsceneImportService.TypeFolder + "/<カテゴリ>/<ショット>__<モデル>.fctrack の形ではないので、カットシーンへは反映しません",
                };

            var asset = AssetDatabase.LoadAssetAtPath<FacialTrackAsset>(info.AssetPath);
            if (asset == null)
                return new FctrackSyncResult { Status = FctrackSyncStatus.Failed, Message = ".fctrack を読み込めません（取り込みに失敗しています。Console のエラーを見てください）: " + info.AssetPath };

            string dataPath = FctrackShotPath.CutsceneDataPath(info, AssetCreationService.ResolveGameDataRoot(AssetCreationService.DefaultGameDataRoot),
                CutsceneImportProfile.FindOrDefault().IdentifierFallback);
            var data = AssetDatabase.LoadAssetAtPath<CutsceneData>(dataPath);
            FctrackSyncResult result = ApplyToCutscene(data, asset, info.RoleTrackName);
            if (data == null) result.Message += "（探した場所: " + dataPath + "）";

            if (result.Status == FctrackSyncStatus.Done && result.Changed)
            {
                EditorUtility.SetDirty(data);
                if (data.Timeline != null) EditorUtility.SetDirty(data.Timeline);
                if (touched != null) { touched.Add(data); if (data.Timeline != null) touched.Add(data.Timeline); }
            }
            return result;
        }
    }
}
