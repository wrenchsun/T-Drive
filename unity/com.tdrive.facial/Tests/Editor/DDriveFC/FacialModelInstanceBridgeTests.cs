// FT-5: FacialModelInstanceBridge（D-Drive の FC-12 IModelInstanceListener）と、FC-2（ブレンドシェイプの重みの復元）・Runner.OnDisable との二重の戻し。
// プールの経路は D-Drive の本物の PoolService（Rent / Return）を使う。ModelsManager 本体と ModelInstancePoolable は internal・レジストリが要るので使わず、
// 通知（ModelsManager.CloseInstance が OnModelReturning を呼ぶ）と重みの復元（ModelInstancePoolable.OnReturn）は TestRestorePoolable と直接の呼び出しで写している。
// 順序は D-Drive のコードのとおり: 通知 → PoolService.ForceReturn（全 IPoolable.OnReturn = 重みの復元 → SetActive(false)）。
using System.Reflection;
using DDrive.Foundation.Pool;
using DDrive.Runtime.Model;
using NUnit.Framework;
using TDrive.Facial.DDrive;
using UnityEditor;
using UnityEngine;

namespace TDrive.Facial.Tests.DDrive
{
    public class FacialModelInstanceBridgeTests
    {
        FacialTestRig _rig;
        GameObject _holder;
        PoolService _pool;
        FacialPoseAsset _pose;

        [SetUp]
        public void SetUp()
        {
            _rig = new FacialTestRig(true);
            _rig.runner.useManualAngles = true;
            _rig.runner.ZeroAllBoundOverride = false; // 実行時（プール）と同じ: 書いた分だけ戻す。編集時は結んだ FC_ をすべて 0 にする（E-4）ので、そのままでは D-Drive の復元の値を壊す
            _holder = EditorUtility.CreateGameObjectWithHideFlags("PoolHolder", HideFlags.HideAndDontSave);
            _pool = new PoolService();
            _pool.SetInstanceParent(_holder.transform);
        }

        [TearDown]
        public void TearDown()
        {
            if (_pose != null) Object.DestroyImmediate(_pose);
            if (_holder != null) Object.DestroyImmediate(_holder); // プールが作ったインスタンスは子
            _rig.Dispose();
        }

        static ModelInstanceContext Ctx(GameObject root) { return new ModelInstanceContext(default, null, root); }

        // 編集時は OnDisable が呼ばれないことがある。無効化と同じ後片付けを実際のメソッドで呼ぶ（2 回呼ばれても無害であることの確認を兼ねる）
        static void CallOnDisable(FacialCorrectionRunner r)
        {
            MethodInfo m = typeof(FacialCorrectionRunner).GetMethod("OnDisable", BindingFlags.Instance | BindingFlags.NonPublic);
            Assert.IsNotNull(m);
            m.Invoke(r, null);
        }

        sealed class Instance
        {
            public PooledObject pooled;
            public GameObject go;
            public FacialCorrectionRunner runner;
            public SkinnedMeshRenderer smr;
            public FacialModelInstanceBridge bridge;
            public TestRestorePoolable restore;
            public float W(string name) { return smr.GetBlendShapeWeight(new FacialShapeIndex(smr.sharedMesh).Find(name)); }
        }

        // 付ける: Runner + ブリッジ（任意）+ 復元（D-Drive が自動で付ける ModelInstancePoolable の代わり）
        Instance Spawn(bool withBridge, bool firstTime)
        {
            try { return SpawnCore(withBridge, firstTime); }
            catch (System.NullReferenceException e) { Assert.Fail("SPAWN NRE " + e.StackTrace); return null; }
        }

