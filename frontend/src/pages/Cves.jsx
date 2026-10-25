import React, { useCallback, useEffect, useState } from "react";
import { api, formatApiError } from "@/lib/api";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { toast } from "sonner";
import { Bug, RefreshCw, Loader2, Flame, ExternalLink, Save, Settings2, AlertTriangle } from "lucide-react";

const fmt = (s) => (s ? new Date(s).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", year: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—");
const scoreCls = (s) => (s >= 9 ? "text-red-300 border-red-500/40" : s >= 7 ? "text-orange-300 border-orange-500/40" : s >= 4 ? "text-amber-300 border-amber-500/40" : "text-slate-400 border-line");
const lbl = "text-xs text-slate-400 mb-1 block";

function Detail({ id, onClose, onSaved }) {
  const [d, setD] = useState(null);
  const [q, setQ] = useState("");
  const [ovr, setOvr] = useState({ product: "", version: "" });
  useEffect(() => { api.get(`/soc/cves/device/${id}`).then(r => { setD(r.data); const p = r.data.products?.[0]; if (p) setOvr({ product: p.product, version: p.version }); }).catch(e => { toast.error(formatApiError(e)); onClose(); }); }, [id, onClose]);
  const save = async (clear) => {
    try { await api.put(`/soc/cves/override/${id}`, clear ? { product: "", version: "" } : ovr); toast.success("Salvo — recalculando"); onSaved(); onClose(); }
    catch (e) { toast.error(formatApiError(e)); }
  };
  const list = (d?.cves || []).filter(c => !q || c.id.toLowerCase().includes(q.toLowerCase()) || c.desc.toLowerCase().includes(q.toLowerCase()));
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="bg-surface border-line text-slate-100 max-w-4xl w-[96vw] max-h-[90vh] overflow-y-auto" data-testid="cve-detail">
        <DialogHeader><DialogTitle>{d ? `${d.device_name} — ${d.version || "versão desconhecida"}` : "Carregando…"}</DialogTitle></DialogHeader>
        {d && <>
          <div className="text-xs text-slate-400 -mt-2">{d.products.map(p => `${p.label} ${p.version}`).join(" · ") || "Produto não identificado"} · analisado {fmt(d.at)}</div>
          <Card className="bg-sunken/60 border-line p-3 space-y-2">
            <div className="text-xs text-slate-400">Produto e versão usados na busca {d.override ? "(definidos à mão)" : "(do backup)"} — ajuste se o BastiON errou:</div>
            <div className="flex flex-wrap gap-2">
              <Input value={ovr.product} onChange={e => setOvr({ ...ovr, product: e.target.value })} placeholder="mikrotik:routeros" className="bg-sunken border-line h-8 w-56 font-mono text-xs" data-testid="cve-ovr-product" />
              <Input value={ovr.version} onChange={e => setOvr({ ...ovr, version: e.target.value })} placeholder="7.11.2" className="bg-sunken border-line h-8 w-40 font-mono text-xs" data-testid="cve-ovr-version" />
              <Button size="sm" variant="outline" onClick={() => save(false)} className="h-8 border-line bg-transparent" data-testid="cve-ovr-save">Usar</Button>
              {d.override && <Button size="sm" variant="ghost" onClick={() => save(true)} className="h-8 text-slate-400">Voltar ao automático</Button>}
            </div>
          </Card>
          <Input value={q} onChange={e => setQ(e.target.value)} placeholder="filtrar por CVE ou texto" className="bg-sunken border-line h-8" />
          <div className="divide-y divide-line">
            {list.length === 0 && <div className="text-sm text-slate-500 py-4">Nenhuma CVE para essa versão na base.</div>}
            {list.map(c => (
              <div key={c.id} className="py-2 text-sm space-y-1" data-testid={`cve-${c.id}`}>
                <div className="flex flex-wrap items-center gap-2">
                  <a href={`https://nvd.nist.gov/vuln/detail/${c.id}`} target="_blank" rel="noreferrer" className="font-mono text-brand-soft hover:underline inline-flex items-center gap-1">{c.id}<ExternalLink className="w-3 h-3" /></a>
                  {c.score != null && <span className={`text-[11px] px-1.5 rounded border ${scoreCls(c.score)}`}>CVSS {c.score}</span>}
                  {c.kev && <span className="text-[11px] px-1.5 rounded border border-red-500/60 bg-red-500/10 text-red-200 inline-flex items-center gap-1"><Flame className="w-3 h-3" />explorada ativamente (KEV)</span>}
                  {c.all_versions && <span className="text-[11px] text-slate-500">todas as versões (confira o texto)</span>}
                  <span className="text-[11px] text-slate-500 ml-auto">{c.pub}</span>
                </div>
                <div className="text-xs text-slate-400">{c.desc}</div>
              </div>
            ))}
          </div>
        </>}
      </DialogContent>
    </Dialog>
  );
}

