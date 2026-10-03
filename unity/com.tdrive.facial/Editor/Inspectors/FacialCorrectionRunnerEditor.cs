// Runner のインスペクター（デザイナー向けのパネル）。
//  プレビュー / データ・対象 / 状態 / 調整（上書き）/ 感情の重み / 視点 / マテリアル出力 / 検証 / ボタン
// 書くのは Runner の公開フィールドと調整用アセットだけ。プレビューの重みは保存されない（FacialPreviewDriver 参照）。
using System.Collections.Generic;
using System.IO;
using TDrive.Facial.Core;
using UnityEditor;
using UnityEngine;

namespace TDrive.Facial.Editor
{
    [CustomEditor(typeof(FacialCorrectionRunner))]
    public sealed class FacialCorrectionRunnerEditor : UnityEditor.Editor
    {
        static readonly string[] HandledByPanel =
        {
            "m_Script", "viewerOverride", "useManualAngles", "manualYaw", "manualPitch",
            "emotionWeights", "mutedLayers", "materialOutput", "materialTargets",
        };

        bool _foldTargets, _foldMissing, _foldWeights;
        bool _foldTuning = true, _foldEmotion = true, _foldViewer = true, _foldMaterial, _foldValidation = true;
        List<FacialIssue> _issues;
        FacialCorrectionData _issuesData;
        readonly List<MorphWeight> _weights = new List<MorphWeight>(64);

        public override bool RequiresConstantRepaint()
        {
            var r = (FacialCorrectionRunner)target;
            return Application.isPlaying || FacialPreviewDriver.IsOn(r);
        }

        void OnEnable() { _issues = null; }

        public override void OnInspectorGUI()
        {
            var r = (FacialCorrectionRunner)target;
            serializedObject.Update();

            DrawPreview(r);

            EditorGUILayout.Space();
            EditorGUILayout.LabelField("データと対象", EditorStyles.boldLabel);
            DrawPropertiesExcluding(serializedObject, HandledByPanel);
            serializedObject.ApplyModifiedProperties();

            if (r.data == null)
            {
                EditorGUILayout.HelpBox("データ（FacialCorrectionData）が未設定です。.fcpose を取り込んだアセットを入れてください。", MessageType.Warning);
            }
            else
            {
                DrawState(r);
                DrawTuning(r);
                DrawEmotion(r);
                DrawViewer();
                DrawMaterial(r);
                DrawValidation(r);
            }
            DrawButtons(r);
        }

        // ---------------------------------------------------------------- プレビュー

        void DrawPreview(FacialCorrectionRunner r)
        {
            EditorGUILayout.LabelField("プレビュー（再生しなくても確認）", EditorStyles.boldLabel);
            if (Application.isPlaying)
            {
                EditorGUILayout.HelpBox("再生中は Runner が自動で動くので、プレビューは使いません。", MessageType.None);
                return;
            }
            bool on = FacialPreviewDriver.IsOn(r);
            using (new EditorGUI.DisabledScope(r.data == null))
            {
                bool now = GUILayout.Toggle(on, on ? "プレビュー: オン（クリックでオフ）" : "プレビュー: オフ（クリックでオン）", "Button", GUILayout.Height(24));
                if (now != on) FacialPreviewDriver.SetOn(r, now);
            }
            FacialPreviewState st = FacialPreviewDriver.GetState(r);
            if (!on)
            {
                EditorGUILayout.LabelField("オンにすると、Scene ビューのカメラの角度で補正が掛かります。オフにする・保存する・再生に入ると元の値に戻ります（シーンは変更されません）。", EditorStyles.wordWrappedMiniLabel);
                return;
            }
            EditorGUILayout.BeginHorizontal();
            if (GUILayout.Toggle(st.correction, "補正あり", "ButtonLeft") != st.correction) { st.correction = true; st.forceEval = true; }
            if (GUILayout.Toggle(!st.correction, "補正なし", "ButtonRight") == st.correction) { st.correction = false; st.forceEval = true; }
            EditorGUILayout.EndHorizontal();
            EditorGUILayout.LabelField("A/B: 補正ありと補正なしを切り替えて比べられます", EditorStyles.miniLabel);

            st.mode = (FacialPreviewViewMode)EditorGUILayout.EnumPopup(new GUIContent("視点", "補正の角度を決める視点"), st.mode);
            FacialGridData g = r.data != null ? r.data.grid : default(FacialGridData);
            switch (st.mode)
            {
                case FacialPreviewViewMode.SceneCamera:
                    if (r.useManualAngles)
                        EditorGUILayout.HelpBox("Runner の「手動の角度を使う」がオンなので、Scene ビューのカメラは使われません。", MessageType.Info);
                    break;
                case FacialPreviewViewMode.Turntable:
                    st.turntableSpeed = EditorGUILayout.Slider(new GUIContent("回る速さ（度/秒）", "仮想の視点が基準ボーンのまわりを回る速さ"), st.turntableSpeed, -180f, 180f);
                    st.pitch = EditorGUILayout.Slider(new GUIContent("Pitch（度）", "カメラが上で正"), st.pitch, -Mathf.Max(1f, g.pitchRange), Mathf.Max(1f, g.pitchRange));
                    st.distance = EditorGUILayout.Slider(new GUIContent("距離（m）", "基準ボーンから仮想の視点までの距離"), st.distance, 0.1f, 10f);
                    break;
                case FacialPreviewViewMode.Manual:
                    st.yaw = EditorGUILayout.Slider(new GUIContent("Yaw（度）", "カメラがキャラクターの左で正"), st.yaw, -180f, 180f);
                    st.pitch = EditorGUILayout.Slider(new GUIContent("Pitch（度）", "カメラが上で正"), st.pitch, -Mathf.Max(1f, g.pitchRange), Mathf.Max(1f, g.pitchRange));
                    st.distance = EditorGUILayout.Slider(new GUIContent("距離（m）", "基準ボーンから仮想の視点までの距離"), st.distance, 0.1f, 10f);
                    break;
            }
            if (GUI.changed) st.forceEval = true;
            if (GUILayout.Button("格子ビューアを開く")) FacialGridWindow.Open(r);
        }

