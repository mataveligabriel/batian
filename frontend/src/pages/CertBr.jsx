import React, { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, formatApiError } from "@/lib/api";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { toast } from "sonner";
import { ShieldAlert, CheckCircle2, EyeOff, RotateCcw, Loader2, Save, Copy, Upload, Waves, Mail, AlertTriangle, ClipboardCheck } from "lucide-react";

const fmt = (s) => (s ? new Date(s).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", year: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—");
const bytes = (b) => (b >= 1e9 ? `${(b / 1e9).toFixed(1)} GB` : b >= 1e6 ? `${(b / 1e6).toFixed(1)} MB` : b >= 1e3 ? `${(b / 1e3).toFixed(0)} kB` : `${b || 0} B`);
const lbl = "text-xs text-slate-400 mb-1 block";
const SRC = { flow: [Waves, "flow", "text-sky-300 border-sky-500/40"], certbr: [Mail, "aviso CERT.br", "text-amber-300 border-amber-500/40"] };

function Items({ services }) {
  const [rows, setRows] = useState(null);
  const [only, setOnly] = useState("open");
  const [open, setOpen] = useState("");
  const load = useCallback(() => api.get("/soc/certbr/items", { params: { status: only } }).then(r => setRows(r.data)).catch(e => toast.error(formatApiError(e))), [only]);
  useEffect(() => { load(); }, [load]);
  const act = async (it, action) => { try { await api.post(`/soc/certbr/items/${it.id}`, { action }); load(); } catch (e) { toast.error(formatApiError(e)); } };
  const bySvc = (rows || []).reduce((a, r) => ({ ...a, [r.service]: (a[r.service] || 0) + 1 }), {});
  return (
    <div className="space-y-3" data-testid="cert-items">
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex gap-1 p-1 bg-sunken border border-line rounded-md text-xs">
          {[["open", "Abertos"], ["resolved", "Resolvidos"], ["ignored", "Ignorados"], ["", "Todos"]].map(([k, l]) => <button key={k} onClick={() => setOnly(k)} className={`px-3 h-7 rounded ${only === k ? "bg-brand/20 text-brand-soft" : "text-slate-400"}`} data-testid={`cert-f-${k || "all"}`}>{l}</button>)}
        </div>
        {Object.entries(bySvc).map(([k, n]) => <span key={k} className="text-xs px-2 py-1 rounded border border-line text-slate-300">{services?.[k]?.name || k}: <b>{n}</b></span>)}
      </div>
      {rows && rows.length === 0 && <Card className="bg-surface border-line p-6 text-sm text-slate-500 text-center">{only === "open" ? "Nenhum serviço exposto em aberto." : "Nada aqui."}</Card>}
      <Card className="bg-surface border-line divide-y divide-line">
        {(rows || []).map(it => {
          const svc = services?.[it.service];
          return (
            <div key={it.id} className="px-4 py-2.5 space-y-1.5" data-testid={`cert-row-${it.ip}`}>
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                <button onClick={() => setOpen(open === it.id ? "" : it.id)} className="font-mono text-slate-100 hover:underline">{it.ip}</button>
                <span className="text-sm text-amber-200">{svc?.name || it.service}{svc ? <span className="text-slate-500 font-mono text-xs"> udp/{svc.port}</span> : ""}</span>
                {it.sources.map(s => { const [I, l, c] = SRC[s] || [AlertTriangle, s, "text-slate-300 border-line"]; return <span key={s} className={`inline-flex items-center gap-1 text-[11px] px-1.5 py-0.5 rounded border ${c}`}><I className="w-3 h-3" />{l}</span>; })}
                {it.reopened > 0 && <span className="text-[11px] text-red-300">voltou {it.reopened}x</span>}
                <span className="text-xs text-slate-500">{it.owner || "sem prefixo no IPAM"}</span>
                <span className="ml-auto text-xs text-slate-500">desde {fmt(it.first)} · último {fmt(it.last)}</span>
                {it.status === "open" ? <>
                  <Button size="sm" variant="outline" onClick={() => act(it, "resolve")} className="h-7 border-line bg-transparent text-xs" data-testid={`cert-res-${it.ip}`}><CheckCircle2 className="w-3.5 h-3.5 mr-1 text-emerald-300" />Resolvido</Button>
                  <Button size="sm" variant="ghost" onClick={() => act(it, "ignore")} className="h-7 text-xs text-slate-400" title="Ex.: DNS autoritativo, NTP público de propósito"><EyeOff className="w-3.5 h-3.5 mr-1" />Ignorar</Button>
                </> : <Button size="sm" variant="ghost" onClick={() => act(it, "reopen")} className="h-7 text-xs text-slate-400"><RotateCcw className="w-3.5 h-3.5 mr-1" />Reabrir</Button>}
              </div>
              {open === it.id && (
                <div className="text-xs text-slate-300 space-y-1.5 bg-sunken/60 border border-line rounded p-3" data-testid="cert-detail">
                  {svc?.fix && <div><b className="text-slate-200">Como corrigir:</b> {svc.fix}</div>}
                  {it.flow && <div className="text-slate-400">Flow ({it.flow.day}): respondeu para {it.flow.remotes} destino(s) de fora em {it.flow.minutes} min, {bytes(it.flow.bytes)}. Ex.: <span className="font-mono">{(it.flow.sample || []).slice(0, 5).join(", ")}</span></div>}
                  {it.notices?.length > 0 && <div className="text-slate-400">{it.notices.length} aviso(s): {it.notices.slice(-3).map((n, i) => <div key={i} className="font-mono text-[11px] text-slate-500 truncate">{fmt(n.at)} · {n.subject} · {n.line}</div>)}</div>}
                  {it.resolved_by && <div className="text-slate-500">Fechado por {it.resolved_by} em {fmt(it.resolved_at)}{it.note ? ` — ${it.note}` : ""}</div>}
                </div>
              )}
            </div>
          );
        })}
      </Card>
    </div>
  );
}

