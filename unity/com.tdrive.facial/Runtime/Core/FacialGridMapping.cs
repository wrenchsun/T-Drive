// 格子の「連続位置 ⇔ 角度」の計算（Python 版 core/presenters.py の grid_pos_to_angles / GridPresenter.locate の写し）。
// UnityEngine 非依存。列・行の連続値は 0..n-1（整数 = 点の中心）。行が大きいほど +Pitch、列が大きいほど +Yaw。
using System;

namespace TDrive.Facial.Core
{
    /// <summary>カメラの角度の格子上の位置（赤い点）。</summary>
    public struct FacialGridMarker
    {
        public double Yaw, Pitch;
        public double ColPos, RowPos;               // 連続値（範囲外は外側へ延長）
        public double ColPosClamped, RowPosClamped; // 格子の内側へ収めた位置（描画用）
        public bool Clamped;                        // yawRange / pitchRange の外
        public int NearestRow, NearestCol;
    }

    public static class FacialGridMapping
    {
        /// <summary>格子の連続位置 → (Yaw, Pitch)。0..n-1 へ収める（端の外は端）。1 列 / 1 行のときその軸は 0。</summary>
        public static void PosToAngles(double colPos, double rowPos, double yawRange, double pitchRange, int cols, int rows,
            out double yaw, out double pitch)
        {
            yaw = AxisToAngle(colPos, yawRange, cols);
            pitch = AxisToAngle(rowPos, pitchRange, rows);
        }

        static double AxisToAngle(double pos, double range, int n)
        {
            if (n <= 1) return 0.0;
            pos = Math.Min(Math.Max(pos, 0.0), n - 1);
            return (pos / (n - 1) * 2.0 - 1.0) * range;
        }

        /// <summary>角度 → 格子の中の位置（PosToAngles の逆）。範囲外は連続値を外へ延長し、描画用に収めた値も返す。</summary>
        public static FacialGridMarker Locate(double yaw, double pitch, double yawRange, double pitchRange, int cols, int rows)
        {
            double colPos, colC, rowPos, rowC;
            AngleToAxis(yaw, yawRange, cols, out colPos, out colC);
            AngleToAxis(pitch, pitchRange, rows, out rowPos, out rowC);
            return new FacialGridMarker
            {
                Yaw = yaw, Pitch = pitch,
                ColPos = colPos, RowPos = rowPos,
                ColPosClamped = colC, RowPosClamped = rowC,
                Clamped = Math.Abs(yaw) > yawRange || Math.Abs(pitch) > pitchRange,
                NearestRow = (int)Math.Floor(rowC + 0.5),
                NearestCol = (int)Math.Floor(colC + 0.5),
            };
        }

        static void AngleToAxis(double angle, double range, int n, out double pos, out double clamped)
        {
            if (n <= 1) { pos = 0.0; clamped = 0.0; return; }
            pos = (angle / Math.Max(range, FacialCore.KindaSmallNumber) + 1.0) * 0.5 * (n - 1);
            clamped = Math.Min(Math.Max(pos, 0.0), n - 1);
        }
    }

    /// <summary>
    /// 赤い点のドラッグの状態機械（IMGUI のイベントから切り離した純粋なクラス。Maya の GridCanvas と同じ規則）。
    /// 赤い点を押す → 保留（動かすまでは普通のクリック）/ しきい値（4 px）を超えたらドラッグ / Shift + 押す = どこでも即ドラッグ。
    /// </summary>
    public sealed class FacialGridDragState
    {
        public const float HitRadius = 10f;  // 赤い点をつかめる半径（px）
        public const float Threshold = 4f;   // 押してからこれ以上動いたらドラッグ

        public enum Phase { Idle, Pending, Dragging }

        [Flags]
        public enum Output
        {
            None = 0,
            BeginDrag = 1,     // ドラッグを始める（カメラ操作の準備）
            MoveTo = 2,        // 今の位置の角度へカメラを動かす
            EndDrag = 4,       // ドラッグを終える（最後の位置を必ず当てる）
            ClickAtPress = 8,  // 押した位置のマスを普通にクリックしたことにする
        }

        public Phase State { get; private set; }
        public float PressX { get; private set; }
        public float PressY { get; private set; }
        public bool IsDragging { get { return State == Phase.Dragging; } }
        public bool IsActive { get { return State != Phase.Idle; } }

        /// <summary>赤い点の上か（点の中心 mx, my から HitRadius 以内）。</summary>
        public static bool OnMarker(float x, float y, float mx, float my)
        {
            float dx = x - mx, dy = y - my;
            return dx * dx + dy * dy <= HitRadius * HitRadius;
        }

        /// <summary>左ボタンを押した。onMarker = 赤い点の上、onCell = マスの上。</summary>
        public Output Press(float x, float y, bool shift, bool onMarker, bool onCell)
        {
            PressX = x; PressY = y;
            if (shift) { State = Phase.Dragging; return Output.BeginDrag | Output.MoveTo; }
            if (onMarker) { State = Phase.Pending; return Output.None; }
            State = Phase.Idle;
            return onCell ? Output.ClickAtPress : Output.None;
        }

        public Output Move(float x, float y)
        {
            if (State == Phase.Pending)
            {
                float dx = x - PressX, dy = y - PressY;
                if (Math.Sqrt(dx * dx + dy * dy) > Threshold) { State = Phase.Dragging; return Output.BeginDrag | Output.MoveTo; }
                return Output.None;
            }
            return State == Phase.Dragging ? Output.MoveTo : Output.None;
        }

        public Output Release(float x, float y)
        {
            Phase was = State;
            State = Phase.Idle;
            if (was == Phase.Dragging) return Output.MoveTo | Output.EndDrag;
            if (was == Phase.Pending) return Output.ClickAtPress; // 動かさずに離した: 普通のクリック
            return Output.None;
        }

        /// <summary>カメラを動かせずに中止された、ウィンドウを閉じた、など。クリックにはしない。</summary>
        public void Cancel() { State = Phase.Idle; }
    }

    /// <summary>ドラッグ中のカメラ更新の間引き（時計を外から渡す純粋なクラス）。最後の位置は離したときに必ず当てる。</summary>
    public sealed class FacialGridThrottle
    {
        readonly double _interval;
        double _last = double.NegativeInfinity;
        bool _has;
        double _yaw, _pitch;

        public FacialGridThrottle(double intervalSeconds) { _interval = intervalSeconds; }

        public bool HasPending { get { return _has; } }

        public void Reset() { _has = false; _last = double.NegativeInfinity; }

        /// <summary>新しい位置を受ける。間隔が空いていれば true を返し、すぐ当てる値を渡す。</summary>
        public bool Offer(double now, double yaw, double pitch, out double outYaw, out double outPitch)
        {
            _yaw = yaw; _pitch = pitch; _has = true;
            return TryTake(now, out outYaw, out outPitch);
        }

        /// <summary>保留中の位置を、間隔が空いていれば取り出す（エディタの更新から呼ぶ）。</summary>
        public bool TryTake(double now, out double yaw, out double pitch)
        {
            yaw = _yaw; pitch = _pitch;
            if (!_has || now - _last < _interval) return false;
            _has = false; _last = now;
            return true;
        }

        /// <summary>間隔に関係なく、保留中の最後の位置を取り出す（離したとき）。</summary>
        public bool Flush(double now, out double yaw, out double pitch)
        {
            yaw = _yaw; pitch = _pitch;
            if (!_has) return false;
            _has = false; _last = now;
            return true;
        }
    }
}
