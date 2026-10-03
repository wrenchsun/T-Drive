// 格子ビューアの「点の状態」（キー / 自動生成 / 無し）。エディタ専用の元 JSON（FacialCorrectionData.sourceJson）から求める。
// 元 JSON が無いデータ（手で作ったものなど）は、シェイプ名があれば「自動生成」扱いにする（キーかどうかが分からない）。
using System;
using TDrive.Facial.Core;

namespace TDrive.Facial.Editor
{
    public enum FacialCellKind { None, Generated, Key }

    public static class FacialGridCells
    {
        /// <summary>元 JSON を読む（読めなければ null。警告は捨てる）。</summary>
        public static FcDocument ParseSource(string json)
        {
            if (string.IsNullOrEmpty(json)) return null;
            try { return FcposeReader.Read(json, null).Document; }
            catch (FormatException) { return null; }
        }

        /// <summary>レイヤー layer の全点の状態（行優先 index = row * cols + col）。範囲外のレイヤーは空の配列。</summary>
        public static FacialCellKind[] Build(FacialCorrectionData data, FcDocument source, int layer)
        {
            if (data == null || data.layers == null || layer < 0 || layer >= data.layers.Length) return new FacialCellKind[0];
            int cols = Math.Max(0, data.grid.cols), rows = Math.Max(0, data.grid.rows);
            var kinds = new FacialCellKind[rows * cols];
            string[] names = data.layers[layer].morphNames;
            FcLayer src = source != null && source.Layers.Count == data.layers.Length ? source.Layers[layer] : null;
            for (int r = 0; r < rows; r++)
                for (int c = 0; c < cols; c++)
                {
                    int i = r * cols + c;
                    bool hasName = names != null && i < names.Length && !string.IsNullOrEmpty(names[i]);
                    if (src != null)
                    {
                        FcPoint p = src.FindPoint(r, c);
                        kinds[i] = p == null ? FacialCellKind.None : (p.IsKey ? FacialCellKind.Key : FacialCellKind.Generated);
                    }
                    else kinds[i] = hasName ? FacialCellKind.Generated : FacialCellKind.None;
                }
            return kinds;
        }

        public static int Count(FacialCellKind[] kinds, FacialCellKind kind)
        {
            int n = 0;
            for (int i = 0; i < kinds.Length; i++) if (kinds[i] == kind) n++;
            return n;
        }
    }
}
