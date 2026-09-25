import React, { useCallback, useEffect, useState } from "react";
import { api, formatApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Checkbox } from "@/components/ui/checkbox";
import { toast } from "sonner";
import { useAuth } from "@/context/AuthContext";
import { Search, Trash2, Eye, Loader2, Eraser, HardDrive, ChevronLeft, ChevronRight, CheckCircle2, XCircle, RefreshCw } from "lucide-react";

const fmt = (iso) => iso ? new Date(iso).toLocaleString("pt-BR") : "—";
export const fmtBytes = (b) => {
  if (!b) return "0 KB";
  const u = ["B", "KB", "MB", "GB"]; let i = 0; let n = b;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(n >= 100 || i === 0 ? 0 : 1)} ${u[i]}`;
};
const PAGE = 50;
const inputCls = "bg-[#05070A] border-[#1E293B] font-mono text-sm";

function Stat({ label, value, sub, tone = "text-slate-100" }) {
  return (
    <div className="bg-[#0B111C] border border-[#1E293B] rounded-md px-4 py-3">
      <div className="text-[10px] uppercase tracking-widest text-slate-500 font-mono">{label}</div>
      <div className={`text-2xl font-heading font-bold mt-0.5 ${tone}`}>{value}</div>
      {sub && <div className="text-[11px] font-mono text-slate-500 mt-0.5">{sub}</div>}
    </div>
  );
}

/** Aba "Gerenciar": todos os backups de todos os hosts, apagar selecionados e limpeza por regras. */
export function BackupsManager({ onView, onChanged }) {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const [stats, setStats] = useState(null);
  const [items, setItems] = useState([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("all");
  const [olderThan, setOlderThan] = useState("");
  const [selected, setSelected] = useState(() => new Set());
  const [loading, setLoading] = useState(false);
  // limpeza
  const [rules, setRules] = useState({ useAge: true, days: 90, unchanged: true, failed: true, keep: 3, orphans: false });
  const [preview, setPreview] = useState(null);
  const [cleaning, setCleaning] = useState(false);

  const loadStats = useCallback(async () => setStats((await api.get("/backups/storage")).data), []);
  const loadList = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await api.get("/backups/all", {
        params: { q, status, older_than_days: olderThan || undefined, skip: page * PAGE, limit: PAGE },
      });
      setItems(data.items); setTotal(data.total);
    } catch (e) { toast.error(formatApiError(e)); }
    finally { setLoading(false); }
  }, [q, status, olderThan, page]);

  useEffect(() => { loadStats().catch(() => {}); }, [loadStats]);
  useEffect(() => { const t = setTimeout(loadList, 250); return () => clearTimeout(t); }, [loadList]);
  useEffect(() => { setPage(0); }, [q, status, olderThan]);

  const refreshAll = async () => { setSelected(new Set()); setPreview(null); await Promise.all([loadStats(), loadList()]); onChanged?.(); };

  // ---- seleção ----
  const pageIds = items.map(i => i.id);
  const selOnPage = pageIds.filter(id => selected.has(id)).length;
  const headerState = pageIds.length && selOnPage === pageIds.length ? true : selOnPage ? "indeterminate" : false;
  const toggleAll = () => setSelected(prev => {
    const n = new Set(prev);
    if (headerState === true) pageIds.forEach(id => n.delete(id)); else pageIds.forEach(id => n.add(id));
    return n;
  });
  const toggle = (id) => setSelected(prev => { const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n; });
  const selBytes = items.filter(i => selected.has(i.id)).reduce((s, i) => s + (i.size || 0), 0);

  const deleteIds = async (ids, label) => {
    if (!window.confirm(`Apagar ${label}? Essa ação não pode ser desfeita.`)) return;
    try {
      const { data } = await api.post("/backups/delete", { ids });
      toast.success(`${data.deleted} backup(s) apagado(s) · ${fmtBytes(data.freed_bytes)} liberados`);
      await refreshAll();
    } catch (e) { toast.error(formatApiError(e)); }
  };

  // ---- limpeza por regras ----
  const cleanupPayload = (dry) => ({
    older_than_days: rules.useAge ? Number(rules.days) || null : null,
    delete_unchanged: rules.unchanged, delete_failed: rules.failed,
    keep_last: Math.max(1, Number(rules.keep) || 1), include_orphans: isAdmin && rules.orphans, dry_run: dry,
  });
  const noRule = !(rules.useAge || rules.unchanged || rules.failed || (isAdmin && rules.orphans));

  useEffect(() => {
    if (noRule) { setPreview(null); return; }
    const t = setTimeout(async () => {
      try { setPreview((await api.post("/backups/cleanup", cleanupPayload(true))).data); }
      catch { setPreview(null); }
    }, 350);
    return () => clearTimeout(t);
  }, [rules, stats]); // eslint-disable-line react-hooks/exhaustive-deps

  const runCleanup = async () => {
    if (!preview?.count) return;
    if (!window.confirm(`Apagar ${preview.count} backup(s) e liberar ${fmtBytes(preview.size_bytes)}? Essa ação não pode ser desfeita.`)) return;
    setCleaning(true);
    try {
      const { data } = await api.post("/backups/cleanup", cleanupPayload(false));
      toast.success(`Limpeza concluída: ${data.deleted} backup(s) apagado(s) · ${fmtBytes(data.size_bytes)} liberados`);
      await refreshAll();
    } catch (e) { toast.error(formatApiError(e)); }
    finally { setCleaning(false); }
  };

  const pages = Math.max(1, Math.ceil(total / PAGE));
  const chip = (v, l) => (
    <button key={v} onClick={() => setStatus(v)} data-testid={`bk-status-${v}`}
      className={`text-xs font-mono px-2.5 py-1 rounded border ${status === v ? "border-[#007AFF] bg-[#007AFF]/15 text-slate-100" : "border-[#1E293B] bg-[#111722] text-slate-400 hover:text-slate-200"}`}>{l}</button>
  );

  return (
    <div className="space-y-4" data-testid="backups-manager">
      {stats && (
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3" data-testid="bk-stats">
          <Stat label="Espaço usado" value={fmtBytes(stats.size_bytes)} sub={`${stats.count} backup(s) · ${stats.devices} host(s)`} tone="text-[#4DA3FF]" />
          <Stat label="Idênticos ao anterior" value={stats.unchanged} sub="versões sem nenhuma mudança" tone="text-amber-300" />
          <Stat label="Falhas guardadas" value={stats.failed} sub="tentativas que deram erro" tone={stats.failed ? "text-red-400" : "text-slate-100"} />
          <Stat label="Mais antigo" value={stats.oldest ? new Date(stats.oldest).toLocaleDateString("pt-BR") : "—"}
                sub={isAdmin && stats.orphans?.count ? `+ ${stats.orphans.count} de hosts excluídos (${fmtBytes(stats.orphans.size_bytes)})` : " "} />
        </div>
      )}

      <Card className="bg-[#111722] border-[#1E293B] p-4" data-testid="bk-cleanup">
        <div className="flex items-center gap-2 mb-3"><Eraser className="w-4 h-4 text-[#4DA3FF]" />
          <div className="text-xs uppercase tracking-widest text-slate-400 font-mono">Limpeza de backups antigos</div></div>
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-x-8 gap-y-2 text-sm text-slate-300">
          <label className="flex items-center gap-2 cursor-pointer">
            <input type="checkbox" checked={rules.useAge} onChange={e => setRules({ ...rules, useAge: e.target.checked })} data-testid="rule-age" />
            Apagar backups com mais de
            <Input type="number" min={1} value={rules.days} onChange={e => setRules({ ...rules, days: e.target.value })}
                   className={`${inputCls} w-20 h-7`} data-testid="rule-days" /> dias
          </label>
          <label className="flex items-center gap-2 cursor-pointer">
            <input type="checkbox" checked={rules.unchanged} onChange={e => setRules({ ...rules, unchanged: e.target.checked })} data-testid="rule-unchanged" />
            Apagar versões idênticas à anterior <span className="text-[11px] text-slate-500">(o histórico de mudanças continua)</span>
          </label>
          <label className="flex items-center gap-2 cursor-pointer">
            <input type="checkbox" checked={rules.failed} onChange={e => setRules({ ...rules, failed: e.target.checked })} data-testid="rule-failed" />
            Apagar tentativas com falha
          </label>
          {isAdmin && (
            <label className="flex items-center gap-2 cursor-pointer">
              <input type="checkbox" checked={rules.orphans} onChange={e => setRules({ ...rules, orphans: e.target.checked })} data-testid="rule-orphans" />
              Apagar backups de equipamentos já excluídos
              {stats?.orphans?.count ? <span className="text-[11px] text-slate-500">({stats.orphans.count})</span> : null}
            </label>
          )}
          <label className="flex items-center gap-2 lg:col-span-2 text-emerald-300/90">
            Sempre manter os
            <Input type="number" min={1} value={rules.keep} onChange={e => setRules({ ...rules, keep: e.target.value })}
                   className={`${inputCls} w-16 h-7`} data-testid="rule-keep" />
            backups mais recentes de cada equipamento (proteção — nunca são apagados)
          </label>
        </div>
        <div className="flex items-center justify-between gap-3 mt-4 flex-wrap border-t border-[#1E293B] pt-3">
          <div className="text-sm font-mono" data-testid="cleanup-preview">
            {noRule ? <span className="text-slate-500">Marque ao menos uma regra.</span>
              : !preview ? <span className="text-slate-500">calculando…</span>
              : preview.count === 0 ? <span className="text-slate-400">Nada para apagar com essas regras.</span>
              : <span className="text-slate-200">Vai apagar <b className="text-amber-300">{preview.count}</b> backup(s) de {preview.devices} host(s)
                  {preview.orphans ? ` + ${preview.orphans} de hosts excluídos` : ""} e liberar <b className="text-emerald-300">{fmtBytes(preview.size_bytes)}</b></span>}
          </div>
          <Button onClick={runCleanup} disabled={cleaning || !preview?.count} data-testid="cleanup-run-btn" className="bg-red-600 hover:bg-red-700">
            {cleaning ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <Eraser className="w-4 h-4 mr-2" />} Limpar agora
          </Button>
        </div>
      </Card>

      <Card className="bg-[#111722] border-[#1E293B] overflow-hidden" data-testid="bk-all">
        <div className="px-4 py-3 border-b border-[#1E293B] flex flex-wrap items-center gap-3">
          <div className="text-xs uppercase tracking-widest text-slate-400 font-mono flex items-center gap-2"><HardDrive className="w-4 h-4" /> Todos os backups</div>
          <div className="relative">
            <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
            <Input placeholder="Buscar host…" value={q} onChange={e => setQ(e.target.value)} className={`${inputCls} pl-9 w-56 h-8`} data-testid="bk-search" />
          </div>
          <div className="flex gap-1">{[["all", "Todos"], ["ok", "OK"], ["unchanged", "Idênticos"], ["failed", "Falhas"]].map(([v, l]) => chip(v, l))}</div>
          <label className="text-xs font-mono text-slate-400 flex items-center gap-1.5">
            mais antigos que
            <Input type="number" min={1} value={olderThan} onChange={e => setOlderThan(e.target.value)} placeholder="—" className={`${inputCls} w-16 h-8`} data-testid="bk-older" /> dias
          </label>
          <Button size="sm" variant="ghost" onClick={refreshAll} className="ml-auto h-8 text-slate-400 hover:bg-slate-800" title="Atualizar"><RefreshCw className="w-4 h-4" /></Button>
        </div>

        {selected.size > 0 && (
          <div className="flex items-center gap-3 px-4 py-2 bg-[#0B1A2E] border-b border-[#007AFF]/40 text-sm" data-testid="bk-selection-bar">
            <span className="font-mono text-slate-100">{selected.size} selecionado(s){selOnPage === selected.size ? ` · ${fmtBytes(selBytes)}` : ""}</span>
            <Button size="sm" variant="ghost" onClick={() => deleteIds([...selected], `${selected.size} backup(s) selecionado(s)`)}
                    className="text-red-400 hover:bg-red-950/40 h-7" data-testid="bk-delete-selected"><Trash2 className="w-4 h-4 mr-1.5" /> Apagar selecionados</Button>
            <Button size="sm" variant="ghost" onClick={() => setSelected(new Set())} className="text-slate-400 hover:bg-slate-800 h-7 ml-auto">Limpar seleção</Button>
          </div>
        )}

        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-xs uppercase tracking-widest text-slate-500 font-mono bg-[#0B111C]">
              <tr>
                <th className="pl-4 pr-1 py-2.5 w-8"><Checkbox checked={headerState} onCheckedChange={toggleAll} disabled={!pageIds.length} className="border-slate-500" data-testid="bk-select-all" /></th>
                <th className="text-left px-3 py-2.5">Data</th>
                <th className="text-left px-3 py-2.5">Equipamento</th>
                <th className="text-left px-3 py-2.5">Tipo</th>
                <th className="text-left px-3 py-2.5">Resultado</th>
                <th className="text-right px-3 py-2.5">Tamanho</th>
                <th className="text-right px-4 py-2.5">Ações</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[#1E293B]">
              {!loading && items.length === 0 && <tr><td colSpan={7} className="text-center py-8 text-slate-500 font-mono">Nenhum backup encontrado</td></tr>}
              {items.map(b => (
                <tr key={b.id} className={selected.has(b.id) ? "bg-[#007AFF]/10" : "hover:bg-slate-900/40"} data-testid={`bk-row-${b.id}`}>
                  <td className="pl-4 pr-1 py-2" onClick={() => toggle(b.id)}><Checkbox checked={selected.has(b.id)} className="border-slate-500" /></td>
                  <td className="px-3 py-2 font-mono text-slate-300 whitespace-nowrap">{fmt(b.created_at)}</td>
                  <td className="px-3 py-2 text-slate-100">{b.device_name}</td>
                  <td className="px-3 py-2 text-xs text-slate-400">{b.device_type}</td>
                  <td className="px-3 py-2 text-xs font-mono">
                    {b.ok ? <span className="inline-flex items-center gap-1 text-emerald-400"><CheckCircle2 className="w-3.5 h-3.5" /> ok</span>
                          : <span className="inline-flex items-center gap-1 text-red-400" title={b.error}><XCircle className="w-3.5 h-3.5" /> falha</span>}
                    {b.ok && (b.changed ? <span className="ml-2 text-amber-400">alterado</span> : <span className="ml-2 text-slate-500">idêntico</span>)}
                  </td>
                  <td className="px-3 py-2 text-right font-mono text-xs text-slate-400">{b.ok ? fmtBytes(b.size) : "—"}</td>
                  <td className="px-4 py-2">
                    <div className="flex justify-end gap-1">
                      {b.ok && <Button size="sm" variant="ghost" onClick={() => onView(b)} className="h-7 text-slate-300 hover:bg-slate-800" title="Ver"><Eye className="w-3.5 h-3.5" /></Button>}
                      <Button size="sm" variant="ghost" onClick={() => deleteIds([b.id], `o backup de ${b.device_name} de ${fmt(b.created_at)}`)}
                              className="h-7 text-red-400 hover:bg-red-950/40" title="Apagar" data-testid={`bk-delete-${b.id}`}><Trash2 className="w-3.5 h-3.5" /></Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="flex items-center justify-between px-4 py-2 border-t border-[#1E293B] text-xs font-mono text-slate-400">
          <span>{total} backup(s){loading && <Loader2 className="inline w-3 h-3 ml-2 animate-spin" />}</span>
          <div className="flex items-center gap-2">
            <Button size="sm" variant="ghost" disabled={page === 0} onClick={() => setPage(p => p - 1)} className="h-7"><ChevronLeft className="w-4 h-4" /></Button>
            <span data-testid="bk-page">página {page + 1} de {pages}</span>
            <Button size="sm" variant="ghost" disabled={page + 1 >= pages} onClick={() => setPage(p => p + 1)} className="h-7"><ChevronRight className="w-4 h-4" /></Button>
          </div>
        </div>
      </Card>
    </div>
  );
}
