"""Botnet nos assinantes: usa o flow exportado pelos BNGs para achar clientes infectados.

Olha só o tráfego que SAI do assinante (origem dentro das faixas de assinantes, vindo de um exportador marcado como
BNG) e procura, por IP de origem, a cada minuto:

  ddos   – inundando um destino (muitos pacotes pequenos / SYN, ou UDP pesado para um IP só)
  scan   – varrendo a internet numa porta típica de botnet (Telnet, TR-069, ADB, Winbox, SMB…)
  spam   – SMTP (25) para muitos servidores
  amp    – respondendo como refletor (DNS/NTP/SSDP/Memcached aberto no cliente)
  c2     – falando com um IP de comando-e-controle conhecido (lista sua ou feeds)

O coletor (flowd) chama add() por flow e tick() a cada minuto. O backend consulta o BNG (CLI) para trocar o IP
pelo login do assinante (lookup_command / parse_subscriber).
"""
import heapq
import ipaddress
import re
import time
from typing import Dict, List, Optional, Tuple

from flowagg import AMP_PORTS, PrefixSet, ip_str
from flowproto import F_BYTES, F_DPORT, F_DST, F_EXP, F_FLAGS, F_PKTS, F_PROTO, F_SPORT, F_SRC, F_VER

DEFAULTS = {
    "enabled": False,
    "exporters": [],                 # IPs dos BNGs (exportadores de flow)
    "subscriber_prefixes": ["100.64.0.0/10"],
    "ddos_pps": 1000,                # pacotes/s para UM destino, com pacote médio pequeno
    "ddos_bps": 50_000_000,          # ou UDP (fora 443) para um destino acima disto
    "syn_pps": 300,                  # SYN/s sem resposta para um destino
    "scan_hosts": 30,                # destinos distintos por minuto na mesma porta de botnet
    "spam_hosts": 15,                # servidores SMTP distintos por minuto
    "amp_bps": 5_000_000,            # resposta de porta de amplificação saindo do cliente
    "min_windows": 2,                # minutos seguidos para abrir o incidente (c2 abre no primeiro)
    "end_windows": 15,               # minutos quieto para encerrar
    "c2_ips": [],                    # sua lista de IPs/redes de C2
    "feeds": [],                     # URLs de listas (um IP ou CIDR por linha), atualizadas a cada 6 h
    "ignore": [],                    # assinantes que não geram incidente (servidor de e-mail do cliente, scanner autorizado…)
    "ignore_dst": [],                # destinos que nunca contam como alvo de flood (servidor de jogo, IPTV, parceiro…)
    "alert": True,
    "lookup": True,                  # perguntar ao BNG quem é o assinante
    "lookup_commands": {},           # fabricante -> comando (vazio = padrão)
    "devices": {},                   # IP do exportador -> id do equipamento (para a consulta por SSH)
}
NUMERIC = ("ddos_pps", "ddos_bps", "syn_pps", "scan_hosts", "spam_hosts", "amp_bps", "min_windows", "end_windows")
KINDS = {"ddos": "Participando de ataque (flood)", "scan": "Varredura / propagação", "spam": "Envio de spam (SMTP)",
         "amp": "Refletor de amplificação", "c2": "Contato com comando-e-controle"}
SEVERITY = {"c2": 3, "ddos": 3, "scan": 2, "amp": 2, "spam": 1}
# portas que as famílias de botnet de CPE/IoT varrem para se espalhar
SCAN_PORTS = {23: "Telnet", 2323: "Telnet", 22: "SSH", 2222: "SSH", 445: "SMB", 3389: "RDP", 5555: "ADB (Android)",
              7547: "TR-069", 37215: "Huawei HG (UPnP)", 52869: "Realtek UPnP", 8291: "Winbox (MikroTik)",
              8728: "API MikroTik", 81: "câmera/DVR", 8080: "HTTP alternativo", 8081: "HTTP alternativo",
              8443: "HTTPS alternativo", 5900: "VNC", 1433: "MSSQL", 3306: "MySQL", 6379: "Redis", 9000: "DVR/PHP-FPM",
              60001: "JAWS (DVR)", 34567: "DVR Xiongmai", 49152: "UPnP", 5060: "SIP", 1900: "SSDP", 161: "SNMP"}
