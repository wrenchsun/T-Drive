// 格子ビューアの幾何・角度（FacialGridMath）と点の状態（FacialGridCells）のテスト。窓は開かない。
using NUnit.Framework;
using TDrive.Facial.Core;
using TDrive.Facial.Editor;
using UnityEngine;

namespace TDrive.Facial.Tests
{
    public class FacialGridMathTests
    {
        static readonly FacialGridData Grid5x3 = new FacialGridData
        {
            yawRange = 90f, pitchRange = 45f, cols = 5, rows = 3, edgeFade = 15f, baseBone = "head", forwardAxis = "+Z", centerOffset = Vector3.zero,
        };

        [Test]
        public void HighestPitchIsOnTopAndNegativeYawOnTheLeft()
        {
            var area = new Rect(0, 0, 500, 300);
            Rect topLeft = FacialGridMath.CellRect(area, 5, 3, 2, 0, 0f);
            Rect bottomRight = FacialGridMath.CellRect(area, 5, 3, 0, 4, 0f);
            Assert.AreEqual(0f, topLeft.y, 1e-4f);
            Assert.AreEqual(0f, topLeft.x, 1e-4f);
            Assert.AreEqual(200f, bottomRight.y, 1e-4f);
            Assert.AreEqual(400f, bottomRight.x, 1e-4f);
            Assert.AreEqual(100f, topLeft.width, 1e-4f);
            Assert.AreEqual(100f, topLeft.height, 1e-4f);
        }

        [Test]
        public void GapShrinksCells()
        {
            Rect r = FacialGridMath.CellRect(new Rect(0, 0, 500, 300), 5, 3, 1, 1, 4f);
            Assert.AreEqual(96f, r.width, 1e-4f);
            Assert.AreEqual(102f, r.x, 1e-4f);
        }

        [Test]
        public void CellCentersMapBackToTheSamePoint()
        {
            var area = new Rect(10, 20, 500, 300);
            for (int r = 0; r < 3; r++)
                for (int c = 0; c < 5; c++)
                {
                    int rr, cc;
                    Assert.IsTrue(FacialGridMath.TryPointAt(area, 5, 3, FacialGridMath.CellRect(area, 5, 3, r, c, 2f).center, out rr, out cc));
                    Assert.AreEqual(r, rr); Assert.AreEqual(c, cc);
                }
            int x, y;
            Assert.IsFalse(FacialGridMath.TryPointAt(area, 5, 3, new Vector2(0, 0), out x, out y));
        }

        [Test]
        public void PointAnglesFollowTheMayaConvention()
        {
            double yaw, pitch;
            FacialGridMath.PointAngles(Grid5x3, 0, 0, out yaw, out pitch);
            Assert.AreEqual(-90.0, yaw, 1e-9); Assert.AreEqual(-45.0, pitch, 1e-9);
            FacialGridMath.PointAngles(Grid5x3, 2, 4, out yaw, out pitch);
            Assert.AreEqual(90.0, yaw, 1e-9); Assert.AreEqual(45.0, pitch, 1e-9);
            FacialGridMath.PointAngles(Grid5x3, 1, 2, out yaw, out pitch);
            Assert.AreEqual(0.0, yaw, 1e-9); Assert.AreEqual(0.0, pitch, 1e-9);
        }

        [Test]
        public void LiveMarkerLandsInTheCellOfItsAngle()
        {
            var area = new Rect(0, 0, 500, 300);
            for (int r = 0; r < 3; r++)
                for (int c = 0; c < 5; c++)
                {
                    double yaw, pitch;
                    FacialGridMath.PointAngles(Grid5x3, r, c, out yaw, out pitch);
                    Vector2 p = FacialGridMath.AngleToPosition(area, Grid5x3, yaw, pitch);
                    Vector2 center = FacialGridMath.CellRect(area, 5, 3, r, c, 0f).center;
                    Assert.AreEqual(center.x, p.x, 1e-3f);
                    Assert.AreEqual(center.y, p.y, 1e-3f);
                }
            Vector2 far = FacialGridMath.AngleToPosition(area, Grid5x3, 500, 500); // 範囲外は端に収まる
            Assert.IsTrue(area.Contains(far) || far.x <= area.xMax && far.y >= area.yMin);
        }

