// .fcpose（JSON 文字列）→ ランタイム用データ（FacialCorrectionData / FacialPoseAsset）。インポーターとテストが共用する。
// 座標・長さは meta の系 → Unity の系（m / Y-up / 左手）へ FacialSpace で変換して持たせる。ここ以外で座標系を気にしない。
using System;
using System.Collections.Generic;
using TDrive.Facial.Core;
using UnityEngine;

namespace TDrive.Facial.Editor
{
    public static class FcposeConverter
    {
        /// <summary>meta の系。単位・軸が未知ならその旨を警告して UE の系（既定）として扱う。</summary>
        public static SpaceSpec SpaceOf(FcMeta meta, Action<string> warn)
        {
            try { return new SpaceSpec(meta.Unit, meta.UpAxis, meta.Handedness); }
            catch (ArgumentException e)
            {
                if (warn != null) warn("meta の座標系を解釈できません（" + e.Message + "）。UE の系（cm / Z-up / 左手）として扱います");
                return FacialSpace.UE;
            }
        }

        /// <summary>JSON 文字列 → FacialCorrectionData（呼び出し側が破棄する。FacialPose だと例外）。</summary>
        public static FacialCorrectionData BuildData(string json, string fallbackAssetName, Action<string> warn)
        {
            FcFile file = FcposeReader.Read(json, warn);
            if (file.Document == null) throw new FcposeException("FacialCorrection 形式ではありません（FacialPose です）");
            FacialCorrectionData data = BuildData(file.Document, fallbackAssetName, warn);
            data.sourceJson = json;
            return data;
        }

        public static FacialCorrectionData BuildData(FcDocument doc, string fallbackAssetName, Action<string> warn)
        {
            SpaceSpec src = SpaceOf(doc.Meta, warn);
            SpaceConverter cv = FacialSpace.Converter(src, FacialSpace.Unity);
            var data = ScriptableObject.CreateInstance<FacialCorrectionData>();

            string asset = !string.IsNullOrEmpty(doc.Asset) ? doc.Asset : fallbackAssetName;
            if (string.IsNullOrEmpty(asset))
            {
                asset = "asset";
                if (warn != null) warn("アセット名（\"asset\" キー）が無いので 'asset' にしました。FC_<アセット名>_… のシェイプと合いません");
            }
            data.assetName = asset;

            // 格子: 前方の軸と中心のずれを Unity の系へ
            string fwd;
            try { fwd = cv.ForwardAxis(doc.Grid.ForwardAxis); }
            catch (ArgumentException e)
            {
                fwd = "+Z";
                if (warn != null) warn("forwardAxis を変換できません（" + e.Message + "）。+Z にしました");
            }
            Vec3 co = cv.Position(doc.Grid.CenterOffset);
            data.grid = new FacialGridData
            {
                yawRange = (float)doc.Grid.YawRange,
                pitchRange = (float)doc.Grid.PitchRange,
                cols = doc.Grid.Cols,
                rows = doc.Grid.Rows,
                edgeFade = (float)doc.Grid.EdgeFade,
                baseBone = doc.Grid.BaseBone,
                forwardAxis = fwd,
                centerOffset = new Vector3((float)co.X, (float)co.Y, (float)co.Z),
            };

            // 方針（距離は m へ）
            data.policy = new FacialPolicyData
            {
                expressionDampen = (float)doc.Policy.ExpressionDampen,
                interpSpeed = (float)doc.Policy.InterpSpeed,
                snapAngle = (float)doc.Policy.SnapAngle,
                fadeStart = (float)(doc.Policy.FadeStart * cv.Scale),
                fadeEnd = (float)(doc.Policy.FadeEnd * cv.Scale),
                globalAlpha = (float)doc.Policy.GlobalAlpha,
            };
            data.quality = new FacialQualityData
            {
                angleEpsilon = (float)doc.Quality.AngleEpsilon,
                maxLod = doc.Quality.MaxLod,
                sharpness = (float)doc.Quality.Sharpness,
                stepFps = (float)doc.Quality.StepFps,
            };

            // レイヤー: 作った点だけシェイプ名を持つ（名前は規則から決まる）。格子の外の点は使わない
            int cols = Math.Max(0, doc.Grid.Cols), rows = Math.Max(0, doc.Grid.Rows);
            var layers = new FacialLayerData[doc.Layers.Count];
            for (int li = 0; li < layers.Length; li++)
            {
                FcLayer l = doc.Layers[li];
                var names = new string[rows * cols];
                for (int i = 0; i < names.Length; i++) names[i] = "";
                int outside = 0;
                for (int pi = 0; pi < l.Points.Count; pi++)
                {
                    FcPoint p = l.Points[pi];
                    if (p.Row < 0 || p.Row >= rows || p.Col < 0 || p.Col >= cols) { outside++; continue; }
                    names[p.Row * cols + p.Col] = FacialNaming.MorphName(asset, l.Name, p.Row, p.Col);
                }
                if (outside > 0 && warn != null) warn("レイヤー '" + l.Name + "' の格子の外の点 " + outside + " 個は使いません");
                layers[li] = new FacialLayerData { name = l.Name, emotionCurve = l.EmotionCurve, enabled = l.Enabled, morphNames = names };
            }
            data.layers = layers;

            // 表情の強さの入力: intensityCurves、無ければ作業セットのシェイプ（UE 版と同じ）
            List<string> intensity = doc.IntensityCurves.Count > 0 ? doc.IntensityCurves : doc.WorkingCurves;
            data.intensityCurves = intensity.ToArray();

            var limits = new List<FacialLimitEntry>();
            foreach (KeyValuePair<string, FcLimit> kv in doc.Limits)
                limits.Add(new FacialLimitEntry { name = kv.Key, min = (float)kv.Value.Min, max = (float)kv.Value.Max });
            data.limits = limits.ToArray();

            data.materialMode = doc.MaterialMode;
            data.targetMesh = doc.TargetMesh;
            data.extraMeshes = doc.TargetExtraMeshes.ToArray();
            data.source = SourceInfo(doc.Meta, doc.Version, doc.Profile, doc.Grid.ForwardAxis, doc.Grid.CenterOffset,
                doc.Policy.FadeStart, doc.Policy.FadeEnd);
            return data;
        }

