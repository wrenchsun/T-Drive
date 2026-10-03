// クリップ 1 つ分のデザイナー値（テンプレート）。Timeline がクリップごとに複製して Mixer へ渡す。
// 値の意味は docs/14 §7.1。ブレンドは FacialCorrectionMixerBehaviour。
using System;
using UnityEngine;
using UnityEngine.Playables;

namespace TDrive.Facial.Timeline
{
    /// <summary>感情レイヤー 1 つ分の重み（レイヤー名で指定。Runner のデータのレイヤー名と合わせる）。</summary>
    [Serializable]
    public struct FacialEmotionEntry
    {
        [Tooltip("感情レイヤーの名前（例: Joy）。Runner のデータにあるレイヤー名と同じ綴りにする（大文字小文字も区別）")]
        public string layer;

        [Tooltip("このクリップの間の重み（0 以上。1 で通常の強さ）")]
        public float weight;
    }

    [Serializable]
    public class FacialCorrectionBehaviour : PlayableBehaviour
    {
        [Header("強さ")]
        [Tooltip("オンのとき、下の「強さ」で補正全体を弱める / 切る（オフなら何もしない）")]
        public bool useAlpha;

        [Tooltip("補正全体に掛ける倍率（0〜1）。0 でこのクリップの間は補正なし")]
        [Range(0f, 1f)] public float alpha = 1f;

        [Header("感情の重み")]
        [Tooltip("表情に合わせた補正の切り替え。レイヤー名と重みの組。書いていないレイヤーは Runner の値のまま")]
        public FacialEmotionEntry[] emotions = new FacialEmotionEntry[0];

        [Header("角度の固定")]
        [Tooltip("オンのとき、カメラの位置に関わらず下の Yaw / Pitch を補正の角度として使う（決め構図用）")]
        public bool fixAngles;

        [Tooltip("固定する Yaw（度）。キャラクターの正面 = 0、カメラがキャラクターから見て左で正")]
        public float yaw;

        [Tooltip("固定する Pitch（度）。カメラが上（ふかん）で正")]
        public float pitch;

        [Header("視点")]
        [Tooltip("補正の基準にするカメラなどの Transform。空なら Runner の設定・メインカメラに従う（シーン内のオブジェクトを指定）")]
        public ExposedReference<Transform> viewer;

        [Header("カット補正")]
        [Tooltip("このクリップの間だけ加算するポーズ（.fcpose から取り込んだもの）。ショット単位の手直し用。元のデータは書き換わらない。空なら使わない")]
        public FacialPoseAsset pose;

        [Tooltip("カット補正の重み（0〜1）")]
        [Range(0f, 1f)] public float poseWeight = 1f;

        [Header("コマ打ち")]
        [Tooltip("オンのとき、このクリップの間だけ補正の更新 fps を変える（重なったときは重みの大きいクリップの値）")]
        public bool useStepFps;

        [Tooltip("補正の更新 fps（0 = 毎フレーム）。更新の間は前の重みのまま止まる")]
        public float stepFps;

        [Header("誇張")]
        [Tooltip("オンのとき、このクリップの間だけ誇張（_Ex シェイプ）の強さを下の値にする（.fctrack に誇張のカーブがあっても、こちらが優先）。オフなら .fctrack のカーブに従う（カーブが無ければ変えない）")]
        public bool useExaggeration;

        [Tooltip("誇張の強さ（0〜1）。1 = 作った通り、0 = 誇張なし（重みを 1 までに収める）")]
        [Range(0f, 1f)] public float exaggeration = 1f;

        [Header("パース補正")]
        [Tooltip("オンのとき、このクリップの間だけパース補正の強さを下の値にする（.fctrack にパース補正のカーブがあっても、こちらが優先）。オフなら .fctrack のカーブに従う（カーブが無ければ変えない）")]
        public bool usePerspective;

        [Tooltip("パース補正の強さ（0〜1）。1 = 作った通り、0 = 補正なし")]
        [Range(0f, 1f)] public float perspective = 1f;

        /// <summary>解決した視点（実行時にクリップが入れる。保存されない）。</summary>
        [NonSerialized] public Transform resolvedViewer;

        /// <summary>曲線で動かすときの .fctrack（クリップが入れる。保存されない）。null = 定数のみ。</summary>
        [NonSerialized] public FacialTrackAsset track;

