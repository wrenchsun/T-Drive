// シェイプ名の規則（Python 版 core/naming.py の写し）。規則を変えるのは MAJOR。
//   角度の補正シェイプ FC_<asset>_<layer>_R{row}_C{col} / 誇張用 …_Ex / パース補正 FC_<asset>_Persp_K{n}
// 名前の照合は大文字小文字を区別する完全一致。UnityEngine 非依存。
using System.Globalization;
using System.Text.RegularExpressions;

namespace TDrive.Facial.Core
{
    public enum FacialNameKind { Point, PointEx, Persp }

    public sealed class ParsedFacialName
    {
        public FacialNameKind Kind;
        public string Asset;
        public string Layer;
        public int Row = -1;
        public int Col = -1;
        public int Index = -1; // パース補正の K 番号
    }

    public static class FacialNaming
    {
        public const string FcPrefix = "FC_";
        public const string ExtremeSuffix = "_Ex";
        public const string PerspectiveLayer = "Persp";

        static readonly Regex PointTail = new Regex(@"^(?<layer>.+)_R(?<row>\d+)_C(?<col>\d+)(?<ex>_Ex)?$", RegexOptions.CultureInvariant);
        static readonly Regex PerspTail = new Regex(@"^Persp_K(?<k>\d+)$", RegexOptions.CultureInvariant);
        static readonly Regex PerspAny = new Regex(@"^(?<asset>.+)_Persp_K(?<k>\d+)$", RegexOptions.CultureInvariant);

        public static string MorphName(string asset, string layer, int row, int col, bool extreme = false)
        {
            string name = FcPrefix + asset + "_" + layer + "_R" + row.ToString(CultureInfo.InvariantCulture)
                + "_C" + col.ToString(CultureInfo.InvariantCulture);
            return extreme ? name + ExtremeSuffix : name;
        }

        public static string PerspectiveName(string asset, int index)
        {
            return FcPrefix + asset + "_" + PerspectiveLayer + "_K" + index.ToString(CultureInfo.InvariantCulture);
        }

        /// <summary>この asset のシェイプの共通の頭 "FC_&lt;asset&gt;_"。</summary>
        public static string AssetPrefix(string asset) { return FcPrefix + asset + "_"; }

        public static bool IsFcName(string name)
        {
            return name != null && name.StartsWith(FcPrefix, System.StringComparison.Ordinal);
        }

        // ------------------------------------------------------------ FBX 取り込み後の名前（"<blendShape ノード名>.<ターゲット名>"）
        // Maya から FBX を出すと、Unity のシェイプ名は "bs.FC_shizuku_Neutral_R0_C0" のようにノード名が前に付く。
        // データ（.fcpose）の名前は付かない形（"FC_..."）と付いた形（"bs.eye_close_L"）が混ざるので、照合は
        // 「完全一致 → 末尾が "." + 名前」の順で行う。

        /// <summary>メッシュのシェイプ名 actual が、探している名前 wanted と同じものか（完全一致、または actual が "&lt;何か&gt;." + wanted）。</summary>
        public static bool ShapeNameMatches(string actual, string wanted)
        {
            if (actual == null || string.IsNullOrEmpty(wanted)) return false;
            if (string.Equals(actual, wanted, System.StringComparison.Ordinal)) return true;
            int dl = actual.Length - wanted.Length - 1;
            return dl >= 0 && actual[dl] == '.' && string.CompareOrdinal(actual, dl + 1, wanted, 0, wanted.Length) == 0;
        }

        /// <summary>
        /// メッシュのシェイプ名から、ノード名の接頭辞を除いた FC_ の名前を取り出す。"FC_x" → "FC_x"、"bs.FC_x" → "FC_x"。
        /// FC_ のシェイプでなければ false。
        /// </summary>
        public static bool TryGetBareFcName(string shapeName, out string bare)
        {
            bare = null;
            if (string.IsNullOrEmpty(shapeName)) return false;
            if (IsFcName(shapeName)) { bare = shapeName; return true; }
            int i = shapeName.IndexOf("." + FcPrefix, System.StringComparison.Ordinal);
            if (i < 0) return false;
            bare = shapeName.Substring(i + 1);
            return true;
        }

        /// <summary>メッシュのシェイプ名が、この頭（"FC_&lt;asset&gt;_" など）で始まる FC_ のシェイプか（ノード名の接頭辞つきも可）。</summary>
        public static bool HasFcPrefix(string shapeName, string prefix)
        {
            string bare;
            return TryGetBareFcName(shapeName, out bare) && bare.StartsWith(prefix, System.StringComparison.Ordinal);
        }

        /// <summary>
        /// "FC_*" の名前を分解する。規則に合わなければ false。
        /// asset を渡すと "FC_&lt;asset&gt;_" で始まるものだけを対象にし、残りを layer として扱う。
        /// 渡さないと、点の名前は「最後の _R{n}_C{n} の直前の _ 区切り 1 語」を layer、それより前を asset とみなす。
        /// </summary>
        public static bool TryParse(string name, string asset, out ParsedFacialName parsed)
        {
            parsed = null;
            if (!IsFcName(name)) return false;
            string body = name.Substring(FcPrefix.Length);
            if (asset != null)
            {
                string head = asset + "_";
                if (!body.StartsWith(head, System.StringComparison.Ordinal)) return false;
                string tail = body.Substring(head.Length);
                Match pm = PerspTail.Match(tail);
                if (pm.Success)
                {
                    parsed = new ParsedFacialName { Kind = FacialNameKind.Persp, Asset = asset, Layer = PerspectiveLayer, Index = Int(pm, "k") };
                    return true;
                }
                Match m = PointTail.Match(tail);
                if (!m.Success) return false;
                parsed = Point(m, asset, m.Groups["layer"].Value);
                return true;
            }
            Match pa = PointTail.Match(body);
            if (pa.Success)
            {
                string head = pa.Groups["layer"].Value;
                int us = head.LastIndexOf('_');
                if (us < 0) return false; // asset と layer が区切れない
                parsed = Point(pa, head.Substring(0, us), head.Substring(us + 1));
                return true;
            }
            Match ps = PerspAny.Match(body);
            if (ps.Success)
            {
                parsed = new ParsedFacialName { Kind = FacialNameKind.Persp, Asset = ps.Groups["asset"].Value, Layer = PerspectiveLayer, Index = Int(ps, "k") };
                return true;
            }
            return false;
        }

        static ParsedFacialName Point(Match m, string asset, string layer)
        {
            return new ParsedFacialName
            {
                Kind = m.Groups["ex"].Success ? FacialNameKind.PointEx : FacialNameKind.Point,
                Asset = asset,
                Layer = layer,
                Row = Int(m, "row"),
                Col = Int(m, "col"),
            };
        }

        static int Int(Match m, string group)
        {
            return int.Parse(m.Groups[group].Value, CultureInfo.InvariantCulture);
        }
    }
}
