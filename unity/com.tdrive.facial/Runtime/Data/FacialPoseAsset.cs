// "format": "FacialPose"（1 点分のポーズ）を取り込んだもの。Timeline のカット補正で使う（FT-1）。
// 値は取り込み時に Unity の系（m / Y-up / 左手）へ変換済み。実行時は読み取り専用。
using System;
using UnityEngine;

namespace TDrive.Facial
{
    [Serializable]
    public struct FacialPoseCurve
    {
        [Tooltip("シェイプ名")] public string name;
        [Tooltip("重み（0〜1。誇張は 2 まで）")] public float value;
    }

    [Serializable]
    public struct FacialPoseBone
    {
        [Tooltip("ボーン名")] public string name;
        [Tooltip("親ボーン空間での加算の移動（m）")] public Vector3 position;
        [Tooltip("親ボーン空間での加算の回転")] public Quaternion rotation;
        [Tooltip("親ボーン空間での加算のスケール")] public Vector3 scale;
    }

    public sealed class FacialPoseAsset : ScriptableObject
    {
        [Tooltip("シェイプ名 → 重み")] public FacialPoseCurve[] curves = new FacialPoseCurve[0];
        [Tooltip("ボーン名 → 親ボーン空間での加算（適用順 Scale → Rotation → Translation）")] public FacialPoseBone[] bones = new FacialPoseBone[0];
        [Tooltip("元の .fcpose の情報（変換前の値。参考）")] public FacialSourceInfo source;
    }
}
