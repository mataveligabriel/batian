import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api, formatApiError } from "@/lib/api";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { toast } from "sonner";
import {
  Siren, Search, Plus, Eye, CheckCircle2, RotateCcw, UserCheck, MessageSquare, TerminalSquare, ExternalLink, X, Loader2,
  Clock, Repeat,
} from "lucide-react";
import { TagOptions } from "@/components/TagSelect";
import { useTerminal } from "@/context/TerminalContext";

export const SEV = {
  critical: { label: "Crítica", bar: "bg-red-500", chip: "text-red-300 border-red-500/50 bg-red-500/10" },
  high: { label: "Alta", bar: "bg-orange-500", chip: "text-orange-300 border-orange-500/50 bg-orange-500/10" },
  medium: { label: "Média", bar: "bg-amber-400", chip: "text-amber-300 border-amber-500/50 bg-amber-500/10" },
  low: { label: "Baixa", bar: "bg-sky-500", chip: "text-sky-300 border-sky-500/50 bg-sky-500/10" },
  info: { label: "Info", bar: "bg-slate-500", chip: "text-slate-300 border-line bg-white/5" },
};
const ST = { open: ["Aberto", "text-red-300"], ack: ["Reconhecido", "text-amber-300"], resolved: ["Resolvido", "text-emerald-300"] };
const SRC = { zabbix: "Zabbix", librenms: "LibreNMS", generic: "Webhook", bastion: "BastiON", manual: "Manual" };
const VIEWS = [["active", "Ativos"], ["open", "Abertos"], ["ack", "Reconhecidos"], ["resolved", "Resolvidos"], ["all", "Todos"]];

