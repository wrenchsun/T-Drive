// docs/19 の Unity 側の指摘（U-1 / U-2 / U-4 / U-5 / U-7）を再現するテスト。直す前は失敗し、直すと通る。
// 合成キャラクター（FacialTestRig）は隠しオブジェクトなので、ユーザーのシーンは汚さない。
using System.Reflection;
using NUnit.Framework;
using UnityEngine;

namespace TDrive.Facial.Tests
{
    public class FacialReviewFixTests
    {
        const float Dt = 10f;
        FacialTestRig _rig;
        FacialPoseAsset _pose;
        Mesh _mesh2;
        GameObject _go2;

        [SetUp]
        public void SetUp()
        {
            _rig = new FacialTestRig();
            _rig.runner.useManualAngles = true;
        }

        [TearDown]
        public void TearDown()
        {
            if (_pose != null) Object.DestroyImmediate(_pose);
            if (_go2 != null) Object.DestroyImmediate(_go2);
            if (_mesh2 != null) Object.DestroyImmediate(_mesh2);
            _rig.Dispose();
        }

        static Mesh MeshWithShapes(params string[] names)
        {
            var m = new Mesh { name = "Mesh2" };
            m.vertices = new[] { Vector3.zero, Vector3.right, Vector3.up };
            m.triangles = new[] { 0, 1, 2 };
            foreach (string n in names)
                m.AddBlendShapeFrame(n, 100f, new[] { Vector3.up * 0.01f, Vector3.zero, Vector3.zero }, new Vector3[3], new Vector3[3]);
            return m;
        }

        // ---------------------------------------------------------------- U-1

        [Test]
        public void U1_MeshSwapDoesNotWriteZeroIntoUnrelatedShapesOfTheNewMesh()
        {
            _rig.runner.EvaluateNow(30f, 0f, Dt); // 古いメッシュの FC_ に書く
            var names = new string[12];
            for (int i = 0; i < names.Length; i++) names[i] = "bs.keep" + i;
            _mesh2 = MeshWithShapes(names);
            _rig.smr.sharedMesh = _mesh2; // 衣装・LOD の切り替え
            for (int i = 0; i < names.Length; i++) _rig.smr.SetBlendShapeWeight(i, 50f);

            _rig.runner.RebuildCaches(); // メッシュの差し替えを検知したときと同じ処理

            for (int i = 0; i < names.Length; i++)
                Assert.AreEqual(50f, _rig.smr.GetBlendShapeWeight(i), 1e-4f, "新しいメッシュの関係ないシェイプ " + names[i] + " が 0 にされた");
        }

        // ---------------------------------------------------------------- U-2

        [Test]
        public void U2_LayerCountChangeWithTheSameDataReferenceDoesNotThrow()
        {
            _rig.runner.EvaluateNow(0f, 0f, Dt);
            FacialLayerData[] old = _rig.data.layers;
            var grown = new FacialLayerData[old.Length + 1];
            old.CopyTo(grown, 0);
            grown[old.Length] = new FacialLayerData { name = "Sad", enabled = true, morphNames = new string[9] };
            _rig.data.layers = grown; // data の参照は同じまま、レイヤーの数だけ変わる
            Assert.DoesNotThrow(() => _rig.runner.EvaluateNow(0f, 0f, Dt));
            Assert.DoesNotThrow(() => _rig.runner.EvaluateNow(0f, 0f, Dt));
        }

        // ---------------------------------------------------------------- U-4

        [Test]
        public void U4_SkipWhenNotVisibleStillRestoresTheCutPose()
        {
            _pose = ScriptableObject.CreateInstance<FacialPoseAsset>();
            _pose.curves = new FacialPoseCurve[0];
            _pose.bones = new[] { new FacialPoseBone { name = "head", position = new Vector3(0f, 1f, 0f), rotation = Quaternion.identity, scale = Vector3.one } };
            Vector3 p0 = _rig.head.transform.localPosition;
            _rig.runner.PushOverride(new FacialFrameOverride { pose = _pose, poseWeight = 1f });
            _rig.runner.EvaluateNow((Transform)null, Dt);
            Assert.AreNotEqual(p0, _rig.head.transform.localPosition, "前提: カット補正でボーンが動く");

            _rig.runner.skipWhenNotVisible = true; // 画面外（テストでは描画されないので「映っていない」）
            MethodInfo late = typeof(FacialCorrectionRunner).GetMethod("LateUpdate", BindingFlags.NonPublic | BindingFlags.Instance);
            late.Invoke(_rig.runner, null);
            Assert.AreEqual(p0, _rig.head.transform.localPosition, "画面外で止めるときも、加算したボーンは戻す");
        }

        // ---------------------------------------------------------------- U-5

