// 格子の「連続位置 ⇔ 角度」・赤い点のドラッグの状態機械・間引きのテスト（Unity なしで動く。数値は tests/facial/test_presenters.py と同じ）。
using NUnit.Framework;
using TDrive.Facial.Core;

namespace TDrive.Facial.Tests
{
    public class FacialGridMappingTests
    {
        // Python 版 make_set の格子: 90 / 45・5 列 × 3 行
        const double YR = 90, PR = 45;
        const int Cols = 5, Rows = 3;

        static FacialGridMarker Loc(double yaw, double pitch) { return FacialGridMapping.Locate(yaw, pitch, YR, PR, Cols, Rows); }

        [TestCase(0, 0, 2.0, 1.0, false, 1, 2)]
        [TestCase(90, 45, 4.0, 2.0, false, 2, 4)]        // 範囲の端ちょうどはクランプでない
        [TestCase(-90.5, 0, -0.0111, 1.0, true, 1, 0)]
        [TestCase(22.5, 0, 2.5, 1.0, false, 1, 3)]       // 同距離は大きい方の点
        [TestCase(0, 100, 2.0, 3.2222, true, 2, 2)]
        public void LocateMatchesPython(double yaw, double pitch, double colPos, double rowPos, bool clamped, int nearestRow, int nearestCol)
        {
            FacialGridMarker m = Loc(yaw, pitch);
            Assert.AreEqual(colPos, m.ColPos, 1e-3);
            Assert.AreEqual(rowPos, m.RowPos, 1e-3);
            Assert.AreEqual(clamped, m.Clamped);
            Assert.AreEqual(nearestRow, m.NearestRow);
            Assert.AreEqual(nearestCol, m.NearestCol);
        }

        [Test]
        public void LocateClampedPositionStaysInsideTheGrid()
        {
            FacialGridMarker m = Loc(-300, 300);
            Assert.AreEqual(0.0, m.ColPosClamped, 1e-9);
            Assert.AreEqual(Rows - 1, m.RowPosClamped, 1e-9);
            Assert.IsTrue(m.Clamped);
        }

        [Test]
        public void SingleColumnAndRowGiveZero()
        {
            FacialGridMarker m = FacialGridMapping.Locate(30, 10, YR, PR, 1, 1);
            Assert.AreEqual(0.0, m.ColPos); Assert.AreEqual(0.0, m.RowPos);
            Assert.AreEqual(0, m.NearestRow); Assert.AreEqual(0, m.NearestCol);
            double yaw, pitch;
            FacialGridMapping.PosToAngles(0.5, 0.5, YR, PR, 1, 1, out yaw, out pitch);
            Assert.AreEqual(0.0, yaw); Assert.AreEqual(0.0, pitch);
        }

        [Test]
        public void PosToAnglesIsTheInverseOfLocate()
        {
            double[,] cases = { { 0, 0 }, { 37.5, -10.0 }, { -90, 45 }, { 90, -45 }, { 13.3, 22.2 } };
            for (int i = 0; i < cases.GetLength(0); i++)
            {
                FacialGridMarker m = Loc(cases[i, 0], cases[i, 1]);
                double yaw, pitch;
                FacialGridMapping.PosToAngles(m.ColPos, m.RowPos, YR, PR, Cols, Rows, out yaw, out pitch);
                Assert.AreEqual(cases[i, 0], yaw, 1e-6);
                Assert.AreEqual(cases[i, 1], pitch, 1e-6);
            }
        }

        [Test]
        public void PosToAnglesClampsAndSingleAxis()
        {
            double yaw, pitch;
            FacialGridMapping.PosToAngles(-5, 99, YR, PR, 5, 3, out yaw, out pitch);
            Assert.AreEqual(-90.0, yaw); Assert.AreEqual(45.0, pitch);   // 端の外は端
            FacialGridMapping.PosToAngles(99, -5, YR, PR, 5, 3, out yaw, out pitch);
            Assert.AreEqual(90.0, yaw); Assert.AreEqual(-45.0, pitch);
            FacialGridMapping.PosToAngles(2.5, 1.0, YR, PR, 5, 3, out yaw, out pitch);
            Assert.AreEqual(22.5, yaw, 1e-9); Assert.AreEqual(0.0, pitch, 1e-9);
        }

        [Test]
        public void NearestPointOfEveryPointAngleIsItself()
        {
            for (int r = 0; r < Rows; r++)
                for (int c = 0; c < Cols; c++)
                {
                    double yaw, pitch;
                    FacialCore.PointAngles(YR, PR, Cols, Rows, r, c, out yaw, out pitch);
                    FacialGridMarker m = Loc(yaw, pitch);
                    Assert.AreEqual(r, m.NearestRow); Assert.AreEqual(c, m.NearestCol);
                }
        }

        // ---------------------------------------------------------------- ドラッグの状態機械

        static FacialGridDragState.Output P(FacialGridDragState s, bool shift, bool onMarker, bool onCell)
        {
            return s.Press(100f, 100f, shift, onMarker, onCell);
        }

        [Test]
        public void PressOnCellClicksImmediately()
        {
            var s = new FacialGridDragState();
            Assert.AreEqual(FacialGridDragState.Output.ClickAtPress, P(s, false, false, true));
            Assert.IsFalse(s.IsActive);
            Assert.AreEqual(FacialGridDragState.Output.None, s.Move(120f, 100f)); // 動いても何も起きない
        }

