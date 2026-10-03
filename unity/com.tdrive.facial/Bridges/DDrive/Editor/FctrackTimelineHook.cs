// D-Drive ブリッジ（Editor）: .fctrack を取り込んだら、同じショットのカットシーンの Timeline に Facial のトラックを反映する（FT-3 / FT-5）。
//  - 対象: <ソースルート>/Cutscene/<カテゴリ>/<ショット>__<モデル>.fctrack の取り込み・移動
//  - D-Drive の FBX 取り込みは delayCall で後回しに動く（FC-5 の「取り込み完了」の通知はまだ無い）ので、順序は保証できない。
//    CutsceneData / 役名のアニメーショントラックがまだ無ければ、少し待って数回やり直し、それでも無ければ日本語の警告を 1 回出してやめる
//  - 手動: メニュー「Tools/T-Drive/Facial/.fctrack を Timeline に反映し直す」（選んだ .fctrack に対して）
using System.Collections.Generic;
using DDrive.Editor.Import;
using TDrive.Facial.Core;
using UnityEditor;
using UnityEngine;

namespace TDrive.Facial.DDrive.Editor
{
    public sealed class FctrackTimelineHook : AssetPostprocessor
    {
        public const string MenuPath = "Tools/T-Drive/Facial/.fctrack を Timeline に反映し直す";

        /// <summary>自動の反映を止める（テスト・一括取り込み用）。</summary>
        public static bool Suppress;

        /// <summary>やり直しの回数（最初の 1 回を含む）と、やり直しの間隔（エディタの更新回数）。</summary>
        public const int MaxAttempts = 6;
        public const int TicksBetweenAttempts = 30;

        static readonly Dictionary<string, int> Pending = new Dictionary<string, int>();
        static int _ticks;
        static bool _scheduled, _ticking;

        // ---------------------------------------------------------------- 取り込みの検知

        /// <summary>取り込み・移動されたパスのうち、カットシーン用の .fctrack だけを返す（純粋な関数）。</summary>
        public static List<string> SelectTargets(IEnumerable<string> paths, string sourceRoot)
        {
            var list = new List<string>();
            if (paths == null) return list;
            foreach (string p in paths)
            {
                FctrackShotInfo info;
                if (FctrackShotPath.TryParse(p, sourceRoot, out info) && !list.Contains(info.AssetPath)) list.Add(info.AssetPath);
            }
            return list;
        }

        static void OnPostprocessAllAssets(string[] importedAssets, string[] deletedAssets, string[] movedAssets, string[] movedFromAssetPaths)
        {
            if (Suppress) return;
            string root = ImportRuleService.ResolveSourceRoot(ImportRuleService.DefaultSourceRoot);
            var targets = SelectTargets(Concat(importedAssets, movedAssets), root);
            if (targets.Count == 0) return;
            foreach (string t in targets) Pending[t] = 0; // 取り込み直しは回数を数え直す
            if (!_scheduled)
            {
                _scheduled = true;
                EditorApplication.delayCall += RunPending; // D-Drive の取り込み（delayCall）と同じ階層から始める
            }
        }

        static IEnumerable<string> Concat(string[] a, string[] b)
        {
            if (a != null) foreach (string s in a) yield return s;
            if (b != null) foreach (string s in b) yield return s;
        }

        // ---------------------------------------------------------------- やり直し

        static void RunPending()
        {
            _scheduled = false;
            // D-Drive の取り込み（delayCall）との順序は契約ではない（D-Drive docs/42 R-1）。やり直しで吸収する
            var done = new List<string>();
            var touched = new List<Object>(); // 変えたアセットは最後に 1 回だけ保存する（プロジェクト全体は保存しない）
            var keys = new List<string>(Pending.Keys);
            foreach (string path in keys)
            {
                FctrackSyncResult r = FctrackCutsceneSync.SyncFromPath(path, touched);
                if (r.Status == FctrackSyncStatus.NotReady)
                {
                    int attempts = Pending[path] + 1;
                    if (attempts < MaxAttempts) { Pending[path] = attempts; continue; }
                    Debug.LogWarning("[T-Drive Facial] " + System.IO.Path.GetFileName(path) + " をカットシーンの Timeline に反映できませんでした: " + r.Message
                        + "。カットシーンの FBX（<ショット>.fbx と <ショット>__<モデル>.fbx）を取り込んだあとで、この .fctrack を選んでメニュー「" + MenuPath + "」を実行するか、右クリック → Reimport してください");
                    done.Add(path);
                }
                else
                {
                    Report(path, r);
                    done.Add(path);
                }
            }
            foreach (string p in done) Pending.Remove(p);
            FctrackCutsceneSync.SaveTouched(touched);
            if (Pending.Count > 0) StartTicking();
        }

        static void StartTicking()
        {
            _ticks = TicksBetweenAttempts;
            if (_ticking) return;
            _ticking = true;
            EditorApplication.update += Tick;
        }

        static void Tick()
        {
            if (--_ticks > 0) return;
            EditorApplication.update -= Tick;
            _ticking = false;
            RunPending();
        }

        static void Report(string path, FctrackSyncResult r)
        {
            string name = System.IO.Path.GetFileName(path);
            if (r.Status == FctrackSyncStatus.Failed) Debug.LogWarning("[T-Drive Facial] " + name + ": " + r.Message);
            else if (r.Changed || (r.Binding == FctrackBindingResult.ConflictLeftAlone)) Debug.Log("[T-Drive Facial] " + name + " を反映しました: " + r.Message);
        }

        // ---------------------------------------------------------------- メニュー

        [MenuItem(MenuPath)]
        static void ReapplySelected()
        {
            foreach (string path in SelectedFctrackPaths())
            {
                FctrackSyncResult r = FctrackCutsceneSync.SyncFromPath(path);
                string name = System.IO.Path.GetFileName(path);
                if (r.Status == FctrackSyncStatus.Done) Debug.Log("[T-Drive Facial] " + name + (r.Changed ? " を反映しました: " : " は最新です: ") + r.Message);
                else Debug.LogWarning("[T-Drive Facial] " + name + " を反映できませんでした: " + r.Message);
            }
        }

        [MenuItem(MenuPath, true)]
        static bool ReapplySelectedValidate()
        {
            foreach (string _ in SelectedFctrackPaths()) return true;
            return false;
        }

        static IEnumerable<string> SelectedFctrackPaths()
        {
            foreach (Object o in Selection.objects)
            {
                string p = AssetDatabase.GetAssetPath(o);
                if (!string.IsNullOrEmpty(p) && p.EndsWith(FctrackReader.FileSuffix, System.StringComparison.OrdinalIgnoreCase)) yield return p;
            }
        }
    }
}
