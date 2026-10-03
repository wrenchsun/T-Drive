// 「割り当て 0」のテスト用の測定器。測り方が壊れていて常に 0 を返す、という見逃しを防ぐため、
// 測る前に「わざと 64 バイトを 1000 回割り当てる」処理が実際に検出できるかを毎回確かめる（検出できない測り方は使わない）。
using System;
using NUnit.Framework;

namespace TDrive.Facial.Tests
{
    public static class AllocProbe
    {
        public static object Sink; // わざとの割り当てを最適化で消されないように置く先

        /// <summary>測る処理を繰り返す回数。Unity（Mono）の GC.GetTotalMemory は少量の割り当てでは動かないので、まとめて測る。</summary>
        public const int Repeats = 200;
        /// <summary>1 回の処理の中の「呼び出し」の数（テストのループ回数に合わせる）。わざとの割り当ては 1 呼び出しあたり 64 バイト。</summary>
        public const int CallsPerAction = 300;

        enum Method { None, AllocatedBytes, TotalMemory }

        static Method _method;

        static long ViaAllocatedBytes(Action a, int repeats)
        {
            long before = GC.GetAllocatedBytesForCurrentThread();
            for (int r = 0; r < repeats; r++) a();
            return GC.GetAllocatedBytesForCurrentThread() - before;
        }

        // Unity の Mono: GC.GetAllocatedBytesForCurrentThread は常に 0。GetTotalMemory(false) は、空き領域がある間は割り当てても増えず、
        // 使い切ると 100KB 前後のブロック単位で増える。そこで (1) 空き領域を先に使い切り (2) 1 回ごとの増分の正の分だけを足す
        // （途中で回収されて減っても、増えた分は消えない）。1 回あたり数十バイトの漏れでも、数百回の繰り返しで検出できる
        static void ExhaustFreePool()
        {
            for (int round = 0; round < 2000; round++)
            {
                long before = GC.GetTotalMemory(false);
                for (int i = 0; i < 1000; i++) Sink = new byte[64];
                if (GC.GetTotalMemory(false) > before) break;
            }
            Sink = null;
        }

        static long ViaTotalMemory(Action a, int repeats)
        {
            ExhaustFreePool();
            long grown = 0;
            for (int r = 0; r < repeats; r++)
            {
                long before = GC.GetTotalMemory(false);
                a();
                long delta = GC.GetTotalMemory(false) - before;
                if (delta > 0) grown += delta;
            }
            return grown;
        }

        static void Deliberate()
        {
            for (int i = 0; i < CallsPerAction; i++) Sink = new byte[64];
        }

        // 64 バイト × CallsPerAction × repeats の半分以上を検出できる測り方を選ぶ
        static Method Pick()
        {
            if (_method != Method.None) return _method;
            long expected = 64L * CallsPerAction * Repeats;
            if (ViaAllocatedBytes(Deliberate, Repeats) >= expected / 2) _method = Method.AllocatedBytes;
            else if (ViaTotalMemory(Deliberate, Repeats) >= expected / 2) _method = Method.TotalMemory;
            Sink = null;
            return _method;
        }

        /// <summary>測り方の自己確認。わざとの割り当てを検出できない環境なら失敗させる（測っても 0 しか出ないため）。</summary>
        public static void SelfCheck()
        {
            Assert.AreNotEqual(Method.None, Pick(), "この環境ではマネージドの割り当てを測れない（わざとの 64 バイト × " + CallsPerAction * Repeats + " 回を検出できない）");
        }

        /// <summary>a を Repeats 回呼ぶ間のマネージドの割り当てバイト数。</summary>
        public static long Measure(Action a)
        {
            Method m = Pick();
            Assert.AreNotEqual(Method.None, m, "割り当てを測れない環境");
            return m == Method.AllocatedBytes ? ViaAllocatedBytes(a, Repeats) : ViaTotalMemory(a, Repeats);
        }

        /// <summary>
        /// a（CallsPerAction 回前後の呼び出しを含む処理）の割り当てが 0 であること。測る前に自己確認（わざとの割り当てを検出できること）をする。
        /// a は Repeats 回呼ばれる（状態が進んでもよい処理にすること）。
        /// </summary>
        public static void AssertNoAlloc(Action a, string message)
        {
            SelfCheck();
            a(); a(); // 1 回目の遅延初期化（キャッシュ・リストの確保）を測定に含めない
            // GetTotalMemory はプロセス全体（Unity エディタの別スレッド・MCP などの割り当ても拾う）なので、3 回測って 1 回でも 0 なら通す。
            // 本物の漏れ（毎回の割り当て）は 3 回とも出る
            long min = long.MaxValue;
            for (int trial = 0; trial < 3 && min != 0; trial++) min = Math.Min(min, Measure(a));
            Assert.AreEqual(0L, min, message);
        }
    }

    public class AllocProbeSelfTests
    {
        [Test]
        public void DeliberateAllocationIsDetected()
        {
            AllocProbe.SelfCheck();
            long bytes = AllocProbe.Measure(() => { for (int i = 0; i < AllocProbe.CallsPerAction; i++) AllocProbe.Sink = new byte[64]; });
            AllocProbe.Sink = null;
            Assert.GreaterOrEqual(bytes, 64L * AllocProbe.CallsPerAction * AllocProbe.Repeats / 2, "わざとの割り当て（1 呼び出しあたり 64 バイト）を検出できなかった");
        }

        [Test]
        public void NoAllocationMeasuresZero()
        {
            int x = 0;
            Assert.AreEqual(0L, AllocProbe.Measure(() => { for (int i = 0; i < AllocProbe.CallsPerAction; i++) x += i; }));
            Assert.Greater(x, 0);
        }
    }
}
