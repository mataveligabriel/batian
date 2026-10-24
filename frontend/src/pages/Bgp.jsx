import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, formatApiError } from "@/lib/api";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { toast } from "sonner";
import { Share2, RefreshCw, Loader2, Plus, X, Pencil, ShieldAlert, ShieldCheck, Globe2, AlertTriangle } from "lucide-react";
import { useAuth } from "@/context/AuthContext";
import { TagOptions } from "@/components/TagSelect";

const sel = "h-9 rounded-md bg-sunken border border-line px-2 text-sm text-slate-200";
const ago = (iso) => {
  if (!iso) return "—";
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  return d ? `${d}d ${h}h` : h ? `${h}h ${m}min` : `${m}min`;
};
const dur = (s) => { if (s == null) return null; const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60); return d ? `${d}d ${h}h` : h ? `${h}h ${m}min` : `${m}min`; };
const num = (n) => (n == null ? "—" : Number(n).toLocaleString("pt-BR"));
const STATE_CLS = { established: "text-emerald-300 border-emerald-500/40", idle: "text-red-300 border-red-500/40", active: "text-red-300 border-red-500/40",
  connect: "text-amber-300 border-amber-500/40", opensent: "text-amber-300 border-amber-500/40", openconfirm: "text-amber-300 border-amber-500/40" };
const STATE_LABEL = { established: "estabelecida", idle: "parada (idle)", active: "tentando (active)", connect: "conectando", opensent: "negociando", openconfirm: "negociando" };
const RPKI = { valid: ["válido", "text-emerald-300"], unknown: ["sem ROA", "text-slate-400"], invalid_asn: ["inválido (AS)", "text-red-300"], invalid_length: ["inválido (tamanho)", "text-red-300"] };
const LEVEL = { critical: "border-red-500/60 bg-red-500/10 text-red-200", high: "border-orange-500/50 bg-orange-500/10 text-orange-200", medium: "border-amber-500/40 bg-amber-500/10 text-amber-200" };