        [Test]
        public void PressOutsideDoesNothing()
        {
            var s = new FacialGridDragState();
            Assert.AreEqual(FacialGridDragState.Output.None, P(s, false, false, false));
            Assert.IsFalse(s.IsActive);
        }

        [Test]
        public void MarkerPressWaitsThenReleaseWithoutMovingIsAClick()
        {
            var s = new FacialGridDragState();
            Assert.AreEqual(FacialGridDragState.Output.None, P(s, false, true, true));
            Assert.AreEqual(FacialGridDragState.Phase.Pending, s.State);
            Assert.AreEqual(FacialGridDragState.Output.None, s.Move(102f, 102f));   // 約 2.8 px: まだクリック候補
            Assert.AreEqual(FacialGridDragState.Output.None, s.Move(104f, 100f));   // ちょうど 4 px はドラッグでない
            Assert.AreEqual(FacialGridDragState.Output.ClickAtPress, s.Release(104f, 100f));
            Assert.AreEqual(FacialGridDragState.Phase.Idle, s.State);
        }

        [Test]
        public void MarkerPressThenMovingMoreThanThresholdStartsDragging()
        {
            var s = new FacialGridDragState();
            P(s, false, true, true);
            Assert.AreEqual(FacialGridDragState.Output.BeginDrag | FacialGridDragState.Output.MoveTo, s.Move(104.1f, 100f));
            Assert.IsTrue(s.IsDragging);
            Assert.AreEqual(FacialGridDragState.Output.MoveTo, s.Move(150f, 130f));
            // 離す: 最後の位置を当てて終わる。クリックにはならない（点を選ばない）
            FacialGridDragState.Output o = s.Release(160f, 130f);
            Assert.AreEqual(FacialGridDragState.Output.MoveTo | FacialGridDragState.Output.EndDrag, o);
            Assert.AreEqual(0, (int)(o & FacialGridDragState.Output.ClickAtPress));
            Assert.IsFalse(s.IsActive);
        }

        [Test]
        public void ShiftPressStartsDraggingAnywhereAndReleaseWithoutMovingStillJumpsThere()
        {
            var s = new FacialGridDragState();
            Assert.AreEqual(FacialGridDragState.Output.BeginDrag | FacialGridDragState.Output.MoveTo, P(s, true, false, false));
            Assert.IsTrue(s.IsDragging);
            Assert.AreEqual(FacialGridDragState.Output.MoveTo | FacialGridDragState.Output.EndDrag, s.Release(100f, 100f)); // Shift + クリック = そこへジャンプ
        }

        [Test]
        public void ShiftPressOverMarkerAndCellIsStillADrag()
        {
            var s = new FacialGridDragState();
            Assert.AreEqual(FacialGridDragState.Output.BeginDrag | FacialGridDragState.Output.MoveTo, P(s, true, true, true));
        }

        [Test]
        public void CancelEndsWithoutClick()
        {
            var s = new FacialGridDragState();
            P(s, false, true, true);
            s.Cancel();
            Assert.AreEqual(FacialGridDragState.Output.None, s.Release(100f, 100f));
            Assert.AreEqual(FacialGridDragState.Output.None, s.Move(200f, 200f));
        }

        [Test]
        public void MarkerHitRadiusIsTenPixels()
        {
            Assert.IsTrue(FacialGridDragState.OnMarker(110f, 100f, 100f, 100f));
            Assert.IsFalse(FacialGridDragState.OnMarker(110.1f, 100f, 100f, 100f));
            Assert.IsTrue(FacialGridDragState.OnMarker(107f, 107f, 100f, 100f));   // 約 9.9 px
            Assert.IsFalse(FacialGridDragState.OnMarker(108f, 108f, 100f, 100f));  // 約 11.3 px
        }

        // ---------------------------------------------------------------- 間引き

        [Test]
        public void ThrottleAppliesFirstThenWaitsAndFlushesTheLastPosition()
        {
            var t = new FacialGridThrottle(0.030);
            double y, p;
            Assert.IsTrue(t.Offer(1.000, 10, 1, out y, out p));
            Assert.AreEqual(10, y);
            Assert.IsFalse(t.Offer(1.010, 20, 2, out y, out p));   // 間隔内: 保留
            Assert.IsFalse(t.Offer(1.020, 30, 3, out y, out p));   // 保留は最新に置き換わる
            Assert.IsTrue(t.HasPending);
            Assert.IsFalse(t.TryTake(1.025, out y, out p));
            Assert.IsTrue(t.TryTake(1.031, out y, out p));
            Assert.AreEqual(30, y); Assert.AreEqual(3, p);
            Assert.IsFalse(t.HasPending);
            Assert.IsFalse(t.Offer(1.040, 40, 4, out y, out p));
            Assert.IsTrue(t.Flush(1.041, out y, out p));            // 離したときは間隔に関係なく最後の位置
            Assert.AreEqual(40, y); Assert.AreEqual(4, p);
            Assert.IsFalse(t.Flush(1.042, out y, out p));           // 保留が無ければ何もしない
        }
    }
}
