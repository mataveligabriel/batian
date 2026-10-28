"""Documentação de IP por grupo (como as seções do phpIPAM) e importação do phpIPAM.

Cada grupo é um espaço próprio de documentação: prefixos (sub-redes) e IPs com hostname, descrição, MAC, dono,
situação e observação. O mesmo IP pode estar em quantos grupos quiser (redes separadas, clientes, cidades); dentro de um
grupo cada IP aparece uma vez (por VRF).

phpIPAM: API REST em /api/<app_id>/ — token estático do app (header "token") ou usuário/senha (POST /user/ devolve
um token temporário). Seções → grupos, sub-redes → prefixos, endereços → IPs.
"""
import ipaddress
import re
from typing import Awaitable, Callable, Dict, List, Optional

STATUSES = {"used": "Em uso", "reserved": "Reservado", "dhcp": "DHCP", "offline": "Desligado", "free": "Livre"}
# tags padrão do phpIPAM (id -> situação aqui); a instalação pode ter outras: lidas em /addresses/tags/
PHP_TAGS = {"1": "offline", "2": "used", "3": "reserved", "4": "dhcp"}
COLORS = ("#2F6FCB", "#1E9E6B", "#C2410C", "#7C3AED", "#BE185D", "#0E7490", "#A16207", "#4B5563")


class DocError(ValueError):
    pass


def _s(v, n: int = 200) -> str:
    return re.sub(r"[\x00-\x08\x0b-\x1f]", "", str(v if v is not None else "")).strip()[:n]


def clean_group(b: dict) -> dict:
    name = _s(b.get("name"), 80)
    if not name:
        raise DocError("Dê um nome ao grupo (ex.: Rede Guaçuí, Cliente X)")
    color = b.get("color") if re.fullmatch(r"#[0-9A-Fa-f]{6}", str(b.get("color") or "")) else COLORS[0]
    return {"name": name, "description": _s(b.get("description"), 500), "color": color}


def clean_subnet(b: dict) -> dict:
    try:
        n = ipaddress.ip_network(str(b.get("prefix") or "").strip(), strict=False)
    except ValueError:
        raise DocError(f"Prefixo inválido: {b.get('prefix')}")
    vlan = b.get("vlan")
    try:
        vlan = int(vlan) if vlan not in (None, "") else None
        if vlan is not None and not 0 < vlan < 4095:
            raise ValueError
    except (TypeError, ValueError):
        raise DocError("VLAN deve ser de 1 a 4094")
    return {"prefix": str(n), "description": _s(b.get("description"), 300), "vlan": vlan, "vrf": _s(b.get("vrf"), 60),
            "location": _s(b.get("location"), 120), "gateway": _clean_ip_opt(b.get("gateway"), n), "notes": _s(b.get("notes"), 2000)}


def _clean_ip_opt(v, net=None) -> str:
    v = str(v or "").strip()
    if not v:
        return ""
    try:
        a = ipaddress.ip_address(v)
    except ValueError:
        raise DocError(f"IP inválido: {v}")
    if net is not None and (a.version != net.version or a not in net):
        raise DocError(f"{a} fora de {net}")
    return str(a)


def clean_ip(b: dict) -> dict:
    ip = _clean_ip_opt(b.get("ip"))
    if not ip:
        raise DocError("Informe o IP")
    st = b.get("status") if b.get("status") in STATUSES else "used"
    mac = _s(b.get("mac"), 40)
    if mac:
        hx = re.sub(r"[^0-9A-Fa-f]", "", mac)
        if len(hx) != 12:
            raise DocError(f"MAC inválido: {mac}")
        mac = ":".join(hx[i:i + 2] for i in range(0, 12, 2)).lower()
    return {"ip": ip, "hostname": _s(b.get("hostname"), 120), "description": _s(b.get("description"), 300), "mac": mac,
            "owner": _s(b.get("owner"), 120), "status": st, "vrf": _s(b.get("vrf"), 60), "notes": _s(b.get("notes"), 2000),
            "device_id": _s(b.get("device_id"), 40) or None}