        [Test]
        public void U5_PropertyBlockIsRemovedWhenOnlyOurValuesRemain()
        {
            _rig.runner.materialOutput = FacialMaterialOutputMode.PropertyBlock;
            _rig.runner.EvaluateNow(10f, 0f, Dt);
            Assert.IsTrue(_rig.smr.HasPropertyBlock(), "前提: 出力中はブロックがある");
            _rig.runner.materialOutput = FacialMaterialOutputMode.Off;
            _rig.runner.EvaluateNow(10f, 0f, Dt);
            Assert.IsFalse(_rig.smr.HasPropertyBlock(), "自分の値だけなら、ブロックを外して SRP Batcher に戻す");
        }

        [Test]
        public void U5_OtherPeoplesValuesInTheBlockAreKept()
        {
            var other = new MaterialPropertyBlock();
            other.SetFloat("_Other", 3f);
            _rig.smr.SetPropertyBlock(other);
            _rig.runner.materialOutput = FacialMaterialOutputMode.PropertyBlock;
            _rig.runner.EvaluateNow(10f, 0f, Dt);
            _rig.runner.materialOutput = FacialMaterialOutputMode.Off;
            _rig.runner.EvaluateNow(10f, 0f, Dt);
            Assert.IsTrue(_rig.smr.HasPropertyBlock(), "他が入れた値があるときはブロックを残す");
            var back = new MaterialPropertyBlock();
            _rig.smr.GetPropertyBlock(back);
            Assert.AreEqual(3f, back.GetFloat("_Other"), 1e-5f);
        }

        // ---------------------------------------------------------------- U-7

        [Test]
        public void U7_ReplacingATargetsElementInPlaceIsNoticed()
        {
            _go2 = UnityEditor.EditorUtility.CreateGameObjectWithHideFlags("Face2", HideFlags.HideAndDontSave);
            _go2.transform.SetParent(_rig.root.transform, false);
            SkinnedMeshRenderer smr2 = _go2.AddComponent<SkinnedMeshRenderer>();
            smr2.sharedMesh = _rig.mesh;
            _rig.runner.targets = new[] { _rig.smr };
            _rig.runner.EvaluateNow(0f, 0f, Dt);
            Assert.Greater(_rig.FcSum(), 0f, "前提: 最初の対象に書く");

            _rig.runner.targets[0] = smr2; // 配列の参照も長さも同じまま、中身だけ差し替え
            _rig.runner.EvaluateNow(0f, 0f, Dt);
            float sum2 = 0f;
            for (int i = 0; i < _rig.mesh.blendShapeCount; i++) sum2 += Mathf.Abs(smr2.GetBlendShapeWeight(i));
            Assert.Greater(sum2, 0f, "差し替えた対象に書く");
            Assert.AreEqual(0f, _rig.FcSum(), 1e-4f, "古い対象は 0 に戻る");
        }

        // ---------------------------------------------------------------- U-6

        [Test]
        public void U6_AmbiguousSuffixMatchesAreReported()
        {
            using (var rig = new FacialTestRig(false, "bs."))
            {
                Assert.AreEqual(0, rig.runner.GetAmbiguousShapeNames().Count, "前提: 末尾が重ならなければあいまいではない");
                rig.AddShape("alt.FC_test_Neutral_R1_C1"); // 末尾が同じシェイプがもう 1 つ
                rig.runner.RebuildCaches();
                CollectionAssert.Contains(rig.runner.GetAmbiguousShapeNames(), "FC_test_Neutral_R1_C1");
            }
        }

        [Test]
        public void U6_ExactMatchIsNeverAmbiguous()
        {
            _rig.AddShape("alt.FC_test_Neutral_R1_C1"); // 完全一致する FC_test_Neutral_R1_C1 もある
            _rig.runner.RebuildCaches();
            Assert.AreEqual(0, _rig.runner.GetAmbiguousShapeNames().Count);
        }

        // ---------------------------------------------------------------- U-8

        static MethodInfo Static(System.Type t, string name)
        {
            return t.GetMethod(name, BindingFlags.NonPublic | BindingFlags.Static);
        }

