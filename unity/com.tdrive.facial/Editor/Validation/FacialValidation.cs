// 検証（R-31 の Unity 側）。データと対象メッシュを突き合わせ、問題の一覧を返す。何も書き換えない（読み取りだけ）。
// 重さ: エラー = 構造の問題 / 警告 = 参照先が無い・孤立など / 情報 = 取り込み設定の推奨など（docs/14 §5.9 と同じ区分）。
using System;
using System.Collections.Generic;
using TDrive.Facial.Core;
using UnityEditor;
using UnityEngine;

namespace TDrive.Facial.Editor
{
    public enum FacialIssueSeverity { Info, Warning, Error }

    public enum FacialIssueKind
    {
        NoData,
        NoTarget,
        BaseBoneMissing,
        InvalidGrid,
        GridLayerMismatch,
        LayerZeroNotNeutral,
        DuplicateLayerName,
        MissingShape,
        OrphanShape,
        IntensityShapeMissing,
        LimitUnknownShape,
        LimitIgnored,
        BlendShapeNormals,
    }

    public sealed class FacialIssue
    {
        public FacialIssueSeverity Severity;
        public FacialIssueKind Kind;
        /// <summary>デザイナー向けの説明（日本語）。</summary>
        public string Message;
        /// <summary>関係するシェイプ名（無ければ null）。</summary>
        public string ShapeName;
        /// <summary>関係するボーン名（無ければ null）。</summary>
        public string BoneName;

        public override string ToString() { return "[" + Severity + "] " + Message; }
    }

    public static class FacialValidation
    {
        /// <summary>Blend Shape Normals の推奨。Maya が法線を再計算して FBX に入れるので、そのまま読み込む（docs/14 §5.7・§6.1）。</summary>
        public const ModelImporterNormals RecommendedBlendShapeNormals = ModelImporterNormals.Import;

        /// <summary>LimitIgnored の要約に名前を出す件数。</summary>
        const int LimitIgnoredNamesShown = 3;

        /// <summary>Runner の解決結果（対象メッシュ・基準ボーン）で検証する。</summary>
        public static List<FacialIssue> Run(FacialCorrectionRunner runner)
        {
            if (runner == null) return new List<FacialIssue>();
            if (runner.data == null) return Run(null, new SkinnedMeshRenderer[0], null);
            var renderers = new List<SkinnedMeshRenderer>(runner.ResolvedTargets);
            return Run(runner.data, renderers, runner.ResolvedBaseBone);
        }

