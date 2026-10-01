import React, { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, formatApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { toast } from "sonner";
import { Archive, Play, Loader2, Eye, GitCompare, CheckCircle2, XCircle, Download, Server, HardDrive, FileSearch, Search, ChevronDown, ChevronRight } from "lucide-react";
import { BackupsManager } from "@/components/BackupsManager";
import { ConfigSearch } from "@/components/ConfigSearch";

function NumberedConfig({ content, focus }) {
  const ref = useRef(null);
  useEffect(() => { ref.current?.scrollIntoView({ block: "center" }); }, [focus, content]);
  return (
    <div className="bg-sunken border border-line rounded p-3 text-xs font-mono text-slate-300 max-h-[65vh] overflow-auto" data-testid="backup-content">
      {(content || "").split("\n").map((l, i) => (
        <div key={i} ref={i + 1 === focus ? ref : null} className={`flex gap-3 ${i + 1 === focus ? "bg-amber-400/15 text-amber-100" : ""}`}>
          <span className="w-12 shrink-0 text-right text-slate-600 select-none">{i + 1}</span>
          <span className="whitespace-pre">{l || " "}</span>
        </div>
      ))}
    </div>
  );
}

const fmt = (iso) => iso ? new Date(iso).toLocaleString("pt-BR") : "—";

function DiffView({ diff }) {
  if (!diff) return <div className="text-sm text-slate-500 font-mono p-4">Sem diferenças entre as duas versões.</div>;
  return (
    <pre className="bg-sunken border border-line rounded p-3 text-xs font-mono max-h-[60vh] overflow-auto" data-testid="backup-diff">
      {diff.split("\n").map((l, i) => (
        <div key={i} className={l.startsWith("+") && !l.startsWith("+++") ? "text-emerald-400 bg-emerald-500/5" :
                                l.startsWith("-") && !l.startsWith("---") ? "text-red-400 bg-red-500/5" :
                                l.startsWith("@@") ? "text-brand-soft" : "text-slate-400"}>{l || " "}</div>
      ))}
    </pre>
  );
}

export default function Backups() {
  const [summary, setSummary] = useState([]);
  const [selected, setSelected] = useState(null);
  const [versions, setVersions] = useState([]);
  const [running, setRunning] = useState(false);
  const [viewing, setViewing] = useState(null);
  const [diffSel, setDiffSel] = useState([]);
  const [diff, setDiff] = useState(null);
  const [params] = useSearchParams();
  const [tab, setTab] = useState(params.get("tab") || "devices");
  const wantDevice = useRef(params.get("device"));
  const wantDiff = useRef(params.get("diff"));

  const [tagsById, setTagsById] = useState({});          // device_id -> tags (vêm do cadastro do equipamento)
  const [tag, setTag] = useState("");                    // "" = todas | nome da tag | "__none__" = sem tag
  const [q, setQ] = useState("");
  const [onlyFail, setOnlyFail] = useState(false);
  const [closed, setClosed] = useState(() => new Set()); // grupos recolhidos

  const loadSummary = async () => {
    const [s, d] = await Promise.all([api.get("/backups/summary"), api.get("/devices").catch(() => ({ data: [] }))]);
    setSummary(s.data);
    setTagsById(Object.fromEntries((d.data || []).map(x => [x.id, x.tags || []])));
  };
  const loadVersions = async (deviceId) => setVersions((await api.get("/backups", { params: { device_id: deviceId } })).data);
  useEffect(() => { loadSummary(); }, []);
  useEffect(() => { if (selected) { loadVersions(selected.device_id); setDiffSel([]); setDiff(null); } }, [selected]);
  // link do alerta de config alterada: /backups?device=ID&diff=BACKUP_ID abre o diff com a versão anterior
  useEffect(() => {
    if (!wantDevice.current || !summary.length) return;
    const s = summary.find(x => x.device_id === wantDevice.current);
    wantDevice.current = null;
    if (s) setSelected(s);
  }, [summary]);
  useEffect(() => {
    const id = wantDiff.current;
    if (!id || !versions.length) return;
    const v = versions.find(x => x.id === id);
    wantDiff.current = null;
    if (!v?.prev_id) return;
    setDiffSel([v.prev_id, v.id]);
    api.get(`/backups/${v.id}/diff/${v.prev_id}`).then(({ data }) => setDiff(data)).catch(() => {});
  }, [versions]);

  const runAll = async (deviceIds) => {
    setRunning(true);
    toast.info(deviceIds ? "Executando backup…" : "Executando backup de todos os equipamentos elegíveis…");
    try {
      const { data } = await api.post("/backups/run", { device_ids: deviceIds || null });
      const ok = data.results.filter(r => r.ok).length;
      const changed = data.results.filter(r => r.ok && r.changed).length;
      const failed = data.results.filter(r => !r.ok);
      (failed.length ? toast.warning : toast.success)(`${ok} backup(s) OK · ${changed} alterado(s) · ${failed.length} falha(s)`);
      await loadSummary();
      if (selected) await loadVersions(selected.device_id);
    } catch (e) { toast.error(formatApiError(e)); }
    finally { setRunning(false); }
  };

  const view = async (b, line) => {
    try { setViewing({ ...(await api.get(`/backups/${b.id}`)).data, focusLine: line || null }); }
    catch (e) { toast.error(formatApiError(e)); }
  };
  const toggleDiff = (id) => {
    setDiffSel(prev => prev.includes(id) ? prev.filter(x => x !== id) : [...prev.slice(-1), id]);
    setDiff(null);
  };
  const showDiff = async () => {
    if (diffSel.length !== 2) return;
    const [older, newer] = [...diffSel].sort((a, b) => versions.findIndex(v => v.id === b) - versions.findIndex(v => v.id === a));
    const { data } = await api.get(`/backups/${newer}/diff/${older}`);
    setDiff(data);
  };
  const download = (b) => {
    const blob = new Blob([b.content], { type: "text/plain;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = `${b.device_name}-${b.created_at.slice(0, 19).replace(/[:T]/g, "-")}.cfg`; a.click();
    URL.revokeObjectURL(a.href);
  };

  // organização por tag: filtro (chips), busca e grupos
  const { allTags, countByTag, failTotal } = useMemo(() => {
    const c = {}; let fail = 0;
    for (const s of summary) {
      const tg = tagsById[s.device_id] || [];
      if (!tg.length) c.__none__ = (c.__none__ || 0) + 1;
      for (const t of tg) c[t] = (c[t] || 0) + 1;
      if (!s.last_ok) fail += 1;
    }
    return { allTags: Object.keys(c).filter(t => t !== "__none__").sort((a, b) => a.localeCompare(b, "pt-BR", { sensitivity: "base" })), countByTag: c, failTotal: fail };
  }, [summary, tagsById]);
  const shown = useMemo(() => {
    const t = q.trim().toLowerCase();
    return summary.filter(s => {
      const tg = tagsById[s.device_id] || [];
      if (tag === "__none__" ? tg.length : tag && !tg.includes(tag)) return false;
      if (onlyFail && s.last_ok) return false;
      return !t || s.device_name.toLowerCase().includes(t) || tg.some(x => x.toLowerCase().includes(t));
    }).sort((a, b) => a.device_name.localeCompare(b.device_name, "pt-BR", { numeric: true, sensitivity: "base" }));
  }, [summary, tagsById, tag, q, onlyFail]);
  const groups = useMemo(() => {
    if (tag) return [[tag, shown]];
    const m = new Map();
    for (const s of shown) {                            // em "Todas", cada equipamento fica no grupo da primeira tag dele
      const g = (tagsById[s.device_id] || [])[0] || "__none__";
      if (!m.has(g)) m.set(g, []);
      m.get(g).push(s);
    }
    return [...m.entries()].sort((a, b) => (a[0] === "__none__") - (b[0] === "__none__") || a[0].localeCompare(b[0], "pt-BR", { sensitivity: "base" }));
  }, [shown, tag, tagsById]);

  return (
    <div className="p-4 md:p-6 flex-1 overflow-y-auto" data-testid="backups-page">
      <div className="flex items-start justify-between mb-6 gap-4">
        <div>
          <h1 className="font-heading text-2xl sm:text-[1.75rem] font-semibold tracking-tight text-slate-100 mt-1">Backups de Configuração</h1>
          <p className="text-slate-400 mt-2 text-sm max-w-2xl">Coleta automática diária (running-config, export etc.) por tipo de equipamento. Compare versões e baixe qualquer snapshot.</p>
        </div>
        <Button onClick={() => runAll(null)} disabled={running} data-testid="run-all-backups-btn" className="bg-brand hover:bg-brand-strong">
          {running ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <Play className="w-4 h-4 mr-2" />} Backup agora (todos)
        </Button>
      </div>

      <div className="flex gap-1 mb-4 border-b border-line" data-testid="backups-tabs">
        {[["devices", "Por equipamento", Server], ["search", "Buscar nas configs", FileSearch], ["manage", "Todos os backups / limpeza", HardDrive]].map(([v, l, Icon]) => (
          <button key={v} onClick={() => setTab(v)} data-testid={`backups-tab-${v}`}
            className={`flex items-center gap-2 px-4 py-2 text-sm -mb-px border-b-2 ${tab === v ? "border-brand text-slate-100" : "border-transparent text-slate-400 hover:text-slate-200"}`}>
            <Icon className="w-4 h-4" /> {l}
          </button>
        ))}
      </div>

      {tab === "search" && <ConfigSearch initialQuery={params.get("q") || ""} onView={(id, line) => view({ id }, line)} />}

      {tab === "manage" && (
        <BackupsManager onView={view} onChanged={async () => { await loadSummary(); if (selected) await loadVersions(selected.device_id); }} />
      )}

      {tab === "devices" && (
      <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">
        <Card className="bg-surface border-line lg:col-span-2 overflow-hidden">
          <div className="px-3 py-2.5 border-b border-line space-y-2">
            <div className="relative">
              <Search className="w-3.5 h-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-500" />
              <input value={q} onChange={e => setQ(e.target.value)} placeholder="Buscar equipamento…" data-testid="backup-search"
                     className="w-full h-8 pl-8 pr-2 rounded-md bg-sunken border border-line text-sm text-slate-100 focus:outline-none focus:border-brand" />
            </div>
            <div className="flex flex-wrap gap-1.5" data-testid="backup-tags">
              {[["", "Todas", summary.length], ...allTags.map(t => [t, t, countByTag[t]]), ...(countByTag.__none__ ? [["__none__", "Sem tag", countByTag.__none__]] : [])].map(([v, l, n]) => (
                <button key={v || "all"} onClick={() => setTag(v)} data-testid={`backup-tag-${v || "all"}`}
                        className={`px-2 py-0.5 rounded-md text-xs border ${tag === v ? "bg-brand border-brand text-white" : "bg-surface border-line text-slate-300 hover:border-line2"}`}>
                  {l} <span className={tag === v ? "text-white/70" : "text-slate-500"}>{n}</span>
                </button>
              ))}
              {failTotal > 0 && (
                <button onClick={() => setOnlyFail(!onlyFail)} data-testid="backup-only-fail" aria-pressed={onlyFail}
                        className={`px-2 py-0.5 rounded-md text-xs border ${onlyFail ? "bg-red-500/20 border-red-400/50 text-red-200" : "border-line text-red-300 hover:border-red-400/40"}`}>
                  com falha {failTotal}
                </button>
              )}
            </div>
            {tag && tag !== "__none__" && (
              <button onClick={() => runAll(shown.map(x => x.device_id))} disabled={running || !shown.length} data-testid="backup-run-tag"
                      className="text-xs text-brand-soft hover:underline disabled:opacity-50">Fazer backup agora dos {shown.length} de {tag}</button>
            )}
          </div>
          {summary.length === 0 && <div className="p-6 text-sm text-slate-500">Nenhum backup ainda. Clique em "Backup agora".</div>}
          {summary.length > 0 && shown.length === 0 && <div className="p-6 text-sm text-slate-500" data-testid="backup-empty">Nenhum equipamento com esse filtro.</div>}
          <div className="max-h-[66vh] overflow-y-auto">
            {groups.map(([g, list]) => {
              const isClosed = closed.has(g) && !q;
              const bad = list.filter(x => !x.last_ok).length;
              return (
                <div key={g} data-testid={`backup-group-${g}`}>
                  {groups.length > 1 && (
                    <button onClick={() => setClosed(prev => { const n = new Set(prev); n.has(g) ? n.delete(g) : n.add(g); return n; })}
                            className="sticky top-0 z-10 w-full flex items-center gap-1.5 px-3 py-1.5 bg-panel border-y border-line text-xs text-slate-300 hover:text-slate-100" aria-expanded={!isClosed}>
                      {isClosed ? <ChevronRight className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
                      <span className="font-medium">{g === "__none__" ? "Sem tag" : g}</span>
                      <span className="text-slate-500">{list.length}</span>
                      {bad > 0 && <span className="ml-auto text-red-300">{bad} com falha</span>}
                    </button>
                  )}
                  {!isClosed && (
                    <div className="divide-y divide-line">
                      {list.map(s => (
                        <button key={s.device_id} onClick={() => setSelected(s)} data-testid={`backup-device-${s.device_id}`}
                                className={`w-full text-left px-4 py-2.5 hover:bg-white/[0.03] ${selected?.device_id === s.device_id ? "bg-panel" : ""}`}>
                          <div className="flex items-center justify-between gap-2">
                            <div className="text-sm text-slate-100 font-medium flex items-center gap-2 min-w-0">
                              {s.last_ok ? <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400 shrink-0" /> : <XCircle className="w-3.5 h-3.5 text-red-400 shrink-0" />}
                              <span className="truncate">{s.device_name}</span>
                            </div>
                            <span className="text-[10px] font-mono text-slate-500 shrink-0">{s.ok_count}/{s.count} ok</span>
                          </div>
                          <div className="text-[11px] text-slate-500 mt-0.5 flex flex-wrap items-center gap-x-2">
                            <span className="font-mono">{s.device_type} · {fmt(s.last_at)}</span>
                            {(tagsById[s.device_id] || []).filter(t => t !== g).map(t => <span key={t} className="px-1 rounded border border-line text-slate-400">{t}</span>)}
                          </div>
                          {!s.last_ok && s.last_error && <div className="text-[11px] font-mono text-red-400 mt-0.5 truncate">{s.last_error}</div>}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </Card>

        <Card className="bg-surface border-line lg:col-span-3 overflow-hidden">
          <div className="px-4 py-3 border-b border-line flex items-center justify-between">
            <div className="text-xs text-slate-400">{selected ? `Versões — ${selected.device_name}` : "Versões"}</div>
            {selected && (
              <div className="flex gap-2">
                <Button size="sm" variant="outline" onClick={showDiff} disabled={diffSel.length !== 2} data-testid="compare-btn"
                        className="border-line bg-panel text-slate-200 hover:bg-slate-800 h-7 text-xs">
                  <GitCompare className="w-3.5 h-3.5 mr-1" /> Comparar ({diffSel.length}/2)
                </Button>
                <Button size="sm" onClick={() => runAll([selected.device_id])} disabled={running} data-testid="run-device-backup-btn" className="bg-brand hover:bg-brand-strong h-7 text-xs">
                  <Archive className="w-3.5 h-3.5 mr-1" /> Backup agora
                </Button>
              </div>
            )}
          </div>
          {!selected && <div className="p-6 text-sm text-slate-500 font-mono">Selecione um equipamento à esquerda.</div>}
          {selected && (
            <div className="divide-y divide-line max-h-[40vh] overflow-y-auto">
              {versions.map(v => (
                <div key={v.id} className="px-4 py-2.5 flex items-center gap-3 hover:bg-slate-900/40" data-testid={`backup-version-${v.id}`}>
                  <input type="checkbox" disabled={!v.ok} checked={diffSel.includes(v.id)} onChange={() => toggleDiff(v.id)} data-testid={`diff-check-${v.id}`} />
                  <div className="flex-1 min-w-0">
                    <div className="text-sm font-mono text-slate-200">{fmt(v.created_at)}</div>
                    <div className="text-[11px] font-mono text-slate-500">
                      {v.ok ? `${v.lines} linhas · ${(v.size / 1024).toFixed(1)} KB` : <span className="text-red-400">{v.error}</span>}
                      {v.ok && v.changed && !v.first && <span className="ml-2 text-amber-400">alterado{v.diff_stats ? ` (+${v.diff_stats.added} −${v.diff_stats.removed})` : ""}</span>}
                      {v.ok && v.first && <span className="ml-2 text-slate-400">primeiro backup</span>}
                    </div>
                  </div>
                  {v.ok && (
                    <Button size="sm" variant="ghost" onClick={() => view(v)} data-testid={`view-backup-${v.id}`} className="h-7 text-slate-300 hover:bg-slate-800">
                      <Eye className="w-3.5 h-3.5" />
                    </Button>
                  )}
                </div>
              ))}
            </div>
          )}
          {diff && <div className="p-3 border-t border-line"><DiffView diff={diff.identical ? "" : diff.diff} /></div>}
        </Card>
      </div>
      )}

      <Dialog open={!!viewing} onOpenChange={(v) => !v && setViewing(null)}>
        <DialogContent className="bg-surface border-line text-slate-100 max-w-4xl" data-testid="backup-view-dialog">
          <DialogHeader><DialogTitle>{viewing?.device_name} — {fmt(viewing?.created_at)}</DialogTitle></DialogHeader>
          <div className="flex justify-end">
            <Button size="sm" variant="outline" onClick={() => download(viewing)} data-testid="download-backup-btn" className="border-line bg-panel text-slate-200 hover:bg-slate-800 h-7 text-xs">
              <Download className="w-3.5 h-3.5 mr-1" /> Baixar
            </Button>
          </div>
          {viewing?.focusLine
            ? <NumberedConfig content={viewing.content} focus={viewing.focusLine} />
            : <pre className="bg-sunken border border-line rounded p-3 text-xs font-mono text-slate-300 max-h-[65vh] overflow-auto whitespace-pre-wrap" data-testid="backup-content">{viewing?.content}</pre>}
        </DialogContent>
      </Dialog>
    </div>
  );
}
