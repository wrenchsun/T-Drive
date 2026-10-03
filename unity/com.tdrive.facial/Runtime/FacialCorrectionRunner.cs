// キャラクターの Prefab に付けるランタイム。処理の順は docs/14 §6.2（UE 版 UFacialCorrectionComponent と同じ）:
//  1 視点 → 2 角度 → 3 スナップ → 4 感情の重み → 5 格子の計算（イプシロンで使い回し）→ 6 全体に掛ける
//  → 7 可動域・スムージング・書き込み（LateUpdate）→ 8 マテリアル（FU-4 で追加）
// 計算は Core（FacialCore）。ここは Unity の入出力（座標の変換・ブレンドシェイプの読み書き）だけ。
// 約束: データは読み取り専用 / 書くのは FC_ だけ / 無い名前は黙って飛ばす（フェイルソフト）/ 無効化で書いた分を 0 へ戻す
//       / ウォームアップ後は毎フレームの GC 割り当てなし（LINQ・文字列の組み立てを LateUpdate に置かない）。
using System;
using System.Collections.Generic;
using TDrive.Facial.Core;
using UnityEngine;

namespace TDrive.Facial
{
    /// <summary>外からの 1 フレームだけの上書き（Timeline など）。次の LateUpdate（または EvaluateNow）で使われて消える。</summary>
    public struct FacialFrameOverride
    {
        /// <summary>true のとき alpha を全体の強さへ掛ける。</summary>
        public bool hasAlpha;
        public float alpha;
        /// <summary>true のとき yaw / pitch を使う（視点の計算より優先）。</summary>
        public bool hasManualAngles;
        public float yaw;
        public float pitch;
        /// <summary>レイヤーごとの感情の重み（index 0 は無視）。null = 上書きなし。配列は呼び出し側のもの（コピーしない）。</summary>
        public float[] emotionWeights;
        /// <summary>視点の Transform。null = 上書きなし。</summary>
        public Transform viewer;
    }

    [DefaultExecutionOrder(10000)] // Animator・D-Drive の AnimManager（Update）より後
    [DisallowMultipleComponent]
    [AddComponentMenu("T-Drive/Facial Correction Runner")]
    public sealed class FacialCorrectionRunner : MonoBehaviour
    {
        const double ToWeight = 0.01; // Unity のブレンドシェイプは 0〜100

        [Header("データ")]
        [Tooltip("取り込んだ補正データ（.fcpose から作られる）。空のときは何もしない")]
        public FacialCorrectionData data;

        [Tooltip("調整値の上書き（任意）。取り込み直しても残る")]
        public FacialCorrectionOverrides overrides;

        [Header("対象")]
        [Tooltip("補正を書き込むメッシュ。空なら子から FC_<アセット名>_ のシェイプを持つものを自動で探す")]
        public SkinnedMeshRenderer[] targets;

        [Tooltip("角度の基準にするボーン。空ならデータの baseBone を名前で子から探す")]
        public Transform baseBone;

        [Header("視点")]
        [Tooltip("視点にする Transform。空ならメインカメラ")]
        public Transform viewerOverride;

        [Tooltip("true のとき視点を使わず、下の角度を直接使う")]
        public bool useManualAngles;

        [Tooltip("手動の Yaw（度）。キャラクターの正面 = 0、カメラが左（キャラクターから見て）で正")]
        public float manualYaw;

        [Tooltip("手動の Pitch（度）。カメラが上（ふかん）で正")]
        public float manualPitch;

        [Header("強さ・感情")]
        [Tooltip("このコンポーネントの強さ（0〜1）。全体の強さに掛かる")]
        [Range(0f, 1f)] public float alpha = 1f;

        [Tooltip("レイヤーごとの感情の重み（レイヤー番号の順。0 番の Neutral は無視。0 以上）")]
        public float[] emotionWeights;

        [Tooltip("true にしたレイヤーは感情の重みを 0 として扱う（レイヤー番号の順）")]
        public bool[] mutedLayers;

        [Header("最適化")]
        [Tooltip("true のとき、対象のメッシュがどのカメラにも映っていない間は計算しない")]
        public bool skipWhenNotVisible;

