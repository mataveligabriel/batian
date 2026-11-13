"""Documentação BGP: grupos (com ASN, prefixos e o catálogo de communities), pares upstream/downstream e a
configuração padrão gerada a partir disso.

- Grupo: nome (ex.: o grupo/empresa ou o backbone), ASN, tag dos equipamentos, catálogo de communities (valor, nome,
  papel e descrição) e o registro de prefixos: prefixo + ASN de origem + communities — é o "anúncio documentado".
- Par BGP: upstream (trânsito), downstream (cliente), IX/PTT, CDN/cache ou iBGP; ASN, IPs v4/v6, roteador, interface,
  limites de prefixo, communities aplicadas na entrada, local-preference, o que exportar e contatos do NOC.
- Config padrão: prefix-lists dos prefixos do grupo e de cada cliente, community-lists, políticas de entrada/saída e o
  peer, para Huawei (VRP), Juniper (Junos, set), Cisco (IOS-XE) e MikroTik (RouterOS 7). Sai para revisar e aplicar.
  Senha MD5 nunca é guardada: o script traz <SENHA> no lugar.
"""
import ipaddress
import re
from datetime import datetime
from typing import Dict, List, Optional, Tuple

KINDS = {"upstream": "Upstream (trânsito)", "downstream": "Downstream (cliente)", "ix": "IX / PTT", "cdn": "CDN / cache", "ibgp": "iBGP"}
ROLES = {
    "upstream_tag": "Marca rotas recebidas de upstream", "downstream_tag": "Marca rotas de clientes (vão para os upstreams)",
    "ix_tag": "Marca rotas recebidas de IX/PTT", "cdn_tag": "Marca rotas de CDN/cache", "announce": "Anunciar para… (controle de anúncio)",
    "no_announce": "Não anunciar para…", "prepend": "Prepend (AS-path)", "local_pref": "Ajusta local-preference",
    "no_export": "No-export", "blackhole": "Blackhole (RTBH)", "custom": "Outra",
}
EXPORTS = {"own": "Só os prefixos do grupo", "own_customers": "Grupo + clientes (com a community de cliente)",
           "full": "Tabela completa", "default": "Só a rota padrão (0/0)"}
VENDORS = {"huawei": "Huawei (VRP)", "juniper": "Juniper (Junos, set)", "cisco": "Cisco (IOS-XE)", "mikrotik": "MikroTik (RouterOS 7)"}
DEFAULT_LP = {"upstream": 100, "ix": 150, "cdn": 200, "downstream": 250, "ibgp": None}
WELL_KNOWN = {"no-export": "65535:65281", "no-advertise": "65535:65282", "blackhole": "65535:666"}


class DocError(ValueError):
    pass


# ---------------------------------------------------------------- validação
def clean_asn(v, need: bool = False) -> Optional[int]:
    s = str(v if v is not None else "").strip().upper().replace("AS", "")
    if not s:
        if need:
            raise DocError("Informe o ASN")
        return None
    if not s.isdigit() or not 1 <= int(s) <= 4294967294:
        raise DocError(f"ASN inválido: {v}")
    return int(s)


def clean_community(v: str) -> str:
    s = str(v or "").strip().lower()
    if s in WELL_KNOWN:
        return s
    m = re.fullmatch(r"(\d{1,10}):(\d{1,10})(?::(\d{1,10}))?", s)
    if not m:
        raise DocError(f"Community inválida: {v} (use ASN:valor, ASN:x:y ou no-export / blackhole)")
    a, b, c = (int(x) if x is not None else None for x in m.groups())
    if c is None and (a > 65535 or b > 65535):
        raise DocError(f"Community padrão vai até 65535:65535 ({v}); para ASN de 4 bytes use large community (ASN:x:y)")
    if c is not None and max(a, b, c) > 4294967295:
        raise DocError(f"Large community inválida: {v}")
    return s


def clean_prefix(v: str) -> str:
    try:
        n = ipaddress.ip_network(str(v).strip(), strict=False)
    except ValueError:
        raise DocError(f"Prefixo inválido: {v}")
    return str(n)


def clean_ip(v: str, version: int) -> str:
    s = str(v or "").strip()
    if not s:
        return ""
    try:
        ip = ipaddress.ip_address(s.split("/")[0])
    except ValueError:
        raise DocError(f"IP inválido: {v}")
    if ip.version != version:
        raise DocError(f"{v} não é IPv{version}")
    return str(ip)


def _text(v, n: int = 200) -> str:
    return re.sub(r"[\r\n\t]+", " ", str(v or "")).strip()[:n]


