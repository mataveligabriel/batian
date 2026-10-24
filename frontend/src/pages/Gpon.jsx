import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, formatApiError } from "@/lib/api";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { toast } from "sonner";
import { Cable, Search, Loader2, RefreshCw, ArrowLeft, Radio, AlertTriangle, Settings2, X } from "lucide-react";
import { useAuth } from "@/context/AuthContext";
import { TagOptions } from "@/components/TagSelect";

const ago = (iso) => { if (!iso) return "—"; const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000); const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60); return d ? `${d}d ${h}h` : h ? `${h}h ${m}min` : `${m}min`; };
const rxCls = (v, bad) => (v == null ? "text-slate-500" : v < bad ? "text-red-300" : v < bad + 2 ? "text-amber-300" : "text-emerald-300");
const fmtRx = (v) => (v == null ? "—" : `${Number(v).toFixed(1)} dBm`);
const PHASE_CLS = (o) => (o.off ? ((o.phase || "").toLowerCase().includes("dying") ? "text-amber-300" : "text-red-300") : "text-emerald-300");
const sel = "h-9 rounded-md bg-sunken border border-line px-2 text-sm text-slate-200";

function Occ({ pct }) {
  return <div className="h-1.5 w-20 rounded bg-white/5 overflow-hidden" title={`${pct}% ocupada`}><div className={`h-full ${pct >= 90 ? "bg-red-500" : pct >= 75 ? "bg-amber-400" : "bg-brand"}`} style={{ width: `${Math.min(100, pct || 0)}%` }} /></div>;
}

function OnuCard({ o, bad, onClose }) {
  const [live, setLive] = useState(null);
  const [busy, setBusy] = useState(false);
  const run = async () => {
    setBusy(true);
    try { setLive((await api.post("/gpon/onu/live", { olt_id: o.olt_id, slot: o.slot, pon: o.pon, onu: o.onu })).data); } catch (e) { toast.error(formatApiError(e)); } finally { setBusy(false); }
  };
  return (
    <Card className="bg-surface border-line p-4 mb-3" data-testid="gpon-onu-card">
      <div className="flex items-start gap-2">
        <div className="flex-1">
          <div className="text-slate-100 font-medium">{o.name || o.description || o.sn || `ONU ${o.onu}`}</div>
          <div className="text-xs text-slate-400 font-mono">{o.olt_name} · gpon-onu_1/{o.slot}/{o.pon}:{o.onu}</div>
        </div>
        <button onClick={onClose} className="text-slate-500 hover:text-slate-200"><X className="w-4 h-4" /></button>
      </div>
      <div className="grid sm:grid-cols-4 gap-3 text-sm mt-3">
        <div><div className="text-xs text-slate-500">Estado</div><div className={PHASE_CLS(o)}>{o.phase}{o.off && o.down_since ? <span className="text-xs text-slate-400"> há {ago(o.down_since)}</span> : null}</div></div>
        <div><div className="text-xs text-slate-500">Sinal na ONU</div><div className={rxCls(o.rx, bad)}>{fmtRx(o.rx)}</div></div>
        <div><div className="text-xs text-slate-500">Serial / modelo</div><div className="font-mono text-slate-200 text-xs">{o.sn || "—"}<br />{o.type || ""}</div></div>
        <div><div className="text-xs text-slate-500">PPPoE</div><div className="font-mono text-slate-200 text-xs">{(o.pppoe || []).join(", ") || "—"}</div></div>
      </div>
      {o.description && <div className="text-xs text-slate-400 mt-2">{o.description}</div>}
      <div className="mt-3 flex items-center gap-2">
        <Button size="sm" variant="ghost" onClick={run} disabled={busy} className="text-brand-soft" data-testid="gpon-live">{busy ? <Loader2 className="w-4 h-4 animate-spin mr-1" /> : <Radio className="w-4 h-4 mr-1" />} Consultar agora na OLT</Button>
        <span className="text-[11px] text-slate-500">estado e atenuação nos dois sentidos</span>
      </div>
      {live && <div className="mt-2 grid lg:grid-cols-2 gap-2" data-testid="gpon-live-out">
        <div className="text-xs text-slate-300">Fase <b>{live.detail.phase || "—"}</b>{live.detail.last_down && <> · última queda {live.detail.last_down}{live.detail.cause ? ` (${live.detail.cause})` : ""}</>}</div>
        <pre className="text-[11px] font-mono bg-sunken border border-line rounded p-2 overflow-x-auto lg:col-span-2">{live.attenuation}</pre>
      </div>}
    </Card>
  );
}

