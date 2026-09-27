"""MPLS pela CLI: LDP (sessões e interfaces), VPWS (l2vc), VPLS (vsi), L3VPN (vpn-instance/VRF) e MP-BGP VPN.

Os comandos por fabricante ficam em DEFAULT_COMMANDS (podem ser trocados na tela) e são tentados em ordem até um
responder sem erro. Os leitores são tolerantes: entendem os formatos de Huawei, Juniper e Cisco (IOS/XE/XR) e usam
regras genéricas (IP no início da linha + palavra de estado) para Datacom DMOS, ZTE e variações de firmware.
"""
import ipaddress
import re
from typing import Dict, List, Optional, Tuple

SECTIONS = ["ldp_session", "ldp_iface", "vpws", "vpls", "vrf", "bgp_vpn"]
SECTION_LABEL = {"ldp_session": "Sessões LDP", "ldp_iface": "Interfaces com LDP", "vpws": "VPWS (l2vc)",
                 "vpls": "VPLS (vsi)", "vrf": "L3VPN (vpn-instance/VRF)", "bgp_vpn": "MP-BGP VPN (vpnv4)"}

DEFAULT_COMMANDS: Dict[str, Dict[str, List[str]]] = {
    "huawei": {
        "ldp_session": ["display mpls ldp session"],
        "ldp_iface": ["display mpls ldp interface"],
        "vpws": ["display mpls l2vc brief", "display mpls l2vc"],
        "vpls": ["display vsi"],
        "vrf": ["display ip vpn-instance"],
        "bgp_vpn": ["display bgp vpnv4 all peer"],
    },
    "juniper": {
        "ldp_session": ["show ldp session"],
        "ldp_iface": ["show ldp interface"],
        "vpws": ["show l2circuit connections"],
        "vpls": ["show vpls connections"],
        "vrf": ["show route instance summary"],
        "bgp_vpn": ["show bgp summary"],
    },
    "cisco": {
        "ldp_session": ["show mpls ldp neighbor brief", "show mpls ldp neighbor"],
        "ldp_iface": ["show mpls interfaces", "show mpls ldp interface brief"],
        "vpws": ["show mpls l2transport vc", "show l2vpn xconnect"],
        "vpls": ["show vfi", "show l2vpn bridge-domain brief"],
        "vrf": ["show vrf", "show vrf all"],
        "bgp_vpn": ["show bgp vpnv4 unicast all summary", "show bgp vpnv4 unicast summary", "show ip bgp vpnv4 all summary"],
    },
    "datacom": {
        "ldp_session": ["show mpls ldp neighbor", "show mpls ldp session"],
        "ldp_iface": ["show mpls ldp interface"],
        "vpws": ["show mpls l2vpn vpws-group", "show mpls l2vpn vpws"],
        "vpls": ["show mpls l2vpn vpls-group", "show mpls l2vpn vpls"],
        "vrf": ["show vrf", "show ip vrf"],
        "bgp_vpn": ["show bgp vpnv4 unicast summary", "show bgp summary"],
    },
    "zte": {
        "ldp_session": ["show mpls ldp neighbor", "show mpls ldp session"],
        "ldp_iface": ["show mpls ldp interface"],
        "vpws": ["show mpls l2transport vc", "show l2vpn vpws"],
        "vpls": ["show vpls", "show l2vpn vpls"],
        "vrf": ["show ip vrf", "show ip vrf brief"],
        "bgp_vpn": ["show bgp vpnv4 unicast summary", "show ip bgp vpnv4 all summary"],
    },
}

IP = r"(?:\d{1,3}\.){3}\d{1,3}"
_IP_RE = re.compile(IP)
_ERR = re.compile(r"(?i)unrecognized command|% ?invalid|invalid input|syntax error|unknown command|% ?unknown|"
                  r"wrong parameter|error: (?:unrecognized|syntax|incomplete|invalid)|incomplete command|not supported|"
                  r"command not found|% ?error|unknown keyword")
_DOWN_WORDS = {"nonexistent", "initialized", "openrec", "opensent", "down", "closed", "closing", "init", "idle",
               "connect", "active", "openconfirm", "ld", "rd", "ci", "np", "ol", "cm", "vc-dn", "dn", "admin-down",
               "inactive", "failed", "notready"}


def is_error(text: str) -> bool:
    body = "\n".join(text.splitlines()[1:])     # 1ª linha = eco do comando
    return bool(_ERR.search(body)) and len(_IP_RE.findall(body)) == 0


