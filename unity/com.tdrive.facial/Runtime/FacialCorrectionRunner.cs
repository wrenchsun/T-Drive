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
    /// <summary>
    /// 外からの上書き（Timeline など）。PushOverride = 次の LateUpdate（または EvaluateNow）だけ有効 / SetOverride = 持ち主ごとに、消すまで有効。
    /// 値はどちらも Runner が自分のバッファへコピーする（呼び出し側の配列は保持しない）。
    /// </summary>
    public struct FacialFrameOverride
    {
        /// <summary>true のとき alpha を全体の強さへ掛ける。</summary>
        public bool hasAlpha;
        public float alpha;
        /// <summary>true のとき yaw / pitch を使う（視点の計算より優先）。</summary>
        public bool hasManualAngles;
        public float yaw;
        public float pitch;
        /// <summary>レイヤーごとの感情の重み（index 0 は無視）。null = 上書きなし。NaN の要素 = そのレイヤーは指定なし（距離で決めるレイヤーは距離の重み、それ以外は Runner の emotionWeights）。値は Runner がコピーする。</summary>
        public float[] emotionWeights;
        /// <summary>視点の Transform。null = 上書きなし。</summary>
        public Transform viewer;
        /// <summary>
        /// hasManualAngles のとき、手動の角度へ寄せる割合（0〜1）。ライブ（視点・コンポーネントの手動）の角度との間を補間する。
        /// 0 は「指定なし」とみなして 1（完全に手動）として扱う（この欄を足す前の呼び出しと同じ結果にするため）。
        /// </summary>
        public float manualAngleBlend;
        /// <summary>カット補正（R-36）: このフレームだけ加算するポーズ。null = なし。曲線はシェイプの重みへ、ボーンはローカルの変形へ加算される。</summary>
        public FacialPoseAsset pose;
        /// <summary>pose に掛ける重み（0〜1）。0 以下 = 加算しない。</summary>
        public float poseWeight;
        /// <summary>true のとき stepFps（コマ打ちの fps。0 = 毎フレーム）でデータ・調整用アセットの値を置き換える。</summary>
        public bool hasStepFps;
        public float stepFps;
        /// <summary>true のとき exaggeration（0〜1）を誇張の強さへ掛ける。</summary>
        public bool hasExaggeration;
        public float exaggeration;
        /// <summary>true のとき perspective（0〜1）をパース補正の強さへ掛ける。</summary>
        public bool hasPerspective;
        public float perspective;
    }

    /// <summary>Timeline の編集時プレビュー（再生していないとき）に、視点として使うカメラ。</summary>
    public enum FacialEditViewer
    {
        [InspectorName("自動（メインカメラがあればそれ）")] Auto,
        [InspectorName("Scene ビューのカメラ")] SceneView,
        [InspectorName("メインカメラ")] MainCamera,
    }

    /// <summary>直近の評価で使った視点の出どころ（診断・デバッグ表示用）。</summary>
    public enum FacialViewerSource { None, Override, Component, Parameter, MainCamera, Fallback }

    /// <summary>直近の評価で使った角度の出どころ（診断・デバッグ表示用）。</summary>
    public enum FacialAngleSource { None, Viewer, ComponentManual, OverrideManual, Blended, Explicit }

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

        [Tooltip("誇張（_Ex シェイプ）の強さ（0〜1）。データ・調整用アセットの誇張に掛かる。1 = 作った通り、0 = 誇張なし")]
        [Range(0f, 1f)] public float exaggeration = 1f;

        [Tooltip("レイヤーごとの感情の重み（レイヤー番号の順。0 番の Neutral は無視。0 以上）。距離で決める設定のレイヤーは、この値の代わりに距離の重みを使う")]
        public float[] emotionWeights;

        [Tooltip("true にしたレイヤーは感情の重みを 0 として扱う（レイヤー番号の順）")]
        public bool[] mutedLayers;

        [Header("最適化")]
        [Tooltip("true のとき、対象のメッシュがどのカメラにも映っていない間は計算しない")]
        public bool skipWhenNotVisible;

        [Header("マテリアル出力")]
        [Tooltip("マテリアルへ角度・感情の重みを渡す方式。「データに従う」= 取り込んだデータの material の指定どおり。Renderer ごとの MaterialPropertyBlock に書く（他のキャラクターと衝突しない）。注意: MaterialPropertyBlock を持つ Renderer は SRP Batcher の対象から外れる（出力している間だけ。出力を切る・無効化すると自分の値だけのブロックは外す）。大量に並べるキャラクターでは「出力しない」を使う")]
        public FacialMaterialOutputMode materialOutput = FacialMaterialOutputMode.FollowData;

        [Tooltip("補正の対象メッシュのほかに、値を渡したい Renderer（まつ毛・眉など別メッシュ）")]
        public Renderer[] materialTargets;

        [Header("プレビュー")]
        [Tooltip("Timeline を再生せずに（編集時に）プレビューするとき、補正の視点にするカメラ。自動 = D-Drive などが今の視点を返せばそれ、無ければメインカメラ（タグ MainCamera）、それも無ければ Scene ビューのカメラ。Scene ビュー / メインカメラを選ぶと、D-Drive の視点より先にそれを使う。再生中は使わない。視点の順（再生中）: クリップの視点 → このコンポーネントの視点 → D-Drive の今の視点 → メインカメラ")]
        public FacialEditViewer editViewer = FacialEditViewer.Auto;

        // --- 診断（読み取り専用） ---
        public float CurrentYaw { get { return (float)_curYaw; } }
        public float CurrentPitch { get { return (float)_curPitch; } }
        /// <summary>直近の評価でスナップ（カット切り替え）と判定したか。</summary>
        public bool Snapped { get { return _snapped; } }
        /// <summary>直近の評価で書いた補正シェイプの数（スムージング後）。</summary>
        public int ActiveWeightCount { get { return _last.Count; } }
        /// <summary>直近の評価で補正に掛けた倍率の合計（表情 × 距離フェード × 全体の強さ）。</summary>
        public float LastScale { get { return (float)_lastScale; } }
        /// <summary>直近の Step でコマ打ちのため評価せず、前の重みを保ったか。</summary>
        public bool StepHolding { get { return _stepHolding; } }
        /// <summary>直近の Step で使ったコマ打ちの fps（0 = 毎フレーム）。</summary>
        public float LastStepFps { get { return (float)_lastStepFps; } }
        /// <summary>直近の評価で使ったシャープさ。</summary>
        public float LastSharpness { get { return (float)_lastSharpness; } }
        /// <summary>直近の評価で使った誇張（データ・調整用アセット × Runner × Timeline）。</summary>
        public float LastExaggeration { get { return (float)_lastExaggeration; } }
        /// <summary>パース補正を使っているか（データの perspective が有効でキーがある）。</summary>
        public bool PerspectiveActive { get { return data != null && data.perspective.enabled && data.perspective.keys != null && data.perspective.keys.Length > 0; } }
        /// <summary>直近の評価でのパース補正の軸の値（距離は m、画角は度）。使っていない・視点や画角が分からないときは NaN。</summary>
        public float LastPerspectiveAxisValue { get { return (float)_lastPerspAxis; } }
        /// <summary>直近の評価でのパース補正の強さ（調整値・Timeline を掛けたあと）。使っていなければ 0。</summary>
        public float LastPerspectiveStrength { get { return (float)_lastPerspStrength; } }
        /// <summary>パース補正のキーの数（データの perspective.keys）。</summary>
        public int PerspectiveKeyCount { get { return _perspCount; } }
        /// <summary>直近の評価でのパース補正のキー k の重み（強さ・全体の倍率を掛ける前の、キーの混ざり具合 0〜1）。範囲外は 0。</summary>
        public float GetPerspectiveKeyWeight(int k) { return k >= 0 && k < _perspCount ? (float)_perspW[k] : 0f; }
        /// <summary>直近の評価で視点から角度を求められたか（手動・視点なしは false）。</summary>
        public bool HasValidAngles { get { return _hasPrev; } }

        /// <summary>直近の評価で使った視点（無ければ null）。</summary>
        public Transform LastViewer { get { return _lastViewer; } }
        /// <summary>直近の評価で使った視点の位置（ワールド）。HasLastViewerPosition が false なら無効。Fallback の視点は Transform が無いのでこちらで読む。</summary>
        public Vector3 LastViewerPosition { get { return _lastViewerPos; } }
        public bool HasLastViewerPosition { get { return _hasViewerPos; } }
        /// <summary>直近の評価で使った視点の縦画角（度。分からなければ 0）。</summary>
        public float LastViewerFov { get { return _lastViewerFov; } }
        /// <summary>直近の評価で使った視点の出どころ。</summary>
        public FacialViewerSource LastViewerSource { get { return _lastViewerSource; } }
        /// <summary>直近の評価で使った角度の出どころ。</summary>
        public FacialAngleSource LastAngleSource { get { return _lastAngleSource; } }
        /// <summary>有効な Runner の一覧（デバッグ表示用。読み取り専用）。</summary>
        public static IReadOnlyList<FacialCorrectionRunner> ActiveRunners { get { return Active; } }

        static readonly List<FacialCorrectionRunner> Active = new List<FacialCorrectionRunner>(8);

        // Domain Reload を切った設定でも、再生のたびに static を初期状態へ戻す（docs/19 U-8）
        [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.SubsystemRegistration)]
        static void ResetStatics()
        {
            Active.Clear();
            ScanRenderers.Clear();
            ScanTransforms.Clear();
        }
        Transform _lastViewer;
        Vector3 _lastViewerPos;
        bool _hasViewerPos;
        float _lastViewerFov;
        FacialViewerSource _lastViewerSource;
        FacialAngleSource _lastAngleSource;

        FacialMaterialOutput _matOut;

        sealed class ShapeBinding
        {
            public SkinnedMeshRenderer[] renderers;
            public int[] indices;
            public int count;
            public Mesh[] meshes; // 作ったときのメッシュ（差し替え後の古い番号へ書かないための記録）
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
        readonly List<string> _ambiguous = new List<string>();
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
        readonly List<SkinnedMeshRenderer> _cacheTargetsCopy = new List<SkinnedMeshRenderer>(4); // 配列の中身の差し替えを見つける
        FacialLayerData[] _cacheLayers;
        int _cacheCols, _cacheRows;
        bool _rebuilding;
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
        // パース補正（F5-4）。名前 → 書き込み先は Rebuild で引く。値は評価のたびにデータから読む（インスペクターでの編集に追従）
        int _perspCount;
        double[] _perspValues = new double[0];
        double[] _perspW = new double[0];
        string[] _perspNames = new string[0];
        ShapeBinding[] _perspBind = new ShapeBinding[0];
        FacialPerspectiveKeyData[] _cachePerspKeys;
        double _lastPerspAxis = double.NaN, _lastPerspStrength;
        // コマ打ち（F5-2）
        bool _stepHolding;
        double _stepAccum, _lastStepFps, _lastSharpness = 1.0, _lastExaggeration = 1.0;
        double _rawSharp = 1.0, _rawExag = 1.0;

        // 上書き（SetOverride = 持ち主ごとに持続 / PushOverride = 1 フレーム）。並びは (priority 昇順, 登録順)。後ろほど強い
        sealed class OverrideSlot
        {
            public object owner;
            public UnityEngine.Object ownerObject;
            public bool hasObject;
            public int priority;
            public FacialFrameOverride o;
            public float[] emo = new float[0];
            public int emoLen; // 0 = 感情の重みの指定なし
        }
        readonly List<OverrideSlot> _slots = new List<OverrideSlot>(4);
        readonly OverrideSlot _pendingSlot = new OverrideSlot();
        bool _hasPending;
        float[] _mergedEmo = new float[0];

        // カット補正（ポーズの加算）。戻し方: 前回書いた値と今の値が同じときだけ元へ戻す（アニメーションが書き直していたら触らない）
        sealed class PoseCache
        {
            public SkinnedMeshRenderer[] shapeRenderers = new SkinnedMeshRenderer[0];
            public int[] shapeIndices = new int[0];
            public float[] shapeValues = new float[0];
            public Transform[] boneTransforms = new Transform[0];
            public FacialPoseBone[] bones = new FacialPoseBone[0];
        }
        struct PoseShapeSave { public SkinnedMeshRenderer renderer; public Mesh mesh; public int index; public float original, applied; }
        struct PoseBoneSave
        {
            public Transform t;
            public Vector3 pos, scale, aPos, aScale;
            public Quaternion rot, aRot;
        }
        readonly Dictionary<FacialPoseAsset, PoseCache> _poseCache = new Dictionary<FacialPoseAsset, PoseCache>();
        readonly List<PoseShapeSave> _poseShapes = new List<PoseShapeSave>(16);
        readonly List<PoseBoneSave> _poseBones = new List<PoseBoneSave>(16);

        // ---------------------------------------------------------------- 公開 API

        /// <summary>
        /// 次の LateUpdate（または EvaluateNow）だけ有効な上書きを渡す。使われたら消える（互換用）。
        /// 持続する上書き（SetOverride）より強い（最後に足したものとして合成される）。値はコピーされる。
        /// </summary>
        public void PushOverride(in FacialFrameOverride o)
        {
            CopyInto(_pendingSlot, o);
            _hasPending = true;
        }

        /// <summary>
        /// 持ち主ごとの、消すまで続く上書き。同じ owner で呼び直すと値を入れ替える（毎フレーム呼んでよい。割り当てなし）。
        /// Timeline が一時停止して評価が来ないフレームでも、値は保たれる。値はコピーされる（emotionWeights も Runner のバッファへ）。
        /// 複数の持ち主の合成（並びは priority の小さい順、同じなら最初に登録した順。後ろほど強い）:
        ///  alpha = すべて掛ける / 感情の重み = レイヤーごとに、そのレイヤー（NaN でない値）を持ついちばん後ろの持ち主 /
        ///  角度の固定 = manualAngleBlend が最大のもの（同じなら後ろ）/ 視点 = null でないいちばん後ろ /
        ///  カット補正のポーズ = すべて加算 / stepFps・誇張 = 指定のあるいちばん後ろ。
        /// ownerObject（任意。owner 自身が UnityEngine.Object なら不要）が破棄されたら、その持ち主の上書きは自動で捨てる。
        /// OnDisable ですべて捨てる（プールに戻ったインスタンスが古いカットを再生しない）。
        /// </summary>
        public void SetOverride(object owner, in FacialFrameOverride o, int priority = 0, UnityEngine.Object ownerObject = null)
        {
            if (owner == null) throw new ArgumentNullException("owner");
            int idx = -1;
            for (int i = 0; i < _slots.Count; i++)
                if (ReferenceEquals(_slots[i].owner, owner)) { idx = i; break; }
            OverrideSlot slot;
            if (idx >= 0)
            {
                slot = _slots[idx];
                if (slot.priority != priority) { _slots.RemoveAt(idx); slot.priority = priority; InsertSlot(slot); }
            }
            else
            {
                slot = new OverrideSlot { owner = owner, priority = priority };
                InsertSlot(slot);
            }
            if (ownerObject == null) ownerObject = owner as UnityEngine.Object;
            slot.ownerObject = ownerObject;
            slot.hasObject = !ReferenceEquals(ownerObject, null);
            CopyInto(slot, o);
        }

        /// <summary>owner の上書きを消す。あったら true。</summary>
        public bool ClearOverride(object owner)
        {
            if (owner == null) return false;
            for (int i = 0; i < _slots.Count; i++)
                if (ReferenceEquals(_slots[i].owner, owner)) { _slots.RemoveAt(i); return true; }
            return false;
        }

        /// <summary>持続する上書きと、まだ使われていない PushOverride をすべて消す。</summary>
        public void ClearAllOverrides()
        {
            _slots.Clear();
            _hasPending = false;
        }

        /// <summary>今ある持続する上書きの数（持ち主の数。診断・テスト用）。</summary>
        public int OverrideCount { get { return _slots.Count; } }

        void InsertSlot(OverrideSlot slot)
        {
            int at = _slots.Count;
            while (at > 0 && _slots[at - 1].priority > slot.priority) at--;
            _slots.Insert(at, slot);
        }

        static void CopyInto(OverrideSlot slot, in FacialFrameOverride o)
        {
            slot.o = o;
            float[] src = o.emotionWeights;
            int n = src != null ? src.Length : 0;
            if (n > 0)
            {
                if (slot.emo.Length < n) slot.emo = new float[n];
                Array.Copy(src, slot.emo, n);
            }
            slot.emoLen = n;
            slot.o.emotionWeights = null; // 参照は持たない（emo のバッファを使う）
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

        /// <summary>
        /// 書いた FC_ シェイプをすべて 0 に戻し、計算の状態も捨てる（次の評価はスナップ）。カット補正（ポーズの加算）も元へ戻す。
        /// 編集時（再生中でないとき）は、結んである FC_ シェイプを書いた記録に関係なくすべて 0 にする（保存に値が残らないように。docs/19 E-4）。
        /// 持続する上書き（SetOverride）は消さない。
        /// </summary>
        public void ResetWeights()
        {
            bool all = ZeroAllBoundOverride ?? !Application.isPlaying;
            if (all && !_built && !_rebuilding) Rebuild(); // まだ結んでいなければ今結ぶ（保存の前に必ず戻せるように）
            ResetCore(all);
        }

        /// <summary>テスト用: 再生中と同じ（書いた分だけ戻す）動きを編集時に確かめるため、ResetWeights の範囲を固定する。null = 自動。</summary>
        internal bool? ZeroAllBoundOverride;

        void ResetCore(bool zeroAllBound)
        {
            RestorePose(); // カット補正で加算した分も元へ
            if (zeroAllBound)
            {
                foreach (ShapeBinding b in _bindings.Values) WriteRaw(b, 0f, true);
            }
            for (int i = 0; i < _written.Count; i++)
            {
                ShapeBinding b = _written[i];
                if (!zeroAllBound) WriteRaw(b, 0f, true);
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
            _stepHolding = false;
            _stepAccum = 0.0;
            _lastPerspAxis = double.NaN;
            _lastPerspStrength = 0.0;
            for (int k = 0; k < _perspW.Length; k++) _perspW[k] = 0.0;
            if (_matOut != null) _matOut.Clear(); // マテリアルに渡した値も 0 へ
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

        /// <summary>
        /// 末尾一致（"." より後ろ）で、複数のシェイプに当たってしまうデータのシェイプ名（先に見つかった 1 つだけが動く。docs/19 U-6）。
        /// 完全一致で見つかったものは含まない。検証が警告に使う。
        /// </summary>
        public IReadOnlyList<string> GetAmbiguousShapeNames()
        {
            EnsureCache();
            return _ambiguous;
        }

        /// <summary>見つかった FC_ シェイプの数（名前の種類）。</summary>
        public int BoundShapeCount { get { EnsureCache(); return _bindings.Count; } }

        /// <summary>解決した対象メッシュ。</summary>
        public IReadOnlyList<SkinnedMeshRenderer> ResolvedTargets { get { EnsureCache(); return _targets; } }

        /// <summary>解決した基準ボーン（無ければ null）。</summary>
        public Transform ResolvedBaseBone { get { EnsureCache(); return _baseResolved; } }

        /// <summary>データと調整用アセットを合成した、今の実効の値（データが無ければ既定値）。</summary>
        public FacialEffectiveParams EffectiveParams { get { return FacialCorrectionOverrides.Resolve(data, overrides); } }

        /// <summary>直近の評価で書いたシェイプの名前と重み（0〜1、スムージング後）を output へ入れる（output は先に空にする）。</summary>
        public void GetActiveWeights(List<MorphWeight> output)
        {
            output.Clear();
            for (int i = 0; i < _last.Count; i++) output.Add(_last[i]);
        }

        /// <summary>マテリアル出力が有効か（Runner の指定とデータの material を合わせた結果）。</summary>
        public bool MaterialOutputActive
        {
            get
            {
                switch (materialOutput)
                {
                    case FacialMaterialOutputMode.Off: return false;
                    case FacialMaterialOutputMode.PropertyBlock: return true;
                    default: return data != null && data.MaterialModeValue == FacialMaterialMode.PropertyBlock;
                }
            }
        }

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
            if (!Active.Contains(this)) Active.Add(this);
        }

        void OnDisable()
        {
            Active.Remove(this);
            ResetWeights(); // 書いた FC_ を 0 に戻す（プールへ返すときも）
            ClearAllOverrides(); // 持続する上書きも捨てる（プールのインスタンスが古いカットを再生しない）
        }

#if UNITY_EDITOR
        void OnValidate()
        {
            _built = false; // インスペクターで対象・データを変えたら、次の評価で引き直す
        }
#endif

        void LateUpdate()
        {
            if (skipWhenNotVisible && !AnyTargetVisible())
            {
                RestorePose(); // 評価しない間は、加算したカット補正も戻しておく（ボーンが動いたままにならない）
                return;
            }
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
            RestorePose(); // 前回のカット補正を戻してから評価する（加算が積もらない）
            DropDestroyedOwners();

            StepCore(deltaTime, viewerParam, explicitAngles, explicitYaw, explicitPitch);

            // カット補正は FC_ の書き込みのあと（= アニメーションのあと）に加算する。持ち主ごとのポーズをすべて足す
            if (data != null)
            {
                for (int i = 0; i <= _slots.Count; i++)
                {
                    OverrideSlot sl = i < _slots.Count ? _slots[i] : (_hasPending ? _pendingSlot : null);
                    if (sl == null || sl.o.pose == null || !(sl.o.poseWeight > 0f)) continue;
                    EnsureCache();
                    ApplyPose(sl.o.pose, Mathf.Min(1f, sl.o.poseWeight));
                }
            }
            _hasPending = false; // PushOverride は 1 回で消す
        }

        // 破棄された持ち主（Timeline が壊れたなど）の上書きを捨てる
        void DropDestroyedOwners()
        {
            for (int i = _slots.Count - 1; i >= 0; i--)
                if (_slots[i].hasObject && _slots[i].ownerObject == null) _slots.RemoveAt(i);
        }

        /// <summary>上書きを 1 つの値に合成する（規則は SetOverride の説明）。何も無ければ false。感情の重みは Runner のバッファ（NaN = 指定なし）。</summary>
        bool MergeOverrides(int layerCount, out FacialFrameOverride m)
        {
            m = default(FacialFrameOverride);
            int total = _slots.Count + (_hasPending ? 1 : 0);
            if (total == 0) return false;
            bool emoInit = false;
            float alpha = 1f, bestBlend = 0f;
            for (int i = 0; i < total; i++)
            {
                OverrideSlot sl = i < _slots.Count ? _slots[i] : _pendingSlot;
                if (sl.o.hasAlpha) { m.hasAlpha = true; alpha *= Mathf.Max(0f, sl.o.alpha); }
                if (sl.o.hasExaggeration) { m.hasExaggeration = true; m.exaggeration = sl.o.exaggeration; }
                if (sl.o.hasPerspective) { m.hasPerspective = true; m.perspective = sl.o.perspective; }
                if (sl.o.hasStepFps) { m.hasStepFps = true; m.stepFps = sl.o.stepFps; }
                if (sl.o.hasManualAngles)
                {
                    float b = sl.o.manualAngleBlend > 0f ? Mathf.Min(1f, sl.o.manualAngleBlend) : 1f;
                    if (!m.hasManualAngles || b >= bestBlend)
                    {
                        m.hasManualAngles = true; m.yaw = sl.o.yaw; m.pitch = sl.o.pitch; m.manualAngleBlend = b; bestBlend = b;
                    }
                }
                if (sl.emoLen > 0)
                {
                    if (!emoInit)
                    {
                        if (_mergedEmo.Length < layerCount) _mergedEmo = new float[layerCount];
                        for (int k = 0; k < layerCount; k++) _mergedEmo[k] = float.NaN;
                        emoInit = true;
                    }
                    int n = Mathf.Min(sl.emoLen, layerCount);
                    for (int k = 0; k < n; k++)
                        if (!float.IsNaN(sl.emo[k])) _mergedEmo[k] = sl.emo[k];
                }
            }
            m.alpha = alpha;
            for (int i = total - 1; i >= 0 && m.viewer == null; i--)
                m.viewer = (i < _slots.Count ? _slots[i] : _pendingSlot).o.viewer;
            m.emotionWeights = emoInit ? _mergedEmo : null;
            return true;
        }

        /// <summary>
        /// 左右反転（スケール -1）のボーンで、世界での前方向と中心のずれを求める（Runner の角度計算とデバッグ表示で同じ式を使う。docs/19 §5）。
        /// </summary>
        public static void MirrorSafeDirections(Transform bone, Vec3 forwardAxis, Vector3 centerOffset, out Vector3 worldForward, out Vector3 worldOffset)
        {
            worldForward = bone.TransformDirection(new Vector3((float)forwardAxis.X, (float)forwardAxis.Y, (float)forwardAxis.Z));
            worldOffset = bone.TransformDirection(centerOffset);
        }

        void StepCore(float deltaTime, Transform viewerParam, bool explicitAngles, double explicitYaw, double explicitPitch)
        {
            FacialCorrectionData d = data;
            if (d == null || d.layers == null || d.layers.Length == 0)
            {
                if (_written.Count > 0) ResetWeights(); // データを外したら書いた分を戻す
                return;
            }
            EnsureCache();
            if (_targets.Count == 0) return;
            FacialFrameOverride ov;
            bool hadOverride = MergeOverrides(d.layers.Length, out ov);

            FacialEffectiveParams p = FacialCorrectionOverrides.Resolve(d, overrides);

            // 1 視点（位置）: 上書きの視点 > viewerOverride > EvaluateNow の引数（編集時の editViewer）> FacialViewResolver.Fallback（D-Drive の今の視点）> メインカメラ
            //   手動の角度のときも距離（レイヤー・フェード）には視点の位置を使うので、同じ順で解決する
            Transform viewer = null;
            Vector3 viewerPos = default(Vector3);
            bool haveViewer = false;
            FacialViewerSource viewerSource = FacialViewerSource.None;
            if (hadOverride && ov.viewer != null) { viewer = ov.viewer; viewerSource = FacialViewerSource.Override; }
            else if (viewerOverride != null) { viewer = viewerOverride; viewerSource = FacialViewerSource.Component; }
            else if (viewerParam != null) { viewer = viewerParam; viewerSource = FacialViewerSource.Parameter; }
            if (viewer != null) { viewerPos = viewer.position; haveViewer = true; }
            else
            {
                Quaternion fbRot;
                float fbFov;
                if (FacialViewResolver.TryResolve(transform, out viewerPos, out fbRot, out fbFov))
                {
                    haveViewer = true; viewerSource = FacialViewerSource.Fallback; _lastViewerFov = fbFov;
                }
                else
                {
                    Camera cam = Camera.main;
                    if (cam != null) { viewer = cam.transform; viewerPos = viewer.position; haveViewer = true; viewerSource = FacialViewerSource.MainCamera; _lastViewerFov = cam.orthographic ? 0f : cam.fieldOfView; }
                }
            }
            if (viewer != null && viewerSource != FacialViewerSource.MainCamera && viewerSource != FacialViewerSource.Fallback)
            {
                Camera vc;
                _lastViewerFov = viewer.TryGetComponent(out vc) && !vc.orthographic ? vc.fieldOfView : 0f;
            }
            _lastViewer = viewer;
            _lastViewerPos = viewerPos;
            _hasViewerPos = haveViewer;
            _lastViewerSource = viewerSource;

            // 2 角度: 直接指定 > 上書きの手動（manualAngleBlend < 1 ならライブとの補間）> コンポーネントの手動 > 視点
            double yaw, pitch;
            if (explicitAngles) { yaw = explicitYaw; pitch = explicitPitch; _lastAngleSource = FacialAngleSource.Explicit; }
            else
            {
                bool manualOv = hadOverride && ov.hasManualAngles;
                float blend = manualOv ? (ov.manualAngleBlend > 0f ? Mathf.Min(1f, ov.manualAngleBlend) : 1f) : 0f;
                bool haveLive = false;
                double liveYaw = 0.0, livePitch = 0.0;
                FacialAngleSource liveSource = FacialAngleSource.None;
                if (!manualOv || blend < 1f)
                {
                    if (useManualAngles) { liveYaw = manualYaw; livePitch = manualPitch; haveLive = true; liveSource = FacialAngleSource.ComponentManual; }
                    else if (haveViewer)
                    {
                        Transform bone = _baseResolved;
                        if (bone != null)
                        {
                            Vector3 hp = bone.position;
                            Quaternion hr = bone.rotation;
                            Vector3 vp = viewerPos;
                            Vector3 co = d.grid.centerOffset;
                            Vec3 axisV;
                            if (bone.localToWorldMatrix.determinant < 0f && FacialSpace.TryAxisVector(_forwardAxis, out axisV))
                            {
                                // 左右反転（スケール -1）のボーン: 回転だけでは軸の向きが裏返るので、世界での向きを TransformDirection で求める（docs/19 §5）
                                Vector3 wf, wo;
                                MirrorSafeDirections(bone, axisV, co, out wf, out wo);
                                FacialSpace.ComputeViewAnglesFromWorldVectors(UnityToCanonical, new Vec3(hp.x, hp.y, hp.z),
                                    new Vec3(wo.x, wo.y, wo.z), new Vec3(wf.x, wf.y, wf.z), new Vec3(vp.x, vp.y, vp.z), out liveYaw, out livePitch);
                            }
                            else
                                FacialSpace.ComputeViewAnglesInSpace(UnityToCanonical,
                                    new Vec3(hp.x, hp.y, hp.z), new Quat(hr.x, hr.y, hr.z, hr.w), _forwardAxis,
                                    new Vec3(vp.x, vp.y, vp.z), new Vec3(co.x, co.y, co.z), out liveYaw, out livePitch);
                            haveLive = true;
                            liveSource = FacialAngleSource.Viewer;
                        }
                        else if (!manualOv)
                        {
                            if (!_warnedBase)
                            {
                                _warnedBase = true;
                                Debug.LogWarning("[FacialCorrectionRunner] 基準ボーン '" + d.grid.baseBone + "' が見つかりません。角度を計算できないので補正を掛けません（手動の角度なら動きます）: " + name, this);
                            }
                            return; // フェイルソフト
                        }
                    }
                    else if (!manualOv) return; // 視点が無いときは何もしない（前回の状態を保つ）
                }
                if (!manualOv) { yaw = liveYaw; pitch = livePitch; _lastAngleSource = liveSource; }
                else if (!haveLive || blend >= 1f) { yaw = ov.yaw; pitch = ov.pitch; _lastAngleSource = FacialAngleSource.OverrideManual; }
                else
                {
                    yaw = liveYaw + blend * FacialCore.NormalizeAxis(ov.yaw - liveYaw);
                    pitch = livePitch + blend * (ov.pitch - livePitch);
                    _lastAngleSource = FacialAngleSource.Blended;
                }
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
            // 優先: 上書き（Timeline・PushOverride）の値 > 距離で決めるレイヤーの距離の重み > Runner の emotionWeights
            float[] ovEmo = hadOverride ? ov.emotionWeights : null;
            int layerCount = d.layers.Length;
            double viewDistance = haveViewer
                ? (double)Vector3.Distance(viewerPos, (_baseResolved != null ? _baseResolved : transform).position) : 0.0;
            if (_emo.Length != layerCount) { _emo = new double[layerCount]; _rawEmo = new double[layerCount]; _hasRaw = false; }
            bool emoSame = true;
            for (int i = 0; i < layerCount; i++)
            {
                double w = 0.0;
                FacialLayerWeightData lwd = d.layers[i].weight;
                if (i > 0 && lwd.source == FacialLayerWeightSource.Distance && haveViewer)
                {
                    w = FacialCore.LayerWeightFromDistance(viewDistance, lwd.start, lwd.end, lwd.from, lwd.to);
                    if (ovEmo != null && i < ovEmo.Length && !float.IsNaN(ovEmo[i])) w = ovEmo[i]; // Timeline などの明示の値が優先
                    if (!(w > 0.0)) w = 0.0;
                }
                else if (i > 0)
                {
                    // 上書きの値（NaN = 指定なし）→ 無ければ Runner の emotionWeights
                    float f = ovEmo != null && i < ovEmo.Length ? ovEmo[i] : float.NaN;
                    if (float.IsNaN(f)) f = emotionWeights != null && i < emotionWeights.Length ? emotionWeights[i] : 0f;
                    if (f > 0f) w = f; // NaN・負は 0
                }
                if (mutedLayers != null && i < mutedLayers.Length && mutedLayers[i]) w = 0.0;
                _emo[i] = w;
                if (w != _rawEmo[i]) emoSame = false;
                LayerEvalInput li = _layerInputs[i];
                li.EmotionWeight = w;
                li.Enabled = d.layers[i].enabled;
            }

            // 4.5 コマ打ち: stepFps > 0 のとき、前回の評価から 1/stepFps 秒たつまで評価せず前の重みを保つ。
            //     最初のフレーム・スナップ（カット）は必ず評価する。保っている間は書き込みもマテリアルも触らない
            double stepFps = hadOverride && ov.hasStepFps ? (double)ov.stepFps : (double)p.stepFps;
            _lastStepFps = stepFps > 0.0 ? stepFps : 0.0;
            bool stepping = stepFps > 0.0;
            if (stepping)
            {
                // float の deltaTime（0.02f など）の丸めで周期に 1e-9 届かず 1 フレーム遅れないよう、マイクロ秒に丸めて数える
                bool doEval = FacialCore.StepGate(_stepAccum, System.Math.Round((double)deltaTime * 1e6) / 1e6, stepFps, snap, out _stepAccum);
                _stepHolding = !doEval;
                if (_stepHolding) return;
                if (snap) _stepAccum = 0.0; // 最初・カットの評価からあらためて 1 周期数える
            }
            else { _stepHolding = false; _stepAccum = 0.0; }

            // 5 格子の計算。角度の変化が小さく感情も同じなら前回の結果を使う
            double sharp = p.sharpness > 0f ? (double)p.sharpness : 1.0; // 0 以下 = 未設定（1）
            double exag = (double)p.exaggeration * (double)Mathf.Clamp01(exaggeration);
            if (hadOverride && ov.hasExaggeration) exag *= System.Math.Max(0.0, System.Math.Min(1.0, (double)ov.exaggeration));
            _lastSharpness = sharp;
            _lastExaggeration = exag;
            double eps = d.quality.angleEpsilon;
            _grid.EdgeFadeDeg = p.edgeFade;
            bool reuse = _hasRaw && eps > 0.0 && emoSame && p.edgeFade == _rawEdge && sharp == _rawSharp && exag == _rawExag
                && System.Math.Abs(FacialCore.NormalizeAxis(yaw - _rawYaw)) < eps
                && System.Math.Abs(pitch - _rawPitch) < eps;
            if (!reuse)
            {
                FacialCore.EvaluateCorrection(_grid, _layerInputs, yaw, pitch, _raw, sharp, exag);
                for (int i = 0; i < layerCount; i++) _rawEmo[i] = _emo[i];
                _rawYaw = yaw; _rawPitch = pitch; _rawEdge = p.edgeFade; _rawSharp = sharp; _rawExag = exag;
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
            if (haveViewer)
            {
                Transform origin = _baseResolved != null ? _baseResolved : transform;
                distFade = FacialCore.DistanceFade((double)Vector3.Distance(viewerPos, origin.position),
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

            // 6.5 パース補正（R-34）: 軸の値（視点と格子の中心の距離 m / 視点の縦の画角）からキーの重みを混ぜ、角度の補正に足す。
            //      全体の倍率（表情 × 距離フェード × 全体の強さ × Runner・Timeline の強さ）は同じく掛かる。シェイプ名は別なので合算は起きない
            AddPerspective(d, p, hadOverride, ov, haveViewer, viewerPos, scale);

            // 7 スムージング（スナップ時は即時）→ ブレンドシェイプへ書く
            // コマ打ち中は追従を使わず、更新のたびに目標へ切り替える
            FacialCore.SmoothWeights(_last, _scaled, deltaTime, p.interpSpeed, snap || stepping, _smoothed);
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

            // 8 マテリアルへ（評価のたびに）
            if (MaterialOutputActive)
            {
                if (_matOut == null) _matOut = new FacialMaterialOutput();
                _matOut.Write(_targets, materialTargets, yaw, pitch, d.grid.yawRange, d.grid.pitchRange, scale, _emo, layerCount);
            }
            else if (_matOut != null && _matOut.WrittenCount > 0) _matOut.Clear();
        }

        void AddPerspective(FacialCorrectionData d, FacialEffectiveParams p, bool hadOverride, in FacialFrameOverride ov,
            bool haveViewer, Vector3 viewerPos, double scale)
        {
            _lastPerspAxis = double.NaN;
            _lastPerspStrength = 0.0;
            for (int k = 0; k < _perspCount; k++) _perspW[k] = 0.0;
            FacialPerspectiveData pd = d.perspective;
            if (!pd.enabled || _perspCount == 0 || pd.keys == null || pd.keys.Length != _perspCount) return;

            double strength = (double)Mathf.Clamp01(p.perspectiveStrength);
            if (hadOverride && ov.hasPerspective) strength *= System.Math.Max(0.0, System.Math.Min(1.0, (double)ov.perspective));
            _lastPerspStrength = strength;

            double axisValue = double.NaN; // 分からないとき（視点なし・画角なし）は NaN = 補正 0
            if (haveViewer)
            {
                if (pd.axis == FacialPerspectiveAxis.Fov)
                {
                    if (_lastViewerFov > 0f && _lastViewerFov < 180f) axisValue = _lastViewerFov;
                }
                else
                {
                    Vector3 center;
                    if (_baseResolved != null) center = _baseResolved.position + _baseResolved.TransformDirection(d.grid.centerOffset);
                    else center = transform.position;
                    axisValue = (double)Vector3.Distance(viewerPos, center);
                }
            }
            _lastPerspAxis = axisValue;
            for (int k = 0; k < _perspCount; k++) _perspValues[k] = pd.keys[k].value;
            FacialCore.PerspectiveWeights(_perspValues, _perspCount, axisValue, _perspW);

            for (int k = 0; k < _perspCount; k++)
            {
                ShapeBinding b = _perspBind[k];
                if (b == null) continue; // ポーズが空のキー・メッシュに無いシェイプ
                double w = _perspW[k] * strength * scale;
                if (FacialCore.IsNearlyZero(w)) continue;
                if (b.hasLimit) w = FacialCore.Clamp(w, b.limMin, b.limMax);
                _scaled.Add(new MorphWeight(_perspNames[k], w));
            }
        }

        // checkMesh: 戻すとき（古い番号が別のメッシュの関係ないシェイプを指さないよう、作ったときのメッシュと同じときだけ書く）
        static void WriteRaw(ShapeBinding b, float percent, bool checkMesh = false)
        {
            for (int k = 0; k < b.count; k++)
            {
                SkinnedMeshRenderer r = b.renderers[k];
                if (r == null) continue;
                if (checkMesh && r.sharedMesh != b.meshes[k]) continue;
                r.SetBlendShapeWeight(b.indices[k], percent);
            }
        }

        // ---------------------------------------------------------------- カット補正（R-36）

        /// <summary>ポーズが指すボーンの Transform を output へ入れる（Timeline の GatherProperties 用。output は先に空にする）。</summary>
        public void GetPoseBoneTransforms(FacialPoseAsset pose, List<Transform> output)
        {
            output.Clear();
            if (pose == null || data == null) return;
            EnsureCache();
            PoseCache pc = GetPoseCache(pose);
            for (int i = 0; i < pc.boneTransforms.Length; i++)
                if (pc.boneTransforms[i] != null) output.Add(pc.boneTransforms[i]);
        }

        /// <summary>ポーズの曲線が指すブレンドシェイプ（対象メッシュと番号）を返す（Timeline の GatherProperties 用）。</summary>
        public void GetPoseShapeTargets(FacialPoseAsset pose, List<SkinnedMeshRenderer> renderers, List<int> indices)
        {
            renderers.Clear();
            indices.Clear();
            if (pose == null || data == null) return;
            EnsureCache();
            PoseCache pc = GetPoseCache(pose);
            for (int i = 0; i < pc.shapeRenderers.Length; i++) { renderers.Add(pc.shapeRenderers[i]); indices.Add(pc.shapeIndices[i]); }
        }

        PoseCache GetPoseCache(FacialPoseAsset pose)
        {
            PoseCache pc;
            if (_poseCache.TryGetValue(pose, out pc)) return pc;
            pc = new PoseCache();
            var rs = new List<SkinnedMeshRenderer>();
            var ix = new List<int>();
            var vs = new List<float>();
            if (pose.curves != null && _targets.Count > 0)
            {
                var index = new FacialShapeIndex[_targets.Count];
                for (int t = 0; t < _targets.Count; t++) index[t] = new FacialShapeIndex(_targetMeshes[t]);
                for (int c = 0; c < pose.curves.Length; c++)
                {
                    string nm = pose.curves[c].name;
                    if (string.IsNullOrEmpty(nm)) continue;
                    for (int t = 0; t < _targets.Count; t++)
                    {
                        int idx = index[t].Find(nm); // 完全一致 → 末尾一致（FC_ と同じ名前の規則）
                        if (idx >= 0) { rs.Add(_targets[t]); ix.Add(idx); vs.Add(pose.curves[c].value); }
                    }
                }
            }
            pc.shapeRenderers = rs.ToArray();
            pc.shapeIndices = ix.ToArray();
            pc.shapeValues = vs.ToArray();

            if (pose.bones != null && pose.bones.Length > 0)
            {
                var bt = new List<Transform>();
                var bd = new List<FacialPoseBone>();
                var all = new List<Transform>();
                GetComponentsInChildren(true, all);
                for (int b = 0; b < pose.bones.Length; b++)
                {
                    string nm = pose.bones[b].name;
                    if (string.IsNullOrEmpty(nm)) continue;
                    for (int k = 0; k < all.Count; k++)
                        if (string.Equals(all[k].name, nm, StringComparison.Ordinal)) { bt.Add(all[k]); bd.Add(pose.bones[b]); break; }
                }
                pc.boneTransforms = bt.ToArray();
                pc.bones = bd.ToArray();
            }
            _poseCache[pose] = pc;
            return pc;
        }

        void ApplyPose(FacialPoseAsset pose, float weight)
        {
            PoseCache pc = GetPoseCache(pose);
            for (int i = 0; i < pc.shapeRenderers.Length; i++)
            {
                SkinnedMeshRenderer r = pc.shapeRenderers[i];
                if (r == null) continue;
                float cur = r.GetBlendShapeWeight(pc.shapeIndices[i]);
                float applied = cur + weight * pc.shapeValues[i] * 100f; // 加算（Unity は 0〜100）
                r.SetBlendShapeWeight(pc.shapeIndices[i], applied);
                _poseShapes.Add(new PoseShapeSave { renderer = r, mesh = r.sharedMesh, index = pc.shapeIndices[i], original = cur, applied = applied });
            }
            for (int i = 0; i < pc.boneTransforms.Length; i++)
            {
                Transform t = pc.boneTransforms[i];
                if (t == null) continue;
                FacialPoseBone b = pc.bones[i];
                Quaternion dq = b.rotation;
                if (dq.x == 0f && dq.y == 0f && dq.z == 0f && dq.w == 0f) dq = Quaternion.identity; // 未設定
                Vector3 ds = b.scale;
                if (ds == Vector3.zero) ds = Vector3.one; // 未設定
                var save = new PoseBoneSave { t = t, pos = t.localPosition, rot = t.localRotation, scale = t.localScale };
                // 位置は足す・回転は親空間で掛ける・スケールは掛ける（いずれも weight で薄める）
                save.aPos = save.pos + b.position * weight;
                save.aRot = Quaternion.Slerp(Quaternion.identity, dq, weight) * save.rot;
                save.aScale = Vector3.Scale(save.scale, Vector3.Lerp(Vector3.one, ds, weight));
                t.localPosition = save.aPos;
                t.localRotation = save.aRot;
                t.localScale = save.aScale;
                _poseBones.Add(save);
            }
        }

        void RestorePose()
        {
            if (_poseShapes.Count == 0 && _poseBones.Count == 0) return;
            for (int i = _poseShapes.Count - 1; i >= 0; i--)
            {
                PoseShapeSave sv = _poseShapes[i];
                if (sv.renderer == null || sv.renderer.sharedMesh != sv.mesh) continue; // メッシュが替わっていたら番号は別のシェイプ
                // 今の値が自分の書いた値のときだけ戻す（アニメーションが書き直していたら、その値を尊重する）
                if (Mathf.Abs(sv.renderer.GetBlendShapeWeight(sv.index) - sv.applied) < 1e-3f)
                    sv.renderer.SetBlendShapeWeight(sv.index, sv.original);
            }
            _poseShapes.Clear();
            for (int i = _poseBones.Count - 1; i >= 0; i--)
            {
                PoseBoneSave sv = _poseBones[i];
                if (sv.t == null) continue;
                if ((sv.t.localPosition - sv.aPos).sqrMagnitude < 1e-10f) sv.t.localPosition = sv.pos;
                if (Mathf.Abs(Quaternion.Dot(sv.t.localRotation, sv.aRot)) > 0.999999f) sv.t.localRotation = sv.rot;
                if ((sv.t.localScale - sv.aScale).sqrMagnitude < 1e-10f) sv.t.localScale = sv.scale;
            }
            _poseBones.Clear();
        }

        // ---------------------------------------------------------------- 名前 → 番号の事前解決

        void EnsureCache()
        {
            if (!_built || _cacheData != data || _cacheBaseField != baseBone || TargetsFieldChanged() || DataShapeChanged())
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

        // 配列の参照・長さ・中身のどれが変わっても引き直す（docs/19 U-7）
        bool TargetsFieldChanged()
        {
            SkinnedMeshRenderer[] t = targets;
            if (!ReferenceEquals(t, _cacheTargetsField)) return true;
            int n = t == null ? 0 : t.Length;
            if (n != _cacheTargetsCopy.Count) return true;
            for (int i = 0; i < n; i++)
                if (!ReferenceEquals(t[i], _cacheTargetsCopy[i])) return true;
            return false;
        }

        // データの参照が同じままレイヤーの配列・格子の大きさが変わったとき（docs/19 U-2）
        bool DataShapeChanged()
        {
            FacialCorrectionData d = data;
            if (d == null) return false;
            return !ReferenceEquals(d.layers, _cacheLayers) || d.grid.cols != _cacheCols || d.grid.rows != _cacheRows
                || !ReferenceEquals(d.perspective.keys, _cachePerspKeys);
        }

        void Rebuild()
        {
            if (_rebuilding) return;
            _rebuilding = true;
            try { RebuildCore(); }
            finally { _rebuilding = false; }
        }

        void RebuildCore()
        {
            ResetCore((ZeroAllBoundOverride ?? !Application.isPlaying) && _built); // 古い対応で書いた分を先に戻す（古い番号は、同じメッシュのときだけ書く）
            _poseCache.Clear();
            _bindings.Clear();
            _written.Clear();
            _missing.Clear();
            _ambiguous.Clear();
            _targets.Clear();
            _targetMeshes.Clear();
            _layerInputs.Clear();
            _baseResolved = null;
            _intRenderers = new SkinnedMeshRenderer[0];
            _intIndices = new int[0];
            _perspCount = 0;
            _perspValues = new double[0]; _perspW = new double[0]; _perspNames = new string[0]; _perspBind = new ShapeBinding[0];

            _built = true;
            _scanFrame = Time.frameCount;
            _cacheData = data;
            _cacheBaseField = baseBone;
            _cacheTargetsField = targets;
            _cacheTargetsCopy.Clear();
            if (targets != null) for (int i = 0; i < targets.Length; i++) _cacheTargetsCopy.Add(targets[i]);
            FacialCorrectionData d = data;
            _cacheLayers = d != null ? d.layers : null;
            _cacheCols = d != null ? d.grid.cols : 0;
            _cacheRows = d != null ? d.grid.rows : 0;
            _cachePerspKeys = d != null ? d.perspective.keys : null;
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
                _layerInputs.Add(new LayerEvalInput(d.layers[i].morphNames, 0.0, d.layers[i].enabled) { ExMorphNames = d.layers[i].exMorphNames });
            _emo = new double[d.layers.Length];
            _rawEmo = new double[d.layers.Length];

            // 名前 → 書き込み先。FC_ で始まるものだけ（元のシェイプには触らない）
            var shapeIndex = new FacialShapeIndex[_targets.Count];
            for (int t = 0; t < _targets.Count; t++) shapeIndex[t] = new FacialShapeIndex(_targetMeshes[t]);
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
                    bool amb = false;
                    for (int t = 0; t < _targets.Count; t++)
                    {
                        int idx = shapeIndex[t].Find(nm);
                        if (idx >= 0) { rs.Add(_targets[t]); ix.Add(idx); if (shapeIndex[t].IsAmbiguous(nm)) amb = true; }
                    }
                    if (rs.Count == 0) { _missing.Add(nm); continue; }
                    if (amb) _ambiguous.Add(nm);
                    _bindings[nm] = NewBinding(rs, ix);
                }
            }
            // 誇張用 _Ex シェイプ: メッシュにあるものだけ結ぶ（任意のシェイプ。無くても「足りない」には数えない）
            for (int li = 0; li < d.layers.Length; li++)
            {
                string[] exn = d.layers[li].exMorphNames;
                if (exn == null) continue;
                for (int n = 0; n < exn.Length; n++)
                {
                    string nm = exn[n];
                    if (string.IsNullOrEmpty(nm) || !FacialNaming.IsFcName(nm) || !seen.Add(nm)) continue;
                    rs.Clear(); ix.Clear();
                    for (int t = 0; t < _targets.Count; t++)
                    {
                        int idx = shapeIndex[t].Find(nm);
                        if (idx >= 0) { rs.Add(_targets[t]); ix.Add(idx); }
                    }
                    if (rs.Count == 0) continue;
                    _bindings[nm] = NewBinding(rs, ix);
                }
            }
            // パース補正のシェイプ（FC_<asset>_Persp_K{n}）。ポーズが空のキーは名前なし。無いシェイプは「足りない」に数える
            FacialPerspectiveKeyData[] pk = d.perspective.keys;
            if (pk != null && pk.Length > 0)
            {
                int pn = pk.Length;
                _perspCount = pn;
                _perspValues = new double[pn]; _perspW = new double[pn]; _perspNames = new string[pn]; _perspBind = new ShapeBinding[pn];
                for (int k = 0; k < pn; k++)
                {
                    _perspValues[k] = pk[k].value;
                    string nm = pk[k].morphName;
                    if (string.IsNullOrEmpty(nm) || !FacialNaming.IsFcName(nm)) continue;
                    _perspNames[k] = nm;
                    ShapeBinding existing;
                    if (_bindings.TryGetValue(nm, out existing)) { _perspBind[k] = existing; continue; }
                    if (!seen.Add(nm)) continue;
                    rs.Clear(); ix.Clear();
                    bool amb = false;
                    for (int t = 0; t < _targets.Count; t++)
                    {
                        int idx = shapeIndex[t].Find(nm);
                        if (idx >= 0) { rs.Add(_targets[t]); ix.Add(idx); if (shapeIndex[t].IsAmbiguous(nm)) amb = true; }
                    }
                    if (rs.Count == 0) { _missing.Add(nm); continue; }
                    if (amb) _ambiguous.Add(nm);
                    _bindings[nm] = NewBinding(rs, ix);
                    _perspBind[k] = _bindings[nm];
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
                        int idx = shapeIndex[t].Find(nm);
                        if (idx >= 0) { ir.Add(_targets[t]); ii.Add(idx); break; }
                    }
                }
                _intRenderers = ir.ToArray();
                _intIndices = ii.ToArray();
            }
        }

        static ShapeBinding NewBinding(List<SkinnedMeshRenderer> rs, List<int> ix)
        {
            var meshes = new Mesh[rs.Count];
            for (int i = 0; i < meshes.Length; i++) meshes[i] = rs[i].sharedMesh;
            return new ShapeBinding { renderers = rs.ToArray(), indices = ix.ToArray(), count = rs.Count, meshes = meshes };
        }

        static bool HasShapeWithPrefix(SkinnedMeshRenderer r, string prefix)
        {
            return r != null && FacialShapeIndex.HasFcShapeWithPrefix(r.sharedMesh, prefix);
        }
    }
}
