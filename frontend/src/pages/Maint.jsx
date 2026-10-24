import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams, Link } from "react-router-dom";
import { api, formatApiError } from "@/lib/api";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { toast } from "sonner";
import { Wrench, Plus, Pencil, Trash2, Loader2, Play, Square, Ban, ArrowLeft, ClipboardCheck, ChevronDown, ChevronRight, BellOff, AlertTriangle, CheckCircle2 } from "lucide-react";
import { TagOptions } from "@/components/TagSelect";
import { useAuth } from "@/context/AuthContext";
import { SEV } from "@/pages/Incidents";

const STATE = { scheduled: ["Agendada", "text-sky-300 border-sky-500/40"], active: ["Em andamento", "text-amber-300 border-amber-500/50"],
  done: ["Encerrada", "text-slate-400 border-line"], cancelled: ["Cancelada", "text-slate-500 border-line"] };
const sel = "h-9 rounded-md bg-sunken border border-line px-2 text-sm text-slate-200";
const dtl = (iso) => { if (!iso) return ""; const d = new Date(iso); const p = (n) => String(n).padStart(2, "0"); return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`; };
const when = (iso) => (iso ? new Date(iso).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—");
const VENDOR = { huawei: "Huawei", cisco: "Cisco", juniper: "Juniper", datacom: "Datacom", zte: "ZTE", mikrotik: "MikroTik", linux: "Linux", other: "Outros" };

function WindowDialog({ win, devices, probes, isAdmin, onClose, onSaved }) {
  const now = new Date();
  const [f, setF] = useState(() => ({ name: "", description: "", start: dtl(new Date(now.getTime() + 3600e3).toISOString()), end: dtl(new Date(now.getTime() + 3 * 3600e3).toISOString()),
    all: false, device_ids: [], tags: [], probe_ids: [], silence: true, auto_checks: true, check_device_ids: [], commands: {}, notify: true,
    ...(win ? { ...win, start: dtl(win.start), end: dtl(win.end) } : {}) }));
  const [tpl, setTpl] = useState(null);
  const [showCmds, setShowCmds] = useState(false);
  const [busy, setBusy] = useState(false);
  useEffect(() => { api.get("/maint/templates").then(r => setTpl(r.data)).catch(() => {}); }, []);
  const set = (k, v) => setF(p => ({ ...p, [k]: v }));
  const allTags = useMemo(() => [...new Set(devices.flatMap(d => d.tags || []))].sort(), [devices]);
  const scopeDevs = useMemo(() => devices.filter(d => f.device_ids.includes(d.id) || (d.tags || []).some(t => f.tags.includes(t))), [devices, f.device_ids, f.tags]);
  const checkDevs = f.check_device_ids.length ? devices.filter(d => f.check_device_ids.includes(d.id)) : scopeDevs;
  const vendors = [...new Set(checkDevs.map(d => (tpl?.templates?.[d.device_type] ? d.device_type : "other")))];
  const cmdText = (vt) => (f.commands[vt] || tpl?.templates?.[vt] || []).map(([k, c]) => `${k} | ${c}`).join("\n");
  const setCmd = (vt, text) => set("commands", { ...f.commands, [vt]: text.split("\n").filter(l => l.trim()).map(l => { const i = l.indexOf("|"); return i > 0 ? [l.slice(0, i).trim(), l.slice(i + 1).trim()] : ["custom", l.trim()]; }) });
  const save = async () => {
    setBusy(true);
    try {
      const body = { ...f, start: new Date(f.start).toISOString(), end: new Date(f.end).toISOString() };
      if (win?.id) await api.put(`/maint/${win.id}`, body); else await api.post("/maint", body);
      toast.success("Janela salva"); onSaved();
    } catch (e) { toast.error(formatApiError(e)); } finally { setBusy(false); }
  };
  const lbl = "text-xs text-slate-400 mb-1 block";
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="bg-surface border-line text-slate-100 max-w-3xl w-[96vw] max-h-[92vh] overflow-y-auto" data-testid="maint-dialog">
        <DialogHeader><DialogTitle>{win?.id ? "Editar janela de manutenção" : "Nova janela de manutenção"}</DialogTitle></DialogHeader>
        <div className="grid sm:grid-cols-2 gap-3">
          <div className="sm:col-span-2"><label className={lbl}>O que vai ser feito</label><Input value={f.name} onChange={e => set("name", e.target.value)} placeholder="Troca do óptico do enlace X, atualização do R1…" className="bg-sunken border-line" data-testid="maint-name" /></div>
          <div><label className={lbl}>Início</label><Input type="datetime-local" value={f.start} onChange={e => set("start", e.target.value)} className="bg-sunken border-line" data-testid="maint-start" /></div>
          <div><label className={lbl}>Fim</label><Input type="datetime-local" value={f.end} onChange={e => set("end", e.target.value)} className="bg-sunken border-line" data-testid="maint-end" /></div>
          <div className="sm:col-span-2"><label className={lbl}>Detalhes (opcional)</label><textarea value={f.description} onChange={e => set("description", e.target.value)} rows={2} className="w-full rounded-md bg-sunken border border-line p-2 text-sm text-slate-200" /></div>
        </div>
        <div className="border-t border-line pt-3">
          <div className="text-sm text-slate-200 mb-2">Escopo — os alarmes disto ficam em silêncio durante a janela</div>
          {isAdmin && <label className="flex items-center gap-2 text-sm text-slate-300 mb-2"><Switch checked={f.all} onCheckedChange={v => set("all", v)} /> Rede toda</label>}
          {!f.all && <div className="grid sm:grid-cols-2 gap-3">
            <div><label className={lbl}>Equipamentos (Ctrl/⌘ para vários)</label>
              <select multiple value={f.device_ids} onChange={e => set("device_ids", [...e.target.selectedOptions].map(o => o.value))} className="w-full h-32 rounded-md bg-sunken border border-line p-1 text-sm text-slate-200" data-testid="maint-devs"><TagOptions devices={devices} /></select></div>
            <div><label className={lbl}>Grupos (tags)</label>
              <div className="flex flex-wrap gap-1.5">{allTags.length === 0 && <span className="text-xs text-slate-500">Nenhuma tag nos equipamentos.</span>}
                {allTags.map(t => <button key={t} onClick={() => set("tags", f.tags.includes(t) ? f.tags.filter(x => x !== t) : [...f.tags, t])}
                  className={`px-2 h-7 rounded border text-xs ${f.tags.includes(t) ? "border-brand bg-brand/15 text-slate-100" : "border-line text-slate-400"}`} data-testid={`maint-tag-${t}`}>{t}</button>)}</div>
              {probes.length > 0 && <><label className={`${lbl} mt-3`}>Sondas</label>
                <select multiple value={f.probe_ids} onChange={e => set("probe_ids", [...e.target.selectedOptions].map(o => o.value))} className="w-full h-20 rounded-md bg-sunken border border-line p-1 text-sm text-slate-200">
                  {probes.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></>}
            </div>
          </div>}
          <div className="flex flex-wrap gap-4 mt-3">
            <label className="flex items-center gap-2 text-sm text-slate-300"><Switch checked={f.silence} onCheckedChange={v => set("silence", v)} /> Silenciar alarmes</label>
            <label className="flex items-center gap-2 text-sm text-slate-300"><Switch checked={f.notify} onCheckedChange={v => set("notify", v)} /> Avisar início/fim no Telegram</label>
          </div>
        </div>
        <div className="border-t border-line pt-3">
          <label className="flex items-center gap-2 text-sm text-slate-200"><Switch checked={f.auto_checks} onCheckedChange={v => set("auto_checks", v)} data-testid="maint-auto" /> Pré-check no início e pós-check no fim (automático)</label>
          <p className="text-[11px] text-slate-500 mt-1">Também dá para rodar na mão na tela da janela. Lê vizinhos OSPF, sessões BGP, rotas, LDP/l2vc e interfaces e compara depois.</p>
          <label className={`${lbl} mt-2`}>Rodar o check em {f.check_device_ids.length ? "" : `(padrão: os ${scopeDevs.length} equipamento(s) do escopo)`}</label>
          <select multiple value={f.check_device_ids} onChange={e => set("check_device_ids", [...e.target.selectedOptions].map(o => o.value))} className="w-full h-24 rounded-md bg-sunken border border-line p-1 text-sm text-slate-200" data-testid="maint-checkdevs"><TagOptions devices={devices} /></select>
          <button onClick={() => setShowCmds(v => !v)} className="text-xs text-brand-soft mt-2 flex items-center gap-1">{showCmds ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />} Comandos por fabricante ({vendors.map(v => VENDOR[v] || v).join(", ") || "—"})</button>
          {showCmds && tpl && vendors.map(vt => (
            <div key={vt} className="mt-2">
              <div className="text-xs text-slate-400 mb-1">{VENDOR[vt] || vt} — uma linha por comando: <span className="font-mono">tipo | comando</span> (tipos: {Object.keys(tpl.kinds).join(", ")})</div>
              <textarea value={cmdText(vt)} onChange={e => setCmd(vt, e.target.value)} rows={6} className="w-full rounded-md bg-sunken border border-line p-2 font-mono text-xs text-slate-200" data-testid={`maint-cmds-${vt}`} />
            </div>
          ))}
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose} className="text-slate-300">Cancelar</Button>
          <Button onClick={save} disabled={busy || !f.name} className="bg-brand hover:bg-brand-strong text-onbrand" data-testid="maint-save">Salvar</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function Metric({ before, after, worse }) {
  const keys = [...new Set([...Object.keys(before || {}), ...Object.keys(after || {})])];
  if (!keys.length) return <span className="text-slate-600">—</span>;
  return <span className="flex flex-wrap gap-x-3">{keys.map(k => {
    const a = before?.[k], b = after?.[k], ch = a !== b;
    return <span key={k} className={ch ? (worse ? "text-red-300" : "text-amber-200") : "text-slate-400"}>{k} {a ?? "—"}{after ? <> → <b>{b ?? "—"}</b></> : ""}</span>;
  })}</span>;
}

function Detail({ id, onBack, onEdit }) {
  const [w, setW] = useState(null);
  const [open, setOpen] = useState({});
  const [raw, setRaw] = useState(null);
  const load = useCallback(() => api.get(`/maint/${id}`).then(r => setW(r.data)).catch(e => toast.error(formatApiError(e))), [id]);
  useEffect(() => { load(); }, [load]);
  const running = w && ["pre", "post"].some(p => w.checks?.[p]?.status === "running");
  useEffect(() => { if (!running) return undefined; const t = setInterval(load, 3000); return () => clearInterval(t); }, [running, load]);
  const act = async (a) => {
    if (a === "cancel" && !window.confirm("Cancelar a janela? Os alarmes voltam ao normal.")) return;
    try { setW((await api.post(`/maint/${id}/${a}`)).data); setTimeout(load, 300); } catch (e) { toast.error(formatApiError(e)); }
  };
  const showRaw = async (phase, device_id, cmd) => {
    try { const r = await api.get(`/maint/${id}/output`, { params: { phase, device_id, cmd } }); setRaw({ title: `${phase === "pre" ? "Pré" : "Pós"}-check · ${cmd}`, text: r.data.out || r.data.error }); }
    catch (e) { toast.error(formatApiError(e)); }
  };
  if (!w) return <div className="py-10 text-center"><Loader2 className="w-5 h-5 animate-spin inline text-slate-500" /></div>;
  const [stl, stc] = STATE[w.state] || STATE.scheduled;
  const ph = (p) => w.checks?.[p];
  return (
    <div data-testid="maint-detail">
      <button onClick={onBack} className="text-sm text-slate-400 hover:text-slate-200 flex items-center gap-1 mb-3"><ArrowLeft className="w-4 h-4" /> Janelas</button>
      <Card className="bg-surface border-line p-4 mb-4">
        <div className="flex flex-wrap items-start gap-3">
          <div className="flex-1 min-w-[14rem]">
            <div className="flex items-center gap-2"><span className="text-lg text-slate-100 font-medium">{w.name}</span><span className={`text-[11px] px-2 rounded border ${stc}`}>{stl}</span>{w.silence && w.state === "active" && <BellOff className="w-4 h-4 text-amber-300" title="alarmes silenciados" />}</div>
            <div className="text-sm text-slate-400">{when(w.start)} → {when(w.end)} · {w.all ? "rede toda" : [w.device_ids.length && `${w.device_ids.length} equipamento(s)`, w.tags.length && `grupos ${w.tags.join(", ")}`, w.probe_ids.length && `${w.probe_ids.length} sonda(s)`].filter(Boolean).join(" · ")} · por {w.created_by}</div>
            {w.description && <div className="text-sm text-slate-300 mt-1 whitespace-pre-wrap">{w.description}</div>}
          </div>
          <div className="flex flex-wrap gap-1.5">
            {w.state === "scheduled" && <Button size="sm" variant="ghost" onClick={() => act("start-now")} className="text-slate-200 h-8"><Play className="w-4 h-4 mr-1" /> Começar agora</Button>}
            {w.state === "active" && <Button size="sm" variant="ghost" onClick={() => act("end-now")} className="text-slate-200 h-8" data-testid="maint-end-now"><Square className="w-4 h-4 mr-1" /> Encerrar agora</Button>}
            {["scheduled", "active"].includes(w.state) && <Button size="sm" variant="ghost" onClick={() => act("cancel")} className="text-red-300 h-8"><Ban className="w-4 h-4 mr-1" /> Cancelar</Button>}
            <Button size="sm" variant="ghost" onClick={() => onEdit(w)} className="text-slate-300 h-8"><Pencil className="w-4 h-4" /></Button>
          </div>
        </div>
        <div className="flex flex-wrap gap-2 mt-3">
          {["pre", "post"].map(p => (
            <div key={p} className="flex items-center gap-2 rounded-md border border-line px-3 py-1.5 text-sm">
              <ClipboardCheck className="w-4 h-4 text-slate-400" />
              <span className="text-slate-200">{p === "pre" ? "Pré-check" : "Pós-check"}</span>
              <span className="text-xs text-slate-400">{ph(p)?.status === "running" ? <><Loader2 className="w-3 h-3 animate-spin inline" /> {ph(p).done}/{ph(p).total}</> : ph(p)?.status === "done" ? `${when(ph(p).finished_at)} · ${ph(p).by}` : "não rodou"}</span>
              <Button size="sm" variant="ghost" disabled={ph(p)?.status === "running" || (p === "post" && ph("pre")?.status !== "done")} onClick={() => act(p)} className="h-7 text-brand-soft" data-testid={`maint-run-${p}`}>{ph(p)?.status === "done" ? "Rodar de novo" : "Rodar"}</Button>
            </div>
          ))}
        </div>
      </Card>
      {w.compare?.length > 0 && <div className="space-y-2 mb-4" data-testid="maint-compare">
        {w.compare.map(c => (
          <Card key={c.device_id} className={`bg-surface border-line ${c.worse ? "border-red-500/50" : ""}`}>
            <div className="flex items-center gap-2 px-4 py-2.5 border-b border-line">
              {c.post_ok == null ? <ClipboardCheck className="w-4 h-4 text-slate-400" /> : c.worse || c.post_ok === false ? <AlertTriangle className="w-4 h-4 text-red-300" /> : <CheckCircle2 className="w-4 h-4 text-emerald-300" />}
              <span className="text-slate-100">{c.device_name}</span>
              <span className="text-xs text-slate-400">{c.pre_error ? `pré-check: ${c.pre_error}` : c.post_ok == null ? "aguardando pós-check" : c.post_error ? `pós-check: ${c.post_error}` : c.worse ? "piorou depois da manutenção" : c.changed ? "mudou, sem piora nos números" : "igual ao pré-check"}</span>
            </div>
            <table className="w-full text-xs"><tbody className="divide-y divide-line">
              {(c.rows.length ? c.rows : c.pre_metrics.map(m => ({ cmd: m.cmd, kind: m.kind, before: m.m, after: null, diff: [], changed: false }))).map(r => {
                const k = `${c.device_id}|${r.cmd}`;
                return (
                  <React.Fragment key={k}>
                    <tr className={r.worse ? "bg-red-500/5" : ""}>
                      <td className="px-4 py-1.5 font-mono text-slate-300 w-[40%]"><button onClick={() => setOpen(o => ({ ...o, [k]: !o[k] }))} className="flex items-center gap-1 text-left hover:text-white" disabled={!r.diff?.length}>
                        {r.diff?.length ? (open[k] ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />) : <span className="w-3" />}{r.cmd}</button></td>
                      <td className="px-2 py-1.5"><Metric before={r.before} after={r.after} worse={r.worse} />{r.error && <span className="text-red-300"> {r.error}</span>}</td>
                      <td className="px-2 py-1.5 text-right whitespace-nowrap">
                        <button onClick={() => showRaw("pre", c.device_id, r.cmd)} className="text-slate-500 hover:text-slate-200">antes</button>
                        {c.post_ok != null && <> · <button onClick={() => showRaw("post", c.device_id, r.cmd)} className="text-slate-500 hover:text-slate-200">depois</button></>}
                      </td>
                    </tr>
                    {open[k] && <tr><td colSpan={3} className="px-4 pb-2"><pre className="text-[11px] font-mono bg-sunken border border-line rounded p-2 overflow-x-auto max-h-72">{r.diff.map((l, i) => <div key={i} className={l.startsWith("+") ? "text-emerald-300" : l.startsWith("-") ? "text-red-300" : l.startsWith("@@") ? "text-slate-500" : "text-slate-400"}>{l}</div>)}</pre></td></tr>}
                  </React.Fragment>
                );
              })}
            </tbody></table>
          </Card>
        ))}
      </div>}
      {w.incidents?.length > 0 && <Card className="bg-surface border-line p-4">
        <div className="text-sm text-slate-200 mb-2 flex items-center gap-1.5"><BellOff className="w-4 h-4 text-amber-300" /> Alarmes silenciados nesta janela ({w.incidents.length})</div>
        <div className="space-y-1">{w.incidents.map(i => <Link key={i.id} to={`/incidents?id=${i.id}`} className="flex items-center gap-2 text-sm hover:bg-white/[0.03] rounded px-1">
          <span className={`w-2 h-2 rounded-full ${SEV[i.severity]?.bar || "bg-slate-500"}`} /><span className="text-slate-500 font-mono text-xs">#{i.number}</span><span className="text-slate-200 truncate flex-1">{i.title}</span><span className="text-xs text-slate-500">{when(i.created_at)} · {i.status === "resolved" ? "resolvido" : "aberto"}</span></Link>)}</div>
      </Card>}
      {raw && <Dialog open onOpenChange={(v) => !v && setRaw(null)}><DialogContent className="bg-surface border-line text-slate-100 max-w-4xl w-[96vw]"><DialogHeader><DialogTitle className="text-sm font-mono">{raw.title}</DialogTitle></DialogHeader>
        <pre className="text-[11px] font-mono bg-sunken border border-line rounded p-3 overflow-auto max-h-[70vh] whitespace-pre">{raw.text}</pre></DialogContent></Dialog>}
    </div>
  );
}

export default function Maint() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const [sp, setSp] = useSearchParams();
  const [rows, setRows] = useState(null);
  const [devices, setDevices] = useState([]);
  const [probes, setProbes] = useState([]);
  const [edit, setEdit] = useState(null);
  const [key, setKey] = useState(0);
  const load = useCallback(() => api.get("/maint").then(r => setRows(r.data || [])).catch(e => toast.error(formatApiError(e))), []);
  useEffect(() => {
    load(); api.get("/devices").then(r => setDevices(r.data || [])).catch(() => {});
    api.get("/probes").then(r => setProbes(r.data?.probes || [])).catch(() => {});
  }, [load]);
  const id = sp.get("id");
  const del = async (w) => { if (!window.confirm(`Apagar a janela "${w.name}" e os checks dela?`)) return; try { await api.delete(`/maint/${w.id}`); load(); } catch (e) { toast.error(formatApiError(e)); } };
  const groups = useMemo(() => {
    const r = rows || [];
    return [["Em andamento", r.filter(w => w.state === "active")], ["Agendadas", r.filter(w => w.state === "scheduled").reverse()], ["Anteriores", r.filter(w => ["done", "cancelled"].includes(w.state))]];
  }, [rows]);
  return (
    <div className="p-4 md:p-6 flex-1 overflow-y-auto" data-testid="maint-page">
      <div className="flex items-start justify-between flex-wrap gap-3 mb-4">
        <div>
          <h1 className="font-heading text-2xl sm:text-[1.75rem] font-semibold tracking-tight text-slate-100 flex items-center gap-2"><Wrench className="w-6 h-6 text-brand-soft" /> Janelas de manutenção</h1>
          <p className="text-sm text-slate-400 mt-1">Agende a manutenção: os alarmes do escopo ficam em silêncio (e não contam no SLA) e o pré/pós-check mostra o que mudou em OSPF, BGP, rotas e l2vc.</p>
        </div>
        {!id && <Button onClick={() => setEdit({})} className="bg-brand hover:bg-brand-strong text-onbrand" data-testid="maint-new"><Plus className="w-4 h-4 mr-1" /> Nova janela</Button>}
      </div>
      {id ? <Detail key={`${id}-${key}`} id={id} onBack={() => { setSp({}); load(); }} onEdit={(w) => setEdit(w)} /> : !rows ? <div className="py-10 text-center"><Loader2 className="w-5 h-5 animate-spin inline text-slate-500" /></div> : rows.length === 0 ? (
        <Card className="bg-surface border-line p-8 text-center text-sm text-slate-400">Nenhuma janela de manutenção.</Card>
      ) : groups.map(([g, list]) => list.length > 0 && (
        <div key={g} className="mb-4">
          <div className="text-xs uppercase tracking-wide text-slate-500 mb-1.5">{g}</div>
          <Card className="bg-surface border-line divide-y divide-line overflow-hidden">
            {list.map(w => {
              const [stl, stc] = STATE[w.state] || STATE.scheduled;
              const pre = w.checks?.pre, post = w.checks?.post;
              return (
                <div key={w.id} className="flex flex-wrap items-center gap-3 px-4 py-2.5 hover:bg-white/[0.03] cursor-pointer" onClick={() => setSp({ id: w.id })} data-testid={`maint-row-${w.id}`}>
                  <div className="flex-1 min-w-[14rem]">
                    <div className="text-slate-100">{w.name}</div>
                    <div className="text-xs text-slate-500">{when(w.start)} → {when(w.end)} · {w.all ? "rede toda" : [w.device_ids.length && `${w.device_ids.length} equip.`, w.tags.length && w.tags.join(", "), w.probe_ids.length && `${w.probe_ids.length} sonda(s)`].filter(Boolean).join(" · ")}</div>
                  </div>
                  <span className={`text-[11px] px-2 rounded border ${stc}`}>{stl}</span>
                  <span className="text-xs text-slate-400 w-40">{pre ? `pré ${pre.status === "running" ? "rodando" : "ok"}` : "sem pré-check"}{post ? ` · pós ${post.status === "running" ? "rodando" : "ok"}` : ""}</span>
                  <div className="flex gap-1" onClick={e => e.stopPropagation()}>
                    <Button size="sm" variant="ghost" onClick={() => setEdit(w)} className="h-8 text-slate-300"><Pencil className="w-4 h-4" /></Button>
                    <Button size="sm" variant="ghost" onClick={() => del(w)} className="h-8 text-red-300"><Trash2 className="w-4 h-4" /></Button>
                  </div>
                </div>
              );
            })}
          </Card>
        </div>
      ))}
      {edit && <WindowDialog win={edit.id ? edit : null} devices={devices} probes={probes} isAdmin={isAdmin} onClose={() => setEdit(null)} onSaved={() => { setEdit(null); load(); setKey(k => k + 1); }} />}
    </div>
  );
}
