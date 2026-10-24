"""Sondas ativas: leitura da saída do jump e avaliação (python -m pytest backend/tests/test_probes.py)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import probes as pr  # noqa: E402

PING = """PING 198.51.100.8 (198.51.100.8) 56(84) bytes of data.
64 bytes from 198.51.100.8: icmp_seq=1 ttl=117 time=12.3 ms
64 bytes from 198.51.100.8: icmp_seq=2 ttl=117 time=11.9 ms

--- 198.51.100.8 ping statistics ---
3 packets transmitted, 2 received, 33.3333% packet loss, time 1002ms
rtt min/avg/max/mdev = 11.9/12.1/12.3/0.2 ms"""
DIG = """;; ->>HEADER<<- opcode: QUERY, status: SERVFAIL, id: 1
;; flags: qr rd ra; QUERY: 1, ANSWER: 0, AUTHORITY: 0, ADDITIONAL: 1
;; Query time: 14 msec"""


def test_parse_and_evaluate():
    p = pr.clean_probe({"type": "ping", "target": "198.51.100.8", "count": 3})
    r = pr.parse_ping(PING, 3)
    assert r["ms"] == 12.1 and r["loss"] == 33.3 and r["samples"] == [12.3, 11.9]
    assert pr.evaluate(p, r) == (True, "perda de 33.3% (limite 20%)")
    d = pr.clean_probe({"type": "dns", "target": "192.0.2.53"})
    assert pr.parse_agent_block(d, DIG)["error"] == "DNS SERVFAIL"
    assert pr.parse_agent_block(d, "DNS 0 1 4.5")["ok"]
    h = pr.clean_probe({"type": "http", "target": "https://www.exemplo.net/", "contains": "ok"})
    assert pr.parse_agent_block(h, "CURL 200 0.120 1") == {"ok": True, "ms": 120.0, "code": 200, "error": ""}
    assert pr.parse_agent_block(h, "CURL 503 0.1 0")["error"] == "HTTP 503"
    t = pr.clean_probe({"type": "tcp", "target": "192.0.2.1", "port": 443})
    assert pr.parse_agent_block(t, "TCP 0 7")["ms"] == 7.0
    assert pr.parse_agent_block(p, "bash: ping: command not found")["skipped"]
    out = pr.parse_agent_output("@@B a\n" + PING + "\n@@E a\n", [("a", p), ("b", t)])
    assert out["a"]["ok"] and out["b"]["skipped"]


def test_tracker():
    p = {"fail_count": 3, "recover_count": 2}
    tr = pr.Tracker()
    assert [tr.feed("k", p, True, "x") for _ in range(3)] == [None, None, "open"]
    assert tr.feed("k", p, None, "") is None              # sem medir não muda nada
    assert [tr.feed("k", p, False, "") for _ in range(2)] == [None, "ok"]
