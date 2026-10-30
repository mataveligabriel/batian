"""Virtualização: Proxmox VE (API REST /api2/json) e VMware ESXi / vCenter (API SOAP vSphere, via pyvmomi).

Tudo vira o mesmo modelo para a tela, a coleta e os alarmes:
  {hosts: [...], vms: [...], storages: [...], version, product}
Proxmox: token de API (PVEAPIToken, PVE 6.2+) ou usuário/senha (ticket, qualquer versão).
VMware: usuário/senha. A API SOAP é a mesma do ESXi 5.x ao 8.x e do vCenter; para hosts antigos o TLS aceita
TLS 1.0/cifras antigas quando "conferir certificado" está desligado. ESXi com licença gratuita só deixa LER pela
API — ligar/desligar VM e snapshot retornam "RestrictedVersion".
"""
import re
import ssl
import time
from datetime import datetime, timezone
from typing import Awaitable, Callable, Dict, List, Optional, Tuple

KINDS = {"proxmox": "Proxmox VE", "vmware": "VMware ESXi / vCenter"}
ACTIONS = {"start": "Ligar", "shutdown": "Desligar (sistema)", "reboot": "Reiniciar (sistema)", "stop": "Desligar à força",
           "reset": "Reset à força", "suspend": "Suspender", "resume": "Retomar"}
DEFAULTS = {"cpu": 90, "mem": 95, "storage": 90}       # alarmes (%)


class VirtError(Exception):
    pass


def _pct(a, b) -> float:
    try:
        return round(100.0 * float(a) / float(b), 1) if b else 0.0
    except (TypeError, ValueError):
        return 0.0


