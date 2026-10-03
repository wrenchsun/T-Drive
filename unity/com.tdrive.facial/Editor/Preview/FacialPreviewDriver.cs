// エディタのプレビュー（再生しなくても Scene ビューのカメラで補正を確認する）。
// 保存データを汚さないための約束:
//  - Runner を [ExecuteAlways] にしない。ここが EvaluateNow を呼ぶ。Undo・SetDirty は使わない（シーンは dirty にならない）
//  - 次のときは必ず ResetWeights（プレビューが書いた FC_* を 0 に戻す）: オフにしたとき / 再生に入る前 / アセンブリのリロード前
//    / エディタの終了時 / シーン・Prefab・アセットの保存の直前（保存後は次の更新で自動的に再開）
//  - 仮想の視点用オブジェクトは EditorUtility.CreateGameObjectWithHideFlags（HideAndDontSave）で作る = シーンに入らず保存されない
using System.Collections.Generic;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace TDrive.Facial.Editor
{
    public enum FacialPreviewViewMode
    {
        [InspectorName("Scene ビューのカメラ")] SceneCamera,
        [InspectorName("ターンテーブル")] Turntable,
        [InspectorName("角度を指定")] Manual,
    }

    /// <summary>Runner ごとのプレビューの状態（メモリ上だけ。シーンには保存されない）。</summary>
    public sealed class FacialPreviewState
    {
        public bool on;
        /// <summary>false = 補正なし（A/B の B）。</summary>
        public bool correction = true;
        public FacialPreviewViewMode mode = FacialPreviewViewMode.SceneCamera;
        /// <summary>ターンテーブルの速さ（度/秒）。</summary>
        public float turntableSpeed = 30f;
        /// <summary>角度を指定 / ターンテーブルの Yaw・Pitch（度）。ターンテーブルでは Yaw が進む。</summary>
        public float yaw, pitch;
        /// <summary>仮想の視点の距離（m）。</summary>
        public float distance = 1.5f;

        internal bool applied;
        /// <summary>true の間、次の更新で必ず評価する（設定を変えたときに立てる）。</summary>
        public bool forceEval = true;
        internal double settleUntil;
        internal double lastEval;
        internal Vector3 lastCamPos;
        internal Quaternion lastCamRot = Quaternion.identity;
        internal float lastYaw, lastPitch, lastScale;
        internal int lastCount;
    }

    [InitializeOnLoad]
    public static class FacialPreviewDriver
    {
        const double TickInterval = 1.0 / 30.0;
        const double SettleSeconds = 1.0;  // 変化のあと、スムージングが落ち着くまで評価し続ける
        const double IdleInterval = 0.2;   // 変化が無くても、ボーンの移動や値の変更を拾うために評価する間隔

        static readonly Dictionary<FacialCorrectionRunner, FacialPreviewState> States = new Dictionary<FacialCorrectionRunner, FacialPreviewState>();
        // オフの間の設定の置き場（オン → オフ → オンで設定を覚える）
        static readonly Dictionary<FacialCorrectionRunner, FacialPreviewState> OffStates = new Dictionary<FacialCorrectionRunner, FacialPreviewState>();
        static readonly List<FacialCorrectionRunner> Scratch = new List<FacialCorrectionRunner>();
        static GameObject _viewer;
        static bool _hooked;
        static double _nextTick, _lastTime;

        static FacialPreviewDriver()
        {
            // 保存されるものを守る側のフックは常時つなぐ
            AssemblyReloadEvents.beforeAssemblyReload += ResetAllWeights;
            EditorApplication.quitting += ResetAllWeights;
            EditorApplication.playModeStateChanged += OnPlayModeChanged;
            EditorSceneManager.sceneSaving += (scene, path) => OnBeforeSave();
            UnityEditor.SceneManagement.PrefabStage.prefabSaving += root => OnBeforeSave();
            EditorApplication.hierarchyChanged += ForceEvalAll;
        }

        // ---------------------------------------------------------------- 公開

        public static bool IsOn(FacialCorrectionRunner runner)
        {
            FacialPreviewState s;
            return runner != null && States.TryGetValue(runner, out s) && s.on;
        }

        /// <summary>プレビューが 1 つでもオンか。</summary>
        public static bool AnyOn { get { return States.Count > 0; } }

        /// <summary>この Runner のプレビューの状態（無ければ既定値で作る。オンにはしない）。</summary>
        public static FacialPreviewState GetState(FacialCorrectionRunner runner)
        {
            FacialPreviewState s;
            if (States.TryGetValue(runner, out s)) return s;
            if (!OffStates.TryGetValue(runner, out s)) { s = new FacialPreviewState(); OffStates[runner] = s; }
            return s;
        }

        public static void SetOn(FacialCorrectionRunner runner, bool on)
        {
            if (ReferenceEquals(runner, null)) return;
            if (runner == null) // 消された Runner: 登録だけ外す
            {
                States.Remove(runner);
                OffStates.Remove(runner);
                if (States.Count == 0) Unhook();
                return;
            }
            if (on)
            {
                if (Application.isPlaying) return; // 再生中は Runner 自身が動く
                FacialPreviewState s = GetState(runner);
                OffStates.Remove(runner);
                s.on = true;
                s.forceEval = true;
                s.applied = false;
                States[runner] = s;
                Hook();
            }
            else
            {
                FacialPreviewState s;
                if (States.TryGetValue(runner, out s))
                {
                    s.on = false;
                    States.Remove(runner);
                    OffStates[runner] = s;
                }
                runner.ResetWeights();
                if (States.Count == 0) Unhook();
                SceneView.RepaintAll();
            }
        }

        /// <summary>すべてのプレビューをやめて、書いた FC_* を 0 に戻す。</summary>
        public static void StopAll()
        {
            Scratch.Clear();
            foreach (var kv in States) Scratch.Add(kv.Key);
            for (int i = 0; i < Scratch.Count; i++) SetOn(Scratch[i], false);
            Scratch.Clear();
            Unhook();
        }

        /// <summary>プレビューが書いた FC_* を、プレビューは続けたまま 0 に戻す（リロード・終了・保存の前に使う）。</summary>
        public static void ResetAllWeights()
        {
            foreach (var kv in States)
            {
                if (kv.Key != null) kv.Key.ResetWeights();
                kv.Value.applied = false;
                kv.Value.forceEval = true;
            }
        }

        /// <summary>保存の直前: 書いた分を戻す。保存のあとは次の更新で自動的に再開する。</summary>
        public static void OnBeforeSave() { ResetAllWeights(); }

        static void ForceEvalAll()
        {
            foreach (var kv in States) kv.Value.forceEval = true;
        }

        static void OnPlayModeChanged(PlayModeStateChange change)
        {
            // 再生に入る前に必ず戻す（編集中の値が再生用のコピーへ入らないように）。入ったあと・戻ったあとはオフのまま
            if (change == PlayModeStateChange.ExitingEditMode) StopAll();
        }

        static void Hook()
        {
            if (_hooked) return;
            _hooked = true;
            _lastTime = EditorApplication.timeSinceStartup;
            EditorApplication.update += Tick;
        }

        static void Unhook()
        {
            if (!_hooked) return;
            _hooked = false;
            EditorApplication.update -= Tick;
            if (_viewer != null) Object.DestroyImmediate(_viewer);
            _viewer = null;
        }

        // ---------------------------------------------------------------- 更新

        static void Tick()
        {
            double now = EditorApplication.timeSinceStartup;
            if (now < _nextTick) return;
            _nextTick = now + TickInterval;
            float dt = (float)System.Math.Max(0.0, System.Math.Min(0.1, now - _lastTime));
            _lastTime = now;
            if (Application.isPlaying) return;

            Scratch.Clear();
            foreach (var kv in States) Scratch.Add(kv.Key);
            Transform cam = null;
            SceneView sv = SceneView.lastActiveSceneView;
            if (sv != null && sv.camera != null) cam = sv.camera.transform;
            bool repaint = false;
            for (int i = 0; i < Scratch.Count; i++)
            {
                FacialCorrectionRunner r = Scratch[i];
                if (r == null) { States.Remove(r); continue; } // 消された Runner
                if (EvaluateOnce(r, States[r], dt, cam)) repaint = true;
            }
            Scratch.Clear();
            if (States.Count == 0) Unhook();
            if (repaint) SceneView.RepaintAll();
        }

        /// <summary>
        /// 1 回の更新。評価したら true（Scene ビューを再描画する）。sceneCamera は Scene ビューのカメラ（テストでは任意の Transform）。
        /// 変化が無く落ち着いているときは評価を飛ばす。
        /// </summary>
        public static bool EvaluateOnce(FacialCorrectionRunner runner, FacialPreviewState st, float dt, Transform sceneCamera)
        {
            if (runner == null || runner.data == null) return false;
            double now = EditorApplication.timeSinceStartup;

            // A/B: 補正なし = 書いた分を戻して何もしない
            if (!st.correction)
            {
                if (!st.applied && !st.forceEval) return false;
                runner.ResetWeights();
                st.applied = false;
                st.forceEval = false;
                return true;
            }

            Transform viewer;
            if (st.mode == FacialPreviewViewMode.SceneCamera)
            {
                if (sceneCamera == null) return false;
                bool moved = (sceneCamera.position - st.lastCamPos).sqrMagnitude > 1e-10f
                    || Quaternion.Angle(sceneCamera.rotation, st.lastCamRot) > 1e-3f;
                if (moved) st.settleUntil = now + SettleSeconds;
                st.lastCamPos = sceneCamera.position;
                st.lastCamRot = sceneCamera.rotation;
                if (!st.forceEval && !moved && now > st.settleUntil && now - st.lastEval < IdleInterval) return false;
                viewer = sceneCamera;
            }
            else
            {
                Transform bone = runner.ResolvedBaseBone;
                if (bone == null) return false; // 基準ボーンが無い（検証に出る）
                if (st.mode == FacialPreviewViewMode.Turntable) st.yaw = (float)FacialGridMath.WrapYaw(st.yaw + st.turntableSpeed * dt);
                FacialGridData g = runner.data.grid;
                Vector3 pos; Quaternion rot;
                FacialGridMath.CameraPose(bone.position, bone.rotation, g.forwardAxis, g.centerOffset, st.yaw, st.pitch,
                    Mathf.Max(0.05f, st.distance), out pos, out rot);
                viewer = VirtualViewer();
                viewer.SetPositionAndRotation(pos, rot);
                st.settleUntil = now + SettleSeconds;
            }

            var ov = new FacialFrameOverride { viewer = viewer };
            runner.PushOverride(ov);
            runner.EvaluateNow(viewer, dt);
            st.lastEval = now;
            st.forceEval = false;
            st.applied = true;

            bool changed = st.lastYaw != runner.CurrentYaw || st.lastPitch != runner.CurrentPitch
                || st.lastScale != runner.LastScale || st.lastCount != runner.ActiveWeightCount;
            st.lastYaw = runner.CurrentYaw; st.lastPitch = runner.CurrentPitch;
            st.lastScale = runner.LastScale; st.lastCount = runner.ActiveWeightCount;
            return changed || st.mode != FacialPreviewViewMode.SceneCamera;
        }

        static Transform VirtualViewer()
        {
            if (_viewer == null)
                _viewer = EditorUtility.CreateGameObjectWithHideFlags("FacialPreviewViewer", HideFlags.HideAndDontSave);
            return _viewer.transform;
        }
    }

    /// <summary>保存の直前（シーン・Prefab・アセットのどれでも）にプレビューが書いた FC_* を戻す。</summary>
    public sealed class FacialPreviewSaveGuard : AssetModificationProcessor
    {
        static string[] OnWillSaveAssets(string[] paths)
        {
            FacialPreviewDriver.OnBeforeSave();
            return paths;
        }
    }
}
