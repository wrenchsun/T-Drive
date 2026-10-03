// メッシュのブレンドシェイプ名 → 番号の対応（ノード名の接頭辞つきの名前に対応）。
// FBX 取り込み後のシェイプ名は "<blendShape ノード名>.<ターゲット名>"（例 bs.FC_shizuku_Neutral_R0_C0）なので、
// 探す名前が "." を含まなくても末尾が一致するものを見つける。完全一致を優先する。作るときだけ割り当てる（毎フレームは使わない）。
using System;
using System.Collections.Generic;
using UnityEngine;

namespace TDrive.Facial
{
    public sealed class FacialShapeIndex
    {
        readonly Dictionary<string, int> _exact = new Dictionary<string, int>(StringComparer.Ordinal);
        readonly Dictionary<string, int> _suffix = new Dictionary<string, int>(StringComparer.Ordinal);

        public FacialShapeIndex(Mesh mesh)
        {
            if (mesh == null) return;
            int n = mesh.blendShapeCount;
            for (int i = 0; i < n; i++)
            {
                string name = mesh.GetBlendShapeName(i);
                if (!_exact.ContainsKey(name)) _exact[name] = i;
                // 最初の "." 以降のすべての切り口を末尾の候補にする（"a.b.c" → "b.c", "c"）
                for (int d = name.IndexOf('.'); d >= 0 && d + 1 < name.Length; d = name.IndexOf('.', d + 1))
                {
                    string tail = name.Substring(d + 1);
                    if (!_suffix.ContainsKey(tail)) _suffix[tail] = i;
                }
            }
        }

        /// <summary>名前の番号（完全一致 → 末尾一致）。無ければ -1。</summary>
        public int Find(string name)
        {
            if (string.IsNullOrEmpty(name)) return -1;
            int i;
            if (_exact.TryGetValue(name, out i)) return i;
            if (_suffix.TryGetValue(name, out i)) return i;
            return -1;
        }

        public bool Contains(string name) { return Find(name) >= 0; }

        /// <summary>メッシュに、FC_ の頭（"FC_&lt;asset&gt;_"）で始まるシェイプ（接頭辞つき可）があるか。</summary>
        public static bool HasFcShapeWithPrefix(Mesh mesh, string prefix)
        {
            if (mesh == null) return false;
            int n = mesh.blendShapeCount;
            for (int i = 0; i < n; i++)
                if (TDrive.Facial.Core.FacialNaming.HasFcPrefix(mesh.GetBlendShapeName(i), prefix)) return true;
            return false;
        }
    }
}