def clean_group(b: dict) -> dict:
    name = _text(b.get("name"), 60)
    if not name:
        raise DocError("Dê um nome ao grupo")
    comms, seen = [], set()
    for c in b.get("communities") or []:
        val = clean_community(c.get("value"))
        if val in seen:
            raise DocError(f"Community repetida no grupo: {val}")
        seen.add(val)
        role = c.get("role") if c.get("role") in ROLES else "custom"
        comms.append({"value": val, "name": _text(c.get("name"), 60), "role": role, "description": _text(c.get("description"), 300),
                      "target": _text(c.get("target"), 80)})
    return {"name": name, "asn": clean_asn(b.get("asn")), "tag": _text(b.get("tag"), 40), "description": _text(b.get("description"), 1000),
            "communities": comms[:500]}


def clean_entry(b: dict, group: dict) -> dict:
    """Prefixo documentado no grupo: prefixo + ASN de origem + communities (do catálogo do grupo ou avulsas)."""
    pfx = clean_prefix(b.get("prefix"))
    asn = clean_asn(b.get("asn"), need=True)
    comms = [clean_community(c) for c in (b.get("communities") or []) if str(c).strip()]
    known = {c["value"] for c in group.get("communities") or []}
    return {"prefix": pfx, "asn": asn, "communities": list(dict.fromkeys(comms))[:20], "unknown": [c for c in comms if c not in known],
            "origin": _text(b.get("origin"), 80), "peer_id": _text(b.get("peer_id"), 40), "note": _text(b.get("note"), 300)}


def clean_peer(b: dict) -> dict:
    kind = b.get("kind") if b.get("kind") in KINDS else "upstream"
    name = _text(b.get("name"), 80)
    if not name:
        raise DocError("Dê um nome ao par (operadora, cliente, PTT…)")
    v4, v6 = clean_ip(b.get("peer_ipv4"), 4), clean_ip(b.get("peer_ipv6"), 6)
    if not v4 and not v6:
        raise DocError("Informe o IP do vizinho (IPv4 e/ou IPv6)")

    def lim(v):
        s = str(v if v is not None else "").strip()
        if not s:
            return None
        if not s.isdigit() or int(s) > 10_000_000:
            raise DocError(f"Limite de prefixos inválido: {v}")
        return int(s)
    lp = str(b.get("local_pref") if b.get("local_pref") is not None else "").strip()
    if lp and (not lp.isdigit() or int(lp) > 4294967295):
        raise DocError("Local-preference inválido")
    exp = b.get("export") if b.get("export") in EXPORTS else ("full" if kind == "downstream" else "own_customers")
    return {"kind": kind, "name": name, "remote_asn": clean_asn(b.get("remote_asn"), need=True),
            "peer_ipv4": v4, "peer_ipv6": v6, "local_ipv4": clean_ip(b.get("local_ipv4"), 4), "local_ipv6": clean_ip(b.get("local_ipv6"), 6),
            "device_id": _text(b.get("device_id"), 40), "interface": _text(b.get("interface"), 60), "vlan": _text(b.get("vlan"), 20),
            "bandwidth_mbps": lim(b.get("bandwidth_mbps")), "max_prefix_v4": lim(b.get("max_prefix_v4")), "max_prefix_v6": lim(b.get("max_prefix_v6")),
            "communities_in": [clean_community(c) for c in (b.get("communities_in") or []) if str(c).strip()][:10],
            "local_pref": int(lp) if lp else None, "export": exp, "md5": bool(b.get("md5")), "bfd": bool(b.get("bfd")),
            "multihop": max(0, min(int(b.get("multihop") or 0), 255)),
            "prefixes": [clean_prefix(p) for p in (b.get("prefixes") or []) if str(p).strip()][:2000],
            "circuit": _text(b.get("circuit"), 80), "noc_email": _text(b.get("noc_email"), 120), "noc_phone": _text(b.get("noc_phone"), 40),
            "status": b.get("status") if b.get("status") in ("ativo", "implantando", "desativado") else "ativo",
            "notes": _text(b.get("notes"), 2000)}


# ---------------------------------------------------------------- config
def _nm(*parts) -> str:
    s = "-".join(re.sub(r"[^A-Za-z0-9]+", "-", str(p)).strip("-") for p in parts if p)
    return s.upper()[:40] or "BGP"


def _split(pfx: List[str]) -> Tuple[List[ipaddress.IPv4Network], List[ipaddress.IPv6Network]]:
    v4, v6 = [], []
    for p in dict.fromkeys(pfx):
        n = ipaddress.ip_network(p, strict=False)
        (v4 if n.version == 4 else v6).append(n)
    return v4, v6


def _upto(n) -> int:
    return max(n.prefixlen, 24) if n.version == 4 else max(n.prefixlen, 48)


