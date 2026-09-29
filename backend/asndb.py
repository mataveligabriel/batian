"""Base IP -> ASN (iptoasn.com, domínio público/PDDL): download, conversão, gravação no Mongo e busca em memória.

O arquivo ip2asn-combined.tsv.gz (≈ 8 MB) tem faixas "início fim ASN país descrição". Guardamos as faixas em
arrays compactos (≈ 10 MB) dentro do Mongo em pedaços, assim o coletor e a API carregam a mesma base sem volume.
"""
import bisect
import gzip
import io
import ipaddress
import json
import struct
import sys
import zlib
from array import array
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

URL = "https://iptoasn.com/data/ip2asn-combined.tsv.gz"
CHUNK = 12 * 1024 * 1024
MAGIC = b"BASN1"


def _arr(code: str) -> array:
    a = array(code)
    if code == "I" and a.itemsize != 4:
        a = array("L") if array("L").itemsize == 4 else array("I")
    return a


def parse_tsv(raw: bytes) -> Tuple[list, list, Dict[int, Tuple[str, str]]]:
    """-> (faixas v4, faixas v6, nomes) com faixas [(início, fim, asn)] ordenadas e juntadas quando contíguas."""
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    r4: list = []
    r6: list = []
    names: Dict[int, Tuple[str, str]] = {}
    for line in io.BytesIO(raw):
        parts = line.rstrip(b"\r\n").split(b"\t")
        if len(parts) < 3:
            continue
        try:
            asn = int(parts[2])
            if not asn:
                continue
            a, b = ipaddress.ip_address(parts[0].decode()), ipaddress.ip_address(parts[1].decode())
        except ValueError:
            continue
        if asn not in names and len(parts) >= 5:
            names[asn] = (parts[4].decode("utf-8", "replace").strip()[:80], parts[3].decode("ascii", "replace").strip()[:3])
        dst = r4 if a.version == 4 else r6
        s, e = int(a), int(b)
        if dst and dst[-1][2] == asn and dst[-1][1] + 1 == s:
            dst[-1] = (dst[-1][0], e, asn)
        else:
            dst.append((s, e, asn))
    r4.sort()
    r6.sort()
    return r4, r6, names


def pack(r4: list, r6: list, names: Dict[int, Tuple[str, str]]) -> bytes:
    s4, e4, a4 = _arr("I"), _arr("I"), _arr("I")
    for s, e, a in r4:
        s4.append(s)
        e4.append(e)
        a4.append(a)
    a6 = _arr("I")
    b6 = bytearray()
    for s, e, a in r6:
        b6 += s.to_bytes(16, "big") + e.to_bytes(16, "big")
        a6.append(a)
    if sys.byteorder != "little":
        for x in (s4, e4, a4, a6):
            x.byteswap()
    nm = json.dumps({str(k): v for k, v in names.items()}, ensure_ascii=False).encode()
    body = struct.pack("<III", len(r4), len(r6), len(nm)) + s4.tobytes() + e4.tobytes() + a4.tobytes() + bytes(b6) + a6.tobytes() + nm
    return MAGIC + zlib.compress(body, 6)


