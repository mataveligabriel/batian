import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest  # noqa: E402
import oltfh as f  # noqa: E402
from oltgen import GenError  # noqa: E402

CARDS = """
CARD   EXIST   CONFIG   DETECT   DETAIL
1      YES     GC8B     GC8B     MATCH
2      YES     GCOB     GCOB     MATCH
9      YES     HSWA     HSWA     MATCH
19     YES     GU6F     GU6F     MATCH
"""
DISC = """
----- ONU Unauth Table, SLOT = 1, PON = 2, ITEM = 2 -----
No     OnuType         PhyAddress     Pwd       LogicSN   LogicPwd   Why
1      HG6143D         FHTT1A2B3C4D   null      null      null       1
2      AN5506-04-F1    FHTT0000ABCD   null      null      null       1
"""
AUTH = """
----- ONU Auth Table, SLOT = 1, PON = 2, ITEM = 3 -----
Slot Pon Onu OnuType       ST Lic OST PhyId        PhyPwd LogicId LogicPwd
1    2   1   HG6143D       A  1   up  FHTT1A2B3C4D null   null    null
1    2   2   AN5506-04-F1  A  1   dn  FHTT0000ABCD null   null    null
1    2   5   HG260         A  1   up  FHTT99998888 null   null    null
"""


def test_parse():
    assert [(c["slot"], c["name"], c["ports"]) for c in f.parse_cards(CARDS)] == [(1, "GC8B", 8), (2, "GCOB", 16)]
    d = f.parse_discovery(DISC, 1, 2)
    assert [(x["sn"], x["type"]) for x in d] == [("FHTT1A2B3C4D", "HG6143D"), ("FHTT0000ABCD", "AN5506-04-F1")]
    a = f.parse_auth(AUTH, 1, 2)
    assert [(x["onu"], x["state"], x["type"]) for x in a] == [(1, "online", "HG6143D"), (2, "offline", "AN5506-04-F1"), (5, "online", "HG260")]
    assert f.free_onu_id([x["onu"] for x in a]) == 3
    assert f.parse_optic("RECV POWER   : -20.17(dBm)\nSEND POWER : 2.1") == -20.17


def test_scripts():
    s = f.onu_script({"slot": 1, "pon": 2, "onu_id": 3, "sn": "fhtt-1a2b3c4d", "type": "HG6143D", "write": True,
                      "services": [{"vlan": 100, "mode": "pppoe", "pppoe_user": "cli", "pppoe_password": "x1"}, {"vlan": 200, "mode": "bridge", "ports": [2]}]})["script"]
    assert "set whitelist phy_addr address FHTT1A2B3C4D password null action add slot 1 pon 2 onu 3 type HG6143D" in s
    assert "set wancfg slot 1 2 3 index 1 mode internet type route 100" in s and "apply wancfg slot 1 2 3" in s
    assert "set epon slot 1 pon 2 onu 3 port 2 service 1 vlan_mode tag 0 33024 200" in s and "apply onu 1 2 3 vlan" in s and s.strip().endswith("save")
    r = f.onu_remove_script({"items": [{"slot": 1, "pon": 2, "onu": 3, "sn": "FHTT1A2B3C4D", "type": "HG6143D"}], "dir": "gpononu"})["script"]
    assert "cd gpononu" in r and "action delete slot 1 pon 2 onu 3 type HG6143D" in r
    with pytest.raises(GenError):
        f.onu_remove_script({"items": [{"slot": 1, "pon": 2, "onu": 3}]})
    g = f.generate({"uplinks": ["19:1"], "vlans": {"enabled": True, "start": 100, "count": 32},
                    "mgmt": {"enabled": True, "vlan": 4000, "ip": "192.0.2.10/30", "gw": "192.0.2.9"}, "snmp": {"enabled": True, "community": "leitura"},
                    "autosave": {"enabled": True, "time": "03:00"}})["script"]
    assert "set uplink port 19:1 enable" in g and "set service CLIENTES vid_begin 100 vid_end 131 uplink 19:1 tagged service_type 1" in g
    assert "set manage vlan name GERENCIA ip 192.0.2.10/30 192.0.2.9" in g and "set auto_save enable period 1440 active_time hour 3 min 0" in g
