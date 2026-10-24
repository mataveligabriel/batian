"""Inventário de IPs (IPAM) a partir dos backups de configuração + prefixos cadastrados + sincronização com NetBox.

Os endereços de interface saem da configuração de cada equipamento (Huawei, Cisco/ZTE/Datacom, Juniper display
set, MikroTik export), com interface, descrição e VRF. Os prefixos são cadastrados (ou importados das redes vistas)
e mostram uso, IPs ocupados, blocos livres e IP duplicado.
"""
import ipaddress
import re
from typing import Dict, Iterable, List, Optional

_MASK = r"(\d{1,3}(?:\.\d{1,3}){3})"


def _net(ip: str, plen: int):
    try:
        return ipaddress.ip_interface(f"{ip}/{plen}")
    except ValueError:
        return None


def _mask_len(mask: str) -> Optional[int]:
    try:
        return ipaddress.IPv4Network(f"0.0.0.0/{mask}").prefixlen
    except ValueError:
        return None


def parse_addresses(device_type: str, cfg: str) -> List[dict]:
    """-> [{ip, plen, interface, description, vrf, secondary}]"""
    t = cfg or ""
    out: List[dict] = []
    if device_type == "juniper" or re.search(r"^set interfaces ", t, re.M):
        vrf_of = {}
        for m in re.finditer(r"^set routing-instances (\S+) interface (\S+)", t, re.M):
            vrf_of[m.group(2)] = m.group(1)
        desc = {}
        for m in re.finditer(r"^set interfaces (\S+)(?: unit (\d+))? description \"?([^\"\n]+)\"?", t, re.M):
            desc[f"{m.group(1)}.{m.group(2)}" if m.group(2) else m.group(1)] = m.group(3).strip()
        for m in re.finditer(r"^set interfaces (\S+) unit (\d+) family inet6? address ([0-9a-fA-F.:]+)/(\d+)", t, re.M):
            ifn = f"{m.group(1)}.{m.group(2)}"
            out.append({"ip": m.group(3), "plen": int(m.group(4)), "interface": ifn,
                        "description": desc.get(ifn) or desc.get(m.group(1), ""), "vrf": vrf_of.get(ifn, ""), "secondary": False})
        return _valid(out)
    if device_type == "mikrotik" or "/ip address" in t:
        sec = re.search(r"^/ip address\s*\n(.*?)(?=^/|\Z)", t, re.M | re.S)
        sec6 = re.search(r"^/ipv6 address\s*\n(.*?)(?=^/|\Z)", t, re.M | re.S)
        for body in [x.group(1) for x in (sec, sec6) if x]:
            body = re.sub(r"\\\s*\n\s*", " ", body)
            for ln in body.splitlines():
                m = re.search(r"\baddress=([0-9a-fA-F.:]+)/(\d+)", ln)
                if not m or not ln.strip().startswith("add"):
                    continue
                ifm = re.search(r"\binterface=(\"[^\"]+\"|\S+)", ln)
                cm = re.search(r"\bcomment=(\"[^\"]*\"|\S+)", ln)
                out.append({"ip": m.group(1), "plen": int(m.group(2)), "interface": (ifm.group(1).strip('"') if ifm else ""),
                            "description": (cm.group(1).strip('"') if cm else ""), "vrf": "", "secondary": "disabled=yes" in ln})
        return _valid([o for o in out if not o["secondary"]] + [])
    # estilo IOS / VRP / ZTE / Datacom: blocos "interface X"
    cur, desc, vrf = None, "", ""
    for ln in t.splitlines():
        m = re.match(r"^\s*interface\s+(.+?)\s*$", ln)
        if m and (not ln.startswith(" ") or ln.startswith("interface")):
            cur, desc, vrf = m.group(1).strip(), "", ""
            continue
        if cur is None:
            continue
        if re.match(r"^\S", ln) and not ln.startswith("interface"):
            cur = None
            continue
        s = ln.strip()
        if s == "!" or s == "#":
            cur = None
            continue
        m = re.match(r"^description\s+(.+)$", s)
        if m:
            desc = m.group(1).strip()[:120]
            continue
        m = re.match(r"^(?:ip\s+binding\s+vpn-instance|vrf\s+forwarding|ip\s+vrf\s+forwarding|vrf\s+member|vrf)\s+(\S+)", s)
        if m:
            vrf = m.group(1)
            continue
        m = re.match(r"^ip\s+address\s+" + _MASK + r"\s+" + _MASK + r"(\s+(sub|secondary))?", s)
        if m:
            pl = _mask_len(m.group(2))
            if pl is not None:
                out.append({"ip": m.group(1), "plen": pl, "interface": cur, "description": desc, "vrf": vrf, "secondary": bool(m.group(3))})
            continue
        m = re.match(r"^(?:ip\s+address|ipv4\s+address|ipv6\s+address|address)\s+([0-9a-fA-F.:]+)/(\d+)(\s+(sub|secondary))?", s)
        if m:
            out.append({"ip": m.group(1), "plen": int(m.group(2)), "interface": cur, "description": desc, "vrf": vrf, "secondary": bool(m.group(3))})
    return _valid(out)


def _valid(rows: List[dict]) -> List[dict]:
    out = []
    for r in rows:
        i = _net(r["ip"], r["plen"])
        if not i or i.ip.is_loopback and str(i.ip).startswith("127.") or i.ip.is_unspecified or i.ip.is_link_local:
            continue
        r["ip"] = str(i.ip)
        r["network"] = str(i.network)
        r["version"] = i.version
        out.append(r)
    return out


