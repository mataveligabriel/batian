import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import virt as V  # noqa: E402

RES = [{"type": "node", "node": "pve1", "status": "online", "cpu": 0.95, "maxcpu": 8, "mem": 7.8e9, "maxmem": 8e9},
       {"type": "node", "node": "pve2", "status": "offline"},
       {"type": "qemu", "node": "pve1", "vmid": 100, "name": "dns", "status": "running", "cpu": 0.1, "maxcpu": 2},
       {"type": "qemu", "node": "pve1", "vmid": 101, "name": "radius", "status": "stopped"},
       {"type": "qemu", "node": "pve1", "vmid": 900, "name": "tpl", "status": "stopped", "template": 1},
       {"type": "storage", "id": "storage/pve1/ceph", "storage": "ceph", "node": "pve1", "status": "available", "disk": 98, "maxdisk": 100, "shared": 1},
       {"type": "storage", "id": "storage/pve2/ceph", "storage": "ceph", "node": "pve2", "status": "available", "disk": 98, "maxdisk": 100, "shared": 1}]


def _client(calls):
    async def req(method, url, headers, data):
        calls.append((method, url.split("/api2/json/")[1], headers, data))
        p = url.split("/api2/json/")[1]
        if p == "version":
            return 200, {"data": {"version": "7.4-3"}}
        if p == "cluster/resources":
            return 200, {"data": RES}
        return 200, {"data": "UPID:ok"}
    return V.Proxmox("https://192.0.2.10:8006", req, token_id="mon@pve!bastion", secret="s")


def test_proxmox_inventory_and_alarms():
    calls = []
    p = _client(calls)
    asyncio.run(p.login())
    inv = asyncio.run(p.inventory())
    assert calls[0][2]["Authorization"] == "PVEAPIToken=mon@pve!bastion=s"
    assert inv["version"] == "7.4-3" and len(inv["hosts"]) == 2 and len(inv["storages"]) == 1
    s = V.summary(inv)
    assert s["vms"] == 2 and s["vms_running"] == 1 and s["hosts_up"] == 1
    keys = {a["key"] for a in V.alarms(inv, {}, ["pve1/qemu/101"])}
    assert keys == {"host:pve2", "cpu:pve1", "mem:pve1", "ds:storage/pve1/ceph", "vm:pve1/qemu/101"}
    assert {a["key"] for a in V.alarms(inv, {"cpu": 99, "mem": 99, "storage": 99}, [])} == {"host:pve2"}


def test_proxmox_actions():
    calls = []
    p = _client(calls)
    asyncio.run(p.login())
    asyncio.run(p.action("pve1/qemu/100", "shutdown"))
    assert calls[-1][:2] == ("POST", "nodes/pve1/qemu/100/status/shutdown")
    asyncio.run(p.snapshot_create("pve1/lxc/200", "antes", memory=True))
    assert calls[-1][1] == "nodes/pve1/lxc/200/snapshot" and "vmstate" not in calls[-1][3]
    for bad in ("../../x", "pve1/qemu/abc", ""):
        try:
            asyncio.run(p.action(bad, "start"))
            raise AssertionError(bad)
        except V.VirtError:
            pass
    try:
        asyncio.run(p.action("pve1/lxc/200", "reset"))
        raise AssertionError("lxc reset")
    except V.VirtError:
        pass


def test_proxmox_ticket_and_errors():
    async def req(method, url, headers, data):
        if url.endswith("access/ticket"):
            return 200, {"data": {"ticket": "T", "CSRFPreventionToken": "C"}}
        if method == "POST":
            assert headers["CSRFPreventionToken"] == "C" and headers["Cookie"] == "PVEAuthCookie=T"
            return 403, {}
        return 200, {"data": {}}
    p = V.Proxmox("https://192.0.2.10:8006", req, user="root@pam", password="x")
    asyncio.run(p.login())
    try:
        asyncio.run(p.action("pve1/qemu/100", "start"))
        raise AssertionError("403")
    except V.VirtError as e:
        assert "PVEVMAdmin" in str(e)


def test_vmware_fault_messages():
    class RestrictedVersion(Exception):
        msg = "Current license or ESXi version prohibits execution of the requested operation."
    assert "licença gratuita" in V._fault(RestrictedVersion())
    assert V.vmware_ssl(False).verify_mode.name == "CERT_NONE"
