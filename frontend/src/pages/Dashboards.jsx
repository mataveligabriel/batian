import React, { useEffect, useMemo, useState } from "react";
import { api, formatApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { toast } from "sonner";
import {
  Gauge, Plus, Pencil, Save, X, Trash2, Copy, ChevronUp, ChevronDown, Activity, Radio, Search, Loader2,
  FolderOpen, Settings2, RefreshCw, Columns2, Square,
} from "lucide-react";
import { InterfacePicker } from "@/components/maps/InterfacePicker";
import { TrafficWidget } from "@/components/dash/TrafficWidget";
import { OpticsPanel } from "@/components/dash/OpticsPanel";

const uid = () => Math.random().toString(36).slice(2, 10);
const RANGES = [[60, "1h"], [360, "6h"], [1440, "24h"], [10080, "7 dias"], [43200, "30 dias"]];
const tabBtn = (on) => `flex items-center gap-2 px-4 py-2 text-sm -mb-px border-b-2 ${on ? "border-[#007AFF] text-slate-100" : "border-transparent text-slate-400 hover:text-slate-200"}`;
const inputCls = "bg-[#05070A] border-[#1E293B]";

function WidgetDialog({ initial, devices, onCancel, onSave }) {
  const [w, setW] = useState(initial || { id: uid(), type: "traffic", title: "", device_id: "", if_index: null, if_name: "", capacity_mbps: null, size: "full", rx_warn_dbm: null, rx_crit_dbm: null });
  const [q, setQ] = useState("");
  const dev = devices.find(d => d.id === w.device_id);
  const list = devices.filter(d => !q || `${d.name} ${d.host} ${(d.tags || []).join(" ")}`.toLowerCase().includes(q.toLowerCase()));
  const num = (v) => (v === "" || v === null || v === undefined ? null : Number(v));
  const ok = w.device_id && w.if_index !== null;
  return (
    <Dialog open onOpenChange={(v) => !v && onCancel()}>
      <DialogContent className="bg-[#111722] border-[#1E293B] text-slate-100 max-w-4xl max-h-[92vh] overflow-y-auto" data-testid="widget-dialog">
        <DialogHeader><DialogTitle>{initial ? "Editar gráfico" : "Novo gráfico"}</DialogTitle></DialogHeader>
        <div className="flex gap-2">
          {[["traffic", "Tráfego (consumo)", Activity], ["optics", "Sinal óptico (RX/TX por lane)", Radio]].map(([v, l, I]) => (
            <button key={v} onClick={() => setW({ ...w, type: v })} data-testid={`wtype-${v}`}
                    className={`flex-1 flex items-center gap-2 px-3 py-2 rounded border text-sm ${w.type === v ? "border-[#007AFF] bg-[#007AFF]/15 text-slate-100" : "border-[#1E293B] text-slate-400"}`}>
              <I className="w-4 h-4" /> {l}
            </button>
          ))}
        </div>
        <div className="grid grid-cols-1 md:grid-cols-5 gap-3">
          <div className="md:col-span-2">
            <Label>Equipamento</Label>
            <div className="relative mt-1"><Search className="w-3.5 h-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-500" />
              <Input value={q} onChange={e => setQ(e.target.value)} placeholder="Buscar…" className={`${inputCls} pl-8 h-8 text-sm`} /></div>
            <div className="h-64 overflow-y-auto border border-[#1E293B] rounded mt-1 divide-y divide-[#111722] bg-[#05070A]">
              {list.map(d => (
                <button key={d.id} onClick={() => setW({ ...w, device_id: d.id, if_index: null, if_name: "" })} data-testid={`wdev-${d.id}`}
                        className={`w-full text-left px-2.5 py-1.5 text-sm ${w.device_id === d.id ? "bg-[#007AFF]/20 text-slate-100" : "text-slate-300 hover:bg-slate-800/60"}`}>
                  <div className="truncate">{d.name}</div><div className="text-[10px] font-mono text-slate-500">{d.host}</div>
                </button>
              ))}
            </div>
          </div>
          <div className="md:col-span-3">
            <Label>Interface {w.if_name && <span className="font-mono text-[#93C5FD] ml-1">{w.if_name}</span>}</Label>
            <div className="mt-1">
              {w.device_id ? (
                <InterfacePicker deviceId={w.device_id} value={w.if_index} height="h-64"
                                 onPick={(i) => setW(prev => ({ ...prev, if_index: i.index, if_name: i.name, title: prev.title || `${dev?.name || ""} · ${i.alias || i.name}` }))} />
              ) : <div className="h-72 flex items-center justify-center text-xs text-slate-500 font-mono border border-[#1E293B] rounded">Escolha o equipamento</div>}
            </div>
          </div>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
          <div className="md:col-span-2"><Label>Título</Label>
            <Input value={w.title} onChange={e => setW({ ...w, title: e.target.value })} placeholder="ex.: Trânsito Operadora X" className={inputCls} data-testid="wtitle" /></div>
          {w.type === "traffic" ? (
            <div><Label>Capacidade contratada (Mbps)</Label>
              <Input type="number" value={w.capacity_mbps ?? ""} onChange={e => setW({ ...w, capacity_mbps: num(e.target.value) })} placeholder="vazio = velocidade da porta" className={`${inputCls} font-mono`} /></div>
          ) : (
            <div className="grid grid-cols-2 gap-2">
              <div><Label>RX atenção (dBm)</Label><Input type="number" step="0.1" value={w.rx_warn_dbm ?? ""} onChange={e => setW({ ...w, rx_warn_dbm: num(e.target.value) })} placeholder="ex.: -8" className={`${inputCls} font-mono`} /></div>
              <div><Label>RX crítico (dBm)</Label><Input type="number" step="0.1" value={w.rx_crit_dbm ?? ""} onChange={e => setW({ ...w, rx_crit_dbm: num(e.target.value) })} placeholder="ex.: -11" className={`${inputCls} font-mono`} /></div>
            </div>
          )}
          <div><Label>Largura</Label>
            <div className="flex gap-1 mt-1">
              {[["full", "Inteira", Square], ["half", "Metade", Columns2]].map(([v, l, I]) => (
                <button key={v} onClick={() => setW({ ...w, size: v })} className={`flex-1 h-9 rounded border text-xs flex items-center justify-center gap-1.5 ${w.size === v ? "border-[#007AFF] bg-[#007AFF]/15 text-slate-100" : "border-[#1E293B] text-slate-400"}`}><I className="w-3.5 h-3.5" /> {l}</button>
              ))}
            </div>
          </div>
        </div>
        {w.type === "optics" && <div className="text-[11px] text-slate-500">Leitura pela CLI a cada 5 min. Interfaces de 40G/100G mostram as 4 lanes. Os limites viram linhas no gráfico e status na tabela.</div>}
        <DialogFooter>
          <Button variant="ghost" onClick={onCancel}>Cancelar</Button>
          <Button disabled={!ok} onClick={() => onSave({ ...w, title: w.title.trim() || `${dev?.name || ""} · ${w.if_name}` })} className="bg-[#007AFF] hover:bg-[#0062CC]" data-testid="wsave">Salvar gráfico</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function OpticsSettingsTab() {
  const [s, setS] = useState(null);
  const [cmds, setCmds] = useState({});
  const [busy, setBusy] = useState(false);
  const load = async () => {
    const { data } = await api.get("/optics/settings");
    setS(data); setCmds(Object.fromEntries(Object.entries(data.commands).map(([k, v]) => [k, v.join("\n")])));
  };
  useEffect(() => { load().catch(() => {}); }, []);
  if (!s) return null;
  const save = async () => {
    setBusy(true);
    try {
      await api.put("/optics/settings", { optics_enabled: s.optics_enabled, optics_interval_sec: Number(s.optics_interval_sec) || 300,
        optics_commands: Object.fromEntries(Object.entries(cmds).map(([k, v]) => [k, v.split("\n").map(x => x.trim()).filter(Boolean)])) });
      toast.success("Configurações da óptica salvas"); await load();
    } catch (e) { toast.error(formatApiError(e)); }
    finally { setBusy(false); }
  };
  const TYPES = [["huawei", "Huawei VRP"], ["juniper", "Juniper Junos"], ["cisco", "Cisco IOS/XE/XR"], ["datacom", "Datacom DmOS"], ["zte", "ZTE"], ["mikrotik", "Mikrotik"]];
  return (
    <div className="grid grid-cols-1 xl:grid-cols-3 gap-4" data-testid="optics-settings">
      <Card className="bg-[#111722] border-[#1E293B] p-5 space-y-3 h-fit">
        <div className="text-xs uppercase tracking-widest text-slate-400 font-mono">Leitura óptica</div>
        <div className="flex items-center justify-between"><Label>Ativa</Label><Switch checked={s.optics_enabled} onCheckedChange={v => setS({ ...s, optics_enabled: v })} disabled={!s.is_admin} /></div>
        <div><Label>Intervalo (segundos)</Label><Input type="number" min={60} value={s.optics_interval_sec} onChange={e => setS({ ...s, optics_interval_sec: e.target.value })} className={`${inputCls} font-mono`} disabled={!s.is_admin} /></div>
        <div className="text-xs font-mono text-slate-400">Última leitura: {s.last_tick ? new Date(s.last_tick).toLocaleString("pt-BR") : "ainda não"}{s.busy ? " · lendo…" : ""}{s.errors ? <span className="text-amber-300"> · {s.errors} interface(s) com erro</span> : ""}</div>
        <Button size="sm" variant="outline" className="border-[#1E293B] bg-[#0B111C] text-slate-200 hover:bg-slate-800"
                onClick={async () => { const { data } = await api.post("/optics/poll-now"); data.started ? toast.info("Leitura iniciada") : toast.warning(data.reason); setTimeout(load, 5000); }}>
          <RefreshCw className="w-3.5 h-3.5 mr-1.5" /> Ler agora
        </Button>
        <div className="text-xs text-slate-400 border-t border-[#1E293B] pt-3 space-y-1.5">
          <div>Lê pela CLI (SSH) as interfaces dos <b>links dos mapas</b> e dos <b>gráficos ópticos</b> dos dashboards.</div>
          <div>Os comandos são tentados em ordem até um deles trazer valores. Use <code>{"{ifname}"}</code> onde entra o nome da interface.</div>
          <div>Nos gráficos, o botão <b>Testar leitura</b> mostra a saída bruta — use para ajustar o comando ao seu firmware.</div>
        </div>
      </Card>
      <Card className="bg-[#111722] border-[#1E293B] p-5 xl:col-span-2 space-y-3">
        <div className="text-xs uppercase tracking-widest text-slate-400 font-mono">Comandos por fabricante (um por linha)</div>
        {TYPES.map(([t, l]) => (
          <div key={t}>
            <div className="flex items-center justify-between"><Label>{l}</Label>
              {s.is_admin && cmds[t] !== (s.defaults[t] || []).join("\n") && <button className="text-[11px] text-slate-400 underline" onClick={() => setCmds({ ...cmds, [t]: (s.defaults[t] || []).join("\n") })}>voltar ao padrão</button>}</div>
            <Textarea value={cmds[t] || ""} onChange={e => setCmds({ ...cmds, [t]: e.target.value })} rows={Math.max(1, (cmds[t] || "").split("\n").length)}
                      className={`${inputCls} font-mono text-xs mt-1`} disabled={!s.is_admin} data-testid={`optics-cmd-${t}`} />
          </div>
        ))}
        {s.is_admin && <div className="flex justify-end"><Button onClick={save} disabled={busy} className="bg-[#007AFF] hover:bg-[#0062CC]">{busy && <Loader2 className="w-4 h-4 mr-2 animate-spin" />} Salvar</Button></div>}
      </Card>
    </div>
  );
}

export default function Dashboards() {
  const [tab, setTab] = useState("dash");
  const [list, setList] = useState([]);
  const [devices, setDevices] = useState([]);
  const [current, setCurrent] = useState(null);
  const [draft, setDraft] = useState(null);
  const [editing, setEditing] = useState(false);
  const [minutes, setMinutes] = useState(1440);
  const [wdlg, setWdlg] = useState(null);
  const [newDash, setNewDash] = useState({ name: "", group: "" });
  const [creating, setCreating] = useState(false);
  const [saving, setSaving] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);

  const dash = editing ? draft : current;
  const dirty = editing && JSON.stringify(draft) !== JSON.stringify(current);
  const groups = useMemo(() => {
    const g = {};
    list.forEach(d => { (g[d.group] = g[d.group] || []).push(d); });
    return Object.entries(g);
  }, [list]);
  const devName = (id) => devices.find(d => d.id === id)?.name || "?";

  const loadList = async () => setList((await api.get("/dashboards")).data);
  useEffect(() => { loadList().catch(() => {}); api.get("/devices").then(r => setDevices(r.data)).catch(() => {}); }, []);
  useEffect(() => { if (!current && list.length) open(list[0].id); }, [list]); // eslint-disable-line react-hooks/exhaustive-deps

  const confirmDiscard = () => !dirty || window.confirm("Há alterações não salvas. Descartar?");
  const open = async (id) => {
    if (!confirmDiscard()) return;
    const { data } = await api.get(`/dashboards/${id}`);
    setCurrent(data); setDraft(null); setEditing(false);
  };
  const create = async () => {
    if (!newDash.name.trim()) return;
    try {
      const { data } = await api.post("/dashboards", { name: newDash.name.trim(), group: newDash.group.trim() || "Geral" });
      setNewDash({ name: "", group: "" }); setCreating(false); await loadList();
      setCurrent(data); setDraft(JSON.parse(JSON.stringify(data))); setEditing(true);
    } catch (e) { toast.error(formatApiError(e)); }
  };
  const save = async () => {
    setSaving(true);
    try {
      const { data } = await api.put(`/dashboards/${draft.id}`, { name: draft.name, group: draft.group, widgets: draft.widgets });
      setCurrent(data); setDraft(null); setEditing(false); toast.success("Dashboard salvo"); loadList();
    } catch (e) { toast.error(formatApiError(e)); }
    finally { setSaving(false); }
  };
  const remove = async () => {
    if (!window.confirm(`Excluir o dashboard "${current.name}"?`)) return;
    await api.delete(`/dashboards/${current.id}`); setCurrent(null); await loadList();
  };
  const duplicate = async () => { const { data } = await api.post(`/dashboards/${current.id}/duplicate`); await loadList(); open(data.id); };
  const move = (i, d) => setDraft(x => { const w = [...x.widgets]; const j = i + d; if (j < 0 || j >= w.length) return x; [w[i], w[j]] = [w[j], w[i]]; return { ...x, widgets: w }; });
  const saveWidget = (w) => {
    setDraft(x => ({ ...x, widgets: x.widgets.some(y => y.id === w.id) ? x.widgets.map(y => (y.id === w.id ? w : y)) : [...x.widgets, w] }));
    setWdlg(null);
  };
  const tb = "h-8 text-xs border-[#1E293B] bg-[#0B111C] text-slate-200 hover:bg-slate-800";

  return (
    <div className="flex-1 flex flex-col min-h-0" data-testid="dashboards-page">
      <div className="px-8 pt-6">
        <div className="text-xs uppercase tracking-widest text-slate-400 font-mono">Consumo & sinais</div>
        <h1 className="font-heading text-3xl font-bold text-slate-100 mt-1">Dashboards</h1>
        <div className="flex gap-1 mt-4 border-b border-[#1E293B]">
          <button className={tabBtn(tab === "dash")} onClick={() => setTab("dash")}><Gauge className="w-4 h-4" /> Dashboards</button>
          <button className={tabBtn(tab === "optics")} onClick={() => setTab("optics")} data-testid="tab-optics-settings"><Settings2 className="w-4 h-4" /> Configurações da óptica</button>
        </div>
      </div>

      {tab === "optics" && <div className="p-8 pt-5 overflow-y-auto flex-1"><OpticsSettingsTab /></div>}

      {tab === "dash" && (
        <div className="flex-1 min-h-0 flex gap-4 p-8 pt-5">
          <Card className="bg-[#111722] border-[#1E293B] w-60 shrink-0 flex flex-col overflow-hidden">
            <div className="p-3 border-b border-[#1E293B]">
              {!creating ? (
                <Button size="sm" onClick={() => setCreating(true)} className="w-full h-8 bg-[#007AFF] hover:bg-[#0062CC]" data-testid="new-dash-btn"><Plus className="w-4 h-4 mr-1" /> Novo dashboard</Button>
              ) : (
                <div className="space-y-1.5">
                  <Input autoFocus value={newDash.name} onChange={e => setNewDash({ ...newDash, name: e.target.value })} placeholder="Nome (ex.: Trânsitos)" className={`${inputCls} h-8 text-sm`} data-testid="new-dash-name" />
                  <Input value={newDash.group} onChange={e => setNewDash({ ...newDash, group: e.target.value })} onKeyDown={e => e.key === "Enter" && create()} placeholder="Grupo (ex.: Borda)" list="dash-groups" className={`${inputCls} h-8 text-sm`} data-testid="new-dash-group" />
                  <datalist id="dash-groups">{groups.map(([g]) => <option key={g} value={g} />)}</datalist>
                  <div className="flex gap-1.5"><Button size="sm" variant="ghost" onClick={() => setCreating(false)} className="flex-1 h-7 text-xs">Cancelar</Button>
                    <Button size="sm" onClick={create} disabled={!newDash.name.trim()} className="flex-1 h-7 text-xs bg-[#007AFF] hover:bg-[#0062CC]" data-testid="new-dash-create">Criar</Button></div>
                </div>
              )}
            </div>
            <div className="flex-1 overflow-y-auto">
              {list.length === 0 && <div className="p-4 text-xs text-slate-500 font-mono">Nenhum dashboard. Crie um (ex.: grupo "Borda" → "Trânsitos").</div>}
              {groups.map(([g, ds]) => (
                <div key={g}>
                  <div className="px-3 pt-3 pb-1 text-[10px] uppercase tracking-widest text-slate-500 font-mono flex items-center gap-1.5"><FolderOpen className="w-3 h-3" /> {g}</div>
                  {ds.map(d => (
                    <button key={d.id} onClick={() => open(d.id)} data-testid={`dash-item-${d.id}`}
                            className={`w-full text-left px-3 py-2 hover:bg-slate-800/50 ${current?.id === d.id ? "bg-[#0B111C] border-l-2 border-[#007AFF]" : "border-l-2 border-transparent"}`}>
                      <div className="text-sm text-slate-100 truncate">{d.name}</div>
                      <div className="text-[10px] font-mono text-slate-500">{d.widgets} gráfico(s)</div>
                    </button>
                  ))}
                </div>
              ))}
            </div>
          </Card>

          {!dash ? (
            <Card className="flex-1 bg-[#111722] border-[#1E293B] flex items-center justify-center text-slate-500 text-sm font-mono">Selecione ou crie um dashboard.</Card>
          ) : (
            <div className="flex-1 min-w-0 flex flex-col gap-3 overflow-y-auto pr-1">
              <div className="flex items-center gap-2 flex-wrap sticky top-0 z-10 bg-[#090D14] pb-2">
                {editing ? (
                  <>
                    <Input value={draft.name} onChange={e => setDraft({ ...draft, name: e.target.value })} className={`${inputCls} h-8 w-52 font-semibold`} />
                    <Input value={draft.group} onChange={e => setDraft({ ...draft, group: e.target.value })} list="dash-groups" className={`${inputCls} h-8 w-40 text-sm`} title="Grupo" />
                    <Button size="sm" variant="outline" onClick={() => setWdlg({})} className={tb} data-testid="add-widget"><Plus className="w-3.5 h-3.5 mr-1.5" /> Gráfico</Button>
                    <div className="ml-auto flex gap-2">
                      <Button size="sm" variant="ghost" onClick={() => { if (confirmDiscard()) { setEditing(false); setDraft(null); } }} className="h-8 text-xs text-slate-300"><X className="w-3.5 h-3.5 mr-1" /> Cancelar</Button>
                      <Button size="sm" onClick={save} disabled={saving} className="h-8 text-xs bg-[#007AFF] hover:bg-[#0062CC]" data-testid="dash-save">
                        {saving ? <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" /> : <Save className="w-3.5 h-3.5 mr-1" />} Salvar{dirty ? " *" : ""}</Button>
                    </div>
                  </>
                ) : (
                  <>
                    <div className="mr-2"><div className="text-[10px] uppercase tracking-widest text-slate-500 font-mono">{dash.group}</div><div className="text-lg font-semibold text-slate-100 leading-tight">{dash.name}</div></div>
                    <div className="flex border border-[#1E293B] rounded overflow-hidden" data-testid="range-picker">
                      {RANGES.map(([m, l]) => (
                        <button key={m} onClick={() => setMinutes(m)} className={`h-8 px-3 text-xs font-mono ${minutes === m ? "bg-[#007AFF]/25 text-slate-100" : "bg-[#0B111C] text-slate-400 hover:text-slate-200"}`}>{l}</button>
                      ))}
                    </div>
                    <Button size="sm" variant="ghost" onClick={() => setRefreshKey(k => k + 1)} className="h-8 text-slate-400 hover:bg-slate-800" title="Atualizar"><RefreshCw className="w-3.5 h-3.5" /></Button>
                    <div className="ml-auto flex gap-2">
                      <Button size="sm" variant="outline" onClick={() => { setDraft(JSON.parse(JSON.stringify(current))); setEditing(true); }} className={tb} data-testid="dash-edit"><Pencil className="w-3.5 h-3.5 mr-1.5" /> Editar</Button>
                      <Button size="sm" variant="outline" onClick={duplicate} className={tb}><Copy className="w-3.5 h-3.5 mr-1.5" /> Duplicar</Button>
                      <Button size="sm" variant="ghost" onClick={remove} className="h-8 text-xs text-red-400 hover:bg-red-950/40"><Trash2 className="w-3.5 h-3.5" /></Button>
                    </div>
                  </>
                )}
              </div>

              {dash.widgets.length === 0 && (
                <Card className="bg-[#111722] border-[#1E293B] p-10 text-center text-sm text-slate-500">
                  Nenhum gráfico ainda.
                  {!editing ? <div className="mt-3"><Button size="sm" onClick={() => { setDraft(JSON.parse(JSON.stringify(current))); setEditing(true); setWdlg({}); }} className="bg-[#007AFF] hover:bg-[#0062CC]"><Plus className="w-3.5 h-3.5 mr-1" /> Adicionar gráfico</Button></div> : null}
                </Card>
              )}
              <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
                {dash.widgets.map((w, i) => (
                  <Card key={w.id} className={`bg-[#111722] border-[#1E293B] p-4 ${w.size === "half" ? "" : "xl:col-span-2"}`} data-testid={`widget-${w.id}`}>
                    <div className="flex items-start gap-2 mb-3">
                      {w.type === "traffic" ? <Activity className="w-4 h-4 text-[#4DA3FF] mt-0.5" /> : <Radio className="w-4 h-4 text-[#4DA3FF] mt-0.5" />}
                      <div className="min-w-0">
                        <div className="text-sm font-semibold text-slate-100 truncate">{w.title}</div>
                        <div className="text-[11px] font-mono text-slate-500 truncate">{devName(w.device_id)} · {w.if_name}{w.type === "optics" ? " · sinal óptico" : ""}</div>
                      </div>
                      {editing && (
                        <div className="ml-auto flex gap-1 shrink-0">
                          <Button size="sm" variant="ghost" onClick={() => move(i, -1)} disabled={i === 0} className="h-7 px-1.5 text-slate-400"><ChevronUp className="w-4 h-4" /></Button>
                          <Button size="sm" variant="ghost" onClick={() => move(i, 1)} disabled={i === dash.widgets.length - 1} className="h-7 px-1.5 text-slate-400"><ChevronDown className="w-4 h-4" /></Button>
                          <Button size="sm" variant="ghost" onClick={() => setWdlg({ widget: w })} className="h-7 px-1.5 text-slate-300"><Pencil className="w-3.5 h-3.5" /></Button>
                          <Button size="sm" variant="ghost" onClick={() => setDraft(x => ({ ...x, widgets: x.widgets.filter(y => y.id !== w.id) }))} className="h-7 px-1.5 text-red-400"><Trash2 className="w-3.5 h-3.5" /></Button>
                        </div>
                      )}
                    </div>
                    {w.type === "traffic"
                      ? <TrafficWidget widget={w} minutes={minutes} refreshKey={refreshKey} height={w.size === "half" ? 200 : 280} />
                      : <OpticsPanel deviceId={w.device_id} ifIndex={w.if_index} ifName={w.if_name} minutes={Math.max(minutes, 360)}
                                     warn={w.rx_warn_dbm} crit={w.rx_crit_dbm} refreshKey={refreshKey} />}
                  </Card>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
      {wdlg && <WidgetDialog initial={wdlg.widget} devices={devices} onCancel={() => setWdlg(null)} onSave={saveWidget} />}
    </div>
  );
}