        // ---------------------------------------------------------------- 状態

        void DrawState(FacialCorrectionRunner r)
        {
            EditorGUILayout.Space();
            EditorGUILayout.LabelField("状態", EditorStyles.boldLabel);
            IReadOnlyList<string> missing = r.GetMissingShapeNames();
            IReadOnlyList<SkinnedMeshRenderer> targets = r.ResolvedTargets;
            EditorGUILayout.LabelField("対象メッシュ", targets.Count.ToString());
            if (targets.Count > 0)
            {
                _foldTargets = EditorGUILayout.Foldout(_foldTargets, "対象メッシュの名前", true);
                if (_foldTargets)
                    for (int i = 0; i < targets.Count; i++) EditorGUILayout.LabelField("  " + (targets[i] != null ? targets[i].name : "（なし）"));
            }
            EditorGUILayout.LabelField("基準ボーン", r.ResolvedBaseBone != null ? r.ResolvedBaseBone.name : "（見つからない）");
            EditorGUILayout.LabelField("見つかったシェイプ", r.BoundShapeCount.ToString());
            EditorGUILayout.LabelField("足りないシェイプ", missing.Count.ToString());
            if (missing.Count > 0)
            {
                EditorGUILayout.HelpBox("データにあるのにメッシュに無いシェイプは飛ばされます（FBX を出し直してください）。", MessageType.Info);
                _foldMissing = EditorGUILayout.Foldout(_foldMissing, "足りないシェイプの名前", true);
                if (_foldMissing)
                    for (int i = 0; i < missing.Count && i < 30; i++) EditorGUILayout.LabelField("  " + missing[i]);
            }
            EditorGUILayout.LabelField("Yaw / Pitch", r.CurrentYaw.ToString("F1") + " / " + r.CurrentPitch.ToString("F1") + (r.Snapped ? "  （スナップ）" : ""));
            EditorGUILayout.LabelField("倍率（表情 × 距離 × 強さ）", r.LastScale.ToString("F2"));
            EditorGUILayout.LabelField("書いているシェイプ数", r.ActiveWeightCount.ToString());
            if (r.ActiveWeightCount > 0)
            {
                _foldWeights = EditorGUILayout.Foldout(_foldWeights, "今の重み（0〜100）", true);
                if (_foldWeights)
                {
                    r.GetActiveWeights(_weights);
                    for (int i = 0; i < _weights.Count && i < 30; i++)
                        EditorGUILayout.LabelField("  " + _weights[i].MorphName, (_weights[i].Weight * 100.0).ToString("F1"));
                }
            }
        }

