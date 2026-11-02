"""Backup incremental de VMs ESXi por CBT (Changed Block Tracking), sem VDDK.

Como funciona (ESXi licenciado ou vCenter — a licença gratuita bloqueia a API de backup):
- a API liga o CBT na VM (uma vez), tira o snapshot do backup e diz quais áreas do disco mudaram desde o changeId
  do backup anterior (ou, no completo, quais áreas estão alocadas: "*");
- por SSH, um script Python que o BastiON manda para o ESXi lê só essas áreas do disco base (que fica somente leitura
  enquanto o snapshot existe), comprime e manda em registros; o BastiON grava no NAS;
- cada cópia é uma pasta AAAA-MM-DD_HHMMSS com point.json, o .vmx, o .nvram e disk<N>.cbt; a incremental tem o
  arquivo _incremental e aponta para a anterior. A restauração aplica o completo e as incrementais em ordem.

Formato dos registros (big-endian): offset u64, tamanho u32, comprimido u32 (0 = bloco zerado; bit 31 = zlib;
senão dados crus) e os dados. Fim de arquivo: offset = 2^64-1.
"""
import json
import posixpath
import re
import shlex
import struct
from typing import Dict, List, Optional

from vbackup import BackupError, STAMP_RE, run, stream, vmdk_extents, vmx_disks, vmx_get

FORMAT = "bastion-cbt-1"
MARK_INC = "_incremental"
END = 0xFFFFFFFFFFFFFFFF
REMOTE = "/tmp/bastion-cbt.py"
PYSET = "PY=$(which python3 2>/dev/null || which python);"
PY = f"{PYSET} $PY"

# Roda no ESXi (Python 2.7/3.x do próprio ESXi) e no nó Proxmox (python3). Só biblioteca padrão.
SCRIPT = r'''
import sys, os, struct, zlib, json
CH = 1 << 20
END = 0xFFFFFFFFFFFFFFFF
ZF = 0x80000000

def bout():
    return getattr(sys.stdout, "buffer", sys.stdout)

def bin_():
    return getattr(sys.stdin, "buffer", sys.stdin)

def read(path, extfile, level):
    ext = json.load(open(extfile))
    level = int(level)
    o = bout()
    f = open(path, "rb")
    tot = sum(int(l) for s, l in ext) or 1
    done = 0
    last = 0
    zero = b"\0" * CH
    for s, l in ext:
        s = int(s)
        end = s + int(l)
        while s < end:
            n = min(CH, end - s)
            f.seek(s)
            d = f.read(n)
            if len(d) < n:
                d += b"\0" * (n - len(d))
            if d == zero[:n]:
                o.write(struct.pack(">QII", s, n, 0))
            elif level:
                z = zlib.compress(d, level)
                o.write(struct.pack(">QII", s, n, len(z) | ZF))
                o.write(z)
            else:
                o.write(struct.pack(">QII", s, n, n))
                o.write(d)
            s += n
            done += n
            if done - last >= (64 << 20):
                sys.stderr.write("PROG %d %d\n" % (done, tot))
                sys.stderr.flush()
                last = done
    o.write(struct.pack(">QII", END, 0, 0))
    o.flush()
    sys.stderr.write("PROG %d %d\n" % (tot, tot))

def exact(i, n):
    b = b""
    while len(b) < n:
        c = i.read(n - len(b))
        if not c:
            raise SystemExit("fluxo cortado")
        b += c
    return b

def write(path, size, fresh):
    i = bin_()
    if not os.path.exists(path):
        open(path, "wb").close()
    f = open(path, "r+b")
    idx = 0
    skip_zero = fresh == "1"
    wrote = 0
    while True:
        h = i.read(16)
        if not h:
            break
        if len(h) < 16:
            h += exact(i, 16 - len(h))
        s, n, c = struct.unpack(">QII", h)
        if s == END:
            idx += 1
            continue
        if c == 0:
            if skip_zero and idx == 0:
                continue
            d = b"\0" * n
        else:
            d = exact(i, c & ~ZF & 0xFFFFFFFF)
            if c & ZF:
                d = zlib.decompress(d)
        f.seek(s)
        f.write(d)
        wrote += len(d)
    size = int(size)
    if size and os.path.getsize(path) < size:
        f.truncate(size)
    f.close()
    sys.stderr.write("OK %d\n" % wrote)

if __name__ == "__main__":
    if sys.argv[1] == "read":
        read(sys.argv[2], sys.argv[3], sys.argv[4])
    else:
        write(sys.argv[2], sys.argv[3], sys.argv[4])
'''


async def put_text(conn, path: str, data: bytes):
    proc = await conn.create_process(f"cat > {shlex.quote(path)}", encoding=None)
    proc.stdin.write(data)
    proc.stdin.write_eof()
    await proc.wait_closed()
    if proc.exit_status not in (0, None):
        raise BackupError(f"não consegui gravar {path} no host")


async def install(conn, where: str = REMOTE):
    await put_text(conn, where, SCRIPT.encode())


def ds_path(fname: str) -> str:
    """"[datastore] pasta/x.vmdk" -> /vmfs/volumes/datastore/pasta/x.vmdk"""
    m = re.match(r"\[(.+?)\]\s*(.+)$", fname or "")
    if not m:
        raise BackupError(f"caminho de disco inesperado: {fname}")
    return posixpath.join("/vmfs/volumes", m.group(1), m.group(2))


