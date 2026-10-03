// デバッグ表示（FU-7、R-27）。UE 版の fc.Debug に相当: 0 = 切 / 1 = HUD / 2 = HUD + 3D（Scene ビューの Gizmo）。
// HUD は Runner ごとに Yaw / Pitch・スナップ・倍率・今効いている重みの上位 N 件・視点の出どころを出す。
// 3D は頭の前方向・視点への向き・格子の中心。レベル 0 のときは何もしない（OnGUI は即 return。GC 割り当てなし）。
// 使い方: 好きな GameObject に付ける（Runner と同じオブジェクトならその Runner だけ、別なら有効な全 Runner）。
//         コードから一括で切り替えるなら FacialDebugOverlay.GlobalLevel（再生中だけ HUD 用の隠しオブジェクトを自動で作る）。
using System.Collections.Generic;
using System.Text;
using TDrive.Facial.Core;
using UnityEngine;

namespace TDrive.Facial
{
    [DisallowMultipleComponent]
    [AddComponentMenu("T-Drive/Facial Debug Overlay")]
    public sealed class FacialDebugOverlay : MonoBehaviour
    {
        static int s_globalLevel;
        static FacialDebugOverlay s_hidden;

        /// <summary>
        /// 全体のデバッグレベル（0〜2）。各コンポーネントの debugLevel との大きいほうが使われる。
        /// 0 より大きくすると、再生中でコンポーネントが 1 つも無ければ HUD 用の隠しオブジェクトを作る。
        /// </summary>
        public static int GlobalLevel
        {
            get { return s_globalLevel; }
            set
            {
                s_globalLevel = Mathf.Clamp(value, 0, 2);
                if (s_globalLevel > 0 && Application.isPlaying && s_hidden == null && FindObjectsByType<FacialDebugOverlay>(FindObjectsSortMode.None).Length == 0)
                {
                    var go = new GameObject("FacialDebugOverlay (auto)") { hideFlags = HideFlags.HideAndDontSave };
                    DontDestroyOnLoad(go);
                    s_hidden = go.AddComponent<FacialDebugOverlay>();
                }
            }
        }

        [Tooltip("デバッグ表示のレベル。0 = 切 / 1 = 画面に HUD（Yaw・Pitch・重み）/ 2 = HUD + Scene ビューに 3D 表示（頭の前方向・視点の向き・格子の中心）")]
        [Range(0, 2)] public int debugLevel;

        [Tooltip("表示する Runner。空なら同じ GameObject の Runner、無ければ有効な全 Runner")]
        public FacialCorrectionRunner runner;

        [Tooltip("HUD に出す、今効いているシェイプの重みの件数（大きい順）")]
        [Range(1, 16)] public int topWeights = 6;

        [Tooltip("HUD の左上の位置（画面のピクセル）")]
        public Vector2 hudPosition = new Vector2(10f, 10f);

        /// <summary>実際に使うレベル（コンポーネントの値と GlobalLevel の大きいほう）。</summary>
        public int EffectiveLevel { get { return Mathf.Max(debugLevel, s_globalLevel); } }

        readonly StringBuilder _sb = new StringBuilder(512);
        readonly List<MorphWeight> _weights = new List<MorphWeight>(64);
        GUIStyle _style;
        FacialCorrectionRunner _own;

        void OnDestroy()
        {
            if (s_hidden == this) s_hidden = null;
        }

        FacialCorrectionRunner SingleRunner()
        {
            if (runner != null) return runner;
            if (_own == null) _own = GetComponent<FacialCorrectionRunner>();
            return _own;
        }

        // ---------------------------------------------------------------- HUD

        void OnGUI()
        {
            if (EffectiveLevel < 1) return;              // 切のときは何もしない
            if (Event.current.type != EventType.Repaint) return;

            _sb.Length = 0;
            FacialCorrectionRunner single = SingleRunner();
            if (single != null) AppendRunner(_sb, single);
            else
            {
                IReadOnlyList<FacialCorrectionRunner> all = FacialCorrectionRunner.ActiveRunners;
                for (int i = 0; i < all.Count; i++) if (all[i] != null) AppendRunner(_sb, all[i]);
            }
            if (_sb.Length == 0) _sb.Append("FacialDebug: no runner");

            if (_style == null)
            {
                _style = new GUIStyle(GUI.skin.box)
                {
                    alignment = TextAnchor.UpperLeft,
                    richText = false,
                    wordWrap = false,
                    fontSize = 12,
                    normal = { textColor = Color.white },
                };
            }
            string text = _sb.ToString();
            Vector2 size = _style.CalcSize(new GUIContent(text));
            GUI.Box(new Rect(hudPosition.x, hudPosition.y, size.x + 12f, size.y + 8f), text, _style);
        }