        // ---------------------------------------------------------------- 調整

        void DrawTuning(FacialCorrectionRunner r)
        {
            EditorGUILayout.Space();
            _foldTuning = EditorGUILayout.Foldout(_foldTuning, "調整（簡単なパラメータ）", true, EditorStyles.foldoutHeader);
            if (!_foldTuning) return;
            if (r.overrides == null)
            {
                EditorGUILayout.HelpBox("値を変えて保存したいときは、調整用アセットを作って割り当てます。取り込んだデータは変わらず、取り込み直しても調整は残ります。", MessageType.Info);
                if (GUILayout.Button("調整用アセットを作る…")) CreateOverridesAsset(r);
                return;
            }
            FacialEffectiveParams imported = FacialCorrectionOverrides.Resolve(r.data, null);
            if (FacialOverrideRows.Draw(new SerializedObject(r.overrides), imported)) RepaintPreview(r);
            EditorGUILayout.LabelField("調整はアセット '" + r.overrides.name + "' に保存されます（Undo できます）。", EditorStyles.miniLabel);
        }

        static void CreateOverridesAsset(FacialCorrectionRunner r)
        {
            string dataPath = AssetDatabase.GetAssetPath(r.data);
            string dir = string.IsNullOrEmpty(dataPath) ? "Assets" : Path.GetDirectoryName(dataPath).Replace('\\', '/');
            string name = (string.IsNullOrEmpty(r.data.assetName) ? "Facial" : r.data.assetName) + "_Overrides";
            // ユーザーが保存先を選んで決定したときだけプロジェクトへ書く
            string path = EditorUtility.SaveFilePanelInProject("調整用アセットを作る", name, "asset", "調整値を保存するアセットの場所を選んでください", dir);
            if (string.IsNullOrEmpty(path)) return;
            var ov = CreateInstance<FacialCorrectionOverrides>();
            AssetDatabase.CreateAsset(ov, path);
            Undo.RecordObject(r, "調整用アセットを割り当て");
            r.overrides = ov;
            EditorUtility.SetDirty(r);
            AssetDatabase.SaveAssets();
        }

        static void RepaintPreview(FacialCorrectionRunner r)
        {
            FacialPreviewState st = FacialPreviewDriver.GetState(r);
            st.forceEval = true;
        }

        // ---------------------------------------------------------------- 感情

        void DrawEmotion(FacialCorrectionRunner r)
        {
            EditorGUILayout.Space();
            _foldEmotion = EditorGUILayout.Foldout(_foldEmotion, "感情の重み", true, EditorStyles.foldoutHeader);
            if (!_foldEmotion) return;
            FacialLayerData[] layers = r.data.layers;
            if (layers == null || layers.Length <= 1)
            {
                EditorGUILayout.LabelField("感情レイヤーはありません（Neutral のみ）", EditorStyles.miniLabel);
                return;
            }
            for (int i = 1; i < layers.Length; i++)
            {
                float w = r.emotionWeights != null && i < r.emotionWeights.Length ? r.emotionWeights[i] : 0f;
                bool muted = r.mutedLayers != null && i < r.mutedLayers.Length && r.mutedLayers[i];
                EditorGUILayout.BeginHorizontal();
                EditorGUI.BeginChangeCheck();
                float nw = EditorGUILayout.Slider(new GUIContent(layers[i].name, "このレイヤーの感情の重み（0〜1）"), w, 0f, 1f);
                bool nm = GUILayout.Toggle(muted, new GUIContent("ミュート", "オンにするとこのレイヤーを 0 として扱う"), GUILayout.Width(64));
                if (EditorGUI.EndChangeCheck())
                {
                    Undo.RecordObject(r, "感情の重み");
                    if (nw != w) r.SetEmotionWeight(i, nw);
                    if (nm != muted) r.SetLayerMuted(i, nm);
                    EditorUtility.SetDirty(r);
                    PrefabUtility.RecordPrefabInstancePropertyModifications(r);
                    RepaintPreview(r);
                }
                EditorGUILayout.EndHorizontal();
            }
        }

        // ---------------------------------------------------------------- 視点