function SettingsCard({ s, onSaved }) {
  const [f, setF] = useState({ ...s, nvd_key: "" });
  const save = async () => {
    try { await api.put("/soc/cves/settings", { ...f, alert_min_score: Number(f.alert_min_score), refresh_days: Number(f.refresh_days) }); toast.success("Salvo"); onSaved(); }
    catch (e) { toast.error(formatApiError(e)); }
  };
  return (
    <Card className="bg-surface border-line p-4 grid gap-3 md:grid-cols-[auto_auto_10rem_10rem_1fr_auto] items-end" data-testid="cve-settings">
      <label className="flex items-center gap-2 text-sm text-slate-200"><Switch checked={f.enabled} onCheckedChange={v => setF({ ...f, enabled: v })} /> Ativo</label>
      <label className="flex items-center gap-2 text-sm text-slate-300"><Switch checked={f.alert_kev} onCheckedChange={v => setF({ ...f, alert_kev: v })} /> Incidente para KEV</label>
      <div><label className={lbl}>…e CVSS a partir de</label><Input type="number" step="0.1" value={f.alert_min_score} onChange={e => setF({ ...f, alert_min_score: e.target.value })} className="bg-sunken border-line h-9" /></div>
      <div><label className={lbl}>Atualizar a NVD a cada (dias)</label><Input type="number" value={f.refresh_days} onChange={e => setF({ ...f, refresh_days: e.target.value })} className="bg-sunken border-line h-9" /></div>
      <div><label className={lbl}>Chave da API da NVD (opcional, deixa bem mais rápido){s.has_key ? " — guardada" : ""}</label>
        <Input type="password" value={f.nvd_key} onChange={e => setF({ ...f, nvd_key: e.target.value })} placeholder={s.has_key ? "vazio = manter" : "nvd.nist.gov/developers/request-an-api-key"} className="bg-sunken border-line h-9" autoComplete="new-password" data-testid="cve-key" /></div>
      <Button size="sm" onClick={save} className="bg-brand hover:bg-brand-strong text-onbrand h-9" data-testid="cve-save"><Save className="w-4 h-4 mr-1" />Salvar</Button>
    </Card>
  );
}

