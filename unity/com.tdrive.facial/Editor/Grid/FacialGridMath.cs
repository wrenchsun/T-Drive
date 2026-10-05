// 格子ビューアの幾何・角度の計算（画面を開かずにテストできる純粋な静的関数）。
// 約束（Maya のグリッドタブと同じ）: 上が +Pitch（カメラが上 = ふかん）、左が -Yaw。点 (row, col) の row 0 = -Pitch、col 0 = -Yaw。
//   +Yaw = カメラがキャラクターの左 / +Pitch = カメラが上。カメラの位置は Runner の視点の角度計算の逆（FacialSpace）。
using TDrive.Facial.Core;
using UnityEngine;

namespace TDrive.Facial.Editor
{
    public static class FacialGridMath
    {
        static readonly SpaceConverter UnityToCanonical = FacialSpace.Converter(FacialSpace.Unity, FacialSpace.Canonical);
        static readonly SpaceConverter CanonicalToUnity = FacialSpace.Converter(FacialSpace.Canonical, FacialSpace.Unity);

        // ---------------------------------------------------------------- 図の中の位置

        /// <summary>画面の行（0 = 一番上）→ 格子の row。上が最大の Pitch なので反転する。</summary>
        public static int DisplayRowToRow(int displayRow, int rows) { return rows - 1 - displayRow; }

        /// <summary>点 (row, col) の矩形。area を cols × rows に等分して gap だけ内側に縮める。row が大きい = 上。</summary>
        public static Rect CellRect(Rect area, int cols, int rows, int row, int col, float gap)
        {
            if (cols <= 0 || rows <= 0) return new Rect(area.x, area.y, 0f, 0f);
            float cw = area.width / cols;
            float ch = area.height / rows;
            int displayRow = rows - 1 - row;
            return new Rect(area.x + col * cw + gap * 0.5f, area.y + displayRow * ch + gap * 0.5f,
                Mathf.Max(0f, cw - gap), Mathf.Max(0f, ch - gap));
        }

        /// <summary>図の中の位置 → 点。area の外・格子が空なら false。</summary>
        public static bool TryPointAt(Rect area, int cols, int rows, Vector2 pos, out int row, out int col)
        {
            row = col = -1;
            if (cols <= 0 || rows <= 0 || !area.Contains(pos)) return false;
            col = Mathf.Clamp((int)((pos.x - area.x) / (area.width / cols)), 0, cols - 1);
            int displayRow = Mathf.Clamp((int)((pos.y - area.y) / (area.height / rows)), 0, rows - 1);
            row = DisplayRowToRow(displayRow, rows);
            return true;
        }

        /// <summary>点 (row, col) の角度（度）。1 点しか無い軸は 0°。</summary>
        public static void PointAngles(FacialGridData g, int row, int col, out double yaw, out double pitch)
        {
            FacialCore.PointAngles(g.yawRange, g.pitchRange, g.cols, g.rows, row, col, out yaw, out pitch);
        }

        /// <summary>角度 → 図の中の位置（セルの中心 = 整数の位置）。範囲の外は図の端のセルの中心へ寄せる（Maya の marker_position と同じ）。</summary>
        public static Vector2 AngleToPosition(Rect area, FacialGridData g, double yaw, double pitch)
        {
            FacialGridMarker m = FacialGridMapping.Locate(yaw, pitch, g.yawRange, g.pitchRange, g.cols, g.rows);
            float cw = g.cols > 0 ? area.width / g.cols : 0f;
            float ch = g.rows > 0 ? area.height / g.rows : 0f;
            float x = area.x + (float)(m.ColPosClamped + 0.5) * cw;
            float y = area.y + (float)((g.rows - 1 - m.RowPosClamped) + 0.5) * ch; // 上が +Pitch
            return new Vector2(x, y);
        }

