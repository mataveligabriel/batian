import React, { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, formatApiError } from "@/lib/api";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { toast } from "sonner";
import { Send, Plus, Trash2, Loader2, CheckCircle2, XCircle, RefreshCw, Save, FileText } from "lucide-react";

const KINDS = [["syslog", "Syslog (Wazuh, QRadar, Sentinel, rsyslog)"], ["gelf", "GELF (Graylog)"], ["splunk", "Splunk HEC"], ["http", "HTTP/Webhook (Elastic, Logstash…)"]];
const FMT = [["cef", "CEF"], ["json", "JSON"], ["leef", "LEEF (QRadar)"]];
const lbl = "text-xs text-slate-400 mb-1 block";
const sel = "w-full h-9 rounded-md bg-sunken border border-line px-2 text-sm text-slate-200";
const fmt = (s) => (s ? new Date(s).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "—");
const NEW = () => ({ name: "", kind: "syslog", enabled: true, host: "", port: 514, proto: "udp", format: "cef", url: "", header: "", secret: "", categories: ["auth", "session", "command", "change", "incident"], min_severity: 0, verify_tls: true });

function Dest({ d, cats, onChange, onDel, onTest, testing }) {
  const set = (k, v) => onChange({ ...d, [k]: v });
  const net = d.kind === "syslog" || d.kind === "gelf";
  const st = d.status;
  return (
    <Card className="bg-surface border-line p-4 space-y-3" data-testid={`siem-dest-${d.name || "novo"}`}>
      <div className="flex flex-wrap items-center gap-2">
        <Switch checked={d.enabled} onCheckedChange={v => set("enabled", v)} />
        <Input value={d.name} onChange={e => set("name", e.target.value)} placeholder="Nome (ex.: Wazuh)" className="bg-sunken border-line h-9 w-52" data-testid="siem-name" />
        <select value={d.kind} onChange={e => set("kind", e.target.value)} className="h-9 rounded-md bg-sunken border border-line px-2 text-sm text-slate-200 max-w-full" data-testid="siem-kind">{KINDS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
        <div className="ml-auto flex gap-1">
          {d.id && <Button size="sm" variant="outline" onClick={onTest} disabled={testing} className="h-8 border-line bg-transparent" data-testid="siem-test">{testing ? <Loader2 className="w-4 h-4 mr-1 animate-spin" /> : <Send className="w-4 h-4 mr-1" />}Testar</Button>}
          <Button size="sm" variant="ghost" onClick={onDel} className="h-8 text-slate-500 hover:text-red-300"><Trash2 className="w-4 h-4" /></Button>
        </div>
      </div>
      {net ? (
        <div className="grid gap-3 sm:grid-cols-[1fr_7rem_8rem_10rem]">
          <div><label className={lbl}>Servidor</label><Input value={d.host || ""} onChange={e => set("host", e.target.value)} placeholder="192.0.2.50" className="bg-sunken border-line h-9 font-mono" data-testid="siem-host" /></div>
          <div><label className={lbl}>Porta</label><Input type="number" value={d.port || ""} onChange={e => set("port", Number(e.target.value))} className="bg-sunken border-line h-9 font-mono" data-testid="siem-port" /></div>
          <div><label className={lbl}>Transporte</label><select value={d.proto || "udp"} onChange={e => set("proto", e.target.value)} className={sel} data-testid="siem-proto">
            {[["udp", "UDP"], ["tcp", "TCP"], ...(d.kind === "syslog" ? [["tls", "TCP + TLS"]] : [])].map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></div>
          {d.kind === "syslog" && <div><label className={lbl}>Formato da mensagem</label><select value={d.format || "cef"} onChange={e => set("format", e.target.value)} className={sel} data-testid="siem-format">{FMT.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></div>}
        </div>
      ) : (
        <div className="grid gap-3 sm:grid-cols-[1fr_12rem_14rem]">
          <div><label className={lbl}>URL</label><Input value={d.url || ""} onChange={e => set("url", e.target.value)} placeholder={d.kind === "splunk" ? "https://splunk.exemplo.net:8088/services/collector" : "https://logstash.exemplo.net:8080/bastion"} className="bg-sunken border-line h-9 font-mono" data-testid="siem-url" /></div>
          {d.kind === "http" ? <div><label className={lbl}>Cabeçalho da chave</label><Input value={d.header || ""} onChange={e => set("header", e.target.value)} placeholder="Authorization" className="bg-sunken border-line h-9" /></div>
            : <div><label className={lbl}>Índice (opcional)</label><Input value={d.index || ""} onChange={e => set("index", e.target.value)} className="bg-sunken border-line h-9" /></div>}
          <div><label className={lbl}>{d.kind === "splunk" ? "Token HEC" : "Valor do cabeçalho"}{d.has_secret ? " (guardado)" : ""}</label>
            <Input type="password" value={d.secret || ""} onChange={e => set("secret", e.target.value)} placeholder={d.has_secret ? "vazio = manter" : ""} className="bg-sunken border-line h-9" autoComplete="new-password" data-testid="siem-secret" /></div>
        </div>
      )}
      {(d.proto === "tls" || !net) && <label className="flex items-center gap-2 text-xs text-slate-300"><Switch checked={d.verify_tls !== false} onCheckedChange={v => set("verify_tls", v)} /> Conferir o certificado TLS</label>}
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-xs text-slate-400 mr-1">Enviar</span>
        {Object.entries(cats).map(([k, l]) => {
          const on = d.categories.includes(k);
          return <button key={k} type="button" onClick={() => set("categories", on ? d.categories.filter(x => x !== k) : [...d.categories, k])} data-testid={`siem-cat-${k}`}
            className={`h-7 px-2 rounded text-xs border ${on ? "border-brand/60 bg-brand/15 text-brand-soft" : "border-line text-slate-500"}`}>{l}</button>;
        })}
        <span className="text-xs text-slate-400 ml-3">severidade a partir de</span>
        <Input type="number" min={0} max={10} value={d.min_severity} onChange={e => set("min_severity", Number(e.target.value))} className="bg-sunken border-line h-7 w-16 text-xs" />
      </div>
      {st && (
        <div className="text-[11px] font-mono text-slate-500 flex flex-wrap gap-x-4" data-testid="siem-status">
          <span className={st.last_error ? "text-red-300" : "text-emerald-300"}>{st.last_error ? <><XCircle className="w-3 h-3 inline mr-1" />{st.last_error}</> : <><CheckCircle2 className="w-3 h-3 inline mr-1" />último envio {fmt(st.last_ok)}</>}</span>
          <span>{st.sent} enviados</span>{st.queued > 0 && <span className="text-amber-300">{st.queued} na fila</span>}{st.dropped > 0 && <span className="text-red-300">{st.dropped} descartados</span>}
        </div>
      )}
    </Card>
  );
}

function Destinations() {
  const [cfg, setCfg] = useState(null);
  const [busy, setBusy] = useState(false);
  const [testing, setTesting] = useState("");
  const [prev, setPrev] = useState(null);
  const [pf, setPf] = useState("cef");
  const load = useCallback(() => api.get("/siem/settings").then(r => setCfg(r.data)).catch(e => toast.error(formatApiError(e))), []);
  useEffect(() => { load(); }, [load]);
  useEffect(() => { api.get("/siem/preview", { params: { format: pf } }).then(r => setPrev(r.data)).catch(() => {}); }, [pf]);
  const save = async () => {
    setBusy(true);
    try { setCfg((await api.put("/siem/settings", { enabled: cfg.enabled, destinations: cfg.destinations })).data); toast.success("Salvo — os eventos novos saem a cada 10 s"); }
    catch (e) { toast.error(formatApiError(e)); } finally { setBusy(false); }
  };
  const test = async (d) => {
    setTesting(d.id);
    try { await api.post("/siem/test", { id: d.id }); toast.success(`Evento de teste enviado para ${d.name}`); } catch (e) { toast.error(formatApiError(e)); } finally { setTesting(""); }
  };
  if (!cfg) return null;
  const setD = (i, v) => setCfg({ ...cfg, destinations: cfg.destinations.map((x, j) => (j === i ? v : x)) });
  return (
    <div className="space-y-4" data-testid="siem-dests">
      <Card className="bg-surface border-line p-4 flex flex-wrap items-center gap-3">
        <label className="flex items-center gap-2 text-sm text-slate-200"><Switch checked={cfg.enabled} onCheckedChange={v => setCfg({ ...cfg, enabled: v })} data-testid="siem-enabled" /> Enviar eventos</label>
        <span className="text-xs text-slate-400 flex-1">Logins, sessões de acesso, comandos pegos pela política, ações do assistente, alterações feitas na interface e incidentes. Só os eventos novos (não reenvia o histórico).</span>
        <Button size="sm" variant="outline" onClick={() => setCfg({ ...cfg, destinations: [...cfg.destinations, NEW()] })} className="border-line bg-transparent" data-testid="siem-add"><Plus className="w-4 h-4 mr-1" />Destino</Button>
        <Button size="sm" onClick={save} disabled={busy} className="bg-brand hover:bg-brand-strong text-onbrand" data-testid="siem-save">{busy ? <Loader2 className="w-4 h-4 mr-1 animate-spin" /> : <Save className="w-4 h-4 mr-1" />}Salvar</Button>
        <Button size="sm" variant="ghost" onClick={load} className="text-slate-400" title="Atualizar status"><RefreshCw className="w-4 h-4" /></Button>
      </Card>
      {cfg.destinations.length === 0 && <Card className="bg-surface border-line p-6 text-sm text-slate-500 text-center">Nenhum destino. Adicione o seu SIEM: Wazuh e QRadar recebem syslog; Graylog, GELF; Splunk, HEC; Elastic/Logstash, HTTP.</Card>}
      {cfg.destinations.map((d, i) => <Dest key={d.id || `n${i}`} d={d} cats={cfg.categories} onChange={v => setD(i, v)} testing={testing === d.id}
        onDel={() => setCfg({ ...cfg, destinations: cfg.destinations.filter((_, j) => j !== i) })} onTest={() => test(d)} />)}
      <Card className="bg-surface border-line p-4 space-y-2" data-testid="siem-preview">
        <div className="flex items-center gap-2 text-sm text-slate-200"><FileText className="w-4 h-4 text-brand-soft" />Como chega no SIEM
          <select value={pf} onChange={e => setPf(e.target.value)} className="h-7 rounded-md bg-sunken border border-line px-2 text-xs text-slate-200 ml-2">{FMT.map(([k, l]) => <option key={k} value={k}>syslog + {l}</option>)}</select></div>
        <pre className="text-[11px] font-mono bg-sunken border border-line rounded p-2 text-slate-300 whitespace-pre-wrap break-all m-0">{(prev?.lines || []).join("\n")}</pre>
        <div className="text-[11px] text-slate-500">Facility local4. Campos: categoria, ação, resultado (success/failure), severidade 0–10, usuário, IP de origem, equipamento e detalhes (comandos, regra, canal…).</div>
      </Card>
    </div>
  );
}

function Audit() {
  const [rows, setRows] = useState(null);
  const [q, setQ] = useState("");
  const load = useCallback(() => api.get("/soc/audit", { params: { q } }).then(r => setRows(r.data)).catch(e => toast.error(formatApiError(e))), [q]);
  useEffect(() => { const t = setTimeout(load, 300); return () => clearTimeout(t); }, [load]);
  return (
    <Card className="bg-surface border-line" data-testid="siem-audit">
      <div className="flex flex-wrap gap-2 px-4 py-3 border-b border-line items-center">
        <Input value={q} onChange={e => setQ(e.target.value)} placeholder="usuário, caminho ou IP" className="bg-sunken border-line h-9 w-72" data-testid="siem-audit-q" />
        <span className="text-xs text-slate-500">Toda alteração feita pela interface/API (criar, editar, apagar, aplicar), guardada por 180 dias.</span>
      </div>
      {rows && rows.length === 0 && <div className="p-6 text-sm text-slate-500 text-center">Nada registrado ainda.</div>}
      <div className="overflow-x-auto"><table className="w-full text-sm"><tbody className="divide-y divide-line">
        {(rows || []).map(r => (
          <tr key={r.id}>
            <td className="px-4 py-1.5 text-xs text-slate-500 whitespace-nowrap">{fmt(r.at)}</td>
            <td className="px-2 text-slate-300 whitespace-nowrap">{r.user_email}</td>
            <td className="px-2 font-mono text-xs whitespace-nowrap"><span className={r.method === "DELETE" ? "text-red-300" : "text-sky-300"}>{r.method}</span> <span className="text-slate-200">{r.path}</span></td>
            <td className={`px-2 font-mono text-xs ${r.status >= 400 ? "text-red-300" : "text-slate-500"}`}>{r.status}</td>
            <td className="px-2 font-mono text-xs text-slate-500">{r.ip}</td>
          </tr>
        ))}
      </tbody></table></div>
    </Card>
  );
}

export default function Siem() {
  const [sp, setSp] = useSearchParams();
  const tab = sp.get("tab") || "";
  return (
    <div className="p-4 md:p-6 flex-1 overflow-y-auto" data-testid="siem-page">
      <div className="flex items-start justify-between flex-wrap gap-3 mb-4">
        <div>
          <h1 className="font-heading text-2xl sm:text-[1.75rem] font-semibold tracking-tight text-slate-100 flex items-center gap-2"><Send className="w-6 h-6 text-brand-soft" /> SIEM e auditoria</h1>
          <p className="text-sm text-slate-400 mt-1">Os eventos de segurança do BastiON no seu SIEM, em syslog (CEF/JSON/LEEF), GELF, Splunk HEC ou HTTP.</p>
        </div>
        <div className="flex gap-1 p-1 bg-sunken border border-line rounded-md text-sm">
          {[["", "Destinos"], ["auditoria", "Auditoria da interface"]].map(([k, l]) => <button key={k} onClick={() => setSp(k ? { tab: k } : {})} className={`px-3 h-7 rounded ${tab === k ? "bg-brand/20 text-brand-soft" : "text-slate-400 hover:text-slate-200"}`} data-testid={`siem-tab-${k || "dest"}`}>{l}</button>)}
        </div>
      </div>
      {tab === "auditoria" ? <Audit /> : <Destinations />}
    </div>
  );
}