async def _read_json(sink, path: str) -> Optional[dict]:
    try:
        buf = b""
        async for b in sink.open_read(path):
            buf += b
            if len(buf) > 2_000_000:
                return None
        return json.loads(buf.decode("utf-8"))
    except Exception:
        return None


async def last_point(sink, vm_folder: str) -> Optional[dict]:
    """Última cópia CBT da VM com a cadeia inteira presente no NAS (senão None = próximo é completo)."""
    stamps = sorted(n for n in await sink.listdir(vm_folder) if STAMP_RE.match(n))
    if not stamps:
        return None
    p = await _read_json(sink, posixpath.join(vm_folder, stamps[-1], "point.json"))
    if not p or p.get("format") != FORMAT:
        return None
    chain = p.get("chain") or []
    if any(s not in stamps for s in chain):
        return None
    p["stamp"] = stamps[-1]
    return p


async def kinds(sink, vm_folder: str) -> List[tuple]:
    """[(stamp, incremental?)] em ordem."""
    out = []
    for s in sorted(n for n in await sink.listdir(vm_folder) if STAMP_RE.match(n)):
        out.append((s, MARK_INC in await sink.listdir(posixpath.join(vm_folder, s))))
    return out


async def prune_chains(sink, vm_folder: str, keep: int) -> List[str]:
    """Mantém as `keep` cópias mais novas sem quebrar cadeia: um completo só sai junto com todas as incrementais
    dele, e só quando a cadeia inteira já está fora das `keep` mais novas."""
    if keep <= 0:
        return []
    pts = await kinds(sink, vm_folder)
    chains: List[List[str]] = []
    for s, inc in pts:
        if inc and chains:
            chains[-1].append(s)
        else:
            chains.append([s])
    newest = {s for s, _ in pts[-keep:]}
    removed = []
    for ch in chains[:-1]:                       # a cadeia atual nunca sai
        if not newest.intersection(ch):
            for s in reversed(ch):
                await sink.rmtree(posixpath.join(vm_folder, s))
                removed.append(s)
    return removed


async def restore_chain(sink, folder: str) -> List[dict]:
    """Do completo até a cópia escolhida (cada item com 'stamp', 'folder' e o point.json)."""
    vm_folder, stamp = posixpath.dirname(folder), posixpath.basename(folder)
    p = await _read_json(sink, posixpath.join(folder, "point.json"))
    if not p or p.get("format") != FORMAT:
        raise BackupError("essa cópia não é um backup incremental do BastiON")
    out = []
    for s in (p.get("chain") or []) + [stamp]:
        f = posixpath.join(vm_folder, s)
        pp = p if s == stamp else await _read_json(sink, posixpath.join(f, "point.json"))
        if not pp:
            raise BackupError(f"falta a cópia {s} da cadeia no NAS (apagada?) — restaure uma cópia completa")
        out.append({"stamp": s, "folder": f, "point": pp})
    return out


async def feed(sink, chain: List[dict], disk_n: int, writer_proc, progress):
    """Manda os arquivos disk<N>.cbt da cadeia, em ordem, para o script de gravação no destino."""
    for c in chain:
        d = next((x for x in c["point"]["disks"] if x["n"] == disk_n), None)
        if not d:
            raise BackupError(f"o disco {disk_n} não existe na cópia {c['stamp']}")
        async for b in sink.open_read(posixpath.join(c["folder"], d["data"])):
            writer_proc.stdin.write(b)
            await writer_proc.stdin.drain()
            progress(len(b))


async def write_disk(conn, sink, chain, disk_n: int, target: str, size: int, fresh: bool, progress, script=REMOTE):
    proc = await conn.create_process(f"{PY} {script} write {shlex.quote(target)} {int(size)} {1 if fresh else 0}", encoding=None)
    try:
        await feed(sink, chain, disk_n, proc, progress)
        proc.stdin.write_eof()
        await proc.wait_closed()
    except BaseException:
        proc.close()
        raise
    if proc.exit_status not in (0, None):
        err = (await proc.stderr.read() or b"").decode("utf-8", "replace") if not proc.stderr.at_eof() else ""
        raise BackupError(f"o destino não gravou o disco {disk_n} (código {proc.exit_status}) {err.strip()[:200]}")


def pack(offset: int, data: bytes) -> bytes:
    """(usado nos testes) um registro com dados crus."""
    return struct.pack(">QII", offset, len(data), len(data)) + data


def need_full(prev: Optional[dict], disks: List[dict], full_every: int) -> str:
    """Motivo para fazer completo (vazio = pode ser incremental)."""
    if not prev:
        return "primeira cópia (ou a anterior não está no NAS)"
    if len(prev.get("chain") or []) + 1 >= max(1, full_every):
        return f"completo a cada {full_every} cópias"
    old = {d["key"]: d for d in prev.get("disks") or []}
    for d in disks:
        o = old.get(d["key"])
        if not o or o.get("capacity") != d["capacity"] or not o.get("change_id"):
            return "disco novo ou aumentado"
    return ""


def vmx_for_disks(vmx: str) -> Dict[str, str]:
    """basename do disco no .vmx -> caminho como está no .vmx"""
    return {posixpath.basename(d): d for d in vmx_disks(vmx)}


__all__ = ["SCRIPT", "install", "ds_path", "last_point", "prune_chains", "restore_chain", "write_disk", "need_full",
           "FORMAT", "MARK_INC", "PY", "PYSET", "REMOTE", "put_text", "stream", "vmdk_extents", "vmx_get", "run"]
