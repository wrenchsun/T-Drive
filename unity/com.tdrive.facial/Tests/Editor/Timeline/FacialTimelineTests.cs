// FT-1: Timeline のトラック / クリップ / Mixer。編集時（再生しない）に director.Evaluate() してブレンドシェイプの重みを確かめる。
// 重みは Runner（合成メッシュ）の値。Scene ビューは使わない（視点は Runner の viewerOverride / クリップの視点 / 手動の角度）。
using System.Collections.Generic;
using NUnit.Framework;
using TDrive.Facial.Core;
using TDrive.Facial.Timeline;
using UnityEngine;
using UnityEngine.Playables;
using UnityEngine.Timeline;

namespace TDrive.Facial.Tests.Timeline
{
    public class FacialTimelineTests
    {
        FacialTimelineTestHarness _h;
        FacialPoseAsset _pose;

        [SetUp]
        public void SetUp() { _h = new FacialTimelineTestHarness(); }

        [TearDown]
        public void TearDown()
        {
            _h.Dispose();
            if (_pose != null) Object.DestroyImmediate(_pose);
        }

        float N11 { get { return _h.rig.W(FacialTestRig.N("Neutral", 1, 1)); } }
        float N12 { get { return _h.rig.W(FacialTestRig.N("Neutral", 1, 2)); } }

        sealed class FakeCollector : IPropertyCollector
        {
            public readonly List<string> names = new List<string>();
            public readonly List<Component> components = new List<Component>();
            public void PushActiveGameObject(GameObject gameObject) { }
            public void PopActiveGameObject() { }
            public void AddFromClip(AnimationClip clip) { }
            public void AddFromClips(IEnumerable<AnimationClip> clips) { }
            public void AddFromName<T>(string name) where T : Component { names.Add(name); }
            public void AddFromName(string name) { names.Add(name); }
            public void AddFromClip(GameObject obj, AnimationClip clip) { }
            public void AddFromClips(GameObject obj, IEnumerable<AnimationClip> clips) { }
            public void AddFromName<T>(GameObject obj, string name) where T : Component { names.Add(name); }
            public void AddFromName(GameObject obj, string name) { names.Add(name); }
            public void AddFromName(Component component, string name) { names.Add(name); }
            public void AddFromComponent(GameObject obj, Component component) { components.Add(component); }
            public void AddObjectProperties(Object obj, AnimationClip clip) { }
        }

        [Test]
        public void ClipAlphaScalesTheCorrectionOnlyInsideTheClip()
        {
            FacialCorrectionTrack track = _h.AddTrack();
            FacialCorrectionClip c = _h.AddClip(track, 0, 2);
            c.template.useAlpha = true; c.template.alpha = 0.5f;

            _h.At(1.0);
            Assert.AreEqual(50f, N11, 0.5f);
            _h.At(3.0); // クリップの外 = 通常の補正
            Assert.AreEqual(100f, N11, 0.5f);
        }

        [Test]
        public void AlphaFlagOffLeavesTheCorrectionAlone()
        {
            FacialCorrectionClip c = _h.AddClip(_h.AddTrack(), 0, 2);
            c.template.useAlpha = false; c.template.alpha = 0f;
            _h.At(1.0);
            Assert.AreEqual(100f, N11, 0.5f);
        }

        [Test]
        public void TwoOverlappingClipsBlendAlpha()
        {
            FacialCorrectionTrack track = _h.AddTrack();
            FacialCorrectionClip a = _h.AddClip(track, 0, 2);
            a.template.useAlpha = true; a.template.alpha = 0f;
            FacialCorrectionClip b = _h.AddClip(track, 1, 2);
            b.template.useAlpha = true; b.template.alpha = 1f;

            _h.At(0.5);
            Assert.AreEqual(0f, N11, 0.5f);
            _h.At(1.5); // 重なりの真ん中 = 0.5 ずつ
            Assert.AreEqual(50f, N11, 2f);
            _h.At(2.5);
            Assert.AreEqual(100f, N11, 0.5f);
        }

