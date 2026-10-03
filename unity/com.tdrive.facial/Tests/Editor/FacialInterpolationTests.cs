// 補間の種類（F5-11）: Runner（データ・調整用アセットの上書き）・取り込み・Maya へ戻す JSON のテスト（Unity EditMode）。
// 合成キャラクター（FacialTestRig: 格子 3×3・頭 (0, 1.5, 0)・顔は +Z）を使う。計算そのものは FacialInterpolationCoreTests と共通のテストデータ。
using System.Collections.Generic;
using NUnit.Framework;
using TDrive.Facial.Core;
using TDrive.Facial.Editor;
using UnityEditor;
using UnityEngine;
using Object = UnityEngine.Object;

namespace TDrive.Facial.Tests
{
    public class FacialInterpolationTests
    {
        const float Dt = 0.02f;
        FacialTestRig _rig;
        GameObject _viewer;
        FacialCorrectionOverrides _ov;
        readonly List<Object> _owned = new List<Object>();

        static string N(string layer, int r, int c) { return FacialTestRig.N(layer, r, c); }

        [SetUp]
        public void SetUp()
        {
            _rig = new FacialTestRig();
            _viewer = EditorUtility.CreateGameObjectWithHideFlags("viewer", HideFlags.HideAndDontSave);
        }

        [TearDown]
        public void TearDown()
        {
            _rig.Dispose();
            if (_viewer != null) Object.DestroyImmediate(_viewer);
            if (_ov != null) Object.DestroyImmediate(_ov);
            foreach (Object o in _owned) if (o != null) Object.DestroyImmediate(o);
            _owned.Clear();
        }

        FacialCorrectionRunner R { get { return _rig.runner; } }

        void UseOverrides()
        {
            _ov = ScriptableObject.CreateInstance<FacialCorrectionOverrides>();
            R.overrides = _ov;
        }

        /// <summary>同じ角度を Core で直接計算した重み（× 100 = ブレンドシェイプの値）。</summary>
        static Dictionary<string, double> Expected(double yaw, double pitch, double sharp, FacialInterpolation it)
        {
            var g = new GridShape(90, 45, 3, 3, 15);
            var names = new List<string>();
            for (int r = 0; r < 3; r++) for (int c = 0; c < 3; c++) names.Add(N("Neutral", r, c));
            var o = new List<MorphWeight>();
            FacialCore.EvaluateCorrection(g, new[] { new LayerEvalInput(names, 0.0, true) }, yaw, pitch, o, sharp, 1.0, it);
            var d = new Dictionary<string, double>();
            foreach (MorphWeight m in o) d[m.MorphName] = m.Weight * 100.0;
            return d;
        }

        // メッシュに無い点（FacialTestRig.Missing）は Runner が飛ばすので比べない
        void AssertMatches(Dictionary<string, double> expected)
        {
            foreach (KeyValuePair<string, double> kv in expected)
                if (kv.Key != FacialTestRig.Missing) Assert.AreEqual(kv.Value, _rig.W(kv.Key), 1e-2, kv.Key);
        }

        [Test]
        public void DefaultDataIsBilinear()
        {
            Assert.AreEqual(FacialInterpolation.Bilinear, _rig.data.quality.interpolation);
            R.EvaluateNow(22.5f, 0f, Dt);
            Assert.AreEqual(FacialInterpolation.Bilinear, R.LastInterpolation);
            Assert.AreEqual(75f, _rig.W(N("Neutral", 1, 1)), 1e-3f);
        }

        [Test]
        public void CatmullRomFromDataMatchesTheCore()
        {
            _rig.data.quality.interpolation = FacialInterpolation.CatmullRom;
            R.EvaluateNow(22.5f, 10f, Dt);
            Assert.AreEqual(FacialInterpolation.CatmullRom, R.LastInterpolation);
            AssertMatches(Expected(22.5, 10.0, 1.0, FacialInterpolation.CatmullRom));
        }

        [Test]
        public void CatmullRomWithSharpnessMatchesTheCore()
        {
            _rig.data.quality.interpolation = FacialInterpolation.CatmullRom;
            _rig.data.quality.sharpness = 2f;
            R.EvaluateNow(-33f, -12f, Dt);
            AssertMatches(Expected(-33.0, -12.0, 2.0, FacialInterpolation.CatmullRom));
        }

        [Test]
        public void ExactGridPointIsTheSameAsBilinear()
        {
            _rig.data.quality.interpolation = FacialInterpolation.CatmullRom;
            R.EvaluateNow(0f, 0f, Dt);
            Assert.AreEqual(100f, _rig.W(N("Neutral", 1, 1)), 1e-3f);
        }

        [Test]
        public void OverrideBeatsTheData()
        {
            UseOverrides();
            R.EvaluateNow(22.5f, 10f, Dt);
            float bilinear = _rig.W(N("Neutral", 1, 1));
            _ov.overrideInterpolation = true; _ov.interpolation = FacialInterpolation.CatmullRom;
            R.EvaluateNow(22.5f, 10f, 1f); // 同じ角度でも、補間が変わったら計算し直す（再利用の印に入っている）
            Assert.AreEqual(FacialInterpolation.CatmullRom, R.LastInterpolation);
            Assert.Greater(Mathf.Abs(bilinear - _rig.W(N("Neutral", 1, 1))), 1e-3f);
            AssertMatches(Expected(22.5, 10.0, 1.0, FacialInterpolation.CatmullRom));
            _ov.interpolation = FacialInterpolation.Bilinear; // 上書きでデータの catmullRom を戻すこともできる
            _rig.data.quality.interpolation = FacialInterpolation.CatmullRom;
            R.EvaluateNow(22.5f, 10f, 1f);
            Assert.AreEqual(FacialInterpolation.Bilinear, R.LastInterpolation);
            Assert.AreEqual(bilinear, _rig.W(N("Neutral", 1, 1)), 1e-3f);
        }

