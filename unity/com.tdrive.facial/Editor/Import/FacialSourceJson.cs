// 元の .fcpose の JSON（格子ビューアの「キー / 自動生成」の区別に使う）。エディタ専用。
// 以前は FacialCorrectionData に [HideInInspector] の欄（#if UNITY_EDITOR）で持っていたが、エディタとビルドでシリアライズの形が変わるので外した（docs/19 §5）。
//  - 取り込み・変換のときに Set で覚える（メモリ上だけ。ドメインのリロードで消える）
//  - 無ければ、アセットの元ファイル（.fcpose。取り込みの入力そのもの）を読む
using System;
using System.IO;
using System.Runtime.CompilerServices;
using UnityEditor;

namespace TDrive.Facial.Editor
{
    public static class FacialSourceJson
    {
        sealed class Entry { public string json; }

        static readonly ConditionalWeakTable<FacialCorrectionData, Entry> Memory = new ConditionalWeakTable<FacialCorrectionData, Entry>();

        // 元ファイルの読み込みの控え（パス → 更新時刻と中身）。ビューアが毎回ファイルを読まないように
        static string _cachePath, _cacheJson;
        static DateTime _cacheTime;

        /// <summary>変換・取り込みの直後に呼ぶ。</summary>
        public static void Set(FacialCorrectionData data, string json)
        {
            if (data == null) return;
            Memory.Remove(data);
            Memory.Add(data, new Entry { json = json });
        }

        /// <summary>元の JSON。無ければ null（元ファイルも無い）。</summary>
        public static string Get(FacialCorrectionData data)
        {
            if (data == null) return null;
            Entry e;
            if (Memory.TryGetValue(data, out e) && e.json != null) return e.json;
            string path = AssetDatabase.GetAssetPath(data);
            if (string.IsNullOrEmpty(path) || !path.EndsWith(".fcpose", StringComparison.OrdinalIgnoreCase)) return null;
            try
            {
                if (!File.Exists(path)) return null;
                DateTime t = File.GetLastWriteTimeUtc(path);
                if (_cachePath == path && _cacheTime == t) return _cacheJson;
                string text = File.ReadAllText(path);
                _cachePath = path; _cacheTime = t; _cacheJson = text;
                return text;
            }
            catch (IOException) { return null; }
            catch (UnauthorizedAccessException) { return null; }
        }
    }
}
