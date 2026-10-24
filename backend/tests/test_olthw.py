import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
import olthw as h
from oltgen import GenError

AUTOFIND = """
   ----------------------------------------------------------------------------
   Number              : 1
   F/S/P               : 0/1/3
   Ont SN              : 48575443A1B2C3D4 (HWTC-A1B2C3D4)
   Password            : 0x00000000000000000000
   VendorID            : HWTC
   Ont Version         : 10C7.A
   Ont SoftwareVersion : V5R019C10S115
   Ont EquipmentID     : EG8145V5
   Ont autofind time   : 2026-10-20 10:11:12+08:00
   ----------------------------------------------------------------------------
   Number              : 2
   F/S/P               : 0/2/0
   Ont SN              : 5A544547C0FFEE01 (ZTEG-C0FFEE01)
   VendorID            : ZTEG
   Ont EquipmentID     : F601
   ----------------------------------------------------------------------------
"""
INFO = """
  -----------------------------------------------------------------------------
  F/S/P   ONT         SN         Control     Run      Config   Match    Protect
          ID                     flag        state    state    state    side
  -----------------------------------------------------------------------------
  0/ 1/0    0  48575443A1B2C3D4  active      online   normal   match    no
  0/ 1/0    1  48575443A1B2C3D5  active      offline  initial  initial  no
  -----------------------------------------------------------------------------
  F/S/P   ONT-ID   Description
  -----------------------------------------------------------------------------
  0/ 1/0     0     CLIENTE_A
  0/ 1/0     1     CLIENTE_B
"""
SUMMARY = """
  In port 0/1/0, the total of ONTs are: 2, online: 1
  -----------------------------------------------------------------------------
  ONT  Run     Last                Last                Last
  ID   State   UpTime              DownTime            DownCause
  -----------------------------------------------------------------------------
  0    online  2026-10-20 08:00:00 2026-10-19 07:00:00 dying-gasp
  1    offline 2026-10-18 08:00:00 2026-10-20 09:30:00 LOSi
  -----------------------------------------------------------------------------
  ONT        SN        Type          Distance Rx/Tx power  Description
  ID                                    (m)      (dBm)
  -----------------------------------------------------------------------------
  0   48575443A1B2C3D4 EG8145V5      1520     -19.85/2.31   CLIENTE_A
  1   48575443A1B2C3D5 -             -        -/-           CLIENTE_B
"""
BOARD = """
  -------------------------------------------------------------------------
  SlotID  BoardName  Status          SubType0 SubType1    Online/Offline
  -------------------------------------------------------------------------
  0
  1       H901GPHF   Normal
  2       H902GPBD   Normal
  8       H901MPLA   Active_normal
  10      H901PILA   Normal
  -------------------------------------------------------------------------
"""
CFG = """
interface gpon 0/1
 ont add 0 0 sn-auth "48575443A1B2C3D4" omci ont-lineprofile-id 10 ont-srvprofile-id 1 desc "CLIENTE_A"
 ont ipconfig 0 0 pppoe vlan 100 priority 0 user-account username "cli_a" password "x"
#
service-port 5 vlan 100 gpon 0/1/0 ont 0 gemport 1 multi-service user-vlan 100 tag-transform translate
"""


def test_sn():
    assert h.sn_hex("HWTC-A1B2C3D4") == "48575443A1B2C3D4"
    assert h.sn_hex("48575443a1b2c3d4") == "48575443A1B2C3D4"
    assert h.sn_vendor("48575443A1B2C3D4") == "HWTC-A1B2C3D4"
    with pytest.raises(GenError):
        h.sn_hex("xyz")


def test_parsers():
    a = h.parse_autofind(AUTOFIND)
    assert len(a) == 2 and a[0]["pon"] == "0/1/3" and a[0]["model"] == "EG8145V5" and a[1]["sn_vendor"] == "ZTEG-C0FFEE01"
    i = h.parse_ont_info(INFO)
    assert [r["run"] for r in i] == ["online", "offline"] and i[1]["description"] == "CLIENTE_B" and i[1]["off"]
    s = h.parse_summary(SUMMARY)
    assert s[0]["rx"] == -19.85 and s[0]["slot"] == 1 and s[1]["cause"] == "LOSi" and s[1]["rx"] is None and s[1]["off"]
    b = h.parse_board(BOARD)
    assert [(x["slot"], x["ports"]) for x in b] == [(1, 16), (2, 8)]
    inv = h.parse_inventory(CFG)
    d = inv[(0, 1, 0, 0)]
    assert d["sn"] == "48575443A1B2C3D4" and d["description"] == "CLIENTE_A" and d["pppoe"] == ["cli_a"]
    assert d["services"][0]["vlan"] == 100 and d["line_profile"] == 10
    assert h.free_ont_id(h.used_ont_ids(i, 0, 1, 0)) == 2


def test_scripts():
    r = h.onu_script({"pon": "0/1/3", "onu_id": 4, "sn": "HWTC-A1B2C3D4", "line_profile": 10, "srv_profile": 1, "name": "CLIENTE X",
                      "services": [{"vlan": 100, "mode": "bridge", "ports": [1]}], "write": True})
    sc = r["script"]
    assert "interface gpon 0/1" in sc and "ont add 3 4 sn-auth 48575443A1B2C3D4" in sc
    assert "service-port vlan 100 gpon 0/1/3 ont 4 gemport 1" in sc and sc.strip().endswith("save")
    rm = h.onu_remove_script({"items": [{"pon": "0/1/3", "onu": 4}, {"pon": "0/2/0", "onu": 1}], "write": True})["script"]
    assert "undo service-port port 0/1/3 ont 4" in rm and " ont delete 3 4" in rm and "interface gpon 0/2" in rm
    g = h.generate({"hostname": "OLT-HW-01", "uplinks": ["0/8/0"], "vlans": {"enabled": True, "start": 100, "count": 10},
                    "mgmt": {"in_enabled": True, "in_vlan": 4000, "in_ip": "192.0.2.10", "in_mask": "255.255.255.0", "in_gw": "192.0.2.1"},
                    "speeds": ["300M", {"name": "1G", "mbps": 1000}], "ntp": ["192.0.2.5"]})["script"]
    assert "vlan 100 to 109 smart" in g and "port vlan 100 to 109 0/8 0" in g and "interface vlanif4000" in g
    assert "dba-profile add profile-id 10" in g and "gem mapping 1 0 vlan 100" in g
    with pytest.raises(GenError):
        h.onu_script({"pon": "0/1", "onu_id": 1})


def test_lookup():
    assert h.looks_like_sn("ZTEGD4F3D9EC") and h.looks_like_sn("48575443A1B2C3D4") and not h.looks_like_sn("7c8bca112233")
    mac = h.hw_mac("7C:8B:CA:11:22:33")
    assert mac == "7c8b-ca11-2233"
    r = h.parse_mac_lookup("  4   -  gpon 7c8b-ca11-2233 dynamic  0 /1 /0  3    1    100\n", mac)
    assert r["pon"] == "0/1/0" and r["ont"] == 3 and r["vlan"] == 100
    b = h.parse_by_sn("  F/S/P                   : 0/1/0\n  ONT-ID                  : 7\n  Run state               : offline\n"
                      "  SN                      : 48575443A1B2C3D4 (HWTC-A1B2C3D4)\n  Description             : CLIENTE_A\n"
                      "  Last down cause         : LOSi\n  Last down time          : 2026-10-20 09:30:00+08:00\n")
    assert b["ont"] == 7 and b["run"] == "offline" and b["description"] == "CLIENTE_A" and b["last_down"] == "2026-10-20 09:30:00"