export default function Cves() {
  const [data, setData] = useState(null);
  const [detail, setDetail] = useState(null);
  const [cfg, setCfg] = useState(false);
  const [only, setOnly] = useState(true);
  const load = useCallback(() => api.get("/soc/cves/summary").then(r => setData(r.data)).catch(e => toast.error(formatApiError(e))), []);
  useEffect(() => { load(); }, [load]);
  useEffect(() => { if (!data?.busy) return undefined; const t = setTimeout(load, 3000); return () => clearTimeout(t); }, [data, load]);
  const refresh = async () => { try { await api.post("/soc/cves/refresh"); toast.success("Atualizando a base"); load(); } catch (e) { toast.error(formatApiError(e)); } };
  const closeDetail = useCallback(() => setDetail(null), []);
  const rows = (data?.devices || []).filter(d => !only || (d.summary?.total || 0) > 0 || !d.products?.length);
  const tot = (data?.devices || []).reduce((a, d) => ({ kev: a.kev + (d.summary?.kev || 0), crit: a.crit + ((d.summary?.critical || 0) > 0 ? 1 : 0) }), { kev: 0, crit: 0 });
  const noProd = (data?.devices || []).filter(d => !d.products?.length).length;
  return (
    <div className="p-4 md:p-6 flex-1 overflow-y-auto" data-testid="cve-page">
      <div className="flex items-start justify-between flex-wrap gap-3 mb-4">
        <div>
          <h1 className="font-heading text-2xl sm:text-[1.75rem] font-semibold tracking-tight text-slate-100 flex items-center gap-2"><Bug className="w-6 h-6 text-brand-soft" /> CVEs do firmware</h1>
          <p className="text-sm text-slate-400 mt-1 max-w-3xl">A versão de cada equipamento (lida dos backups, como na Conformidade) cruzada com a NVD e com o catálogo de vulnerabilidades exploradas da CISA (KEV).
            {data && <> <b className="text-red-300">{tot.kev}</b> CVE(s) KEV · <b className="text-orange-300">{tot.crit}</b> equipamento(s) com crítica.</>}</p>
        </div>
        <div className="flex gap-2 items-center">
          {data?.busy && <span className="text-xs text-slate-400 flex items-center gap-1"><Loader2 className="w-3.5 h-3.5 animate-spin" />{data.step}</span>}
          <Button size="sm" variant="ghost" onClick={() => setCfg(!cfg)} className="text-slate-300"><Settings2 className="w-4 h-4" /></Button>
          <Button size="sm" onClick={refresh} disabled={data?.busy} className="bg-brand hover:bg-brand-strong text-onbrand" data-testid="cve-refresh"><RefreshCw className="w-4 h-4 mr-1.5" />Atualizar base</Button>
        </div>
      </div>
      {cfg && data && <div className="mb-4"><SettingsCard s={data.settings} onSaved={load} /></div>}
      {data && (
        <div className="flex flex-wrap gap-2 mb-3 text-xs" data-testid="cve-products">
          {data.products.map(p => <span key={p.product} className={`px-2 py-1 rounded border ${p.error ? "border-red-500/40 text-red-300" : "border-line text-slate-400"}`} title={p.error || ""}>
            {data.labels[p.product] || p.product}: {p.error ? "erro" : `${p.count} CVEs`} · {fmt(p.ok_at || p.at)}</span>)}
          <span className={`px-2 py-1 rounded border ${data.kev.error ? "border-red-500/40 text-red-300" : "border-line text-slate-400"}`}>KEV: {data.kev.count ?? "—"} · {fmt(data.kev.at)}</span>
          <label className="ml-auto flex items-center gap-2 text-slate-300"><input type="checkbox" className="accent-brand" checked={only} onChange={e => setOnly(e.target.checked)} /> só com CVE ou sem versão</label>
        </div>
      )}
      {noProd > 0 && <div className="text-[11px] text-slate-500 mb-2"><AlertTriangle className="w-3 h-3 inline mr-1" />{noProd} equipamento(s) sem produto/versão reconhecido (sem backup, ou fabricante sem dados na NVD: ZTE, Datacom, FiberHome, Linux…). Dá para informar à mão no detalhe.</div>}
      <Card className="bg-surface border-line overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-xs text-slate-500"><tr><th className="text-left px-4 py-2">Equipamento</th><th className="text-left px-2">Produto</th><th className="text-left px-2">Versão</th>
            <th className="text-right px-2">KEV</th><th className="text-right px-2">Críticas</th><th className="text-right px-2">Altas</th><th className="text-right px-2">Total</th><th className="text-right px-4">Maior CVSS</th></tr></thead>
          <tbody className="divide-y divide-line">
            {rows.map(d => (
              <tr key={d.device_id} onClick={() => setDetail(d.device_id)} className="hover:bg-white/[0.03] cursor-pointer" data-testid={`cve-row-${d.device_name}`}>
                <td className="px-4 py-2 text-slate-100">{d.device_name}<span className="text-xs text-slate-500 ml-2">{d.model || d.device_type}</span></td>
                <td className="px-2 text-xs text-slate-400">{d.products.map(p => p.label).join(", ") || "—"}{d.override ? " ✎" : ""}</td>
                <td className="px-2 font-mono text-xs text-slate-300">{d.products[0]?.version || d.version || "—"}</td>
                <td className="px-2 text-right">{d.summary?.kev ? <span className="text-red-300 font-semibold inline-flex items-center gap-1"><Flame className="w-3.5 h-3.5" />{d.summary.kev}</span> : <span className="text-slate-600">0</span>}</td>
                <td className="px-2 text-right font-mono text-red-300">{d.summary?.critical || <span className="text-slate-600">0</span>}</td>
                <td className="px-2 text-right font-mono text-orange-300">{d.summary?.high || <span className="text-slate-600">0</span>}</td>
                <td className="px-2 text-right font-mono text-slate-300">{d.summary?.total ?? 0}</td>
                <td className="px-4 text-right">{d.summary?.max_score ? <span className={`text-[11px] px-1.5 rounded border ${scoreCls(d.summary.max_score)}`}>{d.summary.max_score}</span> : "—"}</td>
              </tr>
            ))}
            {data && rows.length === 0 && <tr><td colSpan={8} className="px-4 py-6 text-center text-slate-500">{data.devices.length ? "Nenhum equipamento com CVE conhecida." : "Ainda sem análise — clique em Atualizar base."}</td></tr>}
          </tbody>
        </table>
      </Card>
      {detail && <Detail id={detail} onClose={closeDetail} onSaved={load} />}
    </div>
  );
}
