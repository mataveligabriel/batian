import React, { useEffect, useMemo, useRef, useState } from "react";
import { api, formatApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { toast } from "sonner";
import {
  X, Play, Loader2, Download, Printer, Settings2, AlertTriangle, XCircle, Info, Activity, RotateCcw, Unplug, Power, Stethoscope,
} from "lucide-react";
import { MapCanvas } from "@/components/maps/MapCanvas";
import { MplsTab, MplsSettingsDialog } from "@/components/maps/MplsPanel";
import { fmtBps, fmtSpeed, STATUS } from "@/lib/netfmt";

const SEV = {
  crit: { label: "Crítico", color: STATUS.critical, icon: XCircle },
  warn: { label: "Atenção", color: STATUS.warning, icon: AlertTriangle },
  info: { label: "Info", color: "#7FADEB", icon: Info },
};
const REFS = [["", "automática (a que a rede usa)"], ["10000", "10 Gbps"], ["100000", "100 Gbps"], ["400000", "400 Gbps"], ["1000000", "1 Tbps"]];
const TABS = [["findings", "Achados"], ["ospf", "OSPF"], ["mpls", "MPLS"], ["scen", "Cenários"], ["bgp", "BGP"], ["ifaces", "Interfaces"]];
const fmtWhen = (iso) => new Date(iso).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
const num = (v, d = 1) => (v === null || v === undefined ? "—" : v === 0 ? "0" : Number(v).toFixed(d));
const pctColor = (p) => (p === null || p === undefined ? "#8D9A9D" : p >= 100 ? STATUS.critical : p >= 90 ? STATUS.critical : p >= 70 ? STATUS.warning : "#CED6D7");

function SevBadge({ sev }) {
  const s = SEV[sev];
  return <span className="inline-flex items-center gap-1 text-[10px] font-mono font-bold uppercase px-1.5 py-0.5 rounded" style={{ color: s.color, background: `${s.color}22` }}><s.icon className="w-3 h-3" />{s.label}</span>;
}

function Th({ children, right }) { return <th className={`py-1.5 px-2 font-normal ${right ? "text-right" : "text-left"}`}>{children}</th>; }

export function NetAnalysis({ map, devices, onClose }) {
  const [reports, setReports] = useState([]);
  const [doc, setDoc] = useState(null);
  const [running, setRunning] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [opts, setOpts] = useState({ ref: "", util_warn: 70, util_crit: 90, mpls: true });
  const [mplsCfg, setMplsCfg] = useState(false);
  const [showOpts, setShowOpts] = useState(false);
  const [tab, setTab] = useState("findings");
  const [sel, setSel] = useState(null);
  const [sevOn, setSevOn] = useState({ crit: true, warn: true, info: true });
  const [sim, setSim] = useState({ down: [], downNodes: [], costs: {} });   // costs: {"linkId:ab": 50}
  const [simRes, setSimRes] = useState(null);
  const [fitSignal, setFitSignal] = useState(0);
  const tick = useRef(null);

  const loadList = async (open = true) => {
    const { data } = await api.get(`/maps/${map.id}/analysis`);
    setReports(data);
    if (open && data[0]) openReport(data[0].id);
  };
  const openReport = async (id) => {
    try { const { data } = await api.get(`/analysis/${id}`); setDoc(data); setSel(null); clearSim(); }
    catch (e) { toast.error(formatApiError(e)); }
  };
  useEffect(() => { loadList().catch(() => {}); }, [map.id]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { const esc = (e) => e.key === "Escape" && onClose(); window.addEventListener("keydown", esc); return () => window.removeEventListener("keydown", esc); }, [onClose]);

  const run = async () => {
    setRunning(true); setElapsed(0);
    tick.current = setInterval(() => setElapsed(s => s + 1), 1000);
    try {
      const { data } = await api.post(`/maps/${map.id}/analysis`, {
        ref_bw_mbps: opts.ref ? Number(opts.ref) : null, util_warn: Number(opts.util_warn) || 70, util_crit: Number(opts.util_crit) || 90, mpls: opts.mpls,
      }, { timeout: 300000 });
      setDoc(data); setSel(null); clearSim(); setTab("findings");
      const s = data.report.summary;
      toast.success(`Análise concluída: ${s.crit} crítico(s), ${s.warn} de atenção`);
      loadList(false);
    } catch (e) { toast.error(formatApiError(e)); }
    finally { clearInterval(tick.current); setRunning(false); }
  };

  const download = async (print) => {
    try {
      const { data } = await api.get(`/analysis/${doc.id}/html`, { responseType: "blob" });
      const url = URL.createObjectURL(new Blob([data], { type: "text/html" }));
      if (print) { window.open(url, "_blank"); }
      else {
        const a = document.createElement("a");
        a.href = url; a.download = `analise-${map.name.replace(/[^\w-]+/g, "_")}-${doc.at.slice(0, 10)}.html`; a.click();
      }
      setTimeout(() => URL.revokeObjectURL(url), 60000);
    } catch (e) { toast.error(formatApiError(e)); }
  };

  const rep = doc?.report;
  const linkById = useMemo(() => Object.fromEntries((rep?.links || []).map(l => [l.id, l])), [rep]);
  const simActive = sim.down.length || sim.downNodes.length || Object.keys(sim.costs).length;
  function clearSim() { setSim({ down: [], downNodes: [], costs: {} }); setSimRes(null); }

  // simulação (debounce)
  useEffect(() => {
    if (!doc || !simActive) { setSimRes(null); return; }
    const t = setTimeout(() => {
      const costs = Object.entries(sim.costs).map(([k, cost]) => { const [link_id, dir] = k.split(":"); return { link_id, dir, cost: Number(cost) }; })
        .filter(c => c.cost > 0);
      api.post(`/analysis/${doc.id}/simulate`, { down_links: sim.down, down_nodes: sim.downNodes, costs })
        .then(r => setSimRes(r.data)).catch(e => toast.error(formatApiError(e)));
    }, 250);
    return () => clearTimeout(t);
  }, [doc?.id, JSON.stringify(sim)]); // eslint-disable-line react-hooks/exhaustive-deps

  // o que o mapa mostra: tráfego medido ou previsto, custos, halos
  const live = useMemo(() => {
    if (!rep) return null;
    const links = {};
    rep.links.forEach(l => {
      const p = simRes?.links?.[l.id];
      links[l.id] = p ? { ab_bps: p.ab_bps, ba_bps: p.ba_bps, ab_pct: p.ab_pct, ba_pct: p.ba_pct, down: p.down, capacity_mbps: l.capacity_mbps }
                      : { ab_bps: l.ab_bps, ba_bps: l.ba_bps, ab_pct: l.ab_pct, ba_pct: l.ba_pct, down: ["down", "lowerLayerDown"].includes(l.oper_a) || ["down", "lowerLayerDown"].includes(l.oper_b), capacity_mbps: l.capacity_mbps };
    });
    return { links, nodes: {} };
  }, [rep, simRes]);
  const costs = useMemo(() => {
    if (!rep) return null;
    const out = {};
    rep.links.forEach(l => {
      const ab = sim.costs[`${l.id}:ab`] ? Number(sim.costs[`${l.id}:ab`]) : l.cost_ab;
      const ba = sim.costs[`${l.id}:ba`] ? Number(sim.costs[`${l.id}:ba`]) : l.cost_ba;
      out[l.id] = { ab, ba, changed: { ab: !!sim.costs[`${l.id}:ab`], ba: !!sim.costs[`${l.id}:ba`] } };
    });
    return out;
  }, [rep, sim.costs]);
  const marks = useMemo(() => {
    if (!rep || simActive) return null;
    const out = {};
    rep.findings.forEach(f => {          // enlace herda o achado mais grave (OSPF, MPLS, capacidade…)
      if (!f.link_id || f.sev === "info") return;
      if (out[f.link_id] !== "crit") out[f.link_id] = f.sev;
    });
    return out;
  }, [rep, simActive]);

  const selLink = sel?.type === "link" ? linkById[sel.id] : null;
  const selNode = sel?.type === "node" ? doc?.nodes?.find(n => n.id === sel.id) : null;
  const toggle = (key, id) => setSim(s => ({ ...s, [key]: s[key].includes(id) ? s[key].filter(x => x !== id) : [...s[key], id] }));
  const setCost = (id, dir, v) => setSim(s => { const c = { ...s.costs }; if (v === "" || v === null) delete c[`${id}:${dir}`]; else c[`${id}:${dir}`] = v; return { ...s, costs: c }; });
  const overloaded = simRes ? Object.entries(simRes.links).flatMap(([id, p]) => ["ab", "ba"].filter(d => (p[`${d}_pct`] || 0) >= 100).map(d => ({ id, d, pct: p[`${d}_pct`] }))) : [];
  const shownFindings = (rep?.findings || []).filter(f => sevOn[f.sev]);
  const s = rep?.summary;
  const scoreColor = !s ? "#8D9A9D" : s.score >= 80 ? STATUS.good : s.score >= 50 ? STATUS.warning : STATUS.critical;

  return (
    <div className="fixed inset-0 z-[100] bg-canvas flex flex-col" data-testid="net-analysis">
      {/* cabeçalho */}
      <div className="flex items-center gap-2 flex-wrap px-3 md:px-4 py-2 border-b border-line bg-panel pt-[calc(0.5rem+env(safe-area-inset-top))]">
        <Stethoscope className="w-5 h-5 text-brand-soft" />
        <div className="mr-2 min-w-0">
          <div className="text-[10px] text-slate-500">Análise de rede · experimental</div>
          <div className="text-sm font-semibold text-slate-100 truncate">{map.name}</div>
        </div>
        {s && (
          <div className="flex items-center gap-2 text-xs font-mono">
            <span className="px-2 py-1 rounded border border-line" title="Saúde (100 − 15 por crítico − 5 por atenção − 1 por info)">
              saúde <b style={{ color: scoreColor }}>{s.score}</b>
            </span>
            {["crit", "warn", "info"].map(k => <span key={k} style={{ color: SEV[k].color }}>{s[k]} {SEV[k].label.toLowerCase()}</span>)}
          </div>
        )}
        <div className="ml-auto flex items-center gap-1.5 flex-wrap">
          {reports.length > 0 && (
            <select value={doc?.id || ""} onChange={e => openReport(e.target.value)} className="h-8 bg-sunken border border-line rounded px-2 text-xs font-mono" data-testid="na-history">
              {reports.map(r => <option key={r.id} value={r.id}>{fmtWhen(r.at)} · {r.summary?.crit ?? "?"} crít.</option>)}
            </select>
          )}
          <div className="relative">
            <Button size="sm" variant="outline" onClick={() => setShowOpts(v => !v)} className="h-8 border-line bg-panel text-slate-300 hover:bg-slate-800" title="Opções"><Settings2 className="w-3.5 h-3.5" /></Button>
            {showOpts && (
              <div className="absolute right-0 top-full mt-1 z-20 w-72 bg-surface border border-line rounded-md shadow-2xl p-3 space-y-2 text-xs">
                <label className="block text-slate-400">Referência de banda do custo OSPF
                  <select value={opts.ref} onChange={e => setOpts({ ...opts, ref: e.target.value })} className="mt-1 w-full h-8 bg-sunken border border-line rounded px-2">
                    {REFS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                  </select>
                </label>
                <div className="grid grid-cols-2 gap-2">
                  <label className="text-slate-400">Atenção (%)<input type="number" value={opts.util_warn} onChange={e => setOpts({ ...opts, util_warn: e.target.value })} className="mt-1 w-full h-8 bg-sunken border border-line rounded px-2 font-mono" /></label>
                  <label className="text-slate-400">Crítico (%)<input type="number" value={opts.util_crit} onChange={e => setOpts({ ...opts, util_crit: e.target.value })} className="mt-1 w-full h-8 bg-sunken border border-line rounded px-2 font-mono" /></label>
                </div>
                <label className="flex items-start gap-2 text-slate-300 cursor-pointer">
                  <input type="checkbox" className="accent-brand mt-0.5" checked={opts.mpls} onChange={e => setOpts({ ...opts, mpls: e.target.checked })} data-testid="na-opt-mpls" />
                  <span>Ler MPLS pela CLI (LDP, VPWS, VPLS, L3VPN) — entra por SSH em cada equipamento</span>
                </label>
                <button onClick={() => { setShowOpts(false); setMplsCfg(true); }} className="text-[11px] text-brand-soft underline">comandos MPLS por fabricante…</button>
                <div className="text-[11px] text-slate-500">Vale para a próxima análise.</div>
              </div>
            )}
          </div>
          {doc && <>
            <Button size="sm" variant="outline" onClick={() => download(false)} className="h-8 border-line bg-panel text-slate-200 hover:bg-slate-800" data-testid="na-download"><Download className="w-3.5 h-3.5 md:mr-1.5" /><span className="hidden md:inline">Relatório</span></Button>
            <Button size="sm" variant="outline" onClick={() => download(true)} className="h-8 border-line bg-panel text-slate-200 hover:bg-slate-800 hidden md:inline-flex" title="Abrir para imprimir / salvar em PDF"><Printer className="w-3.5 h-3.5" /></Button>
          </>}
          <Button size="sm" onClick={run} disabled={running} className="h-8 bg-brand hover:bg-brand-strong" data-testid="na-run">
            {running ? <><Loader2 className="w-3.5 h-3.5 mr-1.5 animate-spin" /> Coletando… {elapsed}s</> : <><Play className="w-3.5 h-3.5 mr-1.5" /> Analisar agora</>}
          </Button>
          <Button size="sm" variant="ghost" onClick={onClose} className="h-8 text-slate-300 hover:bg-slate-800" title="Fechar (Esc)"><X className="w-4 h-4" /></Button>
        </div>
      </div>

      {!doc ? (
        <div className="flex-1 flex items-center justify-center p-6">
          <div className="max-w-xl text-center">
            <Stethoscope className="w-10 h-10 mx-auto text-brand-soft mb-3" />
            <div className="text-lg font-semibold text-slate-100">Diagnóstico da rede deste mapa</div>
            <p className="text-sm text-slate-400 mt-2">O BastiON lê por SNMP cada equipamento do mapa e verifica:</p>
            <div className="text-left text-sm text-slate-300 mt-3 grid sm:grid-cols-2 gap-x-6 gap-y-1">
              {["Custos OSPF por enlace (e se seguem a banda)", "Custo assimétrico e paralelos sem ECMP", "Área, tipo de rede, timers e MTU divergentes",
                "Adjacências que não chegam a FULL", "Sessões BGP caídas ou reiniciando", "Interfaces com erro / descarte",
                "Enlaces congestionados e lanes ópticas ruins", "Pontos únicos de falha e o que acontece se cada enlace cair"].map(t => (
                <div key={t} className="flex gap-2"><span className="text-brand-soft">•</span>{t}</div>
              ))}
            </div>
            <Button onClick={run} disabled={running} className="mt-5 bg-brand hover:bg-brand-strong">
              {running ? <><Loader2 className="w-4 h-4 mr-2 animate-spin" /> Coletando SNMP… {elapsed}s</> : <><Play className="w-4 h-4 mr-2" /> Rodar a primeira análise</>}
            </Button>
            <p className="text-[11px] text-slate-500 mt-3">Leva de 15 s a 1 min. Usa a community SNMP dos equipamentos (leitura das MIBs OSPF, BGP e IF). Nada é alterado na rede.</p>
          </div>
        </div>
      ) : (
        <div className="flex-1 min-h-0 flex flex-col md:flex-row">
          {/* mapa */}
          <div className="h-[46dvh] md:h-auto md:flex-1 min-w-0 relative border-b md:border-b-0 md:border-r border-line">
            <MapCanvas map={map} live={live} devices={devices} editing={false} tool="select" selected={sel} onSelect={setSel}
                       onMoveNode={() => {}} onConnect={() => {}} fitSignal={fitSignal} costs={costs} marks={marks} />
            <div className="absolute top-2 left-2 right-2 flex flex-wrap gap-2 pointer-events-none">
              <div className="hidden md:block pointer-events-auto text-[11px] font-mono bg-surface/95 border border-line rounded px-2 py-1 text-slate-300">
                número perto do equipamento = custo OSPF dele no enlace · halo = problema
              </div>
              {simActive ? (
                <div className="pointer-events-auto text-[11px] font-mono bg-amber-500/15 border border-amber-500/50 rounded px-2 py-1 text-amber-100 flex items-center gap-2 flex-wrap" data-testid="na-sim-banner">
                  <b>SIMULAÇÃO</b>
                  {simRes && <>
                    <span>{simRes.changed_pairs} rota(s) mudam</span>
                    {simRes.isolated_names?.length > 0 && <span className="text-red-300">isolados: {simRes.isolated_names.join(", ")}</span>}
                    {overloaded.length > 0 && <span className="text-red-300">{overloaded.length} sentido(s) acima de 100%</span>}
                  </>}
                  <button onClick={clearSim} className="underline flex items-center gap-1"><RotateCcw className="w-3 h-3" /> limpar</button>
                </div>
              ) : null}
            </div>
          </div>

          {/* painel */}
          <div className="md:w-[460px] shrink-0 flex flex-col min-h-0 flex-1 md:flex-none">
            {(selLink || selNode) ? (
              <div className="p-3 border-b border-line bg-panel max-h-[55%] overflow-y-auto" data-testid="na-selection">
                <div className="flex items-start gap-2">
                  <div className="min-w-0 flex-1">
                    {selLink ? <>
                      <div className="text-sm font-semibold text-slate-100">{selLink.a_name} ↔ {selLink.b_name}</div>
                      <div className="text-[11px] font-mono text-slate-500">{selLink.a_if} ↔ {selLink.b_if} · {fmtSpeed(selLink.capacity_mbps)}</div>
                    </> : <div className="text-sm font-semibold text-slate-100">{selNode.name}</div>}
                  </div>
                  <button onClick={() => setSel(null)} className="text-slate-500 hover:text-slate-200"><X className="w-4 h-4" /></button>
                </div>
                {selLink && (() => {
                  const p = simRes?.links?.[selLink.id];
                  const sc = rep.scenarios.find(x => x.link_id === selLink.id);
                  const lf = rep.findings.filter(f => f.link_id === selLink.id);
                  return (
                    <div className="mt-2 space-y-2 text-xs">
                      <table className="w-full font-mono text-xs">
                        <thead className="text-[10px] uppercase text-slate-500"><tr><Th></Th><Th right>{selLink.a_name.slice(0, 12)}→</Th><Th right>←{selLink.b_name.slice(0, 12)}</Th></tr></thead>
                        <tbody className="text-slate-200">
                          <tr><td className="px-2 text-slate-500">custo OSPF</td>
                            {["ab", "ba"].map(d => (
                              <td key={d} className="px-2 text-right">
                                <input type="number" min={1} max={65535} placeholder={String(selLink[`cost_${d}`] ?? "—")} value={sim.costs[`${selLink.id}:${d}`] ?? ""}
                                       onChange={e => setCost(selLink.id, d, e.target.value)} disabled={selLink[`cost_${d}`] == null}
                                       className="w-20 h-7 bg-sunken border border-line rounded px-1.5 text-right" title="Digite para simular outro custo" data-testid={`na-cost-${d}`} />
                              </td>
                            ))}</tr>
                          <tr><td className="px-2 text-slate-500">uso agora</td><td className="px-2 text-right">{selLink.ab_pct ?? "—"}%</td><td className="px-2 text-right">{selLink.ba_pct ?? "—"}%</td></tr>
                          {p && <tr><td className="px-2 text-amber-300">na simulação</td>
                            <td className="px-2 text-right font-bold" style={{ color: pctColor(p.ab_pct) }}>{p.down ? "down" : `${p.ab_pct ?? "—"}%`}</td>
                            <td className="px-2 text-right font-bold" style={{ color: pctColor(p.ba_pct) }}>{p.down ? "down" : `${p.ba_pct ?? "—"}%`}</td></tr>}
                        </tbody>
                      </table>
                      <div className="text-[11px] font-mono text-slate-400">
                        esperado ~{selLink.expected_cost ?? "—"} · área {selLink.area_a ?? "—"}{selLink.area_a !== selLink.area_b ? `/${selLink.area_b}` : ""} · {selLink.type_a ?? "—"} · MTU {selLink.mtu_a ?? "—"}/{selLink.mtu_b ?? "—"} · adjacência {selLink.adjacency ?? "—"}
                        {!selLink.in_spf && <span className="text-amber-300"> · fora do cálculo de rotas</span>}
                        {rep.mpls && <> · LDP {selLink.ldp_a === false ? <span className="text-red-300">✕</span> : selLink.ldp_a ? "✓" : "—"}/{selLink.ldp_b === false ? <span className="text-red-300">✕</span> : selLink.ldp_b ? "✓" : "—"}{selLink.ldp_session ? ` · sessão ${selLink.ldp_session}` : ""}</>}
                      </div>
                      {sc && <div className="text-[11px] text-slate-300">Se cair: {sc.isolated_nodes.length ? <b className="text-red-300">isola {sc.isolated_nodes.join(", ")}</b> : "ninguém fica isolado"}
                        {sc.worst_link && <> · mais carregado: {sc.worst_link} <b style={{ color: pctColor(sc.worst_pct) }}>{num(sc.worst_pct, 0)}%</b></>}</div>}
                      {selLink.in_spf && (
                        <Button size="sm" variant="outline" onClick={() => toggle("down", selLink.id)} data-testid="na-sim-down"
                                className={`h-7 text-xs ${sim.down.includes(selLink.id) ? "border-amber-500/60 text-amber-200 bg-amber-500/10" : "border-line bg-sunken text-slate-200"}`}>
                          <Unplug className="w-3.5 h-3.5 mr-1" /> {sim.down.includes(selLink.id) ? "Religar (desfazer queda)" : "Simular queda deste enlace"}
                        </Button>
                      )}
                      {lf.map((f, i) => <div key={i} className="text-[11px]"><SevBadge sev={f.sev} /> <span className="text-slate-200">{f.title}</span></div>)}
                    </div>
                  );
                })()}
                {selNode && (
                  <Button size="sm" variant="outline" onClick={() => toggle("downNodes", selNode.id)}
                          className={`mt-2 h-7 text-xs ${sim.downNodes.includes(selNode.id) ? "border-amber-500/60 text-amber-200 bg-amber-500/10" : "border-line bg-sunken text-slate-200"}`}>
                    <Power className="w-3.5 h-3.5 mr-1" /> {sim.downNodes.includes(selNode.id) ? "Religar equipamento" : "Simular parada deste equipamento"}
                  </Button>
                )}
              </div>
            ) : (
              <div className="px-3 py-2 border-b border-line text-[11px] text-slate-500 bg-panel">
                Toque num <b className="text-slate-300">enlace</b> para ver custos e simular queda/novo custo, ou num <b className="text-slate-300">equipamento</b> para simular a parada dele.
              </div>
            )}

            <div className="flex gap-1 px-2 border-b border-line overflow-x-auto shrink-0">
              {TABS.map(([k, l]) => (
                <button key={k} onClick={() => setTab(k)} className={`px-3 py-2 text-xs -mb-px border-b-2 whitespace-nowrap ${tab === k ? "border-brand text-slate-100" : "border-transparent text-slate-400"}`}>
                  {l}{k === "findings" ? ` (${rep.findings.length})` : k === "bgp" ? ` (${rep.bgp.length})` : ""}
                </button>
              ))}
            </div>
            <div className="flex-1 min-h-0 overflow-y-auto p-2 text-xs" data-testid={`na-tab-${tab}`}>
              {tab === "findings" && <>
                <div className="flex gap-1 mb-2">
                  {["crit", "warn", "info"].map(k => (
                    <button key={k} onClick={() => setSevOn(v => ({ ...v, [k]: !v[k] }))}
                            className={`px-2 py-0.5 rounded border text-[11px] font-mono ${sevOn[k] ? "" : "opacity-40"}`} style={{ borderColor: SEV[k].color, color: SEV[k].color }}>
                      {SEV[k].label} {rep.summary[k]}
                    </button>
                  ))}
                </div>
                {shownFindings.length === 0 && <div className="text-center text-slate-500 py-8">{rep.findings.length ? "Nada com esse filtro." : "Nenhum problema encontrado. 🎉"}</div>}
                {shownFindings.map((f, i) => (
                  <div key={i} onClick={() => f.link_id && setSel({ type: "link", id: f.link_id })}
                       className={`rounded border border-line bg-panel p-2 mb-1.5 ${f.link_id ? "cursor-pointer hover:border-line2" : ""} ${sel?.id && f.link_id === sel.id ? "border-brand" : ""}`}>
                    <div className="flex items-center gap-2"><SevBadge sev={f.sev} /><span className="text-[10px] text-slate-500">{f.cat}</span></div>
                    <div className="text-slate-100 font-semibold mt-1">{f.title}</div>
                    {f.detail && <div className="text-slate-400 mt-0.5">{f.detail}</div>}
                    {f.where && <div className="text-[10px] font-mono text-slate-500 mt-0.5">{f.where}</div>}
                    {f.fix && <div className="text-slate-300 mt-1 border-l-2 border-brand/60 pl-2">{f.fix}</div>}
                  </div>
                ))}
              </>}
              {tab === "ospf" && <>
                <div className="text-[11px] text-slate-500 mb-1.5">Referência {rep.opts.ref_inferred ? "descoberta na rede" : "definida"}: ~{fmtSpeed(rep.opts.ref_used_mbps)} (custo esperado = referência ÷ banda).</div>
                <table className="w-full font-mono text-xs">
                  <thead className="text-[10px] uppercase text-slate-500"><tr><Th>Enlace</Th><Th right>A→B</Th><Th right>B→A</Th><Th right>esp.</Th><Th>adj.</Th></tr></thead>
                  <tbody>
                    {rep.links.map(l => (
                      <tr key={l.id} onClick={() => setSel({ type: "link", id: l.id })} className={`border-t border-line cursor-pointer hover:bg-slate-800/40 ${sel?.id === l.id ? "bg-brand/10" : ""}`}>
                        <td className="px-2 py-1.5"><div className="text-slate-200 truncate max-w-[200px]">{l.a_name} ↔ {l.b_name}</div><div className="text-[10px] text-slate-500">{fmtSpeed(l.capacity_mbps)}{l.sev ? " · " : ""}{l.sev && <span style={{ color: SEV[l.sev].color }}>{SEV[l.sev].label}</span>}</div></td>
                        <td className="px-2 text-right" style={{ color: l.cost_ab !== l.cost_ba ? STATUS.warning : undefined }}>{l.cost_ab ?? "—"}</td>
                        <td className="px-2 text-right" style={{ color: l.cost_ab !== l.cost_ba ? STATUS.warning : undefined }}>{l.cost_ba ?? "—"}</td>
                        <td className="px-2 text-right text-slate-500">{l.expected_cost ?? "—"}</td>
                        <td className="px-2" style={{ color: l.adjacency && l.adjacency !== "full" ? STATUS.critical : undefined }}>{l.adjacency ?? "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </>}
              {tab === "scen" && <>
                <div className="text-[11px] text-slate-500 mb-1.5">Se cada enlace cair: quem fica isolado e qual enlace mais sofre (estimativa com o tráfego atual). Toque para simular no mapa.</div>
                <table className="w-full font-mono text-xs">
                  <thead className="text-[10px] uppercase text-slate-500"><tr><Th>Se cair</Th><Th>Isola</Th><Th right>pior</Th></tr></thead>
                  <tbody>
                    {rep.scenarios.map(x => (
                      <tr key={x.link_id} onClick={() => { setSel({ type: "link", id: x.link_id }); setSim({ down: [x.link_id], downNodes: [], costs: {} }); }}
                          className="border-t border-line cursor-pointer hover:bg-slate-800/40">
                        <td className="px-2 py-1.5 text-slate-200">{x.name}</td>
                        <td className="px-2" style={{ color: x.isolated_nodes.length ? STATUS.critical : "#6E7B7E" }}>{x.isolated_nodes.join(", ") || "—"}</td>
                        <td className="px-2 text-right"><div style={{ color: pctColor(x.worst_pct) }}>{num(x.worst_pct, 0)}%</div><div className="text-[10px] text-slate-500 truncate max-w-[150px]">{x.worst_link}</div></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </>}
              {tab === "bgp" && (
                rep.bgp.length === 0 ? <div className="text-center text-slate-500 py-8">Nenhuma sessão BGP lida (BGP4-MIB).</div> :
                <table className="w-full font-mono text-xs">
                  <thead className="text-[10px] uppercase text-slate-500"><tr><Th>Peer</Th><Th>Estado</Th><Th right>há</Th></tr></thead>
                  <tbody>
                    {rep.bgp.map((p, i) => (
                      <tr key={i} className="border-t border-line">
                        <td className="px-2 py-1.5"><div className="text-slate-200">{p.ip}</div><div className="text-[10px] text-slate-500">{p.device} · {p.remote_as === 23456 ? "AS 4 bytes" : `AS${p.remote_as}`}</div></td>
                        <td className="px-2"><span style={{ color: !p.admin_up ? "#6E7B7E" : p.state === "established" ? STATUS.good : STATUS.critical }}>{p.admin_up ? p.state : "shutdown"}</span>
                          {p.last_error && <div className="text-[10px] text-slate-500">{p.last_error}</div>}</td>
                        <td className="px-2 text-right text-slate-400">{p.state === "established" && p.established_sec != null ? (p.established_sec < 3600 ? `${Math.floor(p.established_sec / 60)} min` : p.established_sec < 86400 ? `${Math.floor(p.established_sec / 3600)} h` : `${Math.floor(p.established_sec / 86400)} d`) : "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
              {tab === "mpls" && <MplsTab rep={rep} rid={doc.id} selId={sel?.id} onSelectLink={(id) => setSel({ type: "link", id })} onOpenSettings={() => setMplsCfg(true)} />}
              {tab === "ifaces" && (
                <table className="w-full font-mono text-xs">
                  <thead className="text-[10px] uppercase text-slate-500"><tr><Th>Interface</Th><Th right>erros/s in·out</Th><Th right>desc/s</Th></tr></thead>
                  <tbody>
                    {rep.ifaces.map((i, k) => {
                      const bad = (i.in_err_ps || 0) + (i.out_err_ps || 0) > 0;
                      return (
                        <tr key={k} className="border-t border-line">
                          <td className="px-2 py-1.5"><div className="text-slate-200">{i.device} · {i.iface}</div><div className="text-[10px] text-slate-500 truncate max-w-[210px]">{i.oper}{i.alias ? ` · ${i.alias}` : ""}{i.in_map ? " · no mapa" : ""}</div></td>
                          <td className="px-2 text-right" style={{ color: bad ? STATUS.warning : "#6E7B7E" }}>{num(i.in_err_ps)} · {num(i.out_err_ps)}</td>
                          <td className="px-2 text-right text-slate-400">{num((i.in_disc_ps || 0) + (i.out_disc_ps || 0), 0)}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              )}
            </div>
            <div className="px-3 py-1.5 border-t border-line text-[10px] font-mono text-slate-500 flex items-center gap-2">
              <Activity className="w-3 h-3" /> {fmtWhen(doc.at)} · {rep.summary.devices_ok}/{rep.summary.devices} equip. lidos · coleta {doc.duration_sec}s
              <button className="ml-auto underline" onClick={() => setFitSignal(x => x + 1)}>ajustar mapa</button>
            </div>
          </div>
        </div>
      )}
      {mplsCfg && <MplsSettingsDialog devices={devices} onClose={() => setMplsCfg(false)} />}
      {running && doc && <div className="absolute inset-x-0 top-[52px] h-0.5 bg-brand/60 animate-pulse" />}
    </div>
  );
}
