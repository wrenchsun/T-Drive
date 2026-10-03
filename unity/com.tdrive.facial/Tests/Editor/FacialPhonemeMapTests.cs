// 解析側の音素名 → T-Drive の音素名の対応（uLipSync の橋渡しから切り出した純粋な処理）。Unity の型を使わないので素の .NET でも動く。
using System.Collections.Generic;
using NUnit.Framework;

namespace TDrive.Facial.Tests
{
    public class FacialPhonemeMapTests
    {
        static FacialPhonemeMapEntry E(string from, string to) { return new FacialPhonemeMapEntry { from = from, to = to }; }

        [Test]
        public void EmptyTableKeepsNames()
        {
            var m = new FacialPhonemeMap(null);
            Assert.AreEqual("A", m.Map("A"));
            Assert.IsNull(m.Map(null));
            Assert.AreEqual(0, m.Count);
        }

        [Test]
        public void TableRenamesPassesThroughUnknownAndDropsEmptyTargets()
        {
            var m = new FacialPhonemeMap(new[] { E("a", "A"), E("n", ""), E("a", "X"), E("", "Y"), E("i", null) });
            Assert.AreEqual("A", m.Map("a"), "同じ from は先の行");
            Assert.IsNull(m.Map("n"), "変換先が空 = 捨てる");
            Assert.IsNull(m.Map("i"), "変換先が null も捨てる");
            Assert.AreEqual("u", m.Map("u"), "表にない名前はそのまま");
        }

        [Test]
        public void FillClearsFirstConvertsAndSkipsDropped()
        {
            var m = new FacialPhonemeMap(new[] { E("a", "A"), E("-", "") });
            var names = new List<string> { "old" };
            var weights = new List<float> { 9f };
            var ratios = new Dictionary<string, float> { { "a", 0.7f }, { "-", 0.2f }, { "i", 0.1f } };
            int n = m.Fill(ratios, names, weights);
            Assert.AreEqual(2, n);
            Assert.AreEqual(new[] { "A", "i" }, names.ToArray());
            Assert.AreEqual(new[] { 0.7f, 0.1f }, weights.ToArray());
            Assert.AreEqual(0, m.Fill(null, names, weights));
            Assert.AreEqual(0, names.Count);
        }

        [Test]
        public void RebuildReplacesTheTable()
        {
            var m = new FacialPhonemeMap(new[] { E("a", "A") });
            m.Rebuild(new[] { E("b", "B") });
            Assert.AreEqual("a", m.Map("a"));
            Assert.AreEqual("B", m.Map("b"));
        }
    }
}
