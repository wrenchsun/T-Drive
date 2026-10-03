// .fctrack（Maya のショットで打った演出カーブ）を取り込んだもの。Timeline のクリップ（FacialCorrectionClip.track）が参照して再生する（FT-3）。
// 値は秒（range の開始 = 0）と値の組。補間は線形、同じ時刻の 2 キーは段差。実行時は読み取り専用。
using System;
using UnityEngine;

namespace TDrive.Facial
{
    [Serializable]
    public struct FacialKey
    {
        [Tooltip("時刻（秒。ショットの開始 = 0）")] public float time;
        [Tooltip("値")] public float value;
        public FacialKey(float time, float value) { this.time = time; this.value = value; }
    }

    /// <summary>感情レイヤー 1 つ分の重みのカーブ（レイヤー名は Runner のデータのレイヤー名と同じ綴り）。</summary>
    [Serializable]
    public sealed class FacialEmotionCurve
    {
        [Tooltip("感情レイヤーの名前（例: Joy）")] public string layer;
        [Tooltip("重みのキー（0 以上。1 で通常の強さ）")] public FacialKey[] keys = new FacialKey[0];
    }

    public sealed class FacialTrackAsset : ScriptableObject
    {
        [Tooltip("ショット名（Maya のショット）")] public string shot;
        [Tooltip("モデル名（Maya のキャラクター）")] public string model;
        [Tooltip("Maya のフレームレート")] public float frameRate = 30f;
        [Tooltip("Maya の範囲（フレーム。開始）")] public float rangeStart;
        [Tooltip("Maya の範囲（フレーム。終了）")] public float rangeEnd;

        [Tooltip("補正全体の強さ（0〜1）のカーブ。空 = 打っていない")] public FacialKey[] alpha = new FacialKey[0];
        [Tooltip("角度の固定のオン / オフ（0 か 1。0.5 より大きいとオン）のカーブ")] public FacialKey[] useManual = new FacialKey[0];
        [Tooltip("固定する Yaw（度）のカーブ")] public FacialKey[] manualYaw = new FacialKey[0];
        [Tooltip("固定する Pitch（度）のカーブ")] public FacialKey[] manualPitch = new FacialKey[0];
        [Tooltip("感情レイヤーごとの重みのカーブ")] public FacialEmotionCurve[] emotions = new FacialEmotionCurve[0];

        /// <summary>範囲の長さ（秒）。</summary>
        public float DurationSeconds { get { return frameRate > 0f ? Mathf.Max(0f, (rangeEnd - rangeStart) / frameRate) : 0f; } }

        public bool HasAlpha { get { return alpha != null && alpha.Length > 0; } }
        public bool HasUseManual { get { return useManual != null && useManual.Length > 0; } }
        public bool HasEmotions { get { return emotions != null && emotions.Length > 0; } }

        /// <summary>
        /// キーの列を時刻 t（秒）で読む。最初のキーより前は最初の値、最後のキーより後は最後の値。
        /// 同じ時刻の 2 キーは段差（その時刻ちょうどは後のキーの値）。キーが無ければ fallback。
        /// </summary>
        public static float Sample(FacialKey[] keys, float t, float fallback = 0f)
        {
            if (keys == null || keys.Length == 0) return fallback;
            int n = keys.Length;
            if (t < keys[0].time) return keys[0].value;
            // t 以下で最後のキー
            int lo = 0, hi = n - 1;
            while (lo < hi)
            {
                int mid = (lo + hi + 1) >> 1;
                if (keys[mid].time <= t) lo = mid; else hi = mid - 1;
            }
            if (lo >= n - 1) return keys[n - 1].value;
            FacialKey a = keys[lo], b = keys[lo + 1]; // b.time > t >= a.time なので割り算は安全
            float u = (t - a.time) / (b.time - a.time);
            return a.value + (b.value - a.value) * u;
        }
    }
}
