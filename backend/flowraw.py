"""Flow detalhado (no estilo Wanguard / Kentik): cada minuto de flows vira um arquivo colunar no disco.

O coletor (flowd) junta os flows das interfaces monitoradas por chave (IPs, protocolo, portas de serviço, interfaces
de entrada e saída) e grava um .npz por minuto em <dir>/AAAAMMDD/HHMM.npz. A API consulta esses arquivos com
qualquer combinação de filtros e até 3 agrupamentos — IP, bloco, AS, país, porta, protocolo, interface, tamanho de
pacote, flags TCP — sem depender do top-K de 5 minutos. É o que permite:

  - ver o consumo de um IP (ou bloco) específico, mesmo que ele nunca apareça entre os maiores;
  - abrir um ataque e ver de quais blocos /24, ASNs e países ele vem, com as portas e o tamanho dos pacotes;
  - cruzar dimensões livremente (ex.: AS de origem × IP de destino, só UDP, só porta 443).

Colunas de cada minuto: ver, sh/sl (IP de origem: 64 bits altos/baixos; IPv4 fica em sl), dh/dl (destino), proto,
sport, dport (portas de serviço: efêmera vira 0), flags (OR das flags TCP), kin/kout (índice da interface de
entrada/saída na lista "keys" do arquivo, -1 = não monitorada), sas, das, bytes, pkts — já multiplicados pela
amostragem. Retenção por dias e por espaço em disco (o que bater primeiro).
"""
import ipaddress
import logging
import os
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np

log = logging.getLogger("flowraw")

DEFAULTS = {"raw_enabled": True, "raw_days": 3, "raw_max_gb": 30}
ROW_CAP = 400_000            # chaves distintas por minuto; acima disso a origem é resumida no /24 (/48) e sem porta
M64 = (1 << 64) - 1
MAX_MINUTES = 4320           # consulta de até 72 h
MAX_GROUP_BY = 3

PROTO_NAME = {1: "ICMP", 2: "IGMP", 6: "TCP", 17: "UDP", 41: "IPv6-in-IP", 47: "GRE", 50: "ESP", 51: "AH", 58: "ICMPv6",
              89: "OSPF", 103: "PIM", 112: "VRRP", 132: "SCTP"}
PROTO_NUM = {v.upper(): k for k, v in PROTO_NAME.items()}
PKT_EDGES = [65, 129, 257, 513, 1025, 1501]   # limites dos baldes de tamanho médio de pacote (bytes)
PKT_LABEL = ["até 64 B", "65–128 B", "129–256 B", "257–512 B", "513–1024 B", "1025–1500 B", "acima de 1500 B (jumbo)"]
TCP_FLAGS = [(0x01, "FIN"), (0x02, "SYN"), (0x04, "RST"), (0x08, "PSH"), (0x10, "ACK"), (0x20, "URG")]

# dimensões de agrupamento: rótulo
DIMS = {
    "dst_ip": "IP de destino", "src_ip": "IP de origem", "dst_pfx": "Bloco de destino", "src_pfx": "Bloco de origem",
    "src_as": "AS de origem", "dst_as": "AS de destino", "src_cc": "País de origem", "dst_cc": "País de destino",
    "proto": "Protocolo", "sport": "Porta de origem", "dport": "Porta de destino", "iface": "Interface",
    "pktsize": "Tamanho do pacote", "tcpflags": "Flags TCP",
}
# dimensões de filtro: rótulo (ip/as/port/cc casam origem OU destino)
FILTERS = {
    "ip": "IP ou bloco (origem ou destino)", "src_ip": "IP / bloco de origem", "dst_ip": "IP / bloco de destino",
    "as": "AS (origem ou destino)", "src_as": "AS de origem", "dst_as": "AS de destino",
    "cc": "País (origem ou destino)", "src_cc": "País de origem", "dst_cc": "País de destino",
    "proto": "Protocolo", "port": "Porta (origem ou destino)", "sport": "Porta de origem", "dport": "Porta de destino",
    "iface": "Interface", "pktsize": "Tamanho do pacote", "tcpflags": "Flags TCP (tem a flag)",
}


class QueryError(ValueError):
    pass


# =====================================================================================
# Pasta
# =====================================================================================
def base_dir() -> Path:
    env = os.environ.get("FLOW_RAW_DIR")
    if env:
        return Path(env)
    return Path("/data/flowraw") if Path("/data").is_dir() else Path(__file__).parent / "data" / "flowraw"


def file_for(base: Path, minute_ts: int) -> Path:
    d = datetime.fromtimestamp(minute_ts, timezone.utc)
    return base / d.strftime("%Y%m%d") / d.strftime("%H%M.npz")


def disk_usage(base: Path) -> dict:
    files, size, oldest, newest = 0, 0, None, None
    if base.is_dir():
        for day in sorted(p for p in base.iterdir() if p.is_dir()):
            for f in sorted(day.glob("*.npz")):
                try:
                    size += f.stat().st_size
                except OSError:
                    continue
                files += 1
                ts = _ts_of(f)
                if ts is not None:
                    oldest = ts if oldest is None else min(oldest, ts)
                    newest = ts if newest is None else max(newest, ts)
    return {"files": files, "bytes": size, "oldest": oldest, "newest": newest}


