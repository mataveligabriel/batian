import asyncio
import json
import os
import socket
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import siem  # noqa: E402


def test_formats():
    e = siem.event("command", "block", "failure", 8, user="op@x", device="BORDA|01", device_ip="192.0.2.1",
                   message="bloqueado a=b", commands=["reset saved-configuration"])
    c = siem.to_cef(e)
    assert c.startswith("CEF:0|BastiON|BastiON|") and "|command:block|" in c
    assert "suser=op@x" in c and "msg=bloqueado a\\=b" in c and "cs1Label=commands" in c
    j = json.loads(siem.to_json(e)); assert j["category"] == "command" and j["details"]["commands"]
    g = json.loads(siem.to_gelf(e)); assert g["_device"] == "BORDA|01" and g["level"] <= 2
    assert siem.to_leef(e).startswith("LEEF:2.0|BastiON|")
    ln = siem.syslog_line(e, "cef"); assert ln.startswith("<163>1 ") and " bastion - command - CEF:0" in ln
    ev = siem.from_login({"ok": False, "reason": "bloqueado", "email": "a@x", "ip": "203.0.113.9", "at": siem.now_iso()})
    assert ev["severity"] == 7 and ev["outcome"] == "failure"


def test_udp_tcp():
    async def go():
        u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); u.bind(("127.0.0.1", 0)); u.settimeout(2)
        got = []

        async def handle(r, w):
            got.append(await r.readline()); w.close()
        srv = await asyncio.start_server(handle, "127.0.0.1", 0)
        tport = srv.sockets[0].getsockname()[1]
        s1 = siem.Sender(siem.clean_dest({"id": "a", "kind": "syslog", "host": "127.0.0.1", "port": u.getsockname()[1], "proto": "udp", "format": "json"}))
        s2 = siem.Sender(siem.clean_dest({"id": "b", "kind": "syslog", "host": "127.0.0.1", "port": tport, "proto": "tcp", "format": "cef"}))
        for s in (s1, s2):
            s.push(siem.sample_events()[0]); await s.flush()
        await asyncio.sleep(0.2)
        data = u.recv(65000).decode()
        srv.close()
        assert '"category": "auth"' in data and s1.sent == 1
        assert got and b"CEF:0" in got[0] and s2.sent == 1
        bad = siem.Sender(siem.clean_dest({"id": "c", "kind": "syslog", "host": "127.0.0.1", "port": 1, "proto": "tcp"}))
        bad.push(siem.sample_events()[0]); await bad.flush()
        assert bad.failed == 1 and len(bad.q) == 1 and bad.last_error
    asyncio.run(go())