def _role(group: dict, role: str) -> List[str]:
    return [c["value"] for c in group.get("communities") or [] if c.get("role") == role]


def _comm_val(v: str) -> str:
    return WELL_KNOWN.get(v, v)


def own_prefixes(group: dict, local_asn: int) -> List[str]:
    return [e["prefix"] for e in group.get("prefixes") or [] if e.get("asn") == local_asn]


def customer_prefixes(group: dict, peer: dict) -> List[str]:
    return list(dict.fromkeys([e["prefix"] for e in group.get("prefixes") or [] if e.get("asn") == peer["remote_asn"]] + (peer.get("prefixes") or [])))


def _plan(group: dict, peer: dict, local_asn: int) -> dict:
    """O que a política de cada par faz, independente do fabricante."""
    kind = peer["kind"]
    tag_in = peer.get("communities_in") or _role(group, {"upstream": "upstream_tag", "downstream": "downstream_tag", "ix": "ix_tag",
                                                          "cdn": "cdn_tag"}.get(kind, "")) if kind != "ibgp" else []
    lp = peer.get("local_pref") if peer.get("local_pref") is not None else DEFAULT_LP.get(kind)
    p = {"kind": kind, "name": _nm(peer["name"]), "tag_in": tag_in, "lp": lp, "in_only": None, "deny_own_in": kind in ("upstream", "ix", "cdn"),
         "export": peer.get("export"), "cust_comm": _role(group, "downstream_tag")}
    if kind == "downstream":
        p["in_only"] = customer_prefixes(group, peer)
    return p


def generate(vendor: str, group: dict, peers: List[dict], local_asn: Optional[int] = None, router_id: str = "",
             base: bool = True) -> dict:
    if vendor not in VENDORS:
        raise DocError("Fabricante sem gerador: " + ", ".join(VENDORS.values()))
    asn = local_asn or group.get("asn")
    if not asn:
        raise DocError("Informe o ASN local (no grupo ou na geração)")
    if router_id:
        try:
            ipaddress.IPv4Address(router_id)
        except ValueError:
            raise DocError("Router-ID precisa ser um IPv4")
    own = own_prefixes(group, asn)
    warns = []
    if not own:
        warns.append(f"Nenhum prefixo do AS{asn} documentado no grupo: o filtro de saída para upstream fica vazio (não anuncia nada).")
    if not _role(group, "downstream_tag") and any(p["kind"] == "downstream" for p in peers):
        warns.append("O grupo não tem community com o papel 'Marca rotas de clientes': rotas de cliente não serão reconhecidas na saída para os upstreams.")
    for p in peers:
        if p["kind"] == "downstream" and not customer_prefixes(group, p):
            warns.append(f"{p['name']}: nenhum prefixo do AS{p['remote_asn']} documentado — a entrada desse cliente vai negar tudo.")
        if p.get("md5"):
            warns.append(f"{p['name']}: troque <SENHA> pela senha MD5 combinada (o BastiON não guarda a senha).")
    gname = _nm(group.get("name"))
    fn = {"huawei": _huawei, "juniper": _juniper, "cisco": _cisco, "mikrotik": _mikrotik}[vendor]
    lines = fn(gname, asn, router_id, own, group, peers, base)
    head = "#" if vendor in ("huawei", "mikrotik") else "#" if vendor == "juniper" else "!"
    banner = [f"{head} BastiON — BGP padrão · grupo {group.get('name')} · AS{asn} · {VENDORS[vendor]} · {datetime.now():%Y-%m-%d %H:%M}",
              f"{head} Revise antes de aplicar. Pares: " + (", ".join(f"{p['name']} (AS{p['remote_asn']}, {KINDS[p['kind']]})" for p in peers) or "nenhum")]
    return {"script": "\n".join(banner + lines) + "\n", "warnings": warns}


