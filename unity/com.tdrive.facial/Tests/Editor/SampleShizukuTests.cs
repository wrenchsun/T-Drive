// 実データの結合テスト（任意）: Maya で作った shizuku の FBX と .fcpose を、Unity へ取り込んだあとのランタイム補正まで通して確かめる。
//   Maya の作成（tools/setup_sample_shizuku_facial.py）→ ベイク → Unity 向け出力 → 取り込み → 補正
// ホストのプロジェクトに Assets/__TDriveFacialSample/shizuku.fbx と shizuku.fcpose が無いときは Ignore（配布アバターなので
// リポジトリには入れない。各自のプロジェクトに置き、Git の除外に入れる）。
// モデルは HideAndDontSave のプレビューシーンへ作り、ユーザーのシーンには入れない。TearDown で破棄する。
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using NUnit.Framework;
using TDrive.Facial.Core;
using TDrive.Facial.Editor;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using Object = UnityEngine.Object;

namespace TDrive.Facial.Tests
{
    public class SampleShizukuTests
    {
        const string Folder = "Assets/__TDriveFacialSample";
        const string FbxPath = Folder + "/shizuku.fbx";
        const string FcposePath = Folder + "/shizuku.fcpose";
        const string FaceMesh = "mdl_face02";
        const float Dt = 0.02f;
        const float Tol = 0.5f;

        Scene _scene;
        GameObject _instance, _viewer;
        FacialCorrectionData _data;
        FacialCorrectionRunner _runner;
        SkinnedMeshRenderer _face;

        [SetUp]
        public void SetUp()
        {
            if (!File.Exists(FbxPath) || !File.Exists(FcposePath))
                Assert.Ignore("サンプルがありません（" + FbxPath + " と " + FcposePath + "）。tools/setup_sample_shizuku_facial.py の出力 facial/shizuku/export/unity/ を置くと走ります");
            // 取り込み済みのアセットを読むだけ（強制の再取り込みはしない。ユーザーのアセットを書き換えない）
            var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(FbxPath);
            _data = AssetDatabase.LoadAssetAtPath<FacialCorrectionData>(FcposePath);
            if (prefab == null || _data == null)
                Assert.Ignore("サンプルがまだ取り込まれていません（Unity が取り込むのを待ってから実行してください）: " + FbxPath);

            // ユーザーのシーンに触れない: 最初からプレビューシーンの中に作り、隠しオブジェクトにする
            _scene = EditorSceneManager.NewPreviewScene();
            _instance = (GameObject)PrefabUtility.InstantiatePrefab(prefab, _scene);
            Hide(_instance);
            _viewer = EditorUtility.CreateGameObjectWithHideFlags("viewer", HideFlags.HideAndDontSave);
            SceneManager.MoveGameObjectToScene(_viewer, _scene);

            _runner = _instance.AddComponent<FacialCorrectionRunner>();
            _runner.data = _data;
            _runner.RebuildCaches();
            Assert.AreEqual(1, _runner.ResolvedTargets.Count, "FC_ シェイプを持つメッシュ（顔）を自動検出できる");
            _face = _runner.ResolvedTargets[0];
        }

        static void Hide(GameObject go)
        {
            go.hideFlags = HideFlags.HideAndDontSave;
            foreach (Transform t in go.GetComponentsInChildren<Transform>(true)) t.gameObject.hideFlags = HideFlags.HideAndDontSave;
        }

        [TearDown]
        public void TearDown()
        {
            if (_runner != null) _runner.ResetWeights();
            if (_instance != null) Object.DestroyImmediate(_instance);
            if (_viewer != null) Object.DestroyImmediate(_viewer);
            if (_scene.IsValid()) EditorSceneManager.ClosePreviewScene(_scene);
        }

        // ---------------------------------------------------------------- 道具

        List<string> ExpectedNames()
        {
            return _data.layers.SelectMany(l => l.morphNames ?? new string[0]).Where(n => !string.IsNullOrEmpty(n)).Distinct().ToList();
        }

        string NameAt(int layer, int row, int col) { return _data.layers[layer].morphNames[row * _data.grid.cols + col]; }

        int LayerIndex(string name) { return System.Array.FindIndex(_data.layers, l => l.name == name); }

        int IndexOf(string wanted)
        {
            return new FacialShapeIndex(_face.sharedMesh).Find(wanted);
        }

        float W(string wanted)
        {
            int i = IndexOf(wanted);
            Assert.GreaterOrEqual(i, 0, "顔のメッシュにシェイプが無い: " + wanted);
            return _face.GetBlendShapeWeight(i);
        }

