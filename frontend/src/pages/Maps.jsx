import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, formatApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { toast } from "sonner";
import {
  Network, Plus, Pencil, Save, X, Trash2, Copy, Cable, Cloud, Type, Server, Loader2, MousePointer2,
  TerminalSquare, BellRing, Settings2, RefreshCw, Search, Maximize, RotateCcw,
} from "lucide-react";
import { MapCanvas } from "@/components/maps/MapCanvas";
import { LinkDialog } from "@/components/maps/LinkDialog";
import { MonitorTab } from "@/components/maps/MonitorTab";
import { TrafficChart } from "@/components/maps/TrafficChart";
import { OpticsPanel } from "@/components/dash/OpticsPanel";
import { fmtBps, fmtSpeed, UTIL_BANDS, STATUS, NO_DATA } from "@/lib/netfmt";

const uid = () => Math.random().toString(36).slice(2, 10);
const fmtTime = (iso) => iso ? new Date(iso).toLocaleTimeString("pt-BR") : "—";
const tabBtn = (on) => `flex items-center gap-2 px-4 py-2 text-sm -mb-px border-b-2 ${on ? "border-[#007AFF] text-slate-100" : "border-transparent text-slate-400 hover:text-slate-200"}`;

function Legend() {
  return (
    <div className="flex items-center gap-2 flex-wrap text-[10px] font-mono text-slate-400" data-testid="map-legend">
      <span className="text-slate-500">utilização:</span>
      {UTIL_BANDS.map(b => (
        <span key={b.label} className="flex items-center gap-1"><span className="w-4 h-1.5 rounded-sm" style={{ background: b.color }} />{b.label}{b.status ? ` ${b.status}` : ""}</span>
      ))}
      <span className="flex items-center gap-1"><span className="w-4 border-t-2 border-dashed" style={{ borderColor: STATUS.critical }} />✕ down</span>
      <span className="flex items-center gap-1"><span className="w-4 border-t-2 border-dashed" style={{ borderColor: NO_DATA }} />sem dados</span>
    </div>
  );
}

