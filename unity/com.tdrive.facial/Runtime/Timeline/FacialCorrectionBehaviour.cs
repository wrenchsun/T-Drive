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

        [Header("コマ打ち（準備中）")]
        [Tooltip("準備中: 値は保持して Runner へ渡しますが、Runner は F5 まで使いません。オンのとき、このクリップの間だけ補正の更新 fps を変える")]
        public bool useStepFps;

        [Tooltip("準備中: 補正の更新 fps（0 = 毎フレーム）")]
        public float stepFps;

        /// <summary>解決した視点（実行時にクリップが入れる。保存されない）。</summary>
        [NonSerialized] public Transform resolvedViewer;
    }
}
