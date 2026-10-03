// トラックの Mixer。重なったクリップを重みでブレンドし、Runner へ SetOverride（持ち主 = この Mixer）で渡す（ブレンドシェイプは直接書かない）。
//  - 上書きは次の ProcessFrame（または消すとき）まで Runner に残る → D-Drive のカットシーンの一時停止中（評価が来ない）も補正が保たれる（E-3）
//  - 消すとき: クリップの無い区間（重みの合計が 0）/ OnGraphStop / OnPlayableDestroy / バインドが替わったとき。Runner 側も OnDisable・破棄で捨てる
//  - 同じ Runner に Facial のトラックが複数あっても、持ち主ごとに合成される（規則は FacialCorrectionRunner.SetOverride）。"(auto)" のトラックは優先度 -1（手で作ったトラックが同じレイヤーを書けば手のほうが勝つ）
//  - 強さ: 重みつきで 1（= 変えない）へ寄せる / 感情: レイヤー名 → Runner のレイヤー番号の重み付き加算
//  - 角度の固定: 固定するクリップの重みの合計 = 手動の割合（manualAngleBlend）。足りない分はライブの角度
//  - 曲線（.fctrack）: クリップの track があれば、各クリップの中の時刻で読んだ値（UpdateEffective）をブレンドに使う。手で入れた値が優先
//  - 視点・コマ打ち: いちばん重みの大きいクリップ / カット補正: 重みの合計が最大のポーズ
//  - 再生中: 渡すだけ（Runner の LateUpdate が評価）。編集時（Timeline ウィンドウのスクラブ）: LateUpdate が来ないので EvaluateNow を自分で呼ぶ
//  - プレビューを抜けたら ResetWeights で FC_ とカット補正を元へ戻す（編集時のみ。再生中は Runner が次の LateUpdate で戻す）
using TDrive.Facial.Core;
using UnityEngine;
using UnityEngine.Playables;
using UnityEngine.Timeline;

namespace TDrive.Facial.Timeline
{
    public sealed class FacialCorrectionMixerBehaviour : PlayableBehaviour
    {
        /// <summary>編集時プレビューで EvaluateNow に渡す deltaTime（大きくして、スクラブでも重みが即座に目標へ着くようにする）。</summary>
        const float EditDeltaTime = 10f;

        public PlayableDirector director;
        public TrackAsset track;

        FacialCorrectionRunner _runner;
        bool _owns; // _runner に自分の上書きを置いているか
        UnityEngine.Object _keyObject;
        bool _hasKey;
        float[] _emo = new float[0];
        bool[] _emoTouched = new bool[0];

        public override void ProcessFrame(Playable playable, FrameData info, object playerData)
        {
            FacialCorrectionRunner runner = FindRunner(playerData as UnityEngine.Object);
            if (runner == null) return; // フェイルソフト（バインドも Runner も無ければ何もしない）

            int count = playable.GetInputCount();
            float total = 0f;
            for (int i = 0; i < count; i++)
            {
                float w = playable.GetInputWeight(i);
                if (w > 0f) total += w;
            }

            if (total > 0f)
            {
                FacialFrameOverride ov = Blend(playable, count, total, runner);
                runner.SetOverride(this, ov, Priority(), director);
                _owns = true;
            }
            else if (_owns)
            {
                runner.ClearOverride(this); // クリップの無い区間では通常へ戻す
                _owns = false;
            }

            // 編集時は LateUpdate が来ない。ここで評価する（再生中は Runner の LateUpdate に任せる）
            if (!Application.isPlaying) runner.EvaluateNow(EditViewer(runner), EditDeltaTime);
        }