function LinkDetails({ link, live, nameOf, nodes, editing, onEdit, onDelete, onMonitor, siblings = [], liveLinks, onPick, onAddParallel, onResetCurve }) {
  const [range, setRange] = useState(60);
  const [points, setPoints] = useState([]);
  const a = nodes[link.from], b = nodes[link.to];
  const side = link.from_if && a?.device_id ? { dev: a.device_id, idx: link.from_if.index, flip: false }
             : link.to_if && b?.device_id ? { dev: b.device_id, idx: link.to_if.index, flip: true } : null;
  useEffect(() => {
    if (!side) return setPoints([]);
    let alive = true;
    const load = () => api.get("/monitor/series", { params: { device_id: side.dev, if_index: side.idx, minutes: range, points: 300 } })
      .then(r => { if (alive) setPoints(r.data.points.map(p => side.flip ? { t: p.t, ab: p.in, ba: p.out } : { t: p.t, ab: p.out, ba: p.in })); })
      .catch(() => {});
    load(); const t = setInterval(load, 30000);
    return () => { alive = false; clearInterval(t); };
  }, [link.id, side?.dev, side?.idx, range]); // eslint-disable-line react-hooks/exhaustive-deps
  const lv = live || {};
  return (
    <div className="space-y-3" data-testid="link-details">
      <div>
        <div className="text-sm font-semibold text-slate-100">{link.label || `${nameOf(a)} ↔ ${nameOf(b)}`}</div>
        <div className="text-[11px] font-mono text-slate-400 mt-1">A · {nameOf(a)} {link.from_if ? `· ${link.from_if.name}` : "· (sem interface)"}</div>
        <div className="text-[11px] font-mono text-slate-400">B · {nameOf(b)} {link.to_if ? `· ${link.to_if.name}` : "· (sem interface)"}</div>
      </div>
      {siblings.length > 1 && (
        <div data-testid="link-siblings">
          <div className="text-[10px] uppercase tracking-widest text-slate-500 font-mono mb-1">{siblings.length} enlaces entre estes equipamentos</div>
          <div className="space-y-0.5">
            {siblings.map((s, i) => {
              const sl = liveLinks?.[s.id] || {};
              const ifn = s.from === link.from ? (s.from_if || s.to_if) : (s.to_if || s.from_if);
              return (
                <button key={s.id} onClick={() => onPick(s.id)}
                        className={`w-full flex items-center gap-2 text-[11px] font-mono px-2 py-1 rounded border ${s.id === link.id ? "border-[#007AFF] bg-[#007AFF]/15 text-slate-100" : "border-[#1E293B] text-slate-300 hover:bg-slate-800/60"}`}>
                  <span className="text-slate-500">{i + 1}</span>
                  <span className="truncate">{s.label || ifn?.name || "sem interface"}</span>
                  <span className={`ml-auto whitespace-nowrap ${sl.down ? "text-red-400" : "text-slate-400"}`}>{sl.down ? "DOWN" : fmtBps(Math.max(sl.ab_bps || 0, sl.ba_bps || 0) || null)}</span>
                </button>
              );
            })}
          </div>
        </div>
      )}
      {lv.down && <div className="text-xs text-red-400 font-mono">✕ interface DOWN</div>}
      {lv.error && <div className="text-xs text-amber-300 font-mono break-words">⚠ {lv.error}</div>}
      <div className="grid grid-cols-2 gap-2 text-center">
        <div className="bg-[#0B111C] border border-[#1E293B] rounded p-2">
          <div className="text-[10px] text-slate-500 font-mono">A → B</div>
          <div className="text-slate-100 font-semibold">{fmtBps(lv.ab_bps)}</div>
          <div className="text-[11px] text-slate-400 font-mono">{lv.ab_pct != null ? `${lv.ab_pct}%` : "—"}</div>
        </div>
        <div className="bg-[#0B111C] border border-[#1E293B] rounded p-2">
          <div className="text-[10px] text-slate-500 font-mono">B → A</div>
          <div className="text-slate-100 font-semibold">{fmtBps(lv.ba_bps)}</div>
          <div className="text-[11px] text-slate-400 font-mono">{lv.ba_pct != null ? `${lv.ba_pct}%` : "—"}</div>
        </div>
      </div>
      <div className="text-[11px] font-mono text-slate-500">capacidade {fmtSpeed(lv.capacity_mbps)}{link.capacity_mbps ? " (manual)" : " (velocidade da porta)"}</div>
      <div>
        <div className="flex gap-1 mb-1">
          {[[60, "1h"], [360, "6h"], [1440, "24h"], [10080, "7d"]].map(([m, l]) => (
            <button key={m} onClick={() => setRange(m)} className={`text-[11px] font-mono px-2 py-0.5 rounded border ${range === m ? "border-[#007AFF] text-slate-100 bg-[#007AFF]/15" : "border-[#1E293B] text-slate-400"}`}>{l}</button>
          ))}
        </div>
        <TrafficChart points={points} labels={{ ab: `→ ${nameOf(b)}`, ba: `→ ${nameOf(a)}` }} />
      </div>
      {[["A", a, link.from_if], ["B", b, link.to_if]].filter(([, n, i]) => n?.device_id && i).map(([k, n, i]) => (
        <div key={k} className="border-t border-[#1E293B] pt-2">
          <div className="text-[11px] uppercase tracking-widest text-slate-400 font-mono mb-1">Sinal óptico · {k} · {nameOf(n)} · {i.name}</div>
          <OpticsPanel deviceId={n.device_id} ifIndex={i.index} ifName={i.name} minutes={1440} compact />
        </div>
      ))}
      <div className="flex flex-wrap gap-2 pt-1">
        {(link.from_if || link.to_if) && (
          <Button size="sm" variant="outline" onClick={onMonitor} className="h-7 text-xs border-[#1E293B] bg-[#0B111C] text-slate-200 hover:bg-slate-800" data-testid="link-monitor">
            <BellRing className="w-3.5 h-3.5 mr-1" /> Alarmar queda no Telegram
          </Button>
        )}
        {editing && <>
          <Button size="sm" variant="outline" onClick={onEdit} className="h-7 text-xs border-[#1E293B] bg-[#0B111C] text-slate-200 hover:bg-slate-800"><Pencil className="w-3.5 h-3.5 mr-1" /> Editar link</Button>
          <Button size="sm" variant="outline" onClick={onAddParallel} data-testid="link-add-parallel" className="h-7 text-xs border-[#1E293B] bg-[#0B111C] text-slate-200 hover:bg-slate-800"><Plus className="w-3.5 h-3.5 mr-1" /> Outro enlace entre estes</Button>
          {link.curve != null && <Button size="sm" variant="ghost" onClick={onResetCurve} className="h-7 text-xs text-slate-300 hover:bg-slate-800"><RotateCcw className="w-3.5 h-3.5 mr-1" /> Curva automática</Button>}
          <Button size="sm" variant="ghost" onClick={onDelete} className="h-7 text-xs text-red-400 hover:bg-red-950/40"><Trash2 className="w-3.5 h-3.5 mr-1" /> Excluir</Button>
        </>}
      {editing && <div className="text-[11px] text-slate-500 w-full">Arraste a bolinha no meio do enlace para curvar/afastar (duplo clique volta ao automático).</div>}
      </div>
    </div>
  );
}