_PROMPT = re.compile(r"^\s*(<[^>]+>|\[[^\]]+\]|\{[^}]+\}|\S+[#>])\s*$")


def _lines(text: str) -> List[str]:
    """Linhas da saída sem o prompt do equipamento (<HUAWEI>, PE#, {master}, user@mx>)."""
    return [l.rstrip() for l in text.replace("\r", "").split("\n") if not _PROMPT.match(l)]


def _state(word: str) -> Optional[str]:
    w = word.strip().strip(";,").lower()
    if w in ("operational", "oper", "established", "establ", "up"):
        return "up"
    if w in _DOWN_WORDS:
        return "down"
    return None


def _valid_ip(s: str) -> bool:
    try:
        ipaddress.IPv4Address(s)
        return True
    except ValueError:
        return False


# ---------------- interfaces: nomes equivalentes entre fabricantes ----------------
_IF_TYPES = [
    ("hundredgigabitethernet", "100ge"), ("hundredgige", "100ge"), ("100gigabitethernet", "100ge"), ("100ge", "100ge"), ("hu", "100ge"),
    ("fortygigabitethernet", "40ge"), ("fortygige", "40ge"), ("40ge", "40ge"), ("fo", "40ge"),
    ("twentyfivegige", "25ge"), ("25ge", "25ge"),
    ("xgigabitethernet", "xge"), ("tengigabitethernet", "xge"), ("tengige", "xge"), ("ten-gigabit-ethernet", "xge"),
    ("xgei", "xge"), ("xge", "xge"), ("te", "xge"),
    ("gigabitethernet", "ge"), ("gigabit-ethernet", "ge"), ("gei", "ge"), ("gi", "ge"), ("ge", "ge"),
    ("eth-trunk", "trunk"), ("port-channel", "trunk"), ("bundle-ether", "trunk"), ("smartgroup", "trunk"), ("lag", "trunk"),
    ("ae", "trunk"), ("po", "trunk"), ("be", "trunk"),
    ("vlanif", "vlan"), ("vlan", "vlan"), ("loopback", "lo"), ("lo", "lo"),
]


def iface_key(name: str) -> str:
    """'GigabitEthernet0/0/1' == 'GE0/0/1' == 'Gi0/0/1'; 'xe-0/0/0.0' ≈ 'xe-0/0/0'."""
    n = (name or "").strip().lower().replace(" ", "")
    m = re.match(r"^(\d*[a-z][a-z\-]*?)[\-]?(\d.*)$", n)
    if not m:
        return n
    typ, rest = m.group(1).rstrip("-"), m.group(2)
    for long, short in _IF_TYPES:
        if typ == long:
            typ = short
            break
    return f"{typ}{rest}"


def iface_match(a: str, b: str) -> bool:
    ka, kb = iface_key(a), iface_key(b)
    if ka == kb:
        return True
    strip0 = lambda k: re.sub(r"\.0$", "", k)          # noqa: E731  (Juniper: unidade .0)
    return strip0(ka) == strip0(kb)


_IFNAME = re.compile(r"^\*?\s*((?:\d+[A-Za-z]+|[A-Za-z][A-Za-z\-]*)[\-]?\d[\w/\.:\-]*)(?:\s|$)")
_NOT_IF = re.compile(r"(?i)^(interface|if_name|total|codes|peer|address|name|vsi|vpn|legend|instance)")


def _vpn_col(lines: List[str]) -> Optional[int]:
    """Tabelas estilo 'Group  VPN  ... State' (DMOS): posição da coluna VPN no cabeçalho."""
    for l in lines[:12]:
        if re.search(r"(?i)group", l):
            m = re.search(r"(?i)\bvpn\b", l)
            if m:
                return m.start()
    return None


def _token_at(row: str, col: int) -> Optional[str]:
    best = None
    for m in re.finditer(r"\S+", row):
        if m.start() <= col + 2 and m.end() > col:
            return m.group(0)
        if best is None or abs(m.start() - col) < abs(best.start() - col):
            best = m
    return best.group(0) if best else None


