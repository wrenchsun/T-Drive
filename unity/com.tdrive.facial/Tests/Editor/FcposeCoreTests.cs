// .fcpose の読み取り（Core。UnityEngine 非依存）と命名規則のテスト。
// Unity の EditMode と unity/FacialCoreTests（素の .NET）の両方で動く。期待値は Python 版（core/fcpose_io.py・naming.py）に合わせる。
using System.Collections.Generic;
using System.IO;
using NUnit.Framework;
using TDrive.Facial.Core;

namespace TDrive.Facial.Tests
{
    /// <summary>tests/facial/fixtures の場所（ConformanceData が見つけた conformance の隣）。</summary>
    public static class FixtureFiles
    {
        public static string Read(string name)
        {
            string dir = ConformanceData.Directory;
            if (dir == null) Assert.Fail(ConformanceData.Error);
            string path = Path.GetFullPath(Path.Combine(dir, "..", "fixtures", name));
            Assert.IsTrue(File.Exists(path), "フィクスチャが無い: " + path);
            return File.ReadAllText(path);
        }
    }

    public class FcposeCoreTests
    {
        static List<string> NewWarnings(out System.Action<string> sink)
        {
            var list = new List<string>();
            sink = s => list.Add(s);
            return list;
        }

        [Test]
        public void MissingKeysGetPythonDefaults()
        {
            FcDocument d = FcposeReader.ReadDocument("{\"format\":\"FacialCorrection\"}", null);
            Assert.AreEqual(1, d.Version);
            Assert.AreEqual("cm", d.Meta.Unit);
            Assert.AreEqual("Z", d.Meta.UpAxis);
            Assert.AreEqual("left", d.Meta.Handedness);
            Assert.AreEqual("", d.Meta.Source);
            Assert.AreEqual(90.0, d.Grid.YawRange);
            Assert.AreEqual(45.0, d.Grid.PitchRange);
            Assert.AreEqual(5, d.Grid.Cols);
            Assert.AreEqual(3, d.Grid.Rows);
            Assert.AreEqual("head", d.Grid.BaseBone);
            Assert.AreEqual("+X", d.Grid.ForwardAxis);
            Assert.AreEqual(0.0, d.Grid.CenterOffset.X + d.Grid.CenterOffset.Y + d.Grid.CenterOffset.Z);
            Assert.AreEqual(15.0, d.Grid.EdgeFade);
            Assert.AreEqual(0.5, d.Policy.ExpressionDampen);
            Assert.AreEqual(10.0, d.Policy.InterpSpeed);
            Assert.AreEqual(45.0, d.Policy.SnapAngle);
            Assert.AreEqual(0.0, d.Policy.FadeStart);
            Assert.AreEqual(0.0, d.Policy.FadeEnd);
            Assert.AreEqual(1.0, d.Policy.GlobalAlpha);
            Assert.AreEqual(1.0, d.Quality.Sharpness);
            Assert.AreEqual(0.0, d.Quality.StepFps);
            Assert.AreEqual(0.1, d.Quality.AngleEpsilon);
            Assert.AreEqual(0, d.Quality.MaxLod);
            Assert.AreEqual("none", d.MaterialMode);
            Assert.IsNull(d.Asset);
            Assert.AreEqual(1, d.Layers.Count, "layers が無いと Neutral が 1 つ入る");
            Assert.AreEqual("Neutral", d.Layers[0].Name);
            Assert.IsTrue(d.Layers[0].Enabled);
            Assert.AreEqual(0, d.Layers[0].Points.Count);
        }

        [Test]
        public void WrongTypesFallBackToDefaults()
        {
            const string json = "{\"format\":\"FacialCorrection\",\"grid\":{\"yawRange\":\"x\",\"cols\":true,\"centerOffset\":[1,2],\"forwardAxis\":3},"
                + "\"policy\":{\"fade\":[5]},\"layers\":[7,{\"name\":5,\"points\":[{\"row\":1},{\"row\":2,\"col\":3.9}]}]}";
            var warn = NewWarnings(out System.Action<string> sink);
            FcDocument d = FcposeReader.ReadDocument(json, sink);
            Assert.AreEqual(90.0, d.Grid.YawRange);
            Assert.AreEqual(5, d.Grid.Cols);
            Assert.AreEqual("+X", d.Grid.ForwardAxis);
            Assert.AreEqual(0.0, d.Grid.CenterOffset.Y);
            Assert.AreEqual(0.0, d.Policy.FadeEnd);
            Assert.AreEqual(1, d.Layers.Count, "オブジェクトでないレイヤーは捨てる");
            Assert.AreEqual("Neutral", d.Layers[0].Name);
            Assert.AreEqual(1, d.Layers[0].Points.Count, "row / col が無い点は捨てる");
            Assert.AreEqual(3, d.Layers[0].Points[0].Col, "小数は 0 方向へ切り捨て");
            Assert.AreEqual(1, warn.Count);
        }

