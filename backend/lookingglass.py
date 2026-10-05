"""Looking Glass: ping, traceroute e consulta de rota BGP a partir dos roteadores marcados.

Quem consulta nunca digita comando: escolhe o roteador, o tipo de consulta e informa um IP, prefixo ou nome.
O destino é validado aqui e encaixado num modelo fixo por fabricante (o administrador pode ajustar os modelos).
"""
import ipaddress
import re
from typing import Dict, Optional

# consulta -> (rótulo, precisa de destino, aceita nome (DNS), aceita prefixo)
QUERIES = {
    "ping": ("Ping", True, True, False),
    "trace": ("Traceroute", True, True, False),
    "route": ("Rota BGP", True, False, True),
    "summary": ("Vizinhos BGP (resumo)", False, False, False),
}
QUERY_KEYS = list(QUERIES)
TEMPLATE_KEYS = [k + s for k in QUERY_KEYS for s in ("", "6")]          # ping, ping6, trace, trace6…

# {target} = como validado (IP, prefixo a.b.c.d/n ou nome) · {addr} = só o endereço · {len} = tamanho do prefixo
# {addr_len} = "endereço tamanho" quando veio prefixo, senão só o endereço (formato do Huawei)
DEFAULT_COMMANDS: Dict[str, Dict[str, str]] = {
    "huawei": {
        "ping": "ping -c 5 {target}", "ping6": "ping ipv6 -c 5 {target}",
        "trace": "tracert -w 1000 {target}", "trace6": "tracert ipv6 -w 1000 {target}",
        "route": "display bgp routing-table {addr_len}", "route6": "display bgp ipv6 routing-table {addr_len}",
        "summary": "display bgp peer", "summary6": "display bgp ipv6 peer",
    },
    "juniper": {
        "ping": "ping count 5 {target}", "ping6": "ping inet6 count 5 {target}",
        "trace": "traceroute wait 1 {target}", "trace6": "traceroute inet6 wait 1 {target}",
        "route": "show route protocol bgp {target} detail", "route6": "show route protocol bgp {target} detail",
        "summary": "show bgp summary", "summary6": "show bgp summary",
    },
    "cisco": {
        "ping": "ping {target} repeat 5", "ping6": "ping ipv6 {target} repeat 5",
        "trace": "traceroute {target}", "trace6": "traceroute ipv6 {target}",
        "route": "show ip bgp {target}", "route6": "show bgp ipv6 unicast {target}",
        "summary": "show ip bgp summary", "summary6": "show bgp ipv6 unicast summary",
    },
    "mikrotik": {
        "ping": "/ping {target} count=5", "ping6": "/ping {target} count=5",
        "trace": "/tool traceroute {target} count=3", "trace6": "/tool traceroute {target} count=3",
        "route": "/ip route print detail where {addr} in dst-address", "route6": "/ipv6 route print detail where {addr} in dst-address",
        "summary": "/routing bgp session print", "summary6": "/routing bgp session print",
    },
    "datacom": {
        "ping": "ping {target} count 5", "ping6": "ping6 {target} count 5",
        "trace": "traceroute {target}", "trace6": "traceroute6 {target}",
        "route": "show ip bgp prefix {target}", "route6": "show ipv6 bgp prefix {target}",
        "summary": "show ip bgp summary", "summary6": "show ipv6 bgp summary",
    },
    "zte": {
        "ping": "ping {target} repeat 5", "ping6": "ping6 {target} repeat 5",
        "trace": "trace {target}", "trace6": "trace6 {target}",
        "route": "show ip bgp route network {addr}", "route6": "show ipv6 bgp route network {addr}",
        "summary": "show ip bgp summary", "summary6": "show bgp ipv6 unicast summary",
    },
    "linux": {
        "ping": "ping -c 5 {target}", "ping6": "ping -6 -c 5 {target}",
        "trace": "traceroute -w 1 {target}", "trace6": "traceroute -6 -w 1 {target}",
        "route": 'vtysh -c "show bgp ipv4 unicast {target}"', "route6": 'vtysh -c "show bgp ipv6 unicast {target}"',
        "summary": 'vtysh -c "show bgp ipv4 unicast summary"', "summary6": 'vtysh -c "show bgp ipv6 unicast summary"',
    },
}
VENDORS = list(DEFAULT_COMMANDS)

_HOST_RE = re.compile(r"^(?=.{1,253}$)([a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,24}$")
_PLACEHOLDERS = ("target", "addr", "len", "addr_len")
_TPL_BAD = re.compile(r"[\r\n;&`$<>\\]|\|\|")           # um comando só; pipe simples é aceito ("| no-more", "| match")


