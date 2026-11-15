"""Mapa de disponibilidade: estados do ICMP, submapas recursivos e validação."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import availmap as A  # noqa: E402

CFG = A.clean_settings({"down_after": 2, "warn_loss": 34, "warn_ms": 150})


def test_states():
    s = A.next_state({}, True, 0, 3.2, CFG)
    assert s == {"state": "up", "fails": 0, "changed": False}            # pending -> up não alarma
    s1 = A.next_state({"state": "up"}, False, 100, None, CFG)
    assert s1["state"] == "up" and s1["fails"] == 1 and not s1["changed"]   # 1 falha ainda não é queda
    s2 = A.next_state({"state": "up", "fails": 1}, False, 100, None, CFG)
    assert s2["state"] == "down" and s2["changed"]
    s3 = A.next_state({"state": "down", "fails": 5}, True, 0, 2, CFG)
    assert s3["state"] == "up" and s3["changed"] and s3["fails"] == 0
    assert A.next_state({"state": "up"}, True, 66.7, 4, CFG)["state"] == "warn"
    assert A.next_state({"state": "up"}, True, 0, 210, CFG) == {"state": "warn", "fails": 0, "changed": False}


def test_submaps_and_summary():
    maps = {"core": {"nodes": [{"kind": "device", "device_id": "r1"}, {"kind": "submap", "map_id": "pop"}]},
            "pop": {"nodes": [{"kind": "device", "device_id": "s1"}, {"kind": "device", "device_id": "s2"}, {"kind": "submap", "map_id": "core"}]}}
    assert A.devices_of("core", maps) == {"r1", "s1", "s2"}               # ciclo core<->pop não trava
    st = {"r1": {"state": "up"}, "s1": {"state": "down"}}
    sm = A.summarize(A.devices_of("core", maps), st)
    assert sm["state"] == "down" and sm["down"] == 1 and sm["up"] == 1 and sm["pending"] == 1 and sm["total"] == 3
    assert A.summarize(set(), st)["state"] == "empty"
    assert A.parents(maps) == {"pop": ["core"], "core": ["pop"]}


def test_clean_map():
    body = {"name": " Backbone ", "nodes": [
        {"id": "a", "kind": "device", "device_id": "r1", "x": 10, "y": 20},
        {"id": "b", "kind": "device", "device_id": "zz"},                   # equipamento inexistente sai
        {"id": "c", "kind": "submap", "map_id": "pop"}, {"id": "d", "kind": "submap", "map_id": "self"},   # submapa de si mesmo sai
        {"id": "e", "kind": "label", "label": ""}, {"id": "f", "kind": "label", "label": "POP SUL"}],
        "links": [{"id": "l1", "from": "a", "to": "c", "snmp": {"device_id": "r1", "if_index": "12", "if_name": "GE0/0/1"}},
                  {"id": "l2", "from": "c", "to": "a", "snmp": {"device_id": "zz", "if_index": 3}},      # rota paralela; equipamento inválido some
                  {"id": "l2", "from": "a", "to": "c"}, {"from": "a", "to": "b"}]}
    m = A.clean_map(body, {"r1"}, {"pop", "self"}, "self")
    assert m["name"] == "Backbone" and [n["id"] for n in m["nodes"]] == ["a", "c", "f"]
    assert [(l["id"], l["snmp"]) for l in m["links"]] == [("l1", {"device_id": "r1", "if_index": 12, "if_name": "GE0/0/1"}), ("l2", None)]
    with pytest.raises(A.MapError):
        A.clean_map({"name": ""}, set(), set())


def test_links_in_summary():
    maps = {"core": {"nodes": [{"kind": "device", "device_id": "r1"}, {"kind": "submap", "map_id": "pop"}],
                     "links": [{"snmp": {"device_id": "r1", "if_index": 1}}]},
            "pop": {"nodes": [{"kind": "device", "device_id": "s1"}], "links": [{"snmp": {"device_id": "s1", "if_index": 7}}, {"snmp": None}]}}
    keys = A.links_of("core", maps)
    assert keys == {"r1|1", "s1|7"}
    st = {"r1": {"state": "up"}, "s1": {"state": "up"}}
    assert A.summarize({"r1", "s1"}, st, keys, {"r1|1": {"oper": "up"}, "s1|7": {"oper": "up"}})["state"] == "up"
    sm = A.summarize({"r1", "s1"}, st, keys, {"r1|1": {"oper": "up"}, "s1|7": {"oper": "down"}})
    assert sm["state"] == "down" and sm["links_down"] == 1 and sm["links"] == 2
    assert A.link_state(None) == "unknown" and A.link_state({"oper": "lowerLayerDown"}) == "down"