        /// <summary>データ・対象メッシュ・基準ボーン（見つからなければ null）で検証する。</summary>
        public static List<FacialIssue> Run(FacialCorrectionData data, IReadOnlyList<SkinnedMeshRenderer> renderers, Transform baseBone)
        {
            var issues = new List<FacialIssue>();
            if (data == null)
            {
                Add(issues, FacialIssueSeverity.Error, FacialIssueKind.NoData, "データ（FacialCorrectionData）が未設定です。.fcpose を取り込んだアセットを入れてください");
                return issues;
            }

            AddStructureIssues(issues, data);
            FacialGridData g = data.grid;
            FacialLayerData[] layers = data.layers ?? new FacialLayerData[0];

            // --- 対象メッシュ・基準ボーン ---
            int targetCount = 0;
            var meshShapes = new HashSet<string>(StringComparer.Ordinal);   // 対象メッシュにあるすべてのシェイプ名（ノード名の接頭辞つき・なしの両方）
            var fcShapes = new List<string>();                              // そのうち FC_ で始まるもの（重複なし）
            if (renderers != null)
                for (int i = 0; i < renderers.Count; i++)
                {
                    SkinnedMeshRenderer r = renderers[i];
                    if (r == null) continue;
                    targetCount++;
                    Mesh m = r.sharedMesh;
                    if (m == null) continue;
                    for (int k = 0; k < m.blendShapeCount; k++)
                    {
                        // FBX 取り込み後は "<ノード名>.<ターゲット名>"（例 bs.FC_x）。完全な名前と、"." のあとの切り口の両方で引けるようにする
                        string n = m.GetBlendShapeName(k);
                        bool isNew = meshShapes.Add(n);
                        for (int d = n.IndexOf('.'); d >= 0 && d + 1 < n.Length; d = n.IndexOf('.', d + 1)) meshShapes.Add(n.Substring(d + 1));
                        string bare;
                        if (isNew && FacialNaming.TryGetBareFcName(n, out bare) && !fcShapes.Contains(bare)) fcShapes.Add(bare);
                    }
                }
            if (targetCount == 0)
                Add(issues, FacialIssueSeverity.Error, FacialIssueKind.NoTarget, "補正を書き込む対象のメッシュ（SkinnedMeshRenderer）がありません。FC_ シェイプ入りのメッシュを「対象」に入れるか、子に置いてください");
            if (baseBone == null)
                Add(issues, FacialIssueSeverity.Error, FacialIssueKind.BaseBoneMissing,
                    "基準ボーン '" + data.grid.baseBone + "' が見つかりません。角度を計算できないので補正は掛かりません（手動の角度なら動きます）", null, data.grid.baseBone);

            // --- FC_ シェイプの過不足 ---
            var expectedShapes = new HashSet<string>(StringComparer.Ordinal);
            for (int li = 0; li < layers.Length; li++)
            {
                string[] mn = layers[li].morphNames;
                if (mn == null) continue;
                for (int i = 0; i < mn.Length; i++)
                {
                    string nm = mn[i];
                    if (string.IsNullOrEmpty(nm) || !FacialNaming.IsFcName(nm) || !expectedShapes.Add(nm)) continue;
                    if (targetCount > 0 && !meshShapes.Contains(nm))
                        Add(issues, FacialIssueSeverity.Warning, FacialIssueKind.MissingShape,
                            "データにあるシェイプ '" + nm + "' がメッシュにありません（その点の補正は飛ばされます。FBX を出し直してください）", nm, null);
                }
            }
            string assetPrefix = string.IsNullOrEmpty(data.assetName) ? FacialNaming.FcPrefix : FacialNaming.AssetPrefix(data.assetName);
            for (int i = 0; i < fcShapes.Count; i++)
            {
                string n = fcShapes[i];
                if (expectedShapes.Contains(n)) continue;
                ParsedFacialName parsed;
                // 誇張 _Ex（任意のシェイプ。無くてもエラーにしない）・パース補正 Persp は管理下のシェイプなので孤立とは数えない
                if (n.StartsWith(assetPrefix, StringComparison.Ordinal) && FacialNaming.TryParse(n, data.assetName, out parsed)
                    && (parsed.Kind == FacialNameKind.Persp || parsed.Kind == FacialNameKind.PointEx)) continue;
                Add(issues, FacialIssueSeverity.Warning, FacialIssueKind.OrphanShape,
                    "メッシュの FC_ シェイプ '" + n + "' はデータが知らないシェイプです（孤立。Maya で作り直したときの古い残りかもしれません）", n, null);
            }

            // --- 表情の強さの入力・可動域 ---
            string[] intensity = data.intensityCurves ?? new string[0];
            for (int i = 0; i < intensity.Length; i++)
            {
                string nm = intensity[i];
                if (string.IsNullOrEmpty(nm)) continue;
                if (targetCount > 0 && !meshShapes.Contains(nm))
                    Add(issues, FacialIssueSeverity.Warning, FacialIssueKind.IntensityShapeMissing,
                        "表情の強さに使うシェイプ '" + nm + "' がメッシュにありません（表情での弱めに数えられません）", nm, null);
            }
            FacialLimitEntry[] limits = data.limits ?? new FacialLimitEntry[0];
            int ignoredCount = 0;
            string ignoredNames = "";
            for (int i = 0; i < limits.Length; i++)
            {
                string nm = limits[i].name;
                if (string.IsNullOrEmpty(nm)) continue;
                bool known = expectedShapes.Contains(nm) || meshShapes.Contains(nm);
                if (!known)
                    Add(issues, FacialIssueSeverity.Warning, FacialIssueKind.LimitUnknownShape,
                        "可動域の指定にあるシェイプ '" + nm + "' が、データにもメッシュにもありません", nm, null);
                else if (!expectedShapes.Contains(nm))
                {
                    // 件数が多いので 1 件にまとめる（先頭の数件だけ名前を出す）
                    if (ignoredCount < LimitIgnoredNamesShown) ignoredNames += (ignoredCount > 0 ? ", " : "") + nm;
                    ignoredCount++;
                }
            }
            if (ignoredCount > 0)
                Add(issues, FacialIssueSeverity.Info, FacialIssueKind.LimitIgnored,
                    "可動域の指定のうち " + ignoredCount + " 件は FC_ 以外のシェイプ向けのため Unity では使われません（" + ignoredNames
                    + (ignoredCount > LimitIgnoredNamesShown ? " ほか" : "") + "）", null, null);

            // --- FBX の取り込み設定（変更はしない。報告だけ） ---
            if (renderers != null)
            {
                var seenPaths = new HashSet<string>(StringComparer.Ordinal);
                for (int i = 0; i < renderers.Count; i++)
                {
                    Mesh m = renderers[i] != null ? renderers[i].sharedMesh : null;
                    if (m == null) continue;
                    string path = AssetDatabase.GetAssetPath(m);
                    if (string.IsNullOrEmpty(path) || !seenPaths.Add(path)) continue;
                    var mi = AssetImporter.GetAtPath(path) as ModelImporter;
                    if (mi == null) continue;
                    FacialIssue issue = CheckBlendShapeNormals(mi.importBlendShapeNormals, path);
                    if (issue != null) issues.Add(issue);
                }
            }
            return issues;
        }