        [Test]
        public void ConventionPlusYawIsCameraOnCharacterLeftAndPlusPitchIsAbove()
        {
            Vector3 pos; Quaternion rot;
            FacialGridMath.CameraPose(new Vector3(0, 1.5f, 0), Quaternion.identity, "+Z", Vector3.zero, 90, 0, 2f, out pos, out rot);
            Assert.AreEqual(-2f, pos.x, 1e-4f); // キャラクターは +Z を向く。左 = -X（Runner のテストと同じ）
            Assert.AreEqual(1.5f, pos.y, 1e-4f);
            FacialGridMath.CameraPose(new Vector3(0, 1.5f, 0), Quaternion.identity, "+Z", Vector3.zero, 0, 45, 2f, out pos, out rot);
            Assert.Greater(pos.y, 1.5f + 1f);
            Assert.Greater(pos.z, 1f);
            Vector3 toCenter = (new Vector3(0, 1.5f, 0) - pos).normalized;
            Assert.AreEqual(1f, Vector3.Dot(rot * Vector3.forward, toCenter), 1e-5f, "カメラは中心を向く");
        }

        [Test]
        public void CameraPoseIsTheInverseOfTheViewAngleComputation()
        {
            var heads = new[]
            {
                Quaternion.identity,
                Quaternion.Euler(0, 35, 0),
                Quaternion.Euler(10, -70, 20),
                Quaternion.Euler(-15, 180, 5),
                Quaternion.Euler(25, 120, -30),
            };
            string[] axes = { "+Z", "-Z", "+X", "-X" };
            var offset = new Vector3(0.01f, 0.02f, 0.05f);
            var basePos = new Vector3(1f, 1.5f, -2f);
            int checkedCount = 0;
            foreach (string axis in axes)
                foreach (Quaternion q in heads)
                    for (int r = 0; r < 3; r++)
                        for (int c = 0; c < 5; c++)
                        {
                            double yaw, pitch;
                            FacialGridMath.PointAngles(Grid5x3, r, c, out yaw, out pitch);
                            Vector3 pos; Quaternion rot;
                            FacialGridMath.CameraPose(basePos, q, axis, offset, yaw, pitch, 1.7f, out pos, out rot);
                            double yy, pp;
                            FacialGridMath.ViewAngles(basePos, q, axis, offset, pos, out yy, out pp);
                            string msg = axis + " " + q.eulerAngles + " R" + r + " C" + c;
                            Assert.AreEqual(0.0, FacialCore.NormalizeAxis(yy - yaw), 1e-3, "yaw " + msg);
                            Assert.AreEqual(pitch, pp, 1e-3, "pitch " + msg);
                            checkedCount++;
                        }
            Assert.AreEqual(4 * 5 * 15, checkedCount);
        }

        [Test]
        public void DistanceIsKept()
        {
            var center = new Vector3(0, 1.5f, 0);
            Vector3 pos; Quaternion rot;
            FacialGridMath.CameraPose(center, Quaternion.Euler(0, 40, 0), "+Z", Vector3.zero, 30, 20, 2.5f, out pos, out rot);
            Assert.AreEqual(2.5f, Vector3.Distance(pos, center), 1e-4f);
        }

        // ---------------------------------------------------------------- 点の状態

        const string TwoLayerDoc = "{\"format\":\"FacialCorrection\",\"version\":1,"
            + "\"meta\":{\"unit\":\"cm\",\"upAxis\":\"Y\",\"handedness\":\"right\"},"
            + "\"grid\":{\"yawRange\":90,\"pitchRange\":45,\"cols\":3,\"rows\":2,\"baseBone\":\"head\",\"forwardAxis\":\"+Z\"},"
            + "\"layers\":["
            + "{\"name\":\"Neutral\",\"points\":[{\"row\":0,\"col\":0,\"isKey\":true,\"curves\":{}},{\"row\":0,\"col\":1,\"isKey\":false,\"curves\":{}},{\"row\":1,\"col\":2,\"isKey\":true,\"curves\":{}}]},"
            + "{\"name\":\"Joy\",\"points\":[{\"row\":1,\"col\":1,\"isKey\":false,\"curves\":{}}]}],\"asset\":\"t\"}";

        [Test]
        public void CellKindsComeFromTheSourceJson()
        {
            FacialCorrectionData d = FcposeConverter.BuildData(TwoLayerDoc, "t", null);
            try
            {
                FcDocument src = FacialGridCells.ParseSource(FacialSourceJson.Get(d));
                Assert.IsNotNull(src);
                FacialCellKind[] n = FacialGridCells.Build(d, src, 0);
                Assert.AreEqual(6, n.Length);
                Assert.AreEqual(FacialCellKind.Key, n[0]);
                Assert.AreEqual(FacialCellKind.Generated, n[1]);
                Assert.AreEqual(FacialCellKind.None, n[2]);
                Assert.AreEqual(FacialCellKind.Key, n[1 * 3 + 2]);
                Assert.AreEqual(2, FacialGridCells.Count(n, FacialCellKind.Key));
                FacialCellKind[] j = FacialGridCells.Build(d, src, 1);
                Assert.AreEqual(FacialCellKind.Generated, j[4]);
                Assert.AreEqual(5, FacialGridCells.Count(j, FacialCellKind.None));
                Assert.AreEqual(0, FacialGridCells.Build(d, src, 9).Length);
            }
            finally { Object.DestroyImmediate(d); }
        }

