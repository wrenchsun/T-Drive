"""LOD1 以降のメッシュ（target.lodMeshes。F5-7）: データ・読み書き・検証（Maya なし）。"""

import pytest

from tdrive_facial.core import fcpose_io
from tdrive_facial.core import model as m
from tdrive_facial.core import validate as V


def make_doc(lods=(), mesh="face", extras=()):
    doc = m.Document()
    doc.asset = "a"
    doc.target = m.Target(mesh=mesh, extra_meshes=list(extras), lod_meshes=[m.LodMesh(n, lod) for n, lod in lods])
    doc.layers[0].points[(0, 0)] = m.GridPoint(0, 0, True, m.SourcePose(curves={"bs.mouth": 0.5, "bs.eye": 0.2, "bs.brow": 1.0}))
    return doc


def info(exists=True, resolved="", targets=(), fc=()):
    return V.MeshInfo(exists=exists, resolved=resolved, targets=list(targets), fc_targets=list(fc))


def scene_for(doc, infos, face_fc=()):
    s = V.SceneInfo(curves=["bs.mouth", "bs.eye", "bs.brow"], targets=list(face_fc))
    s.mesh_infos = infos
    return s


def issues(doc, sc, code=None):
    out = V.validate(doc, sc)
    return [i for i in out if code is None or i.code == code]


# --- データ ---


def test_all_meshes_order_and_dedupe():
    doc = make_doc([("lod1", 1), ("face", 2), ("ex", 3), ("lod2", 2)], extras=["ex", "ex2"])
    assert doc.target.all_meshes() == ["face", "ex", "ex2", "lod1", "lod2"]
    assert m.Target().all_meshes() == []


def test_roundtrip_unknown_keys_and_absent():
    doc = make_doc([("lod1", 1), ("lod2", 3)])
    doc.target.lod_meshes[1].extra = {"note": "x"}
    d = fcpose_io.to_dict(doc)
    assert d["target"]["lodMeshes"] == [{"mesh": "lod1", "lod": 1}, {"mesh": "lod2", "lod": 3, "note": "x"}]
    back = fcpose_io.from_dict(d)
    assert [(x.mesh, x.lod, x.extra) for x in back.target.lod_meshes] == [("lod1", 1, {}), ("lod2", 3, {"note": "x"})]
    assert fcpose_io.to_dict(back) == d
    none = fcpose_io.to_dict(make_doc())
    assert "lodMeshes" not in none["target"]  # 無いときは出さない（既存のファイルを変えない）
    assert fcpose_io.from_dict(none).target.lod_meshes == []


def test_read_skips_broken_entries_and_defaults_lod():
    d = fcpose_io.to_dict(make_doc())
    d["target"]["lodMeshes"] = [{"mesh": "a"}, {"lod": 2}, "x", {"mesh": 5}, {"mesh": "b", "lod": 2.7}]
    got = fcpose_io.from_dict(d).target.lod_meshes
    assert [(x.mesh, x.lod) for x in got] == [("a", 1), ("b", 2)]  # lod が無いときは 1・小数は切り捨て


def test_unknown_target_keys_still_kept():
    d = fcpose_io.to_dict(make_doc([("l", 1)]))
    d["target"]["future"] = {"a": 1}
    assert fcpose_io.to_dict(fcpose_io.from_dict(d))["target"]["future"] == {"a": 1}


def test_schema():
    import json
    from pathlib import Path

    from test_fcpose_io import mini_validate

    schema = json.loads((Path(__file__).parents[2] / "schema" / "fcpose.schema.json").read_text(encoding="utf-8"))
    d = fcpose_io.to_dict(make_doc([("l", 1)]))
    assert not mini_validate(d, schema, schema)
    d["target"]["lodMeshes"][0]["lod"] = 0
    assert mini_validate(d, schema, schema)  # lod は 1 以上


# --- 検証 ---


def test_no_lod_no_checks():
    doc = make_doc()
    assert not [i for i in V.validate(doc, V.SceneInfo()) if i.code.startswith("lod_")]


