// LOD のメッシュ（LODGroup に入った複数の Renderer）への書き込みと quality.maxLod のテスト（Unity EditMode）。
// Runner は対象のすべての Renderer の同じ名前のシェイプに同じ重みを書く（既存の複数対象の仕組み）。maxLod = 0 は制限なし、
// N >= 1 は LOD N まで書き、それより粗い LOD の Renderer は書かない（書かなくなる瞬間に 1 度だけ 0 へ戻す）。LODGroup の外の Renderer は常に書く。
// ゲームオブジェクトはプレビューシーンに作る（ユーザーの開いているシーンを汚さない）。
using System;
using System.Collections.Generic;
using NUnit.Framework;
using TDrive.Facial.Core;
using TDrive.Facial.Editor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using Object = UnityEngine.Object;

namespace TDrive.Facial.Tests
{
    public class FacialLodTests
    {
        const float Dt = 0.02f;
        const string Asset = "test";

        Scene _scene;
        GameObject _root, _head;
        Mesh _mesh0, _mesh1, _mesh2, _meshOut;
        SkinnedMeshRenderer _r0, _r1, _r2, _rOut; // LOD0 / LOD1 / LOD2（LODGroup）と、LODGroup の外
        FacialCorrectionData _data;
        FacialCorrectionOverrides _overrides;
        FacialCorrectionRunner _runner;

        static string N(string layer, int r, int c) { return FacialNaming.MorphName(Asset, layer, r, c); }

        // 頂点数の違うメッシュ（LOD ごとに粗い）。FC_ のシェイプは同じ名前で持つ
        static Mesh MakeMesh(string name, int vertexCount)
        {
            var m = new Mesh { name = name };
            var v = new Vector3[vertexCount];
            for (int i = 0; i < vertexCount; i++) v[i] = new Vector3(i, 0f, 0f);
            m.vertices = v;
            var tri = new int[vertexCount - vertexCount % 3];
            for (int i = 0; i < tri.Length; i++) tri[i] = i;
            m.triangles = tri;
            for (int r = 0; r < 3; r++)
                for (int c = 0; c < 3; c++)
                {
                    var d = new Vector3[vertexCount];
                    d[0] = Vector3.up * 0.01f;
                    m.AddBlendShapeFrame(N("Neutral", r, c), 100f, d, new Vector3[vertexCount], new Vector3[vertexCount]);
                }
            return m;
        }

        GameObject NewGo(string name, Transform parent)
        {
            var go = new GameObject(name);
            SceneManager.MoveGameObjectToScene(go, _scene);
            if (parent != null) go.transform.SetParent(parent, false);
            return go;
        }

        SkinnedMeshRenderer NewRenderer(string name, Mesh mesh)
        {
            var smr = NewGo(name, _root.transform).AddComponent<SkinnedMeshRenderer>();
            smr.sharedMesh = mesh;
            return smr;
        }

        [SetUp]
        public void SetUp()
        {
            _scene = EditorSceneManager.NewPreviewScene();
            _root = NewGo("LodRoot", null);
            _head = NewGo("head", _root.transform);
            _head.transform.position = new Vector3(0f, 1.5f, 0f);
            _mesh0 = MakeMesh("lod0", 12);
            _mesh1 = MakeMesh("lod1", 6);
            _mesh2 = MakeMesh("lod2", 3);
            _meshOut = MakeMesh("eyelash", 6);
            _r0 = NewRenderer("face_LOD0", _mesh0);
            _r1 = NewRenderer("face_LOD1", _mesh1);
            _r2 = NewRenderer("face_LOD2", _mesh2);
            _rOut = NewRenderer("eyelash", _meshOut); // LODGroup に入れない（まつ毛など）

            var lg = _root.AddComponent<LODGroup>();
            lg.SetLODs(new[]
            {
                new LOD(0.5f, new Renderer[] { _r0 }),
                new LOD(0.25f, new Renderer[] { _r1 }),
                new LOD(0.1f, new Renderer[] { _r2 }),
            });

            _data = ScriptableObject.CreateInstance<FacialCorrectionData>();
            _data.assetName = Asset;
            _data.grid = new FacialGridData { yawRange = 90f, pitchRange = 45f, cols = 3, rows = 3, edgeFade = 15f, baseBone = "head", forwardAxis = "+Z", centerOffset = Vector3.zero };
            _data.policy = new FacialPolicyData { expressionDampen = 0.5f, interpSpeed = 10f, snapAngle = 45f, fadeStart = 0f, fadeEnd = 0f, globalAlpha = 1f };
            _data.quality = new FacialQualityData { angleEpsilon = 0.1f, maxLod = 0, sharpness = 1f, stepFps = 0f };
            var neutral = new string[9];
            for (int r = 0; r < 3; r++) for (int c = 0; c < 3; c++) neutral[r * 3 + c] = N("Neutral", r, c);
            _data.layers = new[] { new FacialLayerData { name = "Neutral", emotionCurve = "", enabled = true, morphNames = neutral } };
            _data.intensityCurves = new string[0];

            _runner = _root.AddComponent<FacialCorrectionRunner>();
            _runner.data = _data;
            _runner.targets = new[] { _r0, _r1, _r2, _rOut };
        }

