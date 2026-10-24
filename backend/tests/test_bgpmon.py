"""Monitor BGP: leitura do resumo dos fabricantes e análise dos prefixos (python -m pytest backend/tests/test_bgpmon.py)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bgpmon as b  # noqa: E402

CISCO = """Neighbor        V           AS MsgRcvd MsgSent   TblVer  InQ OutQ Up/Down  State/PfxRcd
198.51.100.1    4        64501  123456   65432  1234567    0    0 1d02h      900123
198.51.100.5    4        64502       0       0        1    0    0 never    Idle (Admin)"""
HUAWEI = """  Peer            V          AS  MsgRcvd  MsgSent  OutQ  Up/Down       State  PrefRcv
  198.51.100.1    4       64501  1234567   765432     0 0010h21m Established   900000
  198.51.100.5    4       64502        0        0     0 00:00:20        Idle        0"""
JUNOS = """Peer                     AS      InPkt     OutPkt    OutQ   Flaps Last Up/Dwn State|#Active/Received/Accepted/Damped...
198.51.100.1          64501     123456      12345       0       1     1w2d3h Establ
  inet.0: 800000/900000/899000/0
203.0.113.9           64503        100        100       0       0       1:00:00 10/20/20/0"""


def test_cli():
    c = b.parse_cli_summary(CISCO)
    assert c["198.51.100.1"] == {"peer": "198.51.100.1", "asn": 64501, "state": "established", "prefixes": 900123}
    assert c["198.51.100.5"]["state"] == "idle"
    h = b.parse_cli_summary(HUAWEI)
    assert h["198.51.100.1"]["prefixes"] == 900000 and h["198.51.100.5"]["state"] == "idle"
    j = b.parse_cli_summary(JUNOS)
    assert j["198.51.100.1"]["prefixes"] == 899000 and j["203.0.113.9"]["prefixes"] == 20


def test_prefix_eval():
    cfg = b.clean_prefixes([{"prefix": "203.0.113.0/24", "origins": ["AS64500"], "upstreams": ["64510"]}])[0]
    rs = {"origins": [{"origin": 64500}], "visibility": {"v4": {"ris_peers_seeing": 300, "total_ris_peers": 320}},
          "more_specifics": [{"prefix": "203.0.113.128/25", "origin": 64666}]}
    lg = {"rrcs": [{"peers": [{"as_path": "3333 64510 64500"}, {"as_path": "3333 64999 64500 64500"}]}]}
    r = b.evaluate_prefix(cfg, rs, lg, {"64500": "valid"}, 50)
    assert [f["code"] for f in r["findings"]] == ["subhijack", "leak"] and r["visibility_pct"] == 93.8
    ok = b.evaluate_prefix(cfg, {**rs, "more_specifics": []}, {"rrcs": []}, {"64500": "valid"}, 50)
    assert ok["findings"] == []