        [Test]
        public void ResolveFollowsDataAndOverride()
        {
            _rig.data.quality.interpolation = FacialInterpolation.CatmullRom;
            Assert.AreEqual(FacialInterpolation.CatmullRom, FacialCorrectionOverrides.Resolve(_rig.data, null).interpolation);
            UseOverrides();
            Assert.AreEqual(FacialInterpolation.CatmullRom, FacialCorrectionOverrides.Resolve(_rig.data, _ov).interpolation, "上書きのチェックが無ければデータ");
            _ov.overrideInterpolation = true; _ov.interpolation = FacialInterpolation.Bilinear;
            Assert.AreEqual(FacialInterpolation.Bilinear, FacialCorrectionOverrides.Resolve(_rig.data, _ov).interpolation);
            Assert.AreEqual(FacialInterpolation.Bilinear, FacialCorrectionOverrides.Resolve(null, null).interpolation);
        }

        [Test]
        public void SteadyStateWithCatmullRomDoesNotAllocate()
        {
            _rig.data.quality.interpolation = FacialInterpolation.CatmullRom;
            _rig.data.quality.sharpness = 2f;
            for (int i = 0; i < 40; i++)
            {
                _viewer.transform.position = new Vector3(Mathf.Sin(i * 0.3f) * 3f, 1.5f, Mathf.Cos(i * 0.3f) * 3f);
                R.EvaluateNow(_viewer.transform, Dt);
            }
            AllocProbe.AssertNoAlloc(() =>
            {
                for (int i = 0; i < 300; i++)
                {
                    _viewer.transform.position = new Vector3(Mathf.Sin(i * 0.1f) * 3f, 1.5f + Mathf.Sin(i * 0.05f), Mathf.Cos(i * 0.1f) * 3f);
                    R.EvaluateNow(_viewer.transform, Dt);
                }
            }, "catmullRom の 2 回目以降の評価でマネージドの割り当てがある");
        }

        // ---------------------------------------------------------------- 取り込み・書き出し

        static string Json(string extra)
        {
            return "{\"format\":\"FacialCorrection\",\"version\":1,"
                + "\"meta\":{\"unit\":\"cm\",\"upAxis\":\"Y\",\"handedness\":\"right\"},"
                + "\"grid\":{\"yawRange\":90,\"pitchRange\":45,\"cols\":3,\"rows\":3,\"baseBone\":\"head\",\"forwardAxis\":\"+Z\",\"centerOffset\":[0,0,0],\"edgeFade\":15},"
                + "\"layers\":[{\"name\":\"Neutral\",\"points\":[{\"row\":1,\"col\":1,\"isKey\":true,\"curves\":{}}]}],\"asset\":\"test\","
                + extra + "}";
        }

        [Test]
        public void ImporterReadsInterpolation()
        {
            FacialCorrectionData d = FcposeConverter.BuildData(Json("\"quality\":{\"interpolation\":\"catmullRom\"}"), "x", null);
            _owned.Add(d);
            Assert.AreEqual(FacialInterpolation.CatmullRom, d.quality.interpolation);
            FacialCorrectionData e = FcposeConverter.BuildData(Json("\"quality\":{\"sharpness\":2}"), "x", null);
            _owned.Add(e);
            Assert.AreEqual(FacialInterpolation.Bilinear, e.quality.interpolation, "キーが無ければ双線形");
            FacialCorrectionData f = FcposeConverter.BuildData(Json("\"layerWeights\":{}"), "x", null);
            _owned.Add(f);
            Assert.AreEqual(FacialInterpolation.Bilinear, f.quality.interpolation, "quality が無くても双線形");
        }

        [Test]
        public void ImporterWarnsOnUnknownValueAndFallsBackToBilinear()
        {
            var warns = new List<string>();
            FacialCorrectionData d = FcposeConverter.BuildData(Json("\"quality\":{\"interpolation\":\"spline\"}"), "x", warns.Add);
            _owned.Add(d);
            Assert.AreEqual(FacialInterpolation.Bilinear, d.quality.interpolation);
            Assert.IsTrue(warns.Exists(w => w.Contains("spline")), string.Join(" / ", warns));
        }

        [Test]
        public void MayaExportWritesInterpolationOnlyWhenOverridden()
        {
            FacialCorrectionData d = FcposeConverter.BuildData(Json("\"quality\":{\"interpolation\":\"catmullRom\"}"), "x", null);
            _owned.Add(d);
            var root = (Dictionary<string, object>)MiniJson.Parse(FacialMayaExport.BuildJson(d, null));
            Assert.IsFalse(((Dictionary<string, object>)root["quality"]).ContainsKey("interpolation"), "上書きしていなければ出さない");
            UseOverrides();
            _ov.overrideInterpolation = true; _ov.interpolation = FacialInterpolation.CatmullRom;
            root = (Dictionary<string, object>)MiniJson.Parse(FacialMayaExport.BuildJson(d, _ov));
            Assert.AreEqual("catmullRom", (string)((Dictionary<string, object>)root["quality"])["interpolation"]);
            _ov.interpolation = FacialInterpolation.Bilinear;
            _ov.overrideExaggeration = true; _ov.exaggeration = 0.4f; // 誇張と同時でも JSON の区切りが崩れない
            root = (Dictionary<string, object>)MiniJson.Parse(FacialMayaExport.BuildJson(d, _ov));
            var q = (Dictionary<string, object>)root["quality"];
            Assert.AreEqual("bilinear", (string)q["interpolation"]);
            Assert.AreEqual(0.4, System.Convert.ToDouble(q["exaggeration"]), 1e-6);
        }
    }
}
