// 格子ビューア（メニュー: T-Drive/Facial/グリッド）。選択中の Runner（または FacialCorrectionData）の格子を Maya のグリッドタブと同じ向きで表示する。
//  上 = +Pitch / 左 = -Yaw。色: 緑 = キー / 水色 = 自動生成 / 灰 = 無し / 赤い枠 = FC_ シェイプがメッシュに無い。赤い点 = 今のプレビューの角度。
//  点をクリック → Scene ビューのカメラをその角度へ動かしてプレビューを始める（Runner があるとき）。
// 幾何・角度の計算はすべて FacialGridMath（テスト済み）。ここは描画と入力だけ。
using System.Collections.Generic;
using TDrive.Facial.Core;
using UnityEditor;
using UnityEngine;

namespace TDrive.Facial.Editor
{
    public sealed class FacialGridWindow : EditorWindow
    {
        static readonly Color KeyColor = new Color(0.36f, 0.74f, 0.38f);
        static readonly Color GenColor = new Color(0.45f, 0.70f, 0.90f);
        static readonly Color NoneColor = new Color(0.30f, 0.30f, 0.30f);
        static readonly Color MissingColor = new Color(0.90f, 0.25f, 0.20f);
        static readonly Color LiveColor = new Color(1f, 0.15f, 0.15f);

        [MenuItem("T-Drive/Facial/グリッド")]
        public static void OpenFromMenu() { Open(null); }

        /// <summary>窓を開く。runner を渡すとその Runner を対象にして固定する。</summary>
        public static void Open(FacialCorrectionRunner runner)
        {
            var w = GetWindow<FacialGridWindow>(false, "Facial グリッド");
            w.minSize = new Vector2(320f, 360f);
            if (runner != null) { w._runner = runner; w._data = runner.data; w._locked = true; }
            else w.PickFromSelection();
            w.Show();
        }

        FacialCorrectionRunner _runner;
        FacialCorrectionData _data;
        bool _locked;
        int _layer;
        Vector2 _scroll;
        FcDocument _source;
        FacialCorrectionData _sourceFor;
        string _sourceJsonCache;
        List<FacialIssue> _issues;
        FacialCorrectionData _issuesFor;
        HashSet<string> _missing = new HashSet<string>();

        void OnEnable()
        {
            Selection.selectionChanged += OnSelectionChanged;
            PickFromSelection();
        }

        void OnDisable() { Selection.selectionChanged -= OnSelectionChanged; }

        void OnSelectionChanged()
        {
            if (!_locked) { PickFromSelection(); Repaint(); }
        }

        void OnInspectorUpdate()
        {
            if (_runner != null && FacialPreviewDriver.IsOn(_runner)) Repaint(); // 赤い点の追従
        }

        void PickFromSelection()
        {
            GameObject go = Selection.activeGameObject;
            if (go != null)
            {
                var r = go.GetComponentInParent<FacialCorrectionRunner>();
                if (r == null) r = go.GetComponentInChildren<FacialCorrectionRunner>();
                if (r != null) { _runner = r; _data = r.data; return; }
            }
            var d = Selection.activeObject as FacialCorrectionData;
            if (d != null) { _runner = null; _data = d; }
            else if (_runner != null) _data = _runner.data; // 選択が無関係ならそのまま
        }

        void OnGUI()
        {
            EditorGUILayout.Space(4);
            DrawHeader();
            if (_data == null)
            {
                EditorGUILayout.HelpBox("Runner（または FacialCorrectionData）を選ぶと、格子を表示します。", MessageType.Info);
                return;
            }
            RefreshCaches();
            if (_data.layers == null || _data.layers.Length == 0 || _data.grid.cols <= 0 || _data.grid.rows <= 0)
            {
                EditorGUILayout.HelpBox("格子またはレイヤーが空です。", MessageType.Warning);
                DrawIssues();
                return;
            }
            _layer = Mathf.Clamp(_layer, 0, _data.layers.Length - 1);

            string[] layerNames = new string[_data.layers.Length];
            for (int i = 0; i < layerNames.Length; i++) layerNames[i] = i + ": " + _data.layers[i].name;
            _layer = EditorGUILayout.Popup(new GUIContent("レイヤー", "表示するレイヤー（0 = Neutral）"), _layer, layerNames);

            _scroll = EditorGUILayout.BeginScrollView(_scroll);
            DrawGrid();
            DrawLegend();
            DrawIssues();
            EditorGUILayout.EndScrollView();
        }

