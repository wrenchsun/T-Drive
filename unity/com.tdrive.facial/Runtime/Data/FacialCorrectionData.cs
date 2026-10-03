// .fcpose から作る、ランタイム用の軽いデータ（ScriptableObject）。インポーターが作る。
// 座標・長さは取り込み時に Unity の系（m / Y-up / 左手）へ変換済み。実行時は読み取り専用（Runner は書き換えない）。
using System;
using UnityEngine;

namespace TDrive.Facial
{
    [Serializable]
    public struct FacialGridData
    {
        [Tooltip("Yaw（左右）の範囲（度）。格子の端の角度 = ±この値")] public float yawRange;
        [Tooltip("Pitch（上下）の範囲（度）。格子の端の角度 = ±この値")] public float pitchRange;
        [Tooltip("格子の列数（Yaw 方向）")] public int cols;
        [Tooltip("格子の行数（Pitch 方向）。行 0 が -Pitch（あおり）")] public int rows;
        [Tooltip("範囲の外側でこの幅（度）をかけて補正が 0 へ減衰する。0 以下 = 範囲外は即 0")] public float edgeFade;
        [Tooltip("角度の基準にするボーンの名前")] public string baseBone;
        [Tooltip("基準ボーンの正面の軸（Unity の系。\"+Z\" など）。取り込み時に変換済み")] public string forwardAxis;
        [Tooltip("格子の中心の、基準ボーン空間でのずれ（m・Unity の系）。取り込み時に変換済み")] public Vector3 centerOffset;
    }

    [Serializable]
    public struct FacialPolicyData
    {
        [Tooltip("表情が強いとき補正を弱める度合い（0〜1）。補正 × (1 - この値 × 表情の強さ)")] public float expressionDampen;
        [Tooltip("重みの追従の速さ（指数補間）。0 以下 = 即時")] public float interpSpeed;
        [Tooltip("角度がこの値（度）を超えて飛んだらカット切り替えとみなして即時反映")] public float snapAngle;
        [Tooltip("距離フェードの開始距離（m）。開始 = 終了 = 0 で無効")] public float fadeStart;
        [Tooltip("距離フェードの終了距離（m）。これより遠いと補正 0")] public float fadeEnd;
        [Tooltip("全体の強さ（0〜1）")] public float globalAlpha;
    }

    [Serializable]
    public struct FacialQualityData
    {
        [Tooltip("角度の変化がこの値（度）未満で感情も同じなら、前回の格子の計算を使い回す")] public float angleEpsilon;
        [Tooltip("評価する LOD の上限（0 = 最も詳細な LOD のみ）。※今は未使用")] public int maxLod;
        [Tooltip("キー角度のシャープニング（既定 1）。※F5 まで未使用")] public float sharpness;
        [Tooltip("コマ打ちの fps（0 = 毎フレーム）。※F5 まで未使用")] public float stepFps;
    }

    [Serializable]
    public struct FacialLayerData
    {
        [Tooltip("レイヤー名。0 番は必ず Neutral（常に全量）")] public string name;
        [Tooltip("感情の重みの入力元に使うカーブ名（Animator などから）")] public string emotionCurve;
        [Tooltip("無効にすると評価しない")] public bool enabled;
        [Tooltip("格子の点（行優先: index = row * cols + col）ごとのシェイプ名。点が無ければ空")] public string[] morphNames;
    }

    [Serializable]
    public struct FacialLimitEntry
    {
        [Tooltip("シェイプ名")] public string name;
        [Tooltip("重みの下限")] public float min;
        [Tooltip("重みの上限")] public float max;
    }

    public sealed class FacialCorrectionData : ScriptableObject
    {
        public const string MorphPrefix = "FC_";

        [Tooltip("シェイプ名 FC_<この名前>_<レイヤー>_R{行}_C{列} の <この名前>")]
        public string assetName;

        [Tooltip("格子の定義（Unity の系へ変換済み）")]
        public FacialGridData grid = new FacialGridData
        {
            yawRange = 90f, pitchRange = 45f, cols = 5, rows = 3, edgeFade = 15f,
            baseBone = "head", forwardAxis = "+Z", centerOffset = Vector3.zero,
        };

        [Tooltip("動きの方針（追従・スナップ・距離フェード・全体の強さ）")]
        public FacialPolicyData policy = new FacialPolicyData
        {
            expressionDampen = 0.5f, interpSpeed = 10f, snapAngle = 45f, fadeStart = 0f, fadeEnd = 0f, globalAlpha = 1f,
        };

        [Tooltip("軽量化・品質の設定")]
        public FacialQualityData quality = new FacialQualityData
        {
            angleEpsilon = 0.1f, maxLod = 0, sharpness = 1f, stepFps = 0f,
        };

        [Tooltip("レイヤー（0 番 = Neutral）。各点のシェイプ名を持つ")]
        public FacialLayerData[] layers = new FacialLayerData[0];

        [Tooltip("表情の強さ（補正を弱める入力）に使うシェイプ名。空なら取り込み時に作業セットのシェイプを入れる")]
        public string[] intensityCurves = new string[0];

        [Tooltip("シェイプごとの可動域。登録のあるシェイプだけ、出力をこの範囲に切る")]
        public FacialLimitEntry[] limits = new FacialLimitEntry[0];

        [Tooltip("マテリアル連携の方式（\"none\" | \"propertyBlock\"）")]
        public string materialMode = "none";

        [Tooltip("ベイク先のメッシュ名（参考情報）")]
        public string targetMesh;

        [Tooltip("同じ重みで動かす別メッシュ（まつ毛など。参考情報）")]
        public string[] extraMeshes = new string[0];

        [Tooltip("元の .fcpose の情報（変換前の値。参考）")]
        public FacialSourceInfo source;

#if UNITY_EDITOR
        // 格子ビューア（エディタ）用に元の JSON を持つ。ビルドには入らない
        [HideInInspector] public string sourceJson;
#endif

        /// <summary>materialMode を列挙型で（未知の文字列は None）。</summary>
        public FacialMaterialMode MaterialModeValue { get { return FacialMaterialOutput.ParseMode(materialMode); } }

        /// <summary>格子の点の数（rows × cols）。</summary>
        public int PointCount { get { return Mathf.Max(0, grid.rows) * Mathf.Max(0, grid.cols); } }

        /// <summary>レイヤー layer の点 (row, col) のシェイプ名。点が無い・範囲外なら空文字。</summary>
        public string MorphNameAt(int layer, int row, int col)
        {
            if (layers == null || layer < 0 || layer >= layers.Length) return "";
            string[] names = layers[layer].morphNames;
            int index = row * grid.cols + col;
            if (names == null || row < 0 || col < 0 || col >= grid.cols || index >= names.Length) return "";
            return names[index] ?? "";
        }
    }

    /// <summary>変換前の .fcpose の情報（参考表示用）。</summary>
    [Serializable]
    public struct FacialSourceInfo
    {
        public int version;
        public string unit;
        public string upAxis;
        public string handedness;
        public string source;
        public string profile;
        public string forwardAxis;
        public Vector3 centerOffset;
        public float fadeStart;
        public float fadeEnd;
    }
}
