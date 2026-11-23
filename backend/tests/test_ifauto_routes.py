"""Monitoramento automático de interfaces e grupos de alerta do Telegram."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("JWT_SECRET", "x" * 40)
import alertroutes as R  # noqa: E402
import ifauto as A  # noqa: E402

IFS = [{"index": 1, "name": "GigabitEthernet0/0/1", "alias": "UPLINK", "oper": "up", "admin": "up"},
       {"index": 2, "name": "GigabitEthernet0/0/2", "alias": "", "oper": "down", "admin": "up"},
       {"index": 3, "name": "GigabitEthernet0/0/1.100", "alias": "", "oper": "up", "admin": "up"},
       {"index": 4, "name": "Vlanif10", "alias": "", "oper": "up", "admin": "up"},
       {"index": 5, "name": "LoopBack0", "alias": "", "oper": "up", "admin": "up"},
       {"index": 6, "name": "Eth-Trunk1", "alias": "BACKBONE", "oper": "up", "admin": "up"},
       {"index": 7, "name": "XGigabitEthernet0/0/3", "alias": "CLIENTE vlan 10", "oper": "up", "admin": "up"}]


def test_presets_and_plan():
    up = A.clean_rule({**A.PRESETS["physical_up"], "preset": "physical_up"})
    assert [i["index"] for i in A.plan(IFS, up, set(), set())] == [1, 6, 7]
    # já monitorada não repete; ignorada pelo usuário não volta
    assert [i["index"] for i in A.plan(IFS, up, {1}, {6})] == [7]
    alias = A.clean_rule(A.PRESETS["physical_desc"])
    assert [i["index"] for i in A.plan(IFS, alias, set(), set())] == [1, 6, 7]
    every = A.clean_rule(A.PRESETS["physical_all"])
    assert [i["index"] for i in A.plan(IFS, every, set(), set())] == [1, 2, 6, 7]
    custom = A.clean_rule({"include": "backbone|uplink", "exclude": "", "preset": "custom"})
    assert [i["index"] for i in A.plan(IFS, custom, set(), set())] == [1, 6]
    assert A.preview(IFS, up) == {"match": 3, "total": 7}
    with pytest.raises(A.RuleError):
        A.clean_rule({"include": "(("})


def test_routes():
    with pytest.raises(R.RouteError):
        R.clean({"name": "x", "chat_id": "abc"})
    with pytest.raises(R.RouteError):
        R.clean({"name": "x", "chat_id": "-100123456", "bot": "custom"})            # sem token
    with pytest.raises(R.RouteError):
        R.clean({"name": "x", "chat_id": "-100123456", "scope": "tags"})            # sem tag
    r = R.clean({"name": "Grupo A", "chat_id": "-1001234567890", "scope": "tags", "tags": ["GRUPO-A"], "kinds": ["interface", "zzz"], "exclusive": True})
    assert r["kinds"] == ["interface"] and r["exclusive"] and r["token_enc"] == ""
    dev_a, dev_b = {"id": "a", "tags": ["GRUPO-A"]}, {"id": "b", "tags": ["GRUPO-B"]}
    assert R.matches(r, dev_a, "interface") and not R.matches(r, dev_b, "interface") and not R.matches(r, dev_a, "config")
    assert not R.matches({**r, "enabled": False}, dev_a, "interface") and not R.matches(r, None, "interface")
    d = R.clean({"name": "VIP", "chat_id": "-100999", "scope": "devices", "device_ids": ["b"]})
    assert R.matches(d, dev_b, "device") and not R.matches(d, dev_a, "device")
    assert R.clean({"name": "Todos", "chat_id": "-100999", "exclusive": True})["exclusive"] is False
    c = R.clean({"name": "Outro bot", "chat_id": "-100999", "bot": "custom", "token": "123456789:" + "A" * 35})
    pub = R.public(c)
    assert "token_enc" not in pub and pub["has_token"]
    # editar sem digitar o token de novo mantém o que estava
    assert R.clean({"name": "Outro bot", "chat_id": "-100999", "bot": "custom"}, c)["token_enc"] == c["token_enc"]


def test_route_by_avail_map():
    maps = {"P": {"nodes": [{"kind": "device", "device_id": "a"}, {"kind": "submap", "map_id": "S"}], "links": [{"snmp": {"device_id": "z"}}]},
            "S": {"nodes": [{"kind": "device", "device_id": "b"}, {"kind": "submap", "map_id": "P"}], "links": []}}
    assert R.map_devices(["P"], maps) == {"a", "b", "z"}            # desce pelos submapas (sem loop)
    assert R.map_devices(["P"], maps, submaps=False) == {"a", "z"}
    r = {**R.clean({"name": "POP", "chat_id": "-1001234", "scope": "maps", "map_ids": ["P"], "submaps": False}), "enabled": True}
    assert R.matches(r, {"id": "a"}, "device", maps) and R.matches(r, {"id": "z"}, "interface", maps)
    assert not R.matches(r, {"id": "b"}, "device", maps) and not R.matches(r, {"id": "x"}, "device", maps)
    try:
        R.clean({"name": "x", "chat_id": "-1001234", "scope": "maps", "map_ids": []})
        assert False
    except R.RouteError:
        pass