function Import() {
  const [subject, setSubject] = useState("");
  const [text, setText] = useState("");
  const [prev, setPrev] = useState(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!text.trim()) { setPrev(null); return undefined; }
    const t = setTimeout(() => api.post("/soc/certbr/import", { text, subject, preview: true }).then(r => setPrev(r.data.items)).catch(() => {}), 400);
    return () => clearTimeout(t);
  }, [text, subject]);
  const go = async () => {
    setBusy(true);
    try {
      const { data } = await api.post("/soc/certbr/import", { text, subject });
      toast.success(`${data.added} IP(s) registrados${data.outside.length ? ` · ${data.outside.length} fora dos seus prefixos (ignorados)` : ""}`);
      setText(""); setSubject("");
    } catch (e) { toast.error(formatApiError(e)); } finally { setBusy(false); }
  };
  return (
    <div className="space-y-3 max-w-4xl" data-testid="cert-import">
      <Card className="bg-surface border-line p-4 space-y-3">
        <div className="text-sm text-slate-300">Cole o e-mail do CERT.br (ou de outro CSIRT). O BastiON acha os IPs e o serviço pelo assunto, pela porta ou pelo texto de cada linha.</div>
        <Input value={subject} onChange={e => setSubject(e.target.value)} placeholder="Assunto do e-mail (ajuda a saber o serviço)" className="bg-sunken border-line h-9" data-testid="cert-subject" />
        <textarea value={text} onChange={e => setText(e.target.value)} rows={10} placeholder="Corpo do e-mail" className="w-full rounded-md bg-sunken border border-line p-2 font-mono text-sm text-slate-200" data-testid="cert-text" />
        {prev && (
          <div className="border border-line rounded-md divide-y divide-line max-h-64 overflow-y-auto" data-testid="cert-prev">
            {prev.length === 0 && <div className="px-3 py-2 text-xs text-amber-300">Nenhum IP público encontrado no texto.</div>}
            {prev.map((x, i) => <div key={i} className="flex gap-3 px-3 py-1 text-xs"><span className="font-mono text-slate-100 w-40">{x.ip}</span><span className={x.service === "unknown" ? "text-amber-300" : "text-slate-300"}>{x.service === "unknown" ? "serviço não identificado" : x.service}</span><span className="text-slate-500 truncate flex-1">{x.line}</span></div>)}
          </div>
        )}
        <Button onClick={go} disabled={busy || !prev?.length} className="bg-brand hover:bg-brand-strong text-onbrand" data-testid="cert-import-go">{busy ? <Loader2 className="w-4 h-4 mr-1.5 animate-spin" /> : <Upload className="w-4 h-4 mr-1.5" />}Registrar {prev?.length || 0} IP(s)</Button>
      </Card>
    </div>
  );
}

