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
                FcDocument src = FacialGridCells.ParseSource(d.sourceJson);
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
    }
}
