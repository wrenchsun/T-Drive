// 取り込んだデータ（FacialCorrectionData）とは別に持つ調整値。取り込み直しても消えない。実行時は読み取り専用。
// 各項目は「上書きする」のフラグ付き。フラグが false の項目は FacialCorrectionData の値を使う。
using System;
using UnityEngine;

namespace TDrive.Facial
{
    /// <summary>データと上書きを合成した、実効の値（毎フレームの計算に使う。割り当てなし）。</summary>
    public struct FacialEffectiveParams
    {
        public float globalAlpha;
        public float interpSpeed;
        public float snapAngle;
        public float fadeStart;
        public float fadeEnd;
        public float expressionDampen;
        public float edgeFade;
        public float sharpness;
        public float stepFps;
        public float exaggeration;
    }

    [CreateAssetMenu(menuName = "T-Drive/Facial Correction Overrides", fileName = "FacialCorrectionOverrides", order = 901)]
    public sealed class FacialCorrectionOverrides : ScriptableObject
    {
        [Header("全体")]
        [Tooltip("全体の強さを上書きする")] public bool overrideGlobalAlpha;
        [Tooltip("全体の強さ（0〜1）")] [Range(0f, 1f)] public float globalAlpha = 1f;

        [Header("動き")]
        [Tooltip("追従の速さを上書きする")] public bool overrideInterpSpeed;
        [Tooltip("重みの追従の速さ。0 以下 = 即時")] public float interpSpeed = 10f;
        [Tooltip("スナップの角度を上書きする")] public bool overrideSnapAngle;
        [Tooltip("角度がこの値（度）を超えて飛んだら即時反映")] public float snapAngle = 45f;

        [Header("距離フェード")]
        [Tooltip("距離フェードの開始距離を上書きする")] public bool overrideFadeStart;
        [Tooltip("距離フェードの開始距離（m）")] public float fadeStart;
        [Tooltip("距離フェードの終了距離を上書きする")] public bool overrideFadeEnd;
        [Tooltip("距離フェードの終了距離（m）。開始以下 = 無効")] public float fadeEnd;

        [Header("表情・端")]
        [Tooltip("表情での弱め具合を上書きする")] public bool overrideExpressionDampen;
        [Tooltip("表情が強いとき補正を弱める度合い（0〜1）")] [Range(0f, 1f)] public float expressionDampen = 0.5f;
        [Tooltip("端のフェードを上書きする")] public bool overrideEdgeFade;
        [Tooltip("範囲の外側で補正が 0 へ減衰する幅（度）")] public float edgeFade = 15f;

        [Header("品質")]
        [Tooltip("シャープさを上書きする")] public bool overrideSharpness;
        [Tooltip("キー角度のシャープさ（既定 1）。大きいほどキーの角度の近くでキーのポーズに寄る")] public float sharpness = 1f;
        [Tooltip("コマ打ちの fps を上書きする")] public bool overrideStepFps;
        [Tooltip("コマ打ちの fps（0 = 毎フレーム）。補正の更新をこの回数 / 秒に間引く")] public float stepFps;
        [Tooltip("誇張を上書きする")] public bool overrideExaggeration;
        [Tooltip("誇張（_Ex シェイプ）の強さ（0〜1）。1 = 作った通り、0 = 誇張なし")] [Range(0f, 1f)] public float exaggeration = 1f;

        /// <summary>data と overrides（null 可）から実効の値を求める。data が null なら既定値。</summary>
        public static FacialEffectiveParams Resolve(FacialCorrectionData data, FacialCorrectionOverrides ov)
        {
            var p = new FacialEffectiveParams();
            if (data != null)
            {
                p.globalAlpha = data.policy.globalAlpha;
                p.interpSpeed = data.policy.interpSpeed;
                p.snapAngle = data.policy.snapAngle;
                p.fadeStart = data.policy.fadeStart;
                p.fadeEnd = data.policy.fadeEnd;
                p.expressionDampen = data.policy.expressionDampen;
                p.edgeFade = data.grid.edgeFade;
                p.sharpness = data.quality.sharpness;
                p.stepFps = data.quality.stepFps;
                p.exaggeration = data.quality.hasExaggeration ? Mathf.Clamp01(data.quality.exaggeration) : 1f;
            }
            else
            {
                p.globalAlpha = 1f; p.interpSpeed = 10f; p.snapAngle = 45f; p.expressionDampen = 0.5f;
                p.edgeFade = 15f; p.sharpness = 1f; p.exaggeration = 1f;
            }
            if (ov == null) return p;
            if (ov.overrideGlobalAlpha) p.globalAlpha = ov.globalAlpha;
            if (ov.overrideInterpSpeed) p.interpSpeed = ov.interpSpeed;
            if (ov.overrideSnapAngle) p.snapAngle = ov.snapAngle;
            if (ov.overrideFadeStart) p.fadeStart = ov.fadeStart;
            if (ov.overrideFadeEnd) p.fadeEnd = ov.fadeEnd;
            if (ov.overrideExpressionDampen) p.expressionDampen = ov.expressionDampen;
            if (ov.overrideEdgeFade) p.edgeFade = ov.edgeFade;
            if (ov.overrideSharpness) p.sharpness = ov.sharpness;
            if (ov.overrideStepFps) p.stepFps = ov.stepFps;
            if (ov.overrideExaggeration) p.exaggeration = Mathf.Clamp01(ov.exaggeration);
            return p;
        }
    }
}