        FacialFrameOverride Blend(Playable playable, int count, float total, FacialCorrectionRunner runner)
        {
            var ov = new FacialFrameOverride();
            for (int i = 0; i < count; i++)
            {
                FacialCorrectionBehaviour eb = Behaviour(playable, i);
                if (eb != null && playable.GetInputWeight(i) > 0f) eb.UpdateEffective(playable.GetInput(i).GetTime()); // .fctrack の曲線を読む
            }
            float norm = total > 1f ? 1f / total : 1f; // 重なりで 1 を超えたら正規化
            float covered = Mathf.Min(1f, total);       // クリップが覆っている割合（残りは「クリップなし」= 変えない）

            // 強さ
            float alphaSum = 0f;
            bool anyAlpha = false;
            for (int i = 0; i < count; i++)
            {
                FacialCorrectionBehaviour b = Behaviour(playable, i);
                float w = playable.GetInputWeight(i);
                if (b == null || w <= 0f) continue;
                w *= norm;
                if (b.effUseAlpha) { anyAlpha = true; alphaSum += w * Mathf.Clamp01(b.effAlpha); }
                else alphaSum += w;
            }
            if (anyAlpha)
            {
                ov.hasAlpha = true;
                ov.alpha = alphaSum + (1f - covered); // クリップのない分は 1
            }

            // 誇張: 「誇張を使う」のクリップの値を重みで混ぜる（使わないクリップ・クリップのない分は 1）
            float exSum = 0f;
            bool anyEx = false;
            for (int i = 0; i < count; i++)
            {
                FacialCorrectionBehaviour b = Behaviour(playable, i);
                float w = playable.GetInputWeight(i);
                if (b == null || w <= 0f) continue;
                w *= norm;
                if (b.effUseExaggeration) { anyEx = true; exSum += w * Mathf.Clamp01(b.effExaggeration); }
                else exSum += w;
            }
            if (anyEx)
            {
                ov.hasExaggeration = true;
                ov.exaggeration = exSum + (1f - covered);
            }

            // 感情の重み: Runner の値を土台に、書いてあるレイヤーだけを重みで寄せる
            FacialCorrectionData data = runner.data;
            int layers = data != null && data.layers != null ? data.layers.Length : 0;
            if (layers > 1)
            {
                bool anyEmo = false;
                for (int i = 0; i < count && !anyEmo; i++)
                {
                    FacialCorrectionBehaviour b = Behaviour(playable, i);
                    if (b != null && playable.GetInputWeight(i) > 0f && b.effEmotions != null && b.effEmotions.Length > 0) anyEmo = true;
                }
                if (anyEmo)
                {
                    if (_emo.Length != layers) { _emo = new float[layers]; _emoTouched = new bool[layers]; }
                    // 書いていないレイヤーは NaN（= 指定なし。Runner が自分の値か距離の重みを使う）にして、ほかのトラックの値を消さない
                    for (int l = 0; l < layers; l++) { _emoTouched[l] = false; _emo[l] = float.NaN; }
                    float[] baseW = runner.emotionWeights;
                    for (int i = 0; i < count; i++)
                    {
                        FacialCorrectionBehaviour b = Behaviour(playable, i);
                        float w = playable.GetInputWeight(i);
                        if (b == null || w <= 0f || b.effEmotions == null) continue;
                        w *= norm;
                        for (int e = 0; e < b.effEmotions.Length; e++)
                        {
                            int li = LayerIndex(data, b.effEmotions[e].layer);
                            if (li <= 0) continue; // 0 番（Neutral）と未知の名前は無視
                            float baseValue = baseW != null && li < baseW.Length ? baseW[li] : 0f;
                            if (!_emoTouched[li]) { _emoTouched[li] = true; _emo[li] = baseValue; }
                            _emo[li] += w * (Mathf.Max(0f, b.effEmotions[e].weight) - baseValue);
                        }
                    }
                    ov.emotionWeights = _emo;
                }
            }

            // 角度の固定
            float amount = 0f;
            float refYaw = 0f;
            bool haveRef = false;
            float yawDelta = 0f, pitchSum = 0f;
            for (int i = 0; i < count; i++)
            {
                FacialCorrectionBehaviour b = Behaviour(playable, i);
                float w = playable.GetInputWeight(i);
                if (b == null || w <= 0f || !b.effFixAngles) continue;
                w *= norm;
                if (!haveRef) { refYaw = b.effYaw; haveRef = true; }
                amount += w;
                yawDelta += w * Mathf.DeltaAngle(refYaw, b.effYaw); // ±180 をまたぐ角度でも正しく混ぜる
                pitchSum += w * b.effPitch;
            }
            if (haveRef && amount > 1e-5f)
            {
                ov.hasManualAngles = true;
                ov.yaw = refYaw + yawDelta / amount;
                ov.pitch = pitchSum / amount;
                ov.manualAngleBlend = Mathf.Min(1f, amount);
            }

            // 視点・コマ打ち: いちばん重みの大きいクリップ
            float bestViewer = 0f, bestFps = 0f;
            for (int i = 0; i < count; i++)
            {
                FacialCorrectionBehaviour b = Behaviour(playable, i);
                float w = playable.GetInputWeight(i);
                if (b == null || w <= 0f) continue;
                if (b.resolvedViewer != null && w > bestViewer) { bestViewer = w; ov.viewer = b.resolvedViewer; }
                if (b.useStepFps && w > bestFps) { bestFps = w; ov.hasStepFps = true; ov.stepFps = b.stepFps; }
            }

            // カット補正: 同じポーズの重み（クリップの重み × ポーズの重み）の合計が最大のもの。重なったクリップのポーズが違うときは大きいほうだけ
            float bestPose = 0f;
            for (int i = 0; i < count; i++)
            {
                FacialCorrectionBehaviour b = Behaviour(playable, i);
                float w = playable.GetInputWeight(i);
                if (b == null || b.pose == null || w <= 0f) continue;
                float sum = 0f;
                for (int k = 0; k < count; k++)
                {
                    FacialCorrectionBehaviour o = Behaviour(playable, k);
                    float wk = playable.GetInputWeight(k);
                    if (o != null && wk > 0f && ReferenceEquals(o.pose, b.pose)) sum += wk * norm * Mathf.Clamp01(o.poseWeight);
                }
                if (sum > bestPose) { bestPose = sum; ov.pose = b.pose; ov.poseWeight = Mathf.Min(1f, sum); }
            }
            return ov;
        }

