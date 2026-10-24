"""Janelas de manutenção e pré/pós-check.

- Janela = período + escopo (todos, equipamentos, grupos/tags, sondas). Durante a janela os alarmes do escopo não
  avisam (Telegram/push/plantão): o incidente ainda é aberto, marcado "em manutenção", para o histórico. No SLA o
  tempo da janela não conta como indisponibilidade.
- Pré/pós-check: roda os comandos do fabricante (vizinhos OSPF, sessões BGP, rotas, l2vc/LDP, interfaces) antes e
  depois; mostra os números que mudaram e o diff das saídas (sem as colunas que mudam sozinhas, como uptime).
"""
import difflib
import re
import time
from datetime import datetime, timezone
from typing import Optional

KINDS = {"ospf": "Vizinhos OSPF", "bgp": "Sessões BGP", "routes": "Tabela de rotas", "l2vc": "L2VC / VPLS", "ldp": "Sessões LDP",
         "iface": "Interfaces", "custom": "Outro"}

# comandos por fabricante (o admin pode trocar na janela)
TEMPLATES = {
    "huawei": [("ospf", "display ospf peer brief"), ("bgp", "display bgp peer"), ("bgp", "display bgp vpnv4 all peer"),
               ("routes", "display ip routing-table statistics"), ("ldp", "display mpls ldp session"),
               ("l2vc", "display mpls l2vc brief"), ("l2vc", "display vsi"), ("iface", "display interface brief")],
    "cisco": [("ospf", "show ip ospf neighbor"), ("bgp", "show ip bgp summary"), ("routes", "show ip route summary"),
              ("ldp", "show mpls ldp neighbor brief"), ("l2vc", "show mpls l2transport vc"), ("iface", "show ip interface brief")],
    "juniper": [("ospf", "show ospf neighbor"), ("bgp", "show bgp summary"), ("routes", "show route summary"),
                ("ldp", "show ldp session"), ("l2vc", "show l2circuit connections"), ("iface", "show interfaces terse")],
    "datacom": [("ospf", "show ip ospf neighbor"), ("bgp", "show ip bgp summary"), ("routes", "show ip route summary"),
                ("ldp", "show mpls ldp neighbor"), ("l2vc", "show mpls l2vpn vpws-group"), ("iface", "show interface link")],
    "zte": [("ospf", "show ip ospf neighbor"), ("bgp", "show ip bgp summary"), ("routes", "show ip forwarding route summary"),
            ("ldp", "show mpls ldp neighbor brief"), ("l2vc", "show mpls l2transport vc"), ("iface", "show ip interface brief")],
    "mikrotik": [("ospf", "/routing ospf neighbor print terse"), ("bgp", "/routing bgp session print terse"),
                 ("routes", "/ip route print count-only"), ("iface", "/interface print terse where running")],
    "linux": [("routes", "ip route | wc -l"), ("iface", "ip -br link")],
}
TEMPLATES["other"] = TEMPLATES["cisco"]
TEMPLATES["ubiquiti"] = TEMPLATES["linux"]