        [Test]
        public void EmotionWeightsApplyByLayerName()
        {
            FacialCorrectionClip c = _h.AddClip(_h.AddTrack(), 0, 2);
            c.template.emotions = new[]
            {
                new FacialEmotionEntry { layer = "Joy", weight = 0.5f },
                new FacialEmotionEntry { layer = "NoSuchLayer", weight = 1f }, // 未知の名前は無視
            };
            _h.At(1.0);
            Assert.AreEqual(50f, _h.rig.W(FacialTestRig.N("Joy", 1, 1)), 0.5f);
            _h.At(3.0);
            Assert.AreEqual(0f, _h.rig.W(FacialTestRig.N("Joy", 1, 1)), 0.5f);
        }

        [Test]
        public void EmotionWeightsBlendAcrossClipsAndKeepRunnerValueForUnlistedLayers()
        {
            _h.rig.runner.SetEmotionWeight(1, 0.2f); // Runner の値が土台
            FacialCorrectionTrack track = _h.AddTrack();
            FacialCorrectionClip a = _h.AddClip(track, 0, 2); // 感情の指定なし
            FacialCorrectionClip b = _h.AddClip(track, 1, 2);
            b.template.emotions = new[] { new FacialEmotionEntry { layer = "Joy", weight = 1f } };

            _h.At(0.5);
            Assert.AreEqual(20f, _h.rig.W(FacialTestRig.N("Joy", 1, 1)), 0.5f, "指定の無いクリップだけのとき Runner の値のまま");
            _h.At(2.5);
            Assert.AreEqual(100f, _h.rig.W(FacialTestRig.N("Joy", 1, 1)), 0.5f);
            _h.At(1.5); // 重なりの真ん中: 0.2 + 0.5 * (1 - 0.2) = 0.6
            Assert.AreEqual(60f, _h.rig.W(FacialTestRig.N("Joy", 1, 1)), 2f);
        }

        [Test]
        public void FixedAnglesOverrideTheViewer()
        {
            _h.rig.runner.useManualAngles = false;
            _h.rig.runner.viewerOverride = _h.NewViewer(new Vector3(0f, 1.5f, 2f)); // 正面
            FacialCorrectionClip c = _h.AddClip(_h.AddTrack(), 0, 2);
            c.template.fixAngles = true; c.template.yaw = 45f; c.template.pitch = 0f;

            _h.At(1.0);
            Assert.AreEqual(45f, _h.rig.runner.CurrentYaw, 1e-2f);
            Assert.AreEqual(50f, N12, 0.5f);
            _h.At(3.0); // 外ではビューアの角度（正面）
            Assert.AreEqual(0f, _h.rig.runner.CurrentYaw, 1e-2f);
            Assert.AreEqual(100f, N11, 0.5f);
        }

        [Test]
        public void PartiallyCoveredFixedAnglesBlendWithTheLiveAngles()
        {
            _h.rig.runner.useManualAngles = false;
            _h.rig.runner.viewerOverride = _h.NewViewer(new Vector3(0f, 1.5f, 2f));
            FacialCorrectionTrack track = _h.AddTrack();
            FacialCorrectionClip a = _h.AddClip(track, 0, 2);
            a.template.fixAngles = true; a.template.yaw = 45f;
            _h.AddClip(track, 1, 2); // 固定しないクリップ

            _h.At(1.5); // 固定する側の重み 0.5 → ライブ（0）と 45 の真ん中
            Assert.AreEqual(22.5f, _h.rig.runner.CurrentYaw, 1.5f);
            Assert.AreEqual(FacialAngleSource.Blended, _h.rig.runner.LastAngleSource);
        }

        [Test]
        public void ClipViewerIsResolvedThroughTheDirectorTable()
        {
            _h.rig.runner.useManualAngles = false;
            Transform v = _h.NewViewer(new Vector3(0f, 1.5f, 2f));
            var key = new PropertyName("facialTestViewer");
            _h.director.SetReferenceValue(key, v);
            FacialCorrectionClip c = _h.AddClip(_h.AddTrack(), 0, 2);
            c.template.viewer = new ExposedReference<Transform> { exposedName = key };

            _h.At(1.0);
            Assert.AreSame(v, _h.rig.runner.LastViewer);
            Assert.AreEqual(FacialViewerSource.Override, _h.rig.runner.LastViewerSource);
            Assert.AreEqual(100f, N11, 0.5f);
        }

