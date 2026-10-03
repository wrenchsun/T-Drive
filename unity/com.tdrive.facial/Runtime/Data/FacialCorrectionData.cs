// .fcpose から作る、ランタイム用の軽いデータ（ScriptableObject）。インポーターが作る。
// 座標・長さは取り込み時に Unity の系（m / Y-up / 左手）へ変換済み。実行時は読み取り専用（Runner は書き換えない）。
using System;
using TDrive.Facial.Core;
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
        [Tooltip("補正を書く LOD の上限（0 = 制限なし。N = LOD N まで書き、それより粗い LOD の Renderer には書かない。LODGroup に入っていない Renderer は常に書く）")] public int maxLod;
        [Tooltip("キー角度のシャープさ（既定 1）。1 より大きいとキーの角度の近くでキーのポーズに寄る。0 以下は 1 として扱う")] public float sharpness;
        [Tooltip("コマ打ちの fps（0 = 毎フレーム）。補正の更新をこの回数 / 秒に間引く（追従はなく、更新のたびに目標へ跳ぶ）")] public float stepFps;
        [Tooltip("誇張（_Ex シェイプ）の既定の強さ（0〜1）。「指定あり」がオンのときだけ使い、オフなら 1")] public float exaggeration;
        [Tooltip("exaggeration を使う（オフ = 1）。古いアセットを 1 のままにするためのフラグ")] public bool hasExaggeration;
        [Tooltip("補間の種類。Bilinear = 4 隅の双線形（標準）/ CatmullRom = キーとキーの間がなめらか（キーの角度以外では少し行き過ぎることがある）")] public FacialInterpolation interpolation;
    }

    /// <summary>レイヤーの重みの入力元（layerWeights）。</summary>
    public enum FacialLayerWeightSource { Direct = 0, Curve = 1, Distance = 2 }

    [Serializable]
    public struct FacialLayerWeightData
    {
        [Tooltip("重みの入力元。Direct / Curve = 呼び出し側（Runner の値・Timeline）が与える。Distance = 視点までの距離で決める")] public FacialLayerWeightSource source;
        [Tooltip("Distance: この距離（m）以下では「近いときの重み」")] public float start;
        [Tooltip("Distance: この距離（m）以上では「遠いときの重み」。開始と同じなら、開始以上で「遠いとき」")] public float end;
        [Tooltip("Distance: 開始距離のときの重み")] public float from;
        [Tooltip("Distance: 終了距離のときの重み")] public float to;
    }

    [Serializable]
    public struct FacialLayerData
    {
        [Tooltip("レイヤー名。0 番は必ず Neutral（常に全量）")] public string name;
        [Tooltip("感情の重みの入力元に使うカーブ名（Animator などから）")] public string emotionCurve;
        [Tooltip("無効にすると評価しない")] public bool enabled;
        [Tooltip("格子の点（行優先: index = row * cols + col）ごとのシェイプ名。点が無ければ空")] public string[] morphNames;
        [Tooltip("誇張用（_Ex）のシェイプ名（morphNames と同じ並び）。メッシュに無いものは黙って飛ばす（任意のシェイプ）。空 = なし")] public string[] exMorphNames;
        [Tooltip("感情の重みの入力元（距離で決める設定など）")] public FacialLayerWeightData weight;
    }

    /// <summary>パース補正の軸（perspective.axis）。</summary>
    public enum FacialPerspectiveAxis { Distance = 0, Fov = 1 }

    [Serializable]
    public struct FacialPerspectiveKeyData
    {
        [Tooltip("軸の値。距離は m（取り込み時に .fcpose の単位から変換済み）、画角は度")] public float value;
        [Tooltip("このキーのシェイプ名 FC_<アセット>_Persp_K{n}。ポーズが空のキー（補正なしの範囲）は空。n は配列の番号")] public string morphName;
    }

    /// <summary>パース補正（広角で寄ったときの奥行きを押さえる）。軸の値からキーの重みを混ぜ、角度の補正に足す。</summary>
    [Serializable]
    public struct FacialPerspectiveData
    {
        [Tooltip("パース補正を使う")] public bool enabled;
        [Tooltip("軸。Distance = 視点と格子の中心の距離（m）、Fov = 視点の縦の画角（度）")] public FacialPerspectiveAxis axis;
        [Tooltip("パース補正の強さ（0〜1）")] public float strength;
        [Tooltip("キー（配列の順 = シェイプの番号）。値は並んでいなくてよい")] public FacialPerspectiveKeyData[] keys;
    }

    [Serializable]
    public struct FacialLipSyncShapeData
    {
        [Tooltip("シェイプ名（.fcpose に書かれた名前。メッシュのシェイプには完全一致 → 末尾一致で結ぶ）")] public string name;
        [Tooltip("重み（0〜1。誇張で 1 を超えることもある）")] public float weight;
    }

    /// <summary>リップシンクの行 1 つ（音素 × 感情 → シェイプの重み）。emotion が空 = 基本。</summary>
    [Serializable]
    public struct FacialLipSyncEntryData
    {
        [Tooltip("音素の名前")] public string phoneme;
        [Tooltip("感情レイヤー名。空 = 基本")] public string emotion;
        [Tooltip("口のシェイプの重み")] public FacialLipSyncShapeData[] shapes;
    }

    /// <summary>声量 → 口の大きさの倍率: lerp(from, to, saturate((v - min) / (max - min)))。max が min 以下のときは v が min 以上なら to、それ以外は from。</summary>
    [Serializable]
    public struct FacialLipSyncVolumeData
    {
        [Tooltip("声量の下限（これ以下で「から」の倍率）")] public float min;
        [Tooltip("声量の上限（これ以上で「まで」の倍率）")] public float max;
        [Tooltip("声量が下限のときの口の大きさの倍率")] public float from;
        [Tooltip("声量が上限のとき（声量が与えられないときも）の口の大きさの倍率")] public float to;
    }

    /// <summary>リップシンクの対応表。音声の解析は持たず、音素の強さ・声量を受け取って口のシェイプの重みに変える。</summary>
    [Serializable]
    public struct FacialLipSyncData
    {
        [Tooltip("リップシンクを使う")] public bool enabled;
        [Tooltip("全体の強さ（0〜1）")] public float strength;
        [Tooltip("音素の名前（順番 = Maya の表の並び）")] public string[] phonemes;
        [Tooltip("音素 × 感情の行")] public FacialLipSyncEntryData[] entries;
        [Tooltip("声量 → 口の大きさの倍率")] public FacialLipSyncVolumeData volume;
        [Tooltip("追従の速さ（1/秒）。0 以下 = 即時")] public float follow;

        /// <summary>計算用（Core）の形にする。取り込み・結ぶときだけ使う（割り当てあり）。</summary>
        public FcLipSync ToCore()
        {
            var l = new FcLipSync { Enabled = enabled, Strength = strength, Follow = follow };
            l.Volume = new FcLipSyncVolume { Min = volume.min, Max = volume.max, From = volume.from, To = volume.to };
            if (phonemes != null) for (int i = 0; i < phonemes.Length; i++) l.Phonemes.Add(phonemes[i] ?? "");
            if (entries != null)
                for (int i = 0; i < entries.Length; i++)
                {
                    var e = new FcLipSyncEntry { Phoneme = entries[i].phoneme ?? "", Emotion = entries[i].emotion ?? "" };
                    if (entries[i].shapes != null)
                        for (int k = 0; k < entries[i].shapes.Length; k++)
                            if (!string.IsNullOrEmpty(entries[i].shapes[k].name)) e.Curves[entries[i].shapes[k].name] = entries[i].shapes[k].weight;
                    l.Entries.Add(e);
                }
            return l;
        }
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

        [Tooltip("パース補正（キーの値とシェイプ名）。使わない設定・キーなしのときは何もしない")]
        public FacialPerspectiveData perspective = new FacialPerspectiveData { strength = 1f };

        [Tooltip("リップシンクの対応表（音素 × 感情 → 口のシェイプ）。使わない設定・行なしのときは何もしない")]
        public FacialLipSyncData lipSync = new FacialLipSyncData
        {
            strength = 1f, follow = 20f, volume = new FacialLipSyncVolumeData { min = 0f, max = 1f, from = 0.5f, to = 1f },
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
