// .fctrack（FacialTrackAsset）を TimelineAsset へ反映する（FT-3）。D-Drive には依存しない（D-Drive 側の入口は Bridges/DDrive/Editor）。
//  - 自動で作るトラックは「<役名>_Facial(auto)」1 本だけ。範囲いっぱいの 1 クリップが FacialTrackAsset を参照する
//  - 再実行はそのトラックを作り直さず更新する（クリップの手で入れた値は残る）。名前が "(auto)" で終わらない Facial のトラックには触らない
//  - クリップの位置・長さ・開始位置は、作ったとき（と、前回の反映のあとデザイナーが動かしていないとき）だけ決める。動かしてあれば触らない（docs/19 E-6。
//    前回書いた値をクリップの syncStamp に覚えておき、今の値と同じときだけ更新する）
//  - D-Drive の SourceFrameRange（FBX から切り出すフレーム）があれば、アニメーションと同じ範囲になるよう開始位置と長さを決める（docs/19 E-5）
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
        /// <summary>
        /// D-Drive の CutsceneData.SourceFrameRange（FBX から切り出すフレーム。End を含む。Start = End = 0 は「全体」で何もしない）。
        /// FBX の時間 = Maya の絶対時間（フレーム ÷ fps）と仮定し、アニメーションの 0 秒（= Start フレーム）に .fctrack の曲線の同じ時刻が当たるよう clipIn / 開始位置を決める。
        /// </summary>
        public int SourceStartFrame, SourceEndFrame;
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
            string display = string.IsNullOrEmpty(asset.name) ? "演出カーブ" : asset.name;
            double wantStart, wantClipIn, wantDuration;
            ComputePlacement(asset, options, out wantStart, out wantClipIn, out wantDuration);

            if (!ReferenceEquals(fc.track, asset)) { fc.track = asset; report.Changed = true; }

            // 位置・長さ・開始位置: 作ったとき、または前回の反映の値のままのときだけ決める（デザイナーが動かしたものは戻さない）
            FacialClipSyncStamp st = fc.syncStamp;
            bool mayPlace = report.CreatedClip
                || (st.valid && Near(clip.start, st.start) && Near(clip.duration, st.duration) && Near(clip.clipIn, st.clipIn));
            if (mayPlace)
            {
                if (!Near(clip.start, wantStart)) { clip.start = wantStart; report.Changed = true; }
                if (!Near(clip.clipIn, wantClipIn)) { clip.clipIn = wantClipIn; report.Changed = true; }
                if (!Near(clip.duration, wantDuration)) { clip.duration = wantDuration; report.Changed = true; }
            }
            else report.Messages.Add("クリップ '" + clip.displayName + "' の位置・長さはデザイナーが変えているので動かしていません（.fctrack の参照だけ更新しました）");
            bool mayName = report.CreatedClip || (st.valid && clip.displayName == st.displayName);
            if (mayName && clip.displayName != display) { clip.displayName = display; report.Changed = true; }
            if (mayPlace || mayName)
            {
                var now = new FacialClipSyncStamp
                {
                    valid = true,
                    start = mayPlace ? clip.start : st.start, duration = mayPlace ? clip.duration : st.duration, clipIn = mayPlace ? clip.clipIn : st.clipIn,
                    displayName = mayName ? clip.displayName : st.displayName,
                };
                if (!st.valid || !Near(now.start, st.start) || !Near(now.duration, st.duration) || !Near(now.clipIn, st.clipIn) || now.displayName != st.displayName)
                {
                    fc.syncStamp = now;
                    report.Changed = true;
                }
            }

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

        static bool Near(double a, double b) { return Math.Abs(a - b) <= 1e-6; }

        /// <summary>
        /// クリップの開始位置（start）・clipIn・長さを求める。SourceFrameRange が無ければ start = 0・clipIn = 0・長さ = .fctrack の範囲。
        /// あれば アニメーションの 0 秒 = Start フレーム に .fctrack の同じ時刻を合わせ、長さはアニメーションの範囲（Start〜End）と .fctrack の残りの短いほう。
        /// </summary>
        public static void ComputePlacement(FacialTrackAsset asset, FacialTrackSyncOptions options, out double start, out double clipIn, out double duration)
        {
            start = 0d; clipIn = 0d;
            double total = asset.DurationSeconds;
            duration = Math.Max(options.MinClipSeconds, total);
            double fps = asset.frameRate;
            bool trim = options.SourceStartFrame != 0 || options.SourceEndFrame != 0;
            if (!trim || !(fps > 0d)) return;
            double offset = options.SourceStartFrame / fps - asset.rangeStart / fps; // アニメーションの 0 秒 が .fctrack の何秒か
            if (offset >= 0d) clipIn = offset; else start = -offset;
            double avail = total - clipIn;
            double animLen = options.SourceEndFrame >= options.SourceStartFrame ? (options.SourceEndFrame - options.SourceStartFrame + 1) / fps : double.MaxValue;
            duration = Math.Max(options.MinClipSeconds, Math.Min(avail, animLen - start));
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