def ip_key(ip: str) -> tuple:
    a = ipaddress.ip_address(ip)
    return (a.version, int(a))


def subnet_of(ip: str, subnets: List[dict]) -> Optional[dict]:
    """O prefixo mais específico do grupo que contém o IP (mesma VRF quando o IP tem VRF)."""
    a = ipaddress.ip_address(ip)
    best, blen = None, -1
    for s in subnets:
        n = ipaddress.ip_network(s["prefix"])
        if n.version == a.version and a in n and n.prefixlen > blen:
            best, blen = s, n.prefixlen
    return best


def usage(prefix: str, ips: List[str]) -> dict:
    n = ipaddress.ip_network(prefix)
    size = n.num_addresses
    usable = size - 2 if n.version == 4 and n.prefixlen <= 30 else size
    used = len({i for i in ips if ipaddress.ip_address(i) in n})
    return {"size": size, "usable": usable, "used": used, "pct": round(100.0 * used / usable, 1) if usable else 0.0}


def free_ips(prefix: str, used: List[str], limit: int = 64) -> List[str]:
    n = ipaddress.ip_network(prefix)
    taken = set(used)
    hosts = n.hosts() if n.version == 4 and n.prefixlen <= 30 else iter(n)
    out = []
    for h in hosts:
        if str(h) not in taken:
            out.append(str(h))
            if len(out) >= limit:
                break
    return out


CSV_COLS = ["ip", "hostname", "description", "mac", "owner", "status", "vrf", "notes"]


def parse_csv_rows(text: str) -> List[dict]:
    """CSV com cabeçalho (ip;hostname;descrição…, vírgula ou ponto e vírgula). Colunas desconhecidas são ignoradas."""
    import csv
    import io
    lines = (text or "").lstrip("﻿")
    if not lines.strip():
        return []
    sample = lines.splitlines()[0]
    delim = ";" if sample.count(";") >= sample.count(",") else ","
    rd = csv.DictReader(io.StringIO(lines), delimiter=delim)
    alias = {"ip address": "ip", "ip_addr": "ip", "endereco": "ip", "endereço": "ip", "descricao": "description", "descrição": "description",
             "dono": "owner", "situacao": "status", "situação": "status", "obs": "notes", "observacao": "notes", "observação": "notes", "note": "notes"}
    out = []
    for row in rd:
        r = {}
        for k, v in row.items():
            if k is None:
                continue
            kk = alias.get(k.strip().lower(), k.strip().lower())
            if kk in CSV_COLS:
                r[kk] = v
        st = str(r.get("status") or "").strip().lower()
        r["status"] = {"em uso": "used", "reservado": "reserved", "desligado": "offline", "livre": "free"}.get(st, st or "used")
        if r.get("ip"):
            out.append(r)
    return out


# ---------------------------------------------------------------- phpIPAM
def php_base_url(url: str) -> str:
    """Aceita o endereço como a pessoa copia do navegador (…/phpipam/index.php?page=login) e devolve a raiz (…/phpipam)."""
    from urllib.parse import urlsplit, urlunsplit
    u = urlsplit((url or "").strip())
    path = re.sub(r"/index\.php$", "", u.path.rstrip("/"))
    path = re.sub(r"/api$", "", path.rstrip("/")).rstrip("/")
    return urlunsplit((u.scheme, u.netloc, path, "", ""))


def _why(st: int, j) -> str:
    if j is None:
        return (f"HTTP {st}, e a resposta não é da API (página HTML). Confira a URL — deve ser a raiz do phpIPAM, ex.: "
                "http://servidor/phpipam — e se o Apache tem o mod_rewrite ligado (AllowOverride All) para o /api/ funcionar")
    msg = str(j.get("message") or "")
    hint = ""
    if "ssl" in msg.lower() or "https" in msg.lower():
        hint = " — o app está como 'SSL with …': use https ou mude a segurança do app para 'User token' (Administração → API)"
    elif st == 401 and not msg:
        hint = " — usuário ou senha do phpIPAM"
    elif "app" in msg.lower() and ("invalid" in msg.lower() or "not" in msg.lower()):
        hint = " — confira o App ID (exatamente o nome do app em Administração → API)"
    return f"HTTP {st}: {msg or 'sem mensagem'}{hint}"


