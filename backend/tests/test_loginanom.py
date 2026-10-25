import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import loginanom as la  # noqa: E402

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
HIST = [{"ip": "198.51.100.10", "cc": "BR", "asn": 64500, "asn_name": "PROVEDOR", "ua": UA, "at": (T0 - timedelta(days=i, hours=i % 3)).isoformat()} for i in range(30)]
CFG = dict(la.DEFAULTS)


def kinds(f):
    return sorted(x["kind"] for x in f)


def test_normal_and_signals():
    cur = {"ip": "198.51.100.10", "cc": "BR", "asn": 64500, "ua": UA, "at": (T0 + timedelta(hours=1)).isoformat()}
    assert la.analyze(cur, HIST, 0, CFG) == []
    f = la.analyze({**cur, "ip": "203.0.113.5", "cc": "NL", "asn": 64511}, HIST, 0, CFG)
    assert "new_country" in kinds(f) and "impossible_travel" in kinds(f) and la.overall(f) == "critical"
    f = la.analyze({**cur, "ip": "203.0.113.5", "asn": 64511, "at": (T0 + timedelta(hours=16)).isoformat()}, HIST, 0, CFG)
    assert kinds(f) == ["new_asn", "odd_hour"] and la.overall(f) == "high", f
    f = la.analyze({**cur, "ua": "Mozilla/5.0 (Linux; Android 14) Firefox/131.0"}, HIST, 6, CFG)
    assert kinds(f) == ["brute_success", "new_device"]
    assert la.analyze({**cur, "ip": "198.51.100.77"}, HIST[:2], 0, CFG) == []      # pouco histórico: não acusa IP novo


def test_geofence():
    c = {**CFG, "allowed_countries": ["BR"], "trusted_nets": ["203.0.113.0/24"]}
    assert la.geofence_blocks("US", "8.8.8.8", c, False)
    assert not la.geofence_blocks("US", "203.0.113.9", c, False)
    assert not la.geofence_blocks("BR", "8.8.8.8", c, False)
    assert not la.geofence_blocks("", "8.8.8.8", c, False)
    assert la.device_of(UA) == "Chrome / Windows"
