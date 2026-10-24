import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, formatApiError } from "@/lib/api";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { toast } from "sonner";
import { ClipboardCheck, RefreshCw, Loader2, Plus, Pencil, Trash2, RotateCcw, FlaskConical, CheckCircle2, XCircle, Cpu } from "lucide-react";
import { useAuth } from "@/context/AuthContext";

const SEVL = { high: ["Alta", "text-red-300 border-red-500/40"], medium: ["Média", "text-amber-300 border-amber-500/40"], low: ["Baixa", "text-sky-300 border-sky-500/40"] };
const FW = { ok: ["no padrão", "text-emerald-300"], outdated: ["desatualizado", "text-red-300"], nostd: ["sem padrão definido", "text-slate-400"], unknown: ["não identificado", "text-slate-500"] };
const sel = "h-9 rounded-md bg-sunken border border-line px-2 text-sm text-slate-200";
const scoreCls = (s) => (s == null ? "bg-slate-600" : s >= 90 ? "bg-emerald-500" : s >= 70 ? "bg-amber-400" : "bg-red-500");

function DeviceDetail({ id, onClose }) {
  const [d, setD] = useState(null);
  useEffect(() => { api.get(`/compliance/device/${id}`).then(r => setD(r.data)).catch(e => { toast.error(formatApiError(e)); onClose(); }); }, [id, onClose]);
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="bg-surface border-line text-slate-100 max-w-3xl w-[96vw] max-h-[90vh] overflow-y-auto" data-testid="comp-detail">
        <DialogHeader><DialogTitle>{d ? `${d.device_name} — ${d.score ?? "—"}%` : "Carregando…"}</DialogTitle></DialogHeader>
        {d && <>
          <div className="text-xs text-slate-400 -mt-2">Backup de {new Date(d.backup_at).toLocaleString("pt-BR")} · firmware {d.firmware?.version || "não identificado"}{d.firmware?.model ? ` · ${d.firmware.model}` : ""}</div>
          <div className="divide-y divide-line">
            {[...d.results].sort((a, b) => (a.ok === b.ok ? 0 : a.ok ? 1 : -1)).map(x => (
              <div key={x.rule_id} className="py-2 text-sm">
                <div className="flex items-center gap-2">
                  {x.ok ? <CheckCircle2 className="w-4 h-4 text-emerald-300 shrink-0" /> : x.ok === false ? <XCircle className="w-4 h-4 text-red-300 shrink-0" /> : <span className="w-4" />}
                  <span className="text-slate-100">{x.rule.name}</span>
                  <span className={`text-[10px] px-1.5 rounded border ${(SEVL[x.rule.severity] || SEVL.medium)[1]}`}>{(SEVL[x.rule.severity] || SEVL.medium)[0]}</span>
                  {x.error && <span className="text-xs text-amber-300">{x.error}</span>}
                </div>
                {x.evidence && <div className="ml-6 text-[11px] font-mono text-slate-400">{x.evidence}</div>}
                {x.ok === false && x.rule.fix && <pre className="ml-6 mt-1 text-[11px] font-mono bg-sunken border border-line rounded px-2 py-1 text-emerald-200 whitespace-pre-wrap">{x.rule.fix}</pre>}
              </div>
            ))}
          </div>
        </>}
      </DialogContent>
    </Dialog>
  );
}

