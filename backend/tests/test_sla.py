"""SLA: quedas, manutenção e disponibilidade (python -m pytest backend/tests/test_sla.py)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sla  # noqa: E402

H = 3600


def test_intervals():
    assert sla.union([(0, 10), (5, 20), (30, 40)]) == [(0, 20), (30, 40)]
    assert sla.subtract([(0, 100)], [(10, 20), (50, 60)]) == [(0, 10), (20, 50), (60, 100)]
    assert sla.intersect([(0, 100)], [(90, 120)]) == [(90, 100)]


def test_iface_and_probe():
    ev = [{"at": "2026-08-30T00:00:00+00:00", "status": "down"}, {"at": "2026-09-01T03:30:00+00:00", "status": "up"}]
    s, e = sla.month_range("2026-09")
    out = sla.iface_outages(ev, s.timestamp(), e.timestamp(), e.timestamp())
    assert out == [(s.timestamp(), s.timestamp() + 30 * 60)]          # já estava fora quando o mês começou
    down, meas = sla.probe_outages([{"hour": s, "n": 60, "fail_n": 6}, {"hour": s.timestamp() + H, "n": 0}])
    assert down == [(s.timestamp(), s.timestamp() + 360)] and meas == [(s.timestamp(), s.timestamp() + H)]


def test_summarize():
    s, e = 0.0, 30 * 86400.0
    comps = [{"label": "A", "type": "device", "down": [(100, 100 + 2 * H)], "measured": None},
             {"label": "B", "type": "interface", "down": [(10 * 86400, 10 * 86400 + 1800), (11 * 86400, 11 * 86400 + 1200)],
              "maint": [(11 * 86400 - 300, 11 * 86400 + H)], "measured": None}]
    r = sla.summarize(comps, s, e, e + 1, 99.9)
    assert r["downtime_s"] == 2 * H + 1800 and r["maint_s"] == 1200 and r["outages"] == 2 and r["met"] is False
    assert abs(r["availability"] - 100 * (1 - (2 * H + 1800) / e)) < 1e-3
    assert sla.fmt_dur(7200) == "2h" and sla.fmt_dur(5400) == "1h 30min"
