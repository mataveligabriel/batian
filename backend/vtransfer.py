"""Restaurar backups e copiar/migrar VMs entre servidores (ESXi -> ESXi, Proxmox -> Proxmox, ESXi -> Proxmox).

Tudo passa em fluxo pelo BastiON, por SSH nos dois lados:
- destino ESXi: arquivos gravados na pasta nova do datastore (`gunzip -c > arquivo`), .vmx ajustado (nome, UUID/MAC
  novos na cópia), discos thin devolvidos ao thin (`vmkfstools -K`) e VM registrada (`vim-cmd solo/registervm`);
- destino Proxmox: arquivo vzdump numa pasta temporária do nó e `qmrestore` / `pct restore`;
- ESXi -> Proxmox: discos gravados esparsos (`dd conv=sparse`) na pasta temporária do nó, `qm create` com a CPU/memória
  do .vmx e `qm importdisk` de cada disco.
"""
import posixpath
import re
import shlex
from typing import Dict, List, Optional

from vbackup import BackupError, CHUNK, run, vmx_disks, vmx_get, vmdk_extents


class SshSink:
    """Grava arquivos no hypervisor de destino por SSH. unpack=True: arquivo .gz é descompactado ao gravar (nome sem .gz);
    sparse=True (Linux/Proxmox): zeros não ocupam disco."""
    label = "SSH"

    def __init__(self, conn, unpack: bool = False, sparse: bool = False):
        self.conn, self.unpack, self.sparse = conn, unpack, sparse
        self.written: List[str] = []

    async def makedirs(self, path: str):
        code, _, err = await run(self.conn, f"mkdir -p {shlex.quote(path)}")
        if code:
            raise BackupError(f"não criei {path} no destino ({err.strip()[:150]})")

    def target_name(self, path: str) -> str:
        return path[:-3] if self.unpack and path.endswith(".gz") else path

    async def open_write(self, path: str):
        dst = self.target_name(path)
        gz = dst != path
        out = f"dd of={shlex.quote(dst)} bs=1M conv=sparse status=none" if self.sparse else f"cat > {shlex.quote(dst)}"
        cmd = (f"gunzip -c | {out}" if gz else out) if self.sparse else (f"gunzip -c > {shlex.quote(dst)}" if gz else out)
        proc = await self.conn.create_process(cmd, encoding=None)
        sink = self

        class W:
            async def write(self, b):
                if proc.exit_status is not None:
                    raise BackupError(f"o destino parou de aceitar {posixpath.basename(dst)}")
                proc.stdin.write(b)
                await proc.stdin.drain()

            async def close(self):
                proc.stdin.write_eof()
                await proc.wait_closed()
                if proc.exit_status not in (0, None):
                    err = (await proc.stderr.read()) if not proc.stderr.at_eof() else b""
                    raise BackupError(f"o destino não gravou {posixpath.basename(dst)} (código {proc.exit_status}): "
                                      f"{(err or b'').decode('utf-8', 'replace').strip()[:200] or 'sem espaço?'}")
                sink.written.append(dst)

            async def abort(self):
                try:
                    proc.close()
                except Exception:
                    pass
                try:
                    await run(sink.conn, f"rm -f {shlex.quote(dst)}")
                except Exception:
                    pass
        return W()

    async def listdir(self, path: str) -> List[str]:
        code, out, _ = await run(self.conn, f"ls -1 {shlex.quote(path)}")
        return [x for x in out.splitlines() if x.strip()] if code == 0 else []

    async def remove(self, path: str):
        await run(self.conn, f"rm -f {shlex.quote(path)}")

    async def rmtree(self, path: str):
        await run(self.conn, f"rm -rf {shlex.quote(path)}", 600)

    async def exists(self, path: str) -> bool:
        code, _, _ = await run(self.conn, f"test -e {shlex.quote(path)}")
        return code == 0

    async def close(self):
        pass


