// Timeline のクリップのインスペクター（デザイナー向けの日本語ラベル）。編集時のみ。
// 値は FacialCorrectionBehaviour（テンプレート）。ラベルとツールチップだけを日本語にした表示で、保存される内容は変わらない。
#if UNITY_EDITOR
using UnityEditor;
using UnityEngine;

namespace TDrive.Facial.Timeline
{
    [CustomEditor(typeof(FacialCorrectionClip))]
    public sealed class FacialCorrectionClipEditor : UnityEditor.Editor
    {
        public override void OnInspectorGUI()
        {
            serializedObject.Update();
            SerializedProperty t = serializedObject.FindProperty("template");
            if (t == null) { DrawDefaultInspector(); return; }

            EditorGUILayout.HelpBox("重なったクリップは重みでブレンドされます。クリップが無い間は、Runner の設定どおりの通常の補正です。", MessageType.None);

            SerializedProperty tr = serializedObject.FindProperty("track");
            if (tr != null)
            {
                Section("Maya の演出カーブ（.fctrack）");
                EditorGUILayout.PropertyField(tr, new GUIContent("演出カーブ", "Maya のショットで打った強さ・感情の重み・角度の固定のカーブ（.fctrack）。クリップの時間に合わせて動く。下で手で入れた値は曲線より優先される。空なら使わない"));
                if (tr.objectReferenceValue != null)
                    EditorGUILayout.HelpBox("曲線は、下の「強さを使う」「角度を固定する」がオフの項目と、手で書いていない感情レイヤーに効きます。手で入れた値は曲線を上書きします。", MessageType.None);
            }

            Section("強さ");
            Toggle(t, "useAlpha", "強さを使う", "オンのとき、下の「強さ」で補正全体を弱める / 切る");
            using (new EditorGUI.DisabledScope(!t.FindPropertyRelative("useAlpha").boolValue))
                Field(t, "alpha", "強さ（0〜1）", "補正全体に掛ける倍率。0 でこのクリップの間は補正なし");

            Section("感情の重み");
            Field(t, "emotions", "感情の重み（レイヤー名と重み）", "表情に合わせた補正の切り替え。レイヤー名は Runner のデータのレイヤー名と同じ綴りにする。書いていないレイヤーは Runner の値のまま");

            Section("角度の固定");
            Toggle(t, "fixAngles", "角度を固定する", "オンのとき、カメラの位置に関わらず下の Yaw / Pitch を補正の角度として使う（決め構図用）");
            using (new EditorGUI.DisabledScope(!t.FindPropertyRelative("fixAngles").boolValue))
            {
                Field(t, "yaw", "Yaw（度）", "キャラクターの正面 = 0、カメラがキャラクターから見て左で正");
                Field(t, "pitch", "Pitch（度）", "カメラが上（ふかん）で正");
            }

            Section("視点");
            Field(t, "viewer", "視点（空 = 既定）", "補正の基準にするカメラなどの Transform（シーン内のオブジェクト）。空なら Runner の設定・メインカメラに従う");

            Section("カット補正");
            Field(t, "pose", "ポーズ", "このクリップの間だけ加算するポーズ（.fcpose から取り込んだもの）。元のデータは書き換わらない。空なら使わない");
            Field(t, "poseWeight", "ポーズの重み（0〜1）", "カット補正の重み");

            Section("コマ打ち");
            Toggle(t, "useStepFps", "コマ打ちを使う", "オンのとき、このクリップの間だけ補正の更新 fps を変える（重なったときは重みの大きいクリップの値）");
            using (new EditorGUI.DisabledScope(!t.FindPropertyRelative("useStepFps").boolValue))
                Field(t, "stepFps", "fps", "補正の更新 fps（0 = 毎フレーム）。更新の間は前の重みのまま止まり、追従は使わず更新のたびに目標へ切り替わる");

            Section("誇張");
            Toggle(t, "useExaggeration", "誇張を使う", "オンのとき、このクリップの間だけ誇張（_Ex シェイプ）の強さを下の値にする。オフのときは .fctrack の誇張のカーブに従う（無ければ変えない）");
            using (new EditorGUI.DisabledScope(!t.FindPropertyRelative("useExaggeration").boolValue))
                Field(t, "exaggeration", "誇張（0〜1）", "1 = 作った通り、0 = 誇張なし。_Ex の無いキャラクターでは変化なし。重なったクリップは重みで混ざる");

            Section("パース補正");
            Toggle(t, "usePerspective", "パース補正を使う", "オンのとき、このクリップの間だけパース補正の強さを下の値にする。オフのときは .fctrack のパース補正のカーブに従う（無ければ変えない）");
            using (new EditorGUI.DisabledScope(!t.FindPropertyRelative("usePerspective").boolValue))
                Field(t, "perspective", "パース補正の強さ（0〜1）", "1 = 作った通り、0 = 補正なし。パース補正を使っていないキャラクターでは変化なし。重なったクリップは重みで混ざる");

            serializedObject.ApplyModifiedProperties();
        }

        static void Section(string title)
        {
            EditorGUILayout.Space();
            EditorGUILayout.LabelField(title, EditorStyles.boldLabel);
        }

        static void Field(SerializedProperty parent, string name, string label, string tooltip)
        {
            SerializedProperty p = parent.FindPropertyRelative(name);
            if (p != null) EditorGUILayout.PropertyField(p, new GUIContent(label, tooltip), true);
        }

        static void Toggle(SerializedProperty parent, string name, string label, string tooltip)
        {
            SerializedProperty p = parent.FindPropertyRelative(name);
            if (p != null) p.boolValue = EditorGUILayout.ToggleLeft(new GUIContent(label, tooltip), p.boolValue);
        }
    }
}
#endif