        // 実効値（Mixer が読む。UpdateEffective が毎フレーム作る）。曲線が無ければ上の定数と同じ
        [NonSerialized] public bool effUseAlpha;
        [NonSerialized] public float effAlpha;
        [NonSerialized] public FacialEmotionEntry[] effEmotions;
        [NonSerialized] public bool effUseExaggeration;
        [NonSerialized] public float effExaggeration;
        [NonSerialized] public bool effUsePerspective;
        [NonSerialized] public float effPerspective;
        [NonSerialized] public bool effFixAngles;
        [NonSerialized] public float effYaw;
        [NonSerialized] public float effPitch;
        [NonSerialized] FacialEmotionEntry[] _emoBuffer;

        /// <summary>
        /// 実効値を作る。time = クリップの中の時刻（秒。クリップの「開始位置」を含む = .fctrack の秒と同じ）。
        /// 曲線は「手で入れた値が無い項目」にだけ効く: 強さは「強さを使う」がオフのとき、角度は「角度を固定する」がオフのとき、
        /// 感情は同じレイヤー名を手で書いていないレイヤーだけ。手で入れた値は曲線を上書きする。
        /// </summary>
        public void UpdateEffective(double time)
        {
            effUseAlpha = useAlpha; effAlpha = alpha;
            effUseExaggeration = useExaggeration; effExaggeration = exaggeration;
            effUsePerspective = usePerspective; effPerspective = perspective;
            effFixAngles = fixAngles; effYaw = yaw; effPitch = pitch;
            effEmotions = emotions;
            FacialTrackAsset a = track;
            if (a == null) return;
            float t = (float)time;

            if (!useAlpha && a.HasAlpha)
            {
                effUseAlpha = true;
                effAlpha = Mathf.Clamp01(FacialTrackAsset.Sample(a.alpha, t, 1f));
            }

            // 誇張: 「誇張を使う」がオフのとき .fctrack の exaggeration（0〜1）を使う（カーブが無ければ従来どおり変えない）
            if (!useExaggeration && a.HasExaggeration)
            {
                effUseExaggeration = true;
                effExaggeration = Mathf.Clamp01(FacialTrackAsset.Sample(a.exaggeration, t, 1f));
            }

            // パース補正: 「パース補正を使う」がオフのとき .fctrack の perspective（0〜1）を使う（カーブが無ければ従来どおり変えない）
            if (!usePerspective && a.HasPerspective)
            {
                effUsePerspective = true;
                effPerspective = Mathf.Clamp01(FacialTrackAsset.Sample(a.perspective, t, 1f));
            }

            // 角度: useManual > 0.5 のあいだ固定（角度のカーブが無ければ 0）
            if (!fixAngles && a.HasUseManual && FacialTrackAsset.Sample(a.useManual, t, 0f) > 0.5f)
            {
                effFixAngles = true;
                effYaw = FacialTrackAsset.Sample(a.manualYaw, t, 0f);
                effPitch = FacialTrackAsset.Sample(a.manualPitch, t, 0f);
            }

            if (a.HasEmotions)
            {
                FacialEmotionEntry[] hand = emotions ?? new FacialEmotionEntry[0];
                int extra = 0;
                for (int h = 0; h < hand.Length; h++) if (!HasCurve(a, hand[h].layer)) extra++;
                int total = a.emotions.Length + extra;
                if (_emoBuffer == null || _emoBuffer.Length != total) _emoBuffer = new FacialEmotionEntry[total];
                int n = 0;
                for (int c = 0; c < a.emotions.Length; c++)
                {
                    FacialEmotionCurve curve = a.emotions[c];
                    float w = Mathf.Max(0f, FacialTrackAsset.Sample(curve.keys, t, 0f));
                    for (int h = 0; h < hand.Length; h++)
                        if (string.Equals(hand[h].layer, curve.layer, StringComparison.Ordinal)) w = hand[h].weight; // 手で書いたレイヤーが優先
                    _emoBuffer[n++] = new FacialEmotionEntry { layer = curve.layer, weight = w };
                }
                for (int h = 0; h < hand.Length; h++)
                    if (!HasCurve(a, hand[h].layer)) _emoBuffer[n++] = hand[h];
                effEmotions = _emoBuffer;
            }
        }

        static bool HasCurve(FacialTrackAsset a, string layer)
        {
            for (int i = 0; i < a.emotions.Length; i++)
                if (string.Equals(a.emotions[i].layer, layer, StringComparison.Ordinal)) return true;
            return false;
        }
    }
}