def _ts_of(f: Path) -> Optional[int]:
    try:
        return int(datetime.strptime(f.parent.name + f.stem, "%Y%m%d%H%M").replace(tzinfo=timezone.utc).timestamp())
    except ValueError:
        return None


def cleanup(base: Path, days: float, max_gb: float, now: Optional[float] = None) -> int:
    """Apaga o que passou de N dias e, se ainda passar do limite de espaço, os minutos mais antigos."""
    if not base.is_dir():
        return 0
    now = now or time.time()
    removed = 0
    cut = datetime.fromtimestamp(now - days * 86400, timezone.utc).strftime("%Y%m%d")
    days_dirs = sorted(p for p in base.iterdir() if p.is_dir() and p.name.isdigit())
    for d in days_dirs:
        if d.name < cut:
            removed += len(list(d.glob("*.npz")))
            shutil.rmtree(d, ignore_errors=True)
        else:
            for f in d.glob("*.npz"):
                ts = _ts_of(f)
                if ts is not None and ts < now - days * 86400:
                    f.unlink(missing_ok=True)
                    removed += 1
    limit = max_gb * 1e9
    files = []
    total = 0
    for d in sorted(p for p in base.iterdir() if p.is_dir()):
        for f in sorted(d.glob("*.npz")):
            try:
                s = f.stat().st_size
            except OSError:
                continue
            files.append((f, s))
            total += s
    i = 0
    while total > limit and i < len(files) - 1:      # nunca apaga o minuto mais novo
        f, s = files[i]
        f.unlink(missing_ok=True)
        total -= s
        removed += 1
        i += 1
    for d in base.iterdir():
        if d.is_dir() and not any(d.iterdir()):
            d.rmdir()
    return removed


# =====================================================================================
# Coleta (no flowd)
# =====================================================================================
class Buffer:
    """Junta os flows de um minuto por chave. add() é chamado para cada flow de interface monitorada."""

    def __init__(self, row_cap: int = ROW_CAP):
        self.rows: Dict[tuple, list] = {}
        self.kidx: Dict[str, int] = {}
        self.row_cap = row_cap
        self.coarse = 0

    def _k(self, key: Optional[str]) -> int:
        if not key:
            return -1
        i = self.kidx.get(key)
        if i is None:
            i = self.kidx[key] = len(self.kidx)
        return i

    def add(self, ver: int, src: int, dst: int, proto: int, sport: int, dport: int, flags: int,
            k_in: Optional[str], k_out: Optional[str], sas: int, das: int, b: int, p: int):
        ki, ko = self._k(k_in), self._k(k_out)
        rows = self.rows
        key = (ver, src, dst, proto, sport, dport, ki, ko)
        r = rows.get(key)
        if r is None:
            if len(rows) >= self.row_cap:
                # minuto cheio (ataque com origem espalhada): resume a origem no bloco e tira a porta de origem
                key = (ver, src & (0xFFFFFF00 if ver == 4 else ~((1 << 80) - 1) & ((1 << 128) - 1)), dst, proto, 0, dport, ki, ko)
                r = rows.get(key)
                self.coarse += 1
            if r is None:
                rows[key] = [b, p, flags, sas, das]
                return
        r[0] += b
        r[1] += p
        r[2] |= flags

    def __len__(self):
        return len(self.rows)

    def swap(self) -> Optional[tuple]:
        """Troca por um buffer vazio (rápido, no laço do coletor); pack() roda depois numa thread."""
        rows, keys, coarse = self.rows, self.kidx, self.coarse
        self.rows, self.kidx, self.coarse = {}, {}, 0
        return (rows, keys, coarse) if rows else None

    def take(self) -> Optional[dict]:
        """Esvazia o buffer e devolve as colunas (numpy) para gravar; None se não houve flow."""
        got = self.swap()
        return pack(*got) if got else None


def pack(rows: Dict[tuple, list], kidx: Dict[str, int], coarse: int = 0) -> dict:
    n = len(rows)
    ks = list(rows.keys())
    vs = list(rows.values())
    ver = np.fromiter((k[0] for k in ks), np.uint8, n)
    src = [k[1] for k in ks]
    dst = [k[2] for k in ks]
    out = {
        "ver": ver,
        "sh": np.fromiter((v >> 64 for v in src), np.uint64, n), "sl": np.fromiter((v & M64 for v in src), np.uint64, n),
        "dh": np.fromiter((v >> 64 for v in dst), np.uint64, n), "dl": np.fromiter((v & M64 for v in dst), np.uint64, n),
        "proto": np.fromiter((k[3] for k in ks), np.uint8, n),
        "sport": np.fromiter((k[4] for k in ks), np.uint16, n), "dport": np.fromiter((k[5] for k in ks), np.uint16, n),
        "kin": np.fromiter((k[6] for k in ks), np.int16, n), "kout": np.fromiter((k[7] for k in ks), np.int16, n),
        "bytes": np.fromiter((v[0] for v in vs), np.uint64, n), "pkts": np.fromiter((v[1] for v in vs), np.uint64, n),
        "flags": np.fromiter((v[2] & 0xFF for v in vs), np.uint8, n),
        "sas": np.fromiter((v[3] & 0xFFFFFFFF for v in vs), np.uint32, n),
        "das": np.fromiter((v[4] & 0xFFFFFFFF for v in vs), np.uint32, n),
    }
    names = [""] * len(kidx)
    for k, i in kidx.items():
        names[i] = k
    out["keys"] = np.array(names, dtype="U128") if names else np.array([], dtype="U1")
    out["coarse"] = np.array([coarse], dtype=np.int64)
    return out


