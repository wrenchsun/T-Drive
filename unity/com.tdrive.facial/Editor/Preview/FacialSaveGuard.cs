// 編集時の FC_ の重みを、シーン・Prefab・アセットに保存させないための見張り（docs/19 E-4）。
// 次のときに、読み込まれているすべての FacialCorrectionRunner（プレビューが追っているものだけでなく、シーンと Prefab ステージの全部）の
// FC_ シェイプを 0 に戻し、プレビューは続ける（次の更新で評価し直す）:
//   シーンの保存の直前 / Prefab の保存の直前 / アセットの保存の直前（AssetModificationProcessor）/ アセンブリのリロード前 /
//   エディタの終了時 / 再生に入る前 / Undo・Redo の直後（Undo が古い重みを戻すことがあるため）
// Runner の ResetWeights は編集時、結んだ FC_ をすべて 0 にする。
using System.Collections.Generic;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace TDrive.Facial.Editor
{
    [InitializeOnLoad]
    public static class FacialSaveGuard
    {
        /// <summary>テスト用: false にすると何もしない（見張りが無いと保存に重みが残ることを確かめるため）。</summary>
        public static bool Enabled = true;

        static FacialSaveGuard()
        {
            AssemblyReloadEvents.beforeAssemblyReload += OnBeforeSave;
            EditorApplication.quitting += OnBeforeSave;
            EditorApplication.playModeStateChanged += OnPlayModeChanged;
            EditorSceneManager.sceneSaving += (scene, path) => OnBeforeSave();
            PrefabStage.prefabSaving += root => OnBeforeSave();
            Undo.undoRedoPerformed += OnBeforeSave;
        }

        static void OnPlayModeChanged(PlayModeStateChange change)
        {
            if (change == PlayModeStateChange.ExitingEditMode) OnBeforeSave();
        }

        /// <summary>保存の直前などに呼ぶ。読み込まれているすべての Runner の FC_ を 0 に戻し、プレビューの再評価を予約する。</summary>
        public static void OnBeforeSave()
        {
            if (!Enabled || Application.isPlaying) return;
            ResetAllRunners();
            FacialPreviewDriver.ResetAllWeights();
        }

        /// <summary>読み込まれているシーン・Prefab ステージのすべての Runner を ResetWeights する。戻した数を返す。</summary>
        public static int ResetAllRunners()
        {
            var seen = new HashSet<FacialCorrectionRunner>();
            // シーンに入っているものすべて（読み込み中のシーン・Prefab ステージ・プレビューシーン）。アセット（Prefab ファイル）と隠しオブジェクトは対象外
            FacialCorrectionRunner[] all = Resources.FindObjectsOfTypeAll<FacialCorrectionRunner>();
            for (int i = 0; i < all.Length; i++)
                if (all[i] != null && all[i].gameObject.scene.IsValid() && !EditorUtility.IsPersistent(all[i])) seen.Add(all[i]);
            PrefabStage stage = PrefabStageUtility.GetCurrentPrefabStage();
            if (stage != null && stage.prefabContentsRoot != null)
            {
                FacialCorrectionRunner[] inStage = stage.prefabContentsRoot.GetComponentsInChildren<FacialCorrectionRunner>(true);
                for (int i = 0; i < inStage.Length; i++) seen.Add(inStage[i]);
            }
            int n = 0;
            foreach (FacialCorrectionRunner r in seen)
            {
                if (r == null) continue;
                r.ResetWeights();
                n++;
            }
            return n;
        }
    }

    /// <summary>アセットの保存の直前（シーン・Prefab・ScriptableObject のどれでも）に FC_ の重みを戻す。</summary>
    public sealed class FacialPreviewSaveGuard : AssetModificationProcessor
    {
        static string[] OnWillSaveAssets(string[] paths)
        {
            FacialSaveGuard.OnBeforeSave();
            return paths;
        }
    }
}
