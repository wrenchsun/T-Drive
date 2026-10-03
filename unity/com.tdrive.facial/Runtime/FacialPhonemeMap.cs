// 解析側の音素名 → T-Drive の音素名の対応（uLipSync などの橋渡し用。Unity の型に依存しない純粋な処理）。
// 表が空 = 同じ名前。表にない名前もそのまま通す。「変換先」が空の行は、その音素を捨てる。毎フレームの割り当てなし（Fill は Dictionary の foreach だけ）。
using System;
using System.Collections.Generic;

namespace TDrive.Facial
{
    [Serializable]
    public struct FacialPhonemeMapEntry
    {
        public string from;
        public string to;
    }

    public sealed class FacialPhonemeMap
    {
        readonly Dictionary<string, string> _map = new Dictionary<string, string>(StringComparer.Ordinal);

        public FacialPhonemeMap(FacialPhonemeMapEntry[] entries) { Rebuild(entries); }

        /// <summary>表を作り直す（同じ from は先の行が効く。from が空の行は無視）。</summary>
        public void Rebuild(FacialPhonemeMapEntry[] entries)
        {
            _map.Clear();
            if (entries == null) return;
            for (int i = 0; i < entries.Length; i++)
            {
                string f = entries[i].from;
                if (string.IsNullOrEmpty(f) || _map.ContainsKey(f)) continue;
                _map[f] = entries[i].to ?? "";
            }
        }

        public int Count { get { return _map.Count; } }

        /// <summary>変換後の名前。表になければそのまま。捨てる音素（変換先が空）と null は null。</summary>
        public string Map(string source)
        {
            if (source == null) return null;
            string to;
            if (_map.Count > 0 && _map.TryGetValue(source, out to)) return to.Length == 0 ? null : to;
            return source;
        }

        /// <summary>
        /// 解析の結果（音素名 → 比率）を、変換後の名前と強さの並びにして names / weights へ入れる（先に空にする）。
        /// 変換後に同じ名前になったものは並べたまま渡す（Runner が大きいほうを使う）。入れた数を返す。ratios が null なら 0。
        /// </summary>
        public int Fill(Dictionary<string, float> ratios, List<string> names, List<float> weights)
        {
            names.Clear();
            weights.Clear();
            if (ratios == null) return 0;
            foreach (KeyValuePair<string, float> kv in ratios)
            {
                string n = Map(kv.Key);
                if (n == null) continue;
                names.Add(n);
                weights.Add(kv.Value);
            }
            return names.Count;
        }
    }
}
