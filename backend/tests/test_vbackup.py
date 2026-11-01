import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import vbackup as B  # noqa: E402

VMX = '''displayName = "web01"
nvram = "web01.nvram"
scsi0:0.present = "TRUE"
scsi0:0.fileName = "web01.vmdk"
scsi0:1.present = "FALSE"
scsi0:1.fileName = "old.vmdk"
sata0:0.fileName = "/vmfs/volumes/ds2/web01/data.vmdk"
ide1:0.deviceType = "cdrom-image"
ide1:0.fileName = "/vmfs/volumes/iso/u.iso"
'''


def test_vmx_and_vmdk():
    assert B.vmx_disks(VMX) == ["web01.vmdk", "/vmfs/volumes/ds2/web01/data.vmdk"]
    assert B.vmx_get(VMX, "nvram") == "web01.nvram"
    assert B.vmdk_extents('# Disk\nRW 41943040 VMFS "web01-flat.vmdk"\nRW 10 VMFSSPARSE "x-delta.vmdk"\n') == ["web01-flat.vmdk", "x-delta.vmdk"]


def test_getallvms():
    out = ("Vmid   Name     File                          Guest OS       Version   Annotation\n"
           "1      web01    [datastore1] web01/web01.vmx  ubuntu64Guest  vmx-14\n"
           "12     db 01    [ds 2] db 01/db 01.vmx        debian10       vmx-19    banco principal\n")
    v = B.parse_getallvms(out)
    assert v[0] == {"vmid": "1", "name": "web01", "ds": "datastore1", "rel": "web01/web01.vmx"}
    assert v[1]["name"] == "db 01" and v[1]["ds"] == "ds 2" and v[1]["rel"] == "db 01/db 01.vmx"


def test_due_and_names():
    d = datetime(2026, 11, 2, 1, 30)
    j = {"days": [d.weekday()], "time": "01:00"}
    assert B.due(j, d) == "2026-11-02 01:00"
    assert B.due({**j, "last_slot": "2026-11-02 01:00"}, d) is None
    assert B.due({**j, "enabled": False}, d) is None
    assert B.due({**j, "days": [(d.weekday() + 1) % 7]}, d) is None
    assert B.safe("../db 01/x") == "db_01_x" and B.STAMP_RE.match(B.stamp(d)) and B.STAMP_RE.match("2026-01-01_0100")
