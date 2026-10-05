import React, { useEffect, useMemo, useState } from "react";
import { api, formatApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { toast } from "sonner";
import { RdpSession } from "@/components/RdpSession";
import { MonitorUp, Loader2, Plus, Pencil, Trash2, Search, AlertTriangle, Play, KeyRound } from "lucide-react";

const sel = "h-9 rounded-md bg-sunken border border-line text-sm text-slate-200 px-2 w-full";
const SEC = { any: "Automática", nla: "NLA", "nla-ext": "NLA estendida", tls: "TLS", rdp: "RDP antigo", vmconnect: "Hyper-V" };
const LAY = { "pt-br-qwerty": "Português (Brasil)", "en-us-qwerty": "Inglês (EUA)", "pt-pt-qwerty": "Português (Portugal)", "es-es-qwerty": "Espanhol", "es-latam-qwerty": "Espanhol (Lat. Am.)", failsafe: "Genérico (unicode)" };
const EMPTY = { name: "", host: "", port: 3389, username: "", domain: "", password: "", agent_id: "", security: "any", layout: "pt-br-qwerty", tags: "" };

function Fields({ f, set, agents, meta, quick }) {
  return (
    <div className="grid grid-cols-6 gap-3">
      {!quick && <div className="col-span-6"><Label className="text-slate-300">Nome</Label><Input value={f.name} onChange={e => set({ ...f, name: e.target.value })} placeholder="Servidor AD — matriz" className="mt-1 bg-sunken border-line" data-testid="rdp-f-name" /></div>}
      <div className="col-span-4"><Label className="text-slate-300">IP ou nome do computador</Label><Input value={f.host} onChange={e => set({ ...f, host: e.target.value })} placeholder="10.0.0.15 ou servidor.empresa.local" spellCheck={false} className="mt-1 bg-sunken border-line font-mono" data-testid="rdp-f-host" /></div>
      <div className="col-span-2"><Label className="text-slate-300">Porta</Label><Input value={f.port} onChange={e => set({ ...f, port: e.target.value.replace(/\D/g, "").slice(0, 5) })} className="mt-1 bg-sunken border-line font-mono" /></div>
      <div className="col-span-3"><Label className="text-slate-300">Usuário</Label><Input value={f.username} onChange={e => set({ ...f, username: e.target.value })} placeholder="usuario ou DOMINIO\usuario" autoComplete="off" spellCheck={false} className="mt-1 bg-sunken border-line font-mono" data-testid="rdp-f-user" /></div>
      <div className="col-span-3"><Label className="text-slate-300">Domínio <span className="text-slate-500">(opcional)</span></Label><Input value={f.domain} onChange={e => set({ ...f, domain: e.target.value })} autoComplete="off" spellCheck={false} className="mt-1 bg-sunken border-line font-mono" /></div>
      <div className="col-span-6"><Label className="text-slate-300">Senha</Label><Input type="password" value={f.password} onChange={e => set({ ...f, password: e.target.value })} autoComplete="new-password"
        placeholder={f.has_password ? "•••••• (salva — deixe vazio para manter)" : quick ? "" : "vazio = pedir ao conectar"} className="mt-1 bg-sunken border-line font-mono" data-testid="rdp-f-pass" /></div>
      <div className="col-span-6 sm:col-span-2"><Label className="text-slate-300">Sair por</Label>
        <select value={f.agent_id || ""} onChange={e => set({ ...f, agent_id: e.target.value })} className={`${sel} mt-1`} data-testid="rdp-f-agent"><option value="">Direto do BastiON</option>{agents.map(a => <option key={a.id} value={a.id}>Agente {a.name}</option>)}</select></div>
      <div className="col-span-3 sm:col-span-2"><Label className="text-slate-300">Segurança</Label>
        <select value={f.security} onChange={e => set({ ...f, security: e.target.value })} className={`${sel} mt-1`}>{(meta.security || []).map(s => <option key={s} value={s}>{SEC[s] || s}</option>)}</select></div>
      <div className="col-span-3 sm:col-span-2"><Label className="text-slate-300">Teclado remoto</Label>
        <select value={f.layout} onChange={e => set({ ...f, layout: e.target.value })} className={`${sel} mt-1`}>{(meta.layouts || []).map(s => <option key={s} value={s}>{LAY[s] || s}</option>)}</select></div>
      {!quick && <div className="col-span-6"><Label className="text-slate-300">Tags <span className="text-slate-500">(separe por vírgula)</span></Label><Input value={f.tags} onChange={e => set({ ...f, tags: e.target.value })} placeholder="LINK10, servidores" className="mt-1 bg-sunken border-line" /></div>}
    </div>
  );
}

export default function Rdp() {
  const [data, setData] = useState(null);
  const [agents, setAgents] = useState([]);
  const [q, setQ] = useState("");
  const [quick, setQuick] = useState({ ...EMPTY });
  const [edit, setEdit] = useState(null);
  const [ask, setAsk] = useState(null);              // conexão salva sem senha: pede na hora
  const [session, setSession] = useState(null);      // {ticket, name, target, again}
  const [busy, setBusy] = useState("");

  const load = async () => { try { setData((await api.get("/rdp/hosts")).data); } catch (e) { toast.error(formatApiError(e)); } };
  useEffect(() => { load(); api.get("/agents").then(r => setAgents(r.data)).catch(() => {}); }, []);
  const shown = useMemo(() => { const t = q.trim().toLowerCase(); return (data?.hosts || []).filter(h => !t || `${h.name} ${h.host} ${h.username} ${(h.tags || []).join(" ")}`.toLowerCase().includes(t)); }, [data, q]);
  const agentName = (id) => agents.find(a => a.id === id)?.name;

  const open = async (payload, key) => {
    setBusy(key);
    try { const { data: t } = await api.post("/rdp/connect", payload); setSession({ ...t, again: () => { setSession(null); setTimeout(() => open(payload, key), 50); } }); setAsk(null); }
    catch (e) { toast.error(formatApiError(e)); } finally { setBusy(""); }
  };
  const connectSaved = (h) => (h.has_password && h.username) ? open({ host_id: h.id }, h.id) : setAsk({ host: h, username: h.username || "", password: "" });
  const connectQuick = (e) => { e.preventDefault(); open({ ...quick, port: Number(quick.port) || 3389, agent_id: quick.agent_id || null }, "quick"); };
  const save = async () => {
    const body = { ...edit, port: Number(edit.port) || 3389, agent_id: edit.agent_id || null, tags: String(edit.tags || "").split(",").map(t => t.trim()).filter(Boolean),
      password: edit.id && !edit.password ? null : edit.password };
    setBusy("save");
    try { edit.id ? await api.put(`/rdp/hosts/${edit.id}`, body) : await api.post("/rdp/hosts", body); toast.success("Conexão salva"); setEdit(null); load(); }
    catch (e) { toast.error(formatApiError(e)); } finally { setBusy(""); }
  };
  const remove = async (h) => { if (!window.confirm(`Remover a conexão "${h.name}"?`)) return; try { await api.delete(`/rdp/hosts/${h.id}`); load(); } catch (e) { toast.error(formatApiError(e)); } };

  if (!data) return <div className="p-10 text-slate-500"><Loader2 className="w-5 h-5 animate-spin" /></div>;
  return (
    <div className="p-4 md:p-8 max-w-6xl" data-testid="rdp-page">
      <div className="flex items-start gap-3 mb-6">
        <div>
          <h1 className="text-2xl md:text-3xl font-semibold text-slate-100 flex items-center gap-2.5"><MonitorUp className="w-6 h-6 text-brand-soft" />Área de Trabalho Remota</h1>
          <p className="text-sm text-slate-400 mt-1">Abra sessões RDP (Windows) aqui no navegador, direto ou passando por um agente.</p>
        </div>
        <Button className="ml-auto bg-brand hover:bg-brand-strong" onClick={() => setEdit({ ...EMPTY })} data-testid="rdp-new"><Plus className="w-4 h-4 mr-2" />Nova conexão</Button>
      </div>

      {!data.guacd && <Card className="bg-surface border-amber-900/60 p-4 mb-5 text-sm text-amber-300 flex gap-2" data-testid="rdp-noguacd"><AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
        <div>O serviço de Área de Trabalho Remota (guacd) não está respondendo neste servidor. Rode <code className="font-mono">sudo bash /opt/bastion/deploy/update.sh</code> e, se continuar, veja <code className="font-mono">docker compose logs guacd</code>.</div></Card>}

      <div className="grid lg:grid-cols-[minmax(0,1fr)_380px] gap-5 items-start">
        <Card className="bg-surface border-line overflow-hidden">
          <div className="px-4 py-3 border-b border-line flex items-center gap-3">
            <span className="text-sm text-slate-200">Conexões salvas <span className="text-slate-500 font-mono text-xs">({data.hosts.length})</span></span>
            <div className="relative ml-auto w-52"><Search className="w-3.5 h-3.5 absolute left-2.5 top-2.5 text-slate-500" /><Input value={q} onChange={e => setQ(e.target.value)} placeholder="buscar" className="pl-8 h-8 bg-sunken border-line text-xs" /></div>
          </div>
          {!shown.length ? <div className="px-5 py-10 text-center text-sm text-slate-500">{data.hosts.length ? "Nada encontrado." : "Nenhuma conexão salva. Use a conexão rápida ao lado ou clique em Nova conexão."}</div> : (
            <div className="divide-y divide-line/60">
              {shown.map(h => (
                <div key={h.id} className="px-4 py-2.5 flex items-center gap-3" data-testid="rdp-row">
                  <MonitorUp className="w-4 h-4 text-slate-500 shrink-0" />
                  <div className="min-w-0 flex-1">
                    <div className="text-sm text-slate-100 truncate">{h.name}{(h.tags || []).map(t => <span key={t} className="ml-2 text-[10px] font-mono text-slate-400 border border-line rounded px-1">{t}</span>)}</div>
                    <div className="text-[11px] font-mono text-slate-500 truncate">{h.host}{h.port !== 3389 ? `:${h.port}` : ""} · {h.domain ? `${h.domain}\\` : ""}{h.username || "sem usuário"}{h.has_password ? "" : " · pede senha"}{h.agent_id ? ` · via ${agentName(h.agent_id) || "agente"}` : ""}</div>
                  </div>
                  <Button size="sm" className="h-8 bg-brand hover:bg-brand-strong" disabled={busy === h.id} onClick={() => connectSaved(h)} data-testid={`rdp-connect-${h.id}`}>{busy === h.id ? <Loader2 className="w-3.5 h-3.5 mr-1.5 animate-spin" /> : <Play className="w-3.5 h-3.5 mr-1.5" />}Conectar</Button>
                  <button className="text-slate-500 hover:text-slate-200" title="Editar" onClick={() => setEdit({ ...EMPTY, ...h, password: "", tags: (h.tags || []).join(", ") })}><Pencil className="w-3.5 h-3.5" /></button>
                  <button className="text-slate-500 hover:text-red-400" title="Remover" onClick={() => remove(h)}><Trash2 className="w-3.5 h-3.5" /></button>
                </div>
              ))}
            </div>
          )}
        </Card>

        <Card className="bg-surface border-line p-4">
          <div className="text-sm text-slate-200 mb-3">Conexão rápida <span className="text-slate-500">— nada é salvo</span></div>
          <form onSubmit={connectQuick} className="space-y-3" data-testid="rdp-quick">
            <Fields f={quick} set={setQuick} agents={agents} meta={data} quick />
            <Button type="submit" disabled={busy === "quick" || !quick.host.trim()} className="w-full bg-brand hover:bg-brand-strong" data-testid="rdp-quick-go">{busy === "quick" ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <Play className="w-4 h-4 mr-2" />}Conectar</Button>
          </form>
        </Card>
      </div>

      <Dialog open={!!edit} onOpenChange={(v) => !v && setEdit(null)}>
        <DialogContent className="bg-surface border-line text-slate-100 max-w-xl max-h-[92vh] overflow-y-auto" data-testid="rdp-edit">
          <DialogHeader><DialogTitle>{edit?.id ? "Editar conexão" : "Nova conexão"}</DialogTitle></DialogHeader>
          {edit && <Fields f={edit} set={setEdit} agents={agents} meta={data} />}
          <p className="text-[11px] text-slate-500">A senha fica cifrada no BastiON e não volta para o navegador. Sem senha salva, ela é pedida a cada conexão.</p>
          <DialogFooter><Button variant="ghost" onClick={() => setEdit(null)}>Cancelar</Button><Button onClick={save} disabled={busy === "save"} className="bg-brand hover:bg-brand-strong" data-testid="rdp-save">{busy === "save" && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}Salvar</Button></DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={!!ask} onOpenChange={(v) => !v && setAsk(null)}>
        <DialogContent className="bg-surface border-line text-slate-100 max-w-sm" data-testid="rdp-ask">
          <DialogHeader><DialogTitle className="flex items-center gap-2"><KeyRound className="w-4 h-4 text-brand-soft" />Entrar em {ask?.host.name}</DialogTitle></DialogHeader>
          <form onSubmit={(e) => { e.preventDefault(); open({ host_id: ask.host.id, username: ask.username, password: ask.password }, ask.host.id); }} className="space-y-3">
            <div><Label className="text-slate-300">Usuário</Label><Input value={ask?.username || ""} onChange={e => setAsk({ ...ask, username: e.target.value })} autoComplete="off" className="mt-1 bg-sunken border-line font-mono" /></div>
            <div><Label className="text-slate-300">Senha</Label><Input type="password" value={ask?.password || ""} onChange={e => setAsk({ ...ask, password: e.target.value })} autoFocus autoComplete="new-password" className="mt-1 bg-sunken border-line font-mono" data-testid="rdp-ask-pass" /></div>
            <DialogFooter><Button type="button" variant="ghost" onClick={() => setAsk(null)}>Cancelar</Button><Button type="submit" className="bg-brand hover:bg-brand-strong" data-testid="rdp-ask-go">Conectar</Button></DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {session && <RdpSession key={session.ticket} ticket={session.ticket} name={session.name} target={session.target} onClose={() => setSession(null)} onRetry={session.again} />}
    </div>
  );
}