        void DrawHeader()
        {
            EditorGUILayout.BeginHorizontal();
            string label = _runner != null ? _runner.name : (_data != null ? _data.name : "（なし）");
            EditorGUILayout.LabelField("対象: " + label, EditorStyles.boldLabel);
            _locked = GUILayout.Toggle(_locked, new GUIContent("固定", "オンにすると、選択を変えても対象を変えない"), "Button", GUILayout.Width(48));
            EditorGUILayout.EndHorizontal();
            if (_runner != null && !Application.isPlaying)
            {
                bool on = FacialPreviewDriver.IsOn(_runner);
                bool now = GUILayout.Toggle(on, on ? "プレビュー: オン" : "プレビュー: オフ", "Button");
                if (now != on) FacialPreviewDriver.SetOn(_runner, now);
            }
        }

        void RefreshCaches()
        {
            if (_sourceFor != _data || _sourceJsonCache != _data.sourceJson)
            {
                _source = FacialGridCells.ParseSource(_data.sourceJson);
                _sourceFor = _data;
                _sourceJsonCache = _data.sourceJson;
            }
            _missing.Clear();
            if (_runner != null && _runner.data == _data)
            {
                IReadOnlyList<string> m = _runner.GetMissingShapeNames();
                for (int i = 0; i < m.Count; i++) _missing.Add(m[i]);
            }
            if (_issues == null || _issuesFor != _data)
            {
                _issues = _runner != null && _runner.data == _data ? FacialValidation.Run(_runner) : FacialValidation.RunStructure(_data);
                _issuesFor = _data;
            }
        }

        void DrawGrid()
        {
            FacialGridData g = _data.grid;
            FacialCellKind[] kinds = FacialGridCells.Build(_data, _source, _layer);
            string[] names = _data.layers[_layer].morphNames;

            GUILayout.Label("上 = +Pitch（カメラが上）　左 = −Yaw（カメラが右）", EditorStyles.miniLabel);
            float cell = Mathf.Clamp((position.width - 60f) / Mathf.Max(1, g.cols), 36f, 80f);
            Rect area = GUILayoutUtility.GetRect(cell * g.cols + 30f, cell * 0.8f * g.rows + 16f, GUILayout.ExpandWidth(false));
            Rect grid = new Rect(area.x + 26f, area.y + 14f, cell * g.cols, cell * 0.8f * g.rows);

            GUI.Label(new Rect(grid.x, area.y - 2f, 100f, 14f), "−Yaw", EditorStyles.miniLabel);
            GUI.Label(new Rect(grid.xMax - 40f, area.y - 2f, 40f, 14f), "+Yaw", EditorStyles.miniLabel);
            GUI.Label(new Rect(area.x, grid.y, 26f, 14f), "+P", EditorStyles.miniLabel);
            GUI.Label(new Rect(area.x, grid.yMax - 14f, 26f, 14f), "−P", EditorStyles.miniLabel);

            Event e = Event.current;
            var small = new GUIStyle(EditorStyles.miniLabel) { alignment = TextAnchor.MiddleCenter, normal = { textColor = Color.black } };
            for (int r = 0; r < g.rows; r++)
                for (int c = 0; c < g.cols; c++)
                {
                    Rect rect = FacialGridMath.CellRect(grid, g.cols, g.rows, r, c, 3f);
                    int i = r * g.cols + c;
                    FacialCellKind k = i < kinds.Length ? kinds[i] : FacialCellKind.None;
                    string n = names != null && i < names.Length ? names[i] : "";
                    bool missing = !string.IsNullOrEmpty(n) && _missing.Contains(n);
                    EditorGUI.DrawRect(rect, k == FacialCellKind.Key ? KeyColor : (k == FacialCellKind.Generated ? GenColor : NoneColor));
                    if (missing) DrawBorder(rect, MissingColor, 2f);
                    double yaw, pitch;
                    FacialGridMath.PointAngles(g, r, c, out yaw, out pitch);
                    var content = new GUIContent("R" + r + " C" + c + "\n" + yaw.ToString("0") + " / " + pitch.ToString("0") + (missing ? "\n!" : ""),
                        n + (missing ? "（メッシュに無いシェイプ）" : ""));
                    GUI.Label(rect, content, k == FacialCellKind.None ? EditorStyles.centeredGreyMiniLabel : small);
                }

            // 今のプレビューの角度（赤い点）
            if (_runner != null && _runner.data == _data && _runner.HasValidAngles)
            {
                Vector2 p = FacialGridMath.AngleToPosition(grid, g, _runner.CurrentYaw, _runner.CurrentPitch);
                EditorGUI.DrawRect(new Rect(p.x - 5f, p.y - 5f, 10f, 10f), LiveColor);
            }

            // クリック: その角度へカメラを動かしてプレビュー開始
            if (e.type == EventType.MouseDown && e.button == 0)
            {
                int row, col;
                if (FacialGridMath.TryPointAt(grid, g.cols, g.rows, e.mousePosition, out row, out col))
                {
                    GoToPoint(row, col);
                    e.Use();
                }
            }
            if (_runner != null)
                EditorGUILayout.LabelField("今の角度 Yaw / Pitch", _runner.CurrentYaw.ToString("F1") + " / " + _runner.CurrentPitch.ToString("F1"));
            else
                EditorGUILayout.LabelField("Runner を選ぶと、点のクリックでカメラが動き、今の角度が表示されます", EditorStyles.miniLabel);
        }

