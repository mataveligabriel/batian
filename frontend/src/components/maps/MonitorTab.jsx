import React, { useEffect, useMemo, useState } from "react";
import { api, formatApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { toast } from "sonner";
import { BellRing, Plus, Trash2, Loader2, CheckCircle2, XCircle, HelpCircle, Search, RefreshCw } from "lucide-react";
import { InterfacePicker } from "@/components/maps/InterfacePicker";
import { fmtBps, isDown, OPER_LABEL } from "@/lib/netfmt";

const fmt = (iso) => iso ? new Date(iso).toLocaleString("pt-BR") : "—";
const dur = (s) => !s ? "" : s < 60 ? `${s}s` : s < 3600 ? `${Math.round(s / 60)} min` : s < 86400 ? `${Math.floor(s / 3600)}h${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}` : `${Math.floor(s / 86400)}d`;

function StatusCell({ oper }) {
  if (oper === "up") return <span className="inline-flex items-center gap-1 text-emerald-400"><CheckCircle2 className="w-3.5 h-3.5" /> up</span>;
  if (isDown(oper)) return <span className="inline-flex items-center gap-1 text-red-400"><XCircle className="w-3.5 h-3.5" /> {OPER_LABEL[oper] || "down"}</span>;
  return <span className="inline-flex items-center gap-1 text-slate-500"><HelpCircle className="w-3.5 h-3.5" /> aguardando</span>;
}

/** Escolher interfaces que alarmam no Telegram quando caem/voltam. */
export function MonitorTab({ devices }) {
  const [rows, setRows] = useState([]);
  const [events, setEvents] = useState([]);
  const [editDev, setEditDev] = useState(null);
  const [sel, setSel] = useState(new Map());
  const [saving, setSaving] = useState(false);
  const [q, setQ] = useState("");

  const load = async () => {
    const [a, b] = await Promise.all([api.get("/monitor/interfaces"), api.get("/monitor/events", { params: { limit: 100 } })]);
    setRows(a.data); setEvents(b.data);
  };
  useEffect(() => { load().catch(() => {}); const t = setInterval(() => load().catch(() => {}), 15000); return () => clearInterval(t); }, []);

  const byDevice = useMemo(() => {
    const g = {};
    rows.forEach(r => { (g[r.device_id] = g[r.device_id] || { name: r.device_name, error: r.snmp_error, items: [] }).items.push(r); });
    return g;
  }, [rows]);

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
  const removeOne = (r) => save(r.device_id, rows.filter(x => x.device_id === r.device_id && x.if_index !== r.if_index).map(x => ({ index: x.if_index, name: x.if_name, alias: x.alias || "" })));

  const devList = devices.filter(d => !q || `${d.name} ${d.host} ${(d.tags || []).join(" ")}`.toLowerCase().includes(q.toLowerCase()));

  return (
    <div className="grid grid-cols-1 xl:grid-cols-5 gap-4" data-testid="monitor-tab">
      <div className="xl:col-span-3 space-y-4">
        <Card className="bg-[#111722] border-[#1E293B] overflow-hidden">
          <div className="px-4 py-3 border-b border-[#1E293B] flex items-center gap-2">
            <BellRing className="w-4 h-4 text-[#4DA3FF]" />
            <div className="text-xs uppercase tracking-widest text-slate-400 font-mono">Interfaces monitoradas</div>
            <span className="ml-auto text-[11px] text-slate-500">caiu/voltou → Telegram configurado em Automação</span>
          </div>
          {rows.length === 0 && <div className="p-6 text-sm text-slate-500 font-mono">Nenhuma interface monitorada. Escolha um equipamento ao lado →</div>}
          {Object.entries(byDevice).map(([devId, g]) => (
            <div key={devId} className="border-b border-[#1E293B] last:border-b-0">
              <div className="px-4 py-2 bg-[#0B111C] flex items-center gap-2">
                <span className="text-sm text-slate-100 font-medium">{g.name}</span>
                {g.error && <span className="text-[11px] text-amber-300 font-mono truncate" title={g.error}>⚠ {g.error}</span>}
                <Button size="sm" variant="ghost" className="ml-auto h-7 text-xs text-slate-300 hover:bg-slate-800"
                        onClick={() => openDevice(devices.find(d => d.id === devId) || { id: devId, name: g.name })}>editar</Button>
              </div>
              <table className="w-full text-sm">
                <tbody className="divide-y divide-[#1E293B]">
                  {g.items.map(r => (
                    <tr key={r.if_index} data-testid={`mon-row-${devId}-${r.if_index}`}>
                      <td className="px-4 py-1.5 font-mono text-xs text-slate-100">{r.if_name}</td>
                      <td className="px-2 py-1.5 text-xs text-slate-400 truncate max-w-[180px]">{r.alias}</td>
                      <td className="px-2 py-1.5 text-xs font-mono"><StatusCell oper={r.oper} /></td>
                      <td className="px-2 py-1.5 text-[11px] font-mono text-slate-400 whitespace-nowrap">↓ {fmtBps(r.in_bps)} · ↑ {fmtBps(r.out_bps)}</td>
                      <td className="px-2 py-1.5 text-right">
                        <Button size="sm" variant="ghost" onClick={() => removeOne(r)} className="h-6 text-red-400 hover:bg-red-950/40" title="Parar de monitorar"><Trash2 className="w-3.5 h-3.5" /></Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ))}
        </Card>

        <Card className="bg-[#111722] border-[#1E293B] overflow-hidden">
          <div className="px-4 py-3 border-b border-[#1E293B] flex items-center">
            <div className="text-xs uppercase tracking-widest text-slate-400 font-mono">Últimos eventos</div>
            <Button size="sm" variant="ghost" onClick={() => load()} className="ml-auto h-7 text-slate-400 hover:bg-slate-800"><RefreshCw className="w-3.5 h-3.5" /></Button>
          </div>
          {events.length === 0 && <div className="p-4 text-sm text-slate-500 font-mono">Nenhuma queda registrada.</div>}
          <div className="divide-y divide-[#1E293B] max-h-80 overflow-y-auto" data-testid="mon-events">
            {events.map(e => (
              <div key={e.id} className="px-4 py-2 text-sm flex items-center gap-3">
                {e.status === "down" ? <XCircle className="w-4 h-4 text-red-400 shrink-0" /> : <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0" />}
                <div className="min-w-0">
                  <div className="text-slate-100 truncate">{e.device_name} · <span className="font-mono">{e.if_name}</span> {e.status === "down" ? "caiu" : "voltou"}
                    {e.admin_down && <span className="text-amber-300 text-xs"> (shutdown)</span>}</div>
                  <div className="text-[11px] font-mono text-slate-500">{fmt(e.at)}{e.alias ? ` · ${e.alias}` : ""}{e.status === "up" && e.prev_duration_s ? ` · fora por ${dur(e.prev_duration_s)}` : ""}</div>
                </div>
              </div>
            ))}
          </div>
        </Card>
      </div>

      <Card className="bg-[#111722] border-[#1E293B] xl:col-span-2 p-4 h-fit">
        {!editDev ? (
          <>
            <div className="text-xs uppercase tracking-widest text-slate-400 font-mono mb-2 flex items-center gap-2"><Plus className="w-3.5 h-3.5" /> Monitorar interfaces de…</div>
            <div className="relative mb-2">
              <Search className="w-3.5 h-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-500" />
              <Input value={q} onChange={e => setQ(e.target.value)} placeholder="Buscar equipamento…" className="pl-8 h-8 bg-[#05070A] border-[#1E293B] text-sm" />
            </div>
            <div className="max-h-[60vh] overflow-y-auto border border-[#1E293B] rounded divide-y divide-[#1E293B]">
              {devList.map(d => {
                const n = rows.filter(r => r.device_id === d.id).length;
                return (
                  <button key={d.id} onClick={() => openDevice(d)} data-testid={`mon-dev-${d.id}`}
                          className="w-full text-left px-3 py-2 hover:bg-slate-800/60 flex items-center gap-2">
                    <span className="text-sm text-slate-100 truncate">{d.name}</span>
                    <span className="text-[11px] font-mono text-slate-500 truncate">{d.host}</span>
                    {n > 0 && <span className="ml-auto text-[10px] font-mono px-1.5 rounded bg-[#007AFF]/20 text-[#93C5FD]">{n}</span>}
                  </button>
                );
              })}
            </div>
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
              <Button onClick={() => save(editDev.id, [...sel.values()])} disabled={saving} data-testid="mon-save" className="bg-[#007AFF] hover:bg-[#0062CC]">
                {saving && <Loader2 className="w-4 h-4 mr-2 animate-spin" />} Salvar
              </Button>
            </div>
          </>
        )}
      </Card>
    </div>
  );
}