        static FacialCorrectionBehaviour Behaviour(Playable playable, int index)
        {
            Playable input = playable.GetInput(index);
            if (!input.IsValid()) return null;
            return ((ScriptPlayable<FacialCorrectionBehaviour>)input).GetBehaviour();
        }

        static int LayerIndex(FacialCorrectionData data, string layerName)
        {
            if (string.IsNullOrEmpty(layerName)) return -1;
            for (int i = 0; i < data.layers.Length; i++)
                if (string.Equals(data.layers[i].name, layerName, System.StringComparison.Ordinal)) return i;
            return -1;
        }

        // 見つからなかったとき、毎フレーム探し直さない（フォールバックは Timeline 全体を走査する）。同じバインドの間は一定フレームおきにだけ再試行する
        const int MissRetryFrames = 120;
        bool _hasMiss;
        UnityEngine.Object _missBound;
        int _missFrames;

        void Miss(UnityEngine.Object bound)
        {
            _hasMiss = true; _missBound = bound; _missFrames = 0;
        }

        // バインド（無ければフォールバック）→ Runner。同じバインドなら前回の結果（見つからなかった結果も）を使う
        FacialCorrectionRunner FindRunner(UnityEngine.Object bound)
        {
            if (_hasMiss && ReferenceEquals(bound, _missBound) && ++_missFrames < MissRetryFrames) return null;
            UnityEngine.Object key = FacialTimelineBinding.ResolveBindingObject(director, track, bound);
            if (key == null) { Miss(bound); return null; }
            if (_hasKey && ReferenceEquals(key, _keyObject) && _runner != null) { _hasMiss = false; return _runner; }
            ReleaseRunner(); // バインドが替わったら前の Runner の上書きを消す
            _keyObject = key;
            _hasKey = true;
            _runner = FacialTimelineBinding.RunnerFrom(key);
            if (_runner == null) Miss(bound); else _hasMiss = false;
            return _runner;
        }

        // 自動で作ったトラック（"(auto)"）は弱くする（手で作ったトラックが同じレイヤー・視点を書けば、そちらが勝つ）
        int Priority()
        {
            return track != null && track.name != null && track.name.EndsWith("(auto)", System.StringComparison.Ordinal) ? -1 : 0;
        }

        // 編集時プレビューの視点（Runner の editViewer）。
        //   Auto = D-Drive などの Fallback が視点を返すなら null（Runner が Fallback を使う）→ メインカメラ → Scene ビュー
        //   SceneView / MainCamera = その指定が Fallback より先（Transform を渡す）
        static Transform EditViewer(FacialCorrectionRunner runner)
        {
            FacialEditViewer mode = runner != null ? runner.editViewer : FacialEditViewer.Auto;
            if (mode == FacialEditViewer.Auto && runner != null)
            {
                Vector3 p; Quaternion r; float f;
                if (FacialViewResolver.TryResolve(runner.transform, out p, out r, out f)) return null;
            }
            Camera main = mode == FacialEditViewer.SceneView ? null : Camera.main;
            if (main != null) return main.transform;
#if UNITY_EDITOR
            UnityEditor.SceneView sv = UnityEditor.SceneView.lastActiveSceneView;
            if (sv != null && sv.camera != null) return sv.camera.transform;
#endif
            Camera cam = Camera.main;
            return cam != null ? cam.transform : null;
        }

        void ReleaseRunner()
        {
            if (_runner != null && _owns) _runner.ClearOverride(this);
            _owns = false;
        }

        // グラフが止まったとき・壊れたとき: 上書きを消す。編集時のプレビューでは FC_ とカット補正も元へ戻す（再生中は Runner が次の LateUpdate / OnDisable で戻す）
        public override void OnGraphStop(Playable playable)
        {
            ReleaseRunner();
            if (!Application.isPlaying && _runner != null) _runner.ResetWeights();
        }

        public override void OnPlayableDestroy(Playable playable)
        {
            ReleaseRunner();
            if (!Application.isPlaying && _runner != null) _runner.ResetWeights();
            _runner = null;
            _keyObject = null;
            _hasKey = false;
        }
    }
}
