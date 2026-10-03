// 補間の種類 catmullRom（F5-11、docs/15 §5.u）の計算のテスト（Unity なしでも動く純粋な計算）。共通のテストデータ evaluate_catmullrom.json の補足。
using System;
using System.Collections.Generic;
using NUnit.Framework;
using TDrive.Facial.Core;

namespace TDrive.Facial.Tests
{
    public class FacialInterpolationCoreTests
    {
        static LayerEvalInput Neutral(int rows, int cols)
        {
            var names = new List<string>();
            for (int r = 0; r < rows; r++) for (int c = 0; c < cols; c++) names.Add("P" + r + "_" + c);
            return new LayerEvalInput(names, 0.0, true);
        }

        static Dictionary<string, double> Eval(GridShape g, LayerEvalInput l, double yaw, double pitch, double sharp, FacialInterpolation it)
        {
            var o = new List<MorphWeight>();
            FacialCore.EvaluateCorrection(g, new[] { l }, yaw, pitch, o, sharp, 1.0, it);
            var d = new Dictionary<string, double>();
            foreach (MorphWeight m in o) d[m.MorphName] = m.Weight;
            return d;
        }

        [Test]
        public void BasisSumsToOneAndIsOneAtTheKeys()
        {
            for (double t = 0.0; t <= 1.0; t += 0.05)
            {
                double a, b, c, d;
                FacialCore.CatmullRomBasis(t, out a, out b, out c, out d);
                Assert.AreEqual(1.0, a + b + c + d, 1e-12);
            }
            double w0, w1, w2, w3;
            FacialCore.CatmullRomBasis(0.0, out w0, out w1, out w2, out w3);
            Assert.AreEqual(1.0, w1, 1e-12); Assert.AreEqual(0.0, w0 + w2 + w3, 1e-12);
            FacialCore.CatmullRomBasis(1.0, out w0, out w1, out w2, out w3);
            Assert.AreEqual(1.0, w2, 1e-12); Assert.AreEqual(0.0, w0 + w1 + w3, 1e-12);
        }

        [Test]
        public void DefaultArgumentIsBilinear()
        {
            var g = new GridShape(90, 45, 5, 3, 15);
            var l = Neutral(3, 5);
            var a = new List<MorphWeight>();
            var b = new List<MorphWeight>();
            FacialCore.EvaluateCorrection(g, new[] { l }, 33.0, 7.0, a);
            FacialCore.EvaluateCorrection(g, new[] { l }, 33.0, 7.0, b, 1.0, 1.0, FacialInterpolation.Bilinear);
            Assert.AreEqual(a.Count, b.Count);
            for (int i = 0; i < a.Count; i++) { Assert.AreEqual(a[i].MorphName, b[i].MorphName); Assert.AreEqual(a[i].Weight, b[i].Weight, 0.0); }
        }

        [Test]
        public void EqualsBilinearAtGridPointsAndSumsToOneInside()
        {
            var g = new GridShape(90, 45, 5, 3, 15);
            var l = Neutral(3, 5);
            for (int r = 0; r < 3; r++)
                for (int c = 0; c < 5; c++)
                {
                    double yaw = (c / 4.0 * 2.0 - 1.0) * 90.0, pitch = (r / 2.0 * 2.0 - 1.0) * 45.0;
                    var cr = Eval(g, l, yaw, pitch, 1.0, FacialInterpolation.CatmullRom);
                    var bl = Eval(g, l, yaw, pitch, 1.0, FacialInterpolation.Bilinear);
                    Assert.AreEqual(bl.Count, cr.Count);
                    foreach (var kv in bl) Assert.AreEqual(kv.Value, cr[kv.Key], 1e-9);
                }
            var rnd = new Random(7);
            for (int i = 0; i < 400; i++)
            {
                double yaw = rnd.NextDouble() * 180.0 - 90.0, pitch = rnd.NextDouble() * 90.0 - 45.0;
                double sharp = i % 3 == 0 ? 1.0 : 0.5 + rnd.NextDouble() * 4.0;
                double sum = 0.0;
                foreach (var kv in Eval(g, l, yaw, pitch, sharp, FacialInterpolation.CatmullRom)) { Assert.GreaterOrEqual(kv.Value, 0.0); sum += kv.Value; }
                Assert.AreEqual(1.0, sum, 1e-6, "yaw=" + yaw + " pitch=" + pitch);
            }
        }

        [Test]
        public void SingleRowAndSinglePointGrids()
        {
            var l1 = Neutral(1, 1);
            var one = Eval(new GridShape(90, 45, 1, 1, 15), l1, 30, 10, 1.0, FacialInterpolation.CatmullRom);
            Assert.AreEqual(1.0, one["P0_0"], 1e-12);
            var row = Eval(new GridShape(90, 45, 3, 1, 15), Neutral(1, 3), 22.5, 0, 1.0, FacialInterpolation.CatmullRom);
            double sum = 0; foreach (var kv in row) sum += kv.Value;
            Assert.AreEqual(1.0, sum, 1e-9);
        }

        [Test]
        public void SteadyStateDoesNotAllocate()
        {
            var g = new GridShape(90, 45, 5, 3, 15);
            var layers = new[] { Neutral(3, 5) };
            var o = new List<MorphWeight>(32);
            for (int i = 0; i < 20; i++) FacialCore.EvaluateCorrection(g, layers, i * 3.0, 5.0, o, 2.0, 1.0, FacialInterpolation.CatmullRom);
            long before = GC.GetAllocatedBytesForCurrentThread();
            for (int i = 0; i < 1000; i++) FacialCore.EvaluateCorrection(g, layers, (i % 60) * 2.0 - 60.0, (i % 20) - 10.0, o, 2.0, 1.0, FacialInterpolation.CatmullRom);
            // 1000 回で呼び出しごとの割り当てがあれば 24000 バイト以上になる（測定そのものの数バイトは許す）
            Assert.Less(GC.GetAllocatedBytesForCurrentThread() - before, 200L);
        }
    }
}
