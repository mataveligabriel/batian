import React, { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, formatApiError } from "@/lib/api";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { toast } from "sonner";
import { Network, Gauge, LayoutGrid, Settings2, ExternalLink, Maximize, Minimize, Timer, Loader2 } from "lucide-react";
import { MapCanvas } from "@/components/maps/MapCanvas";
import { WidgetBody, widgetIcon } from "@/components/dash/WidgetBody";
import { fmtBps, UTIL_BANDS, STATUS, NO_DATA } from "@/lib/netfmt";

const RANGES = [[60, "1h"], [360, "6h"], [1440, "24h"], [10080, "7d"]];
const ROTATE = [[0, "sem rotação"], [30, "trocar a cada 30s"], [60, "a cada 1 min"], [120, "a cada 2 min"], [300, "a cada 5 min"]];

function MapPanel({ id, devices, height, onOpen }) {
  const [map, setMap] = useState(null);
  const [live, setLive] = useState(null);
  const [sel, setSel] = useState(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    let alive = true;
    api.get(`/maps/${id}`).then(r => alive && setMap(r.data)).catch(e => alive && setErr(formatApiError(e)));
    const load = () => api.get(`/maps/${id}/live`).then(r => alive && setLive(r.data)).catch(() => {});
    load(); const t = setInterval(load, 5000);
    return () => { alive = false; clearInterval(t); };
  }, [id]);
  if (err) return <div className="p-4 text-xs text-red-400 font-mono">{err}</div>;
  if (!map) return <div className="p-4 text-xs text-slate-500 font-mono">Carregando mapa…</div>;
  const link = sel?.type === "link" && map.links.find(l => l.id === sel.id);
  const lv = link && live?.links?.[link.id];
  const nm = (nid) => { const n = map.nodes.find(x => x.id === nid); const d = n?.device_id && devices.find(x => x.id === n.device_id); return d?.name || n?.label || "?"; };
  return (
    <div>
      <div className="rounded-md border border-line overflow-hidden" style={{ height }}>
        <MapCanvas map={map} live={live} devices={devices} editing={false} tool="select" selected={sel} onSelect={setSel}
                   onMoveNode={() => {}} onConnect={() => {}} fitSignal={height} />
      </div>
      <div className="flex items-center gap-3 flex-wrap mt-1.5 text-[10px] font-mono text-slate-500">
        {UTIL_BANDS.map(b => <span key={b.label} className="flex items-center gap-1"><span className="w-3 h-1.5 rounded-sm" style={{ background: b.color }} />{b.label}</span>)}
        <span className="flex items-center gap-1"><span className="w-3 border-t-2 border-dashed" style={{ borderColor: STATUS.critical }} />down</span>
        <span className="flex items-center gap-1"><span className="w-3 border-t-2 border-dashed" style={{ borderColor: NO_DATA }} />sem dados</span>
        {link ? (
          <span className="ml-auto text-slate-300">{link.label || `${nm(link.from)} ↔ ${nm(link.to)}`}: →{fmtBps(lv?.ab_bps)} · ←{fmtBps(lv?.ba_bps)}{lv?.down ? " · DOWN" : ""}</span>
        ) : <span className="ml-auto">toque num enlace para ver o tráfego</span>}
        <button onClick={onOpen} className="text-brand-soft hover:underline flex items-center gap-1">abrir no Mapas <ExternalLink className="w-3 h-3" /></button>
      </div>
    </div>
  );
}

