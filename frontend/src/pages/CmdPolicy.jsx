import React, { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, formatApiError } from "@/lib/api";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { toast } from "sonner";
import { ShieldCheck, Plus, Pencil, Trash2, RotateCcw, FlaskConical, CheckCircle2, XCircle, Loader2, ArrowUp, ArrowDown, RefreshCw, Clock } from "lucide-react";
import { useAuth } from "@/context/AuthContext";

const ACT = {
  block: ["Bloquear", "text-red-300 border-red-500/40 bg-red-500/10"],
  approve: ["Pedir aprovação", "text-amber-300 border-amber-500/40 bg-amber-500/10"],
  alert: ["Alertar", "text-sky-300 border-sky-500/40 bg-sky-500/10"],
  allow: ["Permitir", "text-emerald-300 border-emerald-500/40 bg-emerald-500/10"],
};
const EVT = { block: ["bloqueado", "text-red-300"], alert: ["alerta", "text-sky-300"], "approval-requested": ["pediu aprovação", "text-amber-300"],
  approved: ["aprovado", "text-emerald-300"], denied: ["negado", "text-red-300"], "approved-run": ["executado com aprovação", "text-emerald-300"] };
const STATUS = { pending: ["aguardando", "text-amber-300"], approved: ["aprovado", "text-emerald-300"], denied: ["negado", "text-red-300"],
  cancelled: ["cancelado", "text-slate-400"], expired: ["expirou", "text-slate-500"] };