# ---------------- LDP ----------------
def parse_ldp_sessions(text: str) -> Tuple[List[dict], bool]:
    """-> (sessões [{peer, state, raw_state}], reconheceu?)"""
    L = _lines(text)
    out: Dict[str, dict] = {}
    if any(re.search(r"(?i)peer ldp ident", l) for l in L):      # blocos Cisco IOS / ZTE
        cur = None
        for l in L:
            m = re.search(rf"(?i)peer ldp ident(?:ifier)?\s*:\s*({IP})", l)
            if m:
                cur = m.group(1)
                out[cur] = {"peer": cur, "state": "up", "raw_state": "listado"}
                continue
            m = re.search(r"(?i)\bstate\s*:\s*([A-Za-z\-]+)", l)
            if cur and m:
                out[cur]["raw_state"] = m.group(1)
                out[cur]["state"] = _state(m.group(1)) or ("up" if m.group(1).lower().startswith("oper") else "down")
        return list(out.values()), True
    for l in L:
        m = re.match(rf"^\s*\*?\s*({IP})(?::\d+)?\s+(.*)$", l)
        if not m or not _valid_ip(m.group(1)):
            continue
        rest = m.group(2).split()
        st, raw = None, None
        for w in rest:
            s = _state(w)
            if s:
                st, raw = s, w
                break
        out[m.group(1)] = {"peer": m.group(1), "state": st or "up", "raw_state": raw or "listado"}
    recognized = bool(out) or bool(re.search(r"(?i)(total|no ldp|0 session|not enabled|no session|none)", text))
    return list(out.values()), recognized


def parse_ldp_ifaces(text: str) -> Tuple[List[dict], bool]:
    out = []
    for l in _lines(text)[1:]:
        s = l.strip()
        if not s or _NOT_IF.match(s) or s.startswith("-"):
            continue
        m = _IFNAME.match(s)
        if not m or _valid_ip(m.group(1).split(":")[0]):
            continue
        name = m.group(1)
        rest = s[m.end():].strip()
        if re.match(r"(?i)^no\b", rest) and "(ldp)" not in rest.lower():     # Cisco "show mpls interfaces": IP=No -> só TE/estático
            continue
        yn = re.search(r"(?:^|\s)[YN]\s+([YN])(?:\s|$)", rest)          # XR brief: colunas Config Enabled
        if yn:
            active = yn.group(1) == "Y"
        else:
            active = not re.search(r"(?i)\b(inactive|down|disabled)\b", rest)
        if iface_key(name).startswith("lo"):
            continue
        out.append({"iface": name, "active": active})
    return out, bool(out) or bool(re.search(r"(?i)(interface|total)", text))


# ---------------- VPWS ----------------
def parse_vpws(text: str) -> Tuple[List[dict], bool]:
    L = _lines(text)
    vcs: List[dict] = []
    if any(re.search(r"(?i)^\s*\*?\s*client interface\s*:", l) for l in L):        # Huawei l2vc
        cur = None
        for l in L:
            m = re.match(r"(?i)^\s*\*?\s*([A-Za-z][\w /\-]*?)\s*:\s*(.*)$", l)
            if not m:
                continue
            k, v = m.group(1).strip().lower(), m.group(2).strip()
            if k == "client interface":
                cur = {"iface": v, "vcid": None, "peer": None, "state": None, "ac": None}
                vcs.append(cur)
            elif cur is None:
                continue
            elif k == "vc state":
                cur["state"] = "up" if v.lower() == "up" else "down"
            elif k == "ac status":
                cur["ac"] = "up" if v.lower() == "up" else "down"
            elif k == "vc id":
                cur["vcid"] = v.split()[0]
            elif k in ("destination", "peer address", "peer ip"):
                cur["peer"] = v.split()[0]
        return [v for v in vcs if v["vcid"] or v["peer"]], True
    if any(re.match(r"(?i)^\s*neighbor\s*:", l) for l in L):                       # Juniper l2circuit
        peer = None
        for l in L:
            m = re.match(rf"(?i)^\s*neighbor\s*:\s*({IP})", l)
            if m:
                peer = m.group(1)
                continue
            m = re.match(r"^\s*(\S+?)\(vc\s*(\d+)\)\s+\S+\s+(\S+)", l)
            if m and peer:
                st = m.group(3)
                vcs.append({"iface": m.group(1), "vcid": m.group(2), "peer": peer, "state": "up" if st.lower() == "up" else "down",
                            "raw_state": st, "ac": None})
        return vcs, True
    vcol = _vpn_col(L)
    for l in L[1:]:                                                                 # tabelas (Cisco, XR, ZTE, DMOS)
        ips = [m for m in re.finditer(IP, l) if _valid_ip(m.group(0))]
        if not ips:
            continue
        ip = ips[-1]
        after = l[ip.end():].split()
        vcid = next((w for w in after if w.isdigit()), None)
        st = None
        for w in reversed(l.split()):
            st = _state(w)
            if st:
                break
        if vcid is None or st is None:
            continue
        first = l.split()[0] if l.split() else ""
        vcs.append({"iface": first if not _valid_ip(first) else None, "vcid": vcid, "peer": ip.group(0), "state": st,
                    "raw_state": None, "ac": None, "name": _token_at(l, vcol) if vcol is not None else None})
    return vcs, bool(vcs) or bool(re.search(r"(?i)(total|no .*vc|0 vc|circuit)", text))


