// マテリアル出力（FU-4）のテスト。値・衝突しない・他の値を残す・戻る・割り当てなし。
using System;
using NUnit.Framework;
using UnityEngine;

namespace TDrive.Facial.Tests
{
    public class FacialMaterialOutputTests
    {
        const float Dt = 0.02f;
        FacialTestRig _a, _b;
        readonly MaterialPropertyBlock _mpb = new MaterialPropertyBlock();

        [SetUp]
        public void SetUp()
        {
            _a = new FacialTestRig();
            _a.data.materialMode = "propertyBlock";
        }

        [TearDown]
        public void TearDown()
        {
            if (_a != null) _a.Dispose();
            if (_b != null) _b.Dispose();
        }

        Vector4 Get(Renderer r, string prop)
        {
            r.GetPropertyBlock(_mpb);
            return _mpb.GetVector(prop);
        }

        [Test]
        public void AnglesStrengthAndEmotionAreWritten()
        {
            _a.runner.SetEmotionWeight(1, 0.4f);
            _a.runner.EvaluateNow(45f, -22.5f, Dt);
            Vector4 a = Get(_a.smr, "_FC_Angles");
            Assert.AreEqual(0.5f, a.x, 1e-4f);
            Assert.AreEqual(-0.5f, a.y, 1e-4f);
            Assert.AreEqual(_a.runner.LastScale, a.z, 1e-4f);
            Assert.AreEqual(1f, a.z, 1e-4f);
            Assert.AreEqual(0f, a.w);
            Assert.AreEqual(0.4f, Get(_a.smr, "_FC_Emotion0").x, 1e-4f);
        }

        [Test]
        public void StrengthIsTheFinalScale()
        {
            _a.runner.alpha = 0.5f;
            _a.runner.EvaluateNow(0f, 0f, Dt);
            Assert.AreEqual(0.5f, Get(_a.smr, "_FC_Angles").z, 1e-4f);
        }

        [Test]
        public void AnglesAreClampedToMinusOneAndOne()
        {
            _a.runner.EvaluateNow(150f, -80f, Dt);
            Vector4 a = Get(_a.smr, "_FC_Angles");
            Assert.AreEqual(1f, a.x, 1e-6f);
            Assert.AreEqual(-1f, a.y, 1e-6f);
        }

        [Test]
        public void EmotionsArePackedFourPerVectorWithoutNeutral()
        {
            var layers = new FacialLayerData[7];
            for (int i = 0; i < layers.Length; i++)
                layers[i] = new FacialLayerData { name = i == 0 ? "Neutral" : "E" + i, enabled = true, morphNames = new string[9] { "", "", "", "", "", "", "", "", "" } };
            layers[0].morphNames = _a.data.layers[0].morphNames;
            _a.data.layers = layers;
            for (int i = 1; i < 7; i++) _a.runner.SetEmotionWeight(i, i * 0.1f);
            _a.runner.EvaluateNow(0f, 0f, Dt);
            Vector4 e0 = Get(_a.smr, "_FC_Emotion0"), e1 = Get(_a.smr, "_FC_Emotion1");
            Assert.AreEqual(0.1f, e0.x, 1e-5f); Assert.AreEqual(0.2f, e0.y, 1e-5f);
            Assert.AreEqual(0.3f, e0.z, 1e-5f); Assert.AreEqual(0.4f, e0.w, 1e-5f);
            Assert.AreEqual(0.5f, e1.x, 1e-5f); Assert.AreEqual(0.6f, e1.y, 1e-5f);
            Assert.AreEqual(0f, e1.z);
        }

        [Test]
        public void MutedLayerIsWrittenAsZero()
        {
            _a.runner.SetEmotionWeight(1, 0.8f);
            _a.runner.SetLayerMuted(1, true);
            _a.runner.EvaluateNow(0f, 0f, Dt);
            Assert.AreEqual(0f, Get(_a.smr, "_FC_Emotion0").x);
        }

        [Test]
        public void ModeFollowsDataUnlessRunnerOverrides()
        {
            _a.data.materialMode = "none";
            _a.runner.EvaluateNow(45f, 0f, Dt);
            Assert.IsFalse(_a.runner.MaterialOutputActive);
            _a.smr.GetPropertyBlock(_mpb);
            Assert.IsFalse(_mpb.HasVector("_FC_Angles"), "none のときは書かない");

            _a.runner.materialOutput = FacialMaterialOutputMode.PropertyBlock;
            _a.runner.EvaluateNow(45f, 0f, Dt);
            Assert.AreEqual(0.5f, Get(_a.smr, "_FC_Angles").x, 1e-4f);

            _a.data.materialMode = "propertyBlock";
            _a.runner.materialOutput = FacialMaterialOutputMode.Off;
            _a.runner.EvaluateNow(-45f, 0f, Dt);
            Assert.AreEqual(0f, Get(_a.smr, "_FC_Angles").x, "Off にすると前の値は 0 に戻る");
            Assert.AreEqual(FacialMaterialMode.PropertyBlock, _a.data.MaterialModeValue);
        }