function SettingsTab() {
  const [s, setS] = useState(null);
  const [busy, setBusy] = useState(false);
  const load = async () => setS((await api.get("/monitor/settings")).data);
  useEffect(() => { load().catch(() => {}); }, []);
  if (!s) return null;
  const save = async () => {
    setBusy(true);
    try {
      await api.put("/monitor/settings", { enabled: s.enabled, interval_sec: Number(s.interval_sec) || 30, default_community: s.default_community || "",
                                           confirm_polls: Number(s.confirm_polls) || 2, alert_up: s.alert_up });
      toast.success("Configurações salvas"); await load();
    } catch (e) { toast.error(formatApiError(e)); }
    finally { setBusy(false); }
  };
  const inputCls = "bg-[#05070A] border-[#1E293B] font-mono";
  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 max-w-5xl" data-testid="monitor-settings">
      <Card className="bg-[#111722] border-[#1E293B] p-5 space-y-3">
        <div className="text-xs uppercase tracking-widest text-slate-400 font-mono">Coleta SNMP</div>
        <div className="flex items-center justify-between"><Label>Coleta ativa</Label>
          <Switch checked={s.enabled} onCheckedChange={v => setS({ ...s, enabled: v })} disabled={!s.is_admin} /></div>
        <div><Label>Intervalo entre leituras (segundos)</Label>
          <Input type="number" min={10} value={s.interval_sec} onChange={e => setS({ ...s, interval_sec: e.target.value })} className={inputCls} disabled={!s.is_admin} /></div>
        {s.is_admin && <div><Label>Community SNMP padrão (v2c)</Label>
          <Input value={s.default_community || ""} onChange={e => setS({ ...s, default_community: e.target.value })} placeholder="usada quando o equipamento não tem community própria" className={inputCls} /></div>}
        <div><Label>Leituras seguidas para confirmar queda/volta</Label>
          <Input type="number" min={1} max={10} value={s.confirm_polls} onChange={e => setS({ ...s, confirm_polls: e.target.value })} className={inputCls} disabled={!s.is_admin} />
          <div className="text-[11px] text-slate-500 mt-1">2 leituras de {s.interval_sec}s = alarme em até ~{2 * s.interval_sec}s, sem alarmar por oscilação.</div></div>
        <div className="flex items-center justify-between"><Label>Avisar também quando a interface volta</Label>
          <Switch checked={s.alert_up} onCheckedChange={v => setS({ ...s, alert_up: v })} disabled={!s.is_admin} /></div>
        {s.is_admin && <div className="flex justify-end"><Button onClick={save} disabled={busy} className="bg-[#007AFF] hover:bg-[#0062CC]">{busy && <Loader2 className="w-4 h-4 mr-2 animate-spin" />} Salvar</Button></div>}
      </Card>
      <Card className="bg-[#111722] border-[#1E293B] p-5 space-y-2 text-sm text-slate-300">
        <div className="text-xs uppercase tracking-widest text-slate-400 font-mono">Situação</div>
        <div className="font-mono text-xs">Última coleta: {s.last_tick ? new Date(s.last_tick).toLocaleString("pt-BR") : "ainda não"} · levou {s.last_duration}s{s.busy ? " · coletando agora…" : ""}</div>
        <div className="font-mono text-xs">{s.errors ? <span className="text-amber-300">⚠ {s.errors} equipamento(s) sem resposta SNMP</span> : "Todos os equipamentos respondendo"}</div>
        <Button size="sm" variant="outline" className="border-[#1E293B] bg-[#0B111C] text-slate-200 hover:bg-slate-800"
                onClick={async () => { const { data } = await api.post("/monitor/poll-now"); data.started ? toast.info("Coleta iniciada") : toast.warning(data.reason); setTimeout(load, 3000); }}>
          <RefreshCw className="w-3.5 h-3.5 mr-1.5" /> Coletar agora
        </Button>
        <div className="border-t border-[#1E293B] pt-3 mt-2 text-xs text-slate-400 space-y-1.5">
          <div><b className="text-slate-200">Como funciona:</b> o Bastion lê por SNMP v2c só as interfaces usadas nos mapas e as marcadas em "Alarmes".</div>
          <div><b className="text-slate-200">Acesso direto:</b> libere SNMP (UDP 161) no equipamento para o IP do servidor Bastion.</div>
          <div><b className="text-slate-200">Atrás de agente:</b> SNMP (UDP) não passa pelo túnel SSH; o Bastion roda o <code>snmpget</code> no próprio agente — instale lá: <code>sudo apt install snmp</code> e libere o SNMP para o IP do agente.</div>
          <div><b className="text-slate-200">Alertas:</b> vão para o Telegram/webhook de <i>Automação → Notificações</i>.</div>
        </div>
      </Card>
    </div>
  );
}

