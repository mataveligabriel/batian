"""Relatório HTML autônomo da análise de rede (abre em qualquer navegador; Ctrl+P salva em PDF)."""
from datetime import datetime
from html import escape as e
from typing import Optional

SEV = {"crit": ("Crítico", "#c62828"), "warn": ("Atenção", "#b26a00"), "info": ("Info", "#1f6fb2")}


def _bps(v: Optional[float]) -> str:
    if v is None:
        return "—"
    for u, d in (("G", 1e9), ("M", 1e6), ("K", 1e3)):
        if v >= d:
            return f"{v / d:.2f} {u}bps"
    return f"{v:.0f} bps"


def _spd(m) -> str:
    if not m:
        return "—"
    return f"{m / 1000:g}G" if m >= 1000 else f"{m:g}M"


def _svg(doc: dict) -> str:
    nodes = {n["id"]: n for n in doc.get("nodes", [])}
    links = doc["report"]["links"]
    if not nodes or not links:
        return ""
    xs = [n["x"] for n in nodes.values()]
    ys = [n["y"] for n in nodes.values()]
    minx, maxx, miny, maxy = min(xs) - 120, max(xs) + 120, min(ys) - 50, max(ys) + 50
    W, H = maxx - minx, maxy - miny
    out = [f'<svg viewBox="{minx} {miny} {W} {H}" width="100%" style="max-height:560px;background:#fafbfc;border:1px solid #dde3ea;border-radius:8px">']
    groups = {}
    for L in links:
        groups.setdefault(tuple(sorted((L["a"], L["b"]))), []).append(L["id"])
    for L in links:
        a, b = nodes.get(L["a"]), nodes.get(L["b"])
        if not a or not b:
            continue
        g = groups[tuple(sorted((L["a"], L["b"])))]
        i = g.index(L["id"])
        off = (i - (len(g) - 1) / 2) * 26 * (1 if L["a"] < L["b"] else -1)
        dx, dy = b["x"] - a["x"], b["y"] - a["y"]
        ln = (dx * dx + dy * dy) ** 0.5 or 1
        nx, ny = -dy / ln * off, dx / ln * off
        x1, y1, x2, y2 = a["x"] + nx, a["y"] + ny, b["x"] + nx, b["y"] + ny
        col = {"crit": "#c62828", "warn": "#d08a00"}.get(L.get("sev"), "#2f6fa8" if L.get("in_spf") else "#9aa5b1")
        dash = "" if L.get("in_spf") else ' stroke-dasharray="8 6"'
        out.append(f'<line x1="{x1:.0f}" y1="{y1:.0f}" x2="{x2:.0f}" y2="{y2:.0f}" stroke="{col}" stroke-width="4"{dash}/>')
        for c, t in ((L.get("cost_ab"), 0.24), (L.get("cost_ba"), 0.76)):
            if c is None:
                continue
            px, py = x1 + (x2 - x1) * t, y1 + (y2 - y1) * t
            w = 10 + len(str(c)) * 8
            out.append(f'<rect x="{px - w / 2:.0f}" y="{py - 10:.0f}" width="{w}" height="20" rx="4" fill="#fff" stroke="{col}"/>'
                       f'<text x="{px:.0f}" y="{py + 5:.0f}" text-anchor="middle" font-size="12" font-family="monospace" fill="#1d2733">{e(str(c))}</text>')
    for n in nodes.values():
        name = e((n.get("name") or "?")[:26])
        w = max(110, len(n.get("name") or "") * 7.5 + 20)
        out.append(f'<rect x="{n["x"] - w / 2:.0f}" y="{n["y"] - 18:.0f}" width="{w:.0f}" height="36" rx="7" fill="#fff" stroke="#44515f" stroke-width="1.5"/>'
                   f'<text x="{n["x"]:.0f}" y="{n["y"] + 5:.0f}" text-anchor="middle" font-size="13" font-weight="600" font-family="Arial" fill="#1d2733">{name}</text>')
    out.append("</svg>")
    return "".join(out)


