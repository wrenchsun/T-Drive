// D-Drive 1.4.0 以降: FacialCameraExecutionOrderExemption（実行順の検査からの除外の宣言）の中身と、D-Drive の発見規則を満たすかのテスト。
// 実際に Validation に Info が出るか（TypeCache での発見）は D-Drive 側の検査で、人の確認項目（docs/18 G2-19）。
using System.Linq;
using DDrive.Editor.Validation;
using NUnit.Framework;
using TDrive.Facial.DDrive.Editor;

namespace TDrive.Facial.Tests.DDrive
{
    public class FacialCameraExecutionOrderExemptionTests
    {
        [Test]
        public void GetExemptions_declares_runner_with_reason()
        {
            var list = new FacialCameraExecutionOrderExemption().GetExemptions().ToList();
            Assert.AreEqual(1, list.Count);
            Assert.AreEqual(typeof(FacialCorrectionRunner), list[0].Type);
            Assert.IsFalse(string.IsNullOrWhiteSpace(list[0].Reason), "理由は必須（空だと D-Drive は無効扱い）");
        }

        [Test]
        public void Type_satisfies_discovery_rules()
        {
            var t = typeof(FacialCameraExecutionOrderExemption);
            Assert.IsTrue(typeof(ICameraExecutionOrderExemptionProvider).IsAssignableFrom(t));
            Assert.IsTrue(t.IsPublic);
            Assert.IsFalse(t.IsAbstract);
            Assert.IsFalse(t.IsGenericTypeDefinition);
            Assert.IsNotNull(t.GetConstructor(System.Type.EmptyTypes), "public な引数なしコンストラクタ");
        }
    }
}
