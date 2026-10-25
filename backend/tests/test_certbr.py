import ipaddress
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import certbr as cb  # noqa: E402

NOTICE = """Prezado(a),
Recebemos notificações de que os IPs abaixo estão com servidor DNS recursivo aberto.
IP                Timestamp (UTC)
177.10.1.5        2026-10-20 10:00:00
177.10.1.6        2026-10-20 10:05:00
Também: 177.10.2.9 respondendo SSDP (1900/udp)
Contato: 10.0.0.1 2001:db8::1 2801:80:1::5
"""


def test_notice():
    r = cb.parse_notice(NOTICE, "[CERT.br] Servidor DNS recursivo aberto")
    got = {(x["ip"], x["service"]) for x in r}
    assert ("177.10.1.5", "dns") in got and ("177.10.1.6", "dns") in got and ("177.10.2.9", "ssdp") in got, got
    assert not any(x["ip"] == "10.0.0.1" for x in r)
    assert ("2801:80:1::5", "dns") in got
    assert cb.detect_service("Notificação de NTP com monlist") == "ntp" and cb.detect_service("porta 11211/udp") == "memcached"


def flow(src, dst, sport, dport, proto=17):
    f = [None] * 15
    f[3], f[4], f[5], f[6], f[7], f[8], f[10], f[11] = int(ipaddress.ip_address(src)), int(ipaddress.ip_address(dst)), 4, proto, sport, dport, 500, 1
    return tuple(f)


def test_tracker():
    t = cb.FlowTracker(); t.configure(True, ["177.10.0.0/16"], ["177.10.9.9"])
    t.add(flow("177.10.1.5", "8.8.8.8", 53, 40000))         # responde DNS para fora
    t.add(flow("177.10.1.5", "8.8.4.4", 53, 80))
    t.add(flow("177.10.1.6", "9.9.9.9", 123, 123))          # cliente NTP (dport 123): não conta
    t.add(flow("177.10.1.7", "177.10.1.8", 53, 40000))      # interno
    t.add(flow("177.10.9.9", "8.8.8.8", 53, 40000))         # ignorado (DNS autoritativo)
    t.add(flow("177.10.1.5", "8.8.8.8", 53, 40000, 6))      # TCP: não é refletor
    out = t.flush()
    assert len(out) == 1 and out[0]["ip"] == "177.10.1.5" and out[0]["service"] == "dns" and out[0]["n_remotes"] == 2
