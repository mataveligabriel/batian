import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams, Link } from "react-router-dom";
import { api, formatApiError } from "@/lib/api";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { toast } from "sonner";
import { Gauge, Plus, Pencil, Trash2, Loader2, FileText, Download, ChevronDown, ChevronRight, X, Printer } from "lucide-react";
import { useAuth } from "@/context/AuthContext";

const KINDS = { link: "Enlace", pop: "POP", client: "Cliente", service: "Serviço", other: "Outro" };
const TYPE_LABEL = { probe: "Sonda", device: "Equipamento", interface: "Interface" };
const sel = "h-9 rounded-md bg-sunken border border-line px-2 text-sm text-slate-200";
const thisMonth = () => { const d = new Date(Date.now() - 3 * 3600000); return d.toISOString().slice(0, 7); };
export const fmtDur = (s) => {
  s = Math.max(0, Math.round(s || 0));
  if (s < 60) return `${s}s`;
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  return (`${d ? `${d}d ` : ""}${h || d ? `${h}h ` : ""}${m || (!h && !d) ? `${m}min` : ""}`).trim();
};
export const fmtPct = (v, dec = 3) => (v == null ? "—" : `${Number(v).toFixed(dec).replace(".", ",")}%`);
const monthLabel = (m) => { const [y, mo] = m.split("-").map(Number); const t = new Date(y, mo - 1, 1).toLocaleString("pt-BR", { month: "long", year: "numeric" }); return t.charAt(0).toUpperCase() + t.slice(1); };
const when = (iso) => new Date(iso).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });

export function toCsv(rep) {
  const head = ["item", "tipo", "cliente", "meta_%", "disponibilidade_%", "cumpriu", "indisponivel", "manutencao", "quedas", "mttr", "maior_queda", "cobertura_%"];
  const rows = rep.items.map(({ item: it, result: r }) => [it.name, KINDS[it.kind] || it.kind, it.customer || "", String(it.target).replace(".", ","),
    r.availability == null ? "" : r.availability.toFixed(3).replace(".", ","), r.met ? "sim" : "não", fmtDur(r.downtime_s), fmtDur(r.maint_s),
    r.outages, fmtDur(r.mttr_s), fmtDur(r.longest_s), String(r.coverage).replace(".", ",")]);
  return "﻿" + [head, ...rows].map(r => r.map(x => `"${String(x).replace(/"/g, '""')}"`).join(";")).join("\r\n");
}

function Bar({ value, target }) {
  if (value == null) return <div className="h-2 rounded bg-white/5" />;
  // escala de 95% a 100% (o que interessa num SLA)
  const pos = (v) => Math.max(0, Math.min(100, ((v - 95) / 5) * 100));
  return (
    <div className="relative h-2 rounded bg-white/5 overflow-hidden" title={`meta ${target}%`}>
      <div className={`absolute inset-y-0 left-0 ${value >= target ? "bg-emerald-500" : "bg-red-500"}`} style={{ width: `${pos(value)}%` }} />
      <div className="absolute inset-y-0 w-0.5 bg-slate-200" style={{ left: `${pos(target)}%` }} />
    </div>
  );
}

