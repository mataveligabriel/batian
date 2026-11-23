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
    assert A.devices_of("core", maps) == {"r1"}                          # só o que está dentro (não soma o submapa)
    assert A.devices_of("pop", maps) == {"s1", "s2"}
    st = {"r1": {"state": "up"}, "s1": {"state": "down"}}
    assert A.summarize(A.devices_of("core", maps), st)["state"] == "up"   # queda no POP não acende o core
    sm = A.summarize(A.devices_of("pop", maps), st)
    assert sm["state"] == "down" and sm["down"] == 1 and sm["pending"] == 1 and sm["total"] == 2
    index = {**maps, "rede": {"nodes": [{"kind": "submap", "map_id": "core"}, {"kind": "submap", "map_id": "pop"}]}}
    assert A.devices_of("rede", index) == {"r1", "s1", "s2"}             # mapa só de submapas: um nível abaixo
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
    assert m["links"][0]["points"] == [] and m["links"][0]["shape"] == "smooth"
    m2 = A.clean_map({"name": "x", "nodes": body["nodes"], "links": [{"id": "z", "from": "a", "to": "c", "points": [{"x": 10.04, "y": "20"}, "lixo"], "shape": "straight"}]},
                     {"r1"}, {"pop"}, "self")
    assert m2["links"][0]["points"] == [{"x": 10.0, "y": 20.0}] and m2["links"][0]["shape"] == "straight"
    with pytest.raises(A.MapError):
        A.clean_map({"name": ""}, set(), set())


def test_links_in_summary():
    maps = {"core": {"nodes": [{"kind": "device", "device_id": "r1"}, {"kind": "submap", "map_id": "pop"}],
                     "links": [{"snmp": {"device_id": "r1", "if_index": 1}}]},
            "pop": {"nodes": [{"kind": "device", "device_id": "s1"}], "links": [{"snmp": {"device_id": "s1", "if_index": 7}}, {"snmp": None}]}}
    assert A.links_of("core", maps) == {"r1|1"} and A.links_of("pop", maps) == {"s1|7"}
    keys = {"r1|1", "s1|7"}
    st = {"r1": {"state": "up"}, "s1": {"state": "up"}}
    assert A.summarize({"r1", "s1"}, st, keys, {"r1|1": {"oper": "up"}, "s1|7": {"oper": "up"}})["state"] == "up"
    sm = A.summarize({"r1", "s1"}, st, keys, {"r1|1": {"oper": "up"}, "s1|7": {"oper": "down"}})
    assert sm["state"] == "down" and sm["links_down"] == 1 and sm["links"] == 2
    assert A.link_state(None) == "unknown" and A.link_state({"oper": "lowerLayerDown"}) == "down"


def test_style_and_annotations():
    st = A.clean_style({"up": "#00ff00", "down": "vermelho", "bg": "dots", "sub_shape": "circle", "dev_shape": "card", "scale": 9})
    assert st["up"] == "#00ff00" and "down" not in st and st["bg"] == "dots" and st["sub_shape"] == "circle" and st["dev_shape"] == "card" and st["scale"] == 2
    assert A.clean_style(None)["sub_shape"] == "hex"
    m = A.clean_map({"name": "x", "nodes": [
        {"id": "n1", "kind": "note", "x": 10, "y": 20, "w": 5, "h": 9999, "text": "obs: fibra em manutenção", "fill": "#112233", "stroke": "x"},
        {"id": "s1", "kind": "shape", "shape": "ellipse", "w": 300, "h": 200, "opacity": 3}]}, set(), set())
    n1, s1 = m["nodes"]
    assert n1["w"] == 20 and n1["h"] == 4000 and n1["fill"] == "#112233" and n1["stroke"] == "#2dd4bf" and n1["text"].startswith("obs")
    assert s1["shape"] == "ellipse" and s1["opacity"] == 1 and s1["fill"] == "transparent"
    assert m["style"]["bg"] == "grid"


def test_descendants():
    maps = {"a": {"nodes": [{"kind": "submap", "map_id": "b"}]}, "b": {"nodes": [{"kind": "submap", "map_id": "c"}, {"kind": "submap", "map_id": "a"}]},
            "c": {"nodes": [{"kind": "submap", "map_id": "b"}]}, "d": {"nodes": []}}
    assert A.descendants("a", maps) == {"b", "c"}
    assert A.descendants("d", maps) == set()


def test_rate_and_line_style():
    assert A.rate_bps(1000, 2000, 8) == 1000.0
    assert A.rate_bps(None, 2000, 8) is None and A.rate_bps(5000, 10, 8) is None and A.rate_bps(1, 2, 0) is None
    st = A.clean_style({"line_w": 99, "traffic": False})
    assert st["line_w"] == 8 and st["line_auto"] is True and st["traffic"] is False
    m = A.clean_map({"name": "x", "nodes": [{"id": "a", "kind": "label", "label": "A"}, {"id": "b", "kind": "label", "label": "B"}],
                     "links": [{"from": "a", "to": "b", "width": 4, "color": "#ff0000", "dash": "dotted"}, {"from": "a", "to": "b", "color": "red", "dash": "x"}]},
                    set(), set())
    assert (m["links"][0]["width"], m["links"][0]["color"], m["links"][0]["dash"]) == (4, "#ff0000", "dotted")
    assert (m["links"][1]["width"], m["links"][1]["color"], m["links"][1]["dash"]) == (0, "", "solid")


def test_style_own():
    assert A.clean_style({})["own"] is False and A.clean_style({"bg": "grid"})["own"] is False
    assert A.clean_style({"preset": "ocean", "up": "#38bdf8"})["own"] is True      # gravado antes do "own"
    assert A.clean_style({"preset": "ocean", "own": False})["own"] is False


def test_ignored():
    from datetime import datetime, timezone
    st = {"a": {"state": "down"}, "b": {"state": "ignored"}, "c": {"state": "up"}}
    s = A.summarize({"a", "b", "c"}, st)
    assert (s["total"], s["ignored"], s["down"], s["state"]) == (2, 1, 1, "down")
    assert A.summarize({"b"}, st)["state"] == "empty"
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert A.ignore_until(60, now) == "2026-01-01T01:00:00+00:00" and A.ignore_until(0, now) is None
    assert A.ignore_until(10 ** 9, now).startswith("2026-04-01")
    try:
        A.ignore_until(-5, now)
        assert False
    except A.MapError:
        pass


def test_delete_set_and_folder():
    maps = {"F": {"nodes": [{"kind": "submap", "map_id": "A"}, {"kind": "submap", "map_id": "B"}]},
            "A": {"nodes": [{"kind": "submap", "map_id": "B"}, {"kind": "submap", "map_id": "C"}]},
            "B": {"nodes": [{"kind": "submap", "map_id": "A"}]},
            "C": {"nodes": []}, "X": {"nodes": [{"kind": "submap", "map_id": "C"}]}}
    assert A.delete_set(["F"], maps) == {"F"}
    assert A.delete_set(["F"], maps, True) == {"F", "A", "B"}          # C também está no X, que fica
    assert A.delete_set(["F", "X", "nada"], maps, True) == {"F", "A", "B", "C", "X"}
    nodes = A.folder_nodes(["a", "b", "c"], cols=2)
    assert [(n["map_id"], n["x"], n["y"]) for n in nodes] == [("a", 0, 0), ("b", 220, 0), ("c", 0, 200)]
