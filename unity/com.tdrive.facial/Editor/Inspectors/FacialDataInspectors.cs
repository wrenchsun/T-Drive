// FacialCorrectionData（読み取り専用の要約）と FacialCorrectionOverrides（調整の行）のインスペクター。
// データは Maya の出力から作られるので、ここでは編集しない。取り込み設定（.fcpose を選んだとき）にも同じ要約を出す。
using System.Collections.Generic;
using TDrive.Facial.Core;
using UnityEditor;
using UnityEditor.AssetImporters;
using UnityEngine;

namespace TDrive.Facial.Editor
{
    /// <summary>データの要約の描画（共用）。</summary>
    public static class FacialDataSummaryGui
    {
        public static void Draw(FacialCorrectionData d)
        {
            if (d == null) return;
            EditorGUILayout.LabelField("取り込んだデータ（読み取り専用）", EditorStyles.boldLabel);
            Row("アセット名", d.assetName);
            Row("ベイク先のメッシュ", string.IsNullOrEmpty(d.targetMesh) ? "（未指定）" : d.targetMesh);
            Row("基準ボーン", d.grid.baseBone);

            EditorGUILayout.Space(4);
            EditorGUILayout.LabelField("格子", EditorStyles.boldLabel);
            Row("大きさ", d.grid.cols + " 列 × " + d.grid.rows + " 行（" + d.PointCount + " 点）");
            Row("Yaw の範囲", "±" + d.grid.yawRange.ToString("0.##") + " 度");
            Row("Pitch の範囲", "±" + d.grid.pitchRange.ToString("0.##") + " 度");
            Row("端のフェード", d.grid.edgeFade.ToString("0.##") + " 度");
            Row("正面の軸（Unity）", d.grid.forwardAxis);

            EditorGUILayout.Space(4);
            EditorGUILayout.LabelField("レイヤー（焼いた点の数 / 全体）", EditorStyles.boldLabel);
            FacialLayerData[] layers = d.layers ?? new FacialLayerData[0];
            for (int i = 0; i < layers.Length; i++)
            {
                int baked = 0, total = layers[i].morphNames != null ? layers[i].morphNames.Length : 0;
                for (int k = 0; k < total; k++) if (!string.IsNullOrEmpty(layers[i].morphNames[k])) baked++;
                Row((i == 0 ? "0 " : i + " ") + layers[i].name + (layers[i].enabled ? "" : "（無効）"), baked + " / " + total);
            }

            EditorGUILayout.Space(4);
            EditorGUILayout.LabelField("動きの方針", EditorStyles.boldLabel);
            Row("全体の強さ", d.policy.globalAlpha.ToString("0.###"));
            Row("追従の速さ", d.policy.interpSpeed.ToString("0.##"));
            Row("スナップの角度", d.policy.snapAngle.ToString("0.##") + " 度");
            Row("距離フェード", d.policy.fadeEnd > d.policy.fadeStart ? d.policy.fadeStart.ToString("0.###") + " 〜 " + d.policy.fadeEnd.ToString("0.###") + " m" : "なし");
            Row("表情での弱め具合", d.policy.expressionDampen.ToString("0.###"));
            Row("マテリアル出力", d.MaterialModeValue == FacialMaterialMode.PropertyBlock ? "PropertyBlock" : "なし");

            EditorGUILayout.Space(4);
            EditorGUILayout.LabelField("元のファイル（.fcpose）", EditorStyles.boldLabel);
            Row("書き出したツール", string.IsNullOrEmpty(d.source.source) ? "（不明）" : d.source.source);
            Row("単位 / 上軸 / 系", Or(d.source.unit) + " / " + Or(d.source.upAxis) + " / " + Or(d.source.handedness));
            Row("バージョン", d.source.version.ToString());
            Row("プロファイル", Or(d.source.profile));

            List<FacialIssue> issues = FacialValidation.RunStructure(d);
            EditorGUILayout.Space(4);
            EditorGUILayout.LabelField("警告", EditorStyles.boldLabel);
            if (issues.Count == 0) EditorGUILayout.LabelField("データの構造の問題はありません", EditorStyles.miniLabel);
            for (int i = 0; i < issues.Count; i++)
                EditorGUILayout.HelpBox(issues[i].Message, FacialCorrectionRunnerEditor.ToMessageType(issues[i].Severity));
            EditorGUILayout.LabelField("メッシュとの突き合わせは、Runner のインスペクターの「検証」で行います。", EditorStyles.wordWrappedMiniLabel);
        }

        static string Or(string s) { return string.IsNullOrEmpty(s) ? "—" : s; }

        static void Row(string label, string value)
        {
            EditorGUILayout.LabelField(label, value);
        }
    }

    [CustomEditor(typeof(FacialCorrectionData))]
    public sealed class FacialCorrectionDataEditor : UnityEditor.Editor
    {
        public override void OnInspectorGUI()
        {
            FacialDataSummaryGui.Draw((FacialCorrectionData)target);
        }
    }

    [CustomEditor(typeof(FacialCorrectionOverrides))]
    public sealed class FacialCorrectionOverridesEditor : UnityEditor.Editor
    {
        public override void OnInspectorGUI()
        {
            EditorGUILayout.HelpBox("取り込んだデータの値を上書きする調整値です。「上書き」にチェックした行だけが使われます。取り込み直しても残ります。", MessageType.Info);
            FacialOverrideRows.Draw(serializedObject, null);
        }
    }

    /// <summary>.fcpose を選んだときの取り込み設定。要約を見せる（取り込み設定そのものは無い）。</summary>
    [CustomEditor(typeof(FcposeImporter))]
    public sealed class FcposeImporterEditor : ScriptedImporterEditor
    {
        public override void OnInspectorGUI()
        {
            var data = assetTarget as FacialCorrectionData;
            if (data != null) FacialDataSummaryGui.Draw(data);
            else EditorGUILayout.HelpBox("FacialPose（1 点分のポーズ）または読み込めないファイルです。", MessageType.None);
            ApplyRevertGUI();
        }
    }
}