async def copy_folder(src, folder: str, dst: SshSink, dst_folder: str, progress, skip=("RESTAURAR.txt",)) -> List[str]:
    """Copia os arquivos de uma pasta do NAS (SftpSink/FtpSink) para o destino. Devolve os nomes gravados (sem .gz)."""
    names = sorted(n for n in await src.listdir(folder) if n not in skip and not n.startswith("."))
    if not names:
        raise BackupError(f"a pasta {folder} está vazia no NAS")
    await dst.makedirs(dst_folder)
    out = []
    for n in names:
        if hasattr(progress, "set_stage"):
            progress.set_stage(f"copiando {n}")
        w = await dst.open_write(posixpath.join(dst_folder, n))
        try:
            async for b in src.open_read(posixpath.join(folder, n)):
                await w.write(b)
                progress(len(b))
            await w.close()
        except BaseException:
            await w.abort()
            raise
        out.append(posixpath.basename(dst.target_name(n)))
    return out


def backup_kind(names: List[str]) -> str:
    if "point.json" in names:
        return "vmware-cbt"
    if any(n.startswith("vzdump-") for n in names):
        return "proxmox"
    if any(n.endswith(".vmx") for n in names):
        return "vmware"
    return ""


# ------------------------------------------------------------------ ESXi
async def esxi_datastores(conn) -> List[str]:
    code, out, _ = await run(conn, "ls -1 /vmfs/volumes")
    names = [x.strip() for x in out.splitlines() if x.strip()]
    # os nomes amigáveis são links para as pastas com UUID
    return [n for n in names if not re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{12}|[0-9a-f-]{20,}", n)]


async def esxi_prepare(conn, datastore: str, name: str) -> str:
    """Pasta nova no datastore; erro se a VM ou a pasta já existem."""
    from vbackup import parse_getallvms
    code, out, _ = await run(conn, "vim-cmd vmsvc/getallvms")
    if code:
        raise BackupError("o destino não respondeu ao vim-cmd (é um ESXi com SSH ligado?)")
    if any(v["name"] == name for v in parse_getallvms(out)):
        raise BackupError(f"já existe uma VM chamada {name} no ESXi de destino")
    folder = posixpath.join("/vmfs/volumes", datastore, name)
    code, _, _ = await run(conn, f"test -d {shlex.quote(posixpath.join('/vmfs/volumes', datastore))}")
    if code:
        raise BackupError(f"datastore {datastore} não existe no destino")
    code, _, _ = await run(conn, f"test -e {shlex.quote(folder)}")
    if code == 0:
        raise BackupError(f"já existe a pasta {name} no datastore {datastore}")
    return folder


def esxi_fix_vmx(vmx: str, name: str, keep_identity: bool) -> str:
    drop = [r"displayName", r"sched\.swap\.derivedName", r"uuid\.action"]
    if not keep_identity:
        drop += [r"uuid\.bios", r"uuid\.location", r"vc\.uuid", r"ethernet\d+\.generatedAddress", r"ethernet\d+\.generatedAddressOffset"]
    lines = [ln for ln in vmx.splitlines() if not any(re.match(rf"\s*{k}\s*=", ln, re.I) for k in drop)]
    if not keep_identity:
        lines = [re.sub(r'^(\s*ethernet\d+\.addressType\s*=\s*)"vpx"', r'\1"generated"', ln, flags=re.I) for ln in lines]
    lines += [f'displayName = "{name}"', f'uuid.action = "{"keep" if keep_identity else "create"}"']
    return "\n".join(lines) + "\n"


async def esxi_finalize(conn, folder: str, name: str, keep_identity: bool, power_on: bool, progress, punch: bool = True) -> dict:
    code, ls, _ = await run(conn, f"ls -1 {shlex.quote(folder)}")
    files = [x.strip() for x in ls.splitlines() if x.strip()]
    vmxs = [f for f in files if f.endswith(".vmx")]
    if not vmxs:
        raise BackupError("não veio .vmx na cópia")
    vmx_path = posixpath.join(folder, vmxs[0])
    code, vmx, _ = await run(conn, f"cat {shlex.quote(vmx_path)}")
    new = esxi_fix_vmx(vmx, name, keep_identity)
    proc = await conn.create_process(f"cat > {shlex.quote(vmx_path)}", encoding=None)
    proc.stdin.write(new.encode())
    proc.stdin.write_eof()
    await proc.wait_closed()
    # disco thin gravado por inteiro volta a ser thin (zeros devolvidos ao datastore)
    for d in vmx_disks(vmx):
        p = d if d.startswith("/") else posixpath.join(folder, d)
        code, desc, _ = await run(conn, f"cat {shlex.quote(p)}")
        if punch and code == 0 and re.search(r'ddb\.thinProvisioned\s*=\s*"1"', desc):
            if hasattr(progress, "set_stage"):
                progress.set_stage(f"devolvendo espaço vazio de {posixpath.basename(p)} (vmkfstools -K)")
            await run(conn, f"vmkfstools -K {shlex.quote(p)}", 6 * 3600)
    code, out, err = await run(conn, f"vim-cmd solo/registervm {shlex.quote(vmx_path)} {shlex.quote(name)}", 300)
    vid = (out.strip().splitlines() or [""])[-1].strip()
    if code or not vid.isdigit():
        raise BackupError(f"o ESXi não registrou a VM ({(err or out).strip()[:200]})")
    started = False
    if power_on:
        code, out, err = await run(conn, f"vim-cmd vmsvc/power.on {vid}", 600)
        started = code == 0
    return {"vmid": vid, "vmx": vmx_path, "started": started}


async def esxi_shutdown(conn, vid: str, wait: int = 600) -> None:
    import asyncio
    code, st, _ = await run(conn, f"vim-cmd vmsvc/power.getstate {vid}")
    if "Powered off" in st:
        return
    code, out, err = await run(conn, f"vim-cmd vmsvc/power.shutdown {vid}", 120)
    if code:
        raise BackupError(f"não consegui desligar a VM de origem pelo sistema (VMware Tools rodando?): {(err or out).strip()[:150]}")
    for _ in range(wait // 5):
        await asyncio.sleep(5)
        code, st, _ = await run(conn, f"vim-cmd vmsvc/power.getstate {vid}")
        if "Powered off" in st:
            return
    raise BackupError("a VM de origem não desligou em 10 minutos — desligue manualmente e rode de novo")


def find_esxi_vmid(getallvms_out: str, vm: dict) -> Optional[str]:
    from vbackup import parse_getallvms
    lst = parse_getallvms(getallvms_out)
    m = re.match(r"\[(.+?)\]\s*(.+)$", vm.get("vmx") or "")
    t = next((x for x in lst if m and x["ds"] == m.group(1) and x["rel"] == m.group(2)), None) or next((x for x in lst if x["name"] == vm.get("name")), None)
    return t["vmid"] if t else None


# ------------------------------------------------------------------ Proxmox
async def pve_nextid(conn) -> int:
    code, out, err = await run(conn, "pvesh get /cluster/nextid")
    m = re.search(r"(\d+)", out)
    if code or not m:
        raise BackupError(f"não consegui o próximo VMID do Proxmox ({(err or out).strip()[:120]})")
    return int(m.group(1))


async def pve_check_id(conn, vmid: int):
    code, out, _ = await run(conn, "pvesh get /cluster/resources --type vm --output-format json")
    if code == 0 and re.search(rf'"vmid"\s*:\s*{vmid}\b', out):
        raise BackupError(f"o VMID {vmid} já existe no Proxmox de destino")


async def pve_restore(conn, archive: str, vmid: int, storage: str, name: str, unique: bool, power_on: bool) -> dict:
    lxc = "-lxc-" in posixpath.basename(archive)
    q = shlex.quote
    if lxc:
        cmd = f"pct restore {vmid} {q(archive)} --storage {q(storage)}" + (" --unique 1" if unique else "")
    else:
        cmd = f"qmrestore {q(archive)} {vmid} --storage {q(storage)}" + (" --unique 1" if unique else "")
    code, out, err = await run(conn, cmd, 12 * 3600)
    if code:
        tail = " | ".join([x for x in (err or out).strip().splitlines() if x.strip()][-3:])
        raise BackupError(f"o Proxmox não restaurou: {tail[:300]}")
    if name:
        await run(conn, f"{'pct' if lxc else 'qm'} set {vmid} --{'hostname' if lxc else 'name'} {q(name)}")
    started = False
    if power_on:
        code, _, _ = await run(conn, f"{'pct' if lxc else 'qm'} start {vmid}", 300)
        started = code == 0
    return {"vmid": str(vmid), "started": started, "type": "lxc" if lxc else "qemu"}


async def pve_shutdown(conn, vm: dict, wait: int = 600):
    tool = "pct" if vm.get("type") == "lxc" else "qm"
    code, out, err = await run(conn, f"{tool} shutdown {int(vm['vmid'])} --timeout {wait}", wait + 60)
    if code:
        raise BackupError(f"a VM de origem não desligou pelo sistema ({(err or out).strip()[:150]}) — desligue manualmente e rode de novo")


# ------------------------------------------------------------------ ESXi -> Proxmox
def _vmx_kv(vmx: str) -> Dict[str, str]:
    kv = {}
    for line in vmx.splitlines():
        m = re.match(r'\s*([\w:.]+)\s*=\s*"(.*)"\s*$', line)
        if m:
            kv[m.group(1).lower()] = m.group(2)
    return kv


def pve_create_args(vmx: str, name: str, bridge: str, bus: str, nic: str) -> List[str]:
    kv = _vmx_kv(vmx)
    mem = int(kv.get("memsize") or 1024)
    cpus = int(kv.get("numvcpus") or 1)
    cps = int(kv.get("cpuid.corespersocket") or 0) or cpus
    sockets = max(1, cpus // cps) if cps else 1
    guest = (kv.get("guestos") or "").lower()
    ostype = ("win11" if "11" in guest else "win10") if "windows" in guest or guest.startswith("win") else "l26"
    args = [f"--name {shlex.quote(name)}", f"--memory {mem}", f"--cores {max(1, cpus // sockets)}", f"--sockets {sockets}",
            f"--ostype {ostype}", "--scsihw pvscsi" if bus == "scsi" else "--scsihw virtio-scsi-single"]
    nics = sorted({int(m.group(1)) for k in kv for m in [re.fullmatch(r"ethernet(\d+)\.present", k)] if m and kv[k].lower() == "true"})
    for i, n in enumerate(nics or [0]):
        args.append(f"--net{i} {nic},bridge={shlex.quote(bridge)}")
    if kv.get("firmware", "").lower() == "efi":
        args.append("--bios ovmf")
    return args


async def convert_to_pve(conn, folder: str, vmid: int, storage: str, name: str, bridge: str, bus: str, nic: str,
                         power_on: bool, progress, disk_map: Optional[Dict[str, str]] = None) -> dict:
    q = shlex.quote
    code, ls, _ = await run(conn, f"ls -1 {q(folder)}")
    files = [x.strip() for x in ls.splitlines() if x.strip()]
    vmxs = [f for f in files if f.endswith(".vmx")]
    if not vmxs:
        raise BackupError("não veio .vmx")
    code, vmx, _ = await run(conn, f"cat {q(posixpath.join(folder, vmxs[0]))}")
    code, out, err = await run(conn, f"qm create {vmid} " + " ".join(pve_create_args(vmx, name, bridge, bus, nic)), 300)
    if code:
        raise BackupError(f"o Proxmox não criou a VM: {(err or out).strip()[:250]}")
    disks = []
    for i, d in enumerate(vmx_disks(vmx)):
        p = (disk_map or {}).get(posixpath.basename(d)) or posixpath.join(folder, posixpath.basename(d))
        if hasattr(progress, "set_stage"):
            progress.set_stage(f"importando {posixpath.basename(d)} para {storage} (qm importdisk)")
        code, out, err = await run(conn, f"qm importdisk {vmid} {q(p)} {q(storage)}", 12 * 3600)
        m = re.search(r"unused\d+:(\S+?)['\"]?\s*$", out.strip(), re.M) or re.search(r"as '?unused\d+:([^'\s]+)", out)
        if code or not m:
            raise BackupError(f"o importdisk falhou em {posixpath.basename(d)}: {' '.join((err or out).strip().splitlines()[-2:])[:250]}")
        slot = f"{'sata' if bus == 'sata' else 'scsi'}{i}"
        await run(conn, f"qm set {vmid} --{slot} {q(m.group(1))}")
        disks.append(slot)
    if disks:
        await run(conn, f"qm set {vmid} --boot order={disks[0]}")
    if _vmx_kv(vmx).get("firmware", "").lower() == "efi":
        await run(conn, f"qm set {vmid} --efidisk0 {q(storage)}:1,efitype=4m,pre-enrolled-keys=0")
    started = False
    if power_on:
        code, _, _ = await run(conn, f"qm start {vmid}", 300)
        started = code == 0
    return {"vmid": str(vmid), "started": started, "disks": disks}


# ------------------------------------------------------------------ restaurar backup incremental (CBT)
async def _nas_bytes(nas, path: str) -> bytes:
    buf = b""
    async for b in nas.open_read(path):
        buf += b
    return buf


def _vmx_disks_to_basename(vmx: str) -> str:
    """Discos do .vmx passam a apontar para o nome do arquivo (tudo na pasta nova)."""
    for d in vmx_disks(vmx):
        if "/" in d:
            vmx = vmx.replace(f'"{d}"', f'"{posixpath.basename(d)}"')
    return vmx


async def restore_cbt_esxi(conn, nas, chain: List[dict], folder: str, progress):
    """Cria os discos thin no datastore e aplica o completo + incrementais em ordem."""
    import vcbt
    pt = chain[-1]["point"]
    vmx = (await _nas_bytes(nas, posixpath.join(chain[-1]["folder"], pt["vmx"]))).decode("utf-8", "replace")
    await run(conn, f"mkdir -p {shlex.quote(folder)}")
    await vcbt.put_text(conn, posixpath.join(folder, pt["vmx"]), _vmx_disks_to_basename(vmx).encode())
    if pt.get("nvram"):
        await vcbt.put_text(conn, posixpath.join(folder, pt["nvram"]), await _nas_bytes(nas, posixpath.join(chain[-1]["folder"], pt["nvram"])))
    await vcbt.install(conn)
    for d in pt["disks"]:
        target = posixpath.join(folder, d["name"])
        code, out, err = await run(conn, f"vmkfstools -c {int(d['capacity'])} -d thin {shlex.quote(target)}", 600)
        if code:
            raise BackupError(f"não criei o disco {d['name']} no datastore ({(err or out).strip()[:150]})")
        stem = d["name"][:-5] if d["name"].endswith(".vmdk") else d["name"]
        if hasattr(progress, "set_stage"):
            progress.set_stage(f"gravando {d['name']} ({len(chain)} cópia(s) da cadeia)")
        await vcbt.write_disk(conn, nas, chain, d["n"], posixpath.join(folder, f"{stem}-flat.vmdk"), 0, True, progress)


async def restore_cbt_pve_files(conn, nas, chain: List[dict], tmp: str, progress) -> Dict[str, str]:
    """Discos crus (esparsos) na pasta temporária do nó + o .vmx; devolve nome do disco no .vmx -> arquivo cru."""
    import vcbt
    pt = chain[-1]["point"]
    await run(conn, f"mkdir -p {shlex.quote(tmp)}")
    vmx = (await _nas_bytes(nas, posixpath.join(chain[-1]["folder"], pt["vmx"]))).decode("utf-8", "replace")
    await vcbt.put_text(conn, posixpath.join(tmp, pt["vmx"]), _vmx_disks_to_basename(vmx).encode())
    script = posixpath.join(tmp, "bastion-cbt.py")
    await vcbt.install(conn, script)
    out = {}
    for d in pt["disks"]:
        raw = posixpath.join(tmp, f"disk{d['n']}.raw")
        if hasattr(progress, "set_stage"):
            progress.set_stage(f"gravando {d['name']} ({len(chain)} cópia(s) da cadeia)")
        await vcbt.write_disk(conn, nas, chain, d["n"], raw, int(d["capacity"]), True, progress, script=script)
        out[d["name"]] = raw
    return out


async def restore_cbt_local(conn, fs, chain: List[dict], disk_n: int, target: str, size: int, progress, script: str):
    """NFS montado no destino: o próprio host lê os arquivos da cadeia e grava o disco (nada passa pelo BastiON)."""
    import vcbt
    parts = []
    for c in chain:
        d = next((x for x in c["point"]["disks"] if x["n"] == disk_n), None)
        if not d:
            raise BackupError(f"o disco {disk_n} não existe na cópia {c['stamp']}")
        parts.append(shlex.quote(fs.abs(posixpath.join(c["folder"], d["data"]))))
    code, out, err = await run(conn, f"{vcbt.PYSET} cat {' '.join(parts)} | $PY {script} write {shlex.quote(target)} {int(size)} 1", 12 * 3600)
    if code:
        raise BackupError(f"não gravei o disco {disk_n} a partir do NFS ({(err or out).strip()[:200]})")


async def restore_cbt_esxi_nfs(conn, fs, chain: List[dict], folder: str, progress):
    import vcbt
    pt = chain[-1]["point"]
    await run(conn, f"mkdir -p {shlex.quote(folder)}")
    for f in [pt["vmx"]] + ([pt["nvram"]] if pt.get("nvram") else []):
        await run(conn, f"cp {shlex.quote(fs.abs(posixpath.join(chain[-1]['folder'], f)))} {shlex.quote(folder)}/")
    vmxp = posixpath.join(folder, pt["vmx"])
    code, vmx, _ = await run(conn, f"cat {shlex.quote(vmxp)}")
    await vcbt.put_text(conn, vmxp, _vmx_disks_to_basename(vmx).encode())
    await vcbt.install(conn)
    for d in pt["disks"]:
        target = posixpath.join(folder, d["name"])
        code, out, err = await run(conn, f"vmkfstools -c {int(d['capacity'])} -d thin {shlex.quote(target)}", 600)
        if code:
            raise BackupError(f"não criei o disco {d['name']} ({(err or out).strip()[:150]})")
        stem = d["name"][:-5] if d["name"].endswith(".vmdk") else d["name"]
        if hasattr(progress, "set_stage"):
            progress.set_stage(f"gravando {d['name']} a partir do NFS ({len(chain)} cópia(s) da cadeia)")
        await restore_cbt_local(conn, fs, chain, d["n"], posixpath.join(folder, f"{stem}-flat.vmdk"), 0, progress, vcbt.REMOTE)


async def restore_cbt_pve_nfs(conn, fs, chain: List[dict], tmp: str, progress) -> Dict[str, str]:
    import vcbt
    pt = chain[-1]["point"]
    await run(conn, f"mkdir -p {shlex.quote(tmp)}")
    code, vmx, _ = await run(conn, f"cat {shlex.quote(fs.abs(posixpath.join(chain[-1]['folder'], pt['vmx'])))}")
    await vcbt.put_text(conn, posixpath.join(tmp, pt["vmx"]), _vmx_disks_to_basename(vmx).encode())
    script = posixpath.join(tmp, "bastion-cbt.py")
    await vcbt.install(conn, script)
    out = {}
    for d in pt["disks"]:
        raw = posixpath.join(tmp, f"disk{d['n']}.raw")
        if hasattr(progress, "set_stage"):
            progress.set_stage(f"gravando {d['name']} a partir do NFS")
        await restore_cbt_local(conn, fs, chain, d["n"], raw, int(d["capacity"]), progress, script)
        out[d["name"]] = raw
    return out


async def restore_vmware_esxi_nfs(conn, fs, src_folder: str, folder: str, progress):
    """Backup completo (vmx + vmdk) no NFS -> datastore do ESXi: vmkfstools -i de cada disco, cp do resto."""
    import vnfs
    q = shlex.quote
    await run(conn, f"mkdir -p {q(folder)}")
    names = await fs.listdir(src_folder)
    packed = any(n.endswith(".gz") for n in names)          # cópia feita por SFTP/FTP e depois posta no NFS
    for n in names:
        p = fs.abs(posixpath.join(src_folder, n))
        if n == "RESTAURAR.txt" or (not packed and (n.endswith("-flat.vmdk") or n.endswith("-delta.vmdk") or n.endswith("-sesparse.vmdk"))):
            continue
        if packed and not n.endswith(".gz"):
            code, out, err = await run(conn, f"cp {q(p)} {q(folder)}/", 12 * 3600)
        elif n.endswith(".gz"):
            code, out, err = await run(conn, f"gunzip -c {q(p)} > {q(posixpath.join(folder, n[:-3]))}", 12 * 3600)
        elif n.endswith(".vmdk"):
            code, out = await vnfs.run_progress(conn, f"vmkfstools -i {q(p)} -d thin {q(posixpath.join(folder, n))}", progress, f"copiando {n} do NFS")
            err = ""
        else:
            code, out, err = await run(conn, f"cp {q(p)} {q(folder)}/", 3600)
        if code:
            raise BackupError(f"não copiei {n} do NFS ({(err or out).strip()[-200:]})")
    return packed


def disk_files(vmx: str) -> List[str]:
    return [posixpath.basename(d) for d in vmx_disks(vmx)]


__all__ = ["SshSink", "copy_folder", "backup_kind", "esxi_datastores", "esxi_prepare", "esxi_finalize", "esxi_shutdown",
           "find_esxi_vmid", "pve_nextid", "pve_check_id", "pve_restore", "pve_shutdown", "convert_to_pve", "pve_create_args",
           "esxi_fix_vmx", "vmx_get", "vmdk_extents", "CHUNK"]