        // --- 診断（読み取り専用） ---
        public float CurrentYaw { get { return (float)_curYaw; } }
        public float CurrentPitch { get { return (float)_curPitch; } }
        /// <summary>直近の評価でスナップ（カット切り替え）と判定したか。</summary>
        public bool Snapped { get { return _snapped; } }
        /// <summary>直近の評価で書いた補正シェイプの数（スムージング後）。</summary>
        public int ActiveWeightCount { get { return _last.Count; } }
        /// <summary>直近の評価で補正に掛けた倍率の合計（表情 × 距離フェード × 全体の強さ）。</summary>
        public float LastScale { get { return (float)_lastScale; } }
        /// <summary>直近の評価で視点から角度を求められたか（手動・視点なしは false）。</summary>
        public bool HasValidAngles { get { return _hasPrev; } }

        sealed class ShapeBinding
        {
            public SkinnedMeshRenderer[] renderers;
            public int[] indices;
            public int count;
            public bool written;
            public bool hasLimit;
            public float limMin, limMax;
        }

        static readonly SpaceConverter UnityToCanonical = FacialSpace.Converter(FacialSpace.Unity, FacialSpace.Canonical);
        static readonly List<SkinnedMeshRenderer> ScanRenderers = new List<SkinnedMeshRenderer>(8);
        static readonly List<Transform> ScanTransforms = new List<Transform>(64);

        // 名前 → 書き込み先（最初に 1 度だけ引く）
        readonly Dictionary<string, ShapeBinding> _bindings = new Dictionary<string, ShapeBinding>(StringComparer.Ordinal);
        readonly List<ShapeBinding> _written = new List<ShapeBinding>(64);
        readonly List<string> _missing = new List<string>();
        readonly List<SkinnedMeshRenderer> _targets = new List<SkinnedMeshRenderer>(4);
        readonly List<Mesh> _targetMeshes = new List<Mesh>(4);
        readonly List<LayerEvalInput> _layerInputs = new List<LayerEvalInput>(8);
        SkinnedMeshRenderer[] _intRenderers = new SkinnedMeshRenderer[0];
        int[] _intIndices = new int[0];
        GridShape _grid = new GridShape();
        double[] _emo = new double[0];
        double[] _rawEmo = new double[0];

        // 計算の途中経過（再利用するので毎フレーム割り当てない）
        readonly List<MorphWeight> _raw = new List<MorphWeight>(64);
        readonly List<MorphWeight> _scaled = new List<MorphWeight>(64);
        List<MorphWeight> _last = new List<MorphWeight>(64);
        List<MorphWeight> _smoothed = new List<MorphWeight>(64);

        // キャッシュの鍵
        bool _built;
        FacialCorrectionData _cacheData;
        Transform _cacheBaseField;
        SkinnedMeshRenderer[] _cacheTargetsField;
        int _cacheTargetsLen;
        bool _cacheAuto;
        int _scanFrame = int.MinValue;
        Transform _baseResolved;
        string _forwardAxis = "+Z";
        bool _warnedBase;

        // 状態
        bool _hasPrev;
        double _prevYaw, _prevPitch;
        bool _hasRaw;
        double _rawYaw, _rawPitch, _rawEdge;
        double _curYaw, _curPitch, _lastScale = 1.0;
        bool _snapped;
        bool _hasPending;
        FacialFrameOverride _pending;

        // ---------------------------------------------------------------- 公開 API

        /// <summary>次の LateUpdate（または EvaluateNow）だけ有効な上書きを渡す。使われたら消える。</summary>
        public void PushOverride(in FacialFrameOverride o)
        {
            _pending = o;
            _hasPending = true;
        }

        /// <summary>LateUpdate と同じ計算を今すぐ 1 回行う（エディタのプレビュー・テスト用）。viewer が null なら視点の解決は通常どおり。</summary>
        public void EvaluateNow(Transform viewer, float deltaTime)
        {
            Step(deltaTime, viewer, false, 0.0, 0.0);
        }

        /// <summary>角度（度）を直接渡して今すぐ 1 回計算する。</summary>
        public void EvaluateNow(float yawDeg, float pitchDeg, float deltaTime)
        {
            Step(deltaTime, null, true, yawDeg, pitchDeg);
        }

        /// <summary>書いた FC_ シェイプをすべて 0 に戻し、計算の状態も捨てる（次の評価はスナップ）。FC_ 以外には触らない。</summary>
        public void ResetWeights()
        {
            for (int i = 0; i < _written.Count; i++)
            {
                ShapeBinding b = _written[i];
                WriteRaw(b, 0f);
                b.written = false;
            }
            _written.Clear();
            _last.Clear();
            _smoothed.Clear();
            _raw.Clear();
            _scaled.Clear();
            _hasPrev = false;
            _hasRaw = false;
            _snapped = false;
        }