# ---------------------------------------------------------------- Proxmox
class Proxmox:
    """request(method, url, headers, data) -> (status, json) é injetado (httpx no servidor, falso nos testes)."""

    def __init__(self, base: str, request: Callable[..., Awaitable[tuple]], user: str = "", password: str = "",
                 token_id: str = "", secret: str = ""):
        self.base = base.rstrip("/") + "/api2/json"
        self.request, self.user, self.password, self.token_id, self.secret = request, user, password, token_id, secret
        self.headers: Dict[str, str] = {}
        self.csrf = ""

    async def login(self):
        if self.token_id:
            # token: "usuario@realm!nome" + segredo (UUID)
            self.headers = {"Authorization": f"PVEAPIToken={self.token_id}={self.secret}"}
            return
        st, j = await self.request("POST", f"{self.base}/access/ticket", {}, {"username": self.user, "password": self.password})
        d = (j or {}).get("data") or {}
        if st >= 400 or not d.get("ticket"):
            raise VirtError(f"Proxmox recusou o login (HTTP {st}) — usuário no formato usuario@pam ou usuario@pve")
        self.headers = {"Cookie": f"PVEAuthCookie={d['ticket']}"}
        self.csrf = d.get("CSRFPreventionToken", "")

    async def call(self, method: str, path: str, data: Optional[dict] = None):
        h = dict(self.headers)
        if method != "GET" and self.csrf:
            h["CSRFPreventionToken"] = self.csrf
        st, j = await self.request(method, f"{self.base}/{path.lstrip('/')}", h, data)
        if st == 401:
            raise VirtError("Proxmox: autenticação recusada (token/senha ou permissão)")
        if st == 403:
            raise VirtError(f"Proxmox: sem permissão para {path} (dê o papel PVEAuditor para ver ou PVEVMAdmin para ligar/desligar)")
        if st >= 400:
            msg = (j or {}).get("message") or (j or {}).get("errors") or ""
            raise VirtError(f"Proxmox {path}: HTTP {st} {str(msg)[:200]}".strip())
        return (j or {}).get("data")

    async def inventory(self) -> dict:
        ver = await self.call("GET", "version") or {}
        res = await self.call("GET", "cluster/resources") or []
        hosts, vms, stores = [], [], []
        for r in res:
            t = r.get("type")
            if t == "node":
                hosts.append({"id": r.get("node"), "name": r.get("node"), "state": "online" if r.get("status") == "online" else "offline",
                              "cpu_pct": round(100.0 * float(r.get("cpu") or 0), 1), "cpus": int(r.get("maxcpu") or 0),
                              "mem_used": int(r.get("mem") or 0), "mem_total": int(r.get("maxmem") or 0),
                              "disk_used": int(r.get("disk") or 0), "disk_total": int(r.get("maxdisk") or 0),
                              "uptime": int(r.get("uptime") or 0), "maintenance": False, "version": ver.get("version", ""), "model": ""})
            elif t in ("qemu", "lxc"):
                st = r.get("status") or "unknown"
                vms.append({"id": f"{r.get('node')}/{t}/{r.get('vmid')}", "vmid": r.get("vmid"), "name": r.get("name") or f"{t} {r.get('vmid')}",
                            "host": r.get("node"), "type": t, "state": {"running": "running", "stopped": "stopped", "paused": "paused"}.get(st, st),
                            "cpu_pct": round(100.0 * float(r.get("cpu") or 0), 1), "cpus": int(r.get("maxcpu") or 0),
                            "mem_used": int(r.get("mem") or 0), "mem_total": int(r.get("maxmem") or 0),
                            "disk_total": int(r.get("maxdisk") or 0), "uptime": int(r.get("uptime") or 0),
                            "template": bool(r.get("template")), "ip": "", "tags": (r.get("tags") or "").replace(";", ",")})
            elif t == "storage":
                stores.append({"id": r.get("id") or f"{r.get('node')}/{r.get('storage')}", "name": r.get("storage"), "host": r.get("node"),
                               "type": r.get("plugintype") or "", "used": int(r.get("disk") or 0), "total": int(r.get("maxdisk") or 0),
                               "active": r.get("status") == "available", "shared": bool(r.get("shared"))})
        # storage compartilhado aparece uma vez por nó: fica um só
        seen, uniq = set(), []
        for s in stores:
            k = s["name"] if s["shared"] else f"{s['host']}/{s['name']}"
            if k in seen:
                continue
            seen.add(k)
            uniq.append(s)
        return {"product": f"Proxmox VE {ver.get('version', '')}".strip(), "version": ver.get("version", ""), "hosts": hosts, "vms": vms, "storages": uniq}

    @staticmethod
    def _vm(vm_id: str) -> Tuple[str, str, str]:
        m = re.fullmatch(r"([\w.-]+)/(qemu|lxc)/(\d+)", vm_id or "")
        if not m:
            raise VirtError("VM inválida")
        return m.group(1), m.group(2), m.group(3)

    async def action(self, vm_id: str, action: str):
        node, t, vmid = self._vm(vm_id)
        pve = {"start": "start", "shutdown": "shutdown", "reboot": "reboot", "stop": "stop", "reset": "reset",
               "suspend": "suspend", "resume": "resume"}.get(action)
        if not pve or (t == "lxc" and action == "reset"):
            raise VirtError(f"Ação {action} não existe para {t}")
        return await self.call("POST", f"nodes/{node}/{t}/{vmid}/status/{pve}", {})

    async def snapshots(self, vm_id: str) -> List[dict]:
        node, t, vmid = self._vm(vm_id)
        out = []
        for s in await self.call("GET", f"nodes/{node}/{t}/{vmid}/snapshot") or []:
            if s.get("name") == "current":
                continue
            out.append({"id": s.get("name"), "name": s.get("name"), "description": s.get("description") or "", "parent": s.get("parent") or "",
                        "created": datetime.fromtimestamp(int(s["snaptime"]), timezone.utc).isoformat() if s.get("snaptime") else None,
                        "memory": bool(s.get("vmstate"))})
        return sorted(out, key=lambda x: x["created"] or "")

    async def snapshot_create(self, vm_id: str, name: str, description: str = "", memory: bool = False):
        node, t, vmid = self._vm(vm_id)
        data = {"snapname": name, "description": description}
        if t == "qemu" and memory:
            data["vmstate"] = 1
        return await self.call("POST", f"nodes/{node}/{t}/{vmid}/snapshot", data)

    async def snapshot_delete(self, vm_id: str, snap: str):
        node, t, vmid = self._vm(vm_id)
        return await self.call("DELETE", f"nodes/{node}/{t}/{vmid}/snapshot/{snap}")

    async def snapshot_revert(self, vm_id: str, snap: str):
        node, t, vmid = self._vm(vm_id)
        return await self.call("POST", f"nodes/{node}/{t}/{vmid}/snapshot/{snap}/rollback", {})


