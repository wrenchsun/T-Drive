// Timeline のテスト用の足場。TimelineAsset と PlayableDirector をメモリ上（HideAndDontSave）で作る。何も保存しない・ユーザーのシーンに入れない。
using System;
using System.Collections.Generic;
using TDrive.Facial.Timeline;
using UnityEditor;
using UnityEngine;
using UnityEngine.Playables;
using UnityEngine.Timeline;
using Object = UnityEngine.Object;

namespace TDrive.Facial.Tests.Timeline
{
    public sealed class FacialTimelineTestHarness : IDisposable
    {
        public readonly FacialTestRig rig;
        public readonly TimelineAsset timeline;
        public readonly GameObject directorObject;
        public readonly PlayableDirector director;
        public readonly Animator animator;
        readonly List<Object> _owned = new List<Object>();

        public FacialTimelineTestHarness(bool prefixed = false)
        {
            rig = new FacialTestRig(true, prefixed ? "bs." : "");
            rig.runner.useManualAngles = true; // 視点なしで動かす（個別のテストで切り替える）
            animator = rig.root.AddComponent<Animator>();
            timeline = ScriptableObject.CreateInstance<TimelineAsset>();
            timeline.hideFlags = HideFlags.HideAndDontSave;
            directorObject = EditorUtility.CreateGameObjectWithHideFlags("TestDirector", HideFlags.HideAndDontSave);
            director = directorObject.AddComponent<PlayableDirector>();
            director.playOnAwake = false;
            director.timeUpdateMode = DirectorUpdateMode.Manual;
            director.playableAsset = timeline;
        }

        public FacialCorrectionTrack AddTrack(string name = "Role_Facial", bool bind = true)
        {
            var track = timeline.CreateTrack<FacialCorrectionTrack>(name);
            if (bind) director.SetGenericBinding(track, animator);
            return track;
        }

        public FacialCorrectionClip AddClip(FacialCorrectionTrack track, double start, double duration)
        {
            TimelineClip clip = track.CreateClip<FacialCorrectionClip>();
            clip.start = start;
            clip.duration = duration;
            return (FacialCorrectionClip)clip.asset;
        }

        public void At(double time)
        {
            director.time = time;
            director.Evaluate();
        }

        public Transform NewViewer(Vector3 position)
        {
            GameObject go = EditorUtility.CreateGameObjectWithHideFlags("TestViewer", HideFlags.HideAndDontSave);
            go.transform.position = position;
            _owned.Add(go);
            return go.transform;
        }

        public void Dispose()
        {
            if (director != null) director.Stop();
            if (directorObject != null) Object.DestroyImmediate(directorObject);
            if (timeline != null) Object.DestroyImmediate(timeline);
            for (int i = 0; i < _owned.Count; i++) if (_owned[i] != null) Object.DestroyImmediate(_owned[i]);
            rig.Dispose();
        }
    }
}
