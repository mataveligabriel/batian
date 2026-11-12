import React, { useEffect, useState } from "react";
import { api, formatApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { toast } from "sonner";

const KEEP = "__keep__";
const empty = { add_tags: "", remove_tags: "", agent_id: KEEP, device_type: KEEP, protocol: KEEP, port: "", username: "", change_username: false, pw_mode: KEEP, password: "", password2: "", show_pw: false, backup_enabled: KEEP, no_alert: KEEP,
  change_snmp: false, snmp_community: "", snmp_port: "" };
const splitTags = (s) => s.split(/[,;]/).map(t => t.trim()).filter(Boolean);

/** Edita vários equipamentos de uma vez. Campos em "manter" não são alterados. */
export function BulkEditDevicesDialog({ open, onOpenChange, deviceIds, agents, deviceTypes, onDone }) {
  const [f, setF] = useState(empty);
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (open) setF(empty); }, [open]);

  const trigger = "bg-sunken border-line font-mono";
  const content = "bg-surface border-line text-slate-100";

  const apply = async () => {
    const payload = { device_ids: deviceIds, add_tags: splitTags(f.add_tags), remove_tags: splitTags(f.remove_tags) };
    if (f.agent_id !== KEEP) payload.agent_id = f.agent_id === "none" ? "" : f.agent_id;
    if (f.device_type !== KEEP) payload.device_type = f.device_type;
    if (f.protocol !== KEEP) payload.protocol = f.protocol;
    if (String(f.port).trim()) payload.port = Number(f.port);
    if (f.change_username) payload.username = f.username;
    if (f.pw_mode === "set") {
      if (!f.password) return toast.error("Digite a nova senha");
      if (f.password !== f.password2) return toast.error("As duas senhas não conferem");
      payload.password = f.password;
    }
    if (f.pw_mode === "clear") payload.clear_password = true;
    if (f.backup_enabled !== KEEP) payload.backup_enabled = f.backup_enabled === "on";
    if (f.no_alert !== KEEP) payload.no_alert = f.no_alert === "mute";
    if (f.change_snmp) payload.snmp_community = f.snmp_community.trim();
    if (String(f.snmp_port).trim()) payload.snmp_port = Number(f.snmp_port);
    const changes = Object.keys(payload).filter(k => k !== "device_ids" && !(Array.isArray(payload[k]) && !payload[k].length));
    if ((payload.password || payload.clear_password) && !window.confirm(
      `${payload.clear_password ? "Apagar a senha" : "Trocar a senha"} de login de ${deviceIds.length} equipamento(s)?` +
      (payload.username !== undefined ? ` Usuário: ${payload.username || "(padrão)"}.` : ""))) return;
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
      <DialogContent className="bg-surface border-line text-slate-100 max-w-lg max-h-[92vh] overflow-y-auto" data-testid="bulk-edit-dialog">
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
          <div>
            <Label>Alerta no Telegram / push quando cair ou voltar</Label>
            <Select value={f.no_alert} onValueChange={v => setF({ ...f, no_alert: v })}>
              <SelectTrigger data-testid="bulk-no-alert" className={trigger}><SelectValue /></SelectTrigger>
              <SelectContent className={content}>
                <SelectItem value={KEEP}>— manter —</SelectItem>
                <SelectItem value="mute">Não alarmar (só registra no histórico)</SelectItem>
                <SelectItem value="alert">Alarmar normalmente</SelectItem>
              </SelectContent>
            </Select>
            {f.no_alert === "mute" && <p className="text-[11px] text-slate-500 mt-1">Vale para equipamento fora/de volta e interfaces caindo nos Mapas. Os incidentes continuam abertos no histórico, sem aviso.</p>}
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
          <div className="rounded-md border border-line p-3 space-y-2" data-testid="bulk-login">
            <div className="text-sm font-medium">Login SSH/Telnet</div>
            <label className="flex items-center gap-2 text-sm cursor-pointer">
              <input type="checkbox" data-testid="bulk-change-username" checked={f.change_username} onChange={e => setF({ ...f, change_username: e.target.checked })} />
              Alterar usuário
            </label>
            {f.change_username && (
              <Input data-testid="bulk-username" value={f.username} onChange={e => setF({ ...f, username: e.target.value })} placeholder="vazio = usuário padrão" className={trigger} autoComplete="off" />
            )}
            <div>
              <Label>Senha</Label>
              <Select value={f.pw_mode} onValueChange={v => setF({ ...f, pw_mode: v, password: "", password2: "" })}>
                <SelectTrigger data-testid="bulk-pw-mode" className={trigger}><SelectValue /></SelectTrigger>
                <SelectContent className={content}>
                  <SelectItem value={KEEP}>— manter a senha de cada um —</SelectItem>
                  <SelectItem value="set">Definir nova senha</SelectItem>
                  <SelectItem value="clear">Apagar a senha salva</SelectItem>
                </SelectContent>
              </Select>
            </div>
            {f.pw_mode === "set" && (
              <div className="grid grid-cols-2 gap-2">
                <Input data-testid="bulk-password" type={f.show_pw ? "text" : "password"} value={f.password} onChange={e => setF({ ...f, password: e.target.value })}
                       placeholder="nova senha" className={trigger} autoComplete="new-password" aria-label="Nova senha" />
                <Input data-testid="bulk-password2" type={f.show_pw ? "text" : "password"} value={f.password2} onChange={e => setF({ ...f, password2: e.target.value })}
                       placeholder="repita a senha" className={trigger} autoComplete="new-password" aria-label="Repita a senha" />
                <label className="col-span-2 flex items-center gap-2 text-xs text-slate-400 cursor-pointer">
                  <input type="checkbox" checked={f.show_pw} onChange={e => setF({ ...f, show_pw: e.target.checked })} /> mostrar senha
                  {f.password && f.password2 && f.password !== f.password2 && <span className="text-red-300 ml-auto">as senhas não conferem</span>}
                </label>
              </div>
            )}
            {f.pw_mode === "clear" && <p className="text-[11px] text-slate-500">Os equipamentos ficam sem senha salva (acesso só por chave SSH, se houver).</p>}
            <p className="text-[11px] text-slate-500">A senha vai criptografada para os {deviceIds.length} equipamento(s) e nunca aparece de volta na tela.</p>
          </div>
          <div>
            <label className="flex items-center gap-2 text-sm cursor-pointer">
              <input type="checkbox" data-testid="bulk-change-snmp" checked={f.change_snmp} onChange={e => setF({ ...f, change_snmp: e.target.checked })} />
              Alterar community SNMP
            </label>
            {f.change_snmp && (
              <div className="grid grid-cols-[1fr_7rem] gap-2 mt-1">
                <Input data-testid="bulk-snmp-community" value={f.snmp_community} onChange={e => setF({ ...f, snmp_community: e.target.value })}
                  placeholder="vazio = community padrão do monitoramento" className={trigger} autoComplete="off" />
                <Input data-testid="bulk-snmp-port" type="number" value={f.snmp_port} onChange={e => setF({ ...f, snmp_port: e.target.value })} placeholder="porta 161" className={trigger} />
              </div>
            )}
          </div>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancelar</Button>
          <Button onClick={apply} disabled={busy} data-testid="bulk-edit-save" className="bg-brand hover:bg-brand-strong">Aplicar</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
