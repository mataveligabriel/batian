"""Sondas ativas (estilo SmokePing): ping, DNS, HTTP(S) e TCP medidos a partir do servidor do BastiON e de cada
jump server (agente).

- Do servidor: ICMP por socket raw (o backend roda como root na rede do host), DNS montando a consulta na mão,
  HTTP com httpx e TCP com connect().
- Do jump: um único script bash por ciclo com todas as sondas daquele agente rodando em paralelo (ping do sistema,
  dig/python3 para DNS, curl/python3 para HTTP, /dev/tcp para TCP). Vai pela conexão SSH compartilhada do agente
  (JUMP_POOL) — não gera um login por sonda.
- Avaliação: N ciclos ruins seguidos (perda acima do limite, latência acima do limite ou falha) abrem incidente;
  M ciclos bons seguidos resolvem.
"""
import asyncio
import ipaddress
import os
import random
import re
import shlex
import socket
import struct
import time
from typing import Optional

TYPES = ("ping", "dns", "http", "tcp")
TYPE_LABEL = {"ping": "Ping", "dns": "DNS", "http": "HTTP(S)", "tcp": "Porta TCP"}
DEFAULTS = {"enabled": True, "retention_days": 14}
QTYPES = {"A": 1, "AAAA": 28, "MX": 15, "NS": 2, "TXT": 16, "SOA": 6}
RCODES = {0: "NOERROR", 1: "FORMERR", 2: "SERVFAIL", 3: "NXDOMAIN", 4: "NOTIMP", 5: "REFUSED"}

PRESETS = [
    {"name": "DNS público Google", "type": "dns", "target": "8.8.8.8", "query": "google.com", "qtype": "A",
     "group": "DNS", "max_ms": 200},
    {"name": "DNS público Cloudflare", "type": "dns", "target": "1.1.1.1", "query": "google.com", "qtype": "A",
     "group": "DNS", "max_ms": 200},
    {"name": "Trânsito — Google", "type": "ping", "target": "8.8.8.8", "group": "Trânsito", "max_loss": 10, "max_ms": 120},
    {"name": "Trânsito — Cloudflare", "type": "ping", "target": "1.1.1.1", "group": "Trânsito", "max_loss": 10, "max_ms": 120},
    {"name": "HTTP — google.com", "type": "http", "target": "https://www.google.com/", "group": "HTTP", "max_ms": 3000},
]


# ---------------------------------------------------------------- cadastro
def _int(v, lo, hi, default):
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return default


def clean_probe(b: dict) -> dict:
    t = b.get("type") if b.get("type") in TYPES else "ping"
    target = str(b.get("target") or "").strip()[:300]
    if not target:
        raise ValueError("Informe o alvo")
    if t == "http":
        if not re.match(r"^https?://[^\s/]+", target):
            raise ValueError("URL inválida (use http:// ou https://)")
    else:
        if not re.fullmatch(r"[A-Za-z0-9.:\-_]+", target):
            raise ValueError("Alvo inválido (IP ou nome)")
    r = {
        "name": str(b.get("name") or "").strip()[:80] or f"{TYPE_LABEL[t]} {target}",
        "type": t, "target": target, "group": str(b.get("group") or "").strip()[:40],
        "enabled": bool(b.get("enabled", True)),
        "sources": [str(s) for s in (b.get("sources") or ["server"])][:50] or ["server"],
        "interval_s": _int(b.get("interval_s"), 30, 3600, 60),
        "count": _int(b.get("count"), 1, 30, 10),
        "timeout_s": _int(b.get("timeout_s"), 1, 30, 5),
        "max_loss": _int(b.get("max_loss"), 0, 100, 20),
        "max_ms": _int(b.get("max_ms"), 0, 60000, 0),
        "fail_count": _int(b.get("fail_count"), 1, 60, 3),
        "recover_count": _int(b.get("recover_count"), 1, 60, 2),
        "severity": b.get("severity") if b.get("severity") in ("info", "low", "medium", "high", "critical") else "high",
        "alert": bool(b.get("alert", True)),
        "query": str(b.get("query") or "").strip()[:200],
        "qtype": b.get("qtype") if b.get("qtype") in QTYPES else "A",
        "port": _int(b.get("port"), 0, 65535, 0),
        "expect_status": _int(b.get("expect_status"), 0, 599, 0),
        "contains": str(b.get("contains") or "")[:200],
        "insecure": bool(b.get("insecure", False)),
    }
    if t == "dns" and not r["query"]:
        r["query"] = "google.com"
    if t == "dns" and not re.fullmatch(r"[A-Za-z0-9.\-_]+", r["query"]):
        raise ValueError("Nome consultado inválido")
    if t == "tcp" and not r["port"]:
        raise ValueError("Informe a porta TCP")
    return r