class PhpIpam:
    """Cliente mínimo. request(method, url, headers, auth) -> (status, json) é injetado (httpx no servidor, falso nos testes)."""

    def __init__(self, url: str, app_id: str, request: Callable[..., Awaitable[tuple]], token: str = "", user: str = "", password: str = ""):
        self.base = f"{php_base_url(url)}/api/{app_id.strip('/')}"
        self.request, self.static, self.user, self.password = request, token, user, password
        self.token = ""

    async def login(self):
        if self.static:
            self.token = self.static
            return
        st, j = await self.request("POST", f"{self.base}/user/", {}, (self.user, self.password))
        if st >= 400 or not (j or {}).get("success"):
            raise DocError(f"phpIPAM recusou o login ({_why(st, j)})")
        self.token = ((j or {}).get("data") or {}).get("token") or ""
        if not self.token:
            raise DocError("phpIPAM não devolveu token")

    async def get(self, path: str) -> list:
        st, j = await self.request("GET", f"{self.base}/{path.strip('/')}/", {"token": self.token, "phpipam-token": self.token}, None)
        if st == 404 or (j and j.get("code") == 404):
            return []                                    # "No subnets found" etc.
        if st in (401, 403):
            raise DocError(f"phpIPAM: sem permissão em /{path} ({_why(st, j)}; o app precisa de permissão de leitura)")
        if st >= 400 or not j or not j.get("success", True):
            raise DocError(f"phpIPAM /{path}: {_why(st, j)}")
        d = j.get("data")
        return d if isinstance(d, list) else ([d] if isinstance(d, dict) else [])

    async def tag_map(self) -> Dict[str, str]:
        out = dict(PHP_TAGS)
        try:
            for t in await self.get("addresses/tags"):
                name = str(t.get("type") or "").lower()
                st = "used" if "used" in name or "online" in name else "reserved" if "reserv" in name else "dhcp" if "dhcp" in name \
                    else "offline" if "offline" in name else None
                if st and t.get("id") is not None:
                    out[str(t["id"])] = st
        except DocError:
            pass
        return out


def php_subnet(s: dict, vlans: Dict[str, dict], vrfs: Dict[str, dict]) -> Optional[dict]:
    if str(s.get("isFolder") or "0") == "1" or not s.get("subnet") or s.get("mask") in (None, ""):
        return None
    try:
        n = ipaddress.ip_network(f"{s['subnet']}/{s['mask']}", strict=False)
    except ValueError:
        return None
    v = vlans.get(str(s.get("vlanId") or ""))
    vr = vrfs.get(str(s.get("vrfId") or ""))
    return {"prefix": str(n), "description": _s(s.get("description"), 300), "vlan": int(v["number"]) if v and str(v.get("number", "")).isdigit() else None,
            "vrf": _s((vr or {}).get("name"), 60), "location": "", "gateway": "", "notes": "", "php_id": str(s.get("id"))}


def php_address(a: dict, tags: Dict[str, str], vrf: str = "") -> Optional[dict]:
    ip = a.get("ip") or a.get("ip_addr")
    try:
        ip = str(ipaddress.ip_address(str(ip).strip()))
    except ValueError:
        return None
    note = _s(a.get("note"), 2000)
    if str(a.get("is_gateway") or "0") == "1":
        note = ("gateway. " + note).strip()
    return {"ip": ip, "hostname": _s(a.get("hostname"), 120), "description": _s(a.get("description"), 300), "mac": _s(a.get("mac"), 40),
            "owner": _s(a.get("owner"), 120), "status": tags.get(str(a.get("tag") or "2"), "used"), "vrf": vrf, "notes": note,
            "device_id": None, "php_id": str(a.get("id") or "")}