        [Test]
        public void UeFullAssetFixture()
        {
            var warn = NewWarnings(out System.Action<string> sink);
            FcDocument d = FcposeReader.ReadDocument(FixtureFiles.Read("ue_full_asset.fcpose.json"), sink);
            Assert.AreEqual(0, warn.Count);
            Assert.AreEqual("UE5.8", d.Meta.Source);
            Assert.AreEqual("Z", d.Meta.UpAxis);
            Assert.AreEqual("head", d.Grid.BaseBone);
            Assert.AreEqual("+X", d.Grid.ForwardAxis);
            Assert.AreEqual(3.5, d.Grid.CenterOffset.Z);
            Assert.AreEqual(2, d.Layers.Count);
            Assert.AreEqual(5, d.Layers[0].Points.Count);
            Assert.NotNull(d.Layers[0].FindPoint(2, 2));
            Assert.AreEqual("Joy", d.Layers[1].Name);
            Assert.AreEqual("emo_joy", d.Layers[1].EmotionCurve);
            Assert.AreEqual(2, d.Layers[1].Points.Count);
            CollectionAssert.AreEqual(new[] { "bs.mouth_smile_L", "bs.mouth_smile_R", "bs.jaw_open" }, d.IntensityCurves);
            Assert.IsNull(d.Asset, "UE 版の JSON には asset が無い");
            Assert.AreEqual(0, d.Limits.Count);
        }

        [Test]
        public void MayaFullFixtureKeepsExtraKeysAndIgnoresUnknown()
        {
            var warn = NewWarnings(out System.Action<string> sink);
            FcDocument d = FcposeReader.ReadDocument(FixtureFiles.Read("tdrive_maya_full.fcpose.json"), sink);
            Assert.AreEqual(0, warn.Count);
            Assert.AreEqual("Y", d.Meta.UpAxis);
            Assert.AreEqual("right", d.Meta.Handedness);
            Assert.AreEqual("bone_head", d.Grid.BaseBone);
            Assert.AreEqual("+Z", d.Grid.ForwardAxis);
            Assert.AreEqual(1.5, d.Grid.CenterOffset.Y);
            Assert.AreEqual(0.5, d.Grid.CenterOffset.Z);
            Assert.AreEqual("shizuku", d.Asset);
            Assert.AreEqual("mdl_face02", d.TargetMesh);
            CollectionAssert.AreEqual(new[] { "mdl_eyelash" }, d.TargetExtraMeshes);
            Assert.AreEqual("propertyBlock", d.MaterialMode);
            Assert.AreEqual(2, d.Limits.Count);
            Assert.AreEqual(2.0, d.Limits["bs.jaw_open"].Max);
            Assert.AreEqual(0.1, d.Quality.AngleEpsilon);
            Assert.AreEqual(2, d.Layers[0].Points.Count);
            Assert.IsTrue(d.Layers[0].FindPoint(1, 4).IsKey);
            Assert.IsFalse(d.Layers[0].FindPoint(1, 0).IsKey);
            Assert.AreEqual(1, d.Layers[1].Points.Count);
            FcBone eye = d.Layers[0].FindPoint(1, 4).Pose.Bones["bone_eye_L"];
            Assert.AreEqual(0.05, eye.T.Y, 1e-12);
            Assert.AreEqual(0.9961946980917455, eye.R.W, 1e-15);
            Assert.AreEqual(0.4, d.Layers[0].FindPoint(1, 4).Pose.Curves["bs.mouth_smile_L"]);
        }