def _huawei(g, asn, rid, own, group, peers, base) -> List[str]:
    out = []
    v4, v6 = _split(own)

    def ipl(name, nets4, nets6):
        r = []
        for i, n in enumerate(nets4, 1):
            r.append(f"ip ip-prefix {name} index {i * 10} permit {n.network_address} {n.prefixlen} greater-equal {n.prefixlen} less-equal {_upto(n)}")
        for i, n in enumerate(nets6, 1):
            r.append(f"ip ipv6-prefix {name}-V6 index {i * 10} permit {n.network_address} {n.prefixlen} greater-equal {n.prefixlen} less-equal {_upto(n)}")
        return r
    out += ["#", f"# prefixos do grupo (AS{asn})"] + ipl(f"PL-{g}-PROPRIOS", v4, v6)
    cust = _role(group, "downstream_tag")
    if cust:
        out += ["#"] + [f"ip community-filter basic CF-{g}-CLIENTES permit {_comm_val(c)}" for c in cust]
    if base:
        out += ["#", "# âncoras dos anúncios (rota nula do agregado)"]
        out += [f"ip route-static {n.network_address} {n.netmask} NULL0" for n in v4]
        out += [f"ipv6 route-static {n.network_address} {n.prefixlen} NULL0" for n in v6]
    peer_lines = []
    for p in peers:
        pl = _plan(group, p, asn)
        n = pl["name"]
        for fam, ip in (("v4", p.get("peer_ipv4")), ("v6", p.get("peer_ipv6"))):
            if not ip:
                continue
            sfx = "" if fam == "v4" else "-V6"
            match_own = f"if-match ip-prefix PL-{g}-PROPRIOS" if fam == "v4" else f"if-match ipv6 address prefix-list PL-{g}-PROPRIOS-V6"
            if p["kind"] != "ibgp":
                out += ["#", f"# {KINDS[p['kind']]}: {p['name']} AS{p['remote_asn']} ({fam})"]
                rin = f"RP-{n}-IN{sfx}"
                if pl["in_only"] is not None:
                    c4, c6 = _split(pl["in_only"])
                    out += ipl(f"PL-{n}-IN", c4 if fam == "v4" else [], c6 if fam == "v6" else [])
                    mm = f"if-match ip-prefix PL-{n}-IN" if fam == "v4" else f"if-match ipv6 address prefix-list PL-{n}-IN-V6"
                    out += [f"route-policy {rin} permit node 10", f" {mm}"]
                else:
                    if pl["deny_own_in"] and (v4 if fam == "v4" else v6):
                        out += [f"route-policy {rin} deny node 5", f" {match_own}"]
                    out += [f"route-policy {rin} permit node 10"]
                if pl["lp"] is not None:
                    out.append(f" apply local-preference {pl['lp']}")
                if cust and p["kind"] != "downstream":       # ninguém de fora pode trazer a community de cliente (evita vazamento)
                    out.append(f" apply comm-filter CF-{g}-CLIENTES delete")
                if pl["tag_in"]:
                    out.append(f" apply community {' '.join(_comm_val(c) for c in pl['tag_in'])} additive")
                if pl["in_only"] is not None:
                    out.append(f"route-policy {rin} deny node 1000")
                rout = f"RP-{n}-OUT{sfx}"
                exp = pl["export"]
                if exp in ("own", "own_customers"):
                    if v4 if fam == "v4" else v6:
                        out += [f"route-policy {rout} permit node 10", f" {match_own}"]
                    if exp == "own_customers" and cust:
                        out += [f"route-policy {rout} permit node 20", f" if-match community-filter CF-{g}-CLIENTES"]
                    out.append(f"route-policy {rout} deny node 1000")
                elif exp == "default":
                    dflt = "0.0.0.0 0" if fam == "v4" else ":: 0"
                    out += [f"ip {'ip-prefix' if fam == 'v4' else 'ipv6-prefix'} PL-DEFAULT{sfx} index 10 permit {dflt}",
                            f"route-policy {rout} permit node 10",
                            f" if-match {'ip-prefix' if fam == 'v4' else 'ipv6 address prefix-list'} PL-DEFAULT{sfx}", f"route-policy {rout} deny node 1000"]
                else:
                    out += [f"route-policy {rout} permit node 10"]
            peer_lines.append((p, fam, ip, pl))
    out += ["#", f"bgp {asn}"]
    if rid:
        out.append(f" router-id {rid}")
    for p, fam, ip, pl in peer_lines:
        out += [f" peer {ip} as-number {p['remote_asn']}", f" peer {ip} description {p['name'][:60]}"]
        if p.get("md5"):
            out.append(f" peer {ip} password cipher <SENHA>")
        if p.get("multihop"):
            out.append(f" peer {ip} ebgp-max-hop {p['multihop']}")
        if p.get("bfd"):
            out.append(f" peer {ip} bfd enable")
        if p["kind"] == "ibgp":
            out.append(f" peer {ip} connect-interface LoopBack0")
    for fam in ("v4", "v6"):
        fps = [x for x in peer_lines if x[1] == fam]
        nets = v4 if fam == "v4" else v6
        if not fps and not (base and nets):
            continue
        out.append(" ipv4-family unicast" if fam == "v4" else " ipv6-family unicast")
        if base:
            out += [f"  network {n.network_address} {n.prefixlen}" for n in nets]
        for p, _, ip, pl in fps:
            sfx = "" if fam == "v4" else "-V6"
            out.append(f"  peer {ip} enable")
            if p["kind"] != "ibgp":
                out += [f"  peer {ip} route-policy RP-{pl['name']}-IN{sfx} import", f"  peer {ip} route-policy RP-{pl['name']}-OUT{sfx} export"]
            else:
                out.append(f"  peer {ip} next-hop-local")
            out.append(f"  peer {ip} advertise-community")
            mx = p.get("max_prefix_v4" if fam == "v4" else "max_prefix_v6")
            if mx:
                out.append(f"  peer {ip} route-limit {mx} 90 alert-only" if p["kind"] in ("upstream", "ix", "cdn") else f"  peer {ip} route-limit {mx} 90 idle-timeout 30")
    out += [" quit", "quit", "#"]
    return out


