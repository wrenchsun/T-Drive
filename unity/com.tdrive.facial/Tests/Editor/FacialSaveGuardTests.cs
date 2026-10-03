// 編集時の FC_ の重みが保存されないこと（docs/19 E-4）。ユーザーのシーンは使わない:
//  - Runner は「普通のゲームオブジェクト（= 保存される種類）」で、プレビューシーン（NewPreviewScene）に置く
//  - 「保存されるもの」は SerializedObject の m_BlendShapeWeights（シーンに書かれる中身そのもの）で確かめる
//  - 保存・Undo の通知は、実際の購読者（見張り）を呼び出して確かめる（保存の通知を本物の保存なしで発火する）
//  - ユーザーのシーンが保存済みで一時の追加シーンを作れる環境では、プロジェクトの外の一時ファイルへ実際に保存して確かめる
using System.IO;
using System.Reflection;
using NUnit.Framework;
using TDrive.Facial.Editor;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace TDrive.Facial.Tests
{
    public class FacialSaveGuardTests
    {
        const float Dt = 10f;
        Scene _scene;
        FacialTestRig _rig;

        [SetUp]
        public void SetUp()
        {
            _scene = EditorSceneManager.NewPreviewScene();
            _rig = new FacialTestRig(false, "", true);
            SceneManager.MoveGameObjectToScene(_rig.root, _scene);
            _rig.runner.useManualAngles = true;
            FacialSaveGuard.Enabled = true;
        }

        [TearDown]
        public void TearDown()
        {
            FacialSaveGuard.Enabled = true;
            FacialPreviewDriver.StopAll();
            _rig.Dispose();
            EditorSceneManager.ClosePreviewScene(_scene);
        }

        // シリアライズされる（= 保存される）ブレンドシェイプの重みの絶対値の合計
        float SerializedWeightSum()
        {
            var so = new SerializedObject(_rig.smr);
            SerializedProperty arr = so.FindProperty("m_BlendShapeWeights");
            Assert.IsNotNull(arr, "m_BlendShapeWeights が見つからない");
            float sum = 0f;
            for (int i = 0; i < arr.arraySize; i++) sum += Mathf.Abs(arr.GetArrayElementAtIndex(i).floatValue);
            return sum;
        }

        // Prefab の保存の通知（PrefabStage.prefabSaving）の購読者をすべて呼ぶ（本物の保存はしない）。
        // sceneSaving は Unity 内部の実装で購読者を取り出せないので、シーンの保存は RealSceneSave... のテスト（保存済みシーンが開いている環境）と手動確認で見る
        static void FirePrefabSaving(GameObject root)
        {
            FieldInfo f = typeof(PrefabStage).GetField("prefabSaving", BindingFlags.NonPublic | BindingFlags.Public | BindingFlags.Static);
            var d = f != null ? f.GetValue(null) as System.Delegate : null;
            if (d == null) Assert.Ignore("prefabSaving の購読者を取り出せない環境");
            d.DynamicInvoke(root);
        }

        [Test]
        public void SerializedWeightsAreNonZeroWhileEditingSoTheChecksBelowCanFail()
        {
            _rig.runner.EvaluateNow(0f, 0f, Dt);
            Assert.Greater(_rig.FcSum(), 50f);
            Assert.Greater(SerializedWeightSum(), 50f, "シリアライズされる重みに値が入っていない（以下の検査が失敗しうる形になっていない）");
        }

        [Test]
        public void PrefabSavingNotificationClearsWeightsOfRunnersThePreviewDoesNotTrack()
        {
            _rig.runner.EvaluateNow(0f, 0f, Dt);
            Assert.Greater(SerializedWeightSum(), 50f, "前提");
            Assert.IsFalse(FacialPreviewDriver.IsOn(_rig.runner), "前提: プレビューは追っていない");
            FirePrefabSaving(_rig.root);
            Assert.AreEqual(0f, SerializedWeightSum(), 1e-4f, "保存される重みに FC_ が残っている");
        }

        [Test]
        public void DisabledGuardLeavesWeights_ProvingTheNotificationIsWhatClearsThem()
        {
            _rig.runner.EvaluateNow(0f, 0f, Dt);
            FacialSaveGuard.Enabled = false;
            FirePrefabSaving(_rig.root);
            Assert.Greater(SerializedWeightSum(), 50f, "見張りを切っても消えるなら、上のテストは見張りを検査していない");
        }

        [Test]
        public void UndoRedoPerformedClearsWeightsTheUndoBroughtBack()
        {
            _rig.runner.RebuildCaches();
            int i = new FacialShapeIndex(_rig.mesh).Find(FacialTestRig.N("Neutral", 1, 1));
            _rig.smr.SetBlendShapeWeight(i, 60f); // Undo が古い値を戻した状態
            Assert.IsNotNull(Undo.undoRedoPerformed, "見張りが Undo の通知を購読していない");
            Undo.undoRedoPerformed.Invoke();
            Assert.AreEqual(0f, SerializedWeightSum(), 1e-4f, "Undo のあとに FC_ の重みが残っている");
        }

        [Test]
        public void AssetSaveProcessorAlsoClears()
        {
            _rig.runner.EvaluateNow(0f, 0f, Dt);
            MethodInfo m = typeof(FacialPreviewSaveGuard).GetMethod("OnWillSaveAssets", BindingFlags.NonPublic | BindingFlags.Static);
            Assert.IsNotNull(m);
            m.Invoke(null, new object[] { new string[0] });
            Assert.AreEqual(0f, SerializedWeightSum(), 1e-4f);
        }

        [Test]
        public void EnteringPlayModeAndReloadAlsoClear()
        {
            _rig.runner.EvaluateNow(0f, 0f, Dt);
            MethodInfo pm = typeof(FacialSaveGuard).GetMethod("OnPlayModeChanged", BindingFlags.NonPublic | BindingFlags.Static);
            pm.Invoke(null, new object[] { PlayModeStateChange.ExitingEditMode });
            Assert.AreEqual(0f, SerializedWeightSum(), 1e-4f, "再生に入る前");
            _rig.runner.EvaluateNow(0f, 0f, Dt);
            FacialSaveGuard.OnBeforeSave(); // beforeAssemblyReload・quitting・prefabSaving が呼ぶ入口
            Assert.AreEqual(0f, SerializedWeightSum(), 1e-4f);
        }

        [Test]
        public void EditModeResetZeroesEveryBoundShapeNotOnlyTheOnesItWrote()
        {
            _rig.runner.RebuildCaches();
            int i = new FacialShapeIndex(_rig.mesh).Find(FacialTestRig.N("Neutral", 0, 0));
            _rig.smr.SetBlendShapeWeight(i, 77f);
            _rig.runner.ResetWeights();
            Assert.AreEqual(0f, _rig.smr.GetBlendShapeWeight(i), 1e-6f);
        }

        [Test]
        public void SetBlendShapeWeightMarksTheRendererDirtyNatively_SoTheSceneShowsModifiedButSavesZero()
        {
            // 実測（Unity 6000.3）: SetBlendShapeWeight 自体が SkinnedMeshRenderer を dirty にする。プレビュー中はシーンが「変更あり」に見える。
            // 保存される中身は見張りが 0 に戻す（上のテスト）。dirty の印を消すのは、ユーザーの本当の編集の印まで消しかねないので行わない
            EditorUtility.ClearDirty(_rig.smr);
            Assume.That(!EditorUtility.IsDirty(_rig.smr), "ClearDirty で dirty を消せない環境");
            _rig.smr.SetBlendShapeWeight(0, 10f);
            Assert.IsTrue(EditorUtility.IsDirty(_rig.smr), "挙動が変わった: SetBlendShapeWeight が dirty にしなくなった（マニュアルの注意書きを見直す）");
        }

        [Test]
        public void RealSceneSaveToATempFileOutsideTheProjectWritesNoFcWeights()
        {
            // ユーザーのシーンが「無題・未保存」だと追加シーンを作れない。そのときは実際の保存の検査は行わない（上の通知の検査で代える）
            Scene temp;
            try { temp = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive); }
            catch (System.InvalidOperationException e) { Assert.Ignore("追加シーンを作れない環境: " + e.Message); return; }
            string path = Path.Combine(Path.GetTempPath(), "tdrive_facial_guard_" + System.Guid.NewGuid().ToString("N") + ".unity");
            try
            {
                SceneManager.MoveGameObjectToScene(_rig.root, temp);
                _rig.runner.EvaluateNow(0f, 0f, Dt);
                FacialSaveGuard.Enabled = false;
                if (!EditorSceneManager.SaveScene(temp, path)) Assert.Ignore("プロジェクトの外の一時ファイルへ保存できない環境");
                Assert.Greater(SavedWeightSum(path), 50f, "見張りなしの保存に重みが残らない（検査が失敗しうる形になっていない）");
                FacialSaveGuard.Enabled = true;
                _rig.runner.EvaluateNow(0f, 0f, Dt);
                Assert.IsTrue(EditorSceneManager.SaveScene(temp, path));
                Assert.AreEqual(0f, SavedWeightSum(path), 1e-4f, "保存されたシーンに FC_ の重みが残っている");
            }
            finally
            {
                SceneManager.MoveGameObjectToScene(_rig.root, _scene);
                EditorSceneManager.CloseScene(temp, true);
                try { if (File.Exists(path)) File.Delete(path); } catch { }
            }
        }

        static float SavedWeightSum(string path)
        {
            float sum = 0f;
            bool inBlock = false;
            foreach (string raw in File.ReadAllLines(path))
            {
                string line = raw.Trim();
                if (line.StartsWith("m_BlendShapeWeights:")) { inBlock = true; continue; }
                if (!inBlock) continue;
                float v;
                if (line.StartsWith("- ") && float.TryParse(line.Substring(2), System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out v)) sum += Mathf.Abs(v);
                else inBlock = false;
            }
            return sum;
        }

        [Test]
        public void LeftoverPreviewViewersAreSweptByName()
        {
            var leftover = EditorUtility.CreateGameObjectWithHideFlags(FacialPreviewDriver.ViewerName, HideFlags.HideAndDontSave);
            try
            {
                Assert.GreaterOrEqual(FacialPreviewDriver.SweepLeftoverViewers(), 1);
                Assert.IsTrue(leftover == null, "リロードで参照を失った隠しオブジェクトが残っている");
            }
            finally { if (leftover != null) Object.DestroyImmediate(leftover); }
        }
    }
}