        Instance SpawnCore(bool withBridge, bool firstTime)
        {
            if (firstTime && withBridge) _rig.root.AddComponent<FacialModelInstanceBridge>();
            PooledObject p = _pool.Rent(_rig.root);
            Assert.IsNotNull(p, "Rent");
            Assert.IsNotNull(p.GameObject, "Rent の GameObject");
            var inst = new Instance { pooled = p, go = p.GameObject };
            inst.runner = inst.go.GetComponent<FacialCorrectionRunner>();
            if (inst.runner != null) inst.runner.ZeroAllBoundOverride = false; // プールの複製には引き継がれない（シリアライズされない）
            inst.smr = inst.go.GetComponentInChildren<SkinnedMeshRenderer>();
            inst.bridge = inst.go.GetComponent<FacialModelInstanceBridge>();
            inst.restore = inst.go.GetComponent<TestRestorePoolable>();
            if (inst.restore == null) inst.restore = inst.go.AddComponent<TestRestorePoolable>();
            Assert.IsNotNull(inst.restore, "restore"); if (withBridge) Assert.IsNotNull(inst.bridge, "bridge"); Assert.IsNotNull(inst.runner, "runner"); Assert.IsNotNull(inst.smr, "smr"); Assert.IsNotNull(inst.smr.sharedMesh, "mesh");
            return inst;
        }

        // D-Drive の Despawn の順序: 通知（CloseInstance）→ PoolService.Return（OnReturn で復元 → SetActive(false)）→ 無効化の OnDisable
        void Despawn(Instance i)
        {
            if (i.bridge != null) i.bridge.OnModelReturning(Ctx(i.go));
            _pool.Return(i.pooled);
            Assert.IsFalse(i.go.activeSelf, "プールへ戻すと非アクティブ");
            CallOnDisable(i.runner);
        }

        void ArrangeCaptured(Instance i)
        {
            i.smr.SetBlendShapeWeight(new FacialShapeIndex(i.smr.sharedMesh).Find(FacialTestRig.N("Joy", 1, 1)), 7f); // 控えた値が 0 でない FC_
            i.smr.SetBlendShapeWeight(new FacialShapeIndex(i.smr.sharedMesh).Find("bs.other"), 30f);                  // アニメーション側のシェイプ
            i.runner.SetEmotionWeight(1, 1f);
            i.restore.Capture();
        }

        // ---------------------------------------------------------------- リスナーとして見つかる

        [Test]
        public void BridgeIsDiscoveredAsAModelInstanceListenerAndPullsInTheRunner()
        {
            GameObject go = EditorUtility.CreateGameObjectWithHideFlags("NoRunnerYet", HideFlags.HideAndDontSave);
            try
            {
                go.AddComponent<FacialModelInstanceBridge>();
                Assert.IsNotNull(go.GetComponent<FacialCorrectionRunner>(), "RequireComponent で Runner が付く");
                // D-Drive は GetComponentsInChildren<IModelInstanceListener>(true) で集める（ModelInstancePoolable.Capture）
                Assert.AreEqual(1, go.GetComponentsInChildren<IModelInstanceListener>(true).Length);
            }
            finally { Object.DestroyImmediate(go); }
        }

        // ---------------------------------------------------------------- 返却

        [Test]
        public void ReturningResetsTheFcShapesAndTheBookkeepingBeforeD_DriveRestores()
        {
            Instance i = Spawn(true, true);
            ArrangeCaptured(i);
            i.runner.EvaluateNow(0f, 0f, 1f);
            Assert.AreEqual(100f, i.W(FacialTestRig.N("Joy", 1, 1)), 0.5f, "前提: Runner が書いている");

            i.bridge.OnModelReturning(Ctx(i.go));
            Assert.AreEqual(0f, i.W(FacialTestRig.N("Joy", 1, 1)), 1e-4f, "通知の時点で FC_ は 0 に戻っている");
            Assert.AreEqual(0, i.runner.ActiveWeightCount, "記録が空");
            Assert.AreEqual(30f, i.W("bs.other"), 1e-4f, "アニメーションのシェイプには触れない");
        }

        [Test]
        public void ReturningResetsTheCutPoseOffsets()
        {
            _pose = ScriptableObject.CreateInstance<FacialPoseAsset>();
            _pose.curves = new[] { new FacialPoseCurve { name = "bs.other", value = 0.5f } };
            Instance i = Spawn(true, true);
            i.restore.Capture();
            var ov = new FacialFrameOverride { pose = _pose, poseWeight = 1f };
            i.runner.PushOverride(ov);
            i.runner.EvaluateNow(0f, 0f, 1f);
            Assert.AreNotEqual(0f, i.W("bs.other"), "前提: カット補正が加算されている");

            i.bridge.OnModelReturning(Ctx(i.go));
            Assert.AreEqual(0f, i.W("bs.other"), 1e-4f);
        }