# ---------------------------------------------------------------- estatística / avaliação
def median(v):
    s = sorted(v)
    n = len(s)
    if not n:
        return None
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def ping_result(sent: int, rtts: list, error: str = "") -> dict:
    rtts = [round(float(x), 2) for x in rtts][:sent or len(rtts)]
    recv = len(rtts)
    loss = round(100.0 * (sent - recv) / sent, 1) if sent else 100.0
    jit = round(sum(abs(a - b) for a, b in zip(rtts, rtts[1:])) / (len(rtts) - 1), 2) if len(rtts) > 1 else 0.0
    med = median(rtts)
    return {"ok": recv > 0, "ms": round(med, 2) if med is not None else None, "loss": max(0.0, loss),
            "min": min(rtts) if rtts else None, "max": max(rtts) if rtts else None, "jitter": jit,
            "samples": rtts, "sent": sent, "error": error if not recv else ""}


def evaluate(p: dict, r: dict) -> tuple:
    """-> (ruim?, motivo)"""
    if r.get("skipped"):
        return None, r.get("error", "")
    if not r.get("ok"):
        if p["type"] == "ping":
            return True, "sem resposta (100% de perda)"
        return True, r.get("error") or "falhou"
    if p["type"] == "ping" and r.get("loss", 0) > p.get("max_loss", 20):
        return True, f"perda de {r['loss']:g}% (limite {p['max_loss']}%)"
    if p.get("max_ms") and r.get("ms") is not None and r["ms"] > p["max_ms"]:
        return True, f"{'latência' if p['type'] == 'ping' else 'tempo'} {r['ms']:.0f} ms (limite {p['max_ms']} ms)"
    return False, ""


class Tracker:
    """Ciclos ruins/bons seguidos por (sonda, origem) -> ações open / ok."""

    def __init__(self):
        self.st: dict = {}

    def restore(self, key: str):
        self.st[key] = {"bad": 0, "good": 0, "open": True, "reason": ""}

    def feed(self, key: str, p: dict, bad: Optional[bool], reason: str) -> Optional[str]:
        s = self.st.setdefault(key, {"bad": 0, "good": 0, "open": False, "reason": ""})
        if bad is None:
            return None
        if bad:
            s["bad"] += 1
            s["good"] = 0
            s["reason"] = reason
            if not s["open"] and s["bad"] >= p.get("fail_count", 3):
                s["open"] = True
                return "open"
        else:
            s["good"] += 1
            s["bad"] = 0
            if s["open"] and s["good"] >= p.get("recover_count", 2):
                s["open"] = False
                return "ok"
        return None

    def state(self, key: str) -> str:
        s = self.st.get(key)
        if not s:
            return "unknown"
        return "down" if s["open"] else ("warn" if s["bad"] else "ok")


# ---------------------------------------------------------------- ICMP local (socket raw)
def _cksum(b: bytes) -> int:
    if len(b) % 2:
        b += b"\x00"
    s = sum(struct.unpack(f"!{len(b) // 2}H", b))
    s = (s >> 16) + (s & 0xFFFF)
    s += s >> 16
    return ~s & 0xFFFF