def _juniper(g, asn, rid, own, group, peers, base) -> List[str]:
    out = []
    v4, v6 = _split(own)
    out.append(f"set routing-options autonomous-system {asn}")
    if rid:
        out.append(f"set routing-options router-id {rid}")
    for n in v4:
        out.append(f"set policy-options prefix-list PL-{g}-PROPRIOS {n}")
        if base:
            out.append(f"set routing-options static route {n} discard")
    for n in v6:
        out.append(f"set policy-options prefix-list PL-{g}-PROPRIOS-V6 {n}")
        if base:
            out.append(f"set routing-options rib inet6.0 static route {n} discard")
    cust = _role(group, "downstream_tag")
    if cust:
        out.append(f"set policy-options community CM-{g}-CLIENTES members [ {' '.join(_comm_val(c) for c in cust)} ]")
    for p in peers:
        pl = _plan(group, p, asn)
        n = pl["name"]
        if p["kind"] == "ibgp":
            for fam, ip in (("v4", p.get("peer_ipv4")), ("v6", p.get("peer_ipv6"))):
                if ip:
                    out += [f"set protocols bgp group IBGP type internal", f"set protocols bgp group IBGP neighbor {ip} description \"{p['name'][:60]}\""]
            continue
        if pl["tag_in"]:
            out.append(f"set policy-options community CM-{n}-IN members [ {' '.join(_comm_val(c) for c in pl['tag_in'])} ]")
        for fam, ip in (("v4", p.get("peer_ipv4")), ("v6", p.get("peer_ipv6"))):
            if not ip:
                continue
            sfx = "" if fam == "v4" else "-V6"
            own_pl = f"PL-{g}-PROPRIOS{sfx}"
            pin, pout = f"RP-{n}-IN{sfx}", f"RP-{n}-OUT{sfx}"
            then_in = []
            if pl["lp"] is not None:
                then_in.append(f"local-preference {pl['lp']}")
            if cust and p["kind"] != "downstream":
                then_in.append(f"community delete CM-{g}-CLIENTES")
            if pl["tag_in"]:
                then_in.append(f"community add CM-{n}-IN")
            if pl["in_only"] is not None:
                c4, c6 = _split(pl["in_only"])
                for c in (c4 if fam == "v4" else c6):
                    out.append(f"set policy-options prefix-list PL-{n}-IN{sfx} {c}")
                out.append(f"set policy-options policy-statement {pin} term CLIENTE from prefix-list-filter PL-{n}-IN{sfx} orlonger")
                out += [f"set policy-options policy-statement {pin} term CLIENTE then {t}" for t in then_in]
                out += [f"set policy-options policy-statement {pin} term CLIENTE then accept", f"set policy-options policy-statement {pin} term NEGA then reject"]
            else:
                if pl["deny_own_in"] and (v4 if fam == "v4" else v6):
                    out.append(f"set policy-options policy-statement {pin} term NAO-PROPRIOS from prefix-list-filter {own_pl} orlonger")
                    out.append(f"set policy-options policy-statement {pin} term NAO-PROPRIOS then reject")
                out += [f"set policy-options policy-statement {pin} term ACEITA then {t}" for t in then_in]
                out.append(f"set policy-options policy-statement {pin} term ACEITA then accept")
            exp = pl["export"]
            if exp in ("own", "own_customers"):
                if v4 if fam == "v4" else v6:
                    out += [f"set policy-options policy-statement {pout} term PROPRIOS from prefix-list-filter {own_pl} orlonger",
                            f"set policy-options policy-statement {pout} term PROPRIOS then accept"]
                if exp == "own_customers" and cust:
                    out += [f"set policy-options policy-statement {pout} term CLIENTES from community CM-{g}-CLIENTES",
                            f"set policy-options policy-statement {pout} term CLIENTES then accept"]
                out.append(f"set policy-options policy-statement {pout} term NEGA then reject")
            elif exp == "default":
                d = "0.0.0.0/0" if fam == "v4" else "::/0"
                out += [f"set policy-options policy-statement {pout} term DEFAULT from route-filter {d} exact",
                        f"set policy-options policy-statement {pout} term DEFAULT then accept", f"set policy-options policy-statement {pout} term NEGA then reject"]
            else:
                out.append(f"set policy-options policy-statement {pout} term TUDO then accept")
            grp = f"{'EBGP'}-{n}{sfx}"
            out += [f"set protocols bgp group {grp} type external", f"set protocols bgp group {grp} peer-as {p['remote_asn']}",
                    f"set protocols bgp group {grp} neighbor {ip} description \"{p['name'][:60]}\"",
                    f"set protocols bgp group {grp} import {pin}", f"set protocols bgp group {grp} export {pout}"]
            if p.get("md5"):
                out.append(f"set protocols bgp group {grp} authentication-key \"<SENHA>\"")
            if p.get("multihop"):
                out.append(f"set protocols bgp group {grp} multihop ttl {p['multihop']}")
            if p.get("bfd"):
                out.append(f"set protocols bgp group {grp} bfd-liveness-detection minimum-interval 300")
            mx = p.get("max_prefix_v4" if fam == "v4" else "max_prefix_v6")
            fam_kw = "inet" if fam == "v4" else "inet6"
            if mx:
                out.append(f"set protocols bgp group {grp} family {fam_kw} unicast prefix-limit maximum {mx}")
                if p["kind"] == "downstream":
                    out.append(f"set protocols bgp group {grp} family {fam_kw} unicast prefix-limit teardown 90 idle-timeout 30")
            else:
                out.append(f"set protocols bgp group {grp} family {fam_kw} unicast")
    return out


