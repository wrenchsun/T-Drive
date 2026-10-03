// uLipSync（https://github.com/hecomi/uLipSync, MIT）の解析結果を FacialCorrectionRunner のリップシンクへ渡す部品。
// このアセンブリは uLipSync のパッケージ（com.hecomi.ulipsync）があるときだけコンパイルされる（asmdef の versionDefines）。
// 使い方: uLipSync コンポーネントの On Lip Sync Update にこのコンポーネントの OnLipSyncUpdate をつなぐ。
//         または下の「uLipSync」欄に uLipSync を入れると、有効な間だけ自分で購読する（どちらか一方にすること）。
using UnityEngine;
using System.Collections.Generic;
using uLipSync;

namespace TDrive.Facial.ULipSync
{
    [DisallowMultipleComponent]
    [AddComponentMenu("T-Drive/Facial uLipSync Bridge")]
    public sealed class FacialULipSyncBridge : MonoBehaviour
    {
        [Tooltip("音素の強さ・声量を渡す先。空なら親から探す")]
        public FacialCorrectionRunner runner;

        [Tooltip("つなぐ uLipSync。入れると、有効な間だけ解析結果を自分で受け取る（uLipSync のイベントに手でつなぐときは空のまま）")]
        public global::uLipSync.uLipSync source;

        [Tooltip("uLipSync のプロファイルの音素名 → T-Drive の音素名。空なら同じ名前。表にない名前はそのまま通し、「T-Drive の音素名」が空の行はその音素を捨てる")]
        public FacialPhonemeMapEntry[] phonemeMap;

        readonly List<string> _names = new List<string>(16);
        readonly List<float> _weights = new List<float>(16);
        FacialPhonemeMap _map;
        FacialPhonemeMapEntry[] _mapSource;

        void OnValidate() { _map = null; } // 表を編集したら作り直す

        void OnEnable()
        {
            if (runner == null) runner = GetComponentInParent<FacialCorrectionRunner>();
            if (source != null) source.onLipSyncUpdate.AddListener(OnLipSyncUpdate);
        }

        void OnDisable()
        {
            if (source != null) source.onLipSyncUpdate.RemoveListener(OnLipSyncUpdate);
            if (runner != null) runner.ClearLipSync();
        }

        /// <summary>uLipSync の onLipSyncUpdate（UnityEvent&lt;LipSyncInfo&gt;）が呼ぶ。音素の比率と声量（info.volume）を Runner へ渡す。</summary>
        public void OnLipSyncUpdate(LipSyncInfo info)
        {
            if (runner == null) return;
            if (_map == null || !ReferenceEquals(_mapSource, phonemeMap)) { _map = new FacialPhonemeMap(phonemeMap); _mapSource = phonemeMap; }
            int n = _map.Fill(info.phonemeRatios, _names, _weights);
            runner.SetLipSync(_names, _weights, n, info.volume);
        }
    }
}
