"""Syslog/traps: leitura das mensagens dos fabricantes e regras (python -m pytest backend/tests/test_logparse.py)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import logparse as lp  # noqa: E402
import snmp_service as sn  # noqa: E402

P = {p["preset"]: p for p in lp.PRESETS}


def _m(preset, line):
    r = lp.test_rule(P[preset]["pattern"], P[preset]["clear_pattern"], line)[0]
    return r["match"], r["group"]


def test_syslog_vendors():
    c = lp.parse_syslog(b"<189>45: router1: *Oct 24 10:00:00.123: %SYS-5-CONFIG_I: Configured from console by admin")
    assert c["host_name"] == "router1" and c["severity"] == 5 and c["msg"].startswith("%SYS-5-CONFIG_I")
    h = lp.parse_syslog(b"<187>Oct 24 2026 10:00:00 HUAWEI-R1 %%01BGP/2/hwBgpPeerBackwardTransition_active(l)[0]:x")
    assert h["host_name"] == "HUAWEI-R1" and h["program"] == "BGP" and h["severity"] == 3
    j = lp.parse_syslog(b"<28>Oct 24 10:00:00 mx-borda rpd[1234]: BGP_PEER_STATE_CHANGED: x")
    assert j["host_name"] == "mx-borda" and j["program"] == "rpd"
    m = lp.parse_syslog(b"<30>Oct 24 10:00:00 interface,info ether1 link down")
    assert m["host_name"] == "" and m["msg"] == "interface,info ether1 link down"
    r = lp.parse_syslog(b"<14>1 2026-10-24T10:00:00.000-03:00 sw1 app 12 ID47 - hello")
    assert r["host_name"] == "sw1" and r["msg"] == "hello"


def _v2(tag, vbl, rid=7):
    return sn._tlv(0x30, sn._enc_int(1) + sn._tlv(0x04, b"public") + sn._tlv(tag, sn._enc_int(rid) + sn._enc_int(0) + sn._enc_int(0) + sn._tlv(0x30, vbl)))


def test_traps():
    vb = lambda o, v: sn._tlv(0x30, sn._enc_oid(o) + v)  # noqa: E731
    vbl = vb("1.3.6.1.6.3.1.1.4.1.0", sn._enc_oid("1.3.6.1.6.3.1.1.5.3")) + vb("1.3.6.1.2.1.2.2.1.8.3", sn._enc_int(2)) + \
        vb("1.3.6.1.2.1.31.1.1.1.1.3", sn._tlv(0x04, b"ge-0/0/1"))
    t = lp.parse_trap(_v2(0xA7, vbl))
    assert t["msg"] == "linkDown ifOperStatus=down(2) ifName=ge-0/0/1" and t["inform_reply"] is None
    t = lp.parse_trap(_v2(0xA6, vbl, rid=55))
    assert sn.parse_response(t["inform_reply"])[:2] == (55, 0)
    vbl = vb("1.3.6.1.6.3.1.1.4.1.0", sn._enc_oid("1.3.6.1.2.1.15.7.2")) + vb("1.3.6.1.2.1.15.3.1.2.192.0.2.1", sn._enc_int(1))
    assert _m("bgp", lp.parse_trap(_v2(0xA7, vbl))["msg"]) == ("fire", "192.0.2.1")
    pdu = sn._tlv(0xA4, sn._enc_oid("1.3.6.1.4.1.9") + sn._tlv(0x40, bytes([192, 0, 2, 5])) + sn._enc_int(0) + sn._enc_int(0) +
                  sn._tlv(0x43, b"\x00") + sn._tlv(0x30, b""))
    t = lp.parse_trap(sn._tlv(0x30, sn._enc_int(0) + sn._tlv(0x04, b"public") + pdu))
    assert t["trap_name"] == "coldStart" and t["agent"] == "192.0.2.5"


def test_presets():
    assert _m("bgp", "%BGP-5-ADJCHANGE: neighbor 192.0.2.1 Down BGP Notification sent") == ("fire", "192.0.2.1")
    assert _m("bgp", "%BGP-5-ADJCHANGE: neighbor 192.0.2.1 Up") == ("clear", "192.0.2.1")
    assert _m("bgp", "rpd[1]: BGP_PEER_STATE_CHANGED: BGP peer 198.51.100.1 (External AS 64500) changed state from Established to Idle") == ("fire", "198.51.100.1")
    assert _m("bgp", "%%01BGP/2/hwBgpPeerBackwardTransition_active(l)[0]:x (BgpPeerRemoteAddr=192.0.2.9, I=0)") == ("fire", "192.0.2.9")
    assert _m("ospf", "%OSPF-5-ADJCHG: Process 1, Nbr 192.0.2.2 on Gi0/1 from FULL to DOWN, Neighbor Down") == ("fire", "192.0.2.2")
    assert _m("flap", "%LINK-3-UPDOWN: Interface GigabitEthernet0/1, changed state to down") == ("fire", "GigabitEthernet0/1")
    assert _m("flap", "interface,info ether1 link down") == ("fire", "ether1")
    assert _m("los", "GPON ONU LOS alarm gpon-onu_1/2/3:12") == ("fire", "1/2/3")
    assert _m("config", "mgd[1]: UI_COMMIT: User 'admin' requested 'commit'")[0] == "fire"


def test_engine():
    e = lp.RuleEngine()
    e.load([{**lp.clean_rule(p), "id": f"r{i}"} for i, p in enumerate(lp.PRESETS)])
    ev = lambda m: {"kind": "syslog", "severity": 3, "msg": m, "host": "192.0.2.50", "device_id": "d1", "device_name": "R1"}  # noqa: E731
    a, _ = e.feed(ev("%BGP-5-ADJCHANGE: neighbor 192.0.2.1 Down"), 1000)
    assert [x["type"] for x in a] == ["open"] and a[0]["title"] == "BGP caiu: vizinho 192.0.2.1 — R1"
    assert e.feed(ev("%BGP-5-ADJCHANGE: neighbor 192.0.2.1 Down"), 1010)[0] == []
    assert [x["type"] for x in e.feed(ev("%BGP-5-ADJCHANGE: neighbor 192.0.2.1 Up"), 1020)[0]] == ["ok"]
    for t in range(3):
        assert e.feed(ev("%LINK-3-UPDOWN: Interface Gi0/1, changed state to down"), 2000 + t)[0] == []
    assert [x["type"] for x in e.feed(ev("%LINK-3-UPDOWN: Interface Gi0/1, changed state to down"), 2004)[0]] == ["open"]
    assert [x["type"] for x in e.sweep(2004 + 16 * 60)] == ["ok"]
