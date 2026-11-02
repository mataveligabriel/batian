"""Destino NFS: o NAS é montado no próprio hypervisor (datastore NFS no ESXi, /mnt no Proxmox) e o host grava e lê
os backups direto nele — os dados não passam pelo BastiON, que só dá os comandos por SSH.
"""
import posixpath
import re
import shlex
from typing import List, Optional

from vbackup import CHUNK, BackupError, Sink, run, vmdk_extents, vmx_disks, vmx_get, parse_getallvms, snapshot_ids, SNAP_NAME


def ds_name(dest_id: str) -> str:
    return f"BASTION-{dest_id[:8]}"


async def is_esxi(conn) -> bool:
    code, _, _ = await run(conn, "test -d /vmfs/volumes && which vim-cmd")
    return code == 0


async def mount(conn, host: str, share: str, dest_id: str) -> str:
    """Monta (se ainda não estiver) e devolve a pasta raiz do NFS no host."""
    name = ds_name(dest_id)
    q = shlex.quote
    if await is_esxi(conn):
        code, out, _ = await run(conn, "esxcli storage nfs list")
        if not re.search(rf"^{re.escape(name)}\s", out, re.M):
            code, out, err = await run(conn, f"esxcli storage nfs add -H {q(host)} -s {q(share)} -v {q(name)}", 120)
            if code:
                raise BackupError(f"o ESXi não montou o NFS {host}:{share} ({(err or out).strip()[:200]}). Confira no NAS se o export libera o IP "
                                  "do ESXi com leitura/escrita e sem 'root squash' (no_root_squash / 'Mapear root para admin').")
        return f"/vmfs/volumes/{name}"
    root = f"/mnt/{name.lower()}"
    code, out, err = await run(conn, f"mountpoint -q {root} || (mkdir -p {root} && mount -t nfs {q(host)}:{q(share)} {root})", 120)
    if code:
        raise BackupError(f"o nó não montou o NFS {host}:{share} ({(err or out).strip()[:200]}). Instale o cliente (apt install nfs-common) "
                          "e confira se o export libera o IP do nó sem 'root squash'.")
    return root


class HostFs(Sink):
    """Arquivos do NFS vistos pelo hypervisor (por SSH). Os caminhos são os do destino (/backup/vms/...)."""
    label = "NFS"

    def __init__(self, conn, root: str, on_close=None):
        self.conn, self.root, self.on_close = conn, root.rstrip("/"), on_close

    def abs(self, path: str) -> str:
        return self.root + "/" + path.lstrip("/")

    async def makedirs(self, path: str):
        code, _, err = await run(self.conn, f"mkdir -p {shlex.quote(self.abs(path))}")
        if code:
            raise BackupError(f"não criei {path} no NFS ({err.strip()[:150]})")

    async def open_write(self, path: str):
        dst = self.abs(path)
        proc = await self.conn.create_process(f"cat > {shlex.quote(dst)}", encoding=None)
        fs = self

        class W:
            async def write(self, b):
                proc.stdin.write(b)
                await proc.stdin.drain()

            async def close(self):
                proc.stdin.write_eof()
                await proc.wait_closed()
                if proc.exit_status not in (0, None):
                    raise BackupError(f"o NFS não gravou {posixpath.basename(dst)} (sem espaço ou sem permissão?)")

            async def abort(self):
                try:
                    proc.close()
                except Exception:
                    pass
                await fs.remove(path)
        return W()

    async def open_read(self, path: str):
        proc = await self.conn.create_process(f"cat {shlex.quote(self.abs(path))}", encoding=None)
        try:
            while True:
                b = await proc.stdout.read(CHUNK)
                if not b:
                    break
                yield b
            await proc.wait_closed()
            if proc.exit_status not in (0, None):
                raise BackupError(f"não li {path} do NFS")
        finally:
            proc.close()

    async def listdir(self, path: str) -> List[str]:
        code, out, _ = await run(self.conn, f"ls -1 {shlex.quote(self.abs(path))}")
        return [x.strip() for x in out.splitlines() if x.strip()] if code == 0 else []

    async def isdir(self, path: str) -> bool:
        code, _, _ = await run(self.conn, f"test -d {shlex.quote(self.abs(path))}")
        return code == 0

    async def size(self, path: str) -> int:
        code, out, _ = await run(self.conn, f"ls -l {shlex.quote(self.abs(path))}")
        p = out.split()
        return int(p[4]) if code == 0 and len(p) > 4 and p[4].isdigit() else 0

    async def remove(self, path: str):
        await run(self.conn, f"rm -f {shlex.quote(self.abs(path))}")

    async def rmtree(self, path: str):
        await run(self.conn, f"rm -rf {shlex.quote(self.abs(path))}", 1800)

    async def close(self):
        if self.on_close:
            await self.on_close()