        [Test]
        public void PoseFixture()
        {
            FcFile f = FcposeReader.Read(FixtureFiles.Read("ue_single_pose.fcpose.json"), null);
            Assert.AreEqual("FacialPose", f.Format);
            Assert.IsNull(f.Document);
            Assert.AreEqual(7, f.Pose.Pose.Curves.Count);
            Assert.AreEqual(0.20, f.Pose.Pose.Curves["brow_down"], 1e-6);
            Assert.AreEqual(1, f.Pose.Pose.Bones.Count);
            Assert.AreEqual(-0.34202014078474741, f.Pose.Pose.Bones["bone_neck"].R.Y, 1e-15);
            Assert.AreEqual("Z", f.Pose.Meta.UpAxis);
        }

        [Test]
        public void NewerVersionWarnsButReads()
        {
            var warn = NewWarnings(out System.Action<string> sink);
            FcDocument d = FcposeReader.ReadDocument("{\"format\":\"FacialCorrection\",\"version\":2,\"future\":{\"a\":1}}", sink);
            Assert.AreEqual(2, d.Version);
            Assert.AreEqual(1, warn.Count);
            StringAssert.Contains("version=2", warn[0]);
        }

        [Test]
        public void BrokenInputThrows()
        {
            Assert.Throws<FcposeException>(() => FcposeReader.Read("{ not json", null));
            Assert.Throws<FcposeException>(() => FcposeReader.Read("[1,2]", null));
            Assert.Throws<FcposeException>(() => FcposeReader.Read("{\"format\":\"Other\"}", null));
            Assert.Throws<FcposeException>(() => FcposeReader.ReadDocument("{\"format\":\"FacialPose\"}", null));
        }

        [Test]
        public void BomIsSkipped()
        {
            FcFile f = FcposeReader.Read("﻿{\"format\":\"FacialPose\"}", null);
            Assert.AreEqual("FacialPose", f.Format);
        }

        // --- 命名 ---

        [Test]
        public void MorphNames()
        {
            Assert.AreEqual("FC_shizuku_Neutral_R1_C4", FacialNaming.MorphName("shizuku", "Neutral", 1, 4));
            Assert.AreEqual("FC_shizuku_Joy_R0_C2_Ex", FacialNaming.MorphName("shizuku", "Joy", 0, 2, true));
            Assert.AreEqual("FC_shizuku_Persp_K3", FacialNaming.PerspectiveName("shizuku", 3));
            Assert.AreEqual("FC_shizuku_", FacialNaming.AssetPrefix("shizuku"));
            Assert.IsTrue(FacialNaming.IsFcName("FC_x"));
            Assert.IsFalse(FacialNaming.IsFcName("fc_x"));
            Assert.IsFalse(FacialNaming.IsFcName("bs.jaw_open"));
            Assert.IsFalse(FacialNaming.IsFcName(null));
        }

        [Test]
        public void ParseNames()
        {
            ParsedFacialName p;
            Assert.IsTrue(FacialNaming.TryParse("FC_shizuku_Joy_R2_C3", null, out p));
            Assert.AreEqual(FacialNameKind.Point, p.Kind);
            Assert.AreEqual("shizuku", p.Asset);
            Assert.AreEqual("Joy", p.Layer);
            Assert.AreEqual(2, p.Row);
            Assert.AreEqual(3, p.Col);

            Assert.IsTrue(FacialNaming.TryParse("FC_shizuku_Joy_R2_C3_Ex", "shizuku", out p));
            Assert.AreEqual(FacialNameKind.PointEx, p.Kind);

            // asset を渡すと layer に _ が入っていても曖昧にならない
            Assert.IsTrue(FacialNaming.TryParse("FC_my_char_Big_Smile_R0_C0", "my_char", out p));
            Assert.AreEqual("my_char", p.Asset);
            Assert.AreEqual("Big_Smile", p.Layer);

            Assert.IsTrue(FacialNaming.TryParse("FC_shizuku_Persp_K2", "shizuku", out p));
            Assert.AreEqual(FacialNameKind.Persp, p.Kind);
            Assert.AreEqual(2, p.Index);
            Assert.IsTrue(FacialNaming.TryParse("FC_shizuku_Persp_K2", null, out p));
            Assert.AreEqual("shizuku", p.Asset);

            Assert.IsFalse(FacialNaming.TryParse("FC_other_Joy_R2_C3", "shizuku", out p));
            Assert.IsFalse(FacialNaming.TryParse("bs.jaw_open", null, out p));
            Assert.IsFalse(FacialNaming.TryParse("FC_shizuku_Joy", "shizuku", out p));
            Assert.IsFalse(FacialNaming.TryParse("FC_Joy_R1_C1", null, out p), "asset と layer が区切れない");
        }
    }
}