class Pinger:
    """Um socket raw por família; respostas casadas por (id, seq)."""

    def __init__(self):
        self.ident = os.getpid() & 0xFFFF
        self.seq = random.randint(0, 0xFFFF)
        self.wait: dict = {}
        self.socks: dict = {}

    def _sock(self, fam):
        s = self.socks.get(fam)
        if s:
            return s
        if fam == socket.AF_INET:
            s = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP)
        else:
            s = socket.socket(socket.AF_INET6, socket.SOCK_RAW, socket.IPPROTO_ICMPV6)
        s.setblocking(False)
        asyncio.get_running_loop().add_reader(s.fileno(), self._read, s, fam)
        self.socks[fam] = s
        return s

    def _read(self, s, fam):
        while True:
            try:
                data, _ = s.recvfrom(4096)
            except (BlockingIOError, InterruptedError):
                return
            except OSError:
                return
            if fam == socket.AF_INET:
                ihl = (data[0] & 0x0F) * 4
                icmp = data[ihl:]
                if len(icmp) < 8 or icmp[0] != 0:          # echo reply
                    continue
            else:
                icmp = data
                if len(icmp) < 8 or icmp[0] != 129:
                    continue
            ident, seq = struct.unpack("!HH", icmp[4:8])
            fut = self.wait.pop((ident, seq), None)
            if fut and not fut.done():
                fut.set_result(time.perf_counter())

    async def one(self, addr: str, fam, timeout: float) -> Optional[float]:
        s = self._sock(fam)
        self.seq = (self.seq + 1) & 0xFFFF
        seq = self.seq
        payload = struct.pack("!d", time.time()) + b"BastiON-probe" + b"\x00" * 19
        typ = 8 if fam == socket.AF_INET else 128
        hdr = struct.pack("!BBHHH", typ, 0, 0, self.ident, seq)
        ck = _cksum(hdr + payload) if fam == socket.AF_INET else 0   # ICMPv6: o kernel calcula
        pkt = struct.pack("!BBHHH", typ, 0, ck, self.ident, seq) + payload
        fut = asyncio.get_running_loop().create_future()
        self.wait[(self.ident, seq)] = fut
        t0 = time.perf_counter()
        try:
            s.sendto(pkt, (addr, 0) if fam == socket.AF_INET else (addr, 0, 0, 0))
            t1 = await asyncio.wait_for(fut, timeout)
            return (t1 - t0) * 1000.0
        except (asyncio.TimeoutError, OSError):
            return None
        finally:
            self.wait.pop((self.ident, seq), None)


PINGER = Pinger()


async def _resolve(host: str) -> tuple:
    try:
        ip = ipaddress.ip_address(host)
        return str(ip), socket.AF_INET if ip.version == 4 else socket.AF_INET6
    except ValueError:
        pass
    infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_DGRAM)
    for fam in (socket.AF_INET, socket.AF_INET6):
        for i in infos:
            if i[0] == fam:
                return i[4][0], fam
    raise OSError("sem endereço")


async def ping_local(target: str, count: int = 10, timeout: float = 2.0, spacing: float = 0.2) -> dict:
    try:
        addr, fam = await _resolve(target)
    except OSError as e:
        return ping_result(count, [], f"não resolveu {target} ({e})")
    try:
        PINGER._sock(fam)
    except OSError as e:
        return ping_result(count, [], f"ICMP{'v6' if fam == socket.AF_INET6 else ''} indisponível no servidor ({e.strerror or e})")
    tasks = []
    for i in range(count):
        tasks.append(asyncio.create_task(PINGER.one(addr, fam, timeout)))
        if i < count - 1:
            await asyncio.sleep(spacing)
    res = await asyncio.gather(*tasks)
    return ping_result(count, [r for r in res if r is not None], "" if any(r is not None for r in res) else "sem resposta")


# ---------------------------------------------------------------- DNS local
def dns_query(qname: str, qtype: str = "A") -> tuple:
    qid = random.randint(0, 0xFFFF)
    q = b"".join(bytes([len(p)]) + p.encode() for p in qname.strip(".").split(".") if p) + b"\x00"
    return qid, struct.pack("!HHHHHH", qid, 0x0100, 1, 0, 0, 0) + q + struct.pack("!HH", QTYPES.get(qtype, 1), 1)


def dns_parse(data: bytes, qid: int) -> tuple:
    """-> (rcode_nome, respostas)"""
    if len(data) < 12:
        raise ValueError("resposta curta")
    rid, flags, _qd, an, _ns, _ar = struct.unpack("!HHHHHH", data[:12])
    if rid != qid:
        raise ValueError("id diferente")
    return RCODES.get(flags & 0x0F, str(flags & 0x0F)), an


