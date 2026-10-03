// 拡張子 .fcpose を取り込む。Maya の出力（.fcpose.json の写し）を置くと FacialCorrectionData ができる。
//  - "format": "FacialCorrection" → FacialCorrectionData（主アセット）
//  - "format": "FacialPose"       → FacialPoseAsset（主アセット。Timeline のカット補正用）
// 調整値（FacialCorrectionOverrides）は別アセットなので、取り込み直しても消えない。
using System;
using System.IO;
using TDrive.Facial.Core;
using UnityEditor.AssetImporters;

namespace TDrive.Facial.Editor
{
    [ScriptedImporter(1, "fcpose")]
    public sealed class FcposeImporter : ScriptedImporter
    {
        public override void OnImportAsset(AssetImportContext ctx)
        {
            string text;
            try { text = File.ReadAllText(ctx.assetPath); }
            catch (Exception e)
            {
                ctx.LogImportError("fcpose を読めません: " + e.Message);
                return;
            }
            string stem = Path.GetFileNameWithoutExtension(ctx.assetPath);
            if (stem.EndsWith(".fcpose", StringComparison.OrdinalIgnoreCase)) stem = stem.Substring(0, stem.Length - 7);

            try
            {
                Action<string> warn = msg => ctx.LogImportWarning(msg);
                FcFile file = FcposeReader.Read(text, warn);
                UnityEngine.Object main;
                if (file.Document != null)
                {
                    FacialCorrectionData data = FcposeConverter.BuildData(file.Document, stem, warn);
                    data.sourceJson = text;
                    main = data;
                }
                else
                {
                    main = FcposeConverter.BuildPose(file.Pose, warn);
                }
                main.name = stem;
                ctx.AddObjectToAsset("main", main);
                ctx.SetMainObject(main);
            }
            catch (FcposeException e)
            {
                ctx.LogImportError(e.Message);
            }
        }
    }
}