def _cisco(g, asn, rid, own, group, peers, base) -> List[str]:
    out = []
    v4, v6 = _split(own)
    for i, n in enumerate(v4, 1):
        out.append(f"ip prefix-list PL-{g}-PROPRIOS seq {i * 5} permit {n} le {_upto(n)}")
    for i, n in enumerate(v6, 1):
        out.append(f"ipv6 prefix-list PL-{g}-PROPRIOS-V6 seq {i * 5} permit {n} le {_upto(n)}")
    if base:
        out += [f"ip route {n.network_address} {n.netmask} Null0" for n in v4] + [f"ipv6 route {n} Null0" for n in v6]
    cust = _role(group, "downstream_tag")
    if cust:
        out.append(f"ip community-list standard CL-{g}-CLIENTES permit {' '.join(_comm_val(c) for c in cust)}")
    nb = []
    for p in peers:
        pl = _plan(group, p, asn)
        n = pl["name"]
        for fam, ip in (("v4", p.get("peer_ipv4")), ("v6", p.get("peer_ipv6"))):
            if not ip:
                continue
            nb.append((p, fam, ip, pl))
            if p["kind"] == "ibgp":
                continue
            sfx = "" if fam == "v4" else "-V6"
            match_own = f"match ip address prefix-list PL-{g}-PROPRIOS" if fam == "v4" else f"match ipv6 address prefix-list PL-{g}-PROPRIOS-V6"
            rin, rout = f"RM-{n}-IN{sfx}", f"RM-{n}-OUT{sfx}"
            out.append("!")
            if pl["in_only"] is not None:
                c4, c6 = _split(pl["in_only"])
                for i, c in enumerate(c4 if fam == "v4" else c6, 1):
                    out.append(f"{'ip' if fam == 'v4' else 'ipv6'} prefix-list PL-{n}-IN{sfx} seq {i * 5} permit {c} le {_upto(c)}")
                out += [f"route-map {rin} permit 10",
                        f" match {'ip address' if fam == 'v4' else 'ipv6 address'} prefix-list PL-{n}-IN{sfx}"]
            else:
                if pl["deny_own_in"] and (v4 if fam == "v4" else v6):
                    out += [f"route-map {rin} deny 5", f" {match_own}"]
                out.append(f"route-map {rin} permit 10")
            if pl["lp"] is not None:
                out.append(f" set local-preference {pl['lp']}")
            if cust and p["kind"] != "downstream":
                out.append(f" set comm-list CL-{g}-CLIENTES delete")
            if pl["tag_in"]:
                out.append(f" set community {' '.join(_comm_val(c) for c in pl['tag_in'])} additive")
            exp = pl["export"]
            if exp in ("own", "own_customers"):
                if v4 if fam == "v4" else v6:
                    out += [f"route-map {rout} permit 10", f" {match_own}"]
                if exp == "own_customers" and cust:
                    out += [f"route-map {rout} permit 20", f" match community CL-{g}-CLIENTES"]
            elif exp == "default":
                out += [f"{'ip' if fam == 'v4' else 'ipv6'} prefix-list PL-DEFAULT{sfx} seq 5 permit {'0.0.0.0/0' if fam == 'v4' else '::/0'}",
                        f"route-map {rout} permit 10", f" match {'ip address' if fam == 'v4' else 'ipv6 address'} prefix-list PL-DEFAULT{sfx}"]
            else:
                out.append(f"route-map {rout} permit 10")
    out += ["!", f"router bgp {asn}"]
    if rid:
        out.append(f" bgp router-id {rid}")
    out.append(" bgp log-neighbor-changes")
    for p, fam, ip, pl in nb:
        out += [f" neighbor {ip} remote-as {p['remote_asn']}", f" neighbor {ip} description {p['name'][:60]}"]
        if p.get("md5"):
            out.append(f" neighbor {ip} password <SENHA>")
        if p.get("multihop"):
            out.append(f" neighbor {ip} ebgp-multihop {p['multihop']}")
        if p.get("bfd"):
            out.append(f" neighbor {ip} fall-over bfd")
        if p["kind"] == "ibgp":
            out.append(f" neighbor {ip} update-source Loopback0")
    for fam in ("v4", "v6"):
        fps = [x for x in nb if x[1] == fam]
        nets = v4 if fam == "v4" else v6
        if not fps and not (base and nets):
            continue
        out.append(f" address-family {'ipv4' if fam == 'v4' else 'ipv6'} unicast")
        if base:
            out += [f"  network {n.network_address} mask {n.netmask}" if fam == "v4" else f"  network {n}" for n in nets]
        for p, _, ip, pl in fps:
            sfx = "" if fam == "v4" else "-V6"
            out += [f"  neighbor {ip} activate", f"  neighbor {ip} send-community both"]
            if p["kind"] != "ibgp":
                out += [f"  neighbor {ip} route-map RM-{pl['name']}-IN{sfx} in", f"  neighbor {ip} route-map RM-{pl['name']}-OUT{sfx} out"]
            else:
                out.append(f"  neighbor {ip} next-hop-self")
            mx = p.get("max_prefix_v4" if fam == "v4" else "max_prefix_v6")
            if mx:
                out.append(f"  neighbor {ip} maximum-prefix {mx} 90 warning-only" if p["kind"] in ("upstream", "ix", "cdn") else f"  neighbor {ip} maximum-prefix {mx} 90 restart 30")
        out.append(" exit-address-family")
    out.append("!")
    return out