        /// <summary>名前 → 番号の対応と対象・基準ボーンの解決をやり直す（Prefab の構造やメッシュを変えたとき）。</summary>
        public void RebuildCaches()
        {
            Rebuild();
        }

        /// <summary>データにあるのにどの対象メッシュにも無いシェイプ名（フェイルソフトで飛ばしたもの）。</summary>
        public IReadOnlyList<string> GetMissingShapeNames()
        {
            EnsureCache();
            return _missing;
        }

        /// <summary>見つかった FC_ シェイプの数（名前の種類）。</summary>
        public int BoundShapeCount { get { EnsureCache(); return _bindings.Count; } }

        /// <summary>解決した対象メッシュ。</summary>
        public IReadOnlyList<SkinnedMeshRenderer> ResolvedTargets { get { EnsureCache(); return _targets; } }

        /// <summary>解決した基準ボーン（無ければ null）。</summary>
        public Transform ResolvedBaseBone { get { EnsureCache(); return _baseResolved; } }

        /// <summary>感情の重みを 1 つ設定する（配列が足りなければ広げる）。</summary>
        public void SetEmotionWeight(int layer, float weight)
        {
            if (layer < 0) return;
            if (emotionWeights == null || emotionWeights.Length <= layer) Array.Resize(ref emotionWeights, layer + 1);
            emotionWeights[layer] = weight;
        }

        /// <summary>レイヤーのミュートを設定する（配列が足りなければ広げる）。</summary>
        public void SetLayerMuted(int layer, bool muted)
        {
            if (layer < 0) return;
            if (mutedLayers == null || mutedLayers.Length <= layer) Array.Resize(ref mutedLayers, layer + 1);
            mutedLayers[layer] = muted;
        }

        // ---------------------------------------------------------------- Unity

        void OnEnable()
        {
            _built = false; // 有効化のたびに引き直す
            _warnedBase = false;
        }

        void OnDisable()
        {
            ResetWeights(); // 書いた FC_ を 0 に戻す（プールへ返すときも）
        }

        void LateUpdate()
        {
            if (skipWhenNotVisible && !AnyTargetVisible()) return;
            Step(Time.deltaTime, null, false, 0.0, 0.0);
        }

        bool AnyTargetVisible()
        {
            EnsureCache();
            for (int i = 0; i < _targets.Count; i++)
                if (_targets[i] != null && _targets[i].isVisible) return true;
            return _targets.Count == 0;
        }

        // ---------------------------------------------------------------- 本体