        [Test]
        public void ExtraRenderersGetValuesToo()
        {
            var extra = _a.face.AddComponent<MeshRenderer>();
            _a.runner.materialTargets = new Renderer[] { extra, _a.smr };
            _a.runner.EvaluateNow(45f, 0f, Dt);
            Assert.AreEqual(0.5f, Get(extra, "_FC_Angles").x, 1e-4f);
            Assert.AreEqual(0.5f, Get(_a.smr, "_FC_Angles").x, 1e-4f);
            _a.runner.ResetWeights();
            Assert.AreEqual(Vector4.zero, Get(extra, "_FC_Angles"));
        }

        [Test]
        public void TwoRunnersOnTwoRenderersDoNotInterfere()
        {
            _b = new FacialTestRig();
            _b.data.materialMode = "propertyBlock";
            _a.runner.EvaluateNow(45f, 0f, Dt);
            _b.runner.EvaluateNow(-90f, 45f, Dt);
            _a.runner.EvaluateNow(45f, 0f, Dt);
            Vector4 a = Get(_a.smr, "_FC_Angles"), b = Get(_b.smr, "_FC_Angles");
            Assert.AreEqual(0.5f, a.x, 1e-4f); Assert.AreEqual(0f, a.y, 1e-4f);
            Assert.AreEqual(-1f, b.x, 1e-4f); Assert.AreEqual(1f, b.y, 1e-4f);
            _b.runner.ResetWeights();
            Assert.AreEqual(0.5f, Get(_a.smr, "_FC_Angles").x, 1e-4f, "片方を戻してももう片方は残る");
        }

        [Test]
        public void OtherPropertiesInTheBlockArePreserved()
        {
            _mpb.Clear();
            _mpb.SetVector("_Other", new Vector4(1, 2, 3, 4));
            _mpb.SetFloat("_Foo", 7f);
            _a.smr.SetPropertyBlock(_mpb);
            _a.runner.EvaluateNow(45f, 0f, Dt);
            _a.smr.GetPropertyBlock(_mpb);
            Assert.AreEqual(new Vector4(1, 2, 3, 4), _mpb.GetVector("_Other"));
            Assert.AreEqual(7f, _mpb.GetFloat("_Foo"));
            Assert.AreEqual(0.5f, _mpb.GetVector("_FC_Angles").x, 1e-4f);
            _a.runner.ResetWeights();
            _a.smr.GetPropertyBlock(_mpb);
            Assert.AreEqual(new Vector4(1, 2, 3, 4), _mpb.GetVector("_Other"), "戻すときも他の値は残る");
            Assert.AreEqual(7f, _mpb.GetFloat("_Foo"));
        }

        [Test]
        public void ResetZeroesStrengthAndEmotions()
        {
            _a.runner.SetEmotionWeight(1, 1f);
            _a.runner.EvaluateNow(45f, 20f, Dt);
            _a.runner.ResetWeights();
            Assert.AreEqual(Vector4.zero, Get(_a.smr, "_FC_Angles"));
            Assert.AreEqual(Vector4.zero, Get(_a.smr, "_FC_Emotion0"));
        }

        [Test]
        public void DataRemovedResetsMaterial()
        {
            _a.runner.EvaluateNow(45f, 0f, Dt);
            _a.runner.data = null;
            _a.runner.EvaluateNow(45f, 0f, Dt);
            Assert.AreEqual(Vector4.zero, Get(_a.smr, "_FC_Angles"));
        }

        [Test]
        public void SteadyStateDoesNotAllocate()
        {
            var emo = new[] { 0f, 0.3f };
            for (int i = 0; i < 20; i++)
            {
                _a.runner.PushOverride(new FacialFrameOverride { emotionWeights = emo });
                _a.runner.EvaluateNow(i * 3f, i * 1f, Dt);
            }
            AllocProbe.AssertNoAlloc(() =>
            {
                for (int i = 0; i < 300; i++)
                {
                    _a.runner.PushOverride(new FacialFrameOverride { emotionWeights = emo });
                    _a.runner.EvaluateNow(Mathf.Sin(i * 0.1f) * 80f, Mathf.Sin(i * 0.07f) * 40f, Dt);
                }
            }, "マテリアル出力で毎フレームの割り当てがある");
        }
    }
}
