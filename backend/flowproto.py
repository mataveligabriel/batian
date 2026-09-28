"""Decodificadores de flow: NetFlow v5, NetFlow v9, IPFIX (v10) e sFlow v5 — puro Python, sem dependências.

Cada pacote UDP vira uma lista de Flow já multiplicada pela taxa de amostragem (bytes/pacotes estimados reais).
Endereços IP ficam como int + versão (4/6) para agregar rápido; a formatação só acontece na saída.
"""
import struct
import time
from typing import Dict, List, Optional, Tuple

# (exporter, in_if, out_if, src, dst, ipver, proto, sport, dport, tcp_flags, bytes, pkts, src_as, dst_as, direction)
#   direction: -1 desconhecido, 0 = amostrado na entrada, 1 = amostrado na saída
Flow = tuple
F_EXP, F_IN, F_OUT, F_SRC, F_DST, F_VER, F_PROTO, F_SPORT, F_DPORT, F_FLAGS, F_BYTES, F_PKTS, F_SAS, F_DAS, F_DIR = range(15)

_U16 = struct.Struct("!H")
_U32 = struct.Struct("!I")


def _int(b: bytes) -> int:
    return int.from_bytes(b, "big")


class ParseError(Exception):
    pass


# =====================================================================================
# NetFlow v5
# =====================================================================================
_V5_HDR = struct.Struct("!HHIIIIBBH")
_V5_REC = struct.Struct("!IIIHHIIIIHHBBBBHHBBH")


def parse_v5(data: bytes, exporter: str, sampling_override: int = 0) -> Tuple[List[Flow], int]:
    ver, count, _up, _s, _ns, _seq, _et, _eid, samp = _V5_HDR.unpack_from(data, 0)
    rate = sampling_override or (samp & 0x3FFF) or 1
    out = []
    off = 24
    for _ in range(count):
        if off + 48 > len(data):
            break
        (src, dst, _nh, i_in, i_out, pkts, octs, _first, _last, sport, dport, _p1, flags, proto, _tos,
         sas, das, _sm, _dm, _p2) = _V5_REC.unpack_from(data, off)
        off += 48
        out.append((exporter, i_in, i_out, src, dst, 4, proto, sport, dport, flags, octs * rate, pkts * rate, sas, das, -1))
    return out, rate


# =====================================================================================
# NetFlow v9 / IPFIX
# =====================================================================================
# IDs de campo (iguais no v9 e no IPFIX para estes)
BYTES_IDS = (1, 85, 23)             # IN_BYTES / octetDeltaCount, octetTotalCount, OUT_BYTES
PKTS_IDS = (2, 86, 24)
FID = {
    "proto": 4, "flags": 6, "sport": 7, "src4": 8, "in_if": 10, "dport": 11, "dst4": 12, "out_if": 14,
    "src_as": 16, "dst_as": 17, "src6": 27, "dst6": 28, "direction": 61, "in_phys": 252, "out_phys": 253,
    "samp_interval": 34, "samp_random": 50, "samp_pkt_interval": 305, "samp_pkt_space": 306,
}
SAMPLING_IDS = {34, 50, 305, 306}


class TemplateCache:
    """Templates por (exportador, source_id/observation domain, template id). Guarda também a amostragem vista."""

    def __init__(self):
        self.templates: Dict[tuple, list] = {}
        self.options: Dict[tuple, tuple] = {}
        self.sampling: Dict[str, int] = {}          # exportador -> taxa (1:N) mais recente vista nas options
        self.seen_at: Dict[tuple, float] = {}

    def set(self, key, fields):
        self.templates[key] = fields
        self.seen_at[key] = time.time()

    def dump(self) -> dict:
        """Para guardar no banco: sem isso, a cada reinício do coletor o NetFlow v9/IPFIX fica sem dados até o
        roteador reenviar os templates (no Huawei o padrão é a cada 30 min)."""
        return {"t": [[k[0], k[1], k[2], [list(f) for f in v]] for k, v in self.templates.items()],
                "o": [[k[0], k[1], k[2], [list(f) for f in v]] for k, v in self.options.items()],
                "s": dict(self.sampling)}

    def load(self, d: dict):
        for exp, dom, tid, fields in (d or {}).get("t") or []:
            self.templates.setdefault((exp, dom, tid), [tuple(f) for f in fields])
        for exp, dom, tid, fields in (d or {}).get("o") or []:
            self.options.setdefault((exp, dom, tid), [tuple(f) for f in fields])
        for exp, r in ((d or {}).get("s") or {}).items():
            self.sampling.setdefault(exp, r)

    def missing(self) -> int:
        return 0