        // ---------------------------------------------------------------- 本物のプールで往復

        [Test]
        public void PoolRoundTripWithTheBridgeRestoresEveryShapeToItsCapturedValue()
        {
            Instance i = Spawn(true, true);
            ArrangeCaptured(i);
            i.bridge.OnModelSpawned(Ctx(i.go));
            i.runner.EvaluateNow(0f, 0f, 1f);
            Assert.AreEqual(100f, i.W(FacialTestRig.N("Neutral", 1, 1)), 0.5f);

            Despawn(i);

            for (int j = 0; j < i.smr.sharedMesh.blendShapeCount; j++)
                Assert.AreEqual(i.restore.CapturedWeight(0, j), i.smr.GetBlendShapeWeight(j), 1e-4f, i.smr.sharedMesh.GetBlendShapeName(j));
            Assert.AreEqual(7f, i.W(FacialTestRig.N("Joy", 1, 1)), 1e-4f, "控えた値（0 ではない）が戻っている");
        }

        [Test]
        public void ReSpawnFromThePoolAppliesTheCorrectionFromTheFirstFrame()
        {
            Instance first = Spawn(true, true);
            ArrangeCaptured(first);
            first.bridge.OnModelSpawned(Ctx(first.go));
            first.runner.EvaluateNow(0f, 0f, 1f);
            Despawn(first);

            Instance second = Spawn(true, false); // 同じ GameObject が出てくる
            Assert.AreSame(first.go, second.go);
            Assert.IsTrue(second.go.activeSelf);
            second.bridge.OnModelSpawned(Ctx(second.go));
            Assert.IsFalse(second.runner.HasValidAngles, "計算の状態は捨てられている");
            second.runner.EvaluateNow(0f, 0f, 0.001f); // dt が小さくても最初はスナップ
            Assert.AreEqual(100f, second.W(FacialTestRig.N("Neutral", 1, 1)), 0.5f);
            Assert.AreEqual(100f, second.W(FacialTestRig.N("Joy", 1, 1)), 0.5f);
            Assert.IsTrue(second.runner.Snapped);
            Assert.AreEqual(30f, second.W("bs.other"), 1e-4f);

            // もう 1 往復
            Despawn(second);
            Instance third = Spawn(true, false);
            third.bridge.OnModelSpawned(Ctx(third.go));
            third.runner.EvaluateNow(0f, 0f, 0.001f);
            Assert.AreEqual(100f, third.W(FacialTestRig.N("Neutral", 1, 1)), 0.5f);
        }

        [Test]
        public void WithoutTheBridgeAFcShapeWithANonZeroCapturedValueEndsAtZero()
        {
            // ブリッジが無いときの既知の挙動: FC-2 の復元のあとに Runner.OnDisable が 0 を書く（控えた値が 0 の FC_ ならブリッジの有無で結果は同じ）
            Instance i = Spawn(false, true);
            ArrangeCaptured(i);
            i.runner.EvaluateNow(0f, 0f, 1f);
            Despawn(i);
            Assert.AreEqual(0f, i.W(FacialTestRig.N("Joy", 1, 1)), 1e-4f);
            Assert.AreEqual(0f, i.W(FacialTestRig.N("Neutral", 1, 1)), 1e-4f);
            Assert.AreEqual(30f, i.W("bs.other"), 1e-4f);
        }

        [Test]
        public void ReturningTwiceIsHarmless()
        {
            Instance i = Spawn(true, true);
            ArrangeCaptured(i);
            i.runner.EvaluateNow(0f, 0f, 1f);
            i.bridge.OnModelReturning(Ctx(i.go));
            i.bridge.OnModelReturning(Ctx(i.go));
            _pool.Return(i.pooled);
            CallOnDisable(i.runner);
            CallOnDisable(i.runner);
            Assert.AreEqual(7f, i.W(FacialTestRig.N("Joy", 1, 1)), 1e-4f);
        }
    }
}
