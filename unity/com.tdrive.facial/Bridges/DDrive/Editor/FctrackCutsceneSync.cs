// D-Drive ブリッジ（Editor）: .fctrack → D-Drive のカットシーン（CutsceneData + TimelineAsset）への反映（FT-3 / FT-5）。
//  - パスの読み取り（FctrackShotPath）と、メモリ上の CutsceneData への反映（FctrackCutsceneSync.ApplyToCutscene）は AssetDatabase に触れない = テストできる
//  - アセットの探し方・保存は SyncFromPath（FctrackTimelineHook から呼ぶ）
// 命名は D-Drive の CutsceneImportService と同じ:
//   .fctrack  = Assets/SourceAssets/Cutscene/<カテゴリ>/<ショット>__<モデル>.fctrack（FBX の <ショット>__<モデル>.fbx と同じ名前）
//   CutsceneData = <GameData>/<AssetNamingService の Cutscene フォルダ>/CUT_<カテゴリ>_<ショット識別子>.asset
//   アニメーショントラック名 = FBX のファイル名の "__" の後ろ（<モデル>。"_2" 等の末尾も含む）→ 自動トラックは "<モデル>_Facial(auto)"
using System;
using DDrive.Editor.AssetBrowser;
using DDrive.Editor.Cutscene;
using DDrive.Editor.Import;
using DDrive.Editor.Versioning;
using DDrive.Runtime.Cutscene;
using TDrive.Facial.Core;
using TDrive.Facial.Timeline.Editor;
using UnityEditor;

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
            var r = new FctrackSyncResult();
            if (asset == null) { r.Status = FctrackSyncStatus.Failed; r.Message = ".fctrack（FacialTrackAsset）がありません"; return r; }
            if (string.IsNullOrEmpty(roleTrackName)) { r.Status = FctrackSyncStatus.Failed; r.Message = "役名（モデル識別子）が分かりません"; return r; }
            if (data == null) { r.Status = FctrackSyncStatus.NotReady; r.Message = "このショットの CutsceneData がまだありません"; return r; }
            if (data.Timeline == null) { r.Status = FctrackSyncStatus.NotReady; r.Message = "CutsceneData '" + data.name + "' に TimelineAsset がありません"; return r; }

            FacialTrackSyncReport tl = FacialTrackTimelineSync.Apply(data.Timeline, asset, roleTrackName);
            r.Timeline = tl;
            if (!tl.Success) { r.Status = FctrackSyncStatus.NotReady; r.Message = string.Join(" / ", tl.Messages); return r; }

            FctrackBindingResult b = EnsureSameAsTrackBinding(data, tl.TrackName, roleTrackName);
            r.Binding = b;
            r.Changed = tl.Changed || b == FctrackBindingResult.Added;
            r.Status = FctrackSyncStatus.Done;
            r.Message = "'" + data.name + "' の " + string.Join(" / ", tl.Messages);
            if (b == FctrackBindingResult.Added) r.Message += " / Bindings に '" + tl.TrackName + "'（SameAsTrack → '" + roleTrackName + "'）を足しました";
            else if (b == FctrackBindingResult.ConflictLeftAlone)
                r.Message += " / 注意: Bindings に同じ名前 '" + tl.TrackName + "' の別の設定が既にあるので変えていません（SameAsTrack → '" + roleTrackName + "' にするとモデルに結ばれます）";
            return r;
        }

        /// <summary>Bindings に { TrackName, Target = SameAsTrack, SourceTrackName } を 1 つ足す。同名があれば足さない（内容が同じなら AlreadyOk、違えば触らない）。SerializedObject 経由。</summary>
        public static FctrackBindingResult EnsureSameAsTrackBinding(CutsceneData data, string trackName, string sourceTrackName)
        {
            var so = new SerializedObject(data);
            SerializedProperty arr = so.FindProperty("Bindings");
            for (int i = 0; i < arr.arraySize; i++)
            {
                SerializedProperty el = arr.GetArrayElementAtIndex(i);
                if (el.FindPropertyRelative("TrackName").stringValue != trackName) continue;
                bool same = el.FindPropertyRelative("Target").intValue == (int)CutsceneBindTarget.SameAsTrack
                            && el.FindPropertyRelative("SourceTrackName").stringValue == sourceTrackName;
                return same ? FctrackBindingResult.AlreadyOk : FctrackBindingResult.ConflictLeftAlone;
            }
            int index = arr.arraySize;
            arr.InsertArrayElementAtIndex(index); // 末尾の複製になるので、全項目を書き直す
            arr.GetArrayElementAtIndex(index).boxedValue = new CutsceneBinding
            {
                TrackName = trackName,
                Target = CutsceneBindTarget.SameAsTrack,
                SourceTrackName = sourceTrackName,
            };
            so.ApplyModifiedProperties();
            EditorUtility.SetDirty(data);
            return FctrackBindingResult.Added;
        }

        /// <summary>.fctrack のアセットパスから CutsceneData を探して反映し、変更があれば保存する（版数は進めない。D-Drive の自動取り込みと同じ扱い）。</summary>
        public static FctrackSyncResult SyncFromPath(string fctrackPath)
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
                DDriveAssetSave.SaveAllSuppressed();
            }
            return result;
        }
    }
}
