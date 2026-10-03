// FU-4〜FU-6 のテスト用の合成キャラクター。シーンに入らない隠しオブジェクト（HideAndDontSave）で作り、Dispose で破棄する。
// 格子 3×3・レイヤー Neutral / Joy。メッシュに Missing だけ作らない。顔は +Z を向き、頭は (0, 1.5, 0)。
using System;
using NUnit.Framework;
using TDrive.Facial.Core;
using UnityEditor;
using UnityEngine;
using Object = UnityEngine.Object;

namespace TDrive.Facial.Tests
{
    public sealed class FacialTestRig : IDisposable
    {
        public const string Asset = "test";
        public const string Missing = "FC_test_Neutral_R2_C2";

        public GameObject root, head, face;
        public Mesh mesh;
        public SkinnedMeshRenderer smr;
        public FacialCorrectionData data;
        public FacialCorrectionRunner runner;

        public static string N(string layer, int r, int c) { return FacialNaming.MorphName(Asset, layer, r, c); }

        static GameObject Hidden(string name) { return EditorUtility.CreateGameObjectWithHideFlags(name, HideFlags.HideAndDontSave); }

        /// <summary>FC_ シェイプ名の前に付けるノード名の接頭辞（FBX 取り込み後の "bs." を再現する。空 = 付けない）。</summary>
        readonly string _fcPrefix;

        public FacialTestRig(bool includeMissing = false, string fcPrefix = "")
        {
            _fcPrefix = fcPrefix ?? "";
            mesh = new Mesh { name = "RigMesh" };
            mesh.vertices = new[] { Vector3.zero, Vector3.right, Vector3.up };
            mesh.triangles = new[] { 0, 1, 2 };
            for (int r = 0; r < 3; r++)
                for (int c = 0; c < 3; c++)
                {
                    string n = N("Neutral", r, c);
                    if (includeMissing || n != Missing) AddShape(n);
                }
            AddShape(N("Joy", 1, 1));
            AddShape("bs.jaw");
            AddShape("bs.other");

            root = Hidden("RigRoot");
            head = Hidden("head");
            head.transform.SetParent(root.transform, false);
            head.transform.position = new Vector3(0f, 1.5f, 0f);
            face = Hidden("face");
            face.transform.SetParent(root.transform, false);
            smr = face.AddComponent<SkinnedMeshRenderer>();
            smr.sharedMesh = mesh;

            data = ScriptableObject.CreateInstance<FacialCorrectionData>();
            data.assetName = Asset;
            data.grid = new FacialGridData { yawRange = 90f, pitchRange = 45f, cols = 3, rows = 3, edgeFade = 15f, baseBone = "head", forwardAxis = "+Z", centerOffset = Vector3.zero };
            data.policy = new FacialPolicyData { expressionDampen = 0.5f, interpSpeed = 10f, snapAngle = 45f, fadeStart = 0f, fadeEnd = 0f, globalAlpha = 1f };
            data.quality = new FacialQualityData { angleEpsilon = 0.1f, maxLod = 0, sharpness = 1f, stepFps = 0f };
            var neutral = new string[9];
            for (int r = 0; r < 3; r++) for (int c = 0; c < 3; c++) neutral[r * 3 + c] = N("Neutral", r, c);
            var joy = new string[9];
            for (int i = 0; i < 9; i++) joy[i] = "";
            joy[4] = N("Joy", 1, 1);
            data.layers = new[]
            {
                new FacialLayerData { name = "Neutral", emotionCurve = "", enabled = true, morphNames = neutral },
                new FacialLayerData { name = "Joy", emotionCurve = "emo_joy", enabled = true, morphNames = joy },
            };
            data.intensityCurves = new[] { "bs.jaw" };

            runner = root.AddComponent<FacialCorrectionRunner>();
            runner.data = data;
        }

        public void AddShape(string name)
        {
            if (FacialNaming.IsFcName(name)) name = _fcPrefix + name;
            mesh.AddBlendShapeFrame(name, 100f, new[] { Vector3.up * 0.01f, Vector3.zero, Vector3.zero }, new Vector3[3], new Vector3[3]);
        }

        public float W(string name)
        {
            int i = new FacialShapeIndex(mesh).Find(name);
            Assert.GreaterOrEqual(i, 0, "メッシュにシェイプが無い: " + name);
            return smr.GetBlendShapeWeight(i);
        }

        /// <summary>FC_ シェイプの重みの絶対値の合計（0 = 全部戻っている）。</summary>
        public float FcSum()
        {
            float s = 0f;
            for (int i = 0; i < mesh.blendShapeCount; i++)
                if (FacialNaming.HasFcPrefix(mesh.GetBlendShapeName(i), "FC_")) s += Mathf.Abs(smr.GetBlendShapeWeight(i));
            return s;
        }

        public void Dispose()
        {
            if (runner != null) runner.ResetWeights();
            if (root != null) Object.DestroyImmediate(root);
            if (mesh != null) Object.DestroyImmediate(mesh);
            if (data != null) Object.DestroyImmediate(data);
        }
    }
}
