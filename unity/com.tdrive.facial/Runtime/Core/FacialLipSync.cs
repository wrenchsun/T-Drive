// リップシンクの対応表の計算（Python 版 core/evaluate.py の lipsync_* の写し。docs/14 §5.8c）。
// UnityEngine 非依存。LipSyncTable は取り込み・バインドのときに 1 度だけ作り、毎フレームの Output / Activity は割り当てなし。
using System;
using System.Collections.Generic;

namespace TDrive.Facial.Core
{
    public static class FacialLipSync
    {
        /// <summary>0〜1 に丸める。NaN は 0。</summary>
        public static double Saturate(double x)
        {
            if (double.IsNaN(x)) return 0.0;
            return x < 0.0 ? 0.0 : (x > 1.0 ? 1.0 : x);
        }

        /// <summary>
        /// 声量 → 口の大きさの倍率 lerp(from, to, saturate((v - min) / (max - min)))。max &lt;= min のときは v >= min なら to、それ以外は from。
        /// 声量が NaN（与えられない）なら to。倍率が有限でなければ 1。
        /// </summary>
        public static double VolumeScale(double min, double max, double from, double to, double volume)
        {
            double o;
            if (double.IsNaN(volume)) o = to;
            else if (max <= min) o = volume >= min ? to : from;
            else o = from + (to - from) * Saturate((volume - min) / (max - min));
            return double.IsNaN(o) || double.IsInfinity(o) ? 1.0 : o;
        }

        /// <summary>最終 = 今の値 × (1 − a) + 出力 を 0〜upper に丸める（NaN は 0）。</summary>
        public static double Apply(double current, double output, double activity, double upper)
        {
            double v = current * (1.0 - Saturate(activity)) + output;
            if (double.IsNaN(v)) return 0.0;
            if (v > upper) v = upper;
            return v < 0.0 ? 0.0 : v;
        }
    }

    /// <summary>
    /// 対応表を計算しやすい形（音素・シェイプ・感情の番号の密な表）にしたもの。FcLipSync から 1 度作る。
    /// 音素の一覧の重複・空の名前は 1 度だけ・数えない。同じ音素 × 感情の行は先のものだけが効く。
    /// 音素の一覧にない音素の行は無視。emotionNames = 呼び出し側が渡す感情の重みの名前（レイヤー名。順番がそのまま重みの配列の番号）。
    /// </summary>
    public sealed class LipSyncTable
    {
        readonly string[] _phonemes;
        readonly string[] _curves;
        readonly string[] _emotions;
        readonly bool[] _emotionValid;
        readonly double[] _base;    // [p * C + c]
        readonly double[] _emo;     // [(e * P + p) * C + c]
        readonly bool[] _hasEmo;    // [e * P + p]
        readonly Dictionary<string, int> _phonemeIndex = new Dictionary<string, int>();

        public bool Enabled { get; set; }
        /// <summary>有効で、出力するシェイプがある。false のときは何もしない。</summary>
        public bool Active { get { return Enabled && _curves.Length > 0; } }
        public double Strength { get; set; }
        public double VolumeMin { get; set; }
        public double VolumeMax { get; set; }
        public double VolumeFrom { get; set; }
        public double VolumeTo { get; set; }
        /// <summary>追従の速さ（1/秒。0 以下 = 即時）。</summary>
        public double Follow { get; set; }

        public int PhonemeCount { get { return _phonemes.Length; } }
        public int CurveCount { get { return _curves.Length; } }
        public int EmotionCount { get { return _emotions.Length; } }
        public string PhonemeName(int i) { return _phonemes[i]; }
        public string CurveName(int i) { return _curves[i]; }

        /// <summary>音素名の番号。無ければ -1（呼び出し側が文字列で探すのはバインド・入力の変換のときだけ）。</summary>
        public int IndexOfPhoneme(string name)
        {
            int i;
            return name != null && _phonemeIndex.TryGetValue(name, out i) ? i : -1;
        }

