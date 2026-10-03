// マテリアル出力（FU-4、docs/14 §6.4）。対象 Renderer の MaterialPropertyBlock に角度と感情の重みを書く。
//   _FC_Angles   = (yaw / yawRange, pitch / pitchRange を -1〜1 に丸めた値, 全体の強さ, 0)
//   _FC_Emotion0 … = 感情レイヤー（1 番以降。0 番の Neutral は含めない）の重みを 4 つずつ Vector4 に詰める
//   _ToonFacialAngles = _FC_Angles と同じ値（writeToonAngles が on のとき。T-Drive の Toon シェーダーの「顔の角度連動」の入力。F5-9）
// 約束: グローバルには書かない / ブロックは 1 つを使い回し（Get → Set で他の値を残す）/ ウォームアップ後は割り当てなし
//       / 止めるとき・戻すときは自分の値だけ 0 にする（ブロックを Clear しない）。
using System.Collections.Generic;
using UnityEngine;

namespace TDrive.Facial
{
    /// <summary>データの material.mode。</summary>
    public enum FacialMaterialMode { None, PropertyBlock }

    /// <summary>Runner 側の指定。</summary>
    public enum FacialMaterialOutputMode
    {
        [InspectorName("データに従う")] FollowData,
        [InspectorName("出力しない")] Off,
        [InspectorName("PropertyBlock に出力")] PropertyBlock,
    }

    public sealed class FacialMaterialOutput
    {
        public const string AnglesName = "_FC_Angles";
        public const string EmotionName = "_FC_Emotion";
        /// <summary>T-Drive の Toon シェーダー「顔の角度連動」の入力（_FC_Angles と同じ Vector4）。</summary>
        public const string ToonAnglesName = "_ToonFacialAngles";
        /// <summary>感情レイヤーは最大 15 個 → Vector4 が 4 つ。</summary>
        public const int MaxEmotionVectors = 4;

        static bool _init;
        static int _anglesId, _toonAnglesId;
        static readonly int[] EmotionIds = new int[MaxEmotionVectors];

        // Domain Reload を切った設定でも、再生のたびに引き直す（docs/19 U-8）
        [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.SubsystemRegistration)]
        static void ResetStatics() { _init = false; }

        static void Init()
        {
            if (_init) return;
            _anglesId = Shader.PropertyToID(AnglesName);
            _toonAnglesId = Shader.PropertyToID(ToonAnglesName);
            for (int i = 0; i < MaxEmotionVectors; i++) EmotionIds[i] = Shader.PropertyToID(EmotionName + i);
            _init = true;
        }

        /// <summary>_FC_Emotion{index} のプロパティ名。</summary>
        public static string EmotionPropertyName(int index) { return EmotionName + index; }

        /// <summary>文字列 → 列挙（"propertyBlock" だけ有効。未知・null は None）。</summary>
        public static FacialMaterialMode ParseMode(string s)
        {
            return string.Equals(s, "propertyBlock", System.StringComparison.Ordinal) ? FacialMaterialMode.PropertyBlock : FacialMaterialMode.None;
        }

        MaterialPropertyBlock _block;
        readonly List<Renderer> _written = new List<Renderer>(8);
        readonly List<Renderer> _current = new List<Renderer>(8);
        readonly Vector4[] _emo = new Vector4[MaxEmotionVectors];
        int _usedVectors;
        // _ToonFacialAngles を書いたことがあるか（writeToonAngles を切ったときに 0 へ戻すため）
        bool _toonWritten;
        // 最初に書いたとき、そのブロックに他の値があったか（あれば外さず自分の値だけ 0 にする）
        readonly Dictionary<Renderer, bool> _foreign = new Dictionary<Renderer, bool>();

        /// <summary>今、値を書いている Renderer の数。</summary>
        public int WrittenCount { get { return _written.Count; } }