        /// <summary>図の中の位置 → 角度（AngleToPosition の逆。格子の外は端へ収める。Maya の angles_at と同じ）。</summary>
        public static void PositionToAngles(Rect area, FacialGridData g, Vector2 pos, out double yaw, out double pitch)
        {
            float cw = g.cols > 0 ? area.width / g.cols : 1f;
            float ch = g.rows > 0 ? area.height / g.rows : 1f;
            double colPos = (pos.x - area.x) / cw - 0.5;
            double rowPos = (g.rows - 1) - ((pos.y - area.y) / ch - 0.5);
            FacialGridMapping.PosToAngles(colPos, rowPos, g.yawRange, g.pitchRange, g.cols, g.rows, out yaw, out pitch);
        }

        // ---------------------------------------------------------------- カメラの位置（Runner の視点の逆）

        /// <summary>角度 (yaw, pitch) を見るカメラの注視点（格子の中心）。基準ボーンの位置 + 回転で回した centerOffset。</summary>
        public static Vector3 GridCenter(Vector3 basePos, Quaternion baseRot, Vector3 centerOffset)
        {
            return basePos + baseRot * centerOffset;
        }

        /// <summary>視点から格子の中心へ向かう反対の向き = 中心から視点へ向かう単位ベクトル（Unity のワールド）。</summary>
        public static Vector3 ViewDirection(Quaternion baseRot, string forwardAxis, double yaw, double pitch)
        {
            Vec3 axis;
            if (!FacialSpace.TryAxisVector(forwardAxis, out axis)) axis = new Vec3(0, 0, 1);
            Vector3 f = baseRot * new Vector3((float)axis.X, (float)axis.Y, (float)axis.Z);
            Vec3 fc = UnityToCanonical.Direction(new Vec3(f.x, f.y, f.z));
            double forwardYaw = System.Math.Atan2(fc.Y, fc.X) * (180.0 / System.Math.PI);
            Vec3 dc = FacialCore.ComputeViewDirection(forwardYaw, yaw, pitch);
            Vec3 du = CanonicalToUnity.Direction(dc);
            return new Vector3((float)du.X, (float)du.Y, (float)du.Z);
        }

        /// <summary>角度 (yaw, pitch)・距離 distance（m）を見るカメラの位置と向き（中心を向く。上 = ワールドの +Y）。</summary>
        public static void CameraPose(Vector3 basePos, Quaternion baseRot, string forwardAxis, Vector3 centerOffset,
            double yaw, double pitch, float distance, out Vector3 camPos, out Quaternion camRot)
        {
            Vector3 center = GridCenter(basePos, baseRot, centerOffset);
            Vector3 dir = ViewDirection(baseRot, forwardAxis, yaw, pitch);
            camPos = center + dir * distance;
            Vector3 look = -dir;
            camRot = look.sqrMagnitude > 1e-12f ? Quaternion.LookRotation(look, Vector3.up) : Quaternion.identity;
        }

        /// <summary>Runner と同じ式で、視点の位置から (yaw, pitch) を求める（テスト・ビューアのマーカー用）。</summary>
        public static void ViewAngles(Vector3 basePos, Quaternion baseRot, string forwardAxis, Vector3 centerOffset,
            Vector3 viewerPos, out double yaw, out double pitch)
        {
            FacialSpace.ComputeViewAnglesInSpace(UnityToCanonical,
                new Vec3(basePos.x, basePos.y, basePos.z), new Quat(baseRot.x, baseRot.y, baseRot.z, baseRot.w), forwardAxis,
                new Vec3(viewerPos.x, viewerPos.y, viewerPos.z), new Vec3(centerOffset.x, centerOffset.y, centerOffset.z),
                out yaw, out pitch);
        }

        // ---------------------------------------------------------------- 世界の向きで渡す版（左右反転の親でも Runner と同じ角度になる）

