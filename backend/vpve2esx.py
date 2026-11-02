"""Proxmox -> VMware ESXi: a VM (ao vivo ou de um backup vzdump) vira uma VM do ESXi.

1. num nó Proxmox: `vzdump ... --stdout | vma extract -v - <pasta>` (ou o arquivo do backup no lugar do vzdump) gera
   qemu-server.conf + disk-drive-<disco>.raw (esparsos);
2. cada disco cru vai para o ESXi pelo BastiON com o script do CBT (lê no Proxmox, pula blocos zerados, comprime;
   grava num disco thin criado com vmkfstools -c), então o disco chega thin;
3. o .vmx é montado a partir do qemu-server.conf (CPU, memória, BIOS/UEFI, placas de rede) e a VM é registrada.
"""
import asyncio
import json
import posixpath
import re
import shlex
from typing import Dict, List, Optional

from vbackup import BackupError, CHUNK, run

DISK_KEY = re.compile(r"^(scsi|sata|virtio|ide)(\d+)$")


def parse_qemu_conf(text: str) -> Dict[str, str]:
    """Só a configuração atual (o que vem antes do primeiro [snapshot])."""
    out = {}
    for line in text.splitlines():
        if line.startswith("["):
            break
        m = re.match(r"^([\w-]+):\s*(.*)$", line.strip())
        if m:
            out[m.group(1)] = m.group(2)
    return out


def conf_disks(conf: Dict[str, str]) -> List[str]:
    """Discos (sem CD/DVD, efidisk, tpm), na ordem de boot quando dá."""
    keys = [k for k, v in conf.items() if DISK_KEY.match(k) and "media=cdrom" not in v and not v.startswith("none")]
    order = re.search(r"order=([\w;]+)", conf.get("boot", ""))
    first = [k for k in (order.group(1).split(";") if order else []) if k in keys]
    rest = sorted((k for k in keys if k not in first), key=lambda k: (DISK_KEY.match(k).group(1), int(DISK_KEY.match(k).group(2))))
    return first + rest


def guest_os(ostype: str) -> str:
    return {"l26": "otherlinux-64", "l24": "otherlinux-64", "win11": "windows9-64", "win10": "windows9-64", "win8": "windows9-64",
            "win7": "windows7-64", "w2k8": "windows7srv-64", "wvista": "windows7-64", "wxp": "winxppro", "solaris": "solaris11-64"}.get(ostype or "", "other-64")


def hw_version(vmware_v: str) -> int:
    m = re.search(r"ESXi (\d+)\.(\d+)", vmware_v or "")
    v = (int(m.group(1)), int(m.group(2))) if m else (6, 5)
    return 20 if v >= (8, 0) else 17 if v >= (7, 0) else 14 if v >= (6, 7) else 13 if v >= (6, 5) else 11 if v >= (6, 0) else 10


def build_vmx(conf: Dict[str, str], name: str, disks: List[str], hw: int, nic: str, bus: str, portgroup: str, keep_mac: bool) -> str:
    cores = int(conf.get("cores") or 1)
    sockets = int(conf.get("sockets") or 1)
    mem = int(re.match(r"\d+", conf.get("memory") or "1024").group(0))
    lines = ['.encoding = "UTF-8"', 'config.version = "8"', f'virtualHW.version = "{hw}"', f'displayName = "{name}"',
             f'guestOS = "{guest_os(conf.get("ostype", ""))}"', f'memSize = "{mem}"', f'numvcpus = "{cores * sockets}"',
             f'cpuid.coresPerSocket = "{cores}"', 'pciBridge0.present = "TRUE"', 'pciBridge4.present = "TRUE"',
             'pciBridge4.virtualDev = "pcieRootPort"', 'pciBridge4.functions = "8"', 'tools.syncTime = "FALSE"',
             'uuid.action = "create"' if not keep_mac else 'uuid.action = "keep"']
    if conf.get("bios") == "ovmf":
        lines.append('firmware = "efi"')
    if bus == "scsi":
        lines += ['scsi0.present = "TRUE"', 'scsi0.virtualDev = "pvscsi"']
        ctl = "scsi0"
    else:
        lines += ['sata0.present = "TRUE"']
        ctl = "sata0"
    for i, f in enumerate(disks):
        unit = i if bus != "scsi" or i < 7 else i + 1          # scsi0:7 é do controlador
        lines += [f'{ctl}:{unit}.present = "TRUE"', f'{ctl}:{unit}.fileName = "{f}"']
        if bus == "scsi":
            lines.append(f'{ctl}:{unit}.deviceType = "scsi-hardDisk"')
    nets = sorted((k for k in conf if re.fullmatch(r"net\d+", k)), key=lambda k: int(k[3:])) or ["net0"]
    for i, k in enumerate(nets):
        lines += [f'ethernet{i}.present = "TRUE"', f'ethernet{i}.virtualDev = "{nic}"', f'ethernet{i}.networkName = "{portgroup}"',
                  f'ethernet{i}.connectionType = "bridged"']
        mac = re.search(r"=([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})", conf.get(k, ""))
        if keep_mac and mac:
            lines += [f'ethernet{i}.addressType = "static"', f'ethernet{i}.address = "{mac.group(1).lower()}"', f'ethernet{i}.checkMACAddress = "FALSE"']
        else:
            lines.append(f'ethernet{i}.addressType = "generated"')
    return "\n".join(lines) + "\n"