# ---------------- VPLS ----------------
def parse_vpls(text: str) -> Tuple[List[dict], bool]:
    L = _lines(text)
    vsis: List[dict] = []
    if any(re.match(r"(?i)^\s*instance\s*:", l) for l in L):                      # Juniper vpls connections
        cur = None
        for l in L:
            m = re.match(r"(?i)^\s*instance\s*:\s*(\S+)", l)
            if m:
                cur = {"name": m.group(1), "pws": 0, "pws_up": 0}
                vsis.append(cur)
                continue
            m = re.match(r"^\s*(\S+?(?:\([^)]*\))?)\s+(rmt|loc)\s+(\S+)", l)
            if m and cur is not None:
                cur["pws"] += 1
                cur["pws_up"] += m.group(3).lower() == "up"
        for v in vsis:
            v["state"] = "up" if v["pws"] and v["pws_up"] == v["pws"] else ("degraded" if v["pws_up"] else "down")
        return vsis, True
    if any(re.match(r"(?i)^\s*vfi name\s*:", l) for l in L):                       # Cisco show vfi
        cur = None
        for l in L:
            m = re.match(r"(?i)^\s*vfi name\s*:\s*([^,]+),\s*state\s*:\s*(\S+?),", l)
            if m:
                cur = {"name": m.group(1).strip(), "state": "up" if m.group(2).lower() == "up" else "down", "pws": 0, "pws_up": None}
                vsis.append(cur)
                continue
            if cur and re.match(rf"^\s*{IP}\s+\d+", l):
                cur["pws"] += 1
        return vsis, True
    hdr_seen = False
    vcol = _vpn_col(L)
    for l in L[1:]:                                                                  # tabela (Huawei display vsi, XR, DMOS, ZTE)
        s = l.strip()
        if not s:
            continue
        if s.startswith("-"):
            hdr_seen = True
            continue
        toks = s.split()
        if len(toks) < 2 or re.match(r"(?i)^(vsi|name|total|bridge|legend|vpls)(\s|$)", s):
            continue
        st = _state(toks[-1]) or next((_state(t) for t in toks[1:] if _state(t)), None)
        if st is None or _valid_ip(toks[0]):
            continue
        name = _token_at(l, vcol) if vcol is not None else toks[0]
        v = {"name": name or toks[0], "state": st, "pws": None, "pws_up": None}
        m = re.search(r"(\d+)/(\d+)\s*$", s)            # XR: Num PWs/up
        if m:
            v["pws"], v["pws_up"] = int(m.group(1)), int(m.group(2))
        vsis.append(v)
    return vsis, bool(vsis) or hdr_seen or bool(re.search(r"(?i)total vsi number is 0|no vsi|no vpls", text))


# ---------------- L3VPN ----------------
def parse_vrfs(text: str) -> Tuple[List[dict], bool]:
    L = _lines(text)
    vrfs: Dict[str, dict] = {}
    if re.search(r"(?i)primary rib", text):                                          # Juniper route instance summary
        cur = None
        for l in L:
            m = re.match(r"^(\S+)\s+(vrf|virtual-router|vpls|l2vpn|forwarding|evpn|non-forwarding)\s*$", l.strip() and l)
            if m:
                cur = m.group(1) if m.group(2) in ("vrf", "virtual-router") else None
                if cur:
                    vrfs[cur] = {"name": cur, "rd": None, "routes": 0}
                continue
            m = re.match(r"^\s+(\S+)\.inet6?\.0\s+(\d+)/\d+/\d+", l)
            if m and cur:
                vrfs[cur]["routes"] += int(m.group(2))
        return list(vrfs.values()), True
    for l in L[1:]:
        s = l.strip()
        if not s or s.startswith("-") or re.match(r"(?i)^(vpn-instance name|name|total|vrf\b.*\bdefault rd|\*|codes)", s):
            continue
        toks = s.split()
        rd = next((t for t in toks[1:] if re.match(r"^(\d+|" + IP + r"):\d+$", t)), None)
        name = toks[0]
        if _valid_ip(name) or ":" in name or name.endswith(":") or re.match(r"(?i)^(ipv4|ipv6|<not|default|up|down|#)", name):
            continue
        vrfs.setdefault(name, {"name": name, "rd": rd, "routes": None})
    return list(vrfs.values()), bool(vrfs) or bool(re.search(r"(?i)vpn-instances? configured\s*:\s*0|no vrf", text))