# ---------------------------------------------------------------- VMware (síncrono: rodar em thread)
def vmware_ssl(verify: bool) -> ssl.SSLContext:
    if verify:
        return ssl.create_default_context()
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        ctx.minimum_version = ssl.TLSVersion.TLSv1         # ESXi 5.x / 6.0
        ctx.set_ciphers("DEFAULT:@SECLEVEL=0")
    except (ValueError, ssl.SSLError):
        pass
    return ctx


def _fault(e: Exception) -> str:
    name = type(e).__name__
    msg = getattr(e, "msg", "") or str(e)
    if "RestrictedVersion" in name or "prohibits" in msg:
        return "ESXi com licença gratuita só permite leitura pela API (ligar/desligar e snapshot precisam de licença paga ou do vCenter)"
    if "InvalidLogin" in name:
        return "usuário ou senha recusados pelo VMware"
    if "ToolsUnavailable" in name or "Tools" in msg:
        return "o VMware Tools não está rodando nessa VM — use 'Desligar à força' / 'Reset à força'"
    if "InvalidPowerState" in name or "InvalidState" in name:
        return "a VM não está no estado certo para essa ação"
    return f"{name}: {msg}"[:300]


class VMware:
    def __init__(self, host: str, port: int, user: str, password: str, verify: bool = False):
        self.host, self.port, self.user, self.password, self.verify = host, port, user, password, verify
        self.si = None

    def connect(self):
        from pyVim.connect import SmartConnect
        try:
            self.si = SmartConnect(host=self.host, port=self.port, user=self.user, pwd=self.password, sslContext=vmware_ssl(self.verify),
                                   connectionPoolTimeout=30)
        except Exception as e:
            raise VirtError(f"VMware: {_fault(e)}")
        return self

    def close(self):
        try:
            from pyVim.connect import Disconnect
            if self.si:
                Disconnect(self.si)
        except Exception:
            pass
        self.si = None

    def _collect(self, vimtype, paths: List[str]) -> List[Tuple[object, dict]]:
        """Uma ida ao servidor para todos os objetos do tipo (PropertyCollector), em vez de uma por VM."""
        from pyVmomi import vim, vmodl
        content = self.si.RetrieveContent()
        view = content.viewManager.CreateContainerView(content.rootFolder, [vimtype], True)
        try:
            ts = vmodl.query.PropertyCollector.TraversalSpec(name="tv", path="view", skip=False, type=vim.view.ContainerView)
            os_ = vmodl.query.PropertyCollector.ObjectSpec(obj=view, skip=True, selectSet=[ts])
            ps = vmodl.query.PropertyCollector.PropertySpec(type=vimtype, pathSet=paths, all=False)
            fs = vmodl.query.PropertyCollector.FilterSpec(objectSet=[os_], propSet=[ps])
            pc = content.propertyCollector
            out = []
            res = pc.RetrievePropertiesEx([fs], vmodl.query.PropertyCollector.RetrieveOptions(maxObjects=1000))
            while res:
                for o in res.objects:
                    out.append((o.obj, {p.name: p.val for p in (o.propSet or [])}))
                res = pc.ContinueRetrievePropertiesEx(res.token) if res.token else None
            return out
        finally:
            view.Destroy()

    def inventory(self) -> dict:
        from pyVmomi import vim
        about = self.si.RetrieveContent().about
        hosts, hmap = [], {}
        for obj, p in self._collect(vim.HostSystem, ["name", "summary.hardware", "summary.quickStats", "runtime.connectionState",
                                                     "runtime.inMaintenanceMode", "runtime.bootTime", "summary.config.product.version"]):
            hw, qs = p.get("summary.hardware"), p.get("summary.quickStats")
            mhz = (hw.cpuMhz * hw.numCpuCores) if hw else 0
            boot = p.get("runtime.bootTime")
            hmap[obj._moId] = p.get("name")
            cs = str(p.get("runtime.connectionState") or "")
            hosts.append({"id": obj._moId, "name": p.get("name"), "state": "online" if cs == "connected" else "offline",
                          "cpu_pct": _pct(qs.overallCpuUsage if qs else 0, mhz), "cpus": int(hw.numCpuThreads if hw else 0),
                          "mem_used": int((qs.overallMemoryUsage or 0) * 1048576) if qs else 0, "mem_total": int(hw.memorySize if hw else 0),
                          "disk_used": 0, "disk_total": 0, "uptime": int(time.time() - boot.timestamp()) if boot else 0,
                          "maintenance": bool(p.get("runtime.inMaintenanceMode")), "version": p.get("summary.config.product.version") or "",
                          "model": f"{hw.vendor} {hw.model}".strip() if hw else ""})
        vms = []
        for obj, p in self._collect(vim.VirtualMachine, ["name", "runtime.powerState", "runtime.host", "runtime.maxCpuUsage", "summary.quickStats",
                                                         "config.hardware.numCPU", "config.hardware.memoryMB", "config.template",
                                                         "summary.storage.committed", "summary.storage.uncommitted", "guest.ipAddress",
                                                         "guest.toolsRunningStatus"]):
            qs = p.get("summary.quickStats")
            ps = str(p.get("runtime.powerState") or "")
            h = p.get("runtime.host")
            vms.append({"id": obj._moId, "vmid": obj._moId, "name": p.get("name"), "host": hmap.get(h._moId, "") if h is not None else "",
                        "type": "vm", "state": {"poweredOn": "running", "poweredOff": "stopped", "suspended": "suspended"}.get(ps, ps),
                        "cpu_pct": _pct(qs.overallCpuUsage if qs else 0, p.get("runtime.maxCpuUsage") or 0), "cpus": int(p.get("config.hardware.numCPU") or 0),
                        "mem_used": int((qs.hostMemoryUsage or 0) * 1048576) if qs else 0, "mem_total": int((p.get("config.hardware.memoryMB") or 0) * 1048576),
                        "disk_total": int((p.get("summary.storage.committed") or 0) + (p.get("summary.storage.uncommitted") or 0)),
                        "uptime": int(qs.uptimeSeconds or 0) if qs else 0, "template": bool(p.get("config.template")),
                        "ip": p.get("guest.ipAddress") or "", "tools": str(p.get("guest.toolsRunningStatus") or ""), "tags": ""})
        stores = []
        for obj, p in self._collect(vim.Datastore, ["summary.name", "summary.capacity", "summary.freeSpace", "summary.accessible",
                                                    "summary.type", "summary.multipleHostAccess"]):
            cap = int(p.get("summary.capacity") or 0)
            stores.append({"id": obj._moId, "name": p.get("summary.name"), "host": "", "type": p.get("summary.type") or "",
                           "used": cap - int(p.get("summary.freeSpace") or 0), "total": cap, "active": bool(p.get("summary.accessible")),
                           "shared": bool(p.get("summary.multipleHostAccess"))})
        return {"product": about.fullName, "version": about.version, "api_type": about.apiType, "hosts": hosts, "vms": vms, "storages": stores}

    def _vm(self, vm_id: str):
        from pyVmomi import vim
        if not re.fullmatch(r"[\w.-]+", vm_id or ""):
            raise VirtError("VM inválida")
        vm = vim.VirtualMachine(vm_id, self.si._stub)
        try:
            vm.name
        except Exception:
            raise VirtError("VM não encontrada (foi removida?)")
        return vm

    @staticmethod
    def _wait(task, timeout: int = 120):
        t0 = time.time()
        while time.time() - t0 < timeout:
            st = str(task.info.state)
            if st == "success":
                return task.info.result
            if st == "error":
                raise VirtError(f"VMware: {_fault(task.info.error)}")
            time.sleep(0.5)
        return None                                  # segue rodando no VMware; a tela atualiza na próxima coleta

    def action(self, vm_id: str, action: str):
        vm = self._vm(vm_id)
        try:
            if action == "start" or action == "resume":
                return self._wait(vm.PowerOnVM_Task())
            if action == "stop":
                return self._wait(vm.PowerOffVM_Task())
            if action == "reset":
                return self._wait(vm.ResetVM_Task())
            if action == "suspend":
                return self._wait(vm.SuspendVM_Task())
            if action == "shutdown":
                return vm.ShutdownGuest()
            if action == "reboot":
                return vm.RebootGuest()
        except VirtError:
            raise
        except Exception as e:
            raise VirtError(f"VMware: {_fault(e)}")
        raise VirtError(f"Ação {action} não existe")

    def _tree(self, vm) -> list:
        out = []

        def walk(lst, parent=""):
            for s in lst or []:
                out.append({"id": str(s.id), "name": s.name, "description": s.description or "", "parent": parent,
                            "created": s.createTime.isoformat() if s.createTime else None, "memory": str(s.state) == "poweredOn", "_ref": s.snapshot})
                walk(s.childSnapshotList, s.name)
        walk(vm.snapshot.rootSnapshotList if vm.snapshot else [])
        return out

    def snapshots(self, vm_id: str) -> List[dict]:
        return [{k: v for k, v in s.items() if k != "_ref"} for s in self._tree(self._vm(vm_id))]

    def _snap(self, vm_id: str, sid: str):
        vm = self._vm(vm_id)
        s = next((x for x in self._tree(vm) if x["id"] == str(sid)), None)
        if not s:
            raise VirtError("Snapshot não encontrado")
        return s["_ref"]

    def snapshot_create(self, vm_id: str, name: str, description: str = "", memory: bool = False):
        vm = self._vm(vm_id)
        try:
            return self._wait(vm.CreateSnapshot_Task(name=name, description=description, memory=memory, quiesce=False), 600)
        except VirtError:
            raise
        except Exception as e:
            raise VirtError(f"VMware: {_fault(e)}")

    def snapshot_delete(self, vm_id: str, sid: str):
        try:
            return self._wait(self._snap(vm_id, sid).RemoveSnapshot_Task(removeChildren=False), 600)
        except VirtError:
            raise
        except Exception as e:
            raise VirtError(f"VMware: {_fault(e)}")

    def snapshot_revert(self, vm_id: str, sid: str):
        try:
            return self._wait(self._snap(vm_id, sid).RevertToSnapshot_Task(), 600)
        except VirtError:
            raise
        except Exception as e:
            raise VirtError(f"VMware: {_fault(e)}")


