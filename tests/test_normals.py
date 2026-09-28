import math
import random

import pytest

from tdrive_toon import normals


def close(a, b, tol=1e-6):
    return all(math.isclose(x, y, abs_tol=tol) for x, y in zip(a, b))


def random_unit(rng):
    return normals.normalize([rng.uniform(-1, 1) for _ in range(3)])


def test_octahedral_roundtrip_all_directions():
    rng = random.Random(1)
    for _ in range(2000):
        n = random_unit(rng)
        assert close(normals.octahedral_decode(normals.octahedral_encode(n)), n, 1e-6)


@pytest.mark.parametrize("n", [(0, 0, 1), (0, 0, -1), (1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0)])
def test_octahedral_axes(n):
    e = normals.octahedral_encode(n)
    assert all(-1.0 <= c <= 1.0 for c in e)
    assert close(normals.octahedral_decode(e), n)


def test_tangent_space_roundtrip_with_mirrored_uv():
    rng = random.Random(2)
    for _ in range(500):
        n = random_unit(rng)
        t = random_unit(rng)
        mirrored = rng.random() < 0.5
        b = normals.cross(n, t)
        if mirrored:
            b = tuple(-c for c in b)
        v = random_unit(rng)
        ts = normals.to_tangent_space(v, n, t, b)
        assert close(normals.from_tangent_space(ts, n, t, b), v, 1e-6)
        assert normals.tangent_frame(n, t, b)[2] == (-1.0 if mirrored else 1.0)


def test_smooth_by_position_merges_split_vertices():
    # 立方体の角: 同じ位置に 3 頂点（ハードエッジで分割）→ 3 面の法線の平均になる
    positions = [(1, 1, 1), (1, 1, 1), (1, 1, 1), (5, 5, 5)]
    face_normals = [(0, (1, 0, 0)), (1, (0, 1, 0)), (2, (0, 0, 1)), (3, (0, 1, 0))]
    out = normals.smooth_by_position(positions, face_normals)
    k = 1 / math.sqrt(3)
    for vid in (0, 1, 2):
        assert close(out[vid], (k, k, k))
    assert close(out[3], (0, 1, 0))