def _sampling_from(values: Dict[int, int]) -> Optional[int]:
    if values.get(305):
        iv, sp = values[305], values.get(306, 0)
        return (iv + sp) // iv if sp else iv   # IPFIX: 1 amostrado a cada (intervalo+espaço)
    for k in (34, 50):
        if values.get(k):
            return values[k]
    return None


def _read_fields(buf: bytes, off: int, fields: list, ipfix: bool) -> Tuple[Dict[int, bytes], int]:
    vals = {}
    for fid, ln in fields:
        if ln == 65535 and ipfix:                 # campo de tamanho variável
            ln = buf[off]
            off += 1
            if ln == 255:
                ln = _U16.unpack_from(buf, off)[0]
                off += 2
        if off + ln > len(buf):
            raise ParseError("registro truncado")
        if fid is not None:
            vals[fid] = buf[off:off + ln]
        off += ln
    return vals, off


def _rec_len(fields: list) -> Optional[int]:
    if any(ln == 65535 for _, ln in fields):
        return None
    return sum(ln for _, ln in fields)


def _to_flow(vals: Dict[int, bytes], exporter: str, rate: int) -> Optional[Flow]:
    g = lambda k: _int(vals[k]) if k in vals else None      # noqa: E731
    if 8 in vals and 12 in vals:
        src, dst, ver = _int(vals[8]), _int(vals[12]), 4
    elif 27 in vals and 28 in vals:
        src, dst, ver = _int(vals[27]), _int(vals[28]), 6
    else:
        return None
    b = next((g(k) for k in BYTES_IDS if k in vals), 0) or 0
    p = next((g(k) for k in PKTS_IDS if k in vals), 0) or 0
    rec_rate = _sampling_from({k: _int(v) for k, v in vals.items() if k in SAMPLING_IDS})
    r = rec_rate or rate or 1
    i_in = g(10) if 10 in vals else (g(252) or 0)
    i_out = g(14) if 14 in vals else (g(253) or 0)
    d = g(61)
    return (exporter, i_in or 0, i_out or 0, src, dst, ver, g(4) or 0, g(7) or 0, g(11) or 0, (g(6) or 0) & 0xFF,
            b * r, p * r, g(16) or 0, g(17) or 0, d if d in (0, 1) else -1)