# ---------------------------------------------------------------- alarmes
def alarms(inv: dict, th: dict, watched: List[str]) -> List[dict]:
    """[{key, severity, title, description}] do que está errado agora (o que não está na lista normalizou)."""
    th = {**DEFAULTS, **(th or {})}
    out = []
    for h in inv.get("hosts") or []:
        if h["state"] != "online":
            out.append({"key": f"host:{h['id']}", "severity": "critical", "title": f"Host {h['name']} fora do ar / sem resposta"})
            continue
        if h["cpu_pct"] >= th["cpu"]:
            out.append({"key": f"cpu:{h['id']}", "severity": "medium", "title": f"Host {h['name']} com CPU em {h['cpu_pct']}%"})
        mp = _pct(h["mem_used"], h["mem_total"])
        if mp >= th["mem"]:
            out.append({"key": f"mem:{h['id']}", "severity": "high", "title": f"Host {h['name']} com memória em {mp}%"})
    for s in inv.get("storages") or []:
        if not s["active"]:
            out.append({"key": f"ds:{s['id']}", "severity": "high", "title": f"Storage {s['name']} indisponível"})
            continue
        sp = _pct(s["used"], s["total"])
        if s["total"] and sp >= th["storage"]:
            out.append({"key": f"ds:{s['id']}", "severity": "high" if sp >= 97 else "medium", "title": f"Storage {s['name']} com {sp}% usado"})
    w = set(watched or [])
    for v in inv.get("vms") or []:
        if v["id"] in w and v["state"] != "running":
            out.append({"key": f"vm:{v['id']}", "severity": "high", "title": f"VM {v['name']} {('parada' if v['state'] == 'stopped' else v['state'])}"})
    return out


def summary(inv: dict) -> dict:
    vms = [v for v in inv.get("vms") or [] if not v.get("template")]
    hosts = inv.get("hosts") or []
    cpu_tot = sum(h["cpus"] for h in hosts)
    return {"hosts": len(hosts), "hosts_up": sum(1 for h in hosts if h["state"] == "online"), "vms": len(vms),
            "vms_running": sum(1 for v in vms if v["state"] == "running"),
            "cpu_pct": round(sum(h["cpu_pct"] * h["cpus"] for h in hosts) / cpu_tot, 1) if cpu_tot else 0.0,
            "mem_used": sum(h["mem_used"] for h in hosts), "mem_total": sum(h["mem_total"] for h in hosts),
            "storage_used": sum(s["used"] for s in inv.get("storages") or []), "storage_total": sum(s["total"] for s in inv.get("storages") or [])}