        void DrawViewer()
        {
            EditorGUILayout.Space();
            _foldViewer = EditorGUILayout.Foldout(_foldViewer, "視点", true, EditorStyles.foldoutHeader);
            if (!_foldViewer) return;
            serializedObject.Update();
            EditorGUILayout.PropertyField(serializedObject.FindProperty("viewerOverride"));
            SerializedProperty manual = serializedObject.FindProperty("useManualAngles");
            EditorGUILayout.PropertyField(manual);
            if (manual.boolValue)
            {
                EditorGUILayout.PropertyField(serializedObject.FindProperty("manualYaw"));
                EditorGUILayout.PropertyField(serializedObject.FindProperty("manualPitch"));
            }
            if (serializedObject.ApplyModifiedProperties()) RepaintPreview((FacialCorrectionRunner)target);
        }

        // ---------------------------------------------------------------- マテリアル出力

        void DrawMaterial(FacialCorrectionRunner r)
        {
            EditorGUILayout.Space();
            _foldMaterial = EditorGUILayout.Foldout(_foldMaterial, "マテリアル出力（任意）", true, EditorStyles.foldoutHeader);
            if (!_foldMaterial) return;
            serializedObject.Update();
            EditorGUILayout.PropertyField(serializedObject.FindProperty("materialOutput"));
            EditorGUILayout.PropertyField(serializedObject.FindProperty("materialTargets"), true);
            serializedObject.ApplyModifiedProperties();
            EditorGUILayout.LabelField("今の状態", r.MaterialOutputActive ? "出力する（_FC_Angles / _FC_Emotion*）" : "出力しない");
        }

        // ---------------------------------------------------------------- 検証

        void DrawValidation(FacialCorrectionRunner r)
        {
            EditorGUILayout.Space();
            if (_issues == null || _issuesData != r.data)
            {
                _issues = FacialValidation.Run(r);
                _issuesData = r.data;
            }
            int e = FacialValidation.Count(_issues, FacialIssueSeverity.Error);
            int w = FacialValidation.Count(_issues, FacialIssueSeverity.Warning);
            int n = FacialValidation.Count(_issues, FacialIssueSeverity.Info);
            _foldValidation = EditorGUILayout.Foldout(_foldValidation, "検証（エラー " + e + " / 警告 " + w + " / 情報 " + n + "）", true, EditorStyles.foldoutHeader);
            if (!_foldValidation) return;
            if (_issues.Count == 0) EditorGUILayout.HelpBox("問題は見つかりませんでした。", MessageType.None);
            for (int i = 0; i < _issues.Count && i < 40; i++)
                EditorGUILayout.HelpBox(_issues[i].Message, ToMessageType(_issues[i].Severity));
            if (GUILayout.Button("もう一度検証する")) _issues = null;
        }

        public static MessageType ToMessageType(FacialIssueSeverity s)
        {
            switch (s)
            {
                case FacialIssueSeverity.Error: return MessageType.Error;
                case FacialIssueSeverity.Warning: return MessageType.Warning;
                default: return MessageType.Info;
            }
        }

        // ---------------------------------------------------------------- ボタン

        void DrawButtons(FacialCorrectionRunner r)
        {
            EditorGUILayout.Space();
            if (GUILayout.Button(new GUIContent("FC_* を 0 に戻す", "書いた補正シェイプ（FC_*）を 0 に戻す。次の更新でまた掛かります"))) r.ResetWeights();
            if (GUILayout.Button(new GUIContent("キャッシュを作り直す", "シェイプ名 → 番号・対象メッシュ・基準ボーンの解決をやり直す（メッシュやボーンを変えたとき）")))
            {
                r.RebuildCaches();
                _issues = null;
            }
            using (new EditorGUI.DisabledScope(r.data == null))
            {
                if (GUILayout.Button(new GUIContent("Maya へ戻す JSON を書き出す…", "今の調整値（policy / quality）を、元データの単位に戻して JSON に書く。Maya で読み込めます")))
                    ExportJson(r);
            }
        }

        static void ExportJson(FacialCorrectionRunner r)
        {
            string name = (string.IsNullOrEmpty(r.data.assetName) ? "facial" : r.data.assetName) + "_tuning.json";
            string path = EditorUtility.SaveFilePanel("Maya へ戻す JSON を書き出す", "", name, "json");
            if (string.IsNullOrEmpty(path)) return;
            File.WriteAllText(path, FacialMayaExport.BuildJson(r.data, r.overrides));
            Debug.Log("[Facial] 調整値を書き出しました（単位: " + FacialMayaExport.SourceUnit(r.data) + "）: " + path);
        }
    }
}