        [TearDown]
        public void TearDown()
        {
            if (_runner != null) _runner.ResetWeights();
            if (_root != null) Object.DestroyImmediate(_root);
            foreach (Mesh m in new[] { _mesh0, _mesh1, _mesh2, _meshOut }) if (m != null) Object.DestroyImmediate(m);
            if (_data != null) Object.DestroyImmediate(_data);
            if (_overrides != null) Object.DestroyImmediate(_overrides);
            if (_scene.IsValid()) EditorSceneManager.ClosePreviewScene(_scene);
        }

        static float W(SkinnedMeshRenderer r, string name) { return r.GetBlendShapeWeight(r.sharedMesh.GetBlendShapeIndex(name)); }

        FacialCorrectionOverrides MaxLodOverride(int lod)
        {
            if (_overrides == null) { _overrides = ScriptableObject.CreateInstance<FacialCorrectionOverrides>(); _runner.overrides = _overrides; }
            _overrides.overrideMaxLod = true;
            _overrides.maxLod = lod;
            return _overrides;
        }

        // 格子の端（Yaw 45°）= 列 2 の点に 50% の重みが乗る（既存のテストと同じ位置）
        void Eval() { _runner.EvaluateNow(45f, 0f, Dt); }

        string Key { get { return N("Neutral", 1, 2); } }

        // ---------------------------------------------------------------- 既定は全 LOD に同じ重み

        [Test]
        public void DefaultWritesTheSameWeightsToEveryLod()
        {
            Eval();
            Assert.AreEqual(50f, W(_r0, Key), 1e-3f);
            Assert.AreEqual(50f, W(_r1, Key), 1e-3f, "LOD1（頂点数が違うメッシュ）にも同じ重み");
            Assert.AreEqual(50f, W(_r2, Key), 1e-3f, "LOD2 にも同じ重み");
            Assert.AreEqual(50f, W(_rOut, Key), 1e-3f);
            Assert.AreEqual(0, _runner.EffectiveMaxLod, "maxLod = 0 は制限なし（今までの動き）");
        }

        [Test]
        public void RendererLodIsDetectedFromTheLodGroup()
        {
            Eval();
            Assert.AreEqual(0, _runner.GetRendererLod(_r0));
            Assert.AreEqual(1, _runner.GetRendererLod(_r1));
            Assert.AreEqual(2, _runner.GetRendererLod(_r2));
            Assert.AreEqual(0, _runner.GetRendererLod(_rOut), "LODGroup の外は 0（常に書く）");
        }

        // ---------------------------------------------------------------- maxLod

        [Test]
        public void MaxLodSkipsCoarserLodsAndResetsThemOnce()
        {
            Eval();
            Assert.AreEqual(50f, W(_r2, Key), 1e-3f);

            MaxLodOverride(1);
            Eval();
            Assert.AreEqual(1, _runner.EffectiveMaxLod);
            Assert.AreEqual(50f, W(_r0, Key), 1e-3f);
            Assert.AreEqual(50f, W(_r1, Key), 1e-3f, "maxLod = 1 は LOD1 まで書く");
            Assert.AreEqual(0f, W(_r2, Key), 1e-3f, "LOD2 は書かなくなる瞬間に 0 へ戻る");
            Assert.AreEqual(50f, W(_rOut, Key), 1e-3f, "LODGroup の外の Renderer は常に書く");

            // 書かない = 触らない（毎フレーム 0 を書き続けるのではなく、1 度だけ戻す）
            int i2 = _mesh2.GetBlendShapeIndex(Key);
            _r2.SetBlendShapeWeight(i2, 33f);
            Eval();
            Eval();
            Assert.AreEqual(33f, _r2.GetBlendShapeWeight(i2), 1e-3f, "上限を超える LOD には書かない");

            // 上限を外すとまた書く
            MaxLodOverride(0);
            Eval();
            Assert.AreEqual(50f, W(_r2, Key), 1e-3f);
        }