        [Test]
        public void U8_StaticsAreResetOnSubsystemRegistration()
        {
            // Domain Reload を切った設定では、前の再生の static が残る。SubsystemRegistration の初期化が戻すこと
            typeof(FacialCorrectionRunner).GetMethod("OnEnable", BindingFlags.NonPublic | BindingFlags.Instance).Invoke(_rig.runner, null);
            Assert.Greater(FacialCorrectionRunner.ActiveRunners.Count, 0, "前提");
            int savedLevel = FacialDebugOverlay.GlobalLevel;
            try
            {
                FacialDebugOverlay.GlobalLevel = 2;
                foreach (System.Type t in new[] { typeof(FacialCorrectionRunner), typeof(FacialDebugOverlay), typeof(FacialMaterialOutput) })
                {
                    MethodInfo m = Static(t, "ResetStatics");
                    Assert.IsNotNull(m, t.Name + " に ResetStatics が無い");
                    var attr = m.GetCustomAttribute<RuntimeInitializeOnLoadMethodAttribute>();
                    Assert.IsNotNull(attr, t.Name + ".ResetStatics に RuntimeInitializeOnLoadMethod が無い");
                    Assert.AreEqual(RuntimeInitializeLoadType.SubsystemRegistration, attr.loadType);
                    m.Invoke(null, null);
                }
                Assert.AreEqual(0, FacialCorrectionRunner.ActiveRunners.Count);
                Assert.AreEqual(0, FacialDebugOverlay.GlobalLevel);
            }
            finally { FacialDebugOverlay.GlobalLevel = savedLevel; }
        }

        // ---------------------------------------------------------------- シリアライズの形（エディタとビルドで同じ）

        [Test]
        public void RuntimeDataHasNoEditorOnlySerializedField()
        {
            // 以前は #if UNITY_EDITOR の sourceJson があり、エディタとビルドでシリアライズの形が違った。元の JSON は FacialSourceJson（エディタ）が持つ
            Assert.IsNull(typeof(FacialCorrectionData).GetField("sourceJson", BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance));
            foreach (FieldInfo f in typeof(FacialCorrectionData).GetFields(BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance))
                Assert.IsNull(f.GetCustomAttribute<System.ObsoleteAttribute>(), f.Name);
        }

        [Test]
        public void SourceJsonStoreRemembersAndFallsBackToNothingForUnsavedData()
        {
            var d = ScriptableObject.CreateInstance<FacialCorrectionData>();
            try
            {
                Assert.IsNull(TDrive.Facial.Editor.FacialSourceJson.Get(d));
                TDrive.Facial.Editor.FacialSourceJson.Set(d, "{\"x\":1}");
                Assert.AreEqual("{\"x\":1}", TDrive.Facial.Editor.FacialSourceJson.Get(d));
            }
            finally { Object.DestroyImmediate(d); }
        }

        // ---------------------------------------------------------------- 左右反転（スケール -1）の親の下（docs/19 §5）

        [Test]
        public void MirroredParentKeepsTheYawOfAViewerInFront()
        {
            // 親が X 軸反転（スケール (-1,1,1)）。顔の向き（ローカル +Z）は反転しても世界の +Z のまま = 正面のカメラは Yaw 0
            _rig.root.transform.localScale = new Vector3(-1f, 1f, 1f);
            _rig.runner.useManualAngles = false;
            var viewer = UnityEditor.EditorUtility.CreateGameObjectWithHideFlags("MirrorViewer", HideFlags.HideAndDontSave);
            try
            {
                viewer.transform.position = new Vector3(0f, 1.5f, 3f);
                _rig.runner.EvaluateNow(viewer.transform, Dt);
                Assert.AreEqual(0f, _rig.runner.CurrentYaw, 1e-2f, "正面のカメラの Yaw が 0 でない（反転した親の rotation で向きを求めている）");
                viewer.transform.position = new Vector3(-2f, 1.5f, 0f); // キャラクターの左（世界の -X）
                _rig.runner.EvaluateNow(viewer.transform, Dt);
                Assert.AreEqual(90f, _rig.runner.CurrentYaw, 1e-2f, "左のカメラは Yaw +90");
            }
            finally { Object.DestroyImmediate(viewer); }
        }

        [Test]
        public void MirroredParentUsesTheWorldDirectionOfTheForwardAxis()
        {
            // 顔の向きがローカル +X のボーン。親を X 反転すると、世界ではその向きは -X になる（TransformDirection）。回転だけで求めると +X になって後ろ向きと判定される
            _rig.root.transform.localScale = new Vector3(-1f, 1f, 1f);
            _rig.data.grid.forwardAxis = "+X";
            _rig.runner.RebuildCaches();
            _rig.runner.useManualAngles = false;
            var viewer = UnityEditor.EditorUtility.CreateGameObjectWithHideFlags("MirrorViewer2", HideFlags.HideAndDontSave);
            try
            {
                viewer.transform.position = new Vector3(-3f, 1.5f, 0f); // 世界の -X = 反転後の顔の正面
                _rig.runner.EvaluateNow(viewer.transform, Dt);
                Assert.AreEqual(0f, _rig.runner.CurrentYaw, 1e-2f, "反転した親の下で、正面のカメラが Yaw 0 にならない");
            }
            finally { Object.DestroyImmediate(viewer); }
        }
    }
}