function Settings() {
  const [c, setC] = useState(null);
  const [ign, setIgn] = useState("");
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => api.get("/soc/certbr/settings").then(r => { setC(r.data); setIgn(r.data.ignore.join("\n")); }).catch(e => toast.error(formatApiError(e))), []);
  useEffect(() => { load(); }, [load]);
  if (!c) return null;
  const url = `${window.location.origin}/api/soc/certbr/inbound/${c.token}`;
  const save = async () => {
    setBusy(true);
    try { await api.put("/soc/certbr/settings", { enabled: c.enabled, alert: c.alert, min_remotes: Number(c.min_remotes), auto_resolve_days: Number(c.auto_resolve_days), ignore: ign.split(/[\s,;]+/).filter(Boolean) }); toast.success("Salvo"); }
    catch (e) { toast.error(formatApiError(e)); } finally { setBusy(false); }
  };
  const regen = async () => { if (!window.confirm("Gerar outro endereço? O antigo para de funcionar.")) return; try { const { data } = await api.post("/soc/certbr/token"); setC({ ...c, token: data.token }); } catch (e) { toast.error(formatApiError(e)); } };
  const rules = async () => { try { const r = await api.post("/compliance/rules/presets"); toast.success(`${r.data.added} regra(s) adicionada(s) na Conformidade`); } catch (e) { toast.error(formatApiError(e)); } };
  return (
    <div className="space-y-4 max-w-4xl" data-testid="cert-settings">
      {!c.own_prefixes && <Card className="bg-amber-500/5 border-amber-500/40 p-3 text-sm text-amber-200"><AlertTriangle className="w-4 h-4 inline mr-1.5" />Cadastre os <b>prefixos próprios</b> em Análise de Flow → Configuração: sem eles a detecção pelo flow fica desligada.</Card>}
      <Card className="bg-surface border-line p-4 space-y-3">
        <label className="flex items-center gap-2 text-sm text-slate-200"><Switch checked={c.enabled} onCheckedChange={v => setC({ ...c, enabled: v })} data-testid="cert-enabled" /> Detectar pelo flow (IP seu respondendo de porta de serviço UDP para fora)</label>
        <div className="grid sm:grid-cols-2 gap-3">
          <div><label className={lbl}>Confirmar a partir de N destinos de fora no dia</label><Input type="number" value={c.min_remotes} onChange={e => setC({ ...c, min_remotes: e.target.value })} className="bg-sunken border-line h-9" /></div>
          <div><label className={lbl}>Fechar sozinho sem resposta no flow há (dias)</label><Input type="number" value={c.auto_resolve_days} onChange={e => setC({ ...c, auto_resolve_days: e.target.value })} className="bg-sunken border-line h-9" /></div>
        </div>
        <div><label className={lbl}>Nunca acusar (DNS autoritativo, NTP público de propósito…) — um IP/rede por linha</label>
          <textarea value={ign} onChange={e => setIgn(e.target.value)} rows={3} className="w-full rounded-md bg-sunken border border-line p-2 font-mono text-sm text-slate-200" data-testid="cert-ignore" /></div>
        <label className="flex items-center gap-2 text-sm text-slate-300"><Switch checked={c.alert} onCheckedChange={v => setC({ ...c, alert: v })} /> Abrir incidente para cada serviço exposto novo</label>
        <Button onClick={save} disabled={busy} className="bg-brand hover:bg-brand-strong text-onbrand" data-testid="cert-save">{busy ? <Loader2 className="w-4 h-4 mr-1.5 animate-spin" /> : <Save className="w-4 h-4 mr-1.5" />}Salvar</Button>
      </Card>
      <Card className="bg-surface border-line p-4 space-y-2">
        <div className="text-sm font-semibold text-slate-200 flex items-center gap-2"><Mail className="w-4 h-4 text-brand-soft" />Encaminhar os e-mails automaticamente</div>
        <div className="text-xs text-slate-400">Configure uma regra no seu e-mail/n8n para fazer POST do aviso neste endereço (JSON <span className="font-mono">{"{subject, text}"}</span> ou o texto puro). Só IPs dos seus prefixos são registrados.</div>
        <div className="flex gap-2"><Input readOnly value={url} className="bg-sunken border-line h-9 font-mono text-xs" data-testid="cert-url" />
          <Button size="sm" variant="outline" onClick={() => navigator.clipboard.writeText(url).then(() => toast.success("Copiado")).catch(() => {})} className="border-line bg-transparent h-9"><Copy className="w-4 h-4" /></Button>
          <Button size="sm" variant="ghost" onClick={regen} className="h-9 text-slate-400">Gerar outro</Button></div>
      </Card>
      <Card className="bg-surface border-line p-4 space-y-2">
        <div className="text-sm font-semibold text-slate-200 flex items-center gap-2"><ClipboardCheck className="w-4 h-4 text-brand-soft" />Nos seus equipamentos</div>
        <div className="text-xs text-slate-400">A Conformidade confere nos backups: DNS aberto (MikroTik <span className="font-mono">allow-remote-requests</span>, Cisco <span className="font-mono">ip dns server</span>, Huawei <span className="font-mono">dns proxy</span>, Juniper), servidor NTP, UPnP e SOCKS ligados.</div>
        <Button size="sm" variant="outline" onClick={rules} className="border-line bg-transparent" data-testid="cert-rules">Adicionar as regras na Conformidade</Button>
      </Card>
    </div>
  );
}

