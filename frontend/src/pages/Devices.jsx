import React, { useEffect, useState } from "react";
import { api, formatApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { toast } from "sonner";
import { useNavigate } from "react-router-dom";
import { Plus, TerminalSquare, Trash2, Pencil, Search, Wifi, WifiOff, Zap, Upload, KeyRound } from "lucide-react";
import { ImportDevicesDialog } from "@/components/ImportDevicesDialog";

export const DEVICE_TYPES = [
  ["linux", "Linux / Unix"], ["mikrotik", "Mikrotik RouterOS"], ["cisco", "Cisco IOS/NX-OS"], ["huawei", "Huawei VRP"],
  ["ubiquiti", "Ubiquiti"], ["datacom", "Datacom DmOS"], ["zte", "ZTE"], ["other", "Outro (legado)"],
];
const typeLabel = (t) => DEVICE_TYPES.find(x => x[0] === t)?.[1] || t || "linux";

const emptyDevice = { name: "", host: "", port: 22, username: "", password: "", clear_password: false, device_type: "linux", tags: "", agent_id: "", description: "" };

export default function Devices() {
  const [devices, setDevices] = useState([]);
  const [agents, setAgents] = useState([]);
  const [q, setQ] = useState("");
  const [tagFilter, setTagFilter] = useState("");
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState(null);
  const [form, setForm] = useState(emptyDevice);
  const [importOpen, setImportOpen] = useState(false);
  const nav = useNavigate();

  const load = async () => {
    const [d, a] = await Promise.all([api.get("/devices"), api.get("/agents")]);
    setDevices(d.data); setAgents(a.data);
  };
  useEffect(() => { load(); }, []);

  const openNew = () => { setEditing(null); setForm(emptyDevice); setOpen(true); };
  const openEdit = (d) => {
    setEditing(d);
    setForm({ ...d, tags: (d.tags || []).join(", "), agent_id: d.agent_id || "", password: "", clear_password: false, device_type: d.device_type || "linux" });
    setOpen(true);
  };

  const save = async () => {
    const payload = {
      name: form.name.trim(),
      host: form.host.trim(),
      port: Number(form.port) || 22,
      username: form.username.trim(),
      password: form.password || null,
      clear_password: !!form.clear_password,
      device_type: form.device_type || "linux",
      tags: form.tags.split(",").map(t => t.trim()).filter(Boolean),
      agent_id: form.agent_id || null,
      description: form.description || "",
    };
    if (!payload.name || !payload.host) return toast.error("Nome e host são obrigatórios");
    try {
      if (editing) await api.put(`/devices/${editing.id}`, payload);
      else await api.post("/devices", payload);
      toast.success(editing ? "Equipamento atualizado" : "Equipamento cadastrado");
      setOpen(false); await load();
    } catch (e) { toast.error(formatApiError(e)); }
  };

  const del = async (d) => {
    if (!window.confirm(`Excluir ${d.name}?`)) return;
    await api.delete(`/devices/${d.id}`);
    toast.success("Excluído"); load();
  };

  const ping = async (d) => {
    toast.info(`Ping em ${d.name}…`);
    try {
      const { data } = await api.post(`/devices/${d.id}/ping`);
      toast.success(`${d.name}: ${data.status} ${data.latency_ms ? `(${data.latency_ms}ms)` : ""}`);
      load();
    } catch (e) { toast.error(formatApiError(e)); }
  };

  const allTags = Array.from(new Set(devices.flatMap(d => d.tags || [])));
  const filtered = devices.filter(d => {
    const matchQ = !q || d.name.toLowerCase().includes(q.toLowerCase()) || d.host.includes(q);
    const matchTag = !tagFilter || (d.tags || []).includes(tagFilter);
    return matchQ && matchTag;
  });

  const agentName = (id) => agents.find(a => a.id === id)?.name || "—";

  return (
    <div className="p-8 flex-1 overflow-y-auto" data-testid="devices-page">
      <div className="flex items-start justify-between mb-6">
        <div>
          <div className="text-xs uppercase tracking-widest text-slate-400 font-mono">Inventário</div>
          <h1 className="font-heading text-3xl sm:text-4xl font-bold text-slate-100 mt-1">Equipamentos</h1>
          <p className="text-slate-400 mt-2 text-sm">Cadastre hosts com porta SSH customizada e associe a um agente proxy.</p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" onClick={() => setImportOpen(true)} data-testid="import-devices-btn"
                  className="border-[#1E293B] bg-[#0B111C] text-slate-200 hover:bg-slate-800">
            <Upload className="w-4 h-4 mr-2" /> Importar CSV
          </Button>
          <Button onClick={openNew} data-testid="add-device-btn" className="bg-[#007AFF] hover:bg-[#0062CC]">
            <Plus className="w-4 h-4 mr-2" /> Novo Equipamento
          </Button>
        </div>
      </div>
      <ImportDevicesDialog open={importOpen} onOpenChange={setImportOpen} onDone={load} />

      <div className="flex gap-3 mb-4 flex-wrap">
        <div className="relative">
          <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
          <Input placeholder="Buscar por nome ou IP..." data-testid="search-devices-input" value={q} onChange={(e) => setQ(e.target.value)}
                 className="pl-9 w-72 bg-[#111722] border-[#1E293B] font-mono text-sm" />
        </div>
        <div className="flex gap-2 flex-wrap">
          <Badge onClick={() => setTagFilter("")} data-testid="tag-filter-all"
            className={`cursor-pointer ${!tagFilter ? "bg-[#007AFF] text-white" : "bg-[#111722] text-slate-300 border-[#1E293B]"}`}>Todas</Badge>
          {allTags.map(t => (
            <Badge key={t} onClick={() => setTagFilter(t)} data-testid={`tag-filter-${t}`}
              className={`cursor-pointer ${tagFilter === t ? "bg-[#007AFF] text-white" : "bg-[#111722] text-slate-300 border-[#1E293B]"}`}>{t}</Badge>
          ))}
        </div>
      </div>

      <Card className="bg-[#111722] border-[#1E293B] overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-xs uppercase tracking-widest text-slate-500 font-mono bg-[#0B111C]">
              <tr>
                <th className="text-left px-4 py-3">Status</th>
                <th className="text-left px-4 py-3">Nome</th>
                <th className="text-left px-4 py-3">Host:Porta</th>
                <th className="text-left px-4 py-3">Tipo</th>
                <th className="text-left px-4 py-3">Usuário</th>
                <th className="text-left px-4 py-3">Agente</th>
                <th className="text-left px-4 py-3">Tags</th>
                <th className="text-left px-4 py-3">Latência</th>
                <th className="text-right px-4 py-3">Ações</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[#1E293B]">
              {filtered.length === 0 && (
                <tr><td colSpan={9} className="text-center py-10 text-slate-500 font-mono">Nenhum equipamento encontrado</td></tr>
              )}
              {filtered.map(d => (
                <tr key={d.id} data-testid={`device-row-${d.id}`} className="hover:bg-slate-900/40">
                  <td className="px-4 py-3">
                    {d.status === "online" ? (
                      <span className="inline-flex items-center gap-1.5 text-emerald-400 font-mono text-xs"><Wifi className="w-3.5 h-3.5" /> online</span>
                    ) : d.status === "offline" ? (
                      <span className="inline-flex items-center gap-1.5 text-red-400 font-mono text-xs"><WifiOff className="w-3.5 h-3.5" /> offline</span>
                    ) : (
                      <span className="text-slate-500 font-mono text-xs">—</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-slate-100 font-medium">{d.name}</td>
                  <td className="px-4 py-3 font-mono text-slate-300">{d.host}:{d.port}</td>
                  <td className="px-4 py-3 text-xs text-slate-400" data-testid={`device-type-${d.id}`}>{typeLabel(d.device_type)}</td>
                  <td className="px-4 py-3 font-mono text-slate-400">
                    <span className="inline-flex items-center gap-1.5">{d.username || <span className="text-slate-600">padrão</span>}
                      {d.has_password && <KeyRound className="w-3 h-3 text-amber-400" title="senha própria" />}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-slate-400 text-xs">{agentName(d.agent_id)}</td>
                  <td className="px-4 py-3">
                    <div className="flex flex-wrap gap-1">
                      {(d.tags || []).map(t => <Badge key={t} className="bg-[#0B111C] border border-[#1E293B] text-slate-300 text-[10px]">{t}</Badge>)}
                    </div>
                  </td>
                  <td className="px-4 py-3 font-mono text-xs text-slate-400">{d.latency_ms ? `${d.latency_ms} ms` : "—"}</td>
                  <td className="px-4 py-3">
                    <div className="flex justify-end gap-1">
                      <Button size="sm" variant="ghost" onClick={() => ping(d)} data-testid={`ping-device-${d.id}`} className="text-amber-400 hover:bg-amber-950/40">
                        <Zap className="w-4 h-4" />
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => nav(`/terminal/${d.id}`)} data-testid={`connect-ssh-${d.id}`} className="text-[#4DA3FF] hover:bg-[#007AFF]/15">
                        <TerminalSquare className="w-4 h-4" />
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => openEdit(d)} data-testid={`edit-device-${d.id}`} className="text-slate-300 hover:bg-slate-800">
                        <Pencil className="w-4 h-4" />
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => del(d)} data-testid={`delete-device-${d.id}`} className="text-red-400 hover:bg-red-950/40">
                        <Trash2 className="w-4 h-4" />
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="bg-[#111722] border-[#1E293B] text-slate-100 max-w-lg">
          <DialogHeader><DialogTitle>{editing ? "Editar equipamento" : "Novo equipamento"}</DialogTitle></DialogHeader>
          <div className="space-y-3">
            <div>
              <Label>Nome</Label>
              <Input data-testid="device-form-name" value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} className="bg-[#05070A] border-[#1E293B] font-mono" />
            </div>
            <div className="grid grid-cols-3 gap-3">
              <div className="col-span-2">
                <Label>Host / IP</Label>
                <Input data-testid="device-form-host" value={form.host} onChange={e => setForm({ ...form, host: e.target.value })} className="bg-[#05070A] border-[#1E293B] font-mono" />
              </div>
              <div>
                <Label>Porta SSH</Label>
                <Input data-testid="device-form-port" type="number" value={form.port} onChange={e => setForm({ ...form, port: e.target.value })} className="bg-[#05070A] border-[#1E293B] font-mono" />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label>Usuário SSH</Label>
                <Input data-testid="device-form-username" value={form.username} onChange={e => setForm({ ...form, username: e.target.value })} placeholder="vazio = usuário padrão" className="bg-[#05070A] border-[#1E293B] font-mono" />
              </div>
              <div>
                <Label>Senha (RADIUS/TACACS)</Label>
                <Input data-testid="device-form-password" type="password" value={form.password} onChange={e => setForm({ ...form, password: e.target.value })}
                       placeholder={editing?.has_password ? "•••••••• (mantida)" : "vazio = senha padrão / chave"} className="bg-[#05070A] border-[#1E293B] font-mono" />
                {editing?.has_password && (
                  <label className="flex items-center gap-2 mt-1 text-[11px] text-slate-400 cursor-pointer">
                    <input type="checkbox" data-testid="device-form-clear-password" checked={form.clear_password} onChange={e => setForm({ ...form, clear_password: e.target.checked })} /> remover senha própria
                  </label>
                )}
              </div>
            </div>
            <div>
              <Label>Tipo de equipamento</Label>
              <Select value={form.device_type || "linux"} onValueChange={(v) => setForm({ ...form, device_type: v })}>
                <SelectTrigger data-testid="device-form-type" className="bg-[#05070A] border-[#1E293B] font-mono"><SelectValue /></SelectTrigger>
                <SelectContent className="bg-[#111722] border-[#1E293B] text-slate-100">
                  {DEVICE_TYPES.map(([v, l]) => <SelectItem key={v} value={v}>{l}</SelectItem>)}
                </SelectContent>
              </Select>
              <div className="text-[11px] text-slate-500 mt-1 font-mono">Equipamentos de rede habilitam algoritmos SSH legados e execução via shell no batch.</div>
            </div>
            <div>
              <Label>Agente Proxy (opcional)</Label>
              <Select value={form.agent_id || "none"} onValueChange={(v) => setForm({ ...form, agent_id: v === "none" ? "" : v })}>
                <SelectTrigger data-testid="device-form-agent" className="bg-[#05070A] border-[#1E293B] font-mono">
                  <SelectValue placeholder="Sem agente (conexão direta)" />
                </SelectTrigger>
                <SelectContent className="bg-[#111722] border-[#1E293B] text-slate-100">
                  <SelectItem value="none">Sem agente (conexão direta)</SelectItem>
                  {agents.map(a => <SelectItem key={a.id} value={a.id}>{a.name} · {a.location}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div>
              <Label>Tags (separadas por vírgula)</Label>
              <Input data-testid="device-form-tags" value={form.tags} onChange={e => setForm({ ...form, tags: e.target.value })} placeholder="Roteador, SP, Core" className="bg-[#05070A] border-[#1E293B] font-mono" />
            </div>
            <div>
              <Label>Descrição</Label>
              <Textarea data-testid="device-form-desc" value={form.description} onChange={e => setForm({ ...form, description: e.target.value })} className="bg-[#05070A] border-[#1E293B] font-mono" />
            </div>
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setOpen(false)} data-testid="device-form-cancel">Cancelar</Button>
            <Button onClick={save} data-testid="device-form-save" className="bg-[#007AFF] hover:bg-[#0062CC]">Salvar</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