function RuleDialog({ rule, vendors, onClose, onSaved }) {
  const [f, setF] = useState({ name: "", vendor: "huawei", kind: "must", pattern: "", severity: "medium", fix: "", why: "", enabled: true, ...(rule || {}) });
  const [test, setTest] = useState(null);
  const [busy, setBusy] = useState(false);
  const set = (k, v) => setF(p => ({ ...p, [k]: v }));
  const save = async () => {
    setBusy(true);
    try { if (rule?.id) await api.put(`/compliance/rules/${rule.id}`, f); else await api.post("/compliance/rules", f); toast.success("Regra salva — recalculando"); onSaved(); }
    catch (e) { toast.error(formatApiError(e)); } finally { setBusy(false); }
  };
  const runTest = async () => { setBusy(true); try { setTest((await api.post("/compliance/rules/test", { pattern: f.pattern, kind: f.kind, vendor: f.vendor })).data); } catch (e) { toast.error(formatApiError(e)); } finally { setBusy(false); } };
  const lbl = "text-xs text-slate-400 mb-1 block";
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="bg-surface border-line text-slate-100 max-w-2xl w-[96vw] max-h-[92vh] overflow-y-auto" data-testid="comp-rule-dialog">
        <DialogHeader><DialogTitle>{rule?.id ? "Editar regra" : "Nova regra"}</DialogTitle></DialogHeader>
        <div className="grid sm:grid-cols-3 gap-3">
          <div className="sm:col-span-2"><label className={lbl}>Nome</label><Input value={f.name} onChange={e => set("name", e.target.value)} className="bg-sunken border-line" data-testid="comp-rule-name" /></div>
          <div><label className={lbl}>Fabricante</label><select value={f.vendor} onChange={e => set("vendor", e.target.value)} className={`${sel} w-full`}>{Object.entries(vendors).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></div>
          <div><label className={lbl}>A configuração</label><select value={f.kind} onChange={e => set("kind", e.target.value)} className={`${sel} w-full`} data-testid="comp-rule-kind"><option value="must">precisa ter</option><option value="must_not">não pode ter</option></select></div>
          <div><label className={lbl}>Importância</label><select value={f.severity} onChange={e => set("severity", e.target.value)} className={`${sel} w-full`}>{Object.entries(SEVL).map(([k, [l]]) => <option key={k} value={k}>{l}</option>)}</select></div>
          <label className="flex items-center gap-2 text-sm text-slate-300 mt-5"><Switch checked={f.enabled} onCheckedChange={v => set("enabled", v)} /> Ativa</label>
          <div className="sm:col-span-3"><label className={lbl}>Linha (regex; ^ = começo da linha, sem diferenciar maiúsculas)</label><Input value={f.pattern} onChange={e => set("pattern", e.target.value)} className="bg-sunken border-line font-mono" data-testid="comp-rule-pattern" /></div>
          <div className="sm:col-span-3"><label className={lbl}>Como corrigir (comandos)</label><textarea value={f.fix} onChange={e => set("fix", e.target.value)} rows={3} className="w-full rounded-md bg-sunken border border-line p-2 font-mono text-xs text-slate-200" /></div>
        </div>
        <div className="flex items-center gap-2">
          <Button size="sm" variant="ghost" onClick={runTest} disabled={busy || !f.pattern} className="text-slate-200" data-testid="comp-rule-test"><FlaskConical className="w-4 h-4 mr-1" /> Testar nos backups</Button>
          {test && <span className="text-xs text-slate-300" data-testid="comp-rule-test-result">{test.pass} de {test.total} equipamento(s) cumprem</span>}
        </div>
        {test?.results?.length > 0 && <div className="max-h-40 overflow-y-auto text-xs divide-y divide-line border border-line rounded">{test.results.map((r, i) => <div key={i} className="px-2 py-1 flex gap-2"><span className={r.ok ? "text-emerald-300" : "text-red-300"}>{r.ok ? "ok" : "falha"}</span><span className="text-slate-200">{r.device}</span><span className="font-mono text-slate-500 truncate">{r.evidence}</span></div>)}</div>}
        <DialogFooter><Button variant="ghost" onClick={onClose} className="text-slate-300">Cancelar</Button><Button onClick={save} disabled={busy || !f.name || !f.pattern} className="bg-brand hover:bg-brand-strong text-onbrand" data-testid="comp-rule-save">Salvar</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function Firmware({ data, isAdmin, reload }) {
  const [appr, setAppr] = useState(() => ({ ...data.approved }));
  const groups = useMemo(() => {
    const m = {};
    for (const d of data.devices) { const v = d.device_type; m[v] = m[v] || {}; const k = d.firmware?.version || "—"; (m[v][k] = m[v][k] || []).push(d); }
    return Object.entries(m);
  }, [data.devices]);
  const save = async () => { try { await api.put("/compliance/settings", { approved: appr, alert: data.alert }); toast.success("Padrões salvos"); reload(); } catch (e) { toast.error(formatApiError(e)); } };
  return (
    <div className="grid gap-3" data-testid="comp-firmware">
      {groups.map(([vt, vers]) => (
        <Card key={vt} className="bg-surface border-line p-4">
          <div className="flex flex-wrap items-center gap-3 mb-2">
            <Cpu className="w-4 h-4 text-slate-400" /><span className="text-slate-100 font-medium">{data.vendors[vt] || vt}</span>
            {isAdmin && <div className="flex items-center gap-2 ml-auto text-xs text-slate-400">versão mínima aprovada
              <Input value={appr[vt]?.min || ""} onChange={e => setAppr(a => ({ ...a, [vt]: { ...(a[vt] || {}), min: e.target.value } }))} placeholder="ex.: V800R021C10 / 7.14" className="h-8 w-48 bg-sunken border-line font-mono text-xs" data-testid={`comp-min-${vt}`} /></div>}
          </div>
          <div className="space-y-1">
            {Object.entries(vers).sort((a, b) => b[1].length - a[1].length).map(([ver, list]) => {
              const st = list[0].fw_status;
              return (
                <div key={ver} className="flex flex-wrap items-center gap-2 text-sm">
                  <span className="font-mono text-slate-200 w-56 truncate">{ver}</span>
                  <span className="text-xs text-slate-400 w-24">{list.length} equip.</span>
                  <span className={`text-xs ${(FW[st] || FW.unknown)[1]}`}>{(FW[st] || FW.unknown)[0]}</span>
                  <span className="text-xs text-slate-500 truncate flex-1">{list.map(d => d.device_name).slice(0, 8).join(", ")}{list.length > 8 ? "…" : ""}</span>
                </div>
              );
            })}
          </div>
        </Card>
      ))}
      {isAdmin && groups.length > 0 && <div><Button onClick={save} className="bg-brand hover:bg-brand-strong text-onbrand" data-testid="comp-fw-save">Salvar padrões</Button></div>}
    </div>
  );
}

export default function Compliance() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const [sp, setSp] = useSearchParams();
  const tab = sp.get("tab") || "";
  const [data, setData] = useState(null);
  const [detail, setDetail] = useState(null);
  const [edit, setEdit] = useState(null);
  const [vendor, setVendor] = useState("");
  const load = useCallback(() => api.get("/compliance/summary").then(r => setData(r.data)).catch(e => toast.error(formatApiError(e))), []);
  useEffect(() => { load(); }, [load]);
  const closeDetail = useCallback(() => setDetail(null), []);
  const refresh = async () => { try { await api.post("/compliance/refresh"); toast.success("Recalculando"); setTimeout(load, 4000); } catch (e) { toast.error(formatApiError(e)); } };
  const del = async (r) => { if (!window.confirm(`Apagar a regra "${r.name}"?`)) return; try { await api.delete(`/compliance/rules/${r.id}`); load(); } catch (e) { toast.error(formatApiError(e)); } };
  const presets = async () => { try { const r = await api.post("/compliance/rules/presets"); toast.success(`${r.data.added} regra(s) recolocada(s)`); load(); } catch (e) { toast.error(formatApiError(e)); } };
  const toggle = async (r, v) => { try { await api.put(`/compliance/rules/${r.id}`, { ...r, enabled: v }); load(); } catch (e) { toast.error(formatApiError(e)); } };
  const devs = (data?.devices || []).filter(d => !vendor || d.device_type === vendor);
  const avg = devs.filter(d => d.score != null).reduce((a, d, _, arr) => a + d.score / arr.length, 0);
  const outd = (data?.devices || []).filter(d => d.fw_status === "outdated").length;
  return (
    <div className="p-4 md:p-6 flex-1 overflow-y-auto" data-testid="comp-page">
      <div className="flex items-start justify-between flex-wrap gap-3 mb-4">
        <div>
          <h1 className="font-heading text-2xl sm:text-[1.75rem] font-semibold tracking-tight text-slate-100 flex items-center gap-2"><ClipboardCheck className="w-6 h-6 text-brand-soft" /> Conformidade</h1>
          <p className="text-sm text-slate-400 mt-1">Cada backup de configuração passa pelas regras do fabricante (NTP, SNMP, AAA, SSH, telnet…). {data && <>Média <b className="text-slate-200">{avg ? avg.toFixed(0) : "—"}%</b>{outd > 0 && <span className="text-red-300"> · {outd} com firmware desatualizado</span>}{data.no_backup > 0 && <span className="text-slate-500"> · {data.no_backup} sem backup</span>}</>}</p>
        </div>
        <div className="flex gap-2 items-center">
          <div className="flex gap-1 p-1 bg-sunken border border-line rounded-md text-sm">
            {[["", "Equipamentos"], ["regras", "Regras"], ["firmware", "Firmware"]].map(([k, l]) => <button key={k} onClick={() => setSp(k ? { tab: k } : {})} className={`px-3 h-7 rounded ${tab === k ? "bg-brand/20 text-brand-soft" : "text-slate-400 hover:text-slate-200"}`} data-testid={`comp-tab-${k || "devs"}`}>{l}</button>)}
          </div>
          {isAdmin && <Button size="sm" variant="ghost" onClick={refresh} className="text-slate-300" title="Recalcular"><RefreshCw className="w-4 h-4" /></Button>}
        </div>
      </div>
      {!data ? <div className="py-10 text-center"><Loader2 className="w-5 h-5 animate-spin inline text-slate-500" /></div> : tab === "regras" ? (
        <>
          {isAdmin && <div className="flex gap-2 mb-3 justify-end">
            <Button size="sm" variant="ghost" onClick={presets} className="text-slate-300"><RotateCcw className="w-4 h-4 mr-1" /> Recolocar modelos</Button>
            <Button size="sm" onClick={() => setEdit({})} className="bg-brand hover:bg-brand-strong text-onbrand" data-testid="comp-rule-new"><Plus className="w-4 h-4 mr-1" /> Nova regra</Button>
          </div>}
          <Card className="bg-surface border-line overflow-x-auto">
            <table className="w-full text-sm" data-testid="comp-rules">
              <thead className="text-xs text-slate-500 bg-panel"><tr><th className="text-left px-3 py-2">Regra</th><th className="text-left px-2">Fabricante</th><th className="text-left px-2">Importância</th><th className="text-right px-2">Cumprem</th><th /></tr></thead>
              <tbody className="divide-y divide-line">
                {data.rules.map(r => (
                  <tr key={r.id} className={r.enabled ? "" : "opacity-50"}>
                    <td className="px-3 py-1.5"><div className="text-slate-100 flex items-center gap-2">{isAdmin && <Switch checked={r.enabled} onCheckedChange={v => toggle(r, v)} />}{r.name}</div><div className="text-[11px] font-mono text-slate-500 truncate max-w-[32rem]">{r.kind === "must" ? "precisa ter" : "não pode ter"}: {r.pattern}</div></td>
                    <td className="px-2 text-slate-300">{data.vendors[r.vendor] || r.vendor}</td>
                    <td className="px-2"><span className={`text-[10px] px-1.5 rounded border ${(SEVL[r.severity] || SEVL.medium)[1]}`}>{(SEVL[r.severity] || SEVL.medium)[0]}</span></td>
                    <td className={`px-2 text-right ${r.failed ? "text-amber-300" : "text-slate-400"}`}>{r.passed}/{r.passed + r.failed}</td>
                    <td className="px-2 text-right whitespace-nowrap">{isAdmin && <><Button size="sm" variant="ghost" onClick={() => setEdit(r)} className="h-7 text-slate-300"><Pencil className="w-4 h-4" /></Button><Button size="sm" variant="ghost" onClick={() => del(r)} className="h-7 text-red-300"><Trash2 className="w-4 h-4" /></Button></>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        </>
      ) : tab === "firmware" ? <Firmware data={data} isAdmin={isAdmin} reload={load} /> : (
        <>
          <div className="flex gap-2 mb-3">
            <select value={vendor} onChange={e => setVendor(e.target.value)} className={sel} data-testid="comp-vendor"><option value="">todos os fabricantes</option>{[...new Set(data.devices.map(d => d.device_type))].map(v => <option key={v} value={v}>{data.vendors[v] || v}</option>)}</select>
          </div>
          <Card className="bg-surface border-line overflow-x-auto">
            <table className="w-full text-sm" data-testid="comp-devices">
              <thead className="text-xs text-slate-500 bg-panel"><tr><th className="text-left px-3 py-2">Equipamento</th><th className="text-left px-2">Nota</th><th className="text-left px-2">Fora do padrão</th><th className="text-left px-2">Firmware</th></tr></thead>
              <tbody className="divide-y divide-line">
                {devs.length === 0 && <tr><td colSpan={4} className="py-8 text-center text-slate-500">Nenhum equipamento com backup ainda.</td></tr>}
                {devs.map(d => (
                  <tr key={d.device_id} onClick={() => setDetail(d.device_id)} className="hover:bg-white/[0.03] cursor-pointer" data-testid={`comp-dev-${d.device_id}`}>
                    <td className="px-3 py-1.5"><div className="text-slate-100">{d.device_name}</div><div className="text-[11px] text-slate-500">{data.vendors[d.device_type] || d.device_type}</div></td>
                    <td className="px-2"><div className="flex items-center gap-2"><div className="w-20 h-1.5 rounded bg-white/5 overflow-hidden"><div className={`h-full ${scoreCls(d.score)}`} style={{ width: `${d.score || 0}%` }} /></div><span className="text-slate-200 text-xs w-10">{d.score ?? "—"}%</span></div></td>
                    <td className="px-2"><div className="flex flex-wrap gap-1">{d.fails.slice(0, 4).map(f => <span key={f.rule_id} className={`text-[10px] px-1.5 rounded border ${(SEVL[f.severity] || SEVL.medium)[1]}`}>{f.name}</span>)}{d.fails.length > 4 && <span className="text-[10px] text-slate-500">+{d.fails.length - 4}</span>}{!d.fails.length && <span className="text-xs text-emerald-300">tudo ok</span>}</div></td>
                    <td className="px-2"><div className="font-mono text-xs text-slate-300">{d.firmware?.version || "—"}</div><div className={`text-[10px] ${(FW[d.fw_status] || FW.unknown)[1]}`}>{(FW[d.fw_status] || FW.unknown)[0]}</div></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        </>
      )}
      {detail && <DeviceDetail id={detail} onClose={closeDetail} />}
      {edit && <RuleDialog rule={edit.id ? edit : null} vendors={data.vendors} onClose={() => setEdit(null)} onSaved={() => { setEdit(null); setTimeout(load, 1500); }} />}
    </div>
  );
}
