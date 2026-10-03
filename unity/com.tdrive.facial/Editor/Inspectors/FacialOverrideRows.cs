// 「調整」の行（Runner のインスペクターと FacialCorrectionOverrides のインスペクターで共用）。
// 行ごとに「取り込んだ値 | 上書きのチェック | 上書きの値」。SerializedObject 経由なので Undo が効き、アセットの保存対象になる。
using System;
using UnityEditor;
using UnityEngine;

namespace TDrive.Facial.Editor
{
    public static class FacialOverrideRows
    {
        sealed class Row
        {
            public string label, tooltip, flag, value;
            public Func<FacialEffectiveParams, float> get;
            public bool hasMin;
            public float min;
            public string format;
        }

        static readonly Row[] Rows =
        {
            new Row { label = "全体の強さ", tooltip = "補正全体にかかる強さ（0〜1）。0 で補正なし", flag = "overrideGlobalAlpha", value = "globalAlpha", get = p => p.globalAlpha, format = "0.###" },
            new Row { label = "追従の速さ", tooltip = "重みが目標へ追いつく速さ。大きいほど速い。0 以下 = 即時", flag = "overrideInterpSpeed", value = "interpSpeed", get = p => p.interpSpeed, hasMin = true, min = 0f, format = "0.##" },
            new Row { label = "スナップの角度（度）", tooltip = "視点がこの角度より大きく飛んだら、カット切り替えとみなして即時に反映する", flag = "overrideSnapAngle", value = "snapAngle", get = p => p.snapAngle, hasMin = true, min = 0f, format = "0.##" },
            new Row { label = "距離フェード 開始（m）", tooltip = "視点がこれより遠くなると補正が弱まり始める。開始 = 終了 = 0 で無効", flag = "overrideFadeStart", value = "fadeStart", get = p => p.fadeStart, hasMin = true, min = 0f, format = "0.###" },
            new Row { label = "距離フェード 終了（m）", tooltip = "これより遠いと補正 0。開始以下なら距離フェードなし", flag = "overrideFadeEnd", value = "fadeEnd", get = p => p.fadeEnd, hasMin = true, min = 0f, format = "0.###" },
            new Row { label = "表情での弱め具合", tooltip = "表情が強いとき補正を弱める度合い（0〜1）。0 = 弱めない", flag = "overrideExpressionDampen", value = "expressionDampen", get = p => p.expressionDampen, format = "0.###" },
            new Row { label = "端のフェード（度）", tooltip = "格子の範囲の外側で、補正が 0 へ消えていく幅。0 以下 = 範囲外は即 0", flag = "overrideEdgeFade", value = "edgeFade", get = p => p.edgeFade, hasMin = true, min = 0f, format = "0.##" },
            new Row { label = "シャープさ", tooltip = "キーの角度の近くで、キーのポーズそのものに寄せる度合い（既定 1 = 普通の補間）。大きいほど角度の中間でもキーのポーズに近くなり、小さいとなだらかに混ざる。値は Maya へ戻す JSON にも入ります", flag = "overrideSharpness", value = "sharpness", get = p => p.sharpness, hasMin = true, min = 0.01f, format = "0.###" },
            new Row { label = "コマ打ち fps", tooltip = "補正の更新をこの回数 / 秒に間引く（0 = 毎フレーム）。更新の間は前の重みのまま止まり、追従は使わず更新のたびに目標へ切り替わる。値は Maya へ戻す JSON にも入ります", flag = "overrideStepFps", value = "stepFps", get = p => p.stepFps, hasMin = true, min = 0f, format = "0.##" },
            new Row { label = "誇張（0〜1）", tooltip = "重み 1 を超えるポーズ（_Ex シェイプ）をどれだけ効かせるか。1 = 作った通り、0 = 1 までに収める。_Ex の無いキャラクターでは変化なし。値は Maya へ戻す JSON にも入ります", flag = "overrideExaggeration", value = "exaggeration", get = p => p.exaggeration, hasMin = true, min = 0f, format = "0.###" },
            new Row { label = "書き込む LOD の上限", tooltip = "補正のシェイプを書く LOD の上限。0 = 制限なし（すべての LOD に書く）、N = LOD N まで書き、それより粗い LOD の Renderer には書かない。LODGroup に入っていない Renderer は常に書く。値は Maya へ戻す JSON にも入ります", flag = "overrideMaxLod", value = "maxLod", get = p => p.maxLod, hasMin = true, min = 0f, format = "0" },
            new Row { label = "パース補正の強さ（0〜1）", tooltip = "広角で寄ったときの奥行きを押さえる補正（パース補正）の強さ。0 = 補正なし、1 = 作った通り。パース補正を使っていないキャラクターでは変化なし。取り込んだ値はキャラクターのデータの強さです", flag = "overridePerspectiveStrength", value = "perspectiveStrength", get = p => p.perspectiveStrength, hasMin = true, min = 0f, format = "0.###" },
            new Row { label = "リップシンクの強さ（0〜1）", tooltip = "口のシェイプ（リップシンク）を書く強さ。0 = 口を動かさない、1 = 対応表の通り。リップシンクを使っていないキャラクターでは変化なし", flag = "overrideLipSyncStrength", value = "lipSyncStrength", get = p => p.lipSyncStrength, hasMin = true, min = 0f, format = "0.###" },
            new Row { label = "リップシンクの追従（1/秒）", tooltip = "音素の強さ・声量が目標へ追いつく速さ。大きいほど速い。0 以下 = 即時", flag = "overrideLipSyncFollow", value = "lipSyncFollow", get = p => p.lipSyncFollow, hasMin = true, min = 0f, format = "0.##" },
        };

