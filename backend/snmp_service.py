"""Cliente SNMP v2c mínimo (sem dependências): GET e WALK (GETBULK) via UDP.

Equipamentos atrás de agente: SNMP é UDP e não atravessa o túnel SSH, então rodamos
`snmpget` / `snmpbulkwalk` (pacote net-snmp: `apt install snmp`) no próprio agente via SSH.
"""
import asyncio
import os
import re
import shlex
from typing import Dict, List, Optional, Tuple, Union

# ---------- OIDs usados ----------
SYS_DESCR = "1.3.6.1.2.1.1.1.0"
SYS_UPTIME = "1.3.6.1.2.1.1.3.0"
SYS_NAME = "1.3.6.1.2.1.1.5.0"
IF_DESCR = "1.3.6.1.2.1.2.2.1.2"
IF_ADMIN = "1.3.6.1.2.1.2.2.1.7"
IF_OPER = "1.3.6.1.2.1.2.2.1.8"
IF_NAME = "1.3.6.1.2.1.31.1.1.1.1"
IF_HC_IN = "1.3.6.1.2.1.31.1.1.1.6"
IF_HC_OUT = "1.3.6.1.2.1.31.1.1.1.10"
IF_HIGH_SPEED = "1.3.6.1.2.1.31.1.1.1.15"
IF_ALIAS = "1.3.6.1.2.1.31.1.1.1.18"

OPER_STATUS = {1: "up", 2: "down", 3: "testing", 4: "unknown", 5: "dormant", 6: "notPresent", 7: "lowerLayerDown"}


class SnmpError(Exception):
    pass


class NoSuch:
    """noSuchObject / noSuchInstance / endOfMibView."""
    def __init__(self, kind: str):
        self.kind = kind

    def __repr__(self):
        return f"<{self.kind}>"

    def __eq__(self, other):
        return isinstance(other, NoSuch) and other.kind == self.kind


Value = Union[int, str, bytes, None, NoSuch]


# ---------- BER ----------
def _enc_len(n: int) -> bytes:
    if n < 0x80:
        return bytes([n])
    b = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(b)]) + b


def _tlv(tag: int, payload: bytes) -> bytes:
    return bytes([tag]) + _enc_len(len(payload)) + payload