async def run_progress(conn, cmd: str, progress, label: str, timeout_idle: int = 3600) -> tuple:
    """Roda e transforma o "Clone: 37% done." do vmkfstools (ou qualquer NN%) em andamento na tela."""
    import asyncio
    import time
    proc = await conn.create_process(cmd + " 2>&1", encoding=None)
    buf, tail = b"", b""
    t0 = time.time()
    try:
        while True:
            b = await asyncio.wait_for(proc.stdout.read(4096), timeout_idle)
            if not b:
                break
            buf = (buf + b)[-2000:]
            tail = (tail + b)[-4000:]
            m = None
            for m in re.finditer(rb"(\d+)% done", buf):
                pass
            if m and hasattr(progress, "set_stage"):
                pct = int(m.group(1))
                el = time.time() - t0
                left = el * (100 - pct) / pct if pct >= 2 else 0
                eta = (f", faltam ~{int(left // 3600)}h{int(left % 3600 // 60):02d}" if left >= 3600 else f", faltam ~{max(1, int(left // 60))} min") if left else ""
                progress.set_stage(f"{label}: {pct}%{eta}")
        await proc.wait_closed()
    finally:
        proc.close()
    return proc.exit_status or 0, tail.decode("utf-8", "replace")


async def backup_esxi_nfs(conn, vm: dict, fs: HostFs, folder: str, quiesce: bool, progress) -> dict:
    """Completo com o NFS montado no ESXi: vmkfstools clona cada disco (com a cadeia de snapshots já consolidada)
    direto para o NAS. Uma passada só, sem gzip e sem passar pelo BastiON; funciona com os discos travados."""
    q = shlex.quote
    code, out, err = await run(conn, "vim-cmd vmsvc/getallvms")
    lst = parse_getallvms(out)
    m = re.match(r"\[(.+?)\]\s*(.+)$", vm.get("vmx") or "")
    t = next((x for x in lst if m and x["ds"] == m.group(1) and x["rel"] == m.group(2)), None) or next((x for x in lst if x["name"] == vm.get("name")), None)
    if not t:
        raise BackupError("a VM não foi encontrada neste host ESXi")
    vid = t["vmid"]
    vdir = posixpath.join("/vmfs/volumes", t["ds"], posixpath.dirname(t["rel"]))
    vmx_path = posixpath.join("/vmfs/volumes", t["ds"], t["rel"])
    for name, sid in snapshot_ids((await run(conn, f"vim-cmd vmsvc/snapshot.get {vid}"))[1]):
        if name == SNAP_NAME:                                        # sobra de um backup interrompido
            await run(conn, f"vim-cmd vmsvc/snapshot.remove {vid} {sid}", 7200)
    had = bool(snapshot_ids((await run(conn, f"vim-cmd vmsvc/snapshot.get {vid}"))[1]))
    code, vmx, err = await run(conn, f"cat {q(vmx_path)}")
    if code:
        raise BackupError(f"não li o .vmx ({err[:120]})")
    on = "Powered on" in (await run(conn, f"vim-cmd vmsvc/power.getstate {vid}"))[1]
    dst = fs.abs(folder)
    await run(conn, f"mkdir -p {q(dst)}")
    files, snap = [], False
    try:
        if on:
            progress.set_stage("criando snapshot temporário")
            code, out, err = await run(conn, f"vim-cmd vmsvc/snapshot.create {vid} {SNAP_NAME} 'snapshot temporario do backup' 0 {1 if quiesce else 0}", 1800)
            if code or "fault" in (out + err).lower():
                raise BackupError(f"o ESXi não criou o snapshot ({(err or out).strip()[:200]})")
            snap = True
        new_vmx = vmx
        used = set()
        for d in vmx_disks(vmx):
            src = d if d.startswith("/") else posixpath.join(vdir, d)
            base = re.sub(r"-\d{6}\.vmdk$", ".vmdk", posixpath.basename(d))
            if base in used:
                base = f"{len(used)}_{base}"
            used.add(base)
            code, txt = await run_progress(conn, f"vmkfstools -i {q(src)} -d thin {q(posixpath.join(dst, base))}", progress, f"copiando {base} para o NFS")
            if code:
                raise BackupError(f"o vmkfstools não copiou {base} para o NFS: {' '.join(txt.strip().splitlines()[-2:])[:200]}")
            new_vmx = new_vmx.replace(f'"{d}"', f'"{base}"')
            files.append(base)
        from vcbt import put_text
        await put_text(conn, posixpath.join(dst, posixpath.basename(vmx_path)), new_vmx.encode())
        files.append(posixpath.basename(vmx_path))
        nv = vmx_get(vmx, "nvram")
        if nv:
            p = nv if nv.startswith("/") else posixpath.join(vdir, nv)
            if (await run(conn, f"cp {q(p)} {q(dst)}/"))[0] == 0:
                files.append(posixpath.basename(nv))
        await put_text(conn, posixpath.join(dst, "RESTAURAR.txt"), (f"Backup de {t['name']} (NFS, discos thin já consolidados).\n"
                       "Restaurar pelo BastiON, ou: monte o NFS no ESXi, clone os discos com vmkfstools -i para um datastore,\n"
                       "copie o .vmx/.nvram para a mesma pasta e registre o .vmx.\n").encode())
    finally:
        if snap:
            progress.set_stage("removendo o snapshot temporário")
            try:
                if not had:
                    await run(conn, f"vim-cmd vmsvc/snapshot.removeall {vid}", 7200)
                else:
                    for name, sid in snapshot_ids((await run(conn, f"vim-cmd vmsvc/snapshot.get {vid}"))[1]):
                        if name == SNAP_NAME:
                            await run(conn, f"vim-cmd vmsvc/snapshot.remove {vid} {sid}", 7200)
            except Exception:
                pass
    code, out, _ = await run(conn, f"du -sk {q(dst)}")
    kb = int(out.split()[0]) if code == 0 and out.split() and out.split()[0].isdigit() else 0
    return {"bytes": kb * 1024, "files": files, "format": "vmx + vmdk thin (NFS)", "note": "snapshots consolidados na cópia" if had else ""}


