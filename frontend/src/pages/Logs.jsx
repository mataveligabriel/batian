import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams, useNavigate } from "react-router-dom";
import { api, formatApiError } from "@/lib/api";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { toast } from "sonner";
import {
  ScrollText, Search, Pause, Play, Plus, Pencil, Trash2, FlaskConical, RotateCcw, Copy, Loader2, ChevronDown, ChevronRight,
  Siren, Link2, Radio,
} from "lucide-react";
import { TagOptions } from "@/components/TagSelect";
import { useAuth } from "@/context/AuthContext";
import { SEV as INC_SEV } from "@/pages/Incidents";

// severidade do syslog (0 = emerg … 7 = debug)
const SYS_SEV = [
  ["emerg", "text-red-300 bg-red-500/15 border-red-500/40"], ["alert", "text-red-300 bg-red-500/15 border-red-500/40"],
  ["crit", "text-red-300 bg-red-500/15 border-red-500/40"], ["err", "text-orange-300 bg-orange-500/10 border-orange-500/40"],
  ["warning", "text-amber-300 bg-amber-500/10 border-amber-500/40"], ["notice", "text-sky-300 bg-sky-500/10 border-sky-500/40"],
  ["info", "text-slate-300 bg-white/5 border-line"], ["debug", "text-slate-500 bg-white/5 border-line"],
];
const TABS = [["", "Mensagens"], ["regras", "Regras"], ["fontes", "Fontes"], ["configurar", "Configurar"]];
const hhmmss = (iso) => (iso ? new Date(iso).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "—");
const sel = "h-9 rounded-md bg-sunken border border-line px-2 text-sm text-slate-200";

function SevChip({ l }) {
  const [name, cls] = l.kind === "trap" ? ["trap", "text-fuchsia-300 bg-fuchsia-500/10 border-fuchsia-500/40"] : (SYS_SEV[l.severity] || SYS_SEV[6]);
  return <span className={`inline-block w-[3.6rem] text-center text-[10px] uppercase tracking-wide rounded border px-1 py-px ${cls}`}>{name}</span>;
}

