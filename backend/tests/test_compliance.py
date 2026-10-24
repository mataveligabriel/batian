"""Conformidade: regras sobre o backup e versão do firmware (python -m pytest backend/tests/test_compliance.py)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import compliance as c  # noqa: E402

HW = "!Software Version V800R021C10SPC100\n stelnet server enable\n ntp-service unicast-server 192.0.2.1\n telnet server enable\n"


def test_rules_and_score():
    rules = [dict(r, id=r["preset"]) for r in c.PRESETS if r["vendor"] == "huawei"]
    res = {x["rule_id"]: x for x in c.check(HW, rules)}
    assert res["huawei-ntp"]["ok"] and res["huawei-ssh"]["ok"]
    assert res["huawei-telnet"]["ok"] is False and res["huawei-telnet"]["evidence"] == "telnet server enable"
    assert 0 < c.score(list(res.values()), {r["id"]: r for r in rules}) < 100


def test_firmware():
    assert c.firmware("huawei", HW)["version"] == "V800R021C10SPC100"
    assert c.firmware("mikrotik", "# 2026-10-24 by RouterOS 7.14.3\n# model = CCR2004-1G-12S+2XS\n") == {"version": "7.14.3", "model": "CCR2004-1G-12S+2XS"}
    assert c.firmware("juniper", "set version 20.4R3.8\n")["version"] == "20.4R3.8"
    assert c.version_cmp("V800R021C10SPC100", "V800R022C00") == -1 and c.version_cmp("7.14.3", "7.9") == 1