        [Test]
        public void WithoutSourceJsonNamedPointsCountAsGenerated()
        {
            var d = ScriptableObject.CreateInstance<FacialCorrectionData>();
            try
            {
                d.grid.cols = 2; d.grid.rows = 1;
                d.layers = new[] { new FacialLayerData { name = "Neutral", enabled = true, morphNames = new[] { "FC_x_Neutral_R0_C0", "" } } };
                FacialCellKind[] k = FacialGridCells.Build(d, null, 0);
                Assert.AreEqual(FacialCellKind.Generated, k[0]);
                Assert.AreEqual(FacialCellKind.None, k[1]);
                Assert.IsNull(FacialGridCells.ParseSource("not json"));
                Assert.IsNull(FacialGridCells.ParseSource(""));
            }
            finally { Object.DestroyImmediate(d); }
        }

        // ---------------------------------------------------------------- Maya と同じ操作（位置 ⇔ 角度・カメラの距離・左右反転）

        [Test]
        public void PositionToAnglesIsTheInverseOfAngleToPosition()
        {
            var area = new Rect(10, 20, 500, 300);
            double[,] cases = { { 0, 0 }, { 37.5, -10.0 }, { -90, 45 }, { 90, -45 }, { 13.3, 22.2 } };
            for (int i = 0; i < cases.GetLength(0); i++)
            {
                Vector2 p = FacialGridMath.AngleToPosition(area, Grid5x3, cases[i, 0], cases[i, 1]);
                double yaw, pitch;
                FacialGridMath.PositionToAngles(area, Grid5x3, p, out yaw, out pitch);
                Assert.AreEqual(cases[i, 0], yaw, 1e-3);
                Assert.AreEqual(cases[i, 1], pitch, 1e-3);
            }
        }

        [Test]
        public void PositionToAnglesClampsOutsideTheGrid()
        {
            var area = new Rect(0, 0, 500, 300);
            double yaw, pitch;
            FacialGridMath.PositionToAngles(area, Grid5x3, new Vector2(-999, -999), out yaw, out pitch);
            Assert.AreEqual(-90.0, yaw, 1e-9); Assert.AreEqual(45.0, pitch, 1e-9);   // 左上 = -Yaw / +Pitch
            FacialGridMath.PositionToAngles(area, Grid5x3, new Vector2(999, 999), out yaw, out pitch);
            Assert.AreEqual(90.0, yaw, 1e-9); Assert.AreEqual(-45.0, pitch, 1e-9);
            var one = new FacialGridData { yawRange = 90f, pitchRange = 45f, cols = 1, rows = 1 };
            FacialGridMath.PositionToAngles(area, one, new Vector2(300, 100), out yaw, out pitch);
            Assert.AreEqual(0.0, yaw); Assert.AreEqual(0.0, pitch);
        }

        [Test]
        public void OutOfRangeMarkerIsPutOnTheCentreOfTheEdgeCell()
        {
            var area = new Rect(0, 0, 500, 300);
            Vector2 p = FacialGridMath.AngleToPosition(area, Grid5x3, 500, -500);
            Assert.AreEqual(450f, p.x, 1e-3f); // 右端の列の中心
            Assert.AreEqual(250f, p.y, 1e-3f); // 一番下の行の中心
        }

        [Test]
        public void CameraPlacementKeepsTheDistanceAndLooksAtTheCentre()
        {
            var center = new Vector3(0.2f, 1.5f, -0.3f);
            Vector3 fwd = Quaternion.Euler(0, 35, 0) * Vector3.forward;
            double[,] cases = { { 0, 0 }, { 37.5, -10 }, { -90, 45 }, { 90, -45 } };
            for (int i = 0; i < cases.GetLength(0); i++)
            {
                // 今のカメラ（距離 2.5）から中心までの距離を保って、別の角度へ
                var current = center + new Vector3(1f, 0.5f, 2f).normalized * 2.5f;
                float d = FacialGridMath.KeepDistance(current, center, 1.5f);
                Assert.AreEqual(2.5f, d, 1e-4f);
                Vector3 pos; Quaternion rot;
                FacialGridMath.CameraPoseFromFrame(center, fwd, cases[i, 0], cases[i, 1], d, out pos, out rot);
                Assert.AreEqual(2.5f, Vector3.Distance(pos, center), 1e-4f);
                Assert.AreEqual(1f, Vector3.Dot(rot * Vector3.forward, (center - pos).normalized), 1e-5f);
                double yaw, pitch;
                FacialGridMath.ViewAnglesFromFrame(center, fwd, pos, out yaw, out pitch);
                Assert.AreEqual(0.0, FacialCore.NormalizeAxis(yaw - cases[i, 0]), 1e-3);
                Assert.AreEqual(cases[i, 1], pitch, 1e-3);
            }
        }

