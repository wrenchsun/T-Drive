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

        // ---- 非アクティブ / 無効の Runner（編集時は OnDisable が来ない）

        static readonly Vector3 Cam = new Vector3(-2f, 1.5f, 0f);

        [Test]
        public void DeactivatingTheCharacterZeroesWeightsAndStopsWriting()
        {
            FacialPreviewState st = ManualState(45f, 0f);
            FacialPreviewDriver.SetOn(_rig.runner, true);
            FacialPreviewDriver.EvaluateOnce(_rig.runner, st, Dt, null);
            Assert.Greater(_rig.FcSum(), 50f, "前提");

            _rig.root.SetActive(false);
            FacialPreviewDriver.EvaluateOnce(_rig.runner, st, Dt, null);
            Assert.AreEqual(0f, _rig.FcSum(), 1e-6f, "非アクティブで 0 に戻る");

            st.yaw = -60f; st.forceEval = true; // 視点を変えても書かない
            FacialPreviewDriver.EvaluateOnce(_rig.runner, st, Dt, null);
            Assert.AreEqual(0f, _rig.FcSum(), 1e-6f, "非アクティブの間は書かない");

            _rig.root.SetActive(true);
            st.yaw = 45f;
            FacialPreviewDriver.EvaluateOnce(_rig.runner, st, Dt, null);
            Assert.AreEqual(50f, _rig.W(FacialTestRig.N("Neutral", 1, 1)), 0.1f, "再びアクティブで再開");
        }

        [Test]
        public void DisablingTheRunnerComponentZeroesWeightsAndResumesOnEnable()
        {
            FacialPreviewState st = ManualState(45f, 0f);
            FacialPreviewDriver.SetOn(_rig.runner, true);
            FacialPreviewDriver.EvaluateOnce(_rig.runner, st, Dt, null);
            Assert.Greater(_rig.FcSum(), 50f, "前提");

            _rig.runner.enabled = false;
            FacialPreviewDriver.EvaluateOnce(_rig.runner, st, Dt, null);
            Assert.AreEqual(0f, _rig.FcSum(), 1e-6f);
            st.yaw = 10f; st.forceEval = true;
            FacialPreviewDriver.EvaluateOnce(_rig.runner, st, Dt, null);
            Assert.AreEqual(0f, _rig.FcSum(), 1e-6f, "無効の間は書かない");

            _rig.runner.enabled = true;
            FacialPreviewDriver.EvaluateOnce(_rig.runner, st, Dt, null);
            Assert.Greater(_rig.FcSum(), 50f, "有効に戻ると再開");
        }

        [Test]
        public void DeactivatingRestoresLipSyncShapeToBaseline()
        {
            _rig.AddShape("bs.lip_a");
            _rig.data.lipSync = new FacialLipSyncData
            {
                enabled = true, strength = 1f, follow = 0f,
                phonemes = new[] { "A" },
                volume = new FacialLipSyncVolumeData { min = 0f, max = 1f, from = 1f, to = 1f },
                entries = new[]
                {
                    new FacialLipSyncEntryData
                    {
                        phoneme = "A", emotion = "",
                        shapes = new[] { new FacialLipSyncShapeData { name = "bs.lip_a", weight = 1f } },
                    },
                },
            };
            int ia = new FacialShapeIndex(_rig.mesh).Find("bs.lip_a");
            _rig.smr.SetBlendShapeWeight(ia, 30f); // 書く前の値（ベースライン）

            FacialPreviewState st = ManualState(0f, 0f);
            st.lipWeights = new[] { 1f }; st.lipFeed = true;
            FacialPreviewDriver.SetOn(_rig.runner, true);
            FacialPreviewDriver.EvaluateOnce(_rig.runner, st, Dt, null);
            Assert.AreEqual(100f, _rig.smr.GetBlendShapeWeight(ia), 1e-2f, "前提: リップシンクが書いた");

            _rig.root.SetActive(false);
            FacialPreviewDriver.EvaluateOnce(_rig.runner, st, Dt, null);
            Assert.AreEqual(30f, _rig.smr.GetBlendShapeWeight(ia), 1e-2f, "口は 0 でなく書く前の値へ");
            Assert.AreEqual(0f, _rig.FcSum(), 1e-6f);
        }

        [Test]
        public void SceneCameraModeAlsoStopsWhenInactive()
        {
            var cam = EditorUtility.CreateGameObjectWithHideFlags("TestCam", HideFlags.HideAndDontSave);
            try
            {
                cam.transform.position = Cam;
                FacialPreviewState st = FacialPreviewDriver.GetState(_rig.runner);
                st.mode = FacialPreviewViewMode.SceneCamera;
                FacialPreviewDriver.SetOn(_rig.runner, true);
                FacialPreviewDriver.EvaluateOnce(_rig.runner, st, Dt, cam.transform);
                Assert.Greater(_rig.FcSum(), 50f, "前提");
                _rig.root.SetActive(false);
                FacialPreviewDriver.EvaluateOnce(_rig.runner, st, Dt, cam.transform);
                cam.transform.position = new Vector3(2f, 1.5f, 0f);
                FacialPreviewDriver.EvaluateOnce(_rig.runner, st, Dt, cam.transform);
                Assert.AreEqual(0f, _rig.FcSum(), 1e-6f);
            }
            finally { Object.DestroyImmediate(cam); }
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