        // 英数字のみ（実機のフォントに日本語が無くても読める）
        void AppendRunner(StringBuilder sb, FacialCorrectionRunner r)
        {
            sb.Append(r.name).Append('\n');
            sb.Append("  Yaw ").Append(r.CurrentYaw.ToString("F1")).Append("  Pitch ").Append(r.CurrentPitch.ToString("F1"));
            sb.Append(r.Snapped ? "  [SNAP]" : "").Append('\n');
            sb.Append("  Scale ").Append(r.LastScale.ToString("F2")).Append("  Shapes ").Append(r.ActiveWeightCount).Append('\n');
            sb.Append("  Viewer ").Append(r.LastViewerSource.ToString());
            if (r.LastViewer != null) sb.Append(" (").Append(r.LastViewer.name).Append(')');
            sb.Append("  Angles ").Append(r.LastAngleSource.ToString()).Append('\n');

            r.GetActiveWeights(_weights);
            int n = Mathf.Min(topWeights, _weights.Count);
            for (int k = 0; k < n; k++)
            {
                // 大きい順に 1 件ずつ選ぶ（並べ替えの割り当てを避ける）
                int best = k;
                for (int j = k + 1; j < _weights.Count; j++)
                    if (_weights[j].Weight > _weights[best].Weight) best = j;
                MorphWeight tmp = _weights[k]; _weights[k] = _weights[best]; _weights[best] = tmp;
                sb.Append("  ").Append(_weights[k].MorphName).Append(": ").Append(_weights[k].Weight.ToString("F2")).Append('\n');
            }
        }

        // ---------------------------------------------------------------- 3D（Gizmo）

        void OnDrawGizmos()
        {
            if (EffectiveLevel < 2) return;
            FacialCorrectionRunner single = SingleRunner();
            if (single != null) DrawRunner(single);
            else
            {
                IReadOnlyList<FacialCorrectionRunner> all = FacialCorrectionRunner.ActiveRunners;
                for (int i = 0; i < all.Count; i++) if (all[i] != null) DrawRunner(all[i]);
            }
        }

        static void DrawRunner(FacialCorrectionRunner r)
        {
            FacialCorrectionData d = r.data;
            Transform bone = r.ResolvedBaseBone;
            if (d == null || bone == null) return;

            Vector3 co = d.grid.centerOffset;
            Vector3 center = bone.position + bone.rotation * co;
            Vec3 axis;
            Vector3 fwd = bone.forward;
            if (FacialSpace.TryAxisVector(d.grid.forwardAxis, out axis)) fwd = bone.rotation * new Vector3((float)axis.X, (float)axis.Y, (float)axis.Z);

            Color old = Gizmos.color;
            Gizmos.color = new Color(0.3f, 0.6f, 1f);            // 頭の前方向（青）
            Gizmos.DrawLine(center, center + fwd.normalized * 0.4f);
            Gizmos.color = Color.cyan;                            // 格子の中心（水色）
            Gizmos.DrawWireSphere(center, 0.02f);
            if (r.LastViewer != null)
            {
                Gizmos.color = Color.yellow;                      // 視点への向き（黄）
                Vector3 to = r.LastViewer.position - center;
                Gizmos.DrawLine(center, center + to.normalized * Mathf.Min(to.magnitude, 1.5f));
            }
            Gizmos.color = old;
#if UNITY_EDITOR
            UnityEditor.Handles.Label(center + Vector3.up * 0.08f,
                "Yaw " + r.CurrentYaw.ToString("F1") + " / Pitch " + r.CurrentPitch.ToString("F1") + (r.Snapped ? " [SNAP]" : ""));
#endif
        }
    }
}
