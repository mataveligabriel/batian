import React, { useEffect, useState } from "react";
import { api, formatApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { toast } from "sonner";
import { Plus, Radio, Zap, Trash2, Pencil, Copy, Terminal as TerminalIco, Settings2, ShieldCheck, ArrowDownRight, Laptop, Route } from "lucide-react";
import { useAuth } from "@/context/AuthContext";

const empty = { name: "", location: "", mode: "direct", host: "", port: 22, tunnel_port: "", username: "", password: "", clear_password: false, parent_agent_id: "", description: "" };

function CodeBlock({ code, testId }) {
  return (
    <div className="relative">
      <pre data-testid={testId} className="bg-[#05070A] border border-[#1E293B] rounded p-4 text-xs font-mono text-emerald-300 max-h-80 overflow-auto whitespace-pre-wrap">{code}</pre>
      <Button size="sm" variant="outline" onClick={() => { navigator.clipboard.writeText(code); toast.success("Copiado"); }}
              className="absolute top-2 right-2 border-[#1E293B] bg-[#0B111C] text-slate-200 hover:bg-slate-800 h-7">
        <Copy className="w-3.5 h-3.5 mr-1" /> Copiar
      </Button>
    </div>
  );
}

export default function Agents() {
  const [agents, setAgents] = useState([]);
  const [open, setOpen] = useState(false);
  const [scriptModal, setScriptModal] = useState(null);
  const [bastionOpen, setBastionOpen] = useState(false);
  const [bastion, setBastion] = useState({ public_host: "", ssh_port: 22, ssh_user: "bastion" });
  const [setupScript, setSetupScript] = useState("");
  const [editing, setEditing] = useState(null);
  const [form, setForm] = useState(empty);
  const [testing, setTesting] = useState(null);
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";

  const load = async () => setAgents((await api.get("/agents")).data);
  useEffect(() => { load(); api.get("/bastion/settings").then(r => setBastion(r.data)).catch(() => {}); }, []);

  const openNew = () => { setEditing(null); setForm(empty); setOpen(true); };
  const openEdit = (a) => {
    setEditing(a);
    setForm({ ...empty, ...a, tunnel_port: a.tunnel_port ?? "", parent_agent_id: a.parent_agent_id || "", password: "", clear_password: false });
    setOpen(true);
  };

  const save = async () => {
    if (!form.name.trim()) return toast.error("Nome é obrigatório");
    if (form.mode === "direct" && !form.host.trim()) return toast.error("Host é obrigatório no modo direto");
    const payload = {
      ...form,
      host: form.host.trim(),
      port: Number(form.port) || 22,
      tunnel_port: form.mode === "reverse" && form.tunnel_port ? Number(form.tunnel_port) : null,
      username: form.username.trim() || "root",
      password: form.password || null,
      parent_agent_id: form.parent_agent_id || null,
    };
    try {
      if (editing) await api.put(`/agents/${editing.id}`, payload);
      else await api.post("/agents", payload);
      toast.success(editing ? "Agente atualizado" : "Agente cadastrado");
      setOpen(false); load();
    } catch (e) { toast.error(formatApiError(e)); }
  };
  const del = async (a) => {
    if (!window.confirm(`Excluir agente ${a.name}?`)) return;
    await api.delete(`/agents/${a.id}`); load(); toast.success("Agente removido");
  };
  const ping = async (a) => {
    toast.info(`Ping em ${a.name}…`);
    try {
      const { data } = await api.post(`/agents/${a.id}/ping`);
      (data.status === "online" ? toast.success : toast.error)(`${a.name}: ${data.status}${data.latency_ms ? ` (${data.latency_ms}ms)` : ""}`);
      load();
    } catch (e) { toast.error(formatApiError(e)); }
  };
  const testLogin = async (a) => {
    setTesting(a.id);
    try {
      const { data } = await api.post(`/agents/${a.id}/test`);
      if (data.ok) toast.success(`Login OK via ${data.hops.join(" → ")}${data.output ? ` · ${data.output}` : ""}`);
      else toast.error(data.error);
    } catch (e) { toast.error(formatApiError(e)); }
    finally { setTesting(null); }
  };
  const showScript = async (a) => {
    const { data } = await api.get(`/agents/${a.id}/install-script`);
    setScriptModal({ agent: a, ...data });
  };
  const openBastion = async () => {
    setBastionOpen(true);
    try {
      const { data } = await api.get(`/bastion/setup-script`, { params: { api_url: window.location.origin } });
      setSetupScript(data.script);
    } catch { setSetupScript(""); }
  };
  const saveBastion = async () => {
    try {
      await api.put("/bastion/settings", { ...bastion, ssh_port: Number(bastion.ssh_port) || 22 });
      toast.success("Configuração do Bastion salva");
    } catch (e) { toast.error(formatApiError(e)); }
  };

  const nameOf = (id) => agents.find(a => a.id === id)?.name;
  const chainOf = (a) => {
    const names = [a.name]; let cur = a; const seen = new Set([a.id]);
    while (cur?.parent_agent_id && !seen.has(cur.parent_agent_id)) {
      seen.add(cur.parent_agent_id);
      cur = agents.find(x => x.id === cur.parent_agent_id);
      if (cur) names.unshift(cur.name);
    }
    return names;
  };

  return (
    <div className="p-8 flex-1 overflow-y-auto" data-testid="agents-page">
      <div className="flex items-start justify-between mb-6 gap-4">
        <div>
          <div className="text-xs uppercase tracking-widest text-slate-400 font-mono">Cadeia de Saltos</div>
          <h1 className="font-heading text-3xl sm:text-4xl font-bold text-slate-100 mt-1">Agentes / Jump Hosts</h1>
          <p className="text-slate-400 mt-2 text-sm max-w-2xl">
            <b>Túnel reverso</b>: máquina atrás de NAT/VPN que abre um túnel até o Bastion (ex.: seu PC com FortiClient).
            <b> Direto</b>: jump host alcançável pelo Bastion ou pelo agente pai. Encadeie agentes para chegar aos equipamentos.
          </p>
        </div>
        <div className="flex gap-2">
          {isAdmin && (
            <Button variant="outline" onClick={openBastion} data-testid="bastion-settings-btn" className="border-[#1E293B] bg-[#0B111C] text-slate-200 hover:bg-slate-800">
              <Settings2 className="w-4 h-4 mr-2" /> Configurar Bastion
            </Button>
          )}
          {isAdmin && (
            <Button onClick={openNew} data-testid="add-agent-btn" className="bg-[#007AFF] hover:bg-[#0062CC]">
              <Plus className="w-4 h-4 mr-2" /> Novo Agente
            </Button>
          )}
        </div>
      </div>

      {!bastion.public_host && isAdmin && (
        <div className="mb-4 text-xs font-mono text-amber-300 bg-amber-500/10 border border-amber-500/30 rounded px-4 py-2" data-testid="bastion-warning">
          Host público do Bastion não configurado — necessário para gerar instaladores de túnel reverso. Clique em "Configurar Bastion".
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {agents.map(a => (
          <Card key={a.id} data-testid={`agent-card-${a.id}`} className="bg-[#111722] border-[#1E293B] p-5 hover:border-[#334155] transition-colors">
            <div className="flex items-start justify-between">
              <div className="flex items-center gap-3">
                <div className={`w-10 h-10 rounded-md flex items-center justify-center border ${
                  a.status === "online" ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-400" :
                  a.status === "offline" ? "border-red-500/30 bg-red-500/10 text-red-400" :
                  "border-slate-700 bg-slate-800/40 text-slate-500"
                }`}>
                  {a.mode === "reverse" ? <Laptop className="w-5 h-5" /> : <Radio className="w-5 h-5" />}
                </div>
                <div>
                  <div className="font-heading font-semibold text-slate-100">{a.name}</div>
                  <div className="text-xs text-slate-500 font-mono">{a.location}</div>
                </div>
              </div>
              <span className={`text-[10px] font-mono uppercase tracking-widest ${
                a.status === "online" ? "text-emerald-400" : a.status === "offline" ? "text-red-400" : "text-slate-500"
              }`} data-testid={`agent-status-${a.id}`}>{a.status}</span>
            </div>
            <div className="mt-4 space-y-1 text-xs font-mono text-slate-400">
              <div>modo: <span className={a.mode === "reverse" ? "text-[#4DA3FF]" : "text-slate-200"}>{a.mode === "reverse" ? `túnel reverso · porta ${a.tunnel_port}` : "direto"}</span></div>
              <div>{a.mode === "reverse" ? "ssh local" : "host"}: <span className="text-slate-200">{a.mode === "reverse" ? `localhost:${a.port}` : `${a.host}:${a.port}`}</span></div>
              <div>user: <span className="text-slate-200">{a.username}</span>{a.has_password && <span className="text-amber-400 ml-2">· senha</span>}</div>
              {a.parent_agent_id && (
                <div className="flex items-center gap-1 text-slate-500"><Route className="w-3 h-3" /> via <span className="text-slate-200">{chainOf(a).slice(0, -1).join(" → ")}</span></div>
              )}
              <div>latência: <span className="text-slate-200">{a.latency_ms != null ? `${a.latency_ms} ms` : "—"}</span></div>
            </div>
            <div className="flex gap-2 mt-4 flex-wrap">
              <Button size="sm" variant="outline" onClick={() => ping(a)} data-testid={`ping-agent-${a.id}`}
                      className="border-[#1E293B] bg-[#0B111C] text-slate-200 hover:bg-slate-800 flex-1">
                <Zap className="w-3.5 h-3.5 mr-1.5" /> Ping
              </Button>
              <Button size="sm" variant="outline" onClick={() => testLogin(a)} disabled={testing === a.id} data-testid={`test-agent-${a.id}`}
                      className="border-[#1E293B] bg-[#0B111C] text-slate-200 hover:bg-slate-800 flex-1">
                <ShieldCheck className="w-3.5 h-3.5 mr-1.5" /> {testing === a.id ? "…" : "Testar SSH"}
              </Button>
              <Button size="sm" variant="outline" onClick={() => showScript(a)} data-testid={`script-agent-${a.id}`}
                      className="border-[#1E293B] bg-[#0B111C] text-slate-200 hover:bg-slate-800 flex-1">
                <TerminalIco className="w-3.5 h-3.5 mr-1.5" /> Instalar
              </Button>
              {isAdmin && (
                <>
                  <Button size="sm" variant="ghost" onClick={() => openEdit(a)} data-testid={`edit-agent-${a.id}`} className="text-slate-300 hover:bg-slate-800">
                    <Pencil className="w-4 h-4" />
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => del(a)} data-testid={`del-agent-${a.id}`} className="text-red-400 hover:bg-red-950/40">
                    <Trash2 className="w-4 h-4" />
                  </Button>
                </>
              )}
            </div>
          </Card>
        ))}
      </div>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="bg-[#111722] border-[#1E293B] text-slate-100 max-w-lg">
          <DialogHeader><DialogTitle>{editing ? "Editar agente" : "Novo agente"}</DialogTitle></DialogHeader>
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <div><Label>Nome</Label><Input data-testid="agent-form-name" value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} className="bg-[#05070A] border-[#1E293B] font-mono" /></div>
              <div><Label>Localização</Label><Input data-testid="agent-form-location" value={form.location} onChange={e => setForm({ ...form, location: e.target.value })} className="bg-[#05070A] border-[#1E293B] font-mono" /></div>
            </div>
            <div>
              <Label>Modo</Label>
              <Select value={form.mode} onValueChange={(v) => setForm({ ...form, mode: v })}>
                <SelectTrigger data-testid="agent-form-mode" className="bg-[#05070A] border-[#1E293B] font-mono"><SelectValue /></SelectTrigger>
                <SelectContent className="bg-[#111722] border-[#1E293B] text-slate-100">
                  <SelectItem value="direct">Direto — Bastion (ou agente pai) alcança este host</SelectItem>
                  <SelectItem value="reverse">Túnel reverso — esta máquina conecta no Bastion</SelectItem>
                </SelectContent>
              </Select>
            </div>
            {form.mode === "direct" ? (
              <div className="grid grid-cols-3 gap-3">
                <div className="col-span-2"><Label>Host / IP</Label><Input data-testid="agent-form-host" value={form.host} onChange={e => setForm({ ...form, host: e.target.value })} className="bg-[#05070A] border-[#1E293B] font-mono" /></div>
                <div><Label>Porta SSH</Label><Input data-testid="agent-form-port" type="number" value={form.port} onChange={e => setForm({ ...form, port: e.target.value })} className="bg-[#05070A] border-[#1E293B] font-mono" /></div>
              </div>
            ) : (
              <div className="grid grid-cols-2 gap-3">
                <div><Label>Porta do túnel no Bastion</Label><Input data-testid="agent-form-tunnel-port" type="number" placeholder="auto" value={form.tunnel_port} onChange={e => setForm({ ...form, tunnel_port: e.target.value })} className="bg-[#05070A] border-[#1E293B] font-mono" /></div>
                <div><Label>Porta SSH local da máquina</Label><Input data-testid="agent-form-local-port" type="number" value={form.port} onChange={e => setForm({ ...form, port: e.target.value })} className="bg-[#05070A] border-[#1E293B] font-mono" /></div>
              </div>
            )}
            <div className="grid grid-cols-2 gap-3">
              <div><Label>Usuário SSH</Label><Input data-testid="agent-form-user" value={form.username} onChange={e => setForm({ ...form, username: e.target.value })} placeholder={form.mode === "reverse" ? "usuário da sua máquina" : "root"} className="bg-[#05070A] border-[#1E293B] font-mono" /></div>
              <div>
                <Label>Senha (opcional)</Label>
                <Input data-testid="agent-form-password" type="password" value={form.password} onChange={e => setForm({ ...form, password: e.target.value })}
                       placeholder={editing?.has_password ? "•••••••• (mantida)" : "vazio = chave global"} className="bg-[#05070A] border-[#1E293B] font-mono" />
                {editing?.has_password && (
                  <label className="flex items-center gap-2 mt-1 text-[11px] text-slate-400 cursor-pointer">
                    <input type="checkbox" data-testid="agent-form-clear-password" checked={form.clear_password} onChange={e => setForm({ ...form, clear_password: e.target.checked })} /> remover senha
                  </label>
                )}
              </div>
            </div>
            <div>
              <Label>Agente pai (alcançado através de)</Label>
              <Select value={form.parent_agent_id || "none"} onValueChange={(v) => setForm({ ...form, parent_agent_id: v === "none" ? "" : v })}>
                <SelectTrigger data-testid="agent-form-parent" className="bg-[#05070A] border-[#1E293B] font-mono"><SelectValue placeholder="Nenhum (direto do Bastion)" /></SelectTrigger>
                <SelectContent className="bg-[#111722] border-[#1E293B] text-slate-100">
                  <SelectItem value="none">Nenhum — alcançado direto do Bastion</SelectItem>
                  {agents.filter(a => a.id !== editing?.id).map(a => <SelectItem key={a.id} value={a.id}>{a.name} · {a.mode === "reverse" ? "túnel reverso" : a.host}</SelectItem>)}
                </SelectContent>
              </Select>
              <div className="text-[11px] text-slate-500 mt-1 font-mono">Ex.: jump host da VPN → pai = agente gateway da sua máquina.</div>
            </div>
            <div><Label>Descrição</Label><Textarea data-testid="agent-form-desc" value={form.description} onChange={e => setForm({ ...form, description: e.target.value })} className="bg-[#05070A] border-[#1E293B] font-mono" /></div>
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setOpen(false)}>Cancelar</Button>
            <Button onClick={save} data-testid="save-agent-btn" className="bg-[#007AFF] hover:bg-[#0062CC]">Salvar</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={!!scriptModal} onOpenChange={(v) => !v && setScriptModal(null)}>
        <DialogContent className="bg-[#111722] border-[#1E293B] text-slate-100 max-w-3xl" data-testid="install-dialog">
          <DialogHeader><DialogTitle>Instalador — {scriptModal?.agent.name}</DialogTitle></DialogHeader>
          {scriptModal?.mode === "direct" ? (
            <p className="text-sm text-slate-300 font-mono" data-testid="install-direct-note">{scriptModal.note}</p>
          ) : (
            <>
              {scriptModal?.note && <div className="text-xs font-mono text-amber-300 bg-amber-500/10 border border-amber-500/30 rounded px-3 py-2">{scriptModal.note}</div>}
              <p className="text-xs text-slate-500 font-mono">
                Rode na máquina que tem acesso à rede (ex.: seu PC com FortiClient). Ela abrirá um túnel reverso até o Bastion na porta <span className="text-emerald-300">{scriptModal?.tunnel_port}</span>.
                A chave pública deste agente é sincronizada automaticamente no Bastion (veja "Configurar Bastion").
              </p>
              <Tabs defaultValue="bash">
                <TabsList className="bg-[#0B111C] border border-[#1E293B]">
                  <TabsTrigger value="bash" data-testid="install-tab-bash">Linux / macOS</TabsTrigger>
                  <TabsTrigger value="ps" data-testid="install-tab-ps">Windows (PowerShell)</TabsTrigger>
                </TabsList>
                <TabsContent value="bash"><CodeBlock code={scriptModal?.bash || ""} testId="install-script-bash" /></TabsContent>
                <TabsContent value="ps"><CodeBlock code={scriptModal?.powershell || ""} testId="install-script-ps" /></TabsContent>
              </Tabs>
            </>
          )}
        </DialogContent>
      </Dialog>

      <Dialog open={bastionOpen} onOpenChange={setBastionOpen}>
        <DialogContent className="bg-[#111722] border-[#1E293B] text-slate-100 max-w-3xl" data-testid="bastion-dialog">
          <DialogHeader><DialogTitle>Configurar Bastion (servidor central)</DialogTitle></DialogHeader>
          <div className="grid grid-cols-4 gap-3">
            <div className="col-span-2"><Label>Host público / IP do VPS</Label><Input data-testid="bastion-host" value={bastion.public_host} onChange={e => setBastion({ ...bastion, public_host: e.target.value })} placeholder="bastion.meudominio.com" className="bg-[#05070A] border-[#1E293B] font-mono" /></div>
            <div><Label>Porta SSH</Label><Input data-testid="bastion-port" type="number" value={bastion.ssh_port} onChange={e => setBastion({ ...bastion, ssh_port: e.target.value })} className="bg-[#05070A] border-[#1E293B] font-mono" /></div>
            <div><Label>Usuário dos túneis</Label><Input data-testid="bastion-user" value={bastion.ssh_user} onChange={e => setBastion({ ...bastion, ssh_user: e.target.value })} className="bg-[#05070A] border-[#1E293B] font-mono" /></div>
          </div>
          <div className="flex justify-end"><Button onClick={saveBastion} data-testid="bastion-save-btn" className="bg-[#007AFF] hover:bg-[#0062CC]">Salvar</Button></div>
          <div className="flex items-center gap-2 text-xs font-mono text-slate-400 mt-2"><ArrowDownRight className="w-3.5 h-3.5" /> Rode uma vez como root no VPS onde o backend roda (cria o usuário, habilita túneis reversos e sincroniza as chaves dos agentes a cada minuto):</div>
          {setupScript && <CodeBlock code={setupScript} testId="bastion-setup-script" />}
        </DialogContent>
      </Dialog>
    </div>
  );
}
