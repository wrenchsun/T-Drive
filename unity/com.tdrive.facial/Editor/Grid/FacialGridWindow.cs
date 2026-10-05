// 格子ビューア（メニュー: Tools/T-Drive/Facial/グリッド）。選択中の Runner（または FacialCorrectionData）の格子を、Maya のグリッドタブと同じ向き・同じ操作で扱う。
//  上 = +Pitch / 左 = -Yaw / 中央 = 正面。色: 緑 = キー / 水色 = 自動生成 / 灰 = 空 / 橙の枠 = 選択中 / 赤い枠 = FC_ シェイプがメッシュに無い / 赤い点 = 今のカメラの角度。
//  マスをクリック → その点を選び、Scene ビューのカメラをその角度へ動かしてプレビューを始める（「カメラも動かす」オンのとき）。
//  赤い点（または Shift）をドラッグ → カメラを好きな角度へ動かす（点は選ばない）。
// 計算は FacialGridMath / FacialGridMapping / FacialGridDragState / FacialGridThrottle（テスト済み）。ここは描画と入力だけ。
using System.Collections.Generic;
using System.Globalization;
using TDrive.Facial.Core;
using UnityEditor;
using UnityEngine;

namespace TDrive.Facial.Editor
{
    public sealed class FacialGridWindow : EditorWindow
    {
        // Maya のグリッドタブと同じ色
        static readonly Color KeyColor = new Color32(0x3f, 0x9d, 0x51, 255);
        static readonly Color GenColor = new Color32(0x2b, 0x8f, 0xa8, 255);
        static readonly Color NoneColor = new Color32(0x46, 0x49, 0x4f, 255);
        static readonly Color SelectColor = new Color32(0xff, 0x9f, 0x1c, 255);
        static readonly Color MarkerColor = new Color32(0xff, 0x3b, 0x30, 255);
        static readonly Color MissingColor = new Color(0.90f, 0.25f, 0.20f); // Unity だけの状態（FC_ シェイプ不足）

        const float Left = 56f, Top = 38f, Right = 8f, Gap = 3f, LegendH = 22f, CellAspect = 0.75f;
        const double DragIntervalSeconds = 0.030;   // ドラッグ中にカメラを動かす最短の間隔
        const double FollowIntervalSeconds = 0.050; // カメラの追従の確認の間隔
        const float FallbackDistance = 1.5f;        // 今の距離が分からないときだけ使う（m）
        const string ViewMessagePrefix = "Scene ビューが";

        const string DragHelp = "赤い点をドラッグ（または Shift を押しながらドラッグ）すると、カメラを好きな角度へ動かせます。マスをクリックすると、その点を選びます";
        const string AxisHelp = "横 = Yaw（左の端 = −Yaw、中央 = 正面、右の端 = +Yaw）。+Yaw = キャラクターの左側から見る（カメラがキャラクターの左）、−Yaw = キャラクターの右側から見る（カメラがキャラクターの右）\n"
            + "縦 = Pitch（上 = +で見下ろす、下 = −であおる、水平 = 0°）";

        [MenuItem("Tools/T-Drive/Facial/グリッド")]
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

        [SerializeField] bool _moveCamera = true;   // 「カメラも動かす」（マスのクリックにだけ効く。ドラッグは常に動かす）
        bool _hasSelected;
        int _selRow, _selCol;
        readonly FacialGridDragState _drag = new FacialGridDragState();
        readonly FacialGridThrottle _throttle = new FacialGridThrottle(DragIntervalSeconds);
        double _dragYaw, _dragPitch;                // ドラッグ中の赤い点の角度（カメラの読み直しで揺らさない）
        string _status = "";
        string _cameraMessage = "";                 // カメラの角度を出せない理由（1 行）
        double _lastFollowCheck;
        Vector3 _lastCamPos;
        Quaternion _lastCamRot = Quaternion.identity;
        bool _hasLastCam;

        void OnEnable()
        {
            Selection.selectionChanged += OnSelectionChanged;
            EditorApplication.update += OnEditorUpdate;
            wantsMouseMove = true;
            PickFromSelection();
        }

        void OnDisable()
        {
            Selection.selectionChanged -= OnSelectionChanged;
            EditorApplication.update -= OnEditorUpdate;
            _drag.Cancel();
            _throttle.Reset();
        }

        void OnSelectionChanged()
        {
            if (!_locked) { PickFromSelection(); Repaint(); }
        }