async def extract(conn, source_cmd: str, outdir: str, progress) -> None:
    """source_cmd escreve um VMA cru no stdout (vzdump --stdout, ou zstd -dc arquivo)."""
    from vnfs import run_progress
    q = shlex.quote
    code, _, _ = await run(conn, "which vma")
    if code:
        raise BackupError("o nó escolhido não tem a ferramenta vma (precisa ser um nó Proxmox)")
    await run(conn, f"rm -rf {q(outdir)}")
    code, out = await run_progress(conn, f"set -o pipefail; {source_cmd} | vma extract -v - {q(outdir)}", progress, "desempacotando a VM no Proxmox", 6 * 3600)
    if code:
        raise BackupError(f"não consegui desempacotar a VM (falta espaço na pasta temporária?): {' '.join(out.strip().splitlines()[-2:])[:250]}")


def source_from_archive(path: str) -> str:
    q = shlex.quote(path)
    if path.endswith(".zst"):
        return f"zstd -dc {q}"
    if path.endswith(".gz"):
        return f"gzip -dc {q}"
    if path.endswith(".lzo"):
        return f"lzop -dc {q}"
    return f"cat {q}"


async def pump_disk(src_conn, raw: str, size: int, dst_conn, dst_flat: str, progress, src_script: str, dst_script: str, label: str):
    """Lê o disco cru no Proxmox (só blocos com dados) e grava no disco thin do ESXi."""
    import vcbt
    ext = f"{raw}.ext.json"
    await vcbt.put_text(src_conn, ext, json.dumps([[0, size]]).encode())
    reader = await src_conn.create_process(f"{vcbt.PY} {src_script} read {shlex.quote(raw)} {shlex.quote(ext)} 1", encoding=None)
    writer = await dst_conn.create_process(f"{vcbt.PY} {dst_script} write {shlex.quote(dst_flat)} 0 1", encoding=None)

    async def errs():
        buf = b""
        while True:
            b = await reader.stderr.read(4096)
            if not b:
                return
            buf = (buf + b)[-2000:]
            m = None
            for m in re.finditer(rb"PROG (\d+) (\d+)", buf):
                pass
            if m and hasattr(progress, "set_stage"):
                d, t = int(m.group(1)), max(int(m.group(2)), 1)
                progress.set_stage(f"enviando {label} para o ESXi: {min(100, 100 * d // t)}% ({d / 1e9:.1f} de {t / 1e9:.1f} GB)")
    et = asyncio.create_task(errs())
    try:
        while True:
            b = await asyncio.wait_for(reader.stdout.read(CHUNK), 3600)
            if not b:
                break
            writer.stdin.write(b)
            await writer.stdin.drain()
            progress(len(b))
        await reader.wait_closed()
        writer.stdin.write_eof()
        await writer.wait_closed()
    finally:
        et.cancel()
        reader.close()
        writer.close()
    if reader.exit_status not in (0, None):
        raise BackupError(f"não consegui ler {label} no Proxmox (código {reader.exit_status})")
    if writer.exit_status not in (0, None):
        raise BackupError(f"o ESXi não gravou {label} (código {writer.exit_status}; falta espaço no datastore?)")


async def to_esxi(src_conn, xdir: str, dst_conn, folder: str, name: str, nic: str, bus: str, portgroup: str, keep_mac: bool, progress) -> str:
    """Pasta do vma extract (no Proxmox) -> pasta nova no datastore com .vmx e discos thin. Devolve o nome do .vmx."""
    import vcbt
    q = shlex.quote
    code, conf_txt, _ = await run(src_conn, f"cat {q(posixpath.join(xdir, 'qemu-server.conf'))}")
    if code:
        raise BackupError("o backup não tem qemu-server.conf (é um container LXC? container não vira VM do VMware)")
    conf = parse_qemu_conf(conf_txt)
    code, ls, _ = await run(src_conn, f"ls -1 {q(xdir)}")
    files = set(x.strip() for x in ls.splitlines())
    keys = [k for k in conf_disks(conf) if f"disk-drive-{k}.raw" in files]
    if not keys:
        raise BackupError("nenhum disco veio no backup (discos marcados como backup=0 no Proxmox não entram no vzdump)")
    code, vv, _ = await run(dst_conn, "vmware -v")
    hw = hw_version(vv)
    src_script = posixpath.join(xdir, "bastion-cbt.py")
    await vcbt.install(src_conn, src_script)
    await vcbt.install(dst_conn)
    await run(dst_conn, f"mkdir -p {q(folder)}")
    names = []
    for i, k in enumerate(keys):
        raw = posixpath.join(xdir, f"disk-drive-{k}.raw")
        code, out, _ = await run(src_conn, f"stat -c %s {q(raw)}")
        size = int(out.strip() or 0)
        dname = f"disk{i}.vmdk"
        code, out, err = await run(dst_conn, f"vmkfstools -c {size} -d thin {q(posixpath.join(folder, dname))}", 600)
        if code:
            raise BackupError(f"não criei o disco {dname} no datastore ({(err or out).strip()[:150]})")
        await pump_disk(src_conn, raw, size, dst_conn, posixpath.join(folder, f"disk{i}-flat.vmdk"), progress, src_script, vcbt.REMOTE, f"{k} ({size / 1e9:.0f} GB)")
        names.append(dname)
    vmx_name = re.sub(r"[^\w.-]", "_", name) + ".vmx"
    await vcbt.put_text(dst_conn, posixpath.join(folder, vmx_name), build_vmx(conf, name, names, hw, nic, bus, portgroup, keep_mac).encode())
    return vmx_name


__all__ = ["parse_qemu_conf", "conf_disks", "build_vmx", "extract", "source_from_archive", "to_esxi", "hw_version", "guest_os"]