class AsnDb:
    def __init__(self, s4, e4, a4, s6, e6, a6, names):
        self.s4, self.e4, self.a4, self.s6, self.e6, self.a6, self.names = s4, e4, a4, s6, e6, a6, names

    @classmethod
    def unpack(cls, blob: bytes) -> "AsnDb":
        if not blob.startswith(MAGIC):
            raise ValueError("base de ASN inválida")
        body = zlib.decompress(blob[len(MAGIC):])
        n4, n6, nl = struct.unpack_from("<III", body, 0)
        off = 12

        def take(n):
            nonlocal off
            a = _arr("I")
            a.frombytes(body[off:off + 4 * n])
            if sys.byteorder != "little":
                a.byteswap()
            off += 4 * n
            return a
        s4, e4, a4 = take(n4), take(n4), take(n4)
        raw6 = body[off:off + 32 * n6]
        off += 32 * n6
        s6 = [int.from_bytes(raw6[i:i + 16], "big") for i in range(0, len(raw6), 32)]
        e6 = [int.from_bytes(raw6[i + 16:i + 32], "big") for i in range(0, len(raw6), 32)]
        a6 = take(n6)
        names = {int(k): tuple(v) for k, v in json.loads(body[off:off + nl].decode()).items()}
        return cls(s4, e4, a4, s6, e6, a6, names)

    def lookup(self, ver: int, ip: int) -> int:
        s, e, a = (self.s4, self.e4, self.a4) if ver == 4 else (self.s6, self.e6, self.a6)
        i = bisect.bisect_right(s, ip) - 1
        if i >= 0 and ip <= e[i]:
            return a[i]
        return 0

    def range_of(self, ip: str) -> Optional[dict]:
        a = ipaddress.ip_address(ip.strip())
        v = int(a)
        s, e, asn = (self.s4, self.e4, self.a4) if a.version == 4 else (self.s6, self.e6, self.a6)
        i = bisect.bisect_right(s, v) - 1
        if i < 0 or v > e[i]:
            return None
        cls = ipaddress.IPv4Address if a.version == 4 else ipaddress.IPv6Address
        nets = [str(n) for n in ipaddress.summarize_address_range(cls(s[i]), cls(e[i]))]
        return {"asn": asn[i], "prefixes": nets[:20]}

    def prefixes_of(self, asn: int, limit: int = 400) -> Dict[str, List[str]]:
        out = {"v4": [], "v6": []}
        for ver, s, e, a, cls in ((4, self.s4, self.e4, self.a4, ipaddress.IPv4Address),
                                  (6, self.s6, self.e6, self.a6, ipaddress.IPv6Address)):
            nets = []
            for i, x in enumerate(a):
                if x == asn:
                    nets.extend(ipaddress.summarize_address_range(cls(s[i]), cls(e[i])))
            nets = list(ipaddress.collapse_addresses(nets))
            out[f"v{ver}"] = [str(n) for n in nets[:limit]]
            out[f"v{ver}_total"] = len(nets)
        return out

    def name(self, asn: int) -> str:
        n = self.names.get(int(asn))
        return n[0] if n else ""

    def search(self, q: str, limit: int = 20) -> List[dict]:
        q = q.strip().lower()
        out = []
        for asn, (nm, cc) in self.names.items():
            if q in nm.lower():
                out.append({"asn": asn, "name": nm, "cc": cc})
                if len(out) >= limit * 5:
                    break
        out.sort(key=lambda x: (not x["name"].lower().startswith(q), len(x["name"])))
        return out[:limit]

    def __len__(self):
        return len(self.s4) + len(self.s6)


# ---------- Mongo ----------
async def save(db, blob: bytes, meta: dict):
    """Grava em pedaços (limite de 16 MB por documento) e troca a versão no fim (leitores nunca veem meia base)."""
    ver = int(datetime.now(timezone.utc).timestamp())
    parts = [blob[i:i + CHUNK] for i in range(0, len(blob), CHUNK)]
    for i, p in enumerate(parts):
        await db.asn_db.replace_one({"_id": f"{ver}-{i}"}, {"_id": f"{ver}-{i}", "ver": ver, "i": i, "data": p}, upsert=True)
    await db.asn_db.replace_one({"_id": "meta"}, {"_id": "meta", **meta, "ver": ver, "parts": len(parts),
                                                   "size": len(blob), "at": datetime.now(timezone.utc).isoformat()},
                                upsert=True)
    await db.asn_db.delete_many({"_id": {"$ne": "meta"}, "ver": {"$ne": ver}})
    return ver


async def get_meta(db) -> Optional[dict]:
    return await db.asn_db.find_one({"_id": "meta"})


async def load(db, meta: Optional[dict] = None) -> Optional[AsnDb]:
    """Carrega a base gravada (a descompressão roda numa thread para não travar o loop)."""
    import asyncio
    meta = meta or await get_meta(db)
    if not meta:
        return None
    chunks = await db.asn_db.find({"ver": meta["ver"], "_id": {"$ne": "meta"}}).sort("i", 1).to_list(100)
    if len(chunks) != meta.get("parts"):
        return None
    blob = b"".join(bytes(c["data"]) for c in chunks)
    return await asyncio.to_thread(AsnDb.unpack, blob)


async def import_raw(db, raw: bytes, source: str) -> dict:
    """TSV (ou .gz) do iptoasn -> grava no Mongo. Devolve o resumo."""
    import asyncio

    def build():
        r4, r6, names = parse_tsv(raw)
        if len(r4) < 1000:
            raise ValueError("arquivo não parece o ip2asn (poucas faixas IPv4)")
        return pack(r4, r6, names), len(r4), len(r6), len(names)
    blob, n4, n6, nn = await asyncio.to_thread(build)
    meta = {"source": source, "ranges_v4": n4, "ranges_v6": n6, "asns": nn}
    await save(db, blob, meta)
    return meta


async def download(url: str = URL, timeout: float = 180) -> bytes:
    import httpx
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            r = await client.get(url)
    except httpx.ConnectError:
        # servidor com IPv6 configurado mas sem rota: tenta de novo só por IPv4
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True,
                                     transport=httpx.AsyncHTTPTransport(local_address="0.0.0.0")) as client:
            r = await client.get(url)
    r.raise_for_status()
    return r.content