def _mikrotik(g, asn, rid, own, group, peers, base) -> List[str]:
    out = []
    v4, v6 = _split(own)
    if v4:
        out.append("/ip firewall address-list")
        out += [f"add list=PL-{g}-PROPRIOS address={n}" for n in v4]
    if v6:
        out.append("/ipv6 firewall address-list")
        out += [f"add list=PL-{g}-PROPRIOS-V6 address={n}" for n in v6]
    if base:
        if v4:
            out.append("/ip route")
            out += [f"add dst-address={n} blackhole comment=\"anuncio BGP {g}\"" for n in v4]
        if v6:
            out.append("/ipv6 route")
            out += [f"add dst-address={n} blackhole comment=\"anuncio BGP {g}\"" for n in v6]
    cust = _role(group, "downstream_tag")
    out.append("/routing filter rule")

    def own_cond(nets):
        return " || ".join(f"(dst in {n} && dst-len in {n.prefixlen}-{_upto(n)})" for n in nets)
    conns = []
    for p in peers:
        pl = _plan(group, p, asn)
        n = pl["name"]
        for fam, ip in (("v4", p.get("peer_ipv4")), ("v6", p.get("peer_ipv6"))):
            if not ip:
                continue
            conns.append((p, fam, ip, pl))
            if p["kind"] == "ibgp":
                continue
            sfx = "" if fam == "v4" else "-V6"
            nets = v4 if fam == "v4" else v6
            cin, cout = f"RP-{n}-IN{sfx}", f"RP-{n}-OUT{sfx}"
            acts = []
            if pl["lp"] is not None:
                acts.append(f"set bgp-local-pref {pl['lp']}")
            acts += [f"append bgp-communities {_comm_val(c)}" for c in pl["tag_in"]]
            acts.append("accept")
            body = "; ".join(acts)
            if pl["in_only"] is not None:
                c4, c6 = _split(pl["in_only"])
                cl = c4 if fam == "v4" else c6
                if cl:
                    out.append(f"add chain={cin} rule=\"if ({own_cond(cl)}) {{ {body} }}\"")
                out.append(f"add chain={cin} rule=\"reject\"")
            else:
                if pl["deny_own_in"] and nets:
                    out.append(f"add chain={cin} rule=\"if ({own_cond(nets)}) {{ reject }}\"")
                out.append(f"add chain={cin} rule=\"{body}\"")
            exp = pl["export"]
            if exp in ("own", "own_customers"):
                if nets:
                    out.append(f"add chain={cout} rule=\"if ({own_cond(nets)}) {{ accept }}\"")
                if exp == "own_customers":
                    out += [f"add chain={cout} rule=\"if (bgp-communities includes {_comm_val(c)}) {{ accept }}\"" for c in cust]
                out.append(f"add chain={cout} rule=\"reject\"")
            elif exp == "default":
                d = "0.0.0.0/0" if fam == "v4" else "::/0"
                out += [f"add chain={cout} rule=\"if (dst == {d}) {{ accept }}\"", f"add chain={cout} rule=\"reject\""]
            else:
                out.append(f"add chain={cout} rule=\"accept\"")
    if rid or base:
        out += ["/routing bgp template", f"set default as={asn}" + (f" router-id={rid}" if rid else "")]
    out.append("/routing bgp connection")
    for p, fam, ip, pl in conns:
        sfx = "" if fam == "v4" else "-V6"
        parts = [f"add name={pl['name']}{sfx} remote.address={ip} remote.as={p['remote_asn']} as={asn}",
                 f"local.role={'ibgp' if p['kind'] == 'ibgp' else 'ebgp'}", f"comment=\"{p['name'][:60]}\""]
        if p["kind"] != "ibgp":
            parts += [f"input.filter=RP-{pl['name']}-IN{sfx}", f"output.filter-chain=RP-{pl['name']}-OUT{sfx}"]
            if pl["export"] in ("own", "own_customers") and (v4 if fam == "v4" else v6):
                parts.append(f"output.network=PL-{g}-PROPRIOS{sfx}")
        if p.get("md5"):
            parts.append("tcp-md5-key=\"<SENHA>\"")
        if p.get("multihop"):
            parts.append("multihop=yes")
        mx = p.get("max_prefix_v4" if fam == "v4" else "max_prefix_v6")
        if mx:
            parts.append(f"input.limit-process-routes-{'ipv4' if fam == 'v4' else 'ipv6'}={mx}")
        out.append(" ".join(parts))
    return out


