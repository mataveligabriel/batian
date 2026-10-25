import ipaddress
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import threatintel as ti  # noqa: E402


def ip(s):
    return int(ipaddress.ip_address(s))


def flow(src, dst, dport=443, b=1000, p=2):
    f = [None] * 15
    f[0], f[3], f[4], f[5], f[6], f[7], f[8], f[10], f[11] = "r1", ip(src), ip(dst), 4, 6, 50000, dport, b, p
    return tuple(f)


def test_parse():
    txt = """# Feodo
45.10.10.10
45.10.10.11:443
{"cidr":"46.20.0.0/22","sblid":"SBL1","rir":"ripencc"}
10.0.0.0/8
1.2.3.0\t1.2.3.255\t24\t123\tACME
5.6.7.8 ; scanner
"""
    assert ti.parse(txt) == ["45.10.10.10/32", "45.10.10.11/32", "46.20.0.0/22", "1.2.3.0/24", "5.6.7.8/32"]
    assert ti.unpack(ti.pack(["1.1.1.1/32"])) == ["1.1.1.1/32"]
    c = ti.clean_settings({"custom": [{"url": "https://x.example/list.txt", "category": "c2"}], "manual": ["45.10.10.12"]})
    assert c["custom"][0]["category"] == "c2" and c["manual"] == ["45.10.10.12/32"]


def test_tracker():
    intel = ti.Intel({"feodo": ["45.10.10.10/32"], "cins": ["46.20.0.0/22"]},
                     {"feodo": ti.FEED_BY_ID["feodo"], "cins": ti.FEED_BY_ID["cins"]}, manual=["47.1.1.1"], ignore=["46.20.1.0/24"])
    assert [x["id"] for x in intel.lookup("45.10.10.10")] == ["feodo"] and intel.lookup("46.20.1.5") == []
    t = ti.Tracker(); t.configure(intel, ["177.10.0.0/16"])
    t.add(flow("177.10.1.5", "45.10.10.10", 443))          # cliente -> C2
    t.add(flow("177.10.1.5", "45.10.10.10", 8080))
    t.add(flow("46.20.0.9", "177.10.2.2", 22))              # scanner -> você
    t.add(flow("46.20.0.9", "8.8.8.8", 22))                 # não é seu
    t.add(flow("177.10.1.6", "1.1.1.1", 53))                # limpo
    out = sorted(t.flush(), key=lambda h: h["dir"])
    assert len(out) == 2
    inn, outb = out
    assert outb == {"bad_ip": "45.10.10.10", "local_ip": "177.10.1.5", "dir": "out", "bytes": 2000, "pkts": 4, "flows": 2,
                    "feeds": ["feodo"], "category": "c2", "ports": ["6/443", "6/8080"]}
    assert inn["category"] == "scanner" and inn["local_ip"] == "177.10.2.2"
    assert t.flush() == []
