"""Migração de OLT: leitura da origem (ZTE C300/TITAN, Huawei, planilha), plano por PON e scripts."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import oltmig  # noqa: E402

C300 = """
interface gpon-olt_1/2/1
  name PON-EXEMPLO
  onu 1 type ZTE-F601 sn ZTEGC0000001
  onu 2 type ZTE-F670L sn ZTEGC0000002
  onu 5 type ZTE-F601 loid 1234567
!
interface gpon-olt_1/2/2
  onu 1 type ZTE-F601 sn ZTEGC0000003
!
interface gpon-onu_1/2/1:1
  name cliente_um
  description Rua X 100
  tcont 1 profile 100M
  gemport 1 name g1 tcont 1
  service-port 1 vport 1 user-vlan 100 vlan 100
!
interface gpon-onu_1/2/1:2
  name cliente-dois
  tcont 1 profile 200M
  gemport 1 tcont 1
  gemport 2 tcont 1
  service-port 1 vport 1 user-vlan 200 user-etype PPPOE vlan 200
  service-port 2 vport 2 user-vlan 300 vlan 300
!
interface gpon-onu_1/2/2:1
  name tres
  tcont 1 profile 100M
  gemport 1 tcont 1
  service-port 1 vport 1 user-vlan 100 vlan 100
!
pon-onu-mng gpon-onu_1/2/1:1
  service 1 gemport 1 vlan 100
  vlan port eth_0/1 mode tag vlan 100
!
pon-onu-mng gpon-onu_1/2/1:2
  service 1 gemport 1 vlan 200
  service 2 gemport 2 vlan 300
  wan-ip 1 mode pppoe username fulano@provedor password segredo vlan-profile v200 host 1
  vlan port eth_0/2 mode transparent
!
pon-onu-mng gpon-onu_1/2/2:1
  service 1 gemport 1 vlan 100
  vlan port eth_0/1 mode tag vlan 100
!
"""

TITAN = """
interface gpon_olt-1/1/1
  onu 1 type ZTE-F670L sn ZTEGC00000AA
!
interface gpon_onu-1/1/1:1
  name titan1
  tcont 1 profile 1G
  gemport 1 tcont 1
!
interface vport-1/1/1.1:1
  service-port 1 user-vlan 500 vlan 500
!
pon-onu-mng gpon_onu-1/1/1:1
  service 1 gemport 1 vlan 500
  vlan port eth_0/1 mode tag vlan 500
!
"""

HUAWEI = """
ont-srvprofile gpon profile-id 10 profile-name "bridge"
 ont-port pots adaptive 32 eth adaptive 8
 port vlan eth 1-2 translation 100 user-vlan 100
 commit
 quit
interface gpon 0/1
 ont add 0 0 sn-auth "48575443A1B2C3D4" omci ont-lineprofile-id 10 ont-srvprofile-id 10 desc "cliente hw"
 ont add 0 1 sn-auth "48575443A1B2C3D5" omci ont-lineprofile-id 20 ont-srvprofile-id 20 desc "roteada"
 ont port native-vlan 0 0 eth 1 vlan 100 priority 0
