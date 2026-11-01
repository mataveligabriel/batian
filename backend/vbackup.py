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
from typing import Awaitable, Callable, List, Optional

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


async def stream(conn, cmd: str, writer, progress: Callable[[int], None]) -> int:
    """Roda cmd no hypervisor e manda a saída (stdout) para o writer. Devolve os bytes enviados."""
    proc = await conn.create_process(cmd, encoding=None)
    total = 0
    err = bytearray()

    async def drain_err():
        while True:
            b = await proc.stderr.read(65536)
            if not b:
                return
            err.extend(b[-4000:])
            del err[:-8000]
    et = asyncio.create_task(drain_err())
    try:
        while True:
            b = await asyncio.wait_for(proc.stdout.read(CHUNK), IDLE_TIMEOUT)
            if not b:
                break
            await writer.write(b)
            total += len(b)
            progress(len(b))
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
        n = await stream(conn, f"vzdump {vmid} --mode {mode} --compress {comp} --stdout", w, progress)
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


async def backup_esxi(conn, vm: dict, sink: Sink, folder: str, compress: bool, quiesce: bool, progress) -> dict:
    """vm: {name, vmx: "[datastore] pasta/vm.vmx"} (o vmid local é achado pelo caminho do .vmx)."""
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
    code, vmx, err = await run(conn, f"cat {shlex.quote(vmx_path)}")
    if code:
        raise BackupError(f"não li o .vmx ({err[:150]})")

    def absolute(p):
        return p if p.startswith("/") else posixpath.join(vdir, p)
    disks = [absolute(d) for d in vmx_disks(vmx)]
    if any(re.search(r"-\d{6}\.vmdk$", d) for d in disks):
        raise BackupError("a VM já tem snapshots: apague ou consolide os snapshots antes (o backup copia só o disco base)")
    descs = {}
    for d in disks:
        code, desc, err = await run(conn, f"cat {shlex.quote(d)}")
        if code:
            raise BackupError(f"não li o disco {d} ({err[:120]})")
        if "\x00" in desc[:2000]:
            raise BackupError(f"{posixpath.basename(d)} não é um descritor de disco (disco antigo monolítico): converta com vmkfstools")
        descs[d] = desc
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

    async def put_remote(path, name):
        nonlocal total
        dest = name + (".gz" if compress else "")
        w = await sink.open_write(posixpath.join(folder, dest))
        try:
            n = await stream(conn, (f"gzip -1 -c {shlex.quote(path)}" if compress else f"cat {shlex.quote(path)}"), w, progress)
            await w.close()
        except BaseException:
            await w.abort()
            raise
        files.append(dest)
        total += n

    snap = False
    try:
        if on:
            code, out, err = await run(conn, f"vim-cmd vmsvc/snapshot.create {vid} {SNAP_NAME} 'snapshot temporario do backup' 0 {1 if quiesce else 0}", 1800)
            if code or "failed" in (out + err).lower() or "fault" in (out + err).lower():
                raise BackupError(f"o ESXi não criou o snapshot ({(err or out).strip()[:200]})")
            snap = True
        await put_bytes(posixpath.basename(vmx_path), vmx.encode())             # o .vmx de antes do snapshot
        nv = vmx_get(vmx, "nvram")
        if nv:
            code, _, _ = await run(conn, f"test -f {shlex.quote(absolute(nv))}")
            if code == 0:
                await put_remote(absolute(nv), posixpath.basename(nv))
        names = set()
        for d, desc in descs.items():
            base = posixpath.basename(d)
            if base in names:                                                       # discos com mesmo nome em datastores diferentes
                base = f"{len(names)}_{base}"
            names.add(base)
            await put_bytes(base, desc.encode())
            for ext in vmdk_extents(desc):
                await put_remote(posixpath.join(posixpath.dirname(d), ext), posixpath.basename(ext))
        info = (f"Backup da VM {target['name']} feito pelo BastiON em {datetime.now().strftime('%d/%m/%Y %H:%M')}\n"
                f"Origem: [{target['ds']}] {target['rel']}\n\nPara restaurar:\n"
                "1. Crie uma pasta no datastore e envie estes arquivos para ela (navegador de datastore ou scp).\n"
                + ("2. No ESXi (SSH), descompacte: gunzip *.gz\n" if compress else "")
                + "3. Clique com o botão direito no .vmx -> Registrar VM (ou: vim-cmd solo/registervm /vmfs/volumes/<ds>/<pasta>/<vm>.vmx).\n"
                "4. Na primeira vez que ligar, responda 'Eu movi'/'I moved it'.\n")
        await put_bytes("RESTAURAR.txt", info.encode())
    finally:
        if snap:
            # sem snapshots antes do backup (conferido acima): remover todos = remover o nosso e consolidar
            try:
                await run(conn, f"vim-cmd vmsvc/snapshot.removeall {vid}", 7200)
            except Exception:
                pass
    return {"bytes": total, "files": files, "format": "vmx + vmdk" + (" (gzip)" if compress else ""), "was_on": on}


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
    def __init__(self):
        self.bytes, self.t0, self._last = 0, time.time(), 0

    def __call__(self, n: int):
        self.bytes += n

    def rate(self) -> float:
        dt = time.time() - self.t0
        return self.bytes / dt if dt > 0 else 0.0

