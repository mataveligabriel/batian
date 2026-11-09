"""Flow detalhado: gravação por minuto e consulta com filtros/agrupamentos (sem banco)."""
import ipaddress
import os
import sys
import tempfile
import time
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import flowraw  # noqa: E402

IP = lambda s: int(ipaddress.ip_address(s))  # noqa: E731
T0 = 1_780_000_020 // 60 * 60          # um minuto qualquer
TR = "10.0.0.1|10"                     # chave da interface de trânsito
CL = "10.0.0.1|20"                     # interface de cliente


def _minute(base, ts, flows, cap=flowraw.ROW_CAP):
    buf = flowraw.Buffer(cap)
    for f in flows:
        buf.add(*f)
    flowraw.write(base, ts, buf.take())


@pytest.fixture()
def base():
    with tempfile.TemporaryDirectory() as d:
        b = Path(d)
        # minuto 0: download de dois clientes, um ataque NTP para x.x.x.10 vindo de 2 blocos, upload de um cliente
        f0 = []
        for i in range(50):
            f0.append((4, IP(f"198.51.100.{i}"), IP("203.0.113.10"), 17, 123, 0, 0, TR, CL, 64500, 0, 480_000, 1000))
        for i in range(10):
            f0.append((4, IP(f"192.0.2.{i}"), IP("203.0.113.10"), 17, 123, 0, 0, TR, CL, 64501, 0, 96_000, 200))
        f0.append((4, IP("8.8.8.8"), IP("203.0.113.20"), 6, 443, 0, 0x18, TR, CL, 15169, 0, 6_000_000, 4000))
        f0.append((4, IP("203.0.113.20"), IP("8.8.8.8"), 6, 0, 443, 0x10, CL, TR, 0, 15169, 300_000, 3000))
        f0.append((6, IP("2001:db8::1"), IP("2001:db8:ff::5"), 6, 443, 0, 0x10, TR, CL, 64502, 0, 1_500_000, 1000))
        _minute(b, T0, f0)
        # minuto 1: só o cliente .20 baixando
        _minute(b, T0 + 60, [(4, IP("8.8.4.4"), IP("203.0.113.20"), 6, 443, 0, 0x10, TR, CL, 15169, 0, 3_000_000, 2000)])
        yield b


def run(base, specs, **kw):
    return flowraw.run(base, start=T0, end=T0 + 120, ifaces=[TR, CL], specs=specs, asn_cc={64500: "CN", 64501: "US", 15169: "US"}, **kw)


def test_total_and_ip_filter(base):
    r = run(base, [{"id": "all", "direction": "in", "group_by": []},
                   {"id": "ip", "direction": "in", "filters": [{"dim": "dst_ip", "values": ["203.0.113.20"]}], "group_by": []}])
    tot = r["results"][0]["total"]
    # entrada só pelo trânsito... e o upload entra pela interface de cliente (também monitorada)
    assert tot["bytes"] == 50 * 480_000 + 10 * 96_000 + 6_000_000 + 300_000 + 1_500_000 + 3_000_000
    ip = r["results"][1]["total"]
    assert ip["bytes"] == 9_000_000
    assert r["step"] == 60 and len(r["ts"]) == 2
    assert ip["bps"][0] == pytest.approx(6_000_000 * 8 / 60) and ip["bps"][1] == pytest.approx(3_000_000 * 8 / 60)


def test_attack_by_src_block_as_country(base):
    flt = [{"dim": "dst_ip", "values": ["203.0.113.10"]}]
    r = run(base, [{"id": "pfx", "direction": "in", "filters": flt, "group_by": ["src_pfx"]},
                   {"id": "as", "direction": "in", "filters": flt, "group_by": ["src_as"]},
                   {"id": "cc", "direction": "in", "filters": flt, "group_by": ["src_cc"]},
                   {"id": "sz", "direction": "in", "filters": flt, "group_by": ["pktsize", "sport"]}])
    pfx = r["results"][0]["rows"]
    parts, name = flowraw.decode(["src_pfx"], pfx[0]["key"], r["results"][0]["pfx"])
    assert name == "198.51.100.0/24" and parts[0]["value"] == "198.51.100.0/24"
    assert pfx[0]["share"] == pytest.approx(24_000_000 / 24_960_000)
    _, as_name = flowraw.decode(["src_as"], r["results"][1]["rows"][0]["key"], (24, 48), asn_name=lambda a: "EXEMPLO")
    assert as_name == "AS64500 EXEMPLO"
    _, cc = flowraw.decode(["src_cc"], r["results"][2]["rows"][0]["key"], (24, 48))
    assert cc.startswith("CN")
    _, sz = flowraw.decode(["pktsize", "sport"], r["results"][3]["rows"][0]["key"], (24, 48))
    assert sz == "257–512 B · 123 (NTP)"


