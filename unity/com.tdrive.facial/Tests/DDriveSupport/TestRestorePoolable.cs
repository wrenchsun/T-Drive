// テスト用: D-Drive の ModelInstancePoolable（internal）の動きの写し。Capture で全シェイプの重みを控え、プールへ返すとき（IPoolable.OnReturn）に変化のある重みだけ控えた値へ戻す（FC-2）。
using DDrive.Foundation.Pool;
using UnityEngine;

namespace TDrive.Facial.Tests.DDrive
{
    public sealed class TestRestorePoolable : MonoBehaviour, IPoolable
    {
        SkinnedMeshRenderer[] _renderers;
        float[][] _captured;

        public void Capture()
        {
            _renderers = GetComponentsInChildren<SkinnedMeshRenderer>(true);
            _captured = new float[_renderers.Length][];
            for (int i = 0; i < _renderers.Length; i++)
            {
                int n = _renderers[i].sharedMesh.blendShapeCount;
                _captured[i] = new float[n];
                for (int j = 0; j < n; j++) _captured[i][j] = _renderers[i].GetBlendShapeWeight(j);
            }
        }

        public float CapturedWeight(int renderer, int index) { return _captured[renderer][index]; }

        public void OnReturn()
        {
            for (int i = 0; i < _renderers.Length; i++)
                for (int j = 0; j < _captured[i].Length; j++)
                    if (_renderers[i].GetBlendShapeWeight(j) != _captured[i][j]) _renderers[i].SetBlendShapeWeight(j, _captured[i][j]);
        }
    }
}