async def backup_proxmox_nfs(conn, vm: dict, fs: HostFs, folder: str, mode: str, progress, now) -> dict:
    """vzdump no nó gravando direto no NFS montado."""
    from vbackup import pve_version, stream
    major, minor = await pve_version(conn)
    comp, ext = ("zstd", "zst") if (major, minor) >= (6, 2) else ("gzip", "gz")
    kind = "lxc" if vm.get("type") == "lxc" else "qemu"
    name = f"vzdump-{kind}-{int(vm['vmid'])}-{now.strftime('%Y_%m_%d-%H_%M_%S')}.{'tar' if kind == 'lxc' else 'vma'}.{ext}"
    dst = posixpath.join(fs.abs(folder), name)
    await run(conn, f"mkdir -p {shlex.quote(fs.abs(folder))}")
    mode = mode if mode in ("snapshot", "suspend", "stop") else "snapshot"
    threads = " --zstd 0" if comp == "zstd" and major >= 7 else ""

    class Null:
        async def write(self, b):
            pass
    try:
        await stream(conn, f"vzdump {int(vm['vmid'])} --mode {mode} --compress {comp}{threads} --stdout > {shlex.quote(dst)}", Null(), progress)
    except BaseException:
        await run(conn, f"rm -f {shlex.quote(dst)}")
        raise
    size = await fs.size(posixpath.join(folder, name))
    if size < 1024:
        raise BackupError("o vzdump não gerou dados no NFS")
    progress(size)
    return {"bytes": size, "files": [name], "format": f"vzdump (NFS)"}


__all__ = ["mount", "HostFs", "backup_esxi_nfs", "backup_proxmox_nfs", "run_progress", "ds_name", "is_esxi"]
