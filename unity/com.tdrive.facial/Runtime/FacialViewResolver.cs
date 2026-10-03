// 視点の最後の手段の差し込み口。Runtime は D-Drive を参照しないので、D-Drive のブリッジ（ViewCamera.TryGetCurrent）がここへ設定する（FT-6 / D-Drive FC-3）。
// Runner の視点の解決の順: 上書きの視点 → Runner の viewerOverride → EvaluateNow の引数（編集時は editViewer の Scene ビュー / メインカメラ）
//   → 手動の角度（角度の計算には視点を使わない）→ この Fallback → Camera.main。Fallback が null か false を返したときだけ Camera.main になる。
// 編集時の Auto: Fallback が視点を返すならそれ（D-Drive の今の視点）、返さなければメインカメラ → Scene ビュー。SceneView を選んでいれば Fallback より先。
// 実行順: Fallback はカットのカメラ姿勢が確定した後（D-Drive の DDriveCutsceneCameraApplier = 1000 より後）に呼ぶ必要がある。Runner は 10000 の LateUpdate。
using System;
using UnityEngine;

namespace TDrive.Facial
{
    public static class FacialViewResolver
    {
        /// <summary>subject（補正を掛けるキャラクター）から見た今の視点を返す。分かれば true。割り当てなしで呼べること。</summary>
        public delegate bool Provider(Transform subject, out Vector3 position, out Quaternion rotation, out float verticalFovDeg);

        /// <summary>視点の最後の手段。null = 使わない（従来どおり Camera.main）。</summary>
        public static Provider Fallback;

        [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.SubsystemRegistration)]
        static void ResetStatics() { Fallback = null; }

        /// <summary>Fallback を呼ぶ。例外は握りつぶして false（視点の取得で補正を止めない）。</summary>
        public static bool TryResolve(Transform subject, out Vector3 position, out Quaternion rotation, out float verticalFovDeg)
        {
            Provider p = Fallback;
            if (p != null)
            {
                try { if (p(subject, out position, out rotation, out verticalFovDeg)) return true; }
                catch (Exception e) { Debug.LogException(e); }
            }
            position = default(Vector3); rotation = Quaternion.identity; verticalFovDeg = 0f;
            return false;
        }
    }
}
