import React, { useEffect, useMemo, useRef, useState } from "react";
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
import { Checkbox } from "@/components/ui/checkbox";
import { Plus, TerminalSquare, Trash2, Pencil, Search, Wifi, WifiOff, Zap, Upload, Download, KeyRound, Play, X, Loader2, Copy } from "lucide-react";
import { ImportDevicesDialog } from "@/components/ImportDevicesDialog";
import { ExportDevicesDialog } from "@/components/ExportDevicesDialog";
import { BulkEditDevicesDialog } from "@/components/BulkEditDevicesDialog";

export const DEVICE_TYPES = [
  ["linux", "Linux / Unix"], ["mikrotik", "Mikrotik RouterOS"], ["cisco", "Cisco IOS/NX-OS"], ["huawei", "Huawei VRP"], ["juniper", "Juniper Junos"],
  ["ubiquiti", "Ubiquiti"], ["datacom", "Datacom DmOS"], ["zte", "ZTE (OLT / Switch)"], ["other", "Outro (legado)"],
];
const typeLabel = (t) => DEVICE_TYPES.find(x => x[0] === t)?.[1] || t || "linux";

const emptyDevice = { name: "", host: "", port: 22, protocol: "ssh", owner_id: "", username: "", password: "", clear_password: false, device_type: "linux", tags: "", agent_id: "", description: "", backup_enabled: true, backup_command: "", snmp_community: "", snmp_port: 161 };

