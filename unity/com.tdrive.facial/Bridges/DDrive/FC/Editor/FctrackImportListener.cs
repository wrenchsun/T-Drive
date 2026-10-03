// D-Drive ブリッジ（Editor / FC-5 = D-Drive 1.4.0 以降）: カットシーンの取り込み完了の通知で、役ごとに隣の .fctrack を Timeline に反映する（FT-6 / docs/19 E-8）。
//  - D-Drive が FBX 取り込みのたび（再取り込みも）呼ぶ。TypeCache で自動発見される（public・引数なしコンストラクタ）。登録コードは不要
//  - 反映は FctrackImportApply → FctrackCutsceneSync.Apply（後追いの取り込みと同じ処理）。何度呼んでも重複しない。デザイナーが動かしたクリップは動かさない（E-6）
//  - 保存しない・Undo に積まない（D-Drive が全リスナーのあとに 1 回保存する）。.fctrack だけを後から置いたときはここは呼ばれない → FctrackTimelineHook が 1 回だけ反映する
//  - FctrackListenerGate はこの型の名前で「リスナーがある」と判断する。型名・アセンブリ名を変えるときは ListenerTypeName も直す（テストが確かめる）
using System;
using DDrive.Editor.Cutscene;
using UnityEngine;

namespace TDrive.Facial.DDrive.Editor
{
    public sealed class FctrackImportListener : ICutsceneImportListener
    {
        /// <summary>ほかのリスナー（トラックを足すもの）のあとに呼ぶ。</summary>
        public int Order { get { return 1000; } }

        public void OnCutsceneShotImported(CutsceneImportResult result)
        {
            Apply(result, null, null);
        }

        /// <summary>キャラクターの役ごとに反映する。反映した（Done の）役の数を返す。exists / load はテスト用の差し替え（null = AssetDatabase）。</summary>
        public static int Apply(CutsceneImportResult result, Func<string, bool> exists, Func<string, FacialTrackAsset> load)
        {
            if (result == null || result.Data == null || result.Roles == null) return 0;
            int done = 0;
            for (int i = 0; i < result.Roles.Count; i++)
            {
                CutsceneImportRole role = result.Roles[i];
                if (role == null || role.Kind != CutsceneImportRoleKind.Character) continue; // カメラ・小物には表情が無い
                FctrackSyncResult r = FctrackImportApply.ApplyForRole(result.Data, result.Timeline, role.RoleName, role.SourcePath, exists, load);
                if (r == null) continue; // .fctrack が無い役
                string path = FctrackImportApply.FctrackPathFor(role.SourcePath);
                if (r.Status == FctrackSyncStatus.NotReady)
                    Debug.LogWarning("[T-Drive Facial] " + System.IO.Path.GetFileName(path) + " を取り込み時に反映できませんでした: " + r.Message);
                else FctrackTimelineHook.Report(path, r);
                if (r.Status == FctrackSyncStatus.Done) done++;
            }
            return done;
        }
    }
}