        float[][] SnapshotAllWeights()
        {
            return _instance.GetComponentsInChildren<SkinnedMeshRenderer>(true)
                .Select(r => Enumerable.Range(0, r.sharedMesh != null ? r.sharedMesh.blendShapeCount : 0).Select(i => r.GetBlendShapeWeight(i)).ToArray()).ToArray();
        }

        /// <summary>角度 (yaw, pitch) を見る位置へ視点を置き、評価する（毎回スナップして即時反映）。</summary>
        void EvaluateAt(double yaw, double pitch, float distance = 1.5f)
        {
            Transform bone = _runner.ResolvedBaseBone;
            Assert.IsNotNull(bone, "基準ボーン " + _data.grid.baseBone + " が見つからない");
            Vector3 pos; Quaternion rot;
            FacialGridMath.CameraPose(bone.position, bone.rotation, _data.grid.forwardAxis, _data.grid.centerOffset, yaw, pitch, distance, out pos, out rot);
            _viewer.transform.SetPositionAndRotation(pos, rot);
            _runner.ResetWeights();
            _runner.EvaluateNow(_viewer.transform, Dt);
            Assert.AreEqual(yaw, _runner.CurrentYaw, 0.05, "視点の位置から求めた Yaw");
            Assert.AreEqual(pitch, _runner.CurrentPitch, 0.05, "視点の位置から求めた Pitch");
        }

        /// <summary>FC_ シェイプの重みが expected（名前 → 重み）どおり、それ以外の FC_ は 0 であること。</summary>
        void AssertFc(Dictionary<string, float> expected, string context)
        {
            Mesh m = _face.sharedMesh;
            for (int i = 0; i < m.blendShapeCount; i++)
            {
                string actual = m.GetBlendShapeName(i);
                string bare;
                if (!FacialNaming.TryGetBareFcName(actual, out bare)) continue;
                float e;
                if (!expected.TryGetValue(bare, out e)) e = 0f;
                Assert.AreEqual(e, _face.GetBlendShapeWeight(i), Tol, context + " / " + actual);
            }
        }

        // ---------------------------------------------------------------- テスト

        [Test]
        public void ImportFacts_LogsNamingScaleAndNormals()
        {
            Mesh m = _face.sharedMesh;
            var sb = new StringBuilder();
            sb.AppendLine("[SampleShizuku] 顔メッシュ '" + m.name + "' (renderer '" + _face.name + "') のブレンドシェイプ 総数 " + m.blendShapeCount);
            for (int i = 0; i < Mathf.Min(10, m.blendShapeCount); i++) sb.AppendLine("  [" + i + "] " + m.GetBlendShapeName(i));
            int fc = 0;
            string firstFc = null;
            for (int i = 0; i < m.blendShapeCount; i++)
            {
                string b;
                if (FacialNaming.TryGetBareFcName(m.GetBlendShapeName(i), out b)) { fc++; if (firstFc == null) firstFc = m.GetBlendShapeName(i); }
            }
            sb.AppendLine("  FC_ のシェイプ " + fc + " 個。最初の FC_ = '" + firstFc + "'（データの名前は '" + ExpectedNames()[0] + "'）");
            int bareExact = ExpectedNames().Count(n => m.GetBlendShapeIndex(n) >= 0);
            sb.AppendLine("  素の名前で GetBlendShapeIndex が引ける FC_ = " + bareExact + " / " + ExpectedNames().Count + "（0 なら Unity 側は接頭辞つき: 末尾一致の対応が必須）");

            var mi = AssetImporter.GetAtPath(FbxPath) as ModelImporter;
            Assert.IsNotNull(mi);
            sb.AppendLine("  ModelImporter: importBlendShapeNormals=" + FacialValidation.NormalsLabel(mi.importBlendShapeNormals)
                + " (推奨 " + FacialValidation.NormalsLabel(FacialValidation.RecommendedBlendShapeNormals) + ")"
                + " / globalScale=" + mi.globalScale + " / useFileScale=" + mi.useFileScale + " / fileScale=" + mi.fileScale
                + " / importBlendShapes=" + mi.importBlendShapes);
            Transform bone = _runner.ResolvedBaseBone;
            Assert.IsNotNull(bone);
            sb.AppendLine("  基準ボーン '" + bone.name + "' world=" + bone.position.ToString("F4") + " euler=" + bone.rotation.eulerAngles.ToString("F2")
                + " lossyScale=" + bone.lossyScale.ToString("F4") + " / ルート scale=" + _instance.transform.localScale.ToString("F4"));
            Debug.Log(sb.ToString());
            // コンソールは複数行のログの先頭しか見えないことがあるので、要点は 1 行でも出す
            Debug.Log("[SampleShizuku] NAMING shapes=" + m.blendShapeCount + " fc=" + fc + " firstFc='" + firstFc + "' data0='" + ExpectedNames()[0]
                + "' bareGetBlendShapeIndex=" + bareExact + "/" + ExpectedNames().Count + " globalScale=" + mi.globalScale + " useFileScale=" + mi.useFileScale
                + " fileScale=" + mi.fileScale + " normals=" + FacialValidation.NormalsLabel(mi.importBlendShapeNormals)
                + " headWorld=" + bone.position.ToString("F4") + " headLossyScale=" + bone.lossyScale.ToString("F4"));

            Assert.AreEqual(ExpectedNames().Count, fc, "メッシュの FC_ シェイプ数 = データが期待する数（Neutral 15 + Joy の点）");
            Assert.IsTrue(mi.importBlendShapes);
            // 推奨と違うときは検証が情報（Info）で知らせるだけで、自動では変えない。報告のため値をログに残す
            FacialIssue normals = FacialValidation.CheckBlendShapeNormals(mi.importBlendShapeNormals, FbxPath);
            Debug.Log("[SampleShizuku] Blend Shape Normals の検証: " + (normals == null ? "推奨どおり" : normals.ToString()));
        }