function DashPanel({ id, devices, minutes, refreshKey, onOpen }) {
  const [dash, setDash] = useState(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    let alive = true;
    api.get(`/dashboards/${id}`).then(r => alive && setDash(r.data)).catch(e => alive && setErr(formatApiError(e)));
    return () => { alive = false; };
  }, [id, refreshKey]);
  if (err) return <div className="p-4 text-xs text-red-400 font-mono">{err}</div>;
  if (!dash) return <div className="p-4 text-xs text-slate-500 font-mono">Carregando dashboard…</div>;
  const devName = (did) => devices.find(d => d.id === did)?.name || "?";
  return (
    <div>
      {dash.widgets.length === 0 && <div className="text-xs text-slate-500 font-mono py-4">Dashboard sem gráficos.</div>}
      <div className="grid gap-3" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(min(100%, 26rem), 1fr))" }}>
        {dash.widgets.map(w => (
          <div key={w.id} className="bg-panel border border-line rounded-md p-3 min-w-0">
            <div className="flex items-start gap-2 mb-2">
              {React.createElement(widgetIcon(w.type), { className: "w-4 h-4 text-brand-soft mt-0.5 shrink-0" })}
              <div className="min-w-0">
                <div className="text-sm font-semibold text-slate-100 truncate">{w.title}</div>
                <div className="text-[11px] font-mono text-slate-500 truncate">
                  {w.type === "aggregate" ? `soma de ${(w.sources || []).length} interface(s)` : `${devName(w.device_id)} · ${w.if_name}`}
                </div>
              </div>
            </div>
            <WidgetBody w={w} minutes={minutes} refreshKey={refreshKey} height={170} readOnly />
          </div>
        ))}
      </div>
      <div className="flex justify-end mt-1.5">
        <button onClick={onOpen} className="text-[10px] font-mono text-brand-soft hover:underline flex items-center gap-1">abrir em Dashboards <ExternalLink className="w-3 h-3" /></button>
      </div>
    </div>
  );
}

