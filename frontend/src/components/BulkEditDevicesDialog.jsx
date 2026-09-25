import React, { useEffect, useState } from "react";
import { api, formatApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { toast } from "sonner";

const KEEP = "__keep__";
const empty = { add_tags: "", remove_tags: "", agent_id: KEEP, device_type: KEEP, protocol: KEEP, port: "", username: "", change_username: false, backup_enabled: KEEP };
const splitTags = (s) => s.split(/[,;]/).map(t => t.trim()).filter(Boolean);

/** Edita vários equipamentos de uma vez. Campos em "manter" não são alterados. */
export function BulkEditDevicesDialog({ open, onOpenChange, deviceIds, agents, deviceTypes, onDone }) {
  const [f, setF] = useState(empty);
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (open) setF(empty); }, [open]);

  const trigger = "bg-[#05070A] border-[#1E293B] font-mono";
  const content = "bg-[#111722] border-[#1E293B] text-slate-100";

  const apply = async () => {
    const payload = { device_ids: deviceIds, add_tags: splitTags(f.add_tags), remove_tags: splitTags(f.remove_tags) };
    if (f.agent_id !== KEEP) payload.agent_id = f.agent_id === "none" ? "" : f.agent_id;
    if (f.device_type !== KEEP) payload.device_type = f.device_type;
    if (f.protocol !== KEEP) payload.protocol = f.protocol;
    if (String(f.port).trim()) payload.port = Number(f.port);
    if (f.change_username) payload.username = f.username;
    if (f.backup_enabled !== KEEP) payload.backup_enabled = f.backup_enabled === "on";
    const changes = Object.keys(payload).filter(k => k !== "device_ids" && !(Array.isArray(payload[k]) && !payload[k].length));
    if (!changes.length) return toast.error("Nenhuma alteração definida");
    setBusy(true);
    try {
      const { data } = await api.post("/devices/bulk-update", payload);
      toast.success(`${data.updated} equipamento(s) atualizado(s)`);
      onOpenChange(false); onDone?.();
    } catch (e) { toast.error(formatApiError(e)); }
    finally { setBusy(false); }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="bg-[#111722] border-[#1E293B] text-slate-100 max-w-lg max-h-[92vh] overflow-y-auto" data-testid="bulk-edit-dialog">
        <DialogHeader><DialogTitle>Editar {deviceIds.length} equipamento(s)</DialogTitle></DialogHeader>
        <p className="text-xs text-slate-400 font-mono">Só os campos alterados são aplicados. "Manter" deixa o valor atual de cada equipamento.</p>
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label>Adicionar tags</Label>
              <Input data-testid="bulk-add-tags" value={f.add_tags} onChange={e => setF({ ...f, add_tags: e.target.value })} placeholder="Core, SP" className={trigger} />
            </div>
            <div>
              <Label>Remover tags</Label>
              <Input data-testid="bulk-remove-tags" value={f.remove_tags} onChange={e => setF({ ...f, remove_tags: e.target.value })} placeholder="Antigo" className={trigger} />
            </div>
          </div>
          <div>
            <Label>Agente proxy</Label>
            <Select value={f.agent_id} onValueChange={v => setF({ ...f, agent_id: v })}>
              <SelectTrigger data-testid="bulk-agent" className={trigger}><SelectValue /></SelectTrigger>
              <SelectContent className={content}>
                <SelectItem value={KEEP}>— manter —</SelectItem>
                <SelectItem value="none">Sem agente (conexão direta)</SelectItem>
                {agents.map(a => <SelectItem key={a.id} value={a.id}>{a.name}{a.location ? ` · ${a.location}` : ""}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label>Tipo</Label>
              <Select value={f.device_type} onValueChange={v => setF({ ...f, device_type: v })}>
                <SelectTrigger data-testid="bulk-type" className={trigger}><SelectValue /></SelectTrigger>
                <SelectContent className={content}>
                  <SelectItem value={KEEP}>— manter —</SelectItem>
                  {deviceTypes.map(([v, l]) => <SelectItem key={v} value={v}>{l}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div>
              <Label>Backup diário</Label>
              <Select value={f.backup_enabled} onValueChange={v => setF({ ...f, backup_enabled: v })}>
                <SelectTrigger data-testid="bulk-backup" className={trigger}><SelectValue /></SelectTrigger>
                <SelectContent className={content}>
                  <SelectItem value={KEEP}>— manter —</SelectItem>
                  <SelectItem value="on">Ativado</SelectItem>
                  <SelectItem value="off">Desativado</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label>Protocolo</Label>
              <Select value={f.protocol} onValueChange={v => setF({ ...f, protocol: v })}>
                <SelectTrigger data-testid="bulk-protocol" className={trigger}><SelectValue /></SelectTrigger>
                <SelectContent className={content}>
                  <SelectItem value={KEEP}>— manter —</SelectItem>
                  <SelectItem value="ssh">SSH</SelectItem>
                  <SelectItem value="telnet">Telnet</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div>
              <Label>Porta</Label>
              <Input data-testid="bulk-port" type="number" value={f.port} onChange={e => setF({ ...f, port: e.target.value })} placeholder="manter" className={trigger} />
            </div>
          </div>
          <div>
            <label className="flex items-center gap-2 text-sm cursor-pointer">
              <input type="checkbox" data-testid="bulk-change-username" checked={f.change_username} onChange={e => setF({ ...f, change_username: e.target.checked })} />
              Alterar usuário SSH/Telnet
            </label>
            {f.change_username && (
              <Input data-testid="bulk-username" value={f.username} onChange={e => setF({ ...f, username: e.target.value })} placeholder="vazio = usuário padrão" className={`${trigger} mt-1`} />
            )}
          </div>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancelar</Button>
          <Button onClick={apply} disabled={busy} data-testid="bulk-edit-save" className="bg-[#007AFF] hover:bg-[#0062CC]">Aplicar</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
