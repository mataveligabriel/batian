import React, { useEffect, useMemo, useRef, useState } from "react";
import { api, formatApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { toast } from "sonner";
import { Loader2, Radar, Copy, Download, Plus, ArrowLeft, Square } from "lucide-react";

const STATUS = {
  ok: ["text-on", "identificado"], unknown: ["text-amber-300", "entrou, fabricante não reconhecido"], auth: ["text-red-400", "login recusado"],
  closed: ["text-slate-500", "porta fechada"], down: ["text-slate-500", "sem resposta"], error: ["text-amber-300", "erro"], skipped: ["text-slate-500", "cancelado"],
};
const VCOLOR = { huawei: "text-red-300 border-red-900/60", cisco: "text-sky-300 border-sky-900/60", datacom: "text-emerald-300 border-emerald-900/60", juniper: "text-lime-300 border-lime-900/60",
  zte: "text-cyan-300 border-cyan-900/60", mikrotik: "text-violet-300 border-violet-900/60" };
const sel = "h-9 rounded-md bg-sunken border border-line text-sm text-slate-200 px-2";
const groupOf = (r) => r.status === "ok" ? r.label : r.status === "unknown" ? "Não reconhecido" : r.status === "auth" ? "Login recusado" : "Sem acesso";

/** Lista de IPs + usuário/senha → entra em cada um (SSH/Telnet) e diz o fabricante. Depois dá para cadastrar os identificados. */
export function VendorScanDialog({ open, onOpenChange, agents = [], onDone }) {
  const [form, setForm] = useState({ targets: "", username: "", password: "", protocol: "auto", port: "", agent_id: "" });
  const [job, setJob] = useState(null);
  const [starting, setStarting] = useState(false);
  const [filter, setFilter] = useState("");
  const [picked, setPicked] = useState(new Set());
  const [tags, setTags] = useState("");
  const [saving, setSaving] = useState(false);
  const timer = useRef(null);
  const count = useMemo(() => form.targets.split("\n").filter(l => l.split("#")[0].trim()).length, [form.targets]);

  useEffect(() => () => clearTimeout(timer.current), []);
  useEffect(() => { if (!open) clearTimeout(timer.current); else if (job && !job.finished) poll(job.id); }, [open]);

  const poll = (id) => {
    clearTimeout(timer.current);
    timer.current = setTimeout(async () => {
      try {
        const { data } = await api.get(`/discover/scan/${id}`); setJob(data);
        if (!data.finished) poll(id);
        else setPicked(new Set(data.results.map((r, i) => r?.status === "ok" ? i : -1).filter(i => i >= 0)));
      } catch (e) { toast.error(formatApiError(e)); }
    }, 1500);
  };
  const start = async (e) => {
    e.preventDefault(); setStarting(true);
    try {
      const { data } = await api.post("/discover/scan", { ...form, port: form.port ? Number(form.port) : null, agent_id: form.agent_id || null });
      setJob(data); setFilter(""); setPicked(new Set()); poll(data.id);
    } catch (err) { toast.error(formatApiError(err)); } finally { setStarting(false); }
  };
  const cancel = async () => { try { await api.post(`/discover/scan/${job.id}/cancel`); toast("Cancelando — os que já começaram terminam"); } catch (e) { toast.error(formatApiError(e)); } };

  const rows = useMemo(() => (job?.results || []).map((r, i) => r && { ...r, i }).filter(Boolean), [job]);
  const groups = useMemo(() => { const g = {}; for (const r of rows) g[groupOf(r)] = (g[groupOf(r)] || 0) + 1; return Object.entries(g).sort((a, b) => b[1] - a[1]); }, [rows]);
  const shown = filter ? rows.filter(r => groupOf(r) === filter) : rows;
  const nameOf = (r) => r.name || r.hostname || r.host;
  const table = () => [["IP", "Nome", "Fabricante", "Confiança", "Protocolo", "Porta", "Situação", "Detalhe"],
    ...rows.map(r => [r.host, r.name || r.hostname || "", r.label || "", r.confidence || "", r.protocol || "", r.port || "", STATUS[r.status]?.[1] || r.status, r.detail || r.error || ""])];
  const copy = () => navigator.clipboard?.writeText(table().map(l => l.join("\t")).join("\n")).then(() => toast.success("Copiado — cole no Excel"), () => toast.error("Não consegui copiar — use Baixar CSV"));
  const csv = () => {
    const txt = table().map(l => l.map(c => `"${String(c).replace(/"/g, '""')}"`).join(";")).join("\r\n");
    const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob(["﻿" + txt], { type: "text/csv" })); a.download = "fabricantes.csv"; a.click(); URL.revokeObjectURL(a.href);
  };
  const toggle = (i) => setPicked(s => { const n = new Set(s); n.has(i) ? n.delete(i) : n.add(i); return n; });
  const canPick = (r) => r.status === "ok" || r.status === "unknown";
  const save = async () => {
    const list = rows.filter(r => picked.has(r.i) && canPick(r));
    if (!list.length) return;
    setSaving(true);
    try {
      const { data } = await api.post("/devices/import", { rows: list.map(r => ({ name: nameOf(r), host: r.host, port: r.port, protocol: r.protocol, username: job.username, password: form.password,
        device_type: r.vendor || "other", tags, agent: job.agent_id || "", description: r.detail || "" })) });
      toast.success(`${data.created} cadastrado(s)` + (data.skipped ? `, ${data.skipped} já existia(m)` : ""));
      if (data.errors?.length) toast.error(data.errors.slice(0, 3).join(" · "));
      onDone?.();
    } catch (e) { toast.error(formatApiError(e)); } finally { setSaving(false); }
  };
  const pct = job ? Math.round(100 * job.done / Math.max(1, job.total)) : 0;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="bg-surface border-line text-slate-100 max-w-5xl max-h-[94vh] overflow-y-auto" data-testid="scan-dialog">
        <DialogHeader><DialogTitle className="flex items-center gap-2"><Radar className="w-5 h-5 text-brand-soft" />Identificar fabricante por lista de IPs</DialogTitle></DialogHeader>
        {!job ? (
          <form onSubmit={start} className="grid md:grid-cols-[minmax(0,1fr)_300px] gap-5 min-w-0">
            <div>
              <Label className="text-slate-300">Lista de IPs <span className="text-slate-500 font-mono text-xs">({count})</span></Label>
              <Textarea value={form.targets} onChange={e => setForm({ ...form, targets: e.target.value })} rows={16} spellCheck={false} autoFocus
                placeholder={"10.0.0.1\n10.0.0.2  SW-CORE-01\n10.0.10.5:2222\n10.0.20.0/28"} className="mt-1.5 bg-sunken border-line font-mono text-xs" data-testid="scan-targets" />
              <p className="text-[11px] text-slate-500 mt-1.5">Um por linha. Opcional: <code>IP:porta</code>, um nome depois do IP, ou uma rede (até /22). Máximo de 1024 endereços.</p>
            </div>
            <div className="space-y-3 min-w-0">
              <div><Label className="text-slate-300">Usuário</Label><Input value={form.username} onChange={e => setForm({ ...form, username: e.target.value })} autoComplete="off" placeholder="vazio = credencial padrão" className="mt-1 bg-sunken border-line font-mono" data-testid="scan-user" /></div>
              <div><Label className="text-slate-300">Senha</Label><Input type="password" value={form.password} onChange={e => setForm({ ...form, password: e.target.value })} autoComplete="new-password" className="mt-1 bg-sunken border-line font-mono" data-testid="scan-pass" /></div>
              <div className="grid grid-cols-[1fr_90px] gap-2">
                <div><Label className="text-slate-300">Protocolo</Label>
                  <select value={form.protocol} onChange={e => setForm({ ...form, protocol: e.target.value })} className={`${sel} w-full mt-1`} data-testid="scan-proto">
                    <option value="auto">SSH, depois Telnet</option><option value="ssh">Só SSH</option><option value="telnet">Só Telnet</option></select></div>
                <div><Label className="text-slate-300">Porta</Label><Input value={form.port} onChange={e => setForm({ ...form, port: e.target.value.replace(/\D/g, "").slice(0, 5) })} placeholder={form.protocol === "telnet" ? "23" : "22"} className="mt-1 bg-sunken border-line font-mono" /></div>
              </div>
              <div><Label className="text-slate-300">Sair por</Label>
                <select value={form.agent_id} onChange={e => setForm({ ...form, agent_id: e.target.value })} className={`${sel} w-full mt-1`} data-testid="scan-agent">
                  <option value="">Direto do BastiON</option>{agents.map(a => <option key={a.id} value={a.id}>Agente {a.name}</option>)}</select></div>
              <p className="text-[11px] text-slate-500 leading-relaxed">Faz <b className="text-slate-300">uma</b> tentativa de login por equipamento (no modo "SSH, depois Telnet", o Telnet só é tentado se o SSH não abrir). Depois de entrar, só comandos de leitura (<code>display version</code> / <code>show version</code>). A senha não é gravada.</p>
              <Button type="submit" disabled={starting || !count} className="w-full bg-brand hover:bg-brand-strong" data-testid="scan-start">{starting ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <Radar className="w-4 h-4 mr-2" />}Identificar {count || ""}</Button>
            </div>
          </form>
        ) : (
          <div className="space-y-3 min-w-0">
            <div className="flex items-center gap-3 flex-wrap">
              <Button size="sm" variant="ghost" className="text-slate-300" disabled={!job.finished} onClick={() => setJob(null)} data-testid="scan-back"><ArrowLeft className="w-4 h-4 mr-1" />Nova lista</Button>
              <div className="flex-1 min-w-[160px]">
                <div className="h-1.5 rounded bg-sunken overflow-hidden"><div className={`h-full ${job.finished ? "bg-on" : "bg-brand"} transition-all`} style={{ width: `${pct}%` }} /></div>
                <div className="text-[11px] text-slate-500 font-mono mt-1" data-testid="scan-progress">{job.done}/{job.total} {job.finished ? "— concluído" : "— testando…"}{job.agent_name ? ` · via ${job.agent_name}` : ""} · usuário {job.username}</div>
              </div>
              {!job.finished ? <Button size="sm" variant="outline" className="border-line text-slate-200" onClick={cancel}><Square className="w-3.5 h-3.5 mr-1.5" />Parar</Button> : <>
                <Button size="sm" variant="outline" className="border-line text-slate-200" onClick={copy} data-testid="scan-copy"><Copy className="w-3.5 h-3.5 mr-1.5" />Copiar</Button>
                <Button size="sm" variant="outline" className="border-line text-slate-200" onClick={csv}><Download className="w-3.5 h-3.5 mr-1.5" />CSV</Button></>}
            </div>
            {job.error && <div className="text-sm text-red-400">{job.error}</div>}
            {job.invalid?.length > 0 && <div className="text-xs text-amber-300">Linhas ignoradas: {job.invalid.join(" · ")}</div>}
            <div className="flex flex-wrap gap-1.5">
              <button onClick={() => setFilter("")} className={`px-2.5 py-1 rounded text-xs border ${!filter ? "border-brand text-brand-soft bg-brand/10" : "border-line text-slate-400"}`}>Todos {rows.length}</button>
              {groups.map(([g, n]) => <button key={g} onClick={() => setFilter(g === filter ? "" : g)} data-testid={`scan-group-${g}`}
                className={`px-2.5 py-1 rounded text-xs border ${filter === g ? "border-brand text-brand-soft bg-brand/10" : "border-line text-slate-300"}`}>{g} <b className="font-mono">{n}</b></button>)}
            </div>
            <div className="border border-line rounded-md overflow-x-auto max-h-[48vh] overflow-y-auto">
              <table className="w-full text-sm">
                <thead className="sticky top-0 bg-panel"><tr className="text-left text-[11px] uppercase tracking-wider text-slate-500 border-b border-line">
                  <th className="px-3 py-2 w-8" /><th className="px-2 py-2">IP</th><th className="px-2 py-2">Nome</th><th className="px-2 py-2">Fabricante</th><th className="px-2 py-2">Acesso</th><th className="px-2 py-2">Detalhe</th></tr></thead>
                <tbody className="divide-y divide-line/60">
                  {shown.map(r => (
                    <tr key={r.i} data-testid="scan-row">
                      <td className="px-3 py-1.5">{canPick(r) && <Checkbox checked={picked.has(r.i)} onCheckedChange={() => toggle(r.i)} />}</td>
                      <td className="px-2 py-1.5 font-mono text-slate-200 whitespace-nowrap">{r.host}</td>
                      <td className="px-2 py-1.5 text-slate-300 whitespace-nowrap">{r.name || r.hostname || <span className="text-slate-600">—</span>}</td>
                      <td className="px-2 py-1.5 whitespace-nowrap">{r.label ? <span className={`px-1.5 py-0.5 rounded border text-xs ${VCOLOR[r.vendor] || "text-slate-200 border-line"}`} title={r.evidence ? `pista: ${r.evidence}` : ""}>{r.label}{r.confidence && r.confidence !== "alta" ? <span className="text-slate-500"> · {r.confidence}</span> : ""}</span> : <span className="text-slate-600">—</span>}</td>
                      <td className={`px-2 py-1.5 text-xs whitespace-nowrap ${STATUS[r.status]?.[0] || ""}`}>{r.protocol ? `${r.protocol}/${r.port} · ` : ""}{STATUS[r.status]?.[1] || r.status}</td>
                      <td className="px-2 py-1.5 text-xs text-slate-400 font-mono max-w-[340px] truncate" title={r.detail || r.error || ""}>{r.detail || r.error}</td>
                    </tr>
                  ))}
                  {!shown.length && <tr><td colSpan={6} className="px-3 py-8 text-center text-xs text-slate-500 font-mono">{job.finished ? "nada aqui" : "aguardando os primeiros resultados…"}</td></tr>}
                </tbody>
              </table>
            </div>
            {job.finished && (
              <div className="flex items-end gap-3 flex-wrap border-t border-line pt-3">
                <div className="flex-1 min-w-[200px]"><Label className="text-[11px] text-slate-400">Tags para os cadastrados (separe por vírgula)</Label>
                  <Input value={tags} onChange={e => setTags(e.target.value)} placeholder="routers" className="mt-1 h-9 bg-sunken border-line" data-testid="scan-tags" /></div>
                <Button onClick={save} disabled={saving || !picked.size} className="bg-brand hover:bg-brand-strong" data-testid="scan-save">{saving ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <Plus className="w-4 h-4 mr-2" />}Cadastrar {picked.size} em Equipamentos</Button>
                <p className="w-full text-[11px] text-slate-500">Cadastra com o fabricante, protocolo e porta encontrados, o usuário e a senha usados aqui{job.agent_name ? ` e o agente ${job.agent_name}` : ""}. Quem já existe com o mesmo nome e IP é pulado.</p>
              </div>
            )}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