function ChooseDialog({ open, onClose, maps, dashboards, layout, onSave }) {
  const [m, setM] = useState([]);
  const [d, setD] = useState([]);
  const [rot, setRot] = useState(0);
  useEffect(() => { if (open) { setM(layout.maps); setD(layout.dashboards); setRot(layout.rotate_sec || 0); } }, [open]); // eslint-disable-line react-hooks/exhaustive-deps
  const tg = (arr, set) => (id) => set(arr.includes(id) ? arr.filter(x => x !== id) : [...arr, id]);
  const Col = ({ title, icon: Icon, items, sel, onT, sub }) => (
    <div className="flex-1 min-w-0">
      <div className="flex items-center gap-1.5 text-xs text-slate-400 mb-1.5"><Icon className="w-3.5 h-3.5" /> {title}</div>
      <div className="border border-line rounded-md max-h-72 overflow-y-auto divide-y divide-line">
        {!items.length && <div className="px-3 py-6 text-center text-xs text-slate-500 font-mono">Nenhum disponível</div>}
        {items.map(i => (
          <label key={i.id} className={`flex items-center gap-2 px-3 py-2 cursor-pointer text-sm ${sel.includes(i.id) ? "bg-brand/10" : "hover:bg-slate-800/40"}`}>
            <input type="checkbox" className="accent-brand" checked={sel.includes(i.id)} onChange={() => onT(i.id)} />
            <span className="truncate">{sub ? <span className="text-slate-500">{sub(i)} · </span> : null}{i.name}</span>
            {sel.includes(i.id) && <span className="ml-auto text-[10px] font-mono text-slate-400">{sel.indexOf(i.id) + 1}º</span>}
          </label>
        ))}
      </div>
    </div>
  );
  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="bg-surface border-line text-slate-100 max-w-3xl" data-testid="noc-choose-dialog">
        <DialogHeader><DialogTitle>Painéis do NOC</DialogTitle></DialogHeader>
        <p className="text-xs text-slate-400">Marque o que quer acompanhar no Painel NOC. A ordem em que marcar é a ordem das abas.</p>
        <div className="flex gap-4 flex-col md:flex-row">
          <Col title="Mapas de rede" icon={Network} items={maps} sel={m} onT={tg(m, setM)} />
          <Col title="Dashboards" icon={Gauge} items={dashboards} sel={d} onT={tg(d, setD)} sub={i => i.group} />
        </div>
        <div className="flex items-center gap-2 text-sm">
          <Timer className="w-4 h-4 text-slate-400" /> Rotação automática (tela de parede):
          <select value={rot} onChange={e => setRot(Number(e.target.value))} className="h-8 bg-sunken border border-line rounded px-2 text-sm">
            {ROTATE.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>Cancelar</Button>
          <Button onClick={() => onSave({ maps: m, dashboards: d, rotate_sec: rot })} className="bg-brand hover:bg-brand-strong" data-testid="noc-save">Salvar</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** Mapas e dashboards escolhidos por cada usuário, no Painel NOC. */
export function NocPanels({ big = false }) {
  const nav = useNavigate();
  const [layout, setLayout] = useState(null);
  const [maps, setMaps] = useState([]);
  const [dashboards, setDashboards] = useState([]);
  const [devices, setDevices] = useState([]);
  const [active, setActive] = useState("all");
  const [minutes, setMinutes] = useState(360);
  const [refreshKey, setRefreshKey] = useState(0);
  const [choose, setChoose] = useState(false);
  const [fs, setFs] = useState(false);
  const boxRef = useRef(null);

  const load = useCallback(async () => {
    try {
      const [l, m, d, dv] = await Promise.all([api.get("/noc/layout"), api.get("/maps"), api.get("/dashboards"), api.get("/devices")]);
      setLayout(l.data); setMaps(m.data); setDashboards(d.data); setDevices(dv.data);
    } catch (e) { toast.error(formatApiError(e)); }
  }, []);
  useEffect(() => { load(); }, [load]);
  useEffect(() => { const t = setInterval(() => setRefreshKey(k => k + 1), 60000); return () => clearInterval(t); }, []);
  useEffect(() => { const on = () => setFs(!!document.fullscreenElement); document.addEventListener("fullscreenchange", on); return () => document.removeEventListener("fullscreenchange", on); }, []);

  const panels = layout ? [
    ...layout.maps.map(id => ({ key: `m:${id}`, kind: "map", id, name: maps.find(x => x.id === id)?.name })),
    ...layout.dashboards.map(id => ({ key: `d:${id}`, kind: "dash", id, name: dashboards.find(x => x.id === id)?.name })),
  ].filter(p => p.name) : [];

  // rotação automática entre as abas (tela de parede)
  useEffect(() => {
    const sec = layout?.rotate_sec;
    if (!sec || panels.length < 2) return;
    const t = setInterval(() => setActive(a => {
      const i = panels.findIndex(p => p.key === a);
      return panels[(i + 1) % panels.length].key;
    }), sec * 1000);
    return () => clearInterval(t);
  }, [layout?.rotate_sec, panels.map(p => p.key).join()]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (layout?.rotate_sec && panels.length > 1 && active === "all") setActive(panels[0].key); }, [layout?.rotate_sec, panels.length]); // eslint-disable-line react-hooks/exhaustive-deps

  const save = async (lay) => {
    try { const { data } = await api.put("/noc/layout", lay); setLayout(data); setChoose(false); setActive("all"); toast.success("Painel NOC atualizado"); }
    catch (e) { toast.error(formatApiError(e)); }
  };
  const toggleFs = async () => {
    try { if (document.fullscreenElement) await document.exitFullscreen(); else await boxRef.current?.requestFullscreen(); }
    catch { toast.error("O navegador não permitiu tela cheia"); }
  };

  if (!layout) return <Card className="bg-surface border-line p-5 text-xs text-slate-500 font-mono"><Loader2 className="w-4 h-4 animate-spin inline mr-2" />Carregando painéis…</Card>;
  const shown = active === "all" ? panels : panels.filter(p => p.key === active);
  const mapH = fs ? "calc(100vh - 140px)" : active === "all" ? (big ? 520 : 440) : (big ? "calc(100dvh - 260px)" : 560);

  return (
    <div ref={boxRef} className={`${fs ? "bg-canvas p-4 overflow-y-auto" : ""}`} data-testid="noc-panels">
      <div className="flex items-center gap-2 flex-wrap mb-3">
        <div className="mr-2">
          <div className="text-xs text-slate-400">Painéis do NOC</div>
          {!big && <div className="font-heading text-xl font-semibold text-slate-100">Mapas e dashboards</div>}
        </div>
        {panels.length > 0 && (
          <div className="flex gap-1 overflow-x-auto max-w-full" data-testid="noc-tabs">
            <button onClick={() => setActive("all")} className={`h-8 px-3 rounded border text-xs flex items-center gap-1.5 shrink-0 ${active === "all" ? "border-brand bg-brand/15 text-slate-100" : "border-line text-slate-400"}`}>
              <LayoutGrid className="w-3.5 h-3.5" /> Todos
            </button>
            {panels.map(p => (
              <button key={p.key} onClick={() => setActive(p.key)} className={`h-8 px-3 rounded border text-xs flex items-center gap-1.5 shrink-0 ${active === p.key ? "border-brand bg-brand/15 text-slate-100" : "border-line text-slate-400 hover:text-slate-200"}`}>
                {p.kind === "map" ? <Network className="w-3.5 h-3.5" /> : <Gauge className="w-3.5 h-3.5" />} {p.name}
              </button>
            ))}
          </div>
        )}
        <div className="ml-auto flex items-center gap-1.5">
          {layout.dashboards.length > 0 && (
            <div className="flex border border-line rounded overflow-hidden">
              {RANGES.map(([m, l]) => (
                <button key={m} onClick={() => setMinutes(m)} className={`h-8 px-2 text-xs font-mono ${minutes === m ? "bg-brand/25 text-slate-100" : "bg-panel text-slate-400"}`}>{l}</button>
              ))}
            </div>
          )}
          {layout.rotate_sec > 0 && <span className="text-[10px] font-mono text-slate-500 hidden md:inline"><Timer className="w-3 h-3 inline" /> {layout.rotate_sec}s</span>}
          <Button size="sm" variant="outline" onClick={toggleFs} className="h-8 border-line bg-panel text-slate-200 hover:bg-slate-800 hidden md:inline-flex" title="Tela cheia (TV do NOC)">
            {fs ? <Minimize className="w-3.5 h-3.5" /> : <Maximize className="w-3.5 h-3.5" />}
          </Button>
          <Button size="sm" variant="outline" onClick={() => setChoose(true)} className="h-8 border-line bg-panel text-slate-200 hover:bg-slate-800" data-testid="noc-choose">
            <Settings2 className="w-3.5 h-3.5 md:mr-1.5" /><span className="hidden md:inline">Escolher painéis</span>
          </Button>
        </div>
      </div>

      {panels.length === 0 ? (
        <Card className="bg-surface border-line border-dashed p-8 text-center">
          <div className="text-sm text-slate-300">Escolha os mapas de rede e dashboards para acompanhar aqui.</div>
          <div className="text-xs text-slate-500 mt-1">{maps.length + dashboards.length === 0 ? "Nenhum mapa ou dashboard disponível para você ainda." : `${maps.length} mapa(s) e ${dashboards.length} dashboard(s) disponíveis.`}</div>
          {maps.length + dashboards.length > 0 && <Button onClick={() => setChoose(true)} className="mt-4 bg-brand hover:bg-brand-strong"><Settings2 className="w-4 h-4 mr-2" /> Escolher painéis</Button>}
        </Card>
      ) : (
        <div className="space-y-4">
          {shown.map(p => (
            <Card key={p.key} className="bg-surface border-line p-3 md:p-4">
              <div className="flex items-center gap-2 mb-2">
                {p.kind === "map" ? <Network className="w-4 h-4 text-brand-soft" /> : <Gauge className="w-4 h-4 text-brand-soft" />}
                <div className="text-sm font-semibold text-slate-100">{p.name}</div>
              </div>
              {p.kind === "map"
                ? <MapPanel id={p.id} devices={devices} height={mapH} onOpen={() => nav(`/maps?id=${p.id}`)} />
                : <DashPanel id={p.id} devices={devices} minutes={minutes} refreshKey={refreshKey} onOpen={() => nav(`/dashboards?id=${p.id}`)} />}
            </Card>
          ))}
        </div>
      )}
      <ChooseDialog open={choose} onClose={() => setChoose(false)} maps={maps} dashboards={dashboards} layout={layout} onSave={save} />
    </div>
  );
}
