import React, { useEffect, useState } from "react";
import { api, formatApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { toast } from "sonner";
import { Archive, Play, Loader2, Eye, GitCompare, CheckCircle2, XCircle, Download } from "lucide-react";

const fmt = (iso) => iso ? new Date(iso).toLocaleString("pt-BR") : "—";

function DiffView({ diff }) {
  if (!diff) return <div className="text-sm text-slate-500 font-mono p-4">Sem diferenças entre as duas versões.</div>;
  return (
    <pre className="bg-[#05070A] border border-[#1E293B] rounded p-3 text-xs font-mono max-h-[60vh] overflow-auto" data-testid="backup-diff">
      {diff.split("\n").map((l, i) => (
        <div key={i} className={l.startsWith("+") && !l.startsWith("+++") ? "text-emerald-400 bg-emerald-500/5" :
                                l.startsWith("-") && !l.startsWith("---") ? "text-red-400 bg-red-500/5" :
                                l.startsWith("@@") ? "text-[#4DA3FF]" : "text-slate-400"}>{l || " "}</div>
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

  const loadSummary = async () => setSummary((await api.get("/backups/summary")).data);
  const loadVersions = async (deviceId) => setVersions((await api.get("/backups", { params: { device_id: deviceId } })).data);
  useEffect(() => { loadSummary(); }, []);
  useEffect(() => { if (selected) { loadVersions(selected.device_id); setDiffSel([]); setDiff(null); } }, [selected]);

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

  const view = async (b) => setViewing((await api.get(`/backups/${b.id}`)).data);
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

  return (
    <div className="p-8 flex-1 overflow-y-auto" data-testid="backups-page">
      <div className="flex items-start justify-between mb-6 gap-4">
        <div>
          <div className="text-xs uppercase tracking-widest text-slate-400 font-mono">Configurações versionadas</div>
          <h1 className="font-heading text-3xl sm:text-4xl font-bold text-slate-100 mt-1">Backups de Configuração</h1>
          <p className="text-slate-400 mt-2 text-sm max-w-2xl">Coleta automática diária (running-config, export etc.) por tipo de equipamento. Compare versões e baixe qualquer snapshot.</p>
        </div>
        <Button onClick={() => runAll(null)} disabled={running} data-testid="run-all-backups-btn" className="bg-[#007AFF] hover:bg-[#0062CC]">
          {running ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <Play className="w-4 h-4 mr-2" />} Backup agora (todos)
        </Button>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">
        <Card className="bg-[#111722] border-[#1E293B] lg:col-span-2 overflow-hidden">
          <div className="px-4 py-3 border-b border-[#1E293B] text-xs uppercase tracking-widest text-slate-400 font-mono">Equipamentos</div>
          {summary.length === 0 && <div className="p-6 text-sm text-slate-500 font-mono">Nenhum backup ainda. Clique em "Backup agora".</div>}
          <div className="divide-y divide-[#1E293B] max-h-[70vh] overflow-y-auto">
            {summary.map(s => (
              <button key={s.device_id} onClick={() => setSelected(s)} data-testid={`backup-device-${s.device_id}`}
                      className={`w-full text-left px-4 py-3 hover:bg-slate-900/40 ${selected?.device_id === s.device_id ? "bg-[#0B111C]" : ""}`}>
                <div className="flex items-center justify-between">
                  <div className="text-sm text-slate-100 font-medium flex items-center gap-2">
                    {s.last_ok ? <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" /> : <XCircle className="w-3.5 h-3.5 text-red-400" />}
                    {s.device_name}
                  </div>
                  <span className="text-[10px] font-mono text-slate-500">{s.ok_count}/{s.count} ok</span>
                </div>
                <div className="text-[11px] font-mono text-slate-500 mt-0.5">{s.device_type} · último: {fmt(s.last_at)}</div>
                {!s.last_ok && s.last_error && <div className="text-[11px] font-mono text-red-400 mt-0.5 truncate">{s.last_error}</div>}
              </button>
            ))}
          </div>
        </Card>

        <Card className="bg-[#111722] border-[#1E293B] lg:col-span-3 overflow-hidden">
          <div className="px-4 py-3 border-b border-[#1E293B] flex items-center justify-between">
            <div className="text-xs uppercase tracking-widest text-slate-400 font-mono">{selected ? `Versões — ${selected.device_name}` : "Versões"}</div>
            {selected && (
              <div className="flex gap-2">
                <Button size="sm" variant="outline" onClick={showDiff} disabled={diffSel.length !== 2} data-testid="compare-btn"
                        className="border-[#1E293B] bg-[#0B111C] text-slate-200 hover:bg-slate-800 h-7 text-xs">
                  <GitCompare className="w-3.5 h-3.5 mr-1" /> Comparar ({diffSel.length}/2)
                </Button>
                <Button size="sm" onClick={() => runAll([selected.device_id])} disabled={running} data-testid="run-device-backup-btn" className="bg-[#007AFF] hover:bg-[#0062CC] h-7 text-xs">
                  <Archive className="w-3.5 h-3.5 mr-1" /> Backup agora
                </Button>
              </div>
            )}
          </div>
          {!selected && <div className="p-6 text-sm text-slate-500 font-mono">Selecione um equipamento à esquerda.</div>}
          {selected && (
            <div className="divide-y divide-[#1E293B] max-h-[40vh] overflow-y-auto">
              {versions.map(v => (
                <div key={v.id} className="px-4 py-2.5 flex items-center gap-3 hover:bg-slate-900/40" data-testid={`backup-version-${v.id}`}>
                  <input type="checkbox" disabled={!v.ok} checked={diffSel.includes(v.id)} onChange={() => toggleDiff(v.id)} data-testid={`diff-check-${v.id}`} />
                  <div className="flex-1 min-w-0">
                    <div className="text-sm font-mono text-slate-200">{fmt(v.created_at)}</div>
                    <div className="text-[11px] font-mono text-slate-500">
                      {v.ok ? `${v.lines} linhas · ${(v.size / 1024).toFixed(1)} KB` : <span className="text-red-400">{v.error}</span>}
                      {v.ok && v.changed && <span className="ml-2 text-amber-400">alterado</span>}
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
          {diff && <div className="p-3 border-t border-[#1E293B]"><DiffView diff={diff.identical ? "" : diff.diff} /></div>}
        </Card>
      </div>

      <Dialog open={!!viewing} onOpenChange={(v) => !v && setViewing(null)}>
        <DialogContent className="bg-[#111722] border-[#1E293B] text-slate-100 max-w-4xl" data-testid="backup-view-dialog">
          <DialogHeader><DialogTitle>{viewing?.device_name} — {fmt(viewing?.created_at)}</DialogTitle></DialogHeader>
          <div className="flex justify-end">
            <Button size="sm" variant="outline" onClick={() => download(viewing)} data-testid="download-backup-btn" className="border-[#1E293B] bg-[#0B111C] text-slate-200 hover:bg-slate-800 h-7 text-xs">
              <Download className="w-3.5 h-3.5 mr-1" /> Baixar
            </Button>
          </div>
          <pre className="bg-[#05070A] border border-[#1E293B] rounded p-3 text-xs font-mono text-slate-300 max-h-[65vh] overflow-auto whitespace-pre-wrap" data-testid="backup-content">{viewing?.content}</pre>
        </DialogContent>
      </Dialog>
    </div>
  );
}