        void Step(float deltaTime, Transform viewerParam, bool explicitAngles, double explicitYaw, double explicitPitch)
        {
            // 外からの上書きは 1 回で消す
            FacialFrameOverride ov = _pending;
            bool hadOverride = _hasPending;
            _pending = default(FacialFrameOverride);
            _hasPending = false;

            FacialCorrectionData d = data;
            if (d == null || d.layers == null || d.layers.Length == 0)
            {
                if (_written.Count > 0) ResetWeights(); // データを外したら書いた分を戻す
                return;
            }
            EnsureCache();
            if (_targets.Count == 0) return;

            FacialEffectiveParams p = FacialCorrectionOverrides.Resolve(d, overrides);

            // 1 視点: 手動の角度 > 指定した Transform > メインカメラ
            Transform viewer = null;
            if (hadOverride && ov.viewer != null) viewer = ov.viewer;
            else if (viewerOverride != null) viewer = viewerOverride;
            else if (viewerParam != null) viewer = viewerParam;
            else
            {
                Camera cam = Camera.main;
                if (cam != null) viewer = cam.transform;
            }

            // 2 角度
            double yaw, pitch;
            if (explicitAngles) { yaw = explicitYaw; pitch = explicitPitch; }
            else if (hadOverride && ov.hasManualAngles) { yaw = ov.yaw; pitch = ov.pitch; }
            else if (useManualAngles) { yaw = manualYaw; pitch = manualPitch; }
            else
            {
                if (viewer == null) return; // 視点が無いときは何もしない（前回の状態を保つ）
                Transform bone = _baseResolved;
                if (bone == null)
                {
                    if (!_warnedBase)
                    {
                        _warnedBase = true;
                        Debug.LogWarning("[FacialCorrectionRunner] 基準ボーン '" + d.grid.baseBone + "' が見つかりません。角度を計算できないので補正を掛けません（手動の角度なら動きます）: " + name, this);
                    }
                    return; // フェイルソフト
                }
                Vector3 hp = bone.position;
                Quaternion hr = bone.rotation;
                Vector3 vp = viewer.position;
                Vector3 co = d.grid.centerOffset;
                FacialSpace.ComputeViewAnglesInSpace(UnityToCanonical,
                    new Vec3(hp.x, hp.y, hp.z), new Quat(hr.x, hr.y, hr.z, hr.w), _forwardAxis,
                    new Vec3(vp.x, vp.y, vp.z), new Vec3(co.x, co.y, co.z), out yaw, out pitch);
            }
            _curYaw = yaw;
            _curPitch = pitch;

            // 3 スナップ（カット切り替え）
            bool snap = FacialCore.ShouldSnap(_hasPrev, _prevYaw, _prevPitch, yaw, pitch, p.snapAngle);
            _hasPrev = true;
            _prevYaw = yaw;
            _prevPitch = pitch;
            _snapped = snap;

            // 4 感情の重み（0 以上。ミュートしたレイヤーは 0 = 除く）
            float[] emoSource = hadOverride && ov.emotionWeights != null ? ov.emotionWeights : emotionWeights;
            int layerCount = d.layers.Length;
            if (_emo.Length != layerCount) { _emo = new double[layerCount]; _rawEmo = new double[layerCount]; _hasRaw = false; }
            bool emoSame = true;
            for (int i = 0; i < layerCount; i++)
            {
                double w = 0.0;
                if (i > 0 && emoSource != null && i < emoSource.Length)
                {
                    float f = emoSource[i];
                    if (f > 0f) w = f; // NaN・負は 0
                }
                if (mutedLayers != null && i < mutedLayers.Length && mutedLayers[i]) w = 0.0;
                _emo[i] = w;
                if (w != _rawEmo[i]) emoSame = false;
                LayerEvalInput li = _layerInputs[i];
                li.EmotionWeight = w;
                li.Enabled = d.layers[i].enabled;
            }

            // 5 格子の計算。角度の変化が小さく感情も同じなら前回の結果を使う
            double eps = d.quality.angleEpsilon;
            _grid.EdgeFadeDeg = p.edgeFade;
            bool reuse = _hasRaw && eps > 0.0 && emoSame && p.edgeFade == _rawEdge
                && System.Math.Abs(FacialCore.NormalizeAxis(yaw - _rawYaw)) < eps
                && System.Math.Abs(pitch - _rawPitch) < eps;
            if (!reuse)
            {
                FacialCore.EvaluateCorrection(_grid, _layerInputs, yaw, pitch, _raw);
                for (int i = 0; i < layerCount; i++) _rawEmo[i] = _emo[i];
                _rawYaw = yaw; _rawPitch = pitch; _rawEdge = p.edgeFade;
                _hasRaw = true;
            }

            // 6 全体に掛ける: 表情での弱め × 距離フェード × 全体の強さ × コンポーネント（と Timeline）の強さ
            double s = 0.0;
            for (int k = 0; k < _intRenderers.Length; k++)
            {
                SkinnedMeshRenderer r = _intRenderers[k];
                if (r != null) s += System.Math.Abs(r.GetBlendShapeWeight(_intIndices[k])) * ToWeight;
            }
            double exprScale = FacialCore.ExpressionScale((double)p.expressionDampen, s);
            double distFade = 1.0;
            if (viewer != null)
            {
                Transform origin = _baseResolved != null ? _baseResolved : transform;
                distFade = FacialCore.DistanceFade((double)Vector3.Distance(viewer.position, origin.position),
                    (double)p.fadeStart, (double)p.fadeEnd);
            }
            double scale = exprScale * distFade * System.Math.Max(0.0, (double)p.globalAlpha) * Mathf.Clamp01(alpha);
            if (hadOverride && ov.hasAlpha) scale *= System.Math.Max(0.0, (double)ov.alpha);
            _lastScale = scale;

            // 可動域で切る。名前が対象に無いものはここで落とす（フェイルソフト）
            _scaled.Clear();
            for (int i = 0; i < _raw.Count; i++)
            {
                MorphWeight m = _raw[i];
                ShapeBinding b;
                if (!_bindings.TryGetValue(m.MorphName, out b)) continue;
                double w = m.Weight * scale;
                if (b.hasLimit) w = FacialCore.Clamp(w, b.limMin, b.limMax);
                m.Weight = w;
                _scaled.Add(m);
            }

            // 7 スムージング（スナップ時は即時）→ ブレンドシェイプへ書く
            FacialCore.SmoothWeights(_last, _scaled, deltaTime, p.interpSpeed, snap, _smoothed);
            for (int i = 0; i < _last.Count; i++)
            {
                string prevName = _last[i].MorphName;
                bool still = false;
                for (int k = 0; k < _smoothed.Count; k++)
                    if (string.Equals(_smoothed[k].MorphName, prevName, StringComparison.Ordinal)) { still = true; break; }
                if (still) continue;
                ShapeBinding b;
                if (_bindings.TryGetValue(prevName, out b)) WriteRaw(b, 0f); // 前のフレームに書いて今回は無いもの
            }
            for (int i = 0; i < _smoothed.Count; i++)
            {
                ShapeBinding b;
                if (!_bindings.TryGetValue(_smoothed[i].MorphName, out b)) continue;
                WriteRaw(b, (float)(_smoothed[i].Weight * 100.0));
                if (!b.written) { b.written = true; _written.Add(b); }
            }
            List<MorphWeight> t = _last; _last = _smoothed; _smoothed = t;
        }

