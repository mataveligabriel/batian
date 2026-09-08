import React, { useEffect, useState } from "react";
import { api, formatApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { toast } from "sonner";
import { Plus, Radio, Zap, Trash2, Pencil, Copy, Terminal as TerminalIco } from "lucide-react";
import { useAuth } from "@/context/AuthContext";

const empty = { name: "", location: "", host: "", port: 22, username: "bastion", description: "" };

export default function Agents() {
  const [agents, setAgents] = useState([]);
  const [open, setOpen] = useState(false);
  const [scriptModal, setScriptModal] = useState(null);
  const [editing, setEditing] = useState(null);
  const [form, setForm] = useState(empty);
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";

  const load = async () => setAgents((await api.get("/agents")).data);
  useEffect(() => { load(); }, []);

  const openNew = () => { setEditing(null); setForm(empty); setOpen(true); };
  const openEdit = (a) => { setEditing(a); setForm({ ...a }); setOpen(true); };

  const save = async () => {
    const payload = { ...form, port: Number(form.port) || 22 };
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
      toast.success(`${a.name}: ${data.status}${data.latency_ms ? ` (${data.latency_ms}ms)` : ""}`);
      load();
    } catch (e) { toast.error(formatApiError(e)); }
  };
  const showScript = async (a) => {
    const { data } = await api.get(`/agents/${a.id}/install-script`);
    setScriptModal({ agent: a, script: data.script });
  };

  return (
    <div className="p-8 flex-1 overflow-y-auto" data-testid="agents-page">
      <div className="flex items-start justify-between mb-6">
        <div>
          <div className="text-xs uppercase tracking-widest text-slate-400 font-mono">Túneis Reversos</div>
          <h1 className="font-heading text-3xl sm:text-4xl font-bold text-slate-100 mt-1">Agentes Remotos</h1>
          <p className="text-slate-400 mt-2 text-sm max-w-2xl">
            Proxies instalados nas redes remotas (sem VPN). Cada agente abre um túnel SSH reverso para o Bastion Central
            e é usado como ProxyJump para acessar os equipamentos internos.
          </p>
        </div>
        {isAdmin && (
          <Button onClick={openNew} data-testid="add-agent-btn" className="bg-[#007AFF] hover:bg-[#0062CC]">
            <Plus className="w-4 h-4 mr-2" /> Novo Agente
          </Button>
        )}
      </div>

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
                  <Radio className="w-5 h-5" />
                </div>
                <div>
                  <div className="font-heading font-semibold text-slate-100">{a.name}</div>
                  <div className="text-xs text-slate-500 font-mono">{a.location}</div>
                </div>
              </div>
              <span className={`text-[10px] font-mono uppercase tracking-widest ${
                a.status === "online" ? "text-emerald-400" : a.status === "offline" ? "text-red-400" : "text-slate-500"
              }`}>{a.status}</span>
            </div>
            <div className="mt-4 space-y-1 text-xs font-mono text-slate-400">
              <div>host: <span className="text-slate-200">{a.host}:{a.port}</span></div>
              <div>user: <span className="text-slate-200">{a.username}</span></div>
              <div>latência: <span className="text-slate-200">{a.latency_ms ? `${a.latency_ms} ms` : "—"}</span></div>
            </div>
            <div className="flex gap-2 mt-4">
              <Button size="sm" variant="outline" onClick={() => ping(a)} data-testid={`ping-agent-${a.id}`}
                      className="border-[#1E293B] bg-[#0B111C] text-slate-200 hover:bg-slate-800 flex-1">
                <Zap className="w-3.5 h-3.5 mr-1.5" /> Ping
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
        <DialogContent className="bg-[#111722] border-[#1E293B] text-slate-100">
          <DialogHeader><DialogTitle>{editing ? "Editar agente" : "Novo agente"}</DialogTitle></DialogHeader>
          <div className="space-y-3">
            <div><Label>Nome</Label><Input data-testid="agent-form-name" value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} className="bg-[#05070A] border-[#1E293B] font-mono" /></div>
            <div><Label>Localização</Label><Input data-testid="agent-form-location" value={form.location} onChange={e => setForm({ ...form, location: e.target.value })} className="bg-[#05070A] border-[#1E293B] font-mono" /></div>
            <div className="grid grid-cols-3 gap-3">
              <div className="col-span-2"><Label>Host</Label><Input data-testid="agent-form-host" value={form.host} onChange={e => setForm({ ...form, host: e.target.value })} className="bg-[#05070A] border-[#1E293B] font-mono" /></div>
              <div><Label>Porta</Label><Input data-testid="agent-form-port" type="number" value={form.port} onChange={e => setForm({ ...form, port: e.target.value })} className="bg-[#05070A] border-[#1E293B] font-mono" /></div>
            </div>
            <div><Label>Usuário</Label><Input data-testid="agent-form-user" value={form.username} onChange={e => setForm({ ...form, username: e.target.value })} className="bg-[#05070A] border-[#1E293B] font-mono" /></div>
            <div><Label>Descrição</Label><Textarea data-testid="agent-form-desc" value={form.description} onChange={e => setForm({ ...form, description: e.target.value })} className="bg-[#05070A] border-[#1E293B] font-mono" /></div>
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setOpen(false)}>Cancelar</Button>
            <Button onClick={save} data-testid="save-agent-btn" className="bg-[#007AFF] hover:bg-[#0062CC]">Salvar</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={!!scriptModal} onOpenChange={(v) => !v && setScriptModal(null)}>
        <DialogContent className="bg-[#111722] border-[#1E293B] text-slate-100 max-w-3xl">
          <DialogHeader><DialogTitle>Instalador — {scriptModal?.agent.name}</DialogTitle></DialogHeader>
          <p className="text-xs text-slate-500 font-mono">Rode este script no servidor da rede remota para abrir túnel reverso.</p>
          <pre className="bg-[#05070A] border border-[#1E293B] rounded p-4 text-xs font-mono text-emerald-300 max-h-96 overflow-auto whitespace-pre-wrap">{scriptModal?.script}</pre>
          <DialogFooter>
            <Button variant="outline" onClick={() => { navigator.clipboard.writeText(scriptModal.script); toast.success("Copiado"); }} data-testid="copy-script-btn"
                    className="border-[#1E293B] bg-[#0B111C] text-slate-200 hover:bg-slate-800">
              <Copy className="w-4 h-4 mr-2" /> Copiar
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