def write(base: Path, minute_ts: int, cols) -> Path:
    """cols: colunas prontas (pack) ou o que swap() devolveu."""
    if isinstance(cols, tuple):
        cols = pack(*cols)
    path = file_for(base, minute_ts)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.stem + ".tmp.npz")
    np.savez_compressed(tmp, **cols)
    os.replace(tmp, path)
    return path


# =====================================================================================
# Consulta
# =====================================================================================
def _net(value: str):
    try:
        return ipaddress.ip_network(value.strip(), strict=False)
    except ValueError:
        raise QueryError(f"IP ou bloco inválido: {value}")


def _ip_mask(ver, hi, lo, nets) -> np.ndarray:
    m = np.zeros(len(ver), dtype=bool)
    for n in nets:
        if n.version == 4:
            bits = (0xFFFFFFFF << (32 - n.prefixlen)) & 0xFFFFFFFF
            m |= (ver == 4) & ((lo & np.uint64(bits)) == np.uint64(int(n.network_address) & bits))
        else:
            full = ((1 << 128) - 1) ^ ((1 << (128 - n.prefixlen)) - 1)
            hm, lm = np.uint64(full >> 64), np.uint64(full & M64)
            na = int(n.network_address)
            m |= (ver == 6) & ((hi & hm) == np.uint64(na >> 64)) & ((lo & lm) == np.uint64(na & M64))
    return m


def _ints(values, what: str, lo: int = 0, hi: int = 2 ** 32 - 1) -> List[int]:
    out = []
    for v in values:
        s = str(v).strip().upper()
        if what == "as" and s.startswith("AS"):
            s = s[2:]
        if what == "proto" and s in PROTO_NUM:
            out.append(PROTO_NUM[s])
            continue
        if what == "port" and "-" in s:                       # faixa de portas: 1000-2000
            a, _, b = s.partition("-")
            try:
                a, b = int(a), int(b)
            except ValueError:
                raise QueryError(f"Faixa de portas inválida: {v}")
            if not (0 <= a <= b <= 65535) or b - a > 20000:
                raise QueryError(f"Faixa de portas inválida: {v}")
            out.extend(range(a, b + 1))
            continue
        try:
            n = int(s)
        except ValueError:
            raise QueryError({"as": "ASN", "proto": "Protocolo", "port": "Porta"}.get(what, "Valor") + f" inválido: {v}")
        if not lo <= n <= hi:
            raise QueryError(f"Valor fora da faixa: {v}")
        out.append(n)
    return out


def _flag_bits(values) -> int:
    names = {n: b for b, n in TCP_FLAGS}
    bits = 0
    for v in values:
        s = str(v).strip().upper()
        if s not in names:
            raise QueryError(f"Flag TCP inválida: {v} (use SYN, ACK, RST, FIN, PSH, URG)")
        bits |= names[s]
    return bits


def _pkt_bucket(b: np.ndarray, p: np.ndarray) -> np.ndarray:
    avg = b / np.maximum(p, 1)
    return np.digitize(avg, PKT_EDGES).astype(np.uint64)


def _cc_code(cc: str) -> int:
    cc = (cc or "").upper()[:2]
    return (ord(cc[0]) << 8 | ord(cc[1])) if len(cc) == 2 and cc.isalpha() else 0


def _cc_str(code: int) -> str:
    return chr(code >> 8) + chr(code & 0xFF) if code else ""