        /// <summary>JSON 文字列 → FacialPoseAsset（FacialCorrection だと例外）。</summary>
        public static FacialPoseAsset BuildPose(string json, Action<string> warn)
        {
            FcFile file = FcposeReader.Read(json, warn);
            if (file.Pose == null) throw new FcposeException("FacialPose 形式ではありません（FacialCorrection です）");
            return BuildPose(file.Pose, warn);
        }

        public static FacialPoseAsset BuildPose(FcPoseDocument doc, Action<string> warn)
        {
            SpaceConverter cv = FacialSpace.Converter(SpaceOf(doc.Meta, warn), FacialSpace.Unity);
            var asset = ScriptableObject.CreateInstance<FacialPoseAsset>();
            var curves = new List<FacialPoseCurve>();
            foreach (KeyValuePair<string, double> kv in doc.Pose.Curves)
                curves.Add(new FacialPoseCurve { name = kv.Key, value = (float)kv.Value });
            asset.curves = curves.ToArray();
            var bones = new List<FacialPoseBone>();
            foreach (KeyValuePair<string, FcBone> kv in doc.Pose.Bones)
            {
                FcBone b = kv.Value;
                Vec3 t = cv.Position(b.T);
                Quat q = FacialSpace.QuatNormalize(cv.Quaternion(b.R));
                Vec3 s = cv.ScaleVector(b.S);
                bones.Add(new FacialPoseBone
                {
                    name = kv.Key,
                    position = new Vector3((float)t.X, (float)t.Y, (float)t.Z),
                    rotation = new Quaternion((float)q.X, (float)q.Y, (float)q.Z, (float)q.W),
                    scale = new Vector3((float)s.X, (float)s.Y, (float)s.Z),
                });
            }
            asset.bones = bones.ToArray();
            asset.source = SourceInfo(doc.Meta, doc.Version, "", "", new Vec3(0, 0, 0), 0, 0);
            return asset;
        }

        static FacialSourceInfo SourceInfo(FcMeta meta, int version, string profile, string forwardAxis, Vec3 centerOffset,
            double fadeStart, double fadeEnd)
        {
            return new FacialSourceInfo
            {
                version = version,
                unit = meta.Unit,
                upAxis = meta.UpAxis,
                handedness = meta.Handedness,
                source = meta.Source,
                profile = profile,
                forwardAxis = forwardAxis,
                centerOffset = new Vector3((float)centerOffset.X, (float)centerOffset.Y, (float)centerOffset.Z),
                fadeStart = (float)fadeStart,
                fadeEnd = (float)fadeEnd,
            };
        }
    }
}
