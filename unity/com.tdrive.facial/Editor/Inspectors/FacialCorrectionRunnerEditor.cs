// Runner のインスペクター。標準の欄の下に、解決した状態（角度・足りないシェイプ）と「FC_* を 0 に戻す」を出す。
// プレビューの窓・格子ビューアは別チケット（FU-5）。
using UnityEditor;
using UnityEngine;

namespace TDrive.Facial.Editor
{
    [CustomEditor(typeof(FacialCorrectionRunner))]
    public sealed class FacialCorrectionRunnerEditor : UnityEditor.Editor
    {
        bool _showMissing;

        public override bool RequiresConstantRepaint() { return Application.isPlaying; }

        public override void OnInspectorGUI()
        {
            DrawDefaultInspector();
            var r = (FacialCorrectionRunner)target;

            EditorGUILayout.Space();
            EditorGUILayout.LabelField("状態", EditorStyles.boldLabel);
            if (r.data == null)
            {
                EditorGUILayout.HelpBox("データ（FacialCorrectionData）が未設定です。.fcpose を取り込んだアセットを入れてください。", MessageType.Warning);
            }
            else
            {
                var missing = r.GetMissingShapeNames();
                EditorGUILayout.LabelField("対象メッシュ", r.ResolvedTargets.Count.ToString());
                EditorGUILayout.LabelField("基準ボーン", r.ResolvedBaseBone != null ? r.ResolvedBaseBone.name : "（見つからない）");
                EditorGUILayout.LabelField("見つかったシェイプ", r.BoundShapeCount.ToString());
                EditorGUILayout.LabelField("足りないシェイプ", missing.Count.ToString());
                if (missing.Count > 0)
                {
                    EditorGUILayout.HelpBox("データにあるのにメッシュに無いシェイプは飛ばされます（FBX を出し直してください）。", MessageType.Info);
                    _showMissing = EditorGUILayout.Foldout(_showMissing, "足りないシェイプの名前", true);
                    if (_showMissing)
                        for (int i = 0; i < missing.Count && i < 30; i++) EditorGUILayout.LabelField("  " + missing[i]);
                }
                EditorGUILayout.LabelField("Yaw / Pitch", r.CurrentYaw.ToString("F1") + " / " + r.CurrentPitch.ToString("F1")
                    + (r.Snapped ? "  （スナップ）" : ""));
                EditorGUILayout.LabelField("書いているシェイプ数", r.ActiveWeightCount.ToString());
            }

            if (GUILayout.Button("FC_* を 0 に戻す")) r.ResetWeights();
        }
    }
}