        /// <summary>基準ボーンから、格子の中心と前方向（どちらも世界）を求める。左右反転（スケール -1）の親は Runner と同じ MirrorSafeDirections。</summary>
        public static void BoneFrame(Transform bone, FacialGridData g, out Vector3 center, out Vector3 worldForward)
        {
            Vec3 axis;
            bool haveAxis = FacialSpace.TryAxisVector(g.forwardAxis, out axis);
            if (!haveAxis) axis = new Vec3(0, 0, 1);
            if (bone.localToWorldMatrix.determinant < 0f && haveAxis)
            {
                Vector3 wo;
                FacialCorrectionRunner.MirrorSafeDirections(bone, axis, g.centerOffset, out worldForward, out wo);
                center = bone.position + wo;
            }
            else
            {
                worldForward = bone.rotation * new Vector3((float)axis.X, (float)axis.Y, (float)axis.Z);
                center = bone.position + bone.rotation * g.centerOffset;
            }
        }

        /// <summary>中心 center・前方向 worldForward の頭を、角度 (yaw, pitch)・距離 distance で見るカメラの位置と向き。</summary>
        public static void CameraPoseFromFrame(Vector3 center, Vector3 worldForward, double yaw, double pitch, float distance,
            out Vector3 camPos, out Quaternion camRot)
        {
            Vec3 fc = UnityToCanonical.Direction(new Vec3(worldForward.x, worldForward.y, worldForward.z));
            double forwardYaw = System.Math.Atan2(fc.Y, fc.X) * (180.0 / System.Math.PI);
            Vec3 du = CanonicalToUnity.Direction(FacialCore.ComputeViewDirection(forwardYaw, yaw, pitch));
            Vector3 dir = new Vector3((float)du.X, (float)du.Y, (float)du.Z);
            camPos = center + dir * distance;
            Vector3 look = -dir;
            camRot = look.sqrMagnitude > 1e-12f ? Quaternion.LookRotation(look, Vector3.up) : Quaternion.identity;
        }

        /// <summary>視点の位置から (yaw, pitch) を求める（CameraPoseFromFrame の逆。Runner の角度計算と同じ式）。</summary>
        public static void ViewAnglesFromFrame(Vector3 center, Vector3 worldForward, Vector3 viewerPos, out double yaw, out double pitch)
        {
            FacialSpace.ComputeViewAnglesFromWorldVectors(UnityToCanonical, new Vec3(center.x, center.y, center.z), new Vec3(0, 0, 0),
                new Vec3(worldForward.x, worldForward.y, worldForward.z), new Vec3(viewerPos.x, viewerPos.y, viewerPos.z), out yaw, out pitch);
        }

        /// <summary>今のカメラ位置から中心までの距離（近すぎて意味がないときは fallback）。ドラッグ・クリックでこの距離を保つ。</summary>
        public static float KeepDistance(Vector3 camPos, Vector3 center, float fallback)
        {
            float d = Vector3.Distance(camPos, center);
            return d < 1e-3f ? fallback : d;
        }

        // ---------------------------------------------------------------- 表示用の文字（Maya の fmt_angle・カメラのラベルと同じ）

        /// <summary>軸の見出し用の角度（+22.5° / 0° / -45°。ハイフンは半角マイナス）。</summary>
        public static string FormatAngle(double v)
        {
            if (System.Math.Abs(v) < 1e-6) return "0°";
            string t = v.ToString("+0.0;-0.0", System.Globalization.CultureInfo.InvariantCulture);
            if (t.EndsWith(".0")) t = t.Substring(0, t.Length - 2);
            return t + "°";
        }

        /// <summary>右上の読み出し。「カメラ: Scene ビュー  Yaw 37.5° / Pitch -10.0°」（範囲外なら末尾に「（範囲外）」）。</summary>
        public static string FormatCameraReadout(string cameraName, double yaw, double pitch, bool outOfRange)
        {
            var ci = System.Globalization.CultureInfo.InvariantCulture;
            return "カメラ: " + (string.IsNullOrEmpty(cameraName) ? "" : cameraName + "  ")
                + "Yaw " + yaw.ToString("0.0", ci) + "° / Pitch " + pitch.ToString("0.0", ci) + "°" + (outOfRange ? "（範囲外）" : "");
        }

        /// <summary>Yaw を -180〜180 に畳む（ターンテーブルの積算用）。</summary>
        public static double WrapYaw(double yaw) { return FacialCore.NormalizeAxis(yaw); }
    }
}
