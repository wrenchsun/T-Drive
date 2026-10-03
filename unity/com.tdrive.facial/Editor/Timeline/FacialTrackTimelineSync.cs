// .fctrack（FacialTrackAsset）を TimelineAsset へ反映する（FT-3）。D-Drive には依存しない（D-Drive 側の入口は Bridges/DDrive/Editor）。
//  - 自動で作るトラックは「<役名>_Facial(auto)」1 本だけ。範囲いっぱいの 1 クリップが FacialTrackAsset を参照する
//  - 再実行はそのトラックを作り直さず更新する（クリップの手で入れた値は残る）。名前が "(auto)" で終わらない Facial のトラックには触らない
//  - 役名のアニメーショントラックが無ければ何も変えずに報告する
//  - 保存はしない（呼び出し側。ここでは SetDirty まで）
using System;
using System.Collections.Generic;
using TDrive.Facial.Timeline;
using UnityEditor;
using UnityEngine;
using UnityEngine.Timeline;

namespace TDrive.Facial.Timeline.Editor
{
    public sealed class FacialTrackSyncOptions
    {
        /// <summary>自動トラック名の末尾（役名の後ろに付く）。</summary>
        public string AutoSuffix = FacialTrackTimelineSync.AutoSuffix;
        /// <summary>true のとき、役名のアニメーショントラックが無ければ何もしない。</summary>
        public bool RequireRoleTrack = true;
        /// <summary>クリップの最小の長さ（秒）。範囲が空のときに使う。</summary>
        public double MinClipSeconds = 0.01;
    }

    public sealed class FacialTrackSyncReport
    {
        /// <summary>反映できたか（トラックとクリップが期待どおりの状態）。</summary>
        public bool Success;
        /// <summary>Timeline を書き換えたか（再実行で変化が無ければ false）。</summary>
        public bool Changed;
        public bool CreatedTrack, CreatedClip;
        public FacialCorrectionTrack Track;
        public TimelineClip Clip;
        public string TrackName;
        public readonly List<string> Messages = new List<string>();

        public override string ToString()
        {
            return (Success ? "成功" : "失敗") + (Changed ? "（変更あり）" : "（変更なし）") + (Messages.Count > 0 ? ": " + string.Join(" / ", Messages) : "");
        }
    }

    public static class FacialTrackTimelineSync
    {
        public const string AutoSuffix = "_Facial(auto)";

        public static string AutoTrackName(string roleTrackName, FacialTrackSyncOptions options = null)
        {
            return roleTrackName + (options != null && options.AutoSuffix != null ? options.AutoSuffix : AutoSuffix);
        }

        public static FacialTrackSyncReport Apply(TimelineAsset timeline, FacialTrackAsset asset, string roleTrackName, FacialTrackSyncOptions options = null)
        {
            options = options ?? new FacialTrackSyncOptions();
            var report = new FacialTrackSyncReport();
            if (timeline == null) { report.Messages.Add("Timeline が指定されていません"); return report; }
            if (asset == null) { report.Messages.Add(".fctrack（FacialTrackAsset）が指定されていません"); return report; }
            if (string.IsNullOrEmpty(roleTrackName)) { report.Messages.Add("役名（アニメーショントラックの名前）が空です"); return report; }
            string trackName = AutoTrackName(roleTrackName, options);
            report.TrackName = trackName;

            if (options.RequireRoleTrack && FindAnimationTrack(timeline, roleTrackName) == null)
            {
                report.Messages.Add("Timeline に役名 '" + roleTrackName + "' のアニメーショントラックがありません。キャラクターの FBX（<ショット>__" + roleTrackName + ".fbx）を先に取り込んでから、.fctrack を取り込み直してください（何も変更していません）");
                return report;
            }

            FacialCorrectionTrack track = FindAutoTrack(timeline, trackName);
            if (track == null)
            {
                track = timeline.CreateTrack<FacialCorrectionTrack>(null, trackName);
                report.CreatedTrack = true;
                report.Changed = true;
            }
            report.Track = track;

            // 自動トラックの最初の Facial クリップを更新（無ければ作る）。2 つ目以降は触らない
            TimelineClip clip = null;
            foreach (TimelineClip c in track.GetClips())
                if (c.asset is FacialCorrectionClip) { clip = c; break; }
            if (clip == null)
            {
                clip = track.CreateClip<FacialCorrectionClip>();
                report.CreatedClip = true;
                report.Changed = true;
            }
            report.Clip = clip;

            var fc = (FacialCorrectionClip)clip.asset;
            double duration = Math.Max(options.MinClipSeconds, asset.DurationSeconds);
            string display = string.IsNullOrEmpty(asset.name) ? "演出カーブ" : asset.name;

            if (!ReferenceEquals(fc.track, asset)) { fc.track = asset; report.Changed = true; }
            if (Math.Abs(clip.start) > 1e-9) { clip.start = 0d; report.Changed = true; }
            if (Math.Abs(clip.duration - duration) > 1e-6) { clip.duration = duration; report.Changed = true; }
            if (clip.displayName != display) { clip.displayName = display; report.Changed = true; }

            double tlFps = timeline.editorSettings.frameRate;
            if (tlFps > 0 && Math.Abs(tlFps - asset.frameRate) > 1e-3)
                report.Messages.Add("Timeline の fps（" + tlFps + "）と .fctrack の fps（" + asset.frameRate + "）が違います。曲線は秒で再生するので動きは合いますが、キーがコマに乗らないことがあります");

            if (report.Changed)
            {
                EditorUtility.SetDirty(fc);
                EditorUtility.SetDirty(track);
                EditorUtility.SetDirty(timeline);
            }
            report.Success = true;
            report.Messages.Add((report.CreatedTrack ? "トラック '" + trackName + "' を作りました" : report.Changed ? "トラック '" + trackName + "' を更新しました" : "トラック '" + trackName + "' は最新です"));
            return report;
        }

        public static AnimationTrack FindAnimationTrack(TimelineAsset timeline, string name)
        {
            foreach (TrackAsset t in timeline.GetOutputTracks())
            {
                var a = t as AnimationTrack;
                if (a != null && string.Equals(a.name, name, StringComparison.Ordinal)) return a;
            }
            return null;
        }

        static FacialCorrectionTrack FindAutoTrack(TimelineAsset timeline, string trackName)
        {
            foreach (TrackAsset t in timeline.GetOutputTracks())
            {
                var f = t as FacialCorrectionTrack;
                if (f != null && string.Equals(f.name, trackName, StringComparison.Ordinal)) return f;
            }
            return null;
        }
    }
}