        /// <summary>
        /// 対象（と追加の Renderer）へ書く。emotions は layerCount 個（0 番は無視）。前回書いて今回は対象から外れた Renderer は 0 に戻す。
        /// writeToonAngles = true のとき _ToonFacialAngles にも _FC_Angles と同じ値を書く（false にしたら 0 に戻す）。
        /// </summary>
        public void Write(IReadOnlyList<SkinnedMeshRenderer> targets, Renderer[] extras, double yaw, double pitch,
            double yawRange, double pitchRange, double strength, double[] emotions, int layerCount, bool writeToonAngles = true)
        {
            Init();
            if (_block == null) _block = new MaterialPropertyBlock();

            _current.Clear();
            if (targets != null)
                for (int i = 0; i < targets.Count; i++)
                    if (targets[i] != null && !_current.Contains(targets[i])) _current.Add(targets[i]);
            if (extras != null)
                for (int i = 0; i < extras.Length; i++)
                    if (extras[i] != null && !_current.Contains(extras[i])) _current.Add(extras[i]);

            // 対象から外れたものを戻す（後ろから消すので添字がずれない）
            for (int i = _written.Count - 1; i >= 0; i--)
            {
                Renderer w = _written[i];
                if (w != null && _current.Contains(w)) continue;
                ZeroOn(w);
                _written.RemoveAt(i);
            }

            float ny = yawRange > 1e-6 ? Mathf.Clamp((float)(yaw / yawRange), -1f, 1f) : 0f;
            float np = pitchRange > 1e-6 ? Mathf.Clamp((float)(pitch / pitchRange), -1f, 1f) : 0f;
            var angles = new Vector4(ny, np, (float)strength, 0f);

            int emoCount = layerCount - 1;
            if (emoCount < 0) emoCount = 0;
            if (emoCount > MaxEmotionVectors * 4) emoCount = MaxEmotionVectors * 4;
            int vecs = (emoCount + 3) / 4;
            for (int v = 0; v < MaxEmotionVectors; v++) _emo[v] = default(Vector4);
            for (int k = 0; k < emoCount; k++)
            {
                int layer = k + 1;
                float w = emotions != null && layer < emotions.Length ? (float)emotions[layer] : 0f;
                int v = k >> 2;
                switch (k & 3)
                {
                    case 0: _emo[v].x = w; break;
                    case 1: _emo[v].y = w; break;
                    case 2: _emo[v].z = w; break;
                    default: _emo[v].w = w; break;
                }
            }
            if (vecs > _usedVectors) _usedVectors = vecs;

            for (int i = 0; i < _current.Count; i++)
            {
                Renderer r = _current[i];
                r.GetPropertyBlock(_block); // 他が書いた値を残す
                if (!_foreign.ContainsKey(r)) _foreign[r] = !_block.isEmpty;
                _block.SetVector(_anglesId, angles);
                if (writeToonAngles) _block.SetVector(_toonAnglesId, angles);
                else if (_toonWritten) _block.SetVector(_toonAnglesId, Vector4.zero);
                for (int v = 0; v < _usedVectors; v++) _block.SetVector(EmotionIds[v], _emo[v]);
                r.SetPropertyBlock(_block);
                if (!_written.Contains(r)) _written.Add(r);
            }
            _toonWritten = writeToonAngles; // 切ったときは、今の対象を 0 に戻し終えた
        }

        /// <summary>書いた Renderer の _FC_* を 0 に戻す（ブロックは Clear しない）。</summary>
        public void Clear()
        {
            Init();
            for (int i = 0; i < _written.Count; i++) ZeroOn(_written[i]);
            _written.Clear();
            _usedVectors = 0;
            _toonWritten = false;
        }

        void ZeroOn(Renderer r)
        {
            if (r == null) return;
            if (_block == null) _block = new MaterialPropertyBlock();
            r.GetPropertyBlock(_block);
            bool foreign;
            if (!_foreign.TryGetValue(r, out foreign)) foreign = !_block.isEmpty; // 記録が無いときは、今の中身で判断する
            _foreign.Remove(r);
            if (!foreign)
            {
                // 最初に書いたとき空だった = 自分の値だけ → ブロックごと外す（残ると SRP Batcher から外れたままになる。docs/19 U-5）
                r.SetPropertyBlock(null);
                return;
            }
            _block.SetVector(_anglesId, Vector4.zero);
            if (_toonWritten) _block.SetVector(_toonAnglesId, Vector4.zero);
            for (int v = 0; v < MaxEmotionVectors; v++) _block.SetVector(EmotionIds[v], Vector4.zero);
            r.SetPropertyBlock(_block);
        }

    }
}
