"""GPON: inventário a partir do backup, sinal e queda em massa (python -m pytest backend/tests/test_gpon.py)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import gpon  # noqa: E402

CFG = """interface gpon-olt_1/2/1
  onu 1 type ZTE-F601 sn ZTEGc1234567
!
interface gpon-onu_1/2/1:1
  name cliente-joao
  description Joao da Silva
!
pon-onu-mng gpon-onu_1/2/1:1
  wan-ip 1 mode pppoe username joao@provedor password Segredo vlan-profile v100 host 1
!
interface gpon_olt-1/3/2
  onu 5 type ZTE-F6600P sn FHTT12345678
!
pon-onu-mng gpon_onu-1/3/2:5
  wan-ip ipv4 mode pppoe username maria password x
!
"""


def test_inventory():
    inv = gpon.parse_inventory(CFG)
    assert inv[(2, 1, 1)] == {"pppoe": ["joao@provedor"], "type": "ZTE-F601", "sn": "ZTEGC1234567", "name": "cliente-joao", "description": "Joao da Silva"}
    assert inv[(3, 2, 5)]["pppoe"] == ["maria"] and "Segredo" not in str(inv)


def test_power_and_summary():
    p = gpon.parse_power("gpon-onu_1/2/1:1   -21.345(dbm)\ngpon-onu_1/2/1:2   N/A\ngpon_onu-1/3/2:5  -28.5 (dBm)")
    assert p == {(2, 1, 1): -21.345, (3, 2, 5): -28.5}
    onus = [{"off": False, "rx": -20}, {"off": False, "rx": -28}, {"off": True, "phase": "LOS"}, {"off": True, "phase": "DyingGasp"}]
    s = gpon.pon_summary(onus, -27, 128)
    assert (s["online"], s["los"], s["dying_gasp"], s["rx_bad"], s["rx_median"]) == (2, 1, 1, 1, -24)
    cfg = gpon.DEFAULTS
    assert gpon.mass_event(0, 10, 20, 10, cfg) and not gpon.mass_event(0, 2, 20, 2, cfg)
    assert gpon.recovered(1, 20, 10, cfg) and not gpon.recovered(8, 20, 10, cfg)