_VOLATILE = [
    (re.compile(r"\b\d+[dwhms]\d*[dwhms]?\d*[dwhms]?\b"), "<t>"),                 # 1d02h, 3w2d, 5m10s
    (re.compile(r"\b\d{1,3}:\d{2}:\d{2}\b"), "<t>"),                               # 01:02:03
    (re.compile(r"\b\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2})?\S*"), "<t>"),         # datas
    (re.compile(r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d+\s+\d{2}:\d{2}(:\d{2})?"), "<t>"),
    (re.compile(r"(?i)\b(up\s?time|age|last\s+(?:input|output|flap)|dead\s?time|expires?)\b[^\n]*"), r"\1 <t>"),
    (re.compile(r"\b\d{5,}\b"), "<n>"),                                             # contadores de mensagens/bytes
]


def normalize(text: str) -> str:
    lines = []
    for ln in (text or "").splitlines():
        s = ln.rstrip()
        if not s.strip() or re.match(r"^\s*(?:<[\w.\-]+>|\[[\w.\-~]+\]|[\w.\-@]+[#>$]\s*|\{master[:\d]*\})\s*$", s):
            continue                                                  # linha só com o prompt
        for rx, rep in _VOLATILE:
            s = rx.sub(rep, s)
        s = re.sub(r"[ \t]+", " ", s).strip()
        lines.append(s)
    return "\n".join(lines)


_IP = r"\b(?:\d{1,3}\.){3}\d{1,3}\b|\b[0-9a-fA-F]{1,4}(?::[0-9a-fA-F]{0,4}){2,7}\b"


def metrics(kind: str, text: str) -> dict:
    """Números que dá para comparar antes/depois (heurística que serve para os fabricantes mais comuns)."""
    t = text or ""
    low = t.lower()
    if kind == "ospf":
        full = len(re.findall(r"(?i)\bfull\b", t))
        return {"vizinhos Full": full}
    if kind == "bgp":
        est = 0
        peers = 0
        for ln in t.splitlines():
            if not re.match(r"^\s*\*?\s*(" + _IP + r")", ln):
                continue
            peers += 1
            if re.search(r"(?i)\bestablished\b|\bestabl\b", ln) or re.search(r"\s\d+\s*$", ln) or re.search(r"(?i)\bEstab", ln):
                est += 1
        out = {"sessões": peers, "estabelecidas": est}
        m = re.search(r"(?i)peers in established state\s*:\s*(\d+)", t)          # Huawei
        if m:
            out["estabelecidas"] = int(m.group(1))
        m = re.search(r"(?i)total number of peers\s*:\s*(\d+)", t)
        if m:
            out["sessões"] = int(m.group(1))
        return out
    if kind == "routes":
        for rx in (r"(?i)total\s*(?:routes?|number of routes)?\s*[:=]?\s*(\d+)", r"(?i)inet\.0:\s*(\d+) destinations",
                   r"(?i)^\s*total\s+(\d+)", r"^\s*(\d+)\s*$"):
            m = re.search(rx, t, re.M)
            if m:
                return {"rotas": int(m.group(1))}
        return {}
    if kind in ("l2vc", "ldp", "iface"):
        up = len(re.findall(r"(?i)(?<![\w-])(?:up|operational|oper|established|\*up)(?![\w-])", t))
        down = len(re.findall(r"(?i)(?<![\w-])(?:down|\*down|non-operational)(?![\w-])", t))
        return {"up": up, "down": down}
    _ = low
    return {}


def compare(pre: dict, post: dict) -> list:
    """pre/post: {cmd: {kind, out}} -> [{cmd, kind, before, after, changed, diff}]"""
    rows = []
    for cmd in list(dict.fromkeys(list(pre) + list(post))):
        a, b = pre.get(cmd) or {}, post.get(cmd) or {}
        kind = a.get("kind") or b.get("kind") or "custom"
        ma, mb = metrics(kind, a.get("out", "")), metrics(kind, b.get("out", ""))
        na, nb = normalize(a.get("out", "")), normalize(b.get("out", ""))
        diff = list(difflib.unified_diff(na.splitlines(), nb.splitlines(), "antes", "depois", lineterm="", n=1))[2:]
        delta = {k: (ma.get(k), mb.get(k)) for k in dict.fromkeys(list(ma) + list(mb)) if ma.get(k) != mb.get(k)}
        worse = any(_worse(kind, k, x, y) for k, (x, y) in delta.items())
        rows.append({"cmd": cmd, "kind": kind, "before": ma, "after": mb, "delta": delta, "changed": bool(diff),
                     "worse": worse, "diff": diff[:400], "error": a.get("error") or b.get("error") or ""})
    return rows


def _worse(kind, key, a, b) -> bool:
    if a is None or b is None:
        return False
    if key == "down":
        return b > a
    if kind == "routes":
        return b < a * 0.98            # sumiram mais de 2% das rotas
    return b < a


# ---------------------------------------------------------------- janelas
def window_state(w: dict, now: Optional[float] = None) -> str:
    if w.get("status") == "cancelled":
        return "cancelled"
    now = now or time.time()
    a, b = _ts(w.get("start")), _ts(w.get("end"))
    if a is None or b is None:
        return "scheduled"
    if now < a:
        return "scheduled"
    if now < b:
        return "active"
    return "done"


def _ts(v) -> Optional[float]:
    if not v:
        return None
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp()


def covers(w: dict, device_id: Optional[str] = None, tags: Optional[list] = None, probe_id: Optional[str] = None,
           host: str = "", hosts: Optional[dict] = None) -> bool:
    if w.get("all"):
        return True
    if device_id and device_id in (w.get("device_ids") or []):
        return True
    if tags and set(tags) & set(w.get("tags") or []):
        return True
    if probe_id and probe_id in (w.get("probe_ids") or []):
        return True
    if host and hosts and hosts.get(host) in (w.get("device_ids") or []):
        return True
    return False


_cache = {"at": 0.0, "wins": [], "devs": {}}


async def active_for(db, device_id: Optional[str] = None, host: str = "", probe_id: Optional[str] = None) -> Optional[dict]:
    """Janela ativa com silêncio que cobre o equipamento/IP/sonda (cache de 20 s)."""
    now = time.time()
    if now - _cache["at"] > 20:
        wins = []
        async for w in db.maint_windows.find({"status": {"$ne": "cancelled"}, "silence": {"$ne": False}}, {"_id": 0, "checks": 0}):
            if window_state(w, now) == "active":
                wins.append(w)
        devs = {}
        if wins:
            async for d in db.devices.find({}, {"_id": 0, "id": 1, "host": 1, "tags": 1}):
                devs[d["id"]] = d
        _cache.update(at=now, wins=wins, devs=devs)
    wins = _cache["wins"]
    if not wins:
        return None
    devs = _cache["devs"]
    if not device_id and host:
        device_id = next((d["id"] for d in devs.values() if d.get("host") == host), None)
    tags = (devs.get(device_id) or {}).get("tags") or []
    for w in wins:
        if covers(w, device_id, tags, probe_id):
            return w
    return None


def invalidate():
    _cache["at"] = 0.0