MAX_SRC = 400_000
MAX_DST = 600
MAX_SET = 3000

LOOKUP_COMMANDS = {
    "huawei": "display access-user ip-address {ip}",
    "juniper": "show subscribers address {ip} detail",
    "cisco": "show subscriber session filter ipv4-address {ip} detail",
    "mikrotik": "/ppp active print detail where address={ip}",
    "zte": "show subscriber ipv4 {ip}",
    "datacom": "show pppoe-server session ip {ip}",
    "linux": "accel-cmd show sessions match ip {ip}",
}
_CMD_BAD = re.compile(r"[\r\n;&`$<>\\]|\|\|")


def lookup_command(vendor: str, ip: str, overrides: Optional[dict] = None) -> Optional[str]:
    ipaddress.ip_address(ip)                               # só IP entra no comando
    tpl = ((overrides or {}).get(vendor) or "").strip() or LOOKUP_COMMANDS.get(vendor)
    return tpl.replace("{ip}", ip) if tpl else None


def check_lookup_template(tpl: str) -> str:
    tpl = (tpl or "").strip()
    if not tpl:
        return ""
    if len(tpl) > 200 or _CMD_BAD.search(tpl) or "{ip}" not in tpl or re.search(r"\{(?!ip\})", tpl):
        raise ValueError("Comando de consulta inválido: uma linha só, com {ip} no lugar do endereço")
    return tpl


_USER_RX = [re.compile(r"(?im)^\s*user[ -]?name\s*[:=]\s*\"?([^\s\"]+)"), re.compile(r"(?i)\bname=\"([^\"]+)\""),
            re.compile(r"(?im)^\s*(?:username|user|login|subscriber)\s*[:=]\s*\"?([^\s\",]+)"),
            re.compile(r"(?i)\buser(?:name)?\s+is\s+\"?([^\s\",]+)")]
_MAC_RX = re.compile(r"(?i)\b([0-9a-f]{2}(?:[:-][0-9a-f]{2}){5}|[0-9a-f]{4}[.-][0-9a-f]{4}[.-][0-9a-f]{4})\b")
_IF_RX = re.compile(r"(?im)^\s*(?:user )?(?:access[ -])?interface\s*[:=]\s*(\S+)")
_VLAN_RX = re.compile(r"(?im)(?:pe/ce[- ]vlan|qinq vlan/user vlan|vlan[ -]?id|svlan/cvlan)\s*[:=]\s*([\d/ -]+)")
_NONE_RX = re.compile(r"(?i)(no (such|online|matching)|not (found|exist|online)|does not exist|0 (subscribers?|sessions?)|"
                      r"total\s*:?\s*0\b|unrecognized command|invalid input|syntax error|unknown command|bad command)")


def parse_subscriber(output: str) -> dict:
    """Tira login, MAC, interface e VLAN da saída do BNG (formatos de Huawei, Juniper, Cisco, MikroTik…)."""
    out = {"username": "", "mac": "", "interface": "", "vlan": ""}
    text = output or ""
    for rx in _USER_RX:
        m = rx.search(text)
        if m and m.group(1) not in ("-", "--"):
            out["username"] = m.group(1)[:120]
            break
    m = _MAC_RX.search(text)
    if m:
        h = re.sub(r"[^0-9a-fA-F]", "", m.group(1)).lower()
        out["mac"] = ":".join(h[i:i + 2] for i in range(0, 12, 2))
    m = _IF_RX.search(text)
    if m:
        out["interface"] = m.group(1)[:80]
    m = _VLAN_RX.search(text)
    if m:
        out["vlan"] = re.sub(r"\s+", "", m.group(1))[:20]
    out["found"] = bool(out["username"] or out["mac"])
    if not out["found"] and (not text.strip() or _NONE_RX.search(text)):
        out["reason"] = "o BNG não tem sessão com este IP agora" if not re.search(
            r"(?i)unrecognized|invalid input|syntax error|unknown command|bad command", text) else "o BNG não aceitou o comando de consulta"
    return out


