import React, { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, formatApiError } from "@/lib/api";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { toast } from "sonner";
import { Radar, RefreshCw, Loader2, Search, Save, Plus, Trash2, ArrowUpRight, ArrowDownLeft, AlertTriangle, CheckCircle2, ShieldBan, Copy, KeyRound } from "lucide-react";

const CAT_CLS = { c2: "text-red-300 border-red-500/40", malware: "text-red-300 border-red-500/40", compromised: "text-orange-300 border-orange-500/40",
  hijacked: "text-orange-300 border-orange-500/40", attacker: "text-amber-300 border-amber-500/40", scanner: "text-sky-300 border-sky-500/40",
  tor: "text-violet-300 border-violet-500/40", custom: "text-amber-300 border-amber-500/40" };
const fmt = (s) => (s ? new Date(s).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—");
const bytes = (b) => (b >= 1e9 ? `${(b / 1e9).toFixed(1)} GB` : b >= 1e6 ? `${(b / 1e6).toFixed(1)} MB` : b >= 1e3 ? `${(b / 1e3).toFixed(0)} kB` : `${b} B`);
const lbl = "text-xs text-slate-400 mb-1 block";
const sel = "h-9 rounded-md bg-sunken border border-line px-2 text-sm text-slate-200";
const lines = (t) => t.split(/[\s,;]+/).filter(Boolean);
// tudo que o PUT espera, a partir do que veio do GET
const payload = (c, extra = {}) => ({ enabled: c.enabled, feeds: c.feeds, custom: c.custom.map(({ status, ...x }) => x), alert_out: c.alert_out,
  alert_in_min_flows: Number(c.alert_in_min_flows) || 0, retention_days: Number(c.retention_days) || 30, manual: c.manual, ignore: c.ignore,
  out_min_pkts: Number(c.out_min_pkts) || 1, auto_resolve_hours: Number(c.auto_resolve_hours) || 0, ignore_local: c.ignore_local,
  block_categories: c.block_categories, ...extra });
const copy = (t) => { try { navigator.clipboard.writeText(t); toast.success("Copiado"); } catch (e) { toast.error("Não consegui copiar"); } };

function Cat({ c, cats }) {
  return <span className={`text-[11px] px-1.5 py-0.5 rounded border whitespace-nowrap ${CAT_CLS[c] || CAT_CLS.custom}`}>{cats?.[c] || c}</span>;
}
const Dir = ({ d }) => (d === "out"
  ? <span className="inline-flex items-center gap-1 text-red-300 text-xs whitespace-nowrap"><ArrowUpRight className="w-3.5 h-3.5" />saída</span>
  : <span className="inline-flex items-center gap-1 text-slate-400 text-xs whitespace-nowrap"><ArrowDownLeft className="w-3.5 h-3.5" />entrada</span>);

function Hits({ cats, onLookup }) {
  const [rows, setRows] = useState(null);
  const [sum, setSum] = useState(null);
  const [f, setF] = useState({ direction: "out", category: "", q: "", days: 7 });
  const load = useCallback(() => {
    api.get("/soc/ti/hits", { params: f }).then(r => setRows(r.data)).catch(e => toast.error(formatApiError(e)));
    api.get("/soc/ti/summary", { params: { days: f.days } }).then(r => setSum(r.data)).catch(() => {});
  }, [f]);
  useEffect(() => { const t = setTimeout(load, 300); return () => clearTimeout(t); }, [load]);
  const outTotal = (sum?.by_category || []).filter(c => c.dir === "out").reduce((a, c) => a + c.local, 0);
  return (
    <div className="space-y-4" data-testid="ti-hits">
      {sum && (
        <div className="grid gap-3 md:grid-cols-2">
          <Card className="bg-surface border-line p-4">
            <div className="text-sm text-slate-200 mb-2">Seus hosts falando com IPs maliciosos <span className="text-slate-500">({f.days} dia{f.days > 1 ? "s" : ""})</span></div>
            {sum.top_hosts_out.length === 0 ? <div className="text-sm text-emerald-300 flex items-center gap-1.5"><CheckCircle2 className="w-4 h-4" />Nenhum.</div> : (
              <div className="divide-y divide-line">{sum.top_hosts_out.slice(0, 8).map(h => (
                <button key={h.ip} onClick={() => onLookup(h.ip)} className="w-full flex items-center gap-3 py-1.5 text-sm text-left hover:bg-white/[0.03]" data-testid={`ti-top-${h.ip}`}>
                  <span className="font-mono text-slate-100 w-36">{h.ip}</span>
                  <span className="flex gap-1 flex-wrap flex-1">{h.categories.map(c => <Cat key={c} c={c} cats={cats} />)}</span>
                  <span className="text-xs text-slate-500">{h.bad} IP(s) · {h.flows} flows</span>
                </button>))}</div>
            )}
          </Card>
          <Card className="bg-surface border-line p-4">
            <div className="text-sm text-slate-200 mb-2">Por categoria</div>
            <div className="space-y-1">{sum.by_category.map(c => (
              <div key={c.dir + c.category} className="flex items-center gap-3 text-sm"><Dir d={c.dir} /><Cat c={c.category} cats={cats} />
                <span className="text-xs text-slate-400 ml-auto">{c.bad} IP(s) ruins · {c.local} seus · {c.flows} flows · {bytes(c.bytes)}</span></div>))}
              {sum.by_category.length === 0 && <div className="text-sm text-slate-500">Nada no período.</div>}
            </div>
            {outTotal > 0 && <div className="text-[11px] text-amber-300 mt-2"><AlertTriangle className="w-3 h-3 inline mr-1" />Saída para C2/malware costuma ser cliente infectado: veja também <b>Flow → Botnet</b>.</div>}
          </Card>
        </div>
      )}
      <Card className="bg-surface border-line">
        <div className="flex flex-wrap gap-2 px-4 py-3 border-b border-line items-center">
          <div className="flex gap-1 p-1 bg-sunken border border-line rounded-md text-xs">
            {[["out", "Saída (seus hosts)"], ["in", "Entrada"], ["", "Tudo"]].map(([k, l]) => <button key={k} onClick={() => setF({ ...f, direction: k })} className={`px-3 h-7 rounded ${f.direction === k ? "bg-brand/20 text-brand-soft" : "text-slate-400"}`} data-testid={`ti-dir-${k || "all"}`}>{l}</button>)}
          </div>
          <select value={f.category} onChange={e => setF({ ...f, category: e.target.value })} className={sel} data-testid="ti-cat"><option value="">todas as categorias</option>{Object.entries(cats || {}).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
          <select value={f.days} onChange={e => setF({ ...f, days: Number(e.target.value) })} className={sel}>{[1, 7, 30].map(d => <option key={d} value={d}>{d} dia{d > 1 ? "s" : ""}</option>)}</select>
          <Input value={f.q} onChange={e => setF({ ...f, q: e.target.value })} placeholder="IP (início)" className="bg-sunken border-line h-9 w-44 font-mono" data-testid="ti-q" />
          <Button size="sm" variant="ghost" onClick={load} className="text-slate-400"><RefreshCw className="w-4 h-4" /></Button>
        </div>
        {rows && rows.length === 0 && <div className="p-6 text-sm text-slate-500 text-center">Nenhuma ocorrência com esse filtro.</div>}
        <div className="overflow-x-auto"><table className="w-full text-sm">
          {rows && rows.length > 0 && <thead className="text-xs text-slate-500"><tr><th className="text-left px-4 py-2">Sentido</th><th className="text-left px-2">Seu IP</th><th className="text-left px-2">IP malicioso</th><th className="text-left px-2">Lista</th><th className="text-left px-2">Portas</th><th className="text-right px-2">Flows</th><th className="text-right px-2">Volume</th><th className="text-left px-2">Última vez</th></tr></thead>}
          <tbody className="divide-y divide-line">{(rows || []).map(h => (
            <tr key={h.key} data-testid={`ti-row-${h.bad_ip}`}>
              <td className="px-4 py-1.5"><Dir d={h.dir} /></td>
              <td className="px-2"><button onClick={() => onLookup(h.local_ip)} className="font-mono text-slate-200 hover:underline">{h.local_ip}</button></td>
              <td className="px-2"><button onClick={() => onLookup(h.bad_ip)} className="font-mono text-red-200 hover:underline">{h.bad_ip}</button></td>
              <td className="px-2"><Cat c={h.category} cats={cats} /></td>
              <td className="px-2 font-mono text-[11px] text-slate-400">{(h.ports || []).join(" ")}{h.replied ? <span className="ml-1.5 font-sans text-[10px] px-1 rounded border border-sky-500/40 text-sky-300" title="O seu host respondeu (SYN-ACK/RST/ICMP/DNS) — é varredura vinda de fora, não iniciativa dele">respondeu {h.replied}×</span> : null}</td>
              <td className="px-2 text-right font-mono text-slate-300">{h.flows}</td>
              <td className="px-2 text-right font-mono text-slate-400">{bytes(h.bytes)}</td>
              <td className="px-2 text-xs text-slate-500 whitespace-nowrap">{fmt(h.last)}</td>
            </tr>))}</tbody>
        </table></div>
      </Card>
    </div>
  );
}

function Lookup({ ip0, cats }) {
  const [ip, setIp] = useState(ip0 || "");
  const [r, setR] = useState(null);
  const [busy, setBusy] = useState(false);
  const go = useCallback(async (v) => {
    const q = (v ?? ip).trim(); if (!q) return;
    setBusy(true);
    try { setR((await api.get("/soc/ti/lookup", { params: { ip: q } })).data); } catch (e) { toast.error(formatApiError(e)); } finally { setBusy(false); }
  }, [ip]);
  useEffect(() => { if (ip0) { setIp(ip0); go(ip0); } }, [ip0]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <div className="space-y-3" data-testid="ti-lookup">
      <Card className="bg-surface border-line p-4 flex gap-2">
        <Input value={ip} onChange={e => setIp(e.target.value)} onKeyDown={e => e.key === "Enter" && go()} placeholder="IP para consultar" className="bg-sunken border-line h-9 w-72 font-mono" data-testid="ti-ip" />
        <Button onClick={() => go()} disabled={busy} className="bg-brand hover:bg-brand-strong text-onbrand h-9" data-testid="ti-go">{busy ? <Loader2 className="w-4 h-4 mr-1 animate-spin" /> : <Search className="w-4 h-4 mr-1" />}Consultar</Button>
      </Card>
      {r && (
        <Card className="bg-surface border-line p-4 space-y-3" data-testid="ti-result">
          <div className="flex flex-wrap items-center gap-3">
            <span className="font-mono text-lg text-slate-100">{r.ip}</span>
            {r.geo?.cc && <span className="text-sm text-slate-400">{r.geo.cc} · AS{r.geo.asn} {r.geo.asn_name}</span>}
            {r.feeds.length ? <span className="text-sm text-red-300 flex items-center gap-1"><AlertTriangle className="w-4 h-4" />Em {r.feeds.length} lista(s)</span>
              : <span className="text-sm text-emerald-300 flex items-center gap-1"><CheckCircle2 className="w-4 h-4" />Não está em nenhuma lista{r.loaded ? "" : " (listas ainda não carregadas)"}</span>}
          </div>
          {r.feeds.length > 0 && <div className="flex flex-wrap gap-2">{r.feeds.map(f => <span key={f.id} className="flex items-center gap-1.5 text-sm text-slate-300"><Cat c={f.category} cats={cats} />{f.name}</span>)}</div>}
          <div className="text-sm text-slate-300">{r.hits.length ? `${r.hits.length} ocorrência(s) no flow:` : "Nenhuma ocorrência no flow."}</div>
          {r.hits.length > 0 && <div className="divide-y divide-line text-sm">{r.hits.slice(0, 50).map(h => (
            <div key={h.key} className="flex flex-wrap items-center gap-3 py-1"><span className="text-xs text-slate-500 w-24">{h.day}</span><Dir d={h.dir} />
              <span className="font-mono text-slate-200">{h.local_ip}</span><span className="text-slate-500">↔</span><span className="font-mono text-red-200">{h.bad_ip}</span>
              <span className="text-xs text-slate-500">{h.flows} flows · {(h.ports || []).join(" ")}</span></div>))}</div>}
        </Card>
      )}
    </div>
  );
}

function Lists() {
  const [c, setC] = useState(null);
  const [busy, setBusy] = useState("");
  const [manual, setManual] = useState("");
  const [ignore, setIgnore] = useState("");
  const [ignLocal, setIgnLocal] = useState("");
  const load = useCallback(() => api.get("/soc/ti/settings").then(r => { setC(r.data); setManual(r.data.manual.join("\n")); setIgnore(r.data.ignore.join("\n")); setIgnLocal((r.data.ignore_local || []).join("\n")); }).catch(e => toast.error(formatApiError(e))), []);
  useEffect(() => { load(); }, [load]);
  if (!c) return null;
  const toggle = (id) => setC({ ...c, feeds: c.feeds.includes(id) ? c.feeds.filter(x => x !== id) : [...c.feeds, id] });
  const save = async () => {
    setBusy("save");
    try {
      await api.put("/soc/ti/settings", payload(c, { manual: lines(manual), ignore: lines(ignore), ignore_local: lines(ignLocal) }));
      toast.success("Salvo — baixando as listas novas"); setTimeout(load, 3000);
    } catch (e) { toast.error(formatApiError(e)); } finally { setBusy(""); }
  };
  const refresh = async () => { setBusy("ref"); try { await api.post("/soc/ti/refresh"); toast.success("Listas atualizadas"); load(); } catch (e) { toast.error(formatApiError(e)); } finally { setBusy(""); } };
  const St = ({ s }) => (!s ? <span className="text-slate-500">ainda não baixada</span> : s.error
    ? <span className="text-red-300" title={s.error}>erro: {s.error.slice(0, 60)}{s.count ? ` (usando ${s.count.toLocaleString("pt-BR")} de antes)` : ""}</span>
    : <span className="text-slate-400">{(s.count || 0).toLocaleString("pt-BR")} redes · {fmt(s.ok_at || s.at)}</span>);
  return (
    <div className="space-y-4 max-w-4xl" data-testid="ti-lists">
      {!c.flow_own_prefixes && <Card className="bg-amber-500/5 border-amber-500/40 p-3 text-sm text-amber-200"><AlertTriangle className="w-4 h-4 inline mr-1.5" />Cadastre os <b>prefixos próprios</b> em Análise de Flow → Configuração: é com eles que o BastiON sabe qual lado do flow é seu (saída × entrada).</Card>}
      <Card className="bg-surface border-line p-4 flex flex-wrap items-center gap-3">
        <label className="flex items-center gap-2 text-sm text-slate-200"><Switch checked={c.enabled} onCheckedChange={v => setC({ ...c, enabled: v })} data-testid="ti-enabled" /> Cruzar o flow com as listas</label>
        <span className="text-xs text-slate-400 flex-1">{c.networks.toLocaleString("pt-BR")} redes carregadas · atualiza a cada 6 h</span>
        <Button size="sm" variant="outline" onClick={refresh} disabled={!!busy} className="border-line bg-transparent" data-testid="ti-refresh">{busy === "ref" ? <Loader2 className="w-4 h-4 mr-1 animate-spin" /> : <RefreshCw className="w-4 h-4 mr-1" />}Atualizar agora</Button>
        <Button size="sm" onClick={save} disabled={!!busy} className="bg-brand hover:bg-brand-strong text-onbrand" data-testid="ti-save">{busy === "save" ? <Loader2 className="w-4 h-4 mr-1 animate-spin" /> : <Save className="w-4 h-4 mr-1" />}Salvar</Button>
      </Card>
      <Card className="bg-surface border-line divide-y divide-line">
        {c.catalog.map(f => (
          <div key={f.id} className="flex flex-wrap items-center gap-3 px-4 py-2.5" data-testid={`ti-feed-${f.id}`}>
            <Switch checked={c.feeds.includes(f.id)} onCheckedChange={() => toggle(f.id)} />
            <div className="min-w-0 flex-1"><div className="text-sm text-slate-100">{f.name}</div><div className="text-[11px] font-mono text-slate-500 truncate">{f.url}</div></div>
            <Cat c={f.category} cats={c.categories} />
            <div className="text-xs w-64 text-right"><St s={f.status} /></div>
          </div>
        ))}
      </Card>
      <Card className="bg-surface border-line p-4 space-y-2">
        <div className="flex items-center justify-between"><div className="text-sm font-semibold text-slate-200">Listas próprias (URL)</div>
          <Button size="sm" variant="ghost" onClick={() => setC({ ...c, custom: [...c.custom, { name: "", url: "", category: "custom" }] })} className="text-slate-300" data-testid="ti-custom-add"><Plus className="w-4 h-4 mr-1" />Lista</Button></div>
        {c.custom.map((x, i) => (
          <div key={i} className="flex flex-wrap gap-2 items-center">
            <Input value={x.name} onChange={e => setC({ ...c, custom: c.custom.map((y, j) => (j === i ? { ...y, name: e.target.value } : y)) })} placeholder="nome" className="bg-sunken border-line h-9 w-44" />
            <Input value={x.url} onChange={e => setC({ ...c, custom: c.custom.map((y, j) => (j === i ? { ...y, url: e.target.value } : y)) })} placeholder="https://… (um IP/CIDR por linha)" className="bg-sunken border-line h-9 flex-1 min-w-[16rem] font-mono" data-testid={`ti-custom-url-${i}`} />
            <select value={x.category} onChange={e => setC({ ...c, custom: c.custom.map((y, j) => (j === i ? { ...y, category: e.target.value } : y)) })} className={sel}>{Object.entries(c.categories).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
            <span className="text-xs w-44"><St s={x.status} /></span>
            <button onClick={() => setC({ ...c, custom: c.custom.filter((_, j) => j !== i) })} className="text-slate-500 hover:text-red-300"><Trash2 className="w-4 h-4" /></button>
          </div>
        ))}
      </Card>
      <div className="grid gap-4 md:grid-cols-2">
        <Card className="bg-surface border-line p-4"><label className={lbl}>IPs/redes maliciosos (lista manual, um por linha)</label>
          <textarea value={manual} onChange={e => setManual(e.target.value)} rows={5} className="w-full rounded-md bg-sunken border border-line p-2 font-mono text-sm text-slate-200" data-testid="ti-manual" /></Card>
        <Card className="bg-surface border-line p-4"><label className={lbl}>IPs remotos que nunca alertam (falso positivo: CDN, parceiro, scanner autorizado)</label>
          <textarea value={ignore} onChange={e => setIgnore(e.target.value)} rows={5} className="w-full rounded-md bg-sunken border border-line p-2 font-mono text-sm text-slate-200" data-testid="ti-ignore" /></Card>
      </div>
      <Card className="bg-surface border-line p-4 space-y-3" data-testid="ti-noise">
        <div className="text-sm font-semibold text-slate-200">Reduzir falso positivo</div>
        <p className="text-xs text-slate-400">Resposta do seu host a uma varredura (SYN-ACK, RST, ICMP, resposta de DNS) já não conta como saída. Além disso: um incidente por host e categoria (não por IP remoto), saída fraca só com volume, e o incidente fecha sozinho quando o tráfego para.</p>
        <div className="grid gap-3 sm:grid-cols-3">
          <div><label className={lbl}>Saída para DROP/atacante/comprometido: alertar a partir de N pacotes no dia (C2 e malware: sempre)</label><Input type="number" value={c.out_min_pkts} onChange={e => setC({ ...c, out_min_pkts: e.target.value })} className="bg-sunken border-line h-9" data-testid="ti-minpkts" /></div>
          <div><label className={lbl}>Fechar o incidente após N horas sem tráfego (0 = não fecha)</label><Input type="number" value={c.auto_resolve_hours} onChange={e => setC({ ...c, auto_resolve_hours: e.target.value })} className="bg-sunken border-line h-9" data-testid="ti-autoresolve" /></div>
          <div><label className={lbl}>Seus hosts que nunca alertam (honeypot, scanner seu, servidor exposto de propósito)</label>
            <textarea value={ignLocal} onChange={e => setIgnLocal(e.target.value)} rows={3} className="w-full rounded-md bg-sunken border border-line p-2 font-mono text-sm text-slate-200" data-testid="ti-ignore-local" /></div>
        </div>
      </Card>
      <Card className="bg-surface border-line p-4 grid gap-3 sm:grid-cols-3 items-end">
        <label className="flex items-center gap-2 text-sm text-slate-300"><Switch checked={c.alert_out} onCheckedChange={v => setC({ ...c, alert_out: v })} /> Incidente quando um host seu fala com C2/malware/comprometido</label>
        <div><label className={lbl}>Entrada: incidente a partir de N flows/dia (0 = nunca)</label><Input type="number" value={c.alert_in_min_flows} onChange={e => setC({ ...c, alert_in_min_flows: e.target.value })} className="bg-sunken border-line h-9" /></div>
        <div><label className={lbl}>Guardar ocorrências por (dias)</label><Input type="number" value={c.retention_days} onChange={e => setC({ ...c, retention_days: e.target.value })} className="bg-sunken border-line h-9" /></div>
      </Card>
    </div>
  );
}

function Block() {
  const [c, setC] = useState(null);
  const [pv, setPv] = useState(null);
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => {
    api.get("/soc/ti/settings").then(r => setC(r.data)).catch(e => toast.error(formatApiError(e)));
    api.get("/soc/ti/blocklist/preview").then(r => setPv(r.data)).catch(() => {});
  }, []);
  useEffect(() => { load(); }, [load]);
  if (!c) return null;
  const toggle = (k) => setC({ ...c, block_categories: c.block_categories.includes(k) ? c.block_categories.filter(x => x !== k) : [...c.block_categories, k] });
  const save = async () => {
    setBusy(true);
    try { await api.put("/soc/ti/settings", payload(c)); toast.success("Salvo"); load(); } catch (e) { toast.error(formatApiError(e)); } finally { setBusy(false); }
  };
  const newToken = async () => { try { const r = await api.post("/soc/ti/block-token"); setC({ ...c, block_token: r.data.token }); toast.success("Token novo — atualize o script nos roteadores"); } catch (e) { toast.error(formatApiError(e)); } };
  const base = `${window.location.origin}/api/soc/ti/blocklist/${c.block_token}`;
  const ros = `/system script add name=bastion-ti policy=read,write,test source={
  /tool fetch url="${base}?fmt=routeros" dst-path=bastion-ti.rsc
  :delay 3s
  /import file-name=bastion-ti.rsc
}
/system scheduler add name=bastion-ti interval=6h start-time=startup on-event="/system script run bastion-ti"
/ip firewall raw add chain=prerouting in-interface-list=WAN src-address-list=bastion-ti action=drop comment="BastiON threat intel (entrada)"
/ip firewall raw add chain=prerouting dst-address-list=bastion-ti action=drop comment="BastiON threat intel (saida)"`;
  const SAFE = { hijacked: "sem falso positivo prático (Spamhaus pede para descartar)", c2: "servidores de C2 confirmados — baixo risco",
    malware: "baixo risco", compromised: "risco médio: IP pode ser reciclado", attacker: "lista grande e volátil — risco de bloquear IP compartilhado",
    scanner: "lista grande e volátil — não recomendado", tor: "bloqueia usuários legítimos do Tor", custom: "depende da sua lista" };
  return (
    <div className="space-y-4 max-w-4xl" data-testid="ti-block">
      <Card className="bg-surface border-line p-4 space-y-3">
        <div className="flex items-center gap-2 text-sm font-semibold text-slate-200"><ShieldBan className="w-4 h-4 text-brand-soft" />O que vai para a borda</div>
        <p className="text-xs text-slate-400">O roteador baixa a lista sozinho e descarta o tráfego de/para essas redes. Os seus prefixos próprios e os protegidos da mitigação nunca entram, nem o que estiver em "nunca alertar".</p>
        <div className="divide-y divide-line">{Object.entries(c.categories).map(([k, l]) => (
          <label key={k} className="flex items-center gap-3 py-1.5 text-sm" data-testid={`ti-blk-${k}`}>
            <Switch checked={c.block_categories.includes(k)} onCheckedChange={() => toggle(k)} /><Cat c={k} cats={c.categories} /><span className="text-xs text-slate-500">{SAFE[k]}</span>
          </label>))}</div>
        <div className="flex items-center gap-3">
          <span className="text-xs text-slate-400 flex-1">{pv ? `${pv.count.toLocaleString("pt-BR")} redes hoje (${pv.v4} IPv4, ${pv.v6} IPv6) — salve para recalcular` : "…"}</span>
          <Button size="sm" onClick={save} disabled={busy} className="bg-brand hover:bg-brand-strong text-onbrand" data-testid="ti-blk-save">{busy ? <Loader2 className="w-4 h-4 mr-1 animate-spin" /> : <Save className="w-4 h-4 mr-1" />}Salvar</Button>
        </div>
      </Card>
      <Card className="bg-surface border-line p-4 space-y-3">
        <div className="flex items-center justify-between"><div className="text-sm font-semibold text-slate-200">Endereços da lista</div>
          <Button size="sm" variant="ghost" onClick={newToken} className="text-slate-400" data-testid="ti-blk-token"><KeyRound className="w-4 h-4 mr-1" />Gerar token novo</Button></div>
        {[["Texto (um CIDR por linha)", "txt"], ["MikroTik (.rsc)", "routeros"], ["Junos (prefix-list BASTION_TI)", "juniper"], ["Cisco (object-group, IPv4)", "cisco"]].map(([l, f]) => (
          <div key={f} className="flex items-center gap-2"><span className="text-xs text-slate-400 w-52">{l}</span>
            <code className="flex-1 truncate text-[11px] font-mono text-slate-300 bg-sunken border border-line rounded px-2 py-1">{base}?fmt={f}</code>
            <button onClick={() => copy(`${base}?fmt=${f}`)} className="text-slate-500 hover:text-slate-200"><Copy className="w-4 h-4" /></button></div>))}
        <p className="text-[11px] text-slate-500">Quem tem o endereço baixa a lista (não dá acesso a mais nada). Se vazar, gere um token novo.</p>
      </Card>
      <Card className="bg-surface border-line p-4 space-y-2">
        <div className="flex items-center justify-between"><div className="text-sm font-semibold text-slate-200">MikroTik: colar uma vez no terminal</div>
          <Button size="sm" variant="ghost" onClick={() => copy(ros)} className="text-slate-400" data-testid="ti-blk-copy"><Copy className="w-4 h-4 mr-1" />Copiar</Button></div>
        <pre className="text-[11px] font-mono text-slate-300 bg-sunken border border-line rounded p-3 overflow-x-auto whitespace-pre">{ros}</pre>
        <p className="text-[11px] text-slate-500">Ajuste <span className="font-mono">in-interface-list=WAN</span> para a lista das interfaces de trânsito/IX. As entradas expiram em 2 dias: se o BastiON ficar fora do ar, a lista some sozinha em vez de ficar velha. Em Junos use a prefix-list num firewall filter na entrada das interfaces de upstream.</p>
      </Card>
    </div>
  );
}

export default function ThreatIntel() {
  const [sp, setSp] = useSearchParams();
  const tab = sp.get("tab") || "";
  const [cats, setCats] = useState(null);
  const [look, setLook] = useState("");
  useEffect(() => { api.get("/soc/ti/settings").then(r => setCats(r.data.categories)).catch(() => {}); }, []);
  const lookup = (ip) => { setLook(ip); setSp({ tab: "consulta" }); };
  return (
    <div className="p-4 md:p-6 flex-1 overflow-y-auto" data-testid="ti-page">
      <div className="flex items-start justify-between flex-wrap gap-3 mb-4">
        <div>
          <h1 className="font-heading text-2xl sm:text-[1.75rem] font-semibold tracking-tight text-slate-100 flex items-center gap-2"><Radar className="w-6 h-6 text-brand-soft" /> Threat intel</h1>
          <p className="text-sm text-slate-400 mt-1 max-w-3xl">Cada flow é conferido contra listas públicas de IPs maliciosos (C2 de botnets, máquinas comprometidas, blocos sequestrados, atacantes e scanners).</p>
        </div>
        <div className="flex gap-1 p-1 bg-sunken border border-line rounded-md text-sm">
          {[["", "Ocorrências"], ["consulta", "Consultar IP"], ["listas", "Listas"], ["bloqueio", "Bloqueio na borda"]].map(([k, l]) => <button key={k} onClick={() => setSp(k ? { tab: k } : {})} className={`px-3 h-7 rounded ${tab === k ? "bg-brand/20 text-brand-soft" : "text-slate-400 hover:text-slate-200"}`} data-testid={`ti-tab-${k || "hits"}`}>{l}</button>)}
        </div>
      </div>
      {tab === "consulta" ? <Lookup ip0={look} cats={cats} /> : tab === "listas" ? <Lists /> : tab === "bloqueio" ? <Block /> : <Hits cats={cats} onLookup={lookup} />}
    </div>
  );
}