def parse_bgp_vpn(text: str) -> Tuple[List[dict], bool]:
    L = _lines(text)
    peers: List[dict] = []
    cur = None
    for l in L:
        m = re.match(rf"^\s*({IP})\s+(.*)$", l)
        if m and _valid_ip(m.group(1)):
            toks = m.group(2).split()
            st, pref = None, None
            for w in toks:
                if w.lower().startswith("establ"):
                    st = "up"
                    break
                if w.lower() in ("idle", "active", "connect", "opensent", "openconfirm", "idle(admin)", "(admin)"):
                    st = "down"
            if st is None and toks and toks[-1].isdigit():         # Cisco: State/PfxRcd numérico = estabelecida
                st, pref = "up", int(toks[-1])
            elif st == "up" and toks and toks[-1].isdigit():         # Huawei: ... Established PrefRcv
                pref = int(toks[-1])
            if st is None and toks and re.match(r"(?i)^(idle|active|connect|open)", toks[-1]):
                st = "down"
            if st is None:
                continue
            asn = next((t for t in toks if t.isdigit() and t not in ("4",)), None)
            cur = {"peer": m.group(1), "state": st, "as": asn, "prefixes": pref, "vpn": None}
            peers.append(cur)
            continue
        m = re.match(r"^\s+(bgp\.(?:l3vpn|l2vpn|evpn)\.0|\S+\.inet\.0)\s*:\s*(\d+)/(\d+)", l)     # Juniper: tabelas do peer
        if m and cur:
            if m.group(1).startswith("bgp.l"):
                cur["vpn"] = True
                cur["prefixes"] = (cur["prefixes"] or 0) + int(m.group(3))
    if any(p["vpn"] for p in peers):                         # Juniper summary traz todos os peers: fica com os de VPN
        vpn_as = {p["as"] for p in peers if p["vpn"]}
        peers = [p for p in peers if p["vpn"] or (p["state"] == "down" and p["as"] in vpn_as)]
    return peers, bool(peers) or bool(re.search(r"(?i)(total number of peers\s*:\s*0|no bgp|bgp not active)", text))


PARSERS = {"ldp_session": parse_ldp_sessions, "ldp_iface": parse_ldp_ifaces, "vpws": parse_vpws,
           "vpls": parse_vpls, "vrf": parse_vrfs, "bgp_vpn": parse_bgp_vpn}


def commands_for(device_type: str, settings: Optional[dict] = None) -> Dict[str, List[str]]:
    base = DEFAULT_COMMANDS.get(device_type or "", {})
    custom = ((settings or {}).get("mpls_commands") or {}).get(device_type or "", {})
    return {sec: [c for c in (custom.get(sec) or base.get(sec) or []) if c.strip()] for sec in SECTIONS}


async def collect(session, device_type: str, settings: Optional[dict] = None, max_raw: int = 12000) -> dict:
    """Roda as seções na mesma sessão de shell. -> {sec: {ok, items, command, raw}}"""
    out = {}
    for sec, cmds in commands_for(device_type, settings).items():
        res = {"ok": False, "items": [], "command": None, "raw": ""}
        raws = []
        for cmd in cmds:
            try:
                text = await session.run(cmd, timeout=60, idle=2.5)
            except Exception as e:
                raws.append(f"### {cmd}\n(erro: {e})")
                res["error"] = str(e)
                break
            raws.append(f"### {cmd}\n{text.strip()}")
            if is_error(text):
                continue
            items, ok = PARSERS[sec](text)
            if ok:
                res.update(ok=True, items=items, command=cmd)
                break
        res["raw"] = "\n\n".join(raws)[-max_raw:]
        out[sec] = res
    return out