function Spark({ hist }) {
  if (!hist?.length) return null;
  const w = 600, h = 60, max = Math.max(1, ...hist.map(x => x.online + x.offline));
  const t0 = new Date(hist[0].at).getTime(), t1 = Math.max(t0 + 1, new Date(hist[hist.length - 1].at).getTime());
  const X = (t) => ((new Date(t).getTime() - t0) / (t1 - t0)) * w, Y = (v) => h - (v / max) * (h - 4) - 2;
  const line = (k) => hist.map((p, i) => `${i ? "L" : "M"}${X(p.at).toFixed(1)},${Y(p[k]).toFixed(1)}`).join(" ");
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="w-full h-16" preserveAspectRatio="none">
      <path d={line("online")} fill="none" stroke="#34d399" strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
      <path d={line("offline")} fill="none" stroke="#f87171" strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

function PonView({ olt, p, bad, onBack, onPick }) {
  const [data, setData] = useState(null);
  const [filter, setFilter] = useState("");
  useEffect(() => { api.get("/gpon/pon", { params: { olt_id: olt.id, slot: p.slot, pon: p.pon } }).then(r => setData(r.data)).catch(e => toast.error(formatApiError(e))); }, [olt.id, p.slot, p.pon]);
  const list = (data?.onus || []).filter(o => filter === "off" ? o.off : filter === "bad" ? (o.rx != null && o.rx < bad) : true);
  return (
    <div data-testid="gpon-pon-view">
      <button onClick={onBack} className="text-sm text-slate-400 hover:text-slate-200 flex items-center gap-1 mb-3"><ArrowLeft className="w-4 h-4" /> {olt.name}</button>
      <Card className="bg-surface border-line p-4 mb-3">
        <div className="flex flex-wrap items-center gap-4">
          <div className="text-slate-100 font-medium font-mono">PON 1/{p.slot}/{p.pon}</div>
          <span className="text-sm text-slate-300">{p.total} ONUs · <span className="text-emerald-300">{p.online} online</span> · <span className="text-red-300">{p.offline} off</span>{p.los ? ` (${p.los} LOS)` : ""}</span>
          <span className={`text-sm ${rxCls(p.rx_median, bad)}`}>mediana {fmtRx(p.rx_median)}{p.rx_base != null && <span className="text-slate-500"> · normal {fmtRx(p.rx_base)}</span>}</span>
          {p.mass_open && <span className="text-xs px-2 rounded border border-red-500/50 text-red-300">queda em massa</span>}
          {p.degraded && <span className="text-xs px-2 rounded border border-orange-500/50 text-orange-300">sinal degradado</span>}
        </div>
        {data?.history?.length > 1 && <div className="mt-2"><div className="text-[11px] text-slate-500">online × off nos últimos 7 dias</div><Spark hist={data.history} /></div>}
      </Card>
      <div className="flex gap-1 p-1 bg-sunken border border-line rounded-md text-sm mb-2 w-fit">
        {[["", "Todas"], ["off", "Off"], ["bad", "Sinal ruim"]].map(([k, l]) => <button key={k} onClick={() => setFilter(k)} className={`px-3 h-7 rounded ${filter === k ? "bg-brand/20 text-brand-soft" : "text-slate-400"}`}>{l}</button>)}
      </div>
      <Card className="bg-surface border-line overflow-x-auto">
        {!data ? <div className="p-6 text-center"><Loader2 className="w-5 h-5 animate-spin inline text-slate-500" /></div> : (
          <table className="w-full text-sm">
            <thead className="text-xs text-slate-500 bg-panel"><tr><th className="text-left px-3 py-2">ONU</th><th className="text-left px-2">Cliente</th><th className="text-left px-2">Serial</th><th className="text-left px-2">PPPoE</th><th className="text-left px-2">Estado</th><th className="text-right px-3">Sinal</th></tr></thead>
            <tbody className="divide-y divide-line">
              {list.map(o => (
                <tr key={o.key} onClick={() => onPick(o)} className="hover:bg-white/[0.03] cursor-pointer" data-testid={`gpon-onu-${o.onu}`}>
                  <td className="px-3 py-1.5 font-mono text-slate-400">{o.onu}</td>
                  <td className="px-2 text-slate-200 truncate max-w-[16rem]">{o.name || o.description || "—"}</td>
                  <td className="px-2 font-mono text-xs text-slate-400">{o.sn || "—"}</td>
                  <td className="px-2 font-mono text-xs text-slate-400 truncate max-w-[12rem]">{(o.pppoe || []).join(", ")}</td>
                  <td className={`px-2 text-xs ${PHASE_CLS(o)}`}>{o.phase}{o.off && o.down_since ? <span className="text-slate-500"> · {ago(o.down_since)}</span> : null}</td>
                  <td className={`px-3 text-right font-mono text-xs ${rxCls(o.rx, bad)}`}>{fmtRx(o.rx)}</td>
                </tr>
              ))}
              {list.length === 0 && <tr><td colSpan={6} className="py-8 text-center text-slate-500">Nada aqui.</td></tr>}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}

function Configure({ settings, devices, onSaved }) {
  const [c, setC] = useState({ ...settings });
  const set = (k, v) => setC(x => ({ ...x, [k]: v }));
  const save = async () => {
    try {
      const body = { ...c }; ["state_interval_min", "power_interval_min", "mass_min", "mass_pct", "pon_max"].forEach(k => { body[k] = Number(body[k]); });
      body.rx_bad = Number(String(body.rx_bad).replace(",", ".")); body.rx_drop_db = Number(String(body.rx_drop_db).replace(",", "."));
      await api.put("/gpon/settings", body); toast.success("Salvo"); onSaved();
    } catch (e) { toast.error(formatApiError(e)); }
  };
  const lbl = "text-xs text-slate-400 mb-1 block";
  const zte = devices.filter(d => d.device_type === "zte");
  return (
    <Card className="bg-surface border-line p-4" data-testid="gpon-config">
      <label className={lbl}>OLTs acompanhadas (ZTE C300/C320/C600/C650 — Ctrl/⌘ para várias)</label>
      <select multiple value={c.olts} onChange={e => set("olts", [...e.target.selectedOptions].map(o => o.value))} className="w-full h-32 rounded-md bg-sunken border border-line p-1 text-sm text-slate-200" data-testid="gpon-olts">
        <TagOptions devices={zte.length ? zte : devices} />
      </select>
      <p className="text-[11px] text-slate-500 mt-1">Nome, serial, descrição e usuário PPPoE vêm do último backup de configuração da OLT (deixe o backup ligado). A senha do PPPoE não é guardada.</p>
      <div className="grid sm:grid-cols-4 gap-3 mt-3">
        <div><label className={lbl}>Estado das ONUs a cada (min)</label><Input type="number" value={c.state_interval_min} onChange={e => set("state_interval_min", e.target.value)} className="bg-sunken border-line" /></div>
        <div><label className={lbl}>Sinal a cada (min)</label><Input type="number" value={c.power_interval_min} onChange={e => set("power_interval_min", e.target.value)} className="bg-sunken border-line" /></div>
        <div><label className={lbl}>Queda em massa: ONUs de uma vez</label><Input type="number" value={c.mass_min} onChange={e => set("mass_min", e.target.value)} className="bg-sunken border-line" /></div>
        <div><label className={lbl}>…ou % da PON fora</label><Input type="number" value={c.mass_pct} onChange={e => set("mass_pct", e.target.value)} className="bg-sunken border-line" /></div>
        <div><label className={lbl}>Sinal ruim abaixo de (dBm)</label><Input value={c.rx_bad} onChange={e => set("rx_bad", e.target.value)} className="bg-sunken border-line" /></div>
        <div><label className={lbl}>Degradação da PON (dB abaixo do normal)</label><Input value={c.rx_drop_db} onChange={e => set("rx_drop_db", e.target.value)} className="bg-sunken border-line" /></div>
        <div><label className={lbl}>ONUs por PON (100% de ocupação)</label><Input type="number" value={c.pon_max} onChange={e => set("pon_max", e.target.value)} className="bg-sunken border-line" /></div>
        <label className="flex items-center gap-2 text-sm text-slate-300 mt-5"><Switch checked={c.alert} onCheckedChange={v => set("alert", v)} /> Abrir incidente</label>
      </div>
      <Button onClick={save} className="bg-brand hover:bg-brand-strong text-onbrand mt-3" data-testid="gpon-save">Salvar</Button>
    </Card>
  );
}

export default function Gpon() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const [sp, setSp] = useSearchParams();
  const [data, setData] = useState(null);
  const [devices, setDevices] = useState([]);
  const [q, setQ] = useState("");
  const [res, setRes] = useState(null);
  const [onu, setOnu] = useState(null);
  const [cfg, setCfg] = useState(false);
  const load = useCallback(() => api.get("/gpon/overview").then(r => setData(r.data)).catch(e => toast.error(formatApiError(e))), []);
  useEffect(() => { load(); if (isAdmin) api.get("/devices").then(r => setDevices(r.data || [])).catch(() => {}); const t = setInterval(() => !document.hidden && load(), 60000); return () => clearInterval(t); }, [load, isAdmin]);
  useEffect(() => {
    if (q.trim().length < 3) { setRes(null); return undefined; }
    const t = setTimeout(() => api.get("/gpon/search", { params: { q } }).then(r => setRes(r.data)).catch(e => toast.error(formatApiError(e))), 350);
    return () => clearTimeout(t);
  }, [q]);
  const bad = data?.rx_bad_limit ?? -27;
  const olt = data?.olts?.find(o => o.id === sp.get("olt"));
  const pon = olt?.pons?.find(p => `${p.slot}/${p.pon}` === sp.get("pon"));
  const tot = useMemo(() => (data?.olts || []).reduce((a, o) => ({ total: a.total + o.total, online: a.online + o.online, offline: a.offline + o.offline, bad: a.bad + o.rx_bad, alarms: a.alarms + o.alarms }), { total: 0, online: 0, offline: 0, bad: 0, alarms: 0 }), [data]);
  const refresh = async () => { try { await api.post("/gpon/refresh"); toast.success("Lendo as OLTs — leva alguns minutos"); setTimeout(load, 20000); } catch (e) { toast.error(formatApiError(e)); } };
  return (
    <div className="p-4 md:p-6 flex-1 overflow-y-auto" data-testid="gpon-page">
      <div className="flex items-start justify-between flex-wrap gap-3 mb-4">
        <div>
          <h1 className="font-heading text-2xl sm:text-[1.75rem] font-semibold tracking-tight text-slate-100 flex items-center gap-2"><Cable className="w-6 h-6 text-brand-soft" /> GPON</h1>
          <p className="text-sm text-slate-400 mt-1">{tot.total} ONUs · <span className="text-emerald-300">{tot.online} online</span> · <span className="text-red-300">{tot.offline} off</span> · {tot.bad} com sinal ruim{tot.alarms > 0 && <span className="text-red-300"> · {tot.alarms} PON(s) em alarme</span>}</p>
        </div>
        {isAdmin && <div className="flex gap-2">
          <Button size="sm" variant="ghost" onClick={refresh} className="text-slate-300" title="Ler agora"><RefreshCw className="w-4 h-4" /></Button>
          <Button size="sm" variant="ghost" onClick={() => setCfg(v => !v)} className="text-slate-300" data-testid="gpon-cfg-btn"><Settings2 className="w-4 h-4 mr-1" /> Configurar</Button>
        </div>}
      </div>
      {cfg && data?.settings && <div className="mb-4"><Configure settings={data.settings} devices={devices} onSaved={() => { setCfg(false); load(); }} /></div>}
      <div className="relative mb-3">
        <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
        <Input value={q} onChange={e => setQ(e.target.value)} placeholder="Buscar cliente: serial, nome, descrição, usuário PPPoE ou 2/1:15" className="pl-9 bg-sunken border-line h-10" data-testid="gpon-search" />
      </div>
      {res && <Card className="bg-surface border-line mb-4 divide-y divide-line" data-testid="gpon-results">
        {res.length === 0 && <div className="p-4 text-sm text-slate-500">Nenhuma ONU encontrada.</div>}
        {res.map(o => (
          <button key={o.key} onClick={() => { setOnu(o); setQ(""); }} className="w-full text-left px-4 py-2 hover:bg-white/[0.03] flex flex-wrap items-center gap-3 text-sm">
            <span className="text-slate-100 min-w-[10rem]">{o.name || o.description || o.sn}</span>
            <span className="font-mono text-xs text-slate-400">{o.olt_name} · 1/{o.slot}/{o.pon}:{o.onu}</span>
            <span className="font-mono text-xs text-slate-500">{o.sn}</span>
            {(o.pppoe || []).length > 0 && <span className="font-mono text-xs text-slate-500">{o.pppoe.join(", ")}</span>}
            <span className={`ml-auto text-xs ${PHASE_CLS(o)}`}>{o.phase}</span><span className={`text-xs font-mono ${rxCls(o.rx, bad)}`}>{fmtRx(o.rx)}</span>
          </button>
        ))}
      </Card>}
      {onu && <OnuCard o={onu} bad={bad} onClose={() => setOnu(null)} />}
      {!data ? <div className="py-10 text-center"><Loader2 className="w-5 h-5 animate-spin inline text-slate-500" /></div>
        : olt && pon ? <PonView olt={olt} p={pon} bad={bad} onBack={() => setSp({})} onPick={setOnu} />
          : data.olts.length === 0 ? <Card className="bg-surface border-line p-8 text-center text-sm text-slate-400">Nenhuma OLT acompanhada.{isAdmin && " Escolha em Configurar."}</Card>
            : data.olts.map(o => (
              <Card key={o.id} className="bg-surface border-line mb-3 overflow-x-auto" data-testid={`gpon-olt-${o.id}`}>
                <div className="flex flex-wrap items-center gap-3 px-4 py-2.5 border-b border-line">
                  <span className="text-slate-100 font-medium">{o.name}</span>
                  <span className="text-xs text-slate-400">{o.total} ONUs · {o.online} online · {o.offline} off · lido há {ago(o.status.at)}{o.status.inventory === 0 && " · sem backup (nomes/serial vazios)"}</span>
                  {o.status.error && <span className="text-xs text-amber-300 flex items-center gap-1"><AlertTriangle className="w-3.5 h-3.5" /> {o.status.error}</span>}
                </div>
                <table className="w-full text-sm">
                  <thead className="text-xs text-slate-500"><tr><th className="text-left px-4 py-1.5">PON</th><th className="text-left px-2">Ocupação</th><th className="text-right px-2">Online</th><th className="text-right px-2">Off</th><th className="text-right px-2">Sinal (mediana)</th><th className="text-right px-2">Sinal ruim</th><th className="text-left px-3">Situação</th></tr></thead>
                  <tbody className="divide-y divide-line">
                    {o.pons.map(p => (
                      <tr key={p.key} onClick={() => setSp({ olt: o.id, pon: `${p.slot}/${p.pon}` })} className={`hover:bg-white/[0.03] cursor-pointer ${p.mass_open || p.degraded ? "bg-red-500/5" : ""}`} data-testid={`gpon-pon-${p.slot}-${p.pon}`}>
                        <td className="px-4 py-1.5 font-mono text-slate-200">1/{p.slot}/{p.pon}</td>
                        <td className="px-2"><div className="flex items-center gap-2"><Occ pct={p.occupancy_pct} /><span className="text-xs text-slate-400">{p.total}/{data.pon_max}</span></div></td>
                        <td className="px-2 text-right text-emerald-300">{p.online}</td>
                        <td className={`px-2 text-right ${p.offline ? "text-red-300" : "text-slate-500"}`}>{p.offline}{p.los ? <span className="text-[10px] text-slate-500"> ({p.los} LOS)</span> : null}</td>
                        <td className={`px-2 text-right font-mono text-xs ${rxCls(p.rx_median, bad)}`}>{fmtRx(p.rx_median)}</td>
                        <td className={`px-2 text-right ${p.rx_bad ? "text-amber-300" : "text-slate-500"}`}>{p.rx_bad || 0}</td>
                        <td className="px-3 text-xs">{p.mass_open ? <span className="text-red-300">queda em massa</span> : p.degraded ? <span className="text-orange-300">sinal degradado</span> : <span className="text-slate-500">normal</span>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </Card>
            ))}
    </div>
  );
}