const TYPES = ["huawei", "juniper", "cisco", "datacom", "zte", "fiberhome", "mikrotik", "linux", "ubiquiti", "other"];
const CHANNELS = [["terminal", "Terminal"], ["batch", "Lote"], ["runbook", "Roteiros"], ["olt", "Script de OLT"], ["ai", "Assistente IA"]];
const ROLES = [["admin", "Admin"], ["operator", "Operador"]];
const fmt = (s) => (s ? new Date(s).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—");
const Badge = ({ a }) => <span className={`text-[11px] px-1.5 py-0.5 rounded border whitespace-nowrap ${(ACT[a] || ACT.alert)[1]}`}>{(ACT[a] || ACT.alert)[0]}</span>;
const lbl = "text-xs text-slate-400 mb-1 block";
const csv = (v) => String(v || "").split(/[,;\s]+/).map(x => x.trim()).filter(Boolean);

function Chips({ options, value, onChange, testid }) {
  return (
    <div className="flex flex-wrap gap-1" data-testid={testid}>
      {options.map(([k, l]) => {
        const on = value.includes(k);
        return <button key={k} type="button" onClick={() => onChange(on ? value.filter(x => x !== k) : [...value, k])}
          className={`h-7 px-2 rounded text-xs border ${on ? "border-brand/60 bg-brand/15 text-brand-soft" : "border-line text-slate-400 hover:text-slate-200"}`}>{l}</button>;
      })}
    </div>
  );
}

function RuleDialog({ rule, users, onClose, onSaved }) {
  const [f, setF] = useState({ name: "", pattern: "", action: "approve", device_types: [], tags: [], device_ids: [], roles: [], user_ids: [], channels: [],
    severity: "medium", enabled: true, description: "", ...(rule || {}) });
  const [tags, setTags] = useState((rule?.tags || []).join(", "));
  const [sample, setSample] = useState("");
  const [busy, setBusy] = useState(false);
  const set = (k, v) => setF(p => ({ ...p, [k]: v }));
  let rxOk = true, hit = null;
  try { const rx = new RegExp(f.pattern, "i"); hit = sample ? rx.test(sample.trim().replace(/\s+/g, " ")) : null; } catch { rxOk = false; }
  const save = async () => {
    setBusy(true);
    const body = { ...f, tags: csv(tags) };
    try { if (rule?.id) await api.put(`/cmdpolicy/rules/${rule.id}`, body); else await api.post("/cmdpolicy/rules", body); toast.success("Regra salva"); onSaved(); }
    catch (e) { toast.error(formatApiError(e)); } finally { setBusy(false); }
  };
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="bg-surface border-line text-slate-100 max-w-2xl w-[96vw] max-h-[90vh] overflow-y-auto" data-testid="cp-rule-dialog">
        <DialogHeader><DialogTitle>{rule?.id ? "Editar regra" : "Nova regra"}</DialogTitle></DialogHeader>
        <div className="space-y-3">
          <div className="grid sm:grid-cols-[1fr_12rem] gap-3">
            <div><label className={lbl}>Nome</label><Input value={f.name} onChange={e => set("name", e.target.value)} className="bg-sunken border-line" data-testid="cp-rule-name" /></div>
            <div><label className={lbl}>Ação</label>
              <select value={f.action} onChange={e => set("action", e.target.value)} className="w-full h-9 rounded-md bg-sunken border border-line px-2 text-sm text-slate-200" data-testid="cp-rule-action">
                {Object.entries(ACT).map(([k, [l]]) => <option key={k} value={k}>{l}</option>)}
              </select></div>
          </div>
          <div>
            <label className={lbl}>Comando (expressão regular, sem diferenciar maiúsculas; espaços repetidos viram um)</label>
            <Input value={f.pattern} onChange={e => set("pattern", e.target.value)} placeholder="^(reload|reboot)\b" className={`bg-sunken font-mono ${rxOk ? "border-line" : "border-red-500"}`} data-testid="cp-rule-pattern" />
            <div className="flex items-center gap-2 mt-1.5">
              <Input value={sample} onChange={e => setSample(e.target.value)} placeholder="teste: digite um comando" className="bg-sunken border-line font-mono h-8 text-xs" data-testid="cp-rule-sample" />
              {hit !== null && <span className={`text-xs whitespace-nowrap ${hit ? "text-emerald-300" : "text-slate-500"}`} data-testid="cp-rule-hit">{hit ? "casa" : "não casa"}</span>}
            </div>
          </div>
          <div><label className={lbl}>Vale para (vazio = todos)</label>
            <div className="space-y-2 rounded-md border border-line p-3">
              <div><div className="text-[11px] text-slate-500 mb-1">Fabricantes</div><Chips options={TYPES.map(t => [t, t])} value={f.device_types} onChange={v => set("device_types", v)} testid="cp-rule-types" /></div>
              <div><div className="text-[11px] text-slate-500 mb-1">Tags dos equipamentos (qualquer uma)</div><Input value={tags} onChange={e => setTags(e.target.value)} placeholder="borda, core" className="bg-sunken border-line h-8" data-testid="cp-rule-tags" /></div>
              <div><div className="text-[11px] text-slate-500 mb-1">Perfis</div><Chips options={ROLES} value={f.roles} onChange={v => set("roles", v)} testid="cp-rule-roles" /></div>
              <div><div className="text-[11px] text-slate-500 mb-1">Usuários</div><Chips options={users.map(u => [u.id, u.email])} value={f.user_ids} onChange={v => set("user_ids", v)} testid="cp-rule-users" /></div>
              <div><div className="text-[11px] text-slate-500 mb-1">Onde</div><Chips options={CHANNELS} value={f.channels} onChange={v => set("channels", v)} testid="cp-rule-channels" /></div>
            </div>
          </div>
          <div className="grid sm:grid-cols-[12rem_1fr] gap-3">
            <div><label className={lbl}>Severidade (incidente no SOC)</label>
              <select value={f.severity} onChange={e => set("severity", e.target.value)} className="w-full h-9 rounded-md bg-sunken border border-line px-2 text-sm text-slate-200">
                {[["info", "Info"], ["low", "Baixa"], ["medium", "Média"], ["high", "Alta"], ["critical", "Crítica"]].map(([k, l]) => <option key={k} value={k}>{l}</option>)}
              </select></div>
            <div><label className={lbl}>Descrição</label><Input value={f.description} onChange={e => set("description", e.target.value)} className="bg-sunken border-line" /></div>
          </div>
          <label className="flex items-center gap-2 text-sm text-slate-300"><Switch checked={f.enabled} onCheckedChange={v => set("enabled", v)} /> Ativa</label>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>Cancelar</Button>
          <Button onClick={save} disabled={busy || !rxOk || !f.pattern} className="bg-brand hover:bg-brand-strong text-onbrand" data-testid="cp-rule-save">{busy && <Loader2 className="w-4 h-4 mr-1.5 animate-spin" />}Salvar</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function Approvals({ isAdmin, me }) {
  const [rows, setRows] = useState(null);
  const [only, setOnly] = useState("pending");
  const [note, setNote] = useState({});
  const load = useCallback(() => api.get("/cmdpolicy/approvals", { params: { status: only } }).then(r => setRows(r.data)).catch(e => toast.error(formatApiError(e))), [only]);
  useEffect(() => { load(); const t = setInterval(load, 8000); return () => clearInterval(t); }, [load]);
  const decide = async (a, approve) => {
    try { await api.post(`/cmdpolicy/approvals/${a.id}/decide`, { approve, note: note[a.id] || "" }); toast.success(approve ? "Aprovado" : "Negado"); load(); }
    catch (e) { toast.error(formatApiError(e)); }
  };
  const cancel = async (a) => { try { await api.post(`/cmdpolicy/approvals/${a.id}/cancel`); load(); } catch (e) { toast.error(formatApiError(e)); } };
  return (
    <div className="space-y-3" data-testid="cp-approvals">
      <div className="flex gap-1 p-1 bg-sunken border border-line rounded-md text-xs w-fit">
        {[["pending", "Aguardando"], ["", "Todos"]].map(([k, l]) => <button key={k} onClick={() => setOnly(k)} className={`px-3 h-7 rounded ${only === k ? "bg-brand/20 text-brand-soft" : "text-slate-400"}`} data-testid={`cp-ap-${k || "all"}`}>{l}</button>)}
      </div>
      {rows && rows.length === 0 && <Card className="bg-surface border-line p-6 text-sm text-slate-500 text-center">{only === "pending" ? "Nenhum pedido aguardando." : "Nenhum pedido ainda."}</Card>}
      {(rows || []).map(a => {
        const [sl, sc] = STATUS[a.status] || STATUS.pending;
        const mine = a.user_id === me?.id;
        return (
          <Card key={a.id} className={`bg-surface border-line p-4 space-y-2 ${a.status === "pending" ? "border-amber-500/40" : ""}`} data-testid={`cp-ap-row-${a.number}`}>
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
              <span className="font-mono text-slate-400">#{a.number}</span>
              <span className="text-slate-100 font-medium">{a.device_name || "—"}</span>
              <span className="text-slate-400">{a.user_email}</span>
              <span className="text-xs text-slate-500">{CHANNELS.find(c => c[0] === a.channel)?.[1] || a.channel} · {fmt(a.created_at)}</span>
              <span className={`ml-auto text-xs ${sc}`}>{sl}{a.decided_by ? ` por ${a.decided_by}` : ""}{a.status === "approved" && a.channel !== "terminal" ? (a.used ? " · usado" : ` · vale até ${fmt(a.grant_until)}`) : ""}</span>
            </div>
            <pre className="text-xs font-mono bg-sunken border border-line rounded px-3 py-2 text-slate-200 whitespace-pre-wrap m-0">{a.commands.join("\n")}</pre>
            <div className="text-[11px] text-slate-500">Regra: {a.rules.join(", ")}{a.note ? ` · obs.: ${a.note}` : ""}{a.status === "pending" ? <> · <Clock className="w-3 h-3 inline" /> até {fmt(a.expires_at)}</> : ""}</div>
            {a.status === "pending" && (
              <div className="flex flex-wrap gap-2 items-center">
                {isAdmin && !mine && <>
                  <Input value={note[a.id] || ""} onChange={e => setNote(n => ({ ...n, [a.id]: e.target.value }))} placeholder="observação (opcional)" className="bg-sunken border-line h-8 w-60 text-xs" />
                  <Button size="sm" onClick={() => decide(a, true)} className="h-8 bg-emerald-700 hover:bg-emerald-600" data-testid={`cp-ap-ok-${a.number}`}><CheckCircle2 className="w-4 h-4 mr-1" />Aprovar</Button>
                  <Button size="sm" onClick={() => decide(a, false)} className="h-8 bg-red-700 hover:bg-red-600" data-testid={`cp-ap-no-${a.number}`}><XCircle className="w-4 h-4 mr-1" />Negar</Button>
                </>}
                {mine && <Button size="sm" variant="ghost" onClick={() => cancel(a)} className="h-8 text-slate-400" data-testid={`cp-ap-cancel-${a.number}`}>Cancelar meu pedido</Button>}
                {isAdmin && mine && <span className="text-[11px] text-slate-500">Outro administrador precisa aprovar.</span>}
              </div>
            )}
          </Card>
        );
      })}
    </div>
  );
}

function Rules() {
  const [rules, setRules] = useState(null);
  const [users, setUsers] = useState([]);
  const [devices, setDevices] = useState([]);
  const [edit, setEdit] = useState(null);
  const [cfg, setCfg] = useState(null);
  const [test, setTest] = useState({ commands: "reboot\ndisplay version", device_id: "", user_id: "", channel: "terminal" });
  const [res, setRes] = useState(null);
  const load = useCallback(() => api.get("/cmdpolicy/rules").then(r => setRules(r.data)).catch(e => toast.error(formatApiError(e))), []);
  useEffect(() => {
    load();
    api.get("/cmdpolicy/settings").then(r => setCfg(r.data)).catch(() => {});
    api.get("/users").then(r => setUsers(r.data || [])).catch(() => {});
    api.get("/devices").then(r => setDevices(r.data || [])).catch(() => {});
  }, [load]);
  const presets = async () => { try { const r = await api.post("/cmdpolicy/rules/presets"); toast.success(`${r.data.added} regra(s) pronta(s) adicionada(s)`); load(); } catch (e) { toast.error(formatApiError(e)); } };
  const del = async (r) => { if (!window.confirm(`Apagar a regra "${r.name}"?`)) return; try { await api.delete(`/cmdpolicy/rules/${r.id}`); load(); } catch (e) { toast.error(formatApiError(e)); } };
  const toggle = async (r, v) => { try { await api.put(`/cmdpolicy/rules/${r.id}`, { ...r, enabled: v }); load(); } catch (e) { toast.error(formatApiError(e)); } };
  const move = async (i, d) => {
    const ids = rules.map(r => r.id); const j = i + d; if (j < 0 || j >= ids.length) return;
    [ids[i], ids[j]] = [ids[j], ids[i]];
    try { await api.put("/cmdpolicy/rules-order", { ids }); load(); } catch (e) { toast.error(formatApiError(e)); }
  };
  const saveCfg = async (patch) => { const n = { ...cfg, ...patch }; setCfg(n); try { setCfg((await api.put("/cmdpolicy/settings", n)).data); } catch (e) { toast.error(formatApiError(e)); } };
  const runTest = async () => { try { setRes((await api.post("/cmdpolicy/test", test)).data.results); } catch (e) { toast.error(formatApiError(e)); } };
  const scope = (r) => [r.device_types.join("/"), r.tags.length ? `tags ${r.tags.join(",")}` : "", r.roles.map(x => ROLES.find(y => y[0] === x)?.[1] || x).join("/"),
    r.user_ids.length ? `${r.user_ids.length} usuário(s)` : "", r.channels.map(c => CHANNELS.find(x => x[0] === c)?.[1]).join("/")].filter(Boolean).join(" · ") || "todos";
  return (
    <div className="space-y-4" data-testid="cp-rules">
      {cfg && (
        <Card className="bg-surface border-line p-4 grid gap-3 md:grid-cols-[auto_1fr_1fr_auto] items-end" data-testid="cp-settings">
          <label className="flex items-center gap-2 text-sm text-slate-200"><Switch checked={cfg.enabled} onCheckedChange={v => saveCfg({ enabled: v })} data-testid="cp-enabled" /> Política ativa</label>
          <div><label className={lbl}>Pedido espera resposta por (min)</label><Input type="number" value={cfg.wait_min} onChange={e => setCfg({ ...cfg, wait_min: e.target.value })} onBlur={() => saveCfg({ wait_min: Number(cfg.wait_min) })} className="bg-sunken border-line h-9" /></div>
          <div><label className={lbl}>Aprovação vale por (min, lote/roteiro/IA)</label><Input type="number" value={cfg.grant_min} onChange={e => setCfg({ ...cfg, grant_min: e.target.value })} onBlur={() => saveCfg({ grant_min: Number(cfg.grant_min) })} className="bg-sunken border-line h-9" /></div>
          <div className="space-y-1.5">
            <label className="flex items-center gap-2 text-xs text-slate-300"><Switch checked={cfg.notify} onCheckedChange={v => saveCfg({ notify: v })} /> Avisar no Telegram/push</label>
            <label className="flex items-center gap-2 text-xs text-slate-300" title="Só se você for o único administrador"><Switch checked={cfg.self_approve} onCheckedChange={v => saveCfg({ self_approve: v })} data-testid="cp-self" /> Admin aprova o próprio pedido</label>
          </div>
        </Card>
      )}
      <Card className="bg-surface border-line">
        <div className="flex flex-wrap items-center gap-2 px-4 py-3 border-b border-line">
          <div className="text-sm text-slate-300 flex-1 min-w-[14rem]">Em ordem: entre as regras que casam, a mais restritiva vale. <b>Permitir</b> abre exceção para as regras de baixo.</div>
          <Button size="sm" variant="ghost" onClick={presets} className="text-slate-300" data-testid="cp-presets"><RotateCcw className="w-4 h-4 mr-1.5" />Regras prontas</Button>
          <Button size="sm" onClick={() => setEdit({})} className="bg-brand hover:bg-brand-strong text-onbrand" data-testid="cp-new"><Plus className="w-4 h-4 mr-1.5" />Nova regra</Button>
        </div>
        {rules && rules.length === 0 && <div className="p-6 text-sm text-slate-500 text-center">Nenhuma regra. Comece pelas <b>regras prontas</b>: bloquear apagar a configuração, pedir aprovação para reboot e BGP, alertar porta desligada e usuários.</div>}
        <div className="divide-y divide-line">
          {(rules || []).map((r, i) => (
            <div key={r.id} className={`flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2.5 ${r.enabled ? "" : "opacity-50"}`} data-testid={`cp-rule-${i}`}>
              <div className="flex flex-col"><button onClick={() => move(i, -1)} className="text-slate-500 hover:text-slate-200"><ArrowUp className="w-3.5 h-3.5" /></button><button onClick={() => move(i, 1)} className="text-slate-500 hover:text-slate-200"><ArrowDown className="w-3.5 h-3.5" /></button></div>
              <Badge a={r.action} />
              <div className="min-w-0 flex-1">
                <div className="text-sm text-slate-100">{r.name}</div>
                <div className="text-[11px] font-mono text-slate-500 truncate" title={r.pattern}>{r.pattern}</div>
              </div>
              <div className="text-xs text-slate-400 max-w-[18rem] truncate" title={scope(r)}>{scope(r)}</div>
              <Switch checked={r.enabled} onCheckedChange={v => toggle(r, v)} />
              <button onClick={() => setEdit(r)} className="text-slate-400 hover:text-slate-200" data-testid={`cp-rule-edit-${i}`}><Pencil className="w-4 h-4" /></button>
              <button onClick={() => del(r)} className="text-slate-500 hover:text-red-300"><Trash2 className="w-4 h-4" /></button>
            </div>
          ))}
        </div>
      </Card>
      <Card className="bg-surface border-line p-4 space-y-3" data-testid="cp-test">
        <div className="text-sm font-semibold text-slate-200 flex items-center gap-2"><FlaskConical className="w-4 h-4 text-brand-soft" />Simular (não registra nada)</div>
        <div className="grid gap-3 md:grid-cols-[1fr_14rem]">
          <textarea value={test.commands} onChange={e => setTest({ ...test, commands: e.target.value })} rows={4} className="rounded-md bg-sunken border border-line p-2 font-mono text-sm text-slate-200" data-testid="cp-test-cmds" />
          <div className="space-y-2">
            <select value={test.user_id} onChange={e => setTest({ ...test, user_id: e.target.value })} className="w-full h-9 rounded-md bg-sunken border border-line px-2 text-sm text-slate-200"><option value="">como eu</option>{users.map(u => <option key={u.id} value={u.id}>{u.email}</option>)}</select>
            <select value={test.device_id} onChange={e => setTest({ ...test, device_id: e.target.value })} className="w-full h-9 rounded-md bg-sunken border border-line px-2 text-sm text-slate-200"><option value="">qualquer equipamento</option>{devices.map(d => <option key={d.id} value={d.id}>{d.name}</option>)}</select>
            <select value={test.channel} onChange={e => setTest({ ...test, channel: e.target.value })} className="w-full h-9 rounded-md bg-sunken border border-line px-2 text-sm text-slate-200">{CHANNELS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
            <Button size="sm" onClick={runTest} variant="outline" className="w-full border-line bg-transparent" data-testid="cp-test-run">Simular</Button>
          </div>
        </div>
        {res && <div className="divide-y divide-line border border-line rounded-md" data-testid="cp-test-res">{res.map((x, i) => (
          <div key={i} className="flex items-center gap-3 px-3 py-1.5 text-sm"><Badge a={x.action} /><span className="font-mono text-slate-200 flex-1 truncate">{x.command}</span><span className="text-xs text-slate-500">{x.rule || "nenhuma regra"}</span></div>))}</div>}
      </Card>
      {edit && <RuleDialog rule={edit.id ? edit : null} users={users} onClose={() => setEdit(null)} onSaved={() => { setEdit(null); load(); }} />}
    </div>
  );
}

function Events() {
  const [rows, setRows] = useState(null);
  const [q, setQ] = useState("");
  const [action, setAction] = useState("");
  const load = useCallback(() => api.get("/cmdpolicy/events", { params: { q, action } }).then(r => setRows(r.data)).catch(e => toast.error(formatApiError(e))), [q, action]);
  useEffect(() => { const t = setTimeout(load, 300); return () => clearTimeout(t); }, [load]);
  return (
    <Card className="bg-surface border-line" data-testid="cp-events">
      <div className="flex flex-wrap gap-2 px-4 py-3 border-b border-line">
        <Input value={q} onChange={e => setQ(e.target.value)} placeholder="usuário, equipamento ou comando" className="bg-sunken border-line h-9 w-72" data-testid="cp-ev-q" />
        <select value={action} onChange={e => setAction(e.target.value)} className="h-9 rounded-md bg-sunken border border-line px-2 text-sm text-slate-200">
          <option value="">todos</option>{Object.entries(EVT).map(([k, [l]]) => <option key={k} value={k}>{l}</option>)}
        </select>
        <Button size="sm" variant="ghost" onClick={load} className="text-slate-300"><RefreshCw className="w-4 h-4" /></Button>
      </div>
      {rows && rows.length === 0 && <div className="p-6 text-sm text-slate-500 text-center">Nada registrado.</div>}
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <tbody className="divide-y divide-line">
            {(rows || []).map(e => (
              <tr key={e.id}>
                <td className="px-4 py-2 text-xs text-slate-500 whitespace-nowrap">{fmt(e.at)}</td>
                <td className={`px-2 text-xs whitespace-nowrap ${(EVT[e.action] || ["", "text-slate-300"])[1]}`}>{(EVT[e.action] || [e.action])[0]}</td>
                <td className="px-2 text-slate-300 whitespace-nowrap">{e.user_email}</td>
                <td className="px-2 text-slate-300 whitespace-nowrap">{e.device_name}</td>
                <td className="px-2 font-mono text-xs text-slate-200 max-w-[28rem] truncate" title={e.commands.join("\n")}>{e.commands.join(" | ")}</td>
                <td className="px-2 text-xs text-slate-500 whitespace-nowrap">{CHANNELS.find(c => c[0] === e.channel)?.[1] || e.channel}{e.decided_by ? ` · ${e.decided_by}` : ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

export default function CmdPolicy() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const [sp, setSp] = useSearchParams();
  const tab = isAdmin ? sp.get("tab") || "" : "";
  return (
    <div className="p-4 md:p-6 flex-1 overflow-y-auto" data-testid="cp-page">
      <div className="flex items-start justify-between flex-wrap gap-3 mb-4">
        <div>
          <h1 className="font-heading text-2xl sm:text-[1.75rem] font-semibold tracking-tight text-slate-100 flex items-center gap-2"><ShieldCheck className="w-6 h-6 text-brand-soft" /> Política de comandos</h1>
          <p className="text-sm text-slate-400 mt-1 max-w-3xl">Antes de chegar ao equipamento — terminal, lote, roteiros, script de OLT e assistente — cada comando passa pelas regras: bloquear, pedir aprovação de outro administrador ou alertar o SOC.</p>
        </div>
        {isAdmin && (
          <div className="flex gap-1 p-1 bg-sunken border border-line rounded-md text-sm">
            {[["", "Aprovações"], ["regras", "Regras"], ["eventos", "Eventos"]].map(([k, l]) => <button key={k} onClick={() => setSp(k ? { tab: k } : {})} className={`px-3 h-7 rounded ${tab === k ? "bg-brand/20 text-brand-soft" : "text-slate-400 hover:text-slate-200"}`} data-testid={`cp-tab-${k || "ap"}`}>{l}</button>)}
          </div>
        )}
      </div>
      {tab === "regras" ? <Rules /> : tab === "eventos" ? <Events /> : <Approvals isAdmin={isAdmin} me={user} />}
    </div>
  );
}