        static void WriteRaw(ShapeBinding b, float percent)
        {
            for (int k = 0; k < b.count; k++)
            {
                SkinnedMeshRenderer r = b.renderers[k];
                if (r != null) r.SetBlendShapeWeight(b.indices[k], percent);
            }
        }

        // ---------------------------------------------------------------- 名前 → 番号の事前解決

        void EnsureCache()
        {
            if (!_built || _cacheData != data || _cacheBaseField != baseBone || TargetsFieldChanged())
            {
                Rebuild();
                return;
            }
            // 自動検出のとき: メッシュの差し替えを拾う。対象が 1 つも無いときは時々だけ探し直す
            if (_cacheAuto)
            {
                if (_targets.Count == 0)
                {
                    if (Time.frameCount - _scanFrame >= 120) Rebuild();
                    return;
                }
                for (int i = 0; i < _targets.Count; i++)
                    if (_targets[i] == null || _targets[i].sharedMesh != _targetMeshes[i]) { Rebuild(); return; }
            }
            else
            {
                for (int i = 0; i < _targets.Count; i++)
                    if (_targets[i] != null && _targets[i].sharedMesh != _targetMeshes[i]) { Rebuild(); return; }
            }
        }

        bool TargetsFieldChanged()
        {
            SkinnedMeshRenderer[] t = targets;
            if (!ReferenceEquals(t, _cacheTargetsField)) return true;
            int n = t == null ? 0 : t.Length;
            if (n != _cacheTargetsLen) return true;
            return false;
        }