def render(doc: dict) -> str:
    r = doc["report"]
    s = r["summary"]
    o = r.get("opts", {})
    when = doc.get("at", "")
    try:
        when = datetime.fromisoformat(when).astimezone().strftime("%d/%m/%Y %H:%M")
    except ValueError:
        pass
    score_col = "#2e7d32" if s["score"] >= 80 else "#b26a00" if s["score"] >= 50 else "#c62828"
    rows = lambda items: "".join(items) or '<tr><td colspan="9" class="muted">Nada a mostrar.</td></tr>'  # noqa: E731

    find = rows(
        f'<tr><td><span class="sev" style="background:{SEV[f["sev"]][1]}">{SEV[f["sev"]][0]}</span></td><td>{e(f["cat"])}</td>'
        f'<td><b>{e(f["title"])}</b><div class="muted">{e(f.get("detail") or "")}</div>'
        + (f'<div class="where">{e(f["where"])}</div>' if f.get("where") else "")
        + f'</td><td>{e(f.get("fix") or "")}</td></tr>' for f in r["findings"])
    ospf = rows(
        f'<tr><td>{e(L["a_name"])} <span class="muted">{e(L.get("a_if") or "")}</span></td>'
        f'<td>{e(L["b_name"])} <span class="muted">{e(L.get("b_if") or "")}</span></td><td>{_spd(L.get("capacity_mbps"))}</td>'
        f'<td class="num">{L["cost_ab"] if L["cost_ab"] is not None else "—"}</td><td class="num">{L["cost_ba"] if L["cost_ba"] is not None else "—"}</td>'
        f'<td class="num">{L.get("expected_cost") or "—"}</td><td>{e(str(L.get("adjacency") or "—"))}</td>'
        f'<td>{e(str(L.get("type_a") or "—"))}{"" if L.get("type_a") == L.get("type_b") else " / " + e(str(L.get("type_b")))}</td>'
        f'<td class="num">{L.get("ab_pct") if L.get("ab_pct") is not None else "—"}% / {L.get("ba_pct") if L.get("ba_pct") is not None else "—"}%</td></tr>'
        for L in r["links"])
    bgp = rows(
        f'<tr><td>{e(p["device"])}</td><td class="mono">{e(p["ip"])}</td><td>{"AS 4 bytes" if p.get("remote_as") == 23456 else "AS" + str(p.get("remote_as"))}</td>'
        f'<td><b style="color:{"#2e7d32" if p.get("state") == "established" else ("#8a94a0" if not p.get("admin_up") else "#c62828")}">'
        f'{e(str(p.get("state")))}{"" if p.get("admin_up") else " (shutdown)"}</b></td>'
        f'<td>{_dur(p.get("established_sec")) if p.get("state") == "established" else "—"}</td><td class="num">{p.get("transitions") if p.get("transitions") is not None else "—"}</td>'
        f'<td>{e(p.get("last_error") or "—")}</td></tr>' for p in r["bgp"])
    ifs = rows(
        f'<tr><td>{e(i["device"])}</td><td>{e(i.get("iface") or "")} <span class="muted">{e(i.get("alias") or "")}</span></td><td>{e(str(i.get("oper")))}</td>'
        f'<td class="num">{_n(i.get("in_err_ps"))}</td><td class="num">{_n(i.get("out_err_ps"))}</td>'
        f'<td class="num">{_n(i.get("in_disc_ps"))}</td><td class="num">{_n(i.get("out_disc_ps"))}</td><td class="num">{i.get("mtu") or "—"}</td></tr>'
        for i in r["ifaces"])
    scen = rows(
        f'<tr><td>{e(x["name"])}</td><td>{"<b style=color:#c62828>" + e(", ".join(x["isolated_nodes"])) + "</b>" if x["isolated_nodes"] else "ninguém"}</td>'
        f'<td>{e(x.get("worst_link") or "—")}</td>'
        f'<td class="num"><b style="color:{"#c62828" if (x.get("worst_pct") or 0) >= 100 else "#b26a00" if (x.get("worst_pct") or 0) >= o.get("util_crit", 90) else "#1d2733"}">'
        f'{_pct(x.get("worst_pct"))}</b></td></tr>' for x in r["scenarios"])
    devs = rows(
        f'<tr><td>{e(d["name"])}</td><td>{"<span style=color:#c62828>sem SNMP: " + e(d.get("error") or "") + "</span>" if not d.get("ok") else e(d.get("sys_descr") or "")}</td>'
        f'<td class="mono">{e(d.get("router_id") or "—")}</td><td class="num">{d.get("ospf_full", "—")}/{d.get("ospf_nbrs", "—")}</td>'
        f'<td class="num">{d.get("bgp_up", "—")}/{d.get("bgp_peers", "—")}</td></tr>' for d in r["devices"])
    ref = o.get("ref_used_mbps")
    mp = r.get("mpls")
    mpls_html = ""
    if mp:
        ms = mp["summary"]
        yes = lambda v: '<b style="color:#2e7d32">sim</b>' if v else ('<b style="color:#c62828">NÃO</b>' if v is False else "—")  # noqa: E731
        ldp_rows = rows(
            f'<tr><td>{e(L["a_name"])} ↔ {e(L["b_name"])}</td><td>{yes(L.get("ldp_a"))}</td><td>{yes(L.get("ldp_b"))}</td>'
            f'<td>{e(L.get("ldp_session") or "—")}</td><td>{"sim" if L.get("in_spf") else "não"}</td></tr>' for L in r["links"])
        svc = []
        for d in mp["devices"]:
            secs = d.get("sections") or {}
            for vc in (secs.get("vpws") or {}).get("items", []):
                svc.append((d["name"], "VPWS", f'VC {vc.get("vcid")}' + (f' · {vc["iface"]}' if vc.get("iface") else ""), vc.get("peer"), vc.get("state"),
                            "AC down" if vc.get("ac") == "down" else ""))
            for v in (secs.get("vpls") or {}).get("items", []):
                svc.append((d["name"], "VPLS", v.get("name"), "", v.get("state"), f'PWs {v["pws_up"]}/{v["pws"]}' if v.get("pws") else ""))
            for v in (secs.get("vrf") or {}).get("items", []):
                svc.append((d["name"], "L3VPN", v.get("name"), v.get("rd") or "", "—", "" if v.get("routes") is None else f'{v["routes"]} rotas'))
            for p in (secs.get("bgp_vpn") or {}).get("items", []):
                svc.append((d["name"], "MP-BGP", "vpnv4", p.get("peer"), p.get("state"), "" if p.get("prefixes") is None else f'{p["prefixes"]} prefixos'))
        col = lambda st: "#2e7d32" if st == "up" else "#c62828" if st in ("down", "degraded") else "#44515f"  # noqa: E731
        svc_rows = rows(f'<tr><td>{e(a)}</td><td>{e(b)}</td><td>{e(str(c or ""))}</td><td class="mono">{e(str(dd or ""))}</td>'
                        f'<td><b style="color:{col(st)}">{e(str(st or ""))}</b></td><td>{e(ex)}</td></tr>' for a, b, c, dd, st, ex in svc)
        errs = "".join(f'<div class="muted" style="color:#c62828">{e(d["name"])}: {e(d["error"])}</div>' for d in mp["devices"] if d.get("error"))
        mpls_html = f"""<h2>MPLS (LDP, VPWS, VPLS, L3VPN)</h2>
<div class="cards">
 <div class="card"><div class="l">Sessões LDP up</div><div class="v">{ms["ldp_up"]}/{ms["ldp_total"]}</div></div>
 <div class="card"><div class="l">Enlaces OSPF sem LDP</div><div class="v" style="color:{"#c62828" if ms["links_no_ldp"] else "#1d2733"}">{ms["links_no_ldp"]}</div></div>
 <div class="card"><div class="l">VPWS up</div><div class="v">{ms["vc_up"]}/{ms["vc_total"]}</div></div>
 <div class="card"><div class="l">VPLS up</div><div class="v">{ms["vsi_up"]}/{ms["vsi_total"]}</div></div>
 <div class="card"><div class="l">VRFs</div><div class="v">{ms["vrfs"]}</div></div>
 <div class="card"><div class="l">MP-BGP VPN up</div><div class="v">{ms["bgp_vpn_up"]}/{ms["bgp_vpn_total"]}</div></div>
</div>{errs}
<h2 style="font-size:15px">LDP por enlace</h2>
<table><tr><th>Enlace</th><th>LDP em A</th><th>LDP em B</th><th>Sessão LDP</th><th>Roteia (OSPF)</th></tr>{ldp_rows}</table>
<h2 style="font-size:15px">Serviços</h2>
<table><tr><th>Equipamento</th><th>Tipo</th><th>Serviço</th><th>Peer / RD</th><th>Estado</th><th></th></tr>{svc_rows}</table>
<div class="note">MPLS lido pela CLI (SSH) com os comandos configurados por fabricante.</div>"""
    return f"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Análise de rede — {e(doc.get("map_name", ""))}</title>