class _Ctx:
    """O que a consulta precisa, já preparado (filtros convertidos, ASN->país, nomes)."""

    def __init__(self, specs: List[dict], asn_cc: Optional[Dict[int, str]]):
        self.asn_cc = asn_cc or {}
        self.specs = [self._prep(s) for s in specs]

    def _prep(self, s: dict) -> dict:
        gb = [g for g in (s.get("group_by") or []) if g]
        if len(gb) > MAX_GROUP_BY:
            raise QueryError(f"Agrupe por no máximo {MAX_GROUP_BY} dimensões")
        for g in gb:
            if g not in DIMS:
                raise QueryError(f"Agrupamento desconhecido: {g}")
        if len(set(gb)) != len(gb):
            raise QueryError("Dimensão repetida no agrupamento")
        if s.get("direction", "in") not in ("in", "out"):
            raise QueryError("Direção inválida")
        flts = []
        for f in s.get("filters") or []:
            dim, vals = f.get("dim"), [str(v).strip() for v in (f.get("values") or []) if str(v).strip()]
            if not dim or not vals:
                continue
            if dim not in FILTERS:
                raise QueryError(f"Filtro desconhecido: {dim}")
            neg = f.get("op") == "not"
            if dim in ("ip", "src_ip", "dst_ip"):
                arg = [_net(v) for v in vals[:200]]
            elif dim in ("as", "src_as", "dst_as"):
                arg = np.array(_ints(vals[:500], "as"), dtype=np.uint32)
            elif dim in ("cc", "src_cc", "dst_cc"):
                ccs = {v.upper()[:2] for v in vals}
                bad = [v for v in vals if len(v.strip()) != 2 or not v.strip().isalpha()]
                if bad:
                    raise QueryError(f"Use o código de 2 letras do país (BR, US, CN…): {bad[0]}")
                arg = np.array([a for a, c in self.asn_cc.items() if (c or "").upper() in ccs] or [2 ** 32 - 1], dtype=np.uint32)
            elif dim == "proto":
                arg = np.array(_ints(vals, "proto", 0, 255), dtype=np.uint8)
            elif dim in ("port", "sport", "dport"):
                arg = np.array(_ints(vals, "port", 0, 65535), dtype=np.uint16)
            elif dim == "iface":
                arg = set(vals)
            elif dim == "pktsize":
                idx = []
                for v in vals:
                    if v in PKT_LABEL:
                        idx.append(PKT_LABEL.index(v))
                    elif v.isdigit() and int(v) < len(PKT_LABEL):
                        idx.append(int(v))
                    else:
                        raise QueryError(f"Tamanho de pacote inválido: {v}")
                arg = np.array(idx, dtype=np.uint64)
            else:   # tcpflags
                arg = _flag_bits(vals)
            flts.append((dim, neg, arg))
        top = max(1, min(int(s.get("top") or 10), 100))
        pfx = (max(8, min(int(s.get("pfx4") or 24), 32)), max(16, min(int(s.get("pfx6") or 48), 128)))
        return {**s, "group_by": gb, "filters": flts, "top": top, "pfx": pfx, "direction": s.get("direction", "in")}


def _apply_filters(c: dict, flts, kcol: np.ndarray, keys: List[str], asn_cc_arr=None) -> np.ndarray:
    n = len(c["ver"])
    m = np.ones(n, dtype=bool)
    for dim, neg, arg in flts:
        if dim == "ip":
            x = _ip_mask(c["ver"], c["sh"], c["sl"], arg) | _ip_mask(c["ver"], c["dh"], c["dl"], arg)
        elif dim == "src_ip":
            x = _ip_mask(c["ver"], c["sh"], c["sl"], arg)
        elif dim == "dst_ip":
            x = _ip_mask(c["ver"], c["dh"], c["dl"], arg)
        elif dim in ("as", "cc"):
            x = np.isin(c["sas"], arg) | np.isin(c["das"], arg)
        elif dim in ("src_as", "src_cc"):
            x = np.isin(c["sas"], arg)
        elif dim in ("dst_as", "dst_cc"):
            x = np.isin(c["das"], arg)
        elif dim == "proto":
            x = np.isin(c["proto"], arg)
        elif dim == "port":
            x = np.isin(c["sport"], arg) | np.isin(c["dport"], arg)
        elif dim in ("sport", "dport"):
            x = np.isin(c[dim], arg)
        elif dim == "iface":
            idx = [i for i, k in enumerate(keys) if k in arg]
            x = np.isin(kcol, np.array(idx, dtype=np.int16)) if idx else np.zeros(n, dtype=bool)
        elif dim == "pktsize":
            x = np.isin(_pkt_bucket(c["bytes"], c["pkts"]), arg)
        else:   # tcpflags: TCP com todas as flags pedidas
            x = (c["proto"] == 6) & ((c["flags"] & np.uint8(arg)) == np.uint8(arg))
        m &= ~x if neg else x
    return m


def _mask_ip(ver, hi, lo, pfx4, pfx6):
    m4 = np.uint64((0xFFFFFFFF << (32 - pfx4)) & 0xFFFFFFFF)
    full = ((1 << 128) - 1) ^ ((1 << (128 - pfx6)) - 1)
    hm, lm = np.uint64(full >> 64), np.uint64(full & M64)
    is4 = ver == 4
    return np.where(is4, hi, hi & hm), np.where(is4, lo & m4, lo & lm)


def _cc_col(as_col: np.ndarray, asn_cc: Dict[int, str]) -> np.ndarray:
    if not len(as_col):
        return as_col.astype(np.uint64)
    u, inv = np.unique(as_col, return_inverse=True)
    codes = np.array([_cc_code(asn_cc.get(int(a), "")) for a in u], dtype=np.uint64)
    return codes[inv.reshape(-1)]