def test_lod_number_invalid():
    doc = make_doc([("l1", 0), ("l2", -1), ("l3", 1)])
    got = issues(doc, V.SceneInfo(), "lod_number_invalid")
    assert sorted(i.name for i in got) == ["l1", "l2"] and all(i.severity == V.SEVERITY_ERROR for i in got)


def test_duplicate_and_also_listed_without_scene():
    doc = make_doc([("l1", 1), ("l1", 2), ("face", 1), ("ex", 2)], extras=["ex"])
    sc = V.SceneInfo()
    assert [i.name for i in issues(doc, sc, "lod_mesh_duplicate")] == ["l1"]
    assert sorted(i.name for i in issues(doc, sc, "lod_mesh_also_listed")) == ["ex", "face"]


def test_duplicate_by_resolved_name():
    # 書き方が違っても同じメッシュなら重複・顔と同じなら重なり
    doc = make_doc([("l1", 1), ("|grp|l1", 2), ("faceAlias", 1)])
    infos = {"face": info(resolved="|face"), "l1": info(resolved="|grp|l1"), "|grp|l1": info(resolved="|grp|l1"), "faceAlias": info(resolved="|face")}
    sc = scene_for(doc, infos)
    assert [i.name for i in issues(doc, sc, "lod_mesh_duplicate")] == ["|grp|l1"]
    assert [i.name for i in issues(doc, sc, "lod_mesh_also_listed")] == ["faceAlias"]


def test_missing_mesh_is_error_and_skips_target_checks():
    doc = make_doc([("gone", 1)])
    sc = scene_for(doc, {"face": info(resolved="|face"), "gone": info(exists=False)})
    got = issues(doc, sc, "lod_mesh_missing")
    assert len(got) == 1 and got[0].severity == V.SEVERITY_ERROR and got[0].name == "gone"
    assert not issues(doc, sc, "lod_mesh_no_targets")


def test_counterpart_warning_lists_count():
    doc = make_doc([("l1", 1)])
    sc = scene_for(doc, {"face": info(resolved="|face"), "l1": info(resolved="|l1", targets=["mouth", "eye"])})
    got = issues(doc, sc, "lod_mesh_no_targets")
    assert len(got) == 1 and got[0].severity == V.SEVERITY_WARNING
    assert "3 個のうち 1 個" in got[0].message and got[0].candidates == ("brow",)
    full = scene_for(doc, {"face": info(resolved="|face"), "l1": info(resolved="|l1", targets=["mouth", "eye", "brow", "extra"])})
    assert not issues(doc, full, "lod_mesh_no_targets")


def test_counterpart_ignores_curves_missing_from_model():
    # モデル（顔）に無いシェイプは別の検査で出る。LOD の対応の数には数えない
    doc = make_doc([("l1", 1)])
    sc = V.SceneInfo(curves=["bs.mouth"])  # eye / brow は顔にも無い
    sc.mesh_infos = {"face": info(resolved="|face"), "l1": info(resolved="|l1", targets=["mouth"])}
    assert not issues(doc, sc, "lod_mesh_no_targets")


def test_unbaked_lod_warns_when_face_has_fc():
    doc = make_doc([("l1", 1)])
    face_fc = ["FC_a_Neutral_R0_C0", "FC_a_Neutral_R0_C1", "fcs_x"]
    infos = {"face": info(resolved="|face"), "l1": info(resolved="|l1", targets=["mouth", "eye", "brow"], fc=["FC_a_Neutral_R0_C0"])}
    got = issues(doc, scene_for(doc, infos, face_fc), "lod_mesh_unbaked")
    assert len(got) == 1 and "1 本足りません" in got[0].message
    infos["l1"] = info(resolved="|l1", targets=["mouth", "eye", "brow"], fc=face_fc[:2])
    assert not issues(doc, scene_for(doc, infos, face_fc), "lod_mesh_unbaked")
    assert not issues(doc, scene_for(doc, infos, []), "lod_mesh_unbaked")  # 顔にも無ければ（まだ焼いていない）言わない