        [Test]
        public void ExpectedShapeCountIsNeutralPlusJoyPoints()
        {
            int neutral = _data.layers[0].morphNames.Count(n => !string.IsNullOrEmpty(n));
            Assert.AreEqual(15, neutral, "Neutral は 5x3 の全点");
            int joy = LayerIndex("Joy") >= 0 ? _data.layers[LayerIndex("Joy")].morphNames.Count(n => !string.IsNullOrEmpty(n)) : 0;
            Assert.Greater(joy, 0, "Joy レイヤーに点がある");
            Assert.AreEqual(neutral + joy, ExpectedNames().Count);
        }

        [Test]
        public void EveryExpectedMorphIsResolvableOnTheFace()
        {
            Assert.AreEqual(0, _runner.GetMissingShapeNames().Count, "見つからないシェイプ: " + string.Join(", ", _runner.GetMissingShapeNames()));
            Assert.AreEqual(ExpectedNames().Count, _runner.BoundShapeCount);
            foreach (string n in ExpectedNames()) Assert.GreaterOrEqual(IndexOf(n), 0, n);
        }

        [Test]
        public void BaseBoneIsFoundByDataName()
        {
            Assert.IsNotNull(_runner.ResolvedBaseBone);
            Assert.AreEqual(_data.grid.baseBone, _runner.ResolvedBaseBone.name);
        }

        [Test]
        public void ViewerSideMatchesTheCharactersLeftAndFront()
        {
            Transform head = _runner.ResolvedBaseBone;
            Transform eyeL = _instance.GetComponentsInChildren<Transform>(true).FirstOrDefault(t => t.name == "bone_eye_L");
            Transform eyeR = _instance.GetComponentsInChildren<Transform>(true).FirstOrDefault(t => t.name == "bone_eye_R");
            Assert.IsNotNull(eyeL); Assert.IsNotNull(eyeR);
            Vector3 left = FacialGridMath.ViewDirection(head.rotation, _data.grid.forwardAxis, 90, 0);
            Vector3 front = FacialGridMath.ViewDirection(head.rotation, _data.grid.forwardAxis, 0, 0);
            Debug.Log("[SampleShizuku] yaw+90 の視点方向 " + left.ToString("F3") + " / 正面 " + front.ToString("F3")
                + " / eyeL-eyeR " + (eyeL.position - eyeR.position).ToString("F4") + " / 頭の前方(+Z) " + (head.rotation * Vector3.forward).ToString("F3"));
            Assert.Greater(Vector3.Dot(eyeL.position - eyeR.position, left), 0f, "Yaw +90 = キャラクターの左（bone_eye_L 側）から見る");
            Assert.Greater(Vector3.Dot(left, front), -1e-4f, "直交");
            // 顔の前: 顔メッシュの重心は目より後ろにある（顔は前向き）ので、正面の視点は目の中点の側にある
            Vector3 eyeMid = (eyeL.position + eyeR.position) * 0.5f;
            Assert.Greater(Vector3.Dot(eyeMid - head.position, front), 0f, "正面（Yaw 0）の視点は顔の前側");
        }

        [Test]
        public void ExactGridPointsWriteWeight100ToTheirOwnShape()
        {
            GridFacts();
            int cols = _data.grid.cols, rows = _data.grid.rows;
            var cases = new List<int[]> { new[] { 1, 2 }, new[] { 1, cols - 1 }, new[] { 1, 0 }, new[] { rows - 1, 2 }, new[] { 0, 2 }, new[] { 0, cols - 1 }, new[] { rows - 1, 0 } };
            foreach (int[] rc in cases)
            {
                double yaw, pitch;
                FacialGridMath.PointAngles(_data.grid, rc[0], rc[1], out yaw, out pitch);
                float[][] before = SnapshotAllWeights();
                EvaluateAt(yaw, pitch);
                string n = NameAt(0, rc[0], rc[1]);
                Assert.IsNotEmpty(n);
                AssertFc(new Dictionary<string, float> { { n, 100f } }, "R" + rc[0] + " C" + rc[1] + " yaw " + yaw + " pitch " + pitch);
                AssertNonFcUnchanged(before);
            }
        }