def _group_cols(c: dict, sel: np.ndarray, gb: List[str], pfx, kcol_global: np.ndarray, asn_cc) -> List[np.ndarray]:
    cols = []
    for g in gb:
        if g in ("src_ip", "dst_ip", "src_pfx", "dst_pfx"):
            side = "s" if g.startswith("src") else "d"
            ver = c["ver"][sel].astype(np.uint64)
            hi, lo = c[side + "h"][sel], c[side + "l"][sel]
            if g.endswith("pfx"):
                hi, lo = _mask_ip(c["ver"][sel], hi, lo, *pfx)
            cols += [ver, hi, lo]
        elif g in ("src_as", "dst_as"):
            cols.append(c["sas" if g == "src_as" else "das"][sel].astype(np.uint64))
        elif g in ("src_cc", "dst_cc"):
            cols.append(_cc_col(c["sas" if g == "src_cc" else "das"][sel], asn_cc))
        elif g in ("proto", "sport", "dport"):
            cols.append(c[g][sel].astype(np.uint64))
        elif g == "iface":
            cols.append(kcol_global[sel].astype(np.uint64))
        elif g == "pktsize":
            cols.append(_pkt_bucket(c["bytes"][sel], c["pkts"][sel]))
        elif g == "tcpflags":
            cols.append(np.where(c["proto"][sel] == 6, c["flags"][sel] & np.uint8(0x3F), np.uint8(0)).astype(np.uint64))
    return cols


def _unique_rows(mat: np.ndarray):
    """np.unique por linha (rápido: cada linha vira um bloco de bytes)."""
    mat = np.ascontiguousarray(mat)
    if mat.shape[1] == 1:
        u, inv = np.unique(mat[:, 0], return_inverse=True)
        return u.reshape(-1, 1), inv.reshape(-1)
    v = mat.view(np.dtype((np.void, mat.dtype.itemsize * mat.shape[1]))).reshape(-1)
    u, idx, inv = np.unique(v, return_index=True, return_inverse=True)
    return mat[idx], inv.reshape(-1)


def _load(path: Path) -> Optional[dict]:
    try:
        with np.load(path, allow_pickle=False) as z:
            return {k: z[k] for k in z.files}
    except Exception as e:      # arquivo pela metade / corrompido: pula o minuto
        log.warning(f"flow detalhado {path.name}: {e}")
        return None


def _scan_file(path: Path, bucket: int, ctx: _Ctx, ifset: set, gkey: Dict[str, int], keep: int):
    c = _load(path)
    if c is None or not len(c.get("ver", ())):
        return None
    keys = [str(k) for k in c["keys"]]
    lut = np.array([gkey.setdefault(k, len(gkey)) for k in keys] + [65535], dtype=np.int32)   # -1 -> último
    out = []
    for s in ctx.specs:
        kcol = c["kin"] if s["direction"] == "in" else c["kout"]
        allowed = np.array([i for i, k in enumerate(keys) if k in ifset], dtype=np.int16)
        if not len(allowed):
            out.append(None)
            continue
        m = np.isin(kcol, allowed)
        if s["filters"]:
            m &= _apply_filters(c, s["filters"], kcol, keys)
        if not m.any():
            out.append(None)
            continue
        b = c["bytes"][m].astype(np.float64)
        p = c["pkts"][m].astype(np.float64)
        if not s["group_by"]:
            out.append((np.zeros((1, 1), dtype=np.uint64), np.array([b.sum()]), np.array([p.sum()]), 0.0, 0.0))
            continue
        cols = _group_cols(c, m, s["group_by"], s["pfx"], lut[kcol], ctx.asn_cc)
        u, inv = _unique_rows(np.stack(cols, axis=1))
        sb = np.bincount(inv, weights=b, minlength=len(u))
        sp = np.bincount(inv, weights=p, minlength=len(u))
        ob = op = 0.0
        if len(u) > keep:                     # guarda os maiores deste minuto; o resto vira "outros"
            w = sp if s.get("unit") == "pps" else sb
            idx = np.argpartition(-w, keep)[:keep]
            rest = np.ones(len(u), dtype=bool)
            rest[idx] = False
            ob, op = float(sb[rest].sum()), float(sp[rest].sum())
            u, sb, sp = u[idx], sb[idx], sp[idx]
        out.append((u, sb, sp, ob, op))
    return bucket, out, int(c.get("coarse", np.zeros(1))[0])


def pick_step(minutes: int) -> int:
    for lim, step in ((180, 60), (720, 300), (1440, 600), (2880, 1200)):
        if minutes <= lim:
            return step
    return 1800


