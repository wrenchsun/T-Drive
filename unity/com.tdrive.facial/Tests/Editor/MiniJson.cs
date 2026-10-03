// テストから MiniJson を呼ぶための薄い入口（実体は Runtime/Core/MiniJson.cs の 1 つだけ）。
namespace TDrive.Facial.Tests
{
    public static class MiniJson
    {
        public static object Parse(string text) { return TDrive.Facial.Core.MiniJson.Parse(text); }
    }
}