export default function CertBr() {
  const [sp, setSp] = useSearchParams();
  const tab = sp.get("tab") || "";
  const [services, setServices] = useState(null);
  useEffect(() => { api.get("/soc/certbr/settings").then(r => setServices(r.data.services)).catch(() => {}); }, []);
  return (
    <div className="p-4 md:p-6 flex-1 overflow-y-auto" data-testid="cert-page">
      <div className="flex items-start justify-between flex-wrap gap-3 mb-4">
        <div>
          <h1 className="font-heading text-2xl sm:text-[1.75rem] font-semibold tracking-tight text-slate-100 flex items-center gap-2"><ShieldAlert className="w-6 h-6 text-brand-soft" /> Auditoria CERT.br</h1>
          <p className="text-sm text-slate-400 mt-1 max-w-3xl">Serviços que respondem para a internet e viram refletor de DDoS (DNS, NTP, SSDP, SNMP, Memcached…), achados no flow, nos avisos do CERT.br e nos backups — sem varrer a rede.</p>
        </div>
        <div className="flex gap-1 p-1 bg-sunken border border-line rounded-md text-sm">
          {[["", "Pendências"], ["importar", "Importar aviso"], ["config", "Configurações"]].map(([k, l]) => <button key={k} onClick={() => setSp(k ? { tab: k } : {})} className={`px-3 h-7 rounded ${tab === k ? "bg-brand/20 text-brand-soft" : "text-slate-400 hover:text-slate-200"}`} data-testid={`cert-tab-${k || "items"}`}>{l}</button>)}
        </div>
      </div>
      {tab === "importar" ? <Import /> : tab === "config" ? <Settings /> : <Items services={services} />}
    </div>
  );
}