        /// <summary>行の一覧を描く。imported = 取り込んだ値（データが無ければ null で「—」）。値を変えたら true。</summary>
        public static bool Draw(SerializedObject overrides, FacialEffectiveParams? imported)
        {
            overrides.Update();
            EditorGUI.BeginChangeCheck();

            EditorGUILayout.BeginHorizontal();
            GUILayout.Label("項目", EditorStyles.miniBoldLabel, GUILayout.Width(150));
            GUILayout.Label("取り込み", EditorStyles.miniBoldLabel, GUILayout.Width(56));
            GUILayout.Label("上書き", EditorStyles.miniBoldLabel);
            EditorGUILayout.EndHorizontal();

            for (int i = 0; i < Rows.Length; i++)
            {
                Row row = Rows[i];
                SerializedProperty flag = overrides.FindProperty(row.flag);
                SerializedProperty value = overrides.FindProperty(row.value);
                if (flag == null || value == null) continue;
                EditorGUILayout.BeginHorizontal();
                GUILayout.Label(new GUIContent(row.label, row.tooltip), GUILayout.Width(150));
                GUILayout.Label(imported.HasValue ? row.get(imported.Value).ToString(row.format) : "—", GUILayout.Width(56));
                flag.boolValue = EditorGUILayout.ToggleLeft(new GUIContent("上書き", "チェックすると、右の値を使う（取り込んだ値は変わらない）"), flag.boolValue, GUILayout.Width(62));
                using (new EditorGUI.DisabledScope(!flag.boolValue))
                {
                    EditorGUILayout.PropertyField(value, GUIContent.none);
                    if (row.hasMin)
                    {
                        if (value.propertyType == SerializedPropertyType.Integer) { if (value.intValue < (int)row.min) value.intValue = (int)row.min; }
                        else if (value.floatValue < row.min) value.floatValue = row.min;
                    }
                }
                EditorGUILayout.EndHorizontal();
            }
            bool changed = EditorGUI.EndChangeCheck();
            overrides.ApplyModifiedProperties(); // Undo に積まれ、アセットが dirty になる
            return changed;
        }
    }
}