        // 窓を開いているあいだ: ドラッグの保留分を当て、Scene ビューのカメラが動いたら赤い点を描き直す（間引く）
        void OnEditorUpdate()
        {
            double now = EditorApplication.timeSinceStartup;
            double y, p;
            if (_drag.IsDragging && _throttle.TryTake(now, out y, out p)) ApplyDragCamera(y, p);
            if (now - _lastFollowCheck < FollowIntervalSeconds) return;
            _lastFollowCheck = now;
            SceneView sv = FindSceneView();
            if (sv == null || sv.camera == null) { if (_hasLastCam) { _hasLastCam = false; Repaint(); } return; }
            Transform t = sv.camera.transform;
            if (!_hasLastCam || (t.position - _lastCamPos).sqrMagnitude > 1e-10f || Quaternion.Angle(t.rotation, _lastCamRot) > 1e-3f)
            {
                _hasLastCam = true; _lastCamPos = t.position; _lastCamRot = t.rotation;
                Repaint();
            }
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
            if (_hasSelected && (_selRow >= _data.grid.rows || _selCol >= _data.grid.cols)) _hasSelected = false;

            double camYaw, camPitch;
            bool haveCam = ReadCamera(out camYaw, out camPitch);
            if (_drag.IsDragging) { camYaw = _dragYaw; camPitch = _dragPitch; haveCam = true; }

            // 1 行目: レイヤー（左）と、カメラの読み出し（右上）
            EditorGUILayout.BeginHorizontal();
            string[] layerNames = new string[_data.layers.Length];
            for (int i = 0; i < layerNames.Length; i++) layerNames[i] = i + ": " + _data.layers[i].name;
            _layer = EditorGUILayout.Popup(new GUIContent("レイヤー", "表示するレイヤー（0 = Neutral）"), _layer, layerNames);
            var rightStyle = new GUIStyle(EditorStyles.miniLabel) { alignment = TextAnchor.MiddleRight };
            GUILayout.Label(new GUIContent(CameraReadoutText(haveCam, camYaw, camPitch), _cameraMessage), rightStyle, GUILayout.MinWidth(120f));
            EditorGUILayout.EndHorizontal();
            _moveCamera = EditorGUILayout.ToggleLeft(
                new GUIContent("カメラも動かす", "点をクリックしたとき、その角度へ Scene ビューのカメラを動かします（顔の基準ボーンを見る位置）。切ると、点を選ぶだけでカメラは動かしません。ドラッグはこの設定に関係なくカメラを動かします"),
                _moveCamera);

            _scroll = EditorGUILayout.BeginScrollView(_scroll);
            DrawGrid(haveCam, camYaw, camPitch);
            DrawSummary();
            DrawIssues();
            EditorGUILayout.EndScrollView();
        }