        [Test]
        public void CutPoseAddsOnlyInsideTheClipAndDoesNotAccumulate()
        {
            _pose = ScriptableObject.CreateInstance<FacialPoseAsset>();
            _pose.curves = new[] { new FacialPoseCurve { name = "bs.other", value = 0.5f } };
            _pose.bones = new[] { new FacialPoseBone { name = "head", position = new Vector3(0f, 1f, 0f), rotation = Quaternion.identity, scale = Vector3.one } };
            int idx = _h.rig.mesh.GetBlendShapeIndex("bs.other");
            _h.rig.smr.SetBlendShapeWeight(idx, 10f);
            Vector3 p0 = _h.rig.head.transform.localPosition;

            FacialCorrectionClip c = _h.AddClip(_h.AddTrack(), 0, 2);
            c.template.pose = _pose; c.template.poseWeight = 1f;

            for (int i = 0; i < 3; i++)
            {
                _h.At(1.0); // 同じ時刻の繰り返し
                Assert.AreEqual(60f, _h.rig.W("bs.other"), 1e-2f, "積もらない（" + i + "）");
                Assert.AreEqual(p0 + new Vector3(0f, 1f, 0f), _h.rig.head.transform.localPosition);
            }
            _h.At(3.0); // クリップのあと
            Assert.AreEqual(10f, _h.rig.W("bs.other"), 1e-2f);
            Assert.AreEqual(p0, _h.rig.head.transform.localPosition);
        }

        [Test]
        public void DestroyingTheGraphRestoresEverything()
        {
            _pose = ScriptableObject.CreateInstance<FacialPoseAsset>();
            _pose.curves = new[] { new FacialPoseCurve { name = "bs.other", value = 0.5f } };
            _pose.bones = new FacialPoseBone[0];
            _h.rig.smr.SetBlendShapeWeight(_h.rig.mesh.GetBlendShapeIndex("bs.other"), 10f);
            FacialCorrectionClip c = _h.AddClip(_h.AddTrack(), 0, 2);
            c.template.pose = _pose;

            _h.At(1.0);
            Assert.Greater(_h.rig.FcSum(), 50f);
            Assert.AreEqual(60f, _h.rig.W("bs.other"), 1e-2f);

            _h.director.RebuildGraph(); // グラフが壊れる = プレビューを抜ける（Stop() の破棄は次のフレームまで遅れるので、同期で壊れるこちらを使う）
            Assert.AreEqual(0f, _h.rig.FcSum(), 1e-6f, "FC_ が戻る");
            Assert.AreEqual(10f, _h.rig.W("bs.other"), 1e-2f, "カット補正が戻る");
        }

        [Test]
        public void UnboundTrackDoesNothingAndDoesNotThrow()
        {
            FacialCorrectionClip c = _h.AddClip(_h.AddTrack("Role_Facial", false), 0, 2);
            c.template.useAlpha = true; c.template.alpha = 0f;
            Assert.DoesNotThrow(() => _h.At(1.0));
            Assert.AreEqual(0f, _h.rig.FcSum(), 1e-6f, "Runner に触れていない");
        }

        [Test]
        public void BindingToATransformOrGameObjectAlsoFindsTheRunner()
        {
            FacialCorrectionTrack track = _h.AddTrack("Role_Facial", false);
            FacialCorrectionClip c = _h.AddClip(track, 0, 2);
            c.template.useAlpha = true; c.template.alpha = 0.5f;
            _h.director.SetGenericBinding(track, _h.rig.root.transform);
            _h.At(1.0);
            Assert.AreEqual(50f, N11, 0.5f);
        }

