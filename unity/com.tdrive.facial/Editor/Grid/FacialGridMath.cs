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

        /// <summary>角度 → 図の中の位置（セルの中心を点の角度とする）。範囲の外は図の端へ寄せる。</summary>
        public static Vector2 AngleToPosition(Rect area, FacialGridData g, double yaw, double pitch)
        {
            double fu = FractionOf(yaw, g.yawRange, g.cols);
            double fv = FractionOf(pitch, g.pitchRange, g.rows);
            float cw = g.cols > 0 ? area.width / g.cols : 0f;
            float ch = g.rows > 0 ? area.height / g.rows : 0f;
            float x = area.x + (float)(fu + 0.5) * cw;
            float y = area.y + (float)((g.rows - 1 - fv) + 0.5) * ch; // 上が +Pitch
            return new Vector2(Mathf.Clamp(x, area.xMin, area.xMax), Mathf.Clamp(y, area.yMin, area.yMax));
        }

        // 角度 → 連続した点の番号（0〜n-1）。範囲の外は端の少し外まで許し、1 点だけの軸は 0
        static double FractionOf(double angle, double range, int n)
        {
            if (n <= 1 || range <= 1e-9) return 0.0;
            double f = (angle / range + 1.0) * 0.5 * (n - 1);
            return System.Math.Max(-0.5, System.Math.Min(n - 1 + 0.5, f));
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

        /// <summary>Yaw を -180〜180 に畳む（ターンテーブルの積算用）。</summary>
        public static double WrapYaw(double yaw) { return FacialCore.NormalizeAxis(yaw); }
    }
}
