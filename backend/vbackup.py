"""Backup de VMs (Proxmox VE e VMware ESXi) direto para um NAS por SFTP, FTP ou FTPS — em fluxo, sem gravar no BastiON.

Proxmox: SSH no nó e `vzdump <id> --stdout` (backup nativo, com snapshot; restaura pela tela do Proxmox).
ESXi (inclusive o gratuito, 5.5 a 8.x): SSH no host, snapshot temporário com `vim-cmd`, cópia do .vmx (como estava antes
do snapshot), .nvram e discos (descritor + -flat compactado com gzip) e remoção do snapshot — a técnica do ghettoVCB.
"""
import asyncio
import ftplib
import posixpath
import queue
import re
import shlex
import ssl
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Awaitable, Callable, Dict, List, Optional

CHUNK = 1 << 20
IDLE_TIMEOUT = 1800             # 30 min sem chegar nada do hypervisor = travou
STAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{4}(\d{2})?$")
SNAP_NAME = "BastiON-backup"


class BackupError(Exception):
    pass


def safe(name: str, n: int = 60) -> str:
    s = re.sub(r"[^\w.-]+", "_", str(name or "")).strip("._")
    return (s or "x")[:n]


def stamp(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d_%H%M%S")


# ------------------------------------------------------------------ destinos
class Sink:
    """Interface: makedirs, open_write(path) -> writer (write/close/abort), listdir, rmtree, remove, close."""
    label = ""

    async def check(self, base: str):
        """Cria a pasta base e grava/apaga um arquivo de teste."""
        await self.makedirs(base)
        p = posixpath.join(base, ".bastion-teste")
        w = await self.open_write(p)
        await w.write(b"ok\n")
        await w.close()
        await self.remove(p)


class SftpSink(Sink):
    def __init__(self, sftp, on_close: Optional[Callable[[], Awaitable]] = None):
        self.sftp, self.on_close, self.label = sftp, on_close, "SFTP"

    async def makedirs(self, path: str):
        try:
            await self.sftp.makedirs(path, exist_ok=True)
        except Exception as e:
            raise BackupError(f"não consegui criar a pasta {path} no NAS ({type(e).__name__}: {str(e)[:120]})")

    async def open_write(self, path: str):
        f = await self.sftp.open(path, "wb")
        sftp = self.sftp

        class W:
            async def write(self, b):
                await f.write(b)

            async def close(self):
                await f.close()

            async def abort(self):
                try:
                    await f.close()
                finally:
                    try:
                        await sftp.remove(path)
                    except Exception:
                        pass
        return W()

    async def listdir(self, path: str) -> List[str]:
        try:
            return [n for n in await self.sftp.listdir(path) if n not in (".", "..")]
        except Exception:
            return []

    async def isdir(self, path: str) -> bool:
        try:
            return await self.sftp.isdir(path)
        except Exception:
            return False

    async def size(self, path: str) -> int:
        try:
            return (await self.sftp.stat(path)).size or 0
        except Exception:
            return 0

    async def open_read(self, path: str):
        """Iterador assíncrono dos blocos do arquivo."""
        f = await self.sftp.open(path, "rb")
        try:
            while True:
                b = await f.read(CHUNK)
                if not b:
                    return
                yield b
        finally:
            await f.close()

    async def remove(self, path: str):
        await self.sftp.remove(path)

    async def rmtree(self, path: str):
        await self.sftp.rmtree(path, ignore_errors=True)

    async def close(self):
        try:
            self.sftp.exit()
        except Exception:
            pass
        if self.on_close:
            await self.on_close()


class _QueueReader:
    """Arquivo para o ftplib.storbinary ler o que o lado assíncrono vai mandando."""

    def __init__(self):
        self.q: "queue.Queue" = queue.Queue(maxsize=16)
        self.buf = b""

    def read(self, n=-1):
        while not self.buf:
            item = self.q.get()
            if item is None:
                return b""
            self.buf = item
        out, self.buf = self.buf[:n], self.buf[n:]
        return out


class FtpSink(Sink):
    """ftplib (síncrono) numa thread só para ele. tls=True: FTPS explícito (AUTH TLS) com canal de dados cifrado."""

    def __init__(self, host: str, port: int, user: str, password: str, tls: bool = False, verify: bool = False, passive: bool = True):
        self.host, self.port, self.user, self.password, self.tls, self.verify, self.passive = host, port, user, password, tls, verify, passive
        self.ex = ThreadPoolExecutor(max_workers=1)
        self.ftp = None
        self.label = "FTPS" if tls else "FTP"

    async def _run(self, fn, *a):
        return await asyncio.get_running_loop().run_in_executor(self.ex, fn, *a)

    def _connect(self):
        if self.tls:
            ctx = ssl.create_default_context()
            if not self.verify:
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
            f = ftplib.FTP_TLS(context=ctx, timeout=60)
        else:
            f = ftplib.FTP(timeout=60)
        f.connect(self.host, self.port)
        f.login(self.user, self.password)
        if self.tls:
            f.prot_p()
        f.set_pasv(self.passive)
        self.ftp = f

    async def connect(self):
        try:
            await self._run(self._connect)
        except ftplib.error_perm as e:
            raise BackupError(f"o NAS recusou o login FTP ({str(e)[:120]})")
        except Exception as e:
            raise BackupError(f"não conectou no FTP {self.host}:{self.port} ({type(e).__name__}: {str(e)[:120]})")
        return self

    def _makedirs(self, path):
        cur = "/" if path.startswith("/") else ""
        for part in [p for p in path.split("/") if p]:
            cur = posixpath.join(cur, part) if cur else part
            try:
                self.ftp.mkd(cur)
            except ftplib.error_perm:
                pass            # já existe (ou sem permissão: o envio mostra o erro)

    async def makedirs(self, path: str):
        await self._run(self._makedirs, path)

    async def open_write(self, path: str):
        r = _QueueReader()
        loop = asyncio.get_running_loop()
        fut = loop.run_in_executor(self.ex, lambda: self.ftp.storbinary(f"STOR {path}", r, blocksize=CHUNK))
        sink = self

        class W:
            async def write(self, b):
                if fut.done():
                    fut.result()                       # o FTP já parou: mostra o erro
                    raise BackupError("o FTP encerrou o envio")
                await asyncio.to_thread(r.q.put, b)

            async def close(self):
                await asyncio.to_thread(r.q.put, None)
                try:
                    await fut
                except Exception as e:
                    raise BackupError(f"o NAS não aceitou {posixpath.basename(path)} ({str(e)[:150]})")

            async def abort(self):
                try:
                    await asyncio.to_thread(r.q.put, None)
                    await asyncio.wait_for(asyncio.shield(fut), 30)
                except Exception:
                    pass
                try:
                    await sink.remove(path)
                except Exception:
                    pass
        return W()

    def _nlst(self, path):
        try:
            return [posixpath.basename(n.rstrip("/")) for n in self.ftp.nlst(path)]
        except ftplib.error_perm:
            return []

    async def listdir(self, path: str) -> List[str]:
        return [n for n in await self._run(self._nlst, path) if n not in (".", "..")]

    def _isdir(self, path):
        cur = self.ftp.pwd()
        try:
            self.ftp.cwd(path)
            return True
        except ftplib.error_perm:
            return False
        finally:
            try:
                self.ftp.cwd(cur)
            except Exception:
                pass

    async def isdir(self, path: str) -> bool:
        return await self._run(self._isdir, path)

    def _size(self, path):
        try:
            self.ftp.voidcmd("TYPE I")
            return int(self.ftp.size(path) or 0)
        except Exception:
            return 0

    async def size(self, path: str) -> int:
        return await self._run(self._size, path)

    async def open_read(self, path: str):
        q: "queue.Queue" = queue.Queue(maxsize=16)
        stop = {"v": False}

        def cb(b):
            if stop["v"]:
                raise BackupError("cancelado")
            q.put(b)

        def go():
            try:
                self.ftp.retrbinary(f"RETR {path}", cb, blocksize=CHUNK)
                q.put(None)
            except Exception as e:
                q.put(e)
        fut = asyncio.get_running_loop().run_in_executor(self.ex, go)
        try:
            while True:
                item = await asyncio.to_thread(q.get)
                if item is None:
                    break
                if isinstance(item, Exception):
                    raise BackupError(f"não consegui ler {posixpath.basename(path)} do NAS ({str(item)[:150]})")
                yield item
        finally:
            stop["v"] = True
            while not fut.done():                    # destrava o callback se ele estiver esperando espaço na fila
                try:
                    q.get_nowait()
                except queue.Empty:
                    await asyncio.sleep(0.05)
            try:
                await fut
            except Exception:
                pass

    async def remove(self, path: str):
        await self._run(self.ftp.delete, path)

    def _rmtree(self, path):
        try:
            names = self._nlst(path)
        except Exception:
            names = []
        for n in names:
            if n in (".", ".."):
                continue
            p = posixpath.join(path, n)
            try:
                self.ftp.delete(p)
            except ftplib.error_perm:
                self._rmtree(p)
        try:
            self.ftp.rmd(path)
        except ftplib.error_perm:
            pass

    async def rmtree(self, path: str):
        await self._run(self._rmtree, path)

    async def close(self):
        def q():
            try:
                self.ftp.quit()
            except Exception:
                pass
        if self.ftp:
            await self._run(q)
        self.ex.shutdown(wait=False)


# ------------------------------------------------------------------ execução remota
async def run(conn, cmd: str, timeout: int = 120) -> tuple:
    r = await asyncio.wait_for(conn.run(cmd, check=False), timeout)
    out = r.stdout if isinstance(r.stdout, str) else (r.stdout or b"").decode("utf-8", "replace")
    err = r.stderr if isinstance(r.stderr, str) else (r.stderr or b"").decode("utf-8", "replace")
    return r.exit_status or 0, out, err


def _gb(n: float) -> str:
    return f"{n / 1e9:.1f} GB" if n >= 1e9 else f"{n / 1e6:.0f} MB"


async def stream(conn, cmd: str, writer, progress: Callable[[int], None], label: str = "", size: int = 0, gz: bool = False,
                 prog_err: bool = False) -> int:
    """Roda cmd no hypervisor e manda a saída (stdout) para o writer. Devolve os bytes enviados.

    Mostra o andamento real: com gz=True o BastiON descompacta só para contar quanto do disco já foi lido (o que vai
    para o NAS é bem menor que o disco); com size, mostra a porcentagem. A saída do vzdump (INFO: 35% ...) também vira
    andamento."""
    import zlib
    proc = await conn.create_process(cmd, encoding=None)
    total, raw = 0, 0
    err = bytearray()
    inflate = zlib.decompressobj(16 + zlib.MAX_WBITS) if gz else None
    set_stage = getattr(progress, "set_stage", None)
    add_raw = getattr(progress, "add_raw", None)
    t0 = time.time()

    def show():
        if not set_stage or not label:
            return
        dt = max(time.time() - t0, 0.001)
        pct = f" {min(100, 100 * raw / size):.0f}%" if size else ""
        set_stage(f"copiando {label}:{pct} ({_gb(raw)}{' de ' + _gb(size) if size else ''} lidos, {raw / dt / 1e6:.0f} MB/s)")

    async def drain_err():
        buf = b""
        while True:
            b = await proc.stderr.read(65536)
            if not b:
                return
            err.extend(b[-4000:])
            del err[:-8000]
            buf = (buf + b)[-4000:]
            m = None
            for m in re.finditer(rb"INFO:\s+(\d+)% \((.+?) of (.+?)\) in .*?read: ([\d.]+ \w+/s)", buf):
                pass
            if prog_err:                                    # script do CBT: "PROG lidos total"
                pm = None
                for pm in re.finditer(rb"PROG (\d+) (\d+)", buf):
                    pass
                if pm:
                    nonlocal raw
                    done_, tot_ = int(pm.group(1)), int(pm.group(2))
                    if add_raw and done_ > raw:
                        add_raw(done_ - raw)
                    raw = max(raw, done_)
                    if set_stage and label:
                        dt = max(time.time() - t0, 0.001)
                        set_stage(f"copiando {label}: {min(100, 100 * done_ / max(tot_, 1)):.0f}% ({_gb(done_)} de {_gb(tot_)} alterados/usados, {done_ / dt / 1e6:.0f} MB/s)")
            if m and set_stage:
                set_stage(f"vzdump {m.group(1).decode()}% ({m.group(2).decode()} de {m.group(3).decode()}, leitura {m.group(4).decode()})")
    et = asyncio.create_task(drain_err())
    try:
        while True:
            b = await asyncio.wait_for(proc.stdout.read(CHUNK), IDLE_TIMEOUT)
            if not b:
                break
            await writer.write(b)
            total += len(b)
            progress(len(b))
            if prog_err:
                n = 0                                       # quem conta o lido é o script (PROG)
            elif inflate is not None:
                try:
                    n = 0
                    data = inflate.decompress(b, CHUNK * 64)
                    n += len(data)
                    while inflate.unconsumed_tail:
                        data = inflate.decompress(inflate.unconsumed_tail, CHUNK * 64)
                        n += len(data)
                except zlib.error:
                    inflate, n = None, 0
            else:
                n = len(b)
            if not prog_err:
                raw += n
                if add_raw:
                    add_raw(n)
                show()
        await asyncio.wait_for(proc.wait_closed(), 120)
        await asyncio.wait_for(et, 30)
    except asyncio.TimeoutError:
        raise BackupError("o hypervisor parou de mandar dados (30 min sem nada)")
    finally:
        if not et.done():
            et.cancel()
        try:
            proc.close()
        except Exception:
            pass
    code = proc.exit_status
    if code not in (0, None) or (code is None and proc.exit_signal):
        msg = bytes(err).decode("utf-8", "replace").strip().splitlines()
        tail = " | ".join(x for x in msg[-3:] if x.strip())[:300]
        raise BackupError(f"o comando falhou no hypervisor (código {code}): {tail or 'sem mensagem'}")
    return total


# ------------------------------------------------------------------ Proxmox
async def pve_version(conn) -> tuple:
    code, out, _ = await run(conn, "pveversion")
    m = re.search(r"pve-manager/(\d+)\.(\d+)", out)
    if code or not m:
        raise BackupError("este host não parece ser um nó Proxmox (pveversion não respondeu)")
    return int(m.group(1)), int(m.group(2))


async def backup_proxmox(conn, vm: dict, sink: Sink, folder: str, mode: str, progress, now: datetime) -> dict:
    """vm: {vmid, type qemu|lxc}. Gera o arquivo com o nome que o Proxmox reconhece na restauração."""
    major, minor = await pve_version(conn)
    comp, ext = ("zstd", "zst") if (major, minor) >= (6, 2) else ("gzip", "gz")
    kind = "lxc" if vm.get("type") == "lxc" else "qemu"
    fmt = "tar" if kind == "lxc" else "vma"
    vmid = int(vm["vmid"])
    mode = mode if mode in ("snapshot", "suspend", "stop") else "snapshot"
    name = f"vzdump-{kind}-{vmid}-{now.strftime('%Y_%m_%d-%H_%M_%S')}.{fmt}.{ext}"
    path = posixpath.join(folder, name)
    await sink.makedirs(folder)
    w = await sink.open_write(path)
    try:
        threads = " --zstd 0" if comp == "zstd" and major >= 7 else ""        # zstd com metade dos núcleos do nó
        n = await stream(conn, f"vzdump {vmid} --mode {mode} --compress {comp}{threads} --stdout", w, progress)
        if n < 1024:
            raise BackupError("o vzdump não gerou dados")
        await w.close()
    except BaseException:
        await w.abort()
        raise
    return {"bytes": n, "files": [name], "format": f"vzdump {fmt}.{ext}"}


# ------------------------------------------------------------------ ESXi
def vmx_disks(vmx: str) -> List[str]:
    """Discos presentes do .vmx (ignora CD/DVD e o que estiver com present = FALSE)."""
    kv = {}
    for line in vmx.splitlines():
        m = re.match(r'\s*([\w:.]+)\s*=\s*"(.*)"\s*$', line)
        if m:
            kv[m.group(1).lower()] = m.group(2)
    out = []
    for k, v in kv.items():
        m = re.fullmatch(r"((?:scsi|sata|ide|nvme)\d+:\d+)\.filename", k)
        if not m or not v.lower().endswith(".vmdk"):
            continue
        dev = m.group(1)
        if kv.get(f"{dev}.present", "true").lower() == "false" or "cdrom" in kv.get(f"{dev}.devicetype", "").lower():
            continue
        out.append(v)
    return out


def vmx_get(vmx: str, key: str) -> str:
    m = re.search(rf'^\s*{re.escape(key)}\s*=\s*"(.*)"\s*$', vmx, re.M | re.I)
    return m.group(1) if m else ""


def vmdk_extents(desc: str) -> List[str]:
    return re.findall(r'^\s*RW\s+\d+\s+\w+\s+"(.+?)"', desc, re.M)


def parse_getallvms(out: str) -> List[dict]:
    vms = []
    for line in out.splitlines():
        m = re.match(r"^(\d+)\s+(.+?)\s+\[(.+?)\]\s+(.+?\.vmx)\s", line + " ")
        if m:
            vms.append({"vmid": m.group(1), "name": m.group(2).strip(), "ds": m.group(3), "rel": m.group(4)})
    return vms


def snapshot_ids(out: str) -> List[tuple]:
    """[(nome, id)] da saída do `vim-cmd vmsvc/snapshot.get`."""
    return [(n.strip(), i) for n, i in re.findall(r"Snapshot Name\s*:\s*(.*?)\s*\n[-\s|]*Snapshot Id\s*:\s*(\d+)", out)]


def _parent_hint(desc: str) -> str:
    m = re.search(r'^\s*parentFileNameHint\s*=\s*"(.+?)"', desc, re.M)
    return m.group(1) if m else ""


async def backup_esxi(conn, vm: dict, sink: Sink, folder: str, compress: bool, quiesce: bool, progress, consolidate: bool = False,
                      readme: bool = True) -> dict:
    """vm: {name, vmx: "[datastore] pasta/vm.vmx"} (o vmid local é achado pelo caminho do .vmx).

    Copia o estado atual da VM. Se ela já tiver snapshots (ex.: réplicas do Veeam), vai a cadeia inteira de discos
    (base + deltas) e o .vmsd/.vmsn, então a cópia restaura com os mesmos snapshots. Tudo vai para uma pasta só, com
    os caminhos dos descritores e do .vmx reescritos para o nome do arquivo."""
    code, out, err = await run(conn, "vim-cmd vmsvc/getallvms")
    if code:
        raise BackupError(f"vim-cmd não respondeu: é um ESXi com SSH? ({(err or out)[:150]})")
    lst = parse_getallvms(out)
    target = None
    m = re.match(r"\[(.+?)\]\s*(.+)$", vm.get("vmx") or "")
    if m:
        target = next((x for x in lst if x["ds"] == m.group(1) and x["rel"] == m.group(2)), None)
    if not target:
        target = next((x for x in lst if x["name"] == vm.get("name")), None)
    if not target:
        raise BackupError("a VM não foi encontrada neste host ESXi (foi movida para outro host?)")
    vid = target["vmid"]
    vdir = posixpath.join("/vmfs/volumes", target["ds"], posixpath.dirname(target["rel"]))
    vmx_path = posixpath.join("/vmfs/volumes", target["ds"], target["rel"])

    def absolute(p, base=vdir):
        return p if p.startswith("/") else posixpath.normpath(posixpath.join(base, p))

    # sobrou snapshot nosso de um backup interrompido: remove antes (consolida no disco atual)
    code, sg, _ = await run(conn, f"vim-cmd vmsvc/snapshot.get {vid}")
    for name, sid in snapshot_ids(sg):
        if name == SNAP_NAME:
            await run(conn, f"vim-cmd vmsvc/snapshot.remove {vid} {sid}", 7200)
    code, sg, _ = await run(conn, f"vim-cmd vmsvc/snapshot.get {vid}")
    had_snaps = bool(snapshot_ids(sg))

    code, vmx, err = await run(conn, f"cat {shlex.quote(vmx_path)}")
    if code:
        raise BackupError(f"não li o .vmx ({err[:150]})")
    top = [absolute(d) for d in vmx_disks(vmx)]
    chain: Dict[str, str] = {}                       # descritor -> conteúdo (atual + pais até o disco base)
    chain_of: Dict[str, List[str]] = {}              # disco do .vmx -> cadeia dele (atual ... base)
    for d in top:
        cur = d
        chain_of[d] = []
        while cur and cur not in chain_of[d] and len(chain_of[d]) < 64:
            chain_of[d].append(cur)
            if cur in chain:
                hint = _parent_hint(chain[cur])
                cur = absolute(hint, posixpath.dirname(cur)) if hint else None
                continue
            code, desc, err = await run(conn, f"cat {shlex.quote(cur)}")
            if code:
                raise BackupError(f"não li o disco {cur} ({err[:120]})")
            if "\x00" in desc[:2000]:
                raise BackupError(f"{posixpath.basename(cur)} não é um descritor de disco (disco antigo monolítico): converta com vmkfstools")
            chain[cur] = desc
            hint = _parent_hint(desc)
            cur = absolute(hint, posixpath.dirname(cur)) if hint else None
    # nomes na pasta do backup (sem colisão entre datastores)
    used, newname = set(), {}

    def name_for(path):
        if path in newname:
            return newname[path]
        b = posixpath.basename(path)
        n = b if b not in used else f"{len(used)}_{b}"
        used.add(n)
        newname[path] = n
        return n
    vmx_name = name_for(vmx_path)
    for d in chain:
        name_for(d)
    extents = {}                                     # caminho do extent -> nome
    for d, desc in chain.items():
        for e in vmdk_extents(desc):
            extents[absolute(e, posixpath.dirname(d))] = name_for(absolute(e, posixpath.dirname(d)))

    def fix_desc(d, desc):
        for e in vmdk_extents(desc):
            desc = desc.replace(f'"{e}"', f'"{extents[absolute(e, posixpath.dirname(d))]}"')
        hint = _parent_hint(desc)
        if hint:
            desc = re.sub(r'^(\s*parentFileNameHint\s*=\s*)".+?"', lambda mm: f'{mm.group(1)}"{newname[absolute(hint, posixpath.dirname(d))]}"', desc, flags=re.M)
        return desc

    def fix_vmx(text, override=None):
        def rep(mm):
            v = mm.group(2)
            if override and absolute(v) in override:
                return f'{mm.group(1)}"{override[absolute(v)]}"'
            if v.lower().endswith(".vmdk") and absolute(v) in newname:
                return f'{mm.group(1)}"{newname[absolute(v)]}"'
            return mm.group(0)
        return re.sub(r'^(\s*(?:scsi|sata|ide|nvme)\d+:\d+\.fileName\s*=\s*)"(.*)"', rep, text, flags=re.M | re.I)

    # arquivos dos snapshots existentes (.vmsd muda com o nosso snapshot: guarda o conteúdo de agora)
    snap_files, vmsd = [], None
    if had_snaps:
        code, ls, _ = await run(conn, f"ls -1 {shlex.quote(vdir)}")
        for n in ls.splitlines():
            n = n.strip()
            if n.endswith(".vmsd"):
                code, txt, _ = await run(conn, f"cat {shlex.quote(posixpath.join(vdir, n))}")
                if code == 0:
                    vmsd = (name_for(posixpath.join(vdir, n)), txt)
            elif re.search(r"-Snapshot\d+\.(vmsn|vmem)$", n):
                snap_files.append(posixpath.join(vdir, n))
    code, st, _ = await run(conn, f"vim-cmd vmsvc/power.getstate {vid}")
    on = "Powered on" in st
    files, total = [], 0
    await sink.makedirs(folder)

    async def put_bytes(name, data: bytes):
        nonlocal total
        w = await sink.open_write(posixpath.join(folder, name))
        await w.write(data)
        await w.close()
        files.append(name)
        total += len(data)
        progress(len(data))

    async def put_remote(path, name, optional=False):
        nonlocal total
        _stage(f"copiando {name}")
        dest = name + (".gz" if compress else "")
        code, ls, _ = await run(conn, f"ls -l {shlex.quote(path)}")
        parts = ls.split()
        size = int(parts[4]) if code == 0 and len(parts) > 4 and parts[4].isdigit() else 0
        w = await sink.open_write(posixpath.join(folder, dest))
        try:
            n = await stream(conn, (f"gzip -1 -c {shlex.quote(path)}" if compress else f"cat {shlex.quote(path)}"), w, progress,
                             label=name, size=size, gz=compress)
            await w.close()
        except BackupError as e:
            await w.abort()
            if optional and "busy" in str(e).lower():
                notes.append(f"{posixpath.basename(path)} em uso pela VM: não copiado")
                return
            raise
        except BaseException:
            await w.abort()
            raise
        files.append(dest)
        total += n

    def _stage(t):
        if hasattr(progress, "set_stage"):
            progress.set_stage(t)
        elif hasattr(progress, "stage"):
            progress.stage = t

    async def readable(path) -> bool:
        code, _, _ = await run(conn, f"dd if={shlex.quote(path)} of=/dev/null bs=4096 count=1")
        return code == 0

    notes: List[str] = []
    tmpdirs: List[str] = []
    snap = False
    try:
        if on:
            _stage("criando snapshot temporário")
            code, out, err = await run(conn, f"vim-cmd vmsvc/snapshot.create {vid} {SNAP_NAME} 'snapshot temporario do backup' 0 {1 if quiesce else 0}", 1800)
            if code or "failed" in (out + err).lower() or "fault" in (out + err).lower():
                raise BackupError(f"o ESXi não criou o snapshot ({(err or out).strip()[:200]})")
            snap = True
        # disco que a VM ligada mantém travado (comum com snapshots/SEsparse): não dá para ler o arquivo direto,
        # então o vmkfstools clona a cadeia num disco thin temporário no mesmo datastore e a cópia sai dele
        clone = []
        for t in top:
            if consolidate and len(chain_of[t]) > 1:       # destino que não entende delta (Proxmox): um disco só
                clone.append(t)
                continue
            for d in chain_of[t]:
                exts = [absolute(e, posixpath.dirname(d)) for e in vmdk_extents(chain[d])]
                if not all([await readable(x) for x in exts]):
                    clone.append(t)
                    break
        override = {t: newname[chain_of[t][-1]] for t in clone}           # o .vmx aponta para o disco consolidado
        await put_bytes(vmx_name, fix_vmx(vmx, override).encode())         # o .vmx de antes do snapshot
        nv = vmx_get(vmx, "nvram")
        if nv:
            code, _, _ = await run(conn, f"test -f {shlex.quote(absolute(nv))}")
            if code == 0:
                await put_remote(absolute(nv), posixpath.basename(nv), optional=True)
        if vmsd and not clone:
            await put_bytes(vmsd[0], vmsd[1].encode())
            for p in snap_files:
                await put_remote(p, name_for(p), optional=True)
        elif had_snaps:
            notes.append("discos consolidados na cópia (os snapshots da VM não vão junto)")
        sent = set()
        for t in top:
            if t in clone:
                continue
            for d in chain_of[t]:
                if d in sent:
                    continue
                sent.add(d)
                await put_bytes(newname[d], fix_desc(d, chain[d]).encode())
                for e in vmdk_extents(chain[d]):
                    x = absolute(e, posixpath.dirname(d))
                    await put_remote(x, extents[x])
        for i, t in enumerate(clone):
            tmp = posixpath.join(posixpath.dirname(t), f".bastion-tmp-{vid}")
            tmpdirs.append(tmp)
            out_vmdk = posixpath.join(tmp, f"d{i}.vmdk")
            await run(conn, f"rm -rf {shlex.quote(tmp)}; mkdir -p {shlex.quote(tmp)}")
            _stage(f"consolidando {posixpath.basename(t)} com vmkfstools (pode demorar; sem bytes enviados nessa fase)")
            code, out, err = await run(conn, f"vmkfstools -i {shlex.quote(t)} -d thin {shlex.quote(out_vmdk)}", 12 * 3600)
            if code:
                msg = (err or out).strip().splitlines()
                raise BackupError(f"o vmkfstools não clonou {posixpath.basename(t)} (falta espaço no datastore?): {' '.join(msg[-2:])[:200]}")
            code, desc, err = await run(conn, f"cat {shlex.quote(out_vmdk)}")
            name = override[t]
            stem = name[:-5] if name.lower().endswith(".vmdk") else name
            ex = vmdk_extents(desc)
            if code or len(ex) != 1:
                raise BackupError(f"o disco clonado de {posixpath.basename(t)} veio estranho ({err[:120]})")
            await put_bytes(name, desc.replace(f'"{ex[0]}"', f'"{stem}-flat.vmdk"').encode())
            await put_remote(absolute(ex[0], tmp), f"{stem}-flat.vmdk")
            await run(conn, f"rm -rf {shlex.quote(tmp)}", 600)
            tmpdirs.remove(tmp)
        info = (f"Backup da VM {target['name']} feito pelo BastiON em {datetime.now().strftime('%d/%m/%Y %H:%M')}\n"
                f"Origem: [{target['ds']}] {target['rel']}\n"
                + (f"A VM tinha snapshots: a cadeia inteira de discos veio junto ({len(chain)} descritores).\n" if had_snaps and not clone else "")
                + ("Discos consolidados (vmkfstools) porque a VM ligada mantinha os arquivos travados.\n" if clone else "")
                + "".join(f"Obs.: {n}\n" for n in notes)
                + "\nPara restaurar:\n"
                "1. Crie uma pasta no datastore e envie estes arquivos para ela (navegador de datastore ou scp).\n"
                + ("2. No ESXi (SSH), descompacte: gunzip *.gz\n" if compress else "")
                + "3. Clique com o botão direito no .vmx -> Registrar VM (ou: vim-cmd solo/registervm /vmfs/volumes/<ds>/<pasta>/<vm>.vmx).\n"
                "4. Na primeira vez que ligar, responda 'Eu movi'/'I moved it'.\n")
        if readme:
            await put_bytes("RESTAURAR.txt", info.encode())
    finally:
        for tmp in tmpdirs:
            try:
                await run(conn, f"rm -rf {shlex.quote(tmp)}", 600)
            except Exception:
                pass
        if snap:
            _stage("removendo o snapshot temporário")
            try:
                if not had_snaps:
                    await run(conn, f"vim-cmd vmsvc/snapshot.removeall {vid}", 7200)
                else:                                                         # só o nosso; os da VM ficam
                    code, sg, _ = await run(conn, f"vim-cmd vmsvc/snapshot.get {vid}")
                    for name, sid in snapshot_ids(sg):
                        if name == SNAP_NAME:
                            await run(conn, f"vim-cmd vmsvc/snapshot.remove {vid} {sid}", 7200)
            except Exception:
                pass
    return {"bytes": total, "files": files, "format": "vmx + vmdk" + (" (gzip)" if compress else "") + (" + cadeia de snapshots" if had_snaps and not clone else " (discos consolidados)" if clone else ""),
            "was_on": on, "note": "; ".join(notes)}


# ------------------------------------------------------------------ retenção
async def prune(sink: Sink, vm_folder: str, keep: int) -> List[str]:
    """Mantém as `keep` cópias mais novas (pastas AAAA-MM-DD_HHMMSS) da VM."""
    if keep <= 0:
        return []
    olds = sorted(n for n in await sink.listdir(vm_folder) if STAMP_RE.match(n))[:-keep]
    for n in olds:
        await sink.rmtree(posixpath.join(vm_folder, n))
    return olds


def due(job: dict, now_local: datetime) -> Optional[str]:
    """Horário (AAAA-MM-DD HH:MM) que a rotina deve rodar agora, se já passou e ainda não rodou (até 6 h de atraso)."""
    if not job.get("enabled", True):
        return None
    days = job.get("days") or [0, 1, 2, 3, 4, 5, 6]
    if now_local.weekday() not in days:
        return None
    try:
        hh, mm = [int(x) for x in (job.get("time") or "01:00").split(":")]
    except ValueError:
        return None
    slot = now_local.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if now_local < slot or (now_local - slot).total_seconds() > 6 * 3600:
        return None
    key = slot.strftime("%Y-%m-%d %H:%M")
    return None if job.get("last_slot") == key else key


class Progress:
    """Andamento de uma execução. bytes = enviados ao destino; raw = lidos dos discos (antes da compressão).
    child(nome) cria o andamento de uma VM rodando em paralelo (soma no total e tem a etapa própria)."""

    def __init__(self, name: str = "", parent: "Optional[Progress]" = None):
        self.bytes, self.raw, self.t0, self._last = 0, 0, time.time(), 0
        self.stage = ""                  # o que está fazendo agora (mostrado na tela)
        self.name, self.parent = name, parent
        self.children: List["Progress"] = []

    def __call__(self, n: int):
        self.bytes += n
        if self.parent:
            self.parent(n)

    def add_raw(self, n: int):
        self.raw += n
        if self.parent:
            self.parent.add_raw(n)

    def set_stage(self, t: str):
        self.stage = t

    def child(self, name: str) -> "Progress":
        c = Progress(name, self)
        self.children.append(c)
        return c

    def done(self, c: "Progress"):
        if c in self.children:
            self.children.remove(c)

    def describe(self) -> str:
        if self.children:
            return " | ".join(f"{c.name}: {c.stage}" if c.stage else c.name for c in self.children)
        return self.stage

    def rate(self) -> float:
        dt = time.time() - self.t0
        return self.bytes / dt if dt > 0 else 0.0

    def raw_rate(self) -> float:
        dt = time.time() - self.t0
        return self.raw / dt if dt > 0 else 0.0

