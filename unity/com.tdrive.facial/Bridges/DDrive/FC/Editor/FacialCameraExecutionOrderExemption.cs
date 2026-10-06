// D-Drive ブリッジ（Editor / FC = D-Drive 1.4.0 以降）: FacialCorrectionRunner を D-Drive の「実行順の検査」から除外すると宣言する（docs/16 §6 / D-Drive docs/42 §5.14 E-23・契約 G-1）。
//  - D-Drive の Validation は、実行順 1000 以上の LateUpdate を持つ D-Drive 以外のスクリプトに「Cutscene のカメラが効かない」Warning を出す。Runner は実行順 10000 だが、カメラを読むだけで書かない
//  - 除外は理由が必須（空だと無効扱いで Warning）。Validation の Info に「実行順の検査から除外」として理由と宣言元が出る
//  - TypeCache で自動発見される（public・非 abstract・非ジェネリック・public な引数なしコンストラクタ）。登録コードは不要。Runner の実行順（10000）は変えない（FC-3 の視点が 1000 より後に要る）
using System.Collections.Generic;
using DDrive.Editor.Validation;

namespace TDrive.Facial.DDrive.Editor
{
    public sealed class FacialCameraExecutionOrderExemption : ICameraExecutionOrderExemptionProvider
    {
        /// <summary>除外の理由（D-Drive の Validation に表示される）。</summary>
        public const string Reason =
            "カメラの位置・向きを読んで顔のブレンドシェイプを補正するだけで、カメラには書かない。" +
            "Cutscene のカメラ適用（実行順 1000）より後に動く必要がある（docs/16 §6）";

        public IEnumerable<CameraExecutionOrderExemption> GetExemptions()
        {
            yield return new CameraExecutionOrderExemption(typeof(FacialCorrectionRunner), Reason);
        }
    }
}