        /// <summary>データだけで分かる構造の問題（メッシュ・ボーンは見ない）。データのインスペクターの「警告」に使う。</summary>
        public static List<FacialIssue> RunStructure(FacialCorrectionData data)
        {
            var issues = new List<FacialIssue>();
            if (data != null) AddStructureIssues(issues, data);
            return issues;
        }

        static void AddStructureIssues(List<FacialIssue> issues, FacialCorrectionData data)
        {
            FacialGridData g = data.grid;
            bool gridOk = g.cols > 0 && g.rows > 0;
            if (!gridOk)
                Add(issues, FacialIssueSeverity.Error, FacialIssueKind.InvalidGrid, "格子の大きさが不正です（列 " + g.cols + " × 行 " + g.rows + "）。Maya で出し直してください");
            FacialLayerData[] layers = data.layers ?? new FacialLayerData[0];
            if (layers.Length == 0 || !string.Equals(layers[0].name, "Neutral", StringComparison.Ordinal))
                Add(issues, FacialIssueSeverity.Error, FacialIssueKind.LayerZeroNotNeutral, "レイヤー 0 が Neutral ではありません。Maya で出し直してください");
            var names = new HashSet<string>(StringComparer.Ordinal);
            for (int i = 0; i < layers.Length; i++)
                if (!names.Add(layers[i].name ?? ""))
                    Add(issues, FacialIssueSeverity.Error, FacialIssueKind.DuplicateLayerName, "レイヤー名 '" + layers[i].name + "' が重複しています");
            if (gridOk)
            {
                int expected = g.cols * g.rows;
                for (int i = 0; i < layers.Length; i++)
                {
                    int have = layers[i].morphNames != null ? layers[i].morphNames.Length : 0;
                    if (have != expected)
                        Add(issues, FacialIssueSeverity.Error, FacialIssueKind.GridLayerMismatch,
                            "レイヤー '" + layers[i].name + "' の点の数（" + have + "）が格子（" + g.cols + " × " + g.rows + " = " + expected + "）と合いません。Maya で出し直してください");
                }
            }
        }

        /// <summary>Blend Shape Normals の設定が推奨と違うとき情報を返す（同じなら null）。</summary>
        public static FacialIssue CheckBlendShapeNormals(ModelImporterNormals current, string assetPath)
        {
            if (current == RecommendedBlendShapeNormals) return null;
            return new FacialIssue
            {
                Severity = FacialIssueSeverity.Info,
                Kind = FacialIssueKind.BlendShapeNormals,
                Message = "モデル '" + assetPath + "' の Blend Shape Normals は「" + NormalsLabel(current) + "」です。推奨は「"
                    + NormalsLabel(RecommendedBlendShapeNormals) + "」（Maya が再計算した法線をそのまま使う）。見た目が崩れるときはモデルの Import 設定を確認してください（自動では変えません）",
            };
        }

        public static string NormalsLabel(ModelImporterNormals n)
        {
            switch (n)
            {
                case ModelImporterNormals.Import: return "Import";
                case ModelImporterNormals.Calculate: return "Calculate";
                case ModelImporterNormals.None: return "None";
                default: return n.ToString();
            }
        }

        public static int Count(IReadOnlyList<FacialIssue> issues, FacialIssueSeverity severity)
        {
            int c = 0;
            for (int i = 0; i < issues.Count; i++) if (issues[i].Severity == severity) c++;
            return c;
        }

        static void Add(List<FacialIssue> list, FacialIssueSeverity sev, FacialIssueKind kind, string msg, string shape = null, string bone = null)
        {
            list.Add(new FacialIssue { Severity = sev, Kind = kind, Message = msg, ShapeName = shape, BoneName = bone });
        }
    }
}
