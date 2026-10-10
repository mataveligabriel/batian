import React, { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, formatApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { toast } from "sonner";
import { TagSelect } from "@/components/TagSelect";
import { Waypoints, Loader2, Sparkles, Route, FlaskConical, Copy, ListPlus, AlertTriangle, ArrowRight, Trash2, KeyRound } from "lucide-react";

const fmtBps = (v) => (v == null ? "—" : v >= 1e9 ? `${(v / 1e9).toFixed(2)} G` : v >= 1e6 ? `${(v / 1e6).toFixed(0)} M` : `${(v / 1e3).toFixed(0)} k`);
const fmtCap = (m) => (!m ? "?" : m >= 1000 ? `${m / 1000}G` : `${m}M`);
const pctColor = (p) => (p == null ? "bg-slate-600" : p >= 90 ? "bg-red-500" : p >= 80 ? "bg-orange-500" : p >= 60 ? "bg-amber-400" : "bg-emerald-500");
const sel = "h-9 rounded-md bg-sunken border border-line px-2 text-sm text-slate-100";

function Bar({ pct }) {
  return (
    <div className="flex items-center gap-2 min-w-[120px]">
      <div className="h-1.5 flex-1 rounded bg-sunken overflow-hidden"><div className={`h-full ${pctColor(pct)}`} style={{ width: `${Math.min(100, pct || 0)}%` }} /></div>
      <span className={`text-xs font-mono w-12 text-right ${pct >= 90 ? "text-red-300" : pct >= 80 ? "text-orange-300" : "text-slate-300"}`}>{pct == null ? "—" : `${pct}%`}</span>
    </div>
  );
}

async function copy(t) { try { await navigator.clipboard.writeText(t); toast.success("Copiado"); } catch { toast.error("Não consegui copiar"); } }

function UtilTable({ rows, compare, limit = 15, testid }) {
  const prev = useMemo(() => Object.fromEntries((compare || []).map(r => [`${r.link}|${r.dir}`, r])), [compare]);
  return (
    <div className="overflow-x-auto" data-testid={testid}>
      <table className="w-full text-sm">
        <thead><tr className="text-[11px] text-slate-500 text-left"><th className="py-1 pr-3">Sentido</th><th className="pr-3">Tráfego</th>{compare && <th className="pr-3">Antes</th>}<th>{compare ? "Depois" : "Utilização"}</th></tr></thead>
        <tbody>
          {rows.slice(0, limit).map(r => {
            const b = prev[`${r.link}|${r.dir}`];
            return (
              <tr key={`${r.link}|${r.dir}`} className="border-t border-line/60">
                <td className="py-1.5 pr-3 text-slate-200 whitespace-nowrap">{r.from} <ArrowRight className="w-3 h-3 inline text-slate-500" /> {r.to}</td>
                <td className="pr-3 font-mono text-xs text-slate-400">{fmtBps(r.bps)}</td>
                {compare && <td className="pr-3 w-40"><Bar pct={b?.pct} /></td>}
                <td className="w-48"><Bar pct={r.pct} /></td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function Optimize({ run }) {
  const nav = useNavigate();
  const [o, setO] = useState({ target_pct: 80, max_changes: 4, symmetric: true });
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState(null);
  const go = async () => {
    setBusy(true); setRes(null);
    try { const { data } = await api.post(`/te/runs/${run.id}/optimize`, { ...o, target_pct: Number(o.target_pct), max_changes: Number(o.max_changes) }); setRes(data); }
    catch (e) { toast.error(formatApiError(e)); } finally { setBusy(false); }
  };
  const toRunbook = async () => {
    try {
      await api.post("/runbooks", { name: `TE ${run.name} ${new Date().toLocaleString("pt-BR").slice(0, 16)}`.slice(0, 60),
        description: `Custos OSPF sugeridos pela engenharia de tráfego (pico ${res.peak_before}% → ${res.peak_after}%)`, telegram: false, save: false,
        steps: res.commands.map(c => ({ device_id: c.device_id, commands: c.lines.join("\n") })) });
      toast.success("Roteiro criado em Execução em Lote → Roteiros"); nav("/batch?tab=roteiros");
    } catch (e) { toast.error(formatApiError(e)); }
  };
  const allCmds = (res?.commands || []).map(c => `! ${c.device}\n${c.lines.join("\n")}`).join("\n\n");
  return (
    <div className="space-y-4" data-testid="te-opt">
      <div className="flex flex-wrap items-end gap-3">
        <div className="w-28"><Label className="text-xs text-slate-400">Meta de pico (%)</Label><Input value={o.target_pct} onChange={e => setO({ ...o, target_pct: e.target.value })} className="bg-sunken border-line mt-1" data-testid="te-opt-target" /></div>
        <div className="w-28"><Label className="text-xs text-slate-400">Máx. mudanças</Label><Input value={o.max_changes} onChange={e => setO({ ...o, max_changes: e.target.value })} className="bg-sunken border-line mt-1" /></div>
        <div className="flex items-center gap-2 pb-2"><Checkbox id="te-sym" checked={o.symmetric} onCheckedChange={v => setO({ ...o, symmetric: !!v })} /><label htmlFor="te-sym" className="text-sm text-slate-300">Mesmo custo nas duas pontas</label></div>
        <Button onClick={go} disabled={busy} className="bg-brand hover:bg-brand-strong" data-testid="te-opt-go">{busy ? <Loader2 className="w-4 h-4 mr-1.5 animate-spin" /> : <Sparkles className="w-4 h-4 mr-1.5" />}Sugerir custos</Button>
      </div>
      {res && (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-3 text-sm">
            <span className="text-slate-400">Pico previsto:</span>
            <span className="font-mono text-red-300">{res.peak_before}%</span><ArrowRight className="w-4 h-4 text-slate-500" />
            <span className={`font-mono ${res.peak_after <= res.target ? "text-emerald-300" : "text-amber-300"}`} data-testid="te-opt-peak">{res.peak_after}%</span>
            {res.changes.length > 0 && <span className="text-slate-500 text-xs">· {res.changes.length} interface(s) · {res.changed_pairs} par(es) de roteadores mudam de caminho</span>}
          </div>
          {res.changes.length === 0 && <div className="text-sm text-slate-400" data-testid="te-opt-none">
            {res.peak_before <= res.target ? "Nenhum sentido acima da meta — nada a mudar." : "Não achei mudança de custo que reduza o pico."}</div>}
          {res.steps.filter(s => s.stuck).map((s, i) => <div key={i} className="text-xs text-amber-300 flex gap-1.5"><AlertTriangle className="w-3.5 h-3.5 shrink-0" />{s.why}. Para esse caso: aumentar a capacidade, LSP com caminho explícito (aba Caminho/LSP) ou mover clientes.</div>)}
          {res.changes.length > 0 && <>
            <table className="w-full text-sm" data-testid="te-opt-changes">
              <thead><tr className="text-[11px] text-slate-500 text-left"><th className="py-1">Equipamento</th><th>Interface</th><th>Sentido</th><th>Custo</th></tr></thead>
              <tbody>{res.changes.map((c, i) => (
                <tr key={i} className="border-t border-line/60"><td className="py-1.5 text-slate-200">{c.device}</td><td className="font-mono text-xs text-slate-400">{c.iface}</td>
                  <td className="text-xs text-slate-400">→ {c.toward}</td><td className="font-mono text-xs"><span className="text-slate-500">{c.old}</span> → <span className="text-brand-soft">{c.new}</span></td></tr>))}
              </tbody>
            </table>
            <div><div className="text-xs text-slate-400 mb-1">Carga prevista nos sentidos mais cheios</div><UtilTable rows={res.after} compare={res.before} limit={10} testid="te-opt-after" /></div>
            <div>
              <div className="flex items-center gap-2 mb-1"><span className="text-xs text-slate-400">Comandos</span>
                <button onClick={() => copy(allCmds)} className="text-xs text-slate-400 hover:text-slate-100 flex items-center gap-1"><Copy className="w-3 h-3" />copiar</button>
                <Button size="sm" variant="outline" onClick={toRunbook} className="ml-auto h-7 border-line bg-transparent" data-testid="te-opt-runbook"><ListPlus className="w-3.5 h-3.5 mr-1" />Criar roteiro com essas mudanças</Button></div>
              <pre className="text-xs font-mono text-emerald-300 bg-sunken p-3 rounded border border-line overflow-x-auto whitespace-pre">{allCmds}</pre>
            </div>
            <div className="text-[11px] text-slate-500">Estimativa: o tráfego de hoje em cada enlace é tratado como demanda entre as pontas e redistribuído pelo SPF (com ECMP). Aplique uma mudança por vez, numa janela, e acompanhe no mapa.</div>
          </>}
        </div>
      )}
    </div>
  );
}

function PathCalc({ run }) {
  const nodes = run.nodes || [];
  const [p, setP] = useState({ src: "", dst: "", bw_mbps: "0", max_pct: "90", include: [], exclude: [] });
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState(null);
  const go = async () => {
    if (!p.src || !p.dst) return toast.error("Escolha origem e destino");
    setBusy(true); setRes(null);
    try {
      const { data } = await api.post(`/te/runs/${run.id}/path`, { src: p.src, dst: p.dst, bw_mbps: Number(p.bw_mbps) || 0, max_pct: Number(p.max_pct) || 90,
        include_nodes: p.include, exclude_nodes: p.exclude });
      setRes(data);
    } catch (e) { toast.error(formatApiError(e)); } finally { setBusy(false); }
  };
  const nsel = (k, v) => <select value={v} onChange={e => setP({ ...p, [k]: e.target.value })} className={`${sel} w-full mt-1`} data-testid={`te-path-${k}`}>
    <option value="">—</option>{nodes.map(n => <option key={n.id} value={n.id}>{n.name}</option>)}</select>;
  const multi = (k, label) => (
    <div className="flex-1 min-w-[220px]"><Label className="text-xs text-slate-400">{label}</Label>
      <div className="flex flex-wrap gap-1 mt-1">
        {p[k].map(id => <button key={id} onClick={() => setP({ ...p, [k]: p[k].filter(x => x !== id) })} className="text-[11px] px-1.5 py-0.5 rounded border border-line text-slate-300 hover:text-red-300">{nodes.find(n => n.id === id)?.name} ✕</button>)}
        <select value="" onChange={e => e.target.value && setP({ ...p, [k]: [...p[k], e.target.value] })} className="h-7 rounded bg-sunken border border-line px-1 text-xs text-slate-300">
          <option value="">+ adicionar</option>{nodes.filter(n => !p[k].includes(n.id)).map(n => <option key={n.id} value={n.id}>{n.name}</option>)}</select>
      </div></div>
  );
  return (
    <div className="space-y-4" data-testid="te-path">
      <div className="grid sm:grid-cols-4 gap-3">
        <div><Label className="text-xs text-slate-400">Origem</Label>{nsel("src", p.src)}</div>
        <div><Label className="text-xs text-slate-400">Destino</Label>{nsel("dst", p.dst)}</div>
        <div><Label className="text-xs text-slate-400">Banda do LSP (Mbps)</Label><Input value={p.bw_mbps} onChange={e => setP({ ...p, bw_mbps: e.target.value })} className="bg-sunken border-line mt-1" data-testid="te-path-bw" /></div>
        <div><Label className="text-xs text-slate-400">Não passar de (%)</Label><Input value={p.max_pct} onChange={e => setP({ ...p, max_pct: e.target.value })} className="bg-sunken border-line mt-1" /></div>
      </div>
      <div className="flex flex-wrap gap-3">{multi("include", "Passar por")}{multi("exclude", "Evitar")}</div>
      <Button onClick={go} disabled={busy} className="bg-brand hover:bg-brand-strong" data-testid="te-path-go">{busy ? <Loader2 className="w-4 h-4 mr-1.5 animate-spin" /> : <Route className="w-4 h-4 mr-1.5" />}Calcular</Button>
      {res && (
        <div className="grid lg:grid-cols-2 gap-4">
          <Card className="bg-sunken/40 border-line p-3 space-y-2" data-testid="te-path-spf">
            <div className="text-sm font-semibold text-slate-200">Caminho atual (SPF/LDP)</div>
            {!res.spf.reachable ? <div className="text-xs text-red-300 space-y-1" data-testid="te-path-why"><div>Sem caminho OSPF entre os dois.</div>
              {(res.spf.why || []).map((w, i) => <div key={i} className="text-amber-300">• {w}</div>)}</div> : <>
              <div className="text-xs text-slate-400">custo {res.spf.cost}{res.spf.ecmp ? " · ECMP (dividido)" : ""}</div>
              {res.spf.hops.map((h, i) => <div key={i} className="flex items-center gap-2 text-xs"><span className="text-slate-200 w-56 truncate">{h.from} → {h.to}</span>{h.share < 1 && <span className="text-slate-500">{Math.round(h.share * 100)}%</span>}<Bar pct={h.pct_now} /></div>)}
            </>}
          </Card>
          <Card className="bg-sunken/40 border-line p-3 space-y-2" data-testid="te-path-cspf">
            <div className="text-sm font-semibold text-slate-200">Caminho com folga (CSPF)</div>
            {!res.cspf.found ? <div className="text-xs text-amber-300">{res.cspf.why}</div> : <>
              <div className="text-xs text-slate-400">custo {res.cspf.cost} · utilização agora → com o LSP</div>
              {res.cspf.hops.map((h, i) => <div key={i} className="flex items-center gap-2 text-xs"><span className="text-slate-200 w-56 truncate">{h.from} → {h.to}</span><span className="text-slate-500 font-mono w-12 text-right">{h.pct_now == null ? "—" : `${h.pct_now}%`}</span><ArrowRight className="w-3 h-3 text-slate-500" /><Bar pct={h.pct_after} /></div>)}
              <div className="flex items-center gap-2 pt-1"><span className="text-xs text-slate-400">Túnel RSVP-TE na origem ({res.cspf.lsp_name})</span>
                <button onClick={() => copy(res.cspf.config.join("\n"))} className="text-xs text-slate-400 hover:text-slate-100 flex items-center gap-1"><Copy className="w-3 h-3" />copiar</button></div>
              <pre className="text-xs font-mono text-emerald-300 bg-sunken p-2 rounded border border-line overflow-x-auto whitespace-pre" data-testid="te-path-config">{res.cspf.config.join("\n")}</pre>
              <div className="text-[11px] text-slate-500">Precisa de MPLS TE/RSVP habilitado nos roteadores do caminho. Só com LDP, o tráfego segue o SPF — mude o caminho pelos custos OSPF (aba Otimizar).</div>
            </>}
          </Card>
        </div>
      )}
    </div>
  );
}

function Simulate({ run }) {
  const [costs, setCosts] = useState({});
  const [down, setDown] = useState([]);
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState(null);
  const links = (run.links || []).filter(L => L.cost_ab != null && L.cost_ba != null);
  const go = async () => {
    setBusy(true);
    try {
      const body = { costs: Object.entries(costs).filter(([, v]) => v !== "").map(([k, v]) => { const [link, dir] = k.split("|"); return { link, dir, cost: Number(v) }; }), down_links: down };
      const { data } = await api.post(`/te/runs/${run.id}/simulate`, body); setRes(data);
    } catch (e) { toast.error(formatApiError(e)); } finally { setBusy(false); }
  };
  return (
    <div className="space-y-4" data-testid="te-sim">
      <div className="overflow-x-auto max-h-[420px] overflow-y-auto border border-line rounded-md">
        <table className="w-full text-sm">
          <thead className="sticky top-0 bg-surface"><tr className="text-[11px] text-slate-500 text-left"><th className="p-2">Enlace</th><th>Custo A→B</th><th>Custo B→A</th><th>Derrubar</th></tr></thead>
          <tbody>{links.map(L => (
            <tr key={L.id} className="border-t border-line/60">
              <td className="p-2 text-slate-200 text-xs">{L.a_name} <span className="text-slate-500 font-mono">{L.a_if}</span> ↔ {L.b_name} <span className="text-slate-500">· {fmtCap(L.capacity_mbps)}</span></td>
              {["ab", "ba"].map(d => <td key={d}><Input value={costs[`${L.id}|${d}`] ?? ""} placeholder={String(L[`cost_${d}`])} onChange={e => setCosts({ ...costs, [`${L.id}|${d}`]: e.target.value.replace(/\D/g, "") })} className="h-7 w-20 bg-sunken border-line font-mono text-xs" /></td>)}
              <td><Checkbox checked={down.includes(L.id)} onCheckedChange={v => setDown(v ? [...down, L.id] : down.filter(x => x !== L.id))} /></td>
            </tr>))}
          </tbody>
        </table>
      </div>
      <Button onClick={go} disabled={busy} className="bg-brand hover:bg-brand-strong" data-testid="te-sim-go">{busy ? <Loader2 className="w-4 h-4 mr-1.5 animate-spin" /> : <FlaskConical className="w-4 h-4 mr-1.5" />}Simular</Button>
      {res && <div className="space-y-2">
        <div className="text-sm text-slate-300">Pico previsto: <span className="font-mono">{res.peak}%</span> · {res.changed_pairs} par(es) mudam de caminho
          {res.cut_nodes.length > 0 && <span className="text-red-300"> · ficariam isolados: {res.cut_nodes.map(id => run.nodes.find(n => n.id === id)?.name || id).join(", ")}</span>}</div>
        <UtilTable rows={res.utilization} compare={run.utilization} limit={20} testid="te-sim-result" />
      </div>}
    </div>
  );
}

export default function TrafficEng() {
  const [devices, setDevices] = useState([]);
  const [maps, setMaps] = useState([]);
  const [runs, setRuns] = useState([]);
  const [src, setSrc] = useState("devices");
  const [picked, setPicked] = useState([]);
  const [mapId, setMapId] = useState("");
  const [busy, setBusy] = useState(false);
  const [run, setRun] = useState(null);
  const [tab, setTab] = useState("opt");
  const [snmp, setSnmp] = useState({ open: false, community: "", port: "" });
  const [snmpBusy, setSnmpBusy] = useState(false);
  const applySnmp = async () => {
    if (!picked.length) return toast.error("Marque os equipamentos primeiro");
    setSnmpBusy(true);
    try {
      const body = { device_ids: picked, snmp_community: snmp.community.trim() };
      if (String(snmp.port).trim()) body.snmp_port = Number(snmp.port);
      const { data } = await api.post("/devices/bulk-update", body);
      toast.success(`Community SNMP gravada em ${data.updated} equipamento(s)`);
      setSnmp({ open: false, community: "", port: "" });
      api.get("/devices").then(r => setDevices(r.data || [])).catch(() => {});
    } catch (e) { toast.error(formatApiError(e)); } finally { setSnmpBusy(false); }
  };

  const loadRuns = () => api.get("/te/runs").then(r => setRuns(r.data)).catch(() => {});
  useEffect(() => {
    api.get("/devices").then(r => setDevices(r.data || [])).catch(() => {});
    api.get("/maps").then(r => setMaps(r.data || [])).catch(() => {});
    loadRuns();
  }, []);
  const build = async () => {
    setBusy(true);
    try {
      const { data } = await api.post("/te/runs", src === "map" ? { map_id: mapId } : { device_ids: picked, sample_sec: 10 });
      setRun(data); loadRuns();
      if (!data.links.length) toast.warning("Nenhum enlace ponto a ponto entre os equipamentos escolhidos");
    } catch (e) { toast.error(formatApiError(e)); } finally { setBusy(false); }
  };
  const open = async (id) => { try { const { data } = await api.get(`/te/runs/${id}`); setRun(data); } catch (e) { toast.error(formatApiError(e)); } };
  const del = async (id) => { try { await api.delete(`/te/runs/${id}`); if (run?.id === id) setRun(null); loadRuns(); } catch (e) { toast.error(formatApiError(e)); } };

  return (
    <div className="flex-1 overflow-y-auto" data-testid="te-page">
      <div className="px-4 md:px-6 pt-4 pb-2">
        <div className="hidden md:block text-xs text-slate-400">OSPF · SPF/CSPF · LSP · otimização de custos</div>
        <h1 className="font-heading text-2xl sm:text-[1.75rem] font-semibold tracking-tight text-slate-100 mt-1">Engenharia de tráfego</h1>
      </div>
      <div className="px-4 md:px-6 pb-6 grid gap-4 xl:grid-cols-[360px_minmax(0,1fr)]">
        <div className="space-y-4">
          <Card className="bg-surface border-line p-4 space-y-3">
            <div className="flex rounded-md border border-line overflow-hidden text-sm">
              {[["devices", "Equipamentos"], ["map", "Mapa"]].map(([k, l]) => (
                <button key={k} onClick={() => setSrc(k)} data-testid={`te-src-${k}`} className={`flex-1 h-8 ${src === k ? "bg-brand text-white" : "bg-sunken text-slate-400 hover:text-slate-200"}`}>{l}</button>))}
            </div>
            {src === "devices" ? <>
              <TagSelect devices={devices} selected={picked} onChange={setPicked} testid="te-tags" />
              <div className="rounded-md border border-line p-2 space-y-2" data-testid="te-snmp">
                <button onClick={() => setSnmp({ ...snmp, open: !snmp.open })} className="text-xs text-slate-300 hover:text-slate-100 flex items-center gap-1.5" data-testid="te-snmp-toggle">
                  <KeyRound className="w-3.5 h-3.5" />Community SNMP dos {picked.length} escolhido(s)</button>
                {snmp.open && <>
                  <div className="grid grid-cols-[1fr_5.5rem] gap-2">
                    <Input value={snmp.community} onChange={e => setSnmp({ ...snmp, community: e.target.value })} placeholder="community (vazio = a padrão)" autoComplete="off"
                      className="bg-sunken border-line h-8 font-mono text-sm" data-testid="te-snmp-community" />
                    <Input value={snmp.port} onChange={e => setSnmp({ ...snmp, port: e.target.value.replace(/\D/g, "") })} placeholder="161" className="bg-sunken border-line h-8 font-mono text-sm" />
                  </div>
                  <Button size="sm" onClick={applySnmp} disabled={snmpBusy || !picked.length} variant="outline" className="w-full h-8 border-line bg-transparent" data-testid="te-snmp-apply">
                    {snmpBusy && <Loader2 className="w-3.5 h-3.5 mr-1.5 animate-spin" />}Gravar nos {picked.length} equipamento(s)</Button>
                  <div className="text-[11px] text-slate-500">Fica salva no cadastro de cada um (vale também para mapas, dashboards e monitoramento).</div>
                </>}
              </div>
              <div className="text-[11px] text-slate-500">Lê por SNMP: interfaces, IPs, custo OSPF e tráfego (10 s). Os enlaces são achados pelas sub-redes ponto a ponto (/29–/31) entre os escolhidos.</div>
            </> : <>
              <select value={mapId} onChange={e => setMapId(e.target.value)} className={`${sel} w-full`} data-testid="te-map">
                <option value="">Escolha o mapa…</option>{maps.map(m => <option key={m.id} value={m.id}>{m.name}</option>)}</select>
              <div className="text-[11px] text-slate-500">Usa a última Análise de rede do mapa (enlaces, custos OSPF e tráfego).</div>
            </>}
            <Button onClick={build} disabled={busy || (src === "map" ? !mapId : picked.length < 2)} className="w-full bg-brand hover:bg-brand-strong" data-testid="te-build">
              {busy ? <Loader2 className="w-4 h-4 mr-1.5 animate-spin" /> : <Waypoints className="w-4 h-4 mr-1.5" />}{busy ? "Lendo a rede…" : "Montar topologia"}</Button>
          </Card>
          {runs.length > 0 && (
            <Card className="bg-surface border-line p-3 space-y-1">
              <div className="text-xs text-slate-400 mb-1">Topologias recentes</div>
              {runs.map(r => (
                <div key={r.id} className={`flex items-center gap-2 text-xs rounded px-2 py-1 ${run?.id === r.id ? "bg-brand/15" : "hover:bg-slate-800/40"}`}>
                  <button onClick={() => open(r.id)} className="flex-1 text-left text-slate-200 truncate">{r.name} <span className="text-slate-500">· {r.links} enlaces · {new Date(r.at).toLocaleString("pt-BR").slice(0, 16)}</span></button>
                  <button onClick={() => del(r.id)} className="text-slate-500 hover:text-red-300"><Trash2 className="w-3.5 h-3.5" /></button>
                </div>))}
            </Card>
          )}
        </div>

        <div className="space-y-4 min-w-0">
          {!run ? <Card className="bg-surface border-line p-8 text-center text-sm text-slate-500">Escolha os equipamentos (ou um mapa) e clique em Montar topologia.</Card> : <>
            <Card className="bg-surface border-line p-4 space-y-3" data-testid="te-overview">
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
                <span className="font-semibold text-slate-100">{run.name}</span>
                <span className="text-slate-400">{run.summary.devices} equipamentos · {run.summary.links} enlaces ({run.summary.ospf_links} com OSPF)</span>
                <span className={run.summary.over80 ? "text-orange-300" : "text-emerald-300"}>{run.summary.over80} sentido(s) ≥ 80%</span>
              </div>
              {run.errors?.length > 0 && <div className="text-xs text-amber-300">Sem leitura: {run.errors.map(e => `${e.device} (${e.error})`).join(" · ")}</div>}
              <details className="text-xs" data-testid="te-devs">
                <summary className="cursor-pointer text-slate-400">Equipamentos lidos ({(run.nodes || []).length})</summary>
                <table className="w-full mt-2">
                  <thead><tr className="text-[11px] text-slate-500 text-left"><th className="py-1">Equipamento</th><th>Enlaces</th><th>OSPF por SNMP</th><th>OSPF pela CLI</th></tr></thead>
                  <tbody>{(run.nodes || []).map(n => (
                    <tr key={n.id} className="border-t border-line/60">
                      <td className="py-1 text-slate-200">{n.name}{n.ok === false && <span className="text-red-300"> · sem SNMP</span>}</td>
                      <td className={n.links ? "text-slate-300" : "text-amber-300"}>{n.links ?? "—"}</td>
                      <td className={n.ospf_snmp ? "text-slate-300" : "text-slate-500"}>{n.ospf_snmp ?? "—"} interface(s)</td>
                      <td className={n.cli_error ? "text-red-300" : "text-slate-300"}>{n.ospf_cli == null ? "—" : n.cli_error ? n.cli_error : `${n.ospf_cli} interface(s)`}</td>
                    </tr>))}</tbody>
                </table>
              </details>
              {run.notes?.length > 0 && <details className="text-xs text-slate-500"><summary className="cursor-pointer">{run.notes.length} observação(ões)</summary>{run.notes.map((n, i) => <div key={i}>{n.net}: {n.note}</div>)}</details>}
              <UtilTable rows={run.utilization} limit={12} testid="te-util" />
            </Card>
            <div className="flex gap-1 border-b border-line">
              {[["opt", "Otimizar custos OSPF", Sparkles], ["path", "Caminho / LSP", Route], ["sim", "Simular", FlaskConical]].map(([k, l, I]) => (
                <button key={k} onClick={() => setTab(k)} data-testid={`te-tab-${k}`} className={`px-3 py-2 text-sm border-b-2 -mb-px flex items-center gap-1.5 ${tab === k ? "border-brand text-slate-100" : "border-transparent text-slate-400 hover:text-slate-200"}`}><I className="w-4 h-4" />{l}</button>))}
            </div>
            <Card className="bg-surface border-line p-4">
              {tab === "opt" && <Optimize run={run} key={run.id} />}
              {tab === "path" && <PathCalc run={run} key={run.id} />}
              {tab === "sim" && <Simulate run={run} key={run.id} />}
            </Card>
          </>}
        </div>
      </div>
    </div>
  );
}