        string CameraReadoutText(bool haveCam, double yaw, double pitch)
        {
            if (!haveCam) return "カメラ: 角度を取得できません";
            FacialGridData g = _data.grid;
            FacialGridMarker m = FacialGridMapping.Locate(yaw, pitch, g.yawRange, g.pitchRange, g.cols, g.rows);
            return FacialGridMath.FormatCameraReadout("Scene ビュー", yaw, pitch, m.Clamped);
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
            string json = FacialSourceJson.Get(_data);
            if (_sourceFor != _data || !ReferenceEquals(_sourceJsonCache, json))
            {
                _source = FacialGridCells.ParseSource(json);
                _sourceFor = _data;
                _sourceJsonCache = json;
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

        // ---------------------------------------------------------------- 格子の図

        // 格子の図の大きさ（幅 width の中に収める）。セルは横に等分、高さは幅の 0.75 倍
        static void CellSize(float width, FacialGridData g, out float cw, out float ch)
        {
            cw = Mathf.Max((width - Left - Right) / Mathf.Max(g.cols, 1), 8f);
            ch = Mathf.Max(cw * CellAspect, 14f);
        }

        static int LegendRows(float width) { return width < 520f ? 2 : 1; }

        void DrawGrid(bool haveCam, double camYaw, double camPitch)
        {
            FacialGridData g = _data.grid;
            FacialCellKind[] kinds = FacialGridCells.Build(_data, _source, _layer);
            string[] names = _data.layers[_layer].morphNames;

            float viewWidth = Mathf.Max(position.width - 24f, 260f);
            float cw0, ch0;
            CellSize(viewWidth, g, out cw0, out ch0);
            float height = Top + g.rows * ch0 + LegendH * LegendRows(viewWidth) + 4f;
            Rect area = GUILayoutUtility.GetRect(viewWidth, height, GUILayout.ExpandWidth(true));
            float cw, ch;
            CellSize(area.width, g, out cw, out ch);
            var grid = new Rect(area.x + Left, area.y + Top, cw * g.cols, ch * g.rows);
            int id = GUIUtility.GetControlID(FocusType.Passive);
            Event e = Event.current;
            bool markerValid = haveCam && !_cameraMessage.StartsWith(ViewMessagePrefix) && HasRunnerCamera();

            if (e.type == EventType.Repaint) PaintGrid(area, grid, g, kinds, names, markerValid, camYaw, camPitch);

            // ツールチップ（後に描いたものが上）: 図全体 → マス → 赤い点
            GUI.Label(area, new GUIContent("", AxisHelp + "\n" + DragHelp));
            for (int r = 0; r < g.rows; r++)
                for (int c = 0; c < g.cols; c++)
                {
                    int i = r * g.cols + c;
                    string n = names != null && i < names.Length ? names[i] : "";
                    bool missing = !string.IsNullOrEmpty(n) && _missing.Contains(n);
                    GUI.Label(FacialGridMath.CellRect(grid, g.cols, g.rows, r, c, Gap),
                        new GUIContent("", (string.IsNullOrEmpty(n) ? "（シェイプなし）" : n) + (missing ? "（メッシュに無いシェイプ）" : "") + "\n" + DragHelp));
                }
            Vector2 mp = Vector2.zero;
            if (markerValid)
            {
                mp = FacialGridMath.AngleToPosition(grid, g, camYaw, camPitch);
                GUI.Label(new Rect(mp.x - 10f, mp.y - 10f, 20f, 20f), new GUIContent("", DragHelp));
            }

            HandleMouse(id, e, area, grid, g, markerValid, mp);

            // カーソル: 赤い点の上 / Shift 中は手、ドラッグ中は図の外でも手
            if (e.type == EventType.Repaint)
            {
                if (_drag.IsDragging) EditorGUIUtility.AddCursorRect(new Rect(0f, 0f, position.width, position.height), MouseCursor.Pan);
                else if (e.shift) EditorGUIUtility.AddCursorRect(area, MouseCursor.Pan);
                else if (markerValid) EditorGUIUtility.AddCursorRect(new Rect(mp.x - 10f, mp.y - 10f, 20f, 20f), MouseCursor.Pan);
            }

            if (_cameraMessage.Length > 0) EditorGUILayout.LabelField(_cameraMessage, EditorStyles.miniLabel);
            if (_status.Length > 0) EditorGUILayout.LabelField(_status, EditorStyles.miniLabel);
            if (_hasSelected) DrawSelectedInfo(g, names);
        }

        void DrawSelectedInfo(FacialGridData g, string[] names)
        {
            double yaw, pitch;
            FacialGridMath.PointAngles(g, _selRow, _selCol, out yaw, out pitch);
            int i = _selRow * g.cols + _selCol;
            string n = names != null && i < names.Length ? names[i] : "";
            EditorGUILayout.LabelField("選択中: R" + _selRow + " C" + _selCol + "  Yaw " + FacialGridMath.FormatAngle(yaw) + " / Pitch " + FacialGridMath.FormatAngle(pitch)
                + "  シェイプ: " + (string.IsNullOrEmpty(n) ? "（なし）" : n), EditorStyles.miniLabel);
        }

        void PaintGrid(Rect area, Rect grid, FacialGridData g, FacialCellKind[] kinds, string[] names, bool markerValid, double camYaw, double camPitch)
        {
            // 軸の見出し
            GUI.Label(new Rect(area.x, area.y + 2f, Left - 4f, Top - 4f), "Pitch ↑\nYaw →", MiniStyle(false, TextAnchor.LowerRight));
            for (int c = 0; c < g.cols; c++)
            {
                double yaw, pitch;
                FacialGridMath.PointAngles(g, 0, c, out yaw, out pitch);
                bool front = System.Math.Abs(yaw) < 1e-6;
                Rect r = FacialGridMath.CellRect(grid, g.cols, g.rows, 0, c, 0f);
                GUI.Label(new Rect(r.x, area.y, r.width, Top - 2f), FacialGridMath.FormatAngle(yaw) + (front ? "\n正面" : ""), MiniStyle(front, TextAnchor.LowerCenter));
            }
            for (int row = 0; row < g.rows; row++)
            {
                double yaw, pitch;
                FacialGridMath.PointAngles(g, row, 0, out yaw, out pitch);
                bool level = System.Math.Abs(pitch) < 1e-6;
                Rect r = FacialGridMath.CellRect(grid, g.cols, g.rows, row, 0, 0f);
                GUI.Label(new Rect(area.x, r.y, Left - 6f, r.height), FacialGridMath.FormatAngle(pitch) + (level ? "\n水平" : ""), MiniStyle(level, TextAnchor.MiddleRight));
            }

            // マス
            Vector2 mouse = Event.current.mousePosition;
            var cellLabel = MiniStyle(false, TextAnchor.MiddleCenter);
            cellLabel.normal.textColor = new Color(1f, 1f, 1f, 0.75f);
            for (int r = 0; r < g.rows; r++)
                for (int c = 0; c < g.cols; c++)
                {
                    Rect rect = FacialGridMath.CellRect(grid, g.cols, g.rows, r, c, Gap);
                    int i = r * g.cols + c;
                    FacialCellKind k = i < kinds.Length ? kinds[i] : FacialCellKind.None;
                    string n = names != null && i < names.Length ? names[i] : "";
                    bool missing = !string.IsNullOrEmpty(n) && _missing.Contains(n);
                    Color fill = k == FacialCellKind.Key ? KeyColor : (k == FacialCellKind.Generated ? GenColor : NoneColor);
                    if (!_drag.IsDragging && rect.Contains(mouse)) fill = Color.Lerp(fill, Color.white, 0.18f); // 乗せたマスを少し明るく
                    EditorGUI.DrawRect(rect, fill);
                    if (missing) DrawBorder(rect, MissingColor, 2f);
                    if (rect.width >= 40f && rect.height >= 26f) GUI.Label(rect, "R" + r + " C" + c, cellLabel);
                    if (_hasSelected && _selRow == r && _selCol == c)
                        DrawBorder(new Rect(rect.x - 0.5f, rect.y - 0.5f, rect.width + 1f, rect.height + 1f), SelectColor, 3f);
                }

            // カメラの赤い点（範囲外は端に寄せて「範囲外」）
            if (markerValid)
            {
                FacialGridMarker m = FacialGridMapping.Locate(camYaw, camPitch, g.yawRange, g.pitchRange, g.cols, g.rows);
                DrawMarker(FacialGridMath.AngleToPosition(grid, g, camYaw, camPitch), m.Clamped);
            }

            DrawLegend(area, grid);
        }

        static GUIStyle MiniStyle(bool bold, TextAnchor anchor)
        {
            return new GUIStyle(EditorStyles.miniLabel) { alignment = anchor, fontStyle = bold ? FontStyle.Bold : FontStyle.Normal };
        }

        static void DrawMarker(Vector2 c, bool outOfRange)
        {
            Color old = Handles.color;
            var p = new Vector3(c.x, c.y, 0f);
            if (outOfRange)
            {
                Handles.color = new Color(MarkerColor.r, MarkerColor.g, MarkerColor.b, 0.25f);
                Handles.DrawSolidDisc(p, Vector3.forward, 9f);
                Handles.color = MarkerColor;
                Handles.DrawWireDisc(p, Vector3.forward, 9f);
                GUIStyle st = MiniStyle(false, TextAnchor.UpperCenter);
                st.normal.textColor = Color.white;
                GUI.Label(new Rect(c.x - 40f, c.y + 10f, 80f, 14f), "範囲外", st);
            }
            else
            {
                Handles.color = Color.white;
                Handles.DrawSolidDisc(p, Vector3.forward, 8f);
                Handles.color = MarkerColor;
                Handles.DrawSolidDisc(p, Vector3.forward, 6f);
            }
            Handles.color = old;
        }

        static void DrawLegend(Rect area, Rect grid)
        {
            GUIStyle mini = EditorStyles.miniLabel;
            string[] labels = { "キー", "自動生成", "空", "シェイプ不足", "選択中", "カメラ" };
            float x = area.x + Left, y = grid.yMax + 4f, right = area.xMax - Right;
            for (int i = 0; i < labels.Length; i++)
            {
                float w = 18f + mini.CalcSize(new GUIContent(labels[i])).x + 12f;
                if (x + w > right && x > area.x + Left) { x = area.x + Left; y += LegendH; }
                var r = new Rect(x, y + 4f, 14f, 12f);
                switch (i)
                {
                    case 0: EditorGUI.DrawRect(r, KeyColor); break;
                    case 1: EditorGUI.DrawRect(r, GenColor); break;
                    case 2: EditorGUI.DrawRect(r, NoneColor); break;
                    case 3: EditorGUI.DrawRect(r, NoneColor); DrawBorder(r, MissingColor, 2f); break;
                    case 4: DrawBorder(r, SelectColor, 3f); break;
                    default: DrawMarker(r.center, false); break;
                }
                GUI.Label(new Rect(x + 18f, y, w, LegendH), labels[i], mini);
                x += w;
            }
        }

        void DrawSummary()
        {
            FacialCellKind[] kinds = FacialGridCells.Build(_data, _source, _layer);
            EditorGUILayout.LabelField("キー " + FacialGridCells.Count(kinds, FacialCellKind.Key)
                + " / 生成 " + FacialGridCells.Count(kinds, FacialCellKind.Generated)
                + " / 空 " + FacialGridCells.Count(kinds, FacialCellKind.None)
                + (_missing.Count > 0 ? " / シェイプ不足 " + _missing.Count : ""));
            if (_source == null && string.IsNullOrEmpty(FacialSourceJson.Get(_data)))
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

        // ---------------------------------------------------------------- 入力（状態の判断は FacialGridDragState）

        void HandleMouse(int id, Event e, Rect area, Rect grid, FacialGridData g, bool markerValid, Vector2 markerPos)
        {
            EventType t = e.GetTypeForControl(id);
            if (t == EventType.MouseDown && e.button == 0 && area.Contains(e.mousePosition))
            {
                int row, col;
                bool onCell = FacialGridMath.TryPointAt(grid, g.cols, g.rows, e.mousePosition, out row, out col);
                bool onMarker = markerValid && FacialGridDragState.OnMarker(e.mousePosition.x, e.mousePosition.y, markerPos.x, markerPos.y);
                FacialGridDragState.Output o = _drag.Press(e.mousePosition.x, e.mousePosition.y, e.shift, onMarker, onCell);
                if (_drag.IsActive) GUIUtility.hotControl = id;
                Apply(o, e, grid, g);
                e.Use();
            }
            else if (t == EventType.MouseDrag && GUIUtility.hotControl == id)
            {
                Apply(_drag.Move(e.mousePosition.x, e.mousePosition.y), e, grid, g);
                e.Use();
            }
            else if (t == EventType.MouseUp && GUIUtility.hotControl == id && e.button == 0)
            {
                Apply(_drag.Release(e.mousePosition.x, e.mousePosition.y), e, grid, g);
                GUIUtility.hotControl = 0;
                e.Use();
            }
            else if (t == EventType.MouseMove || e.type == EventType.KeyDown || e.type == EventType.KeyUp) Repaint(); // Shift でカーソルを変える
        }

        void Apply(FacialGridDragState.Output o, Event e, Rect grid, FacialGridData g)
        {
            if ((o & FacialGridDragState.Output.BeginDrag) != 0)
            {
                if (!CanDriveCamera())
                {
                    _drag.Cancel(); // カメラを動かせない: 何もしない（理由は 1 行で出る）
                    GUIUtility.hotControl = 0;
                    Repaint();
                    return;
                }
                _throttle.Reset();
                _status = "";
            }
            if ((o & FacialGridDragState.Output.MoveTo) != 0)
            {
                double yaw, pitch, y, p;
                FacialGridMath.PositionToAngles(grid, g, e.mousePosition, out yaw, out pitch);
                _dragYaw = yaw; _dragPitch = pitch;
                if (_throttle.Offer(EditorApplication.timeSinceStartup, yaw, pitch, out y, out p)) ApplyDragCamera(y, p);
            }
            if ((o & FacialGridDragState.Output.EndDrag) != 0)
            {
                double y, p;
                if (_throttle.Flush(EditorApplication.timeSinceStartup, out y, out p)) ApplyDragCamera(y, p); // 最後の位置は必ず当てる
                var ci = CultureInfo.InvariantCulture;
                _status = "カメラを動かしました（Yaw " + _dragYaw.ToString("0.0", ci) + " / Pitch " + _dragPitch.ToString("0.0", ci) + "）";
                Repaint();
            }
            if ((o & FacialGridDragState.Output.ClickAtPress) != 0)
            {
                int row, col;
                if (FacialGridMath.TryPointAt(grid, g.cols, g.rows, new Vector2(_drag.PressX, _drag.PressY), out row, out col)) ClickPoint(row, col);
            }
            Repaint();
        }

        // マスのクリック: 点を選ぶ。「カメラも動かす」がオンなら、その角度へカメラを動かしてプレビューを始める
        void ClickPoint(int row, int col)
        {
            _hasSelected = true; _selRow = row; _selCol = col;
            _status = "";
            if (_moveCamera) GoToPoint(row, col);
        }

        // ---------------------------------------------------------------- Scene ビューを動かす

        static SceneView FindSceneView()
        {
            SceneView sv = SceneView.lastActiveSceneView;
            if (sv == null && SceneView.sceneViews.Count > 0) sv = SceneView.sceneViews[0] as SceneView;
            return sv;
        }

        bool HasRunnerBone(out Transform bone)
        {
            bone = null;
            if (_runner == null || _runner.data != _data) return false;
            bone = _runner.ResolvedBaseBone;
            return bone != null;
        }

        bool HasRunnerCamera()
        {
            Transform bone;
            SceneView sv = FindSceneView();
            return HasRunnerBone(out bone) && sv != null && sv.camera != null && !sv.in2DMode && !sv.orthographic;
        }

        // カメラを動かせない理由（動かせるなら空）。1 行で出す
        string CameraProblem()
        {
            Transform bone;
            if (_runner == null || _runner.data != _data) return "Runner を選ぶと、カメラの角度が出て、カメラを動かせます";
            if (!HasRunnerBone(out bone)) return "基準ボーンが見つからないので、カメラの角度を出せません";
            SceneView sv = FindSceneView();
            if (sv == null || sv.camera == null) return ViewMessagePrefix + "開いていません";
            if (sv.in2DMode || sv.orthographic) return ViewMessagePrefix + "正投影（または 2D）なので、角度を指定してカメラを動かせません。透視に切り替えてください";
            return "";
        }

        /// <summary>Scene ビューのカメラの今の角度（Runner と同じ式）。出せないときは false にして理由を _cameraMessage へ。</summary>
        bool ReadCamera(out double yaw, out double pitch)
        {
            yaw = pitch = 0.0;
            _cameraMessage = CameraProblem();
            if (_cameraMessage.Length > 0) return false;
            Transform bone;
            HasRunnerBone(out bone);
            Vector3 center, fwd;
            FacialGridMath.BoneFrame(bone, _data.grid, out center, out fwd);
            FacialGridMath.ViewAnglesFromFrame(center, fwd, FindSceneView().camera.transform.position, out yaw, out pitch);
            return true;
        }

        bool CanDriveCamera()
        {
            _cameraMessage = CameraProblem();
            return _cameraMessage.Length == 0;
        }

        // ドラッグ中の 1 回分: カメラを (yaw, pitch) へ（今の距離のまま中心を向く）。プレビューは始めない・点は選ばない
        void ApplyDragCamera(double yaw, double pitch)
        {
            Transform bone;
            SceneView sv = FindSceneView();
            if (!HasRunnerBone(out bone) || sv == null || sv.in2DMode || sv.orthographic) return;
            MoveCameraTo(sv, bone, yaw, pitch);
            if (FacialPreviewDriver.IsOn(_runner)) FacialPreviewDriver.GetState(_runner).forceEval = true;
        }

        void MoveCameraTo(SceneView sv, Transform bone, double yaw, double pitch)
        {
            Vector3 center, fwd, pos;
            Quaternion rot;
            FacialGridMath.BoneFrame(bone, _data.grid, out center, out fwd);
            float dist = FacialGridMath.KeepDistance(sv.camera.transform.position, center, FallbackDistance); // 距離は今のまま
            FacialGridMath.CameraPoseFromFrame(center, fwd, yaw, pitch, dist, out pos, out rot);
            MoveSceneView(sv, center, rot, dist);
        }

        void GoToPoint(int row, int col)
        {
            if (_runner == null || _runner.data != _data) return;
            Transform bone = _runner.ResolvedBaseBone;
            if (bone == null) { Debug.LogWarning("[Facial] 基準ボーンが見つからないので、カメラを動かせません"); return; }
            SceneView sv = FindSceneView();
            if (sv == null) { Debug.LogWarning("[Facial] Scene ビューが開いていません"); return; }

            double yaw, pitch;
            FacialGridMath.PointAngles(_data.grid, row, col, out yaw, out pitch);
            MoveCameraTo(sv, bone, yaw, pitch);

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