        [Test]
        public void MidpointBetweenTwoPointsWritesHalfAndHalf()
        {
            double y1, p1, y2, p2;
            FacialGridMath.PointAngles(_data.grid, 1, 2, out y1, out p1);
            FacialGridMath.PointAngles(_data.grid, 1, 3, out y2, out p2);
            float[][] before = SnapshotAllWeights();
            EvaluateAt((y1 + y2) * 0.5, 0);
            AssertFc(new Dictionary<string, float> { { NameAt(0, 1, 2), 50f }, { NameAt(0, 1, 3), 50f } }, "中点");
            AssertNonFcUnchanged(before);
        }

        [Test]
        public void EmotionLayerAddsItsShapeAndResetRestoresZeros()
        {
            int joy = LayerIndex("Joy");
            Assert.GreaterOrEqual(joy, 1, "Joy レイヤー");
            double yaw, pitch;
            FacialGridMath.PointAngles(_data.grid, 1, 2, out yaw, out pitch);
            string neutral = NameAt(0, 1, 2), joyName = NameAt(joy, 1, 2);
            Assert.IsNotEmpty(joyName);

            _runner.SetEmotionWeight(joy, 0f);
            EvaluateAt(yaw, pitch);
            AssertFc(new Dictionary<string, float> { { neutral, 100f } }, "感情 0");

            float[][] before = SnapshotAllWeights();
            _runner.SetEmotionWeight(joy, 1f);
            EvaluateAt(yaw, pitch);
            AssertFc(new Dictionary<string, float> { { neutral, 100f }, { joyName, 100f } }, "感情 1");
            AssertNonFcUnchanged(before);

            _runner.ResetWeights();
            AssertFc(new Dictionary<string, float>(), "ResetWeights 後");
            Assert.AreEqual(0, _runner.ActiveWeightCount);
        }

        [Test]
        public void ValidationHasNoErrors()
        {
            List<FacialIssue> issues = FacialValidation.Run(_runner);
            var sb = new StringBuilder("[SampleShizuku] 検証 " + issues.Count + " 件（エラー " + FacialValidation.Count(issues, FacialIssueSeverity.Error)
                + " / 警告 " + FacialValidation.Count(issues, FacialIssueSeverity.Warning) + " / 情報 " + FacialValidation.Count(issues, FacialIssueSeverity.Info) + "）");
            foreach (FacialIssue i in issues) sb.AppendLine().Append("  " + i);
            Debug.Log(sb.ToString());
            Assert.AreEqual(0, FacialValidation.Count(issues, FacialIssueSeverity.Error), sb.ToString());
            Assert.AreEqual(0, issues.Count(i => i.Kind == FacialIssueKind.MissingShape || i.Kind == FacialIssueKind.OrphanShape), "シェイプの過不足は無いはず\n" + sb);
        }

        void GridFacts()
        {
            Debug.Log("[SampleShizuku] 格子 " + _data.grid.cols + "x" + _data.grid.rows + " yaw±" + _data.grid.yawRange + " pitch±" + _data.grid.pitchRange
                + " base=" + _data.grid.baseBone + " forward=" + _data.grid.forwardAxis + " centerOffset=" + _data.grid.centerOffset.ToString("F4")
                + " layers=" + string.Join(",", _data.layers.Select(l => l.name)) + " intensity=" + string.Join(",", _data.intensityCurves)
                + " limits=" + (_data.limits != null ? _data.limits.Length : 0));
        }

        void AssertNonFcUnchanged(float[][] before)
        {
            SkinnedMeshRenderer[] rs = _instance.GetComponentsInChildren<SkinnedMeshRenderer>(true);
            for (int k = 0; k < rs.Length; k++)
            {
                Mesh m = rs[k].sharedMesh;
                if (m == null) continue;
                for (int i = 0; i < m.blendShapeCount; i++)
                {
                    string b;
                    if (FacialNaming.TryGetBareFcName(m.GetBlendShapeName(i), out b)) continue;
                    Assert.AreEqual(before[k][i], rs[k].GetBlendShapeWeight(i), 1e-6f, "FC_ 以外が変わった: " + rs[k].name + "/" + m.GetBlendShapeName(i));
                }
            }
        }
    }
}
