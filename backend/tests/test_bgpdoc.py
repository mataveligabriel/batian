"""Documentação BGP: validação, prefixos documentados e config padrão por fabricante."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import bgpdoc as B  # noqa: E402

GROUP = B.clean_group({"name": "Grupo Exemplo", "asn": "AS64500", "communities": [
    {"value": "64500:100", "name": "DE-UPSTREAM", "role": "upstream_tag"},
    {"value": "64500:200", "name": "CLIENTE", "role": "downstream_tag", "description": "rotas de clientes"},
    {"value": "64500:666", "name": "RTBH", "role": "blackhole"}]})
GROUP["prefixes"] = [
    B.clean_entry({"prefix": "203.0.113.0/24", "asn": 64500, "communities": ["64500:100"]}, GROUP),
    B.clean_entry({"prefix": "2001:db8::/32", "asn": 64500}, GROUP),
    B.clean_entry({"prefix": "198.51.100.0/24", "asn": "65010", "communities": ["64500:200", "64500:999"], "note": "cliente X"}, GROUP),
]
UP = B.clean_peer({"kind": "upstream", "name": "Operadora X", "remote_asn": 64511, "peer_ipv4": "192.0.2.1", "peer_ipv6": "2001:db8:ffff::1",
                   "max_prefix_v4": 1200000, "md5": True})
DOWN = B.clean_peer({"kind": "downstream", "name": "Cliente X", "remote_asn": 65010, "peer_ipv4": "192.0.2.9", "max_prefix_v4": 50})


def test_validation():
    assert GROUP["asn"] == 64500 and GROUP["prefixes"][2]["unknown"] == ["64500:999"]
    for bad in ("70000:1", "abc", "1:2:3:4"):
        with pytest.raises(B.DocError):
            B.clean_community(bad)
    assert B.clean_community("4200000000:1:2") == "4200000000:1:2"
    with pytest.raises(B.DocError):
        B.clean_peer({"name": "x", "remote_asn": 1})                 # sem IP
    with pytest.raises(B.DocError):
        B.clean_peer({"name": "x", "remote_asn": 1, "peer_ipv4": "2001:db8::1"})
    assert DOWN["export"] == "full" and UP["export"] == "own_customers"


def test_huawei():
    r = B.generate("huawei", GROUP, [UP, DOWN], router_id="10.255.255.1")
    s = r["script"]
    assert "ip ip-prefix PL-GRUPO-EXEMPLO-PROPRIOS index 10 permit 203.0.113.0 24 greater-equal 24 less-equal 24" in s
    assert "ip ipv6-prefix PL-GRUPO-EXEMPLO-PROPRIOS-V6 index 10 permit 2001:db8:: 32 greater-equal 32 less-equal 48" in s
    assert "ip community-filter basic CF-GRUPO-EXEMPLO-CLIENTES permit 64500:200" in s
    assert "route-policy RP-OPERADORA-X-IN deny node 5" in s and " apply community 64500:100 additive" in s
    assert " apply comm-filter CF-GRUPO-EXEMPLO-CLIENTES delete" in s          # upstream não traz community de cliente
    assert "ip ip-prefix PL-CLIENTE-X-IN index 10 permit 198.51.100.0 24" in s and "route-policy RP-CLIENTE-X-IN deny node 1000" in s
    assert " peer 192.0.2.1 password cipher <SENHA>" in s and "  peer 192.0.2.1 route-limit 1200000 90 alert-only" in s
    assert "  peer 2001:db8:ffff::1 route-policy RP-OPERADORA-X-IN-V6 import" in s
    assert "  network 203.0.113.0 24" in s and "ip route-static 203.0.113.0 255.255.255.0 NULL0" in s
    assert any("SENHA" in w for w in r["warnings"])


def test_other_vendors():
    j = B.generate("juniper", GROUP, [UP, DOWN])["script"]
    assert "set policy-options prefix-list PL-CLIENTE-X-IN 198.51.100.0/24" in j
    assert "set protocols bgp group EBGP-OPERADORA-X peer-as 64511" in j
    assert "set policy-options policy-statement RP-OPERADORA-X-OUT term CLIENTES from community CM-GRUPO-EXEMPLO-CLIENTES" in j
    c = B.generate("cisco", GROUP, [UP, DOWN])["script"]
    assert "ip prefix-list PL-GRUPO-EXEMPLO-PROPRIOS seq 5 permit 203.0.113.0/24 le 24" in c
    assert "  neighbor 192.0.2.9 maximum-prefix 50 90 restart 30" in c and " set local-preference 250" in c
    m = B.generate("mikrotik", GROUP, [UP, DOWN])["script"]
    assert 'add chain=RP-OPERADORA-X-OUT rule="if (bgp-communities includes 64500:200) { accept }"' in m
    assert "output.network=PL-GRUPO-EXEMPLO-PROPRIOS" in m and "input.limit-process-routes-ipv4=1200000" in m


def test_markdown():
    md = B.markdown(GROUP, [UP, DOWN], {})
    assert "| `198.51.100.0/24` | AS65010 | `64500:200` `64500:999` |" in md and "## Upstream (trânsito)" in md