// ------------------------------------------------------------------ mensagens
function Messages({ devices, rules, isAdmin }) {
  const [sp] = useSearchParams();
  const [rows, setRows] = useState([]);
  const [q, setQ] = useState("");
  const [dev, setDev] = useState(sp.get("device") || "");
  const [kind, setKind] = useState("");
  const [maxSev, setMaxSev] = useState(7);
  const [onlyRules, setOnlyRules] = useState(false);
  const [live, setLive] = useState(true);
  const [open, setOpen] = useState({});
  const [sum, setSum] = useState(null);
  const [busy, setBusy] = useState(false);
  const newest = useRef(null);
  const ruleName = useMemo(() => Object.fromEntries((rules || []).map(r => [r.id, r.name])), [rules]);
  const params = useMemo(() => ({ q, device_id: dev, kind, max_sev: maxSev, rule: onlyRules ? "any" : "" }), [q, dev, kind, maxSev, onlyRules]);

  const load = useCallback(async () => {
    setBusy(true);
    try {
      const r = await api.get("/logs", { params: { ...params, limit: 300 } });
      setRows(r.data || []);
      newest.current = r.data?.[0]?.at || new Date(Date.now() - 5000).toISOString();
    } catch (e) { toast.error(formatApiError(e)); } finally { setBusy(false); }
  }, [params]);
  useEffect(() => { const t = setTimeout(load, q ? 350 : 0); return () => clearTimeout(t); }, [load, q]);

  useEffect(() => {
    if (!live) return undefined;
    const t = setInterval(async () => {
      if (document.hidden || !newest.current) return;
      try {
        const r = await api.get("/logs", { params: { ...params, after: newest.current, limit: 500 } });
        if (r.data?.length) {
          newest.current = r.data[0].at;
          setRows(prev => [...r.data, ...prev].slice(0, 1500));
        }
      } catch { /* tenta no próximo */ }
    }, 3000);
    return () => clearInterval(t);
  }, [live, params]);

  useEffect(() => {
    const get = () => api.get("/logs/summary").then(r => setSum(r.data)).catch(() => {});
    get();
    const t = setInterval(get, 30000);
    return () => clearInterval(t);
  }, []);

  const older = async () => {
    const last = rows[rows.length - 1];
    if (!last) return;
    try {
      const r = await api.get("/logs", { params: { ...params, until: last.at, limit: 300 } });
      setRows(prev => [...prev, ...(r.data || []).filter(x => x.id !== last.id)]);
    } catch (e) { toast.error(formatApiError(e)); }
  };

  const lh = sum?.last_hour || {};
  const errs = Object.entries(sum?.listen || {}).filter(([, v]) => v && !v.ok && v.error !== "desligado");
  return (
    <>
      <div className="flex flex-wrap gap-2 items-center mb-3 text-xs" data-testid="logs-status">
        <span className={`inline-flex items-center gap-1.5 px-2 h-7 rounded border ${sum?.online ? "border-emerald-500/40 text-emerald-300" : "border-red-500/40 text-red-300"}`}>
          <span className={`w-2 h-2 rounded-full ${sum?.online ? "bg-emerald-400" : "bg-red-400"}`} />
          {sum?.online ? "Coletor ativo" : "Coletor parado (serviço \"log\")"}
        </span>
        {isAdmin && Object.entries(sum?.listen || {}).map(([k, v]) => v?.ok && <span key={k} className="px-2 h-7 inline-flex items-center rounded border border-line text-slate-400">{k === "trap" ? "traps" : "syslog"} UDP {v.port}</span>)}
        <span className="px-2 h-7 inline-flex items-center rounded border border-line text-slate-400">última hora: {Object.values(lh).reduce((a, b) => a + b, 0)} mensagens
          {(lh[0] || 0) + (lh[1] || 0) + (lh[2] || 0) + (lh[3] || 0) > 0 && <span className="text-orange-300 ml-1">· {(lh[0] || 0) + (lh[1] || 0) + (lh[2] || 0) + (lh[3] || 0)} erro(s)</span>}
          {lh.trap > 0 && <span className="text-fuchsia-300 ml-1">· {lh.trap} trap(s)</span>}</span>
        {isAdmin && sum?.stats?.dropped > 0 && <span className="px-2 h-7 inline-flex items-center rounded border border-amber-500/40 text-amber-300" title="Origem mandando mais que o limite por segundo (Configurar)">{sum.stats.dropped} descartada(s) por excesso</span>}
        {isAdmin && sum?.stats?.v3 > 0 && <span className="px-2 h-7 inline-flex items-center rounded border border-amber-500/40 text-amber-300">{sum.stats.v3} trap(s) SNMPv3 ignorado(s) — use v2c</span>}
        {errs.map(([k, v]) => <span key={k} className="px-2 h-7 inline-flex items-center rounded border border-red-500/40 text-red-300">UDP {v.port} não abriu: {v.error}</span>)}
      </div>
      <div className="flex flex-wrap gap-2 items-center mb-3">
        <div className="relative flex-1 min-w-[12rem]">
          <Search className="w-3.5 h-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-500" />
          <Input value={q} onChange={e => setQ(e.target.value)} placeholder="buscar no texto (ex.: BGP, ge-0/0/1, LOS)…" className="pl-8 bg-sunken border-line h-9" data-testid="logs-q" />
        </div>
        <select value={dev} onChange={e => setDev(e.target.value)} className={`${sel} max-w-[14rem]`} data-testid="logs-dev">
          <option value="">todos os equipamentos</option>
          <option value="none">— origem sem equipamento —</option>
          <TagOptions devices={devices} />
        </select>
        <select value={kind} onChange={e => setKind(e.target.value)} className={sel} data-testid="logs-kind">
          <option value="">syslog e traps</option><option value="syslog">só syslog</option><option value="trap">só traps</option>
        </select>
        <select value={maxSev} onChange={e => setMaxSev(Number(e.target.value))} className={sel} data-testid="logs-sev">
          <option value={7}>todas as severidades</option><option value={6}>info ou pior</option><option value={5}>notice ou pior</option>
          <option value={4}>warning ou pior</option><option value={3}>erro ou pior</option><option value={2}>crítico ou pior</option>
        </select>
        <label className="flex items-center gap-1.5 text-sm text-slate-300"><input type="checkbox" className="accent-brand" checked={onlyRules} onChange={e => setOnlyRules(e.target.checked)} data-testid="logs-onlyrules" /> só as que casaram regra</label>
        <Button size="sm" variant="ghost" onClick={() => setLive(v => !v)} className={live ? "text-emerald-300" : "text-slate-300"} data-testid="logs-live">
          {live ? <><Pause className="w-4 h-4 mr-1" /> Ao vivo</> : <><Play className="w-4 h-4 mr-1" /> Pausado</>}
        </Button>
      </div>
      <Card className="bg-surface border-line overflow-hidden">
        <div className="divide-y divide-line font-mono text-xs" data-testid="logs-list">
          {rows.length === 0 && <div className="py-12 text-center text-slate-500 font-sans text-sm">
            {busy ? <Loader2 className="w-4 h-4 animate-spin inline" /> : <>Nenhuma mensagem ainda. Aponte o syslog e os traps dos equipamentos para o BastiON — veja a aba <b>Configurar</b>.</>}
          </div>}
          {rows.map(l => {
            const ex = open[l.id];
            return (
              <div key={l.id} className={`px-3 py-1.5 hover:bg-white/[0.03] cursor-pointer ${l.rules?.length ? "bg-brand/[0.06]" : ""}`} onClick={() => setOpen(o => ({ ...o, [l.id]: !o[l.id] }))} data-testid={`log-row-${l.id}`}>
                <div className="flex gap-x-2 gap-y-0.5 items-start flex-wrap sm:flex-nowrap">
                  {ex ? <ChevronDown className="w-3 h-3 mt-0.5 text-slate-500 shrink-0" /> : <ChevronRight className="w-3 h-3 mt-0.5 text-slate-600 shrink-0" />}
                  <span className="text-slate-500 whitespace-nowrap shrink-0">{hhmmss(l.at)}</span>
                  <SevChip l={l} />
                  <span className="text-slate-200 whitespace-nowrap shrink-0 max-w-[11rem] truncate" title={l.host}>{l.device_name || l.host_name || l.host}</span>
                  <span className={`text-slate-300 min-w-0 order-last sm:order-none basis-full sm:basis-auto pl-5 sm:pl-0 ${ex ? "whitespace-pre-wrap break-all" : "truncate"}`}>{l.msg}</span>
                  {l.rules?.map(r => <span key={r} className="shrink-0 font-sans text-[10px] px-1.5 rounded border border-brand/50 text-brand-soft whitespace-nowrap"><Siren className="w-3 h-3 inline -mt-0.5" /> {ruleName[r] || "regra"}</span>)}
                </div>
                {ex && (
                  <div className="pl-5 mt-1.5 text-[11px] text-slate-400 space-y-0.5 font-sans">
                    <div>Origem <span className="font-mono text-slate-300">{l.host}</span>{l.agent && <> · agente <span className="font-mono">{l.agent}</span></>}{l.host_name && <> · nome na mensagem <span className="font-mono">{l.host_name}</span></>}
                      {l.program && <> · {l.kind === "trap" ? "trap" : "programa"} <span className="font-mono">{l.program}</span></>}{l.kind === "syslog" && l.facility != null && <> · facility {l.facility}</>}
                      {!l.device_id && <span className="text-amber-300"> · sem equipamento (vincule em Fontes)</span>}</div>
                    {l.kind === "trap" && (
                      <table className="font-mono text-[11px] mt-1"><tbody>
                        <tr><td className="pr-3 text-slate-500">trap</td><td className="text-slate-300">{l.trap_oid} ({l.version})</td></tr>
                        {(l.varbinds || []).map(([oid, nm, v], i) => <tr key={i}><td className="pr-3 text-slate-500 align-top">{nm || oid}</td><td className="text-slate-300 break-all">{v}</td></tr>)}
                      </tbody></table>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </Card>
      {rows.length >= 300 && <div className="text-center mt-3"><Button size="sm" variant="ghost" onClick={older} className="text-slate-300">Carregar mais antigas</Button></div>}
    </>
  );
}

// ------------------------------------------------------------------ regras
const EMPTY_RULE = { name: "", enabled: true, pattern: "", clear_pattern: "", kinds: "any", device_ids: [], max_severity: 7,
  threshold: 1, window_s: 60, severity: "medium", auto_resolve_min: 0, title: "" };

function RuleDialog({ rule, devices, onClose, onSaved }) {
  const [f, setF] = useState({ ...EMPTY_RULE, ...(rule || {}) });
  const [text, setText] = useState("");
  const [test, setTest] = useState(null);
  const [busy, setBusy] = useState(false);
  const set = (k, v) => setF(p => ({ ...p, [k]: v }));
  const runTest = async () => {
    setBusy(true);
    try { setTest((await api.post("/log-rules/test", { pattern: f.pattern, clear_pattern: f.clear_pattern, text })).data); }
    catch (e) { toast.error(formatApiError(e)); } finally { setBusy(false); }
  };
  const save = async () => {
    setBusy(true);
    try {
      const body = { ...f, threshold: Number(f.threshold), window_s: Number(f.window_s), auto_resolve_min: Number(f.auto_resolve_min), max_severity: Number(f.max_severity) };
      if (rule?.id) await api.put(`/log-rules/${rule.id}`, body); else await api.post("/log-rules", body);
      toast.success("Regra salva — vale em até 30 s");
      onSaved();
    } catch (e) { toast.error(formatApiError(e)); } finally { setBusy(false); }
  };
  const lbl = "text-xs text-slate-400 mb-1 block";
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="bg-surface border-line text-slate-100 max-w-3xl w-[96vw] max-h-[92vh] overflow-y-auto" data-testid="rule-dialog">
        <DialogHeader><DialogTitle>{rule?.id ? "Editar regra" : "Nova regra"}</DialogTitle></DialogHeader>
        <div className="grid sm:grid-cols-2 gap-3">
          <div><label className={lbl}>Nome</label><Input value={f.name} onChange={e => set("name", e.target.value)} className="bg-sunken border-line" data-testid="rule-name" /></div>
          <div><label className={lbl}>Severidade do incidente</label>
            <select value={f.severity} onChange={e => set("severity", e.target.value)} className={`${sel} w-full`}>
              {["critical", "high", "medium", "low", "info"].map(k => <option key={k} value={k}>{INC_SEV[k].label}</option>)}
            </select></div>
          <div className="sm:col-span-2"><label className={lbl}>Dispara quando a mensagem casar com (regex; o 1º grupo capturado separa os incidentes — ex.: o IP do vizinho, a interface, a PON)</label>
            <textarea value={f.pattern} onChange={e => set("pattern", e.target.value)} rows={3} className="w-full rounded-md bg-sunken border border-line p-2 font-mono text-xs text-slate-200" data-testid="rule-pattern" /></div>
          <div className="sm:col-span-2"><label className={lbl}>Normaliza (resolve o incidente) quando casar com — opcional, mesmo grupo</label>
            <textarea value={f.clear_pattern} onChange={e => set("clear_pattern", e.target.value)} rows={2} className="w-full rounded-md bg-sunken border border-line p-2 font-mono text-xs text-slate-200" data-testid="rule-clear" /></div>
          <div className="flex gap-2 items-end">
            <div className="flex-1"><label className={lbl}>Quantas vezes</label><Input type="number" min={1} value={f.threshold} onChange={e => set("threshold", e.target.value)} className="bg-sunken border-line" data-testid="rule-threshold" /></div>
            <div className="flex-1"><label className={lbl}>em (segundos)</label><Input type="number" min={10} value={f.window_s} onChange={e => set("window_s", e.target.value)} className="bg-sunken border-line" /></div>
          </div>
          <div><label className={lbl}>Resolver sozinho após N min sem repetir (0 = não)</label><Input type="number" min={0} value={f.auto_resolve_min} onChange={e => set("auto_resolve_min", e.target.value)} className="bg-sunken border-line" /></div>
          <div><label className={lbl}>Vale para</label>
            <select value={f.kinds} onChange={e => set("kinds", e.target.value)} className={`${sel} w-full`}>
              <option value="any">syslog e traps</option><option value="syslog">só syslog</option><option value="trap">só traps</option>
            </select></div>
          <div><label className={lbl}>Syslog até a severidade</label>
            <select value={f.max_severity} onChange={e => set("max_severity", Number(e.target.value))} className={`${sel} w-full`}>
              {SYS_SEV.map(([n], i) => <option key={n} value={i}>{i} — {n}{i === 7 ? " (todas)" : ""}</option>)}
            </select></div>
          <div className="sm:col-span-2"><label className={lbl}>Só destes equipamentos (nenhum marcado = todos) — Ctrl/⌘ para marcar vários</label>
            <select multiple value={f.device_ids} onChange={e => set("device_ids", [...e.target.selectedOptions].map(o => o.value))} className="w-full h-28 rounded-md bg-sunken border border-line p-1 text-sm text-slate-200">
              <TagOptions devices={devices} />
            </select></div>
          <div className="sm:col-span-2"><label className={lbl}>Título do incidente — {"{rule} {group} {device} {count} {host}"}</label>
            <Input value={f.title} onChange={e => set("title", e.target.value)} placeholder="{rule}: {group} — {device}" className="bg-sunken border-line font-mono text-xs" /></div>
          <label className="flex items-center gap-2 text-sm text-slate-200"><Switch checked={f.enabled} onCheckedChange={v => set("enabled", v)} /> Ativa</label>
        </div>
        <div className="mt-2 border-t border-line pt-3">
          <div className="text-sm text-slate-200 mb-1 flex items-center gap-1.5"><FlaskConical className="w-4 h-4" /> Testar</div>
          <textarea value={text} onChange={e => setText(e.target.value)} rows={4} placeholder="Cole linhas de log aqui (uma por linha). Vazio = testa com as últimas 500 mensagens recebidas."
                    className="w-full rounded-md bg-sunken border border-line p-2 font-mono text-xs text-slate-200" data-testid="rule-test-text" />
          <Button size="sm" variant="ghost" onClick={runTest} disabled={busy || !f.pattern} className="text-slate-200 mt-1" data-testid="rule-test">{busy ? <Loader2 className="w-4 h-4 animate-spin mr-1" /> : <FlaskConical className="w-4 h-4 mr-1" />} Testar</Button>
          {test && (
            <div className="mt-2 text-xs" data-testid="rule-test-result">
              <div className="text-slate-400 mb-1">{test.lines} linha(s): <span className="text-red-300">{test.fire} disparam</span> · <span className="text-emerald-300">{test.clear} normalizam</span></div>
              <div className="max-h-48 overflow-y-auto rounded border border-line divide-y divide-line font-mono">
                {test.results.map((r, i) => (
                  <div key={i} className="px-2 py-1 flex gap-2">
                    <span className={`w-16 shrink-0 ${r.match === "fire" ? "text-red-300" : r.match === "clear" ? "text-emerald-300" : "text-slate-600"}`}>{r.match === "fire" ? "dispara" : r.match === "clear" ? "normaliza" : "—"}</span>
                    {r.group && <span className="shrink-0 text-brand-soft">[{r.group}]</span>}
                    <span className="text-slate-300 break-all">{r.msg}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose} className="text-slate-300">Cancelar</Button>
          <Button onClick={save} disabled={busy || !f.pattern} className="bg-brand hover:bg-brand-strong text-onbrand" data-testid="rule-save">Salvar</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function Rules({ rules, devices, isAdmin, reload }) {
  const [edit, setEdit] = useState(null);
  const toggle = async (r, v) => {
    try { await api.put(`/log-rules/${r.id}`, { ...r, enabled: v }); reload(); } catch (e) { toast.error(formatApiError(e)); }
  };
  const del = async (r) => {
    if (!window.confirm(`Apagar a regra "${r.name}"? Incidentes já abertos por ela continuam lá.`)) return;
    try { await api.delete(`/log-rules/${r.id}`); reload(); } catch (e) { toast.error(formatApiError(e)); }
  };
  const presets = async () => {
    try { const r = await api.post("/log-rules/presets"); toast.success(r.data.added ? `${r.data.added} modelo(s) recolocado(s)` : "Todos os modelos já estão na lista"); reload(); }
    catch (e) { toast.error(formatApiError(e)); }
  };
  const devName = Object.fromEntries((devices || []).map(d => [d.id, d.name]));
  return (
    <>
      <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
        <p className="text-sm text-slate-400 max-w-3xl">Cada regra olha as mensagens que chegam. Casou N vezes na janela → abre incidente (um por equipamento + o que a regex capturou). Normalizou ou ficou quieto pelo tempo definido → resolve sozinho.</p>
        {isAdmin && <div className="flex gap-2">
          <Button size="sm" variant="ghost" onClick={presets} className="text-slate-300" data-testid="rules-presets"><RotateCcw className="w-4 h-4 mr-1" /> Recolocar modelos</Button>
          <Button size="sm" onClick={() => setEdit({})} className="bg-brand hover:bg-brand-strong text-onbrand" data-testid="rules-new"><Plus className="w-4 h-4 mr-1" /> Nova regra</Button>
        </div>}
      </div>
      <div className="grid gap-2" data-testid="rules-list">
        {rules.length === 0 && <Card className="bg-surface border-line p-6 text-center text-slate-500 text-sm">Nenhuma regra.</Card>}
        {rules.map(r => (
          <Card key={r.id} className={`bg-surface border-line p-3 ${r.enabled ? "" : "opacity-60"}`} data-testid={`rule-${r.preset || r.id}`}>
            <div className="flex flex-wrap items-start gap-3">
              {isAdmin && <Switch checked={!!r.enabled} onCheckedChange={v => toggle(r, v)} className="mt-0.5" />}
              <div className="flex-1 min-w-[14rem]">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-slate-100 font-medium">{r.name}</span>
                  <span className={`text-[10px] px-1.5 rounded border ${INC_SEV[r.severity]?.chip || ""}`}>{INC_SEV[r.severity]?.label}</span>
                  <span className="text-[11px] text-slate-400">{r.threshold > 1 ? `${r.threshold}× em ${r.window_s >= 120 ? `${Math.round(r.window_s / 60)} min` : `${r.window_s} s`}` : "na 1ª ocorrência"}
                    {r.clear_pattern ? " · normaliza pela mensagem de volta" : ""}{r.auto_resolve_min ? ` · resolve após ${r.auto_resolve_min} min quieto` : ""}
                    {r.kinds !== "any" ? ` · só ${r.kinds === "trap" ? "traps" : "syslog"}` : ""}
                    {r.device_ids?.length ? ` · ${r.device_ids.map(d => devName[d] || "?").slice(0, 3).join(", ")}${r.device_ids.length > 3 ? "…" : ""}` : ""}</span>
                </div>
                <div className="font-mono text-[11px] text-slate-500 truncate mt-0.5" title={r.pattern}>{r.pattern}</div>
              </div>
              <div className="text-right text-[11px] text-slate-400 whitespace-nowrap">
                <div>{r.hits || 0} ocorrência(s)</div>
                <div className="text-slate-500">{r.last_hit ? `última ${hhmmss(r.last_hit)}` : "nunca casou"}</div>
              </div>
              {isAdmin && <div className="flex gap-1">
                <Button size="sm" variant="ghost" onClick={() => setEdit(r)} className="h-8 text-slate-300" title="Editar"><Pencil className="w-4 h-4" /></Button>
                <Button size="sm" variant="ghost" onClick={() => del(r)} className="h-8 text-red-300" title="Apagar"><Trash2 className="w-4 h-4" /></Button>
              </div>}
            </div>
          </Card>
        ))}
      </div>
      {edit && <RuleDialog rule={edit.id ? edit : null} devices={devices} onClose={() => setEdit(null)} onSaved={() => { setEdit(null); reload(); }} />}
    </>
  );
}

// ------------------------------------------------------------------ fontes
function Sources({ devices }) {
  const nav = useNavigate();
  const [rows, setRows] = useState(null);
  const load = useCallback(() => api.get("/log-sources").then(r => setRows(r.data || [])).catch(e => toast.error(formatApiError(e))), []);
  useEffect(() => { load(); }, [load]);
  const link = async (ip, device_id) => {
    try { await api.put(`/log-sources/${encodeURIComponent(ip)}`, { device_id: device_id || null }); toast.success("Vínculo salvo — vale em até 1 min"); load(); }
    catch (e) { toast.error(formatApiError(e)); }
  };
  const forget = async (ip) => { try { await api.delete(`/log-sources/${encodeURIComponent(ip)}`); load(); } catch (e) { toast.error(formatApiError(e)); } };
  if (!rows) return <div className="py-10 text-center text-slate-500"><Loader2 className="w-5 h-5 animate-spin inline" /></div>;
  return (
    <>
      <p className="text-sm text-slate-400 mb-3 max-w-3xl">Todo IP que mandou syslog ou trap. O equipamento é achado pelo IP cadastrado ou pelo nome que vem na mensagem; quando o equipamento sai por outro IP (loopback, NAT, um rsyslog no jump), vincule aqui.</p>
      <Card className="bg-surface border-line overflow-x-auto">
        <table className="w-full text-sm" data-testid="sources-table">
          <thead className="text-xs text-slate-500 bg-panel"><tr>
            <th className="text-left px-3 py-2.5">Origem</th><th className="text-left px-3 py-2.5">Nome na mensagem</th><th className="text-left px-3 py-2.5">Equipamento</th>
            <th className="text-left px-3 py-2.5">Tipo</th><th className="text-right px-3 py-2.5">Mensagens</th><th className="text-left px-3 py-2.5">Última</th><th />
          </tr></thead>
          <tbody className="divide-y divide-line">
            {rows.length === 0 && <tr><td colSpan={7} className="py-10 text-center text-slate-500">Nenhuma origem ainda.</td></tr>}
            {rows.map(s => (
              <tr key={s.ip} data-testid={`source-${s.ip}`}>
                <td className="px-3 py-2 font-mono text-xs text-slate-200">{s.ip}</td>
                <td className="px-3 py-2 font-mono text-xs text-slate-400">{s.host_name || "—"}</td>
                <td className="px-3 py-2">
                  <select value={s.linked_device_id || ""} onChange={e => link(s.ip, e.target.value)} className={`${sel} h-8 text-xs max-w-[15rem]`}>
                    <option value="">{s.device_name ? `automático: ${s.device_name}` : "— não identificado —"}</option>
                    <TagOptions devices={devices} />
                  </select>
                </td>
                <td className="px-3 py-2 text-xs text-slate-400">{(s.kinds || []).join(" + ")}</td>
                <td className="px-3 py-2 text-right text-xs text-slate-300 tabular">{s.count || 0}{s.dropped > 0 && <span className="text-amber-300 block">{s.dropped} descartadas</span>}</td>
                <td className="px-3 py-2 text-xs text-slate-400 whitespace-nowrap">{hhmmss(s.last_at)}</td>
                <td className="px-3 py-2 text-right whitespace-nowrap">
                  <Button size="sm" variant="ghost" className="h-7 text-slate-300" title="Ver mensagens" onClick={() => s.device_id && nav(`/logs?device=${s.device_id}`)} disabled={!s.device_id}><ScrollText className="w-4 h-4" /></Button>
                  <Button size="sm" variant="ghost" className="h-7 text-slate-500" title="Esquecer (some da lista até mandar de novo)" onClick={() => forget(s.ip)}><Trash2 className="w-4 h-4" /></Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </>
  );
}

// ------------------------------------------------------------------ configurar
const VENDORS = (ip, sp, tp, comm, src) => [
  ["Cisco IOS / IOS-XE", `logging host ${ip}${sp !== 514 ? ` transport udp port ${sp}` : ""}
logging trap informational
logging source-interface ${src || "Loopback0"}
service timestamps log datetime msec localtime show-timezone
snmp-server host ${ip} version 2c ${comm}${tp !== 162 ? ` udp-port ${tp}` : ""}
snmp-server trap-source ${src || "Loopback0"}
snmp-server enable traps snmp linkdown linkup coldstart warmstart
snmp-server enable traps bgp
snmp-server enable traps ospf state-change
snmp-server enable traps config`],
  ["Cisco IOS-XR", `logging ${ip} vrf default severity info${sp !== 514 ? ` port ${sp}` : ""}
logging source-interface ${src || "Loopback0"}
snmp-server host ${ip} traps version 2c ${comm}${tp !== 162 ? ` udp-port ${tp}` : ""}
snmp-server traps snmp linkdown
snmp-server traps snmp linkup
snmp-server traps bgp
snmp-server traps config`],
  ["Huawei VRP (NE/CE/S)", `info-center enable
info-center loghost source ${src || "LoopBack0"}
info-center loghost ${ip}${sp !== 514 ? ` port ${sp}` : ""}
snmp-agent target-host trap address udp-domain ${ip}${tp !== 162 ? ` udp-port ${tp}` : ""} source ${src || "LoopBack0"} params securityname ${comm} v2c
snmp-agent trap enable`],
  ["Juniper Junos", `set system syslog host ${ip} any notice
set system syslog host ${ip} interactive-commands any${sp !== 514 ? `\nset system syslog host ${ip} port ${sp}` : ""}
set system syslog host ${ip} source-address ${src || "IP_DA_LOOPBACK"}
set snmp trap-options source-address ${src || "IP_DA_LOOPBACK"}
set snmp trap-group BASTION version v2
set snmp trap-group BASTION targets ${ip}${tp !== 162 ? `\nset snmp trap-group BASTION destination-port ${tp}` : ""}
set snmp trap-group BASTION categories link routing startup configuration authentication`],
  ["MikroTik RouterOS", `/system logging action add name=bastion target=remote remote=${ip} remote-port=${sp}${src ? ` src-address=${src}` : ""}
/system logging add action=bastion topics=critical
/system logging add action=bastion topics=error
/system logging add action=bastion topics=warning
/system logging add action=bastion topics=info
/snmp set enabled=yes trap-version=2 trap-community=${comm} trap-target=${ip} trap-generators=interfaces,start-trap trap-interfaces=all
# no RouterOS 7 recente, para mandar também o nome do roteador:
# /system logging action set bastion remote-log-format=syslog`],
  ["Linux / jump server (rsyslog repassando)", `# /etc/rsyslog.d/90-bastion.conf — recebe dos equipamentos da rede do jump e repassa para o BastiON
module(load="imudp")
input(type="imudp" port="514")
*.* @${ip}:${sp}
# depois: systemctl restart rsyslog  (nos equipamentos, o servidor de syslog é o IP do jump)
# traps: /etc/snmp/snmptrapd.conf →  disableAuthorization yes  +  forward default udp:${ip}:${tp}`],
];

function Configure({ isAdmin }) {
  const [cfg, setCfg] = useState(null);
  const [ip, setIp] = useState(() => window.location.hostname);
  const [comm, setComm] = useState("COMUNIDADE");
  const [src, setSrc] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (isAdmin) api.get("/logs-settings").then(r => { setCfg(r.data); if (r.data.trap_community) setComm(r.data.trap_community); }).catch(e => toast.error(formatApiError(e))); }, [isAdmin]);
  const save = async () => {
    setBusy(true);
    try {
      const body = { ...cfg, syslog_port: Number(cfg.syslog_port), trap_port: Number(cfg.trap_port), retention_days: Number(cfg.retention_days),
        rate_per_source: Number(cfg.rate_per_source), store_max_severity: Number(cfg.store_max_severity) };
      setCfg((await api.put("/logs-settings", body)).data);
      toast.success("Salvo — o coletor aplica em até 30 s");
    } catch (e) { toast.error(formatApiError(e)); } finally { setBusy(false); }
  };
  const sp = Number(cfg?.syslog_port || 514), tp = Number(cfg?.trap_port || 162);
  const set = (k, v) => setCfg(c => ({ ...c, [k]: v }));
  const lbl = "text-xs text-slate-400 mb-1 block";
  const copy = (t) => navigator.clipboard?.writeText(t).then(() => toast.success("Copiado"));
  return (
    <div className="grid gap-4">
      {isAdmin && cfg && (
        <Card className="bg-surface border-line p-4" data-testid="logs-settings">
          <div className="flex items-center gap-2 mb-3"><Radio className="w-4 h-4 text-brand-soft" /><span className="text-slate-100 font-medium">Coletor</span>
            <label className="ml-auto flex items-center gap-2 text-sm text-slate-200"><Switch checked={!!cfg.enabled} onCheckedChange={v => set("enabled", v)} data-testid="logs-enabled" /> Recebendo</label></div>
          <div className="grid sm:grid-cols-3 gap-3">
            <div><label className={lbl}>Porta UDP do syslog</label><Input type="number" value={cfg.syslog_port} onChange={e => set("syslog_port", e.target.value)} className="bg-sunken border-line" /></div>
            <div><label className={lbl}>Porta UDP dos traps</label><Input type="number" value={cfg.trap_port} onChange={e => set("trap_port", e.target.value)} className="bg-sunken border-line" /></div>
            <div><label className={lbl}>Guardar por (dias)</label><Input type="number" value={cfg.retention_days} onChange={e => set("retention_days", e.target.value)} className="bg-sunken border-line" data-testid="logs-retention" /></div>
            <div><label className={lbl}>Limite por origem (mensagens/s)</label><Input type="number" value={cfg.rate_per_source} onChange={e => set("rate_per_source", e.target.value)} className="bg-sunken border-line" /></div>
            <div><label className={lbl}>Guardar syslog até a severidade</label>
              <select value={cfg.store_max_severity} onChange={e => set("store_max_severity", Number(e.target.value))} className={`${sel} w-full`}>
                {SYS_SEV.map(([n], i) => <option key={n} value={i}>{i} — {n}{i === 7 ? " (tudo)" : ""}</option>)}
              </select></div>
            <div><label className={lbl}>Community dos traps (vazio = aceita qualquer uma)</label><Input value={cfg.trap_community} onChange={e => set("trap_community", e.target.value)} className="bg-sunken border-line" /></div>
          </div>
          <label className="flex items-center gap-2 text-sm text-slate-300 mt-3"><Switch checked={!!cfg.store_unknown} onCheckedChange={v => set("store_unknown", v)} /> Guardar também o que vem de IP que não é equipamento cadastrado</label>
          <p className="text-[11px] text-slate-500 mt-2">As regras olham todas as mensagens, mesmo as que não são guardadas. Traps SNMPv3 não são decodificados (use v2c para o BastiON).</p>
          <div className="mt-3"><Button onClick={save} disabled={busy} className="bg-brand hover:bg-brand-strong text-onbrand" data-testid="logs-settings-save">Salvar</Button></div>
        </Card>
      )}
      <Card className="bg-surface border-line p-4">
        <div className="text-slate-100 font-medium mb-1">Nos equipamentos</div>
        <p className="text-sm text-slate-400 mb-3">Aponte o syslog e os traps para o IP do BastiON. Equipamento atrás de jump server: mande para o jump e repasse com rsyslog (último modelo) — o BastiON reconhece pelo nome na mensagem ou pelo vínculo em Fontes.</p>
        <div className="grid sm:grid-cols-3 gap-3 mb-3">
          <div><label className={lbl}>IP do BastiON visto pelos equipamentos</label><Input value={ip} onChange={e => setIp(e.target.value)} className="bg-sunken border-line font-mono" data-testid="logs-ip" /></div>
          <div><label className={lbl}>Community dos traps</label><Input value={comm} onChange={e => setComm(e.target.value)} className="bg-sunken border-line font-mono" /></div>
          <div><label className={lbl}>Interface/IP de origem (opcional)</label><Input value={src} onChange={e => setSrc(e.target.value)} placeholder="Loopback0" className="bg-sunken border-line font-mono" /></div>
        </div>
        <div className="grid lg:grid-cols-2 gap-3">
          {VENDORS(ip || "IP_DO_BASTION", sp, tp, comm || "COMUNIDADE", src).map(([name, txt]) => (
            <div key={name} className="rounded-md border border-line bg-sunken">
              <div className="flex items-center justify-between px-3 py-1.5 border-b border-line text-xs text-slate-300">{name}
                <button onClick={() => copy(txt)} className="text-slate-400 hover:text-slate-100" title="Copiar"><Copy className="w-3.5 h-3.5" /></button></div>
              <pre className="p-3 text-[11px] font-mono text-slate-200 whitespace-pre-wrap break-all">{txt}</pre>
            </div>
          ))}
        </div>
        <p className="text-[11px] text-slate-500 mt-3">ZTE, Datacom, Nokia e outros: use o equivalente de "servidor de syslog" e "trap host v2c" do manual da versão — o BastiON aceita syslog padrão (RFC 3164/5424) e traps v1/v2c. Comandos acima são modelos; confira na sua versão antes de aplicar.</p>
        <div className="mt-3 rounded-md border border-amber-500/30 bg-amber-500/5 p-3 text-xs text-amber-200">
          <div className="font-medium mb-1 flex items-center gap-1.5"><Link2 className="w-3.5 h-3.5" /> Firewall do servidor</div>
          Libere as portas só para as redes dos equipamentos/jumps, por exemplo:
          <pre className="mt-1 font-mono text-[11px] text-amber-100 whitespace-pre-wrap">{`sudo ufw allow from 203.0.113.0/24 to any port ${sp} proto udp
sudo ufw allow from 203.0.113.0/24 to any port ${tp} proto udp`}</pre>
        </div>
      </Card>
    </div>
  );
}

// ------------------------------------------------------------------ página
export default function Logs() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const [sp, setSp] = useSearchParams();
  const tab = TABS.some(t => t[0] === sp.get("tab")) ? sp.get("tab") : "";
  const [devices, setDevices] = useState([]);
  const [rules, setRules] = useState([]);
  const loadRules = useCallback(() => api.get("/log-rules").then(r => setRules(r.data || [])).catch(() => {}), []);
  useEffect(() => { api.get("/devices").then(r => setDevices(r.data || [])).catch(() => {}); loadRules(); }, [loadRules]);
  const tabs = TABS.filter(([k]) => isAdmin || k !== "fontes");
  return (
    <div className="p-4 md:p-6 flex-1 overflow-y-auto" data-testid="logs-page">
      <div className="flex items-start justify-between flex-wrap gap-3 mb-4">
        <div>
          <h1 className="font-heading text-2xl sm:text-[1.75rem] font-semibold tracking-tight text-slate-100 flex items-center gap-2"><ScrollText className="w-6 h-6 text-brand-soft" /> Syslog e Traps</h1>
          <p className="text-sm text-slate-400 mt-1">Logs e traps SNMP de todos os equipamentos num lugar só. Regras transformam "BGP caiu", "interface oscilando" ou "LOS em massa" em incidente.</p>
        </div>
        <div className="flex gap-1 p-1 bg-sunken border border-line rounded-md text-sm overflow-x-auto max-w-full">
          {tabs.map(([k, l]) => (
            <button key={k} onClick={() => setSp(k ? { tab: k } : {})} data-testid={`logs-tab-${k || "msgs"}`}
                    className={`px-3 h-7 rounded whitespace-nowrap ${tab === k ? "bg-brand/20 text-brand-soft" : "text-slate-400 hover:text-slate-200"}`}>{l}</button>
          ))}
        </div>
      </div>
      {tab === "" && <Messages devices={devices} rules={rules} isAdmin={isAdmin} />}
      {tab === "regras" && <Rules rules={rules} devices={devices} isAdmin={isAdmin} reload={loadRules} />}
      {tab === "fontes" && isAdmin && <Sources devices={devices} />}
      {tab === "configurar" && <Configure isAdmin={isAdmin} />}
    </div>
  );
}