export default function Devices() {
  const [devices, setDevices] = useState([]);
  const [agents, setAgents] = useState([]);
  const [q, setQ] = useState("");
  const [tagFilter, setTagFilter] = useState("");
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState(null);
  const [cloneOf, setCloneOf] = useState(null);
  const [form, setForm] = useState(emptyDevice);
  const [importOpen, setImportOpen] = useState(false);
  const [exportOpen, setExportOpen] = useState(false);
  const [bulkOpen, setBulkOpen] = useState(false);
  const [selected, setSelected] = useState(() => new Set());
  const [statusFilter, setStatusFilter] = useState("");
  const [pinging, setPinging] = useState(false);
  const lastClicked = useRef(null);
  const nav = useNavigate();

  const load = async () => {
    const [d, a] = await Promise.all([api.get("/devices"), api.get("/agents")]);
    setDevices(d.data); setAgents(a.data);
    // descarta da seleção o que não existe mais
    const ids = new Set(d.data.map(x => x.id));
    setSelected(prev => new Set([...prev].filter(id => ids.has(id))));
  };
  useEffect(() => { load(); }, []);

  const openNew = () => { setEditing(null); setCloneOf(null); setForm(emptyDevice); setOpen(true); };
  const toForm = (d) => ({ ...emptyDevice, ...d, tags: (d.tags || []).join(", "), agent_id: d.agent_id || "", protocol: d.protocol || "ssh", owner_id: d.owner_id || "", password: "", clear_password: false, device_type: d.device_type || "linux", backup_enabled: d.backup_enabled !== false, backup_command: d.backup_command || "", description: d.description || "", username: d.username || "", snmp_community: d.snmp_community || "", snmp_port: d.snmp_port || 161 });
  const openEdit = (d) => { setEditing(d); setCloneOf(null); setForm(toForm(d)); setOpen(true); };
  // Duplicar: mesmo formulário do "novo", pré-preenchido; a senha é copiada no servidor
  const openClone = (d) => {
    setEditing(null); setCloneOf(d);
    setForm({ ...toForm(d), name: `${d.name} (cópia)`, host: "" });
    setOpen(true);
  };

  const save = async () => {
    const payload = {
      name: form.name.trim(),
      host: form.host.trim(),
      port: Number(form.port) || (form.protocol === "telnet" ? 23 : 22),
      protocol: form.protocol || "ssh",
      username: form.username.trim(),
      password: form.password || null,
      clear_password: !!form.clear_password,
      device_type: form.device_type || "linux",
      tags: form.tags.split(",").map(t => t.trim()).filter(Boolean),
      agent_id: form.agent_id || null,
      description: form.description || "",
      backup_enabled: form.backup_enabled !== false,
      backup_command: (form.backup_command || "").trim() || null,
      snmp_community: (form.snmp_community || "").trim(),
      snmp_port: Number(form.snmp_port) || 161,
      copy_password_from: cloneOf?.has_password && !form.password && !form.clear_password ? cloneOf.id : null,
    };
    if (!payload.name || !payload.host) return toast.error("Nome e host são obrigatórios");
    if (cloneOf && devices.some(x => x.name.trim().toLowerCase() === payload.name.toLowerCase() && x.host === payload.host && Number(x.port) === payload.port))
      return toast.error("Já existe um equipamento com esse nome, host e porta");
    try {
      if (editing) await api.put(`/devices/${editing.id}`, payload);
      else await api.post("/devices", payload);
      toast.success(editing ? "Equipamento atualizado" : cloneOf ? `Cópia de ${cloneOf.name} criada` : "Equipamento cadastrado");
      setOpen(false); await load();
    } catch (e) { toast.error(formatApiError(e)); }
  };

  const testSnmp = async () => {
    if (!editing) return;
    toast.info("Testando SNMP…");
    try {
      const { data } = await api.post(`/devices/${editing.id}/snmp-test`);
      data.ok ? toast.success(`SNMP ok: ${data.sys_name || "respondeu"}${data.via_agent ? " (via agente)" : ""}`) : toast.error(`SNMP: ${data.error}`);
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

  const allTags = Array.from(new Set(devices.flatMap(d => d.tags || []))).sort();
  const filtered = useMemo(() => devices.filter(d => {
    const ql = q.toLowerCase();
    const matchQ = !q || d.name.toLowerCase().includes(ql) || d.host.includes(q) || (d.description || "").toLowerCase().includes(ql);
    const matchTag = !tagFilter || (d.tags || []).includes(tagFilter);
    const st = d.status === "online" || d.status === "offline" ? d.status : "unknown";
    const matchStatus = !statusFilter || st === statusFilter;
    return matchQ && matchTag && matchStatus;
  }), [devices, q, tagFilter, statusFilter]);

  // ---- seleção ----
  const filteredIds = filtered.map(d => d.id);
  const selectedIds = [...selected];
  const selInView = filteredIds.filter(id => selected.has(id)).length;
  const allInViewChecked = filteredIds.length > 0 && selInView === filteredIds.length;
  const headerState = allInViewChecked ? true : selInView > 0 ? "indeterminate" : false;

  const toggleAllInView = () => setSelected(prev => {
    const next = new Set(prev);
    if (allInViewChecked) filteredIds.forEach(id => next.delete(id));
    else filteredIds.forEach(id => next.add(id));
    return next;
  });

  // Shift+clique seleciona o intervalo desde o último clicado
  const toggleRow = (id, shift) => {
    setSelected(prev => {
      const next = new Set(prev);
      const on = !prev.has(id);
      const last = lastClicked.current;
      if (shift && last && last !== id) {
        const a = filteredIds.indexOf(last), b = filteredIds.indexOf(id);
        if (a >= 0 && b >= 0) {
          filteredIds.slice(Math.min(a, b), Math.max(a, b) + 1).forEach(x => on ? next.add(x) : next.delete(x));
          return next;
        }
      }
      on ? next.add(id) : next.delete(id);
      return next;
    });
    lastClicked.current = id;
  };
  const clearSelection = () => setSelected(new Set());
  const selectOffline = () => setSelected(new Set(filtered.filter(d => d.status === "offline").map(d => d.id)));

  const pingSelected = async () => {
    setPinging(true);
    toast.info(`Ping em ${selectedIds.length} equipamento(s)…`);
    try {
      const { data } = await api.post("/devices/ping-all", { device_ids: selectedIds });
      const on = data.filter(r => r.status === "online").length;
      toast.success(`${on} online · ${data.length - on} offline`);
      load();
    } catch (e) { toast.error(formatApiError(e)); }
    finally { setPinging(false); }
  };

  const deleteSelected = async () => {
    const n = selectedIds.length;
    if (!window.confirm(`Excluir ${n} equipamento(s)? Essa ação não pode ser desfeita.`)) return;
    try {
      const { data } = await api.post("/devices/bulk-delete", { device_ids: selectedIds });
      toast.success(`${data.deleted} excluído(s)`);
      clearSelection(); load();
    } catch (e) { toast.error(formatApiError(e)); }
  };

  const batchSelected = () => nav("/batch", { state: { deviceIds: selectedIds } });

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
          <Button variant="outline" onClick={() => setExportOpen(true)} disabled={!devices.length} data-testid="export-devices-btn"
                  className="border-[#1E293B] bg-[#0B111C] text-slate-200 hover:bg-slate-800">
            <Download className="w-4 h-4 mr-2" /> Exportar
          </Button>
          <Button onClick={openNew} data-testid="add-device-btn" className="bg-[#007AFF] hover:bg-[#0062CC]">
            <Plus className="w-4 h-4 mr-2" /> Novo Equipamento
          </Button>
        </div>
      </div>
      <ImportDevicesDialog open={importOpen} onOpenChange={setImportOpen} onDone={load} />
      <ExportDevicesDialog open={exportOpen} onOpenChange={setExportOpen} total={devices.length} filteredIds={filteredIds} selectedIds={selectedIds} />
      <BulkEditDevicesDialog open={bulkOpen} onOpenChange={setBulkOpen} deviceIds={selectedIds} agents={agents} deviceTypes={DEVICE_TYPES} onDone={load} />

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
        <div className="flex gap-1 ml-auto" data-testid="status-filter">
          {[["", "Todos"], ["online", "Online"], ["offline", "Offline"], ["unknown", "Sem status"]].map(([v, l]) => (
            <button key={v || "all"} onClick={() => setStatusFilter(v)} data-testid={`status-filter-${v || "all"}`}
              className={`text-xs font-mono px-2.5 py-1 rounded border ${statusFilter === v ? "border-[#007AFF] bg-[#007AFF]/15 text-slate-100" : "border-[#1E293B] bg-[#111722] text-slate-400 hover:text-slate-200"}`}>{l}</button>
          ))}
        </div>
      </div>

      {selected.size > 0 && (
        <div className="sticky top-0 z-10 mb-3 flex flex-wrap items-center gap-2 px-3 py-2 rounded border border-[#007AFF]/50 bg-[#0B1A2E]" data-testid="selection-bar">
          <span className="text-sm text-slate-100 font-mono mr-1" data-testid="selection-count">
            {selected.size} selecionado(s)
            {selected.size !== selInView && <span className="text-slate-400"> · {selInView} visível(is)</span>}
          </span>
          <Button size="sm" variant="ghost" onClick={pingSelected} disabled={pinging} data-testid="bulk-ping-btn" className="text-amber-400 hover:bg-amber-950/40">
            {pinging ? <Loader2 className="w-4 h-4 mr-1.5 animate-spin" /> : <Zap className="w-4 h-4 mr-1.5" />} Ping
          </Button>
          <Button size="sm" variant="ghost" onClick={batchSelected} data-testid="bulk-batch-btn" className="text-[#4DA3FF] hover:bg-[#007AFF]/15">
            <Play className="w-4 h-4 mr-1.5" /> Executar em lote
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setBulkOpen(true)} data-testid="bulk-edit-btn" className="text-slate-200 hover:bg-slate-800">
            <Pencil className="w-4 h-4 mr-1.5" /> Editar em massa
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setExportOpen(true)} data-testid="bulk-export-btn" className="text-slate-200 hover:bg-slate-800">
            <Download className="w-4 h-4 mr-1.5" /> Exportar
          </Button>
          <Button size="sm" variant="ghost" onClick={deleteSelected} data-testid="bulk-delete-btn" className="text-red-400 hover:bg-red-950/40">
            <Trash2 className="w-4 h-4 mr-1.5" /> Excluir
          </Button>
          <Button size="sm" variant="ghost" onClick={clearSelection} data-testid="bulk-clear-btn" className="ml-auto text-slate-400 hover:bg-slate-800">
            <X className="w-4 h-4 mr-1.5" /> Limpar seleção
          </Button>
        </div>
      )}

      <Card className="bg-[#111722] border-[#1E293B] overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-xs uppercase tracking-widest text-slate-500 font-mono bg-[#0B111C]">
              <tr>
                <th className="pl-4 pr-1 py-3 w-8">
                  <Checkbox checked={headerState} onCheckedChange={toggleAllInView} disabled={!filteredIds.length}
                            data-testid="select-all-devices" aria-label="Selecionar todos os visíveis" className="border-slate-500" />
                </th>
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
                <tr><td colSpan={10} className="text-center py-10 text-slate-500 font-mono">Nenhum equipamento encontrado</td></tr>
              )}
              {filtered.map(d => (
                <tr key={d.id} data-testid={`device-row-${d.id}`} className={selected.has(d.id) ? "bg-[#007AFF]/10" : "hover:bg-slate-900/40"}>
                  <td className="pl-4 pr-1 py-3" onClick={(e) => { e.preventDefault(); toggleRow(d.id, e.shiftKey); }}>
                    <Checkbox checked={selected.has(d.id)} data-testid={`select-device-${d.id}`} aria-label={`Selecionar ${d.name}`} className="border-slate-500" />
                  </td>
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
                  <td className="px-4 py-3 font-mono text-slate-300">{d.host}:{d.port} <span data-testid={`device-proto-${d.id}`} className={`ml-1 text-[9px] uppercase px-1 py-0.5 rounded border ${d.protocol === "telnet" ? "border-amber-500/40 text-amber-400" : "border-[#1E293B] text-slate-500"}`}>{d.protocol || "ssh"}</span></td>
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
                      <Button size="sm" variant="ghost" onClick={() => openEdit(d)} data-testid={`edit-device-${d.id}`} className="text-slate-300 hover:bg-slate-800" title="Editar">
                        <Pencil className="w-4 h-4" />
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => openClone(d)} data-testid={`clone-device-${d.id}`} className="text-slate-300 hover:bg-slate-800" title="Duplicar (muda só nome e IP)">
                        <Copy className="w-4 h-4" />
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
        <DialogContent className="bg-[#111722] border-[#1E293B] text-slate-100 max-w-lg max-h-[92vh] overflow-y-auto">
          <DialogHeader><DialogTitle>{editing ? "Editar equipamento" : cloneOf ? `Duplicar ${cloneOf.name}` : "Novo equipamento"}</DialogTitle></DialogHeader>
          {cloneOf && (
            <div className="text-[11px] font-mono text-slate-400 -mt-1" data-testid="clone-hint">
              Tudo copiado de <span className="text-slate-200">{cloneOf.name}</span>{cloneOf.has_password ? ", inclusive a senha" : ""}. Troque o nome e o host/IP.
            </div>
          )}
          <div className="space-y-3">
            <div>
              <Label>Nome</Label>
              <Input data-testid="device-form-name" value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} className="bg-[#05070A] border-[#1E293B] font-mono" />
            </div>
            <div className="grid grid-cols-4 gap-3">
              <div className="col-span-2">
                <Label>Host / IP</Label>
                <Input data-testid="device-form-host" autoFocus={!!cloneOf} placeholder={cloneOf ? `antes: ${cloneOf.host}` : ""} value={form.host} onChange={e => setForm({ ...form, host: e.target.value })} className="bg-[#05070A] border-[#1E293B] font-mono" />
              </div>
              <div>
                <Label>Protocolo</Label>
                <Select value={form.protocol || "ssh"} onValueChange={(v) => setForm({ ...form, protocol: v, port: (v === "telnet" && Number(form.port) === 22) ? 23 : (v === "ssh" && Number(form.port) === 23) ? 22 : form.port })}>
                  <SelectTrigger data-testid="device-form-protocol" className="bg-[#05070A] border-[#1E293B] font-mono"><SelectValue /></SelectTrigger>
                  <SelectContent className="bg-[#111722] border-[#1E293B] text-slate-100">
                    <SelectItem value="ssh">SSH</SelectItem>
                    <SelectItem value="telnet">Telnet</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div>
                <Label>Porta</Label>
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
                       placeholder={editing?.has_password ? "•••••••• (mantida)" : cloneOf?.has_password ? "•••••••• (copiada do original)" : "vazio = senha padrão / chave"} className="bg-[#05070A] border-[#1E293B] font-mono" />
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
            <div className="grid grid-cols-4 gap-3 items-end">
              <div className="col-span-2">
                <Label>SNMP community (v2c)</Label>
                <Input data-testid="device-form-snmp" value={form.snmp_community} onChange={e => setForm({ ...form, snmp_community: e.target.value })}
                       placeholder="vazio = community padrão dos Mapas" className="bg-[#05070A] border-[#1E293B] font-mono" />
              </div>
              <div>
                <Label>Porta SNMP</Label>
                <Input type="number" value={form.snmp_port} onChange={e => setForm({ ...form, snmp_port: e.target.value })} className="bg-[#05070A] border-[#1E293B] font-mono" />
              </div>
              <Button type="button" variant="outline" disabled={!editing} onClick={testSnmp} data-testid="device-form-snmp-test"
                      title={editing ? "Testa com a community já salva" : "Salve o equipamento primeiro"}
                      className="border-[#1E293B] bg-[#0B111C] text-slate-200 hover:bg-slate-800">Testar</Button>
            </div>
            <div className="grid grid-cols-3 gap-3 items-end">
              <label className="flex items-center gap-2 text-xs text-slate-300 cursor-pointer pb-2">
                <input type="checkbox" data-testid="device-form-backup-enabled" checked={form.backup_enabled !== false} onChange={e => setForm({ ...form, backup_enabled: e.target.checked })} /> Backup diário
              </label>
              <div className="col-span-2">
                <Label>Comando de backup (opcional, sobrescreve o padrão do tipo)</Label>
                <Input data-testid="device-form-backup-command" value={form.backup_command} onChange={e => setForm({ ...form, backup_command: e.target.value })} placeholder="ex.: show running-config" className="bg-[#05070A] border-[#1E293B] font-mono" />
              </div>
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
