// .fcpose → FacialCorrectionData / FacialPoseAsset の変換（インポーターが使う静的関数）のテスト。Unity の EditMode 専用。
// アセットはディスクへ作らない（メモリ上に作って TearDown で破棄）。
using System.Collections.Generic;
using NUnit.Framework;
using TDrive.Facial.Core;
using TDrive.Facial.Editor;
using UnityEditor.AssetImporters;
using UnityEngine;

namespace TDrive.Facial.Tests
{
    public class FcposeConverterTests
    {
        readonly List<Object> _owned = new List<Object>();

        [TearDown]
        public void TearDown()
        {
            foreach (Object o in _owned) if (o != null) Object.DestroyImmediate(o);
            _owned.Clear();
        }

        FacialCorrectionData Build(string json, string fallback, List<string> warnings = null)
        {
            FacialCorrectionData d = FcposeConverter.BuildData(json, fallback, warnings == null ? null : (System.Action<string>)(s => warnings.Add(s)));
            _owned.Add(d);
            return d;
        }

        static void AssertVec(Vector3 expected, Vector3 actual, float tol = 1e-6f)
        {
            Assert.AreEqual(expected.x, actual.x, tol, "x");
            Assert.AreEqual(expected.y, actual.y, tol, "y");
            Assert.AreEqual(expected.z, actual.z, tol, "z");
        }

        [Test]
        public void MayaFixtureIsConvertedToUnitySpace()
        {
            string json = FixtureFiles.Read("tdrive_maya_full.fcpose.json");
            var warn = new List<string>();
            FacialCorrectionData d = Build(json, "fallback", warn);
            Assert.AreEqual(0, warn.Count);
            Assert.AreEqual("shizuku", d.assetName, "asset キーがあればそれを使う");
            // Maya（右手・Y-up・cm）の +Z は Unity（左手・Y-up・m）でも +Z = 前
            Assert.AreEqual("+Z", d.grid.forwardAxis);
            // centerOffset: X だけ反転、cm → m（0.01 倍）
            AssertVec(new Vector3(0f, 0.015f, 0.005f), d.grid.centerOffset);
            Assert.AreEqual("bone_head", d.grid.baseBone);
            Assert.AreEqual(90f, d.grid.yawRange);
            Assert.AreEqual(45f, d.grid.pitchRange);
            Assert.AreEqual(5, d.grid.cols);
            Assert.AreEqual(3, d.grid.rows);
            Assert.AreEqual(15f, d.grid.edgeFade);
            Assert.AreEqual(0.5f, d.policy.expressionDampen);
            Assert.AreEqual(10f, d.policy.interpSpeed);
            Assert.AreEqual(45f, d.policy.snapAngle);
            Assert.AreEqual(1f, d.policy.globalAlpha);
            Assert.AreEqual(0.1f, d.quality.angleEpsilon);
            Assert.AreEqual("propertyBlock", d.materialMode);
            Assert.AreEqual("mdl_face02", d.targetMesh);
            CollectionAssert.AreEqual(new[] { "mdl_eyelash" }, d.extraMeshes);
            CollectionAssert.AreEqual(new[] { "bs.mouth_smile_L" }, d.intensityCurves);
            Assert.AreEqual(2, d.limits.Length);
            Assert.AreEqual("bs.jaw_open", d.limits[1].name);
            Assert.AreEqual(2f, d.limits[1].max);

            // 点のあるところだけシェイプ名を持つ（行優先）
            Assert.AreEqual(2, d.layers.Length);
            Assert.AreEqual("Neutral", d.layers[0].name);
            Assert.AreEqual(15, d.layers[0].morphNames.Length);
            Assert.AreEqual("FC_shizuku_Neutral_R1_C0", d.MorphNameAt(0, 1, 0));
            Assert.AreEqual("FC_shizuku_Neutral_R1_C4", d.MorphNameAt(0, 1, 4));
            Assert.AreEqual("", d.MorphNameAt(0, 0, 0));
            Assert.AreEqual("", d.MorphNameAt(0, 5, 0), "格子の外は空");
            Assert.AreEqual("emo_joy", d.layers[1].emotionCurve);
            Assert.AreEqual("FC_shizuku_Joy_R1_C2", d.MorphNameAt(1, 1, 2));
            int named = 0;
            foreach (string n in d.layers[0].morphNames) if (n.Length > 0) named++;
            Assert.AreEqual(2, named);

            // 参考情報とエディタ専用のソース
            Assert.AreEqual("cm", d.source.unit);
            Assert.AreEqual("Y", d.source.upAxis);
            Assert.AreEqual("right", d.source.handedness);
            Assert.AreEqual("+Z", d.source.forwardAxis);
            AssertVec(new Vector3(0f, 1.5f, 0.5f), d.source.centerOffset);
            Assert.AreEqual(json, d.sourceJson);
        }