# ---------------------------------------------------------------- documentação
def markdown(group: dict, peers: List[dict], dev_names: Dict[str, str]) -> str:
    L = [f"# BGP — {group['name']}" + (f" (AS{group['asn']})" if group.get("asn") else ""), ""]
    if group.get("description"):
        L += [group["description"], ""]
    L += ["## Communities", "", "| Community | Nome | Papel | Alvo | Descrição |", "|---|---|---|---|---|"]
    L += [f"| `{c['value']}` | {c['name']} | {ROLES.get(c['role'], c['role'])} | {c.get('target') or ''} | {c['description']} |" for c in group.get("communities") or []] or ["| — | | | | |"]
    L += ["", "## Prefixos documentados", "", "| Prefixo | ASN de origem | Communities | Origem | Obs. |", "|---|---|---|---|---|"]
    L += [f"| `{e['prefix']}` | AS{e['asn']} | {' '.join(f'`{c}`' for c in e.get('communities') or [])} | {e.get('origin') or ''} | {e.get('note') or ''} |"
          for e in sorted(group.get("prefixes") or [], key=lambda e: (e['asn'], ipaddress.ip_network(e['prefix']).version, ipaddress.ip_network(e['prefix'])))] or ["| — | | | | |"]
    for kind in KINDS:
        ps = [p for p in peers if p["kind"] == kind]
        if not ps:
            continue
        L += ["", f"## {KINDS[kind]}", "", "| Nome | ASN | IPv4 | IPv6 | Roteador | Interface | Banda | Máx. prefixos | Local-pref | Exporta | Contato | Situação |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for p in ps:
            L.append(f"| {p['name']} | AS{p['remote_asn']} | {p.get('peer_ipv4') or ''} | {p.get('peer_ipv6') or ''} | {dev_names.get(p.get('device_id'), '')} | "
                     f"{p.get('interface') or ''}{(' vlan ' + p['vlan']) if p.get('vlan') else ''} | {(str(p['bandwidth_mbps']) + ' Mbps') if p.get('bandwidth_mbps') else ''} | "
                     f"{p.get('max_prefix_v4') or ''}{(' / ' + str(p['max_prefix_v6'])) if p.get('max_prefix_v6') else ''} | {p.get('local_pref') or DEFAULT_LP.get(kind) or ''} | "
                     f"{EXPORTS.get(p.get('export'), '')} | {' '.join(x for x in (p.get('noc_email'), p.get('noc_phone'), p.get('circuit')) if x)} | {p.get('status')} |")
    return "\n".join(L) + "\n"


__all__ = ["KINDS", "ROLES", "EXPORTS", "VENDORS", "clean_group", "clean_entry", "clean_peer", "generate", "markdown", "DocError"]
