"""Mapa automático: enlaces por sub-rede /30 e por LLDP, junção e layout."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import teopt  # noqa: E402
import topodisc as T  # noqa: E402

DEVS = {"r1": {"id": "r1", "name": "BORDA-1", "device_type": "huawei"}, "r2": {"id": "r2", "name": "CORE-1", "device_type": "juniper"},
        "s1": {"id": "s1", "name": "SW-A", "device_type": "datacom", "snmp_sys_name": "sw-a.exemplo.net"}}


def _if(name, speed, ips=(), out_bps=None, in_bps=None, descr=""):
    return {"name": name, "descr": descr or name, "speed_mbps": speed, "ips": list(ips), "oper": "up", "out_bps": out_bps, "in_bps": in_bps, "alias": ""}


DATA = {
    "r1": {"ok": True, "sys_name": "BORDA-1", "ifaces": {10: _if("Eth-Trunk1", 20000, ["10.0.0.1/30"], 5e9, 1e9),
                                                         11: _if("GigabitEthernet0/0/1", 1000, [], 2e8, 1e8),
                                                         12: _if("100GE0/1/0", 100000, [], 1e8, 1e8)}},
    "r2": {"ok": True, "sys_name": "core-1", "ifaces": {500: _if("ae0", 20000, ["10.0.0.2/30"], 1e9, 5e9),
                                                        501: _if("xe-0/0/0", 10000, [], 0, 0)}},
    "s1": {"ok": True, "sys_name": "sw-a", "ifaces": {3: _if("ten-gigabit-ethernet-1/1/3", 10000, [], 1e8, 2e8)}},
}
LLDP = {
    # membros físicos do Eth-Trunk (par já ligado por IP: fica de fora) + enlace L2 para o switch
    "r1": [{"local_num": 12, "local_id": "100GE0/1/0", "local_desc": "", "sys_name": "CORE-1.exemplo.net", "port_id": "xe-0/0/0", "port_desc": "", "port_subtype": 5},
           {"local_num": 11, "local_id": "GigabitEthernet0/0/1", "local_desc": "", "sys_name": "sw-a", "port_id": "3", "port_desc": "", "port_subtype": 7},
           {"local_num": 99, "local_id": "GE0/0/9", "local_desc": "", "sys_name": "OLT-FORA", "port_id": "gei-1/1/1", "port_desc": "", "port_subtype": 5}],
    "s1": [{"local_num": 3, "local_id": "ten-gigabit-ethernet-1/1/3", "local_desc": "", "sys_name": "BORDA-1", "port_id": "GigabitEthernet0/0/1", "port_desc": "", "port_subtype": 5}],
}


def test_ip_and_lldp_merge():
    ip, _ = teopt.discover_links(DEVS, DATA)
    ll, outside = T.lldp_links(DEVS, DATA, LLDP)
    assert len(ip) == 1
    assert {(x["a"], x["a_idx"], x["b"], x["b_idx"]) for x in ll} == {("r1", 12, "r2", 501), ("r1", 11, "s1", 3)}
    assert outside[0]["sys_name"] == "OLT-FORA" and outside[0]["seen_by"] == ["BORDA-1"]
    links = T.merge(DEVS, DATA, ip, ll)
    assert len(links) == 2                                   # o físico do bundle r1-r2 fica de fora
    l3 = next(x for x in links if x["method"] == "ip")
    assert l3["a_if"] == "Eth-Trunk1" and l3["b_if"] == "ae0" and l3["capacity_mbps"] == 20000 and l3["ab_pct"] == 25.0
    l2 = next(x for x in links if x["method"] == "lldp")
    assert l2["a_if"] == "GigabitEthernet0/0/1" and l2["b_if"] == "ten-gigabit-ethernet-1/1/3" and l2["capacity_mbps"] == 1000
    assert len(T.merge(DEVS, DATA, ip, ll, physical=True)) == 3


def test_build_map_and_merge_existing():
    ip, _ = teopt.discover_links(DEVS, DATA)
    ll, _ = T.lldp_links(DEVS, DATA, LLDP)
    links = T.merge(DEVS, DATA, ip, ll)
    nodes, mlinks, st = T.build_map_items(links, ["r1", "r2", "s1"])
    assert st == {"added_nodes": 3, "added_links": 2}
    assert all(60 <= n["x"] <= 1340 and 50 <= n["y"] <= 750 for n in nodes)
    assert len({(n["x"], n["y"]) for n in nodes}) == 3
    ln = next(l for l in mlinks if l["from_if"]["name"] == "Eth-Trunk1")
    assert ln["to_if"] == {"index": 500, "name": "ae0", "speed_mbps": 20000}
    # de novo, em cima do mapa salvo: nada duplica e as posições ficam
    existing = {"nodes": nodes, "links": mlinks}
    n2, l2, st2 = T.build_map_items(links, ["r1", "r2", "s1"], existing)
    assert st2 == {"added_nodes": 0, "added_links": 0} and [(n["x"], n["y"]) for n in n2] == [(n["x"], n["y"]) for n in nodes]


def test_norms():
    assert T.norm_if("GigabitEthernet0/0/1") == T.norm_if("GE0/0/1")
    assert T.norm_host("CORE-1.exemplo.net") == "core-1"