def test_multi_groupby_ipv6_and_negation(base):
    r = run(base, [{"id": "x", "direction": "in", "group_by": ["src_as", "dst_ip"], "top": 3},
                   {"id": "v6", "direction": "in", "filters": [{"dim": "ip", "values": ["2001:db8::/32"]}], "group_by": ["dst_pfx"], "pfx6": 48},
                   {"id": "noudp", "direction": "in", "filters": [{"dim": "proto", "op": "not", "values": ["UDP"]}], "group_by": []}])
    x = r["results"][0]
    assert len(x["rows"]) == 3 and x["other"] is not None
    _, name = flowraw.decode(["src_as", "dst_ip"], x["rows"][0]["key"], (24, 48))
    assert name == "AS64500 · 203.0.113.10"
    _, v6 = flowraw.decode(["dst_pfx"], r["results"][1]["rows"][0]["key"], (24, 48))
    assert v6 == "2001:db8:ff::/48"
    assert r["results"][2]["total"]["bytes"] == 6_000_000 + 300_000 + 1_500_000 + 3_000_000


def test_direction_out_and_iface_dim(base):
    r = flowraw.run(base, start=T0, end=T0 + 60, ifaces=[TR], specs=[{"direction": "out", "group_by": ["src_ip"]},
                                                                      {"direction": "in", "group_by": ["iface"]}])
    up = r["results"][0]["rows"]
    assert len(up) == 1 and flowraw.decode(["src_ip"], up[0]["key"], (24, 48))[1] == "203.0.113.20"
    parts, _ = flowraw.decode(["iface"], r["results"][1]["rows"][0]["key"], (24, 48), gnames=r["results"][1]["gnames"],
                              if_label=lambda k: "Trânsito A")
    assert parts[0]["value"] == TR and parts[0]["label"] == "Trânsito A"


def test_row_cap_summarizes_source(base):
    with tempfile.TemporaryDirectory() as d:
        b = Path(d)
        flows = [(4, IP("198.51.100.0") + i, IP("203.0.113.10"), 17, 123, 0, 0, TR, None, 0, 0, 1000, 1) for i in range(200)]
        _minute(b, T0, flows, cap=50)
        r = flowraw.run(b, start=T0, end=T0 + 60, ifaces=[TR], specs=[{"direction": "in", "group_by": ["src_ip"], "top": 100}])
        assert r["coarse"] == 150
        assert r["results"][0]["total"]["bytes"] == 200_000


def test_errors_and_cleanup(base):
    with pytest.raises(flowraw.QueryError):
        run(base, [{"filters": [{"dim": "dst_ip", "values": ["não é ip"]}]}])
    with pytest.raises(flowraw.QueryError):
        flowraw.run(base, start=T0, end=T0 + 60 * (flowraw.MAX_MINUTES + 5), ifaces=[TR], specs=[{}])
    u = flowraw.disk_usage(base)
    assert u["files"] == 2 and u["oldest"] == T0
    assert flowraw.cleanup(base, days=1, max_gb=30, now=T0 + 3 * 86400) == 2
    assert flowraw.disk_usage(base)["files"] == 0


def test_signature_ntp():
    s = flowraw.signature("203.0.113.10", [(17, 0.98)], [(123, 0.95)], [(0, 0.9)], 480.0, [],
                          [("198.51.100.0/24", 0.7), ("192.0.2.0/24", 0.2)], 2)
    assert s["kind"] == "Amplificação NTP"
    assert any("porta de origem 123" in r for r in s["rules"])
    assert any("198.51.100.0/24" in r for r in s["rules"])