function Sessions({ data, isAdmin, reload }) {
  const [q, setQ] = useState("");
  const [onlyBad, setOnlyBad] = useState(false);
  const peers = (data.peers || []).filter(p => (!onlyBad || p.state !== "established") && (!q || `${p.peer} ${p.name} ${p.asn} ${p.device_name}`.toLowerCase().includes(q.toLowerCase())));
  const byDev = useMemo(() => { const m = new Map(); peers.forEach(p => { if (!m.has(p.device_name)) m.set(p.device_name, []); m.get(p.device_name).push(p); }); return [...m.entries()]; }, [peers]);
  const rename = async (p) => {
    const name = window.prompt(`Nome do vizinho ${p.peer} (ex.: operadora, cliente, PTT):`, p.name || "");
    if (name === null) return;
    try { await api.put("/bgpmon/peer-name", { key: p.key, name }); reload(); } catch (e) { toast.error(formatApiError(e)); }
  };
  const st = data.routers_status || {};
  const devIds = Object.fromEntries((data.peers || []).map(p => [p.device_name, p.device_id]));
  return (
    <>
      <div className="flex flex-wrap gap-2 items-center mb-3">
        <Input value={q} onChange={e => setQ(e.target.value)} placeholder="buscar vizinho, AS, nome…" className="bg-sunken border-line h-9 max-w-xs" data-testid="bgp-q" />
        <label className="flex items-center gap-1.5 text-sm text-slate-300"><input type="checkbox" className="accent-brand" checked={onlyBad} onChange={e => setOnlyBad(e.target.checked)} /> só as que não estão estabelecidas</label>
      </div>
      {byDev.length === 0 && <Card className="bg-surface border-line p-8 text-center text-sm text-slate-400">{(data.peers || []).length ? "Nada com esse filtro." : "Nenhuma sessão lida ainda. Escolha os roteadores em Configurar."}</Card>}
      {byDev.map(([dev, list]) => {
        const s = st[devIds[dev]] || {};
        const up = list.filter(p => p.state === "established").length;
        return (
          <Card key={dev} className="bg-surface border-line mb-3 overflow-x-auto" data-testid={`bgp-dev-${dev}`}>
            <div className="flex items-center gap-2 px-4 py-2 border-b border-line text-sm">
              <span className="text-slate-100 font-medium">{dev}</span>
              <span className="text-xs text-slate-400">{up}/{list.length} estabelecidas · lido há {ago(s.at)}</span>
              {s.error && <span className="text-xs text-amber-300 truncate">{s.error}</span>}
            </div>
            <table className="w-full text-sm">
              <thead className="text-xs text-slate-500"><tr><th className="text-left px-4 py-1.5">Vizinho</th><th className="text-left px-2">AS</th><th className="text-left px-2">Estado</th><th className="text-left px-2">Há</th><th className="text-right px-2">Prefixos</th><th className="text-right px-2">Quedas 24h</th>{isAdmin && <th />}</tr></thead>
              <tbody className="divide-y divide-line">
                {list.map(p => {
                  const low = p.prefixes != null && p.prefixes_base && p.prefixes < p.prefixes_base * 0.5;
                  return (
                    <tr key={p.key} className={p.missing > 2 ? "opacity-50" : ""} data-testid={`bgp-peer-${p.peer}`}>
                      <td className="px-4 py-1.5"><div className="font-mono text-slate-200">{p.peer}</div>{p.name && <div className="text-xs text-slate-400">{p.name}</div>}</td>
                      <td className="px-2 font-mono text-slate-300">{p.asn ? `AS${p.asn}` : "—"}</td>
                      <td className="px-2"><span className={`text-[11px] px-1.5 rounded border ${STATE_CLS[p.state] || "text-slate-400 border-line"}`}>{p.admin === "stop" ? "desligada (admin)" : STATE_LABEL[p.state] || p.state}</span>{p.missing > 2 && <span className="text-[10px] text-slate-500 ml-1">sumiu da tabela</span>}</td>
                      <td className="px-2 text-xs text-slate-400 whitespace-nowrap">{p.state === "established" && p.established_s != null ? dur(p.established_s) : ago(p.since)}</td>
                      <td className={`px-2 text-right font-mono ${low ? "text-red-300" : "text-slate-200"}`} title={p.prefixes_base ? `normal ~${num(Math.round(p.prefixes_base))}` : ""}>{num(p.prefixes)}</td>
                      <td className={`px-2 text-right ${p.flaps_24h > 2 ? "text-amber-300" : "text-slate-400"}`}>{p.flaps_24h || 0}</td>
                      {isAdmin && <td className="px-2 text-right"><Button size="sm" variant="ghost" onClick={() => rename(p)} className="h-7 text-slate-400" title="Dar nome"><Pencil className="w-3.5 h-3.5" /></Button></td>}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </Card>
        );
      })}
    </>
  );
}

function Prefixes({ data }) {
  const list = data.prefixes || [];
  if (!list.length) return <Card className="bg-surface border-line p-8 text-center text-sm text-slate-400">Nenhum prefixo cadastrado. Em Configurar, informe seus blocos e o AS que deve originar cada um.</Card>;
  return (
    <div className="grid lg:grid-cols-2 gap-3" data-testid="bgp-prefixes">
      {list.map(p => {
        const bad = (p.findings || []).length > 0;
        return (
          <Card key={p.prefix} className={`bg-surface border-line p-4 ${bad ? "border-red-500/50" : ""}`} data-testid={`bgp-pfx-${p.prefix}`}>
            <div className="flex items-center gap-2 mb-2">
              {p.error ? <AlertTriangle className="w-4 h-4 text-amber-300" /> : bad ? <ShieldAlert className="w-4 h-4 text-red-300" /> : <ShieldCheck className="w-4 h-4 text-emerald-300" />}
              <span className="font-mono text-slate-100">{p.prefix}</span>{p.note && <span className="text-xs text-slate-400">{p.note}</span>}
              <span className="ml-auto text-[11px] text-slate-500">verificado há {ago(p.checked_at)}</span>
            </div>
            {p.error ? <div className="text-xs text-amber-300">{p.error}</div> : <>
              <div className="grid grid-cols-3 gap-2 text-xs mb-2">
                <div><div className="text-slate-500">Origem vista</div><div className="font-mono text-slate-200">{(p.seen_origins || []).map(a => <span key={a} className={(p.expected || []).includes(a) ? "" : "text-red-300 font-semibold"}>AS{a} </span>)}{!(p.seen_origins || []).length && "nenhuma"}</div></div>
                <div><div className="text-slate-500">Visibilidade (RIS)</div>
                  <div className="flex items-center gap-1.5"><div className="flex-1 h-1.5 rounded bg-white/5 overflow-hidden"><div className={`h-full ${p.visibility_pct >= 80 ? "bg-emerald-500" : p.visibility_pct >= 50 ? "bg-amber-400" : "bg-red-500"}`} style={{ width: `${p.visibility_pct || 0}%` }} /></div><span className="text-slate-200">{p.visibility_pct ?? "—"}%</span></div></div>
                <div><div className="text-slate-500">RPKI</div>{Object.entries(p.rpki || {}).map(([a, s]) => { const [l, c] = RPKI[s] || [s || "—", "text-slate-400"]; return <div key={a} className={c}>AS{a}: {l}</div>; })}</div>
              </div>
              {(p.more_specifics || []).length > 0 && <div className="text-xs text-slate-400 mb-2">Mais específicos: {p.more_specifics.slice(0, 8).map(m => <span key={m.prefix} className={`font-mono mr-2 ${(p.expected || []).includes(m.origin) ? "" : "text-red-300"}`}>{m.prefix} (AS{m.origin})</span>)}</div>}
              {(p.findings || []).map(f => <div key={f.code} className={`text-xs rounded border px-2 py-1 mb-1 ${LEVEL[f.level] || LEVEL.medium}`}>{f.text}</div>)}
              {!bad && <div className="text-xs text-emerald-300">Origem certa, sem sub-prefixo estranho{(p.upstreams || []).length ? ", chegando pelos upstreams esperados" : ""}.</div>}
            </>}
          </Card>
        );
      })}
    </div>
  );
}

function Configure({ data, devices, reload }) {
  const [c, setC] = useState(() => JSON.parse(JSON.stringify(data.settings)));
  const [add, setAdd] = useState("");
  const [busy, setBusy] = useState(false);
  const devName = Object.fromEntries(devices.map(d => [d.id, d.name]));
  const set = (k, v) => setC(x => ({ ...x, [k]: v }));
  const save = async () => {
    setBusy(true);
    try {
      const body = { ...c, prefixes: c.prefixes.map(p => ({ ...p, origins: String(p.origins).split(/[\s,;]+/).filter(Boolean), upstreams: String(p.upstreams || "").split(/[\s,;]+/).filter(Boolean) })) };
      ["interval_min", "cli_interval_min", "prefix_drop_pct", "ripe_interval_min", "visibility_min_pct"].forEach(k => { body[k] = Number(body[k]); });
      await api.put("/bgpmon/settings", body); toast.success("Salvo — lendo agora"); reload();
    } catch (e) { toast.error(formatApiError(e)); } finally { setBusy(false); }
  };
  const lbl = "text-xs text-slate-400 mb-1 block";
  const pfxRow = (i, k, v) => set("prefixes", c.prefixes.map((p, j) => (j === i ? { ...p, [k]: v } : p)));
  return (
    <div className="grid gap-4" data-testid="bgp-config">
      <Card className="bg-surface border-line p-4">
        <div className="text-slate-100 font-medium mb-1">Roteadores com sessões BGP</div>
        <p className="text-xs text-slate-500 mb-3">SNMP (BGP4-MIB): estado, AS e tempo de cada sessão, sem login no equipamento — prefixos só em Cisco. CLI: lê o resumo do BGP (traz prefixos em Huawei, Cisco, Juniper, Datacom, ZTE) e faz login a cada leitura; dá para usar SNMP e buscar os prefixos pela CLI de tempo em tempo.</p>
        <div className="space-y-1.5 mb-3">
          {c.routers.map((r, i) => (
            <div key={r.device_id} className="flex flex-wrap items-center gap-2 rounded border border-line px-2 py-1.5 text-sm" data-testid={`bgp-router-${r.device_id}`}>
              <span className="flex-1 text-slate-200 min-w-[10rem]">{devName[r.device_id] || r.device_id}</span>
              <select value={r.mode} onChange={e => set("routers", c.routers.map((x, j) => (j === i ? { ...x, mode: e.target.value } : x)))} className={`${sel} h-8 text-xs`}>
                <option value="snmp">SNMP</option><option value="cli">CLI</option></select>
              {r.mode === "snmp" && <label className="flex items-center gap-1.5 text-xs text-slate-300"><input type="checkbox" className="accent-brand" checked={!!r.cli_prefixes} onChange={e => set("routers", c.routers.map((x, j) => (j === i ? { ...x, cli_prefixes: e.target.checked } : x)))} /> prefixos pela CLI</label>}
              <button onClick={() => set("routers", c.routers.filter((_, j) => j !== i))} className="text-slate-500 hover:text-red-300"><X className="w-4 h-4" /></button>
            </div>
          ))}
        </div>
        <div className="flex gap-2">
          <select value={add} onChange={e => setAdd(e.target.value)} className={`${sel} flex-1`} data-testid="bgp-add-router"><option value="">adicionar roteador…</option><TagOptions devices={devices.filter(d => !c.routers.some(r => r.device_id === d.id))} /></select>
          <Button size="sm" variant="ghost" disabled={!add} onClick={() => { set("routers", [...c.routers, { device_id: add, mode: "snmp", cli_prefixes: false }]); setAdd(""); }} className="h-9 text-slate-200"><Plus className="w-4 h-4 mr-1" /> Adicionar</Button>
        </div>
        <div className="grid sm:grid-cols-4 gap-3 mt-3">
          <div><label className={lbl}>Ler a cada (min)</label><Input type="number" value={c.interval_min} onChange={e => set("interval_min", e.target.value)} className="bg-sunken border-line" /></div>
          <div><label className={lbl}>Prefixos pela CLI a cada (min)</label><Input type="number" value={c.cli_interval_min} onChange={e => set("cli_interval_min", e.target.value)} className="bg-sunken border-line" /></div>
          <div><label className={lbl}>Alarmar queda de prefixos acima de (%)</label><Input type="number" value={c.prefix_drop_pct} onChange={e => set("prefix_drop_pct", e.target.value)} className="bg-sunken border-line" /></div>
          <label className="flex items-center gap-2 text-sm text-slate-300 mt-5"><Switch checked={c.alert_sessions} onCheckedChange={v => set("alert_sessions", v)} /> Abrir incidente</label>
        </div>
      </Card>
      <Card className="bg-surface border-line p-4">
        <div className="text-slate-100 font-medium mb-1">Seus prefixos na Internet</div>
        <p className="text-xs text-slate-500 mb-3">Verificados pelos coletores públicos do RIPE RIS (RIPEstat): quem origina, sub-prefixos de outra origem, visibilidade e RPKI. Informe o AS que deve originar cada bloco (no seu caso, o AS de quem anuncia o bloco) e, se quiser, os upstreams esperados para pegar vazamento.</p>
        <div className="space-y-2">
          {c.prefixes.map((p, i) => (
            <div key={i} className="grid sm:grid-cols-[1fr_1fr_1fr_1fr_auto] gap-2 items-center" data-testid={`bgp-pfx-row-${i}`}>
              <Input value={p.prefix} onChange={e => pfxRow(i, "prefix", e.target.value)} placeholder="203.0.113.0/24" className="bg-sunken border-line font-mono h-9" />
              <Input value={Array.isArray(p.origins) ? p.origins.join(", ") : p.origins} onChange={e => pfxRow(i, "origins", e.target.value)} placeholder="AS de origem (64500)" className="bg-sunken border-line font-mono h-9" />
              <Input value={Array.isArray(p.upstreams) ? p.upstreams.join(", ") : p.upstreams || ""} onChange={e => pfxRow(i, "upstreams", e.target.value)} placeholder="upstreams (opcional)" className="bg-sunken border-line font-mono h-9" />
              <Input value={p.note || ""} onChange={e => pfxRow(i, "note", e.target.value)} placeholder="observação" className="bg-sunken border-line h-9" />
              <button onClick={() => set("prefixes", c.prefixes.filter((_, j) => j !== i))} className="text-slate-500 hover:text-red-300"><X className="w-4 h-4" /></button>
            </div>
          ))}
          <Button size="sm" variant="ghost" onClick={() => set("prefixes", [...c.prefixes, { prefix: "", origins: "", upstreams: "", note: "" }])} className="text-slate-200" data-testid="bgp-add-pfx"><Plus className="w-4 h-4 mr-1" /> Prefixo</Button>
        </div>
        <div className="grid sm:grid-cols-3 gap-3 mt-3">
          <div><label className={lbl}>Verificar a cada (min)</label><Input type="number" value={c.ripe_interval_min} onChange={e => set("ripe_interval_min", e.target.value)} className="bg-sunken border-line" /></div>
          <div><label className={lbl}>Visibilidade mínima (%)</label><Input type="number" value={c.visibility_min_pct} onChange={e => set("visibility_min_pct", e.target.value)} className="bg-sunken border-line" /></div>
          <label className="flex items-center gap-2 text-sm text-slate-300 mt-5"><Switch checked={c.alert_prefixes} onCheckedChange={v => set("alert_prefixes", v)} /> Abrir incidente</label>
        </div>
      </Card>
      <div><Button onClick={save} disabled={busy} className="bg-brand hover:bg-brand-strong text-onbrand" data-testid="bgp-save">Salvar</Button></div>
    </div>
  );
}

export default function Bgp() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const [sp, setSp] = useSearchParams();
  const tab = sp.get("tab") || "";
  const [data, setData] = useState(null);
  const [devices, setDevices] = useState([]);
  const load = useCallback(() => api.get("/bgpmon").then(r => setData(r.data)).catch(e => toast.error(formatApiError(e))), []);
  useEffect(() => { load(); api.get("/devices").then(r => setDevices(r.data || [])).catch(() => {}); const t = setInterval(() => !document.hidden && load(), 30000); return () => clearInterval(t); }, [load]);
  const refresh = async () => { try { await api.post("/bgpmon/refresh"); toast.success("Lendo agora — atualiza em instantes"); setTimeout(load, 8000); } catch (e) { toast.error(formatApiError(e)); } };
  const down = (data?.peers || []).filter(p => p.state !== "established" && p.admin !== "stop" && !(p.missing > 2)).length;
  const alarms = (data?.prefixes || []).reduce((a, p) => a + (p.findings || []).length, 0);
  const tabs = [["", `Sessões${data ? ` (${(data.peers || []).length})` : ""}`], ["prefixos", `Prefixos${data ? ` (${(data.prefixes || []).length})` : ""}`], ...(isAdmin ? [["config", "Configurar"]] : [])];
  return (
    <div className="p-4 md:p-6 flex-1 overflow-y-auto" data-testid="bgp-page">
      <div className="flex items-start justify-between flex-wrap gap-3 mb-4">
        <div>
          <h1 className="font-heading text-2xl sm:text-[1.75rem] font-semibold tracking-tight text-slate-100 flex items-center gap-2"><Share2 className="w-6 h-6 text-brand-soft" /> Monitor BGP</h1>
          <p className="text-sm text-slate-400 mt-1">Sessões dos roteadores e seus prefixos vistos de fora (RIPE RIS + RPKI).
            {down > 0 && <span className="text-red-300"> {down} sessão(ões) fora.</span>}{alarms > 0 && <span className="text-red-300"> {alarms} alerta(s) nos prefixos.</span>}</p>
        </div>
        <div className="flex gap-2 items-center">
          <div className="flex gap-1 p-1 bg-sunken border border-line rounded-md text-sm">
            {tabs.map(([k, l]) => <button key={k} onClick={() => setSp(k ? { tab: k } : {})} className={`px-3 h-7 rounded whitespace-nowrap ${tab === k ? "bg-brand/20 text-brand-soft" : "text-slate-400 hover:text-slate-200"}`} data-testid={`bgp-tab-${k || "sess"}`}>{l}</button>)}
          </div>
          {isAdmin && <Button size="sm" variant="ghost" onClick={refresh} className="text-slate-300" title="Ler agora"><RefreshCw className="w-4 h-4" /></Button>}
        </div>
      </div>
      {!data ? <div className="py-10 text-center"><Loader2 className="w-5 h-5 animate-spin inline text-slate-500" /></div>
        : tab === "prefixos" ? <Prefixes data={data} /> : tab === "config" && isAdmin ? <Configure data={data} devices={devices} reload={load} /> : <Sessions data={data} isAdmin={isAdmin} reload={load} />}
      {tab === "prefixos" && <p className="text-[11px] text-slate-500 mt-3 flex items-center gap-1"><Globe2 className="w-3.5 h-3.5" /> Dados públicos do RIPE NCC (RIPEstat); podem ter alguns minutos de atraso.</p>}
    </div>
  );
}