<style>
body{{font-family:Arial,Helvetica,sans-serif;color:#1d2733;margin:0;background:#fff}}
.wrap{{max-width:1180px;margin:0 auto;padding:28px 24px}}
h1{{margin:0;font-size:26px}} h2{{font-size:18px;margin:28px 0 10px;border-bottom:2px solid #e3e8ee;padding-bottom:6px}}
.muted{{color:#6b7785;font-size:12px}} .where{{font-family:monospace;font-size:11px;color:#44515f;margin-top:2px}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-top:16px}}
.card{{border:1px solid #dde3ea;border-radius:8px;padding:10px 12px}} .card .v{{font-size:24px;font-weight:700}} .card .l{{font-size:11px;color:#6b7785;text-transform:uppercase;letter-spacing:.06em}}
table{{width:100%;border-collapse:collapse;font-size:13px}} th{{text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:#6b7785;border-bottom:1px solid #dde3ea;padding:6px}}
td{{border-bottom:1px solid #eef1f4;padding:6px;vertical-align:top}} .num{{text-align:right;font-variant-numeric:tabular-nums}} .mono{{font-family:monospace}}
.sev{{color:#fff;font-size:11px;font-weight:700;padding:2px 7px;border-radius:10px;white-space:nowrap}}
.note{{background:#f5f7fa;border-left:3px solid #9aa5b1;padding:8px 12px;font-size:12px;color:#44515f;margin-top:10px}}
@media print{{.wrap{{padding:0}} h2{{break-after:avoid}} tr{{break-inside:avoid}}}}
</style></head><body><div class="wrap">
<div class="muted">Bastion · Relatório de análise de rede</div>
<h1>{e(doc.get("map_name", "Mapa"))}</h1>
<div class="muted">Gerado em {e(when)} · {s["devices_ok"]}/{s["devices"]} equipamentos lidos por SNMP · coleta {doc.get("duration_sec", "?")}s</div>
<div class="cards">
 <div class="card"><div class="l">Saúde</div><div class="v" style="color:{score_col}">{s["score"]}/100</div></div>
 <div class="card"><div class="l">Críticos</div><div class="v" style="color:#c62828">{s["crit"]}</div></div>
 <div class="card"><div class="l">Atenção</div><div class="v" style="color:#b26a00">{s["warn"]}</div></div>
 <div class="card"><div class="l">Informativos</div><div class="v" style="color:#1f6fb2">{s["info"]}</div></div>
 <div class="card"><div class="l">Enlaces OSPF</div><div class="v">{s["ospf_links"]}/{s["links"]}</div></div>
 <div class="card"><div class="l">BGP caídas</div><div class="v">{s["bgp_down"]}/{s["bgp_sessions"]}</div></div>
</div>
<h2>Topologia e custos OSPF</h2>
<div class="muted">Cada enlace mostra o custo de saída de cada ponta (o número perto do equipamento é o custo dele para aquele enlace).
Vermelho = problema crítico · laranja = atenção · tracejado = fora do cálculo de rotas (sem OSPF/adjacência).</div>
{_svg(doc)}
<h2>Achados e recomendações</h2>
<table><tr><th>Severidade</th><th>Área</th><th>O que foi encontrado</th><th>Recomendação</th></tr>{find}</table>
<h2>OSPF por enlace</h2>
<div class="muted">Referência de banda {"descoberta na rede" if o.get("ref_inferred") else "definida"}: ~{_spd(ref)} (custo esperado = referência ÷ banda).</div>
<table><tr><th>Ponta A</th><th>Ponta B</th><th>Banda</th><th class="num">Custo A→B</th><th class="num">Custo B→A</th><th class="num">Esperado</th><th>Adjacência</th><th>Tipo</th><th class="num">Uso A→B / B→A</th></tr>{ospf}</table>
<h2>Cenários de falha</h2>
<div class="muted">Para cada enlace: se ele cair, quem fica isolado e qual enlace mais sofre com o tráfego desviado.</div>
<table><tr><th>Se cair</th><th>Isolados</th><th>Enlace mais carregado depois</th><th class="num">Uso estimado</th></tr>{scen}</table>
<div class="note">Estimativa de 1ª ordem: o tráfego medido em cada enlace é refeito pelo menor caminho OSPF (com ECMP) sem ele.
Não há matriz de tráfego real (NetFlow), então trate como ordem de grandeza.</div>
<h2>BGP</h2>
<table><tr><th>Equipamento</th><th>Peer</th><th>AS</th><th>Estado</th><th>Estabelecida há</th><th class="num">Quedas</th><th>Último erro</th></tr>{bgp}</table>
{mpls_html}
<h2>Interfaces (erros, descartes, enlaces do mapa)</h2>
<table><tr><th>Equipamento</th><th>Interface</th><th>Status</th><th class="num">Erros entrada/s</th><th class="num">Erros saída/s</th><th class="num">Descartes entrada/s</th><th class="num">Descartes saída/s</th><th class="num">MTU</th></tr>{ifs}</table>
<h2>Equipamentos</h2>
<table><tr><th>Nome</th><th>Sistema</th><th>Router-ID</th><th class="num">OSPF FULL/vizinhos</th><th class="num">BGP up/total</th></tr>{devs}</table>
<div class="note">Coleta por SNMP v2c com MIBs padrão (OSPF-MIB, BGP4-MIB, IF-MIB, IP-MIB). BGP4-MIB mostra só sessões IPv4 da instância principal;
sessões IPv6/VPN e contagem de prefixos dependem de MIBs do fabricante. Taxas de erro medidas em {o.get("sample_sec", 10)}s.</div>
</div></body></html>"""


def _pct(v) -> str:
    return "" if v is None else f"{v:.0f}%"


def _n(v) -> str:
    if v is None:
        return "—"
    return "0" if v == 0 else f"{v:.1f}"


def _dur(sec) -> str:
    if not isinstance(sec, int):
        return "—"
    if sec < 3600:
        return f"{sec // 60} min"
    if sec < 86400:
        return f"{sec // 3600} h"
    return f"{sec // 86400} d"