def parse_v9_ipfix(data: bytes, exporter: str, cache: TemplateCache, sampling_override: int = 0) -> Tuple[List[Flow], int]:
    """-> (flows, registros sem template ainda)"""
    ver = _U16.unpack_from(data, 0)[0]
    ipfix = ver == 10
    if ipfix:
        total = _U16.unpack_from(data, 2)[0]
        domain = _U32.unpack_from(data, 12)[0]
        off, end = 16, min(total, len(data))
    else:
        domain = _U32.unpack_from(data, 16)[0]
        off, end = 20, len(data)
    t_set, o_set = (2, 3) if ipfix else (0, 1)
    flows: List[Flow] = []
    no_tpl = 0
    while off + 4 <= end:
        sid, slen = struct.unpack_from("!HH", data, off)
        if slen < 4:
            break
        body, set_end = off + 4, min(off + slen, end)
        if sid == t_set:
            p = body
            while p + 4 <= set_end:
                tid, cnt = struct.unpack_from("!HH", data, p)
                p += 4
                fields = []
                for _ in range(cnt):
                    fid, ln = struct.unpack_from("!HH", data, p)
                    p += 4
                    if ipfix and fid & 0x8000:          # campo de fabricante: pula enterprise number
                        p += 4
                        fid = None
                    fields.append((fid, ln))
                cache.set((exporter, domain, tid), fields)
                if cnt == 0:
                    break
        elif sid == o_set:
            p = body
            while p + 6 <= set_end:
                if ipfix:
                    tid, cnt, scnt = struct.unpack_from("!HHH", data, p)
                    p += 6
                    fields = []
                    for _ in range(cnt):
                        fid, ln = struct.unpack_from("!HH", data, p)
                        p += 4
                        if fid & 0x8000:
                            p += 4
                            fid = None
                        fields.append((fid, ln))
                else:
                    tid, slen_b, olen_b = struct.unpack_from("!HHH", data, p)
                    p += 6
                    fields = []
                    for _ in range((slen_b + olen_b) // 4):
                        fid, ln = struct.unpack_from("!HH", data, p)
                        p += 4
                        fields.append((fid, ln))
                    # scope fields do v9 usam outra numeração: marcamos como None
                    ns = slen_b // 4
                    fields = [(None, ln) if i < ns else (fid, ln) for i, (fid, ln) in enumerate(fields)]
                cache.options[(exporter, domain, tid)] = fields
                if set_end - p < 6:
                    break
        elif sid >= 256:
            key = (exporter, domain, sid)
            if key in cache.options:
                fields = cache.options[key]
                rl = _rec_len(fields)
                p = body
                while p < set_end and (rl is None or p + rl <= set_end):
                    try:
                        vals, p = _read_fields(data, p, fields, ipfix)
                    except ParseError:
                        break
                    r = _sampling_from({k: _int(v) for k, v in vals.items() if k in SAMPLING_IDS})
                    if r:
                        cache.sampling[exporter] = r
                    if rl == 0:
                        break
            elif key in cache.templates:
                fields = cache.templates[key]
                rl = _rec_len(fields)
                rate = sampling_override or cache.sampling.get(exporter, 1)
                p = body
                while p < set_end and (rl is None or p + rl <= set_end):
                    try:
                        vals, p = _read_fields(data, p, fields, ipfix)
                    except ParseError:
                        break
                    f = _to_flow(vals, exporter, rate)
                    if f:
                        flows.append(f)
                    if rl == 0:
                        break
            else:
                no_tpl += 1
        off += slen
    return flows, no_tpl


# =====================================================================================
# sFlow v5
# =====================================================================================
def parse_packet_header(h: bytes):
    """Cabeçalho Ethernet amostrado -> (ver, src, dst, proto, sport, dport, flags) ou None."""
    if len(h) < 14:
        return None
    et = _U16.unpack_from(h, 12)[0]
    p = 14
    while et in (0x8100, 0x88A8, 0x9100) and p + 4 <= len(h):      # VLAN / QinQ
        et = _U16.unpack_from(h, p + 2)[0]
        p += 4
    if et in (0x8847, 0x8848):                                        # MPLS: pula a pilha de rótulos
        while p + 4 <= len(h):
            lbl = _U32.unpack_from(h, p)[0]
            p += 4
            if lbl & 0x100:
                break
        if p < len(h):
            et = 0x0800 if h[p] >> 4 == 4 else 0x86DD if h[p] >> 4 == 6 else 0
    if et == 0x0800 and p + 20 <= len(h):
        ihl = (h[p] & 0x0F) * 4
        proto = h[p + 9]
        frag = _U16.unpack_from(h, p + 6)[0] & 0x1FFF
        src, dst = _U32.unpack_from(h, p + 12)[0], _U32.unpack_from(h, p + 16)[0]
        l4 = p + ihl
        sport = dport = flags = 0
        if frag == 0 and proto in (6, 17) and l4 + 4 <= len(h):
            sport, dport = struct.unpack_from("!HH", h, l4)
            if proto == 6 and l4 + 14 <= len(h):
                flags = h[l4 + 13]
        return 4, src, dst, proto, sport, dport, flags
    if et == 0x86DD and p + 40 <= len(h):
        nh = h[p + 6]
        src, dst = _int(h[p + 8:p + 24]), _int(h[p + 24:p + 40])
        l4 = p + 40
        sport = dport = flags = 0
        if nh in (6, 17) and l4 + 4 <= len(h):
            sport, dport = struct.unpack_from("!HH", h, l4)
            if nh == 6 and l4 + 14 <= len(h):
                flags = h[l4 + 13]
        return 6, src, dst, nh, sport, dport, flags
    return None


def parse_sflow(data: bytes, exporter_fallback: str, sampling_override: int = 0) -> Tuple[List[Flow], str]:
    """-> (flows, IP do agente sFlow)"""
    ver, atype = struct.unpack_from("!II", data, 0)
    if ver != 5:
        raise ParseError(f"sFlow versão {ver}")
    if atype == 1:
        agent = ".".join(str(x) for x in data[8:12])
        off = 12
    elif atype == 2:
        import ipaddress
        agent = str(ipaddress.IPv6Address(data[8:24]))
        off = 24
    else:
        agent, off = exporter_fallback, 8
    _sub, _seq, _up, n = struct.unpack_from("!IIII", data, off)
    off += 16
    flows: List[Flow] = []
    for _ in range(n):
        if off + 8 > len(data):
            break
        stype, slen = struct.unpack_from("!II", data, off)
        body, nxt = off + 8, off + 8 + slen
        off = nxt
        fmt = stype & 0xFFF
        if stype >> 12 != 0 or fmt not in (1, 3):
            continue                                                  # contadores / outros
        try:
            if fmt == 1:
                _sq, _srcid, rate, _pool, _drops, i_in, i_out, nrec = struct.unpack_from("!IIIIIIII", data, body)
                i_in &= 0x3FFFFFFF
                i_out &= 0x3FFFFFFF
                p = body + 32
            else:
                _sq, _st, _si, rate, _pool, _drops, _if1, i_in, _of1, i_out, nrec = struct.unpack_from("!IIIIIIIIIII", data, body)
                p = body + 44
        except struct.error:
            continue
        rate = sampling_override or rate or 1
        hdr, frame_len, sas, das = None, 0, 0, 0
        for _r in range(nrec):
            if p + 8 > nxt:
                break
            rfmt, rlen = struct.unpack_from("!II", data, p)
            rb = p + 8
            p = rb + rlen
            if rfmt == 1:                                             # raw packet header
                _hp, frame_len, _stripped, hlen = struct.unpack_from("!IIII", data, rb)
                hdr = data[rb + 16: rb + 16 + hlen]
            elif rfmt == 1003:                                        # extended gateway: AS
                try:
                    nht = _U32.unpack_from(data, rb)[0]
                    q = rb + 4 + (4 if nht == 1 else 16)
                    _my_as, sas, _peer_as, nseg = struct.unpack_from("!IIII", data, q)
                    q += 16
                    for _s in range(nseg):
                        _segt, seglen = struct.unpack_from("!II", data, q)
                        q += 8
                        if seglen:
                            das = _U32.unpack_from(data, q + 4 * (seglen - 1))[0]
                        q += 4 * seglen
                except struct.error:
                    pass
        if hdr is None:
            continue
        pk = parse_packet_header(hdr)
        if not pk:
            continue
        v, src, dst, proto, sport, dport, flags = pk
        flows.append((agent, i_in, i_out, src, dst, v, proto, sport, dport, flags, frame_len * rate, rate, sas, das, -1))
    return flows, agent


# =====================================================================================
def parse_datagram(data: bytes, addr: str, cache: TemplateCache, sampling_override: int = 0, sflow: bool = False):
    """-> (flows, info) — info = {"kind": "v5|v9|ipfix|sflow", "exporter": ip, "no_template": n, "rate": r}"""
    if sflow:
        flows, agent = parse_sflow(data, addr, sampling_override)
        return flows, {"kind": "sflow", "exporter": agent, "no_template": 0, "rate": flows[0][F_PKTS] if flows else None}
    if len(data) < 4:
        raise ParseError("pacote curto")
    ver = _U16.unpack_from(data, 0)[0]
    if ver == 5:
        flows, rate = parse_v5(data, addr, sampling_override)
        return flows, {"kind": "v5", "exporter": addr, "no_template": 0, "rate": rate}
    if ver in (9, 10):
        flows, miss = parse_v9_ipfix(data, addr, cache, sampling_override)
        return flows, {"kind": "v9" if ver == 9 else "ipfix", "exporter": addr, "no_template": miss,
                       "rate": sampling_override or cache.sampling.get(addr)}
    raise ParseError(f"versão de flow desconhecida: {ver}")