        [Test]
        public void UeFixtureIsConvertedAndAssetNameFallsBack()
        {
            FacialCorrectionData d = Build(FixtureFiles.Read("ue_full_asset.fcpose.json"), "ue_full_asset");
            Assert.AreEqual("ue_full_asset", d.assetName, "UE 版の JSON には asset が無いのでファイル名");
            // UE（左手・Z-up）の前 +X は Unity の前 +Z
            Assert.AreEqual("+Z", d.grid.forwardAxis);
            // UE の上 +Z = Unity の上 +Y。3.5 cm → 0.035 m
            AssertVec(new Vector3(0f, 0.035f, 0f), d.grid.centerOffset);
            Assert.AreEqual("head", d.grid.baseBone);
            Assert.AreEqual(0, d.limits.Length);
            Assert.AreEqual("none", d.materialMode);
            CollectionAssert.AreEqual(new[] { "bs.mouth_smile_L", "bs.mouth_smile_R", "bs.jaw_open" }, d.intensityCurves);
            Assert.AreEqual("FC_ue_full_asset_Neutral_R2_C2", d.MorphNameAt(0, 2, 2));
        }

        [Test]
        public void IntensityCurvesFallBackToWorkingSet()
        {
            FacialCorrectionData d = Build("{\"format\":\"FacialCorrection\",\"asset\":\"a\",\"workingSet\":{\"curves\":[\"x\",\"y\"]}}", null);
            CollectionAssert.AreEqual(new[] { "x", "y" }, d.intensityCurves);
        }

        [Test]
        public void FadeDistancesAreConvertedToMeters()
        {
            FacialCorrectionData d = Build("{\"format\":\"FacialCorrection\",\"asset\":\"a\",\"policy\":{\"fade\":[100,400]}}", null);
            Assert.AreEqual(1f, d.policy.fadeStart, 1e-6f);
            Assert.AreEqual(4f, d.policy.fadeEnd, 1e-6f);
            Assert.AreEqual(100f, d.source.fadeStart, "変換前の値は source に残る");
        }

        [Test]
        public void MetersSourceIsKeptAsIs()
        {
            const string json = "{\"format\":\"FacialCorrection\",\"asset\":\"a\",\"meta\":{\"unit\":\"m\",\"upAxis\":\"Y\",\"handedness\":\"left\"},"
                + "\"grid\":{\"forwardAxis\":\"-Z\",\"centerOffset\":[0.1,0.2,0.3]},\"policy\":{\"fade\":[2,5]}}";
            FacialCorrectionData d = Build(json, null);
            Assert.AreEqual("-Z", d.grid.forwardAxis);
            AssertVec(new Vector3(0.1f, 0.2f, 0.3f), d.grid.centerOffset);
            Assert.AreEqual(2f, d.policy.fadeStart, 1e-6f);
        }

        [Test]
        public void WarningsAreReported()
        {
            var warn = new List<string>();
            // version が新しい / asset 名が無い / 格子の外の点 / 上軸を forwardAxis にした
            const string json = "{\"format\":\"FacialCorrection\",\"version\":3,\"meta\":{\"unit\":\"cm\",\"upAxis\":\"Y\",\"handedness\":\"right\"},"
                + "\"grid\":{\"cols\":2,\"rows\":2,\"forwardAxis\":\"+Y\"},"
                + "\"layers\":[{\"name\":\"Neutral\",\"points\":[{\"row\":0,\"col\":0},{\"row\":5,\"col\":5}]}]}";
            FacialCorrectionData d = Build(json, null, warn);
            Assert.AreEqual(4, warn.Count, string.Join(" | ", warn));
            Assert.AreEqual("+Z", d.grid.forwardAxis, "変換できない前方軸は +Z");
            Assert.AreEqual("asset", d.assetName);
            Assert.AreEqual("FC_asset_Neutral_R0_C0", d.MorphNameAt(0, 0, 0));
        }

        [Test]
        public void PoseFixtureIsConvertedToUnitySpace()
        {
            FacialPoseAsset p = FcposeConverter.BuildPose(FixtureFiles.Read("ue_single_pose.fcpose.json"), null);
            _owned.Add(p);
            Assert.AreEqual(7, p.curves.Length);
            float brow = -1f;
            foreach (FacialPoseCurve c in p.curves) if (c.name == "brow_down") brow = c.value;
            Assert.AreEqual(0.2f, brow, 1e-6f);
            Assert.AreEqual(1, p.bones.Length);
            Assert.AreEqual("bone_neck", p.bones[0].name);
            // UE の (x, y, z) = Unity の (y, z, x)。回転のベクトル部も同じ入れ替え（両方左手なので符号はそのまま）
            Assert.AreEqual(-0.34202014f, p.bones[0].rotation.x, 1e-6f);
            Assert.AreEqual(0f, p.bones[0].rotation.y, 1e-6f);
            Assert.AreEqual(0.93969262f, p.bones[0].rotation.w, 1e-6f);
            Assert.AreEqual(1f, p.bones[0].scale.x);
            Assert.AreEqual("cm", p.source.unit);
        }

        [Test]
        public void WrongFormatThrows()
        {
            Assert.Throws<FcposeException>(() => FcposeConverter.BuildData(FixtureFiles.Read("ue_single_pose.fcpose.json"), "x", null));
            Assert.Throws<FcposeException>(() => FcposeConverter.BuildPose(FixtureFiles.Read("tdrive_maya_full.fcpose.json"), null));
        }

        [Test]
        public void ImporterIsRegisteredForFcposeExtension()
        {
            object[] attrs = typeof(FcposeImporter).GetCustomAttributes(typeof(ScriptedImporterAttribute), false);
            Assert.AreEqual(1, attrs.Length);
            var a = (ScriptedImporterAttribute)attrs[0];
            CollectionAssert.Contains(a.fileExtensions, "fcpose");
            Assert.GreaterOrEqual(a.version, 1);
        }
    }
}
