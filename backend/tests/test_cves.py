import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import cves  # noqa: E402


def cm(criteria, **k):
    return {"vulnerable": True, "criteria": criteria, **k}


NVD = {"vulnerabilities": [
    {"cve": {"id": "CVE-2023-30799", "published": "2023-07-19T00:00:00", "descriptions": [{"lang": "en", "value": "RouterOS privilege escalation"}],
             "metrics": {"cvssMetricV31": [{"cvssData": {"baseScore": 9.1, "baseSeverity": "CRITICAL"}}]},
             "configurations": [{"nodes": [{"cpeMatch": [cm("cpe:2.3:o:mikrotik:routeros:*:*:*:*:*:*:*:*", versionStartIncluding="6.0", versionEndExcluding="6.49.7"),
                                                         cm("cpe:2.3:o:mikrotik:routeros:*:*:*:*:*:*:*:*", versionStartIncluding="7.0", versionEndExcluding="7.10")]}]}]}},
    {"cve": {"id": "CVE-2024-0001", "published": "2024-01-01T00:00:00", "descriptions": [{"lang": "en", "value": "Junos issue"}],
             "metrics": {"cvssMetricV31": [{"cvssData": {"baseScore": 7.5, "baseSeverity": "HIGH"}}]},
             "configurations": [{"nodes": [{"cpeMatch": [cm("cpe:2.3:o:juniper:junos:21.4:r3-s5:*:*:*:*:*:*"), cm("cpe:2.3:o:juniper:junos:22.2:r1:*:*:*:*:*:*")]}]}]}},
    {"cve": {"id": "CVE-2024-0002", "descriptions": [], "metrics": {},
             "configurations": [{"nodes": [{"cpeMatch": [cm("cpe:2.3:o:cisco:ios_xe:17.9.4a:*:*:*:*:*:*:*"), {"vulnerable": False, "criteria": "cpe:2.3:h:cisco:asr1000:-"}]}]}]}},
]}


def test_match():
    rt = cves.compact_nvd(NVD, "mikrotik:routeros")
    assert [x["id"] for x in rt] == ["CVE-2023-30799"]
    assert cves.matches(rt, "7.9.2", "mikrotik:routeros", {"CVE-2023-30799"})[0]["kev"]
    assert cves.matches(rt, "7.11.2", "mikrotik:routeros", set()) == []
    assert cves.matches(rt, "6.49.6", "mikrotik:routeros", set())
    j = cves.compact_nvd(NVD, "juniper:junos")
    assert cves.matches(j, "21.4R3-S5.4", "juniper:junos", set()) and not cves.matches(j, "21.4R3-S6.1", "juniper:junos", set())
    x = cves.compact_nvd(NVD, "cisco:ios_xe")
    assert cves.matches(x, "17.9.4a", "cisco:ios_xe", set()) and not cves.matches(x, "17.9.4", "cisco:ios_xe", set())
    assert cves.products_for("cisco", "17.9.4a") == [("cisco:ios_xe", "17.9.4a")]
    assert cves.products_for("cisco", "7.3.2", head="!! IOS XR Configuration 7.3.2") == [("cisco:ios_xr", "7.3.2")]
    assert cves.products_for("cisco", "15.2(4)M7") == [("cisco:ios", "15.2(4)M7")]
    assert cves.products_for("huawei", "V800R021C10SPC600", "NE8000-M8") == [("huawei:ne8000m8_firmware", "v800r021c10spc600")]
    assert cves.vtuple("15.2\\(4\\)m7") == cves.vtuple("15.2(4)M7")
    s = cves.summary(cves.matches(rt, "7.9", "mikrotik:routeros", {"CVE-2023-30799"}))
    assert s == {"total": 1, "kev": 1, "critical": 1, "high": 0, "max_score": 9.1}
