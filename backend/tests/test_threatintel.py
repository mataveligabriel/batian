import ipaddress
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import threatintel as ti  # noqa: E402


def ip(s):
    return int(ipaddress.ip_address(s))


def flow(src, dst, dport=443, b=1000, p=2, sport=50000, proto=6, flags=0):
    f = [None] * 15
    f[0], f[3], f[4], f[5], f[6], f[7], f[8], f[9], f[10], f[11] = "r1", ip(src), ip(dst), 4, proto, sport, dport, flags, b, p
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
                    "feeds": ["feodo"], "category": "c2", "ports": ["6/443", "6/8080"], "replied": 0}
    assert inn["category"] == "scanner" and inn["local_ip"] == "177.10.2.2"
    assert t.flush() == []


def test_replies_are_not_outbound():
    intel = ti.Intel({"spamhaus_drop": ["103.34.60.0/22"]}, {"spamhaus_drop": ti.FEED_BY_ID["spamhaus_drop"]})
    t = ti.Tracker(); t.configure(intel, ["177.10.0.0/16"])
    t.add(flow("103.34.60.244", "177.10.8.226", 23, sport=41000, flags=0x02))           # varredura entrando
    t.add(flow("177.10.8.226", "103.34.60.244", 41000, sport=23, flags=0x14))           # RST de volta
    t.add(flow("177.10.8.226", "103.34.61.172", 55000, sport=37215, flags=0x14))        # RST porta alta fechada
    t.add(flow("177.10.8.226", "103.34.62.44", 52000, sport=8291, flags=0x12))          # SYN-ACK do Winbox
    t.add(flow("177.10.8.226", "103.34.62.14", 0, sport=0, proto=1))                    # ICMP unreachable
    t.add(flow("177.10.8.226", "103.34.62.186", 33000, sport=53, proto=17))             # resposta DNS
    t.add(flow("177.10.8.5", "103.34.63.15", 443, sport=50123, flags=0x1a))             # iniciativa real (com dados)
    out = t.flush()
    outs = [h for h in out if h["dir"] == "out"]
    assert [h["bad_ip"] for h in outs] == ["103.34.63.15"]
    assert sum(h["replied"] for h in out) == 5
    assert ti.is_reply(6, 50000, 443, 0x12) is False         # SYN-ACK indo a serviço remoto = conexão do seu host


def test_out_threshold_and_blocklist():
    cfg = ti.clean_settings({"out_min_pkts": 10})
    assert ti.out_alertable({"category": "c2", "pkts": 1}, cfg)
    assert not ti.out_alertable({"category": "hijacked", "pkts": 3}, cfg)
    assert ti.out_alertable({"category": "hijacked", "pkts": 12}, cfg)
    intel = ti.Intel({"spamhaus_drop": ["103.34.60.0/22", "177.10.0.0/24"], "cins": ["5.6.7.8/32"]},
                     {"spamhaus_drop": ti.FEED_BY_ID["spamhaus_drop"], "cins": ti.FEED_BY_ID["cins"]}, ignore=["103.34.60.0/22"])
    intel2 = ti.Intel({"spamhaus_drop": ["103.34.60.0/22", "177.10.0.0/24", "2001:db8:dead::/48"], "cins": ["5.6.7.8/32"]},
                      {"spamhaus_drop": ti.FEED_BY_ID["spamhaus_drop"], "cins": ti.FEED_BY_ID["cins"]})
    assert ti.blocklist(intel, ["hijacked"], ["177.10.0.0/16"]) == []
    nets = ti.blocklist(intel2, ["hijacked"], ["177.10.0.0/16"])
    assert nets == ["103.34.60.0/22", "2001:db8:dead::/48"]
    ros = ti.render_blocklist(nets, "routeros")
    assert "add list=bastion-ti address=103.34.60.0/22 timeout=2d" in ros and "/ipv6 firewall address-list" in ros
    assert "set policy-options prefix-list BASTION_TI 103.34.60.0/22" in ti.render_blocklist(nets, "juniper")
    assert " 103.34.60.0 255.255.252.0" in ti.render_blocklist(nets, "cisco")