export default function Maps() {
  const nav = useNavigate();
  const [tab, setTab] = useState("maps");
  const [maps, setMaps] = useState([]);
  const [devices, setDevices] = useState([]);
  const [current, setCurrent] = useState(null);   // mapa salvo
  const [draft, setDraft] = useState(null);       // cópia em edição
  const [editing, setEditing] = useState(false);
  const [tool, setTool] = useState("select");
  const [selected, setSelected] = useState(null);
  const [live, setLive] = useState(null);
  const [fitSignal, setFitSignal] = useState(0);
  const [linkDlg, setLinkDlg] = useState(null);
  const [addOpen, setAddOpen] = useState(false);
  const [addQ, setAddQ] = useState("");
  const [newName, setNewName] = useState("");
  const [saving, setSaving] = useState(false);

  const map = editing ? draft : current;
  const dirty = editing && JSON.stringify(draft) !== JSON.stringify(current);
  const nodesById = useMemo(() => Object.fromEntries((map?.nodes || []).map(n => [n.id, n])), [map]);
  const devById = useMemo(() => Object.fromEntries(devices.map(d => [d.id, d])), [devices]);
  const nameOf = useCallback((n) => !n ? "?" : n.device_id ? (devById[n.device_id]?.name || n.label || "?") : (n.label || (n.kind === "cloud" ? "Internet" : "texto")), [devById]);

  const loadMaps = async () => setMaps((await api.get("/maps")).data);
  useEffect(() => {
    loadMaps().catch(() => {});
    api.get("/devices").then(r => setDevices(r.data)).catch(() => {});
  }, []);
  useEffect(() => { if (!current && maps.length) openMap(maps[0].id); }, [maps]); // eslint-disable-line react-hooks/exhaustive-deps

  // tráfego ao vivo
  useEffect(() => {
    if (!current?.id || tab !== "maps") return;
    let alive = true;
    const load = () => api.get(`/maps/${current.id}/live`).then(r => alive && setLive(r.data)).catch(() => {});
    load(); const t = setInterval(load, 5000);
    return () => { alive = false; clearInterval(t); };
  }, [current?.id, tab, current?.updated_at]);

  const confirmDiscard = () => !dirty || window.confirm("Há alterações não salvas neste mapa. Descartar?");
  const openMap = async (id) => {
    if (!confirmDiscard()) return;
    try {
      const { data } = await api.get(`/maps/${id}`);
      setCurrent(data); setDraft(null); setEditing(false); setSelected(null); setLive(null); setTool("select");
      setFitSignal(s => s + 1);
    } catch (e) { toast.error(formatApiError(e)); }
  };
  const createMap = async () => {
    const name = newName.trim();
    if (!name) return;
    try {
      const { data } = await api.post("/maps", { name });
      setNewName(""); await loadMaps();
      setCurrent(data); setDraft(JSON.parse(JSON.stringify(data))); setEditing(true); setSelected(null); setLive(null);
    } catch (e) { toast.error(formatApiError(e)); }
  };
  const startEdit = () => { setDraft(JSON.parse(JSON.stringify(current))); setEditing(true); setTool("select"); };
  const cancelEdit = () => { if (!confirmDiscard()) return; setEditing(false); setDraft(null); setTool("select"); setSelected(null); };
  const save = async () => {
    setSaving(true);
    try {
      const { data } = await api.put(`/maps/${draft.id}`, { name: draft.name, description: draft.description || "", nodes: draft.nodes, links: draft.links });
      setCurrent(data); setDraft(null); setEditing(false); setTool("select");
      toast.success("Mapa salvo"); loadMaps();
    } catch (e) { toast.error(formatApiError(e)); }
    finally { setSaving(false); }
  };
  const removeMap = async () => {
    if (!window.confirm(`Excluir o mapa "${current.name}"?`)) return;
    await api.delete(`/maps/${current.id}`);
    setCurrent(null); setDraft(null); setEditing(false); await loadMaps();
  };
  const duplicate = async () => {
    const { data } = await api.post(`/maps/${current.id}/duplicate`);
    await loadMaps(); openMap(data.id); toast.success("Mapa duplicado");
  };

  // ---- edição ----
  const patch = (fn) => setDraft(d => fn(JSON.parse(JSON.stringify(d))));
  const centerPos = () => {
    const n = draft.nodes.length;
    return { x: 200 + (n % 5) * 200, y: 120 + Math.floor(n / 5) * 140 };
  };
  const addNode = (kind, extra = {}) => { const p = centerPos(); patch(d => { d.nodes.push({ id: uid(), kind, label: "", ...p, ...extra }); return d; }); };
  const addDevice = (dev) => {
    if (draft.nodes.some(n => n.device_id === dev.id)) return toast.info(`${dev.name} já está no mapa`);
    addNode("device", { device_id: dev.id }); setAddOpen(false); setAddQ("");
  };
  const bendLink = (id, curve) => setDraft(d => ({ ...d, links: d.links.map(l => l.id === id ? { ...l, curve } : l) }));
  const moveNode = (id, x, y) => setDraft(d => ({ ...d, nodes: d.nodes.map(n => n.id === id ? { ...n, x, y } : n) }));
  const deleteSelected = () => {
    if (!selected) return;
    if (selected.type === "node") patch(d => { d.nodes = d.nodes.filter(n => n.id !== selected.id); d.links = d.links.filter(l => l.from !== selected.id && l.to !== selected.id); return d; });
    else patch(d => { d.links = d.links.filter(l => l.id !== selected.id); return d; });
    setSelected(null);
  };
  const saveLink = (vals) => {
    if (!vals.from_if && !vals.to_if && !window.confirm("Nenhuma interface escolhida: o link vai aparecer sem tráfego. Continuar?")) return;
    patch(d => {
      if (linkDlg.link?.id) d.links = d.links.map(l => l.id === linkDlg.link.id ? { ...l, ...vals } : l);
      else d.links.push({ id: uid(), from: linkDlg.from, to: linkDlg.to, ...vals });
      return d;
    });
    setLinkDlg(null); setTool("select");
  };
  const monitorLinkIfaces = async (link) => {
    try {
      const cur = (await api.get("/monitor/interfaces")).data;
      const sides = [[nodesById[link.from], link.from_if], [nodesById[link.to], link.to_if]].filter(([n, i]) => n?.device_id && i);
      for (const [n, i] of sides) {
        const keep = cur.filter(r => r.device_id === n.device_id).map(r => ({ index: r.if_index, name: r.if_name, alias: r.alias || "" }));
        if (!keep.some(k => k.index === i.index)) keep.push({ index: i.index, name: i.name, alias: link.label || "" });
        await api.put(`/devices/${n.device_id}/monitor`, { interfaces: keep });
      }
      toast.success("Queda deste link vai alarmar no Telegram");
    } catch (e) { toast.error(formatApiError(e)); }
  };

  const selLink = selected?.type === "link" && map?.links.find(l => l.id === selected.id);
  const selNode = selected?.type === "node" && nodesById[selected.id];
  const pairOf = (l) => [l.from, l.to].sort().join("|");
  const selSiblings = selLink ? map.links.filter(l => pairOf(l) === pairOf(selLink)) : [];
  const selDev = selNode?.device_id && devById[selNode.device_id];
  const addList = devices.filter(d => !addQ || `${d.name} ${d.host} ${(d.tags || []).join(" ")}`.toLowerCase().includes(addQ.toLowerCase()));
  const tb = "h-8 text-xs border-[#1E293B] bg-[#0B111C] text-slate-200 hover:bg-slate-800";

  return (
    <div className="flex-1 flex flex-col min-h-0" data-testid="maps-page">
      <div className="px-8 pt-6 pb-0">
        <div className="text-xs uppercase tracking-widest text-slate-400 font-mono">Topologia & tráfego</div>
        <h1 className="font-heading text-3xl font-bold text-slate-100 mt-1">Mapas de rede</h1>
        <div className="flex gap-1 mt-4 border-b border-[#1E293B]">
          <button className={tabBtn(tab === "maps")} onClick={() => setTab("maps")} data-testid="tab-maps"><Network className="w-4 h-4" /> Mapas</button>
          <button className={tabBtn(tab === "alarms")} onClick={() => setTab("alarms")} data-testid="tab-alarms"><BellRing className="w-4 h-4" /> Alarmes de interface</button>
          <button className={tabBtn(tab === "settings")} onClick={() => setTab("settings")} data-testid="tab-settings"><Settings2 className="w-4 h-4" /> Configurações</button>
        </div>
      </div>

      {tab === "alarms" && <div className="p-8 pt-5 overflow-y-auto flex-1"><MonitorTab devices={devices} /></div>}
      {tab === "settings" && <div className="p-8 pt-5 overflow-y-auto flex-1"><SettingsTab /></div>}

      {tab === "maps" && (
        <div className="flex-1 min-h-0 flex gap-4 p-8 pt-5">
          {/* lista de mapas */}
          <Card className="bg-[#111722] border-[#1E293B] w-60 shrink-0 flex flex-col overflow-hidden">
            <div className="p-3 border-b border-[#1E293B] flex gap-1.5">
              <Input value={newName} onChange={e => setNewName(e.target.value)} onKeyDown={e => e.key === "Enter" && createMap()}
                     placeholder="Novo mapa…" className="h-8 bg-[#05070A] border-[#1E293B] text-sm" data-testid="new-map-name" />
              <Button size="sm" onClick={createMap} disabled={!newName.trim()} className="h-8 px-2 bg-[#007AFF] hover:bg-[#0062CC]" data-testid="new-map-btn"><Plus className="w-4 h-4" /></Button>
            </div>
            <div className="flex-1 overflow-y-auto divide-y divide-[#1E293B]">
              {maps.length === 0 && <div className="p-4 text-xs text-slate-500 font-mono">Nenhum mapa. Crie o primeiro acima (ex.: "Backbone", "POP Cachoeiro").</div>}
              {maps.map(m => (
                <button key={m.id} onClick={() => openMap(m.id)} data-testid={`map-item-${m.id}`}
                        className={`w-full text-left px-3 py-2.5 hover:bg-slate-800/50 ${current?.id === m.id ? "bg-[#0B111C] border-l-2 border-[#007AFF]" : "border-l-2 border-transparent"}`}>
                  <div className="text-sm text-slate-100 truncate">{m.name}</div>
                  <div className="text-[10px] font-mono text-slate-500">{m.nodes} equip. · {m.links} links</div>
                </button>
              ))}
            </div>
          </Card>

          {/* editor / visualização */}
          {!map ? (
            <Card className="flex-1 bg-[#111722] border-[#1E293B] flex items-center justify-center text-slate-500 text-sm font-mono">Selecione ou crie um mapa.</Card>
          ) : (
            <div className="flex-1 min-w-0 flex flex-col gap-2">
              <div className="flex items-center gap-2 flex-wrap">
                {editing ? (
                  <Input value={draft.name} onChange={e => setDraft({ ...draft, name: e.target.value })} className="h-8 w-56 bg-[#05070A] border-[#1E293B] font-semibold" data-testid="map-name" />
                ) : <div className="text-lg font-semibold text-slate-100 mr-2">{map.name}</div>}
                {!editing ? <>
                  <Button size="sm" variant="outline" onClick={startEdit} className={tb} data-testid="map-edit"><Pencil className="w-3.5 h-3.5 mr-1.5" /> Editar</Button>
                  <Button size="sm" variant="outline" onClick={duplicate} className={tb}><Copy className="w-3.5 h-3.5 mr-1.5" /> Duplicar</Button>
                  <Button size="sm" variant="ghost" onClick={removeMap} className="h-8 text-xs text-red-400 hover:bg-red-950/40"><Trash2 className="w-3.5 h-3.5 mr-1.5" /> Excluir</Button>
                </> : <>
                  <div className="relative">
                    <Button size="sm" variant="outline" onClick={() => setAddOpen(v => !v)} className={tb} data-testid="map-add-device"><Server className="w-3.5 h-3.5 mr-1.5" /> Equipamento</Button>
                    {addOpen && (
                      <div className="absolute top-full mt-1 left-0 z-30 w-80 bg-[#111722] border border-[#1E293B] rounded-md shadow-2xl">
                        <div className="p-2 relative"><Search className="w-3.5 h-3.5 absolute left-4 top-1/2 -translate-y-1/2 text-slate-500" />
                          <Input autoFocus value={addQ} onChange={e => setAddQ(e.target.value)} placeholder="Buscar…" className="pl-8 h-8 bg-[#05070A] border-[#1E293B] text-sm" /></div>
                        <div className="max-h-72 overflow-y-auto">
                          {addList.map(d => {
                            const inMap = draft.nodes.some(n => n.device_id === d.id);
                            return (
                              <button key={d.id} onClick={() => addDevice(d)} disabled={inMap} data-testid={`add-dev-${d.id}`}
                                      className="w-full text-left px-3 py-1.5 hover:bg-slate-800 disabled:opacity-40 flex gap-2 items-center">
                                <span className="text-sm text-slate-100 truncate">{d.name}</span>
                                <span className="text-[11px] font-mono text-slate-500 ml-auto">{inMap ? "no mapa" : d.host}</span>
                              </button>
                            );
                          })}
                        </div>
                      </div>
                    )}
                  </div>
                  <Button size="sm" variant="outline" onClick={() => addNode("cloud", { label: "Internet" })} className={tb} title="Nuvem (Internet, IX, operadora)"><Cloud className="w-3.5 h-3.5 mr-1.5" /> Nuvem</Button>
                  <Button size="sm" variant="outline" onClick={() => { const t = window.prompt("Texto:"); if (t) addNode("label", { label: t }); }} className={tb}><Type className="w-3.5 h-3.5 mr-1.5" /> Texto</Button>
                  <div className="flex border border-[#1E293B] rounded overflow-hidden">
                    <button onClick={() => setTool("select")} className={`h-8 px-2.5 text-xs flex items-center gap-1.5 ${tool === "select" ? "bg-[#007AFF]/25 text-slate-100" : "bg-[#0B111C] text-slate-400"}`} title="Mover e selecionar"><MousePointer2 className="w-3.5 h-3.5" /> Mover</button>
                    <button onClick={() => setTool("connect")} className={`h-8 px-2.5 text-xs flex items-center gap-1.5 ${tool === "connect" ? "bg-amber-500/25 text-amber-100" : "bg-[#0B111C] text-slate-400"}`} data-testid="map-tool-connect" title="Ligar dois equipamentos"><Cable className="w-3.5 h-3.5" /> Ligar</button>
                  </div>
                  {selected && <Button size="sm" variant="ghost" onClick={deleteSelected} className="h-8 text-xs text-red-400 hover:bg-red-950/40"><Trash2 className="w-3.5 h-3.5 mr-1" /> Remover selecionado</Button>}
                  <div className="ml-auto flex gap-2">
                    <Button size="sm" variant="ghost" onClick={cancelEdit} className="h-8 text-xs text-slate-300"><X className="w-3.5 h-3.5 mr-1" /> Cancelar</Button>
                    <Button size="sm" onClick={save} disabled={saving} className="h-8 text-xs bg-[#007AFF] hover:bg-[#0062CC]" data-testid="map-save">
                      {saving ? <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" /> : <Save className="w-3.5 h-3.5 mr-1" />} Salvar{dirty ? " *" : ""}
                    </Button>
                  </div>
                </>}
                {!editing && (
                  <div className="ml-auto text-[11px] font-mono text-slate-500 flex items-center gap-3">
                    <span>{live?.enabled === false ? "coleta desativada" : `atualiza a cada ${live?.interval_sec || 30}s · última ${fmtTime(live?.last_tick)}`}</span>
                    <button onClick={() => setFitSignal(s => s + 1)} className="text-slate-400 hover:text-slate-100 flex items-center gap-1"><Maximize className="w-3.5 h-3.5" /> ajustar</button>
                  </div>
                )}
              </div>
              <Legend />
              <div className="flex-1 min-h-0 flex gap-3">
                <div className="flex-1 min-w-0 rounded-lg border border-[#1E293B] overflow-hidden">
                  {map.nodes.length === 0 && !editing ? (
                    <div className="h-full flex flex-col items-center justify-center text-slate-500 text-sm gap-3">
                      Mapa vazio.
                      <Button size="sm" onClick={startEdit} className="bg-[#007AFF] hover:bg-[#0062CC]"><Pencil className="w-3.5 h-3.5 mr-1.5" /> Montar o mapa</Button>
                    </div>
                  ) : (
                    <MapCanvas map={map} live={live} devices={devices} editing={editing} tool={tool} selected={selected}
                               onSelect={setSelected} onMoveNode={moveNode} onBendLink={bendLink} fitSignal={fitSignal}
                               onConnect={(a, b) => setLinkDlg({ from: a, to: b })} />
                  )}
                </div>
                {(selLink || selNode) && (
                  <Card className="w-80 shrink-0 bg-[#111722] border-[#1E293B] p-4 overflow-y-auto relative">
                    <button className="absolute top-3 right-3 text-slate-500 hover:text-slate-200" onClick={() => setSelected(null)}><X className="w-4 h-4" /></button>
                    {selLink && (
                      <LinkDetails link={selLink} live={live?.links?.[selLink.id]} nameOf={nameOf} nodes={nodesById} editing={editing}
                                   onEdit={() => setLinkDlg({ from: selLink.from, to: selLink.to, link: selLink })}
                                   onDelete={deleteSelected} onMonitor={() => monitorLinkIfaces(selLink)}
                                   siblings={selSiblings} liveLinks={live?.links} onPick={(id) => setSelected({ type: "link", id })}
                                   onAddParallel={() => setLinkDlg({ from: selLink.from, to: selLink.to })}
                                   onResetCurve={() => bendLink(selLink.id, null)} />
                    )}
                    {selNode && (
                      <div className="space-y-3" data-testid="node-details">
                        <div className="text-sm font-semibold text-slate-100 pr-6">{nameOf(selNode)}</div>
                        {selDev ? <>
                          <div className="text-[11px] font-mono text-slate-400">{selDev.host}:{selDev.port} · {selDev.device_type}</div>
                          <div className="text-xs font-mono">status: <span className={selDev.status === "online" ? "text-emerald-400" : selDev.status === "offline" ? "text-red-400" : "text-slate-400"}>{live?.nodes?.[selNode.id]?.status || selDev.status || "?"}</span></div>
                          {live?.nodes?.[selNode.id]?.snmp_error && <div className="text-xs text-amber-300 font-mono break-words">⚠ SNMP: {live.nodes[selNode.id].snmp_error}</div>}
                          <div className="text-[11px] text-slate-400">{map.links.filter(l => l.from === selNode.id || l.to === selNode.id).length} link(s) neste mapa</div>
                          <Button size="sm" variant="outline" onClick={() => nav(`/terminal/${selDev.id}`)} className={tb}><TerminalSquare className="w-3.5 h-3.5 mr-1.5" /> Abrir terminal</Button>
                        </> : editing && (
                          <div><Label>Texto</Label>
                            <Input value={selNode.label || ""} onChange={e => patch(d => { d.nodes.find(n => n.id === selNode.id).label = e.target.value; return d; })}
                                   className="bg-[#05070A] border-[#1E293B]" /></div>
                        )}
                        {editing && <Button size="sm" variant="ghost" onClick={deleteSelected} className="h-7 text-xs text-red-400 hover:bg-red-950/40"><Trash2 className="w-3.5 h-3.5 mr-1" /> Remover do mapa</Button>}
                        {editing && <div className="text-[11px] text-slate-500">Para ligar: ferramenta <b>Ligar</b> → clique neste e no outro equipamento.</div>}
                      </div>
                    )}
                  </Card>
                )}
              </div>
            </div>
          )}
        </div>
      )}

      {linkDlg && (
        <LinkDialog key={linkDlg.link?.id || `${linkDlg.from}-${linkDlg.to}`} open link={linkDlg.link}
                    nodeA={nodesById[linkDlg.from]} nodeB={nodesById[linkDlg.to]} nameOf={nameOf}
                    existing={(map?.links || []).filter(l => l.id !== linkDlg.link?.id && pairOf(l) === [linkDlg.from, linkDlg.to].sort().join("|"))
                      .map(l => l.from === linkDlg.from ? l : { ...l, from_if: l.to_if, to_if: l.from_if })}
                    onCancel={() => setLinkDlg(null)} onSave={saveLink} />
      )}
    </div>
  );
}