async def dns_local(server: str, qname: str, qtype: str = "A", timeout: float = 3.0) -> dict:
    loop = asyncio.get_running_loop()
    try:
        addr, fam = await _resolve(server)
    except OSError as e:
        return {"ok": False, "ms": None, "error": f"servidor inválido ({e})"}
    qid, pkt = dns_query(qname, qtype)
    fut = loop.create_future()

    class P(asyncio.DatagramProtocol):
        def datagram_received(self, data, a):
            if not fut.done():
                fut.set_result(data)

        def error_received(self, exc):
            if not fut.done():
                fut.set_exception(exc)

    tr, _ = await loop.create_datagram_endpoint(P, remote_addr=(addr, 53), family=fam)
    t0 = time.perf_counter()
    try:
        tr.sendto(pkt)
        data = await asyncio.wait_for(fut, timeout)
        ms = round((time.perf_counter() - t0) * 1000, 2)
        rc, an = dns_parse(data, qid)
        ok = rc == "NOERROR" and an > 0
        return {"ok": ok, "ms": ms, "code": rc, "answers": an,
                "error": "" if ok else (f"DNS {rc}" if rc != "NOERROR" else "DNS sem resposta (0 registros)")}
    except asyncio.TimeoutError:
        return {"ok": False, "ms": None, "code": "TIMEOUT", "error": f"DNS sem resposta em {timeout:g}s"}
    except (OSError, ValueError) as e:
        return {"ok": False, "ms": None, "error": f"DNS: {e}"}
    finally:
        tr.close()


# ---------------------------------------------------------------- HTTP / TCP local
async def http_local(p: dict) -> dict:
    import httpx
    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=p.get("timeout_s", 5), verify=not p.get("insecure"),
                                     follow_redirects=True, headers={"User-Agent": "BastiON-probe"}) as c:
            r = await c.get(p["target"])
            body = r.text if p.get("contains") else ""
        ms = round((time.perf_counter() - t0) * 1000, 1)
        return _http_eval(p, r.status_code, ms, body)
    except httpx.TimeoutException:
        return {"ok": False, "ms": None, "error": f"HTTP sem resposta em {p.get('timeout_s', 5)}s"}
    except Exception as e:
        return {"ok": False, "ms": None, "error": f"HTTP: {str(e)[:160] or e.__class__.__name__}"}


def _http_eval(p, code: int, ms, body: str = "", has_body: bool = True) -> dict:
    want = p.get("expect_status") or 0
    ok = (code == want) if want else (200 <= code < 400)
    err = "" if ok else f"HTTP {code}"
    if ok and p.get("contains") and has_body and p["contains"] not in body:
        ok, err = False, f"resposta sem \"{p['contains'][:40]}\""
    return {"ok": ok, "ms": ms, "code": code, "error": err}


async def tcp_local(host: str, port: int, timeout: float = 5.0) -> dict:
    t0 = time.perf_counter()
    try:
        _, w = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
        ms = round((time.perf_counter() - t0) * 1000, 2)
        w.close()
        return {"ok": True, "ms": ms, "error": ""}
    except asyncio.TimeoutError:
        return {"ok": False, "ms": None, "error": f"porta {port} sem resposta em {timeout:g}s"}
    except OSError as e:
        return {"ok": False, "ms": None, "error": f"porta {port}: {e.strerror or e}"}


async def run_local(p: dict) -> dict:
    if p["type"] == "ping":
        return await ping_local(p["target"], p.get("count", 10), min(5.0, float(p.get("timeout_s", 2))))
    if p["type"] == "dns":
        return await dns_local(p["target"], p.get("query") or "google.com", p.get("qtype", "A"), float(p.get("timeout_s", 3)))
    if p["type"] == "http":
        return await http_local(p)
    return await tcp_local(p["target"], int(p.get("port") or 0), float(p.get("timeout_s", 5)))


# ---------------------------------------------------------------- jump server (script bash)
_PY_DNS = (
    "import socket,struct,random,sys,time\n"
    "s,n,t,to=sys.argv[1],sys.argv[2],int(sys.argv[3]),float(sys.argv[4])\n"
    "i=random.randint(0,65535);q=b''.join(bytes([len(x)])+x.encode() for x in n.strip('.').split('.') if x)+b'\\0'\n"
    "f=socket.AF_INET6 if ':' in s else socket.AF_INET;k=socket.socket(f,socket.SOCK_DGRAM);k.settimeout(to)\n"
    "a=time.time()\n"
    "try:\n k.sendto(struct.pack('!HHHHHH',i,256,1,0,0,0)+q+struct.pack('!HH',t,1),(s,53));d=k.recv(4096)\n"
    " print('DNS',d[3]&15,struct.unpack('!H',d[6:8])[0],round((time.time()-a)*1000,2))\n"
    "except Exception as e: print('DNSERR',e)\n")