def clean_prefix(p: str) -> str:
    try:
        return str(ipaddress.ip_network(p.strip(), strict=False))
    except ValueError:
        raise ValueError(f"Prefixo inválido: {p}")


def usage(prefix: str, ips: Iterable[str]) -> dict:
    net = ipaddress.ip_network(prefix)
    used = {ipaddress.ip_address(i) for i in ips}
    used = {i for i in used if i in net}
    size = net.num_addresses
    usable = size - 2 if net.version == 4 and net.prefixlen <= 30 else size
    return {"size": size, "usable": usable, "used": len(used), "pct": round(100.0 * len(used) / usable, 1) if usable else 0.0}


def children(prefix: str, all_prefixes: List[str]) -> List[str]:
    net = ipaddress.ip_network(prefix)
    sub = [ipaddress.ip_network(p) for p in all_prefixes if p != prefix and ipaddress.ip_network(p).version == net.version]
    sub = [s for s in sub if s.subnet_of(net)]
    # só os filhos diretos
    return [str(s) for s in sub if not any(s != o and s.subnet_of(o) for o in sub)]


def free_blocks(prefix: str, used_nets: List[str], want_len: Optional[int] = None, limit: int = 20) -> List[str]:
    """Blocos livres dentro do prefixo, descontando sub-prefixos e redes de interface. want_len: só desse tamanho."""
    net = ipaddress.ip_network(prefix)
    taken = sorted({ipaddress.ip_network(u, strict=False) for u in used_nets
                    if ipaddress.ip_network(u, strict=False).version == net.version and ipaddress.ip_network(u, strict=False).overlaps(net)})
    free = [net]
    for t in taken:
        nxt = []
        for f in free:
            if t.subnet_of(f) and t != f:
                nxt += list(f.address_exclude(t))
            elif f.subnet_of(t):
                continue
            else:
                nxt.append(f)
        free = nxt
    free.sort()
    if want_len:
        out = []
        for f in free:
            if f.prefixlen <= want_len:
                for s in f.subnets(new_prefix=want_len):
                    out.append(str(s))
                    if len(out) >= limit:
                        return out
        return out
    return [str(f) for f in free[:limit]]


def duplicates(rows: List[dict]) -> Dict[str, list]:
    """Mesmo IP em dois equipamentos (na mesma VRF)."""
    seen: Dict[tuple, list] = {}
    for r in rows:
        seen.setdefault((r["ip"], r.get("vrf") or ""), []).append(r)
    return {f"{k[0]}{' @' + k[1] if k[1] else ''}": v for k, v in seen.items() if len({x["device_id"] for x in v}) > 1}


def uncovered_networks(rows: List[dict], prefixes: List[str]) -> List[dict]:
    """Redes de interface que não estão dentro de nenhum prefixo cadastrado (candidatas a importar)."""
    nets = [ipaddress.ip_network(p) for p in prefixes]
    out: Dict[str, dict] = {}
    for r in rows:
        n = ipaddress.ip_network(r["network"])
        if n.prefixlen == n.max_prefixlen:
            continue
        if any(n.version == p.version and n.subnet_of(p) for p in nets):
            continue
        o = out.setdefault(str(n), {"network": str(n), "devices": set(), "vrf": r.get("vrf") or ""})
        o["devices"].add(r["device_name"])
    return [{**v, "devices": sorted(v["devices"])} for v in sorted(out.values(), key=lambda x: ipaddress.ip_network(x["network"]))]


# ---------------------------------------------------------------- NetBox
async def netbox_all(client, url: str, path: str, params: Optional[dict] = None) -> List[dict]:
    out, nxt = [], f"{url}/api/{path}/"
    q = {"limit": 1000, **(params or {})}
    while nxt and len(out) < 200000:
        r = await client.get(nxt, params=q)
        r.raise_for_status()
        j = r.json()
        out += j.get("results") or []
        nxt, q = j.get("next"), None
    return out


def netbox_plan(local_prefixes: List[dict], local_ips: List[dict], nb_prefixes: List[dict], nb_ips: List[dict]) -> dict:
    """O que falta criar no NetBox (por prefixo/endereço; VRF pelo nome quando existir lá) e o que só existe lá."""
    have_p = {(p.get("prefix"), ((p.get("vrf") or {}).get("name") or "")) for p in nb_prefixes}
    have_i = {((a.get("address") or "").split("/")[0], ((a.get("vrf") or {}).get("name") or "")) for a in nb_ips}
    new_p = [p for p in local_prefixes if (p["prefix"], p.get("vrf") or "") not in have_p]
    seen, new_i = set(), []
    for a in local_ips:
        k = (a["ip"], a.get("vrf") or "")
        if k in have_i or k in seen:
            continue
        seen.add(k)
        new_i.append(a)
    loc = {(p["prefix"], p.get("vrf") or "") for p in local_prefixes}
    only_nb = [{"prefix": p["prefix"], "vrf": ((p.get("vrf") or {}).get("name") or ""), "description": p.get("description") or ""}
               for p in nb_prefixes if (p.get("prefix"), ((p.get("vrf") or {}).get("name") or "")) not in loc]
    return {"new_prefixes": new_p, "new_ips": new_i, "only_netbox": only_nb}