        void DrawLegend()
        {
            FacialCellKind[] kinds = FacialGridCells.Build(_data, _source, _layer);
            EditorGUILayout.LabelField("キー " + FacialGridCells.Count(kinds, FacialCellKind.Key)
                + " / 自動生成 " + FacialGridCells.Count(kinds, FacialCellKind.Generated)
                + " / 無し " + FacialGridCells.Count(kinds, FacialCellKind.None)
                + (_missing.Count > 0 ? " / シェイプ不足 " + _missing.Count : ""));
            EditorGUILayout.LabelField("緑 = キー　水色 = 自動生成　灰 = 無し　赤い枠 = メッシュに FC_ シェイプが無い　赤い点 = 今の角度", EditorStyles.wordWrappedMiniLabel);
            if (_source == null && string.IsNullOrEmpty(_data.sourceJson))
                EditorGUILayout.LabelField("元の JSON が無いので、キーと自動生成の区別はできません", EditorStyles.miniLabel);
        }

        void DrawIssues()
        {
            EditorGUILayout.Space();
            if (_issues == null) return;
            int e = FacialValidation.Count(_issues, FacialIssueSeverity.Error);
            int w = FacialValidation.Count(_issues, FacialIssueSeverity.Warning);
            int n = FacialValidation.Count(_issues, FacialIssueSeverity.Info);
            EditorGUILayout.LabelField("検証（エラー " + e + " / 警告 " + w + " / 情報 " + n + "）", EditorStyles.boldLabel);
            for (int i = 0; i < _issues.Count && i < 40; i++)
                EditorGUILayout.HelpBox(_issues[i].Message, FacialCorrectionRunnerEditor.ToMessageType(_issues[i].Severity));
            if (GUILayout.Button("もう一度検証する")) _issues = null;
        }

        static void DrawBorder(Rect r, Color c, float t)
        {
            EditorGUI.DrawRect(new Rect(r.x, r.y, r.width, t), c);
            EditorGUI.DrawRect(new Rect(r.x, r.yMax - t, r.width, t), c);
            EditorGUI.DrawRect(new Rect(r.x, r.y, t, r.height), c);
            EditorGUI.DrawRect(new Rect(r.xMax - t, r.y, t, r.height), c);
        }

        // ---------------------------------------------------------------- Scene ビューを動かす

        void GoToPoint(int row, int col)
        {
            if (_runner == null || _runner.data != _data) return;
            Transform bone = _runner.ResolvedBaseBone;
            if (bone == null) { Debug.LogWarning("[Facial] 基準ボーンが見つからないので、カメラを動かせません"); return; }
            SceneView sv = SceneView.lastActiveSceneView;
            if (sv == null && SceneView.sceneViews.Count > 0) sv = SceneView.sceneViews[0] as SceneView;
            if (sv == null) { Debug.LogWarning("[Facial] Scene ビューが開いていません"); return; }

            double yaw, pitch;
            FacialGridMath.PointAngles(_data.grid, row, col, out yaw, out pitch);
            Vector3 center = FacialGridMath.GridCenter(bone.position, bone.rotation, _data.grid.centerOffset);
            // 距離は今のカメラのまま（近すぎる・遠すぎるときは範囲に収める）
            float dist = Mathf.Clamp(Vector3.Distance(sv.camera.transform.position, center), 0.4f, 8f);
            Vector3 pos; Quaternion rot;
            FacialGridMath.CameraPose(bone.position, bone.rotation, _data.grid.forwardAxis, _data.grid.centerOffset, yaw, pitch, dist, out pos, out rot);
            MoveSceneView(sv, center, rot, dist);

            FacialPreviewState st = FacialPreviewDriver.GetState(_runner);
            st.mode = FacialPreviewViewMode.SceneCamera;
            st.correction = true;
            st.forceEval = true;
            if (!FacialPreviewDriver.IsOn(_runner)) FacialPreviewDriver.SetOn(_runner, true);
        }

        /// <summary>Scene ビューのカメラを、center を向き rot の向きで distance だけ離れた位置へ。</summary>
        static void MoveSceneView(SceneView sv, Vector3 center, Quaternion rot, float distance)
        {
            sv.in2DMode = false;
            sv.LookAt(center, rot, 1f, false, true);
            float d1 = sv.cameraDistance; // size = 1 のときの距離（size に比例）
            if (d1 > 1e-4f) sv.size = distance / d1;
            sv.Repaint();
        }
    }
}
