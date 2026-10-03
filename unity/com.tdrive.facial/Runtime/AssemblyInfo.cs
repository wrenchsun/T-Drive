// テストアセンブリへ internal を見せる（再生中と同じ ResetWeights の動きをエディタのテストで確かめるため）。
using System.Runtime.CompilerServices;

[assembly: InternalsVisibleTo("TDrive.Facial.Tests")]
[assembly: InternalsVisibleTo("TDrive.Facial.Timeline.Tests")]
[assembly: InternalsVisibleTo("TDrive.Facial.DDrive.Tests")]
[assembly: InternalsVisibleTo("TDrive.Facial.DDrive.FC.Tests")]
