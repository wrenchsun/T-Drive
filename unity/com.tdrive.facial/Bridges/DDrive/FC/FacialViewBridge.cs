// D-Drive ブリッジ（FC-3 / D-Drive 1.4.0 以降）: 視点の最後の手段 FacialViewResolver.Fallback に ViewCamera.TryGetCurrent を設定する（FT-6）。
//  - 分割画面などで D-Drive が視点を差し替えていても、補正がその視点に従う。Runner の順: クリップ / コンポーネントの視点 → これ → Camera.main
//  - 実行順: ViewCamera はそのフレームのカット姿勢が欲しければ、LateUpdate で DDriveCutsceneCameraApplier（1000）より後に呼ぶ。Runner は 10000 なので満たす（変えない）
//  - 登録は再生の開始（SubsystemRegistration でリセットされたあと）とエディタの読み込み後。自分が入れたものだけを外す（他が差し替えていたら触らない）
//  - デリゲートは 1 度だけ作って使い回す（毎フレームの割り当てなし）
using DDrive.Runtime.Viewing;
using UnityEngine;

namespace TDrive.Facial.DDrive
{
    public static class FacialViewBridge
    {
        static readonly FacialViewResolver.Provider Cached = Provide;

        /// <summary>登録されている Provider が、この橋渡しのものか。</summary>
        public static bool IsInstalled { get { return FacialViewResolver.Fallback == Cached; } }

        /// <summary>Fallback に設定する（何度呼んでもよい）。</summary>
        public static void Install() { FacialViewResolver.Fallback = Cached; }

        /// <summary>この橋渡しのものなら外す。</summary>
        public static void Uninstall()
        {
            if (FacialViewResolver.Fallback == Cached) FacialViewResolver.Fallback = null;
        }

        static bool Provide(Transform subject, out Vector3 position, out Quaternion rotation, out float verticalFovDeg)
        {
            ViewPose pose;
            if (ViewCamera.TryGetCurrent(subject, out pose))
            {
                position = pose.Position; rotation = pose.Rotation; verticalFovDeg = pose.VerticalFovDegrees;
                return true;
            }
            position = default(Vector3); rotation = Quaternion.identity; verticalFovDeg = 0f;
            return false;
        }

        [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterAssembliesLoaded)] // SubsystemRegistration（リセット）のあと
        static void OnRuntimeLoad() { Install(); }

#if UNITY_EDITOR
        // エディタ（編集時の Timeline プレビュー）。再コンパイルの前に外し、読み込み後に入れ直す
        [UnityEditor.InitializeOnLoadMethod]
        static void OnEditorLoad()
        {
            Install();
            UnityEditor.AssemblyReloadEvents.beforeAssemblyReload -= Uninstall;
            UnityEditor.AssemblyReloadEvents.beforeAssemblyReload += Uninstall;
        }
#endif
    }
}