def parse_feed(text: str, limit: int = 200_000) -> List[str]:
    """Lista de IPs/redes de um feed (um por linha; ignora comentários e colunas extras)."""
    out = []
    for line in (text or "").splitlines():
        tok = line.split("#")[0].split(";")[0].strip().split()
        if not tok:
            continue
        t = tok[0].split(",")[0].strip('"')
        if re.match(r"^\d+\.\d+\.\d+\.\d+:\d+$", t):          # "IP:porta"
            t = t.split(":")[0]
        try:
            n = ipaddress.ip_network(t, strict=False)
        except ValueError:
            continue
        if n.is_private or n.prefixlen < 8:
            continue
        out.append(str(n))
        if len(out) >= limit:
            break
    return out


def clean_settings(body: dict) -> dict:
    cfg = dict(DEFAULTS)
    for k in ("enabled", "alert", "lookup"):
        if k in body:
            cfg[k] = bool(body[k])
    for k in NUMERIC:
        if body.get(k) is not None:
            try:
                cfg[k] = max(1, int(float(body[k])))
            except (TypeError, ValueError):
                raise ValueError(f"Valor inválido em {k}")
    exps = []
    for e in body.get("exporters") or []:
        try:
            exps.append(str(ipaddress.ip_address(str(e).strip())))
        except ValueError:
            raise ValueError(f"IP de BNG inválido: {e}")
    cfg["exporters"] = list(dict.fromkeys(exps))[:200]
    for k, cap in (("subscriber_prefixes", 500), ("c2_ips", 20000), ("ignore", 2000), ("ignore_dst", 2000)):
        nets = []
        for c in body.get(k) if k in body else DEFAULTS[k]:
            c = str(c).strip()
            if not c:
                continue
            try:
                nets.append(str(ipaddress.ip_network(c, strict=False)))
            except ValueError:
                raise ValueError(f"Endereço ou rede inválida: {c}")
        cfg[k] = list(dict.fromkeys(nets))[:cap]
    feeds = []
    for u in body.get("feeds") or []:
        u = str(u).strip()
        if u:
            if not re.match(r"^https://[A-Za-z0-9.\-]+(:\d+)?/[\w./%?=&+\-~]*$", u) or len(u) > 300:
                raise ValueError(f"Feed inválido (use um endereço https://…): {u}")
            feeds.append(u)
    cfg["feeds"] = list(dict.fromkeys(feeds))[:10]
    cmds = {}
    for v, tpl in (body.get("lookup_commands") or {}).items():
        if v in LOOKUP_COMMANDS or v == "other":
            t = check_lookup_template(str(tpl or ""))
            if t and t != LOOKUP_COMMANDS.get(v):
                cmds[v] = t
    cfg["lookup_commands"] = cmds
    cfg["devices"] = {e: str(d)[:64] for e, d in (body.get("devices") or {}).items() if e in cfg["exporters"] and d}
    return cfg


