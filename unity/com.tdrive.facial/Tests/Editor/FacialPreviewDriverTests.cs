// エディタのプレビュー（FacialPreviewDriver）のテスト。Scene ビューは使わず、角度指定・仮想のカメラ Transform で動かす。
// ユーザーのシーンには触らない（テストの物はすべて HideAndDontSave）。シーンの dirty は FacialSaveGuardTests（一時のシーン）で確かめる。
using NUnit.Framework;
using TDrive.Facial.Editor;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace TDrive.Facial.Tests
{
    public class FacialPreviewDriverTests
    {
        const float Dt = 0.02f;
        FacialTestRig _rig;

        [SetUp]
        public void SetUp() { _rig = new FacialTestRig(); }

        [TearDown]
        public void TearDown()
        {
            FacialPreviewDriver.StopAll();
            _rig.Dispose();
        }

        FacialPreviewState ManualState(float yaw, float pitch)
        {
            FacialPreviewState st = FacialPreviewDriver.GetState(_rig.runner);
            st.mode = FacialPreviewViewMode.Manual;
            st.yaw = yaw; st.pitch = pitch; st.distance = 2f;
            return st;
        }

        [Test]
        public void EvaluateWritesWeightsAndOffResetsThem()
        {
            FacialPreviewState st = ManualState(45f, 0f);
            FacialPreviewDriver.SetOn(_rig.runner, true);
            Assert.IsTrue(FacialPreviewDriver.IsOn(_rig.runner));
            Assert.IsTrue(FacialPreviewDriver.EvaluateOnce(_rig.runner, st, Dt, null));
            Assert.AreEqual(50f, _rig.W(FacialTestRig.N("Neutral", 1, 1)), 0.1f);
            Assert.AreEqual(50f, _rig.W(FacialTestRig.N("Neutral", 1, 2)), 0.1f);

            FacialPreviewDriver.SetOn(_rig.runner, false);
            Assert.IsFalse(FacialPreviewDriver.IsOn(_rig.runner));
            Assert.AreEqual(0f, _rig.FcSum(), 1e-6f);
        }

        [Test]
        public void ABOffResetsAndOnRestores()
        {
            FacialPreviewState st = ManualState(0f, 0f);
            FacialPreviewDriver.SetOn(_rig.runner, true);
            FacialPreviewDriver.EvaluateOnce(_rig.runner, st, Dt, null);
            Assert.Greater(_rig.FcSum(), 50f);
            st.correction = false; st.forceEval = true;
            FacialPreviewDriver.EvaluateOnce(_rig.runner, st, Dt, null);
            Assert.AreEqual(0f, _rig.FcSum(), 1e-6f);
            st.correction = true; st.forceEval = true;
            FacialPreviewDriver.EvaluateOnce(_rig.runner, st, Dt, null);
            Assert.AreEqual(100f, _rig.W(FacialTestRig.N("Neutral", 1, 1)), 0.1f);
        }

        [Test]
        public void SaveResetsWeightsAndPreviewResumes()
        {
            FacialPreviewState st = ManualState(0f, 0f);
            FacialPreviewDriver.SetOn(_rig.runner, true);
            FacialPreviewDriver.EvaluateOnce(_rig.runner, st, Dt, null);
            Assert.Greater(_rig.FcSum(), 0f);
            FacialPreviewDriver.OnBeforeSave(); // シーン・Prefab・アセットの保存の直前、リロード前、終了時に呼ばれる
            Assert.AreEqual(0f, _rig.FcSum(), 1e-6f, "保存の直前に戻っている");
            Assert.IsTrue(FacialPreviewDriver.IsOn(_rig.runner), "プレビューは続く");
            FacialPreviewDriver.EvaluateOnce(_rig.runner, st, Dt, null);
            Assert.AreEqual(100f, _rig.W(FacialTestRig.N("Neutral", 1, 1)), 0.1f);
        }

        [Test]
        public void ResetAllWeightsHandlesDestroyedRunners()
        {
            FacialPreviewState st = ManualState(0f, 0f);
            FacialPreviewDriver.SetOn(_rig.runner, true);
            FacialPreviewDriver.EvaluateOnce(_rig.runner, st, Dt, null);
            Object.DestroyImmediate(_rig.runner);
            Assert.DoesNotThrow(() => FacialPreviewDriver.ResetAllWeights());
            Assert.DoesNotThrow(() => FacialPreviewDriver.StopAll());
        }

        [Test]
        public void SceneCameraModeUsesTheGivenCameraAndSkipsWhenIdle()
        {
            var cam = EditorUtility.CreateGameObjectWithHideFlags("TestCam", HideFlags.HideAndDontSave);
            try
            {
                FacialPreviewState st = FacialPreviewDriver.GetState(_rig.runner);
                st.mode = FacialPreviewViewMode.SceneCamera;
                cam.transform.position = new Vector3(-2f, 1.5f, 0f); // キャラクターの左 = Yaw +90
                FacialPreviewDriver.SetOn(_rig.runner, true);
                Assert.IsTrue(FacialPreviewDriver.EvaluateOnce(_rig.runner, st, Dt, cam.transform));
                Assert.AreEqual(90f, _rig.runner.CurrentYaw, 1e-2f);
                Assert.AreEqual(100f, _rig.W(FacialTestRig.N("Neutral", 1, 2)), 0.1f);
                Assert.IsFalse(FacialPreviewDriver.EvaluateOnce(_rig.runner, st, Dt, null), "カメラが無いときは何もしない");
            }
            finally { Object.DestroyImmediate(cam); }
        }

        [Test]
        public void TurntableAdvancesYaw()
        {
            FacialPreviewState st = FacialPreviewDriver.GetState(_rig.runner);
            st.mode = FacialPreviewViewMode.Turntable;
            st.turntableSpeed = 90f; st.yaw = 0f; st.pitch = 0f; st.distance = 2f;
            FacialPreviewDriver.SetOn(_rig.runner, true);
            for (int i = 0; i < 25; i++) FacialPreviewDriver.EvaluateOnce(_rig.runner, st, 0.02f, null); // 0.5 秒
            Assert.AreEqual(45f, st.yaw, 0.01f);
            Assert.AreEqual(45f, _rig.runner.CurrentYaw, 0.05f);
        }

        [Test]
        public void PreviewDoesNotRecordUndoAndLeavesNoViewer()
        {
            // シーンが dirty にならないことは、一時のシーンに普通のオブジェクトを置く FacialSaveGuardTests で確かめる
            // （ここの rig は隠しオブジェクトでシーンに無いので、dirty の検査には使えない）
            int undoBefore = Undo.GetCurrentGroup();

            FacialPreviewState st = ManualState(30f, 10f);
            FacialPreviewDriver.SetOn(_rig.runner, true);
            for (int i = 0; i < 10; i++) FacialPreviewDriver.EvaluateOnce(_rig.runner, st, Dt, null);
            FacialPreviewDriver.OnBeforeSave();
            FacialPreviewDriver.SetOn(_rig.runner, false);

            Assert.AreEqual(undoBefore, Undo.GetCurrentGroup(), "プレビューで Undo が積まれた");
            foreach (GameObject go in Resources.FindObjectsOfTypeAll<GameObject>())
                if (go.name == "FacialPreviewViewer") Assert.Fail("仮想の視点がオフのあとも残っている");
        }

        [Test]
        public void VirtualViewerIsNotInAnyScene()
        {
            FacialPreviewState st = ManualState(30f, 10f);
            FacialPreviewDriver.SetOn(_rig.runner, true);
            FacialPreviewDriver.EvaluateOnce(_rig.runner, st, Dt, null);
            bool found = false;
            foreach (GameObject go in Resources.FindObjectsOfTypeAll<GameObject>())
                if (go.name == "FacialPreviewViewer")
                {
                    found = true;
                    Assert.IsFalse(go.scene.IsValid(), "シーンに入っている（保存される）");
                    Assert.IsTrue((go.hideFlags & HideFlags.DontSave) != 0);
                }
            Assert.IsTrue(found);
        }
    }
}