function ItemDialog({ item, onClose, onSaved }) {
  const [f, setF] = useState({ name: "", kind: "link", target: 99.5, customer: "", notes: "", components: [], ...(item || {}) });
  const [opts, setOpts] = useState(null);
  const [add, setAdd] = useState({ type: "interface", v: "" });
  const [busy, setBusy] = useState(false);
  useEffect(() => { api.get("/sla/components").then(r => setOpts(r.data)).catch(e => toast.error(formatApiError(e))); }, []);
  const label = (c) => {
    if (!opts) return c.type;
    if (c.type === "probe") return opts.probes.find(p => p.probe_id === c.probe_id && p.source === c.source)?.label || "sonda removida";
    if (c.type === "interface") return opts.interfaces.find(i => i.device_id === c.device_id && i.if_index === c.if_index)?.label || "interface";
    return opts.devices.find(d => d.id === c.device_id)?.name || "equipamento";
  };
  const choices = !opts ? [] : add.type === "probe" ? opts.probes.map(p => [`${p.probe_id}|${p.source}`, p.label])
    : add.type === "interface" ? opts.interfaces.map(i => [`${i.device_id}|${i.if_index}`, i.label]) : opts.devices.map(d => [d.id, `${d.name} (${d.host})`]);
  const addComp = () => {
    if (!add.v) return;
    let c;
    if (add.type === "probe") { const [probe_id, source] = add.v.split("|"); c = { type: "probe", probe_id, source }; }
    else if (add.type === "interface") { const [device_id, idx] = add.v.split("|"); c = { type: "interface", device_id, if_index: Number(idx) }; }
    else c = { type: "device", device_id: add.v };
    if (f.components.some(x => JSON.stringify(x) === JSON.stringify(c))) return;
    setF(p => ({ ...p, components: [...p.components, c] })); setAdd(a => ({ ...a, v: "" }));
  };
  const save = async () => {
    setBusy(true);
    try {
      const body = { ...f, target: Number(String(f.target).replace(",", ".")) };
      if (item?.id) await api.put(`/sla/items/${item.id}`, body); else await api.post("/sla/items", body);
      toast.success("Item salvo"); onSaved();
    } catch (e) { toast.error(formatApiError(e)); } finally { setBusy(false); }
  };
  const lbl = "text-xs text-slate-400 mb-1 block";
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="bg-surface border-line text-slate-100 max-w-2xl w-[96vw] max-h-[92vh] overflow-y-auto" data-testid="sla-dialog">
        <DialogHeader><DialogTitle>{item?.id ? "Editar item de SLA" : "Novo item de SLA"}</DialogTitle></DialogHeader>
        <div className="grid sm:grid-cols-3 gap-3">
          <div className="sm:col-span-2"><label className={lbl}>Nome</label><Input value={f.name} onChange={e => setF({ ...f, name: e.target.value })} placeholder="Trânsito operadora X, POP Centro, Cliente Y…" className="bg-sunken border-line" data-testid="sla-name" /></div>
          <div><label className={lbl}>Tipo</label><select value={f.kind} onChange={e => setF({ ...f, kind: e.target.value })} className={`${sel} w-full`}>{Object.entries(KINDS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></div>
          <div><label className={lbl}>Meta (%)</label><Input value={f.target} onChange={e => setF({ ...f, target: e.target.value })} className="bg-sunken border-line" data-testid="sla-target" /></div>
          <div className="sm:col-span-2"><label className={lbl}>Cliente / contrato (opcional)</label><Input value={f.customer} onChange={e => setF({ ...f, customer: e.target.value })} className="bg-sunken border-line" /></div>
        </div>
        <div>
          <label className={lbl}>Componentes — o item fica fora quando qualquer um estiver fora</label>
          <div className="space-y-1 mb-2" data-testid="sla-comps">
            {f.components.length === 0 && <div className="text-xs text-slate-500">Nenhum ainda.</div>}
            {f.components.map((c, i) => (
              <div key={i} className="flex items-center gap-2 text-sm rounded border border-line px-2 py-1">
                <span className="text-[10px] uppercase text-slate-500 w-20">{TYPE_LABEL[c.type]}</span>
                <span className="flex-1 text-slate-200 truncate">{label(c)}</span>
                <button onClick={() => setF(p => ({ ...p, components: p.components.filter((_, j) => j !== i) }))} className="text-slate-500 hover:text-red-300"><X className="w-4 h-4" /></button>
              </div>
            ))}
          </div>
          <div className="flex flex-wrap gap-2">
            <select value={add.type} onChange={e => setAdd({ type: e.target.value, v: "" })} className={sel} data-testid="sla-add-type">
              <option value="interface">Interface monitorada</option><option value="probe">Sonda</option><option value="device">Equipamento</option>
            </select>
            <select value={add.v} onChange={e => setAdd({ ...add, v: e.target.value })} className={`${sel} flex-1 min-w-[12rem]`} data-testid="sla-add-value">
              <option value="">{opts ? (choices.length ? "escolha…" : "nada disponível") : "carregando…"}</option>
              {choices.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
            <Button size="sm" variant="ghost" onClick={addComp} disabled={!add.v} className="text-slate-200 h-9" data-testid="sla-add"><Plus className="w-4 h-4 mr-1" /> Adicionar</Button>
          </div>
          <p className="text-[11px] text-slate-500 mt-2">Interface: as marcadas para monitorar (queda/volta). Equipamento: quando o BastiON não alcança. Sonda: ciclos que falharam (Sondas ativas). Janelas de manutenção não contam como indisponibilidade.</p>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose} className="text-slate-300">Cancelar</Button>
          <Button onClick={save} disabled={busy || !f.name || !f.components.length} className="bg-brand hover:bg-brand-strong text-onbrand" data-testid="sla-save">Salvar</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function ItemDetail({ r }) {
  return (
    <div className="px-4 pb-3 text-xs">
      <table className="w-full mb-2">
        <thead className="text-slate-500"><tr><th className="text-left py-1">Componente</th><th className="text-right">Disponibilidade</th><th className="text-right">Fora</th><th className="text-right hidden sm:table-cell">Manutenção</th><th className="text-right">Quedas</th></tr></thead>
        <tbody className="divide-y divide-line">
          {r.components.map((c, i) => <tr key={i}><td className="py-1 text-slate-300">{c.label}{c.coverage < 99 && <span className="text-amber-300"> · medido {c.coverage}% do tempo</span>}</td>
            <td className="text-right font-mono text-slate-200">{fmtPct(c.availability)}</td><td className="text-right font-mono">{fmtDur(c.downtime_s)}</td>
            <td className="text-right font-mono hidden sm:table-cell">{c.maint_s ? fmtDur(c.maint_s) : "—"}</td><td className="text-right">{c.outages}</td></tr>)}
        </tbody>
      </table>
      {r.quality?.length > 0 && <div className="mb-2 text-slate-400">{r.quality.map((q, i) => <div key={i}>{q.label}: latência média <b className="text-slate-200">{q.ms_avg != null ? `${q.ms_avg} ms` : "—"}</b>
        {q.loss != null && <> · perda <b className="text-slate-200">{fmtPct(q.loss, 2)}</b></>} · dentro dos limites da sonda {fmtPct(q.within_limits, 2)}</div>)}</div>}
      {r.outage_list.length > 0 && <div><div className="text-slate-500 mb-1">Quedas</div>
        <div className="flex flex-wrap gap-1">{r.outage_list.slice(0, 40).map((o, i) => <span key={i} className="px-1.5 py-0.5 rounded border border-line text-slate-300">{when(o.start)} · {fmtDur(o.seconds)}</span>)}</div></div>}
    </div>
  );
}

export default function Sla() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const [sp, setSp] = useSearchParams();
  const month = sp.get("month") || thisMonth();
  const [rep, setRep] = useState(null);
  const [edit, setEdit] = useState(null);
  const [open, setOpen] = useState({});
  const [cfg, setCfg] = useState(null);
  const load = useCallback(() => { setRep(null); api.get("/sla/report", { params: { month } }).then(r => setRep(r.data)).catch(e => toast.error(formatApiError(e))); }, [month]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => { if (isAdmin) api.get("/sla/settings").then(r => setCfg(r.data)).catch(() => {}); }, [isAdmin]);
  const del = async (it) => {
    if (!window.confirm(`Apagar o item "${it.name}"?`)) return;
    try { await api.delete(`/sla/items/${it.id}`); load(); } catch (e) { toast.error(formatApiError(e)); }
  };
  const csv = () => {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([toCsv(rep)], { type: "text/csv;charset=utf-8" })); a.download = `sla-${month}.csv`; a.click();
  };
  const toggleMonthly = async (v) => { try { setCfg((await api.put("/sla/settings", { monthly_alert: v })).data); } catch (e) { toast.error(formatApiError(e)); } };
  const items = rep?.items || [];
  const met = items.filter(x => x.result.met).length;
  return (
    <div className="p-4 md:p-6 flex-1 overflow-y-auto" data-testid="sla-page">
      <div className="flex items-start justify-between flex-wrap gap-3 mb-4">
        <div>
          <h1 className="font-heading text-2xl sm:text-[1.75rem] font-semibold tracking-tight text-slate-100 flex items-center gap-2"><Gauge className="w-6 h-6 text-brand-soft" /> SLA e disponibilidade</h1>
          <p className="text-sm text-slate-400 mt-1">Disponibilidade do mês por enlace, POP ou cliente — a partir das interfaces monitoradas, dos equipamentos e das sondas.</p>
        </div>
        <div className="flex flex-wrap gap-2 items-center">
          <Input type="month" value={month} onChange={e => e.target.value && setSp({ month: e.target.value })} className="bg-sunken border-line h-9 w-40" data-testid="sla-month" />
          <Button size="sm" variant="ghost" onClick={csv} disabled={!items.length} className="text-slate-300" data-testid="sla-csv"><Download className="w-4 h-4 mr-1" /> CSV</Button>
          <Link to={`/sla/report?month=${month}`} target="_blank" className="inline-flex items-center h-9 px-3 rounded-md text-sm text-slate-200 hover:bg-white/5" data-testid="sla-report"><FileText className="w-4 h-4 mr-1" /> Relatório PDF</Link>
          <Button size="sm" onClick={() => setEdit({})} className="bg-brand hover:bg-brand-strong text-onbrand" data-testid="sla-new"><Plus className="w-4 h-4 mr-1" /> Novo item</Button>
        </div>
      </div>
      {items.length > 0 && <div className="flex flex-wrap gap-3 mb-3 text-sm text-slate-300">
        <span>{monthLabel(month)}{items[0]?.result.partial ? " (até agora)" : ""}</span>
        <span className="text-emerald-300">{met} cumpriram a meta</span>{items.length - met > 0 && <span className="text-red-300">{items.length - met} abaixo da meta</span>}
        {isAdmin && cfg && <label className="flex items-center gap-2 text-xs text-slate-400 ml-auto"><Switch checked={!!cfg.monthly_alert} onCheckedChange={toggleMonthly} /> mandar o resumo do mês no Telegram (dia 1, 8h)</label>}
      </div>}
      {!rep ? <div className="py-10 text-center"><Loader2 className="w-5 h-5 animate-spin inline text-slate-500" /></div> : items.length === 0 ? (
        <Card className="bg-surface border-line p-8 text-center text-sm text-slate-400">Nenhum item de SLA. Crie um juntando a interface do enlace, o equipamento do POP ou as sondas do serviço.</Card>
      ) : (
        <Card className="bg-surface border-line overflow-hidden divide-y divide-line" data-testid="sla-list">
          {items.map(({ item: it, result: r }) => (
            <div key={it.id} data-testid={`sla-item-${it.id}`}>
              <div className="flex flex-wrap items-center gap-3 px-4 py-3">
                <button onClick={() => setOpen(o => ({ ...o, [it.id]: !o[it.id] }))} className="flex items-center gap-2 min-w-[14rem] flex-1 text-left">
                  {open[it.id] ? <ChevronDown className="w-4 h-4 text-slate-500" /> : <ChevronRight className="w-4 h-4 text-slate-500" />}
                  <span>
                    <span className="block text-slate-100">{it.name}</span>
                    <span className="block text-xs text-slate-500">{KINDS[it.kind]}{it.customer ? ` · ${it.customer}` : ""} · {it.components.length} componente(s)</span>
                  </span>
                </button>
                <div className="w-40"><Bar value={r.availability} target={it.target} /><div className="text-[10px] text-slate-500 mt-0.5">meta {fmtPct(it.target, 2)}</div></div>
                <div className={`w-24 text-right font-mono text-base ${r.met ? "text-emerald-300" : "text-red-300"}`}>{fmtPct(r.availability)}</div>
                <div className="w-32 text-right text-xs text-slate-400">{fmtDur(r.downtime_s)} fora<br />{r.outages} queda(s)</div>
                <div className="flex gap-1">
                  <Button size="sm" variant="ghost" onClick={() => setEdit(it)} className="h-8 text-slate-300" title="Editar"><Pencil className="w-4 h-4" /></Button>
                  <Button size="sm" variant="ghost" onClick={() => del(it)} className="h-8 text-red-300" title="Apagar"><Trash2 className="w-4 h-4" /></Button>
                </div>
              </div>
              {open[it.id] && <ItemDetail r={r} />}
            </div>
          ))}
        </Card>
      )}
      {edit && <ItemDialog item={edit.id ? edit : null} onClose={() => setEdit(null)} onSaved={() => { setEdit(null); load(); }} />}
    </div>
  );
}

// ------------------------------------------------------------------ relatório para imprimir / salvar em PDF
export function SlaReport() {
  const [sp] = useSearchParams();
  const month = sp.get("month") || thisMonth();
  const [rep, setRep] = useState(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    api.get("/sla/report", { params: { month, ids: sp.get("ids") || "" } }).then(r => setRep(r.data)).catch(e => setErr(formatApiError(e)));
  }, [month, sp]);
  const groups = useMemo(() => {
    const m = new Map();
    for (const x of rep?.items || []) { const k = KINDS[x.item.kind] || x.item.kind; if (!m.has(k)) m.set(k, []); m.get(k).push(x); }
    return [...m.entries()];
  }, [rep]);
  useEffect(() => { document.title = `SLA ${month}`; }, [month]);
  if (err) return <div className="p-8 text-red-600">{err}</div>;
  if (!rep) return <div className="p-8 text-[#64748b]">Gerando…</div>;
  const items = rep.items;
  return (
    <div className="sla-print min-h-screen bg-[#ffffff] text-[#0f172a] p-8 print:p-0" data-testid="sla-print">
      <style>{`@media print { @page { size: A4; margin: 14mm; } .no-print { display: none !important; } .sla-print { padding: 0 } .brk { break-inside: avoid; } }
        .sla-print table { border-collapse: collapse; width: 100%; } .sla-print th, .sla-print td { border-bottom: 1px solid #e2e8f0; padding: 4px 6px; font-size: 12px; }
        .sla-print th { text-align: left; color: #475569; font-weight: 600; background: #f8fafc; }`}</style>
      <div className="no-print flex gap-2 mb-6">
        <button onClick={() => window.print()} className="inline-flex items-center gap-1.5 h-9 px-4 rounded-md bg-[#0f172a] text-[#ffffff] text-sm" data-testid="sla-print-btn"><Printer className="w-4 h-4" /> Imprimir / salvar em PDF</button>
        <span className="text-xs text-[#64748b] self-center">No diálogo de impressão escolha "Salvar como PDF".</span>
      </div>
      <header className="flex items-end justify-between border-b-2 border-[#0f172a] pb-3 mb-5">
        <div>
          <div className="text-xs uppercase tracking-widest text-[#64748b]">Relatório de disponibilidade</div>
          <h1 className="text-2xl font-bold">{monthLabel(month)}</h1>
        </div>
        <div className="text-right text-xs text-[#64748b]">BastiON · Central do NOC<br />gerado em {new Date(rep.generated_at).toLocaleString("pt-BR")}{items[0]?.result.partial ? <><br /><b>mês em andamento</b></> : ""}</div>
      </header>
      <section className="mb-6 brk">
        <h2 className="font-semibold mb-2">Resumo</h2>
        <table>
          <thead><tr><th>Item</th><th>Tipo</th><th>Cliente</th><th style={{ textAlign: "right" }}>Meta</th><th style={{ textAlign: "right" }}>Disponibilidade</th><th style={{ textAlign: "right" }}>Fora</th><th style={{ textAlign: "right" }}>Quedas</th><th style={{ textAlign: "right" }}>MTTR</th><th>Situação</th></tr></thead>
          <tbody>
            {items.map(({ item: it, result: r }) => (
              <tr key={it.id}><td>{it.name}</td><td>{KINDS[it.kind]}</td><td>{it.customer || "—"}</td><td style={{ textAlign: "right" }}>{fmtPct(it.target, 2)}</td>
                <td style={{ textAlign: "right", fontWeight: 700, color: r.met ? "#047857" : "#b91c1c" }}>{fmtPct(r.availability)}</td><td style={{ textAlign: "right" }}>{fmtDur(r.downtime_s)}</td>
                <td style={{ textAlign: "right" }}>{r.outages}</td><td style={{ textAlign: "right" }}>{r.outages ? fmtDur(r.mttr_s) : "—"}</td>
                <td style={{ color: r.met ? "#047857" : "#b91c1c" }}>{r.met ? "Cumpriu" : "Abaixo da meta"}</td></tr>
            ))}
          </tbody>
        </table>
      </section>
      {groups.map(([g, list]) => (
        <section key={g} className="mb-4">
          <h2 className="font-semibold mb-2">{g}</h2>
          {list.map(({ item: it, result: r }) => (
            <div key={it.id} className="brk border border-[#e2e8f0] rounded p-3 mb-3">
              <div className="flex justify-between items-baseline mb-1">
                <div className="font-semibold">{it.name}{it.customer && <span className="font-normal text-[#64748b]"> · {it.customer}</span>}</div>
                <div className="text-lg font-bold" style={{ color: r.met ? "#047857" : "#b91c1c" }}>{fmtPct(r.availability)}</div>
              </div>
              <div className="text-xs text-[#475569] mb-2">Meta {fmtPct(it.target, 2)} (permite {fmtDur(r.allowed_s)} no mês) · fora {fmtDur(r.downtime_s)} · {r.outages} queda(s)
                {r.outages ? ` · maior ${fmtDur(r.longest_s)} · MTTR ${fmtDur(r.mttr_s)}` : ""}{r.maint_s ? ` · manutenção programada ${fmtDur(r.maint_s)} (não conta)` : ""}{r.coverage < 99 ? ` · medido ${r.coverage}% do período` : ""}</div>
              <table className="mb-2"><thead><tr><th>Componente</th><th style={{ textAlign: "right" }}>Disponibilidade</th><th style={{ textAlign: "right" }}>Fora</th><th style={{ textAlign: "right" }}>Quedas</th></tr></thead>
                <tbody>{r.components.map((c, i) => <tr key={i}><td>{c.label}</td><td style={{ textAlign: "right" }}>{fmtPct(c.availability)}</td><td style={{ textAlign: "right" }}>{fmtDur(c.downtime_s)}</td><td style={{ textAlign: "right" }}>{c.outages}</td></tr>)}</tbody></table>
              {r.quality?.map((q, i) => <div key={i} className="text-xs text-[#475569]">{q.label}: latência média {q.ms_avg != null ? `${q.ms_avg} ms` : "—"}{q.loss != null ? ` · perda ${fmtPct(q.loss, 2)}` : ""}</div>)}
              {r.outage_list.length > 0 && <div className="text-xs text-[#475569] mt-1"><b>Quedas:</b> {r.outage_list.slice(0, 30).map(o => `${when(o.start)} (${fmtDur(o.seconds)})`).join(" · ")}{r.outage_list.length > 30 ? " …" : ""}</div>}
            </div>
          ))}
        </section>
      ))}
      <footer className="text-[10px] text-[#94a3b8] mt-6">Disponibilidade = 1 − tempo indisponível ÷ tempo medido. O item fica indisponível quando qualquer componente está fora; janelas de manutenção programada não contam. Quedas abaixo de 1 minuto entram no tempo mas não na contagem.</footer>
    </div>
  );
}