        [Test]
        public void MaxLodOneMeansLod0AndLod1AndZeroMeansNoLimit()
        {
            MaxLodOverride(1);
            Eval();
            Assert.AreEqual(0f, W(_r2, Key), 1e-3f);
            MaxLodOverride(2);
            Eval();
            Assert.AreEqual(50f, W(_r2, Key), 1e-3f, "maxLod = 2 は LOD2 まで書く");
            _overrides.overrideMaxLod = false; // データの値（0 = 制限なし）に戻る
            Eval();
            Assert.AreEqual(0, _runner.EffectiveMaxLod);
            Assert.AreEqual(50f, W(_r2, Key), 1e-3f);
        }

        [Test]
        public void MaxLodFromDataAppliesAndOverrideWins()
        {
            _data.quality.maxLod = 1;
            _runner.RebuildCaches();
            Eval();
            Assert.AreEqual(1, _runner.EffectiveMaxLod);
            Assert.AreEqual(0f, W(_r2, Key), 1e-3f);
            Assert.AreEqual(50f, W(_r1, Key), 1e-3f);
            MaxLodOverride(0); // 調整値が優先（制限なしに上書き）
            Eval();
            Assert.AreEqual(0, _runner.EffectiveMaxLod);
            Assert.AreEqual(50f, W(_r2, Key), 1e-3f);
        }

        [Test]
        public void NegativeMaxLodIsTreatedAsNoLimit()
        {
            _data.quality.maxLod = -3;
            _runner.RebuildCaches();
            Eval();
            Assert.AreEqual(0, _runner.EffectiveMaxLod);
            Assert.AreEqual(50f, W(_r2, Key), 1e-3f);
        }

        [Test]
        public void SkippedLodStaysZeroWhileOthersFollowTheAngle()
        {
            MaxLodOverride(0);
            Eval();
            MaxLodOverride(1);
            for (int i = 0; i < 5; i++) _runner.EvaluateNow(0f, 0f, Dt); // 正面へ戻る = 列 1
            _runner.EvaluateNow(90f, 0f, Dt);
            Assert.AreEqual(0f, W(_r2, N("Neutral", 1, 2)), 1e-3f);
            Assert.Greater(W(_r1, N("Neutral", 1, 2)), 0f, "書く LOD は追従している");
        }

        [Test]
        public void ResetWeightsZeroesSkippedLodsToo()
        {
            MaxLodOverride(0);
            Eval();
            MaxLodOverride(1);
            Eval();
            _runner.ResetWeights();
            foreach (SkinnedMeshRenderer r in new[] { _r0, _r1, _r2, _rOut })
                for (int i = 0; i < r.sharedMesh.blendShapeCount; i++)
                    Assert.AreEqual(0f, r.GetBlendShapeWeight(i), 1e-3f, r.name);
        }

        // ---------------------------------------------------------------- 調整値・Maya へ戻す JSON・割り当て

        [Test]
        public void OverrideResolvesAndNeverGoesNegative()
        {
            Assert.AreEqual(0, FacialCorrectionOverrides.Resolve(_data, null).maxLod);
            _data.quality.maxLod = 2;
            Assert.AreEqual(2, FacialCorrectionOverrides.Resolve(_data, null).maxLod);
            var o = ScriptableObject.CreateInstance<FacialCorrectionOverrides>();
            _overrides = o;
            o.maxLod = 5; // フラグが無いのでデータのまま
            Assert.AreEqual(2, FacialCorrectionOverrides.Resolve(_data, o).maxLod);
            o.overrideMaxLod = true;
            Assert.AreEqual(5, FacialCorrectionOverrides.Resolve(_data, o).maxLod);
            o.maxLod = -4;
            Assert.AreEqual(0, FacialCorrectionOverrides.Resolve(_data, o).maxLod);
        }

        [Test]
        public void MayaJsonCarriesTheEffectiveMaxLod()
        {
            _data.quality.maxLod = 1;
            var root = (Dictionary<string, object>)MiniJson.Parse(FacialMayaExport.BuildJson(_data, null));
            Assert.AreEqual(1.0, (double)((Dictionary<string, object>)root["quality"])["maxLod"], 1e-9);
            MaxLodOverride(3);
            root = (Dictionary<string, object>)MiniJson.Parse(FacialMayaExport.BuildJson(_data, _overrides));
            Assert.AreEqual(3.0, (double)((Dictionary<string, object>)root["quality"])["maxLod"], 1e-9);
        }

        [Test]
        public void EvaluatingWithAMaxLodAllocatesNothing()
        {
            MaxLodOverride(1);
            for (int i = 0; i < 40; i++) _runner.EvaluateNow(i * 2f, 5f, Dt);
            AllocProbe.AssertNoAlloc(() =>
            {
                for (int i = 0; i < AllocProbe.CallsPerAction; i++) _runner.EvaluateNow((i % 90) * 1f, (i % 30) * 1f, Dt);
            }, "maxLod 有効時の評価");
        }
    }
}
