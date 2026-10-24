"""Manutenção: comparação do pré/pós-check e escopo das janelas (python -m pytest backend/tests/test_maint.py)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import maint  # noqa: E402

PRE = "Area Id Interface Neighbor id State\n0.0.0.0 GE0/0/1 10.0.0.2 Full\n0.0.0.0 GE0/0/2 10.0.0.3 Full\n"
POST = "Area Id Interface Neighbor id State\n0.0.0.0 GE0/0/1 10.0.0.2 Full\n0.0.0.0 GE0/0/2 10.0.0.3 Init\n"
BGP1 = "  198.51.100.1  4  64500  123456  654321  0  1d02h  Established  900000\n"
BGP2 = "  198.51.100.1  4  64500  123999  654999  0  1d03h  Established  900000\n"


def test_compare():
    r = maint.compare({"o": {"kind": "ospf", "out": PRE}}, {"o": {"kind": "ospf", "out": POST}})[0]
    assert r["delta"] == {"vizinhos Full": (2, 1)} and r["worse"] and r["changed"]
    r = maint.compare({"b": {"kind": "bgp", "out": BGP1}}, {"b": {"kind": "bgp", "out": BGP2}})[0]
    assert not r["changed"] and not r["worse"]          # só uptime e contadores mudaram


def test_scope_and_state():
    w = {"start": "2026-10-24T10:00:00+00:00", "end": "2026-10-24T12:00:00+00:00", "tags": ["Borda"], "device_ids": ["d9"]}
    assert maint.covers(w, "d1", ["Borda"]) and maint.covers(w, "d9") and not maint.covers(w, "d2", ["GPON"])
    t = maint._ts("2026-10-24T11:00:00+00:00")
    assert maint.window_state(w, t) == "active" and maint.window_state(w, t + 7200) == "done"
    assert maint.window_state({**w, "status": "cancelled"}, t) == "cancelled"