_PY_HTTP = (
    "import sys,time,ssl,urllib.request\n"
    "u,to,ins,c=sys.argv[1],float(sys.argv[2]),sys.argv[3]=='1',sys.argv[4]\n"
    "x=ssl._create_unverified_context() if ins else None;a=time.time()\n"
    "try:\n r=urllib.request.urlopen(urllib.request.Request(u,headers={'User-Agent':'BastiON-probe'}),timeout=to,context=x);b=r.read(1000000).decode('utf-8','replace');code=r.status\n"
    "except urllib.error.HTTPError as e: code=e.code;b=''\n"
    "except Exception as e: print('HTTPERR',e);sys.exit()\n"
    "print('HTTP',code,round((time.time()-a)*1000,1),1 if (c and c in b) else 0)\n")


def _q(s) -> str:
    return shlex.quote(str(s))


def agent_script(items: list) -> str:
    """items: [(chave, sonda)] -> script bash que imprime @@B chave / saída / @@E chave para cada uma."""
    out = ['D=$(mktemp -d 2>/dev/null || echo /tmp/bprobe.$$); mkdir -p "$D"',
           'OS=$(uname -s 2>/dev/null); W=2; [ "$OS" = Darwin ] && W=2000',
           'ms() { if [ "$OS" = Darwin ]; then python3 -c "import time;print(int(time.time()*1000))" 2>/dev/null || echo $(( $(date +%s) * 1000 )); else echo $(( $(date +%s%N) / 1000000 )); fi; }']
    for i, (key, p) in enumerate(items):
        f = f'"$D/{i}"'
        t = int(p.get("timeout_s", 5))
        if p["type"] == "ping":
            tgt = _q(p["target"])
            out.append(f'( ping -n -c {int(p.get("count", 10))} -i 0.5 -W $W {tgt} > {f} 2>&1 ) &')
        elif p["type"] == "dns":
            qt = QTYPES.get(p.get("qtype", "A"), 1)
            out.append(f'( if command -v dig >/dev/null 2>&1; then dig +tries=1 +time={t} @{_q(p["target"])} {_q(p.get("query") or "google.com")} {_q(p.get("qtype", "A"))} > {f} 2>&1; '
                       f'elif command -v python3 >/dev/null 2>&1; then python3 -c {_q(_PY_DNS)} {_q(p["target"])} {_q(p.get("query") or "google.com")} {qt} {t} > {f} 2>&1; '
                       f'else echo NOTOOL > {f}; fi ) &')
        elif p["type"] == "http":
            k = "-k " if p.get("insecure") else ""
            if p.get("contains"):
                grep = f'grep -qF -- {_q(p["contains"])} "$D/{i}.b" && C=1 || C=0'
            else:
                grep = "C=0"
            out.append(f'( if command -v curl >/dev/null 2>&1; then R=$(curl -sS {k}-L -A BastiON-probe -o "$D/{i}.b" -m {t} -w "%{{http_code}} %{{time_total}}" {_q(p["target"])} 2>&1); '
                       f'{grep}; echo "CURL $R $C" > {f}; '
                       f'elif command -v python3 >/dev/null 2>&1; then python3 -c {_q(_PY_HTTP)} {_q(p["target"])} {t} {1 if p.get("insecure") else 0} {_q(p.get("contains", ""))} > {f} 2>&1; '
                       f'else echo NOTOOL > {f}; fi ) &')
        else:
            h, port = _q(p["target"]), int(p.get("port") or 0)
            out.append(f'( A=$(ms); if timeout {t} bash -c "exec 3<>/dev/tcp/"{h}"/{port}" 2>/dev/null; then B=$(ms); echo "TCP 0 $((B-A))" > {f}; '
                       f'else echo "TCP 1" > {f}; fi ) &')
    out.append("wait")
    for i, (key, _p) in enumerate(items):
        out.append(f'echo "@@B {key}"; cat "$D/{i}" 2>/dev/null; echo "@@E {key}"')
    out.append('rm -rf "$D" 2>/dev/null; true')
    return "\n".join(out)


_RTT = re.compile(r"time[=<]\s*([\d.]+)\s*ms")
_SENT = re.compile(r"(\d+) packets transmitted, (\d+) (?:packets )?received")


