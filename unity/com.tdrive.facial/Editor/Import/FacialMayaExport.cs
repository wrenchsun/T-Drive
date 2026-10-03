// 「Maya へ戻す」: 実効の調整値（取り込んだ値 + 調整用アセットの上書き）を、元の .fcpose の単位・キー名に戻して JSON にする。
// FcposeConverter（取り込み）の逆変換。出すのは policy と quality（と、端のフェードを上書きしているときの grid.edgeFade）だけ（格子・ポーズ・シェイプは Unity では変えない）。
// 長さ（距離フェード）は Unity の m → 元データの単位（cm など）。それ以外は単位なし。
using System.Globalization;
using System.Text;
using TDrive.Facial.Core;

namespace TDrive.Facial.Editor
{
    /// <summary>元の系（.fcpose の単位）へ戻した policy / quality。</summary>
    public struct FacialSourceTuning
    {
        public string unit;
        public double expressionDampen, interpSpeed, snapAngle, fadeStart, fadeEnd, globalAlpha;
        public double sharpness, stepFps, angleEpsilon;
        /// <summary>true のとき quality.exaggeration も書き出す（誇張を上書きしているときだけ）。</summary>
        public bool hasExaggeration;
        public double exaggeration;
        public int maxLod;
        /// <summary>true のとき grid.edgeFade も書き出す（端のフェードを上書きしているときだけ）。</summary>
        public bool hasEdgeFade;
        public double edgeFade;
    }

    public static class FacialMayaExport
    {
        /// <summary>元データの長さの単位。未設定・未知なら cm（Maya の既定）。</summary>
        public static string SourceUnit(FacialCorrectionData data)
        {
            string u = data != null ? data.source.unit : null;
            return !string.IsNullOrEmpty(u) && FacialSpace.UnitToCm(u) > 0.0 ? u : "cm";
        }

        /// <summary>Unity の m → 元の単位への倍率（取り込みの逆）。</summary>
        public static double LengthScaleToSource(FacialCorrectionData data)
        {
            var src = new SpaceSpec(SourceUnit(data), "Y", "right");
            return FacialSpace.Converter(FacialSpace.Unity, src).Scale; // = m → 元の単位
        }

        /// <summary>実効の値（Resolve の結果）を元の系へ戻す。純粋な関数（テスト用に公開）。</summary>
        public static FacialSourceTuning ToSource(FacialCorrectionData data, FacialEffectiveParams p)
        {
            double len = LengthScaleToSource(data);
            return new FacialSourceTuning
            {
                unit = SourceUnit(data),
                expressionDampen = p.expressionDampen,
                interpSpeed = p.interpSpeed,
                snapAngle = p.snapAngle,
                fadeStart = p.fadeStart * len,
                fadeEnd = p.fadeEnd * len,
                globalAlpha = p.globalAlpha,
                sharpness = p.sharpness,
                stepFps = p.stepFps,
                angleEpsilon = data != null ? data.quality.angleEpsilon : 0.1,
                maxLod = data != null ? data.quality.maxLod : 0,
                edgeFade = p.edgeFade,
                exaggeration = p.exaggeration,
            };
        }

        /// <summary>data と上書きから、Maya が読める JSON（policy / quality と、端のフェードを上書きしているときの grid.edgeFade。.fcpose と同じキー名）を作る。</summary>
        public static string BuildJson(FacialCorrectionData data, FacialCorrectionOverrides overrides)
        {
            FacialSourceTuning t = ToSource(data, FacialCorrectionOverrides.Resolve(data, overrides));
            t.hasEdgeFade = overrides != null && overrides.overrideEdgeFade; // 上書きしているときだけ grid を出す
            t.hasExaggeration = overrides != null && overrides.overrideExaggeration; // 誇張も上書きしているときだけ
            return BuildJson(t);
        }

        public static string BuildJson(FacialSourceTuning t)
        {
            var sb = new StringBuilder(384);
            sb.Append("{\n");
            sb.Append("  \"policy\": {\n");
            sb.Append("    \"expressionDampen\": ").Append(N(t.expressionDampen)).Append(",\n");
            sb.Append("    \"interpSpeed\": ").Append(N(t.interpSpeed)).Append(",\n");
            sb.Append("    \"snapAngle\": ").Append(N(t.snapAngle)).Append(",\n");
            sb.Append("    \"fade\": [").Append(N(t.fadeStart)).Append(", ").Append(N(t.fadeEnd)).Append("],\n");
            sb.Append("    \"globalAlpha\": ").Append(N(t.globalAlpha)).Append("\n");
            sb.Append("  },\n");
            if (t.hasEdgeFade)
            {
                sb.Append("  \"grid\": {\n");
                sb.Append("    \"edgeFade\": ").Append(N(t.edgeFade)).Append("\n");
                sb.Append("  },\n");
            }
            sb.Append("  \"quality\": {\n");
            sb.Append("    \"sharpness\": ").Append(N(t.sharpness)).Append(",\n");
            sb.Append("    \"stepFps\": ").Append(N(t.stepFps)).Append(",\n");
            sb.Append("    \"angleEpsilon\": ").Append(N(t.angleEpsilon)).Append(",\n");
            sb.Append("    \"maxLod\": ").Append(t.maxLod.ToString(CultureInfo.InvariantCulture)).Append(t.hasExaggeration ? ",\n" : "\n");
            if (t.hasExaggeration) sb.Append("    \"exaggeration\": ").Append(N(t.exaggeration)).Append("\n");
            sb.Append("  }\n");
            sb.Append("}\n");
            return sb.ToString();
        }

        // float の誤差（0.1f → 0.10000000149）を出さないよう、float へ丸めてから最短の書式で出す
        static string N(double v)
        {
            if (double.IsNaN(v) || double.IsInfinity(v)) return "0";
            return ((float)v).ToString("R", CultureInfo.InvariantCulture);
        }
    }
}