quit
service-port 10 vlan 100 gpon 0/1/0 ont 0 gemport 1 multi-service user-vlan 100 tag-transform translate
service-port 11 vlan 200 gpon 0/1/0 ont 1 gemport 2 multi-service user-vlan 200 tag-transform translate
"""


def test_parse_zte_c300():
    onus = oltmig.parse_zte(C300)
    assert [(o["pon"], o["onu"]) for o in onus] == [("2/1", 1), ("2/1", 2), ("2/1", 5), ("2/2", 1)]
    a, b, loid = onus[0], onus[1], onus[2]
    assert a["sn"] == "ZTEGC0000001" and a["type"] == "ZTE-F601" and a["name"] == "cliente_um" and a["tcont"] == "100M"
    assert a["services"] == [{"vlan": 100, "uvlan": 100, "mode": "bridge", "ports": [1]}]
    # PPPoE (user-etype PPPOE) + transparente na eth 2; usuário PPPoE sim, senha nunca
    assert [(s["vlan"], s["mode"]) for s in b["services"]] == [(200, "transparent"), (300, "transparent")]
    assert b["pppoe"] == ["fulano@provedor"] and "segredo" not in str(b)
    assert loid["sn"] == "" and loid["auth"] == "loid"
    s = oltmig.summarize(onus)
    assert s["onus"] == 4 and s["pons"][0]["pon"] == "2/1" and s["pons"][0]["onus"] == 3 and s["no_sn"] == 1


def test_parse_titan_and_huawei():
    t = oltmig.parse_zte(TITAN)
    assert t[0]["pon"] == "1/1" and t[0]["services"][0]["vlan"] == 500 and t[0]["services"][0]["mode"] == "bridge"
    h = oltmig.parse_huawei(HUAWEI)
    assert [(o["pon"], o["onu"], o["sn"]) for o in h] == [("0/1/0", 0, "HWTCA1B2C3D4"), ("0/1/0", 1, "HWTCA1B2C3D5")]
    assert h[0]["services"][0]["ports"] == [1] and h[0]["services"][0]["mode"] == "bridge"
    assert h[1]["services"][0]["mode"] == "router" and h[0]["type"] == "line 10 / srv 10"


def test_c300_to_titan_only_selected_pon():
    onus = oltmig.parse_zte(C300)
    plan = oltmig.build_plan(onus, {"kind": "zte-titan", "pons": {"2/1": "1/3"}, "vlan_map": {"100": "1100"}}, "zte",
                             dest_used={"1/3": [2]})
    assert plan["total"] == 3 and len(plan["groups"]) == 1           # só a PON escolhida
    g = plan["groups"][0]
    ids = {o["src_onu"]: o for o in g["onus"]}
    assert ids[1]["dst_onu"] == 1 and ids[2]["dst_onu"] == 3 and ids[2]["warnings"]   # 2 já ocupado no destino
    assert not ids[1]["warnings"]
    assert not ids[5]["ok"] and "LOID" in ids[5]["errors"][0]
    assert ids[1]["services"][0]["vlan"] == 1100 and ids[1]["type"] == "ZTE-F601"
    sc = oltmig.script_dest(plan, "1/3")["script"]
    assert "interface gpon_olt-1/1/3\n onu 1 type ZTE-F601 sn ZTEGC0000001\n onu 3 type ZTE-F670L sn ZTEGC0000002" in sc
    assert "interface vport-1/1/3.1:1\n service-port 1 user-vlan 1100 vlan 1100" in sc
    assert " vlan port eth_0/1 mode tag vlan 1100" in sc and " vlan port eth_0/2 mode transparent" in sc
    assert sc.rstrip().endswith("write") and "ZTEGC0000003" not in sc
    rm = oltmig.script_src_remove(plan, "1/3", "zte", "c300")["script"]
    assert "interface gpon-olt_1/2/1\n no onu 1\n no onu 2\nexit" in rm and "no onu 5" not in rm
    rb = oltmig.script_rollback(plan, "1/3")["script"]
    assert "interface gpon_olt-1/1/3\n no onu 1\n no onu 3" in rb


def test_huawei_to_zte_and_back():
    h = oltmig.parse_huawei(HUAWEI)
    plan = oltmig.build_plan(h, {"kind": "zte-c300", "pons": {"0/1/0": "1/1"}, "default_type": "GENERIC-4ETH", "default_tcont": "100M"}, "huawei")
    ids = [(o["src_onu"], o["dst_onu"]) for o in plan["groups"][0]["onus"]]
    assert ids == [(0, 1), (1, 2)]                                    # Huawei começa em 0, ZTE em 1
    sc = oltmig.script_dest(plan, "1/1")["script"]
    assert " onu 1 type GENERIC-4ETH sn HWTCA1B2C3D4" in sc and " service-port 1 vport 1 user-vlan 100 vlan 100" in sc
    assert "cliente_hw" in sc
    # ZTE -> Huawei: precisa de line/srv profile; ID 1 vira 0
    z = oltmig.parse_zte(TITAN)
    p2 = oltmig.build_plan(z, {"kind": "huawei", "pons": {"1/1": "0/2/5"}, "default_line": 10, "default_srv": 10}, "zte")
    o = p2["groups"][0]["onus"][0]
    assert o["ok"] and o["dst_onu"] == 0
    s2 = oltmig.script_dest(p2, "0/2/5")["script"]
    assert 'ont add 5 0 sn-auth 5A544547C00000AA omci ont-lineprofile-id 10 ont-srvprofile-id 10 desc "titan1"' in s2
    assert "service-port vlan 500 gpon 0/2/5 ont 0 gemport 1 multi-service user-vlan 500 tag-transform translate" in s2
    p3 = oltmig.build_plan(z, {"kind": "huawei", "pons": {"1/1": "0/2/5"}}, "zte")
    assert not p3["groups"][0]["onus"][0]["ok"]                        # sem perfis: pendente


def test_csv_to_fiberhome_and_verify():
    csv = "pon;onu;serial;tipo;nome;vlan;modo;portas\n1/1/2;7;FHTT1A2B3C4D;HG6143D;Joao Silva;100;bridge;1,2\n1/1/2;8;fhtt-0000abcd;HG6143D;Maria;200;router;\n"
    onus = oltmig.parse_csv(csv, "zte")
    assert onus[0]["pon"] == "1/2" and onus[1]["sn"] == "FHTT0000ABCD" and onus[0]["services"][0]["ports"] == [1, 2]
    plan = oltmig.build_plan(onus, {"kind": "fiberhome", "pons": {"1/2": "3/1"}}, "csv", dest_sns={"FHTT0000ABCD": "3/1/9"})
    g = plan["groups"][0]
    assert g["onus"][0]["ok"] and g["onus"][0]["name"] == "Joao_Silva"
    assert not g["onus"][1]["ok"] and "já autorizado" in g["onus"][1]["errors"][0]
    sc = oltmig.script_dest(plan, "3/1")["script"]
    assert "set whitelist phy_addr address FHTT1A2B3C4D password null action add slot 3 pon 1 onu 7 type HG6143D" in sc
    v = oltmig.verify(plan, "3/1", {7: {"off": False, "phase": "working"}})
    assert v["online"] == 1 and v["missing"] == 0


def test_errors():
    with pytest.raises(oltmig.MigError):
        oltmig.build_plan([], {"kind": "zte-titan", "pons": {}}, "zte")
    with pytest.raises(oltmig.MigError):
        oltmig.build_plan(oltmig.parse_zte(TITAN), {"kind": "zte-titan", "pons": {"1/1": "x"}}, "zte")
    with pytest.raises(oltmig.MigError):
        oltmig.parse_csv("pon;onu\nabc;1", "zte")
    assert oltmig.norm_sn("48575443A1B2C3D4") == "HWTCA1B2C3D4" and oltmig.norm_sn("aabbccddeeff") == ""


def test_one_taken_id_does_not_shift_the_rest():
    cfg = "interface gpon_olt-1/1/1\n" + "".join(f"  onu {i} type ZTE-F601 sn ZTEGC0000{i:03d}\n" for i in range(1, 8)) + "!\n"
    plan = oltmig.build_plan(oltmig.parse_zte(cfg), {"kind": "zte-titan", "pons": {"1/1": "1/2"}, "default_tcont": "100M",
                                                    "default_service": {"vlan": 100}}, "zte", dest_used={"1/2": [3]})
    got = {o["src_onu"]: o["dst_onu"] for o in plan["groups"][0]["onus"]}
    assert got == {1: 1, 2: 2, 3: 8, 4: 4, 5: 5, 6: 6, 7: 7}