def parse_ping(text: str, count: int) -> dict:
    m = _SENT.search(text)
    rtts = [float(x) for x in _RTT.findall(text)]
    sent = int(m.group(1)) if m else count
    if not m and not rtts:
        err = text.strip().splitlines()[-1][:160] if text.strip() else "ping não rodou no jump"
        r = ping_result(count, [], err)
        if "not found" in text or "Operation not permitted" in text or "permission denied" in text.lower():
            r.update(skipped=True, error=f"jump sem ping utilizável ({err})")
        return r
    return ping_result(sent, rtts[:sent], "sem resposta")


def parse_agent_block(p: dict, text: str) -> dict:
    t = text.strip()
    if t == "NOTOOL":
        need = {"dns": "dig ou python3", "http": "curl ou python3"}.get(p["type"], "")
        return {"ok": False, "ms": None, "skipped": True, "error": f"jump sem {need} para esta sonda"}
    if p["type"] == "ping":
        return parse_ping(text, int(p.get("count", 10)))
    if p["type"] == "dns":
        m = re.search(r"status: (\w+).*?ANSWER: (\d+)", t, re.S)
        if m:
            qt = re.search(r"Query time: (\d+) msec", t)
            ms = float(qt.group(1)) if qt else None
            rc, an = m.group(1), int(m.group(2))
            ok = rc == "NOERROR" and an > 0
            return {"ok": ok, "ms": ms, "code": rc, "answers": an,
                    "error": "" if ok else (f"DNS {rc}" if rc != "NOERROR" else "DNS sem resposta (0 registros)")}
        m = re.search(r"^DNS (\d+) (\d+) ([\d.]+)", t, re.M)
        if m:
            rc, an = RCODES.get(int(m.group(1)), m.group(1)), int(m.group(2))
            ok = rc == "NOERROR" and an > 0
            return {"ok": ok, "ms": float(m.group(3)), "code": rc, "answers": an,
                    "error": "" if ok else (f"DNS {rc}" if rc != "NOERROR" else "DNS sem resposta (0 registros)")}
        if "timed out" in t or "no servers could be reached" in t or "timeout" in t.lower():
            return {"ok": False, "ms": None, "code": "TIMEOUT", "error": "DNS sem resposta"}
        return {"ok": False, "ms": None, "error": ("DNS: " + (t.splitlines()[-1] if t else "sem saída"))[:160]}
    if p["type"] == "http":
        m = re.search(r"^CURL (\d{3}) ([\d.]+) ([01])", t, re.M)
        if m:
            code = int(m.group(1))
            if code == 0:
                return {"ok": False, "ms": None, "error": "HTTP sem resposta"}
            r = _http_eval(p, code, round(float(m.group(2)) * 1000, 1), has_body=False)
            if r["ok"] and p.get("contains") and m.group(3) != "1":
                r.update(ok=False, error=f"resposta sem \"{p['contains'][:40]}\"")
            return r
        m = re.search(r"^HTTP (\d{3}) ([\d.]+) ([01])", t, re.M)
        if m:
            r = _http_eval(p, int(m.group(1)), float(m.group(2)), has_body=False)
            if r["ok"] and p.get("contains") and m.group(3) != "1":
                r.update(ok=False, error=f"resposta sem \"{p['contains'][:40]}\"")
            return r
        m = re.search(r"CURL (.*)", t)
        msg = (m.group(1) if m else t.splitlines()[-1] if t else "sem saída")
        return {"ok": False, "ms": None, "error": ("HTTP: " + re.sub(r"^curl: \(\d+\)\s*", "", msg))[:160]}
    m = re.search(r"^TCP (\d)(?: (\d+))?", t, re.M)
    if m and m.group(1) == "0":
        return {"ok": True, "ms": float(m.group(2) or 0), "error": ""}
    return {"ok": False, "ms": None, "error": f"porta {p.get('port')} fechada ou sem resposta"}


def parse_agent_output(stdout: str, items: list) -> dict:
    blocks = {}
    for m in re.finditer(r"@@B (\S+)\n(.*?)@@E \1", stdout or "", re.S):
        blocks[m.group(1)] = m.group(2)
    out = {}
    for key, p in items:
        if key in blocks:
            out[key] = parse_agent_block(p, blocks[key])
        else:
            out[key] = {"ok": False, "ms": None, "skipped": True, "error": "jump não respondeu ao ciclo"}
    return out