def run(base: Path, *, start: int, end: int, ifaces: List[str], specs: List[dict], asn_cc: Optional[Dict[int, str]] = None,
        step: Optional[int] = None, workers: int = 4) -> dict:
    """Roda várias consultas lendo cada arquivo uma vez só. start/end: segundos (fim exclusivo)."""
    if not ifaces:
        raise QueryError("Escolha pelo menos uma interface monitorada")
    start = int(start) // 60 * 60
    end = max(start + 60, -(-int(end) // 60) * 60)
    minutes = (end - start) // 60
    if minutes > MAX_MINUTES:
        raise QueryError(f"Período máximo do flow detalhado: {MAX_MINUTES // 60} h")
    step = step or pick_step(minutes)
    nb = max(1, -(-(end - start) // step))
    ctx = _Ctx(specs, asn_cc)
    files = []
    for t in range(start, end, 60):
        f = file_for(base, t)
        if f.exists():
            files.append((f, (t - start) // step))
    keep = max(1000, min(20000, 3_000_000 // max(1, len(files))))
    ifset = set(ifaces)
    gkey: Dict[str, int] = {}
    parts = [[] for _ in ctx.specs]
    coarse = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        for r in ex.map(lambda fb: _scan_file(fb[0], fb[1], ctx, ifset, gkey, keep), files):
            if r is None:
                continue
            bucket, outs, co = r
            coarse += co
            for i, o in enumerate(outs):
                if o is not None:
                    parts[i].append((bucket, o))
    gnames = [""] * len(gkey)
    for k, i in gkey.items():
        gnames[i] = k
    results = []
    for s, pp in zip(ctx.specs, parts):
        results.append(_combine(s, pp, nb, step, gnames))
    return {"ts": [(start + i * step) * 1000 for i in range(nb)], "step": step, "start": start, "end": end,
            "files": len(files), "minutes": minutes, "coarse": coarse, "results": results}


def _combine(s: dict, parts, nb: int, step: int, gnames: List[str]) -> dict:
    unit = "pps" if s.get("unit") == "pps" else "bps"
    tot_b = np.zeros(nb)
    tot_p = np.zeros(nb)
    if not parts:
        return {"id": s.get("id"), "unit": unit, "group_by": s["group_by"], "rows": [], "other": None,
                "total": {"bytes": 0, "pkts": 0, "bps": [0.0] * nb, "pps": [0.0] * nb}}
    mats, bks, bs, ps = [], [], [], []
    for bucket, (u, sb, sp, ob, op) in parts:
        mats.append(u)
        bks.append(np.full(len(u), bucket, dtype=np.int64))
        bs.append(sb)
        ps.append(sp)
        tot_b[bucket] += sb.sum() + ob
        tot_p[bucket] += sp.sum() + op
    mat = np.concatenate(mats)
    bk = np.concatenate(bks)
    b = np.concatenate(bs)
    p = np.concatenate(ps)
    rows = []
    other = None
    if s["group_by"]:
        u, inv = _unique_rows(mat)
        gb_ = np.bincount(inv, weights=b, minlength=len(u))
        gp_ = np.bincount(inv, weights=p, minlength=len(u))
        w = gp_ if unit == "pps" else gb_
        n = min(s["top"], len(u))
        order = np.argsort(-w, kind="stable")[:n]
        rank = np.full(len(u), -1, dtype=np.int64)
        rank[order] = np.arange(n)
        sel = rank[inv] >= 0
        mb = np.zeros((n, nb))
        mp = np.zeros((n, nb))
        np.add.at(mb, (rank[inv][sel], bk[sel]), b[sel])
        np.add.at(mp, (rank[inv][sel], bk[sel]), p[sel])
        for i, gi in enumerate(order):
            rows.append({"key": u[gi].tolist(), "bytes": float(gb_[gi]), "pkts": float(gp_[gi]),
                         "sb": mb[i], "sp": mp[i]})
        ob_ = tot_b - mb.sum(axis=0)
        op_ = tot_p - mp.sum(axis=0)
        if ob_.sum() > max(1.0, tot_b.sum() * 1e-6) or op_.sum() > max(1.0, tot_p.sum() * 1e-6):
            other = {"sb": np.maximum(ob_, 0), "sp": np.maximum(op_, 0), "groups": int(len(u) - n)}
    sec = float(step)
    period = nb * sec
    total_b, total_p = float(tot_b.sum()), float(tot_p.sum())

    def stats(sb, sp, bytes_, pkts):
        bps = sb * 8 / sec
        pps = sp / sec
        v = pps if unit == "pps" else bps
        return {"values": [round(float(x), 2) for x in v], "avg": (pkts if unit == "pps" else bytes_ * 8) / period,
                "max": float(v.max()) if len(v) else 0.0, "p95": float(np.percentile(v, 95)) if len(v) else 0.0,
                "last": float(v[-1]) if len(v) else 0.0, "avg_bps": bytes_ * 8 / period, "avg_pps": pkts / period,
                "max_bps": float(bps.max()) if len(bps) else 0.0, "max_pps": float(pps.max()) if len(pps) else 0.0,
                "bytes": bytes_, "pkts": pkts, "pkt_size": bytes_ / pkts if pkts else 0.0,
                "share": ((pkts / total_p) if unit == "pps" else (bytes_ / total_b)) if (total_p if unit == "pps" else total_b) else 0.0}

    out_rows = [{"key": r["key"], **stats(r["sb"], r["sp"], r["bytes"], r["pkts"])} for r in rows]
    if other:
        other = {**stats(other["sb"], other["sp"], float(other["sb"].sum()), float(other["sp"].sum())), "groups": other["groups"]}
    return {"id": s.get("id"), "unit": unit, "group_by": s["group_by"], "pfx": list(s["pfx"]), "rows": out_rows, "other": other,
            "gnames": gnames,
            "total": {"bytes": total_b, "pkts": total_p, "bps": [round(float(x), 2) for x in tot_b * 8 / sec],
                      "pps": [round(float(x), 2) for x in tot_p / sec]}}


# =====================================================================================
# Rótulos (a API transforma a chave numérica em texto e no valor que vira filtro ao clicar)
# =====================================================================================
PORT_NAME = {20: "FTP-data", 21: "FTP", 22: "SSH", 23: "Telnet", 25: "SMTP", 53: "DNS", 67: "DHCP", 68: "DHCP", 69: "TFTP",
             80: "HTTP", 110: "POP3", 111: "Portmap", 123: "NTP", 137: "NetBIOS", 143: "IMAP", 161: "SNMP", 179: "BGP",
             389: "LDAP/CLDAP", 443: "HTTPS", 445: "SMB", 465: "SMTPS", 500: "IKE", 514: "Syslog", 520: "RIP",
             587: "Submission", 853: "DoT", 993: "IMAPS", 995: "POP3S", 1194: "OpenVPN", 1434: "MSSQL", 1701: "L2TP",
             1723: "PPTP", 1812: "RADIUS", 1900: "SSDP", 3074: "Xbox", 3283: "ARD", 3389: "RDP", 3478: "STUN", 3702: "WS-Discovery",
             4500: "IPsec NAT-T", 5060: "SIP", 5222: "XMPP", 5228: "Google Play", 5353: "mDNS", 8080: "HTTP-alt",
             8443: "HTTPS-alt", 10001: "Ubiquiti", 11211: "Memcached", 27015: "Steam", 37810: "DVR"}

COUNTRY = {"BR": "Brasil", "US": "Estados Unidos", "CN": "China", "RU": "Rússia", "DE": "Alemanha", "NL": "Holanda",
           "FR": "França", "GB": "Reino Unido", "IN": "Índia", "JP": "Japão", "KR": "Coreia do Sul", "SG": "Singapura",
           "HK": "Hong Kong", "TW": "Taiwan", "VN": "Vietnã", "ID": "Indonésia", "TH": "Tailândia", "UA": "Ucrânia",
           "TR": "Turquia", "IR": "Irã", "AR": "Argentina", "CL": "Chile", "CO": "Colômbia", "MX": "México",
           "PE": "Peru", "UY": "Uruguai", "PY": "Paraguai", "CA": "Canadá", "ES": "Espanha", "IT": "Itália",
           "PT": "Portugal", "PL": "Polônia", "RO": "Romênia", "SE": "Suécia", "CH": "Suíça", "IE": "Irlanda",
           "AU": "Austrália", "ZA": "África do Sul", "EG": "Egito", "PK": "Paquistão", "BD": "Bangladesh",
           "PH": "Filipinas", "MY": "Malásia", "BG": "Bulgária", "SC": "Seicheles", "LT": "Lituânia", "LV": "Letônia",
           "EE": "Estônia", "FI": "Finlândia", "NO": "Noruega", "DK": "Dinamarca", "BE": "Bélgica", "AT": "Áustria",
           "CZ": "Tchéquia", "HU": "Hungria", "IL": "Israel", "AE": "Emirados Árabes", "SA": "Arábia Saudita"}


def _ip_text(ver: int, hi: int, lo: int) -> str:
    return str(ipaddress.IPv4Address(lo & 0xFFFFFFFF)) if ver == 4 else str(ipaddress.IPv6Address((hi << 64) | lo))


def decode(gb: List[str], key: List[int], pfx, asn_name: Optional[Callable[[int], str]] = None,
           gnames: Optional[List[str]] = None, if_label: Optional[Callable[[str], str]] = None) -> Tuple[List[dict], str]:
    """Chave numérica -> [{dim, value (para filtro), label}] e o nome da linha."""
    parts = []
    i = 0
    for g in gb:
        if g in ("src_ip", "dst_ip", "src_pfx", "dst_pfx"):
            ver, hi, lo = int(key[i]), int(key[i + 1]), int(key[i + 2])
            i += 3
            ip = _ip_text(ver, hi, lo)
            if g.endswith("pfx"):
                ip = f"{ip}/{pfx[0] if ver == 4 else pfx[1]}"
            parts.append({"dim": g, "value": ip, "label": ip})
            continue
        v = int(key[i])
        i += 1
        if g in ("src_as", "dst_as"):
            nm = asn_name(v) if (asn_name and v) else ""
            parts.append({"dim": g, "value": str(v), "label": (f"AS{v} {nm}".strip() if v else "AS desconhecido / próprio")})
        elif g in ("src_cc", "dst_cc"):
            cc = _cc_str(v)
            parts.append({"dim": g, "value": cc, "label": (f"{cc} · {COUNTRY.get(cc, cc)}" if cc else "país desconhecido")})
        elif g == "proto":
            parts.append({"dim": g, "value": str(v), "label": PROTO_NAME.get(v, f"IP proto {v}")})
        elif g in ("sport", "dport"):
            parts.append({"dim": g, "value": str(v), "label": (f"{v} ({PORT_NAME[v]})" if v in PORT_NAME else
                                                                ("portas altas / efêmeras" if v == 0 else str(v)))})
        elif g == "iface":
            k = gnames[v] if gnames and v < len(gnames) else ""
            parts.append({"dim": g, "value": k, "label": (if_label(k) if if_label and k else k) or "interface não monitorada"})
        elif g == "pktsize":
            parts.append({"dim": g, "value": str(v), "label": PKT_LABEL[min(v, len(PKT_LABEL) - 1)]})
        elif g == "tcpflags":
            names = [n for b, n in TCP_FLAGS if v & b]
            parts.append({"dim": g, "value": "+".join(names), "label": ("+".join(names) if names else "sem flags / não-TCP")})
    return parts, " · ".join(p["label"] for p in parts)


# =====================================================================================
# Leitura de um ataque (assinatura + sugestão de filtro)
# =====================================================================================
AMP = {19: "Chargen", 53: "DNS", 69: "TFTP", 111: "Portmap", 123: "NTP", 137: "NetBIOS", 161: "SNMP", 389: "CLDAP",
       520: "RIP", 1900: "SSDP", 3283: "ARD", 3702: "WS-Discovery", 5353: "mDNS", 10001: "Ubiquiti", 11211: "Memcached",
       37810: "DVR", 27015: "Steam", 3478: "STUN", 1434: "MSSQL"}


def signature(victim: str, proto: List[Tuple[int, float]], sport: List[Tuple[int, float]], dport: List[Tuple[int, float]],
              pkt_size: float, flags: List[Tuple[str, float]], src_pfx: List[Tuple[str, float]], n_src_pfx: int) -> dict:
    """Resumo em texto do que o flow mostra e sugestões de filtro. Listas: (valor, participação 0..1)."""
    host = victim.split("/")[0]
    plen = "/128" if ":" in host else "/32"
    lines, rules = [], []
    p0, ps0 = (proto[0] if proto else (0, 0))
    sp0, sps = (sport[0] if sport else (None, 0))
    dp0, dps = (dport[0] if dport else (None, 0))
    pname = PROTO_NAME.get(p0, f"IP proto {p0}")
    lines.append(f"{pname} é {ps0 * 100:.0f}% do tráfego; pacote médio de {pkt_size:.0f} bytes.")
    kind = "Volumétrico"
    if p0 == 17 and sp0 is not None and sps >= 0.5:
        if sp0 == 0:
            kind = "UDP fragmentado"
            lines.append(f"{sps * 100:.0f}% sem porta de origem: fragmentos UDP (o resto da amplificação).")
            rules.append(f"Flowspec/ACL no upstream: UDP fragmentado com destino {host}{plen}.")
        elif sp0 in AMP:
            kind = f"Amplificação {AMP[sp0]}"
            lines.append(f"{sps * 100:.0f}% vem da porta UDP {sp0} ({AMP[sp0]}): refletores abertos respondendo para a vítima.")
            rules.append(f"Flowspec/ACL no upstream: UDP porta de origem {sp0} com destino {host}{plen} (descartar ou limitar).")
        else:
            kind = "UDP flood"
            rules.append(f"Flowspec/ACL: UDP porta de origem {sp0} com destino {host}{plen}.")
    elif p0 == 17:
        kind = "UDP flood"
        if dp0 and dps >= 0.5:
            rules.append(f"Flowspec/ACL: UDP porta de destino {dp0} para {host}{plen}.")
    elif p0 == 6:
        syn = next((s for f, s in flags if "SYN" in f and "ACK" not in f), 0)
        if syn >= 0.5:
            kind = "TCP SYN flood"
            lines.append(f"{syn * 100:.0f}% dos pacotes TCP são SYN sem ACK (abertura de conexão falsa); origens costumam ser falsificadas.")
            rules.append(f"SYN cookies no servidor; Flowspec TCP flags SYN (sem ACK) para {host}{plen}"
                         + (f" porta {dp0}" if dp0 and dps >= 0.5 else "") + ".")
        else:
            kind = "TCP flood"
            if dp0 and dps >= 0.5:
                rules.append(f"Limitar TCP porta de destino {dp0} para {host}{plen} no upstream.")
    elif p0 in (1, 58):
        kind = "ICMP flood"
        rules.append(f"Flowspec/ACL: limitar ICMP para {host}{plen}.")
    elif p0 == 47:
        kind = "GRE flood"
        rules.append(f"Flowspec/ACL: GRE para {host}{plen} (se o IP não usa túnel GRE, descartar).")
    if src_pfx:
        big = [(p, s) for p, s in src_pfx if s >= 0.05][:8]     # blocos que pesam de verdade
        covered = sum(s for _, s in big)
        if big and covered >= 0.6:
            lines.append(f"Origem concentrada: {len(big)} bloco(s) somam {covered * 100:.0f}% ({', '.join(p for p, _ in big)}).")
            rules.append("Bloquear/limitar na borda os blocos de origem: " + ", ".join(p for p, _ in big) + ".")
        else:
            lines.append(f"Origem espalhada por {n_src_pfx:,} blocos".replace(",", ".") + " — bloquear por origem resolve pouco.")
    rules.append(f"Último recurso: blackhole (RTBH) de {host}{plen} pelo botão Mitigar — derruba o ataque e o IP junto.")
    return {"kind": kind, "lines": lines, "rules": rules}


__all__ = ["Buffer", "pack", "write", "run", "decode", "cleanup", "disk_usage", "base_dir", "signature", "DIMS",
           "FILTERS", "DEFAULTS", "QueryError", "PKT_LABEL", "MAX_MINUTES"]
