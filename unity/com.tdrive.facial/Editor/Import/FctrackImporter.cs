// 拡張子 .fctrack を取り込む。Maya の出力（Timeline 用の演出カーブ）を置くと FacialTrackAsset ができる（FT-3）。
// Timeline への反映は D-Drive ブリッジ（FctrackTimelineHook）または FacialTrackTimelineSync が行う。
using System;
using System.Collections.Generic;
using System.IO;
using TDrive.Facial.Core;
using UnityEditor.AssetImporters;

namespace TDrive.Facial.Editor
{
    public static class FctrackConverter
    {
        /// <summary>FcTrack → FacialTrackAsset（呼び出し側が破棄する）。</summary>
        public static FacialTrackAsset Build(FcTrack t, string name)
        {
            var a = UnityEngine.ScriptableObject.CreateInstance<FacialTrackAsset>();
            a.name = name;
            a.shot = t.Shot;
            a.model = t.Model;
            a.frameRate = (float)t.FrameRate;
            a.rangeStart = (float)t.RangeStart;
            a.rangeEnd = (float)t.RangeEnd;
            var emotions = new List<FacialEmotionCurve>();
            foreach (KeyValuePair<string, List<FcKey>> kv in t.Curves)
            {
                FacialKey[] keys = Keys(kv.Value);
                switch (kv.Key)
                {
                    case "alpha": a.alpha = keys; break;
                    case "useManual": a.useManual = keys; break;
                    case FctrackReader.ExaggerationCurve: a.exaggeration = keys; break;
                    case "manualYaw": a.manualYaw = keys; break;
                    case "manualPitch": a.manualPitch = keys; break;
                    default:
                        if (kv.Key.StartsWith(FctrackReader.EmotionPrefix, StringComparison.Ordinal))
                            emotions.Add(new FacialEmotionCurve { layer = kv.Key.Substring(FctrackReader.EmotionPrefix.Length), keys = keys });
                        break;
                }
            }
            emotions.Sort((x, y) => string.CompareOrdinal(x.layer, y.layer)); // 取り込みのたびに同じ並びにする
            a.emotions = emotions.ToArray();
            return a;
        }

        static FacialKey[] Keys(List<FcKey> src)
        {
            var keys = new FacialKey[src.Count];
            for (int i = 0; i < keys.Length; i++) keys[i] = new FacialKey((float)src[i].Time, (float)src[i].Value);
            return keys;
        }
    }

    [ScriptedImporter(1, "fctrack")]
    public sealed class FctrackImporter : ScriptedImporter
    {
        public override void OnImportAsset(AssetImportContext ctx)
        {
            string text;
            try { text = File.ReadAllText(ctx.assetPath); }
            catch (Exception e)
            {
                ctx.LogImportError("fctrack を読めません: " + e.Message);
                return;
            }
            try
            {
                FcTrack t = FctrackReader.Read(text);
                FacialTrackAsset asset = FctrackConverter.Build(t, Path.GetFileNameWithoutExtension(ctx.assetPath));
                ctx.AddObjectToAsset("main", asset);
                ctx.SetMainObject(asset);
            }
            catch (FctrackException e)
            {
                ctx.LogImportError(".fctrack を取り込めません（" + Path.GetFileName(ctx.assetPath) + "）: " + e.Message);
            }
        }
    }
}
