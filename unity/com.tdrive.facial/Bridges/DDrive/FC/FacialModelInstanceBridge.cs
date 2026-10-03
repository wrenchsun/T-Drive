// D-Drive ブリッジ: モデルの Prefab に付けると、D-Drive のスポーン / 返却の通知（FC-12 IModelInstanceListener）に Runner を合わせる（FT-5）。
//  - OnModelSpawned: スロットの Material 適用・DefaultAnimation 開始のあと。Runner のキャッシュ（FC_ シェイプの場所・対象メッシュ・基準ボーン）を作り直し、
//    前回の計算の状態（スムージング）を捨てる → 最初のフレームは目標の重みへスナップする
//  - OnModelReturning: プールへ戻す直前（D-Drive の FC-2 の「ブレンドシェイプを既定の重みへ戻す」より前）。Runner が書いた FC_ とカット補正を戻し、
//    Runner の「前回書いた値」の記録を空にする → そのあとの D-Drive の復元・Runner の OnDisable が重なっても、記録が古くて余計な 0 を書くことが起きない
// D-Drive が通知を集めるのは Prefab を最初にスポーンするとき 1 回だけ。実行時に後から足したコンポーネントは対象外なので、Prefab に付けておく。
// Runner 本体は D-Drive を参照しない（この部品が橋渡しをする）。D-Drive の無いプロジェクトでは不要。
using DDrive.Runtime.Model;
using UnityEngine;

namespace TDrive.Facial.DDrive
{
    [DisallowMultipleComponent]
    [RequireComponent(typeof(FacialCorrectionRunner))]
    [AddComponentMenu("T-Drive/Facial/Facial Model Instance Bridge")]
    public sealed class FacialModelInstanceBridge : MonoBehaviour, IModelInstanceListener
    {
        FacialCorrectionRunner _runner;

        FacialCorrectionRunner Runner
        {
            get
            {
                if (_runner == null) _runner = GetComponent<FacialCorrectionRunner>();
                return _runner;
            }
        }

        /// <summary>スポーン後: キャッシュを作り直し、計算の状態を捨てる。</summary>
        public void OnModelSpawned(in ModelInstanceContext context)
        {
            FacialCorrectionRunner r = Runner;
            if (r == null) return;
            r.RebuildCaches(); // 中で ResetWeights も呼ぶ（スムージングの状態を捨てる = 最初のフレームはスナップ）
        }

        /// <summary>返却の直前: FC_ とカット補正を戻し、記録を空にする（D-Drive の重みの復元より前）。</summary>
        public void OnModelReturning(in ModelInstanceContext context)
        {
            FacialCorrectionRunner r = Runner;
            if (r == null) return;
            r.ResetWeights();
        }
    }
}
