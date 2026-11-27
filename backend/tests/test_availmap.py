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


def test_transfer_link_and_copy():
    from availmap import link_targets, copy_maps, place_submaps
    maps = {
        "help": {"id": "help", "name": "HELP", "nodes": [{"id": "h1", "kind": "submap", "map_id": "a", "x": 0, "y": 0}], "links": []},
        "net": {"id": "net", "name": "NETDRP", "nodes": [{"id": "n1", "kind": "submap", "map_id": "b", "x": 0, "y": 0},
                                                       {"id": "n2", "kind": "submap", "map_id": "c", "x": 200, "y": 0}],
                "links": [{"id": "l1", "from": "n1", "to": "n2"}]},
        "a": {"id": "a", "name": "POP-A", "nodes": [], "links": []},
        "b": {"id": "b", "name": "POP-B", "nodes": [{"id": "d1", "kind": "device", "device_id": "dev1", "x": 0, "y": 0},
                                                   {"id": "s1", "kind": "submap", "map_id": "c", "x": 50, "y": 0}],
              "links": [{"id": "x", "from": "d1", "to": "s1"}]},
        "c": {"id": "c", "name": "POP-C", "nodes": [], "links": []},
    }
    ok, skip = link_targets(["b", "a", "help", "net"], "help", maps)
    assert ok == ["b", "net"] and set(skip) == {"a", "help"}
    ok, skip = link_targets(["net"], "b", maps)          # b está dentro de net: net não pode entrar em b
    assert ok == [] and "net" in skip
    docs, idmap = copy_maps(["b"], maps, True, {"HELP", "NETDRP", "POP-A", "POP-B", "POP-C"})
    assert {d["name"] for d in docs} == {"POP-B (cópia)", "POP-C (cópia)"}
    nb = next(d for d in docs if d["id"] == idmap["b"])
    assert nb["nodes"][0]["device_id"] == "dev1"
    assert nb["nodes"][1]["map_id"] == idmap["c"]
    assert nb["links"][0]["from"] == nb["nodes"][0]["id"] and nb["links"][0]["to"] == nb["nodes"][1]["id"]
    docs, idmap = copy_maps(["b"], maps, False, set())
    assert len(docs) == 1 and docs[0]["nodes"][1]["map_id"] == "c" and docs[0]["name"] == "POP-B"
    new = place_submaps(maps["help"]["nodes"], ["x", "y"])
    assert len(new) == 2 and new[0]["y"] > 0 and new[1]["x"] > new[0]["x"]


def test_paste_layout_brings_links():
    from availmap import paste_layout
    src = {"nodes": [{"id": "s1", "kind": "submap", "map_id": "A", "x": 500, "y": 300},
                     {"id": "s2", "kind": "submap", "map_id": "B", "x": 700, "y": 400},
                     {"id": "s3", "kind": "submap", "map_id": "C", "x": 900, "y": 100}],
           "links": [{"id": "l1", "from": "s1", "to": "s2", "label": "10G", "snmp": {"device_id": "d", "if_index": 5, "if_name": "xe-0"},
                      "points": [{"x": 600, "y": 350}]},
                     {"id": "l2", "from": "s2", "to": "s3", "label": "", "snmp": None, "points": []}]}
    tgt = {"nodes": [{"id": "t1", "kind": "submap", "map_id": "Z", "x": 0, "y": 0}], "links": []}
    nodes, links, _ = paste_layout(tgt, src, [("A", "A", True), ("B", "B", True)])
    assert len(nodes) == 2 and len(links) == 1                     # só a linha entre os dois colados
    a, b = nodes
    assert (b["x"] - a["x"], b["y"] - a["y"]) == (200, 100)        # mantém a disposição
    assert links[0]["snmp"]["if_index"] == 5 and links[0]["label"] == "10G"
    assert {links[0]["from"], links[0]["to"]} == {a["id"], b["id"]}
    assert links[0]["points"][0]["x"] == 600 - 500 + a["x"]
    nodes, links, _ = paste_layout(tgt, src, [("A", "A", True)])     # sozinho: sem linha
    assert len(nodes) == 1 and links == []
    tgt2 = {"nodes": [{"id": "tb", "kind": "submap", "map_id": "B", "x": 0, "y": 0}], "links": []}
    nodes, links, _ = paste_layout(tgt2, src, [("A", "A2", True), ("B", "B", False)])   # B já estava: liga no existente
    assert len(nodes) == 1 and nodes[0]["map_id"] == "A2" and links[0]["to"] == "tb"


def test_paste_layout_devices():
    from availmap import paste_layout
    src = {"nodes": [{"id": "s1", "kind": "submap", "map_id": "A", "x": 0, "y": 0},
                     {"id": "d1", "kind": "device", "device_id": "dev1", "label": "", "x": 100, "y": 50},
                     {"id": "d2", "kind": "device", "device_id": "dev2", "label": "", "x": 300, "y": 50}],
           "links": [{"id": "l1", "from": "s1", "to": "d1", "snmp": {"device_id": "dev1", "if_index": 3, "if_name": "x"}, "points": []},
                     {"id": "l2", "from": "d1", "to": "d2", "snmp": None, "points": []}]}
    tgt = {"nodes": [{"id": "t2", "kind": "device", "device_id": "dev2", "x": 0, "y": 0}], "links": []}
    nodes, links, nd = paste_layout(tgt, src, [("A", "A", True)], ["d1", "d2"])
    assert nd == 1 and len(nodes) == 2                       # dev2 já estava: não duplica
    dev = next(n for n in nodes if n["kind"] == "device")
    assert dev["device_id"] == "dev1" and dev["id"] != "d1"
    assert len(links) == 2 and any(l["to"] == "t2" for l in links)
    assert next(l for l in links if l["snmp"])["snmp"]["if_index"] == 3
    nodes, links, nd = paste_layout(tgt, src, [], ["d1"])    # equipamento sozinho: sem linha
    assert nd == 1 and links == []
