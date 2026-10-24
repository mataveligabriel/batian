import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import cmdpolicy as cp  # noqa: E402

RULES = [dict(cp.clean_rule(p), id=p["preset"]) for p in cp.PRESETS]
OP = {"id": "u2", "role": "operator"}
ADM = {"id": "u1", "role": "admin"}
DEV = {"id": "d1", "device_type": "huawei", "tags": ["borda"]}


def test_match():
    assert cp.match(RULES, OP, DEV, "reset saved-configuration", "terminal")["action"] == "block"
    assert cp.match(RULES, OP, DEV, "reboot", "terminal")["action"] == "approve"
    assert cp.match(RULES, OP, DEV, "undo peer 192.0.2.1", "batch")["action"] == "approve"
    assert cp.match(RULES, OP, DEV, "  shutdown ", "terminal")["action"] == "alert"
    assert cp.match(RULES, OP, DEV, "display bgp peer", "terminal") is None
    assert cp.match(RULES, OP, DEV, "undo shutdown", "terminal") is None
    ex = [cp.clean_rule({"name": "admins podem", "pattern": "^reboot", "action": "allow", "roles": ["admin"]})] + RULES
    assert cp.match(ex, ADM, DEV, "reboot", "terminal")["action"] == "allow"
    assert cp.match(ex, OP, DEV, "reboot", "terminal")["action"] == "approve"
    scoped = [cp.clean_rule({"name": "só borda", "pattern": "^commit", "action": "approve", "tags": ["core"]})]
    assert cp.match(scoped, OP, DEV, "commit", "terminal") is None
    ev = cp.evaluate(RULES, OP, DEV, cp.command_lines("system-view\n# c\nreboot\nreset saved-configuration\n"), "batch")
    assert ev["action"] == "block" and len(ev["hits"]) == 2


def test_tracker():
    t = cp.LineTracker()
    t.feed("Welcome\r\n<HUAWEI>")
    t.mark_prompt()
    t.feed("dis")
    t.feed("\x08 \x08\x08 \x08")      # apagou 2
    t.feed("splay ver")
    assert t.command() == "dsplay ver", t.command()
    t2 = cp.LineTracker()
    t2.feed("\r\n<HUAWEI>")
    t2.mark_prompt()
    t2.feed("reboo")
    t2.feed("\x1b[1Dot")                     # volta 1 e escreve "ot"
    assert t2.command() == "reboot", t2.command()
    t3 = cp.LineTracker()
    t3.feed("\r\nRouter#")
    t3.feed("\r\x1b[KRouter#reload")         # redesenho da linha (histórico)
    assert t3.command() == "reload"
    assert cp.strip_prompt("[admin@MikroTik] > /system reboot") == "/system reboot"
    assert cp.strip_prompt("OLT(config)#interface gpon") == "interface gpon"
    assert cp.at_password("Password:")