        [Test]
        public void FallbackResolverIsUsedWhenTheTrackHasNoBinding()
        {
            var saved = FacialTimelineBinding.FallbackResolver;
            try
            {
                FacialTimelineBinding.FallbackResolver = (d, t) => _h.animator;
                FacialCorrectionClip c = _h.AddClip(_h.AddTrack("Role_Facial", false), 0, 2);
                c.template.useAlpha = true; c.template.alpha = 0.5f;
                _h.At(1.0);
                Assert.AreEqual(50f, N11, 0.5f);
            }
            finally { FacialTimelineBinding.FallbackResolver = saved; }
        }

        [Test]
        public void StepFpsOfTheClipIsUsedByTheRunner()
        {
            FacialCorrectionClip c = _h.AddClip(_h.AddTrack(), 0, 2);
            c.template.useStepFps = true; c.template.stepFps = 12f;
            _h.At(1.0);
            Assert.AreEqual(100f, N11, 0.5f); // 最初のフレームは必ず評価する
            Assert.AreEqual(12f, _h.rig.runner.LastStepFps, 1e-4f);
            _h.At(3.0); // クリップの外 = データの値（0 = 毎フレーム）
            Assert.AreEqual(0f, _h.rig.runner.LastStepFps, 1e-4f);
        }

        [Test]
        public void ClipExaggerationScalesTheExShapeOnlyInsideTheClip()
        {
            string ex = FacialNaming.MorphName(FacialTestRig.Asset, "Neutral", 1, 1, true);
            _h.rig.AddShape(ex);
            var exNames = new string[9];
            for (int i = 0; i < 9; i++) exNames[i] = "";
            exNames[4] = ex;
            _h.rig.data.layers[0].exMorphNames = exNames;
            FacialCorrectionClip c = _h.AddClip(_h.AddTrack(), 0, 2);
            c.template.useExaggeration = true; c.template.exaggeration = 0.5f;

            _h.At(1.0);
            Assert.AreEqual(100f, N11, 0.5f);
            Assert.AreEqual(50f, _h.rig.W(ex), 0.5f);
            _h.At(3.0); // クリップの外 = 作った通り
            Assert.AreEqual(100f, _h.rig.W(ex), 0.5f);
        }

        [Test]
        public void GatherPropertiesRegistersFcShapesPoseShapesAndBones()
        {
            _pose = ScriptableObject.CreateInstance<FacialPoseAsset>();
            _pose.curves = new[] { new FacialPoseCurve { name = "bs.other", value = 0.5f } };
            _pose.bones = new[] { new FacialPoseBone { name = "head", rotation = Quaternion.identity, scale = Vector3.one } };
            FacialCorrectionTrack track = _h.AddTrack();
            _h.AddClip(track, 0, 2).template.pose = _pose;

            var fake = new FakeCollector();
            track.GatherProperties(_h.director, fake);
            CollectionAssert.Contains(fake.names, "blendShape." + FacialTestRig.N("Neutral", 1, 1));
            CollectionAssert.Contains(fake.names, "blendShape.bs.other");
            CollectionAssert.DoesNotContain(fake.names, "blendShape.bs.jaw");
            Assert.AreEqual(1, fake.components.Count);
            Assert.AreSame(_h.rig.head.transform, fake.components[0]);
        }

        [Test]
        public void TrackAttributesAreAsDesigned()
        {
            var bt = (TrackBindingTypeAttribute)System.Attribute.GetCustomAttribute(typeof(FacialCorrectionTrack), typeof(TrackBindingTypeAttribute));
            Assert.AreEqual(typeof(Animator), bt.type);
            var ct = (TrackClipTypeAttribute)System.Attribute.GetCustomAttribute(typeof(FacialCorrectionTrack), typeof(TrackClipTypeAttribute));
            Assert.AreEqual(typeof(FacialCorrectionClip), ct.inspectedType);
            var clip = ScriptableObject.CreateInstance<FacialCorrectionClip>();
            try { Assert.IsTrue((clip.clipCaps & ClipCaps.Blending) != 0); }
            finally { Object.DestroyImmediate(clip); }
        }
    }
}