def _enc_int(v: int) -> bytes:
    n = max(1, (v.bit_length() + 8) // 8)
    return _tlv(0x02, v.to_bytes(n, "big", signed=True))


def _enc_oid(oid: str) -> bytes:
    arcs = [int(x) for x in oid.strip(".").split(".")]
    if len(arcs) < 2:
        raise SnmpError(f"OID inválido: {oid}")
    out = bytearray([arcs[0] * 40 + arcs[1]])
    for a in arcs[2:]:
        chunk = [a & 0x7F]
        a >>= 7
        while a:
            chunk.append(0x80 | (a & 0x7F))
            a >>= 7
        out += bytes(reversed(chunk))
    return _tlv(0x06, bytes(out))


def _dec_len(buf: bytes, i: int) -> Tuple[int, int]:
    b = buf[i]
    if b < 0x80:
        return b, i + 1
    n = b & 0x7F
    return int.from_bytes(buf[i + 1:i + 1 + n], "big"), i + 1 + n


def _dec_tlv(buf: bytes, i: int) -> Tuple[int, bytes, int]:
    tag = buf[i]
    ln, j = _dec_len(buf, i + 1)
    return tag, buf[j:j + ln], j + ln


def _dec_oid(b: bytes) -> str:
    first = b[0]
    arcs = [first // 40, first % 40] if first < 80 else [2, first - 80]
    v = 0
    for byte in b[1:]:
        v = (v << 7) | (byte & 0x7F)
        if not byte & 0x80:
            arcs.append(v)
            v = 0
    return ".".join(str(a) for a in arcs)


def _dec_value(tag: int, b: bytes) -> Value:
    if tag == 0x02:
        return int.from_bytes(b, "big", signed=True) if b else 0
    if tag in (0x41, 0x42, 0x43, 0x46):  # Counter32, Gauge32, TimeTicks, Counter64
        return int.from_bytes(b, "big", signed=False) if b else 0
    if tag == 0x04:
        try:
            return b.decode("utf-8")
        except UnicodeDecodeError:
            return b.decode("latin-1")
    if tag == 0x06:
        return _dec_oid(b)
    if tag == 0x40:
        return ".".join(str(x) for x in b)
    if tag == 0x05:
        return None
    if tag == 0x80:
        return NoSuch("noSuchObject")
    if tag == 0x81:
        return NoSuch("noSuchInstance")
    if tag == 0x82:
        return NoSuch("endOfMibView")
    return bytes(b)


def build_request(pdu_tag: int, request_id: int, community: str, oids: List[str],
                  non_repeaters: int = 0, max_repetitions: int = 0) -> bytes:
    varbinds = b"".join(_tlv(0x30, _enc_oid(o) + b"\x05\x00") for o in oids)
    if pdu_tag == 0xA5:
        a, b = non_repeaters, max_repetitions
    else:
        a, b = 0, 0
    pdu = _tlv(pdu_tag, _enc_int(request_id) + _enc_int(a) + _enc_int(b) + _tlv(0x30, varbinds))
    return _tlv(0x30, _enc_int(1) + _tlv(0x04, community.encode()) + pdu)


def parse_response(data: bytes) -> Tuple[int, int, int, List[Tuple[str, Value]]]:
    """-> (request_id, error_status, error_index, [(oid, value)])"""
    tag, msg, _ = _dec_tlv(data, 0)
    if tag != 0x30:
        raise SnmpError("resposta SNMP inválida")
    i = 0
    _, _ver, i = _dec_tlv(msg, i)
    _, _comm, i = _dec_tlv(msg, i)
    pdu_tag, pdu, _ = _dec_tlv(msg, i)
    if pdu_tag != 0xA2:
        raise SnmpError(f"PDU inesperada 0x{pdu_tag:02x}")
    j = 0
    _, rid, j = _dec_tlv(pdu, j)
    _, es, j = _dec_tlv(pdu, j)
    _, ei, j = _dec_tlv(pdu, j)
    _, vbl, _ = _dec_tlv(pdu, j)
    out = []
    k = 0
    while k < len(vbl):
        _, vb, k = _dec_tlv(vbl, k)
        _, oid_b, m = _dec_tlv(vb, 0)
        vtag, vval, _ = _dec_tlv(vb, m)
        out.append((_dec_oid(oid_b), _dec_value(vtag, vval)))
    return (int.from_bytes(rid, "big", signed=True), int.from_bytes(es, "big"), int.from_bytes(ei, "big"), out)


def _oid_key(oid: str) -> Tuple[int, ...]:
    return tuple(int(x) for x in oid.strip(".").split("."))


def _under(oid: str, base: str) -> bool:
    return oid == base or oid.startswith(base + ".")


# ---------- transporte UDP ----------
class _Proto(asyncio.DatagramProtocol):
    def __init__(self):
        self.waiters: Dict[int, asyncio.Future] = {}

    def datagram_received(self, data, addr):
        try:
            rid = parse_response(data)[0]
        except Exception:
            return
        fut = self.waiters.pop(rid, None)
        if fut and not fut.done():
            fut.set_result(data)

    def error_received(self, exc):
        for f in self.waiters.values():
            if not f.done():
                f.set_exception(SnmpError(str(exc)))
        self.waiters.clear()


class SnmpClient:
    """SNMP v2c direto do servidor Bastion."""

    def __init__(self, host: str, community: str, port: int = 161, timeout: float = 2.0, retries: int = 1):
        self.host, self.community, self.port = host, community, int(port or 161)
        self.timeout, self.retries = timeout, retries

    async def _request(self, pdu_tag: int, oids: List[str], **kw) -> List[Tuple[str, Value]]:
        loop = asyncio.get_running_loop()
        transport, proto = await loop.create_datagram_endpoint(_Proto, remote_addr=(self.host, self.port))
        try:
            for attempt in range(self.retries + 1):
                rid = int.from_bytes(os.urandom(3), "big")
                fut = loop.create_future()
                proto.waiters[rid] = fut
                transport.sendto(build_request(pdu_tag, rid, self.community, oids, **kw))
                try:
                    data = await asyncio.wait_for(fut, self.timeout)
                except asyncio.TimeoutError:
                    proto.waiters.pop(rid, None)
                    continue
                _, es, ei, vbs = parse_response(data)
                if es:
                    raise SnmpError(f"erro SNMP {es} no item {ei}")
                return vbs
            raise SnmpError(f"sem resposta SNMP de {self.host}:{self.port} (community ou ACL?)")
        finally:
            transport.close()

    async def get(self, oids: List[str]) -> Dict[str, Value]:
        out: Dict[str, Value] = {}
        for i in range(0, len(oids), 40):  # PDUs pequenas: alguns equipamentos recusam GETs grandes
            for oid, val in await self._request(0xA0, oids[i:i + 40]):
                out[oid] = val
        return out

    async def walk(self, base: str, max_rep: int = 25, limit: int = 20000) -> Dict[str, Value]:
        base = base.strip(".")
        out: Dict[str, Value] = {}
        cur = base
        while len(out) < limit:
            vbs = await self._request(0xA5, [cur], non_repeaters=0, max_repetitions=max_rep)
            if not vbs:
                break
            done = False
            for oid, val in vbs:
                if not _under(oid, base) or isinstance(val, NoSuch):
                    done = True
                    break
                if oid in out or _oid_key(oid) <= _oid_key(cur):
                    done = True  # OID não crescente: evita loop infinito
                    break
                out[oid] = val
                cur = oid
            if done:
                break
        return out


# ---------- via agente (net-snmp no host do agente) ----------
_NETSNMP_LINE = re.compile(r"^\.?(\d+(?:\.\d+)+)\s*(.*)$")


def parse_netsnmp(text: str) -> Dict[str, Value]:
    """Saída de `snmpget/snmpbulkwalk -On -Oq -Oe -Ot -OU`: '.1.3.6... valor' por linha."""
    out: Dict[str, Value] = {}
    last = None
    for raw in text.splitlines():
        line = raw.rstrip("\r")
        m = _NETSNMP_LINE.match(line.strip())
        if not m:
            if last and line and not line.startswith(("Timeout", "snmp", "Error")):
                out[last] = f"{out[last]}\n{line}"  # string com quebra de linha
            continue
        oid, v = m.group(1), m.group(2).strip()
        last = oid
        low = v.lower()
        if low.startswith("no such object"):
            out[oid] = NoSuch("noSuchObject")
        elif low.startswith("no such instance"):
            out[oid] = NoSuch("noSuchInstance")
        elif low.startswith("no more variables"):
            out[oid] = NoSuch("endOfMibView")
        elif len(v) >= 2 and v[0] == '"' and v[-1] == '"':
            out[oid] = v[1:-1].replace('\\"', '"')
        elif re.fullmatch(r"-?\d+", v):
            out[oid] = int(v)
        else:
            out[oid] = v
    return out


class AgentSnmpClient:
    """Executa net-snmp no agente (último salto) através de uma conexão SSH já aberta."""

    OPTS = "-On -Oq -Oe -Ot -OU"

    def __init__(self, ssh_run, host: str, community: str, port: int = 161, timeout: float = 2.0, retries: int = 1):
        self.ssh_run = ssh_run  # async (cmd, timeout) -> stdout
        self.target = f"{host}:{int(port or 161)}"
        self.community, self.timeout, self.retries = community, timeout, retries

    def _base(self, tool: str) -> str:
        return (f"{tool} -v2c -c {shlex.quote(self.community)} {self.OPTS} "
                f"-t {self.timeout:g} -r {self.retries} {shlex.quote(self.target)}")

    async def _run(self, cmd: str, timeout: int) -> str:
        out = await self.ssh_run(f"{cmd} 2>&1", timeout)
        if "command not found" in out or "não encontrado" in out:
            raise SnmpError("net-snmp não instalado no agente (rode: apt install snmp)")
        if "Timeout: No Response" in out:
            raise SnmpError(f"sem resposta SNMP de {self.target} a partir do agente (community ou ACL?)")
        return out

    async def get(self, oids: List[str]) -> Dict[str, Value]:
        out: Dict[str, Value] = {}
        for i in range(0, len(oids), 40):
            chunk = " ".join(oids[i:i + 40])
            out.update(parse_netsnmp(await self._run(f"{self._base('snmpget')} {chunk}", 30)))
        return out

    async def walk(self, base: str, max_rep: int = 25, limit: int = 20000) -> Dict[str, Value]:
        res = parse_netsnmp(await self._run(f"{self._base('snmpbulkwalk')} -Cr{max_rep} {base}", 120))
        return {k: v for k, v in res.items() if _under(k, base.strip(".")) and not isinstance(v, NoSuch)}


# ---------- alto nível ----------
def _idx(oid: str, base: str) -> Optional[int]:
    rest = oid[len(base) + 1:]
    return int(rest) if rest.isdigit() else None


async def discover_interfaces(client) -> dict:
    """sysName/sysDescr + lista de interfaces (nome, descrição, alias, velocidade, status)."""
    sysinfo = await client.get([SYS_NAME, SYS_DESCR, SYS_UPTIME])
    cols = {}
    for key, base in (("name", IF_NAME), ("descr", IF_DESCR), ("alias", IF_ALIAS), ("speed", IF_HIGH_SPEED),
                      ("oper", IF_OPER), ("admin", IF_ADMIN)):
        try:
            cols[key] = {_idx(o, base): v for o, v in (await client.walk(base)).items()}
        except SnmpError:
            if key in ("name", "descr"):
                raise
            cols[key] = {}
    idxs = sorted({i for c in cols.values() for i in c if i is not None})
    ifaces = []
    for i in idxs:
        name = cols["name"].get(i) or cols["descr"].get(i) or f"if{i}"
        if not isinstance(name, str):
            name = str(name)
        ifaces.append({
            "index": i, "name": name, "descr": cols["descr"].get(i) if isinstance(cols["descr"].get(i), str) else "",
            "alias": cols["alias"].get(i) if isinstance(cols["alias"].get(i), str) else "",
            "speed_mbps": cols["speed"].get(i) if isinstance(cols["speed"].get(i), int) else 0,
            "oper": OPER_STATUS.get(cols["oper"].get(i), "unknown"),
            "admin": OPER_STATUS.get(cols["admin"].get(i), "unknown"),
        })
    clean = lambda v: v if isinstance(v, (str, int)) else None  # noqa: E731
    return {"sys_name": clean(sysinfo.get(SYS_NAME)), "sys_descr": clean(sysinfo.get(SYS_DESCR)),
            "uptime": clean(sysinfo.get(SYS_UPTIME)), "interfaces": ifaces}


async def poll_counters(client, indexes: List[int]) -> Dict[int, dict]:
    """ifHCIn/Out + ifOperStatus dos índices pedidos."""
    oids = []
    for i in indexes:
        oids += [f"{IF_HC_IN}.{i}", f"{IF_HC_OUT}.{i}", f"{IF_OPER}.{i}", f"{IF_ADMIN}.{i}"]
    vals = await client.get(oids)
    out = {}
    for i in indexes:
        vin, vout = vals.get(f"{IF_HC_IN}.{i}"), vals.get(f"{IF_HC_OUT}.{i}")
        oper, admin = vals.get(f"{IF_OPER}.{i}"), vals.get(f"{IF_ADMIN}.{i}")
        out[i] = {
            "in_octets": vin if isinstance(vin, int) else None,
            "out_octets": vout if isinstance(vout, int) else None,
            "oper": OPER_STATUS.get(oper, "unknown") if isinstance(oper, int) else "unknown",
            "admin": OPER_STATUS.get(admin, "unknown") if isinstance(admin, int) else "unknown",
        }
    return out