class LGError(ValueError):
    pass


def parse_target(raw: str, query: str) -> Optional[dict]:
    """Valida o destino. Devolve {target, addr, len, addr_len, v6} ou None quando a consulta não usa destino."""
    if query not in QUERIES:
        raise LGError("Consulta desconhecida")
    _, needs, allow_host, allow_prefix = QUERIES[query]
    raw = (raw or "").strip()
    if not needs:
        return None
    if not raw:
        raise LGError("Informe o IP" + (" ou prefixo" if allow_prefix else " ou nome"))
    if len(raw) > 253:
        raise LGError("Destino longo demais")
    if "/" in raw:
        if not allow_prefix:
            raise LGError("Esta consulta aceita só um endereço (sem /máscara)")
        try:
            net = ipaddress.ip_network(raw, strict=False)
        except ValueError:
            raise LGError("Prefixo inválido — use o formato 200.160.0.0/20")
        return {"target": str(net), "addr": str(net.network_address), "len": str(net.prefixlen),
                "addr_len": f"{net.network_address} {net.prefixlen}", "v6": net.version == 6}
    try:
        ip = ipaddress.ip_address(raw)
        return {"target": str(ip), "addr": str(ip), "len": "128" if ip.version == 6 else "32",
                "addr_len": str(ip), "v6": ip.version == 6}
    except ValueError:
        pass
    if allow_host and _HOST_RE.match(raw):
        h = raw.lower()
        return {"target": h, "addr": h, "len": "", "addr_len": h, "v6": False}
    raise LGError("Destino inválido — use um IP" + (" ou prefixo (ex.: 8.8.8.0/24)" if allow_prefix else " ou nome (ex.: registro.br)"))


def check_template(tpl: str) -> str:
    """Modelo definido pelo administrador: uma linha, só os marcadores conhecidos."""
    tpl = (tpl or "").strip()
    if not tpl:
        return ""
    if len(tpl) > 200 or _TPL_BAD.search(tpl):
        raise LGError("Modelo de comando inválido (uma linha só, sem ; & ` $ < >)")
    for ph in re.findall(r"\{([^{}]*)\}", tpl):
        if ph not in _PLACEHOLDERS:
            raise LGError(f"Marcador desconhecido: {{{ph}}} — use {{target}}, {{addr}}, {{len}} ou {{addr_len}}")
    if "{" in re.sub(r"\{(%s)\}" % "|".join(_PLACEHOLDERS), "", tpl) or "}" in re.sub(r"\{(%s)\}" % "|".join(_PLACEHOLDERS), "", tpl):
        raise LGError("Chaves soltas no modelo")
    return tpl


def clean_overrides(data: dict) -> dict:
    out: Dict[str, Dict[str, str]] = {}
    for vendor, cmds in (data or {}).items():
        if vendor not in DEFAULT_COMMANDS or not isinstance(cmds, dict):
            continue
        for k, tpl in cmds.items():
            if k not in TEMPLATE_KEYS:
                continue
            t = check_template(str(tpl or ""))
            if t and t != DEFAULT_COMMANDS[vendor][k]:
                out.setdefault(vendor, {})[k] = t
    return out


def commands_for(vendor: str, overrides: Optional[dict] = None) -> Dict[str, str]:
    base = dict(DEFAULT_COMMANDS.get(vendor) or {})
    base.update((overrides or {}).get(vendor) or {})
    return base


def build(vendor: str, query: str, raw_target: str, overrides: Optional[dict] = None, v6: bool = False) -> str:
    """Comando final para o roteador. `v6` vale para a consulta sem destino (resumo)."""
    if vendor not in DEFAULT_COMMANDS:
        raise LGError("Este tipo de equipamento não tem comandos de Looking Glass")
    t = parse_target(raw_target, query)
    is6 = t["v6"] if t else bool(v6)
    tpl = commands_for(vendor, overrides).get(query + ("6" if is6 else ""))
    if not tpl:
        raise LGError("Consulta não disponível para este equipamento")
    if not t:
        return tpl
    return tpl.format(**{k: t[k] for k in _PLACEHOLDERS})


def tidy(out: str, command: str) -> str:
    """Tira o eco do comando e o prompt final da saída."""
    lines = out.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines and command.strip() and lines[0].strip().endswith(command.strip()):
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    if lines and re.match(r"^[\[<]?[\w.@:~/\-() ]{1,60}[>#\]$]\s*$", lines[-1]) and len(lines[-1]) < 70:
        lines.pop()
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)
