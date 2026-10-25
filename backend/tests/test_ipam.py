"""IPAM: endereços das configurações, uso, blocos livres e plano do NetBox (python -m pytest backend/tests/test_ipam.py)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ipam as I  # noqa: E402

HW = """interface GigabitEthernet0/0/1
 description PTP-POP-CENTRO
 ip binding vpn-instance CLIENTES
 ip address 10.10.0.1 255.255.255.252
#
interface Vlanif100
 ip address 203.0.113.1 255.255.255.0
 ipv6 address 2001:DB8::1/64
#
"""
JUN = "set interfaces irb unit 10 family inet address 10.10.0.5/30\nset routing-instances CLIENTES interface irb.10\n"
MK = '/ip address\nadd address=10.20.0.1/24 comment="LAN clientes" interface=bridge1 network=10.20.0.0\n'


def test_parse():
    a = I.parse_addresses("huawei", HW)
    assert [(x["ip"], x["plen"], x["vrf"], x["description"]) for x in a] == [
        ("10.10.0.1", 30, "CLIENTES", "PTP-POP-CENTRO"), ("203.0.113.1", 24, "", ""), ("2001:db8::1", 64, "", "")]
    assert I.parse_addresses("juniper", JUN)[0]["vrf"] == "CLIENTES"
    assert I.parse_addresses("mikrotik", MK)[0]["description"] == "LAN clientes"


def test_usage_free_and_plan():
    assert I.usage("10.10.0.0/24", ["10.10.0.1", "10.10.0.2"])["used"] == 2
    assert I.free_blocks("10.10.0.0/24", ["10.10.0.0/30", "10.10.0.8/29"], 30, 2) == ["10.10.0.4/30", "10.10.0.16/30"]
    plan = I.netbox_plan([{"prefix": "10.10.0.0/24"}], [{"ip": "10.10.0.1", "plen": 30}, {"ip": "10.10.0.2", "plen": 30}],
                         [{"prefix": "198.51.100.0/24", "vrf": None}], [{"address": "10.10.0.1/30", "vrf": None}])
    assert [p["prefix"] for p in plan["new_prefixes"]] == ["10.10.0.0/24"] and [a["ip"] for a in plan["new_ips"]] == ["10.10.0.2"]
    assert plan["only_netbox"][0]["prefix"] == "198.51.100.0/24"


def test_mixed_v4_v6_and_bad_rows():
    import ipam as m
    rows = [{"ip": "10.0.0.1", "network": "10.0.0.0/30", "device_name": "R1", "device_id": "a"},
            {"ip": "2001:db8::1", "network": "2001:db8::/64", "device_name": None, "device_id": "b"},
            {"ip": "x", "network": "lixo", "device_name": "R3", "device_id": "d"}]
    unc = m.uncovered_networks(rows, ["192.0.2.0/24", "ruim"])          # IPv4 e IPv6 juntos não podem dar TypeError
    assert [u["network"] for u in unc] == ["10.0.0.0/30", "2001:db8::/64"] and unc[1]["devices"] == ["?"]
    assert m.free_blocks("10.0.0.0/29", ["10.0.0.0/30", "2001:db8::/64", "lixo"]) == ["10.0.0.4/30"]
    assert m.children("10.0.0.0/8", ["10.1.0.0/16", "10.1.1.0/24", "2001:db8::/32", "bad"]) == ["10.1.0.0/16"]
    assert m.usage("10.0.0.0/30", ["10.0.0.1", "2001:db8::1", "nada"])["used"] == 1
