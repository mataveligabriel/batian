"""Exportar/importar mapas: o arquivo não leva segredo, os equipamentos são casados pelo IP e o desenho sobrevive."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import availmap  # noqa: E402
import mapxfer as X  # noqa: E402

DEVS = {
    "a": {"id": "a", "name": "PE-1", "host": "10.0.0.1", "port": 22, "device_type": "huawei", "tags": ["HELP"],
          "password": "segredo", "username": "admin", "snmp_community": "comunidade", "agent_id": "jump"},
    "b": {"id": "b", "name": "SW-2", "host": "10.0.0.2", "port": 2222, "device_type": "datacom", "tags": []},
}


def test_topology_roundtrip():
    m = {"name": "Core", "description": "x", "geo": {"view": "geo"},
         "nodes": [{"id": "n1", "kind": "device", "device_id": "a", "x": 1, "y": 2, "lat": -20.1, "lon": -41.2, "label": ""},
                   {"id": "n2", "kind": "device", "device_id": "b", "x": 5, "y": 6, "label": ""},
                   {"id": "n3", "kind": "cloud", "label": "Internet", "x": 9, "y": 9}],
         "links": [{"id": "l1", "from": "n1", "to": "n2", "from_if": {"index": 3, "name": "XGE0/0/1"}, "points": [{"x": 3, "y": 4}]}]}
    out = X.export_topology(m, DEVS)
    txt = json.dumps(out)
    assert "segredo" not in txt and "admin" not in txt and "comunidade" not in txt and "jump" not in txt
    data = X.check(json.loads(txt))
    match = X.match_devices(data["devices"], [{"id": "Z1", "host": "10.0.0.1", "port": 22}])
    assert list(match.values()).count(None) == 1
    rows = X.new_device_rows(data["devices"], match, tag="BTP")
    assert rows == [{"ref": rows[0]["ref"], "name": "SW-2", "host": "10.0.0.2", "port": 2222, "protocol": "ssh",
                     "device_type": "datacom", "tags": ["BTP"], "description": ""}]
    b = X.build_topology(data, match)
    kinds = {n["id"]: (n["kind"], n["device_id"], n["label"]) for n in b["nodes"]}
    assert kinds["n1"] == ("device", "Z1", "") and kinds["n2"] == ("cloud", None, "SW-2") and kinds["n3"][0] == "cloud"
    assert b["links"][0]["from_if"]["name"] == "XGE0/0/1" and b["geo"] == {"view": "geo"}


def test_avail_roundtrip():
    maps = {
        "R": {"id": "R", "name": "Rede", "nodes": [{"id": "s1", "kind": "submap", "map_id": "P", "x": 0, "y": 0, "label": ""},
                                                   {"id": "s2", "kind": "submap", "map_id": "OUT", "x": 0, "y": 0, "label": ""}], "links": []},
        "P": {"id": "P", "name": "POP", "style": {"preset": "ocean"},
              "nodes": [{"id": "d1", "kind": "device", "device_id": "a", "x": 1, "y": 1, "label": ""},
                        {"id": "d2", "kind": "device", "device_id": "b", "x": 2, "y": 2, "label": ""},
                        {"id": "back", "kind": "submap", "map_id": "R", "x": 3, "y": 3, "label": ""}],
              "links": [{"id": "l", "from": "d1", "to": "d2", "snmp": {"device_id": "a", "if_index": 7, "if_name": "ge1"}, "points": []}]},
        "OUT": {"id": "OUT", "name": "Outro", "nodes": [], "links": []},
    }
    out = X.export_avail("R", maps, DEVS)
    assert [m["name"] for m in out["maps"]] == ["Rede", "POP", "Outro"]
    only = X.export_avail("R", maps, DEVS, submaps=False)
    assert len(only["maps"]) == 1 and only["maps"][0]["nodes"] == []          # submapas fora do arquivo somem
    data = X.check(json.loads(json.dumps(out)))
    match = X.match_devices(data["devices"], [{"id": "A2", "host": "10.0.0.1", "port": 22}])
    built = X.build_avail(data, match)
    ids = {b["id"] for b in built.values()}
    pop = next(b for b in built.values() if b["body"]["name"] == "POP")
    doc = availmap.clean_map(pop["body"], {"A2"}, ids, pop["id"])
    kinds = sorted((n["kind"], n.get("device_id") or n.get("map_id") or n["label"]) for n in doc["nodes"])
    root_id = built[data["root"]]["id"]
    assert kinds == [("device", "A2"), ("label", "SW-2"), ("submap", root_id)]
    assert doc["links"][0]["snmp"]["device_id"] == "A2" and doc["style"]["preset"] == "ocean"
    assert root_id not in maps


def test_check_rejects():
    for bad in (None, {}, {"format": "x"}, {"format": X.FORMAT, "version": 99, "kind": "topology", "map": {}},
                {"format": X.FORMAT, "version": 1, "kind": "outro"}, {"format": X.FORMAT, "version": 1, "kind": "availability", "maps": []}):
        try:
            X.check(bad)
            assert False, bad
        except X.XferError:
            pass