export const fmtDur = (s) => {
  s = Math.max(0, Math.floor(s || 0));
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  return d ? `${d}d ${h}h` : h ? `${h}h ${m}min` : m ? `${m}min` : `${s}s`;
};
const when = (iso) => (iso ? new Date(iso).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—");

function NewIncident({ open, onClose, onCreated }) {
  const [f, setF] = useState({ title: "", severity: "medium", description: "", device_id: "" });
  const [devs, setDevs] = useState([]);
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (open) api.get("/devices").then(r => setDevs(r.data || [])).catch(() => {}); }, [open]);
  const save = async () => {
    setBusy(true);
    try { const { data } = await api.post("/incidents", { ...f, device_id: f.device_id || null }); onCreated(data); setF({ title: "", severity: "medium", description: "", device_id: "" }); }
    catch (e) { toast.error(formatApiError(e)); }
    setBusy(false);
  };
  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="bg-surface border-line text-slate-100 max-w-lg" data-testid="inc-new-dialog">
        <DialogHeader><DialogTitle>Novo incidente</DialogTitle></DialogHeader>
        <div className="space-y-3 text-sm">
          <Input value={f.title} onChange={e => setF({ ...f, title: e.target.value })} placeholder="o que está acontecendo (ex.: clientes do POP Centro sem navegação)" className="bg-sunken border-line" data-testid="inc-new-title" />
          <div className="grid grid-cols-2 gap-2">
            <select value={f.severity} onChange={e => setF({ ...f, severity: e.target.value })} className="h-9 rounded-md bg-sunken border border-line px-2 text-slate-100" data-testid="inc-new-sev">
              {Object.entries(SEV).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
            </select>
            <select value={f.device_id} onChange={e => setF({ ...f, device_id: e.target.value })} className="h-9 rounded-md bg-sunken border border-line px-2 text-slate-100">
              <option value="">— equipamento (opcional) —</option>
              <TagOptions devices={devs} />
            </select>
          </div>
          <textarea value={f.description} onChange={e => setF({ ...f, description: e.target.value })} rows={4} placeholder="detalhes"
                    className="w-full rounded-md bg-sunken border border-line p-2 text-slate-100" />
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose} className="text-slate-300">Cancelar</Button>
          <Button onClick={save} disabled={busy || !f.title.trim()} className="bg-brand hover:bg-brand-strong text-onbrand" data-testid="inc-new-save">Abrir incidente</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function Detail({ id, onClose, onChanged }) {
  const [inc, setInc] = useState(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState("");
  const [people, setPeople] = useState([]);
  const nav = useNavigate();
  const term = useTerminal();
  const load = useCallback(() => api.get(`/incidents/${id}`).then(r => setInc(r.data)).catch(e => toast.error(formatApiError(e))), [id]);
  useEffect(() => { setInc(null); load(); api.get("/incidents-assignees").then(r => setPeople(r.data)).catch(() => {}); }, [load]);
  const act = async (action, extra = {}) => {
    setBusy(action);
    try { const { data } = await api.post(`/incidents/${id}/${action}`, { note: action === "comment" ? note : "", ...extra }); setInc(data); if (action === "comment") setNote(""); onChanged(); }
    catch (e) { toast.error(formatApiError(e)); }
    setBusy("");
  };
  const openTerminal = async () => {
    try {
      const { data } = await api.get("/devices");
      const d = (data || []).find(x => x.id === inc.device_id);
      if (!d) return toast.error("Esse equipamento não está na sua lista");
      if (term?.openTab) term.openTab(d);
      nav("/terminal");
    } catch (e) { toast.error(formatApiError(e)); }
  };
  if (!inc) return <div className="p-6 text-sm text-slate-500"><Loader2 className="w-4 h-4 animate-spin inline mr-2" />Carregando…</div>;
  const sev = SEV[inc.severity] || SEV.medium;
  const [stl, stc] = ST[inc.status] || ST.open;
  return (
    <div className="flex flex-col h-full" data-testid="inc-detail">
      <div className="p-4 border-b border-line">
        <div className="flex items-start gap-2">
          <div className="min-w-0 flex-1">
            <div className="text-xs text-slate-500 font-mono">#{inc.number} · {SRC[inc.source] || inc.source}</div>
            <div className="text-base font-semibold text-slate-100 mt-0.5 break-words">{inc.title}</div>
          </div>
          <button onClick={onClose} className="text-slate-500 hover:text-slate-200" aria-label="Fechar" data-testid="inc-detail-close"><X className="w-5 h-5" /></button>
        </div>
        <div className="flex flex-wrap items-center gap-2 mt-2 text-xs">
          <span className={`px-1.5 py-0.5 rounded border ${sev.chip}`}>{sev.label}</span>
          <span className={stc}>{stl}</span>
          <span className="text-slate-400 flex items-center gap-1"><Clock className="w-3.5 h-3.5" /> {fmtDur(inc.duration_s)}{inc.status === "resolved" ? "" : " até agora"}</span>
          {inc.count > 1 && <span className="text-slate-400 flex items-center gap-1"><Repeat className="w-3.5 h-3.5" /> repetiu {inc.count}×</span>}
        </div>
        {(inc.device_name || inc.host) && <div className="text-sm text-slate-300 mt-2">📍 {inc.device_name || inc.host}{inc.host && inc.device_name && inc.host !== inc.device_name ? <span className="text-slate-500 font-mono text-xs"> · {inc.host}</span> : ""}</div>}
        <div className="flex flex-wrap gap-2 mt-3">
          {inc.status === "open" && <Button size="sm" onClick={() => act("ack")} disabled={!!busy} className="bg-amber-600 hover:bg-amber-700 text-onbrand" data-testid="inc-ack"><Eye className="w-4 h-4 mr-1.5" /> Reconhecer</Button>}
          {inc.status !== "resolved" && <Button size="sm" onClick={() => act("resolve")} disabled={!!busy} className="bg-emerald-600 hover:bg-emerald-700 text-onbrand" data-testid="inc-resolve"><CheckCircle2 className="w-4 h-4 mr-1.5" /> Resolver</Button>}
          {inc.status === "resolved" && <Button size="sm" variant="ghost" onClick={() => act("reopen")} disabled={!!busy} className="text-slate-300" data-testid="inc-reopen"><RotateCcw className="w-4 h-4 mr-1.5" /> Reabrir</Button>}
          <select value={inc.assignee?.id || ""} onChange={e => act("assign", { user_id: e.target.value || null })} data-testid="inc-assign"
                  className="h-8 rounded-md bg-sunken border border-line px-2 text-xs text-slate-200">
            <option value="">{inc.assignee ? "— assumir eu —" : "sem responsável — assumir"}</option>
            {people.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
          {inc.device_id && <Button size="sm" variant="ghost" onClick={openTerminal} className="text-brand-soft" data-testid="inc-terminal"><TerminalSquare className="w-4 h-4 mr-1.5" /> Terminal</Button>}
          {inc.url && <a href={inc.url} target="_blank" rel="noreferrer" className="h-8 px-2 inline-flex items-center text-xs text-brand-soft hover:underline"><ExternalLink className="w-3.5 h-3.5 mr-1" /> {SRC[inc.source]}</a>}
        </div>
        {inc.assignee && <div className="text-xs text-slate-400 mt-2 flex items-center gap-1"><UserCheck className="w-3.5 h-3.5" /> responsável: {inc.assignee.name}</div>}
      </div>
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {inc.description && <pre className="text-xs text-slate-300 whitespace-pre-wrap bg-sunken border border-line rounded-md p-3 font-mono">{inc.description}</pre>}
        <div>
          <div className="text-xs font-medium text-slate-400 mb-2">Linha do tempo</div>
          <ol className="border-l border-line ml-1.5 space-y-3" data-testid="inc-timeline">
            {(inc.timeline || []).map((t, i) => (
              <li key={i} className="pl-4 relative">
                <span className={`absolute -left-[5px] top-1.5 w-2.5 h-2.5 rounded-full ${t.type === "resolved" || t.type === "resolve" ? "bg-emerald-500" : t.type === "open" ? "bg-red-500" : t.type === "ack" ? "bg-amber-500" : "bg-slate-500"}`} />
                <div className="text-[11px] text-slate-500">{when(t.at)} · {t.by}</div>
                <div className="text-sm text-slate-200 whitespace-pre-wrap break-words">{t.text}</div>
              </li>
            ))}
          </ol>
        </div>
      </div>
      <div className="p-3 pr-[4.5rem] border-t border-line flex gap-2">   {/* espaço para o botão do assistente */}
        <Input value={note} onChange={e => setNote(e.target.value)} onKeyDown={e => e.key === "Enter" && note.trim() && act("comment")}
               placeholder="comentário (o que foi feito, com quem falou…)" className="bg-sunken border-line h-9" data-testid="inc-note" />
        <Button onClick={() => act("comment")} disabled={!note.trim() || !!busy} className="bg-brand hover:bg-brand-strong text-onbrand h-9 shrink-0" data-testid="inc-comment"><MessageSquare className="w-4 h-4" /></Button>
      </div>
    </div>
  );
}

export default function Incidents() {
  const [sp, setSp] = useSearchParams();
  const [view, setView] = useState("active");
  const [q, setQ] = useState("");
  const [sev, setSev] = useState("");
  const [src, setSrc] = useState("");
  const [rows, setRows] = useState(null);
  const [sum, setSum] = useState(null);
  const [newOpen, setNewOpen] = useState(false);
  const sel = sp.get("id");
  const timer = useRef(null);
  const load = useCallback(async () => {
    try {
      const [a, b] = await Promise.all([api.get("/incidents", { params: { status: view, q, severity: sev, source: src } }), api.get("/incidents-summary")]);
      setRows(a.data); setSum(b.data);
    } catch (e) { toast.error(formatApiError(e)); }
  }, [view, q, sev, src]);
  useEffect(() => {
    const t = setTimeout(load, q ? 300 : 0);
    clearInterval(timer.current);
    timer.current = setInterval(load, 15000);
    return () => { clearTimeout(t); clearInterval(timer.current); };
  }, [load, q]);
  const open = (id) => setSp(id ? { id } : {});
  const counts = useMemo(() => sum?.by_severity || {}, [sum]);

  return (
    <div className="flex-1 min-h-0 flex" data-testid="incidents-page">
      <div className={`flex-1 min-w-0 overflow-y-auto p-4 md:p-6 ${sel ? "hidden lg:block" : ""}`}>
        <div className="flex items-start justify-between flex-wrap gap-3 mb-4">
          <div>
            <h1 className="font-heading text-2xl sm:text-[1.75rem] font-semibold tracking-tight text-slate-100 flex items-center gap-2"><Siren className="w-6 h-6 text-red-400" /> Incidentes</h1>
            <p className="text-sm text-slate-400 mt-1">Alarmes do Zabbix, LibreNMS e BastiON num lugar só. Reconheça, assuma e resolva — também pelo Telegram.</p>
          </div>
          <Button onClick={() => setNewOpen(true)} className="bg-brand hover:bg-brand-strong text-onbrand" data-testid="inc-new"><Plus className="w-4 h-4 mr-1.5" /> Novo incidente</Button>
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-5 gap-2 mb-4" data-testid="inc-counters">
          {["critical", "high", "medium", "low", "info"].map(k => (
            <button key={k} onClick={() => setSev(sev === k ? "" : k)} data-testid={`inc-count-${k}`}
                    className={`rounded-md border p-2.5 text-left transition-colors ${sev === k ? "border-brand bg-brand/10" : "border-line bg-surface hover:border-line2"}`}>
              <div className="flex items-center gap-1.5 text-xs text-slate-400"><span className={`w-2 h-2 rounded-full ${SEV[k].bar}`} /> {SEV[k].label}</div>
              <div className="text-2xl font-semibold text-slate-100 tabular">{counts[k] || 0}</div>
            </button>
          ))}
        </div>
        <div className="flex flex-wrap gap-2 items-center mb-3">
          <div className="flex gap-1 p-1 bg-sunken border border-line rounded-md text-sm overflow-x-auto max-w-full">
            {VIEWS.map(([k, l]) => (
              <button key={k} onClick={() => setView(k)} data-testid={`inc-view-${k}`}
                      className={`shrink-0 px-3 h-7 rounded ${view === k ? "bg-brand/20 text-brand-soft" : "text-slate-400 hover:text-slate-200"}`}>
                {l}{k === "active" && sum ? ` (${sum.active})` : ""}
              </button>
            ))}
          </div>
          <select value={src} onChange={e => setSrc(e.target.value)} className="h-9 rounded-md bg-sunken border border-line px-2 text-sm text-slate-200" data-testid="inc-src">
            <option value="">todas as origens</option>
            {Object.entries(SRC).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
          <div className="relative flex-1 min-w-[12rem]">
            <Search className="w-3.5 h-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-500" />
            <Input value={q} onChange={e => setQ(e.target.value)} placeholder="buscar #número, título, equipamento…" className="pl-8 bg-sunken border-line h-9" data-testid="inc-search" />
          </div>
        </div>
        <Card className="bg-surface border-line overflow-hidden">
          {rows === null ? <div className="p-6 text-sm text-slate-500">Carregando…</div>
            : rows.length === 0 ? <div className="p-8 text-center text-sm text-slate-500" data-testid="inc-empty">{view === "active" ? "Nenhum incidente ativo. 🎉" : "Nada por aqui."}</div>
              : (
                <div className="divide-y divide-line" data-testid="inc-list">
                  {rows.map(r => {
                    const s = SEV[r.severity] || SEV.medium, [stl, stc] = ST[r.status] || ST.open;
                    return (
                      <button key={r.id} onClick={() => open(r.id)} data-testid={`inc-row-${r.number}`}
                              className={`w-full text-left flex items-stretch hover:bg-white/[0.03] ${sel === r.id ? "bg-brand/10" : ""}`}>
                        <span className={`w-1 shrink-0 ${s.bar}`} />
                        <div className="flex-1 min-w-0 px-3 py-2.5 grid grid-cols-1 md:grid-cols-[4rem_1fr_11rem_7rem_6rem] gap-x-3 gap-y-0.5 items-center">
                          <span className="text-xs font-mono text-slate-500">#{r.number}</span>
                          <span className="min-w-0">
                            <span className="block text-sm text-slate-100 truncate">{r.title}</span>
                            <span className="block text-xs text-slate-500 truncate">{r.device_name || r.host || "—"} · {SRC[r.source] || r.source}{r.count > 1 ? ` · ${r.count}×` : ""}{r.assignee ? ` · ${r.assignee.name}` : ""}</span>
                          </span>
                          <span className="text-xs text-slate-400">{when(r.created_at)}</span>
                          <span className={`text-xs ${stc}`}>{stl}</span>
                          <span className="text-xs text-slate-400 font-mono md:text-right">{fmtDur(r.duration_s)}</span>
                        </div>
                      </button>
                    );
                  })}
                </div>
              )}
        </Card>
      </div>
      {sel && (
        <aside className="w-full lg:w-[28rem] shrink-0 border-l border-line bg-panel min-h-0">
          <Detail id={sel} onClose={() => open(null)} onChanged={load} />
        </aside>
      )}
      <NewIncident open={newOpen} onClose={() => setNewOpen(false)} onCreated={(i) => { setNewOpen(false); load(); open(i.id); toast.success(`Incidente #${i.number} aberto`); }} />
    </div>
  );
}