        void Rebuild()
        {
            ResetWeights(); // 古い対応で書いた分を先に戻す
            _bindings.Clear();
            _written.Clear();
            _missing.Clear();
            _targets.Clear();
            _targetMeshes.Clear();
            _layerInputs.Clear();
            _baseResolved = null;
            _intRenderers = new SkinnedMeshRenderer[0];
            _intIndices = new int[0];

            _built = true;
            _scanFrame = Time.frameCount;
            _cacheData = data;
            _cacheBaseField = baseBone;
            _cacheTargetsField = targets;
            _cacheTargetsLen = targets == null ? 0 : targets.Length;
            FacialCorrectionData d = data;
            if (d == null) return;

            string prefix = string.IsNullOrEmpty(d.assetName) ? FacialNaming.FcPrefix : FacialNaming.AssetPrefix(d.assetName);

            // 対象メッシュ: 指定があればそれ、無ければ子から FC_<asset>_ のシェイプを持つものを探す
            _cacheAuto = true;
            if (targets != null)
                for (int i = 0; i < targets.Length; i++)
                    if (targets[i] != null) { _targets.Add(targets[i]); _cacheAuto = false; }
            if (_cacheAuto)
            {
                GetComponentsInChildren(true, ScanRenderers);
                for (int i = 0; i < ScanRenderers.Count; i++)
                    if (HasShapeWithPrefix(ScanRenderers[i], prefix)) _targets.Add(ScanRenderers[i]);
                ScanRenderers.Clear();
            }
            for (int i = 0; i < _targets.Count; i++) _targetMeshes.Add(_targets[i].sharedMesh);

            // 基準ボーン: 指定があればそれ、無ければデータの名前で子から探す
            if (baseBone != null) _baseResolved = baseBone;
            else if (!string.IsNullOrEmpty(d.grid.baseBone))
            {
                GetComponentsInChildren(true, ScanTransforms);
                for (int i = 0; i < ScanTransforms.Count; i++)
                    if (string.Equals(ScanTransforms[i].name, d.grid.baseBone, StringComparison.Ordinal)) { _baseResolved = ScanTransforms[i]; break; }
                ScanTransforms.Clear();
            }

            // 格子・レイヤー
            _grid = new GridShape(d.grid.yawRange, d.grid.pitchRange, d.grid.cols, d.grid.rows, d.grid.edgeFade);
            Vec3 axisProbe;
            _forwardAxis = FacialSpace.TryAxisVector(d.grid.forwardAxis, out axisProbe) ? d.grid.forwardAxis : "+Z";
            for (int i = 0; i < d.layers.Length; i++)
                _layerInputs.Add(new LayerEvalInput(d.layers[i].morphNames, 0.0, d.layers[i].enabled));
            _emo = new double[d.layers.Length];
            _rawEmo = new double[d.layers.Length];

            // 名前 → 書き込み先。FC_ で始まるものだけ（元のシェイプには触らない）
            var seen = new HashSet<string>(StringComparer.Ordinal);
            var rs = new List<SkinnedMeshRenderer>(_targets.Count);
            var ix = new List<int>(_targets.Count);
            for (int li = 0; li < d.layers.Length; li++)
            {
                string[] names = d.layers[li].morphNames;
                if (names == null) continue;
                for (int n = 0; n < names.Length; n++)
                {
                    string nm = names[n];
                    if (string.IsNullOrEmpty(nm) || !FacialNaming.IsFcName(nm) || !seen.Add(nm)) continue;
                    rs.Clear(); ix.Clear();
                    for (int t = 0; t < _targets.Count; t++)
                    {
                        Mesh mesh = _targetMeshes[t];
                        int idx = mesh != null ? mesh.GetBlendShapeIndex(nm) : -1;
                        if (idx >= 0) { rs.Add(_targets[t]); ix.Add(idx); }
                    }
                    if (rs.Count == 0) { _missing.Add(nm); continue; }
                    _bindings[nm] = new ShapeBinding { renderers = rs.ToArray(), indices = ix.ToArray(), count = rs.Count };
                }
            }
            if (d.limits != null)
                for (int i = 0; i < d.limits.Length; i++)
                {
                    ShapeBinding b;
                    if (d.limits[i].name != null && _bindings.TryGetValue(d.limits[i].name, out b))
                    {
                        b.hasLimit = true; b.limMin = d.limits[i].min; b.limMax = d.limits[i].max;
                    }
                }

            // 表情の強さの入力（アニメーション適用後の現在値を読む）
            if (d.intensityCurves != null)
            {
                var ir = new List<SkinnedMeshRenderer>();
                var ii = new List<int>();
                for (int i = 0; i < d.intensityCurves.Length; i++)
                {
                    string nm = d.intensityCurves[i];
                    if (string.IsNullOrEmpty(nm)) continue;
                    for (int t = 0; t < _targets.Count; t++)
                    {
                        Mesh mesh = _targetMeshes[t];
                        int idx = mesh != null ? mesh.GetBlendShapeIndex(nm) : -1;
                        if (idx >= 0) { ir.Add(_targets[t]); ii.Add(idx); break; }
                    }
                }
                _intRenderers = ir.ToArray();
                _intIndices = ii.ToArray();
            }
        }

        static bool HasShapeWithPrefix(SkinnedMeshRenderer r, string prefix)
        {
            Mesh m = r != null ? r.sharedMesh : null;
            if (m == null) return false;
            int n = m.blendShapeCount;
            for (int i = 0; i < n; i++)
                if (m.GetBlendShapeName(i).StartsWith(prefix, StringComparison.Ordinal)) return true;
            return false;
        }
    }
}