class Detector:
    def __init__(self):
        self.cfg = dict(DEFAULTS)
        self.on = False
        self.exporters: set = set()
        self.subs = PrefixSet()
        self.ignore = PrefixSet()
        self.quiet_dst = PrefixSet()                       # destinos que não contam como alvo de flood
        self.c2 = PrefixSet()
        self.c2_count = 0
        self.src: Dict[Tuple[int, int], dict] = {}
        self.open: Dict[Tuple[int, int, str], dict] = {}
        self.stats = {"flows": 0, "subs": 0}

    def configure(self, cfg: dict, feed_ips: List[str] = (), own_prefixes: List[str] = ()):
        self.cfg = {**DEFAULTS, **(cfg or {})}
        self.exporters = set(self.cfg["exporters"])
        self.subs = PrefixSet([(c, True) for c in self.cfg["subscriber_prefixes"]])
        self.ignore = PrefixSet([(c, True) for c in self.cfg["ignore"]])
        # tráfego para a própria rede (servidores, cache, IPTV, outros assinantes) não é ataque contra a internet
        self.quiet_dst = PrefixSet([(c, True) for c in list(self.cfg["ignore_dst"]) + list(own_prefixes or []) + list(self.cfg["subscriber_prefixes"])])
        nets = list(dict.fromkeys(list(self.cfg["c2_ips"]) + list(feed_ips or [])))
        self.c2 = PrefixSet([(c, True) for c in nets])
        self.c2_count = len(nets)
        self.on = bool(self.cfg["enabled"] and self.exporters and self.subs)
        if not self.on:
            self.src = {}

    def add(self, f: tuple):
        if f[F_EXP] not in self.exporters:
            return
        ver, src = f[F_VER], f[F_SRC]
        if not self.subs.match(ver, src):
            return                                          # tráfego de descida ou de fora das faixas de assinantes
        self.stats["flows"] += 1
        k = (ver, src)
        s = self.src.get(k)
        if s is None:
            if len(self.src) >= MAX_SRC:
                return
            s = self.src[k] = {"exp": f[F_EXP], "p": 0, "b": 0, "dst": {}, "scan": {}, "smtp": set(), "amp": 0,
                               "amp_port": {}, "c2": {}}
        b, p, pr, dst, dp, fl = f[F_BYTES], f[F_PKTS], f[F_PROTO], f[F_DST], f[F_DPORT], f[F_FLAGS]
        s["p"] += p
        s["b"] += b
        syn = pr == 6 and fl & 0x02 and not fl & 0x10
        d = s["dst"].get(dst)
        if d is None:
            if len(s["dst"]) < MAX_DST:
                d = s["dst"][dst] = [0, 0, 0, 0, {}, 0, 0]   # pacotes, bytes, UDP fora de 443 (bytes), SYN, portas, pacotes/bytes sem ACK
        if d is not None:
            d[0] += p
            d[1] += b
            if pr == 17 and dp != 443:
                d[2] += b
            if syn:
                d[3] += p
            if len(d[4]) < 20:
                d[4][dp] = d[4].get(dp, 0) + p
            if not (pr == 6 and fl & 0x10):                 # ACK de TCP é resposta de download, nunca flood
                d[5] += p
                d[6] += b
        if dp in SCAN_PORTS and (syn or pr == 17):
            st = s["scan"].get(dp)
            if st is None:
                st = s["scan"][dp] = set()
            if len(st) < MAX_SET:
                st.add(dst)
        if pr == 6 and dp == 25 and len(s["smtp"]) < MAX_SET:
            s["smtp"].add(dst)
        if pr == 17 and f[F_SPORT] in AMP_PORTS:
            s["amp"] += b
            s["amp_port"][f[F_SPORT]] = s["amp_port"].get(f[F_SPORT], 0) + b
        if self.c2 and self.c2.match(ver, dst):
            c = s["c2"].get(dst)
            if c is None:
                s["c2"][dst] = [p, dp]
            else:
                c[0] += p

    def _findings(self, ver: int, s: dict, span: float) -> List[dict]:
        cfg, out = self.cfg, []
        worst = None
        quiet = self.quiet_dst
        for dst, (p, b, udp, syn, ports, sp, sb) in s["dst"].items():
            if quiet and quiet.match(ver, dst):
                continue
            pps = p / span
            why = None
            if syn / span >= cfg["syn_pps"]:
                why = "SYN flood"
            elif sp / span >= cfg["ddos_pps"] and sb / sp <= 200:
                why = "flood de pacotes pequenos"
            elif udp * 8 / span >= cfg["ddos_bps"]:
                why = "flood UDP"
            if why and (worst is None or pps > worst[1]):
                worst = (dst, pps, b * 8 / span, why, ports)
        if worst:
            dst, pps, bps, why, ports = worst
            port = max(ports.items(), key=lambda kv: kv[1])[0] if ports else 0
            out.append({"kind": "ddos", "metric": pps, "unit": "pps", "bps": bps, "detail": why,
                        "targets": [[ip_str(ver, dst), round(pps), port]]})
        if s["scan"]:
            port, hosts = max(s["scan"].items(), key=lambda kv: len(kv[1]))
            if len(hosts) >= cfg["scan_hosts"]:
                out.append({"kind": "scan", "metric": len(hosts), "unit": "destinos/min", "port": port,
                            "detail": f"porta {port} ({SCAN_PORTS[port]})",
                            "ports": sorted(([p, len(h)] for p, h in s["scan"].items() if len(h) >= 5), key=lambda x: -x[1])[:6],
                            "targets": [[ip_str(ver, d), 0, port] for d in list(hosts)[:8]]})
        if len(s["smtp"]) >= cfg["spam_hosts"]:
            out.append({"kind": "spam", "metric": len(s["smtp"]), "unit": "servidores/min", "detail": "SMTP porta 25",
                        "targets": [[ip_str(ver, d), 0, 25] for d in list(s["smtp"])[:8]]})
        if s["amp"] * 8 / span >= cfg["amp_bps"]:
            port = max(s["amp_port"].items(), key=lambda kv: kv[1])[0]
            out.append({"kind": "amp", "metric": s["amp"] * 8 / span, "unit": "bps", "port": port,
                        "detail": f"respondendo como {AMP_PORTS.get(port, port)} (porta {port})", "targets": []})
        if s["c2"]:
            top = heapq.nlargest(8, s["c2"].items(), key=lambda kv: kv[1][0])
            out.append({"kind": "c2", "metric": len(s["c2"]), "unit": "servidores", "detail": "IP da lista de C2",
                        "targets": [[ip_str(ver, d), round(c[0] / span, 1), c[1]] for d, c in top]})
        return out

    def tick(self, span: float = 60.0, now: Optional[float] = None) -> List[Tuple[str, dict]]:
        """Fecha a janela e devolve eventos ('start'|'update'|'end', incidente)."""
        now = now or time.time()
        srcs, self.src = self.src, {}
        self.stats["subs"] = len(srcs)
        if not self.on and not self.open:
            return []
        cfg, events, hot = self.cfg, [], set()
        for (ver, ip), s in srcs.items():
            if self.ignore and self.ignore.match(ver, ip):
                continue
            for f in self._findings(ver, s, span):
                key = (ver, ip, f["kind"])
                hot.add(key)
                st = self.open.get(key)
                if st is None:
                    st = self.open[key] = {"ip": ip_str(ver, ip), "kind": f["kind"], "exporter": s["exp"], "start": now,
                                           "windows": 0, "cold": 0, "alerted": False, "peak": 0}
                st["windows"] += 1
                st["cold"] = 0
                st["last"] = now
                st["exporter"] = s["exp"]
                st["peak"] = max(st["peak"], f["metric"])
                st.update(cur=f["metric"], unit=f["unit"], detail=f["detail"], targets=f["targets"], port=f.get("port"),
                          ports=f.get("ports"), bps=f.get("bps"))
                if not st["alerted"] and st["windows"] >= (1 if f["kind"] == "c2" else cfg["min_windows"]):
                    st["alerted"] = True
                    events.append(("start", self._pub(st)))
                elif st["alerted"]:
                    events.append(("update", self._pub(st)))
        for key in list(self.open):
            if key in hot:
                continue
            st = self.open[key]
            st["cold"] += 1
            st["cur"] = 0
            if not st["alerted"] or st["cold"] >= cfg["end_windows"]:
                if st["alerted"]:
                    st["end"] = now
                    events.append(("end", self._pub(st)))
                del self.open[key]
        return events

    @staticmethod
    def _pub(st: dict) -> dict:
        return {k: st.get(k) for k in ("ip", "kind", "exporter", "start", "last", "end", "peak", "cur", "unit", "detail",
                                       "targets", "port", "ports", "bps", "windows")}