        [Test]
        public void KeepDistanceFallsBackOnlyWhenTheCameraIsOnTheCentre()
        {
            Assert.AreEqual(1.5f, FacialGridMath.KeepDistance(Vector3.one, Vector3.one, 1.5f));
            Assert.AreEqual(0.25f, FacialGridMath.KeepDistance(Vector3.zero, new Vector3(0.25f, 0, 0), 1.5f), 1e-6f);
            Assert.AreEqual(40f, FacialGridMath.KeepDistance(Vector3.zero, new Vector3(0, 40f, 0), 1.5f), 1e-4f); // 遠くても丸めない
        }

        [Test]
        public void MirroredParentGivesTheSameDotAsTheRunner()
        {
            using (var rig = new FacialTestRig())
            {
                rig.root.transform.localScale = new Vector3(-1f, 1f, 1f); // 左右反転の親
                rig.data.grid.forwardAxis = "+X";
                rig.runner.useManualAngles = false;
                rig.runner.RebuildCaches();
                Transform bone = rig.head.transform;
                Vector3 center, fwd;
                FacialGridMath.BoneFrame(bone, rig.data.grid, out center, out fwd);
                double[,] cases = { { 0, 0 }, { 45, 10 }, { -60, -20 } };
                for (int i = 0; i < cases.GetLength(0); i++)
                {
                    Vector3 pos; Quaternion rot;
                    FacialGridMath.CameraPoseFromFrame(center, fwd, cases[i, 0], cases[i, 1], 2f, out pos, out rot);
                    double yaw, pitch;
                    FacialGridMath.ViewAnglesFromFrame(center, fwd, pos, out yaw, out pitch);
                    Assert.AreEqual(cases[i, 0], yaw, 1e-3, "窓の逆写像");
                    Assert.AreEqual(cases[i, 1], pitch, 1e-3);
                    GameObject v = UnityEditor.EditorUtility.CreateGameObjectWithHideFlags("GridViewer", HideFlags.HideAndDontSave);
                    try
                    {
                        v.transform.position = pos;
                        rig.runner.EvaluateNow(v.transform, 1f / 60f);
                        Assert.AreEqual(yaw, rig.runner.CurrentYaw, 1e-2, "Runner が見る角度と、窓の赤い点の角度が同じ");
                        Assert.AreEqual(pitch, rig.runner.CurrentPitch, 1e-2);
                    }
                    finally { Object.DestroyImmediate(v); }
                }
                // 反転していない親のときは回転だけで求める従来の式と同じ
                rig.root.transform.localScale = Vector3.one;
                rig.runner.RebuildCaches();
                Vector3 c2, f2;
                FacialGridMath.BoneFrame(bone, rig.data.grid, out c2, out f2);
                Vector3 p1, p2; Quaternion r1, r2;
                FacialGridMath.CameraPoseFromFrame(c2, f2, 30, 10, 2f, out p1, out r1);
                FacialGridMath.CameraPose(bone.position, bone.rotation, "+X", Vector3.zero, 30, 10, 2f, out p2, out r2);
                Assert.AreEqual(0f, Vector3.Distance(p1, p2), 1e-4f);
            }
        }

        [Test]
        public void ReadoutAndAxisLabelsUseThePlainHyphenAndOneDecimal()
        {
            Assert.AreEqual("カメラ: Scene ビュー  Yaw 37.5° / Pitch -10.0°", FacialGridMath.FormatCameraReadout("Scene ビュー", 37.5, -10.0, false));
            Assert.AreEqual("カメラ: Scene ビュー  Yaw 100.0° / Pitch 0.0°（範囲外）", FacialGridMath.FormatCameraReadout("Scene ビュー", 100, 0, true));
            Assert.AreEqual("0°", FacialGridMath.FormatAngle(0));
            Assert.AreEqual("+22.5°", FacialGridMath.FormatAngle(22.5));
            Assert.AreEqual("-45°", FacialGridMath.FormatAngle(-45));
            Assert.AreEqual("+90°", FacialGridMath.FormatAngle(90));
        }
    }
}