        public LipSyncTable(FcLipSync lip, IReadOnlyList<string> emotionNames)
        {
            int ne = emotionNames != null ? emotionNames.Count : 0;
            _emotions = new string[ne];
            _emotionValid = new bool[ne];
            var seenEmotion = new HashSet<string>();
            for (int i = 0; i < ne; i++)
            {
                _emotions[i] = emotionNames[i] ?? "";
                _emotionValid[i] = _emotions[i].Length > 0 && seenEmotion.Add(_emotions[i]);
            }
            var phonemes = new List<string>();
            var curves = new List<string>();
            if (lip == null)
            {
                _phonemes = new string[0];
                _curves = new string[0];
                _base = new double[0];
                _emo = new double[0];
                _hasEmo = new bool[0];
                Strength = 0.0;
                return;
            }
            Enabled = lip.Enabled && lip.Entries.Count > 0;
            Strength = FacialLipSync.Saturate(lip.Strength);
            VolumeMin = lip.Volume.Min; VolumeMax = lip.Volume.Max; VolumeFrom = lip.Volume.From; VolumeTo = lip.Volume.To;
            Follow = lip.Follow;
            for (int i = 0; i < lip.Phonemes.Count; i++)
            {
                string p = lip.Phonemes[i];
                if (string.IsNullOrEmpty(p) || _phonemeIndex.ContainsKey(p)) continue;
                _phonemeIndex[p] = phonemes.Count;
                phonemes.Add(p);
            }
            var curveIndex = new Dictionary<string, int>();
            for (int i = 0; i < lip.Entries.Count; i++)
            {
                FcLipSyncEntry e = lip.Entries[i];
                if (!_phonemeIndex.ContainsKey(e.Phoneme)) continue;
                foreach (KeyValuePair<string, double> kv in e.Curves)
                {
                    if (curveIndex.ContainsKey(kv.Key)) continue;
                    curveIndex[kv.Key] = curves.Count;
                    curves.Add(kv.Key);
                }
            }
            _phonemes = phonemes.ToArray();
            _curves = curves.ToArray();
            int P = _phonemes.Length, C = _curves.Length;
            _base = new double[P * C];
            _emo = new double[ne * P * C];
            _hasEmo = new bool[ne * P];
            var seenCell = new HashSet<string>();
            for (int i = 0; i < lip.Entries.Count; i++)
            {
                FcLipSyncEntry e = lip.Entries[i];
                int p;
                if (!_phonemeIndex.TryGetValue(e.Phoneme, out p)) continue;
                if (!seenCell.Add(e.Phoneme + "\u0000" + e.Emotion)) continue; // 同じ音素 × 感情は先のもの
                if (e.Emotion.Length == 0)
                {
                    foreach (KeyValuePair<string, double> kv in e.Curves) _base[p * C + curveIndex[kv.Key]] = kv.Value;
                    continue;
                }
                for (int k = 0; k < ne; k++)
                {
                    if (!_emotionValid[k] || _emotions[k] != e.Emotion) continue;
                    _hasEmo[k * P + p] = true;
                    foreach (KeyValuePair<string, double> kv in e.Curves) _emo[(k * P + p) * C + curveIndex[kv.Key]] = kv.Value;
                }
            }
        }

        /// <summary>口の活動量 a = saturate(Σ w_p)（各 w_p は 0〜1 に丸める）。phonemeWeights は音素の番号の並び。</summary>
        public double Activity(double[] phonemeWeights)
        {
            double sum = 0.0;
            for (int p = 0; p < _phonemes.Length; p++) sum += FacialLipSync.Saturate(phonemeWeights[p]);
            return FacialLipSync.Saturate(sum);
        }

        /// <summary>
        /// 出力（シェイプの番号の並びで output へ。全シェイプを書く: 寄与が無ければ 0）。Active でなければ何もしない（false を返す）。
        /// volume = NaN は「与えられない」。emotionWeights = 感情の重み（コンストラクターの emotionNames の並び。null 可 = 全部 0）。
        /// 各重みは 0〜1 に丸め、合計が 1 を超えたら合計で割る。割り当てなし。
        /// </summary>
        public bool Output(double[] phonemeWeights, double volume, double[] emotionWeights, double[] output)
        {
            if (!Active) return false;
            int P = _phonemes.Length, C = _curves.Length, E = _emotions.Length;
            for (int c = 0; c < C; c++) output[c] = 0.0;
            double scale = FacialLipSync.VolumeScale(VolumeMin, VolumeMax, VolumeFrom, VolumeTo, volume) * Strength;
            double total = 0.0;
            if (emotionWeights != null)
                for (int e = 0; e < E; e++) if (_emotionValid[e]) total += FacialLipSync.Saturate(emotionWeights[e]);
            double inv = total > 1.0 ? 1.0 / total : 1.0;
            for (int p = 0; p < P; p++)
            {
                double w = FacialLipSync.Saturate(phonemeWeights[p]);
                if (w <= 0.0) continue;
                for (int c = 0; c < C; c++)
                {
                    double b = _base[p * C + c];
                    double v = b;
                    if (emotionWeights != null)
                    {
                        for (int e = 0; e < E; e++)
                        {
                            if (!_emotionValid[e] || !_hasEmo[e * P + p]) continue;
                            double ew = FacialLipSync.Saturate(emotionWeights[e]) * inv;
                            if (ew <= 0.0) continue;
                            v += ew * (_emo[(e * P + p) * C + c] - b);
                        }
                    }
                    output[c] += w * v * scale;
                }
            }
            return true;
        }
    }
}
