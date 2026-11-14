import React, { useEffect, useMemo, useRef, useState } from "react";
import { api, formatApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { toast } from "sonner";
import { BellRing, Plus, Trash2, Loader2, CheckCircle2, XCircle, HelpCircle, Search, RefreshCw, ChevronDown, ChevronRight, Wand2, RotateCcw } from "lucide-react";
import { InterfacePicker } from "@/components/maps/InterfacePicker";
import { fmtBps, isDown, OPER_LABEL } from "@/lib/netfmt";

const fmt = (iso) => iso ? new Date(iso).toLocaleString("pt-BR") : "—";
const dur = (s) => !s ? "" : s < 60 ? `${s}s` : s < 3600 ? `${Math.round(s / 60)} min` : s < 86400 ? `${Math.floor(s / 3600)}h${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}` : `${Math.floor(s / 86400)}d`;
const NO_GROUP = "Sem grupo";
const byName = (a, b) => a.name.localeCompare(b.name, "pt-BR", { numeric: true });

function StatusCell({ oper }) {
  if (oper === "up") return <span className="inline-flex items-center gap-1 text-emerald-400"><CheckCircle2 className="w-3.5 h-3.5" /> up</span>;
  if (isDown(oper)) return <span className="inline-flex items-center gap-1 text-red-400"><XCircle className="w-3.5 h-3.5" /> {OPER_LABEL[oper] || "down"}</span>;
  return <span className="inline-flex items-center gap-1 text-slate-500"><HelpCircle className="w-3.5 h-3.5" /> aguardando</span>;
}

/** Interfaces que alarmam no Telegram quando caem/voltam: por equipamento, ou vários de uma vez com regra automática. */
export function MonitorTab({ devices }) {
  const [rows, setRows] = useState([]);
  const [events, setEvents] = useState([]);
  const [auto, setAuto] = useState({ presets: {}, rules: {} });
  const [editDev, setEditDev] = useState(null);
  const [sel, setSel] = useState(new Map());
  const [saving, setSaving] = useState(false);
  const [q, setQ] = useState("");
  const [picked, setPicked] = useState(() => new Set());   // equipamentos marcados para monitorar em lote
  const [closed, setClosed] = useState(() => new Set());   // grupos recolhidos (lista de equipamentos)
  const [open, setOpen] = useState(() => new Set());       // equipamentos abertos (lista de monitoradas)
  const [chk, setChk] = useState(() => new Set());         // interfaces marcadas para tirar: "dev|idx"
  const [showIgn, setShowIgn] = useState(null);
  const [rule, setRule] = useState({ preset: "physical_up", include: "", exclude: "", only_up: true, only_alias: false });
  const [keepAuto, setKeepAuto] = useState(true);
  const [job, setJob] = useState(null);
  const poll = useRef(null);

  const load = async () => {
    const [a, b, c] = await Promise.all([api.get("/monitor/interfaces"), api.get("/monitor/events", { params: { limit: 100 } }), api.get("/monitor/auto")]);
    setRows(a.data); setEvents(b.data); setAuto(c.data);
  };
  useEffect(() => { load().catch(() => {}); const t = setInterval(() => load().catch(() => {}), 15000); return () => { clearInterval(t); clearInterval(poll.current); }; }, []);
  useEffect(() => { const p = auto.presets?.physical_up; if (p) setRule(r => (r.preset === "physical_up" && !r.exclude ? { ...r, ...p, preset: "physical_up" } : r)); }, [auto.presets]);

  const byDevice = useMemo(() => {
    const g = {};
    rows.forEach(r => { (g[r.device_id] = g[r.device_id] || { name: r.device_name, error: r.snmp_error, items: [] }).items.push(r); });
    Object.entries(auto.rules || {}).forEach(([id, r]) => { if (!g[id]) g[id] = { name: devices.find(d => d.id === id)?.name || "?", error: r.last_error, items: [] }; });
    return g;
  }, [rows, auto.rules, devices]);

  const openDevice = (dev) => {
    setEditDev(dev);
    setSel(new Map(rows.filter(r => r.device_id === dev.id).map(r => [r.if_index, { index: r.if_index, name: r.if_name, alias: r.alias }])));
  };
  const toggle = (i) => setSel(prev => { const n = new Map(prev); n.has(i.index) ? n.delete(i.index) : n.set(i.index, { index: i.index, name: i.name, alias: i.alias || "" }); return n; });
  const save = async (devId, ifaces) => {
    setSaving(true);
    try {
      const { data } = await api.put(`/devices/${devId}/monitor`, { interfaces: ifaces });
      toast.success(`${data.monitored} interface(s) monitorada(s)`);
      setEditDev(null); await load();
    } catch (e) { toast.error(formatApiError(e)); }
    finally { setSaving(false); }
  };
  const remove = async (items, device_ids = []) => {
    try {
      const { data } = await api.post("/monitor/remove", { items, device_ids });
      toast.success(`${data.removed} interface(s) fora do monitoramento`);
      setChk(new Set()); await load();
    } catch (e) { toast.error(formatApiError(e)); }
  };
  const removeChecked = () => remove([...chk].map(k => { const [device_id, i] = k.split("|"); return { device_id, if_index: Number(i) }; }));
  const stopDevice = (devId, name) => { if (window.confirm(`Parar de monitorar todas as interfaces de ${name}?`)) remove([], [devId]); };
  const setAutoRule = async (devId, body, msg) => {
    try { await api.put(`/monitor/auto/${devId}`, body); if (msg) toast.success(msg); await load(); } catch (e) { toast.error(formatApiError(e)); }
  };

  // ----- equipamentos por grupo (tag) -----
  const groups = useMemo(() => {
    const ql = q.toLowerCase();
    const list = devices.filter(d => !ql || `${d.name} ${d.host} ${(d.tags || []).join(" ")}`.toLowerCase().includes(ql));
    const g = {};
    list.forEach(d => ((d.tags || []).length ? d.tags : [NO_GROUP]).forEach(t => (g[t] = g[t] || []).push(d)));
    return Object.entries(g).map(([name, ds]) => ({ name, devs: [...ds].sort(byName) }))
      .sort((a, b) => (a.name === NO_GROUP) - (b.name === NO_GROUP) || a.name.localeCompare(b.name, "pt-BR", { numeric: true }));
  }, [devices, q]);
  const nMon = useMemo(() => { const c = {}; rows.forEach(r => { c[r.device_id] = (c[r.device_id] || 0) + 1; }); return c; }, [rows]);
  const pick = (ids, on) => setPicked(prev => { const n = new Set(prev); ids.forEach(i => (on ? n.add(i) : n.delete(i))); return n; });
  const flip = (set, setter, k) => setter(prev => { const n = new Set(prev); n.has(k) ? n.delete(k) : n.add(k); return n; });
  const setPreset = (k) => setRule({ ...(auto.presets[k] || {}), preset: k });

  const runBulk = async () => {
    try {
      const { data } = await api.post("/monitor/bulk", { device_ids: [...picked], rule, auto: keepAuto });
      setJob(data);
      clearInterval(poll.current);
      poll.current = setInterval(async () => {
        try {
          const j = (await api.get(`/monitor/bulk/${data.id}`)).data;
          setJob(j);
          if (!j.running) {
            clearInterval(poll.current);
            const fail = j.results.filter(r => !r.ok).length;
            (fail ? toast.warning : toast.success)(`${j.added} interface(s) adicionada(s) em ${j.total - fail} equipamento(s)${fail ? ` · ${fail} sem resposta SNMP` : ""}`);
            setPicked(new Set()); load();
          }
        } catch { clearInterval(poll.current); }
      }, 1500);
    } catch (e) { toast.error(formatApiError(e)); }
  };

  const chip = "text-[10px] px-1.5 py-0.5 rounded border";
  return (
    <div className="grid grid-cols-1 xl:grid-cols-5 gap-4" data-testid="monitor-tab">
      <div className="xl:col-span-3 space-y-4">
        <Card className="bg-surface border-line overflow-hidden">
          <div className="px-4 py-3 border-b border-line flex flex-wrap items-center gap-2">
            <BellRing className="w-4 h-4 text-brand-soft" />
            <div className="text-xs text-slate-400">Interfaces monitoradas · {rows.length}</div>
            {chk.size > 0 && <Button size="sm" onClick={removeChecked} className="h-7 text-xs bg-red-600 hover:bg-red-700" data-testid="mon-remove-checked"><Trash2 className="w-3.5 h-3.5 mr-1" />Tirar {chk.size} do monitoramento</Button>}
            <span className="ml-auto text-[11px] text-slate-500">caiu/voltou → Telegram (grupos em Automação)</span>
          </div>
          {Object.keys(byDevice).length === 0 && <div className="p-6 text-sm text-slate-500 font-mono">Nenhuma interface monitorada. Marque os equipamentos ao lado →</div>}
          {Object.entries(byDevice).sort((a, b) => a[1].name.localeCompare(b[1].name, "pt-BR", { numeric: true })).map(([devId, g]) => {
            const r = auto.rules?.[devId];
            const down = g.items.filter(x => isDown(x.oper)).length;
            const isOpen = open.has(devId);
            const ign = r?.ignored || [];
            return (
              <div key={devId} className="border-b border-line last:border-b-0" data-testid={`mon-dev-block-${devId}`}>
                <div className="px-3 py-2 bg-panel flex flex-wrap items-center gap-2">
                  <button onClick={() => flip(open, setOpen, devId)} className="flex items-center gap-1.5 min-w-0" aria-expanded={isOpen}>
                    {isOpen ? <ChevronDown className="w-4 h-4 text-slate-400" /> : <ChevronRight className="w-4 h-4 text-slate-400" />}
                    <span className="text-sm text-slate-100 font-medium truncate">{g.name}</span>
                  </button>
                  <span className="text-[11px] font-mono text-slate-400">{g.items.length} interface(s)</span>
                  {down > 0 && <span className={`${chip} border-red-500/50 text-red-300`}>{down} down</span>}
                  {r && <button onClick={() => setAutoRule(devId, { enabled: !r.enabled }, r.enabled ? "Automático pausado" : "Automático ligado")}
                                title={r.enabled ? "Interfaces novas entram sozinhas. Clique para pausar." : "Pausado. Clique para ligar."}
                                className={`${chip} ${r.enabled ? "border-emerald-500/50 text-emerald-300" : "border-line text-slate-500"}`} data-testid={`mon-auto-${devId}`}>
                    <Wand2 className="w-3 h-3 inline mr-1" />auto: {r.enabled ? (auto.presets?.[r.preset]?.label || "regra").toLowerCase() : "pausado"}</button>}
                  {ign.length > 0 && <button onClick={() => setShowIgn(showIgn === devId ? null : devId)} className={`${chip} border-line text-slate-400 hover:text-slate-200`}>{ign.length} retirada(s)</button>}
                  {(g.error || r?.last_error) && <span className="text-[11px] text-amber-300 font-mono truncate max-w-[220px]" title={g.error || r.last_error}>⚠ {g.error || r.last_error}</span>}
                  <div className="ml-auto flex">
                    <Button size="sm" variant="ghost" className="h-7 text-xs text-slate-300 hover:bg-slate-800"
                            onClick={() => openDevice(devices.find(d => d.id === devId) || { id: devId, name: g.name })}>escolher</Button>
                    <Button size="sm" variant="ghost" className="h-7 text-xs text-red-400 hover:bg-red-950/40" onClick={() => stopDevice(devId, g.name)} title="Parar de monitorar este equipamento">
                      <Trash2 className="w-3.5 h-3.5" /></Button>
                  </div>
                </div>
                {showIgn === devId && ign.length > 0 && (
                  <div className="px-4 py-2 bg-sunken/60 border-t border-line text-[11px]">
                    <div className="text-slate-400 mb-1">Retiradas por você — a regra automática não traz de volta:</div>
                    <div className="flex flex-wrap gap-1.5">
                      {ign.map(x => (
                        <button key={x.index} onClick={() => setAutoRule(devId, { restore: [x.index] }, `${x.name || x.index} voltou a ser monitorada`)}
                                className="px-1.5 py-0.5 rounded border border-line font-mono text-slate-300 hover:border-brand" title="Voltar a monitorar">
                          <RotateCcw className="w-3 h-3 inline mr-1" />{x.name || `if${x.index}`}</button>
                      ))}
                    </div>
                  </div>
                )}
                {isOpen && (
                  <table className="w-full text-sm">
                    <tbody className="divide-y divide-line">
                      {g.items.map(x => {
                        const k = `${devId}|${x.if_index}`;
                        return (
                          <tr key={x.if_index} data-testid={`mon-row-${devId}-${x.if_index}`} className={chk.has(k) ? "bg-red-950/20" : ""}>
                            <td className="pl-4 w-6"><input type="checkbox" checked={chk.has(k)} onChange={() => flip(chk, setChk, k)} aria-label={`Marcar ${x.if_name}`} /></td>
                            <td className="px-2 py-1.5 font-mono text-xs text-slate-100">{x.if_name}</td>
                            <td className="px-2 py-1.5 text-xs text-slate-400 truncate max-w-[180px]">{x.alias}</td>
                            <td className="px-2 py-1.5 text-xs font-mono"><StatusCell oper={x.oper} /></td>
                            <td className="px-2 py-1.5 text-[11px] font-mono text-slate-400 whitespace-nowrap">↓ {fmtBps(x.in_bps)} · ↑ {fmtBps(x.out_bps)}</td>
                            <td className="px-2 py-1.5 text-right">
                              <Button size="sm" variant="ghost" onClick={() => remove([{ device_id: devId, if_index: x.if_index }])} className="h-6 text-red-400 hover:bg-red-950/40" title="Tirar do monitoramento (não volta sozinha)"><Trash2 className="w-3.5 h-3.5" /></Button>
                            </td>
                          </tr>
                        );
                      })}
                      {!g.items.length && <tr><td className="px-4 py-2 text-xs text-slate-500">Nenhuma interface casou com a regra ainda.</td></tr>}
                    </tbody>
                  </table>
                )}
              </div>
            );
          })}
        </Card>

        <Card className="bg-surface border-line overflow-hidden">
          <div className="px-4 py-3 border-b border-line flex items-center">
            <div className="text-xs text-slate-400">Últimos eventos</div>
            <Button size="sm" variant="ghost" onClick={() => load()} className="ml-auto h-7 text-slate-400 hover:bg-slate-800"><RefreshCw className="w-3.5 h-3.5" /></Button>
          </div>
          {events.length === 0 && <div className="p-4 text-sm text-slate-500 font-mono">Nenhuma queda registrada.</div>}
          <div className="divide-y divide-line max-h-80 overflow-y-auto" data-testid="mon-events">
            {events.map(e => (
              <div key={e.id} className="px-4 py-2 text-sm flex items-center gap-3">
                {e.status === "down" ? <XCircle className="w-4 h-4 text-red-400 shrink-0" /> : <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0" />}
                <div className="min-w-0 flex-1">
                  <div className="text-slate-100 truncate">{e.device_name} · <span className="font-mono">{e.if_name}</span> {e.status === "down" ? "caiu" : "voltou"}
                    {e.admin_down && <span className="text-amber-300 text-xs"> (shutdown)</span>}</div>
                  <div className="text-[11px] font-mono text-slate-500">{fmt(e.at)}{e.alias ? ` · ${e.alias}` : ""}{e.status === "up" && e.prev_duration_s ? ` · fora por ${dur(e.prev_duration_s)}` : ""}</div>
                </div>
                {rows.some(r => r.device_id === e.device_id && r.if_index === e.if_index) && (
                  <Button size="sm" variant="ghost" onClick={() => remove([{ device_id: e.device_id, if_index: e.if_index }])} className="h-6 text-[11px] text-slate-500 hover:text-red-300" title="Esta interface alarma demais? Tire do monitoramento.">tirar</Button>
                )}
              </div>
            ))}
          </div>
        </Card>
      </div>

      <Card className="bg-surface border-line xl:col-span-2 p-4 h-fit">
        {!editDev ? (
          <>
            <div className="text-xs text-slate-400 mb-2 flex items-center gap-2"><Plus className="w-3.5 h-3.5" /> Monitorar interfaces de…
              {picked.size > 0 && <button onClick={() => setPicked(new Set())} className="ml-auto text-[11px] text-slate-500 hover:text-slate-300">limpar seleção</button>}</div>
            <div className="relative mb-2">
              <Search className="w-3.5 h-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-500" />
              <Input value={q} onChange={e => setQ(e.target.value)} placeholder="Buscar equipamento ou grupo…" className="pl-8 h-8 bg-sunken border-line text-sm" />
            </div>
            <div className="max-h-[52vh] overflow-y-auto border border-line rounded" data-testid="mon-groups">
              {groups.map(g => {
                const ids = g.devs.map(d => d.id);
                const n = ids.filter(i => picked.has(i)).length;
                const isClosed = closed.has(g.name) && !q;
                return (
                  <div key={g.name} className="border-b border-line last:border-b-0">
                    <div className="flex items-center gap-2 px-2.5 py-1.5 bg-panel sticky top-0 z-10">
                      <input type="checkbox" checked={n === ids.length && n > 0} ref={el => { if (el) el.indeterminate = n > 0 && n < ids.length; }}
                             onChange={e => pick(ids, e.target.checked)} aria-label={`Selecionar o grupo ${g.name}`} data-testid={`mon-group-${g.name}`} />
                      <button onClick={() => flip(closed, setClosed, g.name)} className="flex items-center gap-1 flex-1 min-w-0 text-left">
                        {isClosed ? <ChevronRight className="w-3.5 h-3.5 text-slate-500" /> : <ChevronDown className="w-3.5 h-3.5 text-slate-500" />}
                        <span className="text-xs font-semibold text-slate-200 truncate">{g.name}</span>
                        <span className="text-[10px] font-mono text-slate-500">{n ? `${n}/` : ""}{ids.length}</span>
                      </button>
                    </div>
                    {!isClosed && g.devs.map(d => (
                      <div key={d.id} className={`flex items-center gap-2 pl-5 pr-2 py-1.5 border-t border-line/60 hover:bg-slate-800/40 ${picked.has(d.id) ? "bg-brand/10" : ""}`}>
                        <input type="checkbox" checked={picked.has(d.id)} onChange={e => pick([d.id], e.target.checked)} aria-label={`Selecionar ${d.name}`} data-testid={`mon-pick-${d.id}`} />
                        <button onClick={() => openDevice(d)} data-testid={`mon-dev-${d.id}`} className="flex items-center gap-2 flex-1 min-w-0 text-left" title="Escolher as interfaces deste equipamento uma a uma">
                          <span className="text-sm text-slate-100 truncate">{d.name}</span>
                          <span className="text-[11px] font-mono text-slate-500 truncate">{d.host}</span>
                        </button>
                        {auto.rules?.[d.id]?.enabled && <Wand2 className="w-3 h-3 text-emerald-400 shrink-0" aria-label="automático ligado" />}
                        {nMon[d.id] > 0 && <span className="text-[10px] font-mono px-1.5 rounded bg-brand/20 text-brand-pale">{nMon[d.id]}</span>}
                      </div>
                    ))}
                  </div>
                );
              })}
              {!groups.length && <div className="p-3 text-xs text-slate-500">Nenhum equipamento.</div>}
            </div>

            {picked.size > 0 ? (
              <div className="mt-3 rounded-md border border-brand/40 bg-brand/5 p-3 space-y-2" data-testid="mon-bulk">
                <div className="text-xs text-slate-200"><b>{picked.size}</b> equipamento(s) selecionado(s) — quais interfaces monitorar?</div>
                <div className="flex flex-wrap gap-1">
                  {Object.entries(auto.presets || {}).map(([k, p]) => (
                    <button key={k} onClick={() => setPreset(k)} className={`px-2 py-1 rounded border text-[11px] ${rule.preset === k ? "border-brand bg-brand/20 text-slate-100" : "border-line text-slate-400"}`}>{p.label}</button>
                  ))}
                </div>
                {rule.preset === "custom" && (
                  <div className="space-y-1.5">
                    <Input value={rule.include} onChange={e => setRule({ ...rule, include: e.target.value })} placeholder="incluir (nome ou descrição): ex. uplink|backbone|100GE" className="h-8 bg-sunken border-line text-xs font-mono" aria-label="Incluir" />
                    <Input value={rule.exclude} onChange={e => setRule({ ...rule, exclude: e.target.value })} placeholder="não incluir (nome): ex. vlan|loopback" className="h-8 bg-sunken border-line text-xs font-mono" aria-label="Não incluir" />
                    <div className="flex gap-3 text-[11px] text-slate-300">
                      <label className="flex items-center gap-1.5"><input type="checkbox" checked={rule.only_up} onChange={e => setRule({ ...rule, only_up: e.target.checked })} /> só as que estão up</label>
                      <label className="flex items-center gap-1.5"><input type="checkbox" checked={rule.only_alias} onChange={e => setRule({ ...rule, only_alias: e.target.checked })} /> só com descrição</label>
                    </div>
                  </div>
                )}
                {rule.preset !== "custom" && <div className="text-[10px] text-slate-500">Ficam de fora loopback, gerência, VLAN lógica, túnel, PPPoE, subinterface (.N) e ONU.</div>}
                <label className="flex items-start gap-2 text-[11px] text-slate-300">
                  <input type="checkbox" className="mt-0.5" checked={keepAuto} onChange={e => setKeepAuto(e.target.checked)} data-testid="mon-keep-auto" />
                  <span>Automático: interface nova que casar com a regra entra sozinha (conferido a cada 30 min, com aviso). As que você tirar não voltam.</span>
                </label>
                <Button onClick={runBulk} disabled={job?.running} className="w-full h-9 bg-brand hover:bg-brand-strong" data-testid="mon-bulk-run">
                  {job?.running ? <><Loader2 className="w-4 h-4 mr-2 animate-spin" />Lendo interfaces… {job.done}/{job.total}</> : <><Wand2 className="w-4 h-4 mr-2" />Monitorar {picked.size} equipamento(s)</>}
                </Button>
              </div>
            ) : (
              <div className="mt-2 text-[11px] text-slate-500">Marque equipamentos (ou o grupo inteiro) para monitorar vários de uma vez. Clique no nome para escolher as interfaces de um só.</div>
            )}
            {job && !job.running && job.results.some(r => !r.ok) && (
              <div className="mt-2 text-[11px] text-amber-200 space-y-0.5" data-testid="mon-bulk-fail">
                {job.results.filter(r => !r.ok).slice(0, 8).map(r => <div key={r.device_id} className="truncate" title={r.error}>⚠ {r.name}: {r.error}</div>)}
              </div>
            )}
          </>
        ) : (
          <>
            <div className="flex items-center gap-2 mb-2">
              <div className="text-sm text-slate-100 font-medium truncate">{editDev.name}</div>
              <span className="ml-auto text-[11px] font-mono text-slate-400">{sel.size} selecionada(s)</span>
            </div>
            <div className="text-[11px] text-slate-500 mb-2">Marque as interfaces importantes (uplinks, trânsito, clientes dedicados). Queda confirmada em 2 leituras seguidas.</div>
            <InterfacePicker deviceId={editDev.id} mode="multi" selected={new Set(sel.keys())} onToggle={toggle} height="h-[50vh]" />
            <div className="flex justify-end gap-2 mt-3">
              <Button variant="ghost" onClick={() => setEditDev(null)}>Voltar</Button>
              <Button onClick={() => save(editDev.id, [...sel.values()])} disabled={saving} data-testid="mon-save" className="bg-brand hover:bg-brand-strong">
                {saving && <Loader2 className="w-4 h-4 mr-2 animate-spin" />} Salvar
              </Button>
            </div>
          </>
        )}
      </Card>
    </div>
  );
}
